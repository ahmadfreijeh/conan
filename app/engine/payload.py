"""Step 2: the Phase 1 output payload.

This is the contract Phase 2 (Reason) reads. It holds data and file paths only,
never pixels or raw bytes. Each Phase 1 step fills its own section.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.engine.loader import LoadedImage

SCHEMA_VERSION = "1"


class ImageInfo(BaseModel):
    path: Path
    format: str
    width: int
    height: int
    sha256: str


class Box(BaseModel):
    """Pixel box on the full-size image: top-left (x1, y1), bottom-right (x2, y2)."""

    x1: float
    y1: float
    x2: float
    y2: float


class GPS(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    altitude_m: float | None = None
    direction_deg: float | None = None  # which way the camera faced, 0 = north


class Metadata(BaseModel):
    """Filled in Step 3 (EXIF + file name)."""

    has_exif: bool = False
    likely_stripped: bool = True  # no EXIF or passed through an app that strips it
    source_guess: str = "unknown"  # whatsapp, screenshot, facebook, telegram, camera, unknown
    gps: GPS | None = None
    taken_at: datetime | None = None  # timezone-aware if the camera saved an offset
    device_make: str | None = None
    device_model: str | None = None
    lens: str | None = None
    software: str | None = None
    focal_length_mm: float | None = None
    flash_fired: bool | None = None


class DetectedObject(BaseModel):
    """Filled in Step 5 (object detection)."""

    label: str
    group: str = "other"  # label group, e.g. "text", "buildings", "street"
    confidence: float = Field(ge=0, le=1)
    box: Box
    crop_path: Path | None = None


class TextItem(BaseModel):
    """Filled in Step 6 (OCR)."""

    text: str
    language: str | None = None
    confidence: float = Field(ge=0, le=1)
    box: Box
    source: str = "image"  # "image" or the crop it was read from
    crop_path: Path | None = None  # image of just this text, saved when --out is used


class ExtractionPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    image: ImageInfo
    metadata: Metadata | None = None
    objects: list[DetectedObject] = []
    text: list[TextItem] = []
    # Performance + environment for this run: total time, per-step timings, CPU, GPU,
    # peak memory, machine, library versions, settings. See app/engine/runinfo.py.
    run: dict[str, Any] = {}


def new_payload(loaded: LoadedImage) -> ExtractionPayload:
    """Start a payload from a loaded image; later steps fill the other sections."""
    return ExtractionPayload(
        image=ImageInfo(
            path=loaded.path,
            format=loaded.format,
            width=loaded.width,
            height=loaded.height,
            sha256=loaded.sha256,
        )
    )
