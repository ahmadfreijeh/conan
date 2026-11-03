# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Image geolocation agent: given a photo, extract clues (objects, text, EXIF), guess the region, search candidate places and verify them. Design doc: https://claude.ai/artifact/WyxufsioVRXD8WkcrC14mD

Current state: core app skeleton only. `app/agent/` and `app/tools/` are empty placeholders for the Strands agent and its `@tool` functions.

## How we work

- Build step by step. One small piece at a time, never the whole feature in one go.
- Before writing code for a step, explain in a few plain bullets what it does and why, then wait for the user to agree.
- After each step, say what was added and how to try it. Keep explanations short and simple.
- Discuss before planning: don't jump to a full implementation plan unless asked.

## Commands

All commands go through uv (no venv activation needed). Python 3.12.

- Install: `uv sync`
- Run API: `uv run fastapi dev app/main.py` (docs at /docs)
- Run worker: `uv run arq app.worker.main.WorkerSettings`
- Tests: `uv run pytest`; single test: `uv run pytest tests/test_health.py::test_health`
- Lint/format: `uv run ruff check . && uv run ruff format .`
- Migrations: `uv run alembic revision --autogenerate -m "msg"`, then `uv run alembic upgrade head`
- Add a dependency: `uv add <pkg>` (dev: `uv add --group dev <pkg>`)

## Local environment

- No Docker (too heavy on the dev machine). Postgres 16 (EnterpriseDB install) and Redis run natively on macOS.
- Config comes from `.env` (see `.env.example`) via `app/core/config.py::get_settings()`.
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
