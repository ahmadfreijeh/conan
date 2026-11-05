"""Step 5: prepare the agent input from a payload: clues, the full photo and close-up crops.

The agent only reasons over what it gets here; it does not load images or read the payload.

- Clues: drop what the LLM does not need (run info, pixel boxes, file paths), merge repeated
  objects and text, keep confidence so weak clues stay marked as weak.
- Crops: the full photo is downscaled before the LLM sees it, so small signs, plates or
  buildings become unreadable. Crops cut from the full-size image keep that detail.

Try it on a saved payload (no models run):
    uv run python -m app.modules.engine.prepare out/photo/payload.json
"""

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.modules.engine.constants import (
    AGENT_CROP_MAX_OVERLAP,
    AGENT_CROP_MIN_SEND_SIDE,
    AGENT_CROP_MIN_SIDE,
    AGENT_CROP_PADDING,
    CROP_GROUP_PRIORITY,
)
from app.modules.engine.extractors.loader import load_image
from app.modules.engine.helpers import (
    box_overlap,
    box_position,
    ocr_source_name,
    padded_crop,
    scale_image,
    slugify,
)
from app.modules.engine.schemas import (
    AgentInput,
    Box,
    Clues,
    Crop,
    DetectedObject,
    ExtractionPayload,
    ObjectClue,
    TextClue,
)

# --- Clues --------------------------------------------------------------------------


def build_clues(payload: ExtractionPayload) -> Clues:
    meta = payload.metadata
    metadata = meta.model_dump(mode="json", exclude_none=True) if meta else {}

    objects: dict[str, ObjectClue] = {}
    for obj in payload.objects:
        clue = objects.get(obj.label)
        if clue is None:
            objects[obj.label] = ObjectClue(
                label=obj.label, group=obj.group, count=1, confidence=round(obj.confidence, 2)
            )
        else:
            clue.count += 1
            clue.confidence = max(clue.confidence, round(obj.confidence, 2))

    texts: dict[str, TextClue] = {}
    for item in payload.text:
        key = item.text.strip().casefold()
        if not key:
            continue
        clue = TextClue(
            text=item.text.strip(),
            language=item.language,
            confidence=round(item.confidence, 2),
            where=ocr_source_name(item.source),
        )
        if key not in texts or clue.confidence > texts[key].confidence:
            texts[key] = clue

    return Clues(
        metadata=metadata,
        objects=sorted(objects.values(), key=lambda c: c.confidence, reverse=True),
        text=sorted(texts.values(), key=lambda c: c.confidence, reverse=True),
    )


# --- Crops --------------------------------------------------------------------------


def _cut(image: Image.Image, b: Box) -> Image.Image:
    """Cut a padded box from the full-size image; enlarge it if it is small."""
    pad_x, pad_y = (b.x2 - b.x1) * AGENT_CROP_PADDING, (b.y2 - b.y1) * AGENT_CROP_PADDING
    crop, _ = padded_crop(image, b, pad_x, pad_y)
    longest = max(crop.size)
    if longest < AGENT_CROP_MIN_SEND_SIDE:
        crop = scale_image(crop, AGENT_CROP_MIN_SEND_SIDE / longest)
    return crop


def pick_crops(image: Image.Image, objects: list[DetectedObject], limit: int) -> list[Crop]:
    """Best `limit` crops: useful groups first, then confidence; no tiny boxes, no repeats."""
    rank = {g: i for i, g in enumerate(CROP_GROUP_PRIORITY)}
    candidates = sorted(
        (o for o in objects if o.group in rank),
        key=lambda o: (rank[o.group], -o.confidence),
    )
    picked: list[DetectedObject] = []
    for obj in candidates:
        b = obj.box
        if min(b.x2 - b.x1, b.y2 - b.y1) < AGENT_CROP_MIN_SIDE:
            continue
        if any(box_overlap(b, p.box) > AGENT_CROP_MAX_OVERLAP for p in picked):
            continue
        picked.append(obj)
        if len(picked) == limit:
            break

    return [
        Crop(
            label=o.label,
            group=o.group,
            confidence=round(o.confidence, 2),
            where=box_position(o.box, image.width, image.height),
            image=_cut(image, o.box),
        )
        for o in picked
    ]


# --- Agent input --------------------------------------------------------------------


def prepare_for_agent(payload: ExtractionPayload, max_crops: int | None = None) -> AgentInput:
    """Load the photo again (full size) and build the clues and crops the agent gets."""
    limit = get_settings().reason_max_crops if max_crops is None else max_crops
    photo = load_image(payload.image.path).image
    return AgentInput(
        clues=build_clues(payload),
        photo=photo,
        crops=pick_crops(photo, payload.objects, limit),
    )


def save_agent_input(agent_input: AgentInput, out_dir: str | Path) -> None:
    """Save what the agent gets: clues.json and sent/ (crops + sent/crops.json)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clues.json").write_text(agent_input.clues.model_dump_json(indent=2))
    sent = out_dir / "sent"
    shutil.rmtree(sent, ignore_errors=True)  # a rerun replaces the old crops
    sent.mkdir()
    index = []
    for i, crop in enumerate(agent_input.crops, 1):
        name = f"{i}_{slugify(crop.label).lower().replace('-', '_')}.jpg"
        crop.image.convert("RGB").save(sent / name, quality=90)
        index.append(
            {
                "file": name,
                "label": crop.label,
                "group": crop.group,
                "confidence": crop.confidence,
                "where": crop.where,
            }
        )
    (sent / "crops.json").write_text(json.dumps(index, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.modules.engine.prepare <payload.json>")
    saved = ExtractionPayload.model_validate_json(Path(sys.argv[1]).read_text())
    out = build_clues(saved).model_dump_json(indent=2)
    print(out)
    print(f"{len(out)} chars (was {len(saved.model_dump_json(exclude={'run'}))})", file=sys.stderr)
