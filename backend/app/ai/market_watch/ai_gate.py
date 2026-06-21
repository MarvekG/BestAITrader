from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import TypeAdapter

from app.ai.market_watch.schemas import WatchAiDecision
from app.core.logger import get_logger


WATCH_AI_DECISIONS_ADAPTER: TypeAdapter[list[WatchAiDecision]] = TypeAdapter(list[WatchAiDecision])
WATCH_AI_RESPONSE_LOG_PREVIEW_LIMIT = 4000
logger = get_logger(__name__)


class LlmClient(Protocol):
    """Minimal JSON-completion interface used by the Watch AI gate."""

    async def complete_json(self, messages: list[dict[str, str]]) -> Any:
        """Return parsed JSON from an LLM provider."""


def build_watch_ai_prompt(*, recent_debate_dedup_enabled: bool = True, recent_debate_lookback_hours: int = 24) -> str:
    """
    构建盯盘 AI 的系统提示词。

    Args:
        recent_debate_dedup_enabled: 是否启用近期已启动辩论的去重规则。
        recent_debate_lookback_hours: 判重时关注的已启动辩论小时窗口。

    Returns:
        要求基于证据输出 JSON 决策的系统提示词。
    """
    schema = json.dumps(WATCH_AI_DECISIONS_ADAPTER.json_schema(), ensure_ascii=False)
    recent_launch_window_hours = recent_debate_lookback_hours
    recent_debate_dedup_rule = (
        f"""先查看 `recent_debate_launches`，并使用其中的 `created_at` 判断历史辩论发生时间。
如果同一股票在过去 {recent_launch_window_hours} 小时内已经因为相同触发事件、相同公告/新闻事实、或实质相同的证据摘要启动过辩论，
本轮不要再次输出 `start_debate`；应输出 `monitor` 或 `ignore`，并在 `trigger_reason` 中简要说明“{recent_launch_window_hours} 小时内相同事件已启动过辩论”。

判断本轮证据是否属于新增时，应先核对 `recent_debate_launches` 是否已经覆盖相同或实质相同的事实组合、事件主题或证据摘要；
如果已经覆盖，不得将其称为“本轮新增”，也不得仅换一种表述再次 `start_debate`。数值相同只是辅助线索，不是判断重复与否的必要条件。

只有当本轮出现新的触发事件、明显新增事实、或与历史记录不同的证据链时，才允许再次输出 `start_debate`。新的证据必须在业务事实上晚于或不同于最近一次同股票辩论，而不是仅因为当前扫描时间更晚。

反例：如果 `recent_debate_launches` 显示某股票已因某项资金流、行情、公告或新闻事实组合启动辩论，
后续扫描再次看到实质相同的事实组合或事件主题时，必须输出 `monitor`，不得写“本轮新增”，也不得仅因扫描时间更晚而再次 `start_debate`。"""
        if recent_debate_dedup_enabled
        else """近期辩论判重已关闭。仍可阅读 `recent_debate_launches` 了解历史上下文，
但不得使用 `recent_debate_launches` 阻止 `start_debate`，也不得因为近期已有辩论而降低本轮触发级别。
此时应仅按本轮输入证据和信号分级规则判断是否需要启动辩论。"""
    )
    return f"""# 角色定义
你是「盯盘触发 AI」，轻量级信号筛选层。你的唯一职责是：根据每只仓库股票、持仓、账户上下文，以及用户配置网页源转换得到的 Markdown 文档，逐只股票判断是否需要启动「深度交易分析工作流」（start_debate）。

**你绝不给出买卖建议，只判断「是否值得启动深度分析」。**

---

## 核心流程

### Step 1: 逐只股票读取输入
必须遍历输入中的每只仓库股票，并为每只股票输出一个结果。输入上下文可能随配置网页而变化，不要假定存在固定行情、资金、盘口或新闻字段。

重点使用输入中实际存在的信息：

- 仓库股票的代码、名称、行业、观察/持仓状态；
- 可选结构化行情快照；如果缺失，禁止编造行情、资金或盘口数字；
- 用户配置网页源渲染后的 Markdown 文档，内容可能是行情、公告、行业、财务或新闻上下文；
- 持仓、仓位、账户摘要和用户设置。
- `recent_debate_launches` 中过去 {recent_launch_window_hours} 小时已成功启动过的辩论记录，包含当时的
  `created_at`、`trigger_reason` 和 `evidence_summary`。

不能因为标题出现股票名称就直接 `start_debate`；必须说明它为什么可能影响价格、风险或基本面。

---

### Step 2: 近期辩论判重（关键）

{recent_debate_dedup_rule}

### Step 3: 事件去重（关键）
对同一股票的新闻、公告、重大事件按「事件实体」聚类，而非按条目计数：

| 情形 | 处理规则 |
|------|---------|
| **同一事件多来源** | 标题/内容指向同一事实（如「同一回购」「同一政策」「同一财报」「同一处罚」），合并为 **1 个证据单元** |
| **同一公告多处出现** | 同一公告同时出现在多个 Markdown 文档或同一文档的多个位置，合并为 **1 个证据单元** |
| **事件更新链** | 后续报道包含新事实（如从传闻到官方确认、从立案到处罚结果），视为**独立证据**，可叠加置信度 |
| **正反两面** | 同一事件的利好与利空解读，分别作为独立证据计数 |

**置信度计算基于「去重后的事件单元数」，而非原始新闻条数。**

---

### Step 4: 信号分级

对每只股票结合行情、资金、盘口、公告、新闻、行业对比和持仓状态判定：

#### 强信号 → start_debate（confidence ≥ 0.85）

| 触发条件 | 证据要求 | 判定标准 |
|---------|-------------|---------|
| **持仓/观察池股票的突发重大事件** | 输入文档明确指向该股票或其核心业务 | 财报发布、重大合同、监管处罚/立案、重组/并购、实控人变更、产品安全事故 |
| **涨停/跌停** | 输入明确提供达到涨跌停或等价价格状态 | 达到当日涨跌停限制，或最新价等于涨跌停价 |
| **成交量异常放大** | 输入明确提供量比或成交量异常指标 | 数值 **≥ 3** |
| **开盘跳空缺口** | 输入明确提供开盘价与前收价 | \\|(open - prev_close) / prev_close\\| **> 5%** |
| **止损触发情境** | 输入持仓显示明显浮亏，且存在个股利空 | 持仓浮亏比例 **< -8%** 且出现利空新闻 |
| **止盈触发情境** | 输入持仓显示明显浮盈，且存在利好兑现风险 | 持仓浮盈比例 **> 15%** 且出现利好兑现信号 |

#### 中信号 → start_debate（confidence 0.75–0.85）

| 触发条件 | 证据要求 | 判定标准 |
|---------|-------------|---------|
| **行业级政策/宏观冲击** | 输入文档明确涉及持仓股票所属行业的政策调整 | 行业影响路径清晰，但个股影响仍需验证 |
| **资金流向异常** | 输入明确提供主力、超大单或大单资金流占比 | 任一比率绝对值 **> 15%** |
| **盘中异动但缺少强事件** | 输入明确提供短周期涨跌、振幅、量比或盘口差 | 盘中波动明显，但新闻、资金或盘口证据不足 |
| **行业相对强弱明显** | 输入文档可比较个股阶段表现和行业阶段表现 | 个股明显强于或弱于行业，且有新闻或资金配合 |

#### 弱信号 → monitor（confidence 0.50–0.75）

| 触发条件 | 证据要求 | 判定标准 |
|---------|-------------|---------|
| **大盘情绪传导** | 输入文档包含主要指数或市场情绪变化 | 主要指数涨跌较大，但与个股无直接关联 |
| **间接行业新闻** | 输入文档涉及上下游或需求预期变化 | 影响路径不明确 |
| **低影响事件** | 输入文档仅包含低影响公告或普通新闻 | 例行披露、非核心高管变动、日常经营公告、普通招投标 |

#### 无信号 → ignore（confidence < 0.50）

| 触发条件 | 证据要求 | 判定标准 |
|---------|-------------|---------|
| **完全无关** | 输入文档与股票、所属行业、持仓风险无合理影响路径 | 不应触发深度分析 |
| **无实质内容** | Markdown 文档正文 | 重复、陈旧、无增量信息的内容 |

---

### Step 5: 持仓状态修正

对 **已持仓股票** 的相关信号，根据盈亏状态调整优先级：

| 持仓状态 | 证据要求 | 调整规则 |
|---------|-------------|---------|
| **浮亏 > 5%** | 输入持仓明确给出浮亏比例 | 该股票的**利空信号**优先级 **+1 档**（弱→中，中→强）；利好信号不变 |
| **浮盈 > 10%** | 输入持仓明确给出浮盈比例 | 该股票的**利好兑现/利空信号**优先级 **+1 档**；纯利好信号不变 |
| **正常波动** | 输入持仓显示盈亏处于常规区间 | -5% ≤ 值 ≤ 10% 时不做调整 |

**修正规则**：仅改变信号档位，不改变 confidence 数值。最终 confidence 在对应档位区间内取值。

---

### Step 6: 信号冲突仲裁

| 冲突情形 | 处理规则 |
|---------|---------|
| **同一股票：强利好 + 强利空** | 启动 `start_debate`，confidence 取两者较高值，`trigger_reason` 标注「多空信号冲突」 |
| **同一股票：强信号 + 弱信号（同向或反向）** | 以较强信号为准，忽略弱信号 |
| **同一股票：中利好 + 中利空** | 提升一档处理（中→强），启动 `start_debate` |

---

## 输出格式（严格匹配 list[WatchAiDecision] Schema）

直接输出合法 JSON array，本轮回复只能是 JSON 本身。根节点必须是数组，不能是 object。数组中每个元素对应一只 `warehouse_stocks` 中的股票，不能遗漏输入股票。
禁止输出 markdown、代码围栏、注释、解释文字或尾随逗号。

```json
[
  {{
    "stock_code": "600519.SH",
    "stock_name": "贵州茅台",
    "action": "ignore | monitor | start_debate",
    "confidence": 0.0,
    "urgency": "low | medium | high",
    "trigger_reason": "简洁的决策依据，≤120字，包含：触发信号、股票、持仓修正（如有）",
    "evidence_summary": "对去重后证据的概括，说明涉及哪些事件和关键数据",
    "debate_parameters": {{
      "trading_frequency": "day | swing | position",
      "trading_strategy": "value | trend",
      "simplified": false,
      "debate_focus": ["触发分析的核心议题"],
      "risk_notes": ["需要完整辩论验证的风险或不确定性"]
    }}
  }}
]
```

---

### 字段填充规则

| 字段 | 规则 |
|------|------|
| `action` | 三选一，禁止其他值 |
| `confidence` | 必须匹配 action：`start_debate` ≥ 0.75，`monitor` 0.50–0.75，`ignore` < 0.50 |
| `urgency` | 根据信号强度与持仓状态综合判定：强信号或持仓盈亏临界 → high；中信号 → medium；弱信号/无信号 → low |
| `trigger_reason` | 中文，≤120字，禁止编造数据，必须引用输入中的具体信息 |
| `evidence_summary` | 概括该股票去重后的证据，说明独立事件、关键行情/资金/盘口/持仓数据 |
| `debate_parameters` | `action = "start_debate"` 时必须填充；其他情况为 `null` |

### 交易偏好短代码

`trading_frequency` 只能输出 `["day", "swing", "position"]`：

- `day` = 日内交易 / Day Trading
- `swing` = 波段交易 / Swing Trading
- `position` = 中长线持有 / Position Trading

`trading_strategy` 只能输出 `["value", "trend"]`：

- `value` = 价值投资 / Value Investing
- `trend` = 趋势追踪 / Trend Following

每只股票的交易偏好来自 `warehouse_stocks[].trading_frequency_code` 和
`warehouse_stocks[].trading_strategy_code`。生成该股票的 `debate_parameters` 时必须原样使用这两个短代码。
如果只看到中文或英文长文本，按上面的映射表转换。无法可靠映射时使用默认值：`position` 和 `value`。

---

## 绝对禁令

1. **不得编造**：未在输入中出现的数据不得虚构。若新闻未提供具体数据（如成交量倍数、资金占比），信号降级一档。
2. **禁止交易结论**：不得输出「建议买入」「建议卖出」「目标价XX元」等指令。
3. **禁止重复计数**：同一事件的多篇报道只能算一个证据单元。
4. **禁止输出长交易偏好**：`trading_frequency` 和 `trading_strategy` 只能输出短代码，不能输出中文长文本或带括号的英文。
5. **禁止根节点 object**：根节点必须是 JSON array。

---

## Pydantic JSON Schema
{schema}"""


