from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Literal, Optional, TypedDict
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.ai.agentic.tool_output_summarizer import (
    should_summarize_tool_output,
    summarize_tool_output,
)
from app.ai.json_utils import stable_json_dumps
from app.ai.llm_routing import get_research_usage_lane
from app.ai.llm_providers import get_llm_provider
from app.ai.agentic.tools import get_all_tools, make_json_serializable
from app.ai.agentic.skills_loader.runtime import (
    build_skills_catalog_prompt,
    get_skills_loader_tools,
)
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.core.utils.converters import safe_float
from app.crud.llm_usage_log import record_llm_usage
from app.models.data_storage import KlineData, StockBasic, StockValuationHistory
from app.data.analytics.core_index import get_core_index_constituent_codes
from app.models.stock_indicators import StockIndicators
from app.models.stock_warehouse import StockWarehouse
from app.ai.stock_picker.models import (
    StockSelectionCandidate,
    StockSelectionEvent,
    StockSelectionRun,
)
from app.ai.stock_picker.universe import get_basic_stock_filter_conds
from app.websocket.manager import ws_manager

logger = get_logger(__name__)


class StockPickerGraphState(TypedDict, total=False):
    universe: List[Any]
    ranked_candidates: List[Any]
    researched_candidates: List[Any]
    recommendations: List[Dict[str, Any]]
    summary: Dict[str, Any]
    research_mode: str


STYLE_LABELS = {
    "balanced": "中线平衡",
    "momentum": "短线动量",
    "value": "价值修复",
    "growth": "景气成长",
    "defensive": "防守高质量",
}

SOURCE_LIMITS = {
    "warehouse": 20,
    "core": 30,
    "all": 40,
}


def _build_stock_research_evidence_tool_names(agentic_tools: Iterable[Any]) -> set[str]:
    """
    Build the tool-name whitelist that satisfies stock-picker evidence gathering.

    Loader-only skill tools such as list_skills/load_skill/read_skill_file are intentionally excluded.
    """
    evidence_tool_names = {
        tool_name
        for tool_name in (getattr(tool_obj, "name", None) for tool_obj in agentic_tools)
        if isinstance(tool_name, str) and tool_name
    }
    evidence_tool_names.add("run_skill_script")
    return evidence_tool_names

RESEARCH_LIMIT_CAPS = {
    "warehouse": 12,
    "core": 15,
    "all": 18,
}

DEFAULT_FACTOR_LIMITS = {
    "warehouse": {"balanced": 10, "momentum": 12, "value": 10, "growth": 12, "defensive": 10},
    "core": {"balanced": 16, "momentum": 20, "value": 16, "growth": 20, "defensive": 16},
    "all": {"balanced": 20, "momentum": 24, "value": 20, "growth": 24, "defensive": 20},
}

DEFAULT_RESEARCH_LIMITS = {
    "warehouse": {"balanced": 6, "momentum": 8, "value": 6, "growth": 8, "defensive": 6},
    "core": {"balanced": 8, "momentum": 10, "value": 8, "growth": 10, "defensive": 8},
    "all": {"balanced": 10, "momentum": 12, "value": 10, "growth": 12, "defensive": 10},
}

FACTOR_MIN_COMPLETENESS_RATIO = 0.8
AI_PRIMARY_WEIGHT = 0.75
FACTOR_AUX_WEIGHT = 0.25
DEFAULT_SAME_INDUSTRY_LIMIT = 3

CREATED_STAGE = "created"
UNIVERSE_STAGE = "universe_built"
FACTOR_STAGE = "factor_ranked"
RESEARCH_STAGE = "ai_researched"
COMPLETED_STAGE = "completed"
FAILED_RECOMMENDATION_STAGE = "failed_recommendation"
NON_TERMINAL_RUN_STATUSES = ("created", "running")

FAILURE_STAGE_BY_CURRENT_STAGE = {
    CREATED_STAGE: "failed_universe",
    UNIVERSE_STAGE: "failed_factor",
    FACTOR_STAGE: "failed_ai_research",
    RESEARCH_STAGE: FAILED_RECOMMENDATION_STAGE,
}


@dataclass
class RankedCandidate:
    stock_code: str
    stock_name: str
    industry: Optional[str]
    market: Optional[str]
    factor_score: float
    ai_score: float
    final_score: float
    decision: str
    research_payload: Dict[str, Any]


class StockResearchOutputItem(BaseModel):
    stock_code: str = Field(..., min_length=1)
    ai_score: float = Field(..., ge=0, le=100)
    thesis: str = Field(..., min_length=1)
    catalysts: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    style_fit_explanation: str = Field(..., min_length=1)
    holding_horizon: str = Field(..., min_length=1)
    decision: Literal["keep", "watch", "drop"]


class StockResearchOutput(BaseModel):
    methodology_summary: str = ""
    comparative_view: List[str] = Field(default_factory=list)
    research: List[StockResearchOutputItem] = Field(default_factory=list)


def _mark_stage(
    service: "StockPickerService",
    db: Session,
    run: StockSelectionRun,
    *,
    stage: str,
    status: str,
) -> None:
    service._set_stage(db, run, stage, status)


