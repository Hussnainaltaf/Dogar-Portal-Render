from sqlalchemy import Column, DateTime, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import FlexibleDate


class Client(Base):
    """Foreign sponsor / client company (full sponsor profile fields)."""
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    # Basic
    company_name = Column(String(255), nullable=False, index=True)
    client_type = Column(String(30), default="Company")   # Company | Individual
    status = Column(String(30), default="active")          # active | inactive

    license_no = Column(String(100), default="")
    email = Column(String(150), default="")
    phone = Column(String(50), default="")

    # Sponsor (for demand-file pre-fill)
    sponsor_name = Column(String(255), default="")
    sponsor_name_arabic = Column(String(255), default="")
    sponsor_address = Column(Text, default="")
    sponsor_address_arabic = Column(Text, default="")
    sponsor_phone = Column(String(50), default="")
    sponsor_alt_phone = Column(String(50), default="")
    sponsor_id = Column(String(100), default="")

    # Address
    street = Column(Text, default="")
    city = Column(String(100), default="")
    state = Column(String(100), default="")
    country = Column(String(100), default="Pakistan")
    address = Column(Text, default="")     # legacy combined
    contact_person = Column(String(150), default="")

    # Opening balance
    opening_balance = Column(Numeric(14, 2), default=0)
    opening_balance_direction = Column(String(20), default="Owed to us")   # "Owed to us" | "We owe"
    opening_balance_date = Column(FlexibleDate, nullable=True)

    notes = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    demands = relationship("Demand", back_populates="client", cascade="all, delete-orphan")

    # Declared to match migration 2a4f1b8c9d31 EXACTLY (same name, same column
    # order, same DESC). See app/models/README_INDEXES.md: a fresh install runs
    # create_all() + `alembic stamp head`, so it NEVER executes that migration
    # and would otherwise ship with no index on the clients list sort key.
    # Measured there: clients list 0.33ms -> 0.12ms (-63%).
    __table_args__ = (
        Index("ix_clients_created_at", text("created_at DESC")),
    )
