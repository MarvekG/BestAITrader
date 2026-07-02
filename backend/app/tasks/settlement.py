import logging
from sqlalchemy import select

from app.core import database as database_module
from app.models.position import Position

logger = logging.getLogger(__name__)


async def execute_daily_settlement() -> None:
    """执行 T+1 每日结算：将冻结股份转为可用股份。"""
    from app.trading.trading_engine import TradingEngine

    logger.info("Starting Daily T+1 Settlement...")
    engine = TradingEngine()
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(select(Position))
        positions = result.scalars().all()
        count = 0
        for pos in positions:
            # 重新计算可用股份
            # Recalculate available shares
            old_available = pos.available_shares
            snapshot = engine.build_position_snapshot(pos)
            if not snapshot:
                continue
            pos.available_shares = snapshot["available_shares"]
            pos.frozen_shares = snapshot["frozen_shares"]

            if old_available != pos.available_shares:
                count += 1

        await db.commit()
        logger.info(f"Daily T+1 Settlement completed. Updated availability for {count} positions.")
