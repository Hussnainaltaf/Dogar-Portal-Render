"""Email delivery service using smtplib.

Usage:
    from app.services.email import send_email

    send_email(
        to="user@example.com",
        subject="Password Reset",
        body="Click here to reset your password: ...",
    )

All SMTP credentials are read from app.core.config.settings so they are
never hard-coded. If any credential is missing in the current environment
the function logs a warning and raises RuntimeError rather than silently
swallowing the failure.
"""
import html as _html_mod
import logging
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

log = logging.getLogger("dtc.email")


def _html_to_text(markup: str) -> str:
    """Convert simple HTML markup to a readable plain-text fallback.

    H-5 FIX: previously the raw HTML body was attached as the text/plain part,
    so text-only mail clients displayed literal ``<p>...</p>`` tags. This
    strips tags, converts <br>/<p>/<li> to line breaks, preserves link hrefs,
    and unescapes HTML entities.
    """
    s = markup
    # <a href="URL">label</a>  ->  label (URL)
    s = re.sub(
        r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2)).strip()} ({m.group(1)})",
        s, flags=re.I | re.S,
    )
    # Structural tags -> newlines
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(p|div|h[1-6]|li|tr)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<\s*li\b[^>]*>", "  • ", s, flags=re.I)
    # Drop all remaining tags (incl. <style>/<script> bodies)
    s = re.sub(r"<\s*(style|script)\b[^>]*>.*?</\s*\1\s*>", "", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html_mod.unescape(s)
    # Collapse excess blank lines / trailing spaces
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def send_email(to: str, subject: str, body: str, html: bool = False,
               plain_body: Optional[str] = None) -> None:
    """Send a plain-text (or HTML) email via SMTP.

    Args:
        to:      Recipient address.
        subject: Email subject line.
        body:    Message body — plain text unless *html* is True.
        html:    If True, body is treated as HTML; a plain-text fallback is
                 also attached so mail clients without HTML support still
                 render something sensible.
        plain_body: Optional explicit plain-text fallback used when *html* is
                 True. When omitted, a fallback is derived automatically by
                 stripping the HTML markup (H-5 fix — previously the raw HTML
                 string was attached as the text/plain part).

    Raises:
        RuntimeError: SMTP credentials are not configured.
        smtplib.SMTPException: Delivery failed at the SMTP level.
    """
    from app.core.config import settings

    host = (settings.SMTP_HOST or "").strip()
    user = (settings.SMTP_USER or "").strip()
    password = (settings.SMTP_PASSWORD or "").strip()
    sender = (settings.SMTP_FROM or user or "").strip()

    if not host or not user or not password:
        log.warning(
            "SMTP not configured (SMTP_HOST=%r SMTP_USER=%r). "
            "Cannot deliver email to %r with subject %r.",
            host or "<unset>",
            user or "<unset>",
            to,
            subject,
        )
        raise RuntimeError(
            "SMTP credentials are not configured. "
            "Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, and SMTP_FROM in .env."
        )

    try:
        port = int(settings.SMTP_PORT or 587)
    except (TypeError, ValueError):
        port = 587

    use_tls = str(getattr(settings, "SMTP_TLS", "true")).lower() not in ("false", "0", "no")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    if html:
        # H-5 FIX: attach a real plain-text fallback — never raw HTML markup.
        fallback = (plain_body or "").strip() or _html_to_text(body)
        msg.attach(MIMEText(fallback, "plain"))
        msg.attach(MIMEText(body, "html"))
    else:
        msg.attach(MIMEText(body, "plain"))

    log.info("Sending email to=%r subject=%r via %s:%d (TLS=%s)", to, subject, host, port, use_tls)

    try:
        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.sendmail(sender, [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.login(user, password)
                smtp.sendmail(sender, [to], msg.as_string())
    except smtplib.SMTPException:
        log.exception("SMTP delivery failed for to=%r subject=%r", to, subject)
        raise

    log.info("Email delivered to=%r subject=%r", to, subject)
