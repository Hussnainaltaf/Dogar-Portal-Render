from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import FlexibleDate


class Candidate(Base):
    """A worker being recruited \u2014 fields match the reference Candidate slide-in panel exactly."""
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)

    # ===== Personal Info (Step 1) =====
    full_name = Column(String(255), nullable=False, index=True)
    name_arabic = Column(String(255), default="")
    mother_name = Column(String(255), default="")
    father_name = Column(String(255), default="")
    father_name_arabic = Column(String(255), default="")
    gender = Column(String(10), default="Male")
    marital_status = Column(String(20), default="Single")
    religion = Column(String(30), default="Islam")
    date_of_birth = Column(FlexibleDate, nullable=True)
    place_of_birth = Column(String(100), default="")
    place_of_birth_arabic = Column(String(100), default="")
    nationality = Column(String(50), default="PAKISTANI")
    address = Column(Text, default="")
    phone = Column(String(30), default="")
    tehsil = Column(String(100), default="")
    district = Column(String(100), default="")
    province = Column(String(100), default="")
    photo = Column(String(255), default="")

    # ===== Identification (Step 2) =====
    passport_no = Column(String(50), index=True, default="")
    passport_issue_date = Column(FlexibleDate, nullable=True)
    passport_expiry_date = Column(FlexibleDate, nullable=True)
    issuing_authority = Column(String(100), default="PAKISTAN")
    issuing_authority_arabic = Column(String(100), default="")
    passport_issue_place = Column(String(100), default="")
    cnic = Column(String(30), index=True, default="")
    nadra_token_no = Column(String(50), default="")

    # ===== Employment (Step 3) =====
    permission_no = Column(String(100), default="")
    permission_date = Column(FlexibleDate, nullable=True)
    qualification = Column(String(150), default="")
    age_employee = Column(Integer, nullable=True)
    profession = Column(String(100), default="")  # job/profession
    salary = Column(Numeric(12, 2), default=0)

    # ===== Next of Kin (Step 4) =====
    next_of_kin_name = Column(String(255), default="")
    next_of_kin_nic = Column(String(30), default="")
    next_of_kin_relation = Column(String(50), default="")

    # ===== Protector / Medical / Travel (post-process) =====
    protector_no = Column(String(50), default="")
    protector_date = Column(FlexibleDate, nullable=True)
    medical_center = Column(String(150), default="")
    gamca_number = Column(String(50), default="")
    medical_date = Column(FlexibleDate, nullable=True)
    medical_consignment_no = Column(String(50), default="")
    medical_send_date = Column(FlexibleDate, nullable=True)
    medical_courier_name = Column(String(100), default="")
    e_number = Column(String(50), default="")
    date_of_departure = Column(FlexibleDate, nullable=True)
    flight_no = Column(String(50), default="")
    destination = Column(String(100), default="")
    ticket_no = Column(String(50), default="")
    visa_stamp_date = Column(FlexibleDate, nullable=True)
    ticket_included = Column(String(10), default="No")
    accommodation_allowance = Column(String(50), default="")
    food_allowance = Column(String(50), default="")
    slot_notes = Column(Text, default="")
    price = Column(Numeric(12, 2), default=0)

    # ===== Common =====
    email = Column(String(150), default="")
    status = Column(String(30), default="pending")  # pending, documents_pending, processing, issued, deployed
    notes = Column(Text, default="")

    # ===== Denormalised sort key (PERF -- see migration 3b7d2e4f8a15) =====
    # MAX(demand.id) across this candidate's assignments, i.e. "newest linked
    # demand". The candidates list sorts by this. It used to be computed with a
    # correlated scalar subquery per row, which was 91% of the endpoint's DB
    # time and could not use LIMIT to prune the scan -- a deep page cost the
    # same as page 1 (measured 318ms at 40k candidates vs 13ms with this
    # column indexed, -96%).
    #
    # DO NOT write this from application code. It is maintained by database
    # TRIGGERS created in the migration, because assignments are also written
    # via bulk_save_objects(), raw SQL and ORM cascade deletes -- all of which
    # bypass SQLAlchemy event hooks. The triggers were verified against all
    # eight mutation paths; app-level maintenance was not, and a stale value
    # here is a silent ordering-corruption bug, not just a slow page.
    # MEASURED FRESH-vs-MIGRATED DRIFT 2026-08-19 -- why index=True was removed.
    #
    # This carried `index=True`, which auto-names an index
    # `ix_candidates_latest_demand_id` on ONE column. Migration
    # 3b7d2e4f8a15 instead creates a DIFFERENT, composite covering index by
    # raw SQL: ix_candidates_latest_demand_sort (latest_demand_id DESC, id
    # DESC). Neither side knew about the other, so the two install routes
    # produced two different schemas -- measured on this repo:
    #
    #   migrated control DB : ix_candidates_latest_demand_sort  (composite)
    #   fresh create_all()  : ix_candidates_latest_demand_id    (single col)
    #
    # Both serve the list ORDER BY as a covering scan (measured with
    # EXPLAIN QUERY PLAN over 2,675 real rows), so this never announced
    # itself as a failure -- which is exactly why it survived. The cost is
    # that a plan measured on one install does not describe the other, and
    # the tenant-reconciliation script (scripts/apply_tenant_migrations.py)
    # matches indexes BY NAME, so it kept trying to add the single-column
    # index to migrated tenant DBs forever -- a permanent, silent, growing
    # duplicate of an index that was already there under another name.
    #
    # Declaring the composite index here, with the SAME NAME the migration
    # uses, makes both routes converge on one schema. tests/
    # test_schema_convergence.py now fails if any model index drifts from
    # the migrated DB again.
    latest_demand_id = Column(Integer, nullable=True)

    __table_args__ = (
        Index(
            "ix_candidates_latest_demand_sort",
            text("latest_demand_id DESC"),
            text("id DESC"),
        ),
        # Mirror migration 2a4f1b8c9d31 exactly -- see
        # app/models/README_INDEXES.md for why these must be declared here too.
        Index("ix_candidates_created_at", text("created_at DESC")),
        Index("ix_candidates_status_created", text("status"), text("created_at DESC")),
    )
    
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    assignments = relationship("CandidateAssignment", back_populates="candidate", cascade="all, delete-orphan")
    created_by = relationship("User", foreign_keys=[created_by_id])
    updated_by = relationship("User", foreign_keys=[updated_by_id])


