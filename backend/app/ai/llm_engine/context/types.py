from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Mapping, Protocol

AIContextPayload = Dict[str, Any]
AI_CONTEXT_SECTION_ORDER = (
    "metadata",
    "portfolio",
    "realtime",
    "snapshot",
    "history",
    "signals",
    "events",
)

if TYPE_CHECKING:
    from app.ai.llm_engine.context.runtime import AIContextRuntime


@dataclass(slots=True)
class AIContextLayer:
    name: str
    payload: AIContextPayload


class AIContextProvider(Protocol):
    name: str

    async def build(
        self,
        runtime: "AIContextRuntime",
        sections: Mapping[str, AIContextPayload],
    ) -> AIContextLayer:
        ...


@dataclass(slots=True)
class AIContextBuildResult:
    context: AIContextPayload
    sections: Dict[str, AIContextPayload] = field(default_factory=dict)
