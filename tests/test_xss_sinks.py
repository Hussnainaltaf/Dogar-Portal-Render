"""CI ratchet for the stored-XSS remediation (external audit, 2026-07-26).

The audit reported unescaped API data reaching ``innerHTML`` in several
templates. ``scripts/scan_xss_sinks.py`` enumerates every ``${...}`` inside a
markup-bearing template literal and classifies it, and this test locks the
count at zero so a regression fails the build instead of shipping.

The suite deliberately tests the scanner as well as the templates: a static
analyser that has quietly gone blind would also report zero, so
``test_scanner_detects_a_reintroduced_sink`` re-injects a known-bad
interpolation into a temporary copy of the tree and asserts it is caught.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "scripts" / "scan_xss_sinks.py"


def _run_scanner(scanner: Path = SCANNER) -> subprocess.CompletedProcess:
    """Invoke the scanner.

    The scanner derives its scan root from ``__file__``, not the working
    directory, so the mutation probe below must run the *copied* script inside
    the temporary tree rather than merely changing cwd.
    """
    return subprocess.run(
        [sys.executable, str(scanner), "--check"],
        cwd=str(scanner.resolve().parents[1]),
        capture_output=True,
        text=True,
    )


def test_scanner_script_exists():
    assert SCANNER.is_file(), f"missing XSS scanner at {SCANNER}"


def test_no_unescaped_innerhtml_interpolations():
    """Every interpolation reaching innerHTML must be escaped or reviewed."""
    result = _run_scanner()
    assert result.returncode == 0, (
        "Unescaped HTML interpolation(s) detected \u2014 these are stored-XSS "
        "candidates.\n\n"
        "Fix by wrapping the value at the interpolation site:\n"
        "  \u2022 esc(x)                 \u2014 text/attribute content\n"
        "  \u2022 escJs(x)               \u2014 inside a quoted JS string in an on* handler\n"
        "  \u2022 escUrl(x)              \u2014 href/src values\n"
        "  \u2022 Number(x)              \u2014 numeric ids\n"
        "  \u2022 encodeURIComponent(x)  \u2014 URL path/query segments\n\n"
        "Do NOT silence this by adding a name to TRUSTED_MARKUP_VARS_BY_FILE "
        "unless you have read the assignment and confirmed the value cannot "
        "carry user, API, imported or OCR-derived text.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def _clone_scannable_tree(tmp_path) -> Path:
    """Copy the parts of the repo the scanner reads into a scratch tree."""
    work = tmp_path / "repo"
    for rel in ("scripts", "app/templates", "app/static/js", "app/frontend/src"):
        src = REPO_ROOT / rel
        if src.is_dir():
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
    return work


def test_scanner_detects_a_reintroduced_sink(tmp_path):
    """Guard against the ratchet passing because the scanner stopped working."""
    work = _clone_scannable_tree(tmp_path)

    # Retargeted to the SPA: the Jinja file this probe used to mutate no longer
    # exists, and a probe that skips is a probe that proves nothing.
    target = work / "app" / "frontend" / "src" / "components" / "Chatbot.tsx"
    if not target.is_file():
        pytest.skip("Chatbot.tsx not present in this tree")

    source = target.read_text(encoding="utf-8")
    if "fmtText(m.text)" not in source:
        pytest.skip("expected audited sink not found; update this probe")

    # Strip the audited formatter, recreating the raw-markup bug class.
    target.write_text(
        source.replace("fmtText(m.text)", "m.text", 1), encoding="utf-8"
    )

    result = _run_scanner(work / "scripts" / "scan_xss_sinks.py")
    assert result.returncode != 0, (
        "The scanner did NOT flag a deliberately reintroduced unescaped "
        "raw-markup sink, so a passing run proves nothing. Repair "
        "scripts/scan_xss_sinks.py before trusting this ratchet.\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "m.text" in result.stdout


def test_scanner_detects_a_reintroduced_concatenation_sink(tmp_path):
    """The concatenation detector needs its own mutation probe.

    Template-literal sinks (``el.innerHTML = `<b>${x}</b>` ``) and
    string-concatenation sinks (``el.innerHTML = '<b>' + x`` ) are found by two
    completely separate passes in the scanner. The probe above only exercises the
    template-literal pass, so on its own it would still pass if the concatenation
    pass were deleted — which is exactly how these sinks went unnoticed until the
    second remediation pass. Mutate a concatenation sink specifically.
    """
    work = _clone_scannable_tree(tmp_path)

    # The SPA has no string-concatenation innerHTML sinks (React owns the DOM),
    # so there is no shipped line left to mutate. Rather than skip — which would
    # leave the concatenation pass completely unverified, the very blind spot
    # this test was written to close — synthesise the exact bug class into the
    # scanned tree and require the scanner to catch it.
    target = work / "app" / "frontend" / "src" / "lib" / "_concat_probe.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export function render(el: HTMLElement, detail: string) {\n"
        "  el.innerHTML = '<i class=\"fa fa-x\"></i> ' + detail\n"
        "}\n",
        encoding="utf-8",
    )

    result = _run_scanner(work / "scripts" / "scan_xss_sinks.py")
    assert result.returncode != 0, (
        "The scanner did NOT flag a deliberately reintroduced unescaped "
        "string-concatenation sink. The concatenation pass in "
        "scripts/scan_xss_sinks.py is broken or was removed; template-literal "
        "coverage alone is not sufficient.\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "detail" in result.stdout


def test_spa_bootstrap_json_escapes_html_parser_terminators(monkeypatch, tmp_path):
    """Tenant bootstrap JSON must not expose raw </script> inside inline JS.

    The browser HTML parser terminates a script element before JavaScript parses
    the string literal. That means JSON embedded in an inline script must escape
    raw "<", ">" and "&" as literal backslash-u sequences, not Python's decoded
    Unicode characters. This locks down the tenant-branding bootstrap script.
    """
    import app.main as main_mod

    spa = tmp_path / "index.html"
    spa.write_text("<html><head></head><body><script>window.shell=1;</script></body></html>", encoding="utf-8")
    monkeypatch.setattr(main_mod, "SPA_INDEX_PATH", str(spa))

    malicious = "Acme </script><img src=x onerror=alert(1)> & Sons"
    request = SimpleNamespace(
        state=SimpleNamespace(
            csp_nonce="nonce-for-test",
            tenant_base="/t/acme",
            tenant=SimpleNamespace(
                slug="acme",
                company_name=malicious,
                short_name=malicious,
                primary_color="#2563eb",
                logo_filename=None,
                office_name=malicious,
                subtitle=malicious,
                license_number=malicious,
                office_address=malicious,
                contact_phone="",
                contact_email="",
            ),
        )
    )

    body = main_mod._serve_spa_index(request).body.decode("utf-8")
    assert "</script><img" not in body
    assert "\\u003c/script\\u003e\\u003cimg" in body
    assert "\\u0026 Sons" in body
    assert '<script nonce="nonce-for-test">window.__TENANT_BOOT__=' in body


def test_canonical_escapers_are_exported_once():
    """There must be exactly ONE audited HTML-sanitising boundary.

    Rewritten for the React SPA (the Jinja tree and its ``safe_dom.js`` were
    removed in the Jinja2 eradication pass). In the SPA, React escapes all
    ordinary interpolation automatically, so the only raw-markup boundary is
    ``app/frontend/src/lib/sanitizeHtml.ts``. This test pins that module as the
    single source of truth and fails if a second, unaudited sanitiser appears.
    """
    sanitizer = REPO_ROOT / "app" / "frontend" / "src" / "lib" / "sanitizeHtml.ts"
    assert sanitizer.is_file(), (
        "the canonical SPA sanitiser app/frontend/src/lib/sanitizeHtml.ts is "
        "missing — raw markup would have no audited escaping boundary"
    )
    source = sanitizer.read_text(encoding="utf-8")
    assert "export function sanitizeHtml" in source, (
        "sanitizeHtml() is no longer exported from the canonical module"
    )
    # The sanitiser must still strip the two things that make markup dangerous.
    assert "startsWith('on')" in source, "event-handler stripping was removed"
    assert "javascript" in source, "script-URI rejection was removed"

    # A second sanitiser elsewhere in the SPA would create an unaudited path to
    # innerHTML, which is exactly the bug class this suite exists to prevent.
    offenders = []
    for path in sorted((REPO_ROOT / "app" / "frontend" / "src").rglob("*.ts*")):
        if path == sanitizer:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in ("function sanitizeHtml(", "function escapeHtml(", "function escapeHTML("):
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {marker})")
    assert not offenders, (
        "A second escaper/sanitiser shadows the audited one in "
        "app/frontend/src/lib/sanitizeHtml.ts. Import that module instead.\n"
        + "\n".join(offenders)
    )


def test_no_escaped_html_entities_inside_inline_handler_strings():
    """`esc()` output is not parseable by csp_rebinder.js \u2014 handlers need escJs().

    ``esc()`` turns ``'`` into ``&#39;``. Inline ``on*`` handlers are re-bound by
    csp_rebinder.js using a hand-rolled, eval-free tokenizer that does not decode
    HTML entities, so using ``esc()`` inside a quoted JS argument is both a
    security hole and a functional break.
    """
    import re

    pattern = re.compile(
        r"""on[a-z]+\s*=\s*"[^"]*'\$\{\s*(?:esc|escAttr|escapeHtml|escapeHTML|_esc)\(""",
        re.I,
    )
    offenders = []
    roots = [
        *sorted((REPO_ROOT / "app" / "templates").glob("*.html")),
        *sorted((REPO_ROOT / "app" / "static" / "js").glob("*.js")),
    ]
    for path in roots:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "Use escJs(...) for values inside a quoted JS string in an inline "
        "on* handler; esc() emits HTML entities that csp_rebinder.js cannot "
        "parse.\n" + "\n".join(offenders)
    )


