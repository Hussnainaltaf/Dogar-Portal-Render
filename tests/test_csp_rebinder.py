"""CSP strictness regression suite (SPA era).

The portal ships a strict Content-Security-Policy::

    script-src 'self' 'nonce-…'

with NO ``'unsafe-inline'``, NO ``'unsafe-hashes'`` and NO ``'unsafe-eval'``.
Under that policy the browser blocks BOTH inline event-handler attributes
(``onclick=…``) AND eval-equivalents (``eval()``, ``new Function()``,
``setTimeout(string)``).

**History / why this file changed shape.** The legacy Jinja pages used inline
``on*=`` handler attributes, which that CSP kills. They were kept alive by
``app/static/js/csp_rebinder.js`` — an eval-free micro-interpreter that
re-bound them at runtime. The Jinja2 eradication pass removed the templates,
the inline handlers and therefore the rebinder itself.

Deleting this suite along with the rebinder would have silently dropped the
CSP guarantees it was really protecting, so it is **retargeted** rather than
removed. The React SPA satisfies the same properties structurally (handlers
are attached with ``addEventListener`` via React's synthetic event system, so
there is nothing to rebind), and these tests now pin that:

1. The SPA source and the built bundles contain no eval-equivalents — if a
   dependency or a code-split chunk ever introduces one, the strict CSP would
   break it in production and this fails first.
2. The served SPA shell carries no inline ``on*=`` handler attributes, so no
   rebinder is needed and none may be reintroduced.
3. The live CSP header is nonce-based and free of ``unsafe-eval`` /
   ``unsafe-inline`` for scripts (unchanged from the original suite).
4. The obsolete rebinder has not crept back in, which would signal the return
   of the inline-handler pattern it existed to work around.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
REBINDER = ROOT / "app" / "static" / "js" / "csp_rebinder.js"
TEMPLATES = ROOT / "app" / "templates"
STATIC_JS = ROOT / "app" / "static" / "js"
SPA_SRC = ROOT / "app" / "frontend" / "src"
SPA_DIST = ROOT / "app" / "static" / "spa"

# eval-equivalents blocked by a nonce-only script-src (no 'unsafe-eval')
_EVAL_PATTERNS = [
    re.compile(r"\bnew\s+Function\s*\("),
    re.compile(r"(?<![\w.])eval\s*\("),
    re.compile(r"setTimeout\s*\(\s*['\"]"),
    re.compile(r"setInterval\s*\(\s*['\"]"),
]


def _strip_js_comments(src: str) -> str:
    """Remove /* … */ blocks and // line comments so doc-comments that merely
    MENTION `new Function` don't trip the scanner."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)//[^\n]*$", "", src)
    return src


def _strip_html_comments(src: str) -> str:
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


def test_obsolete_rebinder_is_not_reintroduced():
    """The rebinder exists only to serve inline `on*=` handlers.

    Its return would mean the inline-handler pattern came back — the pattern
    the strict CSP forbids. If it is ever deliberately reinstated, it must also
    be re-proven eval-free (see the eval scan below, which covers it).
    """
    if REBINDER.is_file():
        body = _strip_js_comments(REBINDER.read_text(encoding="utf-8"))
        for pat in _EVAL_PATTERNS:
            assert not pat.search(body), (
                f"csp_rebinder.js was reintroduced and contains eval-equivalent "
                f"`{pat.pattern}` — blocked by the strict nonce-only CSP; it "
                "would be a silent no-op in production."
            )


def test_no_eval_equivalents_in_first_party_frontend():
    """No first-party frontend code may use an eval-equivalent.

    Covers the SPA source (what we write) AND the emitted bundles (what the
    browser actually executes, including third-party code Vite inlines).
    """
    offenders: list[str] = []

    scanned = 0
    # Legacy trees — still scanned when present so this keeps working on
    # branches that predate the Jinja removal.
    for f in sorted(TEMPLATES.glob("*.html")) if TEMPLATES.is_dir() else []:
        body = _strip_js_comments(_strip_html_comments(f.read_text(encoding="utf-8")))
        scanned += 1
        for pat in _EVAL_PATTERNS:
            if pat.search(body):
                offenders.append(f"{f.relative_to(ROOT)}: {pat.pattern}")
    for f in sorted(STATIC_JS.glob("*.js")) if STATIC_JS.is_dir() else []:
        body = _strip_js_comments(f.read_text(encoding="utf-8"))
        scanned += 1
        for pat in _EVAL_PATTERNS:
            if pat.search(body):
                offenders.append(f"{f.relative_to(ROOT)}: {pat.pattern}")

    # The live frontend.
    for ext in ("*.ts", "*.tsx"):
        for f in sorted(SPA_SRC.rglob(ext)):
            body = _strip_js_comments(f.read_text(encoding="utf-8", errors="replace"))
            scanned += 1
            for pat in _EVAL_PATTERNS:
                if pat.search(body):
                    offenders.append(f"{f.relative_to(ROOT)}: {pat.pattern}")

    assert scanned, "the eval scan found no files — the scan roots are wrong"
    assert not offenders, (
        "eval-equivalents found (blocked by nonce-only CSP → dead code in production):\n"
        + "\n".join(offenders)
    )


