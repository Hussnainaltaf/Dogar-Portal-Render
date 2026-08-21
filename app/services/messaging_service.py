"""
Real WhatsApp / SMS delivery via Twilio (Task 9).

This replaces the old client-side `wa.me` quick-links with a genuine
server-side provider integration. Messages are sent through Twilio's REST
Messages API using the account credentials configured in ``Settings``
(env-only — never exposed to the browser).

Design goals
------------
* **Real delivery** when Twilio credentials are configured.
* **Safe dry-run** when they are not: the caller still gets a structured
  result (``status="dry_run"``) so the UI and tests work without sending
  real messages or incurring cost.
* **No heavy SDK**: uses ``httpx`` (already a dependency) to POST to the
  Twilio REST endpoint directly, keeping the deploy footprint small.
* **E.164 normalisation** so operators can store local numbers.

Public API
----------
    send_message(to, body, channel="sms"|"whatsapp") -> MessageResult
    normalize_phone(raw, default_country="92") -> str | None
    provider_status() -> dict   # for a settings/health panel
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.config import settings

TWILIO_API_ROOT = "https://api.twilio.com/2010-04-01"

# Default country calling code used when a local number has no + prefix.
# 92 = Pakistan (the portal's primary market). Override per-call if needed.
DEFAULT_COUNTRY_CODE = "92"


@dataclass
class MessageResult:
    ok: bool
    channel: str
    to: str
    status: str                    # queued|sent|dry_run|failed|invalid
    provider_sid: Optional[str] = None
    error: Optional[str] = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "channel": self.channel,
            "to": self.to,
            "status": self.status,
            "provider_sid": self.provider_sid,
            "error": self.error,
        }


def normalize_phone(raw: str, default_country: str = DEFAULT_COUNTRY_CODE) -> Optional[str]:
    """Best-effort normalisation to E.164 (e.g. ``+923001234567``).

    Rules:
      * strip spaces, dashes, parentheses;
      * a leading ``+`` is preserved;
      * a leading ``00`` international prefix becomes ``+``;
      * a leading ``0`` (local trunk) is replaced by the default country code;
      * otherwise the default country code is prepended.
    Returns ``None`` if no plausible digits remain.
    """
    if not raw:
        return None
    s = re.sub(r"[\s\-()]", "", str(raw).strip())
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s[1:])
        return f"+{digits}" if digits else None
    if s.startswith("00"):
        digits = re.sub(r"\D", "", s[2:])
        return f"+{digits}" if digits else None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("0"):
        digits = digits[1:]
    # Avoid double-prefixing when the number already begins with the country code.
    if digits.startswith(default_country):
        return f"+{digits}"
    return f"+{default_country}{digits}"


def _is_configured() -> bool:
    return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)


def provider_status() -> dict:
    """Non-secret status for a settings / health panel (no credentials leaked)."""
    return {
        "configured": _is_configured(),
        "sms_enabled": bool(
            _is_configured()
            and (settings.TWILIO_SMS_FROM or settings.TWILIO_MESSAGING_SERVICE_SID)
        ),
        "whatsapp_enabled": bool(_is_configured() and settings.TWILIO_WHATSAPP_FROM),
        "sms_from": settings.TWILIO_SMS_FROM or None,
        "whatsapp_from": settings.TWILIO_WHATSAPP_FROM or None,
        "mode": "live" if _is_configured() else "dry_run",
    }


def _whatsapp_addr(number: str) -> str:
    number = number.strip()
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def send_message(
    to: str,
    body: str,
    channel: str = "sms",
    *,
    default_country: str = DEFAULT_COUNTRY_CODE,
    timeout: float = 15.0,
) -> MessageResult:
    """Send an SMS or WhatsApp message via Twilio.

    Returns a :class:`MessageResult`. Never raises for provider/network errors —
    those are captured in ``result.error`` with ``ok=False`` so callers can
    surface a clean message to the user.
    """
    channel = (channel or "sms").lower()
    if channel not in ("sms", "whatsapp"):
        return MessageResult(False, channel, to, "invalid",
                             error="channel must be 'sms' or 'whatsapp'")

    if not body or not body.strip():
        return MessageResult(False, channel, to, "invalid", error="message body is empty")

    e164 = normalize_phone(to, default_country)
    if not e164:
        return MessageResult(False, channel, to, "invalid",
                             error=f"could not parse a valid phone number from '{to}'")

    # ── Dry-run when unconfigured ──────────────────────────────────────────
    if not _is_configured():
        return MessageResult(
            True, channel, e164, "dry_run",
            detail={"note": "Twilio not configured — message logged but not sent"},
        )

    # ── Resolve sender + recipient per channel ─────────────────────────────
    data = {"Body": body}
    if channel == "whatsapp":
        if not settings.TWILIO_WHATSAPP_FROM:
            return MessageResult(False, channel, e164, "failed",
                                 error="TWILIO_WHATSAPP_FROM is not configured")
        data["From"] = _whatsapp_addr(settings.TWILIO_WHATSAPP_FROM)
        data["To"] = _whatsapp_addr(e164)
    else:  # sms
        if settings.TWILIO_MESSAGING_SERVICE_SID:
            data["MessagingServiceSid"] = settings.TWILIO_MESSAGING_SERVICE_SID
        elif settings.TWILIO_SMS_FROM:
            data["From"] = settings.TWILIO_SMS_FROM
        else:
            return MessageResult(False, channel, e164, "failed",
                                 error="TWILIO_SMS_FROM / TWILIO_MESSAGING_SERVICE_SID not configured")
        data["To"] = e164

    url = f"{TWILIO_API_ROOT}/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        resp = httpx.post(
            url,
            data=data,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return MessageResult(False, channel, e164, "failed",
                             error=f"network error contacting Twilio: {exc}")

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code in (200, 201):
        return MessageResult(
            True, channel, e164,
            status=payload.get("status", "queued"),
            provider_sid=payload.get("sid"),
            detail={"price": payload.get("price"), "num_segments": payload.get("num_segments")},
        )

    # Twilio error body: {"code":..., "message":..., "more_info":...}
    err = payload.get("message") or f"Twilio returned HTTP {resp.status_code}"
    return MessageResult(False, channel, e164, "failed",
                         error=err, detail={"twilio_code": payload.get("code")})
