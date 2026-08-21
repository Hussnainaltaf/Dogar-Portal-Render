#!/usr/bin/env python3
"""Secret ratchet: fail the build if a credential lands in a committable file.

WHY THIS EXISTS
---------------
On 2026-08-13 the bootstrap admin password was found hardcoded in
``scripts/ui_verify.mjs`` (two call sites) and printed verbatim in a table in
``FULL_AUDIT_REPORT_2026-08-05.md``. Both files are tracked in git, so the
production admin credential was readable by anyone with repo access, and lived
in every clone, fork, release archive and CI artifact.

The 681-test suite was green throughout. Nothing in it looked at credentials, so
nothing could have caught this. This scanner closes that gap permanently.

WHAT IT CHECKS
--------------
1. **Known-compromised literals.** The specific strings burned in the incident.
   These must never appear anywhere in the tree again, in any file type,
   including hidden files -- not even in ``.env``, because they were rotated and
   restoring them would restore the compromise.

2. **Live ``.env`` secret values.** Every value of a secret-looking key in
   ``.env`` (``*PASSWORD``, ``SECRET*``, ``*_KEY``, ``*TOKEN``) is searched for
   across the tree. ``.env`` itself is where they belong; a hit anywhere else --
   source, docs, fixtures, the built JS bundle, a log, a backup -- is a leak.
   This generalises the ratchet: it guards secrets that do not exist yet.

3. **Generic assignment patterns.** ``password = "literal"`` style assignments
   in source, so a *new* hardcoded credential is flagged even if it never
   appears in ``.env``.

SCOPE
-----
Walks the whole repository including dotfiles, skipping only directories that
cannot be committed or are third-party: ``.git``, ``venv``, ``node_modules``,
``__pycache__``, ``.pytest_cache``, ``.ruff_cache``, ``.wrangler``, ``dist``.

``app/static/spa/assets`` IS scanned on purpose: a secret compiled into the
browser bundle is served to every visitor, which is strictly worse than one
sitting in a source file.

USAGE
-----
    python scripts/scan_committed_secrets.py            # human report
    python scripts/scan_committed_secrets.py --json     # machine-readable
    python scripts/scan_committed_secrets.py --check    # exit 1 on any finding

``--check`` is what ``tests/test_no_committed_secrets.py`` runs.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

# ---------------------------------------------------------------------------
# 1. Known-compromised literals
# ---------------------------------------------------------------------------
# These were exposed in a chat transcript and committed to the repository. They
# are permanently burned. Adding a value here is a one-way ratchet: never
# remove an entry, because "we rotated away from it" is exactly when someone
# pastes it back in as a convenient test fixture.
#
# Each entry is stored SPLIT so this scanner file does not itself contain the
# literal it bans -- otherwise the scanner would flag itself, and a naive fix
# would be to weaken the scanner.
COMPROMISED_LITERALS: tuple[tuple[str, ...], ...] = (
    ("Dogar", "Admin@", "2026!"),
    ("Dogar", "Vendor@", "2026!"),
)

# ---------------------------------------------------------------------------
# 2. Which .env keys hold secrets
# ---------------------------------------------------------------------------
_SECRET_KEY_PATTERN = re.compile(
    r"(PASSWORD|SECRET|_KEY$|TOKEN|CREDENTIAL)", re.IGNORECASE
)

# Values below this length are too short to be meaningful secrets and would
# generate noise (e.g. an empty OPENAI_API_KEY, or SMTP_PORT).
_MIN_SECRET_LEN = 12

# Placeholder values that ship in .env.example and are not real secrets.
# Matching one of these means the key is *unconfigured*, so its "value" being
# repeated in .env.example / config.py placeholder lists is correct, not a leak.
_PLACEHOLDER_VALUES = {
    "change-this-secret-key-in-production",
    "changeme",
    "your-password-here",
    "REPLACE_ME",
}
_PLACEHOLDER_SHAPE = re.compile(
    r"""^(
        your[_-].*                 # your_smtp_password, your-api-key
        |<.*>                      # <redacted>
        |REPLACE_ME.*
        |.*(?:placeholder|example|dummy|changeme|xxxx+|\.\.\.).*
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# 3. Generic hardcoded-credential assignment patterns
# ---------------------------------------------------------------------------
# Matches `password: 'x'`, `password = "x"`, `pw="x"`, `passwd = 'x'` where the
# value is a non-trivial literal. Deliberately narrow: broad patterns produce
# noise, and a noisy ratchet gets disabled.
_ASSIGNMENT = re.compile(
    r"""(?P<key>\b(?:password|passwd|admin_pw|admin_password)\b)
        \s*[:=]\s*
        (?P<quote>['"])
        (?P<value>[^'"\n]{8,})
        \2""",
    re.IGNORECASE | re.VERBOSE,
)

# Assignment values that are obviously not credentials.
#
# NOTE the whitespace rule below. The first draft of this scanner flagged
# `errs.password = 'Password is required.'` in Login.tsx -- a form-validation
# message, not a credential. Six such false positives across four files is
# exactly how a ratchet earns a blanket suppression and stops protecting
# anything at all. A
# credential written into source is a single opaque token; user-facing prose is
# multiple words. Requiring the value to contain no whitespace removes the
# entire class of message/label false positives.
#
# Trade-off, stated explicitly: a hardcoded passphrase *containing spaces*
# would slip past rule 3. It would still be caught by rule 2 the moment it is
# the live .env value, and by rule 1 if it is a burned literal. Rule 3 is the
# speculative net, not the primary guard.
_ASSIGNMENT_ALLOW = re.compile(
    r"""^(
        .*\s.*            # contains whitespace -> prose, not a credential
        |\$\{.*\}         # template interpolation
        |process\.env.*   # env read
        |os\.environ.*
        |settings\..*
        |<.*>             # <redacted> / <your-password>
        |\*+              # ****
        |your[_-].*       # your_smtp_password
        |.*(?:redacted|placeholder|example|dummy|changeme|REPLACE).*
        |[A-Z_]{4,}       # ENV_VAR_NAME used as a value
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

SKIP_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".wrangler",
    "dist", ".pm2", "htmlcov", ".idea", ".vscode",
}

# Files exempt from the *generic assignment* rule only (rules 1 and 2 still
# apply to them). These legitimately discuss password handling in prose or
# assert on test-only values.
_ASSIGNMENT_EXEMPT_SUFFIXES = (".md", ".txt", ".lock", ".map")


def _iter_files() -> list[Path]:
    out: list[Path] = []
    stack = [REPO_ROOT]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            continue
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if p.name not in SKIP_DIRS:
                    stack.append(p)
                continue
            out.append(p)
    return sorted(out)


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > 8_000_000:
            return None
        return path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return None  # binary or unreadable -- nothing to leak in text form


def _env_secret_values() -> dict[str, str]:
    """Map .env secret key -> value, for values long enough to matter."""
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(errors="replace").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip().strip("'\"")
        if not _SECRET_KEY_PATTERN.search(key):
            continue
        if len(value) < _MIN_SECRET_LEN or value in _PLACEHOLDER_VALUES:
            continue
        if _PLACEHOLDER_SHAPE.match(value):
            continue
        out[key] = value
    return out


def scan() -> list[dict]:
    findings: list[dict] = []
    literals = ["".join(parts) for parts in COMPROMISED_LITERALS]
    env_secrets = _env_secret_values()
    self_path = Path(__file__).resolve()

    for path in _iter_files():
        text = _read(path)
        if text is None:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        is_self = path.resolve() == self_path

        # --- rule 1: known-compromised literals (everywhere, incl. .env) ---
        if not is_self:
            for lit in literals:
                for lineno, line in enumerate(text.splitlines(), 1):
                    if lit in line:
                        findings.append({
                            "rule": "compromised-literal",
                            "file": rel,
                            "line": lineno,
                            "detail": (
                                "a known-compromised credential literal is "
                                "present; it was rotated and must never return"
                            ),
                        })

        # --- rule 2: live .env secret values outside .env -------------------
        if rel != ".env" and not is_self:
            for key, value in env_secrets.items():
                for lineno, line in enumerate(text.splitlines(), 1):
                    if value in line:
                        findings.append({
                            "rule": "env-secret-leaked",
                            "file": rel,
                            "line": lineno,
                            "detail": (
                                f"the live value of .env {key} appears here; "
                                "secrets must exist only in .env"
                            ),
                        })

        # --- rule 3: generic hardcoded credential assignment ---------------
        if is_self or path.suffix in _ASSIGNMENT_EXEMPT_SUFFIXES or rel == ".env":
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*")):
                continue
            for m in _ASSIGNMENT.finditer(line):
                value = m.group("value")
                if _ASSIGNMENT_ALLOW.match(value):
                    continue
                findings.append({
                    "rule": "hardcoded-credential",
                    "file": rel,
                    "line": lineno,
                    "detail": (
                        f"{m.group('key')} is assigned a string literal; read "
                        "it from the environment instead"
                    ),
                })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check", action="store_true", help="exit 1 on any finding")
    args = ap.parse_args()

    findings = scan()

    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    else:
        if findings:
            print(f"SECRET SCAN: {len(findings)} finding(s)\n")
            for f in findings:
                print(f"  [{f['rule']}] {f['file']}:{f['line']}")
                print(f"      {f['detail']}")
        else:
            print("SECRET SCAN: 0 findings "
                  "(no compromised literals, no leaked .env values, "
                  "no hardcoded credentials)")

    if args.check and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