def _record_stage_event(
    service: "StockPickerService",
    db: Session,
    run: StockSelectionRun,
    *,
    stage: str,
    event_type: str,
    message: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    service._record_event(
        db,
        run.run_id,
        stage=stage,
        event_type=event_type,
        message=message,
        payload=payload,
    )


def _complete_run(
    service: "StockPickerService",
    db: Session,
    run: StockSelectionRun,
    summary: Dict[str, Any],
) -> None:
    logger.info(
        "stock picker run completed: run_id=%s recommended=%s stage=%s",
        run.run_id,
        len(summary.get("recommended_stock_codes") or []),
        COMPLETED_STAGE,
    )
    run.status = COMPLETED_STAGE
    run.current_stage = COMPLETED_STAGE
    run.summary_payload = summary
    run.finished_at = service._now()
    db.commit()
    _record_stage_event(
        service,
        db,
        run,
        stage=COMPLETED_STAGE,
        event_type="completed",
        message=service._t("events.completed"),
        payload=summary,
    )


def _fail_run(
    service: "StockPickerService",
    db: Session,
    run: StockSelectionRun,
    exc: Exception,
) -> None:
    failed_stage = FAILURE_STAGE_BY_CURRENT_STAGE.get(run.current_stage, FAILED_RECOMMENDATION_STAGE)
    logger.error(
        "stock picker workflow failed: run_id=%s current_stage=%s failed_stage=%s error=%s",
        run.run_id,
        run.current_stage,
        failed_stage,
        exc,
    )
    run.status = failed_stage
    run.current_stage = failed_stage
    run.error_message = str(exc)
    run.finished_at = service._now()
    db.commit()
    _record_stage_event(
        service,
        db,
        run,
        stage=failed_stage,
        event_type="failed",
        message=str(exc),
        payload={"error": str(exc)},
    )


def create_stock_picker_workflow(
    service: "StockPickerService",
    db: Session,
    run: StockSelectionRun,
):
    run_config = service._get_run_config(run)

    async def universe_node(_state: StockPickerGraphState) -> Dict[str, Any]:
        logger.info(
            "stock picker stage start: run_id=%s stage=%s scope=%s allowed_industries=%s",
            run.run_id,
            UNIVERSE_STAGE,
            run.scope,
            run_config["allowed_industries"],
        )
        universe = service._build_universe(db, run.user_id, run.scope, run_config["allowed_industries"])
        if not universe:
            raise ValueError(service._t("errors.no_stocks_for_scope"))
        logger.info("stock picker stage ready: run_id=%s stage=%s universe_count=%s", run.run_id, UNIVERSE_STAGE, len(universe))

        _record_stage_event(
            service,
            db,
            run,
            stage=UNIVERSE_STAGE,
            event_type="universe_ready",
            message=service._t("events.universe_ready", count=len(universe)),
            payload={
                "source": run.scope,
                "count": len(universe),
                "industry_filter_count": len(run_config["allowed_industries"]),
                "allowed_industries": run_config["allowed_industries"],
                "same_industry_limit": run_config["same_industry_limit"],
            },
        )
        _mark_stage(service, db, run, stage=UNIVERSE_STAGE, status="running")
        return {"universe": universe}

    async def factor_node(state: StockPickerGraphState) -> Dict[str, Any]:
        universe = state.get("universe") or []
        logger.info(
            "stock picker stage start: run_id=%s stage=%s incoming_universe=%s style=%s scope=%s",
            run.run_id,
            FACTOR_STAGE,
            len(universe),
            run.style,
            run.scope,
        )
        ranked = service._rank_candidates(
            db,
            universe,
            run.style,
            run.scope,
            run_config["factor_candidate_limit"],
            run_config["same_industry_limit"],
        )
        if not ranked:
            raise ValueError(service._t("errors.no_candidates_passed_factor"))
        logger.info("stock picker stage ready: run_id=%s stage=%s candidate_count=%s", run.run_id, FACTOR_STAGE, len(ranked))

        service._replace_candidates(db, run.run_id, ranked, run.scope, run.style)
        _record_stage_event(
            service,
            db,
            run,
            stage=FACTOR_STAGE,
            event_type="factor_ranked",
            message=service._t("events.factor_ranked", count=len(ranked), style=run.style),
            payload={
                "count": len(ranked),
                "style": run.style,
                "factor_candidate_count": len(ranked),
                "factor_candidate_limit": run_config["factor_candidate_limit"],
                "same_industry_limit": run_config["same_industry_limit"],
            },
        )
        _mark_stage(service, db, run, stage=FACTOR_STAGE, status="running")
        return {"ranked_candidates": ranked}

    async def research_node(state: StockPickerGraphState) -> Dict[str, Any]:
        ranked = state.get("ranked_candidates") or []
        research_input = service._limit_research_candidates(ranked, run_config["research_candidate_limit"])
        logger.info(
            "stock picker stage start: run_id=%s stage=%s candidate_count=%s research_input_count=%s style=%s recommendation_count=%s",
            run.run_id,
            RESEARCH_STAGE,
            len(ranked),
            len(research_input),
            run.style,
            run.recommendation_count,
        )
        researched, research_mode = await service._research_candidates(research_input, run.style, run.recommendation_count)
        combined_candidates = service._merge_researched_candidates(
            ranked,
            researched,
            run_config["research_candidate_limit"],
        )
        logger.info(
            "stock picker stage ready: run_id=%s stage=%s researched_count=%s mode=%s",
            run.run_id,
            RESEARCH_STAGE,
            len(combined_candidates),
            research_mode,
        )

        service._replace_candidates(db, run.run_id, combined_candidates, run.scope, run.style)
        _record_stage_event(
            service,
            db,
            run,
            stage=RESEARCH_STAGE,
            event_type="ai_researched",
            message=service._t("events.ai_researched", count=len(combined_candidates)),
            payload={
                "count": len(combined_candidates),
                "mode": research_mode,
                "research_candidate_count": len(research_input),
            },
        )
        _mark_stage(service, db, run, stage=RESEARCH_STAGE, status="running")
        return {
            "researched_candidates": combined_candidates,
            "research_mode": research_mode,
        }

    async def complete_node(state: StockPickerGraphState) -> Dict[str, Any]:
        researched = state.get("researched_candidates") or []
        research_mode = state.get("research_mode") or "llm"
        logger.info(
            "stock picker stage start: run_id=%s stage=recommendations_built researched_count=%s mode=%s",
            run.run_id,
            len(researched),
            research_mode,
        )
        recommendations, recommendation_summary = service._build_recommendations(researched, run)
        summary = service._build_summary_metrics(
            researched,
            recommendations,
            research_mode,
            run_config,
            len(state.get("universe") or []),
            len(state.get("ranked_candidates") or []),
        )
        summary.update(recommendation_summary)
        logger.info(
            "stock picker stage ready: run_id=%s stage=recommendations_built recommendation_count=%s codes=%s",
            run.run_id,
            len(recommendations),
            summary.get("recommended_stock_codes") or [],
        )
        _record_stage_event(
            service,
            db,
            run,
            stage="recommendations_built",
            event_type="recommendations_ready",
            message=service._t("events.recommendations_ready", count=len(recommendations)),
            payload={
                "count": len(recommendations),
                "recommended_stock_codes": summary.get("recommended_stock_codes") or [],
                "same_industry_limit": run_config["same_industry_limit"],
            },
        )
        _complete_run(service, db, run, summary)
        return {
            "recommendations": recommendations,
            "summary": summary,
        }

    workflow = StateGraph(StockPickerGraphState)
    workflow.add_node("universe_step", universe_node)
    workflow.add_node("factor_step", factor_node)
    workflow.add_node("research_step", research_node)
    workflow.add_node("complete_step", complete_node)

    workflow.add_edge(START, "universe_step")
    workflow.add_edge("universe_step", "factor_step")
    workflow.add_edge("factor_step", "research_step")
    workflow.add_edge("research_step", "complete_step")
    workflow.add_edge("complete_step", END)
    return workflow.compile()


async def run_stock_picker_workflow(
    service: "StockPickerService",
    db: Session,
    run: StockSelectionRun,
) -> None:
    _mark_stage(service, db, run, stage=CREATED_STAGE, status="running")
    logger.info(
        "stock picker workflow started: run_id=%s scope=%s style=%s recommendation_count=%s risk_level=%s factor_candidate_limit=%s research_candidate_limit=%s same_industry_limit=%s",
        run.run_id,
        run.scope,
        run.style,
        run.recommendation_count,
        run.risk_level,
        (run.request_payload or {}).get("factor_candidate_limit"),
        (run.request_payload or {}).get("research_candidate_limit"),
        (run.request_payload or {}).get("same_industry_limit"),
    )
    try:
        workflow = create_stock_picker_workflow(service, db, run)
        await workflow.ainvoke({"summary": {}})
        logger.info("stock picker workflow finished: run_id=%s status=%s", run.run_id, run.status)
    except Exception as exc:
        _fail_run(service, db, run, exc)
        raise


class StockPickerService:
    @staticmethod
    def _t(key: str, **kwargs: Any) -> str:
        message = i18n_service.t(f"ai_stock_picker_backend.{key}")
        return message.format(**kwargs) if kwargs else message

    @staticmethod
    def _default_factor_candidate_limit(scope: str, style: str) -> int:
        return DEFAULT_FACTOR_LIMITS[scope][style]

    @staticmethod
    def _default_research_candidate_limit(scope: str, style: str) -> int:
        return DEFAULT_RESEARCH_LIMITS[scope][style]

    def list_industries(self, db: Session) -> List[str]:
        rows = (
            db.query(StockBasic.industry)
            .filter(StockBasic.industry.isnot(None))
            .distinct()
            .order_by(StockBasic.industry.asc())
            .all()
        )
        industries = sorted({industry.strip() for industry, in rows if isinstance(industry, str) and industry.strip()})
        logger.info("stock picker list_industries: count=%s", len(industries))
        return industries

    def _normalize_request_data(self, db: Session, request_data: Dict[str, Any]) -> Dict[str, Any]:
        scope = str(request_data["scope"])
        style = str(request_data["style"])
        recommendation_count = int(request_data.get("recommendation_count") or 5)
        risk_level = str(request_data.get("risk_level") or "medium")
        factor_candidate_limit = request_data.get("factor_candidate_limit")
        research_candidate_limit = request_data.get("research_candidate_limit")
        same_industry_limit = request_data.get("same_industry_limit")
        raw_allowed_industries = request_data.get("allowed_industries") or []

        if not isinstance(raw_allowed_industries, list):
            raise ValueError(self._t("errors.invalid_allowed_industries"))

        normalized_allowed_industries = sorted(
            {
                str(industry).strip()
                for industry in raw_allowed_industries
                if isinstance(industry, str) and str(industry).strip()
            }
        )
        available_industries = set(self.list_industries(db))
        invalid_industries = [industry for industry in normalized_allowed_industries if industry not in available_industries]
        if invalid_industries:
            raise ValueError(
                self._t("errors.invalid_allowed_industries_with_reason", industries=", ".join(invalid_industries[:10]))
            )

        default_factor_limit = max(self._default_factor_candidate_limit(scope, style), recommendation_count)
        default_research_limit = max(self._default_research_candidate_limit(scope, style), recommendation_count)
        normalized_factor_limit = int(factor_candidate_limit or default_factor_limit)
        normalized_research_limit = int(research_candidate_limit or default_research_limit)
        normalized_same_industry_limit = int(same_industry_limit or DEFAULT_SAME_INDUSTRY_LIMIT)

        if normalized_factor_limit > SOURCE_LIMITS[scope]:
            raise ValueError(
                self._t("errors.invalid_factor_candidate_limit", limit=normalized_factor_limit, max_limit=SOURCE_LIMITS[scope])
            )
        if normalized_research_limit > RESEARCH_LIMIT_CAPS[scope]:
            raise ValueError(
                self._t(
                    "errors.invalid_research_candidate_limit",
                    limit=normalized_research_limit,
                    max_limit=RESEARCH_LIMIT_CAPS[scope],
                )
            )
        if normalized_research_limit > normalized_factor_limit:
            raise ValueError(
                self._t(
                    "errors.invalid_candidate_limit_relation",
                    recommendation_count=recommendation_count,
                    research_limit=normalized_research_limit,
                    factor_limit=normalized_factor_limit,
                )
            )
        if recommendation_count > normalized_research_limit:
            raise ValueError(
                self._t(
                    "errors.invalid_candidate_limit_relation",
                    recommendation_count=recommendation_count,
                    research_limit=normalized_research_limit,
                    factor_limit=normalized_factor_limit,
                )
            )
        if normalized_same_industry_limit > recommendation_count:
            raise ValueError(
                self._t(
                    "errors.invalid_same_industry_limit",
                    limit=normalized_same_industry_limit,
                    recommendation_count=recommendation_count,
                )
            )

        return {
            "scope": scope,
            "style": style,
            "recommendation_count": recommendation_count,
            "risk_level": risk_level,
            "factor_candidate_limit": normalized_factor_limit,
            "research_candidate_limit": normalized_research_limit,
            "allowed_industries": normalized_allowed_industries,
            "same_industry_limit": normalized_same_industry_limit,
        }

    def _get_run_config(self, run: StockSelectionRun) -> Dict[str, Any]:
        payload = run.request_payload or {}
        recommendation_count = int(payload.get("recommendation_count") or run.recommendation_count)
        factor_candidate_limit = int(
            payload.get("factor_candidate_limit")
            or max(self._default_factor_candidate_limit(run.scope, run.style), recommendation_count)
        )
        research_candidate_limit = int(
            payload.get("research_candidate_limit")
            or max(self._default_research_candidate_limit(run.scope, run.style), recommendation_count)
        )
        same_industry_limit = int(payload.get("same_industry_limit") or DEFAULT_SAME_INDUSTRY_LIMIT)
        allowed_industries = [
            industry
            for industry in (payload.get("allowed_industries") or [])
            if isinstance(industry, str) and industry.strip()
        ]
        return {
            "scope": run.scope,
            "style": run.style,
            "risk_level": run.risk_level,
            "recommendation_count": recommendation_count,
            "factor_candidate_limit": factor_candidate_limit,
            "research_candidate_limit": research_candidate_limit,
            "same_industry_limit": same_industry_limit,
            "allowed_industries": allowed_industries,
        }

    def serialize_run_summary(self, run: StockSelectionRun) -> Dict[str, Any]:
        config = self._get_run_config(run)
        return {
            "run_id": run.run_id,
            "scope": run.scope,
            "style": run.style,
            "risk_level": run.risk_level,
            "recommendation_count": config["recommendation_count"],
            "factor_candidate_limit": config["factor_candidate_limit"],
            "research_candidate_limit": config["research_candidate_limit"],
            "allowed_industries": config["allowed_industries"],
            "same_industry_limit": config["same_industry_limit"],
            "status": run.status,
            "current_stage": run.current_stage,
            "error_message": run.error_message,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "created_at": run.created_at,
            "summary_payload": run.summary_payload,
        }

    def create_run(self, db: Session, user_id: int, request_data: Dict[str, Any]) -> StockSelectionRun:
        active_run = (
            db.query(StockSelectionRun)
            .filter(
                StockSelectionRun.user_id == user_id,
                StockSelectionRun.status.in_(NON_TERMINAL_RUN_STATUSES),
            )
            .order_by(StockSelectionRun.created_at.desc())
            .first()
        )
        if active_run:
            raise ValueError(self._t("errors.active_run_exists", run_id=active_run.run_id))

        normalized_request = self._normalize_request_data(db, request_data)
        run = StockSelectionRun(
            user_id=user_id,
            scope=normalized_request["scope"],
            style=normalized_request["style"],
            risk_level=normalized_request["risk_level"],
            recommendation_count=normalized_request["recommendation_count"],
            request_payload=normalized_request,
            status="created",
            current_stage="created",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        logger.info(
            "stock picker run created: run_id=%s user_id=%s scope=%s style=%s recommendation_count=%s risk_level=%s factor_candidate_limit=%s research_candidate_limit=%s same_industry_limit=%s",
            run.run_id,
            user_id,
            run.scope,
            run.style,
            run.recommendation_count,
            run.risk_level,
            normalized_request["factor_candidate_limit"],
            normalized_request["research_candidate_limit"],
            normalized_request["same_industry_limit"],
        )
        self._record_event(
            db,
            run.run_id,
            stage="created",
            event_type="run_created",
            message=self._t("events.run_created"),
            payload=normalized_request,
            push=False,
        )
        return run

    def list_runs(self, db: Session, user_id: int) -> List[StockSelectionRun]:
        return (
            db.query(StockSelectionRun)
            .filter(StockSelectionRun.user_id == user_id)
            .order_by(StockSelectionRun.created_at.desc())
            .all()
        )

    def get_run(self, db: Session, run_id: UUID, user_id: int) -> Optional[StockSelectionRun]:
        return (
            db.query(StockSelectionRun)
            .filter(StockSelectionRun.run_id == run_id, StockSelectionRun.user_id == user_id)
            .first()
        )

    def get_events(self, db: Session, run_id: UUID, user_id: int) -> List[StockSelectionEvent]:
        run = self.get_run(db, run_id, user_id)
        if not run:
            return []
        return (
            db.query(StockSelectionEvent)
            .filter(StockSelectionEvent.run_id == run_id)
            .order_by(StockSelectionEvent.created_at.asc(), StockSelectionEvent.id.asc())
            .all()
        )

    def get_candidates(self, db: Session, run_id: UUID, user_id: int) -> List[StockSelectionCandidate]:
        run = self.get_run(db, run_id, user_id)
        if not run:
            return []
        return (
            db.query(StockSelectionCandidate)
            .filter(StockSelectionCandidate.run_id == run_id)
            .order_by(StockSelectionCandidate.final_score.desc(), StockSelectionCandidate.id.asc())
            .all()
        )

    def delete_run(self, db: Session, run_id: UUID, user_id: int) -> bool:
        run = self.get_run(db, run_id, user_id)
        if not run:
            return False
        db.delete(run)
        db.commit()
        return True

    def delete_all_runs(self, db: Session, user_id: int) -> int:
        runs = self.list_runs(db, user_id)
        count = 0
        for run in runs:
            self.delete_run(db, run.run_id, user_id)
            count += 1
        return count

    def build_result(self, db: Session, run: StockSelectionRun) -> Dict[str, Any]:
        run_summary = self.serialize_run_summary(run)
        candidates = self.get_candidates(db, run.run_id, run.user_id)
        summary_payload = run.summary_payload or {}
        recommended_codes = summary_payload.get("recommended_stock_codes") or []
        candidate_map = {candidate.stock_code: candidate for candidate in candidates}
        recommendations = []
        risk_flags: List[str] = []
        for rank, stock_code in enumerate(recommended_codes, start=1):
            candidate = candidate_map.get(stock_code)
            if not candidate:
                continue
            research_payload = candidate.research_payload or {}
            candidate_risks = research_payload.get("risks") or []
            risk_flags.extend(candidate_risks)
            recommendations.append(
                {
                    "stock_code": candidate.stock_code,
                    "stock_name": research_payload.get("stock_name") or candidate.stock_code,
                    "rank": rank,
                    "conviction_score": round(candidate.final_score, 2),
                    "recommendation_reason": research_payload.get("thesis", ""),
                    "risk_flags": candidate_risks,
                    "holding_horizon": research_payload.get("holding_horizon", ""),
                    "decision": candidate.decision,
                }
            )
        alternatives = [
            candidate for candidate in candidates
            if candidate.stock_code not in set(recommended_codes) and candidate.decision != "drop"
        ]
        return {
            "run": run_summary,
            "summary": summary_payload,
            "recommendations": {
                "stocks": recommendations,
                "recommendation_logic": summary_payload.get("selection_logic", ""),
                "style": run.style,
                "scope": run.scope,
                "generated_at": run.finished_at or run.updated_at,
            },
            "alternatives": [
                {
                    "stock_code": item.stock_code,
                    "factor_score": item.factor_score,
                    "ai_score": item.ai_score,
                    "final_score": item.final_score,
                    "quant_support": (item.research_payload or {}).get("quant_support"),
                    "decision": item.decision,
                    "eliminated_stage": item.eliminated_stage,
                    "eliminated_reason": item.eliminated_reason,
                    "research_payload": item.research_payload,
                }
                for item in alternatives
            ],
            "risk_summary": {
                "risk_level": run.risk_level,
                "distinct_flags": sorted(set(risk_flags)),
            },
        }

    def _cleanup_interrupted_runs_in_db(self, db: Session) -> int:
        runs = (
            db.query(StockSelectionRun)
            .filter(StockSelectionRun.status.in_(NON_TERMINAL_RUN_STATUSES))
            .all()
        )
        if not runs:
            logger.info("stock picker restart cleanup: no interrupted runs found")
            return 0

        failure_message = self._t("errors.run_interrupted_by_restart")
        logger.warning("stock picker restart cleanup: interrupted_runs=%s", len(runs))
        for run in runs:
            failed_stage = FAILURE_STAGE_BY_CURRENT_STAGE.get(run.current_stage, FAILED_RECOMMENDATION_STAGE)
            logger.warning(
                "stock picker restart cleanup: run_id=%s status=%s current_stage=%s -> %s",
                run.run_id,
                run.status,
                run.current_stage,
                failed_stage,
            )
            run.status = failed_stage
            run.current_stage = failed_stage
            run.error_message = failure_message
            run.finished_at = self._now()
            db.commit()
            self._record_event(
                db,
                run.run_id,
                stage=failed_stage,
                event_type="failed",
                message=failure_message,
                payload={"error": failure_message, "reason": "restart_recovery"},
                push=False,
            )
        return len(runs)

    def cleanup_interrupted_runs(self) -> int:
        with SessionLocal() as db:
            return self._cleanup_interrupted_runs_in_db(db)

    async def execute_run(self, run_id: UUID) -> None:
        with SessionLocal() as db:
            run = db.query(StockSelectionRun).filter(StockSelectionRun.run_id == run_id).first()
            if not run:
                logger.warning("stock picker execute skipped: run_id=%s not found", run_id)
                return
            try:
                await run_stock_picker_workflow(self, db, run)
            except Exception as exc:
                logger.exception("stock picker run failed: %s", exc)

    def _set_stage(self, db: Session, run: StockSelectionRun, stage: str, status: str) -> None:
        if stage == "created" and not run.started_at:
            run.started_at = datetime.now()
        run.current_stage = stage
        run.status = status
        db.commit()
        logger.info("stock picker state updated: run_id=%s stage=%s status=%s", run.run_id, stage, status)

    def _build_universe(
        self,
        db: Session,
        user_id: int,
        scope: str,
        allowed_industries: Optional[List[str]] = None,
    ) -> List[StockBasic]:
        logger.info(
            "stock picker build_universe: user_id=%s scope=%s allowed_industries=%s",
            user_id,
            scope,
            allowed_industries or [],
        )
        base_query = (
            db.query(StockBasic)
            .filter(
                get_basic_stock_filter_conds(),
                StockBasic.status == "L",
                StockBasic.name.isnot(None),
            )
        )
        if allowed_industries:
            base_query = base_query.filter(StockBasic.industry.in_(allowed_industries))
        if scope == "warehouse":
            codes = (
                db.query(StockWarehouse.stock_code)
                .filter(StockWarehouse.user_id == user_id, StockWarehouse.is_active.is_(True))
                .all()
            )
            stock_codes = [row[0] for row in codes]
            if not stock_codes:
                logger.warning("stock picker build_universe: user_id=%s scope=%s warehouse is empty", user_id, scope)
                return []
            universe = base_query.filter(StockBasic.stock_code.in_(stock_codes)).all()
            logger.info("stock picker build_universe done: user_id=%s scope=%s count=%s", user_id, scope, len(universe))
            return universe

        if scope == "core":
            stock_codes = self._get_core_codes(db)
            if not stock_codes:
                logger.warning("stock picker build_universe: scope=%s core codes empty", scope)
                return []
            universe = base_query.filter(StockBasic.stock_code.in_(stock_codes)).all()
            logger.info("stock picker build_universe done: user_id=%s scope=%s count=%s", user_id, scope, len(universe))
            return universe

        universe = base_query.all()
        logger.info("stock picker build_universe done: user_id=%s scope=%s count=%s", user_id, scope, len(universe))
        return universe

    def _get_core_codes(self, db: Session) -> List[str]:
        del db
        resolved_codes = get_core_index_constituent_codes()
        logger.info("stock picker get_core_codes: resolved_count=%s", len(resolved_codes))
        return resolved_codes

    def _rank_candidates(
        self,
        db: Session,
        universe: List[StockBasic],
        style: str,
        scope: str,
        factor_candidate_limit: Optional[int] = None,
        same_industry_limit: Optional[int] = None,
    ) -> List[RankedCandidate]:
        logger.info(
            "stock picker rank_candidates: scope=%s style=%s universe_count=%s factor_candidate_limit=%s same_industry_limit=%s",
            scope,
            style,
            len(universe),
            factor_candidate_limit,
            same_industry_limit,
        )
        stock_codes = [item.stock_code for item in universe]
        latest_val_sub = (
            db.query(
                StockValuationHistory.stock_code.label("stock_code"),
                func.max(StockValuationHistory.data_date).label("max_date"),
            )
            .filter(StockValuationHistory.stock_code.in_(stock_codes))
            .group_by(StockValuationHistory.stock_code)
            .subquery()
        )
        latest_kline_sub = (
            db.query(
                KlineData.stock_code.label("stock_code"),
                func.max(KlineData.date).label("max_date"),
            )
            .filter(KlineData.stock_code.in_(stock_codes), KlineData.freq == "D")
            .group_by(KlineData.stock_code)
            .subquery()
        )
        latest_indicator_sub = (
            db.query(
                StockIndicators.stock_code.label("stock_code"),
                func.max(StockIndicators.trade_date).label("max_date"),
            )
            .filter(StockIndicators.stock_code.in_(stock_codes))
            .group_by(StockIndicators.stock_code)
            .subquery()
        )

        rows = (
            db.query(StockBasic, StockValuationHistory, KlineData, StockIndicators)
            .outerjoin(latest_val_sub, StockBasic.stock_code == latest_val_sub.c.stock_code)
            .outerjoin(
                StockValuationHistory,
                and_(
                    StockValuationHistory.stock_code == latest_val_sub.c.stock_code,
                    StockValuationHistory.data_date == latest_val_sub.c.max_date,
                ),
            )
            .outerjoin(latest_kline_sub, StockBasic.stock_code == latest_kline_sub.c.stock_code)
            .outerjoin(
                KlineData,
                and_(
                    KlineData.stock_code == latest_kline_sub.c.stock_code,
                    KlineData.date == latest_kline_sub.c.max_date,
                    KlineData.freq == "D",
                ),
            )
            .outerjoin(latest_indicator_sub, StockBasic.stock_code == latest_indicator_sub.c.stock_code)
            .outerjoin(
                StockIndicators,
                and_(
                    StockIndicators.stock_code == latest_indicator_sub.c.stock_code,
                    StockIndicators.trade_date == latest_indicator_sub.c.max_date,
                ),
            )
            .filter(StockBasic.stock_code.in_(stock_codes))
            .all()
        )

        ranked: List[RankedCandidate] = []
        factor_errors: List[str] = []
        total_rows = len(rows)
        for basic, val, kline, indicators in rows:
            try:
                quant_inputs = self._resolve_quant_inputs(basic, val, kline, indicators)
            except ValueError as exc:
                factor_errors.append(str(exc))
                continue
            quant_support = self._compute_quant_support(style, quant_inputs)
            score = quant_support["final_quant_score"]
            research_payload = {
                "stock_name": basic.name,
                "industry": basic.industry,
                "market": basic.market,
                "quant_support": quant_support,
                "quant_summary": self._build_quant_summary(style, basic, quant_inputs, quant_support),
            }
            ranked.append(
                RankedCandidate(
                    stock_code=basic.stock_code,
                    stock_name=basic.name,
                    industry=basic.industry,
                    market=basic.market,
                    factor_score=score,
                    ai_score=0.0,
                    final_score=score,
                    decision="watch",
                    research_payload=research_payload,
                )
            )

        completeness_ratio = (len(ranked) / total_rows) if total_rows > 0 else 0.0
        logger.info(
            "stock picker rank_candidates stats: scope=%s style=%s total_rows=%s complete_rows=%s completeness_ratio=%.4f factor_errors=%s",
            scope,
            style,
            total_rows,
            len(ranked),
            completeness_ratio,
            len(factor_errors),
        )
        if total_rows > 0 and completeness_ratio < FACTOR_MIN_COMPLETENESS_RATIO:
            preview = "; ".join(factor_errors[:5])
            remaining = len(factor_errors) - 5
            if remaining > 0:
                preview = f"{preview}; {self._t('errors.fragments.more_missing_quant_fields', count=remaining)}"
            ratio_summary = self._t(
                "errors.fragments.factor_completeness_ratio",
                complete_count=len(ranked),
                total_count=total_rows,
                completeness_ratio=round(completeness_ratio * 100, 2),
                required_ratio=int(FACTOR_MIN_COMPLETENESS_RATIO * 100),
            )
            reason = ratio_summary if not preview else f"{ratio_summary}; {preview}"
            raise ValueError(self._t("errors.factor_data_incomplete", reason=reason))

        ranked.sort(key=lambda item: item.factor_score, reverse=True)
        selected = self._apply_industry_limit(
            ranked,
            factor_candidate_limit or SOURCE_LIMITS[scope],
            same_industry_limit,
        )
        logger.info(
            "stock picker rank_candidates done: scope=%s style=%s selected=%s industry_limited=%s",
            scope,
            style,
            len(selected),
            same_industry_limit is not None,
        )
        return selected

    def _apply_industry_limit(
        self,
        ranked: List[RankedCandidate],
        candidate_limit: int,
        same_industry_limit: Optional[int],
    ) -> List[RankedCandidate]:
        if same_industry_limit is None or same_industry_limit >= candidate_limit:
            logger.info(
                "stock picker apply_industry_limit skipped: candidate_limit=%s same_industry_limit=%s",
                candidate_limit,
                same_industry_limit,
            )
            return ranked[:candidate_limit]

        selected: List[RankedCandidate] = []
        industry_counts: Dict[str, int] = {}

        for candidate in ranked:
            industry = candidate.industry or "未分类"
            current_count = industry_counts.get(industry, 0)
            if current_count < same_industry_limit:
                selected.append(candidate)
                industry_counts[industry] = current_count + 1
            if len(selected) >= candidate_limit:
                break
        logger.info(
            "stock picker apply_industry_limit done: input=%s output=%s candidate_limit=%s industry_limit=%s industry_counts=%s",
            len(ranked),
            len(selected[:candidate_limit]),
            candidate_limit,
            same_industry_limit,
            industry_counts,
        )
        return selected[:candidate_limit]

    def _limit_research_candidates(
        self,
        ranked: List[RankedCandidate],
        research_candidate_limit: int,
    ) -> List[RankedCandidate]:
        limited = ranked[:research_candidate_limit]
        logger.info(
            "stock picker limit_research_candidates: input=%s output=%s research_candidate_limit=%s",
            len(ranked),
            len(limited),
            research_candidate_limit,
        )
        return limited

    def _merge_researched_candidates(
        self,
        ranked: List[RankedCandidate],
        researched: List[RankedCandidate],
        research_candidate_limit: int,
    ) -> List[RankedCandidate]:
        researched_map = {candidate.stock_code: candidate for candidate in researched}
        merged: List[RankedCandidate] = []
        for index, candidate in enumerate(ranked):
            if candidate.stock_code in researched_map:
                merged.append(researched_map[candidate.stock_code])
                continue
            if index < research_candidate_limit:
                fallback_payload = dict(candidate.research_payload)
                fallback_payload["eliminated_reason"] = self._t("defaults.not_selected_after_research")
                merged.append(
                    RankedCandidate(
                        stock_code=candidate.stock_code,
                        stock_name=candidate.stock_name,
                        industry=candidate.industry,
                        market=candidate.market,
                        factor_score=candidate.factor_score,
                        ai_score=candidate.ai_score,
                        final_score=candidate.final_score,
                        decision="drop",
                        research_payload=fallback_payload,
                    )
                )
                continue
            fallback_payload = dict(candidate.research_payload)
            fallback_payload["eliminated_reason"] = self._t("defaults.not_selected_for_research")
            merged.append(
                RankedCandidate(
                    stock_code=candidate.stock_code,
                    stock_name=candidate.stock_name,
                    industry=candidate.industry,
                    market=candidate.market,
                    factor_score=candidate.factor_score,
                    ai_score=candidate.ai_score,
                    final_score=candidate.final_score,
                    decision="drop",
                    research_payload=fallback_payload,
                )
            )
        logger.info(
            "stock picker merge_researched_candidates: ranked=%s researched=%s merged=%s research_candidate_limit=%s",
            len(ranked),
            len(researched),
            len(merged),
            research_candidate_limit,
        )
        return merged

    def _resolve_quant_inputs(
        self,
        basic: StockBasic,
        val: Optional[StockValuationHistory],
        kline: Optional[KlineData],
        indicators: Optional[StockIndicators],
    ) -> Dict[str, float]:
        raw_values = {
            "valuation.pe_ttm": val.pe_ttm if val else None,
            "valuation.pb": val.pb if val else None,
            "valuation.ps_ttm": val.ps_ttm if val else None,
            "valuation.dividend_yield": val.dividend_yield if val else None,
            "valuation.total_market_value": val.total_market_value if val else None,
            "kline.close": kline.close if kline else None,
            "kline.volume": kline.volume if kline else None,
            "kline.turnover": kline.turnover if kline else None,
            "stock_indicators.macd": indicators.macd if indicators else None,
            "stock_indicators.macd_signal": indicators.macd_signal if indicators else None,
            "stock_indicators.rsi_12": indicators.rsi_12 if indicators else None,
            "stock_indicators.rsi_24": indicators.rsi_24 if indicators else None,
            "stock_indicators.kdj_j": indicators.kdj_j if indicators else None,
            "stock_indicators.atr": indicators.atr if indicators else None,
        }
        missing_fields = [
            field_name
            for field_name, value in raw_values.items()
            if safe_float(value, allow_non_finite=False) is None
        ]
        if missing_fields:
            raise ValueError(
                self._t(
                    "errors.fragments.missing_quant_fields",
                    stock_code=basic.stock_code,
                    stock_name=basic.name or basic.stock_code,
                    fields=", ".join(missing_fields),
                )
            )

        close = safe_float(raw_values["kline.close"], allow_non_finite=False)
        atr = safe_float(raw_values["stock_indicators.atr"], allow_non_finite=False)
        macd = safe_float(raw_values["stock_indicators.macd"], allow_non_finite=False)
        macd_signal = safe_float(raw_values["stock_indicators.macd_signal"], allow_non_finite=False)
        assert close is not None
        assert atr is not None
        assert macd is not None
        assert macd_signal is not None

        return {
            "pe": safe_float(raw_values["valuation.pe_ttm"], allow_non_finite=False) or 0.0,
            "pb": safe_float(raw_values["valuation.pb"], allow_non_finite=False) or 0.0,
            "ps_ttm": safe_float(raw_values["valuation.ps_ttm"], allow_non_finite=False) or 0.0,
            "dividend_yield": safe_float(raw_values["valuation.dividend_yield"], allow_non_finite=False) or 0.0,
            "market_cap": safe_float(raw_values["valuation.total_market_value"], allow_non_finite=False) or 0.0,
            "close": close,
            "volume": safe_float(raw_values["kline.volume"], allow_non_finite=False) or 0.0,
            "turnover_amount": safe_float(raw_values["kline.turnover"], allow_non_finite=False) or 0.0,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_hist": macd - macd_signal,
            "rsi_12": safe_float(raw_values["stock_indicators.rsi_12"], allow_non_finite=False) or 0.0,
            "rsi_24": safe_float(raw_values["stock_indicators.rsi_24"], allow_non_finite=False) or 0.0,
            "kdj_j": safe_float(raw_values["stock_indicators.kdj_j"], allow_non_finite=False) or 0.0,
            "atr": atr,
            "atr_pct": (atr / close * 100.0) if close > 0 else 0.0,
        }

    def _compute_quant_support(
        self,
        style: str,
        quant_inputs: Dict[str, float],
    ) -> Dict[str, float]:
        pe = quant_inputs["pe"]
        pb = quant_inputs["pb"]
        ps_ttm = quant_inputs["ps_ttm"]
        dividend_yield = quant_inputs["dividend_yield"]
        market_cap = quant_inputs["market_cap"]
        turnover_amount = quant_inputs["turnover_amount"]
        volume = quant_inputs["volume"]
        macd_hist = quant_inputs["macd_hist"]
        rsi_12 = quant_inputs["rsi_12"]
        rsi_24 = quant_inputs["rsi_24"]
        kdj_j = quant_inputs["kdj_j"]
        atr_pct = quant_inputs["atr_pct"]
        liquidity_score = min(turnover_amount / 1e9, 18) + min(volume / 1e8, 12)
        technical_momentum_score = (
            min(max(0.0, macd_hist) * 120, 18)
            + max(0.0, rsi_12 - 50) * 0.45
            + max(0.0, kdj_j - 50) * 0.12
        )
        technical_balance_score = (
            min(max(0.0, macd_hist) * 80, 10)
            + max(0.0, rsi_12 - 48) * 0.25
        )
        stability_score = max(0.0, 18 - atr_pct * 4) + max(0.0, 15 - abs(rsi_24 - 50) * 0.3)

        if style == "momentum":
            style_fit_score = technical_momentum_score
        elif style == "value":
            style_fit_score = max(0.0, 22 - pe) * 1.7 + max(0.0, 3.2 - pb) * 8 + dividend_yield * 5
        elif style == "growth":
            style_fit_score = technical_momentum_score * 0.7 + max(0.0, 10 - ps_ttm) * 5
        elif style == "defensive":
            style_fit_score = dividend_yield * 6 + min(market_cap / 1e11, 14) + stability_score
        else:
            style_fit_score = (
                technical_balance_score * 1.15
                + stability_score * 0.35
                + max(0.0, 18 - pe) * 0.6
                + max(0.0, 2.5 - pb) * 2.5
                + min(dividend_yield, 5.0) * 0.8
            )

        risk_penalty = (
            max(0.0, pe - 45) * 0.35
            + max(0.0, pb - 6) * 1.5
            + max(0.0, atr_pct - 4) * 3
            + max(0.0, 5e8 - turnover_amount) / 1e8 * 0.5
        )
        final_quant_score = max(0.0, min(100.0, style_fit_score + liquidity_score - risk_penalty))
        return {
            "style_fit_score": round(max(0.0, min(100.0, style_fit_score)), 2),
            "liquidity_score": round(max(0.0, min(30.0, liquidity_score)), 2),
            "risk_penalty": round(max(0.0, min(30.0, risk_penalty)), 2),
            "final_quant_score": round(final_quant_score, 2),
        }

    def _build_quant_summary(
        self,
        style: str,
        basic: StockBasic,
        quant_inputs: Dict[str, float],
        quant_support: Dict[str, float],
    ) -> Dict[str, Any]:
        return {
            "style_label": STYLE_LABELS.get(style, style),
            "thesis": (
                f"{basic.name} 的量化辅助信号显示风格匹配分 {quant_support['style_fit_score']:.2f}，"
                f"流动性分 {quant_support['liquidity_score']:.2f}，风险扣分 {quant_support['risk_penalty']:.2f}。"
            ),
            "catalysts": [
                f"行业：{basic.industry or '未分类'}",
                f"市场：{basic.market or '未分类'}",
                f"PE(TTM)：{quant_inputs['pe']:.2f}",
                f"股息率：{quant_inputs['dividend_yield']:.2f}%",
                f"MACD 柱：{quant_inputs['macd_hist']:.4f}",
                f"RSI12：{quant_inputs['rsi_12']:.2f}",
                f"ATR 波动率：{quant_inputs['atr_pct']:.2f}%",
            ],
            "risks": [
                "结果仍依赖市场数据完整性",
                "技术指标和估值指标只作为 LLM 研究的辅助证据",
            ],
            "support_note": (
                f"{STYLE_LABELS.get(style, style)} 风格下，最终量化辅助分 "
                f"{quant_support['final_quant_score']:.2f}。"
            ),
        }

    async def _research_candidates(
        self,
        ranked: List[RankedCandidate],
        style: str,
        recommendation_count: int,
    ) -> tuple[List[RankedCandidate], str]:
        logger.info(
            "stock picker research_candidates: style=%s recommendation_count=%s candidate_count=%s",
            style,
            recommendation_count,
            len(ranked),
        )
        llm_payload = await self._request_llm_research(ranked, style, recommendation_count)
        if not llm_payload:
            raise ValueError(self._t("errors.research_generation_failed"))
        normalized, research_error = self._normalize_llm_research(llm_payload, ranked, style, recommendation_count)
        if not normalized:
            raise ValueError(research_error or self._t("errors.research_payload_invalid"))
        logger.info("stock picker research_candidates done: normalized_count=%s mode=llm", len(normalized))
        return normalized, "llm"

    async def _request_llm_research(
        self,
        ranked: List[RankedCandidate],
        style: str,
        recommendation_count: int,
    ) -> Optional[Dict[str, Any]]:
        start_time, end_time = self._research_time_window()
        candidate_summaries = [
            {
                "stock_code": candidate.stock_code,
                "stock_name": candidate.stock_name,
                "industry": candidate.industry,
                "market": candidate.market,
                "factor_score": round(candidate.factor_score, 2),
                "quant_support": candidate.research_payload.get("quant_support", {}),
                "quant_summary": candidate.research_payload.get("quant_summary", {}),
            }
            for candidate in ranked
        ]
        skills_catalog_prompt = build_skills_catalog_prompt()
        skills_prompt_suffix = f"\n\n{skills_catalog_prompt}" if skills_catalog_prompt else ""
        agentic_tools = get_all_tools()
        evidence_tool_names = _build_stock_research_evidence_tool_names(agentic_tools)
        tools = [*agentic_tools, *get_skills_loader_tools()]
        tool_map = {tool_obj.name: tool_obj for tool_obj in tools}
        llm_provider = get_llm_provider()
        raw_llm = llm_provider.build_chat_model(
            model=settings.LLM_MODEL,
        )
        llm = raw_llm.bind_tools(tools)
        logger.info(
            "stock picker request_llm_research: style=%s recommendation_count=%s candidate_count=%s tool_count=%s window=%s..%s",
            style,
            recommendation_count,
            len(candidate_summaries),
            len(tools),
            start_time,
            end_time,
        )
        messages: List[Any] = [
            SystemMessage(
                content=(
                    "你是资深 A 股选股研究员，负责对同一批候选股票做整池比较研究，并给出结构化研究结论。"
                    "请像原有 agentic 分析流程那样先思考研究路径，再主动调用工具补充证据，然后基于证据完成论证。"
                    "你必须先调用 backend/app/ai/agentic/tools.py 中的一个或多个工具，然后才能输出最终结论。"
                    "不要把候选池拆成多个独立任务；要在同一个研究会话里，对整批股票做横向比较、取舍、排序和论证。"
                    "你可以自行决定调用哪些现有工具、调用多少次、如何组合工具，只要这些调用能帮助你获取足够证据。"
                    "你的结论必须以研究判断和工具证据为主导，量化因子只作为辅助参考，不要机械跟随 factor_score。"
                    "完成工具调用和比较分析后，再一次性输出最终 JSON 对象。"
                    f"{skills_prompt_suffix}"
                    f"最终输出必须满足以下 JSON Schema: {stable_json_dumps(StockResearchOutput.model_json_schema())}"
                    "\n\n"
                    "You are a senior A-share stock research analyst. Your job is to evaluate the entire candidate pool comparatively and produce a structured research output."
                    "Follow the spirit of the original agentic analyst flow: first decide the research approach, then proactively call tools to gather evidence, and finally reason from that evidence."
                    "You must call one or more tools from backend/app/ai/agentic/tools.py before giving the final answer."
                    "Do not split the candidate pool into isolated tasks. Keep the whole pool in one research session and make comparative judgments across all candidates."
                    "You should decide for yourself which existing tools to call, how many times to call them, and how to combine them, as long as you gather enough evidence."
                    "Your final judgment must be driven by research reasoning and tool evidence, with quantitative factor signals used only as supporting context."
                    "After gathering evidence and completing the comparative analysis, return exactly one final JSON object."
                    f"{skills_prompt_suffix}"
                    f"Your final output must satisfy this JSON Schema: {stable_json_dumps(StockResearchOutput.model_json_schema())}"
                )
            ),
            HumanMessage(
                content=(
                    f"风格 / Style: {STYLE_LABELS.get(style, style)}\n"
                    f"推荐股票数 / Target recommendation count: {recommendation_count}\n"
                    "请自行判断需要调用哪些工具来补足证据，并在证据充分后再给出结论。"
                    "可以灵活组合任意相关工具，只要能够补足当前候选池比较所需的证据。"
                    "Decide for yourself which tools are needed, gather enough evidence, and only then provide your conclusion. You may combine any relevant tools as needed to complete the comparative research.\n"
                    "候选股票池如下，请整体比较而不是孤立点评 / Candidate pool below; compare them as a whole rather than in isolation:\n"
                    f"{stable_json_dumps(candidate_summaries)}\n"
                )
            ),
        ]
        used_evidence_tools = False
        try:
            for iteration in range(50):
                logger.info(
                    "stock picker request_llm_research iteration: iteration=%s candidate_count=%s "
                    "message_count=%s used_evidence_tools=%s",
                    iteration + 1,
                    len(candidate_summaries),
                    len(messages),
                    used_evidence_tools,
                )
                response = await llm.ainvoke(messages)
                cache_lane, api_key_alias = get_research_usage_lane()
                record_llm_usage(
                    response,
                    settings.LLM_MODEL,
                    "stock_picker_research",
                    workflow="stock_picker",
                    stage="research",
                    call_kind="agent",
                    iteration_index=iteration + 1,
                    cache_lane=cache_lane,
                    api_key_alias=api_key_alias,
                )
                response, invalid_tool_calls = llm_provider.sanitize_tool_call_response_for_replay(response)
                messages.append(response)

                if response.tool_calls or invalid_tool_calls:
                    logger.info(
                        "stock picker request_llm_research tool_calls: iteration=%s tool_call_count=%s",
                        iteration + 1,
                        len(response.tool_calls),
                    )
                    for tool_call in response.tool_calls:
                        tool_name = tool_call["name"]
                        tool_func = tool_map.get(tool_name)
                        if not tool_func:
                            logger.warning(
                                "stock picker request_llm_research unsupported_tool: iteration=%s tool=%s",
                                iteration + 1,
                                tool_name,
                            )
                            messages.append(
                                ToolMessage(
                                    tool_call_id=tool_call["id"],
                                    content=stable_json_dumps({"error": f"unsupported tool: {tool_name}"}),
                                )
                            )
                            continue
                        if tool_name in evidence_tool_names:
                            used_evidence_tools = True
                        logger.info(
                            "stock picker request_llm_research execute_tool: iteration=%s tool=%s args_keys=%s",
                            iteration + 1,
                            tool_name,
                            tool_call["args"],
                        )
                        tool_result = await tool_func.ainvoke(tool_call["args"])
                        tool_payload = stable_json_dumps(make_json_serializable(tool_result))
                        logger.info(
                            "stock picker request_llm_research tool_result: iteration=%s tool=%s payload_len=%s",
                            iteration + 1,
                            tool_name,
                            tool_payload[:200],
                        )
                        if should_summarize_tool_output(tool_name, tool_payload):
                            logger.info(
                                "stock picker request_llm_research summarize_tool_output: iteration=%s tool=%s payload_len=%s",
                                iteration + 1,
                                tool_name,
                                len(tool_payload),
                            )
                            tool_payload = await summarize_tool_output(
                                raw_llm,
                                role_name="stock_picker_research",
                                tool_name=tool_name,
                                content=tool_payload,
                                tool_args=tool_call["args"],
                                workflow="stock_picker",
                                stage="research_tool_summary",
                                iteration_index=iteration + 1,
                            )
                            logger.info(
                                "stock picker request_llm_research summarized_tool_output: iteration=%s tool=%s summarized_len=%s",
                                iteration + 1,
                                tool_name,
                                tool_payload,
                            )
                        messages.append(
                            ToolMessage(
                                tool_call_id=tool_call["id"],
                                content=tool_payload,
                            )
                        )
                    if invalid_tool_calls:
                        logger.warning(
                            "stock picker request_llm_research invalid_tool_calls: iteration=%s invalid_tool_call_count=%s",
                            iteration + 1,
                            len(invalid_tool_calls),
                        )
                        messages.append(
                            HumanMessage(
                                content=llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls)
                            )
                        )
                    continue

                validated_output = self._parse_research_output(response.content)
                if validated_output is not None:
                    if not used_evidence_tools:
                        logger.warning(
                            "stock picker request_llm_research rejected_no_evidence_tools: iteration=%s",
                            iteration + 1,
                        )
                        messages.append(
                            HumanMessage(
                                content=(
                                    "你还没有调用任何证据工具。请先调用行情、财务、新闻、数据库查询或 "
                                    "run_skill_script 等能获取研究证据的工具，再输出最终 JSON。"
                                    "You have not called any evidence-gathering tool yet. Call market, "
                                    "financial, news, database, or run_skill_script tools to gather evidence, "
                                    "then return the final JSON again."
                                )
                            )
                        )
                        continue
                    logger.info(
                        "stock picker request_llm_research validated_output: iteration=%s research_count=%s",
                        iteration + 1,
                        len(validated_output.research),
                    )
                    return validated_output.model_dump(mode="python")

                logger.warning(
                    "stock picker request_llm_research invalid_structured_output: iteration=%s content_type=%s",
                    iteration + 1,
                    type(response.content).__name__,
                )
                messages.append(
                    HumanMessage(
                        content=(
                            "你的最终输出未通过结构化校验。请严格按照给定 JSON Schema 返回对象，不要输出 markdown，不要输出额外解释。"
                            "Your final output did not pass structured validation. Return an object that strictly follows the given JSON Schema, with no markdown and no extra explanation."
                        )
                    )
                )

            if used_evidence_tools:
                logger.warning("LLM research generation failed: max tool-calling iterations reached")
        except Exception as exc:
            logger.warning("LLM research generation failed: %s", exc)
        return None

    @staticmethod
    def _parse_json_response_content(content: Any) -> Optional[Dict[str, Any]]:
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        text_parts.append(text)
                elif isinstance(item, str):
                    text_parts.append(item)
            if not text_parts:
                return None
            try:
                parsed = json.loads("".join(text_parts))
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _parse_research_output(self, content: Any) -> Optional[StockResearchOutput]:
        payload = self._parse_json_response_content(content)
        if not isinstance(payload, dict):
            return None
        try:
            return StockResearchOutput.model_validate(payload)
        except Exception as exc:
            logger.warning("Invalid research output payload: %s", exc)
            return None

    def _research_time_window(self) -> tuple[str, str]:
        end_dt = self._now()
        start_dt = end_dt - timedelta(days=365)
        return (
            start_dt.strftime("%Y-%m-%d 00:00:00"),
            end_dt.strftime("%Y-%m-%d 23:59:59"),
        )

    def _normalize_llm_research(
        self,
        payload: Dict[str, Any],
        ranked: List[RankedCandidate],
        style: str,
        recommendation_count: int,
    ) -> tuple[List[RankedCandidate], Optional[str]]:
        research_items = payload.get("research")
        if not isinstance(research_items, list):
            return [], self._t("errors.research_payload_missing_list")

        ranked_map = {item.stock_code: item for item in ranked}
        normalized: List[RankedCandidate] = []
        seen_codes = set()
        skipped_non_dict = 0
        skipped_outside_universe = []
        skipped_duplicates = []

        for raw_item in research_items:
            if not isinstance(raw_item, dict):
                skipped_non_dict += 1
                continue
            stock_code = str(raw_item.get("stock_code") or "").strip()
            if stock_code not in ranked_map:
                if stock_code:
                    skipped_outside_universe.append(stock_code)
                continue
            if stock_code in seen_codes:
                skipped_duplicates.append(stock_code)
                continue

            base = ranked_map[stock_code]
            decision = str(raw_item.get("decision") or "watch").strip().lower()
            if decision not in {"keep", "watch", "drop"}:
                decision = "watch"

            ai_score = max(0.0, min(100.0, safe_float(raw_item.get("ai_score"), base.factor_score)))
            risks = raw_item.get("risks")
            catalysts = raw_item.get("catalysts")
            research_payload = dict(base.research_payload)
            research_payload.update(
                {
                    "thesis": str(raw_item.get("thesis") or research_payload["quant_summary"]["thesis"]),
                    "catalysts": catalysts if isinstance(catalysts, list) else research_payload["quant_summary"]["catalysts"],
                    "risks": risks if isinstance(risks, list) and risks else research_payload["quant_summary"]["risks"],
                    "style_fit_explanation": str(
                        raw_item.get("style_fit_explanation")
                        or f"{base.stock_name} 与 {STYLE_LABELS.get(style, style)} 风格匹配。"
                    ),
                    "holding_horizon": str(raw_item.get("holding_horizon") or self._holding_horizon(style)),
                    "decision": decision,
                }
            )

            normalized.append(
                RankedCandidate(
                    stock_code=base.stock_code,
                    stock_name=base.stock_name,
                    industry=base.industry,
                    market=base.market,
                    factor_score=base.factor_score,
                    ai_score=ai_score,
                    final_score=self._combine_priority_score(ai_score, base.factor_score),
                    decision=decision,
                    research_payload=research_payload,
                )
            )
            seen_codes.add(stock_code)

        if not normalized:
            reasons = []
            if skipped_non_dict:
                reasons.append(self._t("errors.fragments.ignored_non_object_items", count=skipped_non_dict))
            if skipped_outside_universe:
                reasons.append(self._t("errors.fragments.ignored_outside_stocks", stocks=", ".join(skipped_outside_universe[:5])))
            if skipped_duplicates:
                reasons.append(self._t("errors.fragments.ignored_duplicate_stocks", stocks=", ".join(skipped_duplicates[:5])))
            reason_text = "; ".join(reasons) if reasons else self._t("errors.fragments.no_valid_research_remaining")
            logger.warning(
                "stock picker normalize_llm_research empty: input_count=%s skipped_non_dict=%s skipped_outside=%s skipped_duplicates=%s reason=%s",
                len(research_items),
                skipped_non_dict,
                len(skipped_outside_universe),
                len(skipped_duplicates),
                reason_text,
            )
            return [], self._t("errors.research_payload_invalid_with_reason", reason=reason_text)

        normalized.sort(key=lambda candidate: candidate.final_score, reverse=True)
        logger.info(
            "stock picker normalize_llm_research done: input_count=%s normalized_count=%s skipped_non_dict=%s skipped_outside=%s skipped_duplicates=%s",
            len(research_items),
            len(normalized),
            skipped_non_dict,
            len(skipped_outside_universe),
            len(skipped_duplicates),
        )
        return normalized, None

    def _build_recommendations(
        self,
        researched: List[RankedCandidate],
        run: StockSelectionRun,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        run_config = self._get_run_config(run)
        logger.info(
            "stock picker build_recommendations: researched_count=%s recommendation_count=%s style=%s same_industry_limit=%s",
            len(researched),
            run.recommendation_count,
            run.style,
            run_config["same_industry_limit"],
        )
        decision_priority = {"keep": 0, "watch": 1, "drop": 2}
        sorted_candidates = sorted(
            researched,
            key=lambda item: (decision_priority.get(item.decision, 3), -item.final_score),
        )
        selected_candidates: List[RankedCandidate] = []
        industry_counts: Dict[str, int] = {}
        for candidate in sorted_candidates:
            if candidate.decision == "drop":
                continue
            industry = candidate.industry or "未分类"
            if industry_counts.get(industry, 0) >= run_config["same_industry_limit"]:
                continue
            selected_candidates.append(candidate)
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
            if len(selected_candidates) >= run.recommendation_count:
                break

        if len(selected_candidates) < run.recommendation_count:
            raise ValueError(
                self._t(
                    "errors.recommendation_count_mismatch",
                    expected=run.recommendation_count,
                    actual=len(selected_candidates),
                )
            )

        recommendations = []
        for rank, candidate in enumerate(selected_candidates, start=1):
            recommendations.append(
                {
                    "stock_code": candidate.stock_code,
                    "rank": rank,
                    "conviction_score": round(candidate.final_score, 2),
                    "recommendation_reason": candidate.research_payload.get("thesis", ""),
                    "risk_flags": candidate.research_payload.get("risks", []),
                    "holding_horizon": candidate.research_payload.get(
                        "holding_horizon",
                        self._holding_horizon(run.style),
                    ),
                    "decision": candidate.decision,
                }
            )

        summary = {
            "source_scope": run.scope,
            "style_label": STYLE_LABELS.get(run.style, run.style),
            "candidate_count": len(researched),
            "selected_count": len(recommendations),
            "selection_logic": self._t("defaults.selection_logic"),
            "recommended_stock_codes": [item["stock_code"] for item in recommendations],
            "same_industry_limit": run_config["same_industry_limit"],
        }
        logger.info(
            "stock picker build_recommendations done: selected_count=%s codes=%s",
            len(recommendations),
            summary["recommended_stock_codes"],
        )
        return recommendations, summary

    def _build_summary_metrics(
        self,
        researched: List[RankedCandidate],
        recommendations: List[Dict[str, Any]],
        research_mode: str,
        run_config: Dict[str, Any],
        universe_count: int,
        factor_candidate_count: int,
    ) -> Dict[str, Any]:
        decision_breakdown = {"keep": 0, "watch": 0, "drop": 0}
        for candidate in researched:
            if candidate.decision in decision_breakdown:
                decision_breakdown[candidate.decision] += 1

        sorted_researched = sorted(researched, key=lambda candidate: candidate.final_score, reverse=True)
        top_candidates = [
            {
                "stock_code": candidate.stock_code,
                "stock_name": candidate.stock_name,
                "factor_score": round(candidate.factor_score, 2),
                "ai_score": round(candidate.ai_score, 2),
                "final_score": round(candidate.final_score, 2),
                "quant_support": candidate.research_payload.get("quant_support"),
                "decision": candidate.decision,
            }
            for candidate in sorted_researched[:5]
        ]

        return {
            "research_mode": research_mode,
            "decision_breakdown": decision_breakdown,
            "top_candidates": top_candidates,
            "universe_count": universe_count,
            "candidate_count": factor_candidate_count,
            "factor_candidate_count": factor_candidate_count,
            "research_candidate_count": min(run_config["research_candidate_limit"], factor_candidate_count),
            "factor_candidate_limit": run_config["factor_candidate_limit"],
            "research_candidate_limit": run_config["research_candidate_limit"],
            "same_industry_limit": run_config["same_industry_limit"],
            "allowed_industries": run_config["allowed_industries"],
            "recommended_stock_codes": [item["stock_code"] for item in recommendations],
        }

    def _combine_priority_score(self, ai_score: float, factor_score: float) -> float:
        normalized_ai = max(0.0, min(100.0, safe_float(ai_score, 0.0)))
        normalized_factor = max(0.0, min(100.0, safe_float(factor_score, 0.0)))
        return round(normalized_ai * AI_PRIMARY_WEIGHT + normalized_factor * FACTOR_AUX_WEIGHT, 2)

    def _replace_candidates(self, db: Session, run_id: UUID, ranked: Iterable[RankedCandidate], scope: str, style: str) -> None:
        db.query(StockSelectionCandidate).filter(StockSelectionCandidate.run_id == run_id).delete()
        for candidate in ranked:
            db.add(
                StockSelectionCandidate(
                    run_id=run_id,
                    stock_code=candidate.stock_code,
                    source_scope=scope,
                    style=style,
                    factor_score=candidate.factor_score,
                    ai_score=candidate.ai_score,
                    final_score=candidate.final_score,
                    decision=candidate.decision,
                    eliminated_stage="ai_researched" if candidate.decision != "keep" else None,
                    eliminated_reason=candidate.research_payload.get("eliminated_reason"),
                    research_payload=candidate.research_payload,
                )
            )
        db.commit()

    def _record_event(
        self,
        db: Session,
        run_id: UUID,
        stage: str,
        event_type: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        push: bool = True,
    ) -> None:
        event = StockSelectionEvent(
            run_id=run_id,
            stage=stage,
            event_type=event_type,
            message=message,
            payload=payload,
        )
        db.add(event)
        db.commit()
        logger.info(
            "stock picker event recorded: run_id=%s stage=%s event_type=%s push=%s payload_keys=%s",
            run_id,
            stage,
            event_type,
            push,
            sorted(list((payload or {}).keys())),
        )
        if push:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    ws_manager.send_stock_picker_update(
                        run_id=str(run_id),
                        stage=stage,
                        status=event_type,
                        message=message,
                        payload=payload or {},
                    )
                )
            except RuntimeError:
                logger.warning("No event loop available while pushing stock picker update for %s", run_id)

    @staticmethod
    def _holding_horizon(style: str) -> str:
        if style == "momentum":
            return "short_term"
        if style in {"value", "defensive"}:
            return "mid_long_term"
        return "mid_term"

    @staticmethod
    def _now() -> datetime:
        return datetime.now()


stock_picker_service = StockPickerService()
