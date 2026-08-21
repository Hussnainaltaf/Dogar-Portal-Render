"""OCR validation endpoint for document uploads.

POST /api/v1/documents/ocr-validate  (multipart file=image)
    Returns: { text, confidence, is_valid, method }
    422 if confidence is below threshold or file is invalid.
"""
from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.metrics import record_ocr_validation
from app.models import User
from app.services.ocr_service import validate_document

router = APIRouter()


class OCRValidateOut(BaseModel):
    text: str
    confidence: float
    is_valid: bool
    method: str
    error: str | None = None


@router.post("/ocr-validate", response_model=OCRValidateOut)
async def ocr_validate(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload an image or PDF and run OCR validation.

    Returns extracted text, confidence score, and validity.
    Rejects files with confidence < 0.5 or unsupported formats (422).
    """
    name = (file.filename or "").lower()
    ct = (file.content_type or "").lower()
    allowed_exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif", ".pdf")
    looks_valid = ct.startswith("image/") or ct == "application/pdf" or name.endswith(allowed_exts)

    if not looks_valid:
        record_ocr_validation("rejected")
        raise HTTPException(
            status_code=422,
            detail="Unsupported file type. Accepts: JPG, PNG, WEBP, HEIC, TIF, BMP, GIF, PDF.",
        )

    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        record_ocr_validation("rejected")
        raise HTTPException(status_code=422, detail="File too large (max 25 MB).")
    if len(content) < 1024:
        record_ocr_validation("rejected")
        raise HTTPException(status_code=422, detail="File too small or empty.")

    # Save to temp file for OCR processing
    suffix = os.path.splitext(name)[1] or ".tmp"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = validate_document(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    # Graceful degradation: if no OCR backend is installed the service
    # returns method="unavailable". Surface that as a machine-readable
    # 422 with an actionable message rather than a bare "OCR failed"
    # string so the frontend can render a helpful hint. We intentionally
    # do NOT return 503 -- that would break the SPA's axios error handler
    # into a silent global toast instead of an inline field-level message.
    if result.get("method") == "unavailable" or result.get("error"):
        record_ocr_validation("failure")
        detail = result.get("error") or (
            "OCR is unavailable on this server (Tesseract not installed and "
            "AI vision not configured). Please upload a clearer image or "
            "enter the document details manually."
        )
        raise HTTPException(
            status_code=422,
            detail=detail,
        )

    confidence = result.get("confidence", 0.0)
    is_valid = result.get("is_valid", False)
    if not is_valid:
        record_ocr_validation("failure")
        raise HTTPException(
            status_code=422,
            detail=f"OCR confidence too low ({confidence:.2f} < 0.50). Please upload a clearer image.",
        )

    record_ocr_validation("success")
    return OCRValidateOut(
        text=result.get("text", ""),
        confidence=confidence,
        is_valid=is_valid,
        method=result.get("method", "unknown"),
    )
