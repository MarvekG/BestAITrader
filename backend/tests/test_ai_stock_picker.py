from datetime import date, datetime
import json
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy import CheckConstraint, Index

from app.crud.user import create_user
from app.models.data_storage import KlineData, StockBasic, StockRealtimeMarket, StockValuationHistory
from app.models.stock_indicators import StockIndicators
from app.models.stock_warehouse import StockWarehouse
from app.models.user import User
from app.schemas.user import UserCreate
from app.ai.llm_providers.litellm import LiteLLMProvider
from app.ai.stock_picker.models import (
    StockSelectionCandidate,
    StockSelectionEvent,
    StockSelectionRun,
)


class _FakeLLMProvider(LiteLLMProvider):
    def __init__(self, llm):
        self.llm = llm
        self.calls = []

    def build_chat_model(self, **kwargs):
        self.calls.append(kwargs)
        return self.llm


def _patch_stock_picker_llm_provider(monkeypatch, stock_picker_service_module, llm):
    provider = _FakeLLMProvider(llm)
    monkeypatch.setattr(stock_picker_service_module, "get_llm_provider", lambda: provider)
    return provider


def _create_authenticated_user(client, db_session, *, username: str):
    password = "password123"
    user = create_user(
        db_session,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password=password,
        ),
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    return user, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_candidate(db_session, stock_code: str, name: str, change_60: float, pe: float):
    trade_date = date(2026, 3, 28)
    db_session.add(
        StockBasic(
            stock_code=stock_code,
            name=name,
            industry="半导体",
            market="科创板",
            list_date=date(2020, 1, 1),
            data_source="test",
        )
    )
    db_session.flush()
    db_session.add(
        StockRealtimeMarket(
            stock_code=stock_code,
            current_price=50.0,
            change_percent=2.1,
            change_60days=change_60,
            turnover_rate=3.5,
            volume_ratio=1.8,
            pe_dynamic=pe,
            pb_ratio=2.2,
            total_market_cap=120000000000.0,
            main_net_inflow_5d=300000000.0,
            timestamp=datetime(2026, 3, 29, 10, 0, 0),
            data_source="test",
        )
    )
    db_session.add(
        KlineData(
            stock_code=stock_code,
            date=trade_date,
            open=48.0,
            close=50.0,
            high=51.0,
            low=47.5,
            volume=30000000.0 + change_60 * 100000,
            turnover=800000000.0 + change_60 * 5000000,
            change=1.0,
            change_percent=2.1,
            freq="D",
            data_source="test",
        )
    )
    db_session.add(
        StockValuationHistory(
            stock_code=stock_code,
            data_date=trade_date,
            total_market_value=120000000000,
            pe_ttm=pe,
            pb=2.2,
            ps_ttm=4.5,
            dividend_yield=1.2,
            data_source="test",
        )
    )
    db_session.add(
        StockIndicators(
            stock_code=stock_code,
            trade_date=trade_date,
            ma20=48.5,
            ma60=47.0,
            macd=0.5 + change_60 / 20,
            macd_signal=0.2,
            macd_hist=0.3 + change_60 / 20,
            kdj_k=55.0 + change_60 / 5,
            kdj_d=52.0 + change_60 / 5,
            kdj_j=60.0 + change_60 / 4,
            rsi_6=58.0 + change_60 / 6,
            rsi_12=55.0 + change_60 / 5,
            rsi_24=52.0 + change_60 / 8,
            cci=100.0,
            wr_14=-20.0,
            boll_upper=52.0,
            boll_mid=50.0,
            boll_lower=48.0,
            atr=1.5,
            obv=1000000.0,
            data_source="test",
        )
    )


def _seed_candidate_batch(db_session, specs):
    for stock_code, name, change_60, pe in specs:
        _seed_candidate(db_session, stock_code, name, change_60, pe)


def _seed_rank_candidate_with_metrics(
    db_session,
    *,
    stock_code: str,
    name: str,
    industry: str = "半导体",
    market: str = "科创板",
    change_60: float = 0.0,
    pe: float = 20.0,
    pb: float = 2.0,
    turnover: float = 1.0,
    volume_ratio: float = 1.0,
    market_cap: float = 10000000000.0,
    inflow_5d: float = 0.0,
    ps_ttm: float = 5.0,
    dividend_yield: float = 0.0,
    rt_timestamp: datetime | None = None,
    val_date: date | None = None,
    kline_date: date | None = None,
    close: float = 50.0,
    kline_volume: float = 30000000.0,
    kline_turnover: float = 800000000.0,
    macd: float = 0.5,
    macd_signal: float = 0.2,
    rsi_12: float = 58.0,
    rsi_24: float = 52.0,
    kdj_j: float = 65.0,
    atr: float = 1.5,
):
    if kline_date is None and val_date is not None:
        kline_date = val_date

    db_session.add(
        StockBasic(
            stock_code=stock_code,
            name=name,
            industry=industry,
            market=market,
            list_date=date(2020, 1, 1),
            data_source="test",
        )
    )
    db_session.flush()
    if rt_timestamp is not None:
        db_session.add(
            StockRealtimeMarket(
                stock_code=stock_code,
                current_price=50.0,
                change_percent=2.1,
                change_60days=change_60,
                turnover_rate=turnover,
                volume_ratio=volume_ratio,
                pe_dynamic=pe,
                pb_ratio=pb,
                total_market_cap=market_cap,
                main_net_inflow_5d=inflow_5d,
                timestamp=rt_timestamp,
                data_source="test",
            )
        )
    if val_date is not None:
        db_session.add(
            StockValuationHistory(
                stock_code=stock_code,
                data_date=val_date,
                total_market_value=market_cap,
                pe_ttm=pe,
                pb=pb,
                ps_ttm=ps_ttm,
                dividend_yield=dividend_yield,
                data_source="test",
            )
        )
    if kline_date is not None:
        db_session.add(
            KlineData(
                stock_code=stock_code,
                date=kline_date,
                open=close * 0.98,
                close=close,
                high=close * 1.02,
                low=close * 0.96,
                volume=kline_volume,
                turnover=kline_turnover,
                change=1.0,
                change_percent=2.0,
                freq="D",
                data_source="test",
            )
        )
        db_session.add(
            StockIndicators(
                stock_code=stock_code,
                trade_date=kline_date,
                ma20=close * 0.98,
                ma60=close * 0.95,
                macd=macd,
                macd_signal=macd_signal,
                macd_hist=macd - macd_signal,
                kdj_k=55.0,
                kdj_d=52.0,
                kdj_j=kdj_j,
                rsi_6=min(99.0, rsi_12 + 4),
                rsi_12=rsi_12,
                rsi_24=rsi_24,
                cci=100.0,
                wr_14=-20.0,
                boll_upper=close * 1.05,
                boll_mid=close,
                boll_lower=close * 0.95,
                atr=atr,
                obv=1000000.0,
                data_source="test",
            )
        )


def _get_stock_picker_service():
    from app.ai.stock_picker.service import stock_picker_service

    return stock_picker_service


def _make_ranked_candidate(
    stock_code: str,
    *,
    stock_name: str = "测试股票",
    factor_score: float = 50.0,
    ai_score: float = 0.0,
    final_score: float | None = None,
    decision: str = "watch",
):
    from app.ai.stock_picker.service import RankedCandidate

    resolved_final_score = factor_score if final_score is None else final_score
    return RankedCandidate(
        stock_code=stock_code,
        stock_name=stock_name,
        industry="测试行业",
        market="主板",
        factor_score=factor_score,
        ai_score=ai_score,
        final_score=resolved_final_score,
        decision=decision,
        research_payload={
            "quant_support": {
                "style_fit_score": 20.0,
                "liquidity_score": 10.0,
                "risk_penalty": 0.0,
                "final_quant_score": factor_score,
            },
            "quant_summary": {
                "thesis": f"{stock_name} 量化辅助结论",
                "catalysts": ["催化A"],
                "risks": ["风险A"],
            },
            "thesis": f"{stock_name} 研究结论",
            "risks": ["风险A"],
            "holding_horizon": "mid_term",
        },
    )


def _mock_stock_picker_llm(monkeypatch, *, cash_ratio: float = 12.0):
    from app.ai.stock_picker.service import stock_picker_service

    research_payload = {
        "research": [
            {
                "stock_code": "688023.SH",
                "ai_score": 88,
                "thesis": "景气度和资金面更强。",
                "profit_logic": "景气趋势和资金面支持赚钱概率。",
                "catalysts": ["订单改善"],
                "trend_evidence": ["趋势质量改善"],
                "risk_evidence": ["波动较大"],
                "risks": ["波动较大"],
                "invalidation_conditions": ["趋势转弱"],
                "style_fit_explanation": "更适合成长风格。",
                "holding_horizon": "mid_term",
                "decision": "keep",
            },
            {
                "stock_code": "688022.SH",
                "ai_score": 82,
                "thesis": "估值和动量平衡。",
                "catalysts": ["利润率修复"],
                "risks": ["兑现节奏不确定"],
                "style_fit_explanation": "适合平衡风格。",
                "holding_horizon": "mid_term",
                "decision": "keep",
            },
            {
                "stock_code": "688021.SH",
                "ai_score": 79,
                "thesis": "防御属性更强。",
                "catalysts": ["低估值"],
                "risks": ["弹性偏弱"],
                "style_fit_explanation": "适合作为稳定仓位。",
                "holding_horizon": "mid_long_term",
                "decision": "keep",
            },
            {
                "stock_code": "688025.SH",
                "ai_score": 77,
                "thesis": "流动性较好。",
                "catalysts": ["交易活跃"],
                "risks": ["业绩波动"],
                "style_fit_explanation": "适合作为补位。",
                "holding_horizon": "mid_term",
                "decision": "watch",
            },
            {
                "stock_code": "688001.SH",
                "ai_score": 76,
                "thesis": "产业链位置良好。",
                "catalysts": ["订单修复"],
                "risks": ["竞争压力"],
                "style_fit_explanation": "适合作为补位。",
                "holding_horizon": "mid_term",
                "decision": "keep",
            },
            {
                "stock_code": "688002.SH",
                "ai_score": 74,
                "thesis": "基本面稳健。",
                "catalysts": ["需求恢复"],
                "risks": ["估值偏高"],
                "style_fit_explanation": "具备持仓价值。",
                "holding_horizon": "mid_term",
                "decision": "keep",
            },
            {
                "stock_code": "688003.SH",
                "ai_score": 71,
                "thesis": "弹性一般。",
                "catalysts": ["行业回暖"],
                "risks": ["景气波动"],
                "style_fit_explanation": "可继续观察。",
                "holding_horizon": "mid_term",
                "decision": "watch",
            },
            {
                "stock_code": "688004.SH",
                "ai_score": 69,
                "thesis": "关注后续兑现。",
                "catalysts": ["新品放量"],
                "risks": ["兑现不确定"],
                "style_fit_explanation": "适合作为观察标的。",
                "holding_horizon": "mid_term",
                "decision": "watch",
            },
        ]
    }

    monkeypatch.setattr(stock_picker_service, "_request_llm_research", AsyncMock(return_value=research_payload))


