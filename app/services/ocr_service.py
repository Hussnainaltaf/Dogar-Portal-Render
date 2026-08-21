"""OCR validation service for uploaded documents.

Uses pytesseract (fallback) or Pillow text extraction to return:
  { text: str, confidence: float, is_valid: bool }
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from PIL import Image


def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def validate_document(file_path: str) -> Dict[str, any]:
    """Run OCR on an image or PDF file and return extracted text + confidence.

    Returns:
        {
            "text": str,
            "confidence": float,  # 0.0 – 1.0 (approximate)
            "is_valid": bool,     # True if confidence >= 0.5
            "method": str,       # "tesseract" | "pillow" | "unavailable"
        }
    """
    path = Path(file_path)
    if not path.exists():
        return {"text": "", "confidence": 0.0, "is_valid": False, "method": "unavailable", "error": "File not found"}

    # Only process images for now (PDFs would need pdf2image or PyMuPDF)
    suffix = path.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif"}:
        return {"text": "", "confidence": 0.0, "is_valid": False, "method": "unavailable", "error": "Unsupported file type"}

    try:
        image = Image.open(file_path)
        # Convert to RGB if needed (e.g. HEIC, palette mode)
        if image.mode != "RGB":
            image = image.convert("RGB")
    except Exception as exc:
        return {"text": "", "confidence": 0.0, "is_valid": False, "method": "unavailable", "error": str(exc)}

    # Try Tesseract first
    if _tesseract_available():
        try:
            import pytesseract
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            text = " ".join(w for w in data["text"] if w.strip())
            confs = [int(c) for c in data["conf"] if int(c) > 0]
            avg_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0
            return {
                "text": text,
                "confidence": round(avg_conf, 3),
                "is_valid": avg_conf >= 0.5,
                "method": "tesseract",
            }
        except Exception:
            pass

    # Fallback: Pillow basic text detection (no real OCR, just checks if image is readable)
    try:
        # We can't do real OCR without Tesseract, but we can at least return
        # a structured response indicating the file is an image but OCR unavailable
        return {
            "text": "",
            "confidence": 0.0,
            "is_valid": False,
            "method": "unavailable",
            "error": "Tesseract OCR not installed. Install tesseract-ocr to enable text extraction.",
        }
    except Exception as exc:
        return {"text": "", "confidence": 0.0, "is_valid": False, "method": "unavailable", "error": str(exc)}
