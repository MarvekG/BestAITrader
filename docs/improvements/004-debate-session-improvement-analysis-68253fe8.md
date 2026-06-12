# Debate 会话改进分析与实施方案：68253fe8-67a2-4865-b006-9d701b44d675

## 1. 文档目的

本文记录对 Debate 会话 `68253fe8-67a2-4865-b006-9d701b44d675` 的质量分析、问题定位和对应改进方案，分析目标是提升系统在 A 股的实际盈利能力。全文分两部分：**第一部分**为会话质量分析（问题与证据），**第二部分**为逐项实施方案（文件级改动细化）。

分析依据包括：该会话全部 14 条 `debate_messages`（含 reasoning 与 analysis 全文）、PM 与技术面专家收到的完整 prompt_input、该会话全部 71 条 `llm_usage_logs`，以及 orchestrator / risk_control / market_watch / experience 模块的相关代码。

本文不构成投资建议。

# 第一部分：会话质量分析

## 2. 会话概况

| 字段 | 内容 |
| --- | --- |
| 会话 ID | `68253fe8-67a2-4865-b006-9d701b44d675` |
| 股票 | `000651.SZ` 格力电器 |
| 交易频率 | 波段交易（Swing Trading） |
| 交易策略 | 成长投资（Growth Investing） |
| 会话时间 | 2026-06-11 00:40 ~ 00:54（约 14 分钟） |
| 会话状态 | `completed` |
| 最终 PM 决策 | `hold`，置信度 `0.70`，目标仓位 `19.05%` |
| 关键参数 | 止损 36.80 / 止盈 42.00 / 持有期 60 天 |
| 订单/成交 | 未生成订单 |
| LLM 用量 | 71 次调用，输入 462 万 token（缓存命中约 80%），输出 12 万 token |

流程完整：7 个第一层分析（风险/资金流/基本面/技术面/情绪/新闻/政策）→ 多空辩论（Round 1）→ 中性/保守/激进三风格辩论（Round 2.1）→ 事实仲裁 → PM 决策。

## 3. 总体结论

本次辩论的文本质量整体较高：第二轮分析师对第一轮观点有实质性反驳（激进分析师主动联网补证回击空头）、事实仲裁逐条裁决了 7 项多空分歧并列出 4 项未解决事实、PM 报告给出了止损/止盈/持有期/反证条件的完整结构。

但从盈利目标看，本次会话暴露出 **1 个已实际污染决策的致命缺陷** 和 **5 个系统性短板**：

1. **致命**：多头研究员的"每股净现金 100.50 元（股价的 263%）"是一个 10 倍算术幻觉（正确值 10.05 元 / 26.3%），事实仲裁未能发现该冲突（中性分析师明明给出了正确口径），错误数字最终进入 PM 终稿并被引用 4 次，作为"安全边际充足、无需减仓"的核心依据。
2. PM 声称"止损 36.80 已在系统中生效"，但系统不存在止损单机制，该声明与事实不符；PM 给出的所有触发条件均为纯文本，无任何组件执行。
3. 事实仲裁发现的数据缺口（SiC 量产公告、大宗交易买方结构、美的北向对比、珠海明骏贷款明细）没有补数据回路，全部以"降权处理"了结，而这些数据用系统已有工具均可获取。
4. 经验复盘工作流（experience review）从未运行（事件表 0 条）；历史会话被删除导致 PM 在不知道"当初为什么买入"的情况下决策是否止损。
5. 非 PM 角色的 `decision`/`confidence` 字段全部为空/0，无法对各角色做历史命中率校准。
6. 单次决策消耗 462 万输入 token，数据以冗长 JSON 嵌入、报告全文层层转发，存在约一半的可压缩空间。

如果只优先改进一个方向，应优先补齐**数值防线（canonical_metrics 事实底座 + 仲裁强制重算）**——本次会话证明，再好的辩论结构也挡不住一个错位小数点。

## 4. 问题与对应改进方案

### 4.1 【P0】算术幻觉穿透全链路：数值无确定性校验

