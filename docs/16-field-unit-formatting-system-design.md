# 字段单位补齐系统设计

## 背景

当前 Debate 和 AI context 中已经开始把数值字段格式化为 `数值+单位`，例如 `38.46元`、`-4.85%`、`14843.48万元`。这样可以减少 LLM 对数值口径的误解，但现有实现分散在 `fundamental.py`、`technical.py`、`capital_flow.py`、`financial.py` 等上下文构造文件中，存在几个问题：

- 字段单位规则散落在多个 context 文件里，新增字段时容易遗漏。
- 数据源原始字段语义和标准字段单位展示混在一起，容易在展示层掩盖源头映射错误。
- 同一标准字段在不同 context 里可能出现不同单位或不同缩放口径。
- 计算链路需要原始数值，LLM 展示链路需要带单位字符串，二者边界不够清晰。

近期发现的 `gross_margin` 问题说明，单位补齐不能只在展示层做兜底：Tushare `fina_indicator.gross_margin` 官方含义是“毛利”绝对金额，`grossprofit_margin` 才是“销售毛利率”。如果源头把二者都映射为标准字段 `gross_margin`，后续再补 `%` 就会得到 `11779497981%` 这种错误上下文。因此，系统必须区分“源数据字段语义归一化”和“标准字段单位展示”。

## 目标

- 为 LLM context 提供统一、可审计的字段单位补齐入口。
- 让数据源插件显式声明原始字段语义、尺度和标准字段映射。
- 让标准表字段通过统一 JSON 配置声明展示单位、缩放和精度。
- 保持数据库和计算链路使用标准数值，不把带单位字符串写入数据库。
- 支持中英文单位国际化，单位文案继续走 `i18n_service.t(...)`。
- 避免通过异常值判断、关键词匹配或展示层猜测来修复源字段语义问题。

## 非目标

- 不改变现有数据库表结构。
- 不把单位字段写入业务数据 payload，例如不新增 `amount_unit`、`price_unit`、`price_source` 等 LLM 字段。
- 不让风控、交易、估值、指标计算直接消费带单位字符串。
- 不做基于字段名关键词的自动单位推断。
- 不把数据质量异常隐藏在格式化函数里。

## 核心原则

- 源数据插件负责原始字段语义和字段映射；必要的源数据尺度归一化在对应采集函数中显式处理。
- 标准字段单位系统负责 `标准表名 + 标准字段名 + 标准数值` 到 `带单位展示值` 的转换。
- 字段单位补齐只发生在 LLM context 边界，不发生在落库前，也不发生在交易和风控计算链路中。
- 单位配置使用标准字段名，不使用 Tushare、Akshare 等原始字段名。
- 单位后缀必须使用 `units.*` i18n key，不硬编码中文或英文单位。
- 对不存在单位配置的字段保持原值，不做猜测。
- 对源字段同名但语义不同的情况，必须在插件映射层解决。

## 总体架构

推荐拆成两层：

```text
原始数据源字段
  -> 数据源插件 column mapping / 采集函数显式归一化
  -> 标准表字段 + 标准数值
  -> table field unit formatter
  -> LLM context 带单位展示值
```

对应职责如下：

- `column_mapping.json`：数据源原始字段名映射到标准字段名。
- `table_field_units.json`：标准表字段展示单位、缩放和精度。
- `field_units.py`：统一格式化入口，接收表名、字段名和值，返回带单位展示值。

这样既保留数据源插件的可扩展性，也避免 AI context 直接依赖某个数据源插件。

## 目录设计

建议新增或调整以下文件：

```text
backend/app/data/ingestors/plugins/
  column_mapping.json

backend/app/data/metadata/
  table_field_labels.json
  table_field_units.json
  field_units.py
```

现有 `column_mapping.json` 和 `table_field_labels.json` 继续保留。新增 `table_field_units.json` 后，LLM context 统一通过 `field_units.py` 格式化标准字段。

## 源字段映射与尺度归一化

数据源插件必须先把原始字段转换成标准字段。以 Tushare 财务指标为例：

```json
[
  {
    "source": "tushare_fina_indicator",
    "table": "data.financial_indicator",
    "source_field": "gross_margin",
    "standard_field": "gross_profit"
  },
  {
    "source": "tushare_fina_indicator",
    "table": "data.financial_indicator",
    "source_field": "grossprofit_margin",
    "standard_field": "gross_margin"
  }
]
```

注意：现有 `column_mapping.json` 的实现约定仍是 `source_field -> standard_field`。上面的对象形式只用于设计文档说明方向，避免把 key/value 误读为标准字段到源字段。

含义如下：

- Tushare `gross_margin` 是“毛利”绝对金额，应映射为标准字段 `gross_profit`。
- Tushare `grossprofit_margin` 是“销售毛利率”，应映射为标准字段 `gross_margin`。

如果数据源原始值存在尺度差异，应该在对应采集函数中显式处理。例如某数据源金额单位是万元，但标准字段要求数据库存元，则在采集函数写入前乘以 `10000`。这类转换不再单独做 JSON 配置，避免引入额外维护成本。

