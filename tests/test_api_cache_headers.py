"""Authenticated API reads must never be served stale from the browser cache.

WHY THIS FILE EXISTS
--------------------
`CacheHeadersMiddleware` used to stamp `private, max-age=30` on every
authenticated API GET, justified in a comment by "we always invalidate on
POST/PUT/DELETE". A server cannot do that. `max-age` is a promise to the
BROWSER's private HTTP cache, and a later PUT to a different URL has no way to
reach into that cache and evict anything. The consequence was that for 30
seconds after ANY successful save, a reload was answered from disk with the
PRE-SAVE body, without the request ever leaving the machine.

It was found by driving the tenant typeface control in a real browser: the
select was changed to `mono`, the PUT went out carrying {"font_key":"mono"},
the API answered 200, the registry row read 'mono' -- and reopening the drawer
still showed 'serif'. Every individual layer was correct and the operator was
still shown a lie. That is the most expensive shape a bug can take, because it
looks exactly like the save failing, so the natural next action is to save
again (and, for a destructive edit, to redo work that was never lost).

Not one test in the suite asserted on a cache header before this file, which
is why a mutation of the caching policy was free. These tests are cheap and
they close that off: they assert on the POLICY (no reuse without
revalidation), not on the exact header string, so a future tightening such as
`no-store` or `no-cache, private, must-revalidate` still passes while any
reintroduction of a reusable freshness window fails.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Endpoints whose bodies change as soon as an operator saves something, and
# which the SPA re-reads immediately after a save. Each one is a route where a
# stale body is directly visible as "my change did not save".
# NOTE the trailing slashes on the list routes: those routers declare
# `@router.get("/")` under a `/api/<thing>` prefix, so the collection lives at
# `/api/candidates/`. Without the slash they 404, both probes skip, and the
# coverage silently drops to the two tenant routes -- which is exactly the
# vacuity the guard at the bottom of this file exists to catch. It caught it.
READ_PATHS = [
    "/api/tenants",
    "/api/tenants/_meta/overview",
    "/api/candidates/",
    "/api/clients/",
]


def _max_age(cache_control: str) -> int | None:
    """The reusable freshness window this header grants, in seconds.

    Returns None when the header grants no reuse without revalidation.
    `no-cache` and `no-store` both mean "do not reuse this body blindly", so a
    numeric max-age alongside them is not a reuse window.
    """
    cc = (cache_control or "").lower()
    if "no-store" in cc or "no-cache" in cc:
        return None
    for part in cc.split(","):
        part = part.strip()
        if part.startswith("max-age="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


@pytest.mark.parametrize("path", READ_PATHS)
def test_authenticated_reads_are_not_blindly_reusable(
    client: TestClient, auth_headers: dict, path: str
) -> None:
    """No authenticated API read may grant a reuse window to the browser.

    This is the assertion that fails if `private, max-age=30` -- or any other
    positive freshness window -- comes back.
    """
    r = client.get(path, headers=auth_headers)
    # A route that has moved or now requires extra scope is not this test's
    # business, but it MUST NOT silently pass by being skipped into oblivion:
    # the fixed list above is asserted to be non-empty and reachable below.
    if r.status_code not in (200, 401, 403):
        pytest.skip(f"{path} answered {r.status_code}; not a readable list here")

    cc = r.headers.get("cache-control", "")
    assert cc, f"{path} returned NO Cache-Control at all -- caching is then " \
               f"up to browser heuristics, which is worse than an explicit policy"
    window = _max_age(cc)
    assert window is None, (
        f"{path} grants the browser a {window}s reuse window "
        f"(Cache-Control: {cc!r}). A save followed by a reload inside that "
        f"window is answered from the browser's own cache with the pre-save "
        f"body, so the operator sees their change vanish. The server cannot "
        f"invalidate a browser cache on write -- only revalidation can."
    )


@pytest.mark.parametrize("path", READ_PATHS)
def test_authenticated_reads_are_marked_private(
    client: TestClient, auth_headers: dict, path: str
) -> None:
    """Per-user data must never be storable by a shared/proxy cache.

    Separate from the staleness question: `public` on an authenticated body
    would let an intermediary serve one tenant's data to another.
    """
    r = client.get(path, headers=auth_headers)
    if r.status_code not in (200, 401, 403):
        pytest.skip(f"{path} answered {r.status_code}")
    cc = r.headers.get("cache-control", "").lower()
    assert "public" not in cc, (
        f"{path} is marked `public` ({cc!r}) while carrying authenticated "
        f"data -- a shared cache may then hand it to a different user"
    )


def test_the_probe_paths_are_actually_reachable(
    client: TestClient, auth_headers: dict
) -> None:
    """Guard against the whole file quietly becoming vacuous.

    Both tests above skip on an unexpected status. If every path started
    404ing after a refactor, the file would report all-green while asserting
    nothing whatsoever -- the self-disabling failure mode this project has
    already been bitten by several times. So at least one probe path must
    answer 200.
    """
    live = [p for p in READ_PATHS
            if client.get(p, headers=auth_headers).status_code == 200]
    assert live, (
        f"none of {READ_PATHS} answered 200, so both cache-header suites "
        f"skipped everything and verified nothing"
    )


def test_static_hashed_bundles_may_still_be_cached_hard(client: TestClient) -> None:
    """The fix must not have thrown away the caching that IS safe.

    Vite emits content-hashed filenames, so those bundles are immutable by
    construction and caching them for a year is correct. If this ever starts
    failing, the staleness fix was applied with too broad a brush and every
    page load got slower for nothing.
    """
    import glob
    import os

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "app", "static", "spa", "assets")
    hits = sorted(glob.glob(os.path.join(root, "index-*.js")))
    if not hits:
        pytest.skip("no built SPA assets present in this checkout")
    name = os.path.basename(hits[0])
    r = client.get(f"/static/spa/assets/{name}")
    assert r.status_code == 200, f"built asset {name} not served: {r.status_code}"
    cc = r.headers.get("cache-control", "").lower()
    assert "max-age=31536000" in cc and "immutable" in cc, (
        f"content-hashed bundle {name} is no longer cached hard "
        f"(Cache-Control: {cc!r}); hashed filenames make this safe and "
        f"dropping it costs a re-download on every navigation"
    )
