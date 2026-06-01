from typing import Dict, Optional
from pydantic import BaseModel

class PromptTemplate(BaseModel):
    """提示词模板响应模型"""
    role: str
    content: str
    version: Optional[str] = "1.0.0"

class PromptStats(BaseModel):
    """提示词统计响应模型"""
    total_calls: int = 0
    total_tokens: int = 0
    by_role: Dict[str, int] = {}
