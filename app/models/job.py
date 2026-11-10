from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class Job(BaseModel):
    """One unit of background work. `type` is `<kind>:<task>`, e.g. `agent:geolocation`:
    the kind is the worker function that runs it, the task is what that function does."""

    __tablename__ = "jobs"

    type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="queued")
    # `metadata` is reserved by SQLAlchemy, so the attribute is `meta`.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    log: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def kind(self) -> str:
        return self.type.split(":", 1)[0]

    @property
    def task(self) -> str:
        return self.type.split(":", 1)[-1]
