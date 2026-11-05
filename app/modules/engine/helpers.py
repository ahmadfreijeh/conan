"""Small helpers shared by the engine steps."""

import re
import time
from collections.abc import Generator
from contextlib import contextmanager

from PIL import Image

from app.modules.engine.schemas import Box, BoxTuple

# --- Boxes and crops ----------------------------------------------------------------


def iou(a: BoxTuple, b: BoxTuple) -> float:
    """Overlap of two boxes: 0 = apart, 1 = identical."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def clamp_box(box: BoxTuple, width: float, height: float) -> Box:
    """Keep a box inside the image and round it to 0.1 px."""
    x1, y1, x2, y2 = box
    return Box(
        x1=round(max(0.0, x1), 1),
        y1=round(max(0.0, y1), 1),
        x2=round(min(width, x2), 1),
        y2=round(min(height, y2), 1),
    )


def box_area(b: Box) -> float:
    return max(0.0, b.x2 - b.x1) * max(0.0, b.y2 - b.y1)


def box_overlap(a: Box, b: Box) -> float:
    """Intersection over the smaller box, so a box inside another counts as a repeat."""
    w = min(a.x2, b.x2) - max(a.x1, b.x1)
    h = min(a.y2, b.y2) - max(a.y1, b.y1)
    if w <= 0 or h <= 0:
        return 0.0
    return (w * h) / max(1.0, min(box_area(a), box_area(b)))


def box_position(b: Box, width: int, height: int) -> str:
    """Rough position of a box in the photo, e.g. "top right" or "center"."""
    cx, cy = (b.x1 + b.x2) / 2 / width, (b.y1 + b.y2) / 2 / height
    row = "top" if cy < 1 / 3 else "bottom" if cy > 2 / 3 else "middle"
    col = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "center"
    return "center" if (row, col) == ("middle", "center") else f"{row} {col}"


def padded_crop(
    image: Image.Image, box: Box, pad_x: float, pad_y: float
) -> tuple[Image.Image, tuple[int, int]]:
    """Cut the box plus padding (kept inside the image). Returns the crop and its top-left."""
    x1 = int(max(0, box.x1 - pad_x))
    y1 = int(max(0, box.y1 - pad_y))
    x2 = int(min(image.width, box.x2 + pad_x))
    y2 = int(min(image.height, box.y2 + pad_y))
    return image.crop((x1, y1, x2, y2)), (x1, y1)


def scale_image(image: Image.Image, scale: float) -> Image.Image:
    """Resize by a factor (high-quality resampling)."""
    size = (round(image.width * scale), round(image.height * scale))
    return image.resize(size, Image.Resampling.LANCZOS)


# --- Text ---------------------------------------------------------------------------


def split_csv(value: str) -> list[str]:
    """ "a, b,,c" -> ["a", "b", "c"]."""
    return [part.strip() for part in value.split(",") if part.strip()]


def ocr_source_name(source: str) -> str:
    """OCR source is "image" or "object:<index>_<label>", e.g. "object:33_sign" -> "sign"."""
    if source.startswith("object:"):
        return source.split("_", 1)[-1]
    return source


def slugify(text: str, max_len: int = 40) -> str:
    """Safe file name part: Latin letters, digits and dashes; the rest becomes "_"."""
    return re.sub(r"[^A-Za-z0-9-]+", "_", text).strip("_")[:max_len]


def to_text(value) -> str | None:
    """EXIF value (str/bytes/other) -> clean text, or None if empty."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    value = str(value).strip("\x00 ").strip()
    return value or None


def to_float(value) -> float | None:
    """Number-like value -> float, or None if it can't be read."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# --- Timing -------------------------------------------------------------------------


@contextmanager
def timed(timings: dict[str, float], step: str) -> Generator[None, None, None]:
    """Record how long the block took, in ms, under timings[step]."""
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[step] = round((time.perf_counter() - start) * 1000, 2)
