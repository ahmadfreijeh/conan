# conan

Image geolocation agent: give it a photo and it works out where the photo was taken.

## What it does

It pulls clues out of the photo, has Claude reason about them, then searches and checks candidate places. See [Roadmap](#roadmap) for the phases.

## Aim

Act like a human geo-detective: explain *why* it thinks a photo is from a place, not just give coordinates, and get as close to the exact spot as the clues allow.

## Stack

FastAPI, async SQLAlchemy + Postgres, Redis, Arq worker, Strands agent with Claude.

## Setup

Needs Postgres and Redis running locally.

```bash
cp .env.example .env        # edit DATABASE_URL / REDIS_URL
uv sync
uv run alembic upgrade head
```

## Run

```bash
uv run fastapi dev app/main.py              # API on http://127.0.0.1:8000 (docs at /docs)
uv run arq app.worker.main.WorkerSettings   # background worker
```

## Image engine (Phase 1: Extract)

Runs every extraction step on a photo (load, EXIF, object detection, OCR) and prints the JSON payload. Steps and status: [docs/phase-1-extract.md](docs/phase-1-extract.md).

### Run

```bash
uv run python -m app.engine samples/photo.jpeg                          # print the payload
uv run python -m app.engine samples/photo.jpeg --out out/photo          # also save files (see below)
uv run python -m app.engine samples/photo.jpeg --no-detect              # skip object detection
uv run python -m app.engine samples/photo.jpeg --no-ocr                 # skip text reading
uv run python -m app.engine samples/photo.jpeg --no-detect --no-ocr     # load + EXIF only (instant, no models)
```

With `--out out/photo` you get:

- `out/photo/payload.json`: the full result
- `out/photo/crops/`: one image per detected object (e.g. `03_sign.jpg`)
- `out/photo/text/`: one image per text line read (e.g. `03_CAFE.jpg`)
- `out/runs.jsonl`: one line added per run (time, CPU, GPU, memory, settings) to compare runs

Every payload has a `run` section: total and per-step time, CPU time and usage, GPU device and memory, peak RAM, machine, library versions and the settings used. GPU usage % isn't available on macOS without sudo.

Put test photos in `samples/` (JPEG, PNG or WebP). `samples/` and `out/` are git-ignored.

### Models (one-time downloads)

```bash
# Object detection: Grounding DINO tiny (~700 MB, into ~/.cache/huggingface)
uv run python -c "from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection as M; m='IDEA-Research/grounding-dino-tiny'; AutoProcessor.from_pretrained(m); M.from_pretrained(m); print('dino ready')"

# OCR: text finder + English/Latin and Arabic readers (into ~/.paddlex/official_models)
uv run python -c "from paddleocr import TextDetection, TextRecognition; TextDetection(model_name='PP-OCRv5_mobile_det'); [TextRecognition(model_name=m) for m in ('PP-OCRv6_medium_rec', 'arabic_PP-OCRv5_mobile_rec')]; print('ocr ready')"
```

Missing models also download automatically on the first run.

### Settings (`.env`, all optional)

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

## Reasoning agent (Phase 2: Reason)

A Strands agent with Claude (no tools yet) reads the payload plus the photo, links the clues and returns ranked candidate places with evidence, plus search queries for Phase 3. Needs `ANTHROPIC_API_KEY` in `.env`.

```bash
uv run python -m app.agent.clues out/photo/payload.json                                          # see the clues sent to the LLM (no API call)
uv run python -m app.engine samples/photo.jpeg --reason --out out/photo                          # Phase 1 + Phase 2
uv run python -m app.engine samples/photo.jpeg --reason --from-payload out/photo/payload.json --out out/photo  # Phase 2 only, reuses the saved payload
```

The result prints and, with `--out`, is saved to `out/photo/reason.json`.

| Setting | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (empty) | Required for `--reason` |
| `REASON_MODEL` | `claude-sonnet-5-5` | Claude model; `claude-opus-5-5` for hard photos |
| `REASON_MAX_TOKENS` | `16000` | Max length of the answer |
| `REASON_MAX_CROPS` | `6` | Close-up crops of detected objects (signs, street, shops, vehicles, buildings) sent with the photo; `0` = photo only |

## Full cycle

```bash
uv sync                                                                  # 1. install (once)
cp .env.example .env                                                     # 2. config (once), then set ANTHROPIC_API_KEY
uv run python -m app.engine samples/photo.jpeg --out out/photo           # 3. Phase 1: extract clues (models download on first run)
uv run python -m app.agent.clues out/photo/payload.json                  # 4. optional: check the clues the LLM will get
uv run python -m app.engine samples/photo.jpeg --reason --from-payload out/photo/payload.json --out out/photo  # 5. Phase 2: reason
cat out/photo/reason.json                                                # 6. read the result
```

Steps 3 and 5 can be one command: `uv run python -m app.engine samples/photo.jpeg --reason --out out/photo`.

Or run all steps with one script (it stops early if `ANTHROPIC_API_KEY` is missing):

```bash
scripts/full_run.sh                              # samples/photo.jpeg -> out/photo
scripts/full_run.sh samples/cafe.jpg out/cafe    # any photo and output folder
```

## Test UI

```bash
uv run fastapi dev app/main.py    # then open http://127.0.0.1:8000
```

Upload a photo and press Run. The server runs `scripts/full_run.sh` on it, streams the log live (steps 1/5 to 5/5), then shows the result: summary, ranked places with confidence and evidence, search queries, OCR fixes. It also shows what was sent to the LLM: the close-up crops and the cleaned clues.

The left panel is the history: every upload, newest first, with its top guess. Click one to see its photo, log, result and inputs again. It is read from `out/uploads/<run id>/` (photo, `run.json`, `log.txt`, `payload.json`, `clues.json`, `sent/`, `reason.json`), so it survives restarts. At the bottom, the extraction log lists every Phase 1 run from `out/runs.jsonl` (UI and CLI): time, image, size, objects, text lines, detect/OCR/total time, peak RAM and device; hover a row for its settings, click a UI row to open that case. Hover a run and click × to delete it, or use "Delete all" (both ask first; a run that is still going is kept). One run at a time; local testing only.

From the CLI, `--reason --out <dir>` saves the same `clues.json` and `sent/` (crops + `crops.json`) before calling the LLM.

## Dev

```bash
uv run ruff check . && uv run ruff format .
```

## Layout

- `app/api` routes and dependencies
- `app/core` config, logging, errors
- `app/db` engine, session, base model
- `app/models` ORM models
- `app/schemas` Pydantic request/response models
- `app/repositories` data access (extend `BaseRepository` per model)
- `app/services` business logic
- `app/worker` Arq background jobs
- `app/engine` image extraction engine (loader, metadata, detection, ocr, payload, pipeline, CLI); no FastAPI/DB
- `app/agent` Phase 2 reasoning agent (clues, result shape, Strands agent)
- `app/tools` reserved for the agent's Phase 3 tools
- `app/ui` test page (upload + live run); `app/api/v1/runs.py` + `app/services/runs.py` run the script for it

## Roadmap

### Phase 1: Extract ✅

Pull raw clues from the photo, all local: objects (Grounding DINO), text (PaddleOCR, Arabic + English) and EXIF metadata. Details: [docs/phase-1-extract.md](docs/phase-1-extract.md).

### Phase 2: Reason 🚧 (in progress)

A Strands agent with Claude reads the clues and the photo, then returns ranked candidate places with evidence and search queries for Phase 3.

- [x] Clean clues from the Phase 1 payload for the LLM
- [x] Send the photo plus close-up crops of key objects
- [x] Return ranked places with confidence, evidence and search queries
- [x] Test UI to run it and see the result
- [ ] Test on more photos and improve the prompt
- [ ] Check that confidence scores match how often it is right

### Phase 3: Search

Give the agent tools to look up the candidate places.

- [ ] Add `@tool` functions in `app/tools/` (e.g. map / place search, web search)
- [ ] Let the agent run the search queries from Phase 2
- [ ] Collect candidate places with coordinates
- [ ] Find reference photos for each candidate

### Phase 4: Verify

Check candidates against the photo and pick the final location.

- [ ] Compare the photo with reference photos (DINOv2 similarity)
- [ ] Score each candidate and pick the best match
- [ ] Return a final location with coordinates, confidence and the reasons why

### Production

- [ ] Run geolocation jobs in the Arq worker instead of the request
- [ ] Save runs and results to Postgres
- [ ] Set up PostGIS for location data
