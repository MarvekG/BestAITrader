from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select

from app.core import database as database_module
from app.core.config import settings
from app.models.data_storage import StockBasic
from app.models.memory_document import MemoryDocument


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _normalize_text(value: str | None) -> str | None:
    """清理可选文本查询参数。

    Args:
        value: 原始查询文本。

    Returns:
        去除首尾空白后的文本；空文本返回 ``None``。
    """
    normalized = (value or "").strip()
    return normalized or None


def _like_pattern(value: str | None) -> str | None:
    """构造安全的大小写不敏感模糊查询模式。

    Args:
        value: 原始查询文本。

    Returns:
        已转义的 SQL LIKE 模式；空文本返回 ``None``。
    """
    normalized = _normalize_text(value)
    if not normalized:
        return None
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _serialize_document(
    document: MemoryDocument,
    *,
    stock_name: str | None,
    include_content: bool,
) -> dict[str, Any]:
    """序列化记忆文档响应。

    Args:
        document: 记忆文档模型。
        stock_name: 股票基本信息中的名称。
        include_content: 是否包含完整 Markdown 正文。

    Returns:
        可供 API schema 校验的字典。
    """
    payload: dict[str, Any] = {
        "id": document.id,
        "stock_code": document.stock_code,
        "stock_name": stock_name or document.stock_code,
        "size_chars": int(document.size_chars or 0),
        "version": int(document.version or 0),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if include_content:
        payload["content"] = document.content or ""
        payload["max_chars"] = settings.MEMORY_DOC_MAX_CHARS
    return payload


class MemoryDocumentService:
    """提供当前用户记忆文档的只读查询。"""

    async def list_documents(
        self,
        *,
        user_id: int,
        stock_code: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """分页查询当前用户的记忆文档摘要。

        Args:
            user_id: 用户 ID。
            stock_code: 股票代码模糊筛选条件。
            keyword: 股票代码或股票名称模糊筛选条件。
            page: 页码，从 1 开始。
            page_size: 每页数量，最大 100。

        Returns:
            不含正文的记忆文档分页结果。
        """
        normalized_stock_code = _normalize_text(stock_code)
        stock_code_pattern = _like_pattern(normalized_stock_code)
        keyword_pattern = _like_pattern(keyword)
        conditions = [MemoryDocument.user_id == user_id]
        if stock_code_pattern:
            conditions.append(MemoryDocument.stock_code.ilike(stock_code_pattern, escape="\\"))
        if keyword_pattern:
            conditions.append(
                or_(
                    MemoryDocument.stock_code.ilike(keyword_pattern, escape="\\"),
                    StockBasic.name.ilike(keyword_pattern, escape="\\"),
                )
            )

        normalized_page = max(1, int(page or 1))
        normalized_page_size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
        offset = (normalized_page - 1) * normalized_page_size

        async with database_module.AsyncSessionLocal() as db:
            base_query = (
                select(MemoryDocument, StockBasic.name)
                .outerjoin(StockBasic, StockBasic.stock_code == MemoryDocument.stock_code)
                .where(*conditions)
            )
            total = int((await db.execute(
                select(func.count(MemoryDocument.id))
                .select_from(MemoryDocument)
                .outerjoin(StockBasic, StockBasic.stock_code == MemoryDocument.stock_code)
                .where(*conditions)
            )).scalar_one())
            rows = (
                await db.execute(
                    base_query
                    .order_by(MemoryDocument.updated_at.desc(), MemoryDocument.stock_code.asc())
                    .offset(offset)
                    .limit(normalized_page_size)
                )
            ).all()

        return {
            "items": [
                _serialize_document(document, stock_name=stock_name, include_content=False)
                for document, stock_name in rows
            ],
            "total": total,
            "page": normalized_page,
            "page_size": normalized_page_size,
            "max_chars": settings.MEMORY_DOC_MAX_CHARS,
        }

    async def get_document(self, *, user_id: int, stock_code: str) -> dict[str, Any] | None:
        """读取当前用户指定股票的完整记忆文档。

        Args:
            user_id: 用户 ID。
            stock_code: 股票代码。

        Returns:
            含完整 Markdown 正文的文档详情；不存在或不属于当前用户时返回 ``None``。
        """
        normalized_stock_code = _normalize_text(stock_code)
        if not normalized_stock_code:
            return None

        async with database_module.AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(MemoryDocument, StockBasic.name)
                    .outerjoin(StockBasic, StockBasic.stock_code == MemoryDocument.stock_code)
                    .where(
                        MemoryDocument.user_id == user_id,
                        MemoryDocument.stock_code == normalized_stock_code,
                    )
                )
            ).first()

        if row is None:
            return None
        document, stock_name = row
        return _serialize_document(document, stock_name=stock_name, include_content=True)


memory_document_service = MemoryDocumentService()
