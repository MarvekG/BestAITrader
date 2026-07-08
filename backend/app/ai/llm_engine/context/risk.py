from datetime import datetime, timedelta
from typing import Dict, Any, List
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
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

    async def _get_pledge(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """获取质押信息，优先使用汇总数据，并辅以明细数据"""
        # 1. 获取最新的汇总数据 (Summary)
        summary_result = await db.execute(
            select(StockPledgeSummary)
            .where(StockPledgeSummary.stock_code == stock_code)
            .order_by(desc(StockPledgeSummary.trade_date))
        )
        summary = summary_result.scalars().first()

        # 2. 获取明细数据 (Pledge Detail) - 虽慢但包含质押日期等
        detail_result = await db.execute(
            select(StockPledge)
            .where(StockPledge.stock_code == stock_code)
            .order_by(desc(StockPledge.ann_date))
        )
        pledge_detail = detail_result.scalars().first()

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

    async def _get_insider(self, db: AsyncSession,
                     stock_code: str) -> List[Dict[str, Any]]:
        # Get recent insider trading (last 3 months/records)
        result = await db.execute(
            select(StockInsider)
            .where(StockInsider.stock_code == stock_code)
            .order_by(desc(StockInsider.trade_date))
            .limit(5)
        )
        insiders = result.scalars().all()

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

    async def _get_lockup(self, db: AsyncSession,
                    stock_code: str) -> List[Dict[str, Any]]:
        # Get upcoming releases (Window extended to 12 months)
        today = datetime.now().date()
        one_year_later = today + timedelta(days=365)

        result = await db.execute(
            select(StockRelease)
            .where(
                StockRelease.stock_code == stock_code,
                StockRelease.release_date >= today,
                StockRelease.release_date <= one_year_later,
            )
            .order_by(StockRelease.release_date)
            .limit(5)
        )
        releases = result.scalars().all()

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

    async def _get_lockup_summary(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """构建未来解禁聚合摘要。

        Args:
            db: 数据库会话。
            stock_code: 标准股票代码。

        Returns:
            未来 3 个月和 12 个月解禁规模摘要。
        """
        today = datetime.now().date()
        one_year_later = today + timedelta(days=365)
        result = await db.execute(
            select(StockRelease)
            .where(
                StockRelease.stock_code == stock_code,
                StockRelease.release_date >= today,
                StockRelease.release_date <= one_year_later,
            )
            .order_by(StockRelease.release_date)
        )
        releases = list(result.scalars().all())
        if not releases:
            return {"status": "missing", "has_upcoming_release": False}

        three_month_end = today + timedelta(days=90)
        releases_3m = [item for item in releases if item.release_date <= three_month_end]
        payload = {
            "status": "available",
            "has_upcoming_release": True,
            "next_release_date": str(releases[0].release_date),
            "days_to_next_release": (releases[0].release_date - today).days,
            "release_count_3m": len(releases_3m),
            "release_count_12m": len(releases),
            "total_release_ratio_3m": sum(item.ratio_to_total or 0 for item in releases_3m),
            "total_release_ratio_12m": sum(item.ratio_to_total or 0 for item in releases),
            "total_release_market_value_3m": sum(item.release_market_value or 0 for item in releases_3m),
            "total_release_market_value_12m": sum(item.release_market_value or 0 for item in releases),
            "has_material_release_3m": any((item.ratio_to_total or 0) >= 1 for item in releases_3m),
        }
        return format_payload_values("risk.lockup_release_summary", payload)

    async def _get_shareholder(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        result = await db.execute(
            select(StockShareholder)
            .where(StockShareholder.stock_code == stock_code)
            .order_by(desc(StockShareholder.end_date))
        )
        sh = result.scalars().first()

        if not sh:
            return {}

        payload = {
            "end_date": str(sh.end_date),
            "count": sh.holder_count,
            "change_ratio": sh.holder_count_change_ratio,
            "avg_shares": sh.avg_hold_shares
        }
        return format_payload_values("risk.shareholder", payload)

    async def _get_shareholder_trend(
            self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        分析股东户数变化趋势
        Analyze shareholder count trend (concentration signal)
        """
        # 获取最近4个季度的股东数据
        result = await db.execute(
            select(StockShareholder)
            .where(StockShareholder.stock_code == stock_code)
            .order_by(desc(StockShareholder.end_date))
            .limit(8)
        )
        shareholders = result.scalars().all()

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
        latest_date = shareholders[0].end_date
        oldest_date = shareholders[-1].end_date
        if oldest_count:
            total_change_pct = (latest_count - oldest_count) / oldest_count * 100
        else:
            total_change_pct = 0

        def cumulative_change_pct(offset: int) -> float | None:
            if len(shareholders) <= offset:
                return None
            base_count = shareholders[offset].holder_count
            if not latest_count or not base_count:
                return None
            return round((latest_count - base_count) / base_count * 100, 2)

        payload = {
            "data_sources": ["data.stock_shareholder"],
            "scope": f"{len(shareholders)} shareholder-count records from {oldest_date} to {latest_date}",
            "start_date": str(oldest_date) if oldest_date else None,
            "end_date": str(latest_date) if latest_date else None,
            "trend_signal": trend_signal,
            "latest_holder_count": latest_count,
            "consecutive_decrease": consecutive_decrease,
            "consecutive_increase": consecutive_increase,
            "total_change_pct": round(total_change_pct, 2),
            "holder_count_change_from_2q_pct": cumulative_change_pct(2),
            "holder_count_change_from_4q_pct": cumulative_change_pct(4),
            "latest_quarter_change_pct": changes[0]["change_pct"] if changes else None,
            "concentration_bias": (
                "concentrating"
                if consecutive_decrease > 0
                else "dispersing" if consecutive_increase > 0 else "stable"
            ),
            "quarters_analyzed": len(changes),
            "recent_changes": changes[:4],  # 最近4个季度
            "change_bases": {
                "total_change_pct": f"latest_holder_count({latest_date}) vs oldest_holder_count({oldest_date})",
                "latest_quarter_change_pct": (
                    f"holder_count({shareholders[0].end_date}) vs holder_count({shareholders[1].end_date})"
                    if len(shareholders) >= 2 else "missing"
                ),
                "holder_count_change_from_2q_pct": (
                    f"holder_count({shareholders[0].end_date}) vs holder_count({shareholders[2].end_date})"
                    if len(shareholders) > 2 else "missing"
                ),
                "holder_count_change_from_4q_pct": (
                    f"holder_count({shareholders[0].end_date}) vs holder_count({shareholders[4].end_date})"
                    if len(shareholders) > 4 else "missing"
                ),
            },
        }
        return format_payload_values("risk.shareholder", payload)
