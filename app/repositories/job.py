from collections.abc import Sequence

from sqlalchemy import func, select

from app.models.job import Job
from app.repositories.base import BaseRepository

FINISHED = ("done", "failed")


class JobRepository(BaseRepository[Job]):
    model = Job

    async def list_by_kind(self, kind: str, *, offset: int, limit: int) -> Sequence[Job]:
        """Jobs of one kind (e.g. "agent"), newest first."""
        stmt = (
            select(Job)
            .where(Job.type.startswith(f"{kind}:"))
            .order_by(Job.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def count_by_kind(self, kind: str) -> int:
        stmt = select(func.count()).select_from(Job).where(Job.type.startswith(f"{kind}:"))
        return await self.session.scalar(stmt) or 0

    async def finished_by_kind(self, kind: str) -> Sequence[Job]:
        stmt = select(Job).where(Job.type.startswith(f"{kind}:"), Job.status.in_(FINISHED))
        return (await self.session.scalars(stmt)).all()
