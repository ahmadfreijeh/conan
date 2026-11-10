"""Worker entry: uv run arq app.worker.main.WorkerSettings"""

import asyncio
import logging
from typing import Any

from redis import Redis

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.jobs import REDIS_SETTINGS
from app.worker.jobs.agent import agent

logger = logging.getLogger(__name__)


def load_models() -> None:
    """Load detection and OCR now, not in the first job. Both are cached per process
    (lru_cache), so every job reuses them."""
    from app.modules.engine.extractors.detection import get_default_detector
    from app.modules.engine.extractors.ocr import get_default_reader

    get_default_detector()
    get_default_reader()


async def startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    logger.info("loading models...")
    await asyncio.to_thread(load_models)
    logger.info("models ready")
    # Plain (sync) client for the live log: jobs print from a worker thread, not the loop.
    ctx["log_redis"] = Redis.from_url(get_settings().redis_url, decode_responses=True)


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx["log_redis"].close()


class WorkerSettings:
    functions: list = [agent]
    redis_settings = REDIS_SETTINGS
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1  # the models use a lot of memory; one run at a time per worker for now
    job_timeout = 900  # seconds; a full run (model load + agent) can take minutes
