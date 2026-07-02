import asyncio
from collections.abc import Awaitable, Callable
from operator import add
from typing import Annotated, Dict, Any, TypedDict, List, Optional
from uuid import UUID
from langgraph.graph import StateGraph, END
from sqlalchemy import desc, or_, select

from app.ai.llm_routing import should_run_debate_agents_in_parallel
from app.ai.llm_engine.context import (
    AIContextService,
)
from app.core import database as database_module
from app.data.metadata.field_units import format_payload_values
from app.core.config import settings
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.core.utils.converters import safe_date, safe_float, safe_isoformat
from app.ai.llm_engine.agents.specialists import (
    FundamentalAgent, TechnicalAgent, CapitalFlowAgent, SentimentAgent, RiskAgent, NewsAgent, PolicyAgent
)
from app.ai.llm_engine.agents.strategic import (
    BullAgent, BearAgent, AggressiveAgent, ConservativeAgent, NeutralAgent, FactArbitrationAgent
)
from app.ai.llm_engine.agents.governance import (
    PortfolioManagerAgent
)
from app.ai.llm_engine.roles import (
    AGENT_NAME_NEWS_ANALYST,
    AGENT_NAME_POLICY_ANALYST,
    AGENT_NAME_SENTIMENT_ANALYST,
    AGENT_ROLE_AGGRESSIVE,
    AGENT_ROLE_BEAR,
    AGENT_ROLE_BULL,
    AGENT_ROLE_CAPITAL_FLOW,
    AGENT_ROLE_CONSERVATIVE,
    AGENT_ROLE_FACT_ARBITRATION,
    AGENT_ROLE_FUNDAMENTAL,
    AGENT_ROLE_NEUTRAL,
    AGENT_ROLE_NEWS_ANALYST,
    AGENT_ROLE_POLICY_ANALYST,
    AGENT_ROLE_PORTFOLIO_MANAGER,
    AGENT_ROLE_RISK,
    AGENT_ROLE_SENTIMENT,
    AGENT_ROLE_TECHNICAL,
    AGENT_NAME_FACT_ARBITRATOR,
)

logger = get_logger(__name__)


def _build_portfolio_field_descriptions() -> Dict[str, str]:
    """构建投资组合输入字段说明。

    Returns:
        随系统语言切换的字段说明，用于帮助 PM 理解 `portfolio_info` 中的持仓字段口径。
    """
    if str(settings.SYSTEM_LANGUAGE).lower().startswith("en"):
        return {
            "position.current_position": (
                "The target stock's current market-value weight in total account assets, shown with %. "
                "Convert it to a 0-1 ratio when comparing with target_position."
            ),
            "position.avg_cost": (
                "Current average holding cost, used to identify cost anchoring, "
                "stop-loss room, and profit-protection needs."
            ),
            "position.profit_loss": "Current unrealized profit/loss amount.",
            "position.profit_loss_pct": "Current unrealized profit/loss percentage, shown with %.",
            "position.available_shares": (
                "Current actual sellable quantity. If it is zero or insufficient, "
                "a sell decision should still be expressed as sell, with T+1 or sellable-share limits "
                "explained in the execution plan."
            ),
        }

    return {
        "position.current_position": "当前目标股票市值占账户总资产的比例，字段值直接带%；与 target_position 比较时换算为 0-1。",
        "position.avg_cost": "当前持仓平均成本，用于识别锚定成本、止损空间和盈亏保护需求。",
        "position.profit_loss": "当前持仓浮盈浮亏金额。",
        "position.profit_loss_pct": "当前持仓浮盈浮亏比例，字段值直接带%。",
        "position.available_shares": "当前真实可卖出数量；为 0 或不足时，卖出决策仍应表达为 sell，并在执行计划说明 T+1 或可卖限制。",
    }


async def _get_latest_position_price(db: Any, stock_code: str, fallback_price: float) -> tuple[float, str, str | None]:
    """获取用于 PM 持仓重估的最新可用价格。

    Args:
        db: 数据库会话。
        stock_code: 标准股票代码。
        fallback_price: 持仓表中的备选价格。

    Returns:
        ``(价格, 来源, 来源时间)``；优先使用新于最新日 K 的实时行情，其次最新日 K 收盘价，最后使用持仓快照价。
    """
    from app.models.data_storage import KlineData, StockRealtimeMarket

    latest_market_result = await db.execute(
        select(StockRealtimeMarket)
        .where(StockRealtimeMarket.stock_code == stock_code)
        .order_by(desc(StockRealtimeMarket.timestamp))
    )
    latest_market = latest_market_result.scalars().first()

    latest_kline_result = await db.execute(
        select(KlineData)
        .where(
            KlineData.stock_code == stock_code,
            KlineData.freq == "D",
        )
        .order_by(desc(KlineData.date))
    )
    latest_kline = latest_kline_result.scalars().first()

    close_price = None
    kline_date = None
    if latest_kline:
        close_price = safe_float(latest_kline.close)
        kline_date = safe_date(latest_kline.date)

    if latest_market:
        market_price = safe_float(latest_market.current_price)
        if market_price is not None and market_price > 0:
            market_date = safe_date(latest_market.timestamp)
            if close_price is None or close_price <= 0:
                return market_price, "realtime_market", safe_isoformat(latest_market.timestamp)
            if market_date is not None and (kline_date is None or market_date > kline_date):
                return market_price, "realtime_market", safe_isoformat(latest_market.timestamp)

    if close_price is not None and close_price > 0:
        return close_price, "daily_kline_close", safe_isoformat(latest_kline.date)

    return fallback_price, "position_snapshot", None


def _find_portfolio_overview_position(static_context: Dict[str, Any], stock_code: str) -> Dict[str, Any]:
    """从组合概览中读取目标股票的动态估值持仓。

    Args:
        static_context: AIContextService 构建的静态上下文。
        stock_code: 标准股票代码。

    Returns:
        组合概览中的目标股票持仓；不存在时返回空字典。
    """
    portfolio = static_context.get("portfolio") if isinstance(static_context, dict) else {}
    overview = portfolio.get("overview") if isinstance(portfolio, dict) else {}
    positions = overview.get("positions") if isinstance(overview, dict) else []
    if not isinstance(positions, list):
        return {}
    for item in positions:
        if isinstance(item, dict) and item.get("stock_code") == stock_code:
            return dict(item)
    return {}


