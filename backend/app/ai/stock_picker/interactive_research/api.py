from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai.stock_picker.interactive_research.schemas import (
    InteractiveResearchActionRequest,
    InteractiveResearchActionResponse,
    InteractiveResearchMessageAppendResponse,
    InteractiveResearchMessageCreate,
    InteractiveResearchMessageResponse,
    InteractiveResearchRunCreate,
    InteractiveResearchRunResponse,
    InteractiveResearchRunSummary,
)
from app.ai.stock_picker.interactive_research.service import interactive_research_service
from app.core.database import get_db
from app.core.i18n import i18n_service
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter()


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究 API 翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


def _raise_service_error(exc: Exception) -> None:
    """把 service 层异常映射为 HTTP 异常。

    Args:
        exc: service 层抛出的异常。

    Raises:
        HTTPException: 根据异常类型映射出的 HTTP 错误。
    """
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=_t("errors.service_error"),
    ) from exc


def _require_run(run_id: UUID, db: Session, current_user: User):
    """获取当前用户拥有的 run，不存在时抛出 404。

    Args:
        run_id: 研究 run ID。
        db: 数据库会话。
        current_user: 当前认证用户。

    Returns:
        当前用户拥有的 run。

    Raises:
        HTTPException: run 不存在时抛出。
    """
    run = interactive_research_service.get_run(db, run_id, current_user.id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_t("errors.run_not_found"))
    return run


@router.post("/runs", response_model=InteractiveResearchRunResponse, status_code=status.HTTP_201_CREATED)
async def create_interactive_research_run(
    payload: InteractiveResearchRunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交自然语言需求并生成等待确认的研究计划。

    Args:
        payload: 自然语言需求和初始研究约束。
        db: 数据库会话依赖。
        current_user: 当前认证用户依赖。

    Returns:
        新建 run 摘要及初始消息。

    Raises:
        HTTPException: 当前用户已有活跃 run 或写入失败时抛出。
    """
    try:
        run = interactive_research_service.create_run(db, current_user.id, payload.model_dump())
    except Exception as exc:
        _raise_service_error(exc)
    return InteractiveResearchRunResponse(
        run=InteractiveResearchRunSummary(**interactive_research_service.serialize_run_summary(run)),
        messages=[
            InteractiveResearchMessageResponse(**interactive_research_service.serialize_message(item))
            for item in interactive_research_service.get_messages(db, run.run_id, current_user.id)
        ],
    )


@router.get("/runs", response_model=list[InteractiveResearchRunSummary])
async def list_interactive_research_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询当前用户的聊天式 Deep Research run 列表。

    Args:
        db: 数据库会话依赖。
        current_user: 当前认证用户依赖。

    Returns:
        当前用户的 run 摘要列表。
    """
    runs = interactive_research_service.list_runs(db, current_user.id)
    return [InteractiveResearchRunSummary(**interactive_research_service.serialize_run_summary(run)) for run in runs]


@router.get("/runs/{run_id}", response_model=InteractiveResearchRunSummary)
async def get_interactive_research_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询单个聊天式 Deep Research run。

    Args:
        run_id: 研究 run ID。
        db: 数据库会话依赖。
        current_user: 当前认证用户依赖。

    Returns:
        run 摘要。

    Raises:
        HTTPException: run 不存在时抛出。
    """
    run = _require_run(run_id, db, current_user)
    return InteractiveResearchRunSummary(**interactive_research_service.serialize_run_summary(run))


@router.get("/runs/{run_id}/messages", response_model=list[InteractiveResearchMessageResponse])
async def get_interactive_research_messages(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询聊天消息流。

    Args:
        run_id: 研究 run ID。
        db: 数据库会话依赖。
        current_user: 当前认证用户依赖。

    Returns:
        按 sequence_no 升序排列的消息列表。

    Raises:
        HTTPException: run 不存在时抛出。
    """
    _require_run(run_id, db, current_user)
    messages = interactive_research_service.get_messages(db, run_id, current_user.id)
    return [
        InteractiveResearchMessageResponse(**interactive_research_service.serialize_message(item))
        for item in messages
    ]


@router.post("/runs/{run_id}/messages", response_model=InteractiveResearchMessageAppendResponse)
async def append_interactive_research_message(
    run_id: UUID,
    payload: InteractiveResearchMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用户追加输入、回答问题或补充要求。

    Args:
        run_id: 研究 run ID。
        payload: 用户消息内容。
        background_tasks: FastAPI 后台任务。
        db: 数据库会话依赖。
        current_user: 当前认证用户依赖。

    Returns:
        新增用户消息和更新后的 run 摘要。

    Raises:
        HTTPException: run 不存在或状态不允许时抛出。

    注意:
        回答问题（awaiting_user_input 状态）会在后台执行 workflow，立即返回。
        其他状态（计划修改、排队输入）立即完成。
    """
    try:
        message = await interactive_research_service.append_user_message(
            db,
            run_id,
            current_user.id,
            payload.content,
            payload.payload,
            background_tasks=background_tasks,
        )
    except Exception as exc:
        _raise_service_error(exc)
    run = _require_run(run_id, db, current_user)
    return InteractiveResearchMessageAppendResponse(
        run=InteractiveResearchRunSummary(**interactive_research_service.serialize_run_summary(run)),
        message=InteractiveResearchMessageResponse(**interactive_research_service.serialize_message(message)),
    )


@router.post("/runs/{run_id}/actions", response_model=InteractiveResearchActionResponse)
async def run_interactive_research_action(
    run_id: UUID,
    payload: InteractiveResearchActionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """执行 approve 或 cancel 动作。

    Args:
        run_id: 研究 run ID。
        payload: 动作请求。
        background_tasks: FastAPI 后台任务。
        db: 数据库会话依赖。
        current_user: 当前认证用户依赖。

    Returns:
        更新后的 run 和消息流。

    Raises:
        HTTPException: run 不存在或状态不允许时抛出。

    注意:
        approve 动作在后台执行 workflow，立即返回。
        前端通过轮询消息流监听进度。
        cancel 立即完成，不需要后台任务。
    """
    try:
        run = await interactive_research_service.process_action(
            db,
            run_id,
            current_user.id,
            payload.action,
            content=payload.content,
            payload=payload.payload,
            background_tasks=background_tasks,
        )
    except Exception as exc:
        _raise_service_error(exc)
    return InteractiveResearchActionResponse(
        run=InteractiveResearchRunSummary(**interactive_research_service.serialize_run_summary(run)),
        messages=[
            InteractiveResearchMessageResponse(**interactive_research_service.serialize_message(item))
            for item in interactive_research_service.get_messages(db, run_id, current_user.id)
        ],
    )
