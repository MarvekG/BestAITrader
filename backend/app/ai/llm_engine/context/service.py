from __future__ import annotations

from typing import Dict, Iterable

from app.ai.llm_engine.context.providers import DEFAULT_CONTEXT_PROVIDERS
from app.ai.llm_engine.context.runtime import AIContextRuntime
from app.ai.llm_engine.context.runtime import merge_status
from app.ai.llm_engine.context.types import (
    AI_CONTEXT_SECTION_ORDER,
    AIContextBuildResult,
    AIContextPayload,
    AIContextProvider,
)


def _missing_layer() -> AIContextPayload:
    return {"status": "missing"}


def _build_coverage(sections: Dict[str, Dict], errors: list[Dict[str, str]]) -> AIContextPayload:
    return {
        "status": merge_status(*(sections.get(name) for name in AI_CONTEXT_SECTION_ORDER if name != "metadata")),
        "layers": {
            name: sections.get(name, {}).get("status", "missing")
            for name in AI_CONTEXT_SECTION_ORDER
            if name != "metadata"
        },
        "errors": errors,
    }


def _build_targets(sections: Dict[str, Dict]) -> AIContextPayload:
    metadata = sections.setdefault("metadata", {})
    return {
        "_target_stock_code": metadata.get("stock_code"),
        "_target_stock_name": metadata.get("stock_name"),
    }


def _with_targets(payload: Dict, targets: AIContextPayload) -> AIContextPayload:
    merged = dict(payload)
    merged.update(targets)
    return merged


def _assemble_context(sections: Dict[str, Dict]) -> AIContextPayload:
    targets = _build_targets(sections)
    return {
        name: _with_targets(sections.get(name, _missing_layer()), targets)
        for name in AI_CONTEXT_SECTION_ORDER
    }


class AIContextService:
    def __init__(self, providers: Iterable[AIContextProvider] | None = None) -> None:
        self.providers = list(providers or DEFAULT_CONTEXT_PROVIDERS)

    async def build_result(self, stock_code: str) -> AIContextBuildResult:
        runtime = AIContextRuntime(stock_code)
        sections: Dict[str, Dict] = {}

        for provider in self.providers:
            try:
                layer = await provider.build(runtime, sections)
                sections[layer.name] = layer.payload
            except Exception as exc:
                runtime.record_error(provider.name, exc)
                sections[provider.name] = {"status": "error", "error": str(exc)}

        metadata = sections.setdefault("metadata", {})
        metadata["coverage"] = _build_coverage(sections, runtime.errors)
        context = _assemble_context(sections)

        return AIContextBuildResult(context=context, sections=sections)

    async def build(self, stock_code: str) -> Dict:
        result = await self.build_result(stock_code)
        return result.context
