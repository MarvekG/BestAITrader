import uuid
from collections.abc import Mapping
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.models.llm_usage_log import LLMUsageLog

logger = get_logger(__name__)


def _cache_hit_rate(cached_tokens: int, input_tokens: int) -> float:
    """计算 prompt cache 命中率。"""

    if input_tokens <= 0:
        return 0.0
    return cached_tokens / input_tokens


def _cache_miss_tokens(input_tokens: int, cached_tokens: int) -> int:
    """派生 prompt cache 未命中 token 数。"""

    return max(input_tokens - cached_tokens, 0)


def _usage_value(value: Any, key: str, default: int = 0) -> int:
    """从 mapping 或 object 风格 usage 对象中读取整数值。"""

    if value is None:
        return default
    if isinstance(value, Mapping):
        raw_value = value.get(key, default)
    else:
        raw_value = getattr(value, key, default)
    return int(raw_value or default)


def _cached_tokens_from_usage(usage: Any) -> int:
    """读取 provider 或 OpenAI-compatible usage 中的缓存命中 token 数。"""

    provider_cached_tokens = _usage_value(usage, "prompt_cache_hit_tokens", default=0)
    if provider_cached_tokens:
        return provider_cached_tokens
    if isinstance(usage, Mapping):
        details = usage.get("prompt_tokens_details") or usage.get("input_token_details")
    else:
        details = getattr(usage, "prompt_tokens_details", None) or getattr(usage, "input_token_details", None)
    return _first_usage_value(details, ("cached_tokens", "cache_read"))


def _cache_miss_tokens_from_usage(usage: Any) -> int:
    """读取 provider 或 OpenAI-compatible usage 中的缓存未命中 token 数。"""

    provider_miss_tokens = _first_usage_value(
        usage,
        ("prompt_cache_miss_tokens", "cache_miss_tokens"),
    )
    if provider_miss_tokens:
        return provider_miss_tokens
    if isinstance(usage, Mapping):
        details = usage.get("prompt_tokens_details") or usage.get("input_token_details")
    else:
        details = getattr(usage, "prompt_tokens_details", None) or getattr(usage, "input_token_details", None)
    return _first_usage_value(details, ("cache_miss", "cache_write"))


def _reasoning_tokens_from_usage(usage: Any) -> int:
    """读取 OpenAI-compatible usage 中的推理 token 数。"""

    if isinstance(usage, Mapping):
        details = usage.get("completion_tokens_details") or usage.get("output_token_details")
    else:
        details = getattr(usage, "completion_tokens_details", None) or getattr(usage, "output_token_details", None)
    return _usage_value(details, "reasoning_tokens")


def _first_usage_value(value: Any, keys: tuple[str, ...]) -> int:
    """按优先级读取第一个非零 usage 值。"""

    for key in keys:
        usage_value = _usage_value(value, key, default=0)
        if usage_value:
            return usage_value
    return 0


def _response_metadata_usage(response: Any) -> Any:
    """读取 LangChain response_metadata 中保留的原始 provider usage。"""

    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    return metadata.get("token_usage") or metadata.get("usage")


