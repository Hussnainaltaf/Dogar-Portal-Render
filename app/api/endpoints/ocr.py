"""OCR endpoints -- passport / ID scanner + Arabic auto-translate.

POST /api/ocr/passport  (multipart file=passport.jpg)
    Returns:
        200 { ok: true,  method: 'vision'|'mrz'|'text',
              fields: {...}, raw_text: str }
        200 { ok: false, method: 'unavailable', reason: 'no_ocr_backend',
              available_backends: [], install_instructions: {...},
              user_message: str, fields: {} }
        200 { ok: false, method: 'failed',      reason: 'unreadable_image',
              user_message: str, fields: {} }

    fields → Candidate-shaped dict (full_name, father_name, passport_no,
    date_of_birth, passport_expiry_date, passport_issue_date,
    passport_issue_place, cnic, nationality, gender, ...)

    The endpoint NEVER raises 5xx for a missing OCR backend. Instead it
    returns a 200 with ``ok: false`` and a machine-readable ``reason`` so
    the frontend can render a helpful "type the details by hand" hint
    inline (see PassportScanStep.tsx). This matches the "graceful
    degradation" contract in docs/RUN_MIGRATION.md — the wizard must
    stay usable even when the OCR backends are unreachable.

POST /api/ocr/translate-to-arabic  (JSON body: {full_name, father_name, place_of_birth, ...})
    Returns: { ok, arabic: { full_name, father_name, place_of_birth, ... } }
        v8 NEW -- feeds the candidate's English name into the LLM and gets
        back the Saudi-Arabic transliteration ready for Saudi visa forms.
"""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.models import User
from app.services.arabic_translate import translate_to_arabic
from app.services.passport_ocr import _TESSERACT_OK, extract_passport_data

router = APIRouter()


def _detect_ocr_backends() -> dict:
    """Report which OCR backends are reachable at this exact moment.

    Returned dict shape:
        {
          "tesseract": bool,     # offline OCR available (system binary + pytesseract pkg)
          "ai_vision": bool,     # AI vision available (OpenAI key configured & reachable)
          "available_backends": ["tesseract", "ai_vision"]  # subset that is up
        }

    This is what the frontend uses to decide whether to show the
    "Scan Passport" button at all, or grey it out with an in-app hint.
    """
    tesseract_up = _TESSERACT_OK
    try:
        from app.services import passport_vision
        ai_vision_up = bool(passport_vision.available())
    except Exception:  # noqa: BLE001
        ai_vision_up = False
    available = []
    if ai_vision_up:
        available.append("ai_vision")
    if tesseract_up:
        available.append("tesseract")
    return {
        "tesseract": tesseract_up,
        "ai_vision": ai_vision_up,
        "available_backends": available,
    }


@router.get("/backends")
def get_ocr_backends(user: User = Depends(get_current_user)):
    """Report which passport-OCR backends are currently reachable.

    Used by the frontend "Scan Passport" step to render a helpful
    in-app hint when NO backend is up, instead of showing a silent
    upload failure. Also returned inline with every failed /passport
    call in the ``available_backends`` field.
    """
    info = _detect_ocr_backends()
    info["install_instructions"] = _install_hints()
    return info


def _install_hints() -> dict:
    """Human-readable pointers the frontend can render when no backend is up."""
    return {
        "tesseract": {
            "debian": "sudo apt install tesseract-ocr tesseract-ocr-eng",
            "nix": "add `pkgs.tesseract4` to your Nix packages",
            "macos": "brew install tesseract",
            "docs": "https://tesseract-ocr.github.io/tessdoc/Installation.html",
        },
        "ai_vision": {
            "env_var": "Set OPENAI_API_KEY (or the equivalent LLM proxy env "
                       "var per app/services/passport_vision.py) and restart.",
        },
    }


