from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from uuid import UUID

from app.core.database import get_async_db
from app.core.i18n import i18n_service
from app.crud.session import crud_session
from app.ai.llm_engine.debate_concurrency import (
    DebateConcurrencyLimitReached,
    DebateStockTaskAlreadyRunning,
    find_running_debate_task_for_session,
    format_ai_analysis_task_name,
    ensure_debate_launch_available,
)
from app.ai.llm_engine.runner import run_analysis_task  # 使用新的 LLM Engine
from app.api.endpoints.debate_ws import send_debate_status
from app.models.debate_message import DebateMessage
from app.models.pm_decision import PMDecisionRecord
from app.core.logger import logger
from app.core.security import get_current_user
from app.models.user import User
from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER
from app.schemas.session import SessionCreate, SessionResponse
from app.tasks.task_manager import task_manager

router = APIRouter()


async def _ensure_debate_launch_allowed(stock_code: str) -> None:
    """校验股票可启动辩论，并把并发限制映射为 HTTP 响应。

    Args:
        stock_code: 标准股票代码。

    Raises:
        HTTPException: 股票已有运行中任务或全局并发达到上限。
    """
    try:
        await ensure_debate_launch_available(stock_code)
    except DebateStockTaskAlreadyRunning as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DebateConcurrencyLimitReached as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


async def _discard_created_session(*, session_id: UUID, user_id: int) -> None:
    """清理启动失败后遗留的空会话，避免列表出现无法执行的 active 会话。

    Args:
        session_id: 刚创建的会话 ID。
        user_id: 会话所属用户 ID。
    """
    try:
        await crud_session.delete_owned(
            session_id=session_id,
            user_id=user_id,
            allow_active=True,
        )
    except Exception as exc:
        logger.exception(
            "Failed to discard session created for rejected debate launch",
            extra={"session_id": str(session_id), "user_id": user_id, "error": str(exc)},
        )


@router.post("/run", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def run_debate(
    request: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    """
    运行辩论流程 (异步后台执行)。

    请求参数:
        - session_id: 已有会话 ID；不传时会在并发校验通过后自动创建会话
        - stock_code: 股票代码
        - trading_frequency: 交易频率
        - trading_strategy: 交易策略
        - simplified: 是否简化模式(可选,默认False)
    """
    try:
        session_id_str = request.get("session_id")
        stock_code = request.get("stock_code")
        simplified = request.get("simplified", False)
        trading_frequency = request.get("trading_frequency")
        trading_strategy = request.get("trading_strategy")
        sync_before_analysis = bool(request.get("sync_before_analysis", True))

        if not stock_code:
            raise HTTPException(status_code=400, detail="stock_code is required")
        if not trading_frequency:
            raise HTTPException(status_code=400, detail="trading_frequency is required")
        if not trading_strategy:
            raise HTTPException(status_code=400, detail="trading_strategy is required")

        created_session = False
        session = None

        if session_id_str:
            try:
                session_id = UUID(session_id_str)
            except (AttributeError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Invalid session_id format") from exc

            # 检查 session 是否存在且状态为 active
            session = await crud_session.get_owned(session_id=session_id, user_id=current_user.id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session.status != "active":
                raise HTTPException(
                    status_code=400,
                    detail=i18n_service.t(
                        "tasks.ai_debate_session_not_active",
                        session_status=session.status,
                    ),
                )

            # 检查该 session 是否已完成辩论 (检查 DebateMessage 中是否有 portfolio_manager 的报告)
            existing_decision_result = await db.execute(
                select(DebateMessage).where(
                    DebateMessage.session_id == session_id,
                    DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER,
                )
            )
            existing_decision = existing_decision_result.scalar_one_or_none()

            if existing_decision:
                raise HTTPException(
                    status_code=400,
                    detail=i18n_service.t(
                        "tasks.ai_debate_session_already_completed",
                        session_id=str(session_id),
                    ),
                )

            # 如果有 debate 记录但没有 PM 决策，说明是断点续传/恢复
            existing_debate_result = await db.execute(
                select(DebateMessage).where(DebateMessage.session_id == session_id)
            )
            existing_debate_msgs = existing_debate_result.scalar_one_or_none()

            if existing_debate_msgs:
                # 记录日志但不报错
                logger.info(f"Session {session_id} has existing debate records but no decision. Resuming debate...")

            existing_session_task = await find_running_debate_task_for_session(str(session_id))
            if existing_session_task:
                logger.info(
                    "Debate already running for session %s, reusing task %s",
                    session_id,
                    existing_session_task.task_id,
                )
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "task_id": existing_session_task.task_id,
                        "session_id": str(session_id),
                        "status": existing_session_task.status,
                        "message": i18n_service.t(
                            "tasks.ai_analysis_already_running",
                            task_id=existing_session_task.task_id,
                        ),
                        "new_task": False,
                    },
                )

            await _ensure_debate_launch_allowed(stock_code)
        else:
            # 先校验并发和股票互斥，再创建会话，避免被拦截后留下无法执行的 active 空会话。
            await _ensure_debate_launch_allowed(stock_code)
            session = await crud_session.create(
                obj_in=SessionCreate(
                    user_id=current_user.id,
                    stock_code=stock_code,
                    trading_frequency=trading_frequency,
                    trading_strategy=trading_strategy,
                )
            )
            session_id = session.session_id
            created_session = True

        task_parameters = {
            "session_id": str(session_id),
            "stock_code": stock_code,
            "trading_frequency": trading_frequency,
            "trading_strategy": trading_strategy,
        }
        task_parameters["sync_before_analysis"] = sync_before_analysis

        try:
            # 提交任务到任务管理器
            task_info = await task_manager.submit_task(
                task_name=format_ai_analysis_task_name(stock_code),
                task_type="ai_analysis",
                parameters=task_parameters,
                allow_concurrent=False,
                user_id=current_user.id,
                task_func=run_analysis_task,
                task_kwargs={
                    "session_id": str(session_id),
                    "stock_code": stock_code,
                    "trading_frequency": trading_frequency,
                    "trading_strategy": trading_strategy,
                    "sync_before_analysis": sync_before_analysis,
                },
                # 其实可以并行,但前端可能乱。这里暂设为False，或者True?
                # 这里的allow_concurrent是针对task_type + parameters.
                # 我们的parameters包含session_id，所以如果同一个session_id再次提交，且allow_concurrent=False，会阻止。
                # 这正是我们想要的: 同一个session同一时间只能跑一个辩论。
            )
        except Exception:
            if created_session:
                await _discard_created_session(session_id=session_id, user_id=current_user.id)
            raise

        if not task_info.get("new_task", True):
            logger.info(
                "Debate already running for session %s, reusing task %s",
                session_id,
                task_info["task_id"],
            )
            if created_session:
                await _discard_created_session(session_id=session_id, user_id=current_user.id)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "task_id": task_info["task_id"],
                    "session_id": str(session_id),
                    "status": task_info["status"],
                    "message": task_info["message"],
                    "new_task": False,
                },
            )

        # 发送辩论开始状态
        await send_debate_status(str(session_id), "started")

        response: Dict[str, Any] = {
            "task_id": task_info["task_id"],
            "session_id": str(session_id),
            "status": "started",
            "message": "AI Analysis started in background",
            "new_task": True,
        }
        if created_session and session is not None:
            response["session"] = SessionResponse.model_validate(session).model_dump(mode="json")
        return response

    except HTTPException:
        raise
    except Exception as e:
        # 如果启动前就挂了，发错误状态
        if 'session_id' in locals():
            await send_debate_status(str(session_id), "error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{session_id}", response_model=List[Dict[str, Any]])
