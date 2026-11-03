import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import BaseModel


class BaseRepository[ModelT: BaseModel]:
    """Generic async CRUD. Subclass per model and add custom queries there."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, id)

    async def list(self, *, offset: int = 0, limit: int = 50) -> Sequence[ModelT]:
        stmt = select(self.model).offset(offset).limit(limit).order_by(self.model.created_at)
        return (await self.session.scalars(stmt)).all()

    async def count(self) -> int:
        return await self.session.scalar(select(func.count()).select_from(self.model)) or 0

    async def create(self, **data: Any) -> ModelT:
        obj = self.model(**data)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, **data: Any) -> ModelT:
        for key, value in data.items():
            setattr(obj, key, value)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()