def _mock_stock_picker_invalid_research_llm(monkeypatch):
    from app.ai.stock_picker.service import stock_picker_service

    monkeypatch.setattr(
        stock_picker_service,
        "_request_llm_research",
        AsyncMock(return_value={"unexpected": []}),
    )


class TestAIStockPickerAPI:
    def test_stock_picker_model_constraints_declared(self):
        run_constraint_names = {
            constraint.name
            for constraint in StockSelectionRun.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        candidate_constraint_names = {
            constraint.name
            for constraint in StockSelectionCandidate.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        event_index_names = {
            index.name
            for index in StockSelectionEvent.__table__.indexes
            if isinstance(index, Index)
        }

        assert "ck_stock_selection_runs_scope" in run_constraint_names
        assert "ck_stock_selection_runs_style" in run_constraint_names
        assert "ck_stock_selection_runs_risk_level" in run_constraint_names
        assert "ck_stock_selection_runs_recommendation_count" in run_constraint_names
        assert "ck_stock_selection_candidates_decision" in candidate_constraint_names
        assert "ck_stock_selection_candidates_factor_score" in candidate_constraint_names
        assert "ck_stock_selection_candidates_ai_score" in candidate_constraint_names
        assert "ck_stock_selection_candidates_final_score" in candidate_constraint_names
        assert "ix_stock_selection_events_run_created_at" in event_index_names
        assert "ix_stock_selection_events_run_stage_created_at" in event_index_names

    def test_stock_picker_evidence_tool_names_exclude_loader_only_tools(self):
        from app.ai.stock_picker.service import _build_stock_research_evidence_tool_names

        class _Tool:
            def __init__(self, name):
                self.name = name

        evidence_tool_names = _build_stock_research_evidence_tool_names([_Tool("query_stock_data")])

        assert "query_stock_data" in evidence_tool_names
        assert "run_skill_script" in evidence_tool_names
        assert "list_skills" not in evidence_tool_names
        assert "load_skill" not in evidence_tool_names
        assert "read_skill_file" not in evidence_tool_names

    def test_list_industries_returns_sorted_distinct_values(self, client, auth_headers, db_session):
        db_session.add_all(
            [
                StockBasic(
                    stock_code="600101.SH",
                    name="样本A",
                    industry="银行",
                    market="主板",
                    list_date=date(2020, 1, 1),
                    data_source="test",
                ),
                StockBasic(
                    stock_code="600102.SH",
                    name="样本B",
                    industry="电力",
                    market="主板",
                    list_date=date(2020, 1, 1),
                    data_source="test",
                ),
                StockBasic(
                    stock_code="600103.SH",
                    name="样本C",
                    industry="银行",
                    market="主板",
                    list_date=date(2020, 1, 1),
                    data_source="test",
                ),
            ]
        )
        db_session.commit()

        response = client.get("/api/v1/ai-stock-picker/industries", headers=auth_headers)

        assert response.status_code == 200
        assert response.json() == ["电力", "银行"]

    def test_create_run_and_fetch_result(self, client, auth_headers, db_session, monkeypatch):
        _mock_stock_picker_llm(monkeypatch, cash_ratio=12.0)
        _seed_candidate(db_session, "688001.SH", "华兴源创", 10.0, 18.0)
        _seed_candidate(db_session, "688002.SH", "睿创微纳", 15.0, 22.0)
        _seed_candidate(db_session, "688003.SH", "天准科技", 8.0, 20.0)
        _seed_candidate(db_session, "688004.SH", "博汇科技", 12.0, 16.0)
        _seed_candidate(db_session, "688021.SH", "奥福环保", 9.0, 15.0)
        _seed_candidate(db_session, "688022.SH", "瀚川智能", 11.0, 18.0)
        _seed_candidate(db_session, "688023.SH", "安恒信息", 14.0, 21.0)
        _seed_candidate(db_session, "688025.SH", "杰普特", 8.0, 17.0)
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        run_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}", headers=auth_headers)
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "completed"

        candidates_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}/candidates", headers=auth_headers)
        assert candidates_response.status_code == 200
        candidates_payload = candidates_response.json()
        assert len(candidates_payload) == 4
        assert candidates_payload[0]["stock_name"] is not None
        assert candidates_payload[0]["quant_support"]["final_quant_score"] == candidates_payload[0]["factor_score"]
        assert candidates_payload[0]["quant_support"]["style_fit_score"] >= 0
        assert run_response.json()["factor_candidate_limit"] == 20
        assert run_response.json()["research_candidate_limit"] >= 4
        assert run_response.json()["same_industry_limit"] == 4

        result_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}/result", headers=auth_headers)
        assert result_response.status_code == 200
        payload = result_response.json()
        assert payload["run"]["status"] == "completed"
        assert len(payload["recommendations"]["stocks"]) == 4
        assert payload["summary"]["research_mode"] == "llm"
        assert payload["summary"]["decision_breakdown"]["keep"] >= 2
        assert payload["summary"]["selection_logic"] == "LLM 研究结果已生成推荐股票列表"
        assert payload["summary"]["top_candidates"][0]["ai_score"] >= 70.0
        assert payload["summary"]["top_candidates"][0]["quant_support"]["final_quant_score"] >= 0

    def test_create_run_with_allowed_industries_and_limits(self, client, auth_headers, db_session, monkeypatch):
        _mock_stock_picker_llm(monkeypatch, cash_ratio=12.0)
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="688021.SH",
            name="奥福环保",
            industry="火力发电",
            market="主板",
            pe=11.0,
            pb=1.4,
            dividend_yield=3.0,
            val_date=date(2026, 3, 29),
            kline_date=date(2026, 3, 29),
        )
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="688022.SH",
            name="瀚川智能",
            industry="火力发电",
            market="主板",
            pe=12.0,
            pb=1.5,
            dividend_yield=2.8,
            val_date=date(2026, 3, 29),
            kline_date=date(2026, 3, 29),
        )
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="688023.SH",
            name="安恒信息",
            industry="火力发电",
            market="主板",
            pe=13.0,
            pb=1.6,
            dividend_yield=2.5,
            val_date=date(2026, 3, 29),
            kline_date=date(2026, 3, 29),
        )
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="688025.SH",
            name="杰普特",
            industry="火力发电",
            market="主板",
            pe=14.0,
            pb=1.7,
            dividend_yield=2.3,
            val_date=date(2026, 3, 29),
            kline_date=date(2026, 3, 29),
        )
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="600202.SH",
            name="银行样本",
            industry="银行",
            market="主板",
            pe=7.0,
            pb=0.9,
            dividend_yield=4.0,
            val_date=date(2026, 3, 29),
            kline_date=date(2026, 3, 29),
        )
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "factor_candidate_limit": 4,
                "research_candidate_limit": 4,
                "same_industry_limit": 4,
                "allowed_industries": ["火力发电"],
            },
            headers=auth_headers,
        )

        assert response.status_code == 201
        run_id = response.json()["run_id"]
        run_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}", headers=auth_headers)
        assert run_response.status_code == 200
        assert run_response.json()["allowed_industries"] == ["火力发电"]
        assert run_response.json()["factor_candidate_limit"] == 4
        assert run_response.json()["research_candidate_limit"] == 4
        candidates_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}/candidates", headers=auth_headers)
        assert candidates_response.status_code == 200
        assert all(item["industry"] == "火力发电" for item in candidates_response.json())

    def test_create_run_rejects_invalid_allowed_industry(self, client, auth_headers, db_session):
        db_session.add(
            StockBasic(
                stock_code="600301.SH",
                name="样本A",
                industry="银行",
                market="主板",
                list_date=date(2020, 1, 1),
                data_source="test",
            )
        )
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "allowed_industries": ["不存在行业"],
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "无效行业" in response.json()["detail"]

    def test_create_run_rejects_parallel_active_run(self, client, auth_headers, db_session):
        existing_run = StockSelectionRun(
            user_id=1,
            scope="all",
            style="balanced",
            risk_level="medium",
            recommendation_count=4,
            status="running",
            current_stage="factor_ranked",
            request_payload={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "factor_candidate_limit": 20,
                "research_candidate_limit": 8,
                "same_industry_limit": 3,
                "allowed_industries": [],
            },
        )
        db_session.add(existing_run)
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "core",
                "style": "growth",
                "recommendation_count": 5,
                "risk_level": "high",
            },
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert str(existing_run.run_id) in response.json()["detail"]

    def test_delete_run(self, client, auth_headers, db_session, monkeypatch):
        _mock_stock_picker_invalid_research_llm(monkeypatch)
        _seed_candidate(db_session, "688011.SH", "新光光电", 6.0, 24.0)
        _seed_candidate(db_session, "688012.SH", "中微公司", 9.0, 28.0)
        _seed_candidate(db_session, "688013.SH", "天臣医疗", 7.0, 19.0)
        _seed_candidate(db_session, "688015.SH", "交控科技", 11.0, 17.0)
        db_session.commit()

        create_response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "value",
                "recommendation_count": 4,
                "risk_level": "low",
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )
        run_id = create_response.json()["run_id"]
        run_uuid = UUID(run_id)

        delete_response = client.delete(f"/api/v1/ai-stock-picker/runs/{run_id}", headers=auth_headers)
        assert delete_response.status_code == 200
        assert db_session.query(StockSelectionEvent).filter(StockSelectionEvent.run_id == run_uuid).count() == 0
        assert db_session.query(StockSelectionCandidate).filter(StockSelectionCandidate.run_id == run_uuid).count() == 0

        fetch_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}", headers=auth_headers)
        assert fetch_response.status_code == 404

    def test_run_resources_are_isolated_by_user(self, client, db_session):
        owner, owner_headers = _create_authenticated_user(client, db_session, username="picker_owner")
        _other, other_headers = _create_authenticated_user(client, db_session, username="picker_other")
        run = StockSelectionRun(
            user_id=owner.id,
            scope="all",
            style="balanced",
            risk_level="medium",
            recommendation_count=4,
            status="completed",
            current_stage="completed",
            request_payload={},
            summary_payload={"recommended_stock_codes": ["600519.SH"]},
        )
        db_session.add(run)
        db_session.flush()
        db_session.add_all(
            [
                StockSelectionEvent(
                    run_id=run.run_id,
                    stage="created",
                    event_type="run_created",
                    message="created",
                ),
                StockSelectionCandidate(
                    run_id=run.run_id,
                    stock_code="600519.SH",
                    source_scope="all",
                    style="balanced",
                    factor_score=80,
                    ai_score=75,
                    final_score=78,
                    decision="watch",
                ),
            ]
        )
        db_session.commit()

        owner_response = client.get(f"/api/v1/ai-stock-picker/runs/{run.run_id}", headers=owner_headers)
        other_detail_response = client.get(f"/api/v1/ai-stock-picker/runs/{run.run_id}", headers=other_headers)
        other_events_response = client.get(f"/api/v1/ai-stock-picker/runs/{run.run_id}/events", headers=other_headers)
        other_candidates_response = client.get(
            f"/api/v1/ai-stock-picker/runs/{run.run_id}/candidates",
            headers=other_headers,
        )
        other_result_response = client.get(f"/api/v1/ai-stock-picker/runs/{run.run_id}/result", headers=other_headers)
        other_delete_response = client.delete(f"/api/v1/ai-stock-picker/runs/{run.run_id}", headers=other_headers)

        assert owner_response.status_code == 200
        assert other_detail_response.status_code == 404
        assert other_events_response.status_code == 404
        assert other_candidates_response.status_code == 404
        assert other_result_response.status_code == 404
        assert other_delete_response.status_code == 404

    def test_structured_llm_path(self, client, auth_headers, db_session, monkeypatch):
        _mock_stock_picker_llm(monkeypatch, cash_ratio=12.0)
        _seed_candidate(db_session, "688021.SH", "奥福环保", 9.0, 15.0)
        _seed_candidate(db_session, "688022.SH", "瀚川智能", 11.0, 18.0)
        _seed_candidate(db_session, "688023.SH", "安恒信息", 14.0, 21.0)
        _seed_candidate(db_session, "688025.SH", "杰普特", 8.0, 17.0)
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        result_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}/result", headers=auth_headers)
        assert result_response.status_code == 200
        payload = result_response.json()
        assert payload["summary"]["selection_logic"] == "LLM 研究结果已生成推荐股票列表"
        assert payload["recommendations"]["stocks"][0]["recommendation_reason"] == "景气趋势和资金面支持赚钱概率。"
        assert payload["recommendations"]["stocks"][0]["trend_evidence"] == ["趋势质量改善"]
        assert payload["recommendations"]["stocks"][0]["invalidation_conditions"] == ["趋势转弱"]
        assert payload["summary"]["research_mode"] == "llm"
        assert payload["summary"]["top_candidates"][0]["decision"] in {"keep", "watch"}

    def test_invalid_llm_payload_marks_run_failed(self, client, auth_headers, db_session, monkeypatch):
        _mock_stock_picker_invalid_research_llm(monkeypatch)
        _seed_candidate(db_session, "688001.SH", "华兴源创", 10.0, 18.0)
        _seed_candidate(db_session, "688002.SH", "睿创微纳", 15.0, 22.0)
        _seed_candidate(db_session, "688003.SH", "天准科技", 8.0, 20.0)
        _seed_candidate(db_session, "688004.SH", "博汇科技", 12.0, 16.0)
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        run_id = response.json()["run_id"]

        run_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}", headers=auth_headers)
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "failed_ai_research"
        assert "缺少 research 列表" in run_response.json()["error_message"]

