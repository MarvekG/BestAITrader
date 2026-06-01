from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.i18n import i18n_service
from app.ai.stock_picker.schemas import (
    StockPickerCandidateResponse,
    StockPickerEventResponse,
    StockPickerResultResponse,
    StockPickerRunCreate,
    StockPickerRunResponse,
    StockPickerRunSummary,
)
from app.models.user import User
from app.ai.stock_picker.service import stock_picker_service
from app.core.security import get_current_user

router = APIRouter()


@router.post("/runs", response_model=StockPickerRunResponse, status_code=status.HTTP_201_CREATED)
async def create_stock_picker_run(
    payload: StockPickerRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        run = stock_picker_service.create_run(db, current_user.id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    background_tasks.add_task(stock_picker_service.execute_run, run.run_id)
    return StockPickerRunResponse(
        run_id=run.run_id,
        status=run.status,
        message=i18n_service.t("ai_stock_picker_backend.api.run_created"),
    )


@router.get("/industries", response_model=list[str])
async def list_stock_picker_industries(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    return stock_picker_service.list_industries(db)


@router.get("/runs", response_model=list[StockPickerRunSummary])
async def list_stock_picker_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    runs = stock_picker_service.list_runs(db, current_user.id)
    return [StockPickerRunSummary(**stock_picker_service.serialize_run_summary(run)) for run in runs]


@router.get("/runs/{run_id}", response_model=StockPickerRunSummary)
async def get_stock_picker_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = stock_picker_service.get_run(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=i18n_service.t("ai_stock_picker_backend.api.run_not_found"))
    return StockPickerRunSummary(**stock_picker_service.serialize_run_summary(run))


@router.get("/runs/{run_id}/events", response_model=list[StockPickerEventResponse])
async def get_stock_picker_run_events(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    events = stock_picker_service.get_events(db, run_id, current_user.id)
    if not events and not stock_picker_service.get_run(db, run_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=i18n_service.t("ai_stock_picker_backend.api.run_not_found"))
    return [
        StockPickerEventResponse(
            id=event.id,
            run_id=event.run_id,
            stage=event.stage,
            event_type=event.event_type,
            message=event.message,
            payload=event.payload,
            created_at=event.created_at,
        )
        for event in events
    ]


@router.get("/runs/{run_id}/candidates", response_model=list[StockPickerCandidateResponse])
async def get_stock_picker_run_candidates(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    candidates = stock_picker_service.get_candidates(db, run_id, current_user.id)
    if not candidates and not stock_picker_service.get_run(db, run_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=i18n_service.t("ai_stock_picker_backend.api.run_not_found"))
    return [
        StockPickerCandidateResponse(
            stock_code=item.stock_code,
            stock_name=(item.research_payload or {}).get("stock_name"),
            industry=(item.research_payload or {}).get("industry"),
            market=(item.research_payload or {}).get("market"),
            factor_score=item.factor_score,
            ai_score=item.ai_score,
            final_score=item.final_score,
            quant_support=(item.research_payload or {}).get("quant_support"),
            decision=item.decision,
            eliminated_stage=item.eliminated_stage,
            eliminated_reason=item.eliminated_reason,
            research_payload=item.research_payload,
        )
        for item in candidates
    ]


@router.get("/runs/{run_id}/result", response_model=StockPickerResultResponse)
async def get_stock_picker_run_result(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = stock_picker_service.get_run(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=i18n_service.t("ai_stock_picker_backend.api.run_not_found"))
    return StockPickerResultResponse(**stock_picker_service.build_result(db, run))


@router.delete("/runs/{run_id}", status_code=status.HTTP_200_OK)
async def delete_stock_picker_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted = stock_picker_service.delete_run(db, run_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=i18n_service.t("ai_stock_picker_backend.api.run_not_found"))
    return {"message": i18n_service.t("ai_stock_picker_backend.api.run_deleted")}


@router.delete("/runs", status_code=status.HTTP_200_OK)
async def delete_all_stock_picker_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = stock_picker_service.delete_all_runs(db, current_user.id)
    return {"message": i18n_service.t("ai_stock_picker_backend.api.runs_deleted"), "count": count}
