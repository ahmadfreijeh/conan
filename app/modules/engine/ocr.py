"""Step 6: read text (Arabic + English) from the full image and from text-bearing crops.

The TextReader interface (image in, text lines out) lets us swap PaddleOCR for
another engine later without touching the pipeline.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from PIL import Image

from app.core.config import get_settings
from app.modules.engine.payload import Box, DetectedObject, TextItem

# Models, cutoffs and recognizers come from settings (.env): OCR_DET_MODEL, OCR_REC_MODELS, ...
IOU_SAME_LINE = 0.5  # boxes overlapping more than this are the same text line
# Detected objects worth a closer OCR pass (text is often small inside them):
# everything in the "text" label group, plus these from other groups.
TEXT_GROUP = "text"
EXTRA_TEXT_LABELS = {
    "license plate", "street sign", "pharmacy sign", "car rental sign", "taxi sign",
}  # fmt: skip
CROP_MIN_SIDE = 64  # upscale smaller crops so tiny text is readable
CROP_PADDING = 0.10
TEXT_CROP_PADDING = 0.25  # padding around saved text crops, as a share of the line height
TEXT_CROP_MIN_HEIGHT = 48  # enlarge tiny text lines so they're readable when you open them

ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class TextLine:
    text: str
    score: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in the given image's pixels


class TextReader(Protocol):
    def read(self, image: Image.Image) -> list[TextLine]: ...


class PaddleTextReader:
    """PaddleOCR split in two: one text finder, then every recognizer reads the same lines.

    Finding lines once (instead of once per language) saves time; each line keeps the
    reading with the highest confidence. Runs on CPU on Mac.
    """

    def __init__(self, det_model: str, rec_models: list[str]) -> None:
        from paddleocr import TextDetection, TextRecognition

        self.detector = TextDetection(model_name=det_model)
        self.recognizers = [TextRecognition(model_name=name) for name in rec_models]

    def read(self, image: Image.Image) -> list[TextLine]:
        import numpy as np

        bgr = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()  # Paddle expects BGR
        h, w = bgr.shape[:2]

        # 1) Find text lines once.
        boxes: list[tuple[float, float, float, float]] = []
        for res in self.detector.predict(bgr):
            for poly in res["dt_polys"]:
                xs, ys = [float(p[0]) for p in poly], [float(p[1]) for p in poly]
                x1, y1 = max(0.0, min(xs)), max(0.0, min(ys))
                x2, y2 = min(float(w), max(xs)), min(float(h), max(ys))
                if x2 - x1 >= 2 and y2 - y1 >= 2:
                    boxes.append((x1, y1, x2, y2))
        if not boxes:
            return []
        line_images = [bgr[int(y1) : int(y2), int(x1) : int(x2)] for x1, y1, x2, y2 in boxes]

        # 2) Every recognizer reads all lines; keep the best reading per line.
        best: list[tuple[str, float]] = [("", 0.0)] * len(boxes)
        for recognizer in self.recognizers:
            for i, res in enumerate(recognizer.predict(line_images)):
                text, score = str(res["rec_text"]), float(res["rec_score"])
                if score > best[i][1]:
                    best[i] = (text, score)

        return [TextLine(text, score, box) for (text, score), box in zip(best, boxes, strict=True)]


@lru_cache(maxsize=1)
def get_default_reader() -> TextReader:
    """Load the OCR models once per process and reuse them."""
    settings = get_settings()
    return PaddleTextReader(
        det_model=f"PP-OCRv5_{settings.ocr_det_model}_det",
        rec_models=[m.strip() for m in settings.ocr_rec_models.split(",") if m.strip()],
    )


def read_text(
    full: Image.Image, objects: list[DetectedObject], reader: TextReader
) -> list[TextItem]:
    """OCR the full image, then each text-bearing object crop. Returns full-image coordinates."""
    found: list[tuple[TextLine, str]] = [(line, "image") for line in reader.read(full)]

    for i, obj in enumerate(objects, start=1):
        if obj.group != TEXT_GROUP and obj.label not in EXTRA_TEXT_LABELS:
            continue
        crop, (ox, oy), scale = _crop_for_ocr(full, obj.box)
        for line in reader.read(crop):
            x1, y1, x2, y2 = line.box
            box = (ox + x1 / scale, oy + y1 / scale, ox + x2 / scale, oy + y2 / scale)
            found.append((TextLine(line.text, line.score, box), f"object:{i:02d}_{obj.label}"))

    # Same text seen in the full image and in a crop: keep the more confident reading.
    min_score = get_settings().ocr_min_score
    best: list[tuple[TextLine, str]] = []
    for line, source in sorted(found, key=lambda f: f[0].score, reverse=True):
        if line.score < min_score or not line.text.strip():
            continue
        if all(_iou(line.box, b.box) < IOU_SAME_LINE for b, _ in best):
            best.append((line, source))

    return [
        TextItem(
            text=line.text.strip(),
            language=detect_language(line.text),
            confidence=round(min(line.score, 1.0), 4),
            box=Box(
                x1=round(max(0.0, line.box[0]), 1),
                y1=round(max(0.0, line.box[1]), 1),
                x2=round(min(full.width, line.box[2]), 1),
                y2=round(min(full.height, line.box[3]), 1),
            ),
            source=source,
        )
        for line, source in sorted(best, key=lambda f: (f[0].box[1], f[0].box[0]))  # reading order
    ]


def save_text_crops(full: Image.Image, items: list[TextItem], text_dir: Path) -> None:
    """Save an image of each text line (padded, enlarged if tiny) and set crop_path."""
    text_dir.mkdir(parents=True, exist_ok=True)
    for i, item in enumerate(items, start=1):
        b = item.box
        pad = (b.y2 - b.y1) * TEXT_CROP_PADDING
        crop = full.crop(
            (
                int(max(0, b.x1 - pad)),
                int(max(0, b.y1 - pad)),
                int(min(full.width, b.x2 + pad)),
                int(min(full.height, b.y2 + pad)),
            )
        )
        if 0 < crop.height < TEXT_CROP_MIN_HEIGHT:
            scale = TEXT_CROP_MIN_HEIGHT / crop.height
            crop = crop.resize(
                (round(crop.width * scale), TEXT_CROP_MIN_HEIGHT), Image.Resampling.LANCZOS
            )
        slug = re.sub(r"[^A-Za-z0-9-]+", "_", item.text).strip("_")[:40]  # Arabic -> number only
        path = text_dir / (f"{i:02d}_{slug}.jpg" if slug else f"{i:02d}.jpg")
        crop.save(path, quality=92)
        item.crop_path = path


def detect_language(text: str) -> str | None:
    """Tag by script: Arabic -> ar, Latin -> en, both -> mixed, digits only -> None."""
    has_ar, has_en = bool(ARABIC.search(text)), bool(LATIN.search(text))
    if has_ar and has_en:
        return "mixed"
    return "ar" if has_ar else "en" if has_en else None


def _crop_for_ocr(full: Image.Image, box: Box) -> tuple[Image.Image, tuple[float, float], float]:
    """Padded crop, upscaled if small. Returns crop, its top-left on the full image, and scale."""
    w, h = box.x2 - box.x1, box.y2 - box.y1
    x1 = int(max(0, box.x1 - w * CROP_PADDING))
    y1 = int(max(0, box.y1 - h * CROP_PADDING))
    x2 = int(min(full.width, box.x2 + w * CROP_PADDING))
    y2 = int(min(full.height, box.y2 + h * CROP_PADDING))
    crop = full.crop((x1, y1, x2, y2))
    scale = 1.0
    if min(crop.size) < CROP_MIN_SIDE:
        scale = CROP_MIN_SIDE / max(1, min(crop.size))
        crop = crop.resize(
            (round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS
        )
    return crop, (float(x1), float(y1)), scale


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
