from typing import Dict, Any
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.i18n import i18n_service
from app.ai.llm_engine.context.section_wrappers import status_payload
from app.models.data_storage import StockHotRank


class SentimentSource:
    """Sentiment-adjacent source data kept for agent seeding."""

    @staticmethod
    def status_payload(data_status: str, **kwargs: Any) -> Dict[str, Any]:
        return status_payload(data_status, **kwargs)

    def _get_hot_rank(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """
        获取个股最新的人气榜和飙升榜排名信息
        Get the latest Hot Rank and Rising Rank information for a stock
        """
        hot = db.query(StockHotRank).filter(
            StockHotRank.stock_code == stock_code,
            StockHotRank.rank_type == 'hot'
        ).order_by(desc(StockHotRank.timestamp)).first()

        rising = db.query(StockHotRank).filter(
            StockHotRank.stock_code == stock_code,
            StockHotRank.rank_type == 'rising'
        ).order_by(desc(StockHotRank.timestamp)).first()

        result = {}
        if hot:
            rank = hot.rank or 0
            if rank <= 50:
                desc_text = i18n_service.get("context.hot_rank.top_focus")
            elif rank <= 100:
                desc_text = i18n_service.get("context.hot_rank.high_focus")
            elif rank <= 300:
                desc_text = i18n_service.get("context.hot_rank.moderate_focus")
            else:
                desc_text = i18n_service.get("context.hot_rank.low_focus")

            result['hot'] = {
                "rank": rank,
                "description": desc_text,
                "timestamp": str(hot.timestamp)
            }

        if rising:
            rank = rising.rank or 0
            if rank <= 50:
                desc_text = i18n_service.get("context.rising_rank.top_rising")
            elif rank <= 100:
                desc_text = i18n_service.get("context.rising_rank.high_rising")
            else:
                desc_text = i18n_service.get("context.rising_rank.warming_up")

            result['rising'] = {
                "rank": rank,
                "description": desc_text,
                "timestamp": str(rising.timestamp)
            }

        if result:
            result["data_status"] = "available"
        return result
