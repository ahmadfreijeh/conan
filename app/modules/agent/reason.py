"""Phase 2, step 3: the reasoning agent (Strands + Claude), no tools yet.

Takes the Phase 1 payload and the photo, links the clues and returns a ReasonResult:
ranked candidate places with evidence, plus search queries for Phase 3.
Phase 3 adds @tool functions to the same agent (tools=[...]); nothing else changes.
"""

import io
import json
import re
import shutil
from pathlib import Path

from PIL import Image
from strands import Agent
from strands.models.anthropic import AnthropicModel

from app.core.config import get_settings
from app.modules.agent.clues import build_clues
from app.modules.agent.crops import Crop, pick_crops
from app.modules.agent.result import ReasonResult
from app.modules.engine.loader import load_image
from app.modules.engine.schemas import ExtractionPayload

# Longest side of the photo sent to the model. Larger images get downscaled by the API anyway.
IMAGE_MAX_SIDE = 1568

SYSTEM_PROMPT = """\
You are a photo geolocation analyst. Given one photo and clues extracted from it, work out \
where the photo was most likely taken.

The clues come from automatic tools and are noisy:
- OCR text can have wrong letters (e.g. "DOCKTAILS" is likely "COCKTAILS"). Fix obvious \
misreadings and list them in corrected_text.
- Detected objects have a confidence score; treat anything under 0.4 as a weak hint and \
check it against the photo yourself.
- Metadata (EXIF) is strong evidence when present, but it is often stripped.
- After the full photo you may get close-up crops of detected objects, cut from the \
full-size image. Use them to read small signs, plates and logos, and to check OCR text. \
A crop's label comes from the detector and can be wrong; trust what you see in it.

How to work:
1. Look at the photo yourself. Note location clues the tools missed (architecture, signs, \
plates, road markings, plants, power lines, climate, language, brands) in seen_in_image.
2. Link the clues: languages, brands, business names, building style and objects together \
narrow the region. Say which clue supports which conclusion.
3. Give 1 to 5 ranked candidates, from country down to city and area when the evidence \
allows. Do not invent a city or street the evidence does not support; leave it empty instead.
4. Confidence must be honest: 0.9+ only with a decisive clue (GPS, a unique name, a famous \
landmark). Note clues that argue against each candidate.
5. Suggest search_queries that would confirm or rule out the candidates: business names, \
street or square names, landmarks. You cannot run them yourself yet.
"""


def _image_bytes(image: Image.Image) -> bytes:
    copy = image.copy()
    copy.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def build_agent() -> Agent:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    model = AnthropicModel(
        client_args={"api_key": settings.anthropic_api_key},
        model_id=settings.reason_model,
        max_tokens=settings.reason_max_tokens,
    )
    # No tools yet (Phase 2). Phase 3 passes tools=[...] here.
    return Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None)


def _save_inputs(out_dir: Path, clues_json: str, crops: list[Crop]) -> None:
    """Save exactly what the LLM gets: clues.json and sent/ (crops + sent/crops.json)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clues.json").write_text(clues_json)
    sent = out_dir / "sent"
    shutil.rmtree(sent, ignore_errors=True)  # a rerun replaces the old crops
    sent.mkdir()
    index = []
    for i, crop in enumerate(crops, 1):
        name = f"{i}_{re.sub(r'[^a-z0-9]+', '_', crop.label.lower()).strip('_')}.jpg"
        (sent / name).write_bytes(_image_bytes(crop.image))
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


def reason(
    payload: ExtractionPayload, agent: Agent | None = None, out_dir: str | Path | None = None
) -> ReasonResult:
    """Send the clues and the photo to the agent and return its structured answer.

    If out_dir is given, first save what is sent (clues.json, sent/) so it can be checked.
    """
    agent = agent or build_agent()
    clues = build_clues(payload)
    loaded = load_image(payload.image.path)

    content: list[dict] = [
        {"text": "Clues extracted from the photo (JSON):\n" + clues.model_dump_json()},
        {"text": "Full photo:"},
        {"image": {"format": "jpeg", "source": {"bytes": _image_bytes(loaded.image)}}},
    ]
    crops = pick_crops(loaded.image, payload.objects, get_settings().reason_max_crops)
    if out_dir is not None:
        _save_inputs(Path(out_dir), clues.model_dump_json(indent=2), crops)
    for i, crop in enumerate(crops, 1):
        content.append(
            {
                "text": f"Crop {i}: {crop.label} ({crop.group}, confidence {crop.confidence}), "
                f"{crop.where} of the photo"
            }
        )
        content.append({"image": {"format": "jpeg", "source": {"bytes": _image_bytes(crop.image)}}})
    content.append({"text": "Where was this photo taken?"})
    result = agent(content, structured_output_model=ReasonResult)
    if not isinstance(result.structured_output, ReasonResult):
        raise RuntimeError("The model did not return a structured result")
    return result.structured_output
