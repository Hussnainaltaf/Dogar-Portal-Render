"""Application configuration loaded from environment variables.

Production-hardening rules enforced here (when ``ENV=production``):
  * SECRET_KEY must NOT be the placeholder / "change-this-…" value
  * SECRET_KEY must be at least 32 chars (256 bits)
  * DEFAULT_ADMIN_PASSWORD must NOT be one of the known weak defaults
  * DEFAULT_ADMIN_PASSWORD must be at least 12 chars

Violations raise ``RuntimeError`` at startup so a misconfigured
production deploy fails loudly instead of silently exposing the
documented credentials.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

# AUDIT 2026-07-29: the directory settings below default to project-relative
# strings ("app/static/uploads", "data/…"). get_settings() os.makedirs() them at
# IMPORT time, so with the wrong working directory the app did not merely
# mis-resolve them -- it tried to create "app/" inside whatever directory it had
# been launched from and died with PermissionError before serving a single
# request. Anchoring on __file__ keeps the shipped defaults working from any CWD
# while leaving an operator's explicit absolute override untouched.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _anchor(path: str) -> str:
    """Resolve a project-relative directory against the project root.

    Absolute paths are returned unchanged so an operator who points UPLOAD_DIR
    at /var/lib/dogar/uploads keeps exactly that.
    """
    p = Path(path).expanduser()
    return str(p if p.is_absolute() else (_PROJECT_ROOT / p))


def anchor_project_path(path: str) -> str:
    """Public alias of :func:`_anchor` for use outside this module.

    ``app.core.tenancy`` needs the same project-root anchoring for tenant SQLite
    paths (the ``tenants.db_path`` column stores relative values like
    ``data/tenants/tenant_acme.db``), and duplicating the root calculation there
    would let the two drift apart.
    """
    return _anchor(path)


def _anchor_sqlite_url(url: str) -> str:
    """Rewrite a CWD-relative ``sqlite:///`` URL to an absolute project path.

    Non-SQLite URLs (postgresql://…), in-memory URLs and already-absolute paths
    are returned untouched. SQLite URL forms:

        sqlite:///relative/path.db   -> 3 slashes + relative  (rewritten)
        sqlite:////absolute/path.db  -> 4 slashes = absolute   (left alone)
        sqlite:///:memory:           -> in-memory              (left alone)
    """
    if not url.startswith("sqlite"):
        return url
    prefix, sep, rest = url.partition(":///")
    if not sep or not rest or rest.startswith(":memory:") or "mode=memory" in rest:
        return url
    # A leading "/" here means the original had four slashes: already absolute.
    if rest.startswith("/"):
        return url
    path = Path(rest.split("?", 1)[0]).expanduser()
    query = rest[len(rest.split("?", 1)[0]):]
    if path.is_absolute():
        return url
    return f"{prefix}:///{(_PROJECT_ROOT / path).resolve()}{query}"


log = logging.getLogger("dtc.config")

SQLITE_URL = "sqlite:///./data/dogar_trading.db"

# Values we MUST refuse in production -- these have appeared in the
# public repo / docs at some point so anyone could try them.
_FORBIDDEN_SECRETS = {
    "change-this-secret-key-in-production",
    "change-this-secret-key-in-production-use-openssl-rand-hex-32",
}
_FORBIDDEN_ADMIN_PASSWORDS = {
    "admin123",
    "admin",
    "password",
    "123456",
    "changeme",
    "change-this-password",
}

# Placeholder SMTP values shipped in .env.example. They are non-empty, so a
# naive `if SMTP_HOST and SMTP_USER` truthiness check treats them as "email is
# configured" — the app then tries a real send, fails to authenticate, and the
# password-reset flow breaks in a much more confusing way than if SMTP had been
# left blank. Treat them as "not configured" so the honest 503 fires instead.
# (audit LOW follow-up, 2026-07-26)
_PLACEHOLDER_SMTP_VALUES = {
    "smtp.yourprovider.com",
    "smtp.example.com",
    "noreply@yourcompany.com",
    "noreply@example.com",
    "your_smtp_password",
    "changeme",
}


def _is_placeholder(value: str) -> bool:
    return (value or "").strip().lower() in _PLACEHOLDER_SMTP_VALUES


class Settings(BaseSettings):
    APP_NAME: str = "Dogar Trading Corporation Portal"
    APP_VERSION: str = "1.1.0"
    # ── React SPA (the ONLY frontend) ────────────────────────────────────────
    # The React SPA is served from app/static/spa. All Jinja2 templates have
    # been removed. This flag is kept for backward compatibility only.
    USE_SPA: bool = True
    SECRET_KEY: str = "change-this-secret-key-in-production"
    # AUDIT M-1 (2026-07-10): access tokens are stateless (no server-side
    # revocation), so a long lifetime widens the window in which a stolen/leaked
    # token stays valid after logout. Reduced the default from 1440 min (24h) to
    # 120 min (2h) — a sane balance between security and re-login friction.
    # Operators can override via ACCESS_TOKEN_EXPIRE_MINUTES in .env.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    DATABASE_URL: str = SQLITE_URL

    # ── Tenant database URL template (Postgres deployment) ────────────────────
    # Option A: one PostgreSQL database per tenant, mirroring the previous
    # SQLite file-per-tenant isolation model. When TENANT_DB_URL_TEMPLATE is
    # set, the tenancy layer uses it to derive each tenant's connection URL:
    #     postgresql://user:pw@host:5432/tenant_{slug}
    # The ``{slug}`` placeholder is required and is replaced with the tenant's
    # slug at engine-build time. When this setting is empty (default), the
    # ``db_path`` column on ``tenants`` is used directly (SQLite behaviour).
    #
    # Examples:
    #   TENANT_DB_URL_TEMPLATE=postgresql://dogar:pw@localhost:5432/tenant_{slug}
    #   TENANT_DB_URL_TEMPLATE=postgresql+psycopg2://user:pw@pg:5432/{slug}
    #
    # When migrating a live SQLite deployment to Postgres, run:
    #   scripts/migrate_to_postgres.py --dry-run   # verify tenant list first
    #   scripts/migrate_to_postgres.py             # actually copy every DB
    TENANT_DB_URL_TEMPLATE: str = ""
    UPLOAD_DIR: str = "app/static/uploads"
    PDF_BG_DIR: str = "app/static/pdf_backgrounds"
    DEFAULT_ADMIN_EMAIL: str = "admin@dogartrading.com"
    # AUDIT M-5 (2026-07-10): the source default was the well-known weak value
    # "admin123". If an operator forgot to set DEFAULT_ADMIN_PASSWORD in .env, the
    # seeder would create a real admin with that password. The default is now an
    # empty string; seed_admin_user() SKIPS creating the bootstrap admin when it
    # is empty (see app/db/init_db.py), so no weak-credential account can ever be
    # created implicitly. Set a strong password (>=12 chars) in .env to seed one.
    DEFAULT_ADMIN_PASSWORD: str = ""
    DEFAULT_ADMIN_NAME: str = "Administrator"
    COMPANY_NAME: str = "Dogar Trading Corporation"
    COMPANY_TAGLINE: str = "Global Trading & Overseas Employment Solutions"
    ALGORITHM: str = "HS256"
    # FIX-08: Vendor/developer super-admin credentials.
    # Must be set in production .env. Previously read via os.environ.get()
    # in init_db.py which doesn't load from .env via pydantic-settings --
    # only real OS environment variables were found, not .env-file values.
    # Now declared here so pydantic-settings loads them from .env properly.
    VENDOR_ADMIN_EMAIL: str = ""
    VENDOR_ADMIN_PASSWORD: str = ""
    VENDOR_ADMIN_NAME: str = "Vendor Support"
    # ── SMTP / Email (for password-reset delivery) ────────────────────────────
    # FIX-09: Password reset tokens are generated but never sent without SMTP.
    # Set these in production .env to enable the email delivery.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_TLS: bool = True
    # ── Twilio SMS / WhatsApp (real provider integration — Task 9) ─────────────
    # Server-side ONLY. These credentials are NEVER exposed to the browser; the
    # frontend calls /api/messaging/* which uses them behind the auth layer.
    # Set in production .env (or via `wrangler secret`-equivalent) to enable
    # real message delivery. Leave blank to run in "dry-run" mode: the API
    # records the message with status="dry_run" and returns success without
    # actually hitting Twilio (so demos/tests never send real texts or cost money).
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    # Sender identities. SMS: an E.164 number you own on Twilio (e.g. +12025550123).
    # WhatsApp: your approved WhatsApp sender, prefixed "whatsapp:" — the service
    # adds the prefix automatically if omitted (e.g. +14155238886 or the sandbox).
    TWILIO_SMS_FROM: str = ""
    TWILIO_WHATSAPP_FROM: str = ""
    # Optional Messaging Service SID (overrides *_FROM when set, for SMS).
    TWILIO_MESSAGING_SERVICE_SID: str = ""
    # ── Redis cache (optional) ─────────────────────────────────────────────
    # If set, the app caches candidate/demand list and dashboard responses
    # for 60 seconds per tenant. Leave blank to run without Redis (no-op
    # cache stub used automatically — the app boots and functions normally).
    REDIS_URL: str = ""
    # Optional explicit base URL — used to build absolute links in email templates
    # (password reset, notifications). If not set, the link is constructed from
    # the incoming request's base_url which is correct in most deployments.
    # Set this when the app sits behind a reverse proxy that rewrites Host headers.
    # Example: APP_BASE_URL=https://portal.dogartrading.com
    APP_BASE_URL: str = ""
    # Runtime mode -- controls strictness of the security guardrails.
    # Allowed: "development", "staging", "production"
    ENV: str = "development"
    # Comma-separated Host header allowlist used by TrustedHostMiddleware.
    # Keep this explicit in production to prevent host-header spoofing and
    # cross-tenant routing bypasses. Override in .env for custom domains.
    #
    # SECURITY (audit 2026-07-07): the production default must NOT contain broad
    # shared-PaaS wildcards (``*.railway.app``, ``*.onrender.com``, ``*.fly.dev``,
    # etc.). On a shared PaaS those wildcards would accept the Host
    # header of *any* tenant on that provider, which is broader than a
    # distributable production default should be. Those preview/PaaS wildcards
    # are now injected ONLY in non-production mode by ``allowed_hosts`` below.
    # The baked-in default here is limited to loopback + the app's real domains;
    # operators set their own domain(s) via the ALLOWED_HOSTS env var.
    ALLOWED_HOSTS: str = (
        "localhost,127.0.0.1,0.0.0.0,testserver,"
        "dogar-trading.com,*.dogar-trading.com,"
        "oep.com.pk,*.oep.com.pk,demo.oep.com.pk"
    )

    # Number of trusted reverse proxies in front of the app (nginx, Cloudflare,
    # a load balancer …). Controls how the client IP is derived from the
    # X-Forwarded-For header for brute-force rate limiting.
    #
    # SECURITY (audit 2026-07-07): X-Forwarded-For is fully client-controlled.
    # A value of 0 (the safe default) means "no trusted proxy" -> ignore XFF
    # entirely and use the real socket peer (request.client.host), so an
    # attacker cannot spoof their IP to evade the login lockout. When you deploy
    # behind N trusted proxies that each APPEND the real client IP, set this to N
    # and the Nth-from-the-right hop is used (the address your own proxy saw).
    TRUSTED_PROXY_COUNT: int = 0

    class Config:
        env_file = ".env"
        extra = "ignore"

    # ------------------------------------------------------------------
    # Production-readiness guard
    # ------------------------------------------------------------------
    def assert_production_safe(self) -> None:
        """Check runtime config for dangerous weak defaults.

        Audit 2026-07-07 (Round 2): the previous version hard-failed on a
        placeholder SECRET_KEY even in development. That was safe but made the
        distributed archive un-plug-and-play — a fresh clone could not boot
        without hand-editing .env first, which forced every evaluator through
        a friction step. New behaviour:

          - Placeholder / missing / too-short SECRET_KEY in PRODUCTION
            → HARD FAIL (unchanged). No legitimate prod deploy should ever
              run with a predictable key.
          - Placeholder / missing / too-short SECRET_KEY in DEV / STAGING
            → auto-generate an ephemeral 64-char random key for this process,
              log a prominent WARNING telling the operator this key is
              per-process (JWTs will not survive a restart), and continue.
              This lets ``python -m uvicorn app.main:app`` boot on a fresh
              clone with zero config while still making the misconfiguration
              impossible to miss in logs.
          - All other weak values (weak admin password) → raise in production,
            log ERROR in dev/staging.
        """
        import os as _os
        import secrets as _secrets  # local import so top-of-file stays clean

        hard_problems: list[str] = []   # always raise
        soft_problems: list[str] = []   # raise in prod, ERROR log in dev
        mode = (self.ENV or "development").strip().lower()
        is_prod = mode == "production"

        # --- SECRET_KEY ---
        secret_bad_reason: str | None = None
        if not self.SECRET_KEY or self.SECRET_KEY in _FORBIDDEN_SECRETS:
            secret_bad_reason = "placeholder / missing"
        elif self.SECRET_KEY.startswith("REPLACE_ME"):
            secret_bad_reason = "unset (still carries the REPLACE_ME_… prefix from .env.example)"
        elif len(self.SECRET_KEY) < 32:
            secret_bad_reason = f"too short ({len(self.SECRET_KEY)} < 32 chars)"

        if secret_bad_reason:
            if is_prod:
                hard_problems.append(
                    f"SECRET_KEY is {secret_bad_reason}. "
                    "Generate one with: "
                    "python3 -c \"import secrets; print(secrets.token_hex(32))\""
                )
            else:
                # Auto-repair: ephemeral per-process key. JWTs signed with it
                # will not survive a restart. This is fine for local dev.
                ephemeral = _secrets.token_hex(32)
                object.__setattr__(self, "SECRET_KEY", ephemeral)
                log.warning(
                    "[%s mode] SECRET_KEY was %s -- generated an ephemeral "
                    "random key for this process. Sessions will not persist "
                    "across restarts. Set SECRET_KEY in .env for a stable dev "
                    "environment (python3 -c 'import secrets; "
                    "print(secrets.token_hex(32))').",
                    mode, secret_bad_reason,
                )

        # --- Admin password ---
        if (self.DEFAULT_ADMIN_PASSWORD or "").strip().lower() in _FORBIDDEN_ADMIN_PASSWORDS:
            soft_problems.append(
                f"DEFAULT_ADMIN_PASSWORD is a known-weak value "
                f"({self.DEFAULT_ADMIN_PASSWORD!r}). Set a strong password in .env."
            )
        elif self.DEFAULT_ADMIN_PASSWORD and len(self.DEFAULT_ADMIN_PASSWORD) < 12:
            # Empty is allowed (M-5): the seeder skips creating a bootstrap admin.
            # A *non-empty but short* password is still weak → flag it.
            soft_problems.append(
                "DEFAULT_ADMIN_PASSWORD must be at least 12 characters (or empty "
                "to skip auto-seeding the bootstrap admin). Update .env."
            )

        # --- ALLOWED_HOSTS (audit M-3) ---
        # In production the Host allowlist must be explicit. Refuse to boot if it
        # is empty (would fall back to loopback-only and reject real traffic) or
        # still contains the catch-all "*" wildcard (defeats TrustedHost/host-
        # header-spoofing protection entirely).
        prod_hosts = [h.strip() for h in (self.ALLOWED_HOSTS or "").split(",") if h.strip()]
        if is_prod:
            if not prod_hosts:
                hard_problems.append(
                    "ALLOWED_HOSTS is empty. Set it to your real public domain(s) "
                    "in .env (comma-separated), e.g. portal.example.com."
                )
            if "*" in prod_hosts:
                hard_problems.append(
                    "ALLOWED_HOSTS contains the catch-all '*' wildcard, which "
                    "disables Host-header validation. List explicit domains instead."
                )

        # --- TESTING flag (audit L-2) ---
        # TESTING=true disables CSRF, rate limiting and account lockout, and leaks
        # the JWT in the login response body. It must NEVER be set in production.
        if is_prod and _os.environ.get("TESTING", "").strip().lower() == "true":
            hard_problems.append(
                "TESTING=true is set in production. This disables CSRF, rate "
                "limiting and login lockout and leaks the JWT in the response "
                "body. Unset TESTING before deploying."
            )

        # --- SMTP / password-reset delivery (audit LOW, 2026-07-26) ---
        # Not a security flaw: /api/auth/forgot-password deliberately fails
        # CLOSED with 503 when it cannot send, rather than telling the user a
        # reset link "was sent" into the void. But it IS a production-readiness
        # dependency, and previously the only signal was a WARNING logged the
        # first time a locked-out user tried to reset — i.e. discovered by an
        # end user, not by the operator. Report it at boot instead.
        #
        # Deliberately NOT a hard failure: plenty of legitimate deployments do
        # password resets out-of-band (an admin resets from the Users screen),
        # and refusing to boot over an optional feature would be worse than the
        # gap it closes.
        if not self.email_configured:
            log.warning(
                "[%s mode] SMTP is not configured (SMTP_HOST/SMTP_USER are "
                "empty or still hold .env.example placeholders), so "
                "self-service password reset is DISABLED: "
                "/api/auth/forgot-password will return 503 and users cannot "
                "reset their own passwords. Either set SMTP_HOST, SMTP_PORT, "
                "SMTP_USER, SMTP_PASSWORD and SMTP_FROM in .env, or plan to "
                "reset passwords for users from the admin Users screen. "
                "Readiness is reported at GET /api/health "
                "(password_reset field).",
                mode,
            )

        # Hard problems always raise
        if hard_problems:
            raise RuntimeError(
                "SECURITY BLOCKER -- fix before starting the app:\n  - "
                + "\n  - ".join(hard_problems)
            )

        # Soft problems: raise in production, ERROR-log in all other modes
        if soft_problems:
            msg = (
                "Weak configuration detected:\n  - "
                + "\n  - ".join(soft_problems)
            )
            if is_prod:
                raise RuntimeError(msg)
            # In dev/staging: log at ERROR so it is impossible to miss in any
            # log aggregator, but do not crash the dev server.
            log.error("[%s mode] %s", mode, msg)

    @property
    def is_production(self) -> bool:
        # Normalize casing AND surrounding whitespace so ENV=" Production " or
        # "PRODUCTION" is still treated as production (audit 2026-07-19 #1/#2).
        return (self.ENV or "").strip().lower() == "production"

    @property
    def email_configured(self) -> bool:
        """True when outbound email can plausibly be sent.

        Single source of truth for "is email set up?", used by the boot-time
        readiness warning, GET /api/health and /api/auth/forgot-password. It was
        previously an inline ``SMTP_HOST and SMTP_USER`` truthiness test repeated
        at each site, which accepted the placeholder values shipped in
        .env.example and so reported email as available on a server that could
        not authenticate to any mail host. (audit LOW follow-up, 2026-07-26)
        """
        host = (self.SMTP_HOST or "").strip()
        user = (self.SMTP_USER or "").strip()
        if not host or not user:
            return False
        return not (_is_placeholder(host) or _is_placeholder(user))

    @property
    def allowed_hosts(self) -> list[str]:
        """Return normalized TrustedHostMiddleware patterns.

        In non-production, local/sandbox preview hosts are allowed so QA can run
        without weakening the production defaults. In production, operators must
        set ALLOWED_HOSTS to their real public domains if they differ from the
        bundled Dogar/OEP domains.
        """
        hosts = [h.strip().lower() for h in (self.ALLOWED_HOSTS or "").split(",") if h.strip()]
        if not hosts:
            hosts = ["localhost", "127.0.0.1"]
        # Preview / sandbox / shared-PaaS wildcards are added ONLY when preview
        # mode is active (see ``_preview_hosts_enabled``). This keeps them out of
        # the shipped production default (so a distributable build does not trust
        # every tenant on railway.app / onrender.com / fly.dev), while still
        # letting hosted-preview URLs work in the sandbox and in any
        # non-production run.
        if self._preview_hosts_enabled:
            _preview = [
                "localhost", "127.0.0.1", "0.0.0.0", "testserver",
                "*.localhost", "*.local", "*.e2b.dev",
                "*.sandbox.novita.ai",
                "*.railway.app", "*.onrender.com", "*.fly.dev",
            ]
            # Operators can extend the preview allowlist without code changes
            # (comma-separated wildcards, e.g. "*.my-preview-host.com").
            import os as _os
            _extra = [h.strip().lower()
                      for h in _os.environ.get("EXTRA_PREVIEW_HOSTS", "").split(",")
                      if h.strip()]
            for h in (*_preview, *_extra):
                if h not in hosts:
                    hosts.append(h)
        return hosts

    @property
    def _preview_hosts_enabled(self) -> bool:
        """Whether to widen the Host allowlist with preview/sandbox/PaaS wildcards.

        True when EITHER:
          * the app is not in production (normal local/QA runs), OR
          * an explicit opt-in flag ``ALLOW_PREVIEW_HOSTS=true`` is set, OR
          * a known managed-sandbox env var is present, so the hosted
            preview keeps working even though the live preview sets
            ENV=production.

        A plain, self-hosted production deployment (no sandbox env vars, flag
        unset) gets NONE of the broad wildcards — only its own ALLOWED_HOSTS.
        """
        import os as _os
        if not self.is_production:
            return True
        if (_os.environ.get("ALLOW_PREVIEW_HOSTS", "").strip().lower()
                in {"1", "true", "yes", "on"}):
            return True
        # Auto-detect the managed sandbox the live preview runs in.
        _sandbox_markers = (
            "E2B_SANDBOX", "E2B_SANDBOX_ID", "NOVITA_SANDBOX_ID",
            "SANDBOX_ID",
        )
        return any(_os.environ.get(m) for m in _sandbox_markers)


@lru_cache
def get_settings() -> Settings:
    s = Settings()

    # Audit fix: the original code silently discarded any PostgreSQL DATABASE_URL
    # and replaced it with the local SQLite path. This meant an operator who
    # correctly set DATABASE_URL=postgresql://... in production would see their
    # real database silently ignored and the app would create/use a local SQLite
    # file instead -- a silent data-loss and misconfiguration bug.
    #
    # The override has been removed. The DATABASE_URL is now used exactly as
    # supplied. If PostgreSQL support is not yet implemented, the operator should
    # not set a postgres:// URL; the .env.example ships with the SQLite default.
    #
    # Note: the app currently only uses SQLAlchemy with SQLite drivers. Full
    # PostgreSQL support (asyncpg / psycopg2) is a planned roadmap item. Until
    # that work is done, keep DATABASE_URL as the SQLite path in .env.

    # Anchor project-relative directories on the project root (see _anchor).
    # Done here rather than in the field defaults so a value supplied via .env
    # or the environment gets the same treatment as the shipped default.
    s.UPLOAD_DIR = _anchor(s.UPLOAD_DIR)
    s.PDF_BG_DIR = _anchor(s.PDF_BG_DIR)

    # AUDIT 2026-08-01 (Medium): the SQLite DATABASE_URL was left CWD-relative
    # ("sqlite:///./data/dogar_trading.db"). The directory *containing* it was
    # anchored below, which made this look handled -- but SQLAlchemy resolves the
    # URL itself against the process CWD, so launching from anywhere other than
    # the project root died with:
    #     sqlite3.OperationalError: unable to open database file
    # (reproduced). In the worse variant, where the alien CWD happens to be
    # writable and contains a `data/` directory, SQLite silently CREATES an empty
    # database and the app boots to a working login page with zero records --
    # indistinguishable from data loss.
    #
    # A systemd unit missing WorkingDirectory=, a cron entry, or a container with
    # a different WORKDIR all hit this. Note that tests/test_portability.py
    # existed and passed: it checked template/static/SPA paths and never the
    # database URL, so it gave false confidence. It now covers this too.
    s.DATABASE_URL = _anchor_sqlite_url(s.DATABASE_URL)

    # Ensure directories exist (SQLite needs the data/ folder)
    if s.DATABASE_URL.startswith("sqlite"):
        db_path = s.DATABASE_URL.replace("sqlite:///", "").replace("./", "")
        os.makedirs(_anchor(os.path.dirname(db_path) or "data"), exist_ok=True)
    os.makedirs(s.UPLOAD_DIR, exist_ok=True)
    os.makedirs(s.PDF_BG_DIR, exist_ok=True)

    # Enforce production guardrails (or log warnings outside prod).
    s.assert_production_safe()
    return s


settings = get_settings()
