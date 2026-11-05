"""Step 4: run every Phase 1 step on one image and return the payload.

Kept separate from the CLI so the API and the worker can call it later.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.config import get_settings
from app.engine.detection import Detector, detect_objects, get_default_detector, save_crops
from app.engine.loader import load_image
from app.engine.metadata import extract_metadata
from app.engine.ocr import TextReader, get_default_reader, read_text, save_text_crops
from app.engine.payload import ExtractionPayload, new_payload
from app.engine.runinfo import RunMeter, append_history


@contextmanager
def _timed(timings: dict[str, float], step: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[step] = round((time.perf_counter() - start) * 1000, 2)


def run_pipeline(
    path: str | Path,
    out_dir: str | Path | None = None,
    *,
    detect: bool = True,
    detector: Detector | None = None,
    ocr: bool = True,
    reader: TextReader | None = None,
) -> ExtractionPayload:
    """Run all steps. If out_dir is given, write payload.json, crops/ and text/ there.

    detect=False / ocr=False skip those steps (fast, no model).
    detector / reader override the default models (used by tests).
    """
    meter = RunMeter()
    timings: dict[str, float] = {}
    out = Path(out_dir) if out_dir is not None else None

    with _timed(timings, "load"):
        loaded = load_image(path)
    payload = new_payload(loaded)

    with _timed(timings, "metadata"):
        payload.metadata = extract_metadata(loaded)

    if detect:
        if detector is None:
            with _timed(timings, "detector_load"):  # only slow on the first photo per process
                detector = get_default_detector()
        with _timed(timings, "objects"):
            payload.objects = detect_objects(loaded.image, loaded.working, detector)
        if out is not None:
            with _timed(timings, "crops"):
                save_crops(loaded.image, payload.objects, out / "crops")

    if ocr:
        if reader is None:
            with _timed(timings, "ocr_load"):  # only slow on the first photo per process
                reader = get_default_reader()
        with _timed(timings, "text"):
            payload.text = read_text(loaded.image, payload.objects, reader)
        if out is not None:
            with _timed(timings, "text_crops"):
                save_text_crops(loaded.image, payload.text, out / "text")

    payload.run = meter.finish(timings, gpu_device=getattr(detector, "device", None))
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "payload.json").write_text(payload.model_dump_json(indent=2))
        append_history(Path(get_settings().engine_runs_log), payload.model_dump(mode="json"))
    return payload
