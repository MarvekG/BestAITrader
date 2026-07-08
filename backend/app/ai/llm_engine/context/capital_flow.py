from typing import Dict, Any, List, Sequence
from datetime import datetime, timedelta
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import i18n_service
from app.ai.llm_engine.context import constants as ctx_const
from app.ai.llm_engine.context.calculations import percent_change, to_float, value_n_records_ago
from app.data.metadata.field_units import format_payload_values
from app.ai.llm_engine.context.section_wrappers import status_payload
from app.models.data_storage import (
    StockMoneyFlow, NorthboundData, DragonTigerData, StockMargin, KlineData,
    StockBlockTrade, SectorMoneyFlow, StockBasic, StockShareholder
)


def _build_money_flow_trend_summary(flows: Sequence[StockMoneyFlow]) -> Dict[str, Any]:
    if not flows:
        return {"status": "missing"}

    ordered = sorted(flows, key=lambda flow: flow.trade_date)
    total_yuan = sum(flow.net_inflow_main or 0 for flow in ordered)
    count = len(ordered)
    inflow_days = sum(1 for flow in ordered if (flow.net_inflow_main or 0) > 0)
    outflow_days = sum(1 for flow in ordered if (flow.net_inflow_main or 0) < 0)
    flat_days = sum(1 for flow in ordered if (flow.net_inflow_main or 0) == 0)
    if total_yuan > 0:
        net_flow_bias = "positive"
    elif total_yuan < 0:
        net_flow_bias = "negative"
    else:
        net_flow_bias = "flat"
    recent = list(reversed(ordered))

    def streak(sign: int) -> int:
        count = 0
        for flow in recent:
            value = flow.net_inflow_main or 0
            if (sign > 0 and value > 0) or (sign < 0 and value < 0):
                count += 1
                continue
            break
        return count

    def max_streak(sign: int) -> int:
        longest = 0
        current = 0
        for flow in ordered:
            value = flow.net_inflow_main or 0
            if (sign > 0 and value > 0) or (sign < 0 and value < 0):
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        return longest

    def rolling_sum(days: int) -> float | None:
        if not recent:
            return None
        return sum(flow.net_inflow_main or 0 for flow in recent[:days])

    payload = {
        "status": "available",
        "data_sources": ["data.stock_money_flow"],
        "scope": f"{count} money-flow records from {ordered[0].trade_date} to {ordered[-1].trade_date}",
        "window_records": count,
        "start_date": str(ordered[0].trade_date),
        "end_date": str(ordered[-1].trade_date),
        "net_inflow_main_total": total_yuan,
        "net_inflow_main_daily_average": total_yuan / count,
        "inflow_days": inflow_days,
        "outflow_days": outflow_days,
        "flat_days": flat_days,
        "inflow_day_ratio": inflow_days / count * 100,
        "outflow_day_ratio": outflow_days / count * 100,
        "flat_day_ratio": flat_days / count * 100,
        "net_flow_bias": net_flow_bias,
        "latest_inflow_streak_days": streak(1),
        "latest_outflow_streak_days": streak(-1),
        "max_inflow_streak_days": max_streak(1),
        "max_outflow_streak_days": max_streak(-1),
        "net_inflow_main_3d": rolling_sum(3),
        "net_inflow_main_5d": rolling_sum(5),
        "net_inflow_main_10d": rolling_sum(10),
        "change_bases": {
            "net_inflow_main_total": f"sum from {ordered[0].trade_date} to {ordered[-1].trade_date}",
            "net_inflow_main_daily_average": f"average over {count} records from {ordered[0].trade_date} to {ordered[-1].trade_date}",
            "inflow_day_ratio": f"inflow_days / window_records from {ordered[0].trade_date} to {ordered[-1].trade_date}",
            "outflow_day_ratio": f"outflow_days / window_records from {ordered[0].trade_date} to {ordered[-1].trade_date}",
            "net_inflow_main_3d": "sum of latest 3 records ending at end_date",
            "net_inflow_main_5d": "sum of latest 5 records ending at end_date",
            "net_inflow_main_10d": "sum of latest 10 records ending at end_date",
        },
    }
    return format_payload_values("capital_flow.money_flow_trend_summary", payload)