**问题。** 多头研究员报告（`strategic_round_1`）声称：

> 净现金头寸 562.89 亿元。总股本 56.01 亿股，**每股净现金 = 100.50 元**。当前股价仅 38.20 元，**净现金/股价 = 263%**。

正确计算：562.89 亿 ÷ 56.01 亿股 = **10.05 元/股**；净现金/市值 = **26.3%**。错误为整 10 倍（小数点错位），且多头基于错误数字进一步推导出"剔除现金后核心业务隐含估值 ≈ 0 元""清算即可收回远超股价的价值"等结论。

**传播路径（这是比错误本身更严重的部分）：**

| 环节 | 表现 |
| --- | --- |
| 多头研究员 | 错误数字引用 6 次，并构造配套论证 |
| 中性分析师 | 给出**正确口径**："净现金/市值 = 562亿/2139亿 = 26%"——冲突已经显式存在 |
| 事实仲裁 | 裁决了 7 项分歧，**未发现这条数值冲突** |
| PM 终稿 | 错误数字引用 4 次，包括"核心理由第 1 条"与格雷厄姆安全边际栏；"562亿净现金(每股100.50元)"一句自身即自相矛盾（562/56≈10） |

**对决策的影响。** PM 的 hold 判决核心依据是"极端低估安全垫"。净现金占市值 26% 是良好但平常的水平；"263%"则是"白送公司"级别。若使用正确数字，多头安全边际论证大幅削弱，中性（减仓 20-30%）/保守（减仓 40%）方案很可能胜出。本单决策质量已被污染。

**改进方案。**

1. **canonical_metrics 事实底座（纯代码，非 LLM）**：高频派生指标（每股 X、占比、估值倍数、同比）由代码从原始财务字段统一计算一次，注入所有 agent 的 Context 并强制引用，从源头消灭算错的可能；不对自由 markdown 做正则抽数（不可维护）。
2. **事实仲裁员强制重算**：仲裁 agent 已有代码执行工具，在提示词中加入硬性规则——凡两个分析师对同一指标给出不同数值、或报告数值自相矛盾，必须先重算再裁决；禁止只裁决"叙事分歧"而跳过数值分歧。

### 4.2 【P0】决策执行闭环缺失：止损与触发条件停留在纸面

**问题。** PM 终稿写道"现有止损 36.80 元已在系统中生效"。核实结果：

- `orders` 表历史订单全部为 `market` 类型，系统**不存在止损单/条件单机制**；
- `risk_control/service.py` 仅在买入时校验"是否填写了止损价"（`require_stop_loss`），不监控持仓是否触及止损；
- `market_watch/ai_gate.py` 的止损触发情境是通用的"浮亏 < -8%"，不读取 PM 给出的具体止损价。

PM 同时给出了一组高质量、机器可执行的触发条件——"跌破 36.80 清仓""中报营收 < 0% 减仓至 10% 以下""反弹至 39 以上止损上移至 37.50"——全部只存在于 markdown 文本中，无任何组件会执行。下次盘中跌破 36.80，系统不会有任何动作。对波段交易风格，这是直接造成超额亏损的缺口。

**改进方案。**

1. 将 PM 输出 schema 中已有的 `stop_loss` / `take_profit` / `holding_horizon_days` **写到 `positions` 表新增字段上**（持仓即"当前有效纪律"的天然载体，不新建表、不加状态机）。
2. market_watch 周期扫描时逐持仓比对实时价格与止损/止盈/到期：触发后发布事件并自动发起复议辩论——**不直接下单**，是否卖出由复议辩论的 PM 决定。
3. 事件型反证条件（中报指标、新减持公告）v1 保持文本，由下一次辩论处理，不做结构化。
4. 禁止 PM 在报告中声明"止损已在系统中生效"这类未经验证的系统状态；prompt 中明确"系统只执行三个结构化字段"。

### 4.3 【P1】事实仲裁发现数据缺口后无补数据回路

**问题。** 仲裁列出 4 项"未解决事实"，处理方式全部是"PM 应核实/降权处理"，PM 实际未核实：

