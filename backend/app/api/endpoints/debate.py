from datetime import datetime  # noqa: F401
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from uuid import UUID

from app.core.database import get_db
from app.tasks.task_manager import task_manager
from app.ai.llm_engine.runner import run_analysis_task  # 使用新的 LLM Engine
from app.api.ownership import get_owned_session
from app.api.endpoints.debate_ws import send_debate_status
from app.models.debate_message import DebateMessage
from app.core.logger import logger
from app.core.security import get_current_user
from app.models.async_task import AsyncTask
from app.models.user import User
from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER

router = APIRouter()


@router.post("/run", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def run_debate(
    request: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    运行辩论流程 (异步后台执行)

    请求参数:
        - session_id: 会话ID
        - stock_code: 股票代码
        - simplified: 是否简化模式(可选,默认False)
    """
    try:
        session_id_str = request.get("session_id")
        stock_code = request.get("stock_code")
        simplified = request.get("simplified", False)
        trading_frequency = request.get("trading_frequency")
        trading_strategy = request.get("trading_strategy")

        if not session_id_str:
            raise HTTPException(status_code=400, detail="session_id is required")
        if not stock_code:
            raise HTTPException(status_code=400, detail="stock_code is required")
        if not trading_frequency:
            raise HTTPException(status_code=400, detail="trading_frequency is required")
        if not trading_strategy:
            raise HTTPException(status_code=400, detail="trading_strategy is required")

        try:
            session_id = UUID(session_id_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid session_id format")

        # 检查 session 是否存在且状态为 active
        session = get_owned_session(db, session_id, current_user)

        if session.status != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Session status is '{session.status}'. Only 'active' sessions can start debates."
            )

        # 检查该 session 是否已完成辩论 (检查 DebateMessage 中是否有 portfolio_manager 的报告)
        existing_decision = db.query(DebateMessage).filter(
            DebateMessage.session_id == session_id,
            DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER
        ).first()

        if existing_decision:
            raise HTTPException(
                status_code=400,
                detail=f"Session {session_id} already has a completed debate. Cannot start a new debate for the same session."
            )

        # 如果有 debate 记录但没有 PM 决策，说明是断点续传/恢复
        existing_debate_msgs = db.query(DebateMessage).filter(
            DebateMessage.session_id == session_id
        ).first()

        if existing_debate_msgs:
            # 记录日志但不报错
            logger.info(f"Session {session_id} has existing debate records but no decision. Resuming debate...")

        existing_stock_task = db.query(AsyncTask).filter(
            AsyncTask.task_name == f"AI Analysis - {stock_code}",
            AsyncTask.status.in_(["pending", "running"]),
        ).first()
        if existing_stock_task:
            existing_task_session_id = str((existing_stock_task.parameters or {}).get("session_id") or "")
            if existing_task_session_id != str(session_id):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Analysis for stock {stock_code} is already running "
                        f"(Task ID: {existing_stock_task.task_id}). Please wait for it to complete."
                    ),
                )

        # 提交任务到任务管理器
        task_info = task_manager.submit_task(
            db=db,
            task_name=f"AI Analysis - {stock_code}",
            task_type="ai_analysis",
            parameters={
                "session_id": str(session_id),
                "stock_code": stock_code,
                "trading_frequency": trading_frequency,
                "trading_strategy": trading_strategy,
            },
            allow_concurrent=False  # 同一个Session不建议并行辩论?
            # 其实可以并行,但前端可能乱。这里暂设为False，或者True?
            # 这里的allow_concurrent是针对task_type + parameters.
            # 我们的parameters包含session_id，所以如果同一个session_id再次提交，且allow_concurrent=False，会阻止。
            # 这正是我们想要的: 同一个session同一时间只能跑一个辩论。
        )

        if not task_info.get("new_task", True):
            logger.info(
                "Debate already running for session %s, reusing task %s",
                session_id,
                task_info["task_id"],
            )
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

        # 添加后台任务 (使用新的 runner)
        background_tasks.add_task(
            run_analysis_task,
            task_id=task_info["task_id"],
            session_id=str(session_id),
            stock_code=stock_code,
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy
        )

        return {
            "task_id": task_info["task_id"],
            "session_id": str(session_id),
            "status": "started",
            "message": "AI Analysis started in background",
            "new_task": True,
        }

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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get debate history"""
    try:
        get_owned_session(db, session_id, current_user)
        # Query debate messages
        debate_messages = db.query(DebateMessage).filter(
            DebateMessage.session_id == session_id
        ).order_by(
            DebateMessage.created_at.asc()
        ).all()

        # Convert to frontend format
        debate_history = [{
            "role": msg.agent_role,
            "agent_role": msg.agent_role,
            "content": msg.reasoning or "",
            "timestamp": msg.created_at.isoformat(),
            "round_number": msg.round_number,
            "stage": msg.stage,
            "analysis": msg.analysis
        } for msg in debate_messages]

        return debate_history
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threads/{session_id}", response_model=List[Dict[str, Any]])
async def get_debate_threads(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get debate threads (actually debate messages)"""
    try:
        get_owned_session(db, session_id, current_user)
        # Query debate messages (not debate_threads table)
        debate_messages = db.query(DebateMessage).filter(
            DebateMessage.session_id == session_id
        ).order_by(
            DebateMessage.created_at
        ).all()

        # Convert to frontend format matching DebateThread interface
        threads = [{
            "id": str(msg.message_id),
            "session_id": str(msg.session_id),
            "round_number": msg.round_number,
            "role": msg.agent_role,
            "agent_role": msg.agent_role,
            "speaker_role": msg.agent_role,
            "content": msg.reasoning or "",
            "reasoning_chain": msg.analysis,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get PM decisions list (Includes execution details) Ratio/Action/Plan"""
    try:
        get_owned_session(db, session_id, current_user)
        # Query messages from DebateMessage table for PM decisions
        messages = db.query(DebateMessage).filter(
            DebateMessage.session_id == session_id,
            DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER
        ).order_by(
            DebateMessage.created_at.asc()
        ).all()

        pm_decisions = []
        for msg in messages:
            # Extract data from analysis JSON
            analysis = msg.analysis or {}
            target_pos = analysis.get("target_position", 0.0)
            action = msg.decision or analysis.get("action", "hold")

            pm_decisions.append({
                "id": str(msg.message_id),
                "session_id": str(msg.session_id),
                "action": action.lower(),
                "confidence": msg.confidence or 0.0,
                "target_position": target_pos,
                "reasoning": msg.reasoning,
                "execution_plan": analysis.get("execution_details", ""),
                "created_at": msg.created_at.isoformat(),
                "agent_role": msg.agent_role
            })

        return pm_decisions
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
