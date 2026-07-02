from fastapi import APIRouter, Depends, status

from app.ai.stock_analysis.schemas import StockAnalysisRequest, StockAnalysisTaskResponse
from app.ai.stock_analysis.service import submit_stock_analysis_task
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter()


@router.post("/run", response_model=StockAnalysisTaskResponse, status_code=status.HTTP_201_CREATED)
async def run_stock_analysis(
    request: StockAnalysisRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    提交单 LLM 股票自主分析任务。

    Args:
        request: 股票分析请求。
        current_user: 当前登录用户。

    Returns:
        异步任务提交结果。
    """
    return await submit_stock_analysis_task(request, current_user)
