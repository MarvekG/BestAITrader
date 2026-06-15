from fastapi import Depends, FastAPI

from app.core.config import settings
from app.core.security import get_current_user


def register_api_routes(app: FastAPI) -> None:
    """
    注册 HTTP API 路由，避免导入 API 包时提前加载所有业务端点。

    Args:
        app: 需要挂载 API 路由的 FastAPI 应用实例。
    """
    from app.api.endpoints import (
        accounts,
        auth,
        data,
        debate,
        debate_ws,
        general,
        llm,
        market_watch,
        mcp,
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
    from app.ai.stock_picker.interactive_research.api import router as interactive_stock_picker_router
    from app.ai.stock_picker.api import router as stock_picker_router

    prefix = settings.API_V1_STR
    authenticated = [Depends(get_current_user)]

    app.include_router(sessions.router, prefix=f"{prefix}/sessions", tags=["sessions"], dependencies=authenticated)
    app.include_router(data.router, prefix=f"{prefix}/data", tags=["data"], dependencies=authenticated)
    app.include_router(debate.router, prefix=f"{prefix}/debate", tags=["debate"], dependencies=authenticated)
    app.include_router(trading.router, prefix=f"{prefix}/trading", tags=["trading"], dependencies=authenticated)
    app.include_router(accounts.router, prefix=f"{prefix}/accounts", tags=["accounts"], dependencies=authenticated)
    app.include_router(auth.router, prefix=f"{prefix}/auth", tags=["auth"])
    app.include_router(
        stock_warehouse.router,
        prefix=f"{prefix}/stock-warehouse",
        tags=["stock-warehouse"],
        dependencies=authenticated,
    )
    # market_watch has a WebSocket route; HTTP endpoints already depend on get_current_user,
    # and the WebSocket endpoint validates its bearer token explicitly.
    app.include_router(market_watch.router, prefix=f"{prefix}/market-watch", tags=["market-watch"])
    app.include_router(sources.router, prefix=f"{prefix}/sources", tags=["sources"], dependencies=authenticated)
    app.include_router(task.router, prefix=f"{prefix}/tasks", tags=["tasks"], dependencies=authenticated)
    app.include_router(llm.router, prefix=f"{prefix}/llm", tags=["llm"], dependencies=authenticated)
    app.include_router(debate_ws.router, prefix=f"{prefix}/debate", tags=["debate-websocket"])
    app.include_router(general.router, prefix=f"{prefix}/general", tags=["general"])
    app.include_router(performance.router, prefix=f"{prefix}/performance", tags=["performance"], dependencies=authenticated)
    app.include_router(portfolio.router, prefix=f"{prefix}/portfolio", tags=["portfolio"], dependencies=authenticated)
    app.include_router(
        risk_control.router,
        prefix=f"{prefix}/risk-control",
        tags=["risk-control"],
        dependencies=authenticated,
    )
    app.include_router(prompt.router, prefix=f"{prefix}/prompt", tags=["prompt"], dependencies=authenticated)
    app.include_router(testing.router, prefix=f"{prefix}/testing", tags=["testing"], dependencies=authenticated)
    app.include_router(
        stock_analysis_router,
        prefix=f"{prefix}/stock-analysis",
        tags=["stock-analysis"],
        dependencies=authenticated,
    )
    if settings.ENABLE_RUNTIME_EXTENSIONS:
        app.include_router(
            news_plugins.router,
            prefix=f"{prefix}/news-plugins",
            tags=["news-plugins"],
            dependencies=authenticated,
        )
        app.include_router(skills.router, prefix=f"{prefix}/skills", tags=["skills"], dependencies=authenticated)
        app.include_router(mcp.router, prefix=f"{prefix}/mcp", tags=["mcp"], dependencies=authenticated)
    app.include_router(
        stock_picker_router,
        prefix=f"{prefix}/ai-stock-picker",
        tags=["ai-stock-picker"],
        dependencies=authenticated,
    )
    app.include_router(
        interactive_stock_picker_router,
        prefix=f"{prefix}/ai-stock-picker/interactive",
        tags=["ai-stock-picker-interactive"],
        dependencies=authenticated,
    )
    app.include_router(experience_router, prefix=f"{prefix}/experience", tags=["experience"], dependencies=authenticated)
