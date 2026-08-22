import asyncio
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from migration_bootstrap import main as migrate_database
from app.core.logger import get_logger
from app.core.startup_db import initialize_database

logger = get_logger(__name__)


async def main() -> None:
    """执行 schema 迁移并初始化默认管理员账号。"""
    logger.info("Starting database migration and bootstrap...")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"PYTHONPATH: {sys.path}")
    migrate_database()
    await asyncio.wait_for(initialize_database(), timeout=60)


if __name__ == "__main__":
    # CLI-only asyncio.run bridge.
    try:
        asyncio.run(main())
    except TimeoutError:
        logger.error("Error: Database initialization execution time exceeded 60 seconds")
        logger.info("\nPossible reasons:")
        logger.info("1. PostgreSQL database not started or connection failed")
        logger.info("2. Database connection configuration error")
        logger.info("3. Database performance issue or network delay")
        logger.info("\nRecommended solutions:")
        logger.info("1. Check if PostgreSQL is running: docker-compose ps")
        logger.info("2. Check if database connection configuration is correct")
        logger.info("3. Try restarting PostgreSQL container: docker-compose restart db")
        logger.info("4. Check database logs: docker-compose logs db")
        sys.exit(1)
    logger.info("Database migration and bootstrap completed")
    sys.exit(0)