def test_no_json_stringify_inside_single_quoted_handler():
    """``onclick='f(${JSON.stringify(o)})'`` breaks out on any apostrophe."""
    import re

    pattern = re.compile(r"on[a-z]+\s*=\s*'[^']*JSON\.stringify", re.I)
    offenders = []
    for path in sorted((REPO_ROOT / "app" / "templates").glob("*.html")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "A record serialised into a single-quoted handler attribute escapes the "
        "attribute as soon as a field contains an apostrophe (e.g. \"O'Brien\"). "
        "Pass an id and look the record up instead.\n" + "\n".join(offenders)
    )


# ── Page-script integrity ─────────────────────────────────────────────────────
# The remediation added `const escapeHtml = window.esc;` to several templates to
# retire their per-page escaper copies. In two pages that shadowed the top-level
# `const escapeHtml` already declared by /static/js/app.js (which loads BEFORE
# the page's `{% block scripts %}`), throwing
#   "Identifier 'escapeHtml' has already been declared"
# — a SyntaxError that aborts the ENTIRE script block, leaving the page inert
# with no visible error. Caught by browser smoke test; locked down here.

_SCRIPT_BLOCK_RE = None  # lazily compiled in the helper below


def _toplevel_lexical_names(js: str) -> set[str]:
    """Names bound by const/let/class at brace depth 0 of a script block.

    `function` is deliberately excluded: duplicate function declarations are
    legal (the later one simply wins), and several pages intentionally override
    a helper such as closeModal() from app.js. Only const/let/class collisions
    are hard SyntaxErrors, so only those are reported.
    """
    import re

    names: set[str] = set()
    depth = i = 0
    n = len(js)
    decl = re.compile(r"\b(?:const|let|class)\s+([A-Za-z_$][\w$]*)")
    while i < n:
        c = js[i]
        if c in "\"'":
            quote = c
            i += 1
            while i < n and js[i] != quote:
                i += 2 if js[i] == "\\" else 1
            i += 1
            continue
        if c == "`":
            i += 1
            while i < n and js[i] != "`":
                i += 2 if js[i] == "\\" else 1
            i += 1
            continue
        if js[i : i + 2] == "//":
            while i < n and js[i] != "\n":
                i += 1
            continue
        if js[i : i + 2] == "/*":
            close = js.find("*/", i)
            i = n if close < 0 else close + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0:
            m = decl.match(js, i)
            if m and (i == 0 or not js[i - 1].isalnum() and js[i - 1] not in "_$."):
                names.add(m.group(1))
        i += 1
    return names


