"""Step 5: find location-relevant objects and cut crops.

The Detector interface (image + words in, boxes out) lets us swap the local model
for a cloud one later without touching the pipeline.
"""

import os
from functools import lru_cache
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.modules.engine.constants import (
    DETECT_CROP_PADDING,
    DETECT_MODEL_ID,
    IOU_MERGE,
    LABEL_GROUPS,
    LABEL_TO_GROUP,
    MIN_CROP_SIDE,
)
from app.modules.engine.helpers import clamp_box, iou, padded_crop, slugify, split_csv
from app.modules.engine.schemas import DetectedObject, Detection, Detector


def selected_labels() -> list[str]:
    """Labels for the groups in DETECT_GROUPS ("all" or e.g. "text,street,buildings")."""
    wanted = get_settings().detect_groups.strip().lower()
    groups = list(LABEL_GROUPS) if wanted in ("", "all") else split_csv(wanted)
    unknown = [g for g in groups if g not in LABEL_GROUPS]
    if unknown:
        raise ValueError(f"Unknown DETECT_GROUPS {unknown}; choose from {list(LABEL_GROUPS)}")
    return list(dict.fromkeys(label for g in groups for label in LABEL_GROUPS[g]))


class GroundingDinoDetector:
    """Local Grounding DINO via transformers. Loads once, runs on MPS (Mac GPU) or CPU."""

    def __init__(self, model_id: str = DETECT_MODEL_ID) -> None:
        # Let unsupported MPS ops fall back to CPU instead of crashing. Must be set before torch.
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self._torch = torch
        self.settings = get_settings()
        wanted = self.settings.engine_device
        mps_ok = torch.backends.mps.is_available()
        self.device = "mps" if wanted == "mps" or (wanted == "auto" and mps_ok) else "cpu"
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id)
        self.model.to(self.device).eval()

    def detect(self, image: Image.Image, labels: list[str]) -> list[Detection]:
        prompt = " ".join(f"{label.lower()}." for label in labels)  # "plate. cup. sign."
        inputs = self.processor(images=image, text=prompt, return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            outputs = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            threshold=self.settings.detect_box_threshold,
            text_threshold=self.settings.detect_text_threshold,
            target_sizes=[(image.height, image.width)],
        )[0]
        scores, boxes = result["scores"], result["boxes"]
        if len(scores) == 0:
            # Nothing found for this batch. transformers still returns [""] as names here.
            return []
        names = result["text_labels"]  # "labels" is deprecated and warns
        return [
            Detection(label=_match_label(str(name), labels), score=float(score), box=_box(box))
            for name, score, box in zip(names, scores, boxes, strict=True)
            if str(name).strip()
        ]


@lru_cache(maxsize=1)
def get_default_detector() -> Detector:
    """Load the model once per process and reuse it."""
    return GroundingDinoDetector()


def detect_objects(
    full: Image.Image,
    working: Image.Image,
    detector: Detector,
    labels: list[str] | None = None,
) -> list[DetectedObject]:
    """Run on the small working copy, return boxes in full-size coordinates, best first.

    Labels are sent in batches (DETECT_BATCH_SIZE) because the model reads at most
    256 text tokens and gets less accurate with long prompts. One model run per batch.
    """
    labels = labels if labels is not None else selected_labels()
    size = max(1, get_settings().detect_batch_size)
    raw: list[Detection] = []
    for i in range(0, len(labels), size):
        raw.extend(detector.detect(working, labels[i : i + size]))

    sx, sy = full.width / working.width, full.height / working.height
    detections = _merge_duplicates(raw)
    objects = [
        DetectedObject(
            label=d.label,
            group=LABEL_TO_GROUP.get(d.label, "other"),
            confidence=round(min(max(d.score, 0.0), 1.0), 4),
            box=clamp_box(
                (d.box[0] * sx, d.box[1] * sy, d.box[2] * sx, d.box[3] * sy),
                full.width,
                full.height,
            ),
        )
        for d in detections
    ]
    return sorted(objects, key=lambda o: o.confidence, reverse=True)


def save_crops(full: Image.Image, objects: list[DetectedObject], crops_dir: Path) -> None:
    """Cut a padded crop per object from the full image and set crop_path. Tiny boxes skipped."""
    crops_dir.mkdir(parents=True, exist_ok=True)
    for i, obj in enumerate(objects, start=1):
        b = obj.box
        w, h = b.x2 - b.x1, b.y2 - b.y1
        if min(w, h) < MIN_CROP_SIDE:
            continue
        crop, _ = padded_crop(full, b, w * DETECT_CROP_PADDING, h * DETECT_CROP_PADDING)
        path = crops_dir / f"{i:02d}_{slugify(obj.label)}.jpg"
        crop.save(path, quality=92)
        obj.crop_path = path


def _match_label(name: str, labels: list[str]) -> str:
    """The model may return merged words ("palm tree tree"); map back to one of our labels."""
    name = name.strip().lower()
    if name in labels:
        return name
    for label in sorted(labels, key=len, reverse=True):  # prefer "palm tree" over "tree"
        if label in name:
            return label
    return name


def _box(box) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(v) for v in box)
    return x1, y1, x2, y2


def _merge_duplicates(detections: list[Detection]) -> list[Detection]:
    """Keep the highest-scoring box when same-label boxes overlap heavily."""
    kept: list[Detection] = []
    for d in sorted(detections, key=lambda d: d.score, reverse=True):
        if all(k.label != d.label or iou(k.box, d.box) < IOU_MERGE for k in kept):
            kept.append(d)
    return kept
