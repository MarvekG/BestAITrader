from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Optional

from app.core.security import get_current_user
from app.models.user import User
from app.tasks.task_manager import task_manager

router = APIRouter()


@router.get("/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Query task status

    Args:
        task_id: Task ID

    Returns:
        Task detailed information
    """
    task_info = await task_manager.get_task_status(task_id, user_id=current_user.id)
    if not task_info:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return task_info


@router.get("")
async def get_task_list(
    status: Optional[str] = Query(None, description="Filter by status: pending/running/completed/failed"),
    task_type: Optional[str] = Query(None, description="Filter by task type"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of tasks to return"),
    skip: int = Query(0, ge=0, description="Number of tasks to skip"),
    current_user: User = Depends(get_current_user),
):
    """Query task list

    Args:
        status: Task status filter
        task_type: Task type filter
        limit: Limit number of returns
        skip: Number of tasks to skip

    Returns:
        Task list
    """
    return await task_manager.get_task_list(
        user_id=current_user.id,
        status=status,
        task_type=task_type,
        limit=limit,
        skip=skip,
    )


@router.delete("/clear", status_code=status.HTTP_200_OK)
async def clear_tasks(
    task_type: str = Query(..., description="Task type to clear"),
    current_user: User = Depends(get_current_user),
):
    """清空当前用户指定类型的异步任务记录。

    Args:
        task_type: 要清空的任务类型
        current_user: 当前登录用户
        db: 数据库会话

    Returns:
        删除数量
    """
    deleted_count = await task_manager.clear_tasks(user_id=current_user.id, task_type=task_type)
    return {"deleted_count": deleted_count}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """删除当前用户拥有的异步任务记录。

    Args:
        task_id: 要删除的任务 ID
        current_user: 当前登录用户
        db: 数据库会话

    Raises:
        HTTPException: 任务不存在或不属于当前用户时返回 404
    """
    deleted = await task_manager.delete_task(user_id=current_user.id, task_id=task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return None