| 未解决事实 | 对决策的影响 | 是否可用现有工具获取 |
| --- | --- | --- |
| SiC 工厂 6 月量产（激进派联网搜索所得，未经公告核实） | 激进派核心加仓逻辑被整体降权 | 可（巨潮公告检索） |
| 16.6 亿大宗交易买方结构（机构 88% vs 机构仅 1506 万，两口径相差 90 倍） | 直接关系"37 元底部承接是否真实"，是 25 亿剩余卖压判断的关键变量 | 可（tushare 大宗交易明细） |
| 美的集团北向同期对比 | 决定北向撤退是行业性还是格力 α 恶化 | 可（000333.SZ 北向持仓） |
| 珠海明骏剩余贷款缺口 | 决定剩余 6900 万股减持是否会全额执行 | 部分可（公告检索） |

**改进方案。** 不新增节点：仲裁员已携带全量数据工具，在仲裁 prompt 中加硬性要求——拟列入"未解决事实"的每一项必须先用工具补证，补证后仍无法确认的才允许列入并注明已尝试来源。配合数据层修复（大宗交易全量聚合）消灭最大的一处口径冲突。

**附带问题：北向数据滞后 72 天。** 所有北向分析基于 2026-03-31 数据，7 个 agent 各自重复做免责声明。建议在数据层补充更新鲜的资金面替代信号：融资融券日频余额、龙虎榜、ETF 份额变动、大宗交易折价率序列。

### 4.4 【P1】学习回路从未运转：系统不会从自己的盈亏中变聪明

**问题。**

- `experience_review_events` 表为 **0 条**。复盘工作流（`experience/workflow.py`）与自动调度器（`tasks/experience_review_scheduler.py`，每日 18:30，配置已启用）均已实现，但从未产生任何复盘——根因是候选会话不存在：旧会话在满足复盘周期（5d 周期需要决策后 ≥6 个交易日）之前就被删除了，库里仅剩的 2 个会话都是当天的。
- 数据库仅剩 2 个 session，历史会话已被删除。后果在本次会话中实际发生：PM 写"无历史 PM 决策、无历史可复用规则"，但 `trade_records` 显示此前 AI 以 40.49/40.29 买入过格力——**当初买入的决策逻辑已随会话删除而丢失**，PM 是在不知道"自己为什么套牢"的情况下决定是否止损的。
- PM 主动跳过了 `recall_memory`（理由："当前证据已足够充分"）。

**改进方案。**

1. **会话删除不再摧毁复盘候选**：PM 决策快照与 session 生命周期解耦（删除会话前先落快照表），使复盘调度器始终有候选可扫。
2. 同股票历史决策沿既有 `same_stock_history` / `previous_pm_decision` 通道注入（代码已实现），快照表保证会话删除后该通道仍有数据；经验记忆的消费仍由 agent 经 `recall_memory` 自主决定，不做强制注入。
3. 止损触发、持有期到期时**即时触发** experience review（开关可控，结果在前端经验库展示触发来源），不只依赖每日定时扫描。

详细实施方案见第二部分 §4。

### 4.5 【暂不处理】非 PM 角色 decision/confidence 全空

**问题。** `debate_messages` 中所有非 PM 消息 `decision=''`、`confidence=0`。根因在 `orchestrator.py:268-288`：代码尝试从 report 对象读取 `decision`/`confidence_score` 属性，但垂直专家与辩手的输出 schema 只有 `markdown` 字段。

这不只是数据完整性问题：若每个角色每轮输出结构化方向判断 + 置信度，积累多个 session 后即可做**分角色校准统计**（如"空头研究员的看空在 60 天窗口的命中率""风险专家 45 分评级历史上对应多大回撤"），进而为 PM 裁决提供历史加权依据。

**处理决定：暂不实施。** 该方案要求所有角色改结构化输出，schema 会持续膨胀，且方向命中率校准不是当前核心盈利能力。保留问题记录，待复盘数据积累后再评估轻量方案。

### 4.6 【P2】成本与上下文工程：单次决策 462 万输入 token