采集函数只负责把源数据转为标准数据，不负责给 LLM 拼接单位字符串。

## 标准字段单位配置

`table_field_units.json` 使用标准表名和标准字段名配置展示单位。

示例：

```json
{
  "data.financial_indicator": {
    "gross_profit": {
      "unit": "units.cny",
      "display_scale": 1,
      "precision": 2
    },
    "gross_margin": {
      "unit": "units.percent",
      "display_scale": 1,
      "precision": 2
    },
    "net_profit_dedt_yoy": {
      "unit": "units.percent",
      "display_scale": 1,
      "precision": 2
    }
  },
  "data.stock_money_flow": {
    "net_inflow_main": {
      "unit": "units.ten_thousand_cny",
      "display_scale": 0.0001,
      "precision": 2
    }
  },
  "data.stock_realtime_market": {
    "current_price": {
      "unit": "units.cny",
      "display_scale": 1,
      "precision": 2
    },
    "change_percent": {
      "unit": "units.percent",
      "display_scale": 1,
      "precision": 2
    }
  }
}
```

字段说明：

- `unit`：单位 i18n key，例如 `units.cny`、`units.percent`、`units.ten_thousand_cny`。
- `display_scale`：展示缩放。数据库存元但展示万元时使用 `0.0001`。
- `precision`：展示小数位。
- `mode`：可选。未来可支持 `number`、`percent`、`ratio_as_percent`、`date` 等格式模式。
- `nullable`：可选。为空值时默认返回 `None`。

## 格式化接口

只保留一个公开入口：

```python
def format_payload_values(
    table_name: str,
    payload: Any,
    *,
    language: str | None = None,
) -> Any:
    """按标准表字段单位配置格式化嵌套 payload。"""
```

接口行为：

- 找到字段单位配置时，返回 `数值+本地化单位`。
- 找不到字段单位配置时，返回原值。
- `None`、空字符串、非数值字段默认返回原值。
- 数字字符串只按原始标准值解析，不反解析已经带单位的展示字符串。
- 单位后缀通过 `i18n_service.t(unit_key, language=language)` 获取。

## 插件绑定方式

插件仍然保留自己的源字段配置，但不直接承担所有 LLM 展示格式化职责。

建议插件或采集器保留一个清晰能力：

```python
class DataSourcePlugin:
    def standardize_field(self, table_name: str, source_field: str, value: Any) -> tuple[str, Any]:
        """把源字段和值转为标准字段和值。"""
```

如果某个源字段需要尺度转换，转换逻辑直接写在对应采集函数中，并用测试固定口径。例如源数据返回万元但标准字段需要元，采集函数在组装标准 payload 前显式乘以 `10000`。

LLM context 不直接调用数据源插件。LLM context 只调用：

```python
format_payload_values("data.financial_indicator", record)
```

这样可以保证同一标准字段无论来自哪个数据源，展示单位都一致。

## 与现有代码的关系

`backend/app/data/metadata/field_units.py` 是字段单位补齐的唯一实现入口，不再保留独立的 `backend/app/ai/llm_engine/context/formatting.py`。

`field_units.py` 负责：

- 读取 `table_field_units.json`。
- 根据 `table_name` 和 payload 内的标准字段名生成 LLM 展示值。
- 使用 `i18n_service.t(unit_key, language=language)` 获取单位后缀。
- 处理数字格式化、精度、展示缩放和空值。
- 在迁移完成后删除旧 `context/formatting.py`，避免两套单位格式化逻辑并存。

现有 context 文件应逐步从手写单位迁移为统一调用：

```python
format_payload_values("data.financial_indicator", data)
format_payload_values("data.stock_realtime_market", data)
format_payload_values("data.stock_money_flow", data)
```

对于非数据库标准表生成的运行时字段，可以新增虚拟表名，例如：

```text
portfolio.account
portfolio.position
portfolio.risk_summary
```

这些虚拟表名同样写入 `table_field_units.json`，避免 PM context 继续手写单位。

## 数据流示例

Tushare 财务指标数据：

```text
source field: gross_margin = 11779497981.0
source plugin mapping: gross_margin -> gross_profit
canonical record: gross_profit = 11779497981.0
unit config: data.financial_indicator.gross_profit -> units.cny
LLM context: 毛利 = 11779497981元
```

```text
source field: grossprofit_margin = 27.4158
source plugin mapping: grossprofit_margin -> gross_margin
canonical record: gross_margin = 27.4158
unit config: data.financial_indicator.gross_margin -> units.percent
LLM context: 毛利率 = 27.42%
```

资金流数据：

```text
canonical record: net_inflow_main = -148434700
unit config: data.stock_money_flow.net_inflow_main -> units.ten_thousand_cny, display_scale=0.0001
LLM context: 主力净流入 = -14843.47万元
```

## 迁移计划

### 阶段 1：配置与工具落地

状态：已完成。

工作项：

