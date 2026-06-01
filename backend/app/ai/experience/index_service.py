from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.experience_index import ExperienceIndex
from app.models.experience_review_event import ExperienceReviewEvent


TAG_KEYS = (
    "stock_tags",
    "industry_tags",
    "strategy_tags",
    "failure_lesson_tags",
    "position_discipline_tags",
    "signal_tags",
    "market_regime_tags",
)


def _safe_string(value: Any) -> str | None:
    """将输入值转换为去空白字符串。

    Args:
        value: 需要转换的原始值。

    Returns:
        去空白后的字符串；空值返回 ``None``。
    """
    text = str(value or "").strip()
    return text or None


def _safe_uuid(value: Any) -> UUID | None:
    """将输入值转换为 UUID。

    Args:
        value: UUID 对象或可解析为 UUID 的字符串。

    Returns:
        UUID 对象；无法解析时返回 ``None``。
    """
    if isinstance(value, UUID):
        return value
    text = _safe_string(value)
    if not text:
        return None
    try:
        return UUID(text)
    except ValueError:
        return None


class ExperienceIndexService:
    def _normalize_tags(self, value: Any) -> dict[str, list[str]]:
        """规范化经验索引用标签结构。

        Args:
            value: 复盘结果中的原始标签结构。

        Returns:
            包含固定标签键的字典。
        """
        raw = value if isinstance(value, dict) else {}
        normalized: dict[str, list[str]] = {}
        for key in TAG_KEYS:
            items = raw.get(key)
            if isinstance(items, list):
                normalized[key] = [str(item).strip() for item in items if str(item).strip()]
            elif items in (None, ""):
                normalized[key] = []
            else:
                normalized[key] = [str(items).strip()]
        return normalized

    def _memory_write_succeeded(self, memory: dict[str, Any]) -> bool:
        """判断 Memory 写入条目是否成功。

        Args:
            memory: 复盘结果中的 Memory 写入条目。

        Returns:
            成功写入时返回 ``True``。
        """
        if memory.get("error"):
            return False
        status = _safe_string(memory.get("status"))
        return status in (None, "success", "accepted")

    def _build_summary(self, memory: dict[str, Any]) -> str:
        """生成经验索引展示摘要。

        Args:
            memory: 成功写入的 Memory 条目。

        Returns:
            最长 240 字符的展示摘要。
        """
        content = _safe_string(memory.get("content")) or "-"
        return content[:240]

    def _outcome_label(self, result: dict[str, Any], memory: dict[str, Any]) -> str:
        """根据市场结果生成展示用收益标签。

        Args:
            result: 经验复盘结果。
            memory: Memory 写入条目，可携带证据链。

        Returns:
            结果标签。
        """
        payload = result.get("analysis_payload") if isinstance(result.get("analysis_payload"), dict) else {}
        evidence = memory.get("evidence_chain") if isinstance(memory.get("evidence_chain"), dict) else {}
        market = evidence.get("market_outcome_summary") if isinstance(evidence.get("market_outcome_summary"), dict) else {}
        if not market:
            market = payload.get("market_outcome_summary") if isinstance(payload.get("market_outcome_summary"), dict) else {}
        selected = market.get("selected_horizon_outcome") if isinstance(
            market.get("selected_horizon_outcome"),
            dict,
        ) else {}
        absolute_return = selected.get("absolute_return")
        relative_return = selected.get("relative_return_vs_index")
        if isinstance(relative_return, (int, float)):
            return "outperform" if relative_return >= 0 else "underperform"
        if isinstance(absolute_return, (int, float)):
            return "profit" if absolute_return >= 0 else "loss"
        return "inconclusive"

    def _find_existing(self, db: Session, *, user_id: int, memory: dict[str, Any]) -> ExperienceIndex | None:
        """按 Memory 标识查找已有经验索引。

        Args:
            db: 数据库会话。
            user_id: 用户 ID。
            memory: Memory 写入条目。

        Returns:
            已存在的经验索引；没有匹配时返回 ``None``。
        """
        observation_id = _safe_string(memory.get("observation_id"))
        source_id = _safe_string(memory.get("source_id"))
        filters = []
        if observation_id:
            filters.append(ExperienceIndex.memory_observation_id == observation_id)
        if source_id:
            filters.append(ExperienceIndex.memory_source_id == source_id)
        if not filters:
            return None
        return db.query(ExperienceIndex).filter(ExperienceIndex.user_id == user_id, or_(*filters)).first()

    def _row_payload(self, *, user_id: int, result: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
        """构建经验索引行字段。

        Args:
            user_id: 用户 ID。
            result: 经验复盘结果。
            memory: 成功写入的 Memory 条目。

        Returns:
            可用于创建或更新 `ExperienceIndex` 的字段字典。
        """
        payload = result.get("analysis_payload") if isinstance(result.get("analysis_payload"), dict) else {}
        return {
            "user_id": user_id,
            "memory_observation_id": _safe_string(memory.get("observation_id")),
            "memory_source_id": _safe_string(memory.get("source_id")),
            "review_run_id": str(result.get("review_run_id") or ""),
            "session_id": _safe_uuid(result.get("session_id")),
            "stock_code": _safe_string(memory.get("stock_code")) or _safe_string(result.get("stock_code")),
            "stock_name": _safe_string(memory.get("stock_name")) or _safe_string(result.get("stock_name")),
            "industry": _safe_string(result.get("industry")),
            "strategy": _safe_string(result.get("trading_strategy")),
            "review_horizon": _safe_string(result.get("review_horizon")),
            "outcome_label": self._outcome_label(result, memory),
            "correctness": _safe_string(payload.get("debate_correctness")),
            "importance": _safe_string(memory.get("importance")) or "medium",
            "summary": self._build_summary(memory),
            "tags": self._normalize_tags(payload.get("experience_tags")),
        }

    def sync_from_review_result(self, db: Session, *, user_id: int, result: dict[str, Any]) -> dict[str, int]:
        """从复盘结果同步成功写入 Memory 的经验索引。

        Args:
            db: 数据库会话。
            user_id: 用户 ID。
            result: 经验复盘结果。

        Returns:
            包含 created、updated、skipped 数量的同步摘要。
        """
        payload = result.get("analysis_payload") if isinstance(result.get("analysis_payload"), dict) else {}
        memories = payload.get("written_memories") if isinstance(payload.get("written_memories"), list) else []
        stats = {"created": 0, "updated": 0, "skipped": 0}
        for memory in memories:
            if not isinstance(memory, dict) or not self._memory_write_succeeded(memory):
                stats["skipped"] += 1
                continue
            row_payload = self._row_payload(user_id=user_id, result=result, memory=memory)
            if not row_payload["review_run_id"] or not row_payload["session_id"] or not (
                row_payload["memory_observation_id"] or row_payload["memory_source_id"]
            ):
                stats["skipped"] += 1
                continue
            row = self._find_existing(db, user_id=user_id, memory=memory)
            if row:
                for key, value in row_payload.items():
                    setattr(row, key, value)
                row.updated_at = datetime.now()
                stats["updated"] += 1
            else:
                db.add(ExperienceIndex(**row_payload))
                stats["created"] += 1
        db.commit()
        return stats

    def _serialize_item(self, row: ExperienceIndex) -> dict[str, Any]:
        """序列化经验索引列表项。

        Args:
            row: 经验索引模型。

        Returns:
            API 和前端可直接使用的字典。
        """
        return {
            "id": str(row.id),
            "memory_observation_id": row.memory_observation_id,
            "memory_source_id": row.memory_source_id,
            "review_run_id": row.review_run_id,
            "session_id": str(row.session_id),
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "industry": row.industry,
            "strategy": row.strategy,
            "review_horizon": row.review_horizon,
            "outcome_label": row.outcome_label,
            "correctness": row.correctness,
            "importance": row.importance,
            "summary": row.summary,
            "tags": row.tags or {},
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _fuzzy_pattern(self, value: Any) -> str | None:
        """构建 SQL 模糊匹配模式。

        Args:
            value: 用户输入的筛选文本。

        Returns:
            可用于 ILIKE 的模式；空输入返回 ``None``。
        """
        normalized = _safe_string(value)
        return f"%{normalized}%" if normalized else None

    def _text_matches(self, value: Any, keyword: str | None) -> bool:
        """判断文本是否包含用户输入片段。

        Args:
            value: 待匹配的文本值。
            keyword: 用户输入片段。

        Returns:
            文本大小写不敏感包含片段时返回 ``True``。
        """
        normalized_keyword = _safe_string(keyword)
        if not normalized_keyword:
            return True
        normalized_value = _safe_string(value)
        return bool(normalized_value and normalized_keyword.casefold() in normalized_value.casefold())

    def _matches_tag(self, row: ExperienceIndex, tag: str | None) -> bool:
        """判断索引行是否包含匹配的标签片段。

        Args:
            row: 经验索引模型。
            tag: 需要匹配的标签片段。

        Returns:
            任一标签分组包含该片段时返回 ``True``。
        """
        normalized_tag = _safe_string(tag)
        if not normalized_tag:
            return True
        tags = row.tags if isinstance(row.tags, dict) else {}
        return any(
            self._text_matches(value, normalized_tag)
            for values in tags.values()
            if isinstance(values, list)
            for value in values
        )

    def _matches_keyword_scalar(self, row: ExperienceIndex, keyword: str | None) -> bool:
        """判断全局关键词是否命中普通索引字段。

        Args:
            row: 经验索引模型。
            keyword: 全局关键词。

        Returns:
            任一普通文本字段包含关键词时返回 ``True``。
        """
        return any(
            self._text_matches(value, keyword)
            for value in (
                row.summary,
                row.stock_code,
                row.stock_name,
                row.industry,
                row.strategy,
                row.review_horizon,
                row.outcome_label,
                row.correctness,
                row.importance,
            )
        )

    def _matches_keyword_tags(self, row: ExperienceIndex, keyword: str | None) -> bool:
        """判断全局关键词是否命中标签。

        Args:
            row: 经验索引模型。
            keyword: 全局关键词。

        Returns:
            任一标签值包含关键词时返回 ``True``。
        """
        return self._matches_tag(row, keyword)

    def list_items(
        self,
        db: Session,
        *,
        user_id: int,
        stock_code: str | None = None,
        industry: str | None = None,
        strategy: str | None = None,
        review_horizon: str | None = None,
        correctness: str | None = None,
        importance: str | None = None,
        tag: str | None = None,
        keyword: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """查询经验索引列表。

        Args:
            db: 数据库会话。
            user_id: 用户 ID。
            stock_code: 股票代码筛选。
            industry: 行业筛选。
            strategy: 策略筛选。
            review_horizon: 复盘周期筛选。
            correctness: 原始判断正确性筛选。
            importance: 重要性筛选。
            tag: 标签筛选。
            keyword: 全局关键词筛选。
            created_from: 创建时间下界。
            created_to: 创建时间上界。
            page: 页码。
            page_size: 每页数量。

        Returns:
            分页后的经验索引列表和统计信息。
        """
        query = db.query(ExperienceIndex).filter(ExperienceIndex.user_id == user_id)
        fuzzy_filters = (
            (ExperienceIndex.stock_code, stock_code),
            (ExperienceIndex.industry, industry),
            (ExperienceIndex.strategy, strategy),
            (ExperienceIndex.review_horizon, review_horizon),
            (ExperienceIndex.correctness, correctness),
            (ExperienceIndex.importance, importance),
        )
        for column, value in fuzzy_filters:
            pattern = self._fuzzy_pattern(value)
            if pattern:
                query = query.filter(column.ilike(pattern))
        if created_from:
            query = query.filter(ExperienceIndex.created_at >= created_from)
        if created_to:
            query = query.filter(ExperienceIndex.created_at <= created_to)
        rows = query.order_by(ExperienceIndex.created_at.desc()).all()
        if tag:
            rows = [row for row in rows if self._matches_tag(row, tag)]
        keyword_text = _safe_string(keyword)
        if keyword_text:
            rows = [
                row
                for row in rows
                if self._matches_keyword_scalar(row, keyword_text) or self._matches_keyword_tags(row, keyword_text)
            ]
        total = len(rows)
        page = max(1, int(page or 1))
        page_size = max(1, min(100, int(page_size or 20)))
        start = (page - 1) * page_size
        items = rows[start : start + page_size]
        return {
            "items": [self._serialize_item(row) for row in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "summary": {"total": total},
        }

    def get_detail(self, db: Session, *, user_id: int, index_id: UUID) -> dict[str, Any] | None:
        """获取经验索引详情。

        Args:
            db: 数据库会话。
            user_id: 用户 ID。
            index_id: 经验索引 ID。

        Returns:
            经验详情；不存在时返回 ``None``。
        """
        row = db.query(ExperienceIndex).filter(ExperienceIndex.user_id == user_id, ExperienceIndex.id == index_id).first()
        if row is None:
            return None
        event = (
            db.query(ExperienceReviewEvent)
            .filter(
                ExperienceReviewEvent.user_id == user_id,
                ExperienceReviewEvent.review_run_id == row.review_run_id,
                ExperienceReviewEvent.status == "completed",
            )
            .order_by(ExperienceReviewEvent.created_at.desc())
            .first()
        )
        result = (event.payload or {}).get("result") if event and isinstance(event.payload, dict) else {}
        payload = result.get("analysis_payload") if isinstance(result, dict) and isinstance(result.get("analysis_payload"), dict) else {}
        memories = payload.get("written_memories") if isinstance(payload.get("written_memories"), list) else []
        matched_memory = next(
            (
                item
                for item in memories
                if isinstance(item, dict)
                and (
                    item.get("observation_id") == row.memory_observation_id
                    or item.get("source_id") == row.memory_source_id
                )
            ),
            {},
        )
        evidence = matched_memory.get("evidence_chain") if isinstance(matched_memory.get("evidence_chain"), dict) else {}
        market = evidence.get("market_outcome_summary") if isinstance(evidence.get("market_outcome_summary"), dict) else {}
        detail = self._serialize_item(row)
        detail.update(
            {
                "review_triads": payload.get("review_triads") or {},
                "market_outcome_summary": market,
                "memory": matched_memory,
            }
        )
        return detail

    def rebuild_for_user(self, db: Session, *, user_id: int) -> dict[str, int]:
        """从历史已完成复盘事件重建当前用户的经验索引。

        Args:
            db: 数据库会话。
            user_id: 用户 ID。

        Returns:
            包含创建、更新、跳过和失败数量的重建摘要。
        """
        stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        events = (
            db.query(ExperienceReviewEvent)
            .filter(
                ExperienceReviewEvent.user_id == user_id,
                ExperienceReviewEvent.stage == "experience_review",
                ExperienceReviewEvent.status == "completed",
            )
            .order_by(ExperienceReviewEvent.created_at.asc())
            .all()
        )
        for event in events:
            try:
                result = (event.payload or {}).get("result") if isinstance(event.payload, dict) else None
                if not isinstance(result, dict):
                    stats["skipped"] += 1
                    continue
                item_stats = self.sync_from_review_result(db, user_id=user_id, result=result)
                for key in ("created", "updated", "skipped"):
                    stats[key] += item_stats[key]
            except Exception:
                stats["failed"] += 1
        return stats


experience_index_service = ExperienceIndexService()
