"""Guards for the SPA's critical rendering path.

BACKGROUND
----------
app/frontend/index.html is the document every authenticated screen boots from.
Anything render-blocking in its <head> is paid on EVERY route by EVERY user,
which makes it the easiest place in the whole project to accidentally destroy
page-load performance — and the hardest to notice, because the app still works
perfectly, just slower.

Two specific regressions are pinned here.

1. CHART.JS MUST NOT BE A BLOCKING SCRIPT IN <head>.
   It used to be:
       <script src="/static/vendor/js/chart.umd.min.js"></script>
   A classic, non-deferred script: the browser stops parsing the document,
   downloads 205 KB (68 KB gzipped) and evaluates it BEFORE it can even discover
   the React module bundle. Chart.js is imported by exactly one page
   (pages/Dashboard.tsx) and always via ensureChart(), so src/lib/chart.ts now
   injects the same self-hosted bundle on first use instead. Non-Dashboard
   routes pay zero bytes for charting.

   Verified when the change was made: the served index.html contains no
   reference to chart.umd, yet the server logged six 200 OK responses for it
   during a Dashboard session, and the dashboard canvases rendered real pixels.

2. FONT PRELOADS MUST STAY.
   Webfonts declared inside a stylesheet are only discovered after that
   stylesheet is fetched and parsed, so the shell first-painted in fallback
   glyphs and then reflowed. The three faces visible on first paint are
   preloaded so they download alongside the HTML.

Both are trivially easy to undo while "tidying up the head", and neither would
fail any functional test. Hence these.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC_HTML = ROOT / "app" / "frontend" / "index.html"
BUILT_HTML = ROOT / "app" / "static" / "spa" / "index.html"
CHART_TS = ROOT / "app" / "frontend" / "src" / "lib" / "chart.ts"
VENDOR = ROOT / "app" / "static" / "vendor"


def _html_files():
    """The source template plus the built artefact, when the SPA has been built."""
    files = [SRC_HTML]
    if BUILT_HTML.is_file():
        files.append(BUILT_HTML)
    return files


# ---------------------------------------------------------------------------
# 1. Chart.js off the critical path
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", _html_files(), ids=lambda p: p.name + ":" + p.parent.name)
def test_chartjs_is_not_a_blocking_script_tag(path: Path):
    html = path.read_text(encoding="utf-8")
    # Strip HTML comments first: the head documents WHY the tag was removed and
    # names the file, which would otherwise trip a naive substring search.
    stripped = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    tags = re.findall(r"<script[^>]*src=[\"'][^\"']*chart\.umd[^\"']*[\"'][^>]*>", stripped)
    assert not tags, (
        f"{path} reintroduced a blocking Chart.js <script> in the document head "
        f"({tags}). That costs every route 205 KB / 68 KB gzipped before the "
        f"React bundle is even discovered. Load it on demand via "
        f"src/lib/chart.ts::ensureChart() instead."
    )


def test_chart_module_loads_the_bundle_on_demand():
    """ensureChart() must actually inject the script, or the Dashboard silently
    renders empty canvases once the head tag is gone."""
    src = CHART_TS.read_text(encoding="utf-8")
    assert "/static/vendor/js/chart.umd.min.js" in src, (
        "lib/chart.ts no longer references the self-hosted bundle"
    )
    assert "createElement('script')" in src, (
        "lib/chart.ts no longer injects the bundle — with no <head> tag either, "
        "Dashboard charts will never render"
    )
    # Must stay self-hosted: the strict CSP (script-src 'self' 'nonce-…') blocks
    # third-party scripts, and a CDN URL here would fail silently in production.
    #
    # Only CODE counts. The module's header comment deliberately records the old
    # jsdelivr URL as history, so comment lines are stripped before this check —
    # otherwise the guard would fire on its own documentation. (It did, the first
    # time this test ran.)
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )
    assert "://" not in code, (
        "Chart.js must be loaded from the same origin — the CSP blocks external "
        "scripts, so an absolute URL would silently break the Dashboard:\n"
        + "\n".join(l for l in code.splitlines() if "://" in l)
    )


def test_chart_bundle_is_actually_present_on_disk():
    """A same-origin URL is only useful if the file ships."""
    f = VENDOR / "js" / "chart.umd.min.js"
    assert f.is_file(), f"missing vendored Chart.js at {f}"
    assert f.stat().st_size > 100_000, "vendored Chart.js looks truncated"


def test_dashboard_is_the_only_chart_consumer():
    """If a second page starts importing charts, the on-demand trade-off should
    be re-evaluated deliberately rather than by accident."""
    pages = ROOT / "app" / "frontend" / "src" / "pages"
    users = sorted(
        p.name for p in pages.glob("*.tsx")
        if "ensureChart" in p.read_text(encoding="utf-8")
    )
    assert users == ["Dashboard.tsx"], (
        f"chart consumers changed: {users}. On-demand loading is chosen because "
        f"exactly one route needs Chart.js; revisit if that is no longer true."
    )


# ---------------------------------------------------------------------------
# 2. First-paint font preloads
# ---------------------------------------------------------------------------
FIRST_PAINT_FONTS = [
    "Inter-400.woff2",     # body text
    "Inter-600.woff2",     # headings, table headers, buttons
    "fa-solid-900.woff2",  # every sidebar / topbar / button icon
]


@pytest.mark.parametrize("font", FIRST_PAINT_FONTS)
def test_first_paint_fonts_are_preloaded(font: str):
    html = SRC_HTML.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<link[^>]*rel=[\"']preload[\"'][^>]*" + re.escape(font) + r"[^>]*>"
        r"|<link[^>]*" + re.escape(font) + r"[^>]*rel=[\"']preload[\"'][^>]*>"
    )
    assert pattern.search(html), (
        f"{font} is no longer preloaded. Fonts declared in a stylesheet are "
        f"discovered a full round trip late, so the shell first-paints in "
        f"fallback glyphs and then reflows."
    )


@pytest.mark.parametrize("font", FIRST_PAINT_FONTS)
def test_preloaded_fonts_exist_and_are_crossorigin(font: str):
    """A preload pointing at a missing file is a wasted request; a font preload
    without crossorigin is fetched TWICE (fonts are always CORS requests)."""
    f = VENDOR / "fonts" / font
    assert f.is_file(), f"preloaded font missing on disk: {f}"

    html = SRC_HTML.read_text(encoding="utf-8")
    line = next(l for l in html.splitlines() if font in l and "preload" in l)
    assert "crossorigin" in line, (
        f"preload for {font} lacks crossorigin — the browser will download it "
        f"twice, making the preload a net loss:\n  {line.strip()}"
    )
    assert 'as="font"' in line, f"preload for {font} lacks as=\"font\":\n  {line.strip()}"


def test_no_external_origins_in_the_head():
    """The CSP is 'self'-only; an external CDN link would silently fail. This
    also keeps the privacy/reliability property of full self-hosting."""
    html = re.sub(r"<!--.*?-->", "", SRC_HTML.read_text(encoding="utf-8"), flags=re.S)
    external = re.findall(r"(?:href|src)=[\"'](https?://[^\"']+)[\"']", html)
    assert not external, f"external asset references in index.html: {external}"