- 新增 `backend/app/data/metadata/table_field_units.json`。
- 新增 `backend/app/data/metadata/field_units.py`。
- 在 `field_units.py` 中实现字段单位配置加载、数字格式化、单位 i18n 和反解析。
- 给 `data.financial_indicator`、`data.stock_realtime_market`、`data.stock_money_flow`、`data.stock_block_trade` 增加第一批单位配置。
- 添加配置加载和单字段格式化测试。

验收标准：

- `format_payload_values("data.financial_indicator", {"gross_margin": 27.4158})` 返回 `{"gross_margin": "27.42%"}`。
- `format_payload_values("data.financial_indicator", {"gross_profit": 11779497981})` 返回带 `元` 的展示值。
- 未配置字段保持原值。
- 中英文单位由 `settings.SYSTEM_LANGUAGE` 或传入 `language` 控制。

### 阶段 2：采集函数源字段口径收敛

状态：已完成。

工作项：

- 在 Tushare ingestor 中显式处理必要的源字段尺度归一化。
- 保持 `column_mapping.json` 专注字段名映射。
- 对 `gross_margin -> gross_profit`、`grossprofit_margin -> gross_margin` 增加固定测试。

验收标准：

- 同一标准字段在 DB 中只有一种规范数值口径。
- 源字段尺度转换发生在采集函数落库前，不发生在 LLM context 里。
- 多数据源同字段最终落到相同标准单位口径。

### 阶段 3：Context 文件逐步迁移

状态：已完成。

迁移顺序：

- `financial.py`
- `fundamental.py`
- `technical.py`
- `capital_flow.py`
- `orchestrator.py` 中 PM portfolio context

迁移方式：

- 先保留现有输出结构。
- 用 `format_payload_values()` 替换局部手写的单位拼接和百分比格式化。
- 每迁移一个 context，跑对应近邻测试。
- 全部迁移完成后删除旧 `backend/app/ai/llm_engine/context/formatting.py`。

验收标准：

- LLM 输出字段值仍直接带单位。
- 不新增额外 `unit` 字段。
- 风控和交易测试不受格式化字符串影响。
- `pytest backend/tests/test_llm_orchestrator.py backend/tests/test_financial_standard_keys.py backend/tests/test_ai_context_service.py -q` 通过。

### 阶段 4：覆盖检查与回归防线

状态：待实施。

工作项：

- 增加单位配置覆盖检查脚本或测试。
- 对关键表统计未配置但高频进入 LLM context 的数值字段。
- 对存在同名异义风险的源字段增加 mapping 测试。

验收标准：

- 新增数据源字段时，如果进入 LLM context，应能快速发现缺失单位配置。
- `gross_margin` 类同名异义字段必须在插件映射层有测试。
- `git diff --check` 和相关后端测试通过。

## 测试策略

单元测试：

- `format_payload_values()` 对金额、百分比、万元、股数、手数、倍数、点数做覆盖。
- `format_payload_values()` 对嵌套字典和列表做覆盖。
- 未配置字段保持原值。
- i18n 单位中英文切换正确。

插件映射测试：

- Tushare `gross_margin` 映射到 `gross_profit`。
- Tushare `grossprofit_margin` 映射到 `gross_margin`。
- Tushare 资金流金额字段按标准口径归一化。
- Akshare/Tushare 同一标准字段最终落库口径一致。

Context 回归测试：

- `financial_indicator_latest` 中 `gross_margin` 展示为百分比。
- `financial_indicator_latest` 中 `gross_profit` 不展示为百分比。
- 实时行情价格展示为元，涨跌幅展示为百分比。
- 资金流金额展示为万元时已按配置缩放。
- PM 持仓上下文金额、股数和仓位比例直接带单位。

真实数据验证：

- 抽样 `000651.SZ`、`000333.SZ`、`600519.SH`、`300750.SZ`、`601318.SH` 等不同类型股票。
- 验证毛利和毛利率分离。
- 验证银行、保险等无毛利率字段的行业不被强制补错误单位。

## 风险与处理

风险：配置过多，维护成本上升。

处理：只对进入 LLM context 的字段先做配置，后续按上下文覆盖逐步补齐。

风险：采集函数源字段尺度转换和展示缩放混淆。

处理：采集函数转换必须有就近注释和测试，展示缩放只允许写在 `table_field_units.json` 的 `display_scale` 中。

风险：格式化后的字符串进入计算链路。

处理：格式化只在 context 构造边界调用，风险、交易、组合估值继续读取原始 DB 数值。

风险：已经格式化过的字符串被重复送入单位格式化入口。

处理：`format_payload_values()` 只在 context 输出边界调用一次；计算链路和中间过程保留原始数值。

## 验收标准

- 新增 `table_field_units.json` 并能通过统一函数格式化标准字段。
- 数据源插件不再把源字段单位问题留给展示层猜测。
- `gross_profit` 和 `gross_margin` 在 Tushare 财务指标中语义分离。
- LLM context 中关键金额、价格、股数、比例字段直接带单位。
- 不新增额外单位字段。
- 全量后端测试通过。
- 至少 5 只真实股票的财务上下文验证通过。
