from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.ai.stock_analysis.runner import run_stock_analysis_task
from app.ai.stock_analysis.schemas import StockAnalysisRequest
from app.core.request_context import get_or_create_request_id
from app.core.utils.formatters import StockCodeStandardizer
from app.models.data_storage import StockBasic
from app.models.user import User
from app.tasks.async_task_runner import async_task_runner
from app.tasks.task_manager import task_manager

STOCK_ANALYSIS_TASK_TYPE = "stock_analysis"


def load_stock_basic(db: Session, stock_code: str) -> StockBasic:
    """
    加载股票基础信息并统一股票代码格式。

    Args:
        db: 数据库会话。
        stock_code: 用户输入的股票代码。

    Returns:
        股票基础信息模型。

    Raises:
        HTTPException: 股票不存在时抛出 404。
    """
    standard_code = StockCodeStandardizer.standardize(stock_code)
    stock = db.query(StockBasic).filter(StockBasic.stock_code == standard_code).first()
    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock {standard_code} not found",
        )
    return stock


def submit_stock_analysis_task(
    db: Session,
    request: StockAnalysisRequest,
    current_user: User,
) -> dict[str, Any]:
    """
    创建单 LLM 股票分析后台任务。

    Args:
        db: 数据库会话。
        request: 股票分析请求。
        current_user: 当前登录用户。

    Returns:
        任务提交结果。
    """
    stock = load_stock_basic(db, request.stock_code) if request.stock_code else None
    stock_code = stock.stock_code if stock else None
    stock_name = stock.name if stock else None
    task_name = f"AI Research Analysis - {stock_code}" if stock_code else "AI Research Analysis"
    parameters = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "question": request.question,
    }
    task_info = task_manager.submit_task(
        db=db,
        task_name=task_name,
        task_type=STOCK_ANALYSIS_TASK_TYPE,
        parameters=parameters,
        allow_concurrent=True,
        user_id=current_user.id,
    )
    if task_info.get("new_task", True):
        async_task_runner.submit_task(
            task_id=task_info["task_id"],
            task_func=run_stock_analysis_task,
            task_kwargs={
                "task_id": task_info["task_id"],
                "stock_code": stock_code,
                "stock_name": stock_name,
                "question": request.question,
            },
            task_name=task_info["task_name"],
            request_id=get_or_create_request_id(),
        )
    return task_info
