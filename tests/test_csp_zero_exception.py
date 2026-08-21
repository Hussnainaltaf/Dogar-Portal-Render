"""Integration tests for the zero-exception CSP on document responses.

WHY THIS FILE EXISTS
--------------------
tests/test_style_hoist.py unit-tests the hoister in isolation. This file tests
the part unit tests CANNOT reach: that the per-response signal actually crosses
the Starlette middleware boundary and changes the real header.

That boundary is the whole risk. ``BaseHTTPMiddleware`` runs the downstream app
in a CHILD ``contextvars.Context``, so a plain ``ContextVar.set()`` inside a
route handler is INVISIBLE to the middleware that wrapped it. The signal
therefore mutates a dict the middleware already holds. If someone "simplifies"
that back to a plain ContextVar set, the hoisting still happens but the header
silently keeps ``style-src-attr 'unsafe-inline'`` forever — the security win
evaporates with every unit test still green. These tests are that ratchet.

The split is deliberate and load-bearing:

  * standalone print/preview documents -> style-src-attr 'none'
        They are self-contained: no external JS, and their own scripts only
        touch the CSSOM (el.style.*), which CSP does not police.
  * the Jinja app shell                -> style-src-attr 'unsafe-inline'
        It loads /static/js/safe_dom.js, which legitimately calls
        setAttribute('style', …) at runtime. CSP checks style ATTRIBUTES when
        they are WRITTEN, so the hoister (which only sees server-rendered HTML)
        cannot protect it. Tightening the shell would break the UI.
"""
from __future__ import annotations

import contextlib
import re

from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@contextlib.contextmanager
def temp_route(path: str, html_factory):
    """Register a throwaway HTML route, PREPENDED so the SPA cannot shadow it.

    app/main.py registers ``@app.get("/{spa_path:path}")`` as the last route,
    and it matches everything. A probe route appended after it is never reached
    — the request silently returns the SPA shell with the shell's CSP, which
    looks exactly like "the hoist signal did not work". Inserting at index 0
    keeps the probe reachable.
    """
    async def _endpoint():
        return HTMLResponse(html_factory())

    app.router.add_api_route(path, _endpoint, methods=["GET"],
                             response_class=HTMLResponse)
    route = app.router.routes.pop()          # move it ahead of the catch-all
    app.router.routes.insert(0, route)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            app.router.routes.remove(route)


def _csp_of(response) -> dict:
    raw = response.headers.get("content-security-policy")
    assert raw, "response carries no Content-Security-Policy header"
    out = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition(" ")
        out[name.strip()] = value.strip()
    return out


# ── The shell keeps the exception (and must not be "hardened" by accident) ────

def test_jinja_shell_keeps_style_src_attr_unsafe_inline():
    """The app shell needs style attributes at RUNTIME; do not tighten it.

    /static/js/safe_dom.js calls setAttribute('style', …). CSP evaluates style
    attributes at write time, so no amount of server-side hoisting can make
    style-src-attr 'none' safe here.
    """
    d = _csp_of(client.get("/login"))
    attr = d.get("style-src-attr")
    assert attr is not None, "style-src-attr directive vanished from the shell"
    assert "'unsafe-inline'" in attr, (
        "the Jinja shell still writes style attributes at runtime via "
        "safe_dom.js; tightening style-src-attr here breaks the UI silently"
    )


def test_shell_strict_directives_are_unaffected():
    """Hardening documents must not have loosened the shell's strict half."""
    d = _csp_of(client.get("/login"))
    assert re.search(r"'nonce-[A-Za-z0-9_\-]+'", d.get("script-src", ""))
    assert "'unsafe-inline'" not in d.get("script-src", "")
    assert "'unsafe-inline'" not in d.get("style-src-elem", "")


# ── The signal mechanism itself ───────────────────────────────────────────────

def test_hoist_signal_crosses_the_middleware_boundary():
    """A hoisted response must come back with style-src-attr 'none'.

    Uses a temporary route so the test does not depend on document fixtures,
    auth, or DB state — it isolates exactly one thing: does the signal survive
    the child-Context boundary of BaseHTTPMiddleware?
    """
    from app.core.style_hoist import hoist_inline_styles

    path = "/__csp_hoist_probe__"

    def _html():
        return hoist_inline_styles(
            '<html><body><div style="color:red">x</div></body></html>'
        )

    with temp_route(path, _html):
        r = client.get(path)
        assert r.status_code == 200
        d = _csp_of(r)

        assert d.get("style-src-attr") == "'none'", (
            "hoisted response must be served with style-src-attr 'none'. Got "
            f"{d.get('style-src-attr')!r}. If this fails while the unit tests "
            "pass, the per-response signal is no longer crossing the "
            "BaseHTTPMiddleware child-Context boundary — see "
            "app/core/csp_nonce.mark_styles_hoisted."
        )

        # No 'unsafe-inline' ANYWHERE on this response — the actual claim.
        offenders = {k: v for k, v in d.items() if "'unsafe-inline'" in v}
        assert not offenders, (
            f"hoisted response still carries 'unsafe-inline': {offenders}"
        )

        # The legacy fallback must be tightened in step, or browsers that
        # ignore the CSP3 split still get the permissive policy.
        assert "'unsafe-inline'" not in d.get("style-src", ""), (
            "style-src fallback still allows 'unsafe-inline', so legacy "
            "browsers get no benefit from the hoist"
        )

        # And the response really did have its attributes rewritten.
        assert 'style="' not in r.text.split("<style", 1)[0]
        assert "data-csp-h=" in r.text


def test_signal_does_not_leak_to_the_next_response():
    """Per-request isolation: one hoisted response must not tighten others.

    The flag lives in a per-request container. If it were process-global (or
    reset incorrectly), a single document print would tighten the CSP for every
    later request in the same worker and break the shell intermittently — the
    worst kind of bug to debug in production.
    """
    from app.core.style_hoist import hoist_inline_styles

    path = "/__csp_hoist_probe2__"

    with temp_route(
        path, lambda: hoist_inline_styles("<body><i style='top:0'>x</i></body>")
    ):
        assert _csp_of(client.get(path)).get("style-src-attr") == "'none'"
        # Immediately afterwards, an ordinary page must be back to the shell
        # policy.
        attr = _csp_of(client.get("/login")).get("style-src-attr")
        assert attr == "'unsafe-inline'", (
            f"CSP hardening leaked from a hoisted response into a later "
            f"request (got {attr!r} on /login) — the flag is not per-request"
        )


def test_non_html_responses_are_unaffected():
    """JSON APIs never get a CSP header; hoisting must not change that."""
    r = client.get("/api/health")
    assert r.status_code in (200, 503)
    assert "content-security-policy" not in {k.lower() for k in r.headers}
