import asyncio
from contextlib import asynccontextmanager
from time import perf_counter
from urllib.parse import parse_qsl
from urllib.parse import urlencode

from fastapi import Depends, FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.init_db import init_db
from app.core.logger import get_logger
from app.core.request_context import clear_request_id
from app.core.request_context import clear_current_user_id
from app.core.request_context import get_or_create_request_id
from app.core.request_context import set_request_id
from app.core.security import get_current_user
from app.websocket.routes import router as websocket_router

# Get logger
logger = get_logger(__name__)

SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "jwt",
    "key",
    "passwd",
    "password",
    "refresh_token",
    "secret",
    "sign",
    "signature",
    "token",
}
MAX_QUERY_LOG_LENGTH = 1024
MAX_QUERY_LOG_PARAMS = 50


def sanitize_query_string(query_string: str) -> str:
    """
    Return a query string safe for access logs.

    Args:
        query_string: Raw URL query string.

    Returns:
        Sanitized query string with sensitive values redacted and output bounded.
    """
    if not query_string:
        return ""

    query_parts = query_string.split("&")
    truncated_params = len(query_parts) > MAX_QUERY_LOG_PARAMS
    params = parse_qsl("&".join(query_parts[:MAX_QUERY_LOG_PARAMS]), keep_blank_values=True)
    sanitized_params = []
    for key, value in params:
        sanitized_value = "[REDACTED]" if key.lower() in SENSITIVE_QUERY_KEYS else value
        sanitized_params.append((key, sanitized_value))

    if truncated_params:
        sanitized_params.append(("__truncated__", "true"))

    sanitized_query = urlencode(sanitized_params, doseq=True)
    if len(sanitized_query) > MAX_QUERY_LOG_LENGTH:
        return f"{sanitized_query[:MAX_QUERY_LOG_LENGTH]}...[TRUNCATED]"
    return sanitized_query


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("Application starting, initializing database and background tasks")
    with SessionLocal() as db:
        init_db(db)
        logger.info("Database initialization completed")

    # Check if Redis is initialized, if not initialize it in the event loop
    from app.core.redis_client import redis_client
    if redis_client.redis is None:
        try:
            await asyncio.wait_for(redis_client.init_pool(), timeout=15.0)
            logger.info("Redis connection pool initialized in startup_event")
        except asyncio.TimeoutError:
            logger.error("Redis connection pool initialization timed out")
            raise SystemExit("Redis initialization timed out")
        except Exception as e:
            logger.error(f"Failed to initialize Redis connection pool: {e}")
            logger.warning("Please ensure Redis service is running and connection configuration is correct.")
            raise SystemExit(f"Redis initialization failed: {e}")

    # 止据/止赏任务已自禁用，由 AI 自主决策是否卖出
    # Stop-loss/take-profit task disabled, AI makes sell decisions autonomously
    logger.info("Stop-loss and take-profit checking task has been removed, AI makes decisions autonomously")
    
    # Start data refresh scheduler
    try:
        from app.data.refresh_scheduler import refresh_scheduler
        refresh_scheduler.start()
        logger.info("Data refresh scheduler started")
    except Exception as e:
        logger.error(f"Failed to start data refresh scheduler: {e}")
    
    # Cleanup zombie tasks
    try:
        from app.tasks.task_manager import task_manager
        with SessionLocal() as db:
            task_manager.cleanup_zombie_tasks(db)
    except Exception as e:
        logger.error(f"Failed to cleanup zombie tasks: {e}")

    # Reset active AI analysis sessions to failed upon restart
    try:
        from app.models.session import Session as AnalysisSession
        with SessionLocal() as db:
            active_sessions = db.query(AnalysisSession).filter(AnalysisSession.status == "active").all()
            if active_sessions:
                logger.info(f"Found {len(active_sessions)} active sessions on startup. Marking them as failed.")
                for session in active_sessions:
                    session.status = "failed"
                db.commit()
    except Exception as e:
        logger.error(f"Failed to reset active sessions: {e}")

    # Reset interrupted AI stock picker runs to failed upon restart
    try:
        from app.ai.stock_picker.service import stock_picker_service
        cleaned_runs = stock_picker_service.cleanup_interrupted_runs()
        if cleaned_runs:
            logger.info(f"Found {cleaned_runs} interrupted stock picker runs on startup. Marked them as failed.")
    except Exception as e:
        logger.error(f"Failed to reset interrupted stock picker runs: {e}")

    # Reset interrupted experience review runs to failed upon restart
    try:
        from app.ai.experience.service import experience_service
        cleaned_reviews = experience_service.cleanup_interrupted_review_runs()
        if cleaned_reviews:
            logger.info(
                f"Found {cleaned_reviews} interrupted experience review runs on startup. Marked them as failed."
            )
    except Exception as e:
        logger.error(f"Failed to reset interrupted experience review runs: {e}")

    # Start async system scheduler after interrupted runs are cleaned up
    try:
        from app.tasks.async_scheduler import async_task_scheduler
        async_task_scheduler.start()
        logger.info("Async task scheduler started")
    except Exception as e:
        logger.error(f"Failed to start async task scheduler: {e}")

    yield

    # Shutdown logic
    logger.info("Application shutting down, stopping background tasks")

    # Stop data refresh scheduler
    try:
        from app.data.refresh_scheduler import refresh_scheduler
        refresh_scheduler.stop()
        logger.info("Data refresh scheduler stopped")
    except Exception as e:
        logger.error(f"Failed to stop data refresh scheduler: {e}")
    
    # Stop async system scheduler before closing shared resources
    try:
        from app.tasks.async_scheduler import async_task_scheduler
        async_task_scheduler.stop()
        logger.info("Async task scheduler stopped")
    except Exception as e:
        logger.error(f"Failed to stop async task scheduler: {e}")
    
    # 止据/止赏任务已自禁用，无需停止
    # Stop-loss/take-profit task disabled, no need to stop
    logger.info("Stop-loss and take-profit checking task has been removed, no longer stopping")
    
    # Cancel in-process async background tasks
    try:
        from app.tasks.async_task_runner import async_task_runner
        await async_task_runner.stop_all()
        logger.info("Async task runner cleaned up")
    except Exception as e:
        logger.error(f"Failed to cleanup async task runner: {e}")

    # Close Redis connection after scheduled and background tasks are stopped
    try:
        from app.core.redis_client import redis_client
        await redis_client.close()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.error(f"Failed to close Redis connection: {e}")

    try:
        from app.ai.agentic.tooling.browser_tool import close_browser_context
        await close_browser_context(reason="backend_shutdown")
        logger.info("CloakBrowser context closed")
    except Exception as e:
        logger.error(f"Failed to close CloakBrowser context: {e}")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json" if settings.ENABLE_OPENAPI_DOCS else None,
    docs_url=f"{settings.API_V1_STR}/docs" if settings.ENABLE_OPENAPI_DOCS else None,
    redoc_url=f"{settings.API_V1_STR}/redoc" if settings.ENABLE_OPENAPI_DOCS else None,
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging middleware
@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    started_at = perf_counter()
    request_id = get_or_create_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    token = set_request_id(request_id)
    logger.info(
        "http request started",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query_string": sanitize_query_string(request.url.query),
            "client_ip": request.client.host if request.client else "unknown",
            "source": "backend_access",
        },
    )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http request failed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                "client_ip": request.client.host if request.client else "unknown",
                "source": "backend_access",
            },
        )
        clear_request_id(token)
        clear_current_user_id()
        raise
    response.headers["x-request-id"] = request_id
    logger.info(
        "http request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "client_ip": request.client.host if request.client else "unknown",
            "source": "backend_access",
        },
    )
    clear_request_id(token)
    clear_current_user_id()
    return response

# Register API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Register WebSocket router
app.include_router(websocket_router)

@app.get("/")
def root(current_user=Depends(get_current_user)):
    del current_user
    return {"message": "天枢智投 API"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
