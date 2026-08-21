"""Audit log + Candidate wizard state persistence."""
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class AuditLog(Base):
    """Immutable record of state-changing events.

    Every assign / unassign / stage-change / wizard-finalize / delete writes
    a row here so we have a permanent trail of *who did what when*.
    """
    __tablename__ = "audit_logs"

    id            = Column(Integer, primary_key=True, index=True)
    entity_type   = Column(String(40), nullable=False, index=True)  # AuditEntity
    entity_id     = Column(Integer, nullable=True, index=True)
    action        = Column(String(40), nullable=False, index=True)  # AuditAction
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_name    = Column(String(150), default="")
    summary       = Column(String(500), default="")
    before_json   = Column(JSON, nullable=True)
    after_json    = Column(JSON, nullable=True)
    ip_address    = Column(String(64), default="")
    user_agent    = Column(String(255), default="")
    occurred_at   = Column(DateTime, server_default=func.now(), index=True)

    # OUTCOME (2026-08-18). Before this, the trail recorded ONLY actions that
    # succeeded, because the permission guards raise 403 and write nothing. A
    # user probing for documents:print or audit:view therefore left no trace at
    # all -- precisely the events a security review needs. 'success' | 'denied'.
    #
    # NOT NULL with a 'success' default so every pre-existing row keeps a
    # truthful value: anything already in the table did, by construction, go
    # through. See alembic 9b5e7d3f2c81.
    # NO `index=True` here, deliberately. A column-level index would make
    # SQLAlchemy emit `ix_audit_logs_outcome` on a fresh `create_all()` install
    # while alembic 9b5e7d3f2c81 creates only the composite index -- so a
    # migrated database and a fresh one would carry DIFFERENT indexes, and a
    # query plan measured on one would not describe the other. The composite
    # `ix_audit_outcome_time` below already has `outcome` as its LEADING column,
    # so it serves `WHERE outcome = ?` on its own; a second index would only
    # cost writes. `tests/test_audit_outcome.py` pins model and migration
    # together so this cannot drift back apart silently.
    outcome       = Column(String(16), nullable=False, server_default="success",
                           default="success")
    # The permission key that was required and refused. NULL for successes.
    permission    = Column(String(80), nullable=True)
    # The document type ("category") for document actions; NULL otherwise.
    # Denormalised OUT of after_json deliberately -- see alembic a1c6e8b4d072
    # for why filtering the JSON blob was rejected (not portable across the
    # SQLite and PostgreSQL tenant backends, and unindexable on both).
    #
    # Declared in __table_args__ below rather than with `index=True`, for the
    # same reason as `outcome`: a column-level index is auto-NAMED
    # `ix_audit_logs_doc_category`, which would NOT match the
    # `ix_audit_doc_category` the migration creates -- so a fresh install and a
    # migrated install would carry indexes with different names, and a
    # subsequent migration that drops one by name would fail on the other.
    doc_category  = Column(String(60), nullable=True)

    actor = relationship("User", foreign_keys=[actor_user_id])

    __table_args__ = (
        Index("ix_audit_entity_time", "entity_type", "entity_id", "occurred_at"),
        # Backs the viewer's "denied actions, newest first" query, which would
        # otherwise scan a table that only ever grows.
        Index("ix_audit_outcome_time", "outcome", "occurred_at"),
        # Backs "show me every action on visa documents". Name must match
        # alembic a1c6e8b4d072 exactly -- see the doc_category column comment.
        Index("ix_audit_doc_category", "doc_category"),
    )


class CandidateWizardState(Base):
    """Persistent draft state for the 5-step candidate wizard.

    Created the first time a user opens the wizard (POST /api/candidates/wizard).
    Each PATCH /api/candidates/wizard/{id}/step/{n} writes the partial payload
    into step_data[n] and bumps current_step so the user can close the
    drawer and resume later.

    When the wizard is finalised, candidate_id is populated and
    is_finalized = 1. Reopening a finalised wizard clears
    is_finalized and lets the user edit any step.
    """
    __tablename__ = "candidate_wizard_states"

    id              = Column(Integer, primary_key=True, index=True)
    candidate_id    = Column(Integer, ForeignKey("candidates.id"), nullable=True, index=True)
    job_category_id = Column(Integer, ForeignKey("job_categories.id"), nullable=True)

    current_step    = Column(Integer, default=1)
    total_steps     = Column(Integer, default=5)
    is_finalized    = Column(Integer, default=0)

    step_data       = Column(JSON, default=dict)

    created_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime, server_default=func.now())
    updated_at      = Column(DateTime, server_default=func.now(), onupdate=func.now())
    finalized_at    = Column(DateTime, nullable=True)

    candidate     = relationship("Candidate", foreign_keys=[candidate_id])
    job_category  = relationship("JobCategory", foreign_keys=[job_category_id])
    created_by    = relationship("User", foreign_keys=[created_by_id])


class CandidateDocumentChecklist(Base):
    """Per-candidate document checklist state."""
    __tablename__ = "candidate_document_checklist"

    id            = Column(Integer, primary_key=True, index=True)
    candidate_id  = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
    document_key  = Column(String(50), nullable=False)
    received      = Column(Integer, default=0)
    received_at   = Column(DateTime, nullable=True)
    received_by   = Column(String(150), default="")
    notes         = Column(Text, default="")

    candidate = relationship("Candidate")

    __table_args__ = (
        Index("ix_doc_checklist_cand_key", "candidate_id", "document_key", unique=True),
    )
