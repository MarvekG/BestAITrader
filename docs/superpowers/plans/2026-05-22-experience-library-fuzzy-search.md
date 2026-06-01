# Experience Library Fuzzy Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every experience library search/filter field support fuzzy contains matching.

**Architecture:** Keep the existing API contract and frontend form. Centralize fuzzy matching helpers inside `ExperienceIndexService`, use SQL `ILIKE` for scalar columns, and use Python-side case-insensitive substring checks for JSON tags.

**Tech Stack:** Python 3.11, SQLAlchemy, pytest, React, TypeScript, Ant Design.

---

## File Structure

- Modify: `backend/app/ai/experience/index_service.py`
  - Add small private helpers for normalized fuzzy patterns and text matching.
  - Change scalar field filters from equality to `ILIKE`.
  - Change tag matching from exact list membership to case-insensitive substring matching.
  - Expand global `keyword` matching to scalar columns plus tags.
- Modify: `backend/tests/test_experience_index_service.py`
  - Add regression tests proving every filter accepts partial values.
  - Add regression tests proving global keyword can match non-summary fields and tags.
- Modify: `frontend/src/pages/experience/ExperienceLibraryPanel.tsx`
  - Update keyword placeholder only.
- Modify: `backend/app/locales/zh.json`
  - Change `experience_library.keyword_placeholder` to mention fuzzy search.
- Modify: `backend/app/locales/en.json`
  - Change `experience_library.keyword_placeholder` to mention fuzzy search.

## Task 1: Backend Fuzzy Search Tests

**Files:**
- Modify: `backend/tests/test_experience_index_service.py`

- [ ] **Step 1: Add failing tests for fuzzy field filters**

Insert these tests after `test_list_items_filters_by_horizon_tag_keyword_and_stock`:

```python
def test_list_items_uses_fuzzy_matching_for_each_filter_field(db_session):
    user, session = _create_user_and_session(db_session)
    experience_index_service.sync_from_review_result(db_session, user_id=user.id, result=_review_result(session))

    filter_cases = [
        {"stock_code": "000001"},
        {"industry": "银"},
        {"strategy": "tre"},
        {"review_horizon": "20"},
        {"correctness": "partial"},
        {"importance": "hi"},
        {"tag": "追"},
    ]

    for filters in filter_cases:
        result = experience_index_service.list_items(db_session, user_id=user.id, **filters)
        assert result["total"] == 1, filters
        assert result["items"][0]["memory_observation_id"] == "obs-memory-1"


def test_list_items_keyword_searches_multiple_index_fields_and_tags(db_session):
    user, session = _create_user_and_session(db_session)
    experience_index_service.sync_from_review_result(db_session, user_id=user.id, result=_review_result(session))

    keyword_cases = ["平安", "银行", "trend", "20d", "profit", "partial", "high", "追"]

    for keyword in keyword_cases:
        result = experience_index_service.list_items(db_session, user_id=user.id, keyword=keyword)
        assert result["total"] == 1, keyword
        assert result["items"][0]["memory_observation_id"] == "obs-memory-1"


def test_list_items_fuzzy_search_returns_empty_when_no_field_matches(db_session):
    user, session = _create_user_and_session(db_session)
    experience_index_service.sync_from_review_result(db_session, user_id=user.id, result=_review_result(session))

    result = experience_index_service.list_items(
        db_session,
        user_id=user.id,
        stock_code="999999",
        keyword="不存在的经验",
    )

    assert result["total"] == 0
    assert result["items"] == []
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_experience_index_service.py::test_list_items_uses_fuzzy_matching_for_each_filter_field tests/test_experience_index_service.py::test_list_items_keyword_searches_multiple_index_fields_and_tags tests/test_experience_index_service.py::test_list_items_fuzzy_search_returns_empty_when_no_field_matches -v
```

Expected: first two tests fail because current filters are exact for most fields and keyword only searches `summary`.

- [ ] **Step 3: Commit the failing tests**

Do not commit RED tests separately. Keep them staged only after implementation passes.

## Task 2: Backend Fuzzy Search Implementation

**Files:**
- Modify: `backend/app/ai/experience/index_service.py`
- Test: `backend/tests/test_experience_index_service.py`

- [ ] **Step 1: Add fuzzy helper methods**

Inside `ExperienceIndexService`, replace `_matches_tag()` with these helpers:

```python
    def _fuzzy_pattern(self, value: str | None) -> str | None:
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
        normalized_keyword = _safe_string(keyword).casefold()
        if not normalized_keyword:
            return True
        normalized_value = _safe_string(value).casefold()
        return normalized_keyword in normalized_value

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

    def _matches_keyword_tags(self, row: ExperienceIndex, keyword: str | None) -> bool:
        """判断全局关键词是否命中标签。

        Args:
            row: 经验索引模型。
            keyword: 全局关键词。

        Returns:
            任一标签值包含关键词时返回 ``True``。
        """
        return self._matches_tag(row, keyword)
```

