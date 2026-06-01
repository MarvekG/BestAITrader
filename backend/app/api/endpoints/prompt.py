from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.prompt import PromptTemplate, PromptStats
from app.ai.llm_engine.prompts.templates import PROMPT_MAP
from app.core.config import settings

router = APIRouter()


@router.get("/", response_model=Dict[str, str])
def get_all_prompts():
    """获取所有当前语言的静态提示词模板。"""
    lang = settings.SYSTEM_LANGUAGE
    results = {}

    for role, content_map in PROMPT_MAP.items():
        results[role] = content_map.get(lang, content_map.get("zh", ""))

    return results


@router.get("/{role}", response_model=PromptTemplate)
def get_prompt_by_role(role: str):
    """获取特定角色的静态提示词模板。"""
    if role not in PROMPT_MAP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt role '{role}' not found"
        )

    lang = settings.SYSTEM_LANGUAGE
    content = PROMPT_MAP[role].get(lang, PROMPT_MAP[role].get("zh", ""))

    return PromptTemplate(role=role, content=content)


@router.get("/stats/usage", response_model=PromptStats)
def get_prompt_usage_stats(
    db: Session = Depends(get_db)
):
    """获取提示词使用统计信息"""
    from app.crud.llm_usage_log import llm_usage_log
    stats = llm_usage_log.get_stats(db)
    return PromptStats(**stats)
