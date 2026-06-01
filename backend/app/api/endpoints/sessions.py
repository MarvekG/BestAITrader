from datetime import datetime
from fastapi import Body
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Iterable, List, Optional
from uuid import UUID

from app.core.database import get_db
from app.schemas.session import SessionCreate, SessionUpdate, SessionResponse
from app.crud.session import crud_session
from app.core.logger import logger
from app.core.i18n import i18n_service
from app.core.security import get_current_user
from app.api.ownership import get_owned_session
from app.models.user import User
from app.models.position import Position
from app.models.order import Order
from app.models.trade_record import TradeRecord
from app.models.debate_message import DebateMessage
from app.models.async_task import AsyncTask
from app.models.session import Session as AnalysisSession

router = APIRouter()


def _build_session_ended_at_map(
    db: Session,
    sessions: Iterable,
) -> dict[str, datetime]:
    """Build a best-effort ended_at map for analysis sessions."""
    session_list = list(sessions)
    if not session_list:
        return {}

    session_ids = {str(session.session_id) for session in session_list}
    ended_at_map: dict[str, datetime] = {}

    async_tasks = db.query(AsyncTask).filter(
        AsyncTask.task_type == "ai_analysis",
        AsyncTask.completed_at.isnot(None),
    ).all()
    for task in async_tasks:
        parameters = task.parameters if isinstance(task.parameters, dict) else {}
        task_session_id = parameters.get("session_id")
        if task_session_id not in session_ids or task.completed_at is None:
            continue

        current_ended_at = ended_at_map.get(task_session_id)
        if current_ended_at is None or task.completed_at > current_ended_at:
            ended_at_map[task_session_id] = task.completed_at

    debate_rows = db.query(
        DebateMessage.session_id,
        DebateMessage.created_at,
    ).filter(
        DebateMessage.session_id.in_([session.session_id for session in session_list])
    ).all()
    for session_id, created_at in debate_rows:
        session_id_str = str(session_id)
        if session_id_str in ended_at_map:
            continue

        current_ended_at = ended_at_map.get(session_id_str)
        if current_ended_at is None or created_at > current_ended_at:
            ended_at_map[session_id_str] = created_at

    return ended_at_map


