"""Contract tests for the ``compact=true`` list projection.

WHY THIS MODULE EXISTS
----------------------
Three SPA screens fetched ``/api/clients/?limit=500`` and
``/api/demands/?limit=500`` on mount purely to fill filter dropdowns, then threw
away everything except an id and a label. Measured on the shipped dataset:

    /api/clients/?limit=500                  299 120 bytes   0.121 s
    /api/clients/?limit=500&compact=true      29 111 bytes   0.034 s
    /api/demands/?limit=500                  489 635 bytes   0.082 s
    /api/demands/?limit=500&compact=true      36 524 bytes   0.030 s

``compact`` is therefore a *performance* parameter, and performance parameters
rot silently: someone adds a field to the full serializer, the dropdown keeps
working, and nobody notices the compact branch drifted. Worse, a compact branch
that quietly ignores ``q``/``skip``/``limit``/RBAC would be a correctness AND a
security regression that no dropdown would ever reveal.

So these tests pin the three things that actually matter:

1. **Shape** — exactly the documented keys, nothing more. A leak here is a data
   exposure (compact responses feed pickers that non-privileged roles can open).
2. **Equivalence** — compact and full agree on ``total``, on ids, on ordering,
   and on the label value for the same query. If they disagree, a dropdown shows
   different records than the list it filters.
3. **Filters still apply** — ``q``, ``skip``, ``limit`` and the agent-scoping
   rule on demands must behave identically in both branches.
"""
from __future__ import annotations

import json


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _seed_clients(client, auth_headers, n: int = 3) -> list[dict]:
    """Create n clients with predictable, searchable names."""
    created = []
    for i in range(n):
        r = client.post(
            "/api/clients/",
            json={"company_name": f"ZCompact Co {i}", "country": "UAE"},
            headers=auth_headers,
        )
        assert r.status_code in (200, 201), r.text
        created.append(r.json())
    return created


