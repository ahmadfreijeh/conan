# Phase 1: Extract

Photo in, raw clues out. All local, no APIs. The output payload (JSON + crops) is what Phase 2 (Reason) consumes.

Built step by step; each step is reviewed before the next starts.

| # | Step | What it does | Status |
|---|---|---|---|
| 1 | Load the image | Validate JPEG/PNG/WebP by content, fix rotation, RGB copy + working copy (max 1280 px), size, format, SHA-256. Original bytes never modified | Todo |
| 2 | Payload skeleton | Define the output shape with empty sections; later steps fill their part | Todo |
| 3 | Metadata | EXIF GPS, timestamp, device, software; source-app guess from file name | Todo |
| 4 | CLI runner | `run photo.jpg` prints the payload, so every step can be tried on real photos | Todo |
| 5 | Object detection + crops | Grounding DINO (local, tiny) finds labeled objects; save a crop per box. Behind a detector interface so it can be swapped later | Todo |
| 6 | OCR | PaddleOCR (Arabic + English) on the full image and on crops | Todo |
| 7 | Speed and memory | Load models once, use Mac GPU (MPS), pick model sizes from real timings | Todo |

## Decisions

- Formats: JPEG, PNG, WebP only (no HEIC yet).
- The original file is never modified; we work on copies so EXIF stays intact.
- Engine lives in `app/engine/`, independent of FastAPI, DB and auth.
- Grounding DINO (object detection) is not DINOv2 (photo similarity, used in Phase 3).
