from typing import Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.i18n import i18n_service
from app.ai.llm_engine.context import constants as ctx_const
from app.data.metadata.field_units import format_payload_values
from app.ai.llm_engine.context.section_wrappers import status_payload
from app.models.data_storage import (
    StockMoneyFlow, NorthboundData, DragonTigerData, StockMargin,
    StockBlockTrade, SectorMoneyFlow, StockBasic, StockShareholder
)


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

    def _get_stock_name(self, db: Session, stock_code: str) -> str:
        stock = db.query(StockBasic).filter(StockBasic.stock_code == stock_code).first()
        return stock.name if stock else "Unknown"

    def _get_money_flow(self, db: Session, stock_code: str) -> Dict[str, Any]:
        # Get latest
        flow = db.query(StockMoneyFlow).filter(
            StockMoneyFlow.stock_code == stock_code
        ).order_by(desc(StockMoneyFlow.trade_date)).first()

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

    def _get_money_flow_trend(self, db: Session, stock_code: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取主力资金流向趋势 (最近N天)
        Get main money flow trend (Last N days)
        """
        flows = db.query(StockMoneyFlow).filter(
            StockMoneyFlow.stock_code == stock_code
        ).order_by(desc(StockMoneyFlow.trade_date)).limit(limit).all()

        trend = []
        for f in flows:
            trend.append({
                "date": str(f.trade_date),
                "net_inflow_main": f.net_inflow_main,
                "net_inflow_ratio_main": f.net_inflow_ratio_main,
                "pct_chg": f.change_pct,
            })
        return format_payload_values("capital_flow.money_flow", trend)

    def _get_northbound(self, db: Session, stock_code: str) -> Dict[str, Any]:
        nb = db.query(NorthboundData).filter(
            NorthboundData.stock_code == stock_code
        ).order_by(desc(NorthboundData.date)).first()

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

    def _get_dragon_tiger(self, db: Session, stock_code: str) -> Dict[str, Any]:
        # Get latest appearing on list
        dt = db.query(DragonTigerData).filter(
            DragonTigerData.stock_code == stock_code
        ).order_by(desc(DragonTigerData.trade_date)).first()

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

    def _get_margin(self, db: Session, stock_code: str) -> Dict[str, Any]:
        mg = db.query(StockMargin).filter(
            StockMargin.stock_code == stock_code
        ).order_by(desc(StockMargin.trade_date)).first()

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

    def _get_block_trade(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """获取大宗交易数据（近 30 个自然日全量窗口 + 买方结构聚合）"""
        window_start = datetime.now().date() - timedelta(days=30)
        trades = db.query(StockBlockTrade).filter(
            StockBlockTrade.stock_code == stock_code,
            StockBlockTrade.trade_date >= window_start,
        ).order_by(desc(StockBlockTrade.trade_date)).all()
        if not trades:
            trades = db.query(StockBlockTrade).filter(
                StockBlockTrade.stock_code == stock_code
            ).order_by(desc(StockBlockTrade.trade_date)).limit(10).all()

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

    def _get_sector_flow(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """获取所属板块的资金流向数据"""
        # 首先获取股票所属行业
        from app.models.data_storage import StockBasic
        stock = db.query(StockBasic).filter(StockBasic.stock_code == stock_code).first()

        if not stock or not stock.industry:
            return self.status_payload("missing", status="Industry Info Unavailable")

        industry = stock.industry
        
        # 1. 尝试直接匹配
        sector_flow = db.query(SectorMoneyFlow).filter(
            SectorMoneyFlow.sector_name == industry
        ).order_by(desc(SectorMoneyFlow.trade_date)).first()

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
                sector_flow = db.query(SectorMoneyFlow).filter(
                    SectorMoneyFlow.sector_name == mapped_name
                ).order_by(desc(SectorMoneyFlow.trade_date)).first()

        # 3. 如果仍然失败，尝试模糊匹配
        if not sector_flow:
            try:
                all_sectors = db.query(SectorMoneyFlow.sector_name).distinct().all()
                all_names = [s[0] for s in all_sectors]
                from difflib import get_close_matches
                matches = get_close_matches(industry, all_names, n=1, cutoff=0.3)
                if matches:
                    sector_flow = db.query(SectorMoneyFlow).filter(
                        SectorMoneyFlow.sector_name == matches[0]
                    ).order_by(desc(SectorMoneyFlow.trade_date)).first()
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

    def _get_northbound_trend(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """
        分析北向资金连续变动趋势
        Analyze northbound fund continuous trend
        """
        # 获取最近20日北向资金数据
        data = db.query(NorthboundData).filter(
            NorthboundData.stock_code == stock_code
        ).order_by(desc(NorthboundData.date)).limit(20).all()

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

    def _analyze_dragon_tiger_effect(
            self, db: Session, stock_code: str) -> Dict[str, Any]:
        """
        分析龙虎榜历史效应
        Analyze dragon tiger list historical effect (post-event returns)
        """
        # 获取历史所有龙虎榜记录
        records = db.query(DragonTigerData).filter(
            DragonTigerData.stock_code == stock_code
        ).order_by(desc(DragonTigerData.trade_date)).limit(20).all()

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

    def _get_shareholder(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """
        获取股东人数及筹码分布趋势
        Get shareholder count and chip distribution trend
        """
        # 获取最近 5 期
        records = db.query(StockShareholder).filter(
            StockShareholder.stock_code == stock_code
        ).order_by(desc(StockShareholder.end_date)).limit(5).all()

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
