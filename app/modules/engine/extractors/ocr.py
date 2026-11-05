"""Step 6: read text (Arabic + English) from the full image and from text-bearing crops.

The TextReader interface (image in, text lines out) lets us swap PaddleOCR for
another engine later without touching the pipeline.
"""

from functools import lru_cache
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.modules.engine.constants import (
    ARABIC,
    EXTRA_TEXT_LABELS,
    IOU_SAME_LINE,
    LATIN,
    OCR_CROP_MIN_SIDE,
    OCR_CROP_PADDING,
    TEXT_CROP_MIN_HEIGHT,
    TEXT_CROP_PADDING,
    TEXT_GROUP,
)
from app.modules.engine.helpers import (
    clamp_box,
    iou,
    padded_crop,
    scale_image,
    slugify,
    split_csv,
)
from app.modules.engine.schemas import Box, DetectedObject, TextItem, TextLine, TextReader


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
        rec_models=split_csv(settings.ocr_rec_models),
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
        if all(iou(line.box, b.box) < IOU_SAME_LINE for b, _ in best):
            best.append((line, source))

    return [
        TextItem(
            text=line.text.strip(),
            language=detect_language(line.text),
            confidence=round(min(line.score, 1.0), 4),
            box=clamp_box(line.box, full.width, full.height),
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
        crop, _ = padded_crop(full, b, pad, pad)
        if 0 < crop.height < TEXT_CROP_MIN_HEIGHT:
            crop = scale_image(crop, TEXT_CROP_MIN_HEIGHT / crop.height)
        slug = slugify(item.text)  # Arabic -> number only
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
    crop, (x1, y1) = padded_crop(full, box, w * OCR_CROP_PADDING, h * OCR_CROP_PADDING)
    scale = 1.0
    if min(crop.size) < OCR_CROP_MIN_SIDE:
        scale = OCR_CROP_MIN_SIDE / max(1, min(crop.size))
        crop = scale_image(crop, scale)
    return crop, (float(x1), float(y1)), scale
