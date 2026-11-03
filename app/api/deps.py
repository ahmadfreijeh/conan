from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import get_redis
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
