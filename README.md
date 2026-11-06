# conan

> 🧭 **[How I built it →](docs/how-i-built-it.md)** scaffold first, then one phase at a time, with diagrams.

[![How I built conan](docs/how-i-built-it.svg)](docs/how-i-built-it.md)

Image geolocation agent: give it a photo and it works out where the photo was taken.

## What it does

It pulls clues out of the photo, has Claude reason about them, then searches and checks candidate places. See [Roadmap](#roadmap) for the phases.

## Aim

Act like a human geo-detective: explain *why* it thinks a photo is from a place, not just give coordinates, and get as close to the exact spot as the clues allow.

## Design Philosophy

Work like a detective: **see → think → look up → confirm**. Each phase hands a clean result to the next, so each one can be built, run and checked by itself.

- **Local first:** cheap, private extraction on the machine before any LLM call.
- **Evidence over guesses:** every candidate place comes with the clues that support it.
- **Narrow step by step:** from the whole world to a region, then to a few places, then to one spot.
- **Small, focused context:** the LLM gets cleaned clues and a few crops, not raw output; in Search, old tool results are summarised so long runs stay within limits.
- **Saved run state:** every phase reads and writes its results to Postgres, so a run can pause for a person and resume later.
- **Human in the loop:** a person approves tool use in Search, confirms the result in Verify, and can send the agent back to search deeper.

![Design: Photo → Extract → Reason → Search → Verify → Location](docs/design.svg)

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

## Modules

Each module has its own README with commands, settings and details:

| Module | What it does | Docs |
|---|---|---|
| `app/modules/engine` | Phase 1: pull clues from the photo, all local (EXIF, objects with Grounding DINO, text with PaddleOCR) | [engine/README.md](app/modules/engine/README.md) |
| `app/modules/agent` | Phase 2 + 3: Claude reasons over the clues and checks places with its tools (OpenStreetMap, Wikipedia); tool choice, approval, prompt caching, spend | [agent/README.md](app/modules/agent/README.md) |

Quick try:

```bash
uv run python -m app.modules.engine samples/photo.jpeg --out out/photo                # extract clues
uv run python -m app.modules.engine samples/photo.jpeg --reason --out out/photo       # extract + reason (needs ANTHROPIC_API_KEY)
uv run python -m app.modules.agent.tools --wiki "Mohammad Al-Amin Mosque Beirut"      # try one tool by hand (no LLM)
```

## Full cycle

```bash
uv sync                                                                  # 1. install (once)
cp .env.example .env                                                     # 2. config (once), then set ANTHROPIC_API_KEY
uv run python -m app.modules.engine samples/photo.jpeg --out out/photo           # 3. Phase 1: extract clues (models download on first run)
uv run python -m app.modules.engine.prepare out/photo/payload.json                  # 4. optional: check the clues the LLM will get
uv run python -m app.modules.engine samples/photo.jpeg --reason --from-payload out/photo/payload.json --out out/photo  # 5. Phase 2: reason
cat out/photo/reason.json                                                # 6. read the result
```

Steps 3 and 5 can be one command: `uv run python -m app.modules.engine samples/photo.jpeg --reason --out out/photo`.

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

**Rate limit:** starting a run (`POST /api/v1/runs`) is limited to `RUN_LIMIT_PER_HOUR` (default 3) per IP in any rolling hour, counted in Redis (`app/services/rate_limit.py`). The next one gets `429` with `Retry-After` and "Try again in N min", shown in the Progress card. Only runs that start count; reading history and results is not limited. It is **off when `APP_ENV=local`**; set `RUN_LIMIT_ON_LOCAL=true` to test it locally. Behind a proxy, set `TRUST_PROXY=true` so the IP comes from `X-Forwarded-For`.

Tool approval: if a tool needs approval (`NEEDS_APPROVAL`, or `AGENT_APPROVAL=all` in `.env` to ask for every tool), the Progress card shows the call the agent wants to make with Yes / No / Allow all. The run waits until you answer, or `AGENT_APPROVAL_TIMEOUT` seconds (default 300) pass; then it counts as No. The log shows each request and answer as `[approval] ...` lines. See [Tool approval](app/modules/agent/README.md#tool-approval).

Tools: a Tools card under the Photo card lists every tool with a toggle and a short description per tool (from `GET /api/v1/runs/tools`, the first paragraph of each docstring). Only the tools that are on go to the run; the choice is kept in the browser, and a new tool starts on. Hover a run in the history to see which tools it had (saved in `run.json`).

Top bar: last run's tokens and cost, total spent, and (with `CREDIT_BUDGET` set) about how much credit is left, e.g. `Last run: 54k tokens · $0.059 | Total: 12 runs · 650k tokens · $1.42 | Left: ~$18.58 of $20`. "Left" turns red under 10%. It comes from `GET /api/v1/runs/spend`.

From the CLI, `--reason --out <dir>` saves the same `clues.json` and `sent/` (crops + `crops.json`) before calling the LLM.

### Screenshots

A past run opened from History (the Wadi Rum photo, top guess Aqaba Governorate, Jordan at 82%).

![Overview](assets/screenshots/overview.png)

| | |
|---|---|
| ![Start](assets/screenshots/1-empty.png) | ![Pick a past run](assets/screenshots/2-history.png) |
| ![Everything fills in](assets/screenshots/3-filled.png) | ![Ranked guesses](assets/screenshots/4-candidates.png) |
| ![Pipeline progress](assets/screenshots/5-progress.png) | ![Tool switches](assets/screenshots/6-tools.png) |
| ![Spend tracker](assets/screenshots/7-header.png) | ![Extraction log](assets/screenshots/8-extract-log.png) |

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
- `app/modules/engine` image extraction engine: `extractors/` (loader, metadata, detection, ocr), `pipeline`, `prepare` (clues + crops for the agent), `schemas`, `constants`, `helpers`, CLI; no FastAPI/DB
- `app/modules/agent` the reasoning agent: takes the input the engine prepared and returns the result (`reason` builds the agent and handles approval pauses, `hooks` (lookup limit, approval, printing), `schemas`, `constants` with the prompt, `helpers`, `spend` (token cost and spend log))
- `app/modules/agent/tools` the agent's `@tool` functions: `TOOLS` and `NEEDS_APPROVAL` lists (`__init__`), `places` (map tools), `wiki` (Wikipedia tools), `http` (shared `ApiClient`), CLI to try them (`__main__`)
- `app/ui` test page (upload + live run); `app/api/v1/runs.py` + `app/services/runs.py` run the script for it

## Roadmap

### Phase 1: Extract ✅

Pull raw clues from the photo, all local: objects (Grounding DINO), text (PaddleOCR, Arabic + English) and EXIF metadata. Details: [docs/phase-1-extract.md](docs/phase-1-extract.md).

### Phase 2: Reason 🚧 (in progress)

A Strands agent with Claude reads the clues and the photo, then returns ranked candidate places with evidence and search queries for Phase 3. Details: [docs/phase-2-reason.md](docs/phase-2-reason.md).

- [x] Clean clues from the Phase 1 payload for the LLM
- [x] Send the photo plus close-up crops of key objects
- [x] Return ranked places with confidence, evidence and search queries
- [x] Test UI to run it and see the result
- [ ] Test on more photos and improve the prompt
- [ ] Check that confidence scores match how often it is right

### Phase 3: Search 🚧 (in progress)

Give the agent tools to look up the candidate places, using many sources (maps, web, wiki, street and review photos). Plan: [docs/phase-3-search.md](docs/phase-3-search.md).

- [x] Map tools: `find_place` and `reverse_geocode` (OpenStreetMap)
- [x] Agent calls tools itself, retries with hints, lookup limit, cache
- [x] Tool approval in the CLI and the test UI, with a timeout
- [x] Candidate places get coordinates when a lookup confirms them
- [x] Wiki tools: `wiki_search` (with fuzzy / wildcard / fragment search for OCR words), `wiki_summary`, `wiki_nearby`
- [x] Choose tools per run (UI toggles, `--tools`, `AGENT_TOOLS`); a tool that is off is removed from the run and the prompt
- [x] Prompt caching and spend tracking (tokens, USD, budget left in the UI)
- [ ] `wiki_features(kind, region)`: list features of one kind in a region from Wikidata (e.g. every mountain in Istanbul Province, with coordinates and height), then check each with `wiki_nearby` / `wiki_summary`. The tool builds the query from a fixed menu of kinds (mountain, lake, island, beach, lighthouse, castle, bridge, mosque, church, tower), so the agent never writes SPARQL
- [ ] Web search tool
- [ ] Pattern search on OpenStreetMap (Overpass) for cut-off shop signs in an area
- [ ] Merge results from all sources and rank places
- [ ] Find reference photos for each candidate

### Phase 4: Retrieve (RAG)

Give the agent memory and reference data with a vector DB (Qdrant: local mode for dev, server or cloud later).

- [ ] Photo similarity: match the photo against geotagged reference photos (CLIP / GeoCLIP embeddings)
- [ ] Clue knowledge base: notes like plate colors, scripts, phone formats → country; `search_clues` tool
- [ ] Fuzzy place names: embed OSM place names so misread OCR signs still match
- [ ] Past runs: save solved runs and reuse them for photos with similar clues

### Phase 5: Verify

Check candidates against the photo and pick the final location.

- [ ] Compare the photo with reference photos (DINOv2 similarity)
- [ ] Score each candidate and pick the best match
- [ ] Return a final location with coordinates, confidence and the reasons why

### Production

- [ ] Run geolocation jobs in the Arq worker instead of the request
- [ ] Save runs and results to Postgres
- [ ] Set up PostGIS for location data

### Known issues

- [ ] Test UI live log sometimes shows the lines after the last approval twice (display only; `log.txt` is clean). Likely an event-stream reconnect; check for two `GET .../events` per run

## Open discussions

Choices made for Phase 1 that are still open to change.

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

Open: the detector sits behind an interface, so it can be swapped (e.g. YOLO-World for speed) without touching the rest.
