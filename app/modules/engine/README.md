# Image engine (Phase 1: Extract)

Runs every extraction step on a photo (load, EXIF, object detection, OCR) and prints the JSON payload. Steps and status: [docs/phase-1-extract.md](../../../docs/phase-1-extract.md).

## Run

```bash
uv run python -m app.modules.engine samples/photo.jpeg                          # print the payload
uv run python -m app.modules.engine samples/photo.jpeg --out out/photo          # also save files (see below)
uv run python -m app.modules.engine samples/photo.jpeg --no-detect              # skip object detection
uv run python -m app.modules.engine samples/photo.jpeg --no-ocr                 # skip text reading
uv run python -m app.modules.engine samples/photo.jpeg --no-detect --no-ocr     # load + EXIF only (instant, no models)
```

With `--out out/photo` you get:

- `out/photo/payload.json`: the full result
- `out/photo/crops/`: one image per detected object (e.g. `03_sign.jpg`)
- `out/photo/text/`: one image per text line read (e.g. `03_CAFE.jpg`)
- `out/runs.jsonl`: one line added per run (time, CPU, GPU, memory, settings) to compare runs

Every payload has a `run` section: total and per-step time, CPU time and usage, GPU device and memory, peak RAM, machine, library versions and the settings used. GPU usage % isn't available on macOS without sudo.

Put test photos in `samples/` (JPEG, PNG or WebP). `samples/` and `out/` are git-ignored.

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

Next step: the [reasoning agent](../agent/README.md) takes this payload. Back to the [main README](../../../README.md).