def _build_margin_trend_summary_payload(
    margins: Sequence[StockMargin],
    klines: Sequence[KlineData] | None = None,
) -> Dict[str, Any]:
    if not margins:
        return {"status": "missing"}

    ordered_margins = sorted(margins, key=lambda item: item.trade_date)
    recent_margins = list(reversed(ordered_margins))
    latest = ordered_margins[-1]
    latest_balance = to_float(latest.margin_balance)
    peak = max(ordered_margins, key=lambda item: to_float(item.margin_balance) or float("-inf"))
    peak_balance = to_float(peak.margin_balance)

    ordered_klines = sorted(klines or [], key=lambda item: item.date)
    latest_close = to_float(ordered_klines[-1].close) if ordered_klines else None
    kline_start_date = ordered_klines[0].date if ordered_klines else None
    kline_end_date = ordered_klines[-1].date if ordered_klines else None
    peak_date_close = None
    for kline in ordered_klines:
        if kline.date == peak.trade_date:
            peak_date_close = to_float(kline.close)
            break

    def balance_change(offset: int) -> float | None:
        if offset <= 0:
            return None
        # N 日变化使用最新记录与 N 个交易日前收盘后的余额比较。
        return percent_change(latest_balance, value_n_records_ago(recent_margins, "margin_balance", offset - 1))

    def balance_change_base_date(offset: int) -> str:
        if offset <= 0 or len(recent_margins) < offset:
            return "missing"
        return str(recent_margins[offset - 1].trade_date)

    payload = {
        "status": "available",
        "data_sources": ["data.stock_margin_data", "data.kline_data"],
        "scope": (
            f"{len(ordered_margins)} margin records from {ordered_margins[0].trade_date} to {latest.trade_date}; "
            f"daily closes from {kline_start_date or 'missing'} to {kline_end_date or 'missing'}"
        ),
        "window_records": len(ordered_margins),
        "start_date": str(ordered_margins[0].trade_date),
        "end_date": str(latest.trade_date),
        "price_start_date": str(kline_start_date) if kline_start_date else None,
        "price_end_date": str(kline_end_date) if kline_end_date else None,
        "latest_margin_balance": latest_balance,
        "latest_margin_buy_amount": latest.margin_buy_amount,
        "latest_margin_repay_amount": latest.margin_repay_amount,
        "latest_short_balance": latest.short_balance,
        "latest_margin_short_balance": latest.margin_short_balance,
        "margin_balance_change_5d_pct": balance_change(5),
        "margin_balance_change_10d_pct": balance_change(10),
        "margin_balance_change_20d_pct": balance_change(20),
        "peak_margin_balance": peak_balance,
        "peak_margin_balance_date": str(peak.trade_date),
        "margin_balance_drawdown_from_peak_pct": percent_change(latest_balance, peak_balance),
        "price_change_since_margin_peak_pct": percent_change(latest_close, peak_date_close),
        "latest_price": latest_close,
        "price_at_margin_peak": peak_date_close,
        "leverage_pressure_bias": "unknown",
        "change_bases": {
            "margin_balance_change_5d_pct": f"latest_margin_balance({latest.trade_date}) vs margin_balance({balance_change_base_date(5)})",
            "margin_balance_change_10d_pct": f"latest_margin_balance({latest.trade_date}) vs margin_balance({balance_change_base_date(10)})",
            "margin_balance_change_20d_pct": f"latest_margin_balance({latest.trade_date}) vs margin_balance({balance_change_base_date(20)})",
            "margin_balance_drawdown_from_peak_pct": f"latest_margin_balance({latest.trade_date}) vs peak_margin_balance({peak.trade_date})",
            "price_change_since_margin_peak_pct": f"latest_close({kline_end_date or 'missing'}) vs close_at_margin_peak({peak.trade_date})",
        },
    }
    margin_drawdown = to_float(payload["margin_balance_drawdown_from_peak_pct"])
    price_drawdown = to_float(payload["price_change_since_margin_peak_pct"])
    if margin_drawdown is not None and price_drawdown is not None:
        if price_drawdown <= -10 and margin_drawdown >= price_drawdown / 2:
            payload["leverage_pressure_bias"] = "crowded_not_cleared"
        elif margin_drawdown <= price_drawdown:
            payload["leverage_pressure_bias"] = "deleveraging_faster_than_price"
        elif margin_drawdown < 0:
            payload["leverage_pressure_bias"] = "deleveraging_in_progress"
        else:
            payload["leverage_pressure_bias"] = "margin_expanding"
    return format_payload_values("capital_flow.margin_trend_summary", payload)