class TestAIStockPickerStages:
    @pytest.mark.asyncio
    async def test_workflow_mocked_happy_path_calls_each_stage_in_order(self, db_session, monkeypatch):
        from app.ai.stock_picker.service import run_stock_picker_workflow, stock_picker_service

        monkeypatch.setattr("app.ai.stock_picker.service.ws_manager.send_stock_picker_update", AsyncMock())

        run = stock_picker_service.create_run(
            db_session,
            user_id=1,
            request_data={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
            },
        )

        universe = [
            StockBasic(
                stock_code="600001.SH",
                name="阶段样本A",
                industry="银行",
                market="主板",
                list_date=date(2020, 1, 1),
                status="L",
                data_source="test",
            )
        ]
        ranked = [_make_ranked_candidate("600001.SH", stock_name="阶段样本A", factor_score=61.0)]
        researched = [_make_ranked_candidate("600001.SH", stock_name="阶段样本A", factor_score=61.0, ai_score=82.0, final_score=76.75, decision="keep")]
        recommendations = [
            {
                "stock_code": "600001.SH",
                "rank": 1,
                "conviction_score": 76.75,
                "recommendation_reason": "阶段样本A 研究结论",
                "risk_flags": ["风险A"],
                "holding_horizon": "mid_term",
                "decision": "keep",
            }
        ]
        recommendation_summary = {
            "source_scope": "all",
            "style_label": "中线平衡",
            "candidate_count": 1,
            "selected_count": 1,
            "selection_logic": "mock recommendation logic",
            "recommended_stock_codes": ["600001.SH"],
        }
        summary_metrics = {
            "research_mode": "llm",
            "decision_breakdown": {"keep": 1, "watch": 0, "drop": 0},
            "top_candidates": [{"stock_code": "600001.SH", "final_score": 76.75}],
            "recommended_stock_codes": ["600001.SH"],
        }

        stage_calls: list[str] = []

        def _mock_build_universe(db, user_id, scope, allowed_industries=None):
            stage_calls.append("universe")
            assert user_id == 1
            assert scope == "all"
            assert allowed_industries == []
            return universe

        def _mock_rank_candidates(db, incoming_universe, style, scope, factor_candidate_limit=None, same_industry_limit=None):
            stage_calls.append("factor")
            assert incoming_universe == universe
            assert style == "balanced"
            assert scope == "all"
            assert factor_candidate_limit == 20
            assert same_industry_limit == 3
            return ranked

        async def _mock_research_candidates(incoming_ranked, style, recommendation_count):
            stage_calls.append("research")
            assert incoming_ranked == ranked
            assert style == "balanced"
            assert recommendation_count == 4
            return researched, "llm"

        def _mock_build_recommendations(incoming_researched, incoming_run):
            stage_calls.append("recommendation")
            assert incoming_researched == researched
            assert incoming_run.run_id == run.run_id
            return recommendations, recommendation_summary

        def _mock_build_summary_metrics(
            incoming_researched,
            incoming_recommendations,
            research_mode,
            run_config,
            universe_count,
            factor_candidate_count,
        ):
            stage_calls.append("summary")
            assert incoming_researched == researched
            assert incoming_recommendations == recommendations
            assert research_mode == "llm"
            assert run_config["factor_candidate_limit"] == 20
            assert universe_count == 1
            assert factor_candidate_count == 1
            return summary_metrics

        replace_candidates = Mock()
        monkeypatch.setattr(stock_picker_service, "_build_universe", _mock_build_universe)
        monkeypatch.setattr(stock_picker_service, "_rank_candidates", _mock_rank_candidates)
        monkeypatch.setattr(stock_picker_service, "_research_candidates", _mock_research_candidates)
        monkeypatch.setattr(stock_picker_service, "_build_recommendations", _mock_build_recommendations)
        monkeypatch.setattr(stock_picker_service, "_build_summary_metrics", _mock_build_summary_metrics)
        monkeypatch.setattr(stock_picker_service, "_replace_candidates", replace_candidates)

        await run_stock_picker_workflow(stock_picker_service, db_session, run)
        db_session.refresh(run)

        assert stage_calls == ["universe", "factor", "research", "recommendation", "summary"]
        assert replace_candidates.call_count == 2
        assert run.status == "completed"
        assert run.current_stage == "completed"
        assert run.summary_payload["selection_logic"] == "mock recommendation logic"
        assert run.summary_payload["decision_breakdown"]["keep"] == 1
        assert run.summary_payload["recommended_stock_codes"] == ["600001.SH"]

        events = (
            db_session.query(StockSelectionEvent)
            .filter(StockSelectionEvent.run_id == run.run_id)
            .order_by(StockSelectionEvent.id.asc())
            .all()
        )
        assert [event.stage for event in events] == [
            "created",
            "universe_built",
            "factor_ranked",
            "ai_researched",
            "recommendations_built",
            "completed",
        ]
        assert [event.event_type for event in events] == [
            "run_created",
            "universe_ready",
            "factor_ranked",
            "ai_researched",
            "recommendations_ready",
            "completed",
        ]

    @pytest.mark.asyncio
    async def test_workflow_mocked_universe_stage_failure_marks_failed_universe(self, db_session, monkeypatch):
        from app.ai.stock_picker.service import run_stock_picker_workflow, stock_picker_service

        monkeypatch.setattr("app.ai.stock_picker.service.ws_manager.send_stock_picker_update", AsyncMock())
        run = stock_picker_service.create_run(
            db_session,
            user_id=1,
            request_data={
                "scope": "warehouse",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
            },
        )
        rank_mock = Mock()
        research_mock = AsyncMock()
        recommendation_mock = Mock()
        monkeypatch.setattr(stock_picker_service, "_build_universe", Mock(return_value=[]))
        monkeypatch.setattr(stock_picker_service, "_rank_candidates", rank_mock)
        monkeypatch.setattr(stock_picker_service, "_research_candidates", research_mock)
        monkeypatch.setattr(stock_picker_service, "_build_recommendations", recommendation_mock)

        with pytest.raises(ValueError, match="当前来源下没有可分析股票"):
            await run_stock_picker_workflow(stock_picker_service, db_session, run)

        db_session.refresh(run)
        assert run.status == "failed_universe"
        assert run.current_stage == "failed_universe"
        rank_mock.assert_not_called()
        research_mock.assert_not_called()
        recommendation_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_workflow_graph_build_failure_marks_run_failed(self, db_session, monkeypatch):
        import app.ai.stock_picker.service as stock_picker_service_module
        from app.ai.stock_picker.service import run_stock_picker_workflow, stock_picker_service

        monkeypatch.setattr("app.ai.stock_picker.service.ws_manager.send_stock_picker_update", AsyncMock())
        run = stock_picker_service.create_run(
            db_session,
            user_id=1,
            request_data={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
            },
        )
        monkeypatch.setattr(
            stock_picker_service_module,
            "create_stock_picker_workflow",
            Mock(side_effect=ValueError("图构建失败")),
        )

        with pytest.raises(ValueError, match="图构建失败"):
            await run_stock_picker_workflow(stock_picker_service, db_session, run)

        db_session.refresh(run)
        assert run.status == "failed_universe"
        assert run.current_stage == "failed_universe"
        assert run.error_message == "图构建失败"

    @pytest.mark.asyncio
    async def test_workflow_mocked_factor_stage_failure_marks_failed_factor(self, db_session, monkeypatch):
        from app.ai.stock_picker.service import run_stock_picker_workflow, stock_picker_service

        monkeypatch.setattr("app.ai.stock_picker.service.ws_manager.send_stock_picker_update", AsyncMock())
        run = stock_picker_service.create_run(
            db_session,
            user_id=1,
            request_data={
                "scope": "all",
                "style": "growth",
                "recommendation_count": 4,
                "risk_level": "medium",
            },
        )
        universe = [
            StockBasic(
                stock_code="600010.SH",
                name="阶段因子失败样本",
                industry="银行",
                market="主板",
                list_date=date(2020, 1, 1),
                status="L",
                data_source="test",
            )
        ]
        monkeypatch.setattr(stock_picker_service, "_build_universe", Mock(return_value=universe))
        research_mock = AsyncMock()
        recommendation_mock = Mock()
        monkeypatch.setattr(stock_picker_service, "_rank_candidates", Mock(side_effect=ValueError("因子阶段 mock 失败")))
        monkeypatch.setattr(stock_picker_service, "_research_candidates", research_mock)
        monkeypatch.setattr(stock_picker_service, "_build_recommendations", recommendation_mock)

        with pytest.raises(ValueError, match="因子阶段 mock 失败"):
            await run_stock_picker_workflow(stock_picker_service, db_session, run)

        db_session.refresh(run)
        assert run.status == "failed_factor"
        assert run.current_stage == "failed_factor"
        research_mock.assert_not_called()
        recommendation_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_workflow_mocked_research_stage_failure_marks_failed_ai_research(self, db_session, monkeypatch):
        from app.ai.stock_picker.service import run_stock_picker_workflow, stock_picker_service

        monkeypatch.setattr("app.ai.stock_picker.service.ws_manager.send_stock_picker_update", AsyncMock())
        run = stock_picker_service.create_run(
            db_session,
            user_id=1,
            request_data={
                "scope": "all",
                "style": "value",
                "recommendation_count": 4,
                "risk_level": "medium",
            },
        )
        universe = [
            StockBasic(
                stock_code="600011.SH",
                name="阶段研究失败样本",
                industry="银行",
                market="主板",
                list_date=date(2020, 1, 1),
                status="L",
                data_source="test",
            )
        ]
        ranked = [_make_ranked_candidate("600011.SH", stock_name="阶段研究失败样本", factor_score=66.0)]
        monkeypatch.setattr(stock_picker_service, "_build_universe", Mock(return_value=universe))
        monkeypatch.setattr(stock_picker_service, "_rank_candidates", Mock(return_value=ranked))
        monkeypatch.setattr(stock_picker_service, "_replace_candidates", Mock())
        recommendation_mock = Mock()
        monkeypatch.setattr(
            stock_picker_service,
            "_research_candidates",
            AsyncMock(side_effect=ValueError("研究阶段 mock 失败")),
        )
        monkeypatch.setattr(stock_picker_service, "_build_recommendations", recommendation_mock)

        with pytest.raises(ValueError, match="研究阶段 mock 失败"):
            await run_stock_picker_workflow(stock_picker_service, db_session, run)

        db_session.refresh(run)
        assert run.status == "failed_ai_research"
        assert run.current_stage == "failed_ai_research"
        recommendation_mock.assert_not_called()

    def test_universe_stage_builds_scope_specific_universe(self, db_session, monkeypatch):
        stock_picker_service = _get_stock_picker_service()
        user_id = 7
        db_session.add(
            User(
                id=user_id,
                username="stage_user",
                email="stage_user@example.com",
                password_hash="test_hash",
            )
        )
        db_session.commit()
        _seed_candidate_batch(
            db_session,
            [
                ("600001.SH", "测试主板一", 10.0, 16.0),
                ("600002.SH", "测试主板二", 12.0, 18.0),
                ("688001.SH", "测试科创一", 15.0, 20.0),
                ("688002.SH", "测试科创二", 8.0, 22.0),
            ],
        )
        db_session.add(
            StockBasic(
                stock_code="600099.SH",
                name="已退市样本",
                industry="综合",
                market="主板",
                list_date=date(2020, 1, 1),
                status="D",
                data_source="test",
            )
        )
        db_session.add_all(
            [
                StockWarehouse(stock_code="600001.SH", user_id=user_id, is_active=True),
                StockWarehouse(stock_code="688001.SH", user_id=user_id, is_active=False),
            ]
        )
        db_session.commit()
        monkeypatch.setattr(stock_picker_service, "_get_core_codes", lambda db: ["688001.SH", "600002.SH"])

        warehouse_universe = stock_picker_service._build_universe(db_session, user_id, "warehouse")
        core_universe = stock_picker_service._build_universe(db_session, user_id, "core")
        all_universe = stock_picker_service._build_universe(db_session, user_id, "all")

        assert {item.stock_code for item in warehouse_universe} == {"600001.SH"}
        assert {item.stock_code for item in core_universe} == {"600002.SH", "688001.SH"}
        assert {item.stock_code for item in all_universe} == {"600001.SH", "600002.SH", "688001.SH", "688002.SH"}

    def test_factor_stage_ranks_candidates_and_applies_source_limit(self, db_session):
        stock_picker_service = _get_stock_picker_service()
        specs = []
        for idx in range(45):
            code = f"688{100 + idx:03d}.SH"
            specs.append((code, f"测试股票{idx}", 5.0 + idx, 10.0 + idx * 0.2))
        _seed_candidate_batch(db_session, specs)
        db_session.commit()

        universe = stock_picker_service._build_universe(db_session, user_id=1, scope="all")
        ranked = stock_picker_service._rank_candidates(db_session, universe, "growth", "all")

        assert len(ranked) == 40
        assert ranked == sorted(ranked, key=lambda item: item.factor_score, reverse=True)
        assert ranked[0].factor_score >= ranked[-1].factor_score
        assert ranked[0].research_payload["quant_support"]["final_quant_score"] == ranked[0].factor_score
        assert "profit_condition_score" in ranked[0].research_payload["quant_support"]
        assert "profit_logic" in ranked[0].research_payload["quant_summary"]
        assert ranked[0].research_payload["quant_summary"]["trend_evidence"]

    def test_get_core_codes_uses_tushare_index_constituents(self, db_session, monkeypatch):
        stock_picker_service = _get_stock_picker_service()
        monkeypatch.setattr(
            "app.ai.stock_picker.service.get_core_index_constituent_codes",
            lambda: ["000001.SZ", "600519.SH"],
        )

        result = stock_picker_service._get_core_codes(db_session)

        assert result == ["000001.SZ", "600519.SH"]

    @pytest.mark.parametrize(
        ("scope", "expected_limit"),
        [
            ("warehouse", 20),
            ("core", 30),
            ("all", 40),
        ],
    )
    def test_rank_candidates_applies_scope_limits_for_each_scope(self, db_session, scope, expected_limit):
        stock_picker_service = _get_stock_picker_service()
        specs = []
        for idx in range(45):
            code = f"689{100 + idx:03d}.SH"
            specs.append((code, f"范围股票{idx}", 10.0 + idx, 12.0 + idx * 0.1))
        _seed_candidate_batch(db_session, specs)
        db_session.commit()

        universe = stock_picker_service._build_universe(db_session, user_id=1, scope="all")
        ranked = stock_picker_service._rank_candidates(db_session, universe, "growth", scope)

        assert len(ranked) == expected_limit
        assert ranked == sorted(ranked, key=lambda item: item.factor_score, reverse=True)

    def test_rank_candidates_uses_latest_market_and_valuation_snapshots(self, db_session):
        stock_picker_service = _get_stock_picker_service()
        stock_code = "688301.SH"
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code=stock_code,
            name="最新快照测试",
            change_60=5.0,
            pe=36.0,
            pb=4.5,
            turnover=0.9,
            volume_ratio=0.8,
            inflow_5d=50000000.0,
            ps_ttm=8.5,
            dividend_yield=0.2,
            rt_timestamp=datetime(2026, 3, 28, 10, 0, 0),
            val_date=date(2026, 3, 27),
            kline_date=date(2026, 3, 27),
            close=48.0,
            kline_volume=12000000.0,
            kline_turnover=350000000.0,
            macd=0.15,
            macd_signal=0.18,
            rsi_12=46.0,
            rsi_24=48.0,
            kdj_j=45.0,
            atr=1.8,
        )
        db_session.add(
            StockRealtimeMarket(
                stock_code=stock_code,
                current_price=62.0,
                change_percent=4.0,
                change_60days=26.0,
                turnover_rate=3.0,
                volume_ratio=2.4,
                pe_dynamic=14.0,
                pb_ratio=2.2,
                total_market_cap=180000000000.0,
                main_net_inflow_5d=900000000.0,
                timestamp=datetime(2026, 3, 29, 10, 0, 0),
                data_source="test",
            )
        )
        db_session.add(
            StockValuationHistory(
                stock_code=stock_code,
                data_date=date(2026, 3, 29),
                total_market_value=180000000000.0,
                pe_ttm=14.0,
                pb=2.2,
                ps_ttm=2.8,
                dividend_yield=1.8,
                data_source="test",
            )
        )
        db_session.add(
            KlineData(
                stock_code=stock_code,
                date=date(2026, 3, 29),
                open=60.0,
                close=62.0,
                high=63.0,
                low=59.0,
                volume=65000000.0,
                turnover=2400000000.0,
                change=2.0,
                change_percent=3.5,
                freq="D",
                data_source="test",
            )
        )
        db_session.add(
            StockIndicators(
                stock_code=stock_code,
                trade_date=date(2026, 3, 29),
                ma20=58.0,
                ma60=54.0,
                macd=0.62,
                macd_signal=0.28,
                macd_hist=0.34,
                kdj_k=71.0,
                kdj_d=66.0,
                kdj_j=81.0,
                rsi_6=69.0,
                rsi_12=64.0,
                rsi_24=58.0,
                cci=120.0,
                wr_14=-18.0,
                boll_upper=64.0,
                boll_mid=60.0,
                boll_lower=56.0,
                atr=2.2,
                obv=2000000.0,
                data_source="test",
            )
        )
        db_session.commit()

        universe = stock_picker_service._build_universe(db_session, user_id=1, scope="all")
        ranked = stock_picker_service._rank_candidates(db_session, universe, "growth", "all")

        latest_val = (
            db_session.query(StockValuationHistory)
            .filter(StockValuationHistory.stock_code == stock_code)
            .order_by(StockValuationHistory.data_date.desc())
            .first()
        )
        latest_kline = (
            db_session.query(KlineData)
            .filter(KlineData.stock_code == stock_code, KlineData.freq == "D")
            .order_by(KlineData.date.desc())
            .first()
        )
        latest_indicators = (
            db_session.query(StockIndicators)
            .filter(StockIndicators.stock_code == stock_code)
            .order_by(StockIndicators.trade_date.desc())
            .first()
        )
        quant_inputs = stock_picker_service._resolve_quant_inputs(
            universe[0],
            latest_val,
            latest_kline,
            latest_indicators,
        )
        expected_support = stock_picker_service._compute_quant_support("growth", quant_inputs)

        assert len(ranked) == 1
        assert ranked[0].stock_code == stock_code
        assert ranked[0].factor_score == expected_support["final_quant_score"]
        assert ranked[0].research_payload["quant_support"] == expected_support
        assert ranked[0].research_payload["quant_summary"]["profit_logic"]
        assert ranked[0].research_payload["quant_summary"]["invalidation_conditions"]

    def test_rank_candidates_raises_on_missing_required_quant_data(self, db_session):
        stock_picker_service = _get_stock_picker_service()
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="688302.SH",
            name="缺失快照测试",
            rt_timestamp=None,
            val_date=None,
            kline_date=None,
        )
        db_session.commit()

        universe = stock_picker_service._build_universe(db_session, user_id=1, scope="all")
        with pytest.raises(ValueError, match="缺少必要数据"):
            stock_picker_service._rank_candidates(db_session, universe, "balanced", "all")

    def test_rank_candidates_allows_analysis_when_factor_completeness_reaches_threshold(self, db_session):
        stock_picker_service = _get_stock_picker_service()
        for idx in range(5):
            stock_code = f"68835{idx}.SH"
            kwargs = {
                "stock_code": stock_code,
                "name": f"完整率样本{idx}",
                "rt_timestamp": datetime(2026, 3, 29, 10, 0, 0),
                "val_date": date(2026, 3, 29),
                "kline_date": date(2026, 3, 29),
            }
            if idx == 4:
                kwargs["val_date"] = None
            _seed_rank_candidate_with_metrics(db_session, **kwargs)
        db_session.commit()

        universe = stock_picker_service._build_universe(db_session, user_id=1, scope="all")
        ranked = stock_picker_service._rank_candidates(
            db_session,
            universe,
            "balanced",
            "all",
            factor_candidate_limit=30,
            same_industry_limit=4,
        )

        assert len(ranked) == 4
        assert {candidate.stock_code for candidate in ranked} == {
            "688350.SH",
            "688351.SH",
            "688352.SH",
            "688353.SH",
        }

    def test_rank_candidates_changes_order_by_style(self, db_session):
        stock_picker_service = _get_stock_picker_service()
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="688311.SH",
            name="高动量样本",
            change_60=25.0,
            pe=35.0,
            pb=5.0,
            turnover=2.0,
            volume_ratio=1.5,
            inflow_5d=1000000000.0,
            ps_ttm=8.0,
            dividend_yield=0.1,
            rt_timestamp=datetime(2026, 3, 29, 10, 0, 0),
            val_date=date(2026, 3, 29),
            kline_date=date(2026, 3, 29),
            close=50.0,
            kline_volume=65000000.0,
            kline_turnover=2600000000.0,
            macd=0.8,
            macd_signal=0.2,
            rsi_12=68.0,
            rsi_24=60.0,
            kdj_j=82.0,
            atr=1.6,
        )
        _seed_rank_candidate_with_metrics(
            db_session,
            stock_code="688312.SH",
            name="低估值样本",
            change_60=2.0,
            pe=8.0,
            pb=1.2,
            turnover=2.0,
            volume_ratio=1.5,
            inflow_5d=100000000.0,
            ps_ttm=4.0,
            dividend_yield=4.0,
            rt_timestamp=datetime(2026, 3, 29, 10, 0, 0),
            val_date=date(2026, 3, 29),
            kline_date=date(2026, 3, 29),
            close=30.0,
            kline_volume=20000000.0,
            kline_turnover=600000000.0,
            macd=0.18,
            macd_signal=0.12,
            rsi_12=52.0,
            rsi_24=50.0,
            kdj_j=58.0,
            atr=0.8,
        )
        db_session.commit()

        universe = stock_picker_service._build_universe(db_session, user_id=1, scope="all")
        momentum_ranked = stock_picker_service._rank_candidates(db_session, universe, "momentum", "all")
        value_ranked = stock_picker_service._rank_candidates(db_session, universe, "value", "all")

        assert [item.stock_code for item in momentum_ranked] == ["688311.SH", "688312.SH"]
        assert [item.stock_code for item in value_ranked] == ["688312.SH", "688311.SH"]
        assert momentum_ranked[0].factor_score > momentum_ranked[1].factor_score
        assert value_ranked[0].factor_score > value_ranked[1].factor_score

    def test_compute_quant_support_does_not_reward_invalid_valuation(self):
        stock_picker_service = _get_stock_picker_service()
        valid_inputs = {
            "pe": 12.0,
            "pb": 1.4,
            "ps_ttm": 3.0,
            "dividend_yield": 3.0,
            "market_cap": 120000000000.0,
            "close": 40.0,
            "volume": 30000000.0,
            "turnover_amount": 900000000.0,
            "macd": 0.45,
            "macd_signal": 0.25,
            "macd_hist": 0.2,
            "rsi_12": 58.0,
            "rsi_24": 52.0,
            "kdj_j": 66.0,
            "atr": 1.2,
            "atr_pct": 3.0,
        }
        invalid_inputs = dict(valid_inputs, pe=-12.0, pb=0.0, ps_ttm=-3.0)

        valid_support = stock_picker_service._compute_quant_support("value", valid_inputs)
        invalid_support = stock_picker_service._compute_quant_support("value", invalid_inputs)

        assert invalid_support["valuation_safety_score"] < valid_support["valuation_safety_score"]
        assert invalid_support["risk_penalty"] > valid_support["risk_penalty"]
        assert invalid_support["final_quant_score"] < valid_support["final_quant_score"]

    def test_resolve_quant_inputs_rejects_invalid_price_or_atr(self):
        stock_picker_service = _get_stock_picker_service()
        basic = StockBasic(stock_code="688399.SH", name="异常数值样本")
        val = StockValuationHistory(
            stock_code="688399.SH",
            pe_ttm=12.0,
            pb=1.5,
            ps_ttm=3.0,
            dividend_yield=2.0,
            total_market_value=100000000000.0,
        )
        indicators = StockIndicators(
            stock_code="688399.SH",
            macd=0.3,
            macd_signal=0.1,
            rsi_12=55.0,
            rsi_24=52.0,
            kdj_j=62.0,
            atr=1.0,
        )

        zero_close = KlineData(stock_code="688399.SH", close=0.0, volume=10000000.0, turnover=500000000.0)
        with pytest.raises(ValueError, match="kline.close<=0"):
            stock_picker_service._resolve_quant_inputs(basic, val, zero_close, indicators)

        valid_kline = KlineData(stock_code="688399.SH", close=40.0, volume=10000000.0, turnover=500000000.0)
        indicators.atr = -1.0
        with pytest.raises(ValueError, match="stock_indicators.atr<0"):
            stock_picker_service._resolve_quant_inputs(basic, val, valid_kline, indicators)

    def test_rank_candidates_limits_single_industry_concentration(self, db_session):
        stock_picker_service = _get_stock_picker_service()
        bank_specs = [
            ("688410.SH", "银行样本0", 7.0, 5.0, 0.7),
            ("688411.SH", "银行样本1", 7.0, 5.5, 0.8),
            ("688412.SH", "银行样本2", 7.0, 6.0, 0.9),
            ("688413.SH", "银行样本3", 7.0, 6.5, 1.0),
            ("688414.SH", "银行样本4", 7.0, 7.0, 1.1),
            ("688415.SH", "银行样本5", 7.0, 7.5, 1.2),
            ("688416.SH", "银行样本6", 7.0, 8.0, 1.3),
            ("688417.SH", "银行样本7", 7.0, 8.5, 1.4),
        ]
        other_specs = [
            ("688420.SH", "白酒样本", "白酒"),
            ("688421.SH", "电力样本", "火力发电"),
            ("688422.SH", "保险样本", "保险"),
            ("688423.SH", "建筑样本", "建筑工程"),
        ]
        for stock_code, name, change_60, pe, pb in bank_specs:
            _seed_rank_candidate_with_metrics(
                db_session,
                stock_code=stock_code,
                name=name,
                industry="银行",
                market="主板",
                change_60=change_60,
                pe=pe,
                pb=pb,
                dividend_yield=3.5,
                val_date=date(2026, 3, 29),
                kline_date=date(2026, 3, 29),
                rt_timestamp=datetime(2026, 3, 29, 10, 0, 0),
                kline_turnover=600000000.0,
                kline_volume=12000000.0,
                macd=0.25,
                macd_signal=0.18,
                rsi_12=54.0,
                rsi_24=51.0,
                kdj_j=60.0,
                atr=0.8,
            )
        for idx, (stock_code, name, industry) in enumerate(other_specs):
            _seed_rank_candidate_with_metrics(
                db_session,
                stock_code=stock_code,
                name=name,
                industry=industry,
                market="主板",
                change_60=9.0 + idx,
                pe=12.0 + idx,
                pb=1.8 + idx * 0.2,
                dividend_yield=2.0 + idx * 0.2,
                val_date=date(2026, 3, 29),
                kline_date=date(2026, 3, 29),
                rt_timestamp=datetime(2026, 3, 29, 10, 0, 0),
                kline_turnover=900000000.0 + idx * 100000000.0,
                kline_volume=20000000.0 + idx * 1000000.0,
                macd=0.38 + idx * 0.02,
                macd_signal=0.2,
                rsi_12=59.0 + idx,
                rsi_24=53.0,
                kdj_j=68.0 + idx,
                atr=1.0 + idx * 0.1,
            )
        db_session.commit()

        universe = stock_picker_service._build_universe(db_session, user_id=1, scope="all")
        ranked = stock_picker_service._rank_candidates(
            db_session,
            universe,
            "balanced",
            "all",
            factor_candidate_limit=30,
            same_industry_limit=5,
        )

        bank_count = sum(1 for item in ranked if item.industry == "银行")
        assert bank_count == 5
        assert len(ranked) == 9

    def test_research_stage_normalizes_llm_payload_with_ai_as_primary_signal(self):
        from app.ai.stock_picker.service import RankedCandidate

        stock_picker_service = _get_stock_picker_service()
        ranked = [
            RankedCandidate(
                stock_code="688021.SH",
                stock_name="奥福环保",
                industry="环保",
                market="科创板",
                factor_score=40.0,
                ai_score=0.0,
                final_score=40.0,
                decision="watch",
                research_payload={
                    "quant_summary": {
                        "thesis": "量化辅助观点A",
                        "catalysts": ["催化A"],
                        "risks": ["风险A"],
                    },
                    "quant_support": {"final_quant_score": 40.0},
                },
            ),
            RankedCandidate(
                stock_code="688022.SH",
                stock_name="瀚川智能",
                industry="自动化",
                market="科创板",
                factor_score=80.0,
                ai_score=0.0,
                final_score=80.0,
                decision="watch",
                research_payload={
                    "quant_summary": {
                        "thesis": "量化辅助观点B",
                        "catalysts": ["催化B"],
                        "risks": ["风险B"],
                    },
                    "quant_support": {"final_quant_score": 80.0},
                },
            ),
        ]
        payload = {
            "research": [
                {
                    "stock_code": "688021.SH",
                    "ai_score": 95,
                    "thesis": "AI 强烈看好。",
                    "profit_logic": "趋势和订单证据支持赚钱概率。",
                    "catalysts": ["订单加速"],
                    "trend_evidence": ["趋势走强"],
                    "risk_evidence": ["波动偏高"],
                    "risks": ["波动偏高"],
                    "invalidation_conditions": ["趋势破位"],
                    "style_fit_explanation": "更适合成长。",
                    "holding_horizon": "mid_term",
                    "decision": "keep",
                },
                {
                    "stock_code": "688022.SH",
                    "ai_score": 70,
                    "decision": "watch",
                },
                {
                    "stock_code": "000001.SZ",
                    "ai_score": 99,
                    "decision": "keep",
                },
                {
                    "stock_code": "688021.SH",
                    "ai_score": 60,
                    "decision": "drop",
                },
            ]
        }

        normalized, error = stock_picker_service._normalize_llm_research(payload, ranked, "growth", 2)

        assert error is None
        assert [item.stock_code for item in normalized] == ["688021.SH", "688022.SH"]
        assert normalized[0].final_score == 81.25
        assert normalized[1].final_score == 72.5
        assert normalized[0].research_payload["thesis"] == "AI 强烈看好。"
        assert normalized[0].research_payload["profit_logic"] == "趋势和订单证据支持赚钱概率。"
        assert normalized[0].research_payload["trend_evidence"] == ["趋势走强"]
        assert normalized[0].research_payload["risk_evidence"] == ["波动偏高"]
        assert normalized[0].research_payload["invalidation_conditions"] == ["趋势破位"]
        assert normalized[1].research_payload["thesis"] == "量化辅助观点B"
        assert "profit_logic" in normalized[1].research_payload
        assert normalized[0].decision == "keep"
        assert normalized[1].decision == "watch"

    @pytest.mark.asyncio
    async def test_request_llm_research_uses_agentic_tools_and_pydantic_output(self, monkeypatch):
        from app.ai.stock_picker.service import RankedCandidate
        import app.ai.stock_picker.service as stock_picker_service_module

        stock_picker_service = _get_stock_picker_service()
        ranked = [
            RankedCandidate(
                stock_code="688021.SH",
                stock_name="奥福环保",
                industry="环保",
                market="科创板",
                factor_score=40.0,
                ai_score=0.0,
                final_score=40.0,
                decision="watch",
                research_payload={
                    "quant_summary": {
                        "thesis": "量化辅助观点A",
                        "catalysts": ["催化A"],
                        "risks": ["风险A"],
                    },
                    "quant_support": {"final_quant_score": 40.0},
                },
            ),
            RankedCandidate(
                stock_code="688022.SH",
                stock_name="瀚川智能",
                industry="自动化",
                market="科创板",
                factor_score=80.0,
                ai_score=0.0,
                final_score=80.0,
                decision="watch",
                research_payload={
                    "quant_summary": {
                        "thesis": "量化辅助观点B",
                        "catalysts": ["催化B"],
                        "risks": ["风险B"],
                    },
                    "quant_support": {"final_quant_score": 80.0},
                },
            ),
        ]
        tool_calls = []
        bound_tools = []

        class _FakeResponse:
            def __init__(self, *, tool_calls=None, content=""):
                self.tool_calls = tool_calls or []
                self.content = content

        class _FakeTool:
            name = "query_stock_data"

            async def ainvoke(self, args):
                tool_calls.append(args)
                return {
                    "stock_code": args["stock_code"],
                    "results": {
                        "basic": {"stock_code": args["stock_code"], "stock_name": "测试股票"},
                        "valuation": [{"pe_ttm": 18.0, "pb": 2.1}],
                    },
                }

        class _FakeLLM:
            def bind_tools(self, tools):
                bound_tools[:] = tools
                return self

            async def ainvoke(self, messages):
                if any(message.__class__.__name__ == "ToolMessage" for message in messages):
                    return _FakeResponse(
                        content=json.dumps(
                            {
                                "research": [
                                    {
                                        "stock_code": "688021.SH",
                                        "ai_score": 81,
                                        "thesis": "研究结论A",
                                        "catalysts": ["催化A"],
                                        "risks": ["风险A"],
                                        "style_fit_explanation": "匹配平衡风格",
                                        "holding_horizon": "mid_term",
                                        "decision": "keep",
                                    },
                                    {
                                        "stock_code": "688022.SH",
                                        "ai_score": 83,
                                        "thesis": "研究结论B",
                                        "catalysts": ["催化B"],
                                        "risks": ["风险B"],
                                        "style_fit_explanation": "匹配平衡风格",
                                        "holding_horizon": "mid_term",
                                        "decision": "keep",
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        )
                    )
                return _FakeResponse(
                    tool_calls=[
                        {
                            "id": "tool-1",
                            "name": "query_stock_data",
                            "args": {
                                "stock_code": "688021.SH",
                                "data_configs": {
                                    "basic": {
                                        "limit": 1,
                                        "start_time": "2025-03-30 00:00:00",
                                        "end_time": "2026-03-30 23:59:59",
                                    },
                                    "valuation": {
                                        "limit": 3,
                                        "start_time": "2025-03-30 00:00:00",
                                        "end_time": "2026-03-30 23:59:59",
                                    },
                                },
                            },
                        }
                    ]
                )

        monkeypatch.setattr(stock_picker_service_module, "get_all_tools", lambda: [_FakeTool()])
        _patch_stock_picker_llm_provider(monkeypatch, stock_picker_service_module, _FakeLLM())

        payload = await stock_picker_service._request_llm_research(ranked, "balanced", 2)

        assert payload is not None
        assert bound_tools
        assert bound_tools[0].name == "query_stock_data"
        assert tool_calls
        assert tool_calls[0]["stock_code"] == "688021.SH"
        assert [item["stock_code"] for item in payload["research"]] == ["688021.SH", "688022.SH"]

    def test_llm_candidate_summaries_include_numeric_units(self):
        stock_picker_service = _get_stock_picker_service()
        ranked = [_make_ranked_candidate("688021.SH", stock_name="奥福环保", factor_score=66.0)]
        ranked[0].research_payload["quant_inputs"] = {
            "pe": 12.5,
            "dividend_yield": 2.3,
            "turnover_amount": 1200000000.0,
            "atr_pct": 3.2,
        }

        summaries = stock_picker_service._build_llm_candidate_summaries(ranked)

        assert summaries[0]["formatted_quant_inputs"]["pe"] == "12.5倍"
        assert summaries[0]["formatted_quant_inputs"]["dividend_yield"] == "2.3%"
        assert summaries[0]["formatted_quant_inputs"]["turnover_amount"] == "12亿元"
        assert summaries[0]["formatted_quant_support"]["final_quant_score"] == "66点"

    @pytest.mark.asyncio
    async def test_request_llm_research_requires_evidence_tool_after_loader_tool(self, monkeypatch):
        from app.ai.stock_picker.service import RankedCandidate
        import app.ai.stock_picker.service as stock_picker_service_module

        stock_picker_service = _get_stock_picker_service()
        ranked = [
            RankedCandidate(
                stock_code="688021.SH",
                stock_name="奥福环保",
                industry="环保",
                market="科创板",
                factor_score=40.0,
                ai_score=0.0,
                final_score=40.0,
                decision="watch",
                research_payload={
                    "quant_summary": {"thesis": "量化辅助观点A", "catalysts": ["催化A"], "risks": ["风险A"]},
                    "quant_support": {"final_quant_score": 40.0},
                },
            )
        ]
        tool_calls = []

        class _FakeResponse:
            def __init__(self, *, tool_calls=None, content=""):
                self.tool_calls = tool_calls or []
                self.content = content

        class _FakeTool:
            def __init__(self, name):
                self.name = name

            async def ainvoke(self, args):
                tool_calls.append(self.name)
                return {"success": True, "tool": self.name}

        final_payload = json.dumps(
            {
                "research": [
                    {
                        "stock_code": "688021.SH",
                        "ai_score": 81,
                        "thesis": "研究结论A",
                        "catalysts": ["催化A"],
                        "risks": ["风险A"],
                        "style_fit_explanation": "匹配平衡风格",
                        "holding_horizon": "mid_term",
                        "decision": "keep",
                    }
                ]
            },
            ensure_ascii=False,
        )

        class _FakeLLM:
            def __init__(self):
                self.calls = 0

            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return _FakeResponse(
                        tool_calls=[{"id": "tool-1", "name": "load_skill", "args": {"skill_id": "tushare-data"}}]
                    )
                if self.calls == 2:
                    return _FakeResponse(content=final_payload)
                if self.calls == 3:
                    return _FakeResponse(
                        tool_calls=[
                            {
                                "id": "tool-2",
                                "name": "query_stock_data",
                                "args": {"stock_code": "688021.SH", "data_configs": {}},
                            }
                        ]
                    )
                return _FakeResponse(content=final_payload)

        monkeypatch.setattr(stock_picker_service_module, "get_all_tools", lambda: [_FakeTool("query_stock_data")])
        monkeypatch.setattr(
            stock_picker_service_module,
            "get_skills_loader_tools",
            lambda: [_FakeTool("load_skill")],
        )
        _patch_stock_picker_llm_provider(monkeypatch, stock_picker_service_module, _FakeLLM())

        payload = await stock_picker_service._request_llm_research(ranked, "balanced", 1)

        assert payload is not None
        assert tool_calls == ["load_skill", "query_stock_data"]
        assert payload["research"][0]["stock_code"] == "688021.SH"

    @pytest.mark.asyncio
    async def test_request_llm_research_records_usage_for_each_llm_turn(self, monkeypatch):
        from app.core.config import settings
        from app.ai.stock_picker.service import RankedCandidate
        import app.ai.stock_picker.service as stock_picker_service_module

        stock_picker_service = _get_stock_picker_service()
        ranked = [
            RankedCandidate(
                stock_code="688021.SH",
                stock_name="测试一号",
                industry="半导体",
                market="科创板",
                factor_score=61.0,
                ai_score=0.0,
                final_score=61.0,
                decision="watch",
                research_payload={
                    "quant_summary": {"thesis": "量化观点", "catalysts": [], "risks": []},
                    "quant_support": {"final_quant_score": 61.0},
                },
            )
        ]
        usage_recorder = Mock()

        class _FakeResponse:
            def __init__(self, *, tool_calls=None, content=""):
                self.tool_calls = tool_calls or []
                self.content = content
                self.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

        class _FakeTool:
            name = "query_stock_data"

            async def ainvoke(self, args):
                return {
                    "stock_code": args["stock_code"],
                    "results": {"basic": {"stock_code": args["stock_code"], "stock_name": "测试股票"}},
                }

        class _FakeLLM:
            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages):
                if any(message.__class__.__name__ == "ToolMessage" for message in messages):
                    return _FakeResponse(
                        content=json.dumps(
                            {
                                "research": [
                                    {
                                        "stock_code": "688021.SH",
                                        "ai_score": 86,
                                        "thesis": "研究结论",
                                        "catalysts": ["催化"],
                                        "risks": ["风险"],
                                        "style_fit_explanation": "匹配平衡风格",
                                        "holding_horizon": "mid_term",
                                        "decision": "keep",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    )
                return _FakeResponse(
                    tool_calls=[
                        {
                            "id": "tool-1",
                            "name": "query_stock_data",
                            "args": {
                                "stock_code": "688021.SH",
                                "data_configs": {"basic": {"limit": 1}},
                            },
                        }
                    ]
        )

        monkeypatch.setattr(stock_picker_service_module, "get_all_tools", lambda: [_FakeTool()])
        provider = _patch_stock_picker_llm_provider(monkeypatch, stock_picker_service_module, _FakeLLM())
        monkeypatch.setattr(stock_picker_service_module, "record_llm_usage", usage_recorder)

        payload = await stock_picker_service._request_llm_research(ranked, "balanced", 1)

        assert payload is not None
        assert usage_recorder.call_count == 2
        assert usage_recorder.call_args_list[0].args[1] == settings.LLM_MODEL
        assert usage_recorder.call_args_list[0].args[2] == "stock_picker_research"
        assert usage_recorder.call_args_list[1].args[2] == "stock_picker_research"
        assert "api_key" not in provider.calls[0] or provider.calls[0]["api_key"] is None
        assert usage_recorder.call_args_list[0].kwargs["cache_lane"] == "research"
        assert usage_recorder.call_args_list[0].kwargs["api_key_alias"] == "research_llm_api_key"

    @pytest.mark.asyncio
    async def test_request_llm_research_summarizes_news_tool_output(self, monkeypatch):
        from app.ai.stock_picker.service import RankedCandidate
        import app.ai.stock_picker.service as stock_picker_service_module

        stock_picker_service = _get_stock_picker_service()
        ranked = [
            RankedCandidate(
                stock_code="600519.SH",
                stock_name="贵州茅台",
                industry="白酒",
                market="主板",
                factor_score=55.0,
                ai_score=0.0,
                final_score=55.0,
                decision="watch",
                research_payload={
                    "quant_summary": {
                        "thesis": "量化辅助观点",
                        "catalysts": ["催化"],
                        "risks": ["风险"],
                    },
                    "quant_support": {"final_quant_score": 55.0},
                },
            )
        ]
        tool_message_contents = []
        summary_calls = []

        class _FakeResponse:
            def __init__(self, *, tool_calls=None, content=""):
                self.tool_calls = tool_calls or []
                self.content = content

        class _FakeTool:
            name = "search_news"

            async def ainvoke(self, args):
                return {"articles": [{"title": "新闻"}], "raw": "N" * 13000}

        class _FakeLLM:
            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages):
                for message in messages:
                    if message.__class__.__name__ == "ToolMessage":
                        tool_message_contents.append(message.content)
                if tool_message_contents:
                    return _FakeResponse(
                        content=json.dumps(
                            {
                                "research": [
                                    {
                                        "stock_code": "600519.SH",
                                        "ai_score": 88,
                                        "thesis": "新闻和工具证据支持。",
                                        "catalysts": ["渠道反馈改善"],
                                        "risks": ["估值不低"],
                                        "style_fit_explanation": "适合平衡风格。",
                                        "holding_horizon": "mid_term",
                                        "decision": "keep",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    )
                return _FakeResponse(
                    tool_calls=[
                        {
                            "id": "tool-news-1",
                            "name": "search_news",
                            "args": {"query": "贵州茅台 新闻"},
                        }
                    ]
                )

        async def _fake_summarize_tool_output(llm, *, role_name, tool_name, content, tool_args=None, **_kwargs):
            summary_calls.append(
                {
                    "role_name": role_name,
                    "tool_name": tool_name,
                    "content_len": len(content),
                    "tool_args": tool_args,
                }
            )
            return "[Structured Summary of search_news]:\n新闻摘要"

        monkeypatch.setattr(stock_picker_service_module, "get_all_tools", lambda: [_FakeTool()])
        _patch_stock_picker_llm_provider(monkeypatch, stock_picker_service_module, _FakeLLM())
        monkeypatch.setattr(
            stock_picker_service_module,
            "summarize_tool_output",
            _fake_summarize_tool_output,
        )

        payload = await stock_picker_service._request_llm_research(ranked, "balanced", 1)

        assert payload is not None
        assert summary_calls
        assert summary_calls[0]["tool_name"] == "search_news"
        assert summary_calls[0]["role_name"] == "stock_picker_research"
        assert tool_message_contents
        assert tool_message_contents[0] == "[Structured Summary of search_news]:\n新闻摘要"
        assert payload["research"][0]["stock_code"] == "600519.SH"

    @pytest.mark.asyncio
    async def test_request_llm_research_retries_invalid_tool_calls_without_replaying_them(self, monkeypatch):
        from langchain_core.messages import AIMessage, HumanMessage
        from langchain_core.messages.tool import invalid_tool_call
        from app.ai.stock_picker.service import RankedCandidate
        import app.ai.stock_picker.service as stock_picker_service_module

        stock_picker_service = _get_stock_picker_service()
        ranked = [
            RankedCandidate(
                stock_code="600519.SH",
                stock_name="贵州茅台",
                industry="白酒",
                market="主板",
                factor_score=55.0,
                ai_score=0.0,
                final_score=55.0,
                decision="watch",
                research_payload={
                    "quant_summary": {
                        "thesis": "量化辅助观点",
                        "catalysts": ["催化"],
                        "risks": ["风险"],
                    },
                    "quant_support": {"final_quant_score": 55.0},
                },
            )
        ]
        call_messages = []

        class _FakeTool:
            name = "query_stock_data"

            async def ainvoke(self, args):
                return {"ok": True, "stock_code": args["stock_code"]}

        class _FakeLLM:
            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages):
                call_messages.append(list(messages))
                if len(call_messages) == 1:
                    return AIMessage(
                        content="tool args malformed",
                        additional_kwargs={
                            "tool_calls": [
                                {
                                    "id": "bad-tool-1",
                                    "type": "function",
                                    "function": {
                                        "name": "query_stock_data",
                                        "arguments": '{"stock_code"',
                                    },
                                }
                            ]
                        },
                        invalid_tool_calls=[
                            invalid_tool_call(
                                name="query_stock_data",
                                id="bad-tool-1",
                                args='{"stock_code"',
                                error="unexpected end of JSON input",
                            )
                        ],
                    )
                if len(call_messages) == 2:
                    return AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "query_stock_data",
                                "args": {"stock_code": "600519.SH"},
                                "id": "tool-fixed-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                return AIMessage(
                    content=json.dumps(
                        {
                            "research": [
                                {
                                    "stock_code": "600519.SH",
                                    "ai_score": 88,
                                    "thesis": "修正后直接给出研究结论。",
                                    "catalysts": ["品牌韧性"],
                                    "risks": ["估值偏高"],
                                    "style_fit_explanation": "适合平衡风格。",
                                    "holding_horizon": "mid_term",
                                    "decision": "keep",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )

        monkeypatch.setattr(stock_picker_service_module, "get_all_tools", lambda: [_FakeTool()])
        _patch_stock_picker_llm_provider(monkeypatch, stock_picker_service_module, _FakeLLM())

        payload = await stock_picker_service._request_llm_research(ranked, "balanced", 1)

        assert payload is not None
        assert len(call_messages) == 3
        replayed_ai_messages = [
            message
            for message in call_messages[1]
            if isinstance(message, AIMessage)
        ]
        assert replayed_ai_messages
        assert all(not message.invalid_tool_calls for message in replayed_ai_messages)
        assert all("tool_calls" not in message.additional_kwargs for message in replayed_ai_messages)
        retry_messages = [
            message.content
            for message in call_messages[1]
            if isinstance(message, HumanMessage)
        ]
        assert any("invalid tool-call arguments" in content for content in retry_messages)
        tool_messages = [
            message.content
            for message in call_messages[2]
            if message.__class__.__name__ == "ToolMessage"
        ]
        assert tool_messages
        assert payload["research"][0]["stock_code"] == "600519.SH"

    def test_recommendation_stage_selects_non_drop_candidates(self):
        from app.ai.stock_picker.service import RankedCandidate

        stock_picker_service = _get_stock_picker_service()
        researched = [
            RankedCandidate(
                stock_code=code,
                stock_name=name,
                industry="半导体",
                market="科创板",
                factor_score=60.0 + idx,
                ai_score=80.0 + idx,
                final_score=75.0 + idx,
                decision="drop" if idx == 3 else "keep",
                research_payload={
                    "thesis": f"{name} 的研究逻辑",
                    "profit_logic": f"{name} 的赚钱逻辑",
                    "trend_evidence": [f"{name} 趋势证据"],
                    "risk_evidence": [f"{name} 风险证据"],
                    "risks": [f"{name} 风险"],
                    "invalidation_conditions": [f"{name} 失效条件"],
                    "holding_horizon": "mid_term",
                },
            )
            for idx, (code, name) in enumerate(
                [
                    ("688021.SH", "奥福环保"),
                    ("688022.SH", "瀚川智能"),
                    ("688023.SH", "安恒信息"),
                    ("688025.SH", "杰普特"),
                ]
            )
        ]
        run = StockSelectionRun(
            user_id=1,
            scope="all",
            style="balanced",
            risk_level="medium",
            recommendation_count=3,
            request_payload={"same_industry_limit": 4},
        )

        items, summary = stock_picker_service._build_recommendations(researched, run)

        assert [item["rank"] for item in items] == [1, 2, 3]
        assert [item["stock_code"] for item in items] == ["688023.SH", "688022.SH", "688021.SH"]
        assert items[0]["recommendation_reason"] == "安恒信息 的赚钱逻辑"
        assert items[0]["trend_evidence"] == ["安恒信息 趋势证据"]
        assert items[0]["risk_evidence"] == ["安恒信息 风险证据"]
        assert items[0]["invalidation_conditions"] == ["安恒信息 失效条件"]
        assert all(item["decision"] == "keep" for item in items)
        assert summary["selected_count"] == 3
        assert summary["recommended_stock_codes"] == ["688023.SH", "688022.SH", "688021.SH"]

    def test_cleanup_interrupted_runs_marks_non_terminal_runs_failed(self, db_session):
        stock_picker_service = _get_stock_picker_service()

        created_run = StockSelectionRun(
            user_id=1,
            scope="all",
            style="balanced",
            risk_level="medium",
            recommendation_count=5,
            status="created",
            current_stage="created",
            request_payload={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 5,
                "risk_level": "medium",
                "factor_candidate_limit": 16,
                "research_candidate_limit": 8,
                "same_industry_limit": 3,
                "allowed_industries": [],
            },
        )
        running_run = StockSelectionRun(
            user_id=1,
            scope="core",
            style="growth",
            risk_level="medium",
            recommendation_count=5,
            status="running",
            current_stage="factor_ranked",
            request_payload={
                "scope": "core",
                "style": "growth",
                "recommendation_count": 5,
                "risk_level": "medium",
                "factor_candidate_limit": 20,
                "research_candidate_limit": 10,
                "same_industry_limit": 3,
                "allowed_industries": [],
            },
        )
        completed_run = StockSelectionRun(
            user_id=1,
            scope="warehouse",
            style="value",
            risk_level="medium",
            recommendation_count=5,
            status="completed",
            current_stage="completed",
            request_payload={
                "scope": "warehouse",
                "style": "value",
                "recommendation_count": 5,
                "risk_level": "medium",
                "factor_candidate_limit": 10,
                "research_candidate_limit": 6,
                "same_industry_limit": 3,
                "allowed_industries": [],
            },
        )
        db_session.add_all([created_run, running_run, completed_run])
        db_session.commit()
        stock_picker_service._record_event(
            db_session,
            created_run.run_id,
            stage="created",
            event_type="run_created",
            message=stock_picker_service._t("events.run_created"),
            push=False,
        )
        stock_picker_service._record_event(
            db_session,
            running_run.run_id,
            stage="factor_ranked",
            event_type="factor_ranked",
            message=stock_picker_service._t("events.factor_ranked", count=3, style="growth"),
            payload={"count": 3},
            push=False,
        )

        cleaned = stock_picker_service._cleanup_interrupted_runs_in_db(db_session)

        db_session.refresh(created_run)
        db_session.refresh(running_run)
        db_session.refresh(completed_run)

        assert cleaned == 2
        assert created_run.status == "failed_universe"
        assert created_run.current_stage == "failed_universe"
        assert created_run.error_message == "服务重启，未完成的 AI 智能选股任务已标记为失败"
        assert created_run.finished_at is not None
        assert running_run.status == "failed_ai_research"
        assert running_run.current_stage == "failed_ai_research"
        assert running_run.error_message == "服务重启，未完成的 AI 智能选股任务已标记为失败"
        assert running_run.finished_at is not None
        assert completed_run.status == "completed"
        assert completed_run.current_stage == "completed"

        created_events = stock_picker_service.get_events(db_session, created_run.run_id, created_run.user_id)
        running_events = stock_picker_service.get_events(db_session, running_run.run_id, running_run.user_id)
        assert created_events[-1].stage == "failed_universe"
        assert created_events[-1].event_type == "failed"
        assert created_events[-1].payload["reason"] == "restart_recovery"
        assert running_events[-1].stage == "failed_ai_research"
        assert running_events[-1].event_type == "failed"
        assert running_events[-1].payload["reason"] == "restart_recovery"

    def test_workflow_success_records_stage_events_in_order(self, client, auth_headers, db_session, monkeypatch):
        _mock_stock_picker_llm(monkeypatch, cash_ratio=12.0)
        _seed_candidate_batch(
            db_session,
            [
                ("688021.SH", "奥福环保", 9.0, 15.0),
                ("688022.SH", "瀚川智能", 11.0, 18.0),
                ("688023.SH", "安恒信息", 14.0, 21.0),
                ("688025.SH", "杰普特", 8.0, 17.0),
            ],
        )
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )
        run_id = response.json()["run_id"]

        events_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}/events", headers=auth_headers)
        assert events_response.status_code == 200
        events = events_response.json()

        assert [event["stage"] for event in events] == [
            "created",
            "universe_built",
            "factor_ranked",
            "ai_researched",
            "recommendations_built",
            "completed",
        ]
        assert [event["event_type"] for event in events] == [
            "run_created",
            "universe_ready",
            "factor_ranked",
            "ai_researched",
            "recommendations_ready",
            "completed",
        ]
        assert events[1]["payload"]["count"] == 4
        assert events[3]["payload"]["mode"] == "llm"
        assert events[4]["payload"]["count"] == 4

    def test_workflow_failure_stops_after_research_stage_boundary(self, client, auth_headers, db_session, monkeypatch):
        _mock_stock_picker_invalid_research_llm(monkeypatch)
        _seed_candidate_batch(
            db_session,
            [
                ("688001.SH", "华兴源创", 10.0, 18.0),
                ("688002.SH", "睿创微纳", 15.0, 22.0),
                ("688003.SH", "天准科技", 8.0, 20.0),
                ("688004.SH", "博汇科技", 12.0, 16.0),
            ],
        )
        db_session.commit()

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )
        run_id = response.json()["run_id"]

        run_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}", headers=auth_headers)
        events_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}/events", headers=auth_headers)
        assert run_response.status_code == 200
        assert events_response.status_code == 200

        events = events_response.json()
        assert run_response.json()["status"] == "failed_ai_research"
        assert [event["stage"] for event in events] == [
            "created",
            "universe_built",
            "factor_ranked",
            "failed_ai_research",
        ]
        assert events[-1]["event_type"] == "failed"
        assert "缺少 research 列表" in events[-1]["message"]

    def test_workflow_failure_marks_failed_recommendation_when_recommendation_build_breaks(
        self,
        client,
        auth_headers,
        db_session,
        monkeypatch,
    ):
        stock_picker_service = _get_stock_picker_service()
        _mock_stock_picker_llm(monkeypatch, cash_ratio=12.0)
        _seed_candidate_batch(
            db_session,
            [
                ("688021.SH", "奥福环保", 9.0, 15.0),
                ("688022.SH", "瀚川智能", 11.0, 18.0),
                ("688023.SH", "安恒信息", 14.0, 21.0),
                ("688025.SH", "杰普特", 8.0, 17.0),
            ],
        )
        db_session.commit()

        monkeypatch.setattr(
            stock_picker_service,
            "_build_recommendations",
            Mock(side_effect=ValueError("推荐结果生成阶段故障")),
        )

        response = client.post(
            "/api/v1/ai-stock-picker/runs",
            json={
                "scope": "all",
                "style": "balanced",
                "recommendation_count": 4,
                "risk_level": "medium",
                "same_industry_limit": 4,
            },
            headers=auth_headers,
        )
        run_id = response.json()["run_id"]

        run_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}", headers=auth_headers)
        events_response = client.get(f"/api/v1/ai-stock-picker/runs/{run_id}/events", headers=auth_headers)

        assert run_response.status_code == 200
        assert events_response.status_code == 200
        assert run_response.json()["status"] == "failed_recommendation"
        assert run_response.json()["current_stage"] == "failed_recommendation"

        events = events_response.json()
        assert [event["stage"] for event in events] == [
            "created",
            "universe_built",
            "factor_ranked",
            "ai_researched",
            "failed_recommendation",
        ]
        assert events[-1]["event_type"] == "failed"
        assert events[-1]["message"] == "推荐结果生成阶段故障"
