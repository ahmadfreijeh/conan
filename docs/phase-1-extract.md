# Phase 1: Extract

Photo in, raw clues out. All local, no APIs. The output payload (JSON + crops) is what Phase 2 (Reason) consumes.

Status: **done**. Built step by step; each step is reviewed before the next starts.

## In and out

- **In:** one photo (JPEG, PNG or WebP).
- **Out:** `payload.json` with four parts: `image` (size, format, hash), `metadata` (EXIF GPS, time, device, source app), `objects` (label, box, crop) and `text` (OCR text, language, box). Plus a `run` part with timings and machine use. Shape: `app/engine/payload.py`.

Run it: `uv run python -m app.engine samples/photo.jpeg --out out/photo` (more options in the [README](../README.md#image-engine-phase-1-extract)).

| # | Step | What it does | Status |
|---|---|---|---|
| 1 | Load the image | Validate JPEG/PNG/WebP by content, fix rotation, RGB copy + working copy (max 1280 px), size, format, SHA-256. Original bytes never modified | Done |
| 2 | Payload skeleton | Define the output shape with empty sections; later steps fill their part | Done |
| 3 | Metadata | EXIF GPS, timestamp, device, software; source-app guess from file name | Done |
| 4 | CLI runner | `run photo.jpg` prints the payload, so every step can be tried on real photos | Done |
| 5 | Object detection + crops | Grounding DINO (local, tiny) finds labeled objects; save a crop per box. Behind a detector interface so it can be swapped later | Done |
| 6 | OCR | PaddleOCR (Arabic + English) on the full image and on crops | Done |
| 7 | Speed and settings | OCR finds text lines once, then each recognizer (English/Latin + Arabic) reads them. Engine settings (device, cutoffs, OCR models) in `.env` | Done |
| 8 | Run metrics | Every run records time per step, CPU, GPU, peak RAM and settings; `--out` adds a line to `out/runs.jsonl` to compare runs | Done |

## Decisions

- Formats: JPEG, PNG, WebP only (no HEIC yet).
- The original file is never modified; we work on copies so EXIF stays intact.
- Engine lives in `app/engine/`, independent of FastAPI, DB and auth.
- OCR recognizers: `PP-OCRv6_medium_rec` (English/Latin + Chinese) and `arabic_PP-OCRv5_mobile_rec` (Arabic). Passing a model name to `PaddleOCR(...)` makes it ignore `lang`, so models are named explicitly.
- Object labels: 284 words in 16 groups (`LABEL_GROUPS` in `app/engine/detection.py`), sent in batches of 25 (max 78 of 256 prompt tokens). No people or faces. OCR zooms into every object in the "text" group.
- Grounding DINO (object detection) is not DINOv2 (photo similarity, used in Phase 4: Verify).

## Known limits

- No HEIC (iPhone default) yet; convert to JPEG first.
- Object detection is the slowest step (about 10 s of 21 s on the last run); `--no-detect` skips it.
- No people or faces on purpose: the goal is places, not who is in the photo.
