"""Multi-tenant Copy Portal model.

Each Tenant is an independent portal instance for another business partner /
office / client. Per-tenant data isolation is achieved by giving each tenant
its own SQLite database file (`db_path`). Per-tenant feature flags are stored
as a JSON column so the admin can switch features (AI, document overlay,
role creation, settings, etc.) on/off without code changes.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text

from app.db.session import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)

    # Branding
    slug = Column(String(50), unique=True, nullable=False, index=True)   # e.g. "demo-oep"
    company_name = Column(String(200), nullable=False)                   # e.g. "New OEP Pvt Ltd"
    short_name = Column(String(50), nullable=False)                      # e.g. "New OEP"
    subtitle = Column(String(150), default="Overseas Employment Promoters")
    primary_color = Column(String(20), default="#2563eb")                # corporate blue
    # Typeface KEY, not a font-family string (2026-08-19).
    #
    # This holds a short token ("system", "inter", "roboto", "lato", "serif",
    # "mono") that the frontend resolves against the FONT_STACKS allow-list in
    # app/frontend/src/lib/branding.ts. Storing the resolved `font-family`
    # DECLARATION instead would be wrong twice over: it is written straight into
    # a live CSS custom property, so a stray `}` in the stored value terminates
    # the rule and breaks the entire shell's typography, and it would fork the
    # font list between the DB and the stylesheet so the two could drift.
    # A key can only ever select a stack the frontend already vouches for; an
    # unknown key falls back to the system stack rather than rendering nothing.
    #
    # NOTE: no index=True here. Index reconciliation in
    # scripts/apply_tenant_migrations.py matches BY NAME, so an auto-named
    # ix_tenants_font_key would be re-added on every deploy alongside any
    # hand-named migration index. See app/models/README_INDEXES.md.
    font_key = Column(String(30), default="system", nullable=True)
    logo_filename = Column(String(255), nullable=True)
    # LEGACY, UNUSED. Read by nothing -- not the API, not the letterhead or
    # document renderers, not the boot payload, not the SPA bundle. The comment
    # that used to sit here said it was "kept for older code paths that still
    # reference logo_path"; a repo-wide search finds no such path, and the
    # branding upload endpoint was still dutifully writing it inside a bare
    # `except Exception: pass` -- which is exactly how two columns holding the
    # same value drift apart and produce "the logo is wrong on one screen only".
    #
    # Kept as a column rather than dropped: a pre-existing row could have
    # logo_path set with logo_filename empty, and losing an office's logo to a
    # cleanup commit is a worse outcome than an unused column. Anything that
    # needs the logo must read ``logo_filename``.
    logo_path = Column(String(255), nullable=True)

    # Enterprise branding (per-tenant office identity)
    office_name = Column(String(200), default="", nullable=True)         # e.g. "Lahore Office"
    license_number = Column(String(100), default="", nullable=True)      # OEP licence # printed on letterhead
    office_address = Column(String(400), default="", nullable=True)      # full postal address for letterhead footer
    letterhead_path = Column(String(255), default="", nullable=True)     # filename under data/tenants/<slug>/
    receipt_template = Column(Text, default="", nullable=True)           # custom receipt HTML / boilerplate
    demand_format = Column(String(50), default="", nullable=True)        # demand file number format e.g. "DEM-{YYYY}-{####}"

    # Data isolation -- each tenant has its OWN SQLite database file
    db_path = Column(String(500), nullable=False)                        # ./data/tenant_<slug>.db

    # Custom hosting -- tenant can bind their own domain to this portal copy.
    # NULL/empty = tenant is only reachable via <slug>.<root-domain>.
    # When set, the TenantResolverMiddleware matches request Host header
    # against this field FIRST (case-insensitive, without port/scheme).
    # DNS + TLS provisioning happens OUTSIDE the app (reverse proxy layer);
    # this column is just the app-side binding.
    custom_domain = Column(String(255), nullable=True, unique=True, index=True)

    # Per-tenant RBAC feature flags (admin can hide capabilities)
    # Example: {"ai_chatbot": false, "document_overlay": true, "settings": true,
    #           "role_creation": false, "agents": true, "reports": true, "documents": true}
    features = Column(JSON, default=dict, nullable=False)

    # Owner / partner info
    contact_name = Column(String(150))
    contact_email = Column(String(150))
    contact_phone = Column(String(50))

    # Status
    status = Column(String(20), default="active")          # active|suspended|provisioning|archived
    plan = Column(String(30), default="standard")          # standard|pro|enterprise

    # Default admin credentials for the new tenant (one-time seeded)
    admin_email = Column(String(150))
    admin_password_set = Column(Boolean, default=False)

    notes = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Feature defaults -- used when features={} (newly provisioned)
    @staticmethod
    def default_features() -> dict:
        return {
            # Core (cannot be turned off via UI, but listed for completeness)
            "dashboard": True,
            "clients": True,
            "demands": True,
            "candidates": True,
            # Toggleable
            "documents": True,           # Document templates page
            "document_overlay": True,    # Document Coordinate engine (canvas designer)
            "ai_chatbot": True,          # DtcBot floating widget
            "ai_ocr": True,              # Passport OCR
            "agents": True,              # Sub-Agents legacy page
            "reports": True,             # Reports page
            "settings": True,            # Settings menu (in addition to admin-only RBAC)
            "role_creation": True,       # Roles management page
            "login_history": True,       # Login history page
            "depositors": True,          # Depositors page
            "service_charges": True,     # Service charges page
        }
