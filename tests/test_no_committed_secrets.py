"""Regression guard: credentials must never be committed to this repository.

INCIDENT THIS TEST EXISTS FOR (2026-08-13)
------------------------------------------
The bootstrap admin password was found:

  * hardcoded in ``scripts/ui_verify.mjs`` at two call sites (tracked in git);
  * printed verbatim in a configuration table in
    ``FULL_AUDIT_REPORT_2026-08-05.md`` (tracked in git).

The vendor super-admin password was in the same markdown table.

The suite was at 681 passing tests when this was discovered. Not one of them
inspected credentials, so a live production password sitting in version control
was invisible to the entire test stack. Green tests are not evidence of
security; they are evidence of what you chose to assert.

Remediation performed:
  1. literals removed from both files;
  2. ``ui_verify.mjs`` now sources credentials from ``$ADMIN_EMAIL`` /
     ``$ADMIN_PW`` or ``.env``, with no fallback -- it exits 2 rather than guess;
  3. both accounts rotated via ``scripts/rotate_bootstrap_passwords.py``, the
     project's own user-management path;
  4. ``SECRET_KEY`` rotated, invalidating every previously issued
     ``access_token`` JWT (the app's only session credential);
  5. outstanding ``password_reset_tokens`` revoked;
  6. this test file, so it cannot happen again silently.

WHAT THIS FILE ASSERTS
----------------------
Structural facts about the repository, backed by
``scripts/scan_committed_secrets.py``. Every assertion here fails if its
protection is removed -- see ``test_the_scanner_actually_detects_a_planted_secret``
and ``test_the_scanner_detects_a_planted_env_value``, which prove the scanner is
not vacuously reporting zero.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "scripts" / "scan_committed_secrets.py"

# The burned literals, stored split so this test file does not itself contain
# the strings it bans (which would make the scanner flag the guard).
_BURNED = (
    ("Dogar", "Admin@", "2026!"),
    ("Dogar", "Vendor@", "2026!"),
)
BURNED_LITERALS = ["".join(parts) for parts in _BURNED]

SKIP_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".wrangler",
    "dist", ".pm2", "htmlcov",
}


def _run_scanner(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )


def _iter_text_files():
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
            try:
                if p.stat().st_size > 8_000_000:
                    continue
                yield p, p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue


# ---------------------------------------------------------------------------
# The scanner exists and passes
# ---------------------------------------------------------------------------

def test_the_secret_scanner_exists():
    assert SCANNER.is_file(), (
        f"{SCANNER} is missing. It is the ratchet that keeps credentials out of "
        "version control; without it this test file asserts nothing enforceable."
    )


def test_the_repository_contains_no_committed_secrets():
    """The primary gate. Runs the real scanner in --check mode."""
    proc = _run_scanner("--check")
    assert proc.returncode == 0, (
        "scripts/scan_committed_secrets.py --check found committed "
        f"credential(s):\n\n{proc.stdout}\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# The burned literals are gone from every file, checked independently of the
# scanner so a bug in the scanner cannot hide the original defect
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("literal", BURNED_LITERALS,
                         ids=["admin-literal", "vendor-literal"])
def test_no_file_contains_a_burned_credential_literal(literal):
    """Independent sweep, not delegated to the scanner.

    Deliberately duplicates rule 1 of the scanner. If the scanner regresses
    (a broken regex, an over-broad skip list), this still fails.
    """
    hits = []
    for path, text in _iter_text_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        # The scanner and this test store the literal split; neither contains it
        # whole, so neither should ever be a hit. Anything else is a real leak.
        if rel in ("scripts/scan_committed_secrets.py",
                   "tests/test_no_committed_secrets.py"):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if literal in line:
                hits.append(f"{rel}:{lineno}")
    assert not hits, (
        "A known-compromised credential literal is present in:\n  "
        + "\n  ".join(hits)
        + "\n\nThis credential was rotated after being exposed. It must never "
          "be reintroduced -- not as a default, not as a test fixture, not in "
          "documentation."
    )


def test_the_literal_sweep_is_not_vacuous():
    """Anti-vacuity: prove the sweep reads a meaningful number of files.

    A typo in SKIP_DIRS, or a repo-root mistake, would make the walk yield
    nothing -- and a sweep over zero files reports zero leaks.
    """
    count = sum(1 for _ in _iter_text_files())
    assert count > 200, (
        f"The file walk only found {count} readable text files. The repository "
        "has far more than that, so the walk is broken and its clean result is "
        "meaningless."
    )


# ---------------------------------------------------------------------------
# The specific files from the incident
# ---------------------------------------------------------------------------

def test_ui_verify_sources_credentials_from_the_environment():
    """``scripts/ui_verify.mjs`` must not carry credentials in source."""
    src = (REPO_ROOT / "scripts" / "ui_verify.mjs").read_text()
    assert "process.env.ADMIN_PW" in src, (
        "ui_verify.mjs no longer reads $ADMIN_PW. It previously hardcoded the "
        "admin password; credentials must come from the environment or .env."
    )
    # No quoted literal passed as the 4th positional arg of checkPortal().
    bad = re.findall(r"checkPortal\([^)]*,\s*'[^']{6,}'\s*\)", src)
    assert not bad, (
        "checkPortal() is being called with a hardcoded credential literal: "
        f"{bad}"
    )


def test_the_audit_report_does_not_print_credential_values():
    """The audit report must record assessments, never secret values."""
    text = (REPO_ROOT / "FULL_AUDIT_REPORT_2026-08-05.md").read_text()
    for line in text.splitlines():
        if "DEFAULT_ADMIN_PASSWORD" in line or "VENDOR_ADMIN_PASSWORD" in line:
            if line.strip().startswith("|"):
                assert "redacted" in line.lower(), (
                    "A configuration table row in FULL_AUDIT_REPORT_2026-08-05.md "
                    f"appears to print a credential value:\n  {line}\n"
                    "Record the assessment (length / strength), not the secret."
                )


def _git_is_usable() -> tuple[bool, str]:
    """Can git answer questions about THIS tree, or is there no repository?

    Returns ``(True, "")`` inside a working tree, else ``(False, reason)``.
    Factored out so both answers are directly testable; a probe that can only
    ever answer one way is not a probe.
    """
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return False, (
            "not a git working tree (rc="
            f"{probe.returncode}), so git cannot be asked whether .env is "
            "ignored or tracked; the .gitignore text is asserted directly "
            "instead"
        )
    if probe.stdout.strip() != "true":
        return False, f"git reports this is not a work tree: {probe.stdout.strip()!r}"
    return True, ""


def _gitignore_has_an_env_rule() -> bool:
    """Does .gitignore carry a rule that would match ``.env``?

    A deliberately literal reading of the ignore file, used when git itself is
    unavailable. It accepts the handful of spellings that actually match .env
    and rejects ``.env.example``-only rules, which do NOT protect the secrets.
    """
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.is_file():
        return False
    for raw in gitignore.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in {".env", "/.env", "*.env", ".env*", "/.env*", ".env.*"}:
            return True
    return False


def test_env_is_gitignored_and_untracked():
    """``.env`` holds the live secrets; it must never be committable.

    MEASURED 2026-08-18 while verifying the release ZIP. The archive correctly
    ships no ``.git`` directory, so inside an extracted release both git
    queries below returned 128 ("fatal: not a git repository"). The first
    assertion read that as ".env is NOT matched by .gitignore" and failed the
    packaged suite with a false alarm about committable secrets. The second
    assertion was worse: ``git ls-files --error-unmatch`` also exits 128
    without a repository, so ``!= 0`` PASSED for entirely the wrong reason --
    a green result that proved nothing.

    Both are now gated on an explicit repository probe. Where git cannot
    answer, the ignore rule is asserted straight from the .gitignore text
    rather than skipped, so the protection is still positively verified inside
    a packaged build.
    """
    usable, why = _git_is_usable()

    if not usable:
        assert _gitignore_has_an_env_rule(), (
            f"{why}, and .gitignore carries no rule matching .env. The live "
            "SECRET_KEY, admin password and SMTP password would be "
            "committable in any checkout made from this tree."
        )
        # There is no index to consult, so "untracked" is verified the only way
        # available here: the archive/tree must not carry a .env at all.
        assert not (REPO_ROOT / ".env").exists() or _gitignore_has_an_env_rule(), (
            "a .env is present in a tree with no git repository and no ignore "
            "rule to protect it."
        )
        return

    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=str(REPO_ROOT), capture_output=True,
    )
    assert ignored.returncode == 0, (
        ".env is NOT matched by .gitignore. The live SECRET_KEY, admin password "
        "and SMTP password would be committable."
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    # rc 128 would mean "no repository", which is not evidence of untracked;
    # that case is handled above, so here a non-zero rc is a real answer.
    assert tracked.returncode != 0, (
        ".env is TRACKED in git. Remove it from the index immediately: "
        "git rm --cached .env"
    )


def test_the_git_availability_probe_answers_both_ways(tmp_path):
    """The probe that gates the .env checks must be able to say "no".

    Without this, the fallback branch added above could never be exercised and
    a future refactor could quietly make ``_git_is_usable`` always true --
    restoring the false alarm this test module was fixed for.

    MEASURED 2026-08-18: an earlier version of this test asserted that
    ``REPO_ROOT`` itself is a git work tree. That is an assertion about the
    ENVIRONMENT, not about the probe, and it is false by design inside a
    packaged release -- which ships no ``.git``. It duly failed the packaged
    suite for a second false reason. The positive branch now builds its own
    repository in a temporary directory, so both answers are proven whether or
    not the surrounding tree happens to be a checkout.
    """
    import tests.test_no_committed_secrets as mod

    original = mod.REPO_ROOT
    try:
        # --- negative branch: a bare directory is not a work tree ------------
        bare = tmp_path / "bare"
        bare.mkdir()
        mod.REPO_ROOT = bare
        usable_outside, reason = mod._git_is_usable()
        assert usable_outside is False, (
            "a directory that is not a git work tree was reported as usable"
        )
        assert "work tree" in reason or "working tree" in reason, (
            f"the reason should explain the missing repository, got: {reason!r}"
        )
        # MEASURED 2026-08-18 via mutation MA. Disabling the `returncode != 0`
        # branch did NOT fail this test, because a bare directory also trips
        # the stdout check further down and still returns False -- so the two
        # branches were indistinguishable and one of them was unprotected.
        # They answer genuinely different conditions (git absent / erroring vs
        # a location git can answer for but which is not a work tree), so each
        # is now pinned by the distinct reason it must produce. This branch's
        # contract is that it reports the failing exit status, which is the
        # detail that makes the message actionable.
        assert "rc=" in reason, (
            "a git invocation that FAILED should be reported as such, quoting "
            f"the exit status; got: {reason!r}"
        )

        # --- the other negative branch: git answers, but says "not a work
        # tree". A bare repository is exactly that situation.
        bare_repo = tmp_path / "bare_repo.git"
        init_bare = subprocess.run(
            ["git", "init", "--bare", "-q", str(bare_repo)],
            capture_output=True, text=True,
        )
        if init_bare.returncode == 0:
            mod.REPO_ROOT = bare_repo
            usable_bare, reason_bare = mod._git_is_usable()
            assert usable_bare is False, (
                "a bare repository has no work tree and must not be reported "
                "as usable for .env ignore/tracking questions"
            )
            assert "not a work tree" in reason_bare, (
                "the bare-repository branch should say git answered but there "
                f"is no work tree; got: {reason_bare!r}"
            )

        # --- positive branch: a real repository IS a work tree ---------------
        repo = tmp_path / "repo"
        repo.mkdir()
        init = subprocess.run(
            ["git", "init", "-q"], cwd=str(repo), capture_output=True, text=True,
        )
        if init.returncode != 0:
            pytest.skip(f"git init unavailable here: {init.stderr.strip()}")
        mod.REPO_ROOT = repo
        usable_inside, why_inside = mod._git_is_usable()
        assert usable_inside is True, (
            f"a freshly initialised repository was reported unusable: {why_inside}"
        )
        assert why_inside == "", (
            f"a usable repository should carry no reason, got: {why_inside!r}"
        )
    finally:
        mod.REPO_ROOT = original


def test_the_gitignore_reader_rejects_an_env_example_only_rule(tmp_path):
    """``.env.example`` being ignored does NOT protect ``.env``.

    Pins the fallback assertion's discrimination: it must not be satisfiable
    by an unrelated ignore rule, otherwise the packaged-build branch would
    rubber-stamp a tree whose real secrets are committable.
    """
    import tests.test_no_committed_secrets as mod

    original = mod.REPO_ROOT
    try:
        mod.REPO_ROOT = tmp_path
        (tmp_path / ".gitignore").write_text("# comment\n.env.example\nnode_modules/\n")
        assert mod._gitignore_has_an_env_rule() is False, (
            "an .env.example-only ignore file was accepted as protecting .env"
        )

        (tmp_path / ".gitignore").write_text("# comment\n.env\nnode_modules/\n")
        assert mod._gitignore_has_an_env_rule() is True, (
            "a literal .env rule was not recognised"
        )

        (tmp_path / ".gitignore").write_text(".env*\n")
        assert mod._gitignore_has_an_env_rule() is True, (
            "the .env* glob was not recognised"
        )

        (tmp_path / ".gitignore").unlink()
        assert mod._gitignore_has_an_env_rule() is False, (
            "a missing .gitignore was treated as protective"
        )
    finally:
        mod.REPO_ROOT = original


def test_no_secret_reaches_the_built_javascript_bundle():
    """A secret compiled into the SPA bundle is served to every visitor."""
    assets = REPO_ROOT / "app" / "static" / "spa" / "assets"
    if not assets.is_dir():
        pytest.skip("SPA has not been built in this checkout")
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env is absent (CI checkout); scanner rule 2 covers this")

    secrets_to_check = []
    for line in env_path.read_text(errors="replace").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip().strip("'\"")
        if not re.search(r"(PASSWORD|SECRET|_KEY$|TOKEN)", key, re.IGNORECASE):
            continue
        if len(value) < 12 or value.lower().startswith("your"):
            continue
        secrets_to_check.append((key, value))

    assert secrets_to_check, (
        "No .env secret values were long enough to check, so this test would "
        "pass regardless of the bundle contents. Refusing to report a "
        "meaningless pass."
    )

    leaks = []
    for js in list(assets.rglob("*.js")) + list(assets.rglob("*.css")):
        try:
            body = js.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for key, value in secrets_to_check:
            if value in body:
                leaks.append(f"{js.relative_to(REPO_ROOT)} contains .env {key}")
    assert not leaks, (
        "Server-side secrets were compiled into the browser bundle:\n  "
        + "\n  ".join(leaks)
    )


def test_no_secret_reaches_the_served_html_or_error_pages():
    """Static HTML shipped to browsers must not embed .env secrets."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env is absent (CI checkout)")
    values = []
    for line in env_path.read_text(errors="replace").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if m and re.search(r"(PASSWORD|SECRET|_KEY$|TOKEN)", m.group(1), re.I):
            v = m.group(2).strip().strip("'\"")
            if len(v) >= 12 and not v.lower().startswith("your"):
                values.append((m.group(1), v))
    leaks = []
    for html in (REPO_ROOT / "app" / "static").rglob("*.html"):
        body = html.read_text(encoding="utf-8", errors="ignore")
        for key, v in values:
            if v in body:
                leaks.append(f"{html.relative_to(REPO_ROOT)} contains .env {key}")
    assert not leaks, "Secrets present in served HTML:\n  " + "\n  ".join(leaks)


