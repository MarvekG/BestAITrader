from app.ai.llm_engine.context.readers import RiskReader
import app.ai.llm_engine.context as context_package


def test_risk_reader_does_not_expose_regulatory_info():
    assert not hasattr(RiskReader(), "regulatory_info")


def test_context_package_does_not_export_agent_adapter_builders():
    removed_exports = {
        "build_agent_targets",
        "build_news_agent_input",
        "build_pm_agent_input",
        "build_policy_agent_input",
        "build_sentiment_agent_input",
        "build_vertical_agent_input",
    }

    for export_name in removed_exports:
        assert not hasattr(context_package, export_name)
