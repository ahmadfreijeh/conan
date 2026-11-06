# Image engine (Phase 1: Extract)

Photo in, raw clues out. All local, no APIs, no LLM. The result (`payload.json` + crops) is what the [agent](../agent/README.md) reads. Back to the [main README](../../../README.md).

Status: **done**. Lives in `app/modules/engine/`, independent of FastAPI, DB and auth.

## In and out

- **In:** one photo (JPEG, PNG or WebP).
- **Out:** `payload.json` with `image` (size, format, hash), `metadata` (EXIF GPS, time, device, source app), `objects` (label, box, crop), `text` (OCR text, language, box) and `run` (timings and machine use). Shape: `schemas.py`.

## How it works

1. **Load:** check the format by content, fix rotation, make an RGB copy and a working copy (max 1280 px), hash it. The original file is never modified, so EXIF stays intact.
2. **Metadata:** EXIF GPS, time, device, software; guess the source app from the file name.
3. **Objects:** Grounding DINO (tiny, local) looks for 284 labels in 16 groups (`LABEL_GROUPS` in `constants.py`), sent in batches of 25. Saves a crop per box. No people or faces on purpose: the goal is places, not who is in the photo.
4. **Text:** PaddleOCR finds text lines once on the full image and on every "text" group crop, then each reader (English/Latin, Arabic) reads them.
5. **Run metrics:** time per step, CPU, GPU, peak RAM and settings, so runs can be compared.
6. **Prepare for the agent** (`prepare.py`): clean clues + the photo + up to 6 close-up crops. See [agent: how reasoning works](../agent/README.md#how-it-works).

## Run

```bash
uv run python -m app.modules.engine samples/photo.jpeg                          # print the payload
uv run python -m app.modules.engine samples/photo.jpeg --out out/photo          # also save files (see below)
uv run python -m app.modules.engine samples/photo.jpeg --no-detect              # skip object detection
uv run python -m app.modules.engine samples/photo.jpeg --no-ocr                 # skip text reading
uv run python -m app.modules.engine samples/photo.jpeg --no-detect --no-ocr     # load + EXIF only (instant, no models)
uv run python -m app.modules.engine.prepare out/photo/payload.json              # see the clues the LLM will get
```

With `--out out/photo` you get:

- `out/photo/payload.json`: the full result
- `out/photo/crops/`: one image per detected object (e.g. `03_sign.jpg`)
- `out/photo/text/`: one image per text line read (e.g. `03_CAFE.jpg`)
- `out/runs.jsonl`: one line per run (time, CPU, GPU, memory, settings)

GPU usage % isn't available on macOS without sudo. Put test photos in `samples/`; `samples/` and `out/` are git-ignored.

## Models (one-time downloads)

```bash
# Object detection: Grounding DINO tiny (~700 MB, into ~/.cache/huggingface)
uv run python -c "from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection as M; m='IDEA-Research/grounding-dino-tiny'; AutoProcessor.from_pretrained(m); M.from_pretrained(m); print('dino ready')"

# OCR: text finder + English/Latin and Arabic readers (into ~/.paddlex/official_models)
uv run python -c "from paddleocr import TextDetection, TextRecognition; TextDetection(model_name='PP-OCRv5_mobile_det'); [TextRecognition(model_name=m) for m in ('PP-OCRv6_medium_rec', 'arabic_PP-OCRv5_mobile_rec')]; print('ocr ready')"
```

Missing models also download automatically on the first run.

## Settings (`.env`, all optional)

| Setting | Default | Meaning |
|---|---|---|
| `ENGINE_DEVICE` | `auto` | Object detection on `mps` (Mac GPU) or `cpu` |
| `DETECT_BOX_THRESHOLD` | `0.30` | Min object confidence |
| `DETECT_TEXT_THRESHOLD` | `0.25` | Min confidence the box matches the word |
| `DETECT_GROUPS` | `all` | Label groups to look for, e.g. `text,street,buildings`. Groups: text, food, table, interior, venue, buildings, architecture, landmarks, street, commerce, rooftops, vehicles, nature, plants, animals, symbols |
| `DETECT_BATCH_SIZE` | `25` | Labels per model run. Fewer runs = faster, longer prompts = less accurate |
| `OCR_DET_MODEL` | `mobile` | Text finder: `mobile` (fast) or `server` (more accurate) |
| `OCR_REC_MODELS` | `PP-OCRv6_medium_rec,arabic_PP-OCRv5_mobile_rec` | Text readers, comma-separated |
| `OCR_MIN_SCORE` | `0.5` | Min text reading confidence |
| `ENGINE_RUNS_LOG` | `out/runs.jsonl` | Run history file (written when `--out` is used) |

## Decisions

- Formats: JPEG, PNG, WebP only (no HEIC yet).
- OCR readers are named explicitly (`PP-OCRv6_medium_rec` for English/Latin + Chinese, `arabic_PP-OCRv5_mobile_rec` for Arabic): passing a model name to `PaddleOCR(...)` makes it ignore `lang`.
- Labels fit the prompt: batches of 25 use at most 78 of the 256 prompt tokens.
- The detector sits behind an interface, so it can be swapped without touching the rest.
- Grounding DINO (object detection) is not DINOv2 (photo similarity, planned for Phase 5: Verify).

## Known limits

- No HEIC (iPhone default) yet; convert to JPEG first.
- Object detection is the slowest step (about 10 s of 21 s on the last run); `--no-detect` skips it.

## Open discussions

### Why PaddleOCR for text?

Photos for geolocation have street signs, shop names and plates, often in Arabic and English side by side.

- **PaddleOCR (chosen):** good on scene text (signs, not just scanned pages), has a ready Arabic reader, runs locally on CPU, models are small.
- **Tesseract:** made for clean scanned documents; weak on tilted or noisy text in photos.
- **EasyOCR:** easy to use and has Arabic, but generally slower and less accurate on scene text.
- **Cloud OCR (Google Vision, AWS Textract):** very accurate, but costs per call and sends the photo off the machine (against "local first").
- **Claude vision only:** can read text, but costs tokens on every photo; we'd rather send it clean text and a few crops.

Open: swap in a stronger Arabic reader if street signs keep coming out wrong.

### Why Grounding DINO for objects?

We need to find things that hint at a place (road signs, license plates, flags, shop signs, mosques, bollards), and that list keeps changing.

- **Grounding DINO (chosen):** open-vocabulary: you give it the labels as text, so adding a new clue type is a text change, not retraining. The tiny model runs locally.
- **YOLO (v8/v11):** fast and easy, but only knows its fixed classes (COCO: car, person...); new clue types need labeled data and training.
- **OWL-ViT / OWLv2:** also open-vocabulary, but generally weaker on small objects like signs.
- **Florence-2 / other VLMs:** can detect and caption, but heavier and slower on a laptop.
- **Cloud vision APIs:** fixed labels, cost per call, and the photo leaves the machine.

Open: try YOLO-World if detection speed becomes the bottleneck (it is the slowest step today).
