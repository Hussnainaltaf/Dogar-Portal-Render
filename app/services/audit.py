"""Audit logging service - emits AuditLog rows for state-changing events."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.workflow import AuditAction, AuditEntity
from app.models import AuditLog, User


def _json_safe(v: Any) -> Any:
    """Coerce a single value to something the stdlib json module can dump.

    SQLAlchemy's ``JSON`` column calls ``json.dumps`` which by default
    chokes on ``datetime``, ``date``, ``Decimal``, ``bytes`` and ORM
    instances. We normalise those here so audit-log writes never crash
    the originating request (the v5 → v6 regression was a 500 on stage
    update caused by a ``Decimal`` salary).
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        # Preserve fractional precision but stay JSON-friendly.
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, AttributeError):
            return "<binary>"
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except (TypeError, ValueError):
            return str(v)
    if isinstance(v, (list, tuple, set)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    return str(v)


def _row_to_dict(obj) -> Dict[str, Any]:
    """Best-effort SQLAlchemy row -> dict snapshot."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    out = {}
    try:
        for col in obj.__table__.columns:
            v = getattr(obj, col.name, None)
            out[col.name] = _json_safe(v)
    except (AttributeError, TypeError) as exc:
        # ORM row missing __table__ or column getter raised -- log and
        # return whatever we collected so far. Audit logging must never
        # break the request that triggered it.
        import logging
        logging.getLogger("dtc.audit").exception("audit._row_to_dict failed: %s", exc)
    return out


def log_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: Optional[int],
    action: str,
    actor: Optional[User] = None,
    summary: str = "",
    before: Any = None,
    after: Any = None,
    request: Optional[Request] = None,
    commit: bool = True,
    outcome: str = "success",
    permission: Optional[str] = None,
    doc_category: Optional[str] = None,
) -> AuditLog:
    """Write a single audit log row.

    ``outcome`` is 'success' or 'denied'. It defaults to 'success' because every
    existing caller in this codebase logs an action that already went through --
    the value is a statement of fact for them, not a guess. Denials come in via
    :func:`log_permission_denied`.

    ``permission`` records the permission key that was required and refused, and
    is meaningful only for denials.

    ``doc_category`` is the document type for document actions (NULL for
    everything else), denormalised so the Audit Log page can filter on it with
    an index instead of reaching into ``after_json`` -- see alembic
    a1c6e8b4d072 for why the JSON route was rejected.
    """
    ip = ""
    ua = ""
    if request is not None:
        try:
            ip = (request.client.host if request.client else "") or ""
            ua = (request.headers.get("user-agent") or "")[:255]
        except (AttributeError, KeyError) as exc:
            # Some test clients have no request.client -- keep audit best-effort.
            import logging
            logging.getLogger("dtc.audit").debug("audit: could not read request meta: %s", exc)

    if before is None:
        before_json = None
    elif isinstance(before, (dict, list)):
        before_json = _json_safe(before)
    else:
        before_json = _row_to_dict(before)

    if after is None:
        after_json = None
    elif isinstance(after, (dict, list)):
        after_json = _json_safe(after)
    else:
        after_json = _row_to_dict(after)

    entry = AuditLog(
        entity_type=str(entity_type),
        entity_id=entity_id,
        action=str(action),
        actor_user_id=actor.id if actor else None,
        actor_name=(actor.name if actor else "system")[:150],
        summary=summary[:500],
        before_json=before_json,
        after_json=after_json,
        ip_address=ip[:64],
        user_agent=ua,
        outcome=(str(outcome) or "success")[:16],
        permission=(str(permission)[:80] if permission else None),
        doc_category=(str(doc_category)[:60] if doc_category else None),
    )
    try:
        db.add(entry)
        if commit:
            db.commit()
            db.refresh(entry)
    except Exception as exc:
        # Last line of defence: audit writes MUST NEVER break the
        # originating request. If JSON serialisation or DB constraints
        # blow up, we roll back the audit row and log a warning so the
        # business operation (stage change, update, etc.) can succeed.
        import logging
        logging.getLogger("dtc.audit").exception(
            "audit.log_event swallowed exception: %s", exc
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 -- rollback failures are non-recoverable
            pass
        return entry
    return entry


# ---- Convenience wrappers -------------------------------------------------
def log_assign(db, *, candidate, job_category, actor, request=None):
    return log_event(
        db,
        entity_type=AuditEntity.ASSIGNMENT.value,
        entity_id=candidate.id,
        action=AuditAction.ASSIGN.value,
        actor=actor, request=request,
        summary=f"Assigned candidate '{candidate.full_name}' to trade '{job_category.trade}' (demand #{job_category.demand_id})",
        before=None,
        after={
            "candidate_id": candidate.id,
            "candidate_name": candidate.full_name,
            "job_category_id": job_category.id,
            "trade": job_category.trade,
            "demand_id": job_category.demand_id,
        },
    )


def log_unassign(db, *, candidate, job_category, assignment_snapshot, cleared_fields, actor, request=None):
    return log_event(
        db,
        entity_type=AuditEntity.ASSIGNMENT.value,
        entity_id=candidate.id,
        action=AuditAction.UNASSIGN.value,
        actor=actor, request=request,
        summary=f"Unassigned candidate '{candidate.full_name}' from trade '{job_category.trade if job_category else ''}'; cleared {len(cleared_fields)} derived field(s)",
        before=assignment_snapshot,
        after={
            "candidate_id": candidate.id,
            "candidate_status": candidate.status,
            "cleared_fields": cleared_fields,
        },
    )


def log_stage_change(db, *, candidate, old_stage, new_stage, actor, request=None, reason=""):
    return log_event(
        db,
        entity_type=AuditEntity.CANDIDATE.value,
        entity_id=candidate.id,
        action=AuditAction.STAGE_CHANGE.value,
        actor=actor, request=request,
        summary=f"Stage: {old_stage} -> {new_stage}" + (f" ({reason})" if reason else ""),
        before={"status": old_stage},
        after={"status": new_stage, "reason": reason},
    )


def log_wizard_step(db, *, wizard_state, step, actor, request=None):
    return log_event(
        db,
        entity_type=AuditEntity.WIZARD_STATE.value,
        entity_id=wizard_state.id,
        action=AuditAction.WIZARD_STEP.value,
        actor=actor, request=request,
        summary=f"Saved wizard step {step}/{wizard_state.total_steps}",
        after={"step": step, "current_step": wizard_state.current_step},
    )


def log_wizard_finalize(db, *, wizard_state, candidate, actor, request=None):
    return log_event(
        db,
        entity_type=AuditEntity.WIZARD_STATE.value,
        entity_id=wizard_state.id,
        action=AuditAction.WIZARD_FINALIZE.value,
        actor=actor, request=request,
        summary=f"Finalized wizard -> candidate #{candidate.id} '{candidate.full_name}'",
        after={"candidate_id": candidate.id, "candidate_name": candidate.full_name},
    )


def log_wizard_reopen(db, *, wizard_state, actor, request=None):
    return log_event(
        db,
        entity_type=AuditEntity.WIZARD_STATE.value,
        entity_id=wizard_state.id,
        action=AuditAction.WIZARD_REOPEN.value,
        actor=actor, request=request,
        summary=f"Reopened wizard for candidate #{wizard_state.candidate_id}",
    )


# ---- Document access trail (2026-08-15) -----------------------------------
#
# The four role-protected document capabilities are view / edit / print /
# disable. Until now NONE of them wrote an audit row: `select count(*) from
# audit_logs where entity_type='document'` returned 0 on the live database,
# and app/api/endpoints/documents.py contained no audit call at all.
#
# That is the gap this closes. VIEW and PRINT are reads, which is precisely
# why they matter: rendering an OEP form or a protector packet discloses a
# candidate's passport number, CNIC and address, so "who printed this, for
# which candidate, and when?" is an access-control question the portal could
# not previously answer.
#
# Design constraints inherited from log_event():
#   * Writes are best-effort -- an audit failure must never break the print
#     or download the operator asked for.
#   * The row lands in the caller's session, which is the PER-TENANT session
#     (documents.py uses Depends(get_tenant_db)), so a tenant's access trail
#     stays inside that tenant's database.
def log_document_action(
    db,
    *,
    action: str,
    template,
    actor=None,
    request=None,
    record_id=None,
    detail: str = "",
    before=None,
    after=None,
):
    """Record one role-protected document action with actor + timestamp.

    ``occurred_at`` is a server-side ``func.now()`` default on the AuditLog
    column, so the timestamp is the database's, not the caller's -- a client
    cannot backdate its own trail.

    Parameters
    ----------
    action : str
        An :class:`AuditAction` value -- typically VIEW, UPDATE, PRINT,
        DISABLE or ENABLE.
    template : DocumentTemplate | None
        The template being acted on. ``None`` is tolerated so a caller can
        still record an attempt against a template that no longer exists.
    record_id : int | str | None
        The candidate/demand/client record the document was rendered for.
        Recorded in ``after_json`` because *which person's data was
        disclosed* is the part an auditor actually needs.
    Failure semantics
    -----------------
    Best-effort, like :func:`log_event` -- but the protection has to live
    HERE too, which a first cut of this helper got wrong.

    ``log_event`` wraps only the ``db.add`` / ``db.commit`` in a try. It does
    NOT protect the caller from the *argument construction* that happens
    before the call: a template whose attribute access raises (a detached
    SQLAlchemy instance after a rollback is the realistic case), or a
    ``record_id`` whose ``__str__`` blows up, would propagate out of this
    helper and 500 the request it was only supposed to observe.

    tests/test_audit_trail.py::test_a_failing_audit_write_does_not_break_the_print
    caught exactly that: with the logger made to raise, the print endpoint
    returned 500 instead of 200. A missing log line is bad; a print that
    fails *because* logging failed is worse, and it would be a new failure
    mode introduced by an observability feature. Hence the outer guard.
    """
    try:
        tpl_id = getattr(template, "id", None)
        tpl_name = getattr(template, "name", "") or ""
        category = getattr(template, "category", "") or ""

        bits = [f"{action} document '{tpl_name}'" if tpl_name else f"{action} document"]
        if category:
            bits.append(f"[{category}]")
        if record_id not in (None, ""):
            bits.append(f"record #{record_id}")
        if detail:
            bits.append(f"-- {detail}")

        payload = {
            "template_id": tpl_id,
            "template_name": tpl_name,
            "category": category,
            "data_source": getattr(template, "data_source", "") or "",
            "record_id": record_id,
        }
        if after is not None:
            payload.update(after if isinstance(after, dict) else {"after": after})

        return log_event(
            db,
            entity_type=AuditEntity.DOCUMENT.value,
            entity_id=tpl_id,
            action=str(action),
            actor=actor,
            request=request,
            summary=" ".join(bits),
            before=before,
            after=payload,
            # Same value that goes into the payload, promoted to its own
            # indexed column so "document type" is filterable. `category` is
            # already normalised on DocumentTemplate; passing "" through as
            # None keeps the column meaning "no type recorded" rather than
            # inventing an empty-string category to match on.
            doc_category=category or None,
        )
    except Exception as exc:  # noqa: BLE001 -- see "Failure semantics" above
        import logging

        logging.getLogger("dtc.audit").exception(
            "audit.log_document_action swallowed exception (action=%r): %s",
            action, exc,
        )
        return None


# ---- Permission denials ---------------------------------------------------
# WHY THIS EXISTS (2026-08-18)
#
# Before this, a permission denial produced an HTTP 403 and NOTHING ELSE. The
# audit trail recorded only actions that succeeded, so a user probing for
# documents:print, candidates:delete or audit:view left no trace whatsoever --
# exactly the events a security review is looking for, and the reason the
# operator could not "find denied actions" on the Audit Log page. It was not a
# missing filter; the data did not exist.
DENIED = "denied"
SUCCESS = "success"


def log_permission_denied(
    db: Session,
    *,
    user: Optional[User],
    permission: str,
    entity_type: str = "security",
    entity_id: Optional[int] = None,
    request: Optional[Request] = None,
) -> Optional[AuditLog]:
    """Record that ``user`` was refused ``permission``.

    FAILURE SEMANTICS. This runs on the way to raising a 403, so it must never
    change the outcome of the request:

      * It commits on its OWN, because the caller is about to raise and any
        pending transaction would otherwise be discarded -- an audit row that
        is rolled back is the same as no audit row, which is the bug being
        fixed here.
      * It swallows its own exceptions and logs them loudly. A denial that
        cannot be written must not turn a clean 403 (correct behaviour) into a
        500 (a new fault). But it is logged at WARNING with the reason, because
        a persistent failure here is indistinguishable from having no denial
        auditing at all -- the same fail-open-but-loudly rule the brute-force
        lockout check follows.
    """
    path = ""
    method = ""
    if request is not None:
        try:
            path = str(getattr(request.url, "path", "") or "")[:200]
            method = str(getattr(request, "method", "") or "")[:10]
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            pass
    who = getattr(user, "email", None) or getattr(user, "name", None) or "anonymous"
    summary = f"DENIED {permission}"
    if method or path:
        summary += f" on {method} {path}".rstrip()
    summary += f" for {who}"

    try:
        return log_event(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            action="denied",
            actor=user,
            request=request,
            summary=summary,
            outcome=DENIED,
            permission=permission,
            commit=True,
        )
    except Exception as exc:  # noqa: BLE001 -- see "FAILURE SEMANTICS" above
        import logging

        logging.getLogger("dtc.audit").warning(
            "audit.log_permission_denied FAILED -- this denial is NOT in the "
            "trail (permission=%r user=%r): %s", permission, who, exc,
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 -- rollback failures are non-recoverable
            pass
        return None