- [ ] **Step 2: Replace exact scalar filters with ILIKE**

In `list_items()`, replace the scalar filter block with:

```python
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
```

- [ ] **Step 3: Expand keyword matching**

In `list_items()`, replace:

```python
        if keyword:
            query = query.filter(ExperienceIndex.summary.ilike(f"%{keyword}%"))
```

with:

```python
        keyword_pattern = self._fuzzy_pattern(keyword)
        if keyword_pattern:
            query = query.filter(
                or_(
                    ExperienceIndex.summary.ilike(keyword_pattern),
                    ExperienceIndex.stock_code.ilike(keyword_pattern),
                    ExperienceIndex.stock_name.ilike(keyword_pattern),
                    ExperienceIndex.industry.ilike(keyword_pattern),
                    ExperienceIndex.strategy.ilike(keyword_pattern),
                    ExperienceIndex.review_horizon.ilike(keyword_pattern),
                    ExperienceIndex.outcome_label.ilike(keyword_pattern),
                    ExperienceIndex.correctness.ilike(keyword_pattern),
                    ExperienceIndex.importance.ilike(keyword_pattern),
                )
            )
```

Then after `rows = query.order_by(...).all()`, apply tag filters with:

```python
        if tag:
            rows = [row for row in rows if self._matches_tag(row, tag)]
        if keyword_pattern:
            keyword_rows = [row for row in rows if self._matches_keyword_tags(row, keyword)]
            if keyword_rows:
                existing_ids = {row.id for row in rows}
                rows.extend(row for row in keyword_rows if row.id not in existing_ids)
```

If this exact post-filter placement cannot make tag-only keyword matches work because SQL already filtered them out, use this complete query flow instead:

```python
        rows = query.order_by(ExperienceIndex.created_at.desc()).all()
        if tag:
            rows = [row for row in rows if self._matches_tag(row, tag)]
        if keyword_pattern:
            rows = [
                row for row in rows
                if self._matches_keyword_scalar(row, keyword) or self._matches_keyword_tags(row, keyword)
            ]
```

Add `_matches_keyword_scalar()` if using the second flow:

```python
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
```

- [ ] **Step 4: Run backend tests to verify GREEN**

Run:

```bash
pytest tests/test_experience_index_service.py -v
```

Expected: all tests in `test_experience_index_service.py` pass.

- [ ] **Step 5: Commit backend implementation and tests**

Run:

```bash
git add backend/app/ai/experience/index_service.py backend/tests/test_experience_index_service.py
git commit -m "feat: support fuzzy experience library filters"
```

## Task 3: Frontend Placeholder Copy

**Files:**
- Modify: `backend/app/locales/zh.json`
- Modify: `backend/app/locales/en.json`
- Modify: `frontend/src/pages/experience/ExperienceLibraryPanel.tsx`

- [ ] **Step 1: Update locale strings**

In `backend/app/locales/zh.json`, change:

```json
"keyword_placeholder": "关键词"
```

to:

```json
"keyword_placeholder": "关键词，支持模糊搜索"
```

In `backend/app/locales/en.json`, change:

```json
"keyword_placeholder": "Keyword"
```

to:

```json
"keyword_placeholder": "Keyword, fuzzy search supported"
```

- [ ] **Step 2: Widen keyword input if needed**

In `frontend/src/pages/experience/ExperienceLibraryPanel.tsx`, change the keyword input width from `180` to `220`:

```tsx
<Input allowClear placeholder={t('experience_library.keyword_placeholder')} style={{ width: 220 }} />
```

- [ ] **Step 3: Run frontend typecheck**

Run:

```bash
npm run typecheck
```

Expected: `tsc --noEmit` exits 0.

- [ ] **Step 4: Commit frontend copy update**

Run:

```bash
git add backend/app/locales/zh.json backend/app/locales/en.json frontend/src/pages/experience/ExperienceLibraryPanel.tsx
git commit -m "chore: clarify fuzzy experience search copy"
```

## Task 4: Final Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run backend regression tests**

Run:

```bash
pytest tests/test_experience_index_service.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend typecheck**

Run:

```bash
npm run typecheck
```

Expected: `tsc --noEmit` exits 0.

- [ ] **Step 3: Check source diff**

Run:

```bash
git status --short
git log --oneline -3
```

Expected: working tree is clean after task commits, and the latest commits are the fuzzy search implementation commits.

## Self-Review

- Spec coverage: backend scalar filters, tag filter, global keyword expansion, frontend copy, tests, and verification are covered by Tasks 1-4.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: all new helpers live on `ExperienceIndexService`; tests call existing `experience_index_service.list_items()` API and use existing `memory_observation_id` response field.
