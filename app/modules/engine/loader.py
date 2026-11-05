"""Step 1: load an image file into a validated, normalized LoadedImage.

The original bytes are kept untouched (EXIF stays intact for step 3).
All processing happens on in-memory copies.
"""

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
WORKING_MAX_SIDE = 1280
MAX_FILE_BYTES = 30 * 1024 * 1024  # 30 MB


class ImageLoadError(Exception):
    """Raised when a file can't be used as an input image."""


@dataclass(frozen=True)
class LoadedImage:
    path: Path
    format: str  # "JPEG" | "PNG" | "WEBP", detected from content, not extension
    width: int  # after rotation fix
    height: int
    sha256: str  # of the original bytes, to spot duplicates
    original_bytes: bytes  # untouched file content
    image: Image.Image  # full-size RGB copy, rotation fixed
    working: Image.Image  # smaller RGB copy for models (longest side <= WORKING_MAX_SIDE)


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path)
    if not path.is_file():
        raise ImageLoadError(f"File not found: {path}")

    data = path.read_bytes()
    if not data:
        raise ImageLoadError(f"File is empty: {path}")
    if len(data) > MAX_FILE_BYTES:
        raise ImageLoadError(f"File too large: {len(data)} bytes (max {MAX_FILE_BYTES})")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageLoadError(f"Not a readable image: {path}") from exc

    if img.format not in SUPPORTED_FORMATS:
        raise ImageLoadError(f"Unsupported format {img.format!r}; allowed: {SUPPORTED_FORMATS}")
    fmt = img.format

    # Phones store rotation as an EXIF flag; apply it so pixels are upright.
    rgb = ImageOps.exif_transpose(img).convert("RGB")

    working = rgb.copy()
    working.thumbnail((WORKING_MAX_SIDE, WORKING_MAX_SIDE), Image.Resampling.LANCZOS)

    return LoadedImage(
        path=path,
        format=fmt,
        width=rgb.width,
        height=rgb.height,
        sha256=hashlib.sha256(data).hexdigest(),
        original_bytes=data,
        image=rgb,
        working=working,
    )