**问题与量化。**

| 现象 | 证据 | 改进 |
| --- | --- | --- |
| 数据以冗长 JSON 嵌入 | 北向持仓序列逐条 `"close_price_cny": "40.23元"`，PM prompt 中"北向"出现 85 次 | 改为 CSV 风格紧凑表格，预计省 60-70% |
| 报告全文层层转发 | PM prompt 达 20 万字符；基本面专家单次调用 134k 输入、74.5k 缓存未命中（单次最贵） | 第二轮辩手与 PM 改用"论点-证据结构化摘要 + 原文引用索引" |

时延 14 分钟对盘前决策可接受（Layer 1 已并行，strategic→仲裁→PM 为必要串行），不是优先项。

### 4.7 【P2】机制问题（非 bug）

**未回应的最强反证不强制作答。** 仲裁指出经营现金流 -29.11% 是"多头论证中最明显的弱点"——多头通篇引用 2025 年报 OCF +57.93%，对 Q1 恶化只字未提（选择性引用）。建议辩论规则：仲裁标记"各方未回应的最强反证"，PM 必须逐条写明"该反证为何不改变结论"或据此下调置信度。

## 5. 优先级落地清单

| 优先级 | 改进项 | 涉及模块 | 一句话理由 |
| --- | --- | --- | --- |
| P0 | canonical_metrics 事实底座 + 仲裁员强制重算规则 | llm_engine（context 构建 + 仲裁 prompt） | 10 倍幻觉已实际污染本次决策 |
| P0 | PM 止损/止盈/持有期落地为可执行监控 | positions 扩展字段、market_watch | 跌破 36.80 系统目前不会有任何动作 |
| P1 | 仲裁员补证后再裁决 + 大宗交易数据聚合 | 仲裁 prompt、context/capital_flow | 4 项关键事实本可用现有工具查清 |
| P1 | 经验复盘自动触发 + PM 决策快照与 session 删除解耦 | experience、sessions 删除逻辑 | 系统目前零学习能力 |
| P2 | 数据紧凑化、摘要转发 | context 构建、agents/base.py | 降低成本，质量不降 |
| P2 | 最强反证强制作答 | 辩论 prompt 规则 | 长期纪律性收益 |

## 6. 本次会话值得保留的优点

为避免改进时误伤，记录本次会话中已经做对的部分：

- 第二轮辩手对第一轮有实质性交叉反驳，激进分析师主动联网补证（SiC 量产线索即来自于此，虽未核实但行为方向正确）；
- 事实仲裁对"减持 38% 的双向表述""扣非 -0.27% 的定性""超卖信号在减持压制下的有效性折扣"等裁决质量高；
- PM 报告结构完整：投资大师多框架裁决表、机会成本对比（持有股息 ~14,600 元/年 vs 现金收益 ~10,880 元/年）、HOLD 反证与卖出反证双向列举、反锚定纪律声明；
- 全体分析师对"36.80 硬止损"达成唯一无分歧结论，且 PM 明确以此为纪律底线（缺的是执行层，见 4.2）。

---

# 第二部分：实施方案细化

本部分对第一部分的各问题逐项给出：改动文件清单、具体修改内容（表结构 / Pydantic schema / 代码骨架 / prompt diff）、验证方法。所有行号基于 2026-06-11 的 main 分支。以下 § 编号均指第二部分内部章节。

实施前提（已核实的代码事实）：

- 所有 agent 经 `agents/base.py` 的 `get_tools()`（base.py:99-104）默认携带全量工具，**包括 `execute_python_sandboxed`（tools.py:444）和 `query_and_calculate`（tools.py:933）**——事实仲裁员已有计算能力，缺的只是 prompt 强制要求。
- 除 PM 外所有 agent 的 `get_output_model()` 返回 `str`（specialists.py / strategic.py），`models.py` 中的 `AnalystOutput`、`StrategicReport` 当前**无人使用**。
- `persist_agent_report`（orchestrator.py:227-326）已支持从 Pydantic 模型提取 `decision`/`confidence`（268-296 行），只是 str 输出走不到该分支。
- 经验复盘调度器已实现且启用（`tasks/experience_review_scheduler.py`，system_settings 中 `enabled: true, 18:30`），0 条记录的根因是会话删除（sessions.py:219-229 直接删除 `DebateMessage`）。
- 表结构通过 `Base.metadata.create_all`（core/init_db.py:27）创建，无 Alembic；新增表只需新增 model 并注册到 `models/__init__.py`，**修改已有表列需要手写 SQL 迁移脚本**（放 `backend/scripts/`）。

