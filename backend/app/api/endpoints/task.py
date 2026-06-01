from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.async_task import AsyncTask
from app.models.user import User
from app.tasks.task_manager import task_manager

router = APIRouter()


@router.get("/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Query task status
    
    Args:
        task_id: Task ID
        
    Returns:
        Task detailed information
    """
    task_info = task_manager.get_task_status(db, task_id, user_id=current_user.id)
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
    db: Session = Depends(get_db)
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
    query = db.query(AsyncTask).filter(AsyncTask.user_id == current_user.id)
    
    # Apply filters
    if status:
        query = query.filter(AsyncTask.status == status)
    if task_type:
        query = query.filter(AsyncTask.task_type == task_type)
    
    # Order by creation time (newest first)
    query = query.order_by(AsyncTask.created_at.desc())
    
    # Get total count
    total = query.count()
    
    # Apply pagination
    tasks = query.offset(skip).limit(limit).all()
    
    # Convert to dict
    task_list = [task.to_dict() for task in tasks]
    
    return {
        "total": total,
        "items": task_list,
        "limit": limit,
        "skip": skip
    }