async def get_debate_history(
    session_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    """Get debate history"""
    try:
        if not await crud_session.get_owned(session_id=session_id, user_id=current_user.id):
            raise HTTPException(status_code=404, detail="Session not found")
        # Query debate messages
        result = await db.execute(
            select(DebateMessage)
            .where(DebateMessage.session_id == session_id)
            .order_by(DebateMessage.created_at.asc())
        )
        debate_messages = result.scalars().all()

        # Convert to frontend format
        debate_history = [{
            "role": msg.agent_role,
            "agent_role": msg.agent_role,
            "content": msg.reasoning or "",
            "timestamp": msg.created_at.isoformat(),
            "round_number": msg.round_number,
            "stage": msg.stage,
        } for msg in debate_messages]

        return debate_history
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threads/{session_id}", response_model=List[Dict[str, Any]])
async def get_debate_threads(
    session_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    """Get debate threads (actually debate messages)"""
    try:
        if not await crud_session.get_owned(session_id=session_id, user_id=current_user.id):
            raise HTTPException(status_code=404, detail="Session not found")
        # Query debate messages (not debate_threads table)
        result = await db.execute(
            select(DebateMessage)
            .where(DebateMessage.session_id == session_id)
            .order_by(DebateMessage.created_at)
        )
        debate_messages = result.scalars().all()

        # Convert to frontend format matching DebateThread interface
        threads = [{
            "id": str(msg.message_id),
            "session_id": str(msg.session_id),
            "round_number": msg.round_number,
            "role": msg.agent_role,
            "agent_role": msg.agent_role,
            "speaker_role": msg.agent_role,
            "content": msg.reasoning or "",
            "prompt_input": msg.prompt_input or "",  # 添加推理输入字段
            "timestamp": msg.created_at.isoformat(),
            "stage": msg.stage
        } for msg in debate_messages]

        return threads
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/decisions/{session_id}", response_model=List[Dict[str, Any]])
async def get_pm_decisions(
    session_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    """Get PM decisions list (Includes execution details) Ratio/Action/Plan"""
    try:
        if not await crud_session.get_owned(session_id=session_id, user_id=current_user.id):
            raise HTTPException(status_code=404, detail="Session not found")
        # Query messages from DebateMessage table for PM decisions
        result = await db.execute(
            select(PMDecisionRecord, DebateMessage)
            .join(DebateMessage, DebateMessage.session_id == PMDecisionRecord.session_id)
            .where(
                PMDecisionRecord.session_id == session_id,
                DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER,
            )
            .order_by(DebateMessage.created_at.asc())
        )
        rows = result.all()

        pm_decisions = []
        for pm_row, msg in rows:

            pm_decisions.append({
                "id": str(pm_row.decision_id),
                "session_id": str(msg.session_id),
                "confidence": float(pm_row.confidence_score or 0) / 100.0,
                "target_position": pm_row.target_position,
                "stop_loss": pm_row.stop_loss,
                "take_profit": pm_row.take_profit,
                "holding_horizon_days": pm_row.holding_horizon_days,
                "reasoning": msg.reasoning,
                "created_at": msg.created_at.isoformat(),
                "agent_role": msg.agent_role
            })

        return pm_decisions
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
