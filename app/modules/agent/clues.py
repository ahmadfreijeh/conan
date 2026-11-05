"""Phase 2, step 1: turn the Phase 1 payload into a short clue list for the LLM.

Drops what the LLM does not need (run info, pixel boxes, file paths), merges repeated
objects and repeated text, and keeps confidence so weak clues stay marked as weak.

Try it on a saved payload (no models run):
    uv run python -m app.modules.agent.clues out/photo/payload.json
"""

import sys
from pathlib import Path

from pydantic import BaseModel

from app.modules.engine.payload import ExtractionPayload


class ObjectClue(BaseModel):
    label: str
    group: str
    count: int
    confidence: float  # best score among the repeats


class TextClue(BaseModel):
    text: str
    language: str | None
    confidence: float
    where: str  # "image" or the object it was read from, e.g. "sign"


class Clues(BaseModel):
    metadata: dict  # only the fields that have a value
    objects: list[ObjectClue]
    text: list[TextClue]


def _where(source: str) -> str:
    # OCR source is "image" or "object:<index>_<label>", e.g. "object:33_sign"
    if source.startswith("object:"):
        return source.split("_", 1)[-1]
    return source


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
            where=_where(item.source),
        )
        if key not in texts or clue.confidence > texts[key].confidence:
            texts[key] = clue

    return Clues(
        metadata=metadata,
        objects=sorted(objects.values(), key=lambda c: c.confidence, reverse=True),
        text=sorted(texts.values(), key=lambda c: c.confidence, reverse=True),
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.modules.agent.clues <payload.json>")
    saved = ExtractionPayload.model_validate_json(Path(sys.argv[1]).read_text())
    clues = build_clues(saved)
    out = clues.model_dump_json(indent=2)
    print(out)
    print(f"{len(out)} chars (was {len(saved.model_dump_json(exclude={'run'}))})", file=sys.stderr)
