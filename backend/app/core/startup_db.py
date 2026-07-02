from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update

from app.core import database as database_module
from app.core.config import settings
from app.core.logger import get_logger
from app.crud.user import User, get_password_hash

logger = get_logger(__name__)


async def initialize_database() -> None:
    """初始化数据库 schema、表结构和默认管理员账号。"""
    import app.models  # noqa: F401

    if database_module.async_engine.dialect.name.startswith("postgresql"):
        async with database_module.async_engine.begin() as conn:
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS data;"))
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS stock_picker_interactive;"))
    async with database_module.async_engine.begin() as conn:
        await conn.run_sync(database_module.Base.metadata.create_all)

    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == settings.FIRST_SUPERUSER))
        user = result.scalar_one_or_none()
        if user:
            logger.info("Superuser already exists")
            return

        logger.info(f"Creating superuser: {settings.FIRST_SUPERUSER} with email: {settings.FIRST_SUPERUSER_EMAIL}")
        db.add(
            User(
                email=settings.FIRST_SUPERUSER_EMAIL,
                username=settings.FIRST_SUPERUSER,
                password_hash=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
                full_name="Administrator",
                is_active=True,
                is_superuser=True,
            )
        )
        await db.commit()
        logger.info("Superuser created successfully")


async def reset_active_analysis_sessions() -> int:
    """将重启前仍为 active 的分析会话标记为 failed。"""
    from app.models.session import Session as AnalysisSession

    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(select(AnalysisSession.session_id).where(AnalysisSession.status == "active"))
        active_session_ids = result.scalars().all()
        if not active_session_ids:
            return 0

        await db.execute(
            update(AnalysisSession)
            .where(AnalysisSession.status == "active")
            .values(status="failed")
        )
        await db.commit()
        return len(active_session_ids)
