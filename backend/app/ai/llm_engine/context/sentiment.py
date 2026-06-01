from typing import Dict, Any, List
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.core.i18n import i18n_service
from app.ai.llm_engine.context.section_wrappers import status_payload
from app.models.data_storage import (
    StockHotRank,
    StockInteractiveQA,
)


class SentimentSource:
    """Sentiment-adjacent source data kept for agent seeding."""

    @staticmethod
    def status_payload(data_status: str, **kwargs: Any) -> Dict[str, Any]:
        return status_payload(data_status, **kwargs)

    def _get_recent_interactive_qa(self, db: Session, stock_code: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取最近一段时间的互动问答，补充管理层表态与投资者关注点。
        """
        from datetime import datetime, timedelta

        cutoff = datetime.now() - timedelta(days=30)
        cutoff_date = cutoff.date()
        base_query = db.query(StockInteractiveQA).filter(
            StockInteractiveQA.stock_code == stock_code
        )
        order_by_clauses = (
            desc(StockInteractiveQA.answer_time),
            desc(StockInteractiveQA.question_time),
            desc(StockInteractiveQA.trade_date),
        )

        qa_list = base_query.filter(
            or_(
                StockInteractiveQA.answer_time >= cutoff,
                StockInteractiveQA.question_time >= cutoff,
                StockInteractiveQA.trade_date >= cutoff_date,
            )
        ).order_by(*order_by_clauses).limit(limit).all()

        if not qa_list:
            qa_list = base_query.order_by(*order_by_clauses).limit(limit).all()

        results = []
        for qa in qa_list:
            event_time = qa.answer_time or qa.question_time or qa.trade_date
            results.append({
                "date": str(event_time) if event_time else None,
                "question": qa.question,
                "answer": qa.answer,
            })
        return results

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
