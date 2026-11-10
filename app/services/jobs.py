"""Queue background jobs for the Arq worker. A job type is `<kind>:<task>` (see Job)."""

import uuid
from pathlib import Path
from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.job import Job
from app.repositories.job import JobRepository

ROOT = Path(__file__).resolve().parents[2]
JOBS = ROOT / "out" / "jobs"  # each job's files go to JOBS/<job id>/
REDIS_SETTINGS = RedisSettings.from_dsn(get_settings().redis_url)

_pool: ArqRedis | None = None


def job_key(job_id: str, name: str) -> str:
    """A job's Redis key: `log` (stream of live lines and events), `approval` (the tool call
    the job waits on), `answer` (the person's reply)."""
    return f"job:{job_id}:{name}"


async def arq_pool() -> ArqRedis:
    """One Arq connection per process (the app's Redis client decodes text; Arq needs bytes)."""
    global _pool
    if _pool is None:
        _pool = await create_pool(REDIS_SETTINGS)
    return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def queue_job(job_type: str, meta: dict[str, Any], job_id: uuid.UUID | None = None) -> Job:
    """Save the job row in its own transaction, then queue it, so the worker always finds it."""
    async with SessionLocal() as session:
        job = await JobRepository(session).create(
            id=job_id or uuid.uuid4(), type=job_type, meta=meta
        )
        await session.commit()
    pool = await arq_pool()
    await pool.enqueue_job(job.kind, str(job.id), _job_id=str(job.id))
    return job
