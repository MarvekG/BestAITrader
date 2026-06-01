from pydantic import BaseModel, Field, field_validator

MAX_STOCK_ANALYSIS_QUESTION_LENGTH = 100000


class StockAnalysisRequest(BaseModel):
    """单 LLM 股票分析请求。"""

    stock_code: str | None = Field(None, max_length=20)
    question: str = Field(..., max_length=MAX_STOCK_ANALYSIS_QUESTION_LENGTH)

    @field_validator("stock_code", mode="before")
    @classmethod
    def normalize_stock_code(cls, value: object) -> str | None:
        """
        将空白股票代码归一化为空上下文。

        Args:
            value: 用户传入的可选股票代码。

        Returns:
            非空股票代码或 None。
        """
        text = str(value or "").strip()
        return text or None

    @field_validator("question", mode="before")
    @classmethod
    def normalize_question(cls, value: object) -> str:
        """
        去除问题首尾空白并拒绝空问题。

        Args:
            value: 用户传入的问题文本。

        Returns:
            归一化后的问题文本。

        Raises:
            ValueError: 问题为空白时抛出。
        """
        text = str(value or "").strip()
        if not text:
            raise ValueError("question is required")
        return text


class StockAnalysisTaskResponse(BaseModel):
    """单 LLM 股票分析任务提交响应。"""

    task_id: str
    task_name: str
    status: str
    message: str
    new_task: bool