def _inline_script_blocks(html: str) -> list[str]:
    import re

    global _SCRIPT_BLOCK_RE
    if _SCRIPT_BLOCK_RE is None:
        _SCRIPT_BLOCK_RE = re.compile(
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I
        )
    jinja = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
    return [jinja.sub("0", b) for b in _SCRIPT_BLOCK_RE.findall(html)]


def test_spa_raw_markup_sinks_are_all_audited():
    """Every `dangerouslySetInnerHTML` in the SPA must be an audited sink.

    Replaces the old app.js global-collision test, which guarded a failure mode
    (two inline <script> blocks sharing one global scope) that cannot exist in
    the module-scoped SPA bundle. The surviving risk in React is the raw-markup
    escape hatch, so that is what this now pins: each occurrence must either
    pass through the canonical sanitiser or an audited SAFE_HELPERS formatter,
    which is precisely what the scanner enforces.
    """
    spa_src = REPO_ROOT / "app" / "frontend" / "src"
    sinks = []
    for path in sorted(spa_src.rglob("*.ts*")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if "dangerouslySetInnerHTML" in line:
                sinks.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    # Not zero-by-assertion: the chatbot legitimately renders a markdown subset.
    # The point is that the scanner must have classified every one of them, so
    # a NEW unescaped sink fails the ratchet below rather than slipping in.
    result = _run_scanner()
    assert result.returncode == 0, (
        "A raw-markup sink in the SPA is not escaped/sanitised:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    # Guard against the sinks being deleted and this test silently going vacuous.
    assert sinks, (
        "no dangerouslySetInnerHTML found — if the escape hatch really was "
        "removed, delete this test; otherwise the scan roots are wrong"
    )
