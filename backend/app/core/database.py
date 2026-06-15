from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# 创建 SQLAlchemy 引擎。全项目只使用这一套 engine/session，避免连接池配置分叉。
engine = create_engine(
    str(settings.DATABASE_URL),
    pool_size=20,         # 基础连接池大小
    max_overflow=40,      # 允许溢出的最大连接数 (合计 60)
    pool_pre_ping=True,   # 检出连接前检查其有效性
    pool_recycle=3600     # 1小时后自动回收连接，防止因超时被数据库关闭
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy ORM models."""

    pass


def get_db() -> Iterator[Session]:
    """
    Provide a database session for FastAPI dependencies.

    Yields:
        SQLAlchemy session bound to the application engine.
    """
    with SessionLocal() as db:
        yield db


def get_db_session() -> Iterator[Session]:
    """
    Provide a database session generator for non-FastAPI call sites.

    Yields:
        SQLAlchemy session bound to the application engine.
    """
    with SessionLocal() as db:
        yield db
