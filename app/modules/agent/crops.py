"""Phase 2: pick the most useful object crops to send to the LLM with the photo.

The full photo is downscaled before the LLM sees it, so small background signs, plates or
buildings become unreadable. Crops cut from the full-size image keep that detail.
"""

from dataclasses import dataclass

from PIL import Image

from app.modules.engine.payload import Box, DetectedObject

# Groups most likely to hold a location clue, best first. Others (food, interior, plants...)
# are visible enough in the full photo.
GROUP_PRIORITY = [
    "text",
    "street",
    "commerce",
    "landmarks",
    "vehicles",
    "buildings",
    "architecture",
    "rooftops",
    "symbols",
]
MIN_SIDE = 24  # skip crops smaller than this (pixels); nothing readable inside
PAD = 0.15  # add 15% around the box so the context is visible
MAX_OVERLAP = 0.5  # skip a box that mostly overlaps one already picked
MIN_SEND_SIDE = 512  # enlarge smaller crops to this (longest side); adds no detail, reads better


@dataclass
class Crop:
    label: str
    group: str
    confidence: float
    where: str  # rough position in the photo, e.g. "top right"
    image: Image.Image


def _area(b: Box) -> float:
    return max(0.0, b.x2 - b.x1) * max(0.0, b.y2 - b.y1)


def _overlap(a: Box, b: Box) -> float:
    """Intersection over the smaller box, so a box inside another counts as a repeat."""
    w = min(a.x2, b.x2) - max(a.x1, b.x1)
    h = min(a.y2, b.y2) - max(a.y1, b.y1)
    if w <= 0 or h <= 0:
        return 0.0
    return (w * h) / max(1.0, min(_area(a), _area(b)))


def _where(b: Box, width: int, height: int) -> str:
    cx, cy = (b.x1 + b.x2) / 2 / width, (b.y1 + b.y2) / 2 / height
    row = "top" if cy < 1 / 3 else "bottom" if cy > 2 / 3 else "middle"
    col = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "center"
    return "center" if (row, col) == ("middle", "center") else f"{row} {col}"


def _cut(image: Image.Image, b: Box) -> Image.Image:
    pw, ph = (b.x2 - b.x1) * PAD, (b.y2 - b.y1) * PAD
    crop = image.crop(
        (
            max(0, int(b.x1 - pw)),
            max(0, int(b.y1 - ph)),
            min(image.width, int(b.x2 + pw)),
            min(image.height, int(b.y2 + ph)),
        )
    )
    longest = max(crop.size)
    if longest < MIN_SEND_SIDE:
        scale = MIN_SEND_SIDE / longest
        crop = crop.resize(
            (round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS
        )
    return crop


def pick_crops(image: Image.Image, objects: list[DetectedObject], limit: int) -> list[Crop]:
    """Best `limit` crops: useful groups first, then confidence; no tiny boxes, no repeats."""
    rank = {g: i for i, g in enumerate(GROUP_PRIORITY)}
    candidates = sorted(
        (o for o in objects if o.group in rank),
        key=lambda o: (rank[o.group], -o.confidence),
    )
    picked: list[DetectedObject] = []
    for obj in candidates:
        b = obj.box
        if min(b.x2 - b.x1, b.y2 - b.y1) < MIN_SIDE:
            continue
        if any(_overlap(b, p.box) > MAX_OVERLAP for p in picked):
            continue
        picked.append(obj)
        if len(picked) == limit:
            break

    return [
        Crop(
            label=o.label,
            group=o.group,
            confidence=round(o.confidence, 2),
            where=_where(o.box, image.width, image.height),
            image=_cut(image, o.box),
        )
        for o in picked
    ]
