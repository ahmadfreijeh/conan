"""Rate limit for starting runs: RUN_LIMIT_PER_HOUR per IP in any rolling hour.

Only POST /api/v1/runs is limited (each run calls the LLM and costs money); reading
history and results is free. Skipped when APP_ENV=local, unless RUN_LIMIT_ON_LOCAL=true
(to test it).

Each IP has a Redis sorted set of its run start times. Before a run: drop times older
than one hour, and refuse if RUN_LIMIT_PER_HOUR are left. After the run started: add
the time. So a refused or failed upload does not use up a slot.
"""

import time
import uuid

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import AppError

WINDOW = 3600  # seconds: one rolling hour
KEY = "ratelimit:runs:{ip}"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"


class RateLimitUnavailableError(AppError):
    status_code = 503
    code = "rate_limit_unavailable"


def client_ip(request: Request) -> str:
    """The caller's IP. Behind a proxy (TRUST_PROXY=true), the first X-Forwarded-For entry."""
    if get_settings().trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enabled() -> bool:
    settings = get_settings()
    return settings.run_limit_per_hour > 0 and (
        not settings.is_local or settings.run_limit_on_local
    )


async def check_run_limit(redis: Redis, ip: str) -> None:
    """Raise RateLimitError (429, with Retry-After) if this IP used up its runs this hour."""
    if not _enabled():
        return
    limit = get_settings().run_limit_per_hour
    key = KEY.format(ip=ip)
    now = time.time()
    try:
        await redis.zremrangebyscore(key, 0, now - WINDOW)
        if await redis.zcard(key) < limit:
            return
        oldest = await redis.zrange(key, 0, 0, withscores=True)
    except RedisError as exc:
        raise RateLimitUnavailableError("Rate limiter is unavailable; try again later") from exc
    wait = int(oldest[0][1] + WINDOW - now) + 1 if oldest else WINDOW
    error = RateLimitError(
        f"Limit reached: {limit} runs per hour. Try again in {max(1, round(wait / 60))} min."
    )
    error.headers = {"Retry-After": str(wait)}
    raise error


async def record_run(redis: Redis, ip: str) -> None:
    """Count one started run for this IP."""
    if not _enabled():
        return
    key = KEY.format(ip=ip)
    now = time.time()
    try:
        await redis.zadd(key, {f"{now}-{uuid.uuid4().hex[:6]}": now})
        await redis.expire(key, WINDOW)
    except RedisError:
        pass  # the run already started; a missed count is better than a failed request
