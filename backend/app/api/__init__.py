from fastapi import APIRouter, Depends

from app.api.endpoints import (
    accounts,
    auth,
    data,
    debate,
    debate_ws,
    general,
    llm,
    market_watch,
    news_plugins,
    performance,
    portfolio,
    prompt,
    risk_control,
    sessions,
    skills,
    sources,
    stock_warehouse,
    task,
    testing,
    trading,
)
from app.ai.experience.api import router as experience_router
from app.ai.stock_analysis.api import router as stock_analysis_router
from app.ai.stock_picker.api import router as stock_picker_router
from app.core.config import settings
from app.core.security import get_current_user

api_router = APIRouter()
authenticated = [Depends(get_current_user)]

# 注册路由
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"], dependencies=authenticated)
api_router.include_router(data.router, prefix="/data", tags=["data"], dependencies=authenticated)
api_router.include_router(debate.router, prefix="/debate", tags=["debate"], dependencies=authenticated)
api_router.include_router(trading.router, prefix="/trading", tags=["trading"], dependencies=authenticated)
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"], dependencies=authenticated)
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    stock_warehouse.router,
    prefix="/stock-warehouse",
    tags=["stock-warehouse"],
    dependencies=authenticated,
)
# market_watch has a WebSocket route; HTTP endpoints already depend on get_current_user,
# and the WebSocket endpoint validates its bearer token explicitly.
api_router.include_router(market_watch.router, prefix="/market-watch", tags=["market-watch"])

api_router.include_router(sources.router, prefix="/sources", tags=["sources"], dependencies=authenticated)

api_router.include_router(task.router, prefix="/tasks", tags=["tasks"], dependencies=authenticated)
api_router.include_router(llm.router, prefix="/llm", tags=["llm"], dependencies=authenticated)
api_router.include_router(debate_ws.router, prefix="/debate", tags=["debate-websocket"])
api_router.include_router(general.router, prefix="/general", tags=["general"])
api_router.include_router(performance.router, prefix="/performance", tags=["performance"], dependencies=authenticated)
api_router.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"], dependencies=authenticated)
api_router.include_router(risk_control.router, prefix="/risk-control", tags=["risk-control"], dependencies=authenticated)
api_router.include_router(prompt.router, prefix="/prompt", tags=["prompt"], dependencies=authenticated)
api_router.include_router(testing.router, prefix="/testing", tags=["testing"], dependencies=authenticated)
api_router.include_router(
    stock_analysis_router,
    prefix="/stock-analysis",
    tags=["stock-analysis"],
    dependencies=authenticated,
)
if settings.ENABLE_RUNTIME_EXTENSIONS:
    api_router.include_router(
        news_plugins.router,
        prefix="/news-plugins",
        tags=["news-plugins"],
        dependencies=authenticated,
    )
    api_router.include_router(skills.router, prefix="/skills", tags=["skills"], dependencies=authenticated)
api_router.include_router(
    stock_picker_router,
    prefix="/ai-stock-picker",
    tags=["ai-stock-picker"],
    dependencies=authenticated,
)
api_router.include_router(experience_router, prefix="/experience", tags=["experience"], dependencies=authenticated)