def _build_portfolio_info_position(
    *,
    overview_position: Dict[str, Any],
    stock_code: str,
    total_shares: int,
    available_shares: int,
    avg_cost: float,
    fallback_price: float,
) -> Dict[str, Any]:
    """构建 PM 目标股票持仓输入，优先复用组合概览动态估值口径。

    Args:
        overview_position: `portfolio.overview.positions` 中的目标股票行。
        stock_code: 标准股票代码。
        total_shares: 当前持仓数量。
        available_shares: 当前可卖数量。
        avg_cost: 持仓均价。
        fallback_price: 组合概览缺失时使用的兜底价格。

    Returns:
        给 PM 使用的目标股票持仓字段；仓位、价格和盈亏优先来自组合概览。
    """
    position_payload = dict(overview_position) if overview_position else {}
    position_payload.update(
        {
            "stock_code": stock_code,
            "total_shares": total_shares,
            "available_shares": available_shares,
            "avg_cost": avg_cost,
        }
    )
    if not overview_position:
        position_payload.update(
            {
                "current_price": fallback_price,
                "current_position": 0,
                "profit_loss": 0,
                "profit_loss_pct": 0,
            }
        )
    return position_payload

# Define State


def _build_runtime_context(
    state: "AnalystState",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the runtime-only context passed as the second agent input."""
    runtime_context = dict(state.get("context", {}) or {})
    if extra:
        runtime_context.update(extra)
    return runtime_context


class AnalystState(TypedDict):
    stock_code: str
    trading_frequency: str
    trading_strategy: str
    session_id: Optional[UUID]  # 会话ID,用于数据持久化
    user_id: Optional[int]
    static_context: Dict[str, Any]
    context: Dict[str, Any]
    sentiment_report: Optional[str]
    news_report: Optional[str]
    policy_report: Optional[str]
    vertical_reports: Dict[str, str]
    strategic_reports: Dict[str, str]
    strategic_round_2_1_reports: Dict[str, str]  # Round 2.1 intermediate reports
    fact_arbitration_report: Optional[str]
    pm_decision: str
    post_trade_reflection: Dict[str, Any]
    errors: Annotated[List[str], add]


# 持久化辅助函数
async def persist_agent_report(
    session_id: Optional[UUID],
    stage: str,
    round_number: int,
    agent_name: str,
    agent_role: str,
    report_content: Any,
    prompt_input: str = ""
):
    """
    保存 Agent 报告到数据库并通过 WebSocket 推送

    Args:
        session_id: 会话ID
        stage: 辩论阶段
        round_number: 轮次
        agent_name: Agent 名称
        agent_role: Agent 角色
        report_content: 报告内容 (Markdown 格式)
    """
    if not session_id:
        logger.warning(f"Skipping persistence: session_id={session_id}")
        return

    from app.models.debate_message import DebateMessage
    from app.api.endpoints.debate_ws import send_debate_message
    from app.models.session import Session as SessionModel

    try:
        async with database_module.AsyncSessionLocal() as db:
            # 预先检查 Session 是否存在，避免外键冲突
            # Pre-check if session exists to avoid ForeignKeyViolation
            result = await db.execute(select(SessionModel.session_id).where(SessionModel.session_id == session_id))
            if result.scalar_one_or_none() is None:
                logger.warning(f"Session {session_id} not found, probably deleted. Aborting persistence.")
                return

            reasoning_val = report_content if isinstance(report_content, str) else str(report_content)

            # 创建数据库记录
            debate_msg = DebateMessage(
                session_id=session_id,
                stage=stage,
                round_number=round_number,
                agent_name=agent_name,
                agent_role=agent_role,
                reasoning=reasoning_val,
                prompt_input=prompt_input,
            )

            db.add(debate_msg)
            await db.commit()
            await db.refresh(debate_msg)
            message_payload = debate_msg.to_dict(exclude_prompt=True)

            logger.info(f"Saved {agent_role} report to database: {debate_msg.message_id}")

        # 推送到 WebSocket
        await send_debate_message(str(session_id), message_payload)

    except Exception:
        logger.exception("Persistence failed")


def _build_error_message(agent_name: str, exc: Exception) -> str:
    """Build a stable, user-facing agent execution error message."""
    return f"{agent_name} execution failed: {exc}"


def _halt_on_errors(state: AnalystState, next_node: str):
    """Route to END when the workflow has accumulated functional errors."""
    if state.get("errors"):
        return END
    return next_node


async def layer1_gate(_state: AnalystState) -> Dict[str, Any]:
    """Barrier node for the first analysis layer before strategic debate."""
    return {}


async def _run_agent_callables(callables: list[Callable[[], Awaitable[Any]]]) -> list[Any]:
    """Run agent calls according to the debate parallelism setting."""

    if not should_run_debate_agents_in_parallel():
        results = []
        for call in callables:
            results.append(await call())
        return results
    return await asyncio.gather(*(call() for call in callables))


# Define Nodes
async def fetch_context(state: AnalystState) -> Dict[str, Any]:
    stock_code = state["stock_code"]
    session_id = state.get("session_id")
    try:
        ai_context_snapshot = await AIContextService().build(stock_code)

        portfolio_info = {
            "account": {},
            "position": {},
            "field_descriptions": _build_portfolio_field_descriptions(),
        }
        user_id: Optional[int] = None

        if session_id:
            # 获取账户和持仓信息
            from app.models.session import Session as SessionModel
            from app.models.account import Account
            from app.models.position import Position

            async with database_module.AsyncSessionLocal() as db:
                session_result = await db.execute(select(SessionModel).where(SessionModel.session_id == session_id))
                session_obj = session_result.scalar_one_or_none()
                if session_obj:
                    user_id = session_obj.user_id
                    # 获取账户信息
                    account_result = await db.execute(select(Account).where(Account.user_id == session_obj.user_id))
                    account = account_result.scalar_one_or_none()
                    if account:
                        portfolio_overview = ai_context_snapshot.get("portfolio", {}).get("overview", {})
                        portfolio_summary = (
                            portfolio_overview.get("summary", {})
                            if isinstance(portfolio_overview, dict)
                            else {}
                        )
                        portfolio_info["account"] = {
                            "total_assets": portfolio_summary.get("total_assets", account.total_assets),
                            "available_cash": portfolio_summary.get("available_cash", account.available_cash),
                            "market_value": portfolio_summary.get("market_value", account.market_value),
                        }
                        portfolio_info["account"] = format_payload_values(
                            "portfolio.account",
                            portfolio_info["account"],
                        )

                        # 获取当前股票持仓信息
                        position_result = await db.execute(
                            select(Position).where(
                                Position.account_id == account.account_id,
                                Position.stock_code == stock_code,
                            )
                        )
                        position = position_result.scalar_one_or_none()
                        if position:
                            # PM 的价格、仓位和盈亏主口径来自 portfolio.overview，避免与组合快照不一致。
                            overview_position = _find_portfolio_overview_position(ai_context_snapshot, stock_code)
                            snapshot_price = safe_float(position.current_price) or 0
                            total_shares = int(position.total_shares or 0)
                            avg_cost = safe_float(position.avg_cost) or 0
                            if not overview_position:
                                logger.info(
                                    "Target position missing from portfolio overview; using execution-only fallback",
                                    extra={"stock_code": stock_code},
                                )
                            portfolio_info["position"] = _build_portfolio_info_position(
                                overview_position=overview_position,
                                stock_code=position.stock_code,
                                total_shares=total_shares,
                                available_shares=int(position.available_shares or 0),
                                avg_cost=avg_cost,
                                fallback_price=snapshot_price,
                            )
                            portfolio_info["position"] = format_payload_values(
                                "portfolio.position",
                                portfolio_info["position"],
                            )
        static_context = dict(state.get("static_context", {}) or {})
        static_context["data"] = ai_context_snapshot
        static_context["portfolio_info"] = portfolio_info
        return {
            "static_context": static_context,
            "context": {},
            "user_id": user_id,
        }
    except Exception as e:
        logger.exception("Context Fetch Error")
        return {"errors": [f"Context Fetch Error: {str(e)}"]}


async def news_analysis(state: AnalystState) -> Dict[str, Any]:
    """新闻分析师节点：对海量新闻进行预处理与深度归纳"""
    static_context = state.get("static_context", {})
    runtime_context = _build_runtime_context(state)

    session_id = state.get("session_id")
    from app.core.i18n import i18n_service

    # 初始化 NewsAgent
    agent = NewsAgent(state=state)
    try:
        report = await agent.run(static_context, runtime_context)

        # 持久化新闻分析报告
        await persist_agent_report(
            session_id=session_id,
            stage="news_analysis",
            round_number=0,
            agent_name=i18n_service.get("ai_analyst.agents.news_analyst", AGENT_NAME_NEWS_ANALYST),
            agent_role=AGENT_ROLE_NEWS_ANALYST,
            report_content=report,
            prompt_input=agent.last_prompt
        )

        return {"news_report": report}
    except Exception as e:
        logger.exception("%s execution failed", AGENT_NAME_NEWS_ANALYST)
        return {"errors": [_build_error_message(AGENT_NAME_NEWS_ANALYST, e)]}


async def policy_analysis(state: AnalystState) -> Dict[str, Any]:
    """政策分析师节点：聚焦中国政府网最新政策与政策解读"""
    static_context = state.get("static_context", {})
    if not static_context:
        logger.warning("policy_analysis: context is empty or missing")
        return {"errors": ["Policy analysis skipped because context is empty or missing."]}

    session_id = state.get("session_id")
    from app.core.i18n import i18n_service

    agent = PolicyAgent(state=state)
    try:
        runtime_context = _build_runtime_context(state)
        report = await agent.run(static_context, runtime_context)

        await persist_agent_report(
            session_id=session_id,
            stage="policy_analysis",
            round_number=0,
            agent_name=i18n_service.get("ai_analyst.agents.policy_analyst", AGENT_NAME_POLICY_ANALYST),
            agent_role=AGENT_ROLE_POLICY_ANALYST,
            report_content=report,
            prompt_input=agent.last_prompt
        )

        return {"policy_report": report}
    except Exception as e:
        logger.exception("%s execution failed", AGENT_NAME_POLICY_ANALYST)
        return {"errors": [_build_error_message(AGENT_NAME_POLICY_ANALYST, e)]}


def _build_layer1_reports(
    vertical_reports: Dict[str, str],
    sentiment_report: Optional[str],
    news_report: Optional[str],
    policy_report: Optional[str]
) -> Dict[str, str]:
    layer1_reports = dict(vertical_reports or {})
    if sentiment_report:
        layer1_reports[AGENT_ROLE_SENTIMENT] = sentiment_report
    if news_report:
        layer1_reports[AGENT_ROLE_NEWS_ANALYST] = news_report
    if policy_report:
        layer1_reports[AGENT_ROLE_POLICY_ANALYST] = policy_report
    return layer1_reports


async def _build_previous_execution_summary(db, session_id: UUID) -> Dict[str, Any]:
    """构建上一轮 PM 决策关联的最小交易执行摘要。

    Args:
        db: 数据库会话。
        session_id: 上一轮 Debate session ID。

    Returns:
        包含订单数、成交数、成交均价、成交数量、已实现盈亏和最近成交时间的摘要。
    """
    from app.models.order import Order
    from app.models.trade_record import TradeRecord

    orders_result = await db.execute(
        select(Order)
        .where(Order.session_id == session_id)
        .order_by(Order.created_at.asc(), Order.order_id.asc())
    )
    orders = list(orders_result.scalars().all())
    trades_result = await db.execute(
        select(TradeRecord)
        .where(TradeRecord.session_id == session_id)
        .order_by(TradeRecord.trade_time.asc(), TradeRecord.created_at.asc())
    )
    trades = list(trades_result.scalars().all())
    total_quantity = sum(int(item.quantity or 0) for item in trades)
    total_fill_amount = sum(
        int(item.quantity or 0) * float(item.fill_price)
        for item in trades
        if item.fill_price is not None and int(item.quantity or 0) > 0
    )
    return {
        "has_orders": bool(orders),
        "has_trades": bool(trades),
        "order_count": len(orders),
        "filled_order_count": len([item for item in orders if item.status == "filled"]),
        "avg_fill_price": total_fill_amount / total_quantity if total_quantity > 0 else None,
        "total_quantity": total_quantity,
        "realized_pnl": sum(float(item.realized_pnl or 0) for item in orders),
        "first_order_time": orders[0].created_at.isoformat() if orders and orders[0].created_at else None,
        "latest_order_time": orders[-1].created_at.isoformat() if orders and orders[-1].created_at else None,
        "first_trade_time": trades[0].trade_time.isoformat() if trades and trades[0].trade_time else None,
        "latest_trade_time": trades[-1].trade_time.isoformat() if trades and trades[-1].trade_time else None,
    }


def _build_pm_history_item(
    pm_record: Any,
    debate_msg: Any,
    session_obj: Any,
    execution_summary: Dict[str, Any],
) -> Dict[str, Any]:
    """构建同股历史 PM 决策摘要。

    Args:
        pm_record: PM 结构化决策记录。
        debate_msg: PM 决策消息记录。
        session_obj: 决策所属投研会话记录。
        execution_summary: 该会话关联的订单和成交摘要。

    Returns:
        面向 PM 的压缩历史决策摘要，不包含完整长报告。
    """
    return {
        "session_id": str(session_obj.session_id),
        "created_at": safe_isoformat(debate_msg.created_at),
        "trading_frequency": session_obj.trading_frequency,
        "trading_strategy": session_obj.trading_strategy,
        "confidence": float(pm_record.confidence_score or 0) / 100.0,
        "target_position": pm_record.target_position,
        "stop_loss": pm_record.stop_loss,
        "take_profit": pm_record.take_profit,
        "holding_horizon_days": pm_record.holding_horizon_days,
        "execution_summary": execution_summary,
    }


def _llm_order_id(order_id: Any) -> str | None:
    """生成给 LLM 使用的订单 ID。

    Args:
        order_id: 订单 UUID 或可转字符串的订单 ID。

    Returns:
        供交易工具识别的订单 ID；缺失时返回 None。
    """
    if not order_id:
        return None
    return str(order_id).replace("-", "")[:8]


def _build_order_history_item(order: Any, pm_by_session: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """构建同股历史订单摘要。

    Args:
        order: 订单数据库记录。
        pm_by_session: 按会话 ID 索引的 PM 决策摘要。

    Returns:
        包含实际方向、数量、成交价、盈亏和止损参考的订单摘要。
    """
    session_id = str(order.session_id) if order.session_id else None
    pm_snapshot = pm_by_session.get(session_id or "", {})
    return {
        "order_id": _llm_order_id(order.order_id),
        "session_id": session_id,
        "created_at": safe_isoformat(order.created_at),
        "filled_at": safe_isoformat(order.filled_at),
        "action": order.action,
        "order_type": order.order_type,
        "status": order.status,
        "price": safe_float(order.price),
        "shares": int(order.shares or 0),
        "filled_shares": int(order.filled_shares or 0),
        "avg_fill_price": safe_float(order.avg_fill_price),
        "realized_pnl": safe_float(order.realized_pnl),
        "source": order.source,
        "pm_stop_loss": pm_snapshot.get("stop_loss"),
        "pm_take_profit": pm_snapshot.get("take_profit"),
        "pm_target_position": pm_snapshot.get("target_position"),
    }


def _build_pending_order_item(order: Any) -> Dict[str, Any]:
    """构建 PM 当前待成交订单摘要。

    Args:
        order: 订单数据库记录。

    Returns:
        包含订单 ID 和委托信息的订单摘要。
    """
    return {
        "order_id": _llm_order_id(order.order_id),
        "session_id": str(order.session_id) if order.session_id else None,
        "stock_code": order.stock_code,
        "action": order.action,
        "order_type": order.order_type,
        "status": order.status,
        "price": safe_float(order.price),
        "shares": int(order.shares or 0),
        "filled_shares": int(order.filled_shares or 0),
        "created_at": safe_isoformat(order.created_at),
        "source": order.source,
    }


def _build_trade_history_item(trade: Any, order_by_id: Dict[str, Any]) -> Dict[str, Any]:
    """构建同股历史成交摘要。

    Args:
        trade: 成交记录。
        order_by_id: 按订单 ID 索引的订单记录，用于补充订单级已实现盈亏。

    Returns:
        包含实际成交方向、成交数量、成交价、费用和订单盈亏的摘要。
    """
    raw_order_id = str(trade.order_id) if trade.order_id else None
    order = order_by_id.get(raw_order_id or "")
    return {
        "trade_id": str(trade.trade_id),
        "order_id": _llm_order_id(trade.order_id),
        "session_id": str(trade.session_id) if trade.session_id else None,
        "trade_time": safe_isoformat(trade.trade_time),
        "action": trade.action,
        "quantity": int(trade.quantity or 0),
        "fill_price": safe_float(trade.fill_price),
        "commission": safe_float(trade.commission),
        "stamp_duty": safe_float(trade.stamp_duty),
        "transfer_fee": safe_float(trade.transfer_fee),
        "total_fees": safe_float(trade.total_fees),
        "net_amount": safe_float(trade.net_amount),
        "order_realized_pnl": safe_float(order.realized_pnl) if order else None,
    }


def _is_actual_sell_execution(item: Dict[str, Any]) -> bool:
    """判断订单摘要是否代表实际卖出成交。

    Args:
        item: `_build_order_history_item` 构建的订单摘要。

    Returns:
        订单方向为卖出且状态或成交数量表明已有实际成交时返回 True。
    """
    if str(item.get("action") or "").lower() != "sell":
        return False
    if int(item.get("filled_shares") or 0) > 0:
        return True
    return str(item.get("status") or "").lower() == "filled"


def _build_pm_review_focus() -> list[str]:
    """构建 PM 同股历史复盘关注点。

    Returns:
        随系统语言切换的复盘问题列表，供 PM 在报告中逐项回答。
    """
    return [
        i18n_service.t("ai_analyst.pm_review_focus.actual_trades"),
        i18n_service.t("ai_analyst.pm_review_focus.realized_pnl"),
        i18n_service.t("ai_analyst.pm_review_focus.latest_exit_reference"),
        i18n_service.t("ai_analyst.pm_review_focus.new_verifiable_edge"),
    ]


async def _get_same_stock_history(
    session_id: Optional[UUID],
    stock_code: str,
    *,
    decision_limit: int = 5,
    order_limit: int = 10,
    trade_limit: int = 10,
) -> Dict[str, Any]:
    """获取同一用户同一股票的压缩交易历史。

    Args:
        session_id: 当前投研会话 ID。
        stock_code: 股票代码。
        decision_limit: 最近 PM 决策摘要数量上限。
        order_limit: 最近订单数量上限。
        trade_limit: 最近成交数量上限。

    Returns:
        PM 可直接阅读的结构化历史，包括实际买卖、盈亏、止损参考和回补复盘提示。
    """
    if not session_id:
        return {}

    from app.models.account import Account
    from app.models.debate_message import DebateMessage
    from app.models.order import Order
    from app.models.pm_decision import PMDecisionRecord
    from app.models.session import Session as SessionModel
    from app.models.trade_record import TradeRecord

    async with database_module.AsyncSessionLocal() as db:
        try:
            current_result = await db.execute(select(SessionModel).where(SessionModel.session_id == session_id))
            current_session = current_result.scalar_one_or_none()
            if not current_session:
                return {}

            previous_result = await db.execute(select(SessionModel).where(
                SessionModel.user_id == current_session.user_id,
                SessionModel.stock_code == stock_code,
                SessionModel.session_id != session_id,
            ))
            previous_sessions = list(previous_result.scalars().all())
            previous_session_ids = [item.session_id for item in previous_sessions]
            account_result = await db.execute(select(Account.account_id).where(Account.user_id == current_session.user_id))
            account_ids = [
                item
                for item in account_result.scalars().all()
            ]

            pm_rows = []
            if previous_session_ids:
                pm_result = await db.execute(
                    select(PMDecisionRecord, DebateMessage, SessionModel)
                    .join(DebateMessage, DebateMessage.session_id == PMDecisionRecord.session_id)
                    .join(SessionModel, SessionModel.session_id == PMDecisionRecord.session_id)
                    .where(
                        PMDecisionRecord.session_id.in_(previous_session_ids),
                        DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER,
                    )
                    .order_by(DebateMessage.created_at.desc())
                    .limit(decision_limit)
                )
                pm_rows = pm_result.all()

            recent_pm_decisions = []
            for pm_record, debate_msg, session_obj in pm_rows:
                recent_pm_decisions.append(_build_pm_history_item(
                    pm_record,
                    debate_msg,
                    session_obj,
                    await _build_previous_execution_summary(db, session_obj.session_id),
                ))
            pm_by_session = {item["session_id"]: item for item in recent_pm_decisions}

            order_filters = [
                Order.stock_code == stock_code,
                or_(Order.session_id.is_(None), Order.session_id != session_id),
            ]
            ownership_filters = []
            if account_ids:
                ownership_filters.append(Order.account_id.in_(account_ids))
            if previous_session_ids:
                ownership_filters.append(Order.session_id.in_(previous_session_ids))
            if ownership_filters:
                order_filters.append(or_(*ownership_filters))
            else:
                order_filters.append(Order.session_id.in_([]))

            orders_result = await db.execute(
                select(Order).where(*order_filters).order_by(Order.created_at.desc()).limit(order_limit)
            )
            orders = list(orders_result.scalars().all())

            trade_filters = [
                TradeRecord.stock_code == stock_code,
                or_(TradeRecord.session_id.is_(None), TradeRecord.session_id != session_id),
            ]
            trade_ownership_filters = []
            if account_ids:
                trade_ownership_filters.append(TradeRecord.account_id.in_(account_ids))
            if previous_session_ids:
                trade_ownership_filters.append(TradeRecord.session_id.in_(previous_session_ids))
            if trade_ownership_filters:
                trade_filters.append(or_(*trade_ownership_filters))
            else:
                trade_filters.append(TradeRecord.session_id.in_([]))

            trades_result = await db.execute(
                select(TradeRecord).where(*trade_filters).order_by(
                    TradeRecord.trade_time.desc(),
                    TradeRecord.created_at.desc(),
                ).limit(trade_limit)
            )
            trades = list(trades_result.scalars().all())

            order_by_id = {str(order.order_id): order for order in orders}
            missing_trade_order_ids = [
                trade.order_id
                for trade in trades
                if trade.order_id and str(trade.order_id) not in order_by_id
            ]
            if missing_trade_order_ids:
                missing_orders_result = await db.execute(select(Order).where(Order.order_id.in_(missing_trade_order_ids)))
                for order in missing_orders_result.scalars().all():
                    order_by_id[str(order.order_id)] = order

            recent_orders = [_build_order_history_item(order, pm_by_session) for order in orders]
            recent_trades = [_build_trade_history_item(trade, order_by_id) for trade in trades]
            realized_pnls = [item["realized_pnl"] for item in recent_orders if item["realized_pnl"] is not None]
            loss_orders = [item for item in recent_orders if (item["realized_pnl"] or 0) < 0]
            executed_sell_orders = [item for item in recent_orders if _is_actual_sell_execution(item)]
            recent_realized_pnl = round(sum(realized_pnls), 4) if realized_pnls else 0.0
            has_recent_realized_loss = any(value < 0 for value in realized_pnls)

            return {
                "stock_code": stock_code,
                "lookback": {
                    "decision_limit": decision_limit,
                    "order_limit": order_limit,
                    "trade_limit": trade_limit,
                },
                "recent_execution_summary": {
                    "has_orders": bool(recent_orders),
                    "has_trades": bool(recent_trades),
                    "recent_order_count": len(recent_orders),
                    "recent_trade_count": len(recent_trades),
                    "recent_realized_pnl": recent_realized_pnl,
                    "has_recent_realized_loss": has_recent_realized_loss,
                    "latest_exit_order": executed_sell_orders[0] if executed_sell_orders else None,
                    "latest_loss_order": loss_orders[0] if loss_orders else None,
                    "new_edge_review_required": has_recent_realized_loss or bool(executed_sell_orders),
                    "pm_review_focus": _build_pm_review_focus(),
                },
                "recent_orders": recent_orders,
                "recent_trades": recent_trades,
                "recent_pm_decisions": recent_pm_decisions,
            }
        except Exception:
            logger.exception("Failed to fetch same-stock history")
            return {}


async def _get_pending_orders_for_pm(session_id: Optional[UUID], limit: int = 20) -> list[dict[str, Any]]:
    """获取当前用户账户下待成交订单，供 PM 撤单或改挂参考。

    Args:
        session_id: 当前投研会话 ID。
        limit: 最多返回的待成交订单数量。

    Returns:
        待成交订单摘要列表，包含给 LLM 使用的订单 ID。
    """
    if not session_id:
        return []

    from app.models.account import Account
    from app.models.order import Order
    from app.models.session import Session as SessionModel

    async with database_module.AsyncSessionLocal() as db:
        try:
            current_result = await db.execute(select(SessionModel).where(SessionModel.session_id == session_id))
            current_session = current_result.scalar_one_or_none()
            if not current_session:
                return []

            account_result = await db.execute(select(Account).where(Account.user_id == current_session.user_id))
            account = account_result.scalar_one_or_none()
            if not account:
                return []

            orders_result = await db.execute(
                select(Order)
                .where(
                    Order.account_id == account.account_id,
                    Order.status == "pending",
                )
                .order_by(Order.created_at.desc(), Order.order_id.desc())
                .limit(limit)
            )
            orders = orders_result.scalars().all()
            return [_build_pending_order_item(order) for order in orders]
        except Exception:
            logger.exception("Failed to fetch PM pending orders")
            return []


async def _get_previous_pm_decision(
    session_id: Optional[UUID],
    stock_code: str
) -> Dict[str, Any]:
    """Fetch the latest prior PM decision for the same user and stock."""
    if not session_id:
        return {}

    from app.models.session import Session as SessionModel
    from app.models.debate_message import DebateMessage
    from app.models.pm_decision import PMDecisionRecord

    async with database_module.AsyncSessionLocal() as db:
        try:
            current_result = await db.execute(select(SessionModel).where(SessionModel.session_id == session_id))
            current_session = current_result.scalar_one_or_none()
            if not current_session:
                return {}

            previous_result = await db.execute(
                select(PMDecisionRecord, DebateMessage, SessionModel)
                .join(DebateMessage, DebateMessage.session_id == PMDecisionRecord.session_id)
                .join(SessionModel, SessionModel.session_id == PMDecisionRecord.session_id)
                .where(
                    SessionModel.user_id == current_session.user_id,
                    SessionModel.stock_code == stock_code,
                    DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER,
                    PMDecisionRecord.session_id != session_id,
                )
                .order_by(DebateMessage.created_at.desc())
            )
            previous_msg = previous_result.first()
            if not previous_msg:
                return {}

            pm_record, debate_msg, prev_session = previous_msg
            execution_summary = await _build_previous_execution_summary(db, prev_session.session_id)
            return {
                "session_id": str(prev_session.session_id),
                "session_status": prev_session.status,
                "created_at": debate_msg.created_at.isoformat() if debate_msg.created_at else None,
                "trading_frequency": prev_session.trading_frequency,
                "trading_strategy": prev_session.trading_strategy,
                "confidence": float(pm_record.confidence_score or 0) / 100.0,
                "target_position": pm_record.target_position,
                "stop_loss": pm_record.stop_loss,
                "take_profit": pm_record.take_profit,
                "holding_horizon_days": pm_record.holding_horizon_days,
                "report_markdown": debate_msg.reasoning or "",
                "execution_summary": execution_summary,
            }
        except Exception:
            logger.exception("Failed to fetch previous PM decision")
            return {}


async def sentiment_analysis(state: AnalystState) -> Dict[str, Any]:
    """情绪分析师节点：基于已有情绪数据与搜索工具独立研判市场情绪"""
    static_context = state.get("static_context", {})
    if not static_context:
        logger.warning("sentiment_analysis: context is empty or missing")
        return {"errors": ["Sentiment analysis skipped because context is empty or missing."]}

    session_id = state.get("session_id")
    from app.core.i18n import i18n_service

    agent = SentimentAgent(state=state)
    try:
        runtime_context = _build_runtime_context(state)

        report = await agent.run(static_context, runtime_context)

        await persist_agent_report(
            session_id=session_id,
            stage="sentiment_analysis",
            round_number=0,
            agent_name=i18n_service.get("ai_analyst.agents.sentiment", AGENT_NAME_SENTIMENT_ANALYST),
            agent_role=AGENT_ROLE_SENTIMENT,
            report_content=report,
            prompt_input=agent.last_prompt
        )

        return {"sentiment_report": report}
    except Exception as e:
        logger.exception("%s execution failed", AGENT_NAME_SENTIMENT_ANALYST)
        return {"errors": [_build_error_message(AGENT_NAME_SENTIMENT_ANALYST, e)]}


async def vertical_analysis(state: AnalystState) -> Dict[str, Any]:
    static_context = state.get("static_context", {})
    if not static_context:
        logger.warning("vertical_analysis: context is empty or missing")
        return {"errors": ["Vertical analysis skipped because context is empty or missing."]}
    session_id = state.get("session_id")
    # Initialize agents
    agents = {
        AGENT_ROLE_FUNDAMENTAL: FundamentalAgent(
            state=state,
        ),
        AGENT_ROLE_TECHNICAL: TechnicalAgent(
            state=state,
        ),
        AGENT_ROLE_CAPITAL_FLOW: CapitalFlowAgent(
            state=state,
        ),
        AGENT_ROLE_RISK: RiskAgent(
            state=state,
        )
    }

    # Agent 名称映射 (使用国际化)
    from app.core.i18n import i18n_service
    agent_names = {
        AGENT_ROLE_FUNDAMENTAL: i18n_service.t("ai_analyst.agents.fundamental"),
        AGENT_ROLE_TECHNICAL: i18n_service.t("ai_analyst.agents.technical"),
        AGENT_ROLE_CAPITAL_FLOW: i18n_service.t("ai_analyst.agents.capital_flow"),
        AGENT_ROLE_RISK: i18n_service.t("ai_analyst.agents.risk")
    }

    # Define validation tasks
    async def run_agent(name, agent):
        try:
            runtime_context = _build_runtime_context(state)

            result = await agent.run(static_context, runtime_context)

            # 持久化报告
            await persist_agent_report(
                session_id=session_id,
                stage="vertical_analysis",
                round_number=0,
                agent_name=agent_names.get(name, name),
                agent_role=name,
                report_content=result,
                prompt_input=agent.last_prompt
            )

            return name, result, None
        except Exception as e:
            logger.error(f"Agent {name} execution failed: {e}", exc_info=True)
            return name, None, _build_error_message(agent_names.get(name, name), e)

    results = await _run_agent_callables([
        lambda name=name, agent=agent: run_agent(name, agent)
        for name, agent in agents.items()
    ])

    reports = {}
    errors: list[str] = []
    for name, result, error_message in results:
        if result:
            reports[name] = result
        if error_message:
            errors.append(error_message)

    update: Dict[str, Any] = {"vertical_reports": reports}
    if errors:
        update["errors"] = errors
    return update


async def strategic_round_1(state: AnalystState) -> Dict[str, Any]:
    static_context = state.get("static_context", {})
    layer1_reports = _build_layer1_reports(
        state.get("vertical_reports", {}),
        state.get("sentiment_report"),
        state.get("news_report"),
        state.get("policy_report")
    )
    session_id = state.get("session_id")
    # Round 1: Core Conflict (Bull vs Bear)
    # They see raw data and all layer-1 analyst outputs
    runtime_context = _build_runtime_context(state, {"layer1_analysis": layer1_reports})

    agents = {
        AGENT_ROLE_BULL: BullAgent(
            state=state,
        ),
        AGENT_ROLE_BEAR: BearAgent(
            state=state,
        )
    }

    from app.core.i18n import i18n_service
    agent_names = {
        AGENT_ROLE_BULL: i18n_service.t("ai_analyst.agents.bull"),
        AGENT_ROLE_BEAR: i18n_service.t("ai_analyst.agents.bear")
    }

    async def run_agent(name, agent):
        try:
            result = await agent.run(static_context, runtime_context)

            # 持久化报告
            await persist_agent_report(
                session_id=session_id,
                stage="strategic_round_1",
                round_number=1,
                agent_name=agent_names.get(name, name),
                agent_role=name,
                report_content=result,
                prompt_input=agent.last_prompt
            )

            return name, result, None
        except Exception as e:
            logger.error(f"Agent {name} execution failed: {e}", exc_info=True)
            return name, None, _build_error_message(agent_names.get(name, name), e)

    results = await _run_agent_callables([
        lambda name=name, agent=agent: run_agent(name, agent)
        for name, agent in agents.items()
    ])

    reports = {}
    errors: list[str] = []
    for name, result, error_message in results:
        if result:
            reports[name] = result
        if error_message:
            errors.append(error_message)

    # Initialize strategic_reports with Round 1 results
    update: Dict[str, Any] = {"strategic_reports": reports}
    if errors:
        update["errors"] = errors
    return update


async def strategic_round_2_1(state: AnalystState) -> Dict[str, Any]:
    static_context = state.get("static_context", {})
    layer1_reports = _build_layer1_reports(
        state.get("vertical_reports", {}),
        state.get("sentiment_report"),
        state.get("news_report"),
        state.get("policy_report")
    )
    session_id = state.get("session_id")
    # Get Bull/Bear reports from Round 1
    existing_reports = state.get("strategic_reports", {})

    # Round 2.1: Initial Cross-Examination (Aggressive, Conservative, Neutral)
    # They see raw data, all layer-1 analyst outputs, AND Round 1 arguments (Bull vs Bear)
    runtime_context = _build_runtime_context(
        state,
        {
            "layer1_analysis": layer1_reports,
            "debate_round_1": existing_reports,
        },
    )

    # Use specialized prompt template for Initial Cross-Ex if available
    # For now, they use their default persona to analyze the situation

    agents = {
        AGENT_ROLE_AGGRESSIVE: AggressiveAgent(
            state=state,
        ),
        AGENT_ROLE_CONSERVATIVE: ConservativeAgent(
            state=state,
        ),
        AGENT_ROLE_NEUTRAL: NeutralAgent(
            state=state,
        )
    }

    from app.core.i18n import i18n_service
    agent_names = {
        AGENT_ROLE_AGGRESSIVE: i18n_service.t("ai_analyst.agents.aggressive"),
        AGENT_ROLE_CONSERVATIVE: i18n_service.t("ai_analyst.agents.conservative"),
        AGENT_ROLE_NEUTRAL: i18n_service.t("ai_analyst.agents.neutral")
    }

    async def run_agent(name, agent):
        try:
            result = await agent.run(static_context, runtime_context)

            # Persist Round 2.1 Report
            await persist_agent_report(
                session_id=session_id,
                stage="strategic_round_2_1",  # Changed stage name
                round_number=2,
                agent_name=agent_names.get(name, name) + " (Round 2.1)",
                agent_role=name,
                report_content=result,
                prompt_input=agent.last_prompt
            )

            return name, result, None
        except Exception as e:
            logger.error(f"Agent {name} execution failed: {e}", exc_info=True)
            return name, None, _build_error_message(agent_names.get(name, name), e)

    results = await _run_agent_callables([
        lambda name=name, agent=agent: run_agent(name, agent)
        for name, agent in agents.items()
    ])

    round_2_1_reports = {}
    errors: list[str] = []
    for name, result, error_message in results:
        if result:
            round_2_1_reports[name] = result
        if error_message:
            errors.append(error_message)

    merged_reports = dict(existing_reports)
    merged_reports.update(round_2_1_reports)
    update: Dict[str, Any] = {
        "strategic_round_2_1_reports": round_2_1_reports,
        "strategic_reports": merged_reports,
    }
    if errors:
        update["errors"] = errors
    return update


async def strategic_round_2_rebuttal(state: AnalystState) -> Dict[str, Any]:
    """Compatibility wrapper for the removed second strategic rebuttal round."""
    merged_reports = dict(state.get("strategic_reports", {}))
    merged_reports.update(state.get("strategic_round_2_1_reports", {}))
    return {"strategic_reports": merged_reports}


async def fact_arbitration(state: AnalystState) -> Dict[str, Any]:
    """仲裁关键事实冲突，向 PM 提供 Markdown 摘要。

    Args:
        state: 当前 Debate 工作流状态，包含一层分析、战略层报告和运行上下文。

    Returns:
        包含 `fact_arbitration_report` 的状态更新；失败时返回工作流错误。
    """
    static_context = state.get("static_context", {})
    session_id = state.get("session_id")
    layer1_reports = _build_layer1_reports(
        state.get("vertical_reports", {}),
        state.get("sentiment_report"),
        state.get("news_report"),
        state.get("policy_report"),
    )
    runtime_context = _build_runtime_context(
        state,
        {
            "layer1_analysis": layer1_reports,
            "strategic_debate": state.get("strategic_reports", {}),
            "strategic_round_2_1": state.get("strategic_round_2_1_reports", {}),
        },
    )

    agent = FactArbitrationAgent(state=state)
    try:
        report = await agent.run(static_context, runtime_context)
        await persist_agent_report(
            session_id=session_id,
            stage="fact_arbitration",
            round_number=3,
            agent_name=i18n_service.get("ai_analyst.agents.fact_arbitrator", AGENT_NAME_FACT_ARBITRATOR),
            agent_role=AGENT_ROLE_FACT_ARBITRATION,
            report_content=report,
            prompt_input=agent.last_prompt,
        )
        return {"fact_arbitration_report": report}
    except Exception as e:
        logger.exception("%s execution failed", AGENT_NAME_FACT_ARBITRATOR)
        return {"errors": [_build_error_message(AGENT_NAME_FACT_ARBITRATOR, e)]}


async def portfolio_management(state: AnalystState) -> Dict[str, Any]:
    """执行 PM 最终裁决并持久化结果。

    Args:
        state: 当前 Debate 工作流状态，包含静态上下文、各 Agent 报告和会话信息。

    Returns:
        包含 `pm_decision` 的更新字典；若执行失败则包含 `errors`。
    """
    static_context = state.get("static_context", {})
    session_id = state.get("session_id")
    previous_pm_decision = await _get_previous_pm_decision(session_id, state["stock_code"])
    same_stock_history = await _get_same_stock_history(session_id, state["stock_code"])
    pending_orders = await _get_pending_orders_for_pm(session_id)
    vertical_reports = state.get("vertical_reports", {})
    runtime_context = _build_runtime_context(
        state,
        {
            "sentiment_report": state.get("sentiment_report", ""),
            "news_report": state.get("news_report", ""),
            "policy_report": state.get("policy_report", ""),
            "risk_report": vertical_reports.get(AGENT_ROLE_RISK, ""),
            "previous_pm_decision": previous_pm_decision,
            "same_stock_history": same_stock_history,
            "pending_orders": pending_orders,
            "vertical_views": vertical_reports,
            "strategic_debate": state.get("strategic_reports", {}),
            "fact_arbitration_report": state.get("fact_arbitration_report", ""),
        },
    )

    from app.core.i18n import i18n_service

    if not session_id:
        logger.error("session_id is missing in portfolio_management state")
        return {"errors": ["PM Error: session_id is required for trading operations"]}

    agent = PortfolioManagerAgent(state=state)
    try:
        decision = await agent.run(static_context, runtime_context)

        # 持久化 PM 决策
        await persist_agent_report(
            session_id=session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name=i18n_service.t("ai_analyst.agents.portfolio_manager"),
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            report_content=decision,
            prompt_input=agent.last_prompt
        )

        from app.ai.llm_engine.pm_decision_service import get_pm_decision_for_session

        decision_data = await get_pm_decision_for_session(session_id)

        return {"pm_decision": decision_data}
    except Exception as e:
        logger.exception("PM execution failed")
        return {"errors": [f"PM Error: {str(e)}"]}


# Build Graph


def should_continue(state: AnalystState):
    """Check if we should proceed to analysis or stop due to errors."""
    if state.get("errors"):
        return END
    if not should_run_debate_agents_in_parallel():
        return "news_analysis"
    return [
        "news_analysis",
        "policy_analysis",
        "sentiment_analysis",
        "vertical_analysis",
    ]


def _after_news_analysis(_state: AnalystState):
    if not should_run_debate_agents_in_parallel():
        return "policy_analysis"
    return "layer1_gate"


def _after_policy_analysis(_state: AnalystState):
    if not should_run_debate_agents_in_parallel():
        return "sentiment_analysis"
    return "layer1_gate"


def _after_sentiment_analysis(_state: AnalystState):
    if not should_run_debate_agents_in_parallel():
        return "vertical_analysis"
    return "layer1_gate"


def create_analyst_workflow():
    """创建 AI Analyst 工作流"""
    workflow = StateGraph(AnalystState)

    workflow.add_node("fetch_context", fetch_context)
    workflow.add_node("news_analysis", news_analysis)
    workflow.add_node("policy_analysis", policy_analysis)
    workflow.add_node("sentiment_analysis", sentiment_analysis)
    workflow.add_node("vertical_analysis", vertical_analysis)
    workflow.add_node("layer1_gate", layer1_gate)
    workflow.add_node("strategic_round_1", strategic_round_1)
    workflow.add_node("strategic_round_2_1", strategic_round_2_1)
    workflow.add_node("fact_arbitration", fact_arbitration)
    workflow.add_node("portfolio_management", portfolio_management)

    workflow.set_entry_point("fetch_context")

    # Use conditional edge instead of direct edge
    workflow.add_conditional_edges(
        "fetch_context",
        should_continue,
        {
            END: END,
            "news_analysis": "news_analysis",
            "policy_analysis": "policy_analysis",
            "sentiment_analysis": "sentiment_analysis",
            "vertical_analysis": "vertical_analysis",
        }
    )

    workflow.add_conditional_edges(
        "news_analysis",
        _after_news_analysis,
        {"policy_analysis": "policy_analysis", "layer1_gate": "layer1_gate"},
    )
    workflow.add_conditional_edges(
        "policy_analysis",
        _after_policy_analysis,
        {"sentiment_analysis": "sentiment_analysis", "layer1_gate": "layer1_gate"},
    )
    workflow.add_conditional_edges(
        "sentiment_analysis",
        _after_sentiment_analysis,
        {"vertical_analysis": "vertical_analysis", "layer1_gate": "layer1_gate"},
    )
    workflow.add_edge("vertical_analysis", "layer1_gate")
    workflow.add_conditional_edges(
        "layer1_gate",
        lambda state: _halt_on_errors(state, "strategic_round_1"),
        {
            END: END,
            "strategic_round_1": "strategic_round_1",
        },
    )
    workflow.add_conditional_edges(
        "strategic_round_1",
        lambda state: _halt_on_errors(state, "strategic_round_2_1"),
        {
            END: END,
            "strategic_round_2_1": "strategic_round_2_1",
        },
    )
    workflow.add_conditional_edges(
        "strategic_round_2_1",
        lambda state: _halt_on_errors(state, "fact_arbitration"),
        {
            END: END,
            "fact_arbitration": "fact_arbitration",
        },
    )
    workflow.add_conditional_edges(
        "fact_arbitration",
        lambda state: _halt_on_errors(state, "portfolio_management"),
        {
            END: END,
            "portfolio_management": "portfolio_management",
        },
    )
    workflow.add_edge("portfolio_management", END)

    return workflow.compile()