def test_built_spa_bundles_are_eval_free():
    """The shipped bundles must survive `script-src` without 'unsafe-eval'."""
    assets = SPA_DIST / "assets"
    if not assets.is_dir():
        import pytest

        pytest.skip("SPA build not present; run `npm run build` in app/frontend")

    bundles = sorted(assets.glob("*.js"))
    assert bundles, "no JS bundles in the SPA build output"

    offenders = []
    for f in bundles:
        body = f.read_text(encoding="utf-8", errors="replace")
        # Minified bundles have no comments worth stripping, and stripping "//"
        # would corrupt protocol-relative URLs, so scan the raw text.
        if re.search(r"\bnew\s+Function\s*\(", body):
            offenders.append(f"{f.name}: new Function(")
    assert not offenders, (
        "the built SPA contains an eval-equivalent, which the strict CSP "
        "blocks at runtime:\n" + "\n".join(offenders)
    )


def test_spa_shell_has_no_inline_event_handlers():
    """No inline `on*=` attributes → nothing for a rebinder to rescue."""
    index = SPA_DIST / "index.html"
    if not index.is_file():
        import pytest

        pytest.skip("SPA build not present; run `npm run build` in app/frontend")

    html = _strip_html_comments(index.read_text(encoding="utf-8"))
    found = re.findall(r"\son[a-z]+\s*=\s*[\"']", html, re.I)
    assert not found, (
        "the SPA shell carries inline event-handler attributes, which the "
        f"strict nonce-only CSP blocks: {sorted(set(found))}"
    )


def test_live_csp_header_is_strict():
    with TestClient(app) as client:
        r = client.get("/login")
        csp = r.headers.get("content-security-policy", "")
        assert csp, "CSP header missing on HTML response"
        m = re.search(r"script-src([^;]*)", csp)
        assert m, f"no script-src directive in CSP: {csp}"
        script_src = m.group(1)
        assert "'nonce-" in script_src, f"script-src is not nonce-based: {script_src}"
        assert "unsafe-eval" not in script_src, (
            "script-src allows 'unsafe-eval' — the strict policy was weakened"
        )
        assert "unsafe-inline" not in script_src, (
            "script-src allows 'unsafe-inline' — the strict policy was weakened"
        )

        # style-src hardening (2026-07-20): <style>/<link> loading is strict
        # (style-src-elem is nonce-based, no 'unsafe-inline'); 'unsafe-inline'
        # survives ONLY for inert style ATTRIBUTES (style-src-attr) and the
        # legacy-browser plain style-src fallback.
        me = re.search(r"style-src-elem([^;]*)", csp)
        assert me, f"no style-src-elem directive in CSP: {csp}"
        style_elem = me.group(1)
        assert "'nonce-" in style_elem, (
            f"style-src-elem is not nonce-based: {style_elem}"
        )
        assert "unsafe-inline" not in style_elem, (
            "style-src-elem allows 'unsafe-inline' — injected <style> is not "
            "blocked; style-element hardening was weakened"
        )
        ma = re.search(r"style-src-attr([^;]*)", csp)
        assert ma, f"no style-src-attr directive in CSP: {csp}"
        assert "unsafe-inline" in ma.group(1), (
            "style-src-attr must keep 'unsafe-inline' for legacy shell and "
            "React/CSSOM compatibility surfaces; script-src remains strict"
        )

        # Nonce-bypass hardening must stay in place.
        for directive in ("object-src 'none'", "base-uri 'self'", "form-action 'self'"):
            assert directive in csp, f"CSP lost `{directive}`: {csp}"
