from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.crud.user import create_user
from app.models.async_task import AsyncTask
from app.models.account import Account
from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.models.position import Position
from app.models.user import User
from app.schemas.user import UserCreate


async def _seed_stock_basic(
    test_db,
    *,
    stock_code: str,
    name: str,
    industry: str = "Banking",
    market: str = "SZSE",
):
    async with test_db() as db:
        record = StockBasic(
            stock_code=stock_code,
            name=name,
            industry=industry,
            market=market,
            data_source="test",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record


def _session_payload(stock_code: str) -> dict:
    return {
        "stock_code": stock_code,
        "trading_frequency": "swing",
        "trading_strategy": "trend_following",
    }


def _create_session(client, auth_headers, stock_code: str) -> str:
    response = client.post(
        "/api/v1/sessions/",
        json=_session_payload(stock_code),
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()["session_id"]


async def _create_user(test_db, *, username: str, password: str) -> User | None:
    async with test_db() as db:
        return await create_user(
            db,
            UserCreate(
                username=username,
                email=f"{username}@example.com",
                password=password,
            ),
        )


def _create_authenticated_user(client, test_db, run_async, *, username: str):
    password = "password123"
    run_async(_create_user(test_db, username=username, password=password))
    response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class TestAuthAPI:
    def test_register(self, client):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "register_user",
                "email": "register_user@example.com",
                "password": "password123",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "User registration is disabled"

    def test_login(self, client, test_db, run_async):
        run_async(_create_user(test_db, username="login_user", password="password123"))
        response = client.post(
            "/api/v1/auth/login",
            data={
                "username": "login_user",
                "password": "password123",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["token_type"] == "bearer"


class TestStockWarehouseAPI:
    def test_get_warehouse_list(self, client, auth_headers):
        response = client.get("/api/v1/stock-warehouse/", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_add_stock(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="600519.SH", name="Kweichow Moutai", industry="Liquor", market="SSE"))

        response = client.post(
            "/api/v1/stock-warehouse/",
            json={"stock_code": "600519.SH"},
            headers=auth_headers,
        )

        assert response.status_code == 201
        assert response.json()["stock_code"] == "600519.SH"
        assert response.json()["auto_analysis_enabled"] is False
        assert response.json()["auto_analysis_frequency"] == "daily"

    def test_update_stock_auto_analysis_config(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="600519.SH", name="Kweichow Moutai", industry="Liquor", market="SSE"))
        client.post(
            "/api/v1/stock-warehouse/",
            json={"stock_code": "600519.SH"},
            headers=auth_headers,
        )

        response = client.put(
            "/api/v1/stock-warehouse/600519.SH",
            json={
                "auto_analysis_enabled": True,
                "auto_analysis_frequency": "weekly",
                "auto_analysis_time": "09:40",
                "auto_analysis_trading_frequency": "Swing Trading",
                "auto_analysis_trading_strategy": "Trend Following",
                "auto_analysis_run_immediately": True,
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["auto_analysis_enabled"] is True
        assert payload["auto_analysis_frequency"] == "weekly"
        assert payload["auto_analysis_time"] == "09:40"
        assert payload["auto_analysis_trading_frequency"] == "Swing Trading"
        assert payload["auto_analysis_run_immediately"] is True

    def test_update_stock_auto_analysis_rejects_null_config(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="600519.SH", name="Kweichow Moutai", industry="Liquor", market="SSE"))
        client.post(
            "/api/v1/stock-warehouse/",
            json={"stock_code": "600519.SH"},
            headers=auth_headers,
        )

        response = client.put(
            "/api/v1/stock-warehouse/600519.SH",
            json={"auto_analysis_frequency": None},
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_stock_warehouse_entries_are_isolated_by_user(self, client, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="600519.SH", name="Kweichow Moutai", industry="Liquor", market="SSE"))
        owner_headers = _create_authenticated_user(client, test_db, run_async, username="warehouse_owner")
        other_headers = _create_authenticated_user(client, test_db, run_async, username="warehouse_other")

        create_response = client.post(
            "/api/v1/stock-warehouse/",
            json={"stock_code": "600519.SH"},
            headers=owner_headers,
        )
        other_get_response = client.get("/api/v1/stock-warehouse/600519.SH", headers=other_headers)
        other_list_response = client.get("/api/v1/stock-warehouse/", headers=other_headers)

        assert create_response.status_code == 201
        assert other_get_response.status_code == 404
        assert other_list_response.status_code == 200
        assert other_list_response.json() == []


class TestPromptAPI:
    def test_get_templates(self, client, auth_headers):
        response = client.get("/api/v1/prompt/", headers=auth_headers)
        assert response.status_code == 200
        assert isinstance(response.json(), dict)
        assert response.json()

    def test_prompt_templates_are_read_only(self, client, auth_headers):
        response = client.post(
            "/api/v1/prompt/FUNDAMENTAL",
            json={"content": "prompt override is disabled"},
            headers=auth_headers,
        )

        assert response.status_code == 405

    def test_generate_prompt_endpoint_removed(self, client, auth_headers):
        response = client.post(
            "/api/v1/prompt/generate",
            json={"content": "unused"},
            headers=auth_headers,
        )

        assert response.status_code == 405


class TestSourcesAPI:
    def test_list_sources(self, client, auth_headers):
        response = client.get("/api/v1/sources/", headers=auth_headers)
        assert response.status_code == 200
        payload = response.json()
        assert "sources" in payload
        assert "default_source" in payload

    def test_data_source_config_persists_to_system_settings(self, client, auth_headers, test_db, run_async):
        from app.models.system_setting import SystemSetting

        response = client.post(
            "/api/v1/sources/config",
            headers=auth_headers,
            json={
                "tushare_token": "tushare-secret",
                "tushare_api_url": "https://api.example.com/tushare",
                "tavily_api_key": ["tavily-secret"],
                "news_api_key": ["news-secret"],
            },
        )

        assert response.status_code == 200, response.text
        async def _load_values():
            async with test_db() as db:
                result = await db.execute(
                    select(SystemSetting).where(
                        SystemSetting.key.in_(
                            [
                                "data_sources.tushare.token",
                                "data_sources.tushare.api_url",
                                "data_sources.tavily.api_key",
                                "data_sources.newsapi.api_key",
                            ]
                        )
                    )
                )
                return {row.key: row.value for row in result.scalars().all()}

        values = run_async(_load_values())
        assert values == {
            "data_sources.tushare.token": "tushare-secret",
            "data_sources.tushare.api_url": "https://api.example.com/tushare",
            "data_sources.tavily.api_key": ["tavily-secret"],
            "data_sources.newsapi.api_key": ["news-secret"],
        }

        config_response = client.get("/api/v1/sources/config", headers=auth_headers)

        assert config_response.status_code == 200
        assert config_response.json()["config"] == {
            "tushare_api_url": "https://api.example.com/tushare",
            "tushare_token": "tushare-secret",
            "tavily_api_key": ["tavily-secret"],
            "news_api_key": ["news-secret"],
        }

    def test_data_source_config_cache_invalidates_after_update(self, client, auth_headers):
        first_response = client.post(
            "/api/v1/sources/config",
            headers=auth_headers,
            json={"tavily_api_key": ["first-key"]},
        )
        assert first_response.status_code == 200, first_response.text

        first_config = client.get("/api/v1/sources/config", headers=auth_headers)
        assert first_config.status_code == 200
        assert first_config.json()["config"]["tavily_api_key"] == ["first-key"]

        second_response = client.post(
            "/api/v1/sources/config",
            headers=auth_headers,
            json={"tavily_api_key": ["second-secret"]},
        )
        assert second_response.status_code == 200, second_response.text

        second_config = client.get("/api/v1/sources/config", headers=auth_headers)
        assert second_config.status_code == 200
        assert second_config.json()["config"]["tavily_api_key"] == ["second-secret"]

    def test_data_source_config_test_endpoints_passthrough(self, client, auth_headers):
        class FakeTushareClient:
            def stock_basic(self, **kwargs):
                return [{"ok": True, "params": kwargs}]

        config_response = client.post(
            "/api/v1/sources/config",
            headers=auth_headers,
            json={
                "tushare_token": "tushare-token",
                "tushare_api_url": "https://api.example.com/tushare",
                "tavily_api_key": ["tavily-token-a", "tavily-token-b"],
                "news_api_key": ["news-token-a", "news-token-b"],
            },
        )
        assert config_response.status_code == 200, config_response.text

        with (
            patch(
                "app.api.endpoints.sources.TushareIngestor.get_pro_client",
                return_value=FakeTushareClient(),
            ),
            patch(
                "app.api.endpoints.sources.tavily.search_with_api_keys",
                AsyncMock(return_value=[{"title": "tavily result", "source": "tavily"}]),
            ) as tavily_search,
            patch(
                "app.api.endpoints.sources.newsapi.search_with_api_keys",
                AsyncMock(return_value=[{"title": "newsapi result", "source": "newsapi"}]),
            ) as newsapi_search,
        ):
            tushare_response = client.post("/api/v1/sources/config/test/tushare", headers=auth_headers)
            tavily_response = client.post(
                "/api/v1/sources/config/test/tavily",
                headers=auth_headers,
                json={"query": "AI"},
            )
            newsapi_response = client.post(
                "/api/v1/sources/config/test/newsapi",
                headers=auth_headers,
                json={"query": "AI"},
            )

        assert tushare_response.status_code == 200
        assert tushare_response.json()["status"] == "success"
        assert tushare_response.json()["data"][0]["params"]["ts_code"] == "000001.SZ"
        assert tushare_response.json()["data"][0]["params"]["fields"] == "ts_code,symbol,name,area,industry,list_date"

        for response in [tavily_response, newsapi_response]:
            assert response.status_code == 200
            assert response.json()["status"] == "completed"
            assert len(response.json()["results"]) == 2
            for item in response.json()["results"]:
                assert item["status"] == "success"
                assert item["data"]

        assert [call.args[0] for call in tavily_search.await_args_list] == [["tavily-token-a"], ["tavily-token-b"]]
        assert [call.args[1] for call in tavily_search.await_args_list] == ["AI", "AI"]
        assert [call.args[0] for call in newsapi_search.await_args_list] == [["news-token-a"], ["news-token-b"]]
        assert [call.args[1] for call in newsapi_search.await_args_list] == ["AI", "AI"]


class TestSessionAPI:
    def test_create_session(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))

        response = client.post(
            "/api/v1/sessions/",
            json=_session_payload("000001.SZ"),
            headers=auth_headers,
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["stock_code"] == "000001.SZ"
        assert payload["stock_name"] == "Ping An Bank"

    def test_get_sessions(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")

        async def _seed_task():
            async with test_db() as db:
                db.add(
                    AsyncTask(
                        task_id="task-1",
                        task_name="AI Analysis - 000001.SZ",
                        task_type="ai_analysis",
                        status="completed",
                        parameters={"session_id": session_id},
                        completed_at=datetime(2024, 1, 2, 3, 4, 5),
                    )
                )
                await db.commit()

        run_async(_seed_task())

        response = client.get("/api/v1/sessions/", headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["ended_at"] == "2024-01-02T03:04:05"

    def test_get_sessions_paginated_searches_stock_name(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        run_async(_seed_stock_basic(test_db, stock_code="600519.SH", name="Kweichow Moutai"))
        _create_session(client, auth_headers, "000001.SZ")
        _create_session(client, auth_headers, "600519.SH")

        response = client.get(
            "/api/v1/sessions/",
            params={"paginated": True, "skip": 0, "limit": 1, "q": "Moutai"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["skip"] == 0
        assert payload["limit"] == 1
        assert len(payload["items"]) == 1
        assert payload["items"][0]["stock_code"] == "600519.SH"
        assert payload["items"][0]["stock_name"] == "Kweichow Moutai"

        strategy_response = client.get(
            "/api/v1/sessions/",
            params={"paginated": True, "q": "trend_following"},
            headers=auth_headers,
        )

        assert strategy_response.status_code == 200
        assert strategy_response.json()["total"] == 0


class TestDataAPI:
    def test_get_stock_data(self, client, auth_headers):
        with patch(
            "app.api.endpoints.data.data_storage_service.get_stock_data_from_db",
            new=AsyncMock(return_value={"stock_code": "000001.SZ", "name": "Ping An Bank"}),
        ):
            response = client.get("/api/v1/data/stocks/000001", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["stock_code"] == "000001.SZ"

    def test_get_stock_name(self, client, auth_headers):
        with patch(
            "app.api.endpoints.data.data_storage_service.get_stock_basic",
            new=AsyncMock(return_value={"stock_code": "000001.SZ", "name": "Ping An Bank"}),
        ):
            response = client.get("/api/v1/data/stock/name/000001", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["stock_name"] == "Ping An Bank"


class TestTradingAPI:
    def test_get_my_orders_initializes_missing_account(self, client, auth_headers):
        response = client.get("/api/v1/trading/my-orders", headers=auth_headers)

        assert response.status_code == 200
        assert response.json() == []

    def test_place_order(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")

        mocked_trade_result = {
            "success": True,
            "message": "order accepted",
            "order": SimpleNamespace(order_id="order-1"),
            "trade_result": {
                "message": "order accepted",
                "trade_record": {
                    "id": "trade-1",
                    "price": 10.0,
                    "shares": 100,
                    "turnover": 1000.0,
                    "commission": 1.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                    "total_fee": 1.0,
                },
            },
        }

        with patch(
            "app.trading.service.trading_service.execute_order_and_update_db",
            new=AsyncMock(return_value=mocked_trade_result),
        ):
            response = client.post(
                "/api/v1/trading/orders",
                json={
                    "session_id": session_id,
                    "stock_code": "000001",
                    "stock_name": "Ping An Bank",
                    "action": "buy",
                    "order_type": "market",
                    "price": 10.0,
                    "shares": 100,
                    "stop_loss": 9.5,
                },
                headers=auth_headers,
            )

        assert response.status_code == 201
        assert response.json()["success"] is True

    def test_place_order_rejects_invalid_stop_loss(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")

        response = client.post(
            "/api/v1/trading/orders",
            json={
                "session_id": session_id,
                "stock_code": "000001",
                "stock_name": "Ping An Bank",
                "action": "buy",
                "order_type": "market",
                "price": 10.0,
                "shares": 100,
                "stop_loss": 0,
            },
            headers=auth_headers,
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"][-1] == "stop_loss"

    def test_place_order_rejects_missing_stop_loss_by_risk_control(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")

        response = client.post(
            "/api/v1/trading/orders",
            json={
                "session_id": session_id,
                "stock_code": "000001",
                "stock_name": "Ping An Bank",
                "action": "buy",
                "order_type": "limit",
                "price": 10.0,
                "shares": 100,
            },
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"]["reason"] == "risk_control_blocked"
        assert response.json()["detail"]["blocks"][0]["rule"] == "require_stop_loss"

    def test_place_order_returns_400_when_service_risk_control_blocks(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")
        risk_result = {
            "enabled": True,
            "passed": False,
            "severity": "block",
            "accepted": [],
            "blocks": [{"rule": "require_stop_loss", "message": "blocked"}],
            "metrics": {},
        }

        mocked_trade_result = {
            "success": False,
            "reason": "risk_control_blocked",
            "message": "Order blocked by portfolio risk control",
            "risk_control": risk_result,
        }

        with patch(
            "app.risk_control.service.portfolio_risk_control_service.evaluate_order",
        ) as mock_precheck, patch(
            "app.trading.service.trading_service.execute_order_and_update_db",
            new=AsyncMock(return_value=mocked_trade_result),
        ):
            response = client.post(
                "/api/v1/trading/orders",
                json={
                    "session_id": session_id,
                    "stock_code": "000001",
                    "stock_name": "Ping An Bank",
                    "action": "buy",
                    "order_type": "market",
                    "price": 10.0,
                    "shares": 100,
                    "stop_loss": 9.5,
                },
                headers=auth_headers,
            )

        assert response.status_code == 400
        assert response.json()["detail"]["reason"] == "risk_control_blocked"
        assert response.json()["detail"]["risk_control"] == risk_result
        mock_precheck.assert_not_called()

    def test_place_order_rejects_invalid_action_schema(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")

        response = client.post(
            "/api/v1/trading/orders",
            json={
                "session_id": session_id,
                "stock_code": "000001",
                "stock_name": "Ping An Bank",
                "action": "hold",
                "order_type": "market",
                "price": 10.0,
                "shares": 100,
                "stop_loss": 9.5,
            },
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_place_order_rejects_non_positive_shares_schema(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")

        response = client.post(
            "/api/v1/trading/orders",
            json={
                "session_id": session_id,
                "stock_code": "000001",
                "stock_name": "Ping An Bank",
                "action": "buy",
                "order_type": "market",
                "price": 10.0,
                "shares": 0,
                "stop_loss": 9.5,
            },
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_place_order_rejects_negative_price_schema(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        session_id = _create_session(client, auth_headers, "000001.SZ")

        response = client.post(
            "/api/v1/trading/orders",
            json={
                "session_id": session_id,
                "stock_code": "000001",
                "stock_name": "Ping An Bank",
                "action": "buy",
                "order_type": "limit",
                "price": -1.0,
                "shares": 100,
                "stop_loss": 9.5,
            },
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_place_order_rejects_empty_stock_code_schema(self, client, auth_headers):
        response = client.post(
            "/api/v1/trading/orders",
            json={
                "stock_code": "",
                "stock_name": "Ping An Bank",
                "action": "buy",
                "order_type": "market",
                "price": 10.0,
                "shares": 100,
                "stop_loss": 9.5,
            },
            headers=auth_headers,
        )

        assert response.status_code == 422


class TestAccountAPI:
    def test_trading_page_account_reads_initialize_missing_account(self, client, auth_headers):
        assets_response = client.get("/api/v1/accounts/my-assets", headers=auth_headers)
        positions_response = client.get("/api/v1/accounts/my-positions", headers=auth_headers)

        assert assets_response.status_code == 200
        assert positions_response.status_code == 200
        assert float(assets_response.json()["cash_balance"]) == 1000000.0
        assert positions_response.json() == []

    def test_get_my_account_assets(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        _create_session(client, auth_headers, "000001.SZ")

        response = client.get("/api/v1/accounts/my-assets", headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert "cash_balance" in payload
        assert "user_id" in payload

    def test_my_total_funds(self, client, auth_headers, test_db, run_async):
        run_async(_seed_stock_basic(test_db, stock_code="000001.SZ", name="Ping An Bank"))
        _create_session(client, auth_headers, "000001.SZ")

        get_response = client.get("/api/v1/accounts/my-total-funds", headers=auth_headers)
        assert get_response.status_code == 200

        put_response = client.put(
            "/api/v1/accounts/my-total-funds",
            json={"total_funds": 200000.0},
            headers=auth_headers,
        )

        assert put_response.status_code == 200
        assert float(put_response.json()["total_funds"]) == 200000.0

    def test_set_my_total_funds_preserves_frozen_cash(self, client, auth_headers, test_db, run_async):
        async def _seed_account_data():
            async with test_db() as db:
                result = await db.execute(select(User).where(User.username.like("test_%")))
                user = result.scalars().first()
                db.add(
                    Account(
                        user_id=user.id,
                        total_assets=Decimal("100000.0000"),
                        available_cash=Decimal("85000.0000"),
                        frozen_cash=Decimal("5000.0000"),
                        market_value=Decimal("10000.0000"),
                        initial_capital=Decimal("100000.0000"),
                        total_profit_loss=Decimal("0.0000"),
                    )
                )
                await db.commit()

        run_async(_seed_account_data())

        response = client.put(
            "/api/v1/accounts/my-total-funds",
            json={"total_funds": 120000.0},
            headers=auth_headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert float(payload["total_funds"]) == 120000.0
        assert float(payload["cash_balance"]) == 105000.0
        assert float(payload["frozen_cash"]) == 5000.0

    def test_set_my_total_funds_rejects_less_than_market_value_plus_frozen_cash(
        self,
        client,
        auth_headers,
        test_db,
        run_async,
    ):
        async def _seed_account_data():
            async with test_db() as db:
                result = await db.execute(select(User).where(User.username.like("test_%")))
                user = result.scalars().first()
                db.add(
                    Account(
                        user_id=user.id,
                        total_assets=Decimal("100000.0000"),
                        available_cash=Decimal("85000.0000"),
                        frozen_cash=Decimal("5000.0000"),
                        market_value=Decimal("10000.0000"),
                        initial_capital=Decimal("100000.0000"),
                        total_profit_loss=Decimal("0.0000"),
                    )
                )
                await db.commit()

        run_async(_seed_account_data())

        response = client.put(
            "/api/v1/accounts/my-total-funds",
            json={"total_funds": 14999.0},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Total funds cannot be less than market value plus frozen cash"

    def test_my_total_funds_uses_dynamic_portfolio_valuation(self, client, auth_headers, test_db, run_async):
        async def _seed_account_data():
            async with test_db() as db:
                result = await db.execute(select(User).where(User.username.like("test_%")))
                user = result.scalars().first()
                account = Account(
                    user_id=user.id,
                    total_assets=Decimal("1000000.0000"),
                    available_cash=Decimal("300000.0000"),
                    frozen_cash=Decimal("10000.0000"),
                    market_value=Decimal("100000.0000"),
                    initial_capital=Decimal("1000000.0000"),
                    total_profit_loss=Decimal("0.0000"),
                )
                db.add(account)
                await db.flush()
                db.add(
                    StockBasic(
                        stock_code="000001.SZ",
                        name="Ping An Bank",
                        industry="Banking",
                        market="SZSE",
                        data_source="test",
                    )
                )
                db.add_all([
                    StockRealtimeMarket(
                        stock_code="000001.SZ",
                        current_price=Decimal("12.0000"),
                        timestamp=datetime(2026, 6, 2, 10, 0, 0),
                    ),
                    Position(
                        account_id=account.account_id,
                        stock_code="000001.SZ",
                        total_shares=10000,
                        available_shares=10000,
                        frozen_shares=0,
                        avg_cost=Decimal("10.0000"),
                        current_price=Decimal("10.0000"),
                        market_value=Decimal("100000.0000"),
                        profit_loss=Decimal("0.0000"),
                        profit_loss_pct=Decimal("0.0000"),
                        purchase_details={"ledger": []},
                    ),
                ])
                await db.commit()

        run_async(_seed_account_data())

        response = client.get("/api/v1/accounts/my-total-funds", headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert float(payload["market_value"]) == 120000.0
        assert float(payload["total_funds"]) == 430000.0

    def test_my_positions_ignore_latest_zero_realtime_price(self, client, auth_headers, test_db, run_async):
        """
        账户持仓应忽略时间更新但价格无效的实时行情。

        Args:
            client: 测试 HTTP 客户端。
            auth_headers: 已登录用户的鉴权请求头。
            test_db: 异步测试数据库会话工厂。
        """
        async def _seed_position_data():
            async with test_db() as db:
                result = await db.execute(select(User).where(User.username.like("test_%")))
                user = result.scalars().first()
                account = Account(
                    user_id=user.id,
                    total_assets=Decimal("1000000.0000"),
                    available_cash=Decimal("900000.0000"),
                    frozen_cash=Decimal("0.0000"),
                    market_value=Decimal("100000.0000"),
                    initial_capital=Decimal("1000000.0000"),
                    total_profit_loss=Decimal("0.0000"),
                )
                db.add(account)
                await db.flush()
                db.add(
                    StockBasic(
                        stock_code="000001.SZ",
                        name="Ping An Bank",
                        industry="Banking",
                        market="SZSE",
                        data_source="test",
                    )
                )
                db.add_all([
                    StockRealtimeMarket(
                        stock_code="000001.SZ",
                        current_price=12.0,
                        timestamp=datetime(2026, 6, 10, 15, 0, 0),
                    ),
                    StockRealtimeMarket(
                        stock_code="000001.SZ",
                        current_price=0.0,
                        timestamp=datetime(2026, 6, 11, 9, 20, 0),
                    ),
                    Position(
                        account_id=account.account_id,
                        stock_code="000001.SZ",
                        total_shares=100,
                        available_shares=100,
                        frozen_shares=0,
                        avg_cost=Decimal("10.0000"),
                        current_price=Decimal("10.0000"),
                        market_value=Decimal("1000.0000"),
                        profit_loss=Decimal("0.0000"),
                        profit_loss_pct=Decimal("0.0000"),
                        purchase_details={"ledger": []},
                    ),
                ])
                await db.commit()

        run_async(_seed_position_data())

        response = client.get("/api/v1/accounts/my-positions", headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert payload[0]["current_price"] == 12.0
        assert payload[0]["market_value"] == 1200.0


class TestTaskAPI:
    def test_sync_stock_basic_async(self, client, auth_headers):
        with patch("app.tasks.async_task_runner.async_task_runner.submit_task", return_value=True):
            response = client.post("/api/v1/data/db/sync/stock-basic", headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "pending"
        assert "task_id" in payload

    def test_sync_stock_basic_concurrent_control(self, client, auth_headers):
        with patch("app.tasks.async_task_runner.async_task_runner.submit_task", return_value=True):
            first = client.post("/api/v1/data/db/sync/stock-basic", headers=auth_headers)
            second = client.post("/api/v1/data/db/sync/stock-basic", headers=auth_headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["task_id"] == second.json()["task_id"]

    def test_sync_dragon_tiger_data(self, client, auth_headers):
        with patch("app.tasks.async_task_runner.async_task_runner.submit_task", return_value=True):
            response = client.post(
                "/api/v1/data/db/sync/dragon-tiger",
                params={"date": "2024-01-01"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "pending"
        assert "2024-01-01" in payload["task_name"]

    def test_get_task_status(self, client, auth_headers):
        with patch("app.tasks.async_task_runner.async_task_runner.submit_task", return_value=True):
            create_response = client.post("/api/v1/data/db/sync/stock-basic", headers=auth_headers)

        task_id = create_response.json()["task_id"]
        response = client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["task_id"] == task_id

    def test_get_task_list(self, client, auth_headers):
        with patch("app.tasks.async_task_runner.async_task_runner.submit_task", return_value=True):
            create_response = client.post("/api/v1/data/db/sync/stock-basic", headers=auth_headers)

        assert create_response.status_code == 200

        response = client.get(
            "/api/v1/tasks",
            params={"status": "pending", "task_type": "stock_basic_sync", "limit": 10, "skip": 0},
            headers=auth_headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["limit"] == 10
        assert payload["skip"] == 0

    def test_get_task_not_found(self, client, auth_headers):
        response = client.get("/api/v1/tasks/non-existent-task-id", headers=auth_headers)
        assert response.status_code == 404


class TestLLMAPI:
    def test_llm_health_check(self, client, auth_headers):
        response = client.get("/api/v1/llm/health", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_get_llm_models(self, client, auth_headers):
        response = client.get("/api/v1/llm/models", headers=auth_headers)
        assert response.status_code == 200
        payload = response.json()
        assert "current_model" in payload
        assert isinstance(payload["available_models"], list)
        assert payload["available_models"]

    def test_llm_test_call(self, client, auth_headers):
        response = client.post(
            "/api/v1/llm/test",
            json={
                "prompt": "Say OK",
                "temperature": 0.7,
                "max_tokens": 50,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_llm_probe_runs_without_client_input(self, client, auth_headers):
        probe_result = {
            "status": "success",
            "checks": {},
        }

        with patch("app.api.endpoints.llm.run_llm_probe", new=AsyncMock(return_value=probe_result)) as mock_probe:
            response = client.get(
                "/api/v1/llm/probe",
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert response.json() == probe_result
        mock_probe.assert_awaited_once_with()

    def test_llm_probe_rejects_client_input(self, client, auth_headers):
        response = client.post(
            "/api/v1/llm/probe",
            json={"prompt": "unused"},
            headers=auth_headers,
        )

        assert response.status_code == 405
