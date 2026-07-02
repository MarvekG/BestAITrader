from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

async_engine = create_async_engine(
    str(settings.ASYNC_DATABASE_URL),
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy ORM models."""

    pass


async def get_async_db() -> AsyncIterator[AsyncSession]:
    """提供应用运行时使用的异步数据库会话。

    Yields:
        绑定异步引擎的 SQLAlchemy 异步会话。
    """
    async with AsyncSessionLocal() as db:
        yield db