class CandidateAssignment(Base):
    """Link candidate to a specific trade (visa category) within a demand."""
    __tablename__ = "candidate_assignments"

    id = Column(Integer, primary_key=True, index=True)
    # Indexed FKs: the candidate list joins candidate → assignment →
    # job_category → demand → client on every page load. Without these indexes
    # SQLite full-scans candidate_assignments per row. (perf, v18)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
    job_category_id = Column(Integer, ForeignKey("job_categories.id"), nullable=False, index=True)
    status = Column(String(30), default="pending")  # pending, documents_pending, processing, visa_issued, deployed
    embassy = Column(String(150), default="") # To persist the selected embassy choice for this assignment
    
    assigned_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, server_default=func.now())
    unassigned_at = Column(DateTime, nullable=True)
    notes = Column(Text, default="")

    candidate = relationship("Candidate", back_populates="assignments")
    job_category = relationship("JobCategory", back_populates="assignments")
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])

    # Mirrors migration 2a4f1b8c9d31 exactly -- see
    # app/models/README_INDEXES.md. This one removes the INNER temp B-tree
    # sort from the candidate-list correlated subquery; the migration's own
    # notes record it "will matter more as the assignment table grows".
    __table_args__ = (
        Index(
            "ix_cand_assign_cand_assigned",
            text("candidate_id"),
            text("assigned_at DESC"),
            text("id DESC"),
        ),
    )
