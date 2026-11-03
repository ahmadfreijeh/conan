from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import setup_logging


async def startup(ctx: dict[str, Any]) -> None:
    setup_logging()


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions: list = []  # register background jobs here
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
