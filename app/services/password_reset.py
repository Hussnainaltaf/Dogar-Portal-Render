"""Transaction-safe password-reset token operations."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.lookups import PasswordResetToken


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(UTC)


def claim_reset_token(db: Session, token_hash: str, now: datetime | None = None) -> bool:
    """Atomically mark an unused, unexpired reset token as claimed.

    The conditional UPDATE is the concurrency boundary: exactly one concurrent
    request can change ``used_at`` from NULL, so a reset link is genuinely
    single-use rather than merely checked-then-marked.
    """
    timestamp = _now(now)
    result = db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > timestamp,
        )
        .values(used_at=timestamp)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def revoke_unused_reset_tokens(
    db: Session, user_id: int, exclude_id: int | None = None,
    now: datetime | None = None,
) -> int:
    """Invalidate all outstanding reset links for a user.

    ``exclude_id`` keeps the just-redeemed token's original claim timestamp
    intact for auditability while revoking every other outstanding link.
    """
    predicate = [
        PasswordResetToken.user_id == user_id,
        PasswordResetToken.used_at.is_(None),
    ]
    if exclude_id is not None:
        predicate.append(PasswordResetToken.id != exclude_id)
    result = db.execute(
        update(PasswordResetToken)
        .where(*predicate)
        .values(used_at=_now(now))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0