---

## 1.【P0】数值校验双层防线

### 1.1 第一层：canonical_metrics 事实底座（确定性计算，杜绝源头）

**目标**：每股净现金、净现金/市值、PE、PB、股息率等高频派生指标由代码算一次，所有 agent 引用同一份数字，从源头消灭"10.05 算成 100.50"。

**新增** `backend/app/ai/llm_engine/context/canonical_metrics.py`：

```python
from dataclasses import dataclass

@dataclass
class CanonicalMetric:
    key: str            # e.g. "per_share_net_cash"
    label_cn: str       # "每股净现金"
    value: float | None
    unit: str           # "元/股" / "%" / "倍"
    formula: str        # "净现金562.89亿 / 总股本56.01亿股"

def build_canonical_metrics(db, stock_code: str) -> dict:
    """从已有 context 数据源计算派生指标，返回 {"table_markdown": str, "metrics": {key: value}}。

    必算指标（v1）：
    - market_cap          = close * total_shares
    - per_share_net_cash  = (money_funds - st_borrow - lt_borrow) / total_shares
    - net_cash_to_mcap    = net_cash / market_cap
    - pe_ttm / pb / dividend_yield（直接取行情字段，但带上"取数日期"）
    - per_share_dividend, payout_ratio
    每个指标必须携带 formula 字段（人可读的算式），缺原始字段时 value=None 并注明缺哪个字段。
    """
```

**接线**：

- `context/service.py` 组装 static_context 时调用，挂为 `static_context["canonical_metrics"]`（markdown 表 + 机器可读 dict 两种形态都放）。
- `templates.py` 的 `COMMON_AGENT_SYSTEM_PROMPT_CN`（templates.py:11，EN 版 137 同步）增加一条全局约束：

```
## 派生指标引用纪律
Context 中的 `canonical_metrics` 是唯一可信的派生指标口径（每股X、占比、估值倍数等）。
1. 引用这些指标时必须使用 canonical_metrics 的数值，禁止自行心算。
2. 若需要 canonical_metrics 之外的派生数值，必须调用 `execute_python_sandboxed`
   或 `query_and_calculate` 工具计算，并在报告中给出算式（A/B=C 形式）。
3. 报告中任何"每股 X 元""占比 X%"类数字，若与 canonical_metrics 冲突，以 canonical_metrics 为准。
```

### 1.2 第二层：仲裁员强制重算（裁决数值冲突）

**修改** `templates.py:1001` `SYSTEM_PROMPT_FACT_ARBITRATION_CN`（EN 版同步），在"仲裁原则"后追加：

```
数值仲裁规则（强制）：
5. 凡两个及以上 Agent 对同一指标给出不同数值，或同一报告内数值自相矛盾
   （如"562亿净现金"与"每股100.50元"无法对应总股本），你必须调用
   execute_python_sandboxed 重算，给出唯一正确值。禁止"双方各有道理"式裁决数值。
6. 对每个报告中权重最高的 3 个派生数值（每股X、占比、估值倍数），即使无冲突也必须抽查重算。
```

并在输出模板（templates.py:1012-1032）增加一节：

```
## 数值核验

| 指标 | 各方口径 | 重算值（含算式） | 裁决 |
| --- | --- | --- | --- |
```

**验证方法**：单测 `build_canonical_metrics`（含每股净现金 10.05 元案例）；回归跑一次格力辩论，确认 PM 终稿中"每股净现金"为 10.05 元口径，且仲裁报告含数值核验表。

---

## 2.【P0】PM 决策纪律落地为可执行监控