def _populate_session_metadata(db: Session, sessions: Iterable) -> None:
    """Populate frontend-facing derived fields for session responses."""
    session_list = list(sessions)
    if not session_list:
        return

    from app.models.data_storage import StockBasic

    stock_codes = [session.stock_code for session in session_list]
    basics = db.query(StockBasic.stock_code, StockBasic.name).filter(
        StockBasic.stock_code.in_(stock_codes)
    ).all()
    name_map = {code: name for code, name in basics}
    ended_at_map = _build_session_ended_at_map(db, session_list)

    for session in session_list:
        setattr(session, "stock_name", name_map.get(session.stock_code, session.stock_code))
        setattr(session, "ended_at", ended_at_map.get(str(session.session_id)))


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    session_in: SessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create new analysis session"""
    logger.info(f"Creating new session for stock: {session_in.stock_code}", extra={"source": "api"})
    session_in.user_id = current_user.id
    try:
        session = crud_session.create(db=db, obj_in=session_in)
        _populate_session_metadata(db, [session])

        logger.info(f"Session created successfully: {session.session_id}", extra={
                    "source": "api", "session_id": str(session.session_id)})
        return session
    except Exception as e:
        logger.error(f"Failed to create session: {e}", extra={"source": "api", "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to create session")


@router.get("/", response_model=List[SessionResponse])
def get_sessions(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get session list, support filtering by status"""
    logger.info(f"Fetching sessions list, status={status}", extra={"source": "api"})
    filters = {"user_id": current_user.id}
    if status:
        filters["status"] = status
    sessions = crud_session.get_multi(db=db, skip=skip, limit=limit, **filters)
    _populate_session_metadata(db, sessions)
    return sessions


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get session details by ID"""
    session = get_owned_session(db, session_id, current_user)

    _populate_session_metadata(db, [session])
    return session


@router.put("/{session_id}", response_model=SessionResponse)
def update_session(
    session_id: UUID,
    session_in: SessionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update session information"""
    session = get_owned_session(db, session_id, current_user)
    updated_session = crud_session.update(db=db, db_obj=session, obj_in=session_in)
    _populate_session_metadata(db, [updated_session])
    return updated_session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete session"""
    session = get_owned_session(db, session_id, current_user)

    if session.status == "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=i18n_service.t("session.delete_active_error")
        )

    # Manually delete related records to avoid ForeignKeyViolation
    # Note: Some tables might have ON DELETE CASCADE, but Position clearly doesn't
    try:
        db.query(Position).filter(Position.session_id == session_id).update(
            {"session_id": None}, synchronize_session=False)
        db.query(Order).filter(Order.session_id == session_id).update({"session_id": None}, synchronize_session=False)
        db.query(TradeRecord).filter(TradeRecord.session_id == session_id).update(
            {"session_id": None}, synchronize_session=False)
        db.query(DebateMessage).filter(DebateMessage.session_id == session_id).delete(synchronize_session=False)

        db.commit()  # Commit deletions of children

        crud_session.remove(db=db, id=session_id)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete session {session_id}: {e}", extra={"source": "api", "error": str(e)})
    return None


@router.post("/batch-delete", status_code=status.HTTP_200_OK)
def batch_delete_sessions(
    session_ids: List[UUID] = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Batch delete sessions"""
    if not session_ids:
        raise HTTPException(status_code=400, detail="No session IDs provided")

    try:
        sessions = db.query(AnalysisSession).filter(
            AnalysisSession.session_id.in_(session_ids),
            AnalysisSession.user_id == current_user.id,
        ).all()

        to_delete_ids = [s.session_id for s in sessions if s.status != "active"]
        active_count = sum(1 for s in sessions if s.status == "active")

        if not to_delete_ids:
            if active_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=i18n_service.t("session.delete_active_error")
                )
            raise HTTPException(status_code=404, detail="No sessions found to delete")

        # Cascade delete manually as with single delete
        db.query(Position).filter(Position.session_id.in_(to_delete_ids)).update(
            {"session_id": None}, synchronize_session=False)
        db.query(Order).filter(Order.session_id.in_(to_delete_ids)).update(
            {"session_id": None}, synchronize_session=False)
        db.query(TradeRecord).filter(TradeRecord.session_id.in_(to_delete_ids)).update(
            {"session_id": None}, synchronize_session=False)
        db.query(DebateMessage).filter(DebateMessage.session_id.in_(to_delete_ids)
                                       ).delete(synchronize_session=False)

        db.commit()

        # Now delete the sessions themselves
        for sid in to_delete_ids:
            crud_session.remove(db=db, id=sid)

        msg = i18n_service.t("session.batch_deleted_msg")
        if active_count > 0:
            warning = i18n_service.t("session.batch_delete_active_warning").replace("{{count}}", str(active_count))
            msg = f"{msg}. {warning}"

        return {"message": msg, "deleted_count": len(to_delete_ids), "skipped_active_count": active_count}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to batch delete sessions: {e}", extra={"source": "api", "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to batch delete sessions: {str(e)}")


@router.post("/batch-archive", status_code=status.HTTP_200_OK)
def batch_archive_sessions(
    session_ids: List[UUID] = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Batch archive sessions"""
    if not session_ids:
        raise HTTPException(status_code=400, detail="No session IDs provided")

    try:
        updated_count = 0
        sessions = db.query(AnalysisSession).filter(
            AnalysisSession.session_id.in_(session_ids),
            AnalysisSession.user_id == current_user.id,
        ).all()
        for session in sessions:
            if session.status != "archived":
                crud_session.update(db=db, db_obj=session, obj_in={"status": "archived"})
                updated_count += 1

        return {"message": f"Successfully archived {updated_count} sessions", "updated_count": updated_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to batch archive sessions: {e}", extra={"source": "api", "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to batch archive sessions: {str(e)}")


@router.post("/{session_id}/archive")
def archive_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Archive session"""
    session = get_owned_session(db, session_id, current_user)
    updated_session = crud_session.update(db=db, db_obj=session, obj_in={"status": "archived"})
    _populate_session_metadata(db, [updated_session])
    return updated_session
