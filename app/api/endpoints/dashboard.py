from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.core.permissions import require_permission
from app.core.tenancy import get_tenant_db
from app.models import Candidate, Client, Demand, JobCategory, User
from app.services.cache import get_cache

router = APIRouter()


@router.get("/stats")
def dashboard_stats(request: Request, db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("dashboard:view"))):
    # AUDIT FIX (2026-06-19): cache key scoped by tenant slug as well as
    # user.id -- see the matching fix/comment in candidates.py for the
    # cross-tenant cache-collision rationale.
    cache = get_cache()
    _req_tenant = getattr(request.state, "tenant", None)
    _tenant_key = _req_tenant.slug if _req_tenant else "root"
    cache_key = f"dash:stats:{_tenant_key}:{user.id if user else 'anon'}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    total_clients = db.query(func.count(Client.id)).scalar() or 0
    total_demands = db.query(func.count(Demand.id)).scalar() or 0
    total_candidates = db.query(func.count(Candidate.id)).scalar() or 0

    total_visa_quota = db.query(func.coalesce(func.sum(JobCategory.quantity), 0)).scalar() or 0

    from sqlalchemy import select

    from app.models import CandidateAssignment

    # AUDIT FIX (2026-07-08, HIGH #6): use an explicit select() scalar-subquery
    # instead of Query.subquery() inside IN(...). SQLAlchemy 2.0 emits a
    # SAWarning ("Coercing Subquery object into a select() for use in IN()")
    # when a legacy Query.subquery() is passed to Column.in_(); building the
    # subquery with select(...).scalar_subquery() is the supported 2.0 form and
    # removes the warning without changing the generated SQL.
    assigned_sq = select(CandidateAssignment.candidate_id).distinct().scalar_subquery()
    total_assigned = db.query(func.count(CandidateAssignment.id)).scalar() or 0

    # Pending: Available slots in demand files (overall trade available 0/1 logic)
    pending = int(total_visa_quota) - int(total_assigned)
    if pending < 0:
        pending = 0

    # Processing: Assigned to a trade, but not yet completed
    processing = db.query(func.count(Candidate.id)).filter(
        Candidate.id.in_(assigned_sq),
        Candidate.status.not_in(["issued", "visa_issued", "stamped", "visa_stamping", "e_number", "travel_ready", "deployed", "completed", "cancelled"])
    ).scalar() or 0

    # Issued: Got visa
    issued = db.query(func.count(Candidate.id)).filter(
        Candidate.status.in_(["issued", "visa_issued", "stamped", "visa_stamping", "e_number", "travel_ready"])
    ).scalar() or 0

    # Deployed: Actually travelled
    deployed = db.query(func.count(Candidate.id)).filter(
        Candidate.status.in_(["deployed", "completed"])
    ).scalar() or 0

    # REMOVED 2026-07-30 (operator request): the `finance` block used to sum
    # AgentCash.debit / AgentCash.credit here. The Sub-Agents feature that fed
    # that ledger has been deleted, and no client ever read the block -- the
    # Dashboard page does not reference `finance`, and the debit/credit tiles on
    # the client and demand screens come from their own statement endpoints. So
    # this also drops two full-table SUM aggregates from every dashboard load.

    # Top trades
    top_trades_rows = (
        db.query(JobCategory.trade, func.sum(JobCategory.quantity).label("qty"))
        .group_by(JobCategory.trade)
        .order_by(func.sum(JobCategory.quantity).desc())
        .limit(7)
        .all()
    )
    top_trades = [{"trade": t, "qty": int(q or 0)} for t, q in top_trades_rows]

    # Demands by status
    demands_status_rows = (
        db.query(Demand.status, func.count(Demand.id))
        .group_by(Demand.status)
        .all()
    )
    demands_status = {s or "unknown": int(c) for s, c in demands_status_rows}

    result = {
        "totals": {
            "clients": total_clients,
            "demands": total_demands,
            "candidates": total_candidates,
            "visa_quota": int(total_visa_quota),
        },
        "candidates_status": {
            "pending": pending,
            "processing": processing,
            "issued": issued,
            "deployed": deployed,
        },
        "top_trades": top_trades,
        "demands_status": demands_status,
        "last_updated": datetime.now(UTC).isoformat(),
    }
    cache.set(cache_key, result, ttl=60)
    return result


@router.get("/monthly")
def dashboard_monthly(
    year: int = None,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("dashboard:view")),
):
    """Real monthly breakdown of demands and candidates created per month.
    Returns 12 months for the given year (defaults to current year).
    Used by the Recruitment Overview line chart for live data.
    """
    if year is None:
        year = datetime.now(UTC).year

    months = list(range(1, 13))

    # Candidates created per month
    cand_rows = (
        db.query(
            extract("month", Candidate.created_at).label("m"),
            func.count(Candidate.id).label("cnt"),
        )
        .filter(extract("year", Candidate.created_at) == year)
        .group_by("m")
        .all()
    )
    cand_by_month = {int(m): int(c) for m, c in cand_rows}

    # Demands created per month
    demand_rows = (
        db.query(
            extract("month", Demand.created_at).label("m"),
            func.count(Demand.id).label("cnt"),
        )
        .filter(extract("year", Demand.created_at) == year)
        .group_by("m")
        .all()
    )
    demand_by_month = {int(m): int(c) for m, c in demand_rows}

    return {
        "year": year,
        "months": months,
        "candidates": [cand_by_month.get(m, 0) for m in months],
        "demands": [demand_by_month.get(m, 0) for m in months],
        "last_updated": datetime.now(UTC).isoformat(),
    }
