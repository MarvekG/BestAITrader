from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryDocumentSummaryResponse(BaseModel):
    """记忆文档列表项。"""

    id: UUID
    stock_code: str
    stock_name: Optional[str] = None
    size_chars: int = 0
    version: int = 0
    created_at: datetime
    updated_at: datetime


class MemoryDocumentDetailResponse(MemoryDocumentSummaryResponse):
    """记忆文档详情。"""

    content: str
    max_chars: int


class MemoryDocumentListResponse(BaseModel):
    """记忆文档分页列表。"""

    items: List[MemoryDocumentSummaryResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    max_chars: int