class CapitalFlowSource:
    """
    Builds context for Capital Flow Analyst.
    Fetches:
    - Main money flow (Inflow/Outflow of large orders)
    - Northbound fund flow (Smart Money?)
    - Dragon Tiger List (Institutional/Top Player activity)
    - Margin trading data (Leverage sentiment)
    """

    @staticmethod
    def status_payload(data_status: str, **kwargs: Any) -> Dict[str, Any]:
        return status_payload(data_status, **kwargs)

    async def _get_stock_name(self, db: AsyncSession, stock_code: str) -> str:
        result = await db.execute(select(StockBasic).where(StockBasic.stock_code == stock_code))
        stock = result.scalars().first()
        return stock.name if stock else "Unknown"

    async def _get_money_flow(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        # Get latest
        result = await db.execute(
            select(StockMoneyFlow)
            .where(StockMoneyFlow.stock_code == stock_code)
            .order_by(desc(StockMoneyFlow.trade_date))
        )
        flow = result.scalars().first()

        if not flow:
            return {}

        payload = {
            "date": str(flow.trade_date),
            "net_inflow_main": flow.net_inflow_main,  # Main = Large + Huge
            "net_inflow_retail": (flow.net_inflow_small or 0) + (flow.net_inflow_medium or 0),
            "net_inflow_ratio_main": flow.net_inflow_ratio_main,
            "net_inflow_huge": flow.net_inflow_huge,
            # 多日累计趋势 (高价值信号)
            "net_inflow_main_3d": flow.net_inflow_main_3d,
            "net_inflow_main_5d": flow.net_inflow_main_5d,
            "net_inflow_main_10d": flow.net_inflow_main_10d,
            "close_price": flow.close_price,
            "pct_chg": flow.change_pct,
        }
        return format_payload_values("capital_flow.money_flow", payload)

    async def _get_money_flow_trend(self, db: AsyncSession, stock_code: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取主力资金流向趋势 (最近N天)
        Get main money flow trend (Last N days)
        """
        result = await db.execute(
            select(StockMoneyFlow)
            .where(StockMoneyFlow.stock_code == stock_code)
            .order_by(desc(StockMoneyFlow.trade_date))
            .limit(limit)
        )
        flows = result.scalars().all()

        trend = []
        for f in flows:
            trend.append({
                "date": str(f.trade_date),
                "net_inflow_main": f.net_inflow_main,
                "net_inflow_ratio_main": f.net_inflow_ratio_main,
                "pct_chg": f.change_pct,
            })
        return format_payload_values("capital_flow.money_flow", trend)

    async def _get_money_flow_trend_summary(
        self,
        db: AsyncSession,
        stock_code: str,
        limit: int = 20,
    ) -> Dict[str, Any]:
        result = await db.execute(
            select(StockMoneyFlow)
            .where(StockMoneyFlow.stock_code == stock_code)
            .order_by(desc(StockMoneyFlow.trade_date))
            .limit(limit)
        )
        flows = result.scalars().all()
        return _build_money_flow_trend_summary(flows)

    async def _get_northbound(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        result = await db.execute(
            select(NorthboundData)
            .where(NorthboundData.stock_code == stock_code)
            .order_by(desc(NorthboundData.date))
        )
        nb = result.scalars().first()

        if not nb:
            return self.status_payload(
                "missing",
                status="Data Unavailable",
                scope="stock_specific",
                data_granularity="latest_record",
                message=i18n_service.get("context.northbound.unavailable"),
            )

        # Check data freshness
        from datetime import datetime, date
        data_date = nb.date
        days_diff = (date.today() - data_date).days
        warning = ""
        if days_diff > 30:
            warning = f" (Data as of {data_date}, potentially outdated)"

        payload = {
            "data_status": "available",
            "scope": "stock_specific",
            "data_granularity": "latest_record",
            "reference_status": "stale" if days_diff > 30 else "active",
            "age_days": days_diff,
            "date": str(nb.date),
            "hold_shares": nb.hold_shares,
            "hold_ratio": nb.hold_ratio,
            "net_buy_amount": nb.net_buy_amount,
            "net_buy_volume": nb.net_buy_volume,
            "warning": warning
        }
        return format_payload_values("capital_flow.northbound", payload)

    async def _get_dragon_tiger(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        # Get latest appearing on list
        result = await db.execute(
            select(DragonTigerData)
            .where(DragonTigerData.stock_code == stock_code)
            .order_by(desc(DragonTigerData.trade_date))
        )
        dt = result.scalars().first()

        if not dt:
            return self.status_payload(
                "missing",
                status="No Recent Record",
                message=i18n_service.get("context.dragon_tiger.no_record"),
            )

        from datetime import date
        days_diff = (date.today() - dt.trade_date).days
        if days_diff > 30:
            return self.status_payload(
                "stale",
                status="No Recent Record",
                last_record_date=str(dt.trade_date),
                message=f"Last appearance {days_diff} days ago",
            )

        payload = {
            "data_status": "available",
            "date": str(dt.trade_date),
            "reason": dt.listing_reason,
            "net_buy": dt.net_buy_amount,
            "buy_amount": dt.buy_amount,
            "sell_amount": dt.sell_amount,
            "turnover_rate": dt.turnover_rate,
            "price_change": dt.price_change_percent,
            "interpretation": dt.interpretation  # 机构解读
        }
        return format_payload_values("capital_flow.dragon_tiger", payload)

    async def _get_margin(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        result = await db.execute(
            select(StockMargin)
            .where(StockMargin.stock_code == stock_code)
            .order_by(desc(StockMargin.trade_date))
        )
        mg = result.scalars().first()

        if not mg:
            return self.status_payload("missing", status="Data Unavailable")

        payload = {
            "data_status": "available",
            "date": str(mg.trade_date),
            "rz_balance": mg.margin_balance,  # 融资余额
            "rz_buy": mg.margin_buy_amount,  # 融资买入
            "rq_balance": mg.short_balance,  # 融券余额
            "rq_sell": mg.short_sell_volume,  # 融券卖出量
        }
        return format_payload_values("capital_flow.margin", payload)

    async def _get_margin_trend_summary(
        self,
        db: AsyncSession,
        stock_code: str,
        limit: int = 60,
    ) -> Dict[str, Any]:
        """从两融和日 K 线源表确定性计算融资拥挤度摘要。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            limit: 参与计算的最近记录数。

        Returns:
            带单位的融资余额趋势、峰值回撤和价格对照摘要。
        """
        margin_result = await db.execute(
            select(StockMargin)
            .where(StockMargin.stock_code == stock_code)
            .order_by(desc(StockMargin.trade_date))
            .limit(limit)
        )
        margin_records = list(margin_result.scalars().all())
        latest_margin_date = max((item.trade_date for item in margin_records), default=None)
        kline_filters = [KlineData.stock_code == stock_code, KlineData.freq == 'D']
        if latest_margin_date is not None:
            kline_filters.append(KlineData.date <= latest_margin_date)
        kline_result = await db.execute(
            select(KlineData)
            .where(*kline_filters)
            .order_by(desc(KlineData.date))
            .limit(limit)
        )
        return _build_margin_trend_summary_payload(
            margin_records,
            list(kline_result.scalars().all()),
        )

    async def _get_block_trade(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """获取大宗交易数据（近 30 个自然日全量窗口 + 买方结构聚合）"""
        window_start = datetime.now().date() - timedelta(days=30)
        result = await db.execute(
            select(StockBlockTrade)
            .where(
                StockBlockTrade.stock_code == stock_code,
                StockBlockTrade.trade_date >= window_start,
            )
            .order_by(desc(StockBlockTrade.trade_date))
        )
        trades = result.scalars().all()
        if not trades:
            fallback_result = await db.execute(
                select(StockBlockTrade)
                .where(StockBlockTrade.stock_code == stock_code)
                .order_by(desc(StockBlockTrade.trade_date))
                .limit(10)
            )
            trades = fallback_result.scalars().all()

        if not trades:
            return self.status_payload(
                "missing",
                status="Data Unavailable",
                message="No recent block trade data found or data source unavailable.",
            )

        # 统计近期大宗交易情况
        total_volume = sum(t.volume or 0 for t in trades)
        total_amount = sum(t.amount or 0 for t in trades)
        avg_premium = sum(t.premium_rate or 0 for t in trades) / len(trades) if trades else 0

        # 分析买卖方营业部集中度
        buyers = {}
        sellers = {}
        for t in trades:
            if t.buyer:
                buyers[t.buyer] = buyers.get(t.buyer, 0) + (t.amount or 0)
            if t.seller:
                sellers[t.seller] = sellers.get(t.seller, 0) + (t.amount or 0)

        # 买方类型结构：机构专用 / 营业部 / 其他（基于窗口内全量成交额，避免抽样口径冲突）
        buyer_type_amounts = {"institution": 0.0, "branch": 0.0, "other": 0.0}
        for name, amt in buyers.items():
            if "机构专用" in name:
                buyer_type_amounts["institution"] += amt
            elif "营业部" in name or "证券" in name:
                buyer_type_amounts["branch"] += amt
            else:
                buyer_type_amounts["other"] += amt
        known_buyer_amount = sum(buyer_type_amounts.values())
        buyer_type_breakdown = {
            key: {
                "amount": amt,
                "ratio_pct": round(amt / known_buyer_amount * 100, 2) if known_buyer_amount else None,
            }
            for key, amt in buyer_type_amounts.items()
        }

        # 识别主要买方机构
        top_buyers = sorted(buyers.items(), key=lambda x: x[1], reverse=True)[:3]

        # 评估大宗交易意图 (基于平均折溢价率)
        if avg_premium < -5:
            trade_intent = i18n_service.get(ctx_const.BLOCK_TRADE_INTENT_HIGH_DISCOUNT)
        elif avg_premium < -2:
            trade_intent = i18n_service.get(ctx_const.BLOCK_TRADE_INTENT_LOW_DISCOUNT)
        elif avg_premium > 5:
            trade_intent = i18n_service.get(ctx_const.BLOCK_TRADE_INTENT_HIGH_PREMIUM) # 极强看多信号
        elif avg_premium > 2:
            trade_intent = i18n_service.get(ctx_const.BLOCK_TRADE_INTENT_LOW_PREMIUM)
        else:
            trade_intent = i18n_service.get(ctx_const.BLOCK_TRADE_INTENT_NEUTRAL)

        # 评估交易活跃度
        if len(trades) >= 5:
            activity_level = i18n_service.get(ctx_const.BLOCK_TRADE_ACTIVITY_FREQUENT)
        elif len(trades) >= 3:
            activity_level = i18n_service.get(ctx_const.BLOCK_TRADE_ACTIVITY_MODERATE)
        else:
            activity_level = i18n_service.get(ctx_const.BLOCK_TRADE_ACTIVITY_LOW)

        trade_list = []
        for t in trades[:10]:  # 最近10笔大宗交易明细
            trade_list.append({
                "date": str(t.trade_date),
                "price": t.price,
                "volume": t.volume,
                "amount": t.amount,
                "premium_rate": t.premium_rate,
                "buyer": t.buyer if t.buyer else "",
                "seller": t.seller if t.seller else ""
            })

        payload = {
            "data_status": "available",
            "window_days": 30,
            "count": len(trades),
            "total_amount": total_amount,
            "avg_premium": avg_premium,
            "trade_intent": trade_intent,
            "activity_level": activity_level,
            "buyer_type_breakdown": buyer_type_breakdown,
            "top_buyers": [
                {"name": name, "amount": amt}
                for name, amt in top_buyers
            ],
            "recent_trades": trade_list
        }
        return format_payload_values("capital_flow.block_trade", payload)

    async def _get_sector_flow(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """获取所属板块的资金流向数据"""
        # 首先获取股票所属行业
        from app.models.data_storage import StockBasic
        stock_result = await db.execute(select(StockBasic).where(StockBasic.stock_code == stock_code))
        stock = stock_result.scalars().first()

        if not stock or not stock.industry:
            return self.status_payload("missing", status="Industry Info Unavailable")

        industry = stock.industry
        
        # 1. 尝试直接匹配
        sector_result = await db.execute(
            select(SectorMoneyFlow)
            .where(SectorMoneyFlow.sector_name == industry)
            .order_by(desc(SectorMoneyFlow.trade_date))
        )
        sector_flow = sector_result.scalars().first()

        # 2. 如果直接匹配失败，尝试显式映射
        if not sector_flow:
            mapping = {
                '白酒': '酿酒行业',
                '地产': '房地产开发',
                '房地产': '房地产开发',
                '银行': '银行行业',
                '光伏': '光伏设备',
            }
            if industry in mapping:
                mapped_name = mapping[industry]
                mapped_result = await db.execute(
                    select(SectorMoneyFlow)
                    .where(SectorMoneyFlow.sector_name == mapped_name)
                    .order_by(desc(SectorMoneyFlow.trade_date))
                )
                sector_flow = mapped_result.scalars().first()

        # 3. 如果仍然失败，尝试模糊匹配
        if not sector_flow:
            try:
                all_sectors_result = await db.execute(select(SectorMoneyFlow.sector_name).distinct())
                all_sectors = all_sectors_result.all()
                all_names = [s[0] for s in all_sectors]
                from difflib import get_close_matches
                matches = get_close_matches(industry, all_names, n=1, cutoff=0.3)
                if matches:
                    fuzzy_result = await db.execute(
                        select(SectorMoneyFlow)
                        .where(SectorMoneyFlow.sector_name == matches[0])
                        .order_by(desc(SectorMoneyFlow.trade_date))
                    )
                    sector_flow = fuzzy_result.scalars().first()
            except Exception:
                pass

        if not sector_flow:
            return self.status_payload(
                "missing",
                status="Data Unavailable",
                sector_name=industry,
                message=f"No fund flow data for sector {industry}",
            )

        # 评估板块资金状态 (单位为元)
        net_inflow = sector_flow.net_inflow or 0
        if net_inflow > 1000000000:  # 10 亿
            flow_status = i18n_service.get(ctx_const.SECTOR_FLOW_HIGH_INFLOW)
        elif net_inflow > 100000000:   # 1 亿
            flow_status = i18n_service.get(ctx_const.SECTOR_FLOW_INFLOW)
        elif net_inflow < -1000000000: # -10 亿
            flow_status = i18n_service.get(ctx_const.SECTOR_FLOW_HIGH_OUTFLOW)
        elif net_inflow < -100000000:  # -1 亿
            flow_status = i18n_service.get(ctx_const.SECTOR_FLOW_OUTFLOW)
        else:
            flow_status = i18n_service.get(ctx_const.SECTOR_FLOW_BALANCED)

        # 评估个股与板块的联动关系 (需要结合个股资金流,这里先给出板块状态)
        # 实际上应该由 LLM 基于个股和板块的数据进行综合分析
        linkage_hint = i18n_service.get(ctx_const.LINKAGE_HINT)

        payload = {
            "data_status": "available",
            "sector_name": sector_flow.sector_name,
            "date": str(sector_flow.trade_date),
            "net_inflow": net_inflow,
            "net_inflow_rate": sector_flow.net_inflow_rate,
            "main_net_inflow": sector_flow.main_net_inflow if sector_flow.main_net_inflow else 0,
            "leading_stock": sector_flow.leading_stock,
            "flow_status": flow_status,
            "linkage_hint": linkage_hint
        }
        return format_payload_values("capital_flow.sector_flow", payload)

    async def _get_northbound_trend(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        分析北向资金连续变动趋势
        Analyze northbound fund continuous trend
        """
        # 获取最近20日北向资金数据
        result = await db.execute(
            select(NorthboundData)
            .where(NorthboundData.stock_code == stock_code)
            .order_by(desc(NorthboundData.date))
            .limit(20)
        )
        data = result.scalars().all()

        # Since 2024.08, detailed stock data is quarterly (disclosed 5 days after quarter end).
        # We compare the latest record with the previous quarterly record (usually > 60 days apart).
        if len(data) < 2:
            return self.status_payload("missing", status="Trend Unavailable (Insufficient Quarterly Data)")

        latest = data[0]
        prev_quarter = None
        for d in data[1:]:
            if (latest.date - d.date).days >= 60:
                prev_quarter = d
                break

        if not prev_quarter:
            return self.status_payload(
                "partial",
                status="Quarterly Comparison Unavailable",
                latest_hold_ratio=format_payload_values(
                    "capital_flow.northbound",
                    {"latest_hold_ratio": latest.hold_ratio},
                )["latest_hold_ratio"],
                update_date=str(latest.date),
            )

        # Calculate quarterly change
        hold_change_shares = (latest.hold_shares or 0) - (prev_quarter.hold_shares or 0)
        hold_ratio_change = (latest.hold_ratio or 0) - (prev_quarter.hold_ratio or 0)
        
        # Percentage change in holdings (could be large, e.g., -28.98%)
        hold_shares_growth_pct = 0
        if prev_quarter.hold_shares and prev_quarter.hold_shares > 0:
            hold_shares_growth_pct = (hold_change_shares / prev_quarter.hold_shares) * 100

        payload = {
            "data_status": "available",
            "period": "Quarterly",
            "latest_hold_ratio": latest.hold_ratio,
            "prev_hold_ratio": prev_quarter.hold_ratio,
            "ratio_change": hold_ratio_change,
            "hold_shares_growth_pct": hold_shares_growth_pct,
            "latest_update_date": str(latest.date),
            "prev_update_date": str(prev_quarter.date),
            "frequency_hint": "Individual stock holdings are disclosed quarterly since 2024.08"
        }
        return format_payload_values("capital_flow.northbound", payload)

    async def _analyze_dragon_tiger_effect(
            self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        分析龙虎榜历史效应
        Analyze dragon tiger list historical effect (post-event returns)
        """
        # 获取历史所有龙虎榜记录
        result = await db.execute(
            select(DragonTigerData)
            .where(DragonTigerData.stock_code == stock_code)
            .order_by(desc(DragonTigerData.trade_date))
            .limit(20)
        )
        records = result.scalars().all()

        if not records:
            return {}

        # 统计后5日涨幅表现
        post_5d_changes = [
            r.post_5_day_price_change_percent
            for r in records if r.post_5_day_price_change_percent is not None
        ]
        post_10d_changes = [
            r.post_10_day_price_change_percent
            for r in records if r.post_10_day_price_change_percent is not None
        ]

        if not post_5d_changes:
            return self.status_payload(
                "partial",
                historical_records=len(records),
                effect_data=i18n_service.get("context.dragon_tiger.no_post_data"),
            )

        # 计算正收益率 (成功率)
        positive_5d = len([x for x in post_5d_changes if x > 0])
        success_rate_5d = positive_5d / len(post_5d_changes) * 100

        # 计算平均涨幅
        avg_5d_change = sum(post_5d_changes) / len(post_5d_changes)
        avg_10d_change = sum(post_10d_changes) / len(post_10d_changes) if post_10d_changes else None

        # 最近一次龙虎榜效应
        latest = records[0]
        latest_effect = {
            "date": str(latest.trade_date),
            "reason": latest.listing_reason,
            "post_1d": latest.post_1_day_price_change_percent,
            "post_5d": latest.post_5_day_price_change_percent,
            "post_10d": latest.post_10_day_price_change_percent,
        }

        payload = {
            "data_status": "available",
            "historical_records": len(records),
            "success_rate_5d": success_rate_5d,  # 5日正收益率
            "avg_post_5d_change": avg_5d_change,  # 平均5日涨幅
            "avg_post_10d_change": avg_10d_change if avg_10d_change else None,
            "latest_effect": latest_effect
        }
        return format_payload_values("capital_flow.dragon_tiger", payload)

    async def _get_shareholder(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        获取股东人数及筹码分布趋势
        Get shareholder count and chip distribution trend
        """
        # 获取最近 5 期
        result = await db.execute(
            select(StockShareholder)
            .where(StockShareholder.stock_code == stock_code)
            .order_by(desc(StockShareholder.end_date))
            .limit(5)
        )
        records = result.scalars().all()

        if not records:
            return {}

        latest = records[0]
        
        # 计算户数变化趋势
        trend = i18n_service.t("capital_flow.trend_stable")
        if len(records) >= 2:
            if records[0].holder_count < records[1].holder_count:
                trend = i18n_service.t("capital_flow.trend_decreasing")
            elif records[0].holder_count > records[1].holder_count:
                trend = i18n_service.t("capital_flow.trend_increasing")
        
        history = []
        for r in records:
            history.append({
                "date": str(r.end_date),
                "holder_count": r.holder_count,
                "avg_hold_shares": r.avg_hold_shares,
                "change_ratio": r.holder_count_change_ratio,
            })

        payload = {
            "data_status": "available",
            "latest_count": latest.holder_count,
            "avg_hold_shares": latest.avg_hold_shares,
            "change_ratio": latest.holder_count_change_ratio,
            "trend": trend,
            "history": history
        }
        return format_payload_values("capital_flow.shareholder", payload)