def _normalize_observability_label(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _group_key(value: Any) -> str:
    return str(value or "unknown")


def _usage_summary_from_row(row: Any) -> dict[str, Any]:
    input_tokens = int(row.input_tokens or 0)
    cached_tokens = int(row.cached_tokens or 0)
    return {
        "calls": int(row.calls or 0),
        "input_tokens": input_tokens,
        "output_tokens": int(row.output_tokens or 0),
        "total_tokens": int(row.total_tokens or 0),
        "cached_tokens": cached_tokens,
        "cache_miss_tokens": int(row.cache_miss_tokens or 0),
        "reasoning_tokens": int(row.reasoning_tokens or 0),
        "cache_hit_rate": _cache_hit_rate(cached_tokens, input_tokens),
    }


def _usage_breakdown(db: Session, field_name: str) -> dict[str, dict[str, Any]]:
    column = getattr(LLMUsageLog, field_name)
    rows = db.query(
        column.label("group_value"),
        func.count(LLMUsageLog.id).label("calls"),
        func.sum(LLMUsageLog.input_tokens).label("input_tokens"),
        func.sum(LLMUsageLog.output_tokens).label("output_tokens"),
        func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
        func.sum(LLMUsageLog.cached_tokens).label("cached_tokens"),
        func.sum(LLMUsageLog.cache_miss_tokens).label("cache_miss_tokens"),
        func.sum(LLMUsageLog.reasoning_tokens).label("reasoning_tokens"),
    ).group_by(column).all()
    return {_group_key(row.group_value): _usage_summary_from_row(row) for row in rows}


def _usage_breakdown_by_fields(db: Session, field_names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    columns = [getattr(LLMUsageLog, field_name) for field_name in field_names]
    rows = db.query(
        *columns,
        func.count(LLMUsageLog.id).label("calls"),
        func.sum(LLMUsageLog.input_tokens).label("input_tokens"),
        func.sum(LLMUsageLog.output_tokens).label("output_tokens"),
        func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
        func.sum(LLMUsageLog.cached_tokens).label("cached_tokens"),
        func.sum(LLMUsageLog.cache_miss_tokens).label("cache_miss_tokens"),
        func.sum(LLMUsageLog.reasoning_tokens).label("reasoning_tokens"),
    ).group_by(*columns).all()
    breakdown: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_value = "/".join(_group_key(getattr(row, field_name)) for field_name in field_names)
        breakdown[group_value] = _usage_summary_from_row(row)
    return breakdown


def _add_iteration_indexes(
    breakdown: dict[str, dict[str, Any]],
    db: Session,
    field_name: str,
) -> dict[str, dict[str, Any]]:
    column = getattr(LLMUsageLog, field_name)
    rows = db.query(column, LLMUsageLog.iteration_index).filter(
        LLMUsageLog.iteration_index.isnot(None)
    ).distinct().all()
    indexes_by_key: dict[str, list[int]] = {}
    for group_value, iteration_index in rows:
        indexes_by_key.setdefault(_group_key(group_value), []).append(int(iteration_index))
    for group_value, indexes in indexes_by_key.items():
        if group_value in breakdown:
            breakdown[group_value]["iteration_indexes"] = sorted(indexes)
    for summary in breakdown.values():
        summary.setdefault("iteration_indexes", [])
    return breakdown


class CRUDLLMUsageLog:
    """LLM 使用日志 CRUD"""

    def create(
        self,
        db: Session,
        *,
        model: str,
        role: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cached_tokens: int = 0,
        cache_miss_tokens: int = 0,
        reasoning_tokens: int = 0,
        session_id: Optional[uuid.UUID] = None,
        workflow: str | None = None,
        stage: str | None = None,
        call_kind: str | None = None,
        iteration_index: int | None = None,
        cache_lane: str | None = None,
        api_key_alias: str | None = None,
    ) -> LLMUsageLog:
        """创建使用记录"""
        db_obj = LLMUsageLog(
            model=model,
            role=role,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            cache_miss_tokens=cache_miss_tokens,
            reasoning_tokens=reasoning_tokens,
            session_id=session_id,
            workflow=_normalize_observability_label(workflow),
            stage=_normalize_observability_label(stage),
            call_kind=_normalize_observability_label(call_kind),
            iteration_index=iteration_index,
            cache_lane=_normalize_observability_label(cache_lane),
            api_key_alias=_normalize_observability_label(api_key_alias),
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def get_stats(self, db: Session) -> Dict[str, Any]:
        """获取汇总统计数据"""
        # 总计
        total_stats = db.query(
            func.count(LLMUsageLog.id).label("total_calls"),
            func.sum(LLMUsageLog.input_tokens).label("input_tokens"),
            func.sum(LLMUsageLog.output_tokens).label("output_tokens"),
            func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
            func.sum(LLMUsageLog.cached_tokens).label("cached_tokens"),
            func.sum(LLMUsageLog.cache_miss_tokens).label("cache_miss_tokens"),
            func.sum(LLMUsageLog.reasoning_tokens).label("reasoning_tokens"),
        ).first()

        # 按角色统计
        role_stats = db.query(
            LLMUsageLog.role,
            func.count(LLMUsageLog.id).label("calls")
        ).group_by(LLMUsageLog.role).all()

        input_tokens = int(total_stats.input_tokens or 0)
        cached_tokens = int(total_stats.cached_tokens or 0)
        by_role_detail = _add_iteration_indexes(_usage_breakdown(db, "role"), db, "role")
        return {
            "total_calls": total_stats.total_calls or 0,
            "input_tokens": input_tokens,
            "output_tokens": int(total_stats.output_tokens or 0),
            "total_tokens": int(total_stats.total_tokens or 0),
            "cached_tokens": cached_tokens,
            "cache_miss_tokens": int(total_stats.cache_miss_tokens or 0),
            "reasoning_tokens": int(total_stats.reasoning_tokens or 0),
            "cache_hit_rate": _cache_hit_rate(cached_tokens, input_tokens),
            "by_role": {stat.role: stat.calls for stat in role_stats},
            "by_role_detail": by_role_detail,
            "by_workflow": _usage_breakdown(db, "workflow"),
            "by_stage": _add_iteration_indexes(_usage_breakdown(db, "stage"), db, "stage"),
            "by_workflow_stage": _usage_breakdown_by_fields(db, ("workflow", "stage")),
            "by_workflow_call_kind": _usage_breakdown_by_fields(db, ("workflow", "call_kind")),
            "by_call_kind": _usage_breakdown(db, "call_kind"),
            "by_cache_lane": _usage_breakdown(db, "cache_lane"),
            "by_api_key_alias": _usage_breakdown(db, "api_key_alias"),
        }

    def clear(self, db: Session) -> int:
        """删除所有 LLM 使用记录。"""

        deleted = db.query(LLMUsageLog).delete(synchronize_session=False)
        db.commit()
        return int(deleted or 0)


llm_usage_log = CRUDLLMUsageLog()


def record_llm_usage(
    response,
    model: str,
    role: str,
    session_id: Optional[uuid.UUID] = None,
    *,
    workflow: str | None = None,
    stage: str | None = None,
    call_kind: str | None = None,
    iteration_index: int | None = None,
    cache_lane: str | None = None,
    api_key_alias: str | None = None,
):
    """
    统一记录 LLM 调用的 token 使用量，支持 LangChain 和 OpenAI 响应格式
    (Unified LLM usage recording, supporting both LangChain and OpenAI response formats)
    """
    try:
        # LangChain 格式: response.usage_metadata (dict)
        usage = getattr(response, "usage_metadata", None)
        if usage and isinstance(usage, dict):
            raw_usage = _response_metadata_usage(response)
            input_tokens = _usage_value(usage, "input_tokens")
            cached_tokens = _cached_tokens_from_usage(raw_usage) or _cached_tokens_from_usage(usage)
            explicit_miss_tokens = (
                _cache_miss_tokens_from_usage(raw_usage)
                or _cache_miss_tokens_from_usage(usage)
            )
            from app.core.database import SessionLocal
            with SessionLocal() as db:
                llm_usage_log.create(
                    db=db,
                    model=model,
                    role=role,
                    input_tokens=input_tokens,
                    output_tokens=_usage_value(usage, "output_tokens"),
                    total_tokens=_usage_value(usage, "total_tokens"),
                    cached_tokens=cached_tokens,
                    cache_miss_tokens=explicit_miss_tokens or _cache_miss_tokens(input_tokens, cached_tokens),
                    reasoning_tokens=_reasoning_tokens_from_usage(raw_usage) or _reasoning_tokens_from_usage(usage),
                    session_id=session_id,
                    workflow=workflow,
                    stage=stage,
                    call_kind=call_kind,
                    iteration_index=iteration_index,
                    cache_lane=cache_lane,
                    api_key_alias=api_key_alias,
                )
            return

        # OpenAI 格式: response.usage (object with prompt_tokens, etc.)
        usage_obj = getattr(response, "usage", None)
        if usage_obj:
            input_tokens = _usage_value(usage_obj, "prompt_tokens")
            cached_tokens = _cached_tokens_from_usage(usage_obj)
            explicit_miss_tokens = _cache_miss_tokens_from_usage(usage_obj)
            from app.core.database import SessionLocal
            with SessionLocal() as db:
                llm_usage_log.create(
                    db=db,
                    model=model,
                    role=role,
                    input_tokens=input_tokens,
                    output_tokens=_usage_value(usage_obj, "completion_tokens"),
                    total_tokens=_usage_value(usage_obj, "total_tokens"),
                    cached_tokens=cached_tokens,
                    cache_miss_tokens=explicit_miss_tokens or _cache_miss_tokens(input_tokens, cached_tokens),
                    reasoning_tokens=_reasoning_tokens_from_usage(usage_obj),
                    session_id=session_id,
                    workflow=workflow,
                    stage=stage,
                    call_kind=call_kind,
                    iteration_index=iteration_index,
                    cache_lane=cache_lane,
                    api_key_alias=api_key_alias,
                )
    except Exception as e:
        logger.exception(f"Failed to record LLM usage for {role}: {e}")