**为什么需要持久化载体**：执行方（market_watch 定时扫描）与产生方（辩论会话）是两个独立流程，扫描时需要机器可读的"当前每个持仓的止损/止盈价"。现状这两个数字只存在于 `debate_messages.analysis` 的 JSON 里——查询别扭、会话删除即丢失、同股多次决策无"当前有效"概念。

**不新建表**：`positions` 本来就是"每账户每股票一行"，天然承载"当前有效纪律"，新决策直接覆盖旧值，无需状态机。

### 2.1 扩展 positions 表

**修改** `app/models/position.py` 增加四列（需手写 SQL 迁移脚本放 `backend/scripts/`，`create_all` 不会改已有表）：

```python
stop_loss        = Column(DECIMAL(10, 4), nullable=True)
take_profit      = Column(DECIMAL(10, 4), nullable=True)
horizon_deadline = Column(DateTime, nullable=True)   # PM 决策时间 + holding_horizon_days
pm_session_id    = Column(UUID, nullable=True)        # 给出该纪律的会话，便于追溯与复盘
```

不新增 PMDecision 字段：`stop_loss` / `take_profit` / `holding_horizon_days` 三个结构化字段已存在（models.py:63-65），直接复用。事件型反证条件（"中报营收<0%减仓"）v1 保持文本，由下一次辩论处理，不做结构化。

### 2.2 PM prompt 修正

**修改** `templates.py:1039` PM prompt（CN/EN 同步）：增加硬规则「禁止声称任何止损/监控"已在系统中生效"——系统只执行你输出的 `stop_loss` / `take_profit` / `holding_horizon_days` 三个结构化字段，markdown 文本中的纪律不会被执行」。直接针对本次会话 PM 虚构"止损已生效"的问题。

### 2.3 决策落地：同步到持仓

**新增** `backend/app/trading/pm_rules.py`：

```python
def sync_pm_discipline_to_position(db, *, session_id, user_id, stock_code, decision: dict) -> None:
    """PM 决策持久化后调用：把 stop_loss/take_profit/horizon_deadline 写到对应 position 行。
    当前无持仓（decision=buy 未成交）时跳过，待成交建仓后由 trading_service 回填。
    写入前做合理性检查（buy/hold 时应满足 stop_loss < 现价 < take_profit），
    异常仅记 warning 日志，不阻塞。"""

def evaluate_position_disciplines(db, *, user_id) -> list[dict]:
    """对该用户所有带止损/止盈的持仓：取最新价（复用 tools.py:90 _resolve_latest_stock_price
    的数据源），判定 跌破止损/触及止盈/持有期到期，返回触发列表。纯确定性，无 LLM。"""
```

**修改** `orchestrator.py` `portfolio_management`（1318-1333 行），在 `persist_agent_report` 之后调用 `sync_pm_discipline_to_position`。

### 2.4 触发后的行为：只启动复议辩论，不直接下单

**修改** `market_watch/service.py` `scan_market_watch`（service.py:127）：在进入 LLM watch gate 之前调用 `evaluate_position_disciplines`。触发（跌破止损/触及止盈/持有期到期）后的行为固定：**发布 MarketWatchEvent（标注"PM止损/止盈/到期触发"）+ 走既有 `_primary_debate_launch` 路径自动发起复议辩论**。market_watch 不执行卖出——是否卖出、卖多少由复议辩论的 PM 决定，交易仍走既有风控与订单链路。

同股重复触发用既有 `audit_is_in_cooldown` 冷却机制防抖；触发记录写入已有 `market_watch_events` 表。止损触发属于时间敏感动作，需确认 market_watch 盘中扫描间隔至少为分钟级。

**验证方法**：单测 `evaluate_position_disciplines`（mock 跌破/突破/到期三种）；集成测试：给测试持仓写 stop_loss=999 跑一次 scan，确认产生事件并自动启动复议辩论。

---

## 3.【P1】仲裁补证（节点内完成，不新增节点、不改输出格式）

### 3.1 仲裁员自主补证（仅改 prompt，零代码）