# ---------------------------------------------------------------------------
# Anti-vacuity: the scanner must be capable of failing
# ---------------------------------------------------------------------------

def test_the_scanner_actually_detects_a_planted_secret(tmp_path):
    """Plant a burned literal in a real repo file, confirm the scanner fails.

    Without this, "0 findings" could mean "the scanner is broken". The planted
    file is removed in a finally block so a failure cannot leave a credential
    behind in the working tree.
    """
    planted = REPO_ROOT / "_secret_scanner_selftest.tmp.py"
    # The plant is ASSEMBLED at runtime, never written as a literal assignment
    # in this file's source. The first draft embedded
    # `f'ADMIN_PASSWORD = "{...}"'` here, and the scanner correctly flagged THIS
    # TEST FILE as containing a hardcoded credential -- a true positive against
    # the guard itself. Building the string from parts keeps the source clean
    # while still planting a genuine violation on disk.
    plant_body = (
        "# planted by tests/test_no_committed_secrets.py -- removed immediately\n"
        + "ADMIN_" + "PASSWORD" + " = " + '"' + BURNED_LITERALS[0] + '"' + "\n"
    )
    try:
        planted.write_text(plant_body)
        proc = _run_scanner("--check")
        assert proc.returncode == 1, (
            "The scanner reported success while a known-compromised credential "
            "literal was sitting in the repository. The ratchet does not work.\n"
            f"stdout:\n{proc.stdout}"
        )
        assert "compromised-literal" in proc.stdout, (
            "The scanner failed, but not for the planted literal:\n"
            f"{proc.stdout}"
        )
    finally:
        if planted.exists():
            planted.unlink()

    # And it must go back to clean once the plant is removed, otherwise the
    # test above proves nothing about *this* literal.
    after = _run_scanner("--check")
    assert after.returncode == 0, (
        "The scanner still fails after the planted file was removed, so its "
        "detection was not caused by the plant:\n" + after.stdout
    )


