import asyncio
from collections.abc import Awaitable, Callable
from operator import add
from typing import Annotated, Dict, Any, TypedDict, List, Optional
from uuid import UUID
from langgraph.graph import StateGraph, END

from app.ai.llm_routing import should_run_debate_agents_in_parallel
from app.ai.llm_engine.context import (
    AIContextService,
)
from app.core.logger import get_logger
from app.ai.llm_engine.agents.specialists import (
    FundamentalAgent, TechnicalAgent, CapitalFlowAgent, SentimentAgent, RiskAgent, NewsAgent, PolicyAgent
)
from app.ai.llm_engine.agents.strategic import (
    BullAgent, BearAgent, AggressiveAgent, ConservativeAgent, NeutralAgent
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
    AGENT_ROLE_FUNDAMENTAL,
    AGENT_ROLE_NEUTRAL,
    AGENT_ROLE_NEWS_ANALYST,
    AGENT_ROLE_POLICY_ANALYST,
    AGENT_ROLE_PORTFOLIO_MANAGER,
    AGENT_ROLE_RISK,
    AGENT_ROLE_SENTIMENT,
    AGENT_ROLE_TECHNICAL,
)

logger = get_logger(__name__)

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

    from app.core.database import SessionLocal
    from app.models.debate_message import DebateMessage
    from app.api.endpoints.debate_ws import send_debate_message

    from pydantic import BaseModel

    with SessionLocal() as db:
        try:
            from app.models.session import Session as SessionModel
            # 预先检查 Session 是否存在，避免外键冲突
            # Pre-check if session exists to avoid ForeignKeyViolation
            session_obj = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
            if not session_obj:
                logger.warning(f"Session {session_id} not found, probably deleted. Aborting persistence.")
                return

            # 提取结构化数据
            decision_val = ""
            confidence_val = 0.0
            reasoning_val = ""
            analysis_dict = {}

            if isinstance(report_content, str):
                reasoning_val = report_content
                analysis_dict = {"markdown": report_content}
            elif isinstance(report_content, BaseModel):
                # 如果是 Pydantic 模型
                analysis_dict = report_content.model_dump()
                # 尝试提取公共字段
                if hasattr(report_content, "decision"):
                    decision_val = str(getattr(report_content, "decision"))
                if hasattr(report_content, "action"):
                    decision_val = str(getattr(report_content, "action"))

                if hasattr(report_content, "confidence_score"):
                    confidence_val = float(getattr(report_content, "confidence_score")) / 100.0
                elif hasattr(report_content, "confidence"):
                    confidence_val = float(getattr(report_content, "confidence"))

                # report_markdown 是我们新加的，用于存储完整的 Markdown 报告
                if hasattr(report_content, "report_markdown"):
                    reasoning_val = getattr(report_content, "report_markdown")
                elif hasattr(report_content, "markdown_content"):
                    reasoning_val = getattr(report_content, "markdown_content")
                else:
                    reasoning_val = str(report_content)
            else:
                reasoning_val = str(report_content)
                analysis_dict = {"data": report_content}

            # 创建数据库记录
            debate_msg = DebateMessage(
                session_id=session_id,
                stage=stage,
                round_number=round_number,
                agent_name=agent_name,
                agent_role=agent_role,
                decision=decision_val,
                confidence=confidence_val,
                reasoning=reasoning_val,
                prompt_input=prompt_input,
                analysis=analysis_dict
            )

            db.add(debate_msg)
            db.commit()
            db.refresh(debate_msg)

            logger.info(f"✅ Saved {agent_role} report to database: {debate_msg.message_id}")

            # 推送到 WebSocket
            await send_debate_message(str(session_id), debate_msg.to_dict(exclude_prompt=True))

        except Exception:
            logger.exception("Persistence failed")
            db.rollback()


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

        portfolio_info = {"account": {}, "position": {}}
        user_id: Optional[int] = None

        if session_id:
            # 获取账户和持仓信息
            from app.core.database import SessionLocal
            from app.models.session import Session as SessionModel
            from app.models.account import Account
            from app.models.position import Position

            with SessionLocal() as db:
                session_obj = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
                if session_obj:
                    user_id = session_obj.user_id
                    # 获取账户信息
                    account = db.query(Account).filter(Account.user_id == session_obj.user_id).first()
                    if account:
                        portfolio_info["account"] = {
                            "total_assets": float(account.total_assets or 0),
                            "available_cash": float(account.available_cash or 0),
                            "market_value": float(account.market_value or 0)
                        }

                        # 获取当前股票持仓信息
                        position = db.query(Position).filter(
                            Position.account_id == account.account_id,
                            Position.stock_code == stock_code
                        ).first()
                        if position:
                            # 动态价格对齐：如果数据库中 current_price 为 0，尝试从实时行情补全
                            curr_price = float(position.current_price or 0)
                            if curr_price <= 0:
                                from app.models.data_storage import StockRealtimeMarket
                                from sqlalchemy import desc
                                latest_market = db.query(StockRealtimeMarket).filter(
                                    StockRealtimeMarket.stock_code == stock_code
                                ).order_by(desc(StockRealtimeMarket.timestamp)).first()
                                if latest_market:
                                    curr_price = float(latest_market.current_price)
                                    logger.info(
                                        "Fixed position price using realtime market",
                                        extra={"stock_code": stock_code, "current_price": curr_price},
                                    )

                            # 计算当前该股仓位比例 (current_position = 市值 / 总资产)
                            total_assets = float(account.total_assets or 0)
                            current_pos_ratio = (
                                (position.total_shares * curr_price) / total_assets if total_assets > 0 else 0
                            )

                            portfolio_info["position"] = {
                                "stock_code": position.stock_code,
                                "total_shares": position.total_shares,
                                "available_shares": position.available_shares,
                                "avg_cost": float(position.avg_cost or 0),
                                "current_price": curr_price,
                                "current_position": round(current_pos_ratio, 4),
                                "profit_loss": float(position.profit_loss or 0),
                                "profit_loss_pct": float(position.profit_loss_pct or 0)
                            }
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


