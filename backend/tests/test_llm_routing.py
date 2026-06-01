from types import SimpleNamespace

from app.ai import llm_routing


def test_debate_parallelism_is_config_controlled(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_routing,
        "settings",
        SimpleNamespace(DEBATE_AGENT_PARALLEL_ENABLED=False),
        raising=False,
    )

    assert llm_routing.should_run_debate_agents_in_parallel() is False


def test_debate_parallelism_defaults_to_enabled(monkeypatch) -> None:
    monkeypatch.setattr(llm_routing, "settings", SimpleNamespace(), raising=False)

    assert llm_routing.should_run_debate_agents_in_parallel() is True


def test_usage_lanes_use_research_and_shared_labels() -> None:
    assert llm_routing.get_research_usage_lane() == (
        llm_routing.CACHE_LANE_RESEARCH,
        llm_routing.API_KEY_ALIAS_RESEARCH,
    )
    assert llm_routing.get_shared_usage_lane() == (
        llm_routing.CACHE_LANE_SHARED,
        llm_routing.API_KEY_ALIAS_SHARED,
    )
