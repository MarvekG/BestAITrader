from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.ai.llm_engine.runner import run_analysis_task
from app.core.request_context import clear_current_user_id, get_current_user_id
from app.models.session import Session as SessionModel
from app.models.user import User

async def _seed_runner_session(session_factory, *, username: str):
    session_id = uuid4()
    async with session_factory() as db:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash="hashed",
        )
        db.add(user)
        await db.flush()
        user_id = user.id
        db.add(
            SessionModel(
                session_id=session_id,
                user_id=user_id,
                stock_code="000001.SZ",
                trading_frequency="daily",
                trading_strategy="value",
            )
        )
        await db.commit()
    return session_id, user_id


@pytest.mark.asyncio
async def test_run_analysis_task_binds_session_user_context(monkeypatch, test_db) -> None:
    """后台分析任务应把会话所属用户绑定到请求上下文。

    Args:
        monkeypatch: pytest monkeypatch 工具。
        test_db: 测试数据库会话工厂。
    """
    import app.api.endpoints.debate_ws as debate_ws_module
    import app.ai.llm_engine.runner as runner_module

    session_id, user_id = await _seed_runner_session(test_db, username="runner_context_user")
    captured = {}

    class _FakeWorkflow:
        async def ainvoke(self, initial_state):
            """记录工作流执行时可见的用户上下文。

            Args:
                initial_state: 传入工作流的初始状态。

            Returns:
                带空错误列表的工作流状态。
            """
            captured["user_id"] = get_current_user_id()
            return {**initial_state, "errors": []}

    async def _fake_send_debate_status(*_args, **_kwargs):
        """跳过 WebSocket 状态推送。

        Args:
            *_args: 原状态推送调用的位置参数。
            **_kwargs: 原状态推送调用的关键字参数。
        """
        return None

    monkeypatch.setattr(runner_module.database_module, "AsyncSessionLocal", test_db)
    monkeypatch.setattr(runner_module, "_update_task_status", AsyncMock())
    monkeypatch.setattr(runner_module, "_update_session_status", AsyncMock())
    monkeypatch.setattr(runner_module, "create_analyst_workflow", lambda: _FakeWorkflow())
    monkeypatch.setattr(debate_ws_module, "send_debate_status", _fake_send_debate_status)
    clear_current_user_id()

    await run_analysis_task(
        task_id="task-runner-context",
        stock_code="000001.SZ",
        trading_frequency="daily",
        trading_strategy="value",
        session_id=str(session_id),
    )

    assert captured["user_id"] == user_id
    assert get_current_user_id() is None


@pytest.mark.asyncio
async def test_run_analysis_task_passes_trigger_reason_to_initial_state(monkeypatch, test_db) -> None:
    """后台分析任务应把盯盘启动原因传入工作流初始上下文。

    Args:
        monkeypatch: pytest monkeypatch 工具。
        test_db: 测试数据库会话工厂。
    """
    import app.api.endpoints.debate_ws as debate_ws_module
    import app.ai.llm_engine.runner as runner_module

    session_id, _ = await _seed_runner_session(test_db, username="runner_trigger_user")
    captured = {}

    class _FakeWorkflow:
        async def ainvoke(self, initial_state):
            """记录传入工作流的初始状态。

            Args:
                initial_state: 传入工作流的初始状态。

            Returns:
                带空错误列表的工作流状态。
            """
            captured["initial_state"] = initial_state
            return {**initial_state, "errors": []}

    async def _fake_send_debate_status(*_args, **_kwargs):
        """跳过 WebSocket 状态推送。

        Args:
            *_args: 原状态推送调用的位置参数。
            **_kwargs: 原状态推送调用的关键字参数。
        """
        return None

    monkeypatch.setattr(runner_module.database_module, "AsyncSessionLocal", test_db)
    monkeypatch.setattr(runner_module, "_update_task_status", AsyncMock())
    monkeypatch.setattr(runner_module, "_update_session_status", AsyncMock())
    monkeypatch.setattr(runner_module, "create_analyst_workflow", lambda: _FakeWorkflow())
    monkeypatch.setattr(debate_ws_module, "send_debate_status", _fake_send_debate_status)

    await run_analysis_task(
        task_id="task-runner-trigger",
        stock_code="000001.SZ",
        trading_frequency="daily",
        trading_strategy="value",
        session_id=str(session_id),
        trigger_reason="Strong anomaly and news context",
        evidence_summary="Quote anomaly and news are aligned.",
    )

    assert captured["initial_state"]["static_context"]["market_watch_trigger"] == {
        "source": "market_watch",
        "trigger_reason": "Strong anomaly and news context",
        "evidence_summary": "Quote anomaly and news are aligned.",
    }


@pytest.mark.asyncio
async def test_run_analysis_task_passes_discipline_trigger_to_initial_state(monkeypatch, test_db) -> None:
    """后台分析任务应把持仓纪律触发信息传入工作流初始上下文。

    Args:
        monkeypatch: pytest monkeypatch 工具。
        test_db: 测试数据库会话工厂。
    """
    import app.api.endpoints.debate_ws as debate_ws_module
    import app.ai.llm_engine.runner as runner_module

    session_id, _ = await _seed_runner_session(test_db, username="runner_discipline_user")
    captured = {}

    class _FakeWorkflow:
        async def ainvoke(self, initial_state):
            """记录传入工作流的初始状态。

            Args:
                initial_state: 传入工作流的初始状态。

            Returns:
                带空错误列表的工作流状态。
            """
            captured["initial_state"] = initial_state
            return {**initial_state, "errors": []}

    async def _fake_send_debate_status(*_args, **_kwargs):
        """跳过 WebSocket 状态推送。

        Args:
            *_args: 原状态推送调用的位置参数。
            **_kwargs: 原状态推送调用的关键字参数。
        """
        return None

    discipline_trigger = {
        "trigger_type": "stop_loss",
        "threshold": "9.5000",
        "latest_price": "9.40",
        "source_pm_session_id": str(uuid4()),
        "source": "position_discipline",
    }
    monkeypatch.setattr(runner_module.database_module, "AsyncSessionLocal", test_db)
    monkeypatch.setattr(runner_module, "_update_task_status", AsyncMock())
    monkeypatch.setattr(runner_module, "_update_session_status", AsyncMock())
    monkeypatch.setattr(runner_module, "create_analyst_workflow", lambda: _FakeWorkflow())
    monkeypatch.setattr(debate_ws_module, "send_debate_status", _fake_send_debate_status)

    await run_analysis_task(
        task_id="task-runner-discipline-trigger",
        stock_code="000001.SZ",
        trading_frequency="daily",
        trading_strategy="value",
        session_id=str(session_id),
        discipline_trigger=discipline_trigger,
    )

    assert captured["initial_state"]["static_context"]["discipline_trigger"] == discipline_trigger
