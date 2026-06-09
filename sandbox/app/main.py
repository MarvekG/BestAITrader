from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.schemas import ExecuteRequest, ExecuteResponse
from app.services.python_sandbox import execute_python_in_sandbox
from app.services.python_sandbox_pool import get_prewarmed_sandbox_pool


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """
    管理沙箱服务生命周期。

    Args:
        _: FastAPI 应用实例。

    Yields:
        应用运行上下文。
    """
    settings = get_settings()
    logger.info(
        "python sandbox service started",
        extra={
            "prewarm_enabled": settings.SANDBOX_PREWARM_POOL_ENABLED,
            "prewarm_pool_size": settings.SANDBOX_PREWARM_POOL_SIZE,
            "max_concurrent_executions": settings.SANDBOX_MAX_CONCURRENT_EXECUTIONS,
            "timeout_seconds": settings.SANDBOX_TIMEOUT_SECONDS,
        },
    )
    try:
        if settings.SANDBOX_PREWARM_POOL_ENABLED and settings.SANDBOX_PREWARM_ON_STARTUP:
            await get_prewarmed_sandbox_pool().prewarm()
            logger.info("python sandbox prewarmed worker pool started")
        yield
    finally:
        logger.info("python sandbox service stopping")
        if settings.SANDBOX_PREWARM_POOL_ENABLED:
            await get_prewarmed_sandbox_pool().shutdown()
            logger.info("python sandbox prewarmed worker pool stopped")
        logger.info("python sandbox service stopped")


app = FastAPI(title="Best AI Trader Python Sandbox", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """
    返回服务健康状态。

    Returns:
        健康状态字典。
    """
    return {"status": "ok"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute_python(request: ExecuteRequest) -> ExecuteResponse:
    """
    执行受限 Python 代码并返回标准沙箱响应。

    Args:
        request: Python 沙箱执行请求。

    Returns:
        Python 沙箱执行响应。
    """
    result = await execute_python_in_sandbox(
        code=request.code,
        limits=request.limits,
        timeout_seconds=request.timeout_seconds,
    )
    return ExecuteResponse.model_validate(result)
