"""AUDIT H-1 (2026-07-10): PDF background filename sanitisation.

Verifies that _normalize_bg_filename collapses any path-traversal / absolute /
nested value down to a bare, safe filename, and that _safe_bg_path never returns
a path outside PDF_BG_DIR.
"""
from __future__ import annotations

import os


def test_normalize_strips_traversal_sequences():
    from app.api.endpoints.documents import _normalize_bg_filename

    # Classic traversal attempts must collapse to just the leaf name (or empty).
    assert _normalize_bg_filename("../../etc/passwd") == "passwd"
    assert _normalize_bg_filename("....//....//etc/shadow") == "shadow"
    assert _normalize_bg_filename("/etc/passwd") == "passwd"
    assert _normalize_bg_filename("subdir/nested/img.jpg") == "img.jpg"
    assert _normalize_bg_filename("..\\..\\windows\\win.ini") == "win.ini"


def test_normalize_rejects_pure_traversal_tokens():
    from app.api.endpoints.documents import _normalize_bg_filename

    assert _normalize_bg_filename("..") == ""
    assert _normalize_bg_filename(".") == ""
    assert _normalize_bg_filename("/") == ""


def test_normalize_preserves_legit_names_and_strips_url_prefix():
    from app.api.endpoints.documents import _normalize_bg_filename

    assert _normalize_bg_filename("tpl_5_ab12cd34.jpg") == "tpl_5_ab12cd34.jpg"
    assert _normalize_bg_filename("/static/pdf_backgrounds/form7.jpg") == "form7.jpg"
    assert _normalize_bg_filename("static/pdf_backgrounds/form7.jpg") == "form7.jpg"


def test_safe_bg_path_stays_inside_backgrounds_dir():
    from app.api.endpoints.documents import _safe_bg_path
    from app.core.config import settings

    base = os.path.realpath(settings.PDF_BG_DIR)

    # A traversal attempt must never resolve outside PDF_BG_DIR.
    p = _safe_bg_path("../../etc/passwd")
    assert p is not None
    assert p == os.path.realpath(os.path.join(base, "passwd"))
    assert p.startswith(base + os.sep) or p == base

    # Empty / pure-traversal → None.
    assert _safe_bg_path("") is None
    assert _safe_bg_path("..") is None

    # A normal filename resolves directly under the backgrounds dir.
    ok = _safe_bg_path("logo.jpg")
    assert ok == os.path.join(base, "logo.jpg")


def test_pdf_engine_resolver_rejects_traversal():
    from app.services.pdf_engine import _resolve_background_path

    class _Tpl:
        background_image = "../../../../etc/passwd"

    # Even if /etc/passwd exists, the resolver must not return it: after
    # basename-collapse it looks for "passwd" inside the backgrounds dirs, where
    # it does not exist → None.
    assert _resolve_background_path(_Tpl()) is None
