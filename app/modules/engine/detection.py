"""Step 5: find location-relevant objects and cut crops.

The Detector interface (image + words in, boxes out) lets us swap the local model
for a cloud one later without touching the pipeline.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from PIL import Image

from app.core.config import get_settings
from app.modules.engine.payload import Box, DetectedObject

# What we ask the model to look for, grouped by what each tells us about the location.
# People and faces are left out on purpose (privacy). Pick groups with DETECT_GROUPS in .env.
LABEL_GROUPS: dict[str, list[str]] = {
    # Names, prices, addresses: the most valuable group (OCR zooms into these).
    "text": [
        "sign", "shop sign", "neon sign", "street name sign", "logo", "label", "poster",
        "banner", "billboard", "menu", "menu board", "chalkboard", "price list", "price tag",
        "receipt", "screen", "plaque", "sticker", "graffiti", "house number", "newspaper",
        "magazine", "book", "packaging", "business card",
    ],
    # Cuisine, local dishes and brands.
    "food": [
        "plate", "dish", "bowl", "cup", "mug", "glass", "wine glass", "beer glass", "bottle",
        "can", "teapot", "coffee pot", "tray", "cutlery", "chopsticks", "bread", "pizza",
        "burger", "sushi", "salad", "cake", "dessert", "fruit", "drink", "ice cream",
    ],
    # Small table details: restaurant tier and local habits.
    "table": [
        "napkin", "tablecloth", "salt shaker", "candle", "vase", "ashtray", "hookah", "coaster",
        "sugar packet", "straw", "takeaway cup", "paper bag", "food box", "placemat",
        "bill folder", "water jug", "olive oil bottle", "sauce bottle",
    ],
    # Decor and furniture: venue style, matches against candidate photos.
    "interior": [
        "window", "door", "arch", "lamp", "chandelier", "ceiling fan", "chair", "sofa",
        "bench", "table", "counter", "bar counter", "shelf", "mirror", "painting", "wall art",
        "photo frame", "tiles", "mosaic", "carpet", "rug", "curtain", "plant", "flower pot",
        "clock",
    ],
    # Type of venue.
    "venue": [
        "open kitchen", "coffee machine", "display fridge", "cash register", "pastry display",
        "wine rack", "bar stool", "stage", "fireplace", "awning", "parasol", "umbrella",
        "outdoor seating", "pergola", "terrace", "buffet", "aquarium",
    ],
    # Landmarks and building types.
    "buildings": [
        "building", "hotel", "skyscraper", "tower", "apartment block", "house", "villa",
        "mosque", "minaret", "church", "cathedral", "bell tower", "temple", "synagogue",
        "pagoda", "dome", "castle", "fortress", "palace", "ruins", "city wall", "city gate",
    ],
    # Architectural style: old town vs new district, regional styles.
    "architecture": [
        "balcony", "column", "arcade", "facade", "window shutters", "roof", "red tile roof",
        "flat roof", "chimney", "staircase", "stone wall", "brick wall", "wooden facade",
        "glass facade", "archway", "courtyard", "wrought iron railing", "mashrabiya",
    ],
    # Famous or rare structures and public places.
    "landmarks": [
        "bridge", "fountain", "statue", "monument", "obelisk", "clock tower", "lighthouse",
        "ferris wheel", "stadium", "harbor", "pier", "marina", "promenade", "public square",
        "park", "cable car", "billboard tower",
    ],
    # Street furniture: classic country clues.
    "street": [
        "street sign", "road sign", "stop sign", "traffic light", "street lamp", "bus stop",
        "crosswalk", "sidewalk", "curb", "bollard", "utility pole", "power lines",
        "fire hydrant", "mailbox", "phone booth", "trash bin", "parking meter",
        "road marking", "manhole cover", "bike rack", "kiosk", "market stall", "guard rail",
    ],
    # Shops and services.
    "commerce": [
        "shop", "storefront", "supermarket", "pharmacy sign", "atm", "gas station",
        "fuel pump", "vending machine", "shopping bag", "shopping cart", "market",
        "newsstand", "car rental sign", "taxi sign",
    ],
    # Rooftop gear differs a lot by country and climate.
    "rooftops": [
        "satellite dish", "solar water heater", "solar panel", "water tank", "rooftop antenna",
        "air conditioner", "rooftop terrace", "clothesline",
    ],
    # Plate style, taxi colors, transport type.
    "vehicles": [
        "car", "taxi", "bus", "double-decker bus", "tram", "train", "metro", "truck", "van",
        "pickup truck", "motorcycle", "scooter", "tuk-tuk", "bicycle", "boat", "ship", "ferry",
        "airplane", "license plate", "police car", "ambulance",
    ],
    # Terrain and water.
    "nature": [
        "mountain", "hill", "cliff", "sea", "beach", "river", "lake", "waterfall", "desert",
        "sand dune", "forest", "field", "snow", "rock", "island", "canal",
    ],
    # Plants: climate zone.
    "plants": [
        "palm tree", "pine tree", "cypress tree", "olive tree", "cactus", "bamboo",
        "banana tree", "birch tree", "tree", "bush", "flowers", "grass", "vineyard",
        "bougainvillea",
    ],
    # Animals that hint at region.
    "animals": [
        "dog", "cat", "camel", "horse", "donkey", "cow", "sheep", "goat", "pigeon", "seagull",
        "parrot", "monkey",
    ],
    # National and religious symbols.
    "symbols": [
        "flag", "coat of arms", "emblem", "map", "portrait", "cross", "crescent",
        "religious symbol", "calligraphy",
    ],
}  # fmt: skip

# label -> group (first group wins if a word appears twice)
LABEL_TO_GROUP: dict[str, str] = {}
for _group, _labels in LABEL_GROUPS.items():
    for _label in _labels:
        LABEL_TO_GROUP.setdefault(_label, _group)


def selected_labels() -> list[str]:
    """Labels for the groups in DETECT_GROUPS ("all" or e.g. "text,street,buildings")."""
    wanted = get_settings().detect_groups.strip().lower()
    groups = (
        list(LABEL_GROUPS)
        if wanted in ("", "all")
        else [g.strip() for g in wanted.split(",") if g.strip()]
    )
    unknown = [g for g in groups if g not in LABEL_GROUPS]
    if unknown:
        raise ValueError(f"Unknown DETECT_GROUPS {unknown}; choose from {list(LABEL_GROUPS)}")
    return list(dict.fromkeys(label for g in groups for label in LABEL_GROUPS[g]))


MODEL_ID = "IDEA-Research/grounding-dino-tiny"
# Confidence cutoffs and device come from settings (.env): DETECT_BOX_THRESHOLD, etc.
IOU_MERGE = 0.6  # same-label boxes overlapping more than this are duplicates
CROP_PADDING = 0.10  # grow each crop by 10% per side for context
MIN_CROP_SIDE = 24  # px on the full image; smaller boxes aren't worth a crop


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in the given image's pixels


class Detector(Protocol):
    def detect(self, image: Image.Image, labels: list[str]) -> list[Detection]: ...


class GroundingDinoDetector:
    """Local Grounding DINO via transformers. Loads once, runs on MPS (Mac GPU) or CPU."""

    def __init__(self, model_id: str = MODEL_ID) -> None:
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
            box=Box(
                x1=round(max(0.0, d.box[0] * sx), 1),
                y1=round(max(0.0, d.box[1] * sy), 1),
                x2=round(min(full.width, d.box[2] * sx), 1),
                y2=round(min(full.height, d.box[3] * sy), 1),
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
        pad_x, pad_y = w * CROP_PADDING, h * CROP_PADDING
        area = (
            int(max(0, b.x1 - pad_x)),
            int(max(0, b.y1 - pad_y)),
            int(min(full.width, b.x2 + pad_x)),
            int(min(full.height, b.y2 + pad_y)),
        )
        path = crops_dir / f"{i:02d}_{obj.label.replace(' ', '_')}.jpg"
        full.crop(area).save(path, quality=92)
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


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _merge_duplicates(detections: list[Detection]) -> list[Detection]:
    """Keep the highest-scoring box when same-label boxes overlap heavily."""
    kept: list[Detection] = []
    for d in sorted(detections, key=lambda d: d.score, reverse=True):
        if all(k.label != d.label or _iou(k.box, d.box) < IOU_MERGE for k in kept):
            kept.append(d)
    return kept
