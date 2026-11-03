from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import RedisDep, SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep, redis: RedisDep, response: Response) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc.__class__.__name__}"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc.__class__.__name__}"
    if any(v != "ok" for v in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return checks