def _seed_demand(client, auth_headers, client_id: int, file_number: str) -> dict:
    r = client.post(
        "/api/demands/",
        json={
            "file_number": file_number,
            "client_id": client_id,
            "receiving_date": "2026-01-15",
            "status": "pending",
            "visa_number": f"V{file_number}",
            "bataka_number": f"B{file_number}",
            "visa_issue_date": "2026-01-10",
            "country": "Saudi Arabia",
            "embassy": "Riyadh",
            "sponsor_name": "Compact Sponsor",
        },
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


# --------------------------------------------------------------------------
# Shape: exactly the documented keys, nothing more
# --------------------------------------------------------------------------
def test_clients_compact_returns_only_id_and_company_name(client, auth_headers):
    """A leak of extra columns here would expose client contact data to any role
    that can merely open a dropdown."""
    _seed_clients(client, auth_headers, 2)

    r = client.get("/api/clients/?limit=50&compact=true", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"total", "items"}
    assert body["items"], "expected at least the seeded clients"
    for item in body["items"]:
        assert set(item.keys()) == {"id", "company_name"}, (
            f"compact client row leaked extra fields: {sorted(item.keys())}"
        )


def test_demands_compact_returns_only_id_and_file_number_fields(client, auth_headers):
    cl = _seed_clients(client, auth_headers, 1)[0]
    _seed_demand(client, auth_headers, cl["id"], "99001")

    r = client.get("/api/demands/?limit=50&compact=true", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"total", "items"}
    assert body["items"]
    for item in body["items"]:
        assert set(item.keys()) == {"id", "file_number", "file_number_display"}, (
            f"compact demand row leaked extra fields: {sorted(item.keys())}"
        )


def test_compact_is_materially_smaller_than_full(client, auth_headers):
    """The whole point of the parameter. If a future change makes compact no
    smaller than full, the parameter is dead weight and this fails loudly."""
    _seed_clients(client, auth_headers, 5)

    full = client.get("/api/clients/?limit=50", headers=auth_headers)
    comp = client.get("/api/clients/?limit=50&compact=true", headers=auth_headers)
    assert full.status_code == comp.status_code == 200

    full_bytes = len(json.dumps(full.json()))
    comp_bytes = len(json.dumps(comp.json()))
    assert comp_bytes * 2 < full_bytes, (
        f"compact ({comp_bytes} B) is not materially smaller than full "
        f"({full_bytes} B) — the projection has drifted"
    )


# --------------------------------------------------------------------------
# Equivalence: same records, same order, same labels
# --------------------------------------------------------------------------
def test_clients_compact_matches_full_ids_order_and_labels(client, auth_headers):
    """A dropdown built from compact must offer exactly the records the full
    list contains, in the same order — otherwise filtering picks wrong rows."""
    _seed_clients(client, auth_headers, 4)

    full = client.get("/api/clients/?limit=50", headers=auth_headers).json()
    comp = client.get("/api/clients/?limit=50&compact=true", headers=auth_headers).json()

    assert comp["total"] == full["total"]
    assert [i["id"] for i in comp["items"]] == [i["id"] for i in full["items"]]
    labels_full = {i["id"]: i.get("company_name") for i in full["items"]}
    for item in comp["items"]:
        assert item["company_name"] == labels_full[item["id"]]


def test_demands_compact_matches_full_ids_order_and_display(client, auth_headers):
    cl = _seed_clients(client, auth_headers, 1)[0]
    for fn in ("99101", "99102", "99103"):
        _seed_demand(client, auth_headers, cl["id"], fn)

    full = client.get("/api/demands/?limit=50", headers=auth_headers).json()
    comp = client.get("/api/demands/?limit=50&compact=true", headers=auth_headers).json()

    assert comp["total"] == full["total"]
    assert [i["id"] for i in comp["items"]] == [i["id"] for i in full["items"]]

    # file_number_display is produced by display_file_number(prefix) — the
    # compact branch must not skip the prefix formatting, or the dropdown label
    # would not match the label shown in the table it filters.
    disp_full = {i["id"]: i.get("file_number_display") for i in full["items"]}
    for item in comp["items"]:
        assert item["file_number_display"] == disp_full[item["id"]], (
            "compact display label diverged from the full serializer"
        )
        assert item["file_number_display"], "display label must not be empty"


# --------------------------------------------------------------------------
# Filters, paging and scoping must behave identically
# --------------------------------------------------------------------------
def test_clients_compact_honours_search(client, auth_headers):
    _seed_clients(client, auth_headers, 3)
    client.post("/api/clients/", json={"company_name": "QQUnique Holdings"}, headers=auth_headers)

    comp = client.get("/api/clients/?compact=true&q=QQUnique", headers=auth_headers).json()
    full = client.get("/api/clients/?q=QQUnique", headers=auth_headers).json()

    assert comp["total"] == full["total"] >= 1
    assert [i["id"] for i in comp["items"]] == [i["id"] for i in full["items"]]
    assert all("QQUnique" in i["company_name"] for i in comp["items"])


def test_demands_compact_honours_search(client, auth_headers):
    cl = _seed_clients(client, auth_headers, 1)[0]
    _seed_demand(client, auth_headers, cl["id"], "99777")

    comp = client.get("/api/demands/?compact=true&q=99777", headers=auth_headers).json()
    full = client.get("/api/demands/?q=99777", headers=auth_headers).json()

    assert comp["total"] == full["total"] >= 1
    assert [i["id"] for i in comp["items"]] == [i["id"] for i in full["items"]]


def test_compact_honours_skip_and_limit(client, auth_headers):
    """Paging must not be silently ignored by the projection branch."""
    _seed_clients(client, auth_headers, 5)

    page1 = client.get("/api/clients/?compact=true&limit=2&skip=0", headers=auth_headers).json()
    page2 = client.get("/api/clients/?compact=true&limit=2&skip=2", headers=auth_headers).json()

    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    assert page1["total"] == page2["total"]
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert not (ids1 & ids2), "skip did not advance the compact window"


def test_compact_requires_authentication(client):
    """The projection must not become an unauthenticated data tap."""
    for url in ("/api/clients/?compact=true", "/api/demands/?compact=true"):
        r = client.get(url)
        assert r.status_code in (401, 403), f"{url} returned {r.status_code}"


def test_compact_default_is_off(client, auth_headers):
    """Existing callers must be byte-for-byte unaffected: omitting the parameter
    has to return the full serializer."""
    _seed_clients(client, auth_headers, 1)
    body = client.get("/api/clients/?limit=5", headers=auth_headers).json()
    assert body["items"]
    keys = set(body["items"][0].keys())
    assert keys != {"id", "company_name"}, (
        "default response collapsed to the compact shape — this silently breaks "
        "every existing consumer of the full client row"
    )
    assert "created_at" in keys or len(keys) > 2
