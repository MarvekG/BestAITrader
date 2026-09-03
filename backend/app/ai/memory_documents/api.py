from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.ai.memory_documents.schemas import (
    MemoryDocumentDetailResponse,
    MemoryDocumentListResponse,
)
from app.ai.memory_documents.service import memory_document_service
from app.core.i18n import i18n_service
from app.core.security import get_current_user
from app.models.user import User


router = APIRouter()


@router.get("", response_model=MemoryDocumentListResponse)
async def list_memory_documents(
    stock_code: str | None = Query(None, description="Stock code filter"),
    keyword: str | None = Query(None, description="Stock code or stock name filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """分页列出当前用户的记忆文档摘要。

    Args:
        stock_code: 股票代码筛选条件。
        keyword: 股票代码或股票名称筛选条件。
        page: 页码。
        page_size: 每页数量。
        current_user: 已认证用户依赖。

    Returns:
        当前用户可见的记忆文档摘要列表。
    """
    return await memory_document_service.list_documents(
        user_id=current_user.id,
        stock_code=stock_code,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/{stock_code}", response_model=MemoryDocumentDetailResponse)
async def get_memory_document(
    stock_code: str,
    current_user: User = Depends(get_current_user),
):
    """读取当前用户指定股票的完整记忆文档。

    Args:
        stock_code: 股票代码。
        current_user: 已认证用户依赖。

    Returns:
        记忆文档详情和完整 Markdown 正文。

    Raises:
        HTTPException: 文档不存在或不属于当前用户时抛出 404。
    """
    document = await memory_document_service.get_document(
        user_id=current_user.id,
        stock_code=stock_code,
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=i18n_service.t("memory_documents.not_found"),
        )
    return document