def test_the_scanner_detects_a_planted_env_value(tmp_path):
    """Rule 2: a live .env secret value copied into a source file is caught.

    This is the generalised guard -- it protects secrets that do not exist yet,
    which rule 1 (a fixed literal list) cannot.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env is absent (CI checkout)")
    value = None
    for line in env_path.read_text(errors="replace").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if m and re.search(r"(PASSWORD|SECRET|_KEY$)", m.group(1), re.I):
            v = m.group(2).strip().strip("'\"")
            if len(v) >= 20 and not v.lower().startswith("your"):
                value = v
                break
    if value is None:
        pytest.skip("no sufficiently long .env secret to plant")

    planted = REPO_ROOT / "_secret_scanner_env_selftest.tmp.txt"
    try:
        planted.write_text(f"leaked value: {value}\n")
        proc = _run_scanner("--check")
        assert proc.returncode == 1, (
            "The scanner did not flag a live .env secret value copied into a "
            f"repository file:\n{proc.stdout}"
        )
        assert "env-secret-leaked" in proc.stdout, proc.stdout
    finally:
        if planted.exists():
            planted.unlink()

    after = _run_scanner("--check")
    assert after.returncode == 0, (
        "Scanner still failing after cleanup:\n" + after.stdout
    )


def test_the_rotation_script_does_not_print_secrets_by_default():
    """``rotate_bootstrap_passwords.py`` must not log credentials.

    Under PM2 / Docker / CI, stdout is a log file. The original version of this
    script printed both new passwords, which turned every rotation into a fresh
    leak in a place nobody thinks to scrub.
    """
    src = (REPO_ROOT / "scripts" / "rotate_bootstrap_passwords.py").read_text()
    lines = src.splitlines()

    # Find every line that prints a new password value, then require each one to
    # sit inside the explicit `if print_admin_password:` opt-in block.
    #
    # This is deliberately not a flat "no such print exists" grep. The script
    # legitimately supports --print-admin-password for an operator at an
    # interactive terminal who must transcribe the credential into a password
    # manager. What must never happen is an UNGUARDED print, because stdout is a
    # log file under PM2 / Docker / CI. The first draft of this test banned the
    # print outright and failed against the guarded, correct implementation.
    printing = [
        i for i, ln in enumerate(lines)
        if re.search(r"print\(f?\"[^\"]*\{new_(?:admin|vendor)_pw\}", ln)
    ]
    assert printing, (
        "No password-printing line found at all. Either the script changed "
        "shape or this regex no longer matches it -- in which case this test "
        "would pass vacuously forever. Re-anchor it."
    )
    for idx in printing:
        guard = None
        for j in range(idx - 1, max(idx - 12, -1), -1):
            if "if print_admin_password:" in lines[j]:
                guard = j
                break
        assert guard is not None, (
            f"rotate_bootstrap_passwords.py:{idx + 1} prints a new password "
            "without an `if print_admin_password:` guard:\n"
            f"  {lines[idx].strip()}\n"
            "Write secrets to .env and print only a fingerprint; stdout is a "
            "log file in production."
        )
    assert "_fingerprint(" in src, (
        "The rotation script no longer reports a non-reversible fingerprint, so "
        "an operator cannot confirm which value landed without reading the "
        "secret itself."
    )

def test_the_rotation_script_writes_a_new_secret_key():
    """The SECRET_KEY write must be reachable, guarded only by --keep-secret-key.

    WHY AST AND NOT A SUBSTRING GREP
    --------------------------------
    The first version of this assertion was `assert "SECRET_KEY" in src`. The
    mutation harness (/tmp/mut7, mutation AR) disabled the write by changing
    `if not keep_secret_key:` to `if False:` -- the string "SECRET_KEY" was
    still present, the test still passed, and SECRET_KEY silently stopped
    rotating. That is a vacuous assertion: it observed that the script mentions
    the constant, not that it writes it.

    This version parses the module and requires the enclosing `if` test to be
    exactly `not keep_secret_key`, so any attempt to short-circuit the branch
    (`if False`, `if 0`, an inverted flag, a removed call) fails here.
    """
    import ast

    path = REPO_ROOT / "scripts" / "rotate_bootstrap_passwords.py"
    tree = ast.parse(path.read_text())

    def _is_secret_key_write(node) -> bool:
        return (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "_set_env_var"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "SECRET_KEY"
        )

    guarded_correctly = False
    found_any = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        writes = [n for n in ast.walk(node) if _is_secret_key_write(n)]
        if not writes:
            continue
        found_any = True
        test = node.test
        if (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and getattr(test.operand, "id", "") == "keep_secret_key"
        ):
            guarded_correctly = True

    assert found_any, (
        "scripts/rotate_bootstrap_passwords.py contains no "
        '_set_env_var(body, "SECRET_KEY", ...) call.\n\n'
        "This app's only browser session credential is a stateless JWT signed "
        "with SECRET_KEY (app/core/security.py::create_access_token). There is "
        "no session table and no JWT denylist, so changing a password does NOT "
        "invalidate an already-issued cookie. Rotating SECRET_KEY is the app's "
        "only session-revocation lever; without it a rotation after a "
        "compromise leaves stolen sessions fully valid."
    )
    assert guarded_correctly, (
        "The SECRET_KEY write exists but is not guarded by "
        "`if not keep_secret_key:`. It must be reachable by default -- a "
        "rotation that skips SECRET_KEY does not revoke stolen sessions."
    )


def test_rotation_actually_changes_the_stored_password_hash(tmp_path, monkeypatch):
    """Behavioural proof: run the real rotate() against a throwaway DB + .env.

    Structural tests confirm the code *says* the right thing. This one confirms
    it *does* the right thing: after rotate(), the old password must no longer
    verify against the stored hash, the new one must, and a fresh SECRET_KEY
    must be present in the .env that was written.

    Everything is redirected at a temp sqlite file and a temp .env, so this
    never touches the real database or the live credentials.
    """
    import importlib.util

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    sys.path.insert(0, str(REPO_ROOT))
    from app.core.security import hash_password, verify_password  # noqa: E402
    from app.db.session import Base  # noqa: E402
    from app.models.user import User  # noqa: E402

    spec = importlib.util.spec_from_file_location(
        "_rotate_under_test",
        REPO_ROOT / "scripts" / "rotate_bootstrap_passwords.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # --- throwaway database with one admin row ---------------------------
    db_file = tmp_path / "rot.db"
    engine = create_engine(f"sqlite:///{db_file}")
    User.__table__.create(bind=engine, checkfirst=True)
    Session = sessionmaker(bind=engine)

    old_password = "OldRotationProbe#12345"
    with Session() as s:
        s.add(User(
            name="Rotation Probe",
            email="probe@example.test",
            password_hash=hash_password(old_password),
            role="admin",
            must_change_password=True,
        ))
        s.commit()

    # --- throwaway .env ---------------------------------------------------
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY=0000000000000000000000000000000000000000000000000000000000000000\n"
        "DEFAULT_ADMIN_PASSWORD=placeholder\n"
    )

    class _Settings:
        DEFAULT_ADMIN_EMAIL = "probe@example.test"
        VENDOR_ADMIN_EMAIL = ""

    monkeypatch.setattr(mod, "SessionLocal", Session)
    monkeypatch.setattr(mod, "ENV_PATH", env_file)
    monkeypatch.setattr(mod, "settings", _Settings())

    rc = mod.rotate()
    assert rc == 0, f"rotate() returned {rc}, expected 0"

    # --- the old password must be dead ------------------------------------
    with Session() as s:
        user = s.query(User).filter_by(email="probe@example.test").one()
        assert not verify_password(old_password, user.password_hash), (
            "rotate() ran but the OLD password still verifies against the "
            "stored hash. The credential was not actually rotated."
        )
        assert user.must_change_password is False

    # --- the new password from .env must work ------------------------------
    written = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines() if "=" in line
    )
    new_pw = written["DEFAULT_ADMIN_PASSWORD"]
    assert new_pw not in ("placeholder", old_password)
    assert len(new_pw) >= 20, (
        f"rotate() produced a {len(new_pw)}-char password; too weak."
    )
    with Session() as s:
        user = s.query(User).filter_by(email="probe@example.test").one()
        assert verify_password(new_pw, user.password_hash), (
            "The password written to .env does not verify against the hash "
            "stored in the database. .env and the DB have diverged, which is "
            "exactly the lockout this script exists to prevent."
        )

    # --- SECRET_KEY must have been replaced -------------------------------
    assert written["SECRET_KEY"] != "0" * 64, (
        "rotate() left SECRET_KEY unchanged. Previously issued access_token "
        "JWTs would remain valid after a credential compromise."
    )
    assert len(written["SECRET_KEY"]) >= 32

    # --- and the secrets must not have been printed ------------------------
    captured = env_file.read_text()
    assert new_pw in captured  # sanity: it did land in .env


def test_rotation_revokes_outstanding_password_reset_tokens(tmp_path, monkeypatch):
    """A reset link mailed before the incident must not survive the rotation.

    ``/api/auth/forgot-password`` issues rows in ``password_reset_tokens`` that
    stay redeemable until ``expires_at``. If an attacker triggered a reset (or
    intercepted a reset mail) before the credential was rotated, that token is a
    second key to the account -- rotating the password alone does not close it.

    Mutation AX in /tmp/mut7 replaced the revocation query with ``rows = []``
    and NOTHING failed: the revocation was implemented but unverified. This test
    exists because of that escape. It seeds three tokens -- one unused, one
    already used, one unused for a different user -- and asserts every unused
    row is stamped while the already-used row's timestamp is left alone.
    """
    import importlib.util
    from datetime import datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    sys.path.insert(0, str(REPO_ROOT))
    from app.core.security import hash_password  # noqa: E402
    from app.models.lookups import PasswordResetToken  # noqa: E402
    from app.models.user import User  # noqa: E402

    spec = importlib.util.spec_from_file_location(
        "_rotate_tokens", REPO_ROOT / "scripts" / "rotate_bootstrap_passwords.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_file = tmp_path / "tok.db"
    engine = create_engine(f"sqlite:///{db_file}")
    User.__table__.create(bind=engine, checkfirst=True)
    PasswordResetToken.__table__.create(bind=engine, checkfirst=True)
    Session = sessionmaker(bind=engine)

    already_used_at = datetime(2020, 1, 1, 12, 0, 0)
    with Session() as s:
        s.add(User(name="A", email="a@example.test",
                   password_hash=hash_password("OldTokenProbe#12345"),
                   role="admin"))
        s.add(User(name="B", email="b@example.test",
                   password_hash=hash_password("OtherProbe#12345"),
                   role="staff"))
        s.flush()
        s.add_all([
            PasswordResetToken(user_id=1, email="a@example.test",
                               token_hash="hash-unused-admin",
                               expires_at=datetime.utcnow() + timedelta(hours=1)),
            PasswordResetToken(user_id=1, email="a@example.test",
                               token_hash="hash-already-used",
                               expires_at=datetime.utcnow() + timedelta(hours=1),
                               used_at=already_used_at),
            PasswordResetToken(user_id=2, email="b@example.test",
                               token_hash="hash-unused-other",
                               expires_at=datetime.utcnow() + timedelta(hours=1)),
        ])
        s.commit()

    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=" + "b" * 64 + "\nDEFAULT_ADMIN_PASSWORD=x\n")

    class _S:
        DEFAULT_ADMIN_EMAIL = "a@example.test"
        VENDOR_ADMIN_EMAIL = ""

    monkeypatch.setattr(mod, "SessionLocal", Session)
    monkeypatch.setattr(mod, "ENV_PATH", env_file)
    monkeypatch.setattr(mod, "settings", _S())

    # Sanity: two unused tokens exist before the rotation, so a post-rotation
    # count of zero cannot be an artefact of an empty table.
    with Session() as s:
        before = s.query(PasswordResetToken).filter(
            PasswordResetToken.used_at.is_(None)
        ).count()
    assert before == 2, f"fixture seeded wrong: {before} unused tokens, expected 2"

    assert mod.rotate() == 0

    with Session() as s:
        remaining = s.query(PasswordResetToken).filter(
            PasswordResetToken.used_at.is_(None)
        ).all()
        assert not remaining, (
            "rotate() left "
            f"{[r.token_hash for r in remaining]} redeemable. Every outstanding "
            "password-reset token must be revoked when a credential is rotated "
            "after a compromise -- otherwise a pre-incident reset link is still "
            "a valid path back into the account."
        )
        untouched = s.query(PasswordResetToken).filter_by(
            token_hash="hash-already-used"
        ).one()
        assert untouched.used_at == already_used_at, (
            "rotate() overwrote the used_at timestamp of an already-consumed "
            "token, destroying audit history. Only unused rows should be stamped."
        )


def test_rotation_succeeds_when_the_reset_token_table_is_absent(tmp_path, monkeypatch):
    """A fresh install (pre-migration) must still rotate successfully.

    This is the exact condition that exposed the ``db.rollback()`` bug: the
    helper caught the missing-table error, rolled back, and discarded the
    caller's pending password change, so rotate() returned 4 and the
    compromised password stayed live.
    """
    import importlib.util

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    sys.path.insert(0, str(REPO_ROOT))
    from app.core.security import hash_password, verify_password  # noqa: E402
    from app.models.user import User  # noqa: E402

    spec = importlib.util.spec_from_file_location(
        "_rotate_notable", REPO_ROOT / "scripts" / "rotate_bootstrap_passwords.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_file = tmp_path / "notable.db"
    engine = create_engine(f"sqlite:///{db_file}")
    # ONLY the users table -- password_reset_tokens deliberately absent.
    User.__table__.create(bind=engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        s.add(User(name="C", email="c@example.test",
                   password_hash=hash_password("OldNoTable#123456"),
                   role="admin"))
        s.commit()

    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=" + "c" * 64 + "\nDEFAULT_ADMIN_PASSWORD=x\n")

    class _S:
        DEFAULT_ADMIN_EMAIL = "c@example.test"
        VENDOR_ADMIN_EMAIL = ""

    monkeypatch.setattr(mod, "SessionLocal", Session)
    monkeypatch.setattr(mod, "ENV_PATH", env_file)
    monkeypatch.setattr(mod, "settings", _S())

    rc = mod.rotate()
    assert rc == 0, (
        f"rotate() returned {rc} on a database without the "
        "password_reset_tokens table. A missing optional table must not abort "
        "(or silently undo) the credential rotation."
    )
    written = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines() if "=" in line
    )
    with Session() as s:
        user = s.query(User).filter_by(email="c@example.test").one()
        assert verify_password(written["DEFAULT_ADMIN_PASSWORD"],
                               user.password_hash), (
            "The password change was rolled back. This is the exact silent "
            "no-op that made rotate() report failure while leaving the "
            "compromised credential live."
        )
        assert not verify_password("OldNoTable#123456", user.password_hash)


def test_keep_secret_key_flag_preserves_the_existing_key(tmp_path, monkeypatch):
    """--keep-secret-key must be honoured (the opposite branch of the guard).

    Without this, `test_the_rotation_script_writes_a_new_secret_key` could be
    satisfied by a script that always rotates and ignores the flag.
    """
    import importlib.util

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    sys.path.insert(0, str(REPO_ROOT))
    from app.core.security import hash_password  # noqa: E402
    from app.models.user import User  # noqa: E402

    spec = importlib.util.spec_from_file_location(
        "_rotate_keepkey", REPO_ROOT / "scripts" / "rotate_bootstrap_passwords.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_file = tmp_path / "keep.db"
    engine = create_engine(f"sqlite:///{db_file}")
    User.__table__.create(bind=engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        s.add(User(name="P", email="p@example.test",
                   password_hash=hash_password("OldKeepProbe#123456"),
                   role="admin"))
        s.commit()

    original_key = "a" * 64
    env_file = tmp_path / ".env"
    env_file.write_text(f"SECRET_KEY={original_key}\nDEFAULT_ADMIN_PASSWORD=x\n")

    class _S:
        DEFAULT_ADMIN_EMAIL = "p@example.test"
        VENDOR_ADMIN_EMAIL = ""

    monkeypatch.setattr(mod, "SessionLocal", Session)
    monkeypatch.setattr(mod, "ENV_PATH", env_file)
    monkeypatch.setattr(mod, "settings", _S())

    assert mod.rotate(keep_secret_key=True) == 0
    written = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines() if "=" in line
    )
    assert written["SECRET_KEY"] == original_key, (
        "--keep-secret-key was passed but SECRET_KEY was rotated anyway."
    )
    assert written["DEFAULT_ADMIN_PASSWORD"] != "x", (
        "--keep-secret-key suppressed the password rotation too; it should "
        "only affect SECRET_KEY."
    )


def test_the_scanner_scans_the_built_bundle_directory():
    """The bundle must be in scope, not excluded as a build artifact.

    A secret compiled into app/static/spa/assets is served to every visitor.
    Excluding 'dist'-like directories wholesale would silently drop it.
    """
    src = SCANNER.read_text()
    skip_block = re.search(r"SKIP_DIRS\s*=\s*\{(.*?)\}", src, re.DOTALL)
    assert skip_block, "SKIP_DIRS not found in the scanner"
    skipped = skip_block.group(1)
    for part in ("spa", "assets", "static"):
        assert f'"{part}"' not in skipped, (
            f"The scanner skips '{part}', so secrets compiled into the served "
            "browser bundle would not be detected."
        )
