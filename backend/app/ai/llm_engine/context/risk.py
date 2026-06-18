from typing import Dict, Any, List
from sqlalchemy import desc
from sqlalchemy.orm import Session
from app.core.i18n import i18n_service
from app.ai.llm_engine.context.section_wrappers import status_payload
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import (
    StockPledge, StockInsider, StockRelease, StockShareholder, StockPledgeSummary
)


class RiskSource:
    """
    Builds context for Risk Analyst.
    Fetches:
    - Stock pledge ratio
    - Insider trading (reduction)
    - Restricted shares release schedule
    - Shareholder count changes
    """

    @staticmethod
    def status_payload(data_status: str, **kwargs: Any) -> Dict[str, Any]:
        return status_payload(data_status, **kwargs)

    def _get_pledge(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """获取质押信息，优先使用汇总数据，并辅以明细数据"""
        # 1. 获取最新的汇总数据 (Summary)
        summary = db.query(StockPledgeSummary).filter(
            StockPledgeSummary.stock_code == stock_code
        ).order_by(desc(StockPledgeSummary.trade_date)).first()

        # 2. 获取明细数据 (Pledge Detail) - 虽慢但包含质押日期等
        pledge_detail = db.query(StockPledge).filter(
            StockPledge.stock_code == stock_code
        ).order_by(desc(StockPledge.ann_date)).first()

        if not summary and not pledge_detail:
            return {}

        res = {
            "pledgor": pledge_detail.pledgor_name if pledge_detail else "未知",
            "ratio_total": (
                summary.pledge_ratio
                if summary
                else (pledge_detail.pledge_ratio_to_total if pledge_detail else 0.0)
            ),
            "ratio_holder": pledge_detail.pledge_ratio_to_holder if pledge_detail else None,
            "start_date": (
                str(pledge_detail.pledge_date)
                if pledge_detail
                else (str(summary.trade_date) if summary else "未知")
            ),

            # 统计信息
            "total_shares": summary.pledge_shares if summary else None,
            "market_value": summary.pledge_market_value if summary else None,
            "pledge_count": summary.pledge_count if summary else None,

            # 爆仓风险分析 (明细表才有的字段)
            "pledge_price": pledge_detail.pledge_price if pledge_detail else None,
            "current_price": pledge_detail.current_price if pledge_detail else None,
            "liquidate_price": pledge_detail.liquidate_price if pledge_detail else None
        }
        return format_payload_values("risk.pledge", res)

    def _get_insider(self, db: Session,
                     stock_code: str) -> List[Dict[str, Any]]:
        # Get recent insider trading (last 3 months/records)
        insiders = db.query(StockInsider).filter(
            StockInsider.stock_code == stock_code
        ).order_by(desc(StockInsider.trade_date)).limit(5).all()

        results = []
        for i in insiders:
            results.append({
                "date": str(i.trade_date),
                "name": i.insider_name,
                "relationship": i.relationship,  # 关系(控股股东/董事)
                "type": i.change_type,
                "shares": i.change_shares,
                "ratio": i.change_ratio,
                "avg_price": i.change_avg_price,  # 减持均价
                "shares_after": i.shares_after_change  # 减持后剩余
            })
        return format_payload_values("risk.insider", results)

    def _get_lockup(self, db: Session,
                    stock_code: str) -> List[Dict[str, Any]]:
        # Get upcoming releases (Window extended to 12 months)
        from datetime import datetime, timedelta
        today = datetime.now().date()
        one_year_later = today + timedelta(days=365)

        releases = db.query(StockRelease).filter(
            StockRelease.stock_code == stock_code,
            StockRelease.release_date >= today,
            StockRelease.release_date <= one_year_later
        ).order_by(StockRelease.release_date).limit(5).all()

        results = []
        for r in releases:
            results.append({
                "date": str(r.release_date),
                "shares": r.release_shares,
                "ratio": r.ratio_to_total,
                "ratio_float": r.ratio_to_float,  # 占流通股比(更敏感)
                "release_type": r.release_type,  # 解禁类型
                "market_value": r.release_market_value  # 解禁市值
            })
        return format_payload_values("risk.lockup_release", results)

    def _get_shareholder(self, db: Session, stock_code: str) -> Dict[str, Any]:
        sh = db.query(StockShareholder).filter(
            StockShareholder.stock_code == stock_code
        ).order_by(desc(StockShareholder.end_date)).first()

        if not sh:
            return {}

        payload = {
            "end_date": str(sh.end_date),
            "count": sh.holder_count,
            "change_ratio": sh.holder_count_change_ratio,
            "avg_shares": sh.avg_hold_shares
        }
        return format_payload_values("risk.shareholder", payload)

    def _get_shareholder_trend(
            self, db: Session, stock_code: str) -> Dict[str, Any]:
        """
        分析股东户数变化趋势
        Analyze shareholder count trend (concentration signal)
        """
        # 获取最近4个季度的股东数据
        shareholders = db.query(StockShareholder).filter(
            StockShareholder.stock_code == stock_code
        ).order_by(desc(StockShareholder.end_date)).limit(8).all()

        if len(shareholders) < 2:
            return {}

        # 计算连续变化
        changes = []
        for i in range(len(shareholders) - 1):
            current = shareholders[i].holder_count
            previous = shareholders[i + 1].holder_count
            if current and previous:
                change_pct = (current - previous) / previous * 100
                changes.append({
                    "date": str(shareholders[i].end_date),
                    "count": current,
                    "change_pct": round(change_pct, 2)
                })

        if not changes:
            return {}

        # 判断趋势: 连续减少=筹码集中, 连续增加=筹码分散
        consecutive_decrease = 0
        consecutive_increase = 0
        for c in changes:
            if c["change_pct"] < 0:
                consecutive_decrease += 1
            else:
                break
        for c in changes:
            if c["change_pct"] > 0:
                consecutive_increase += 1
            else:
                break

        # 信号评估
        if consecutive_decrease >= 3:
            trend_signal = i18n_service.get("context.shareholder_trend.highly_concentrated")
        elif consecutive_decrease >= 2:
            trend_signal = i18n_service.get("context.shareholder_trend.concentrating")
        elif consecutive_increase >= 3:
            trend_signal = i18n_service.get("context.shareholder_trend.dispersing")
        elif consecutive_increase >= 2:
            trend_signal = i18n_service.get("context.shareholder_trend.slightly_dispersing")
        else:
            trend_signal = i18n_service.get("context.shareholder_trend.stable")

        # 计算累计变化
        latest_count = shareholders[0].holder_count or 0
        oldest_count = shareholders[-1].holder_count or latest_count
        if oldest_count:
            total_change_pct = (latest_count - oldest_count) / oldest_count * 100
        else:
            total_change_pct = 0

        payload = {
            "trend_signal": trend_signal,
            "consecutive_decrease": consecutive_decrease,
            "consecutive_increase": consecutive_increase,
            "total_change_pct": round(total_change_pct, 2),
            "quarters_analyzed": len(changes),
            "recent_changes": changes[:4]  # 最近4个季度
        }
        return format_payload_values("risk.shareholder", payload)