def _strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```json"):
        stripped = stripped[len("```json"):]
    elif stripped.startswith("```"):
        stripped = stripped[len("```"):]
    stripped = stripped.strip()
    if stripped.endswith("```"):
        stripped = stripped[:-len("```")]
    return stripped.strip()


def parse_watch_ai_decision(payload: str | list[dict[str, Any]]) -> list[WatchAiDecision]:
    """
    Parse and validate Watch AI JSON output.

    Args:
        payload: JSON string or already parsed JSON object.

    Returns:
        Validated Watch AI decisions.
    """
    if isinstance(payload, str):
        try:
            data = json.loads(_strip_json_code_fence(payload))
        except json.JSONDecodeError:
            logger.exception(
                "Failed to parse Watch AI JSON response",
                extra={
                    "response_length": len(payload),
                    "response_preview": payload[:WATCH_AI_RESPONSE_LOG_PREVIEW_LIMIT],
                },
            )
            raise
    else:
        data = payload
    return WATCH_AI_DECISIONS_ADAPTER.validate_python(data)


def should_launch_debate(decision: WatchAiDecision, min_confidence: float = 0.75) -> bool:
    """
    Return whether a Watch AI decision clears launch-only thresholds.

    Args:
        decision: Validated Watch AI decision.
        min_confidence: Minimum confidence required to launch.

    Returns:
        Whether the decision can launch a full debate before idempotency/cooldown checks.
    """
    return (
        decision.action == "start_debate"
        and decision.confidence >= min_confidence
        and decision.debate_parameters is not None
    )


