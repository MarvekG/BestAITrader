from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.i18n import i18n_service
from app.ai.experience.schemas import (
    ExperienceAnalyzeRequest,
    ExperienceAnalyzeResponse,
    ExperienceDebateSessionResponse,
    ExperienceReviewCandidateListResponse,
    ExperienceReviewSchedulerConfig,
    ExperienceReviewEventResponse,
    ExperienceReviewRunResponse,
)
from app.ai.experience.service import experience_service
from app.models.user import User
from app.core.security import get_current_user

router = APIRouter()


@router.get("/scheduler-config", response_model=ExperienceReviewSchedulerConfig)
async def get_scheduler_config(
    current_user: User = Depends(get_current_user),
):
    """返回当前用户的经验复盘调度配置。

    Args:
        current_user: 已认证用户依赖。

    Returns:
        已应用默认值的规范化调度配置。
    """
    del current_user
    from app.tasks.experience_review_scheduler import get_experience_review_scheduler_config

    return await get_experience_review_scheduler_config()


@router.put("/scheduler-config", response_model=ExperienceReviewSchedulerConfig)
async def update_scheduler_config(
    payload: ExperienceReviewSchedulerConfig,
    current_user: User = Depends(get_current_user),
):
    """更新经验复盘调度配置。

    Args:
        payload: 客户端提交的调度配置。
        current_user: 已认证用户依赖。

    Returns:
        规范化并持久化后的调度配置。
    """
    del current_user
    from app.tasks.async_scheduler import async_task_scheduler
    from app.tasks.experience_review_scheduler import (
        update_experience_review_scheduler_config,
    )

    config = await update_experience_review_scheduler_config(payload.model_dump())
    async_task_scheduler.refresh_schedule()
    return config


@router.post("/analyze", response_model=ExperienceAnalyzeResponse, status_code=status.HTTP_201_CREATED)
async def analyze_debate_with_experience(
    payload: ExperienceAnalyzeRequest,
    current_user: User = Depends(get_current_user),
):
    """为已完成的辩论会话发起经验复盘。

    Args:
        payload: 包含会话 ID 和可选复盘周期的复盘请求。
        current_user: 已认证用户依赖。

    Returns:
        序列化后的经验复盘结果。

    Raises:
        HTTPException: 当请求的会话或复盘周期无效时抛出。
    """
    try:
        result = await experience_service.analyze(
            user_id=current_user.id,
            session_id=payload.session_id,
            review_horizon=payload.review_horizon,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ExperienceAnalyzeResponse(
        review_run_id=result.get("review_run_id"),
        review_horizon=result.get("review_horizon"),
        market_day_count=result.get("market_day_count"),
        session_id=result["session_id"],
        stock_code=result["stock_code"],
        stock_name=result.get("stock_name"),
        industry=result.get("industry"),
        style_bucket=result["style_bucket"],
        trading_frequency=result.get("trading_frequency"),
        trading_strategy=result.get("trading_strategy"),
        analysis_date=result["analysis_date"],
        reviewed_at=result["reviewed_at"],
        analysis_payload=result.get("analysis_payload") or {},
        tool_trace=result.get("tool_trace") or [],
    )


@router.get("/review-candidates", response_model=ExperienceReviewCandidateListResponse)
async def list_review_candidates(
    current_user: User = Depends(get_current_user),
):
    """列出可手动复盘的已完成辩论会话。

    Args:
        current_user: 已认证用户依赖。

    Returns:
        带周期可复盘状态元数据的候选会话列表。
    """
    return await experience_service.list_review_candidates(user_id=current_user.id)


@router.get("/debate-sessions", response_model=list[ExperienceDebateSessionResponse])
async def list_debate_sessions_for_review(
    current_user: User = Depends(get_current_user),
):
    """列出经验复盘页面可展示的辩论会话。

    Args:
        current_user: 已认证用户依赖。

    Returns:
        当前认证用户可见的辩论会话列表。
    """
    return await experience_service.list_debate_sessions(user_id=current_user.id)


@router.get("/review-events/{session_id}", response_model=list[ExperienceReviewEventResponse])
async def list_review_events_for_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """列出指定辩论会话的实时复盘事件。

    Args:
        session_id: 辩论会话标识。
        current_user: 已认证用户依赖。

    Returns:
        与该会话关联的复盘事件列表。
    """
    return await experience_service.list_review_events(
        user_id=current_user.id,
        session_id=session_id,
    )


@router.get("/review-runs", response_model=list[ExperienceReviewRunResponse])
async def list_review_runs(
    current_user: User = Depends(get_current_user),
):
    """列出当前用户最近的经验复盘运行记录。

    Args:
        current_user: 已认证用户依赖。

    Returns:
        按最近更新时间排序的复盘运行摘要。
    """
    return await experience_service.list_review_runs(user_id=current_user.id)


@router.get("/review-run-events/{review_run_id}", response_model=list[ExperienceReviewEventResponse])
async def list_review_events_for_run(
    review_run_id: str,
    current_user: User = Depends(get_current_user),
):
    """列出指定经验复盘运行的事件历史。

    Args:
        review_run_id: 经验复盘运行标识。
        current_user: 已认证用户依赖。

    Returns:
        该复盘运行记录下的有序事件列表。
    """
    return await experience_service.list_review_events_by_run(
        user_id=current_user.id,
        review_run_id=review_run_id,
    )


@router.get("/review-run-result/{review_run_id}", response_model=ExperienceAnalyzeResponse | None)
async def get_review_run_result(
    review_run_id: str,
    current_user: User = Depends(get_current_user),
):
    """返回指定复盘运行的完成结果。

    Args:
        review_run_id: 经验复盘运行标识。
        current_user: 已认证用户依赖。

    Returns:
        复盘已完成时返回复盘结果，否则返回 ``None``。

    Raises:
        HTTPException: 当复盘运行不存在时抛出。
    """
    result = await experience_service.get_review_run_result(
        user_id=current_user.id,
        review_run_id=review_run_id,
    )
    if result is None:
        exists = await experience_service.list_review_events_by_run(
            user_id=current_user.id,
            review_run_id=review_run_id,
        )
        if not exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=i18n_service.t("experience.review_run_not_found"))
    return result


@router.delete("/review-runs/{review_run_id}")
async def delete_review_run(
    review_run_id: str,
    current_user: User = Depends(get_current_user),
):
    """删除一个经验复盘运行及其事件。

    Args:
        review_run_id: 经验复盘运行标识。
        current_user: 已认证用户依赖。

    Returns:
        本地化删除结果消息。

    Raises:
        HTTPException: 当复盘运行正在执行、无效或不存在时抛出。
    """
    try:
        deleted = await experience_service.delete_review_run(
            user_id=current_user.id,
            review_run_id=review_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=i18n_service.t("experience.review_run_not_found"))
    return {"message": i18n_service.t("experience.delete_run_success")}


@router.delete("/review-runs")
async def delete_all_review_runs(
    current_user: User = Depends(get_current_user),
):
    """删除当前用户所有非进行中的经验复盘运行。

    Args:
        current_user: 已认证用户依赖。

    Returns:
        本地化删除结果消息和删除数量。

    Raises:
        HTTPException: 当存在进行中的复盘运行导致无法删除时抛出。
    """
    try:
        count = await experience_service.delete_all_review_runs(user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    message = i18n_service.t("experience.clear_runs_success").replace("{{count}}", str(count))
    return {"message": message, "count": count}
