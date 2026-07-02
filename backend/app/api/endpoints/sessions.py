from fastapi import Body, Query
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID

from app.core.database import get_async_db
from app.crud.session import crud_session
from app.schemas.session import SessionCreate, SessionUpdate, SessionResponse, SessionListResponse
from app.core.logger import logger
from app.core.i18n import i18n_service
from app.core.security import get_current_user
from app.models.user import User
from app.models.position import Position
from app.models.order import Order
from app.models.trade_record import TradeRecord
from app.models.debate_message import DebateMessage
from app.models.session import Session as AnalysisSession

router = APIRouter()


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    session_in: SessionCreate,
    current_user: User = Depends(get_current_user)
):
    """Create new analysis session"""
    logger.info(f"Creating new session for stock: {session_in.stock_code}", extra={"source": "api"})
    session_in.user_id = current_user.id
    try:
        session = await crud_session.create(obj_in=session_in)
        logger.info(f"Session created successfully: {session.session_id}", extra={
                    "source": "api", "session_id": str(session.session_id)})
        return session
    except Exception as e:
        logger.error(f"Failed to create session: {e}", extra={"source": "api", "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to create session")


@router.get("/", response_model=List[SessionResponse] | SessionListResponse)
async def get_sessions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    status: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    paginated: bool = False,
    current_user: User = Depends(get_current_user),
):
    """Get session list, support filtering by status"""
    logger.info(
        "Fetching sessions list",
        extra={"source": "api", "status": status, "session_source": source, "paginated": paginated},
    )
    sessions, total = await crud_session.list_for_user(
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        status=status,
        source=source,
        q=q,
    )
    if paginated:
        return {"total": total, "items": sessions, "limit": limit, "skip": skip}
    return sessions


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Get session details by ID"""
    session = await crud_session.get_owned(session_id=session_id, user_id=current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.put("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: UUID,
    session_in: SessionUpdate,
    current_user: User = Depends(get_current_user),
):
    """Update session information"""
    session = await crud_session.update(session_id=session_id, user_id=current_user.id, obj_in=session_in)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Delete session"""
    delete_error = await crud_session.delete_owned(session_id=session_id, user_id=current_user.id)
    if delete_error == "not_found":
        raise HTTPException(status_code=404, detail="Session not found")
    if delete_error == "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=i18n_service.t("session.delete_active_error")
        )
    return None


@router.post("/batch-delete", status_code=status.HTTP_200_OK)
async def batch_delete_sessions(
    session_ids: List[UUID] = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
):
    """Batch delete sessions"""
    if not session_ids:
        raise HTTPException(status_code=400, detail="No session IDs provided")

    try:
        deleted_count, active_count = await crud_session.batch_delete_owned(
            session_ids=session_ids,
            user_id=current_user.id,
        )
        if deleted_count == 0:
            if active_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=i18n_service.t("session.delete_active_error")
                )
            raise HTTPException(status_code=404, detail="No sessions found to delete")

        msg = i18n_service.t("session.batch_deleted_msg")
        if active_count > 0:
            warning = i18n_service.t("session.batch_delete_active_warning").replace("{{count}}", str(active_count))
            msg = f"{msg}. {warning}"

        return {"message": msg, "deleted_count": deleted_count, "skipped_active_count": active_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to batch delete sessions: {e}", extra={"source": "api", "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to batch delete sessions: {str(e)}")
