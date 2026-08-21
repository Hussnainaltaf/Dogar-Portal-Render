"""Portal backup creation, listing, download, and archive structure."""
from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

from app.api.endpoints import backups


def test_admin_can_create_list_and_download_backup(client, auth_headers, monkeypatch, tmp_path):
    backup_dir = tmp_path / "backups"
    asset_dir = tmp_path / "uploads"
    asset_dir.mkdir()
    (asset_dir / "proof.txt").write_text("portal asset", encoding="utf-8")
    monkeypatch.setattr(backups, "_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backups, "_ASSET_DIRS", (asset_dir,))

    created = client.post("/api/backups/", headers=auth_headers)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["filename"].startswith("portal-backup-root-")

    listed = client.get("/api/backups/", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    assert [row["filename"] for row in listed.json()["items"]] == [item["filename"]]

    downloaded = client.get(f"/api/backups/{item['filename']}", headers=auth_headers)
    assert downloaded.status_code == 200, downloaded.text
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "assets/uploads/proof.txt" in names
        assert any(name.startswith("database/") and name.endswith(".jsonl") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "dogar-portal-backup-v1"
        assert manifest["tenant"] == "root"
        assert manifest["tables"]
        assert manifest["assets"] == [{"path": "assets/uploads/proof.txt", "size": 12}]


def test_backup_download_rejects_invalid_filename(client, auth_headers):
    response = client.get("/api/backups/not-a-backup.zip", headers=auth_headers)
    assert response.status_code == 404


def _manager_headers() -> dict:
    """A manager has settings:view but NOT the new settings:backup permission."""
    from app.core.security import create_access_token, hash_password
    from app.db.session import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        email = "backup_manager@test.local"
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            existing.role = "manager"
            existing.is_active = True
            existing.is_super_admin = False
            db.commit()
            uid = existing.id
        else:
            u = User(
                name="Manager User",
                email=email,
                role="manager",
                password_hash=hash_password("Passw0rd!123"),
                is_active=True,
                is_super_admin=False,
            )
            db.add(u)
            db.commit()
            db.refresh(u)
            uid = u.id
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token(str(uid))}"}


def test_backup_endpoints_deny_non_backup_permission(client):
    """Whole-DB export must be gated by settings:backup, not the broad
    settings:view a manager holds (M-audit 2026-07-24). A manager can open
    the settings screen but must NOT be able to list, create, or download
    a backup containing users / login_history / password_reset_tokens."""
    headers = _manager_headers()
    assert client.get("/api/backups/", headers=headers).status_code == 403
    assert client.post("/api/backups/", headers=headers).status_code == 403
    assert client.get("/api/backups/portal-backup-root-20200101T000000Z.zip",
                      headers=headers).status_code == 403


def test_tenant_asset_export_is_fail_closed(monkeypatch, tmp_path):
    uploads = tmp_path / "uploads"
    (uploads / "acme").mkdir(parents=True)
    (uploads / "other").mkdir()
    (uploads / "acme" / "owned.txt").write_text("owned", encoding="utf-8")
    (uploads / "other" / "secret.txt").write_text("secret", encoding="utf-8")
    (uploads / "global.txt").write_text("legacy global", encoding="utf-8")
    (uploads / "acme-logo.png").write_bytes(b"logo")
    monkeypatch.setattr(backups, "_ASSET_DIRS", (uploads,))

    request = SimpleNamespace(
        state=SimpleNamespace(
            tenant=SimpleNamespace(
                slug="acme",
                logo_filename="acme-logo.png",
                letterhead_path="../outside.pdf",
            )
        )
    )
    exported = {
        path.resolve().relative_to(root.resolve()).as_posix()
        for root, path in backups._iter_tenant_assets(request, "acme")
    }

    assert exported == {"acme/owned.txt", "acme-logo.png"}
