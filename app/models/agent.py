"""Agent and AgentCash ORM models.

These tables track external recruiting agents and their financial ledger
(cash advances, commissions, refunds). They exist in every tenant DB
and contain live production data. Previously undocumented in ORM — the
tables were created by a raw SQL migration and used directly by the
legacy desktop app. Adding ORM models ensures Alembic autogenerate
never tries to delete them, and future endpoint development can use
typed queries instead of raw SQL.
"""
from datetime import datetime as _dt

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from app.db.session import Base


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    company_name = Column(String(255), default="")
    phone = Column(String(50), default="")
    mobile = Column(String(50), default="")
    email = Column(String(150), default="")
    address = Column(Text, default="")
    city = Column(String(100), default="")
    status = Column(String(30), default="active")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=_dt.utcnow)
    updated_at = Column(DateTime, default=_dt.utcnow, onupdate=_dt.utcnow)

    # Declared to match the baseline migration 1c1e09f245c6, which creates
    # ix_agents_id and ix_agents_name via batch_op.create_index(). The ORM
    # models for these two tables were added LATER (see the module docstring:
    # the tables originally came from raw SQL and the legacy desktop app), and
    # the indexes were never carried across -- so a fresh create_all() install
    # silently lacked them. See app/models/README_INDEXES.md.
    __table_args__ = (
        Index("ix_agents_id", "id"),
        Index("ix_agents_name", "name"),
    )


class AgentCash(Base):
    __tablename__ = "agent_cash"

    id = Column(Integer, primary_key=True, autoincrement=True)
    datetime = Column(DateTime, default=_dt.utcnow)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    details = Column(String(500), default="")
    debit = Column(Float, default=0)
    credit = Column(Float, default=0)
    method = Column(String(30), default="")
    ref_id = Column(String(100), default="")
    user_id = Column(Integer, default=None)
    created_at = Column(DateTime, default=_dt.utcnow)

    # Matches baseline migration 1c1e09f245c6 (ix_agent_cash_id).
    __table_args__ = (
        Index("ix_agent_cash_id", "id"),
    )
