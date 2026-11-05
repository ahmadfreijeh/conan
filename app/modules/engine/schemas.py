"""Every data shape the engine uses, in one place.

- Payload models: the Phase 1 output (payload.json) that Phase 2 (agent) reads.
  It holds data and file paths only, never pixels or raw bytes.
- Internal types: what the steps pass to each other (loaded image, raw detections,
  raw OCR lines) and the model interfaces (Detector, TextReader) so models can be swapped.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from PIL import Image
from pydantic import BaseModel, Field

from app.modules.engine.constants import SCHEMA_VERSION

BoxTuple = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels


# --- Payload (payload.json) ---------------------------------------------------------


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
    # peak memory, machine, library versions, settings. See app/modules/engine/runinfo.py.
    run: dict[str, Any] = {}


# --- Step 1: loader -----------------------------------------------------------------


@dataclass(frozen=True)
class LoadedImage:
    path: Path
    format: str  # "JPEG" | "PNG" | "WEBP", detected from content, not extension
    width: int  # after rotation fix
    height: int
    sha256: str  # of the original bytes, to spot duplicates
    original_bytes: bytes  # untouched file content
    image: Image.Image  # full-size RGB copy, rotation fixed
    working: Image.Image  # smaller RGB copy for models (longest side <= WORKING_MAX_SIDE)


# --- Step 5: detection --------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: BoxTuple  # in the given image's pixels


class Detector(Protocol):
    def detect(self, image: Image.Image, labels: list[str]) -> list[Detection]: ...


# --- Step 6: OCR --------------------------------------------------------------------


@dataclass(frozen=True)
class TextLine:
    text: str
    score: float
    box: BoxTuple  # in the given image's pixels


class TextReader(Protocol):
    def read(self, image: Image.Image) -> list[TextLine]: ...


# --- Agent input (prepare) ----------------------------------------------------------


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
    """The payload cut down to what the LLM needs."""

    metadata: dict  # only the fields that have a value
    objects: list[ObjectClue]
    text: list[TextClue]


@dataclass
class Crop:
    """A close-up of one object, cut from the full-size image."""

    label: str
    group: str
    confidence: float
    where: str  # rough position in the photo, e.g. "top right"
    image: Image.Image


@dataclass
class AgentInput:
    """Everything the agent gets: clues, the full photo and the close-up crops (in memory)."""

    clues: Clues
    photo: Image.Image
    crops: list[Crop]
