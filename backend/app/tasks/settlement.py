import logging
from app.core.database import SessionLocal
from app.models.position import Position

logger = logging.getLogger(__name__)


def execute_daily_settlement():
    """
    T+1 每日结算：将冻结的股份（当日买入）转为可用股份（可卖出）
    """
    logger.info("Starting Daily T+1 Settlement...")
    with SessionLocal() as db:
        try:
            from app.trading.trading_engine import TradingEngine
            engine = TradingEngine()

            positions = db.query(Position).all()
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

            db.commit()
            logger.info(f"Daily T+1 Settlement completed. Updated availability for {count} positions.")
        except Exception as e:
            logger.error(f"Error during Daily T+1 Settlement: {e}")
            db.rollback()