仲裁员经 `get_tools()`（base.py:99-104）已携带全量数据工具（`query_stock_data`、公告检索、`execute_python_sandboxed` 等）——本次会话中激进分析师主动联网补证已证明 agent 有此行为能力，缺的只是对仲裁员的硬性要求。

**修改** `templates.py:1001` `SYSTEM_PROMPT_FACT_ARBITRATION_CN` 仲裁原则追加：

```
7. 对你拟列入"未解决事实"的每一项，必须先尝试用数据工具补证
   （公告检索 / 大宗交易明细 / 同行对比数据 / 融资融券），把补证结果写入裁决依据。
   只有补证后仍无法确认的才允许列入"未解决事实"，并在表中注明"已尝试的来源与结果"。
```

输出模板的"未解决事实"表（templates.py:1022-1026）增加一列「已尝试补证」。本次会话的 4 项缺口中，SiC 公告、大宗买方结构、美的北向三项都能用现有工具拿到，本不该流到 PM 手里。

成本影响：仲裁阶段增加若干次工具调用（结果经 `tool_output_summarizer` 控制长度），换取"未解决事实"数量下降；补证结果随仲裁报告 markdown 自然进入 PM 上下文，PM 侧零改动。

### 3.2 大宗交易买方结构聚合（数据层根因修复，直接消灭本次"88% vs 1506万"冲突）

**修改** `context/capital_flow.py` `_get_block_trade`（capital_flow.py:167-242）：当前 `limit(10)` 且只展示 5 笔明细、买卖方名称截断 30 字符，是仲裁认定"无法验证全貌"的直接原因。改为：

- 窗口扩到减持公告日以来（或近 30 个自然日）全量记录；
- 明细保留 10 笔，**新增聚合统计**：`buyer_type_breakdown`（按"机构专用/营业部/其他"分类的成交额与占比，分类规则：buyer 字段含"机构专用"→机构）、`total_amount`、`avg_discount_pct`（成交价 vs 当日收盘折价率均值）。

**验证方法**：以格力 6 月数据跑 `_get_block_trade`，确认输出含全量买方结构占比；回归一次辩论，确认"未解决事实"表带补证记录。

---

## 4.【P1】复盘闭环与决策历史保全

### 4.1 PM 决策快照与会话删除解耦

**新增** `backend/app/models/pm_decision_snapshot.py`：

```python
class PMDecisionSnapshot(Base):
    __tablename__ = "pm_decision_snapshots"
    snapshot_id  = Column(UUID, primary_key=True, default=uuid.uuid4)
    warehouse_id = Column(Integer,
        ForeignKey("stock_warehouse.id", ondelete="CASCADE"),
        index=True, nullable=False)   # 股票移出仓库时级联删除快照
    user_id      = Column(Integer, index=True)
    stock_code   = Column(String(10), index=True)
    session_id   = Column(UUID, nullable=True)   # 不带外键：会话删除后快照仍保留（这正是本表目的）
    decision     = Column(String(10))
    confidence   = Column(Float)
    stop_loss    = Column(DECIMAL(10, 4))
    take_profit  = Column(DECIMAL(10, 4))
    holding_horizon_days = Column(Integer)
    verdict_summary      = Column(Text)
    investment_plan      = Column(Text)
    created_at   = Column(DateTime, index=True)
```

**自动清理**：复用 market_watch 审计清理的既有模式（`audit.py:66 cleanup_old_events`，保留 90 天）——新增 `cleanup_old_snapshots(retention_days=90)`，挂在经验复盘每日 18:30 的调度 tick 末尾执行，自动删除 3 个月前的快照。

**修改** `api/endpoints/sessions.py` 两处删除路径（batch_delete_sessions:219-229 与单删端点）：在 `db.query(DebateMessage)...delete()` **之前**：

```python
pm_rows = db.query(DebateMessage).filter(
    DebateMessage.session_id.in_(to_delete_ids),
    DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER,
).all()
for row in pm_rows:
    db.add(PMDecisionSnapshot.from_debate_message(row))
```

