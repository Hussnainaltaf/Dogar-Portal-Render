"""Strict image validation and normalization for same-origin uploads."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidImage(ValueError):
    """Raised when uploaded bytes are not a supported, safe image."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    extension: str
    media_type: str
    width: int
    height: int


_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}


def normalize_image(
    raw: bytes,
    *,
    allowed_formats: frozenset[str] = frozenset(_FORMATS),
    max_pixels: int = 40_000_000,
) -> NormalizedImage:
    """Decode and re-encode an image, stripping metadata and trailing payloads.

    SVG is deliberately unsupported because same-origin SVG can execute active
    content. Re-encoding also prevents image/polyglot files from retaining
    appended HTML or script data.
    """
    if not raw:
        raise InvalidImage("Image is empty")
    try:
        with Image.open(BytesIO(raw)) as source:
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            fmt = (source.format or "").upper()
            if fmt not in allowed_formats or fmt not in _FORMATS:
                raise InvalidImage("Unsupported image format")
            width, height = source.size
            if width < 1 or height < 1 or width * height > max_pixels:
                raise InvalidImage("Image dimensions are invalid or too large")

            image = ImageOps.exif_transpose(source)
            output = BytesIO()
            extension, media_type = _FORMATS[fmt]
            if fmt == "JPEG":
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=90, optimize=True)
            elif fmt == "PNG":
                image.save(output, format="PNG", optimize=True)
            elif fmt == "WEBP":
                image.save(output, format="WEBP", quality=90, method=4)
            else:
                # Preserve legitimate animated branding GIFs while Pillow
                # reconstructs the container rather than copying raw bytes.
                image.save(output, format="GIF", save_all=True)
    except (InvalidImage, Image.DecompressionBombError):
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("File is not a valid image") from exc

    return NormalizedImage(
        data=output.getvalue(),
        extension=extension,
        media_type=media_type,
        width=width,
        height=height,
    )
