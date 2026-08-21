"""Run the frontend reference-list cache check from the Python suite.

WHY THIS BRIDGE EXISTS
----------------------
`pytest` is this project's single test entrypoint — it is what the release
checklist runs and what the documentation counts. The shared reference-list
cache (app/frontend/src/lib/options.ts) is the client half of the biggest
performance change in v1.1.x:

    /candidates cold mount   826 KB  ->  ~103 KB
    /candidates return visit ~103 KB  ->  0 KB   (served from the cache)

If someone later "simplifies" that module — drops the in-flight dedupe, removes
`compact: true`, or changes a dropdown's value from a name to an id — nothing in
the Python suite would notice, the UI would still look right, and the
regression would ship. Exactly the class of silent regression this codebase has
been bitten by before.

So the Node check runs here too. It needs no server, no database and no
browser; it bundles the real module with a stubbed axios client and asserts on
request counts, in ~1 second.

The test SKIPS (rather than fails) when Node or the frontend's node_modules are
absent, so a backend-only environment can still run the suite.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "app" / "frontend"
RUNNER = FRONTEND / "tests" / "run-options-check.mjs"


def _node() -> str | None:
    return shutil.which("node")


requires_node = pytest.mark.skipif(
    _node() is None or not (FRONTEND / "node_modules").is_dir(),
    reason="node and app/frontend/node_modules are required for the frontend cache check",
)


@requires_node
def test_options_cache_check_passes():
    """Execute app/frontend/tests/optionsCache.check.ts and require a clean exit."""
    assert RUNNER.is_file(), f"missing runner: {RUNNER}"

    proc = subprocess.run(
        [_node(), str(RUNNER)],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "NO_COLOR": "1"},
    )
    output = proc.stdout + proc.stderr

    # Surface the check's own PASS/FAIL lines in the pytest failure report —
    # a bare exit code would make this test useless to debug.
    assert proc.returncode == 0, (
        "frontend reference-list cache check failed\n"
        "-------- output --------\n" + output
    )
    assert "ALL CACHE CHECKS PASSED" in output, output


@requires_node
def test_options_cache_check_is_wired_into_npm_scripts():
    """`npm run test:options` must exist, or the check is invisible to anyone
    working in the frontend and will rot."""
    pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    scripts = pkg.get("scripts", {})
    assert "test:options" in scripts, (
        "app/frontend/package.json is missing the test:options script"
    )
    assert "run-options-check" in scripts["test:options"]


def test_pages_do_not_bulk_fetch_reference_lists_directly():
    """Static guard: no screen may bulk-fetch a reference list on its own.

    Runs with no Node at all, so it protects the perf fix even in a backend-only
    environment. A page that reverts to
    ``api.get('/clients/', { params: { limit: 500 } })`` would reintroduce
    ~790 KB per mount while still looking perfectly correct on screen — the
    exact kind of silent regression this project keeps getting bitten by.

    The signature of an *option* fetch is a HARDCODED large limit. A paginated
    table load uses ``limit: pageSize`` and is legitimate — e.g. Demands.tsx
    line ~150 loads its own table with ``api.get('/demands/', { params })`` and
    must keep doing so. So this matches the literal bulk limits only, not the
    endpoint name.
    """
    import re

    src = FRONTEND / "src" / "pages"
    bulk = re.compile(r"limit:\s*(200|300|500)\b")
    offenders: list[str] = []

    for path in sorted(src.glob("*.tsx")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if bulk.search(line) and "api.get(" in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "these call sites bulk-fetch a reference list directly instead of going "
        "through lib/options.ts (which caches it and requests the compact "
        "projection):\n  " + "\n  ".join(offenders)
    )


def test_heavy_pages_import_the_options_cache():
    """The three screens the perf work targeted must actually use the module."""
    src = FRONTEND / "src"
    missing = [
        name for name in ("pages/Candidates.tsx", "pages/Demands.tsx", "pages/DemandDetail.tsx")
        if "@/lib/options" not in (src / name).read_text(encoding="utf-8")
    ]
    assert not missing, (
        "these screens no longer import the shared reference-list cache: " + ", ".join(missing)
    )


def test_options_module_exists_and_uses_compact():
    """The cache module itself must still ask for the server-side projection."""
    mod = (FRONTEND / "src" / "lib" / "options.ts").read_text(encoding="utf-8")
    assert "compact: true" in mod, "lib/options.ts no longer requests compact=true"
    assert "inflight" in mod, "lib/options.ts lost its in-flight dedupe"
