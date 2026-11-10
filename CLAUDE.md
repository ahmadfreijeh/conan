# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Image geolocation agent: given a photo, extract clues (objects, text, EXIF), guess the region, search candidate places and verify them. Design doc: https://claude.ai/artifact/WyxufsioVRXD8WkcrC14mD

Current state: Phase 1 (extract) in `app/modules/engine/`; Phase 2 (reason) in `app/modules/agent/`: a Strands agent with Claude. The engine prepares the agent input (`app/modules/engine/prepare.py`: clues, photo, crops); the agent only reasons over it. Phase 3 `@tool` functions live in `app/modules/agent/tools/` (`places.py`: `find_place`, `reverse_geocode` on OpenStreetMap Nominatim; `wiki.py`: `wiki_search`, `wiki_summary`, `wiki_nearby` on the Wikipedia API, which needs `TOOLS_CONTACT` in the User-Agent or it answers 403). Every tool is listed in `TOOLS` (`app/modules/agent/tools/__init__.py`), which `build_agent()` passes to the agent; to add a tool, write its file and add it to `TOOLS` (and a hint in `TOOL_TIPS`, `agent/constants.py`). A run can switch tools off (`AGENT_TOOLS`, `--tools`, UI toggles): `select_tools()` drops them from the agent and `system_prompt()` names only the tools that are on. Hooks (functions run at moments of the run, e.g. before each tool call) live in `app/modules/agent/hooks.py` in a `HOOKS` list, also passed in `build_agent()`. Tools that call a web API use `ApiClient` (`app/modules/agent/tools/http.py`: timeout, User-Agent, wait between calls, errors as `ToolError`) and return `{"error": ...}` instead of raising. Approval: tools named in `NEEDS_APPROVAL` (`tools/__init__.py`) pause the agent before each call (`ask_approval` hook, Strands interrupt); `reason(..., approve=fn)` asks `fn` (yes / no / all) and resumes. The CLI asks on the terminal. Worker jobs use `redis_approve` (`app/worker/live/approval.py`): the request goes to `job:{id}:approval` and as an `approval` event on the live log stream `job:{id}:log`; the UI answers with `POST /api/v1/runs/{id}/approval`, which pushes to `job:{id}:answer`; after `AGENT_APPROVAL_TIMEOUT` seconds with no answer the worker counts no. UI runs are `agent:geolocation` jobs in the `jobs` table, run by the Arq worker (`app/worker/jobs/agent.py`). `AGENT_APPROVAL=all` asks for every tool (handy to test). Prompt caching is on (`REASON_CACHE`). Each run's tokens and USD cost go to `SPEND_LOG` (`out/spend.jsonl`, `app/modules/agent/spend.py`, prices in `PRICES` in `agent/constants.py`); `GET /api/v1/runs/spend` feeds the UI top bar, where "left" = `CREDIT_BUDGET` minus spent (Anthropic has no balance API). Per-run state (lookup count, allowed tools) is reset by `hooks.start_run()`, not by an invocation hook, since resuming counts as a new invocation.

Docs: the main `README.md` is the overview (setup, full cycle, test UI, roadmap). Module details live next to the code: `app/modules/engine/README.md` (extraction: run, models, settings) and `app/modules/agent/README.md` (reasoning, tools, tool choice, approval, caching, spend, settings, adding a tool). Update the module README when that module changes.

## How we work

- Build step by step. One small piece at a time, never the whole feature in one go.
- Before writing code for a step, explain in a few plain bullets what it does and why, then wait for the user to agree.
- After each step, say what was added and how to try it. Keep explanations short and simple.
- Don't write or run tests unless the user asks.
- Discuss before planning: don't jump to a full implementation plan unless asked.
- Every new feature or process starts with a short plan file in `docs/plans/<topic>.md` (why, steps, open points, decisions). We discuss it point by point, record what we agree under "Decisions", then build it in small steps. Never write the whole thing in one go.

## Commands

All commands go through uv (no venv activation needed). Python 3.12.

- Install: `uv sync`
- Run API: `uv run fastapi dev app/main.py` (docs at /docs)
- Run worker: `uv run arq app.worker.main.WorkerSettings`
- Engine CLI: `uv run python -m app.modules.engine samples/photo.jpeg [--out out/photo] [--no-detect] [--reason] [--from-payload out/photo/payload.json]` (test photo: `samples/photo.jpeg`). `--reason` needs `ANTHROPIC_API_KEY` in `.env`
- Try agent tools by hand (no LLM): `uv run python -m app.modules.agent.tools "Place de l'Etoile" --city Beirut` or `--reverse 33.8959 35.5063`
- Lint/format: `uv run ruff check . && uv run ruff format .`
- Migrations: `uv run alembic revision --autogenerate -m "msg"`, then `uv run alembic upgrade head`
- Add a dependency: `uv add <pkg>` (dev: `uv add --group dev <pkg>`)

## Local environment

- No Docker (too heavy on the dev machine). Postgres 16 (EnterpriseDB install) and Redis run natively on macOS.
- Never read or edit `.env` (it holds the user's secrets). Put new settings in `.env.example` and tell the user what to add to their `.env`.
- Config comes from `.env` (see `.env.example`) via `app/core/config.py::get_settings()`, including engine settings (device, detection/OCR cutoffs, OCR model names).
- Run rate limit (`app/services/rate_limit.py`, Redis): `RUN_LIMIT_PER_HOUR` runs per IP per rolling hour on `POST /api/v1/runs`; skipped when `APP_ENV=local` unless `RUN_LIMIT_ON_LOCAL=true`.
- PostGIS is NOT installed yet, so don't use geometry types or the `postgis` extension until it is set up.

## Architecture

- `app/main.py::create_app()` builds the app. The lifespan sets up logging and closes the Redis client and DB engine on shutdown. Routers mount under `/api/v1` via `app/api/v1/router.py`.
- Request flow: route → service (`app/services`) → repository (`app/repositories`) → ORM model (`app/models`). Routes take `SessionDep` / `RedisDep` from `app/api/deps.py`.
- Transactions: `app/db/session.py::get_session` commits once at the end of the request and rolls back on error. Repositories only `flush()`, never commit.
- Repositories: subclass `BaseRepository[Model]` (`app/repositories/base.py`), set `model = ...`, and add custom queries there. It already provides get, list, count, create, update and delete.
- Models: inherit `app.db.base.BaseModel` (UUID `id`, `created_at`, `updated_at`). Import every new model in `app/models/__init__.py`, or Alembic autogenerate won't see it.
- Alembic (`migrations/env.py`) is async and reads the DB URL from settings, not `alembic.ini`.
- Errors: raise `AppError` subclasses (`app/core/errors.py`). The handler returns `{"error": {"code", "message"}}`.
- Background jobs: register async functions in `WorkerSettings.functions` (`app/worker/main.py`). Long geolocation runs are meant to go here, not in request handlers.
