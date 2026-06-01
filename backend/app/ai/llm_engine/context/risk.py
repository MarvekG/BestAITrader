from typing import Dict, Any, List
from sqlalchemy import desc
from sqlalchemy.orm import Session
from app.core.i18n import i18n_service
from app.ai.llm_engine.context.section_wrappers import status_payload
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

    def _analyze_financial_risks(self, fin_ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        从财务上下文中提取风险预警信号
        Extract financial risk warning signals from financial context
        """
        def pick(data: Dict[str, Any], *keys: str) -> Any:
            for key in keys:
                if key in data and data.get(key) is not None:
                    return data.get(key)
            return None

        latest_indicator = fin_ctx.get("financial_indicator_latest", {}) or {}
        latest_balance_sheet = fin_ctx.get("balance_sheet_latest", {}) or {}
        latest_cashflow = fin_ctx.get("cashflow_statement_latest", {}) or {}

        latest = {}
        if isinstance(latest_indicator.get("data"), dict):
            latest.update(latest_indicator["data"])
        if isinstance(latest_balance_sheet.get("data"), dict):
            latest.update(latest_balance_sheet["data"])
        if isinstance(latest_cashflow.get("data"), dict):
            latest.update(latest_cashflow["data"])

        latest_meta = (
            latest_indicator.get("meta")
            or latest_balance_sheet.get("meta")
            or latest_cashflow.get("meta")
            or {}
        )

        if not latest:
            return {
                "data_status": "missing",
                "status": "DATA_MISSING",
                "data_scope": "latest_financial_snapshot",
            }

        # 1. 商誉风险 (Goodwill Risk)
        goodwill = pick(latest, "goodwill", "商誉") or 0
        total_assets = pick(latest, "total_assets", "资产总计") or 1  # 避免除零
        goodwill_ratio = round(goodwill / total_assets * 100, 2)

        # 2. 负债率 (Debt Ratio) - AkShare/"资产负债率"→debt_to_assets_ratio, Tushare/debt_to_assets→debt_to_assets_ratio
        raw_debt_ratio = pick(
            latest,
            "debt_to_assets_ratio",   # AkShare & Tushare 统一映射目标
            "debt_to_assets",         # Tushare 原始字段 (兜底)
            "debt_to_asset",
            "al_ratio",
            "资产负债率",
        )
        if raw_debt_ratio is not None:
            debt_ratio = float(raw_debt_ratio)
        else:
            # 手动计算: 总负债 / 总资产 * 100
            total_liab = pick(latest, "total_liabilities", "total_liab", "负债合计") or 0
            debt_ratio = (total_liab / total_assets * 100) if total_assets and total_assets > 1 else 0

        # 3. 经营现金流质量 (Earnings Quality - Cash Profit Ratio)
        # 优先级: 总量值(AkShare) > ocfps反推(Tushare) > ocf比率判断(Tushare)
        # 净利润: AkShare "归母净利润"→net_profit_attributable_to_parent | Tushare 无直接净利润 (fina_indicator 只有 recurring_profit)
        net_profit_raw = pick(
            latest,
            "net_profit_attributable_to_parent",   # AkShare 归母净利润
            "n_income_attr_p",                     # Tushare cashflow 表 (若有)
            "net_profit",                          # AkShare "净利润"
            "n_income",
            "归属于母公司所有者的净利润",
            "净利润",
            "归母净利润",
        )
        # 经营性现金流总量 (AkShare 有): 经营活动产生的现金流量净额→net_cash_flow_from_operating_activities
        ocf_absolute = pick(
            latest,
            "net_cash_flow_from_operating_activities",  # AkShare
            "n_cashflow_act",                           # Tushare cashflow 表
            "n_cash_flows_oper",
            "经营活动产生的现金流量净额",
        )

        cash_quality = 0
        ocf_signal = "DATA_MISSING"

        if ocf_absolute is not None and net_profit_raw:
            # 方案 A: 用绝对值计算 (最准确)
            net_profit_f = float(net_profit_raw)
            cash_quality = round(float(ocf_absolute) / net_profit_f, 2) if net_profit_f != 0 else 0
            ocf_signal = "AVAILABLE"
        else:
            # 方案 B: Tushare fina_indicator 只有 ocf_to_debt / ocfps 等衍生指标
            # 用 ocf_to_debt (经营现金流/总债务) 当作方向信号
            ocf_to_debt = pick(latest, "ocf_to_debt")
            ocfps = pick(latest, "ocfps", "每股经营现金流")
            total_share = pick(latest, "total_share", "capital", "实收资本(或股本)")

            if ocf_to_debt is not None:
                # ocf_to_debt < 0 说明现金流为负
                cash_quality = round(float(ocf_to_debt), 4)
                ocf_signal = "RATIO_PROXY"  # 非直接比率, 用债务比代替
            elif ocfps is not None and total_share:
                # 方案 C: ocfps × 总股本 反推总现金流
                ocf_est = float(ocfps) * float(total_share) * 10000  # 总股本单位万股
                net_profit_f = float(net_profit_raw) if net_profit_raw else 0
                cash_quality = round(ocf_est / net_profit_f, 2) if net_profit_f != 0 else 0
                ocf_signal = "ESTIMATED"   # 估算值

        # 4. 存贷双高判断 (Double High Risk - 疑似资金占用/造假)
        monetary_cap = float(pick(latest, "monetary_funds", "money_cap", "货币资金") or 0)
        st_borrow = float(pick(latest, "short_term_borrowing", "st_borrow", "短期借款") or 0)
        lt_borrow = float(pick(latest, "long_term_borrowing", "lt_borrow", "长期借款") or 0)
        total_debt = st_borrow + lt_borrow
        double_high = (
            "YES"
            if (monetary_cap / total_assets > 0.2 and total_debt / total_assets > 0.2)
            else "NO"
        )

        return {
            "data_status": "available",
            "status": "AVAILABLE",
            "data_scope": "latest_financial_snapshot",
            "goodwill_ratio": goodwill_ratio,
            "debt_ratio": round(debt_ratio, 2),
            "cash_profit_ratio": cash_quality,
            "ocf_signal": ocf_signal,   # 告知 AI 数据来源质量
            "double_high_risk": double_high,
            "latest_report_date": latest_meta.get("report_date") or latest_meta.get("报告期")
        }

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
            "ratio_total": summary.pledge_ratio if summary else (pledge_detail.pledge_ratio_to_total if pledge_detail else 0.0),
            "ratio_holder": pledge_detail.pledge_ratio_to_holder if pledge_detail else None,
            "start_date": str(pledge_detail.pledge_date) if pledge_detail else (str(summary.trade_date) if summary else "未知"),

            # 统计信息
            "total_shares": summary.pledge_shares if summary else None,
            "market_value": summary.pledge_market_value if summary else None,
            "pledge_count": summary.pledge_count if summary else None,

            # 爆仓风险分析 (明细表才有的字段)
            "pledge_price": pledge_detail.pledge_price if pledge_detail else None,
            "current_price": pledge_detail.current_price if pledge_detail else None,
            "liquidate_price": pledge_detail.liquidate_price if pledge_detail else None
        }
        return res

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
        return results

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
        return results

    def _get_shareholder(self, db: Session, stock_code: str) -> Dict[str, Any]:
        sh = db.query(StockShareholder).filter(
            StockShareholder.stock_code == stock_code
        ).order_by(desc(StockShareholder.end_date)).first()

        if not sh:
            return {}

        return {
            "end_date": str(sh.end_date),
            "count": sh.holder_count,
            "change_ratio": sh.holder_count_change_ratio,
            "avg_shares": sh.avg_hold_shares
        }

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

        return {
            "trend_signal": trend_signal,
            "consecutive_decrease": consecutive_decrease,
            "consecutive_increase": consecutive_increase,
            "total_change_pct": round(total_change_pct, 2),
            "quarters_analyzed": len(changes),
            "recent_changes": changes[:4]  # 最近4个季度
        }
