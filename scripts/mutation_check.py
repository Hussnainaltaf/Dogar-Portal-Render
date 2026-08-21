#!/usr/bin/env python3
"""Mutation testing harness -- prove the regression tests can actually fail.

Why this file exists
--------------------
The project rule is that a test only counts as evidence if it is *capable of
failing* when the protection it guards is removed. Checking that requires
deliberately breaking the source ("mutating" it), re-running the tests, and
confirming they go red. Up to 2026-08-16 that was done with throwaway scripts
in /tmp. Those scripts were **wrong**, and the way they were wrong is worth
recording here so nobody rebuilds them.

The stale-bytecode trap (found 2026-08-16)
------------------------------------------
CPython decides whether a cached ``__pycache__/*.pyc`` is still valid by
comparing two things against the source file:

    (integer-second mtime, byte size)

Not a hash. Not sub-second time. The old harness did::

    shutil.copy(path, backup)        # copy does NOT preserve mtime
    path.write_text(mutated_source)  # mutant, same second
    ...run pytest...
    shutil.move(backup, path)        # restore, mtime = the copy's mtime

Combine that with a *same-size* mutation -- the real one was ``404`` -> ``403``,
identical byte length -- and both fields match the mutant's cached ``.pyc``.
Python therefore kept executing **mutant bytecode from a correct source file**.

The symptom was a genuinely baffling contradiction that cost a debugging
session: ``app/core/tenancy.py`` said ``status_code=404``, ``inspect.getsource``
(which reads the *file*) agreed, a minimal FastAPI repro preserved the 404 --
and yet the live app raised 403 with the mutation's exact payload. Two tests in
tests/test_tenant_isolation_live.py failed in the full suite and passed alone.
Nothing was wrong with the application code at all.

Two consequences, both handled below:

1.  A poisoned cache can make a **later** mutation report a false CAUGHT (the
    tests fail, but because of leftover mutant bytecode, not the mutation under
    test). So results from the old harness cannot be trusted and were re-earned
    with this one.
2.  A poisoned cache outlives the harness and corrupts ordinary test runs
    afterwards, which is how it was noticed.

How this harness stays sound
----------------------------
*   Every subprocess runs with ``PYTHONDONTWRITEBYTECODE=1`` and ``-B``, so the
    mutant can never be written to a cache in the first place.
*   ``__pycache__`` is purged before the run and after every mutation, so a
    cache poisoned by anything else cannot be inherited.
*   Restore is byte-exact from an in-memory copy (not a file that can be left
    behind by a crash), and the restored file's mtime is bumped to *now*, which
    invalidates any cache that somehow survived.
*   The restore is verified by re-reading the bytes and comparing; a failed
    restore aborts the whole run loudly rather than leaving the tree mutated.
*   Every mutation must find its anchor text. An anchor that no longer matches
    is reported as NO-OP and treated as a failure, because a mutation that
    silently changes nothing would otherwise look like a passing test suite and
    be miscounted as CAUGHT.

Usage
-----
    ./venv/bin/python scripts/mutation_check.py <batch> [<batch> ...]
    ./venv/bin/python scripts/mutation_check.py --list

Exit code is non-zero if any mutation ESCAPED (tests stayed green) or was a
NO-OP (anchor missing).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

REPO = pathlib.Path(__file__).resolve().parent.parent
PY = REPO / "venv" / "bin" / "python"


# --------------------------------------------------------------------------
# Mutation model
# --------------------------------------------------------------------------
@dataclass
class Mutation:
    """One deliberate break, plus the tests that are supposed to notice it."""

    ident: str
    description: str
    path: str          # repo-relative
    old: str           # anchor text
    new: str           # replacement
    expect: str = "caught"
    """What a *correct* suite should do with this mutation.

    ``"caught"`` (default)
        The mutation removes a real protection; the tests must go red.
    ``"survives"``
        A **control**. The mutation is behaviour-preserving -- typically a
        rename or a reformat -- so the tests must stay GREEN. Controls are not
        padding: they are what distinguishes a test that checks a behaviour
        from one that merely pins an identifier. Mutation JH (renaming the
        scroll-lock counter) originally failed as a normal mutation, which
        looked like good news but actually proved the assertion was matching
        the word ``count`` rather than the counting logic. The test was
        rewritten to inspect the increment / decrement / zero-check structure,
        and JH was reclassified as a control that must survive.
    """

    mode: str = "unique"
    """How many occurrences of ``old`` to replace.

    ``"unique"``
        ``old`` must match exactly once. Anything else is reported AMBIGUOUS
        rather than guessed at (see the note in ``run_batch``).
    ``"all"``
        Replace every occurrence. This is the right mode when the protection
        is applied at many call sites and the question is "does the suite
        notice if this protection disappears?" -- e.g. removing the audit call
        from all six document handlers, or stripping a permission guard from
        every route that uses it. Using ``"unique"`` there would be impossible
        (the anchor legitimately repeats), and silently mutating only the first
        occurrence would overstate the evidence: it proves one call site is
        covered while implying all of them are.
    """


@dataclass
class Batch:
    name: str
    tests: list[str]
    mutations: list[Mutation] = field(default_factory=list)


def _purge_pycache() -> None:
    """Remove every bytecode cache under the code and test trees.

    Cheap insurance. The subprocesses are already told not to write caches;
    this clears anything an *earlier* process left behind.
    """
    for root in ("app", "tests", "scripts"):
        base = REPO / root
        if not base.exists():
            continue
        for pycache in base.rglob("__pycache__"):
            shutil.rmtree(pycache, ignore_errors=True)


def _run_tests(tests: list[str], timeout: int) -> tuple[int, str]:
    env = dict(os.environ)
    # The single most important line in this file: no bytecode caches, ever.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO)
    proc = subprocess.run(
        [str(PY), "-B", "-m", "pytest", *tests,
         "-p", "no:randomly", "-q", "--tb=line", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=timeout, env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _verdict(mut: Mutation, code: int) -> str:
    """Translate a pytest exit code into a verdict, honouring ``expect``.

    For a control (``expect="survives"``) the polarity is inverted: the suite
    going red means the test is over-fitted to something the mutation renamed,
    which is a finding in its own right.
    """
    tests_failed = code != 0
    if mut.expect == "survives":
        return "CONTROL-OK" if not tests_failed else "OVERFIT"
    return "CAUGHT" if tests_failed else "ESCAPED"


def run_batch(batch: Batch, timeout: int = 1200) -> list[tuple[str, str, str]]:
    """Apply each mutation in turn; return (ident, verdict, evidence)."""
    results: list[tuple[str, str, str]] = []
    _purge_pycache()

    # A green baseline is required. Without it "CAUGHT" is meaningless -- the
    # tests would be failing regardless of the mutation.
    code, out = _run_tests(batch.tests, timeout)
    if code != 0:
        tail = "\n".join(out.splitlines()[-12:])
        raise SystemExit(
            f"BASELINE IS NOT GREEN for batch {batch.name!r}; mutation results "
            f"would be meaningless. Fix the suite first.\n{tail}"
        )

    for mut in batch.mutations:
        path = REPO / mut.path
        original = path.read_bytes()          # in memory: no stray .mutbak files
        try:
            text = original.decode()
            occurrences = text.count(mut.old)
            if occurrences == 0:
                results.append((mut.ident, "NO-OP",
                                f"anchor not found in {mut.path}"))
                continue
            if mut.mode == "all":
                path.write_text(text.replace(mut.old, mut.new))
                _purge_pycache()
                code, out = _run_tests(batch.tests, timeout)
                evidence = f"{occurrences} site(s) mutated"
                for line in out.splitlines():
                    low = line.lower()
                    if "assert" in low or "failed" in low:
                        evidence = f"{occurrences} sites | " + line.strip()[-84:]
                results.append((mut.ident, _verdict(mut, code), evidence))
                continue
            if occurrences > 1:
                # A non-unique anchor mutates the FIRST match, which may be a
                # completely different code path from the one the batch tests.
                # That produced a false ESCAPED for mutation IC on 2026-08-16
                # (a bare "status_code=404," matched tenant resolution instead
                # of the feature gate). Refuse to guess.
                results.append((mut.ident, "AMBIGUOUS",
                                f"anchor matches {occurrences}x in {mut.path}; "
                                f"make it unique"))
                continue
            path.write_text(text.replace(mut.old, mut.new, 1))
            _purge_pycache()
            code, out = _run_tests(batch.tests, timeout)
            evidence = ""
            for line in out.splitlines():
                low = line.lower()
                if "assert" in low or "failed" in low:
                    evidence = line.strip()[-100:]
            results.append((mut.ident, _verdict(mut, code), evidence))
        finally:
            path.write_bytes(original)
            # Bump mtime so any cache written by a stray process is invalid.
            now = time.time()
            os.utime(path, (now, now))
            if path.read_bytes() != original:      # pragma: no cover
                raise SystemExit(
                    f"FATAL: could not restore {mut.path}. The working tree is "
                    f"mutated. Restore it from git before doing anything else."
                )
            _purge_pycache()

    return results


# --------------------------------------------------------------------------
# Batches
# --------------------------------------------------------------------------
TENANCY = Batch(
    name="tenancy",
    tests=["tests/test_tenant_isolation_live.py", "tests/test_tenancy.py"],
    mutations=[
        Mutation(
            "IA", "feature gate always allows (the pre-fix state of the world)",
            "app/core/tenancy.py",
            '    flags = getattr(rt, "features", None) or {}\n'
            "    if feature not in flags:\n"
            "        return True\n"
            "    return bool(flags[feature])",
            "    return True",
        ),
        Mutation(
            "IB", "features never reach the request snapshot",
            "app/core/tenancy.py",
            'features=dict(getattr(row, "features", None) or {}),',
            "features={},",
        ),
        Mutation(
            # This is the same-size 404->403 edit that poisoned the old harness.
            # The anchor carries the detail line as well, because a bare
            # "status_code=404," also appears (more deeply indented) in the
            # tenant-resolution code around line 494 -- and a substring match
            # hits THAT one first, mutating a route nothing in this batch
            # exercises. The mutation then "escapes" for a reason that has
            # nothing to do with the protection under test. Anchors must be
            # unique, not merely present.
            "IC", "a disabled feature leaks its existence via 403 instead of 404",
            "app/core/tenancy.py",
            "                status_code=404,\n"
            "                detail=f\"The '{feature}' feature is not enabled for this portal.\",",
            "                status_code=403,\n"
            "                detail=f\"The '{feature}' feature is not enabled for this portal.\",",
        ),
        Mutation(
            "ID", "the OCR router loses its feature gate",
            "app/main.py",
            'app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"],\n'
            '                   dependencies=[Depends(require_tenant_feature("ai_ocr"))])',
            'app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"])',
        ),
        Mutation(
            "IE", "the chatbot router loses its feature gate",
            "app/main.py",
            'app.include_router(chatbot.router, prefix="/api/chatbot", tags=["DtcBot"],\n'
            '                   dependencies=[Depends(require_tenant_feature("ai_chatbot"))])',
            'app.include_router(chatbot.router, prefix="/api/chatbot", tags=["DtcBot"])',
        ),
        # -------------------------------------------------------------------
        # THE WRITE SIDE (added 2026-08-20 alongside section 5 of
        # tests/test_tenant_isolation_live.py).
        #
        # IA-IE all concern feature gating and reads. Nothing pinned the
        # direction a WRITE travels, which is the failure the operator's
        # "will not be shared or mixed with another agency" sentence is
        # actually about. A read leak is loud -- an office sees a name it does
        # not recognise. A write leak is silent: the API returns 201, the row
        # appears in the office's own list because the read leaks the same way,
        # and the row physically sits in Dogar's file until a subpoena, an
        # export or a restore moves it across a boundary that was never there.
        # -------------------------------------------------------------------
        Mutation(
            "IF", "client rows are written to the CONTROL database, not the tenant's",
            "app/api/endpoints/clients.py",
            "def create_client(payload: dict, request: Request, db: Session = Depends(get_tenant_db),",
            'def create_client(payload: dict, request: Request, db: Session = Depends(__import__("app.db.session", fromlist=["get_db"]).get_db),',
        ),
        Mutation(
            # Distinct from IF: the business row lands correctly and only the
            # AUDIT row leaks. That is the more plausible mistake of the two --
            # a service call handed a session it built itself rather than the
            # request's -- and it is invisible in every functional test,
            # because the feature under test still works perfectly.
            "IG", "the client audit row is written through a control-DB session",
            "app/api/endpoints/clients.py",
            "    audit_svc.log_event(\n"
            "        db, entity_type=AuditEntity.CLIENT.value, entity_id=obj.id,\n"
            "        action=AuditAction.CREATE.value, actor=user, request=request,",
            "    from app.db.session import SessionLocal as _SL\n"
            "    _leak = _SL()\n"
            "    audit_svc.log_event(\n"
            "        _leak, entity_type=AuditEntity.CLIENT.value, entity_id=obj.id,\n"
            "        action=AuditAction.CREATE.value, actor=user, request=request,",
        ),
        Mutation(
            # The defect that was actually FOUND by writing section 5: clients
            # were not audited at all. AuditEntity.CLIENT existed and no
            # endpoint ever emitted it. Removing the call reproduces that
            # state exactly, so a future refactor cannot quietly restore it.
            "IH", "client creation stops being audited at all (the found defect)",
            "app/api/endpoints/clients.py",
            "    audit_svc.log_event(\n"
            "        db, entity_type=AuditEntity.CLIENT.value, entity_id=obj.id,\n"
            "        action=AuditAction.CREATE.value, actor=user, request=request,\n"
            "        summary=f\"Created client '{obj.company_name}'\",\n"
            "        after=obj,\n"
            "    )\n",
            "",
        ),
    ],
)

AUDIT = Batch(
    name="audit",
    tests=["tests/test_audit_trail.py", "tests/test_rbac_enforcement.py"],
    mutations=[
        Mutation(
            # mode="all": the audit call appears at six handlers. Mutating one
            # would only prove that one handler is covered.
            "GA", "NO document handler writes an audit row any more",
            "app/api/endpoints/documents.py",
            "    log_document_action(", "    _mutant_skip_log(", mode="all",
        ),
        Mutation(
            # ANCHOR REPAIR 2026-08-18. This was anchored on
            #   "def audit_log(\n    db: Session = Depends(get_tenant_db),"
            # i.e. on `db` being the FIRST parameter. Adding `request: Request`
            # ahead of it (for the office-scope check) broke the match, and a
            # mutation whose anchor no longer matches is a silent NO-OP: the
            # batch would still have reported green while testing nothing.
            #
            # Re-anchored on the dependency binding alone, which is the actual
            # behaviour under test -- "the viewer reads the TENANT database" --
            # rather than on the parameter ORDER, which is incidental. mode="all"
            # because both audit routes must be pinned; catching one would leave
            # the metadata route free to leak another office's filter values.
            "GB", "the audit viewer reads the control DB instead of the tenant DB",
            "app/api/endpoints/users.py",
            "    db: Session = Depends(get_tenant_db),\n"
            '    admin: User = Depends(require_permission("audit:view")),',
            "    db: Session = Depends(get_db),\n"
            '    admin: User = Depends(require_permission("audit:view")),',
            mode="all",
        ),
        Mutation(
            "GC", "the audit viewer drops its permission guard",
            "app/api/endpoints/users.py",
            'admin: User = Depends(require_permission("audit:view")),\n'
            "    entity_type:",
            "admin: User = Depends(get_current_user),\n    entity_type:",
        ),
        Mutation(
            "GD", "the audit timestamp becomes client-supplied",
            "app/models/audit.py",
            "server_default=func.now()", "default=None", mode="all",
        ),
        Mutation(
            "GI", "a permission is enforced but never declared in the catalogue",
            "app/core/permissions.py",
            '{"key": "audit:view",         "label": "View the admin audit log"},',
            "",
        ),
    ],
)

GUARDS = Batch(
    name="guards",
    tests=["tests/test_document_route_guards.py"],
    mutations=[
        Mutation(
            "HA", "EVERY route drops the documents:edit guard",
            "app/api/endpoints/documents.py",
            'require_permission("documents:edit")',
            "get_current_user", mode="all",
        ),
        Mutation(
            "HB", "EVERY route drops the documents:print guard",
            "app/api/endpoints/documents.py",
            'require_permission("documents:print")',
            "get_current_user", mode="all",
        ),
        Mutation(
            "HC", "EVERY route drops the documents:designer guard",
            "app/api/endpoints/documents.py",
            'require_permission("documents:designer")',
            "get_current_user", mode="all",
        ),
    ],
)

DIALOGS = Batch(
    name="dialogs",
    tests=[
        "tests/test_dialog_usability_contract.py",
        "tests/test_fixed_overlay_containing_block.py",
    ],
    mutations=[
        Mutation(
            # The original 2026-08-13 outage, reintroduced at its root.
            "JA", "the shared Modal stops portalling to <body>",
            "app/frontend/src/components/ui.tsx",
            "  return createPortal(", "  return (",
        ),
        Mutation(
            "JB", "Escape handling is removed from the shared hook",
            "app/frontend/src/hooks/useDialogKeys.ts",
            "if (e.key === 'Escape') {", "if (false) {",
        ),
        Mutation(
            "JC", "the focus trap stops honouring Shift+Tab",
            "app/frontend/src/hooks/useDialogKeys.ts",
            "e.shiftKey", "false", mode="all",
        ),
        Mutation(
            "JD", "the background scroll lock is dropped",
            "app/frontend/src/hooks/useDialogKeys.ts",
            "lockBodyScroll()", "void 0", mode="all",
        ),
        Mutation(
            "JE", "focus is no longer restored to the opening element",
            "app/frontend/src/hooks/useDialogKeys.ts",
            "opener", "_unusedOpener", mode="all",
        ),
        Mutation(
            # A Topbar dialog loses the hook -- the exact defect found on
            # 2026-08-16, where three dialogs claimed aria-modal="true" while
            # trapping nothing.
            "JF", "a Topbar dialog drops the keyboard hook",
            "app/frontend/src/layout/Topbar.tsx",
            "useDialogKeys", "useNoDialogKeys", mode="all",
        ),
        Mutation(
            "JG", "a dialog keeps role=dialog but loses aria-modal",
            "app/frontend/src/pages/ClientDetail.tsx",
            'aria-modal="true"', "", mode="all",
        ),
        Mutation(
            # Renaming the counter must NOT be enough to fail the test -- if it
            # is, the test is pinning an identifier rather than a behaviour.
            # This mutation escaped on the first run for exactly that reason
            # and the test was rewritten to check the structure instead.
            "JH", "the scroll-lock counter is renamed (control: must survive)",
            "app/frontend/src/lib/scrollLock.ts",
            "count", "depth", mode="all", expect="survives",
        ),
        Mutation(
            "JI", "unlock restores body overflow without checking the counter",
            "app/frontend/src/lib/scrollLock.ts",
            "  count = Math.max(0, count - 1)\n  if (count === 0) {",
            "  count = Math.max(0, count - 1)\n  if (true) {",
        ),
        Mutation(
            "JJ", "lock stops counting, so two dialogs fight over the lock",
            "app/frontend/src/lib/scrollLock.ts",
            "  count += 1", "",
        ),
    ],
)

GLASS = Batch(
    name="glass",
    # The glass layer must not be able to reintroduce the containing-block
    # outage, so the dialog/overlay guards run alongside its own suite: a
    # mutation that blurs the page wrapper should trip BOTH.
    tests=[
        "tests/test_glass_design_system.py",
        "tests/test_fixed_overlay_containing_block.py",
    ],
    mutations=[
        Mutation(
            # THE outage, reintroduced through the new design system instead of
            # through `.page-enter`. `.spa-main-content` is the routed content
            # wrapper: blurring it makes every non-portalled `fixed inset-0`
            # overlay resolve against it and get clipped.
            "KA", "the routed content wrapper is given a backdrop blur",
            "app/static/css/app.css",
            ".data-table { background: transparent; }",
            ".spa-main-content { backdrop-filter: blur(4px);\n"
            "  -webkit-backdrop-filter: blur(4px); }",
        ),
        Mutation(
            "KB", "the ambient canvas is switched from a background to a filter",
            "app/static/css/app.css",
            "  background-attachment: fixed;\n  background-repeat: no-repeat;",
            "  background-attachment: fixed;\n  backdrop-filter: blur(2px);",
        ),
        Mutation(
            "KC", "a containing-block property creeps into the glass layer",
            "app/static/css/app.css",
            "  border-radius: var(--radius-lg);\n"
            "  transition: box-shadow 220ms var(--ease-out),",
            "  border-radius: var(--radius-lg);\n  will-change: transform;\n"
            "  transition: box-shadow 220ms var(--ease-out),",
        ),
        Mutation(
            # Losing the -webkit- prefix is invisible on desktop Chrome and
            # breaks the entire look on iPhone, so it must be caught here
            # rather than by a user with an iPad.
            "KD", "a glass surface loses its -webkit-backdrop-filter partner",
            "app/static/css/app.css",
            "  -webkit-backdrop-filter: var(--blur-lg);\n"
            "  backdrop-filter: var(--blur-lg);\n"
            "  border: 1px solid var(--glass-rim);",
            "  backdrop-filter: var(--blur-lg);\n"
            "  border: 1px solid var(--glass-rim);",
        ),
        Mutation(
            "KE", "the reduced-transparency fallback stops disabling the blur",
            "app/static/css/app.css",
            "    -webkit-backdrop-filter: none !important;\n"
            "    backdrop-filter: none !important;",
            "    -webkit-backdrop-filter: var(--blur-md);\n"
            "    backdrop-filter: var(--blur-md);",
        ),
        Mutation(
            "KF", "the print path stops forcing opaque surfaces",
            "app/static/css/app.css",
            "    backdrop-filter: none !important;\n"
            "    -webkit-backdrop-filter: none !important;\n"
            "    box-shadow: none !important;",
            "    box-shadow: none !important;",
        ),
        Mutation(
            # The anchor deliberately stops at `?v=` and does NOT name a
            # version. It used to read 'app.css?v=23.0' -> 'app.css?v=22.0',
            # which went silently NO-OP the moment the bust was bumped to
            # v24.0 for the canvas retune: the mutation reported "anchor not
            # found" and the cache-bust protection sat UNPROVEN while the
            # batch still looked healthy. Rewriting to a version-free anchor
            # makes the mutation survive every future bump -- replacing `?v=`
            # with `?stale=` strips the cache buster whatever its number is.
            "KG", "the stylesheet changes but the cache bust is left behind",
            "app/frontend/index.html",
            'app.css?v=', "app.css?stale=",
        ),
        Mutation(
            "KH", "the glass layer hides a control (looks like a broken permission)",
            "app/static/css/app.css",
            ".pg-btn {\n  background: var(--glass-2);",
            ".pg-btn {\n  display: none;\n  background: var(--glass-2);",
        ),
        Mutation(
            "KI", "the accent stops reading the brand token",
            "app/static/css/app.css",
            "  --accent-wash:    color-mix(in srgb, var(--brand) 9%, transparent);\n"
            "  --accent-wash-2:  color-mix(in srgb, var(--brand) 16%, transparent);",
            "  --accent-wash:    rgba(29, 78, 216, 0.09);\n"
            "  --accent-wash-2:  rgba(29, 78, 216, 0.16);",
        ),
        Mutation(
            # CONTROL. Retuning a colour is a design decision and must NOT fail
            # the suite -- if it does, these tests pin taste instead of safety
            # and every future visual tweak would be blocked by a red build.
            "KJ", "a glass alpha value is retuned (control: must survive)",
            "app/static/css/app.css",
            "  --glass-2:        rgba(255, 255, 255, 0.72);",
            "  --glass-2:        rgba(255, 255, 255, 0.66);",
            expect="survives",
        ),
        # -------------------------------------------------------------------
        # The 2026-08-17 containing-block finding. The old guard banned only
        # `both`, so `forwards` -- which is equally fatal, because it also
        # retains the final keyframe -- sailed through for four days. These
        # three mutations exist to prove the hardened guard actually fails on
        # every shape of that bug, not just the one historical spelling.
        # -------------------------------------------------------------------
        Mutation(
            # The exact regression that was live in production until today.
            "KK", "the retained fill mode `forwards` comes back on .page-enter",
            "app/frontend/src/index.css",
            "  animation: page-enter 140ms cubic-bezier(0.16, 1, 0.3, 1);",
            "  animation: page-enter 140ms cubic-bezier(0.16, 1, 0.3, 1) forwards;",
        ),
        Mutation(
            # Same trap wearing the longhand, which a shorthand-only regex misses.
            "KL", "a fill mode is reintroduced via the longhand property",
            "app/frontend/src/index.css",
            "  animation: page-enter 140ms cubic-bezier(0.16, 1, 0.3, 1);",
            "  animation: page-enter 140ms cubic-bezier(0.16, 1, 0.3, 1);\n"
            "  animation-fill-mode: forwards;",
        ),
        Mutation(
            # The original outage spelling, kept so the older assertion stays proven.
            "KM", "the original `both` fill mode comes back on .page-enter",
            "app/frontend/src/index.css",
            "  animation: page-enter 140ms cubic-bezier(0.16, 1, 0.3, 1);",
            "  animation: page-enter 140ms cubic-bezier(0.16, 1, 0.3, 1) both;",
        ),
        Mutation(
            # CONTROL. The 4px rise is polish, and animating `translate` with no
            # fill mode is provably safe (measured: transform AND translate both
            # revert to the keyword `none`). Retiming the animation must NOT fail
            # the suite, or the guard is pinning taste rather than the invariant.
            "KN", "the page transition is retimed (control: must survive)",
            "app/frontend/src/index.css",
            "  animation: page-enter 140ms cubic-bezier(0.16, 1, 0.3, 1);",
            "  animation: page-enter 180ms cubic-bezier(0.16, 1, 0.3, 1);",
            expect="survives",
        ),

        # -------------------------------------------------------------------
        # CASCADE REACHABILITY (2026-08-17). A CSS-only design layer can be
        # entirely correct inside app.css and still render nothing, in two ways
        # that every app.css assertion is blind to. Both of these SHIPPED, and
        # both were found by pixel / contrast measurement rather than by the
        # test suite -- which is the reason these mutations now exist:
        #
        #   KO: a background utility on <body> outranks the app.css `body`
        #       selector, because app.css is linked BEFORE the compiled
        #       Tailwind bundle and both are single-class specificity. This
        #       pinned the canvas at rgb(248,250,252); the measured
        #       card-vs-canvas delta was 19.2, and 26.8 once removed.
        #   KP: a page builds its panels out of utilities and never opts into
        #       `.card`, so the layer cannot reach it at all. The Dashboard
        #       measured 0 glass surfaces this way, while five other pages
        #       measured 4-5 -- and it still reported PASS overall.
        #   KQ: the subtler half of KP -- the glass class IS present, but a
        #       clashing utility sits beside it and wins on source order.
        #
        # NOTE ON KO: the first version of its guard was ITSELF vacuous. It
        # regex-matched `<body ...>` against raw HTML and hit the prose inside
        # an explanatory comment, which carries no class attribute, so this
        # exact mutation escaped with all 18 tests green. The helper now
        # strips comments and asserts the class attribute is present. KO is
        # kept permanently so that regression can never recur silently.
        # -------------------------------------------------------------------
        Mutation(
            "KO", "a background utility on <body> outranks the glass canvas",
            "app/frontend/index.html",
            '<body class="text-slate-800',
            '<body class="bg-slate-50 text-slate-800',
        ),
        Mutation(
            # mode="all": `card p-5` is legitimately repeated across the
            # Dashboard's panels, so "unique" would report AMBIGUOUS. Mutating
            # every site is also the stronger claim -- it shows the guard
            # notices the opaque-panel regression wherever it appears, not
            # merely at the first occurrence.
            "KP", "a Dashboard panel is rebuilt from opaque utilities",
            "app/frontend/src/pages/Dashboard.tsx",
            '<div className="card p-5">',
            '<div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">',
            mode="all",
        ),
        Mutation(
            "KQ", "an opaque utility sits beside .card and wins on source order",
            "app/frontend/src/pages/Dashboard.tsx",
            '<div className="card p-5">',
            '<div className="card bg-white shadow-sm p-5">',
            mode="all",
        ),
        Mutation(
            # CONTROL. Padding is taste, not an invariant. If tightening a
            # card's padding fails the suite, the guard is over-fitted to the
            # current visual design rather than to glass reachability.
            "KR", "a card's padding is adjusted (control: must survive)",
            "app/frontend/src/pages/Dashboard.tsx",
            '<div className="card p-5">',
            '<div className="card p-6">',
            expect="survives",
            mode="all",
        ),
        # ---- the <select> caret (2026-08-19) --------------------------------
        # `.form-select` draws its dropdown arrow as an inline-SVG
        # background-image. A later rule using the `background` SHORTHAND resets
        # background-image and erases that arrow on EVERY select in the portal
        # -- measured in Chromium as backgroundImage:"none" with
        # appearance:none and 36px of padding held for an arrow that never
        # paints. Nothing errors; the control simply becomes indistinguishable
        # from a text input, so the only way to discover it is a dropdown is to
        # click it. Both halves (the shorthand, and the caret itself) are
        # pinned separately.
        Mutation(
            "KS", "the glass layer erases the <select> caret again",
            "app/static/css/app.css",
            "  background-color: var(--glass-1);\n  -webkit-backdrop-filter",
            "  background: var(--glass-1);\n  -webkit-backdrop-filter",
        ),
        Mutation(
            "KT", "the caret's reserved padding is dropped",
            "app/static/css/app.css",
            "  padding-right: 2.25rem;\n  appearance: none;",
            "  appearance: none;",
        ),
        Mutation(
            # Control: the exact cache-bust NUMBER is not the contract, only
            # that it moved past the fix. Bumping it FURTHER must stay green,
            # or the test is pinned to a version string instead of to the
            # invariant "returning browsers cannot serve the broken stylesheet".
            "KU", "the cache-bust is bumped further (control: must survive)",
            "app/frontend/index.html",
            "app.css?v=24.1",
            "app.css?v=25.0",
            expect="survives",
        ),
    ],
)

GEOMETRY = Batch(
    name="geometry",
    # ---------------------------------------------------------------------
    # WHY THIS BATCH EXISTS (2026-08-17)
    #
    # test_the_snapshot_is_not_stale_relative_to_its_source_database began
    # SKIPPING after a provenance gate was added to it. The gate is correct --
    # the local untracked data/ DB had been written during the test session,
    # and regenerating the shipped snapshot to force the test green would have
    # baked real page movement (OEP Form field 19 y 735.11 -> 684.8, field 20
    # y 719.28 -> 822.6, field 20 x 156.58 -> 128.9, plus font_bold flips)
    # into the geometry that prints embassy and bank paperwork.
    #
    # But a skip is also exactly what a DISABLED test looks like, and the
    # difference was originally established only by a one-off manual mutation
    # (backdating the DB and corrupting a Karachi coordinate to 999.9). That
    # proof evaporates the moment anyone edits the gate. These mutations make
    # it permanent: they attack the gate's discrimination and the staleness
    # detector it gates, in both directions.
    # ---------------------------------------------------------------------
    tests=["tests/test_fresh_install_geometry.py"],
    mutations=[
        Mutation(
            # The gate widened into an unconditional escape hatch: every run
            # skips, the staleness check never executes anywhere, and the
            # suite still reports green. This is the failure mode the batch
            # exists to make impossible.
            "LA", "the provenance gate always claims the DB has drifted",
            "tests/test_fresh_install_geometry.py",
            "    if snapshot_committed is None:\n        return False\n"
            "    return os.path.getmtime(db_path) > snapshot_committed",
            "    if snapshot_committed is None:\n        return True\n"
            "    return True",
        ),
        Mutation(
            # The opposite widening: the gate never fires, so a locally
            # mutated DB is again treated as the authority over the shipped
            # snapshot -- which is what would have corrupted the coordinates.
            "LB", "the provenance gate never fires, so local DB drift wins",
            "tests/test_fresh_install_geometry.py",
            "    return os.path.getmtime(db_path) > snapshot_committed",
            "    return False",
        ),
        Mutation(
            # Fails OPEN with no git clock: a tarball install would skip the
            # geometry staleness check entirely instead of enforcing it.
            "LC", "with no git clock the gate skips instead of checking",
            "tests/test_fresh_install_geometry.py",
            "    if snapshot_committed is None:\n        return False",
            "    if snapshot_committed is None:\n        return True",
        ),
        Mutation(
            # Attacks the DETECTOR rather than the gate. If --check always
            # exits 0, the gate would be hiding a dead check and no geometry
            # drift would ever be reported again.
            "LD", "the staleness detector accepts a drifted database",
            "scripts/export_template_geometry.py",
            "        if current != rendered:",
            "        if False and current != rendered:",
        ),
        Mutation(
            # Rounding is the exporter's defence against false precision from
            # a 300dpi raster (0.24pt/px). Dropping it to whole points is
            # coarse enough to erase sub-point measurement differences.
            "LE", "coordinate precision is coarsened to whole points",
            "scripts/export_template_geometry.py",
            "COORD_PRECISION = 2",
            "COORD_PRECISION = 0",
        ),
        Mutation(
            # CONTROL. The gate's help text is prose. If rewording the skip
            # reason turns the suite red, the tests are pinning a string
            # rather than the provenance logic.
            "LF", "the skip message is reworded (control: must survive)",
            "tests/test_fresh_install_geometry.py",
            "and reviewing the diff.",
            "and reviewing the resulting diff carefully.",
            expect="survives",
        ),
        Mutation(
            # The unseeded-shell guard, found while verifying the release ZIP
            # (2026-08-18). The archive ships no data/ (it holds real PII), but
            # importing the app in an extracted tree CREATES an empty
            # data/dogar_trading.db with zero tables. Without this guard the
            # exporter dies with `no such table: document_templates` and the
            # failure reads as "the shipped geometry snapshot is broken", which
            # it is not. Treating an unseeded shell as the geometry authority
            # must stay impossible.
            # The anchor is the helper's rejection branch, NOT the call site.
            # An earlier version anchored on `if not has_templates:` inside the
            # test body; refactoring the guard into _db_is_a_seeded_control
            # deleted that line and the mutation went silently NO-OP -- the
            # same decay that hit KG. Anchoring on the returned reason keeps it
            # tied to the behaviour rather than to one call site's phrasing.
            "LG", "an unseeded database shell is accepted as the geometry authority",
            "tests/test_fresh_install_geometry.py",
            "    if row is None:\n        return False, (",
            "    if False and row is None:\n        return False, (",
        ),
    ],
)

CONTRACT = Batch(
    name="contract",
    # ---------------------------------------------------------------------
    # WHY THIS BATCH EXISTS (2026-08-20)
    #
    # The GEOMETRY batch above attacks the snapshot/provenance machinery --
    # "has the shipped geometry drifted from the database?". It says nothing
    # about the defect the operator actually named: "mismatch in documents
    # coordinate engine".
    #
    # That mismatch is a CROSS-LANGUAGE one. Three independent engines must
    # agree on where a glyph lands:
    #
    #   designer canvas   app/frontend/src/pages/designer/engine.ts   (TS, y from TOP)
    #   HTML print view   app/api/endpoints/documents.py              (Python, CSS top)
    #   ReportLab PDF     app/services/pdf_engine.py                 (Python, y from BOTTOM)
    #
    # app/core/geometry_contract.py was made the single authority and
    # tests/test_geometry_contract.py (14 tests) asserts the TS mirror matches
    # it. But those 14 tests had never been proven capable of FAILING. A
    # cross-language guard is precisely the kind that rots invisibly: it reads
    # the other language as TEXT, so any change to formatting, naming or
    # comment style can turn the extraction into a no-op that still passes.
    # An always-true comparison here is indistinguishable from a real proof,
    # and the cost of being wrong is a field printed in the wrong place on a
    # visa application or a bank letter.
    #
    # Each mutation below breaks ONE side of ONE agreement, in the direction a
    # careless edit actually takes: change the constant in one language and
    # forget the other.
    # ---------------------------------------------------------------------
    tests=["tests/test_geometry_contract.py"],
    mutations=[
        Mutation(
            # The headline number. 0.8 -> 0.75 moves every text baseline by
            # 5% of the font size: about 0.6pt at 12pt, which is invisible on
            # screen and lands a field outside a printed box on a pre-printed
            # government form. Mutating the TS side ONLY is the realistic
            # mistake -- a frontend dev tuning the canvas by eye.
            "MA", "the TypeScript ascent ratio drifts from the Python authority",
            "app/frontend/src/pages/designer/engine.ts",
            "export const ASCENT_RATIO = 0.8",
            "export const ASCENT_RATIO = 0.75",
        ),
        Mutation(
            # The same disagreement introduced from the OTHER side. Both
            # directions are registered because a guard that only reads one
            # file and compares it to a hard-coded literal would catch one and
            # sail through the other.
            "MB", "the Python ascent authority drifts from the TypeScript mirror",
            "app/core/geometry_contract.py",
            "ASCENT_RATIO: float = 0.8",
            "ASCENT_RATIO: float = 0.75",
        ),
        Mutation(
            # pt -> css scale. A 96/72 "fix" is the single most tempting wrong
            # edit in this file: it is the correct CSS px-per-inch ratio in
            # general, and it is WRONG here because the canvas is deliberately
            # 1pt == 1px at zoom 1.0. It would scale every coordinate by 1.333.
            "MC", "the canvas point-to-pixel scale is 'corrected' to 96/72",
            "app/frontend/src/pages/designer/engine.ts",
            "export const PT_TO_CSS = 1",
            "export const PT_TO_CSS = 96 / 72",
        ),
        Mutation(
            # The height fallback. A field with NULL/0 height still needs a
            # baseline, and all three engines must derive it identically or
            # the baseline drifts by (h - font_size) -- a per-field error that
            # only shows up on the templates that leave height unset.
            "MD", "the TS height fallback multiple drifts from the authority",
            "app/frontend/src/pages/designer/engine.ts",
            "1.25",
            "1.4",
            mode="all",
        ),
        Mutation(
            "ME", "the Python height fallback minimum drifts from the TS mirror",
            "app/core/geometry_contract.py",
            "HEIGHT_FALLBACK_MINIMUM: float = 14.0",
            "HEIGHT_FALLBACK_MINIMUM: float = 12.0",
        ),
        Mutation(
            # The barcode fallback must stay DISTINCT from the text fallback.
            # Collapsing them is a plausible "simplification" that silently
            # shrinks every barcode with an unset height.
            "MF", "the barcode height fallback collapses onto the text fallback",
            "app/core/geometry_contract.py",
            "BARCODE_HEIGHT_FALLBACK: float = 32.0",
            "BARCODE_HEIGHT_FALLBACK: float = 14.0",
        ),
        Mutation(
            # The baseline formula itself. Dropping the max() clamp lets a box
            # SHORTER than its font size produce a negative offset, pulling
            # the baseline below the box instead of pinning it to the top.
            "MG", "the baseline offset loses its zero clamp",
            "app/core/geometry_contract.py",
            "    return float(y) + max(float(height) - float(font_size), 0.0)",
            "    return float(y) + (float(height) - float(font_size))",
        ),
        Mutation(
            # Sign error on the baseline: the classic PDF-vs-CSS mistake, since
            # PDF y counts from the BOTTOM and CSS top from the TOP. Anchors
            # the round-trip test rather than any single engine.
            "MH", "the baseline is measured from the box bottom instead of the top",
            "app/core/geometry_contract.py",
            "    return float(y) + max(float(height) - float(font_size), 0.0)",
            "    return float(y)",
        ),
        Mutation(
            # The comment stripper is what lets a reversion gate tell CODE from
            # PROSE. Several fixes in this codebase deliberately quote the old
            # buggy expression in a comment, so a stripper that eats real code
            # -- or one that eats nothing -- silently changes what every
            # source-reading assertion in this file is looking at.
            #
            # This mutation makes it strip EVERYTHING after the first '#'
            # regardless of position, so a line like
            #   ASCENT_RATIO: float = 0.8  # matches engine.ts
            # survives, but any line containing a '#' inside a string is
            # truncated. test_the_comment_stripper_still_sees_real_code exists
            # precisely to notice that, and this proves it does.
            #
            # (The first version of this mutation attacked a `_BLOCK_COMMENT`
            # regex for TypeScript block comments. No such regex exists -- the
            # stripper is Python-line-oriented -- so the anchor was not found
            # and the batch reported NO-OP: a mutation counted in the total
            # while exercising nothing. The same anchor decay that hit BO and
            # LG. Verified present before re-registering.)
            "MI", "the comment stripper discards real code, not just prose",
            "tests/test_geometry_contract.py",
            '        if stripped.startswith("#"):\n            continue\n',
            "        if True:\n            continue\n",
        ),
    ],
)

PACKAGING = Batch(
    name="packaging",
    # ---------------------------------------------------------------------
    # WHY THIS BATCH EXISTS (2026-08-18)
    #
    # test_env_is_gitignored_and_untracked failed inside the extracted release
    # ZIP with ".env is NOT matched by .gitignore" -- a false alarm about
    # committable secrets. The archive correctly ships no .git, so
    # `git check-ignore` returned 128 ("fatal: not a git repository") and the
    # assertion read rc != 0 as "not ignored".
    #
    # The second half of that test was the more dangerous one: `git ls-files
    # --error-unmatch .env` ALSO exits 128 without a repository, and the test
    # asserted `rc != 0` meaning "untracked". So in every packaged build that
    # half PASSED while proving nothing whatsoever. A green assertion that
    # cannot fail is worse than a red one.
    #
    # Both halves are now gated on an explicit repository probe, with a
    # git-free fallback that reads .gitignore directly so the protection stays
    # positively verified inside a package rather than skipped. These mutations
    # exist because that fallback is only reachable when git is absent, i.e.
    # never during a normal repo run -- exactly the condition under which a
    # protection rots unnoticed.
    # ---------------------------------------------------------------------
    tests=["tests/test_no_committed_secrets.py"],
    mutations=[
        Mutation(
            # If the probe cannot say "no", the packaged-build branch is dead
            # code and the original false alarm returns.
            "MA", "the git-availability probe always claims git is usable",
            "tests/test_no_committed_secrets.py",
            "    if probe.returncode != 0:\n        return False, (",
            "    if False and probe.returncode != 0:\n        return False, (",
        ),
        Mutation(
            # The inverse: if the probe always says "no", the repo-side
            # check-ignore/ls-files assertions never run at all.
            "MB", "the git-availability probe always claims git is unusable",
            "tests/test_no_committed_secrets.py",
            '    if probe.stdout.strip() != "true":',
            '    if True or probe.stdout.strip() != "true":',
        ),
        Mutation(
            # The fallback's whole job is discrimination. If it accepts any
            # ignore file at all, a tree whose real secrets are committable
            # gets rubber-stamped inside every packaged build.
            "MC", "the gitignore reader accepts any rule as protecting .env",
            "tests/test_no_committed_secrets.py",
            '        if line in {".env", "/.env", "*.env", ".env*", "/.env*", ".env.*"}:',
            "        if line:",
        ),
        Mutation(
            # A missing .gitignore must never read as protective.
            "MD", "a missing gitignore is treated as protective",
            "tests/test_no_committed_secrets.py",
            "    if not gitignore.is_file():\n        return False",
            "    if not gitignore.is_file():\n        return True",
        ),
        Mutation(
            # Control: the fallback's explanatory wording is not the contract.
            # Rewording it must NOT turn the batch red, otherwise the tests are
            # pinned to prose instead of behaviour.
            "ME", "the fallback assertion message is reworded (control: must survive)",
            "tests/test_no_committed_secrets.py",
            '            "committable in any checkout made from this tree."',
            '            "committable in any clone taken from this directory."',
            expect="survives",
        ),
    ],
)

DENIALS = Batch(
    name="denials",
    tests=["tests/test_audit_denials.py", "tests/test_audit_doc_category.py"],
    mutations=[
        Mutation(
            # mode="all": BOTH guards must be pinned. The route-level guard and
            # the field-level one are separate code paths, and auditing only the
            # coarse one produces a trail whose completeness silently depends on
            # WHICH guard refused -- worse than no auditing, because it looks
            # complete.
            "NA", "NO permission guard records its denial any more",
            "app/core/permissions.py",
            "_audit_denial(db, user,", "_mutant_skip_audit(db, user,",
            mode="all",
        ),
        Mutation(
            "NB", "denials are written but labelled as successes",
            "app/services/audit.py",
            "            outcome=DENIED,\n            permission=permission,",
            "            outcome=SUCCESS,\n            permission=permission,",
        ),
        Mutation(
            "NC", "the refused permission key is not recorded on the row",
            "app/services/audit.py",
            "permission=(str(permission)[:80] if permission else None),",
            "permission=None,",
        ),
        Mutation(
            "ND", "the outcome filter is accepted but ignored",
            "app/api/endpoints/users.py",
            '    if outcome and outcome.lower() not in ("all", ""):\n'
            "        query = query.filter(AuditLog.outcome == outcome.lower())",
            '    if False and outcome:\n        query = query.filter(AuditLog.outcome == outcome)',
        ),
        Mutation(
            "NE", 'the "all" sentinel is matched as a literal outcome value',
            "app/api/endpoints/users.py",
            '    if outcome and outcome.lower() not in ("all", ""):',
            "    if outcome:",
        ),
        Mutation(
            "NF", "the listing stops reporting each row's outcome",
            "app/api/endpoints/users.py",
            '                "outcome": r.outcome or "success",',
            '                "outcome_hidden": r.outcome or "success",',
        ),
        Mutation(
            "NG", "free-text search narrows back to the summary column only",
            "app/api/endpoints/users.py",
            "            or_(\n                AuditLog.summary.ilike(needle),\n"
            "                AuditLog.actor_name.ilike(needle),\n"
            "                AuditLog.permission.ilike(needle),\n"
            "                AuditLog.entity_type.ilike(needle),\n            )",
            "            AuditLog.summary.ilike(needle)",
        ),
        Mutation(
            "NH", 'the filter metadata derives "denied" from the data, hiding it',
            "app/api/endpoints/users.py",
            '        "outcomes": ["success", "denied"],',
            '        "outcomes": sorted({r[0] for r in db.query(AuditLog.outcome).distinct().all() if r[0]}),',
        ),
        Mutation(
            "NI", "a failing audit write is allowed to break the 403",
            "app/core/permissions.py",
            "            except Exception as exc:  # noqa: BLE001 -- never break the 403\n"
            '                log.warning("denial audit raised past its own guard: %s", exc)',
            "            except ZeroDivisionError as exc:\n"
            '                log.warning("denial audit raised past its own guard: %s", exc)',
        ),
        Mutation(
            # Control: the denial summary's exact prose is not the contract.
            # Rewording must NOT turn the batch red, or the tests are pinned to
            # wording instead of to the recorded facts.
            "NJ", "the denial summary's leading verb changes (control: must survive)",
            "app/services/audit.py",
            '    summary = f"DENIED {permission}"',
            '    summary = f"REFUSED-ACCESS {permission}"',
            expect="survives",
        ),
        # ---- document TYPE (2026-08-19) -------------------------------------
        # The column existed, the filter parameter was accepted, and the live
        # dropdown was EMPTY. That combination is the worst kind of half-done
        # feature: nothing 500s, nothing looks broken, and the operator simply
        # cannot filter by document type. These pin the two halves separately.
        Mutation(
            "NK", "document actions stop recording the document type",
            "app/services/audit.py",
            "            doc_category=category or None,",
            "            doc_category=None,",
        ),
        Mutation(
            "NL", "the doc_category filter is accepted but ignored",
            "app/api/endpoints/users.py",
            "        query = query.filter(AuditLog.doc_category == doc_category)",
            "        pass  # mutant: filter dropped",
        ),
        Mutation(
            "NM", "an empty category is invented instead of NULL",
            # "" is not a harmless synonym for NULL here: "" is the ENDPOINT'S
            # OWN "no constraint" sentinel, so a row stored as "" can never be
            # selected by the document-type filter it is supposed to be
            # filterable on, and `doc_category IS NULL` stops finding every
            # event that genuinely has no document type. Two encodings of one
            # fact in one indexed column.
            #
            # WHY THIS ANCHOR AND NOT THE CALL SITE (measured 2026-08-19).
            # This mutation first pointed at log_document_action's
            # `doc_category=category or None` and ESCAPED -- correctly, because
            # `log_event` normalises again on the way into the row
            # (`if doc_category else None`). The two are deliberate
            # defence-in-depth, so mutating EITHER one alone is a genuine no-op
            # in behaviour and no test can legitimately fail. Verified by hand:
            # mutating both together fails 3 tests; mutating either alone fails
            # 0. The contract is "the column never stores a blank", and the
            # last writer before the column is where that contract lives, so
            # the anchor sits on log_event's normalisation -- behaviour, not a
            # call site.
            "app/services/audit.py",
            "        doc_category=(str(doc_category)[:60] if doc_category else None),",
            '        doc_category=(str(doc_category)[:60] if doc_category is not None else ""),',
        ),
        Mutation(
            "NN", "the listing stops reporting each row's document type",
            "app/api/endpoints/users.py",
            '                "doc_category": r.doc_category,',
            '                "doc_category_hidden": r.doc_category,',
        ),
    ],
)


# ---------------------------------------------------------------------------
# SCHEMA -- the two install routes, tenant drift, and log muting
# ---------------------------------------------------------------------------
# WHY THIS BATCH EXISTS (2026-08-19)
#
# Three defects were fixed in this area, and all three shared one property:
# they were INVISIBLE. Nothing raised, nothing 500'd on the default office, and
# every existing test stayed green.
#
#   1. Two schema-install routes (alembic vs ORM metadata diff) produced
#      DIFFERENT schemas. A fresh install shipped without any of the six perf
#      indexes -- measured -89%/-77%/-63% wins -- because `alembic stamp head`
#      marks migrations applied WITHOUT running their bodies.
#   2. Tenant databases were silently left behind by the boot-time migration,
#      so an office's Audit Log page 500'd on `no such column:
#      audit_logs.doc_category` while the default office worked perfectly.
#   3. alembic's in-process `fileConfig()` rebuilt the ROOT logger from
#      alembic.ini ([logger_root] level = WARN), demoting the app from INFO to
#      WARNING and replacing the JSON handler -- for the whole process.
#
# A test suite that stays green through all three is not protecting anything.
# These mutations exist to prove the new tests would go red.
SCHEMA = Batch(
    name="schema",
    tests=[
        "tests/test_schema_convergence.py",
        "tests/test_tenant_schema_check.py",
        "tests/test_logging_not_muted.py",
    ],
    mutations=[
        Mutation(
            "SA", "the ORM install route stops creating missing indexes",
            # This is defect 1 restored: alembic-built DBs get the index, ORM
            # -built ones do not, and both routes are permanently load-bearing
            # (measured: `alembic upgrade head` on an unstamped legacy tenant
            # DB fails outright with "table agents already exists").
            "scripts/apply_tenant_migrations.py",
            "                    index.create(bind=conn)",
            "                    pass  # mutant: index never created",
        ),
        Mutation(
            "SB", "a model index goes back to an auto-generated name",
            # index=True auto-names ix_<table>_<column>, which does NOT match
            # the composite name the migration creates -- so the reconciler,
            # which matches BY NAME, adds a permanent duplicate every deploy.
            "app/models/candidate.py",
            "    latest_demand_id = Column(Integer, nullable=True)",
            "    latest_demand_id = Column(Integer, nullable=True, index=True)",
        ),
        Mutation(
            "SC", "the perf indexes are declared on the migration only again",
            # Restores the exact condition in which every brand-new deployment
            # shipped into the un-optimised schema, forever and silently.
            "app/models/reminders.py",
            'Index("ix_reminders_created_at", text("created_at DESC"))',
            'Index("ix_reminders_created_at_mutant", text("created_at DESC"))',
        ),
        Mutation(
            "SD", "tenant schema drift is detected but not reported",
            "app/db/tenant_schema_check.py",
            "            behind.append((slug, rev))",
            "            pass  # mutant: drift swallowed",
        ),
        Mutation(
            "SE", "the drift check compares nothing, so every fleet looks current",
            "app/db/tenant_schema_check.py",
            "        if rev != head:",
            "        if False:",
        ),
        Mutation(
            "SF", "inspecting a tenant CREATES the database file it was checking",
            # sqlite happily creates an empty shell on connect. A diagnostic
            # that mutates what it inspects leaves a schema-less DB behind that
            # then looks like a real tenant at revision None.
            "app/db/tenant_schema_check.py",
            "            if not Path(url.split(\"///\", 1)[-1]).exists():\n                continue",
            "            if False:\n                continue",
        ),
        Mutation(
            "SG", "alembic is allowed to mute the application's root logger",
            "app/db/init_db.py",
            "        _restore_root_logging(saved_level, saved_handlers)",
            "        pass  # mutant: logging left as alembic reconfigured it",
        ),
        Mutation(
            "SH", "provisioning an office mutes the live portal",
            # command.stamp() loads env.py exactly as command.upgrade() does, so
            # the SAME muting happens inside the running web app when an
            # operator provisions an office from the admin UI.
            "app/services/tenant_service.py",
            "        _restore_root_logging(saved_level, saved_handlers)",
            "        pass  # mutant: portal left muted after provisioning",
        ),
        Mutation(
            "SI", "logging is restored only on success, not after a failure",
            # A FAILED migration reconfigures logging just as thoroughly as a
            # successful one -- and that is precisely when the operator most
            # needs to be able to read the logs.
            "app/db/init_db.py",
            "    finally:\n        # Restore in `finally`",
            "    else:\n        # Restore in `finally`",
        ),
        Mutation(
            # Control: the WORDING of the drift warning is not the contract.
            # The tests must assert on the facts named (slug, revision, head,
            # fix command), not on the sentence around them.
            "SJ", "the drift warning is reworded (control: must survive)",
            "app/db/tenant_schema_check.py",
            '"TENANT SCHEMA DRIFT: %d tenant database(s) are behind',
            '"TENANT SCHEMA LAG DETECTED: %d tenant database(s) are behind',
            expect="survives",
        ),
    ],
)

# ---------------------------------------------------------------------------
# BRANDING -- per-office topbar and DOCUMENT identity (logo / colours / fonts)
#
# WHY THIS BATCH EXISTS (2026-08-19)
#
# The operator asked for branding to follow the active office automatically and
# named three things: "logo/colors/fonts". Logo and colour worked; the FONT was
# a no-op through FIVE consecutive links, every one of which looked correct on
# its own:
#
#   1. no column to store a font in;
#   2. the boot payload never carried it;
#   3. branding.ts read it through an `as any` cast, so tsc could not object to
#      a property that did not exist;
#   4. `--brand-font` was written and NO css rule read it; and
#   5. the DOCUMENT renderer received the tenant on 1 of its 13 call sites.
#
# Nothing failed anywhere. The whole failure mode of this feature is silence, so
# every mutation below removes one link and must be caught. The controls check
# the opposite risk -- tests that pin cosmetics instead of behaviour.
# ---------------------------------------------------------------------------
BRANDING = Batch(
    name="branding",
    tests=["tests/test_tenant_font_branding.py"],
    mutations=[
        Mutation(
            "BA", "the CSS stops consuming the brand font token",
            "app/frontend/src/index.css",
            "  font-family: var(--brand-font, var(--font-body));\n",
            "",
        ),
        Mutation(
            # Dogar must be unaffected by the tenant path. Without the fallback,
            # the unset property resolves to nothing on the root portal and
            # Dogar loses the typography it authors itself.
            "BB", "the brand font is consumed without its Dogar fallback",
            "app/frontend/src/index.css",
            "font-family: var(--brand-font, var(--font-body));",
            "font-family: var(--brand-font);",
        ),
        Mutation(
            "BC", "the boot payload stops carrying the office typeface",
            "app/main.py",
            '            "fontKey": (getattr(tenant, "font_key", None) or "system"),\n',
            "",
        ),
        Mutation(
            # The cast is the camouflage, not the bug -- but it is what let the
            # bug live. With it back, links 1-3 can break with a clean build.
            "BD", "the `as any` cast returns and hides the boot contract",
            "app/frontend/src/lib/branding.ts",
            "resolveFontStack(tenant.fontKey)",
            "resolveFontStack((tenant as any).fontKey)",
        ),
        Mutation(
            "BE", "branding is applied even with no tenant (Dogar re-skinned)",
            "app/frontend/src/lib/branding.ts",
            "  if (!root || !tenant) return null\n",
            "  if (!root) return null\n",
        ),
        Mutation(
            # THE document defect. `document_font_stack(None)` is what every
            # document did implicitly before this work.
            "BF", "documents ignore the office and print in Dogar branding",
            "app/services/letterhead_renderer.py",
            '.replace("%DOCFONT%", document_font_stack(tenant)))',
            '.replace("%DOCFONT%", document_font_stack(None)))',
        ),
        Mutation(
            "BG", "Dogar's own documents switch to the tenant default typeface",
            "app/core/branding_contract.py",
            "        return DOGAR_DOCUMENT_FONT\n",
            "        return FONT_STACKS_PRINT[_DEFAULT_FONT_KEY]\n",
        ),
        Mutation(
            # 12 of the 13 document types reach the tenant ONLY through this
            # publish; without it they all silently revert to Dogar.
            "BH", "the middleware stops publishing the active office",
            "app/core/tenancy.py",
            '            current_tenant.set(getattr(request.state, "tenant", None))',
            "            pass  # publish removed",
        ),
        Mutation(
            # Same class of defect `features` already had: on the row, editable
            # in admin, absent from the snapshot the renderer actually receives.
            "BI", "ResolvedTenant loses the font, so no document can read it",
            "app/core/tenancy.py",
            "    font_key: Optional[str] = None\n",
            "",
        ),
        Mutation(
            # The value is interpolated into a live <style> block, so the
            # allow-list is a security control, not tidiness.
            "BJ", "unknown font values are passed through raw (CSS injection)",
            "app/core/branding_contract.py",
            "    return key if key in ALLOWED_FONT_KEYS else _DEFAULT_FONT_KEY",
            "    return key if key else _DEFAULT_FONT_KEY",
        ),
        Mutation(
            "BK", "the API accepts any font key the frontend cannot resolve",
            "app/api/endpoints/tenants.py",
            "        if key not in ALLOWED_FONT_KEYS:",
            "        if False:",
        ),
        Mutation(
            # Control: the PRINT stacks are tuning, not contract. Only the KEYS
            # must agree across the two languages -- a document legitimately
            # needs a different fallback chain from a screen. A test that pinned
            # the exact stack string would fail on a harmless typography tweak.
            "BL", "a print font stack is retuned (control: must survive)",
            "app/core/branding_contract.py",
            '"roboto": "Roboto, \'Helvetica Neue\', Helvetica, Arial, sans-serif",',
            '"roboto": "Roboto, Arial, Helvetica, sans-serif",',
            expect="survives",
        ),
        Mutation(
            # Control: the tenant-facing SCREEN stack is likewise tuning.
            "BM", "a screen font stack is retuned (control: must survive)",
            "app/frontend/src/lib/branding.ts",
            "  lato: 'Lato, -apple-system, BlinkMacSystemFont, sans-serif',",
            "  lato: 'Lato, system-ui, sans-serif',",
            expect="survives",
        ),
        Mutation(
            # THE DEFECT A REAL BROWSER FOUND (2026-08-19), which thirty-four
            # green source-level tests did not. Every mutation above pins the
            # branding MECHANISM; this one pins WHERE it runs. The original code
            # applied branding from AppLayout, which mounts inside
            # <RequireAuth>, so a tenant's own login screen -- the first thing
            # their staff ever see -- rendered in Dogar's colours and typeface,
            # and the branding vanished again on every 401.
            #
            # A correct mechanism wired into the wrong place is still a broken
            # feature, and only that second kind of mistake fails silently.
            "BN", "branding moves back behind the authentication gate",
            "app/frontend/src/main.tsx",
            "applyTenantBranding(TENANT_INFO)\n",
            "",
        ),
        Mutation(
            # The ordering half of the same lesson: still outside the auth gate,
            # but after the first paint, which flashes Dogar branding on a
            # partner office's portal before repainting it.
            "BO", "branding is applied after React has already rendered",
            "app/frontend/src/main.tsx",
            # RE-ANCHORED 2026-08-20. The original anchor spanned from the
            # branding call to the `// Path-mode` comment, which was adjacent at
            # the time. Adding the boot-time title write BETWEEN them made the
            # anchor unmatchable, and the batch reported NO-OP -- i.e. this
            # protection had silently stopped being exercised at all while the
            # summary line still counted it. Now anchored on the two boot writes
            # as a unit and moved AFTER createRoot's render() call, which is the
            # behaviour the test actually pins (ordering), and which no longer
            # depends on what happens to sit next to them.
            "applyTenantBranding(TENANT_INFO)",
            "queueMicrotask(() => applyTenantBranding(TENANT_INFO))",
        ),
        Mutation(
            # The SPECIFICITY defect. `--brand-font` was set on <html> and
            # `body { font-family: var(--brand-font, ...) }` was present, and
            # the computed font was still Inter: index.html ships
            # <body class="font-sans">, and Tailwind's `.font-sans` (0,1,0)
            # outranks the `body` type selector (0,0,1). Two correct-looking
            # declarations, neither wrong on its own, and the tenant lost.
            # Fixed at the theme so every font-sans surface follows the office.
            "BP", "the font-sans utility stops resolving through the brand token",
            "app/frontend/tailwind.config.js",
            "        sans: ['var(--brand-font, Inter)', 'system-ui', 'sans-serif'],",
            "        sans: ['Inter', 'system-ui', 'sans-serif'],",
            expect="survives",
            # CONTROL, deliberately. The failure this pins is only observable
            # in a browser -- it is a CASCADE outcome, and no source-level
            # assertion can see that one selector outranks another. Registering
            # it as "caught" would be a false claim; registering it as a control
            # records honestly that the guard is /tmp/probe_font.py, which
            # asserts the COMPUTED font on both portals.
        ),
        Mutation(
            "BQ", "the tenant dashboard hardcodes the root company name",
            "app/frontend/src/pages/Dashboard.tsx",
            "Overview of {brandFullName(TENANT_INFO)}",
            "Overview of Dogar Trading Corporation",
        ),
        Mutation(
            # Written on every route change, so it also reaches bookmarks, the
            # window switcher and printed page headers.
            "BR", "the browser tab title hardcodes the root company name",
            "app/frontend/src/layout/Breadcrumbs.tsx",
            "`${meta.title} · ${brandFullName(TENANT_INFO)}`",
            "`${meta.title} · Dogar Trading Corporation`",
        ),
        Mutation(
            "BS", "the footer credits the root company on every office's portal",
            "app/frontend/src/layout/AppLayout.tsx",
            "{new Date().getFullYear()} {brandFullName(TENANT_INFO)}",
            "{new Date().getFullYear()} Dogar Trading Corporation",
        ),
        Mutation(
            # The fallback every one of the three fixes above depends on. If it
            # went away, all three surfaces would blank out on the ROOT portal
            # simultaneously -- the opposite failure, and the one the brief
            # forbids ("keeping the Dogar tenant unchanged").
            "BT", "brandFullName stops falling back to the root company name",
            "app/frontend/src/lib/branding.ts",
            "  return (tenant?.name || 'Dogar Trading Corporation').trim() || 'Dogar Trading Corporation'",
            "  return (tenant?.name || '').trim()",
        ),
        # ── The unauthenticated hero art and the primary action (2026-08-20) ──
        # Registered after a SCREENSHOT, not a failing assertion, showed a
        # partner office's login page rendering Dogar's own logo-wall photograph
        # behind a Dogar-blue Sign In button -- on a page where the accent,
        # typeface, company name and tab title were all already verified correct.
        Mutation(
            "BU", "the auth hero panel goes back to hard-coding Dogar's photograph",
            "app/frontend/src/index.css",
            "background-image: var(--brand-hero, url('/static/img/login/office-left.jpg'));\n"
            "  background-size: cover; background-position: center;",
            "background-image: url('/static/img/login/office-left.jpg');\n"
            "  background-size: cover; background-position: center;",
        ),
        Mutation(
            "BV", "the phone-sized auth banner stops following the office",
            "app/frontend/src/index.css",
            "background-image: var(--brand-hero, url('/static/img/login/office-left.jpg'));\n"
            "    background-size: cover; background-position: top center;",
            "background-image: url('/static/img/login/office-left.jpg');\n"
            "    background-size: cover; background-position: top center;",
        ),
        Mutation(
            "BW", "the hero token loses its Dogar fallback",
            # The OTHER direction, and the one a careless fix actually takes:
            # dropping the fallback themes the tenants correctly and silently
            # strips the ROOT portal's own hero art. "Dogar is unchanged" is a
            # property of this fallback, so it needs its own anchor.
            "app/frontend/src/index.css",
            "var(--brand-hero-soft, url('/static/img/login/office-right.jpg'))",
            "var(--brand-hero-soft)",
        ),
        Mutation(
            "BX", "the Sign In button goes back to a hard-coded Dogar-blue gradient",
            "app/frontend/src/index.css",
            "background: linear-gradient(135deg, var(--brand-solid) 0%, var(--brand-solid-hover) 100%);",
            "background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);",
        ),
        Mutation(
            "BY", "the Sign In label is hard-coded white instead of the WCAG-checked ink",
            "app/frontend/src/index.css",
            "  color: var(--brand-on-solid) !important;",
            "  color: #ffffff !important;",
        ),
        Mutation(
            "BZ", "a raw Tailwind blue utility returns to a login call site",
            "app/frontend/src/pages/Login.tsx",
            'className="text-[12.5px] auth-accent hover:underline font-medium"',
            'className="text-[12.5px] text-blue-600 hover:underline font-medium"',
        ),
        Mutation(
            "CA", "the hero gradient interpolates the raw tenant colour",
            # Bypassing derivePalette() skips the WCAG work AND lets an
            # unparseable legacy value go straight into a live stylesheet
            # property. Anchored on the helper body, which is the last writer
            # before the token.
            "app/frontend/src/lib/branding.ts",
            "  return `linear-gradient(140deg, ${palette.solid} 0%, ${palette.hover} 62%, ${palette.solid} 100%)`",
            "  return `linear-gradient(140deg, #1d4ed8 0%, #1a44bd 62%, #1d4ed8 100%)`",
        ),
    ],
)

BATCHES = {
    b.name: b
    for b in (
        TENANCY, AUDIT, GUARDS, DIALOGS, GLASS, GEOMETRY, PACKAGING, DENIALS,
        SCHEMA, BRANDING, CONTRACT,
    )
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("batches", nargs="*", help="batch names, or 'all'")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or not args.batches:
        print("available batches:")
        for name, b in BATCHES.items():
            print(f"  {name:10} {len(b.mutations)} mutations -> {', '.join(b.tests)}")
        return 0

    names = list(BATCHES) if "all" in args.batches else args.batches
    problems = 0
    for name in names:
        batch = BATCHES.get(name)
        if batch is None:
            print(f"unknown batch {name!r}")
            return 2
        print(f"\n===== MUTATION BATCH: {name} =====")
        for ident, verdict, evidence in run_batch(batch):
            mut = next(m for m in batch.mutations if m.ident == ident)
            good = verdict in ("CAUGHT", "CONTROL-OK")
            flag = "   " if good else "***"
            print(f" {flag} {ident} {verdict:10} {mut.description}")
            if evidence:
                print(f"        {evidence}")
            if not good:
                problems += 1
    print(f"\n{'FAILED' if problems else 'OK'}: "
          f"{problems} escaped / no-op / ambiguous / overfit mutation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