def _build_previous_execution_summary(db, session_id: UUID) -> Dict[str, Any]:
    """构建上一轮 PM 决策关联的最小交易执行摘要。

    Args:
        db: 数据库会话。
        session_id: 上一轮 Debate session ID。

    Returns:
        包含订单数、成交数、成交均价、成交数量、已实现盈亏和最近成交时间的摘要。
    """
    from app.models.order import Order
    from app.models.trade_record import TradeRecord

    orders = db.query(Order).filter(Order.session_id == session_id).all()
    trades = (
        db.query(TradeRecord)
        .filter(TradeRecord.session_id == session_id)
        .order_by(TradeRecord.trade_time.asc(), TradeRecord.created_at.asc())
        .all()
    )
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


def _get_previous_pm_decision(
    session_id: Optional[UUID],
    stock_code: str
) -> Dict[str, Any]:
    """Fetch the latest prior PM decision for the same user and stock."""
    if not session_id:
        return {}

    from app.core.database import SessionLocal
    from app.models.session import Session as SessionModel
    from app.models.debate_message import DebateMessage

    with SessionLocal() as db:
        try:
            current_session = db.query(SessionModel).filter(
                SessionModel.session_id == session_id
            ).first()
            if not current_session:
                return {}

            previous_msg = db.query(DebateMessage, SessionModel).join(
                SessionModel, SessionModel.session_id == DebateMessage.session_id
            ).filter(
                SessionModel.user_id == current_session.user_id,
                SessionModel.stock_code == stock_code,
                DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER,
                DebateMessage.session_id != session_id
            ).order_by(
                DebateMessage.created_at.desc()
            ).first()

            if not previous_msg:
                return {}

            debate_msg, prev_session = previous_msg
            analysis = debate_msg.analysis if isinstance(debate_msg.analysis, dict) else {}
            execution_summary = _build_previous_execution_summary(db, prev_session.session_id)
            return {
                "session_id": str(prev_session.session_id),
                "session_status": prev_session.status,
                "created_at": debate_msg.created_at.isoformat() if debate_msg.created_at else None,
                "trading_frequency": prev_session.trading_frequency,
                "trading_strategy": prev_session.trading_strategy,
                "decision": debate_msg.decision or analysis.get("decision"),
                "confidence": debate_msg.confidence,
                "target_position": analysis.get("target_position"),
                "stop_loss": analysis.get("stop_loss"),
                "take_profit": analysis.get("take_profit"),
                "holding_horizon_days": analysis.get("holding_horizon_days"),
                "price_range": analysis.get("price_range"),
                "execution_details": analysis.get("execution_details"),
                "report_markdown": analysis.get("report_markdown") or debate_msg.reasoning or "",
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


async def portfolio_management(state: AnalystState) -> Dict[str, Any]:
    static_context = state.get("static_context", {})
    session_id = state.get("session_id")
    previous_pm_decision = _get_previous_pm_decision(session_id, state["stock_code"])
    runtime_context = _build_runtime_context(
        state,
        {
            "sentiment_report": state.get("sentiment_report", ""),
            "news_report": state.get("news_report", ""),
            "policy_report": state.get("policy_report", ""),
            "previous_pm_decision": previous_pm_decision,
            "vertical_views": state.get("vertical_reports", {}),
            "strategic_debate": state.get("strategic_reports", {}),
        },
    )

    from app.core.i18n import i18n_service
    from app.trading.service import trading_service
    from app.core.config import settings as app_settings

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

        decision_data = decision.model_dump() if hasattr(decision, "model_dump") else decision

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
        lambda state: _halt_on_errors(state, "portfolio_management"),
        {
            END: END,
            "portfolio_management": "portfolio_management",
        },
    )
    workflow.add_edge("portfolio_management", END)

    return workflow.compile()