**修改** `orchestrator.py` 的 `_get_previous_pm_decision`（906）与 `_get_same_stock_history`（722）：查询 DebateMessage 之后 UNION 快照表，保证"上一轮为什么买"在会话删除后仍可注入。

### 4.2 事件驱动复盘（开关控制，结果前端可见）

**触发**：`evaluate_position_disciplines` 判定止损触发或持有期到期时，调用 `experience_service` 的既有入口（复用 `api.py:174 analyze_debate_with_experience` 的 service 层函数），按 `positions.pm_session_id` 为对应会话排一次即时复盘。止损被打掉的单子是最有学习价值的样本，不应等到 60 天周期。

**开关**：`experience_review_scheduler_config`（system_settings，已有 GET/PUT `/experience/scheduler-config` API 管理，api.py:127-148）增加字段 `event_triggered_review_enabled: bool`（默认 true），关闭后止损/到期不触发即时复盘，只保留每日定时扫描。

**前端展示**：事件驱动复盘产生的记录写入既有 `experience_review_events` 与经验库（前端已有经验库列表页，走 `/experience/library`）；记录 payload 增加 `trigger_source: "stop_loss" | "horizon_expired" | "scheduled"`，经验库列表增加"触发来源"列与筛选，让用户能直接看到"哪些复盘是被止损打出来的"。

经验的消费仍走既有 `recall_memory` 工具，由 agent 自主决定是否检索，不做强制注入。

**验证方法**：删除一个测试会话→确认快照存在且新辩论的 `same_stock_history` 能读到；给测试持仓写一个必触发的止损价跑一次判定→确认 experience_review_events 产生记录。

---

## 5.【P2】成本优化

| 改动 | 文件:位置 | 预期收益 |
| --- | --- | --- |
| context 序列化去缩进：`stable_json_dumps(static_context, indent=2)` → `indent=None` | base.py:211-220 | 输入 token ↓ 约 8-12%（纯空白） |
| 时间序列预渲染为 CSV 行（北向、资金流 20 日、K 线 30 日），不再以逐字段 JSON 输出 | context/capital_flow.py:57/327、context/technical.py | 相关段落 ↓ 60-70% |

第二轮辩手与 PM 改用"摘要 + 引用索引"传递上层报告属于更大的重构，本期不做。

---

## 6.【P2】机制类修改（仅 prompt）

**最强反证强制作答**：仲裁输出模板增加一节 `## 各方未回应的最强反证`（每方 1 条）；PM 必填检查项增加一行：「对每条未回应反证，必须写明"该反证为何不改变结论"或据此下调置信度，禁止跳过」。本次会话中"多头无视经营现金流 -29%"即属此类。仅 prompt 改动，无代码。

---

## 7. 实施顺序与 PR 切分

| PR | 内容 | 依赖 | 核心验收 |
| --- | --- | --- | --- |
| PR-1 | §1 数值双层防线 + §3 仲裁补证与大宗交易聚合 + §6 最强反证（三者主体都是 prompt/context 改造） | 无 | 格力案例回归：错误数字被仲裁重算修正、仲裁报告含数值核验表、"未解决事实"带补证记录 |
| PR-2 | §2 止损/止盈落地 positions + market_watch 前置检查 | 无 | 持仓行带 PM 止损价；scan 触发止损产生事件并启动复议辩论 |
| PR-3 | §4 快照解耦（含外键级联与 90 天自动清理） + 事件驱动复盘（开关 + 前端触发来源展示） | PR-2（触发源） | 删会话后历史可查；移出股票仓库快照级联删除；止损触发产生复盘事件且前端可见 |
| PR-4 | §5 成本优化 | 无（独立） | 同一股票回归会话输入 token 对比下降 ≥20% |

每个 PR 跑一次完整辩论回归（可用 601919.SH 小市值持仓会话），对比 `llm_usage_logs` 的 token 与辩论报告质量。`pm_decision_snapshots` 新表通过 `scripts/run_init_db.py` 创建；`positions` 加列需手写 SQL 脚本（见 §2.1）。
