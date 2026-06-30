from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import Base, engine
from app.core.logger import get_logger
from app.core.system_language import load_persisted_system_language
from app.crud.user import User, get_password_hash  # 统一使用 crud.user 逻辑
from app.models import *  # noqa: F401,F403

logger = get_logger(__name__)


def _ensure_required_schemas() -> None:
    if not engine.dialect.name.startswith("postgresql"):
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS data;"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS stock_picker_interactive;"))


def init_db(db: Session) -> None:
    """
    Initialize the database, create tables, and create a superuser if it doesn't exist.
    """
    logger.info("Creating database tables...")
    _ensure_required_schemas()
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully!")

    try:
        language = load_persisted_system_language(db)
        logger.info(f"System language initialized: {language}")
    except Exception as exc:
        logger.warning(f"Failed to initialize system language from database: {exc}")

    # Check if superuser exists
    user = db.query(User).filter(User.username == settings.FIRST_SUPERUSER).first()
    if not user:
        logger.info(f"Creating superuser: {settings.FIRST_SUPERUSER} with email: {settings.FIRST_SUPERUSER_EMAIL}")
        # 使用配置文件中的邮箱创建超级用户
        user = User(
            email=settings.FIRST_SUPERUSER_EMAIL,
            username=settings.FIRST_SUPERUSER,
            password_hash=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
            full_name="Administrator",
            is_active=True,
            is_superuser=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("Superuser created successfully")
    else:
        logger.info("Superuser already exists")