class WatchAiGate:
    """Small adapter for Watch AI calls."""

    def __init__(self, llm_client: LlmClient):
        self.llm_client = llm_client

    async def decide(self, payload: dict[str, Any]) -> list[WatchAiDecision]:
        """
        Ask Watch AI for a structured decision.

        Args:
            payload: Structured Watch AI input assembled by the scan service.

        Returns:
            Validated Watch AI decisions.
        """
        messages = build_watch_ai_messages(payload)
        result = await self.llm_client.complete_json(messages)
        return parse_watch_ai_decision(result)


def build_watch_ai_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    """
    构建盯盘 AI 对话消息，并将稳定数据库上下文与易变网页文档分离。

    Args:
        payload: 扫描服务组装的结构化盯盘 AI 输入。

    Returns:
        用于调用盯盘 AI 大模型的对话消息。
    """
    database_context = {
        "user_id": payload.get("user_id"),
        "settings": payload.get("settings"),
        "warehouse_stocks": payload.get("warehouse_stocks", []),
        "account_summary": payload.get("account_summary"),
        "positions": payload.get("positions", []),
        "recent_debate_launches": payload.get("recent_debate_launches", []),
    }
    source_document_context = {
        "data_documents": payload.get("data_documents", []),
        "news_documents": payload.get("news_documents", []),
    }
    return [
        {
            "role": "system",
            "content": build_watch_ai_prompt(
                recent_debate_dedup_enabled=bool(
                    (payload.get("settings") or {}).get("recent_debate_dedup_enabled", True)
                ),
                recent_debate_lookback_hours=int(
                    (payload.get("settings") or {}).get("recent_debate_lookback_hours", 24)
                ),
            ),
        },
        {"role": "user", "content": _format_watch_ai_input("DATABASE_CONTEXT", database_context)},
        {"role": "user", "content": _format_watch_ai_input("SOURCE_DOCUMENT_CONTEXT", source_document_context)},
    ]


def _format_watch_ai_input(label: str, payload: dict[str, Any]) -> str:
    return f"{label}\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
