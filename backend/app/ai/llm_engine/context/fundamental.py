from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import i18n_service
from app.ai.llm_engine.context import constants as ctx_const
from app.data.metadata.field_units import format_payload_values
from app.ai.llm_engine.context.section_wrappers import status_payload
from app.models.data_storage import (
    StockBasic, StockValuationHistory,
    StockTopHolders,
    StockFundHolding,
    IndustryData,
    NorthboundData, DragonTigerData,
    StockInsider, StockRelease, StockPledge,
    StockSEO, StockPledgeSummary,
    StockMargin,
    StockLimitDownPool
)


class FundamentalSource:
    """

    Builds context for Fundamental Analyst.
    Fetches:
    - Basic stock info
    - Financial indicators (Growth, Profitability)
    - Valuation metrics (PE, PB, History)
    """

    @staticmethod
    def _is_empty_context_value(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and value.strip().lower() in {"null", "none", "nan"}:
            return True
        return False

    @staticmethod
    def _drop_empty_context_values(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: FundamentalSource._drop_empty_context_values(v)
                for k, v in value.items()
                if not FundamentalSource._is_empty_context_value(v)
            }
        if isinstance(value, list):
            return [
                FundamentalSource._drop_empty_context_values(item)
                for item in value
                if not FundamentalSource._is_empty_context_value(item)
            ]
        return value

    @staticmethod
    def status_payload(data_status: str, **kwargs: Any) -> Dict[str, Any]:
        return status_payload(data_status, **kwargs)

    @staticmethod
    async def _get_latest_top_holder_rows(db: AsyncSession, stock_code: str) -> List[StockTopHolders]:
        latest_result = await db.execute(
            select(StockTopHolders.report_date)
            .where(StockTopHolders.stock_code == stock_code)
            .order_by(desc(StockTopHolders.report_date))
        )
        latest_report = latest_result.first()

        if not latest_report:
            return []

        report_date = latest_report[0]
        result = await db.execute(
            select(StockTopHolders)
            .where(
                StockTopHolders.stock_code == stock_code,
                StockTopHolders.report_date == report_date,
            )
            .order_by(
                StockTopHolders.holder_rank.asc().nullslast(),
                desc(StockTopHolders.hold_amount),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def _is_institutional_holder(holder: StockTopHolders) -> bool:
        holder_type = str(holder.holder_type or "").strip()
        holder_name = str(holder.holder_name or "").strip()

        institutional_type_keywords = (
            "机构", "基金", "证券", "保险", "信托", "银行", "资管",
            "法人", "qfii", "社保", "养老", "投资", "公司"
        )
        institutional_name_keywords = (
            "基金", "证券", "保险", "信托", "银行", "资管", "资产管理",
            "投资", "汇金", "证金", "社保", "养老", "中央结算", "香港中央结算",
            "有限公司", "股份有限公司", "有限合伙", "合伙企业", "公司"
        )

        lower_type = holder_type.lower()
        lower_name = holder_name.lower()
        if any(keyword in lower_type for keyword in institutional_type_keywords):
            return True
        if any(keyword.lower() in lower_name for keyword in institutional_name_keywords):
            return True
        return False

    @staticmethod
    def _normalize_holder_change_label(change_value: Any) -> str:
        if change_value is None:
            return ""

        text = str(change_value).strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            return ""

        try:
            numeric = float(text.replace(",", ""))
        except ValueError:
            return text

        if numeric > 0:
            return "增加"
        if numeric < 0:
            return "减少"
        return "不变"

    async def _get_basic_info(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        result = await db.execute(select(StockBasic).where(StockBasic.stock_code == stock_code))
        stock = result.scalars().first()
        if not stock:
            return {}
        payload = {
            "name": stock.name,
            "industry": stock.industry,
            "area": stock.area,
            "list_date": str(stock.list_date) if stock.list_date else None,
            "total_share": stock.total_share,
            "float_share": stock.float_share,
        }
        return payload

    async def _get_valuation(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        # Get latest valuation (from EM or other sources)
        result = await db.execute(
            select(StockValuationHistory)
            .where(StockValuationHistory.stock_code == stock_code)
            .order_by(desc(StockValuationHistory.data_date))
        )
        val = result.scalars().first()

        if not val:
            return {}

        payload = {
            "date": str(val.data_date),
            "pe_ttm": val.pe_ttm,
            "pb": val.pb,
            "ps": val.ps_ttm,
            "peg": val.peg,
            "dividend_yield": val.dividend_yield,
            "total_mv": val.total_market_value,
            "float_mv": val.circulating_market_value,
            "total_share": val.total_share,
            "float_share": val.float_share,
            "free_share": val.free_share,
        }
        return format_payload_values("fundamental.valuation", payload)

    async def _get_northbound_flow(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """北向资金最近 12 条记录，适合 LLM 判断外资情绪变化"""
        result = await db.execute(
            select(NorthboundData)
            .where(NorthboundData.stock_code == stock_code)
            .order_by(desc(NorthboundData.date))
            .limit(12)
        )
        records = result.scalars().all()

        if not records:
            return {}

        latest = records[0]
        previous = None
        for candidate in records[1:]:
            if latest.date and candidate.date and (latest.date - candidate.date).days >= 60:
                previous = candidate
                break
        if previous is None and len(records) > 1:
            previous = records[1]

        age_days = (datetime.now().date() - latest.date).days if latest.date else None
        latest_hold_ratio = latest.hold_ratio
        previous_hold_ratio = previous.hold_ratio if previous else None
        hold_ratio_change = (
            round((latest_hold_ratio or 0) - (previous_hold_ratio or 0), 4)
            if latest_hold_ratio is not None and previous_hold_ratio is not None
            else None
        )
        net_buy_amount = latest.net_buy_amount or 0

        if (
            hold_ratio_change is not None and hold_ratio_change >= 0.1
        ) or net_buy_amount >= 50_000_000:
            flow_label = "accumulating"
            foreign_sentiment_label = "positive"
        elif (
            hold_ratio_change is not None and hold_ratio_change <= -0.1
        ) or net_buy_amount <= -50_000_000:
            flow_label = "reducing"
            foreign_sentiment_label = "negative"
        elif hold_ratio_change is not None:
            flow_label = "stable"
            foreign_sentiment_label = "mixed"
        else:
            flow_label = "insufficient_history"
            foreign_sentiment_label = "unclear"

        risk_flags = []
        if age_days is not None and age_days > 120:
            risk_flags.append("Northbound holding data is stale relative to the current quarter")
        if foreign_sentiment_label == "negative":
            risk_flags.append("Foreign capital positioning is weakening")
        if latest.change_percent is not None and latest.change_percent <= -5 and net_buy_amount <= 0:
            risk_flags.append("Northbound flow did not absorb the recent selloff")
        if age_days is None:
            signal_weight = "unknown"
        elif age_days > 120:
            signal_weight = "downgraded_stale"
        elif age_days > 60:
            signal_weight = "downgraded_quarterly"
        else:
            signal_weight = "normal"

        latest_two_record_hold_ratio_change_pp = None
        if len(records) >= 2 and records[0].hold_ratio is not None and records[1].hold_ratio is not None:
            latest_two_record_hold_ratio_change_pp = round((records[0].hold_ratio - records[1].hold_ratio) * 100, 4)

        recent_records = []
        for record in records:
            recent_records.append({
                "date": str(record.date) if record.date else None,
                "hold_shares": record.hold_shares,
                "hold_value_10k_cny": (record.hold_value or 0) / 10000 if record.hold_value is not None else None,
                "hold_ratio_pct": record.hold_ratio,
                "close_price_cny": record.close_price,
                "change_percent": record.change_percent,
                "net_buy_volume": record.net_buy_volume,
                "net_buy_amount_10k_cny": (record.net_buy_amount or 0) / 10000 if record.net_buy_amount is not None else None,
                "hold_value_change_10k_cny": (record.hold_value_change or 0) / 10000 if record.hold_value_change is not None else None,
            })

        payload = {
            "overview": {
                "scope": "stock_specific",
                "window": "latest_12_records",
                "data_frequency": "northbound_holding_series",
                "latest_date": str(latest.date) if latest.date else None,
                "oldest_date": str(records[-1].date) if records[-1].date else None,
                "record_count": len(records),
                "previous_reference_date": str(previous.date) if previous and previous.date else None,
                "reference_status": "stale" if age_days is not None and age_days > 120 else "active",
                "age_days": age_days,
            },
            "latest_position": {
                "hold_shares": latest.hold_shares,
                "hold_value_10k_cny": (latest.hold_value or 0) / 10000 if latest.hold_value is not None else None,
                "hold_ratio_pct": latest_hold_ratio,
                "close_price_cny": latest.close_price,
                "change_percent": latest.change_percent,
            },
            "quarter_change": {
                "hold_ratio_change_pct": hold_ratio_change,
                "net_buy_volume": latest.net_buy_volume,
                "net_buy_amount_10k_cny": net_buy_amount / 10000 if latest.net_buy_amount is not None else None,
                "hold_value_change_10k_cny": (latest.hold_value_change or 0) / 10000 if latest.hold_value_change is not None else None,
            },
            "signal": {
                "flow_label": flow_label,
                "foreign_sentiment_label": foreign_sentiment_label,
                "signal_weight": signal_weight,
                "latest_two_record_hold_ratio_change_pp": latest_two_record_hold_ratio_change_pp,
            },
            "risk_flags": risk_flags,
            "recent_records": recent_records,
        }
        return format_payload_values("fundamental.northbound_flow", payload)

    async def _get_market_wide_dragon_tiger_activity(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """龙虎榜近 3 日全市场快照，供 LLM 判断市场短线博弈热度。"""
        cutoff_3d = datetime.now().date() - timedelta(days=3)
        result = await db.execute(
            select(DragonTigerData)
            .where(DragonTigerData.trade_date >= cutoff_3d)
            .order_by(desc(DragonTigerData.trade_date))
        )
        records = result.scalars().all()

        if not records:
            return {}

        event_count = len(records)
        unique_trade_dates = len({record.trade_date for record in records if record.trade_date})
        unique_stock_count = len({record.stock_code for record in records if record.stock_code})
        cumulative_net_buy = sum(record.net_buy_amount or 0 for record in records)
        positive_events = sum(1 for record in records if (record.net_buy_amount or 0) > 0)
        negative_events = sum(1 for record in records if (record.net_buy_amount or 0) < 0)

        if cumulative_net_buy >= 50_000_000:
            sentiment_label = "market_buying_bias"
        elif cumulative_net_buy <= -50_000_000:
            sentiment_label = "market_selling_bias"
        elif positive_events > negative_events:
            sentiment_label = "buying_bias"
        elif negative_events > positive_events:
            sentiment_label = "selling_bias"
        else:
            sentiment_label = "mixed"

        if event_count >= 20 or unique_stock_count >= 15:
            activity_label = "frequent"
        elif event_count >= 8 or unique_stock_count >= 5:
            activity_label = "active"
        else:
            activity_label = "sporadic"

        risk_flags = []
        if unique_trade_dates >= 3:
            risk_flags.append("Repeated Dragon Tiger appearances indicate elevated short-term trading intensity")
        if sentiment_label == "market_selling_bias":
            risk_flags.append("Dragon Tiger activity shows persistent net selling")
        if any(abs(record.price_change_percent or 0) >= 9 for record in records):
            risk_flags.append("Recent Dragon Tiger listings coincided with high price volatility")

        latest_record = records[0]
        latest_trade_date = latest_record.trade_date if records else None
        payload = {
            "overview": {
                "window": "3day",
                "scope": "market_wide",
                "event_count": event_count,
                "unique_stock_count": unique_stock_count,
                "unique_trade_date_count": unique_trade_dates,
                "latest_trade_date": str(latest_trade_date) if latest_trade_date else None,
                "cumulative_net_buy_10k_cny": cumulative_net_buy / 10000,
            },
            "signal": {
                "activity_label": activity_label,
                "market_sentiment_label": sentiment_label,
            },
            "aggregates": {
                "positive_event_count": positive_events,
                "negative_event_count": negative_events,
            },
            "all_records": [
                {
                    "date": str(record.trade_date) if record.trade_date else None,
                    "stock_code": record.stock_code,
                    "stock_name": record.stock_name,
                    "listing_reason": record.listing_reason,
                    "net_buy_amount_10k_cny": (record.net_buy_amount or 0) / 10000 if record.net_buy_amount is not None else None,
                    "buy_amount_10k_cny": (record.buy_amount or 0) / 10000 if record.buy_amount is not None else None,
                    "sell_amount_10k_cny": (record.sell_amount or 0) / 10000 if record.sell_amount is not None else None,
                    "price_change_percent": record.price_change_percent,
                    "net_buy_ratio_pct": record.net_buy_ratio,
                    "post_1_day_price_change_percent": record.post_1_day_price_change_percent,
                    "post_5_day_price_change_percent": record.post_5_day_price_change_percent,
                }
                for record in records
            ],
            "risk_flags": risk_flags,
        }
        return format_payload_values("fundamental.dragon_tiger_activity", payload)

    async def _get_top_holders(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """获取十大股东持仓信息"""
        holders = (await self._get_latest_top_holder_rows(db, stock_code))[:10]

        if not holders:
            return {}

        # 统计信息
        latest_report_date = holders[0].report_date
        institutional_holders = [h for h in holders if self._is_institutional_holder(h)]

        # 计算持股集中度 (前10大股东持股比例之和)
        total_hold_ratio = sum(h.hold_ratio or 0 for h in holders)

        # 评估集中度标签
        if total_hold_ratio >= 60:
            concentration_level = i18n_service.get(ctx_const.CONCENTRATION_HIGH)
        elif total_hold_ratio >= 40:
            concentration_level = i18n_service.get(ctx_const.CONCENTRATION_MODERATE)
        else:
            concentration_level = i18n_service.get(ctx_const.CONCENTRATION_DISPERSED)

        # 统计变动情况
        increasing_count = len([
            h for h in holders if '增' in self._normalize_holder_change_label(h.change)
        ])
        decreasing_count = len([
            h for h in holders if '减' in self._normalize_holder_change_label(h.change)
        ])

        # 评估变动趋势
        if increasing_count > decreasing_count:
            change_trend = i18n_service.get(ctx_const.HOLDER_TREND_INCREASING)
        elif decreasing_count > increasing_count:
            change_trend = i18n_service.get(ctx_const.HOLDER_TREND_DECREASING)
        else:
            change_trend = i18n_service.get(ctx_const.HOLDER_TREND_STABLE)

        age_days = (datetime.now().date() - latest_report_date).days if latest_report_date else None
        is_stale = age_days is not None and age_days > 180

        holder_list = []
        for h in holders:
            holder_list.append({
                "name": h.holder_name,
                "type": h.holder_type,
                "hold_ratio_pct": round(h.hold_ratio, 2) if h.hold_ratio else 0,
                "change": h.change,
                "rank": h.holder_rank
            })

        payload = {
            "overview": {
                "report_date": str(latest_report_date) if latest_report_date else None,
                "reference_status": "stale" if is_stale else "active",
                "age_days": age_days,
                "institutional_count": len(institutional_holders),
                "holder_count": len(holders),
            },
            "concentration": {
                "total_hold_ratio_pct": round(total_hold_ratio, 2),
                "concentration_level": concentration_level,
            },
            "change": {
                "increasing_holder_count": increasing_count,
                "decreasing_holder_count": decreasing_count,
                "trend_label": change_trend,
            },
            "holders_latest": holder_list[:10]
        }
        return format_payload_values("fundamental.top_holders", payload)

    async def _get_fund_holding(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """获取公募基金持仓信息，含环比变化 | Get fund holdings with QoQ change"""
        # 获取最新两期报告期
        report_result = await db.execute(
            select(StockFundHolding.report_date)
            .where(StockFundHolding.stock_code == stock_code)
            .order_by(desc(StockFundHolding.report_date))
            .distinct()
            .limit(2)
        )
        report_dates = report_result.all()

        if not report_dates:
            return {}

        latest_date = report_dates[0][0]
        prev_date = report_dates[1][0] if len(report_dates) > 1 else None

        holdings_result = await db.execute(
            select(StockFundHolding).where(
                StockFundHolding.stock_code == stock_code,
                StockFundHolding.report_date == latest_date,
            )
        )
        holdings = holdings_result.scalars().all()
        sorted_holdings = sorted(
            holdings,
            key=lambda h: (
                h.hold_market_value or 0,
                h.hold_ratio_stock or 0,
                h.hold_ratio_fund or 0,
            ),
            reverse=True,
        )

        # 计算汇总数据
        total_hold_value = sum(h.hold_market_value or 0 for h in holdings)
        total_hold_ratio = sum(h.hold_ratio_stock or 0 for h in holdings)

        # 评估基金持仓强度
        if len(holdings) >= 50:
            intensity_level = i18n_service.get(ctx_const.FUND_INTENSITY_STAR)
        elif len(holdings) >= 20:
            intensity_level = i18n_service.get(ctx_const.FUND_INTENSITY_HIGH)
        elif len(holdings) >= 5:
            intensity_level = i18n_service.get(ctx_const.FUND_INTENSITY_MODERATE)
        else:
            intensity_level = i18n_service.get(ctx_const.FUND_INTENSITY_LOW)

        # 评估持仓集中度 (前5大基金占总持仓比例)
        top5_holdings = sorted_holdings[:5]
        top5_ratio = sum(h.hold_ratio_stock or 0 for h in top5_holdings)
        if total_hold_ratio > 0:
            top5_concentration = round((top5_ratio / total_hold_ratio) * 100, 2)
        else:
            top5_concentration = 0

        # 评估基金持仓确信度 (High Conviction)
        # 逻辑: 如果有基金持仓占净值比 > 8%, 说明是顶格配置
        high_conviction_funds = [h for h in holdings if (h.hold_ratio_fund or 0) > 8]
        medium_conviction_funds = [h for h in holdings if (h.hold_ratio_fund or 0) > 5]

        if high_conviction_funds:
            conviction_level = i18n_service.get(ctx_const.FUND_CONVICTION_HIGH)
        elif medium_conviction_funds:
            conviction_level = i18n_service.get(ctx_const.FUND_CONVICTION_MODERATE)
        else:
            conviction_level = i18n_service.get(ctx_const.FUND_CONVICTION_LOW)

        fund_list = []
        for h in top5_holdings:
            fund_list.append({
                "fund_name": h.fund_name,
                "fund_code": h.fund_code,
                "hold_value_10k_cny": round(h.hold_market_value / 10000, 2) if h.hold_market_value else 0,
                "hold_ratio_stock_pct": round(h.hold_ratio_stock, 2) if h.hold_ratio_stock else 0,
                "hold_ratio_fund_pct": round(h.hold_ratio_fund, 2) if h.hold_ratio_fund else 0,
            })

        result = {
            "overview": {
                "report_date": str(latest_date),
                "fund_count": len(holdings),
                "total_hold_value_10k_cny": round(total_hold_value / 10000, 2),
                "total_hold_ratio_pct": round(total_hold_ratio, 2),
                "intensity_level": intensity_level,
            },
            "concentration": {
                "top5_hold_ratio_pct": round(top5_ratio, 2),
                "top5_concentration_pct": top5_concentration,
            },
            "conviction": {
                "conviction_level": conviction_level,
                "high_conviction_fund_count": len(high_conviction_funds),
                "medium_conviction_fund_count": len(medium_conviction_funds),
            },
            "top_funds_latest": fund_list,
        }

        # 环比变化计算 (对比上期报告期) | QoQ change vs previous report period
        if prev_date:
            prev_result = await db.execute(
                select(StockFundHolding).where(
                    StockFundHolding.stock_code == stock_code,
                    StockFundHolding.report_date == prev_date,
                )
            )
            prev_holdings = prev_result.scalars().all()

            prev_count = len(prev_holdings)
            prev_ratio = sum(h.hold_ratio_stock or 0 for h in prev_holdings)
            prev_value = sum(h.hold_market_value or 0 for h in prev_holdings)

            count_change = len(holdings) - prev_count
            ratio_change = round(total_hold_ratio - prev_ratio, 4)
            value_change = round((total_hold_value - prev_value) / 10000, 2)

            # 生成环比信号
            if prev_count == 0 and len(holdings) > 0:
                qoq_signal = i18n_service.get("context.fund_holding.new_entry")
            elif len(holdings) == 0 and prev_count > 0:
                qoq_signal = i18n_service.get("context.fund_holding.cleared")
            elif ratio_change > 0.5:
                qoq_signal = i18n_service.get("context.fund_holding.increased")
            elif ratio_change < -0.5:
                qoq_signal = i18n_service.get("context.fund_holding.decreased")
            else:
                qoq_signal = i18n_service.get("context.fund_holding.stable")

            result["previous_report_delta"] = {
                "prev_report_date": str(prev_date),
                "fund_count_change": count_change,
                "hold_ratio_change_pct": ratio_change,
                "market_value_change_10k_cny": value_change,
                "market_value_change_note": "Market value change mixes position change and price move",
                "signal": qoq_signal
            }

        return format_payload_values("fundamental.fund_holding", result)

    async def _get_industry_rank(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        获取所属行业的排名和涨跌幅数据
        Get industry rank and change percent from IndustryData
        """
        stock_result = await db.execute(select(StockBasic).where(StockBasic.stock_code == stock_code))
        stock = stock_result.scalars().first()
        if not stock or not stock.industry:
            return {}

        industry_name = stock.industry
        industry_result = await db.execute(
            select(IndustryData)
            .where(IndustryData.board_name == industry_name)
            .order_by(
                desc(
                    func.coalesce(
                        IndustryData.updated_at,
                        IndustryData.timestamp,
                        IndustryData.created_at,
                    )
                )
            )
        )
        industry_info = industry_result.scalars().first()

        if not industry_info:
            return {
                "overview": {
                    "industry": industry_name,
                    "window": "latest",
                }
            }

        rising_count = industry_info.rising_stocks_count or 0
        falling_count = industry_info.falling_stocks_count or 0
        total_count = rising_count + falling_count
        advance_decline_ratio = round(rising_count / falling_count, 2) if falling_count else None

        if industry_info.rank is not None and industry_info.rank <= 10:
            strength_label = "leading"
        elif (industry_info.change_percent or 0) >= 2:
            strength_label = "strong"
        elif (industry_info.change_percent or 0) <= -2:
            strength_label = "weak"
        else:
            strength_label = "mixed"

        if total_count == 0:
            breadth_label = "unknown"
        elif rising_count >= max(1, falling_count * 2):
            breadth_label = "broadly_positive"
        elif falling_count >= max(1, rising_count * 2):
            breadth_label = "broadly_negative"
        else:
            breadth_label = "mixed"

        payload = {
            "overview": {
                "industry": industry_name,
                "window": "latest",
                "board_rank": industry_info.rank,
                "change_pct": round(industry_info.change_percent, 2) if industry_info.change_percent is not None else None,
                "latest_price": round(industry_info.latest_price, 2) if industry_info.latest_price is not None else None,
                "reference_time": industry_info.updated_at.isoformat() if industry_info.updated_at else (
                    industry_info.timestamp.isoformat() if industry_info.timestamp else None
                ),
            },
            "breadth": {
                "rising_stocks_count": rising_count,
                "falling_stocks_count": falling_count,
                "advance_decline_ratio": advance_decline_ratio,
                "breadth_label": breadth_label,
            },
            "leader": {
                "stock_name": industry_info.leading_stock_name,
                "change_pct": round(industry_info.leading_stock_change_percent, 2) if industry_info.leading_stock_change_percent is not None else None,
            },
            "signal": {
                "strength_label": strength_label,
                "breadth_label": breadth_label,
            },
            "market_cap": {
                "total_market_cap_10k_cny": round(industry_info.total_market_cap, 2) if industry_info.total_market_cap is not None else None,
            },
        }
        return format_payload_values("fundamental.industry_rank", payload)

    async def _get_insider_activity(self, db: AsyncSession, stock_code: str, months: int = 6) -> Dict[str, Any]:
        """
        获取最近董监高与大股东增减持记录
        Get recent insider trading records
        """
        cutoff_date = datetime.now().date() - timedelta(days=months * 30)
        result = await db.execute(
            select(StockInsider)
            .where(
                StockInsider.stock_code == stock_code,
                StockInsider.trade_date >= cutoff_date,
            )
            .order_by(desc(StockInsider.trade_date))
            .limit(10)
        )
        records = result.scalars().all()

        if not records:
            return {}

        activities = []
        net_change_shares = 0
        net_change_value = 0.0
        buy_count = 0
        sell_count = 0
        buy_shares = 0
        sell_shares = 0
        role_summary: Dict[str, Dict[str, Any]] = {}

        for r in records:
            shares = abs(r.change_shares or 0)
            avg_price = r.change_avg_price
            transaction_value = None
            if shares and avg_price is not None:
                transaction_value = round(shares * avg_price, 2)

            role_key = str(r.relationship or "未披露").strip() or "未披露"
            role_entry = role_summary.setdefault(
                role_key,
                {
                    "relationship": role_key,
                    "buy_count": 0,
                    "sell_count": 0,
                    "net_change_shares": 0,
                },
            )

            if r.change_type == '增持':
                net_change_shares += shares
                buy_shares += shares
                buy_count += 1
                role_entry["buy_count"] += 1
                role_entry["net_change_shares"] += shares
                if transaction_value is not None:
                    net_change_value += transaction_value
            else:
                net_change_shares -= shares
                sell_shares += shares
                sell_count += 1
                role_entry["sell_count"] += 1
                role_entry["net_change_shares"] -= shares
                if transaction_value is not None:
                    net_change_value -= transaction_value

            activities.append({
                "trade_date": str(r.trade_date) if r.trade_date else None,
                "ann_date": str(r.ann_date) if r.ann_date else None,
                "insider_name": r.insider_name,
                "relationship": r.relationship,
                "direction": r.change_type,
                "shares": shares,
                "avg_price_cny": round(avg_price, 2) if avg_price is not None else None,
                "transaction_value_cny": transaction_value,
                "change_ratio_pct": round(r.change_ratio, 4) if r.change_ratio is not None else None,
                "shares_after_change": r.shares_after_change,
                "ratio_after_change_pct": round(r.ratio_after_change, 4) if r.ratio_after_change is not None else None,
            })

        if net_change_shares > 0:
            sentiment_label = i18n_service.get("context.insider_sentiment.bullish")
            direction_label = "net_buying"
        elif net_change_shares < 0:
            sentiment_label = i18n_service.get("context.insider_sentiment.bearish")
            direction_label = "net_selling"
        else:
            sentiment_label = i18n_service.get("context.insider_sentiment.neutral")
            direction_label = "balanced"

        if sell_shares >= max(1, buy_shares * 2) and sell_count >= 2:
            intensity_label = "heavy_selling"
        elif buy_shares >= max(1, sell_shares * 2) and buy_count >= 2:
            intensity_label = "meaningful_buying"
        elif buy_count or sell_count:
            intensity_label = "mixed"
        else:
            intensity_label = "quiet"

        risk_flags = []
        if direction_label == "net_selling" and sell_count >= 2:
            risk_flags.append("Repeated insider selling within the window")
        if sell_shares > buy_shares and net_change_value < 0:
            risk_flags.append("Net insider flow is negative in both shares and cash value")

        role_breakdown = sorted(
            [
                {
                    "relationship": item["relationship"],
                    "buy_count": item["buy_count"],
                    "sell_count": item["sell_count"],
                    "net_change_shares": item["net_change_shares"],
                }
                for item in role_summary.values()
            ],
            key=lambda item: abs(item["net_change_shares"]),
            reverse=True,
        )

        payload = {
            "overview": {
                "window": f"{months}month",
                "record_count": len(records),
                "latest_trade_date": str(records[0].trade_date) if records[0].trade_date else None,
                "net_change_shares": net_change_shares,
                "net_change_value_cny": round(net_change_value, 2),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "sentiment_label": sentiment_label,
            },
            "signal": {
                "direction_label": direction_label,
                "intensity_label": intensity_label,
                "sentiment_label": sentiment_label,
            },
            "role_breakdown": role_breakdown,
            "recent_events": activities[:5],
            "risk_flags": risk_flags,
        }
        return format_payload_values("fundamental.insider_activity", payload)

    async def _get_lockup_release(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        获取限售股解禁日程
        Get lockup release schedule (past and future)
        """
        today = datetime.now().date()
        start_date = today - timedelta(days=90)
        end_date = today + timedelta(days=365)
        result = await db.execute(
            select(StockRelease)
            .where(
                StockRelease.stock_code == stock_code,
                StockRelease.release_date >= start_date,
                StockRelease.release_date <= end_date,
            )
            .order_by(StockRelease.release_date)
        )
        records = result.scalars().all()

        if not records:
            return {}

        recent_releases = []
        upcoming_releases = []

        for record in records:
            item = {
                "release_date": str(record.release_date) if record.release_date else None,
                "release_type": record.release_type,
                "release_shares": record.release_shares,
                "release_market_value_10k_cny": round(record.release_market_value, 2) if record.release_market_value is not None else None,
                "ratio_to_total_pct": round(record.ratio_to_total, 4) if record.ratio_to_total is not None else None,
                "ratio_to_float_pct": round(record.ratio_to_float, 4) if record.ratio_to_float is not None else None,
            }
            if record.release_date and record.release_date >= today:
                days_until = (record.release_date - today).days
                item["days_until_release"] = days_until
                upcoming_releases.append(item)
            else:
                days_since = (today - record.release_date).days if record.release_date else None
                item["days_since_release"] = days_since
                recent_releases.append(item)

        total_upcoming_ratio_to_float = round(
            sum(item["ratio_to_float_pct"] or 0 for item in upcoming_releases),
            2,
        )
        total_upcoming_market_value = round(
            sum(item["release_market_value_10k_cny"] or 0 for item in upcoming_releases),
            2,
        )

        if total_upcoming_ratio_to_float > 10:
            pressure_label = "severe"
        elif total_upcoming_ratio_to_float > 5:
            pressure_label = "elevated"
        elif total_upcoming_ratio_to_float > 2:
            pressure_label = "moderate"
        elif upcoming_releases:
            pressure_label = "limited"
        else:
            pressure_label = "none"

        risk_flags = []
        if upcoming_releases:
            next_release = upcoming_releases[0]
            if (next_release.get("days_until_release") or 0) <= 30 and (next_release.get("ratio_to_float_pct") or 0) >= 2:
                risk_flags.append("Near-term lockup release may pressure float supply")
        if total_upcoming_ratio_to_float >= 5:
            risk_flags.append("Upcoming lockup ratio is meaningful relative to float")

        payload = {
            "overview": {
                "window": "past90day_to_next12month",
                "recent_release_count": len(recent_releases),
                "upcoming_release_count": len(upcoming_releases),
                "next_release_date": upcoming_releases[0]["release_date"] if upcoming_releases else None,
                "total_upcoming_ratio_to_float_pct": total_upcoming_ratio_to_float,
                "total_upcoming_market_value_10k_cny": total_upcoming_market_value,
            },
            "signal": {
                "pressure_label": pressure_label,
            },
            "upcoming_releases": upcoming_releases[:5],
            "recent_releases": list(reversed(recent_releases[-3:])),
            "risk_flags": risk_flags,
        }
        return format_payload_values("fundamental.lockup_release", payload)

    async def _get_seo_history(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        获取定增/增发历史
        Get SEO history
        """
        today = datetime.now().date()
        recent_cutoff = today - timedelta(days=365 * 3)
        result = await db.execute(
            select(StockSEO)
            .where(StockSEO.stock_code == stock_code)
            .order_by(desc(func.coalesce(StockSEO.announce_date, StockSEO.issue_date)))
            .limit(5)
        )
        records = result.scalars().all()

        if not records:
            return {}

        offerings = []
        recent_count = 0
        for record in records:
            reference_date = record.announce_date or record.issue_date
            age_days = (today - reference_date).days if reference_date else None
            if reference_date and reference_date >= recent_cutoff:
                recent_count += 1

            offerings.append({
                "reference_date": str(reference_date) if reference_date else None,
                "announce_date": str(record.announce_date) if record.announce_date else None,
                "issue_date": str(record.issue_date) if record.issue_date else None,
                "age_days": age_days,
                "issue_price_cny": round(record.issue_price, 4) if record.issue_price is not None else None,
                "issue_volume_raw": round(record.issue_volume, 2) if record.issue_volume is not None else None,
                "raise_amount_raw": round(record.raise_amount, 2) if record.raise_amount is not None else None,
                "issue_object_summary": record.issue_object[:120] if record.issue_object else None,
                "lock_period": record.lock_period,
            })

        latest_reference_date = records[0].announce_date or records[0].issue_date
        latest_age_days = (today - latest_reference_date).days if latest_reference_date else None

        if recent_count >= 2:
            dilution_label = "repeated_equity_financing"
        elif recent_count == 1:
            dilution_label = "recent_equity_financing"
        else:
            dilution_label = "historical_equity_financing"

        if latest_age_days is not None and latest_age_days <= 365:
            recency_label = "recent"
        elif latest_age_days is not None and latest_age_days <= 365 * 3:
            recency_label = "moderate"
        else:
            recency_label = "historical"

        risk_flags = []
        if recent_count >= 2:
            risk_flags.append("Repeated SEO activity within the last 3 years")
        if latest_age_days is not None and latest_age_days <= 365:
            risk_flags.append("Recent equity financing may still weigh on dilution expectations")

        payload = {
            "overview": {
                "window": "latest5_alltime",
                "record_count": len(records),
                "recent_3year_count": recent_count,
                "latest_reference_date": str(latest_reference_date) if latest_reference_date else None,
            },
            "signal": {
                "dilution_label": dilution_label,
                "recency_label": recency_label,
            },
            "recent_offerings": offerings,
            "risk_flags": risk_flags,
        }
        return format_payload_values("fundamental.seo_history", payload)

    async def _get_pledge_info(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """
        获取股权质押风险信息
        Get stock pledge risk info
        """
        result = await db.execute(
            select(StockPledgeSummary)
            .where(StockPledgeSummary.stock_code == stock_code)
            .order_by(desc(StockPledgeSummary.trade_date))
        )
        latest_pledge = result.scalars().first()

        if not latest_pledge:
            return {}

        ratio = latest_pledge.pledge_ratio or 0
        risk_level = "Low"
        if ratio > 50:
            risk_level = "High"
        elif ratio > 20:
            risk_level = "Medium"

        return {
            "trade_date": str(latest_pledge.trade_date),
            "pledge_ratio": ratio,
            "pledge_shares": latest_pledge.pledge_shares,
            "pledge_market_value": latest_pledge.pledge_market_value,
            "pledge_count": latest_pledge.pledge_count,
            "risk_level": risk_level,
            "warning": i18n_service.get("context.pledge_risk.high_warning") if risk_level == "High" else None
        }

    async def _get_margin_analysis(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        """获取融资融券博弈分析"""
        margin_result = await db.execute(
            select(StockMargin)
            .where(StockMargin.stock_code == stock_code)
            .order_by(desc(StockMargin.trade_date))
            .limit(5)
        )
        margin_records = margin_result.scalars().all()

        if not margin_records:
            return {}

        latest_margin = margin_records[0]

        market_cap = 0
        valuation_result = await db.execute(
            select(StockValuationHistory)
            .where(StockValuationHistory.stock_code == stock_code)
            .order_by(desc(StockValuationHistory.data_date))
        )
        valuation = valuation_result.scalars().first()
        if valuation:
            market_cap = valuation.total_market_value or 0

        margin_balance = latest_margin.margin_balance or 0
        short_balance = latest_margin.short_balance or 0
        margin_ratio_pct = round((margin_balance / market_cap * 100), 2) if market_cap > 0 else None
        short_ratio_pct = round((short_balance / market_cap * 100), 2) if market_cap > 0 else None
        margin_short_ratio = round((margin_balance / short_balance), 2) if short_balance > 0 else None

        change_5d_pct = None
        change_5d_base_date = None
        if len(margin_records) >= 5:
            change_5d_base_date = margin_records[4].trade_date
            prev_balance = margin_records[4].margin_balance or 0
            if prev_balance > 0:
                change_5d_pct = round(((margin_balance - prev_balance) / prev_balance) * 100, 2)

        if margin_ratio_pct is not None and margin_ratio_pct > 10:
            leverage_label = "high_leverage"
            sentiment = i18n_service.get("context.margin_sentiment.high_leverage")
        else:
            leverage_label = "normal_leverage"
            sentiment = i18n_service.get("context.margin_sentiment.neutral")

        if margin_short_ratio is None:
            positioning_label = "long_only"
        elif margin_short_ratio > 20:
            positioning_label = "long_crowded"
            if leverage_label == "normal_leverage":
                sentiment = i18n_service.get("context.margin_sentiment.bullish_leverage")
        elif margin_short_ratio < 5:
            positioning_label = "hedged_or_short_pressure"
        else:
            positioning_label = "balanced"

        if change_5d_pct is not None and change_5d_pct >= 10:
            flow_label = "rising_fast"
        elif change_5d_pct is not None and change_5d_pct <= -10:
            flow_label = "falling_fast"
        elif change_5d_pct is not None:
            flow_label = "stable"
        else:
            flow_label = "insufficient_history"

        risk_flags = []
        if margin_ratio_pct is not None and margin_ratio_pct > 10:
            risk_flags.append("Margin balance is high relative to market cap")
        if margin_short_ratio is not None and margin_short_ratio > 20:
            risk_flags.append("Margin positioning is skewed heavily to the long side")
        if change_5d_pct is not None and change_5d_pct >= 10:
            risk_flags.append("Margin balance expanded quickly over the last 5 trading days")

        payload = {
            "data_sources": ["data.stock_margin_data", "data.stock_valuation_history"],
            "scope": (
                f"{len(margin_records)} margin records from "
                f"{margin_records[-1].trade_date if margin_records else 'missing'} to {latest_margin.trade_date}; "
                f"market cap dated {valuation.data_date if valuation and valuation.data_date else 'missing'}"
            ),
            "overview": {
                "trade_date": str(latest_margin.trade_date) if latest_margin.trade_date else None,
                "valuation_date": str(valuation.data_date) if valuation and valuation.data_date else None,
                "market_cap_cny": round(market_cap, 2) if market_cap > 0 else None,
                "margin_balance_cny": round(margin_balance, 2),
                "short_balance_cny": round(short_balance, 2),
                "margin_ratio_to_market_cap_pct": margin_ratio_pct,
                "short_ratio_to_market_cap_pct": short_ratio_pct,
                "sentiment": sentiment,
            },
            "trend": {
                "window": "5tradingday",
                "start_date": str(change_5d_base_date) if change_5d_base_date else None,
                "end_date": str(latest_margin.trade_date) if latest_margin.trade_date else None,
                "margin_balance_change_5d_pct": change_5d_pct,
                "margin_buy_amount_cny": round(latest_margin.margin_buy_amount, 2) if latest_margin.margin_buy_amount is not None else None,
                "margin_repay_amount_cny": round(latest_margin.margin_repay_amount, 2) if latest_margin.margin_repay_amount is not None else None,
                "change_bases": {
                    "margin_balance_change_5d_pct": (
                        f"margin_balance({latest_margin.trade_date}) vs margin_balance({change_5d_base_date})"
                        if change_5d_base_date else "missing"
                    ),
                },
            },
            "signal": {
                "leverage_label": leverage_label,
                "positioning_label": positioning_label,
                "flow_label": flow_label,
                "margin_short_ratio": margin_short_ratio,
            },
            "risk_flags": risk_flags,
        }
        return format_payload_values("fundamental.margin_analysis", payload)
