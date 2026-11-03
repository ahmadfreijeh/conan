# conan

Image geolocation agent. Core app skeleton: FastAPI, async SQLAlchemy + Postgres, Redis, Arq worker.

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

## Dev

```bash
uv run pytest
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
- `app/agent`, `app/tools` reserved for the Strands agent and its tools