@router.post("/passport")
async def scan_passport(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """Upload a passport image (jpg / png / webp). Returns extracted fields
    ready for the Candidate wizard.

    Graceful-degradation contract (audit 2026-07-08):
      * If BOTH offline Tesseract and AI vision are unreachable we return
        HTTP 200 with ``ok=false``, ``reason='no_ocr_backend'``, and a
        ``user_message`` that the frontend renders as an inline hint.
      * We never raise 503 here -- that would give the SPA a silent axios
        failure with no in-app explanation, which is exactly the problem
        this endpoint was fixed for.
    """
    # Phone uploads frequently arrive as application/octet-stream or
    # image/heic (iPhone), so accept by content-type OR file extension and let
    # the decoder (Pillow + pillow-heif) be the real gatekeeper. (Problem 7)
    name = (file.filename or "").lower()
    ct = (file.content_type or "").lower()
    img_exts = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff")
    looks_image = ct.startswith("image/") or name.endswith(img_exts) or ct in (
        "application/octet-stream", "")
    if not looks_image:
        raise HTTPException(400, "Only image files are accepted (JPG, PNG, WEBP, HEIC)")

    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 25 MB)")
    if len(content) < 1024:
        raise HTTPException(400, "Image too small or empty")

    backends = _detect_ocr_backends()

    # Fast-path: NO backend at all. Return a helpful, structured 200 so the
    # frontend can display an inline hint (see PassportScanStep.tsx).
    if not backends["available_backends"]:
        return {
            "ok": False,
            "method": "unavailable",
            "reason": "no_ocr_backend",
            "available_backends": [],
            "install_instructions": _install_hints(),
            "user_message": (
                "Automatic passport scanning is unavailable on this server "
                "(offline Tesseract not installed and AI vision not "
                "configured). Please type the passport details into the "
                "form below -- the fields are ready for you."
            ),
            "fields": {},
            "raw_text": "",
        }

    result = extract_passport_data(content)

    # A backend was available but the specific image was unreadable / OCR
    # couldn't find MRZ. Return 200 + reason='unreadable_image' so the SPA
    # can prompt the user to reshoot / crop tighter instead of silently
    # failing.
    if not result.get("ok") and not result.get("fields"):
        return {
            "ok": False,
            "method": "failed",
            "reason": "unreadable_image",
            "available_backends": backends["available_backends"],
            "user_message": (
                result.get("error")
                or "We couldn't read the passport photo. Please crop tightly "
                   "to the bio-data page (with the two lines of << text at "
                   "the bottom) and try again, or type the fields by hand."
            ),
            "fields": {},
            "raw_text": result.get("raw_text", ""),
        }

    return {
        "ok": True,
        "method": result.get("method", "text"),
        "available_backends": backends["available_backends"],
        "fields": result.get("fields", {}),
        "raw_text": result.get("raw_text", ""),
    }


# ---------------------------------------------------------------------------
# v8 NEW: English → Saudi Arabic auto-translate for Saudi visa forms
# ---------------------------------------------------------------------------
class TranslateRequest(BaseModel):
    full_name: Optional[str] = ""
    father_name: Optional[str] = ""
    place_of_birth: Optional[str] = ""
    issuing_authority: Optional[str] = ""


@router.post("/translate-to-arabic")
async def translate_endpoint(payload: TranslateRequest, user: User = Depends(get_current_user)):
    """Translate / transliterate English candidate fields into Saudi Arabic
    so the Saudi visa application form (Arabic columns) can be auto-filled.

    Returns the same field names with their Arabic-script equivalents.
    Falls back to a deterministic transliteration table if the LLM is
    unreachable so the feature *always* works, even offline.
    """
    inputs: Dict[str, str] = {
        "full_name":         (payload.full_name or "").strip(),
        "father_name":       (payload.father_name or "").strip(),
        "place_of_birth":    (payload.place_of_birth or "").strip(),
        "issuing_authority": (payload.issuing_authority or "").strip(),
    }
    # Drop empty inputs so we don't waste tokens on blank translations
    inputs = {k: v for k, v in inputs.items() if v}
    if not inputs:
        return {"ok": False, "error": "Nothing to translate", "arabic": {}}

    arabic = translate_to_arabic(inputs)
    return {"ok": True, "arabic": arabic, "method": arabic.pop("_method", "llm")}
