"""Tenant-aware, administrator-only portal backup exports."""
from __future__ import annotations

import base64
import json
import os
import re
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import require_permission
from app.core.tenancy import get_tenant_db
from app.models import User

router = APIRouter()
_BACKUP_NAME = re.compile(r"^portal-backup-[a-zA-Z0-9_-]+-\d{8}T\d{6}Z\.zip$")
_BACKUP_DIR = Path("data/backups")
_ASSET_DIRS = (Path(settings.UPLOAD_DIR), Path(settings.PDF_BG_DIR))


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return {"$binary_base64": base64.b64encode(value).decode("ascii")}
    return str(value)


def _tenant_slug(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    raw = getattr(tenant, "slug", None) or "root"
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw)[:60] or "root"


def _safe_backup_path(filename: str) -> Path:
    if not _BACKUP_NAME.fullmatch(filename):
        raise HTTPException(404, "Backup not found")
    path = (_BACKUP_DIR / filename).resolve()
    if path.parent != _BACKUP_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "Backup not found")
    return path


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _iter_tenant_assets(request: Request, slug: str):
    """Yield owned asset files as ``(configured_root, file_path)`` pairs.

    Root-portal backups retain the legacy all-assets behavior. Tenant backups
    only include files below ``<asset-root>/<tenant-slug>/`` plus explicitly
    referenced tenant logo/letterhead files that remain inside a configured
    asset root. This fail-closed policy prevents one tenant from exporting a
    different tenant's files when legacy deployments share upload folders.
    """
    seen: set[Path] = set()

    def emit(root: Path, path: Path):
        resolved = path.resolve()
        if (
            resolved in seen
            or not path.is_file()
            or path.is_symlink()
            or not _is_within(resolved, root)
        ):
            return None
        seen.add(resolved)
        return root, path

    for asset_root in _ASSET_DIRS:
        if not asset_root.is_dir():
            continue
        scan_root = asset_root if slug == "root" else asset_root / slug
        if scan_root.is_dir():
            for path in scan_root.rglob("*"):
                item = emit(asset_root, path)
                if item:
                    yield item

    if slug == "root":
        return

    tenant = getattr(request.state, "tenant", None)
    for attribute in ("logo_filename", "letterhead_path"):
        raw = str(getattr(tenant, attribute, "") or "").strip()
        if not raw:
            continue
        supplied = Path(raw)
        for asset_root in _ASSET_DIRS:
            candidates = [supplied] if supplied.is_absolute() else [asset_root / supplied]
            for candidate in candidates:
                item = emit(asset_root, candidate)
                if item:
                    yield item
                    break


@router.get("/")
def list_backups(request: Request, user: User = Depends(require_permission("settings:backup"))):
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    slug = _tenant_slug(request)
    prefix = f"portal-backup-{slug}-"
    items = []
    for path in sorted(_BACKUP_DIR.glob(f"{prefix}*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        stat = path.stat()
        items.append({
            "filename": path.name,
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "download_url": f"/backups/{path.name}",
        })
    return {"items": items}


@router.post("/", status_code=201)
def create_backup(
    request: Request,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("settings:backup")),
):
    """Create a portable ZIP containing every table row plus stored portal assets."""
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    slug = _tenant_slug(request)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"portal-backup-{slug}-{stamp}.zip"
    final_path = _BACKUP_DIR / filename
    temp_path = final_path.with_suffix(".tmp")
    engine = db.get_bind()
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    manifest = {
        "format": "dogar-portal-backup-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "tenant": slug,
        "created_by_user_id": user.id,
        "database_dialect": engine.dialect.name,
        "tables": {},
        "assets": [],
    }
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            preparer = engine.dialect.identifier_preparer
            for table in tables:
                quoted = preparer.quote(table)
                rows = db.execute(text(f"SELECT * FROM {quoted}"))
                count = 0
                with archive.open(f"database/{table}.jsonl", "w") as output:
                    for row in rows.mappings():
                        payload = {key: _json_value(value) for key, value in row.items()}
                        output.write((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                        count += 1
                manifest["tables"][table] = {
                    "rows": count,
                    "columns": [column["name"] for column in inspector.get_columns(table)],
                }
            for asset_root, path in _iter_tenant_assets(request, slug):
                relative = path.resolve().relative_to(asset_root.resolve())
                arcname = Path("assets") / asset_root.name / relative
                archive.write(path, arcname.as_posix())
                manifest["assets"].append({"path": arcname.as_posix(), "size": path.stat().st_size})
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(temp_path, final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    # Keep the 20 newest backups for this tenant to avoid unbounded disk growth.
    tenant_files = sorted(_BACKUP_DIR.glob(f"portal-backup-{slug}-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in tenant_files[20:]:
        old.unlink(missing_ok=True)
    return {
        "filename": filename,
        "size": final_path.stat().st_size,
        "created_at": manifest["created_at"],
        "download_url": f"/backups/{filename}",
    }


@router.get("/{filename}")
def download_backup(
    filename: str,
    request: Request,
    user: User = Depends(require_permission("settings:backup")),
):
    path = _safe_backup_path(filename)
    if not path.name.startswith(f"portal-backup-{_tenant_slug(request)}-"):
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, media_type="application/zip", filename=path.name)
