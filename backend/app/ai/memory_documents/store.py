"""单文档记忆存储层。

每个 ``(user_id, stock_code)`` 锚定一份 Markdown 记忆文档；
读取返回整份文档，写入是整文档替换，配合乐观版本号与容量上限两道安全栏。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core import database as database_module
from app.core.config import settings
from app.core.logger import get_logger
from app.models.memory_document import MemoryDocument

logger = get_logger(__name__)


async def read_document(*, user_id: int, stock_code: str) -> dict[str, Any]:
    """读取整份记忆文档。

    Args:
        user_id: 用户 ID。
        stock_code: 股票代码。

    Returns:
        包含 ``exists``、``content``、``size_chars``、``version`` 的字典；
        文档不存在时返回空文档标记，首次写入会自动建行。
    """
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(MemoryDocument).where(
                MemoryDocument.user_id == user_id,
                MemoryDocument.stock_code == stock_code,
            )
        )
        row = result.scalars().first()
    if row is None:
        return {"exists": False, "content": "", "size_chars": 0, "version": 0}
    return {
        "exists": True,
        "content": row.content or "",
        "size_chars": int(row.size_chars or 0),
        "version": int(row.version or 0),
    }


async def update_document(
    *,
    user_id: int,
    stock_code: str,
    content: str,
    base_version: int,
) -> dict[str, Any]:
    """整文档替换写入，带乐观版本校验与容量上限。

    Args:
        user_id: 用户 ID。
        stock_code: 股票代码。
        content: 替换后的整份 Markdown 文档内容。
        base_version: 调用方读取文档时的版本号；文档尚不存在时必须为 0。

    Returns:
        成功时返回 ``success``、``status``、``memory_id``、``stock_code``、
        ``version``、``size_chars``；失败时返回 ``success=False`` 与 ``error``，
        版本冲突时附带最新 ``content``、``version`` 供调用方合并重试。
    """
    normalized_content = (content or "").strip()
    size_chars = len(normalized_content)
    if size_chars == 0:
        return {"success": False, "error": "memory document content is empty"}
    max_chars = settings.MEMORY_DOC_MAX_CHARS
    if size_chars > max_chars:
        return {
            "success": False,
            "error": (
                f"memory document exceeds limit: {size_chars} chars > {max_chars}. "
                "Consolidate or remove outdated content before retrying."
            ),
            "size_chars": size_chars,
            "max_chars": max_chars,
        }

    try:
        async with database_module.AsyncSessionLocal() as db:
            existing = await _load_document(db, user_id=user_id, stock_code=stock_code)
            if existing is None:
                if base_version != 0:
                    return {
                        "success": False,
                        "error": "memory document not found; call read_memory first",
                        "version": 0,
                    }
                db.add(
                    MemoryDocument(
                        user_id=user_id,
                        stock_code=stock_code,
                        content=normalized_content,
                        size_chars=size_chars,
                        version=1,
                    )
                )
                new_version = 1
            else:
                new_version = int(existing.version or 0) + 1
                updated = await db.execute(
                    update(MemoryDocument)
                    .where(
                        MemoryDocument.id == existing.id,
                        MemoryDocument.version == base_version,
                    )
                    .values(
                        content=normalized_content,
                        size_chars=size_chars,
                        version=new_version,
                        updated_at=datetime.now(),
                    )
                )
                if updated.rowcount == 0:
                    latest = await _load_document(db, user_id=user_id, stock_code=stock_code)
                    return {
                        "success": False,
                        "error": (
                            f"memory document version conflict: expected {base_version}, "
                            f"current {int(latest.version or 0)}. "
                            "Merge your changes into the latest content returned here and retry."
                        ),
                        "content": latest.content or "",
                        "version": int(latest.version or 0),
                        "size_chars": int(latest.size_chars or 0),
                    }
            await db.commit()
    except IntegrityError:
        # 并发首写撞 (user_id, stock_code) 唯一约束：按版本冲突处理，
        # 让调用方基于最新文档合并内容后用正确版本号重试。
        logger.info(
            "memory document concurrent first write user_id=%s stock_code=%s",
            user_id,
            stock_code,
        )
        latest = await read_document(user_id=user_id, stock_code=stock_code)
        return {
            "success": False,
            "error": (
                "memory document was created concurrently; "
                "merge your changes into the latest content returned here and retry"
            ),
            "content": latest["content"],
            "version": latest["version"],
            "size_chars": latest["size_chars"],
        }
    except Exception:
        logger.exception(
            "memory document update failed user_id=%s stock_code=%s", user_id, stock_code
        )
        raise
    return {
        "success": True,
        "status": "success",
        "memory_id": f"md_{uuid4().hex}",
        "stock_code": stock_code,
        "version": new_version,
        "size_chars": size_chars,
    }


async def _load_document(db, *, user_id: int, stock_code: str) -> MemoryDocument | None:
    """按用户与股票加载记忆文档行。

    Args:
        db: 数据库会话。
        user_id: 用户 ID。
        stock_code: 股票代码。

    Returns:
        已存在的记忆文档行；不存在时返回 ``None``。
    """
    result = await db.execute(
        select(MemoryDocument).where(
            MemoryDocument.user_id == user_id,
            MemoryDocument.stock_code == stock_code,
        )
    )
    return result.scalars().first()
