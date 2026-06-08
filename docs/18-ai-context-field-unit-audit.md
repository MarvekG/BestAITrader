# AI Context 字段单位取样审计

## 背景

本记录汇总一次只读取样分析，目标是确认 AI Context 中哪些数值字段仍缺少单位，哪些字段应该从 `元` 缩放为 `万元` 或 `亿元` 后再展示给 LLM。

本次没有修改代码或数据库，只在 Docker 后端容器内构建 AI Context，并对照数据库原始值。

## 取样范围

取样股票：

| 股票代码 | 股票名称 |
| --- | --- |
| `600519.SH` | 贵州茅台 |
| `000651.SZ` | 格力电器 |
| `601318.SH` | 中国平安 |
| `000725.SZ` | 京东方A |
| `000858.SZ` | 五粮液 |
| `601888.SH` | 中国中免 |

数据库可用样本量：

| 表 | 记录数 |
| --- | ---: |
| `data.stock_basic` | 5518 |
| `data.financial_indicator` | 1599 |
| `data.stock_valuation_history` | 133017 |
| `data.northbound_data` | 25574 |

本次重点检查的上下文路径：

| AI Context 路径 | 构建入口 |
| --- | --- |
| `snapshot.valuation` | `backend/app/ai/llm_engine/context/fundamental.py` |
| `snapshot.northbound` | `backend/app/ai/llm_engine/context/fundamental.py` |
| `snapshot.flow.northbound` | `backend/app/ai/llm_engine/context/capital_flow.py` |
| `snapshot.financial_statements.financial_indicator_latest` | `backend/app/ai/llm_engine/context/financial.py` |
| `history.financial_trend` | `backend/app/ai/llm_engine/context/fundamental.py` |
| `signals.flow.*` | `backend/app/ai/llm_engine/context/capital_flow.py` |

## 当前单位配置状态

统一单位格式化入口在 `backend/app/data/metadata/field_units.py`，配置文件是 `backend/app/data/metadata/table_field_units.json`。

当前关键缺口：

| 配置段 | 当前状态 |
| --- | --- |
| `fundamental.valuation` | `total_mv`、`float_mv` 配置为 `元`，未缩放 |
| `fundamental.northbound_flow` | 只覆盖 `age_days`、持股数、持股比例、涨跌幅、净买入股数，未覆盖 `*_10k_cny` 金额字段 |
| `capital_flow.northbound` | 未覆盖 `net_buy_amount` |
| `data.financial_indicator` | 主要只覆盖同比、环比、增长率字段，大量金额、每股、百分比、天数、次数字段未覆盖 |
| `fundamental.financial_trend` | 只覆盖增长率，未覆盖 `roe`、`gross_margin`、`net_margin`、`debt_to_asset` |
| `capital_flow.dragon_tiger` | 空配置 |
| `capital_flow.block_trade` | 只配置 `volume`，未覆盖 `amount`、`total_amount`、`premium_rate` |

## VALUATION 结论

`StockValuationHistory.total_market_value` 和 `circulating_market_value` 的模型注释是 `元`。采集逻辑也确认 Tushare `total_mv/circ_mv` 原始单位为 `万元`，入库前乘以 `10000` 转为 `元`。

相关代码：

| 文件 | 说明 |
| --- | --- |
| `backend/app/data/ingestors/plugins/tushare_ingestor.py` | `total_market_value`、`circulating_market_value` 入库前乘以 `10000` |
| `backend/app/models/data_storage.py` | `StockValuationHistory` 字段注释为 `元` |
| `backend/app/ai/llm_engine/context/fundamental.py` | AI Context 输出 `total_mv`、`float_mv` |

实际样本：

| 股票 | 当前 AI Context | 原始元值 | 建议显示 |
| --- | ---: | ---: | ---: |
| 贵州茅台 | `1602492105138元` | 1602492105138 | `16024.92亿元` |
| 格力电器 | `215430064799元` | 215430064799 | `2154.30亿元` |
| 中国平安 | `972380375132元` | 972380375132 | `9723.80亿元` |
| 京东方A | `207077793878元` | 207077793878 | `2070.78亿元` |
| 五粮液 | `319650419212元` | 319650419212 | `3196.50亿元` |
| 中国中免 | `118538298540元` | 118538298540 | `1185.38亿元` |

建议：

| 字段 | 当前配置 | 建议配置 |
| --- | --- | --- |
| `fundamental.valuation.total_mv` | `units.cny`，`display_scale=1` | `units.hundred_million_cny`，`display_scale=0.00000001` |
| `fundamental.valuation.float_mv` | `units.cny`，`display_scale=1` | `units.hundred_million_cny`，`display_scale=0.00000001` |

### VALUATION 修复方案

只需要修改 `backend/app/data/metadata/table_field_units.json`，不改数据库字段和采集逻辑。

建议把 `fundamental.valuation` 改成：

```json
{
  "fundamental.valuation": {
    "dividend_yield": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "total_mv": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
    "float_mv": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2}
  }
}
```

预期效果：

| 字段 | 修复前 | 修复后 |
| --- | --- | --- |
| `total_mv` | `1602492105138元` | `16024.92亿元` |
| `float_mv` | `1602492105138元` | `16024.92亿元` |

## NORTHBOUND 结论

`NorthboundData.net_buy_amount` 的入库逻辑是 `net_buy_volume * close_price`，单位为 `元`。`hold_value` 和 `hold_value_change` 也是 `元` 口径。

相关代码：

| 文件 | 说明 |
| --- | --- |
| `backend/app/data/ingestors/plugins/tushare_ingestor.py` | 北向 `hold_value`、`net_buy_amount`、`hold_value_change` 由股数和价格计算，单位为 `元` |
| `backend/app/ai/llm_engine/context/fundamental.py` | `snapshot.northbound` 已手动除以 `10000` 生成 `*_10k_cny` 字段 |
| `backend/app/ai/llm_engine/context/capital_flow.py` | `snapshot.flow.northbound.net_buy_amount` 仍输出原始元值 |

实际样本：

| 股票 | `snapshot.flow.northbound.net_buy_amount` 当前值 | 除以 1 万 | 除以 1 亿 |
| --- | ---: | ---: | ---: |
| 贵州茅台 | 5342126250.00 | 534212.63万元 | 53.42亿元 |
| 格力电器 | -383074808.90 | -38307.48万元 | -3.83亿元 |
| 中国平安 | -488790913.90 | -48879.09万元 | -4.89亿元 |
| 京东方A | -2239207601.06 | -223920.76万元 | -22.39亿元 |
| 五粮液 | -671065912.63 | -67106.59万元 | -6.71亿元 |
| 中国中免 | -522688119.66 | -52268.81万元 | -5.23亿元 |

建议：

| 字段 | 当前状态 | 建议 |
| --- | --- | --- |
| `capital_flow.northbound.net_buy_amount` | 原始元值，未配置单位 | 展示为 `亿元`，`display_scale=0.00000001` |
| `fundamental.northbound_flow.hold_value_10k_cny` | 已除以 `1万`，但没格式化为字符串 | 只补 `万元`，`display_scale=1` |
| `fundamental.northbound_flow.net_buy_amount_10k_cny` | 已除以 `1万`，但没格式化为字符串 | 只补 `万元`，`display_scale=1` |
| `fundamental.northbound_flow.hold_value_change_10k_cny` | 已除以 `1万`，但没格式化为字符串 | 只补 `万元`，`display_scale=1` |

注意：`snapshot.northbound.*_10k_cny` 已经是万元数值，不能再次除以 `10000`。

### 北向持股比例缩放问题

北向持股比例还有一个比单位缺失更严重的问题。采集逻辑把 Tushare 返回的百分比除以 `100` 后入库，当前单位配置又按 `display_scale=1` 展示为 `%`，导致展示值疑似小了 100 倍。

实际现象：

| 字段 | 当前展示 | 更合理展示 |
| --- | ---: | ---: |
| `hold_ratio_pct` | `0.0469%` | `4.69%` |
| `hold_ratio_change_pct` | `0.003%` | `0.30%` |
| `capital_flow.northbound.hold_ratio` | `0.0469%` | `4.69%` |

建议：

| 字段 | 当前配置 | 建议配置 |
| --- | --- | --- |
| `fundamental.northbound_flow.hold_ratio_pct` | `display_scale=1` | `display_scale=100` |
| `fundamental.northbound_flow.hold_ratio_change_pct` | `display_scale=1` | `display_scale=100` |
| `capital_flow.northbound.hold_ratio` | `display_scale=1` | `display_scale=100` |
| `capital_flow.northbound.latest_hold_ratio` | `display_scale=1` | `display_scale=100` |
| `capital_flow.northbound.prev_hold_ratio` | `display_scale=1` | `display_scale=100` |
| `capital_flow.northbound.ratio_change` | `display_scale=1` | `display_scale=100` |

### NORTHBOUND 修复方案

只改 `backend/app/data/metadata/table_field_units.json`。不要改北向数据库存储口径，当前入库已把 Tushare 百分比转为 ratio，展示层乘以 `100` 即可。

建议配置：

```json
{
  "capital_flow.northbound": {
    "age_days": {"unit": "units.days", "display_scale": 1, "precision": 0},
    "hold_shares": {"unit": "units.shares", "display_scale": 1, "precision": 0},
    "hold_ratio": {"unit": "units.percent", "display_scale": 100, "precision": 4},
    "net_buy_amount": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
    "net_buy_volume": {"unit": "units.shares", "display_scale": 1, "precision": 0},
    "latest_hold_ratio": {"unit": "units.percent", "display_scale": 100, "precision": 4},
    "prev_hold_ratio": {"unit": "units.percent", "display_scale": 100, "precision": 4},
    "ratio_change": {"unit": "units.percent", "display_scale": 100, "precision": 4},
    "hold_shares_growth_pct": {"unit": "units.percent", "display_scale": 1, "precision": 2}
  },
  "fundamental.northbound_flow": {
    "age_days": {"unit": "units.days", "display_scale": 1, "precision": 0},
    "hold_shares": {"unit": "units.shares", "display_scale": 1, "precision": 0},
    "hold_ratio_pct": {"unit": "units.percent", "display_scale": 100, "precision": 4},
    "change_percent": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "hold_ratio_change_pct": {"unit": "units.percent", "display_scale": 100, "precision": 4},
    "net_buy_volume": {"unit": "units.shares", "display_scale": 1, "precision": 0},
    "hold_value_10k_cny": {"unit": "units.ten_thousand_cny", "display_scale": 1, "precision": 2},
    "net_buy_amount_10k_cny": {"unit": "units.ten_thousand_cny", "display_scale": 1, "precision": 2},
    "hold_value_change_10k_cny": {"unit": "units.ten_thousand_cny", "display_scale": 1, "precision": 2},
    "close_price_cny": {"unit": "units.cny", "display_scale": 1, "precision": 2}
  }
}
```

关键点：

| 字段 | 修复策略 |
| --- | --- |
| `net_buy_amount` | 原始元值，除以 `1e8` 显示为亿元 |
| `*_10k_cny` | 已经是万元，只补 `万元` |
| `hold_ratio`、`hold_ratio_pct` | 数据库存 ratio，展示时乘以 `100` |
| `hold_ratio_change_pct` | 数据库存 ratio 差值，展示时乘以 `100` |

## FINANCIAL_INDICATOR 结论

当前 `data.financial_indicator` 只配置了少量同比、环比、增长率字段。实际 AI Context 中，大量绝对金额、每股指标、百分比、天数和次数仍以裸数字进入 LLM 上下文。

### 金额字段

以下字段在当前数据库中是元级大数，建议展示为 `亿元`，即 `display_scale=0.00000001`。

| 字段 | 中文含义 | 样本原值 | 建议显示 |
| --- | --- | ---: | ---: |
| `retained_earnings` | 留存收益 | 贵州茅台 269690838329.60 | 2696.91亿元 |
| `invest_capital` | 全部投入资本 | 京东方A 355591375152.00 | 3555.91亿元 |
| `fixed_assets` | 固定资产 | 京东方A 239769537900.00 | 2397.70亿元 |
| `working_capital` | 营运资本 | 贵州茅台 233068933753.52 | 2330.69亿元 |
| `netdebt` | 净债务 | 京东方A 70835898762.00 | 708.36亿元 |
| `operating_income` | 经营活动净收益 | 中国平安 44029000000.00 | 440.29亿元 |
| `q_opincome` | 单季度经营收益 | 中国平安 44029000000.00 | 440.29亿元 |
| `ebit` | 息税前利润 | 贵州茅台 37424749642.98 | 374.25亿元 |
| `q_dtprofit` | 单季度扣非利润 | 贵州茅台 27239985194.41 | 272.40亿元 |
| `recurring_profit` | 扣非净利润 | 中国平安 23912000000.00 | 239.12亿元 |
| `q_investincome` | 单季度投资收益 | 中国平安 -8852000000.00 | -88.52亿元 |
| `valuechange_income` | 公允价值变动收益 | 中国平安 -8852000000.00 | -88.52亿元 |
| `non_op_profit` | 营业外收支 | 中国平安 -97000000.00 | -0.97亿元 |

同类字段还包括：

| 建议单位 | 字段 |
| --- | --- |
| 亿元 | `current_exint`、`noncurrent_exint`、`interestdebt`、`tangible_asset`、`networking_capital`、`ebitda`、`free_cash_flow_firm`、`free_cash_flow_equity`、`extra_item`、`interst_income`、`profit_prefin_exp`、`gross_margin` |

### 每股字段

以下字段建议补 `元`，不做缩放：

| 字段 | 中文含义 |
| --- | --- |
| `eps` | 每股收益 |
| `diluted_eps` | 稀释每股收益 |
| `q_eps` | 单季度每股收益 |
| `bps` | 每股净资产 |
| `ocfps` | 每股经营现金流 |
| `cash_flow_ps` | 每股现金流 |
| `ebit_ps` | 每股息税前利润 |
| `fcff_ps` | 每股企业自由现金流量 |
| `fcfe_ps` | 每股股东自由现金流量 |
| `revenue_ps` | 每股营业收入 |
| `total_revenue_ps` | 每股营业总收入 |
| `retained_earnings_ps` | 每股留存收益 |
| `capital_reserve_ps` | 每股资本公积金 |
| `surplus_reserve_ps` | 每股盈余公积金 |
| `undistributed_profit_ps` | 每股未分配利润 |

### 百分比字段

以下字段语义明确为百分比，建议补 `%`，`display_scale=1`：

| 字段 | 中文含义 |
| --- | --- |
| `roe` | 净资产收益率 |
| `roe_waa` | 加权净资产收益率 |
| `roe_diluted` | 扣非净资产收益率 |
| `roa` | 总资产报酬率 |
| `roic` | 投入资本回报率 |
| `net_profit_margin` | 销售净利率 |
| `debt_to_assets_ratio` | 资产负债率 |
| `cogs_ratio` | 销售成本率 |
| `expense_ratio` | 销售期间费用率 |
| `profit_to_gr` | 利润总额/营业总收入 |
| `operating_profit_to_revenue` | 营业利润/营业总收入 |
| `ebit_to_revenue` | 息税前利润/营业总收入 |
| `grossprofit_margin` | 销售毛利率 |
| `q_gsprofit_margin` | 单季度销售毛利率 |
| `q_netprofit_margin` | 单季度销售净利率 |
| `q_op_to_gr` | 单季度营业利润/营业总收入 |
| `q_profit_to_gr` | 单季度利润总额/营业总收入 |
| `q_ocf_to_sales` | 单季度经营活动现金流/营业收入 |

以下字段是比率或倍数字段，不建议直接按字段名统一补 `%`，需要按财务语义逐个确认。多数应显示为 `倍` 或保持无单位：

| 字段 | 倾向单位 |
| --- | --- |
| `current_ratio` | 倍 |
| `quick_ratio` | 倍 |
| `cash_ratio` | 倍 |
| `assets_to_eqt` | 倍 |
| `debt_to_eqt` | 倍 |
| `eqt_to_debt` | 倍 |
| `eqt_to_interestdebt` | 倍 |
| `tangibleasset_to_debt` | 倍 |
| `tangasset_to_intdebt` | 倍 |
| `tangibleasset_to_netdebt` | 倍 |
| `longdebt_to_workingcapital` | 倍 |

### 天数和次数字段

| 字段 | 建议单位 |
| --- | --- |
| `turn_days` | 天 |
| `invturn_days` | 天 |
| `arturn_days` | 天 |
| `accounts_receivable_turnover` | 次 |
| `fixed_asset_turnover` | 次 |
| `current_asset_turnover` | 次 |
| `asset_turnover` | 次 |
| `inv_turn` | 次 |

### FINANCIAL_INDICATOR 修复方案

主要修改 `backend/app/data/metadata/table_field_units.json` 的 `data.financial_indicator` 段。`gross_margin/grossprofit_margin` 按原始字段语义处理：`gross_margin` 是毛利金额，`grossprofit_margin` 是销售毛利率。历史字段不迁移、不作为本轮验收目标。

金额字段建议第一批配置为 `亿元`：

```json
{
  "extra_item": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "recurring_profit": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "gross_margin": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "operating_income": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "ebit": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "ebitda": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "free_cash_flow_firm": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "free_cash_flow_equity": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "current_exint": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "noncurrent_exint": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "interestdebt": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "netdebt": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "tangible_asset": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "working_capital": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "networking_capital": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "invest_capital": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "retained_earnings": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "fixed_assets": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "q_opincome": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "q_dtprofit": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "q_investincome": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "valuechange_income": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "non_op_profit": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "interst_income": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
  "profit_prefin_exp": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2}
}
```

每股字段显示为 `元`，不缩放：

```json
{
  "eps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "diluted_eps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "q_eps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "bps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "ocfps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "cash_flow_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "ebit_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "fcff_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "fcfe_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "revenue_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "total_revenue_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "retained_earnings_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "capital_reserve_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "surplus_reserve_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4},
  "undistributed_profit_ps": {"unit": "units.cny", "display_scale": 1, "precision": 4}
}
```

明确百分比字段显示为 `%`，不缩放：

```json
{
  "roe": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "roe_waa": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "roe_diluted": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "roa": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "roic": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "net_profit_margin": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "debt_to_assets_ratio": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "cogs_ratio": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "expense_ratio": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "profit_to_gr": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "operating_profit_to_revenue": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "ebit_to_revenue": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "grossprofit_margin": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "q_gsprofit_margin": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "q_netprofit_margin": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "q_op_to_gr": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "q_profit_to_gr": {"unit": "units.percent", "display_scale": 1, "precision": 2},
  "q_ocf_to_sales": {"unit": "units.percent", "display_scale": 1, "precision": 2}
}
```

天数和周转率字段：

```json
{
  "turn_days": {"unit": "units.days", "display_scale": 1, "precision": 2},
  "invturn_days": {"unit": "units.days", "display_scale": 1, "precision": 2},
  "arturn_days": {"unit": "units.days", "display_scale": 1, "precision": 2},
  "accounts_receivable_turnover": {"unit": "units.times", "display_scale": 1, "precision": 2},
  "fixed_asset_turnover": {"unit": "units.times", "display_scale": 1, "precision": 2},
  "current_asset_turnover": {"unit": "units.times", "display_scale": 1, "precision": 2},
  "asset_turnover": {"unit": "units.times", "display_scale": 1, "precision": 2},
  "inv_turn": {"unit": "units.times", "display_scale": 1, "precision": 2}
}
```

比率类字段不要一次性按 `%` 处理。`current_ratio`、`quick_ratio`、`cash_ratio`、`assets_to_eqt`、`debt_to_eqt` 等更适合显示为 `倍` 或保持无单位，需要逐项确认财务定义。

## gross_margin 和 grossprofit_margin 字段语义冲突

`gross_margin` 不能直接补 `%`。按 Tushare 原始字段，`gross_margin` 是毛利金额，`grossprofit_margin` 才是销售毛利率。全表统计显示：

| 项 | 数量 |
| --- | ---: |
| 含 `gross_margin` 的记录 | 1399 |
| `gross_margin` 绝对值大于 1000 的记录 | 1385 |
| `gross_margin` 看起来像百分比的记录 | 14 |

实际样本：

| 股票 | `gross_margin` | `gross_profit` | `q_gsprofit_margin` | 判断 |
| --- | ---: | ---: | ---: | --- |
| 贵州茅台 | 89.7592 | 48388523020.19 | 89.7592 | 历史混杂，旧数据暂不处理 |
| 格力电器 | 27.4158 | 11779497981.00 | 27.4158 | 历史混杂，旧数据暂不处理 |
| 京东方A | 7954015105.00 | 空 | 15.5957 | `gross_margin` 实际是毛利金额 |
| 五粮液 | 18598004617.09 | 空 | 81.4344 | `gross_margin` 实际是毛利金额 |
| 中国中免 | 5685792562.72 | 空 | 33.6318 | `gross_margin` 实际是毛利金额 |

现有映射把原始字段改成了另一套标准字段名，容易让人误读：

| Tushare 源字段 | 标准字段 |
| --- | --- |
| `gross_margin` | `gross_profit` |
| `grossprofit_margin` | `gross_margin` |

后续修复改为按原始字段名保留，历史 JSON 字段暂不迁移、不回填。

### gross_margin/grossprofit_margin 修复方案

只修新口径，不处理历史字段。重点是让新采集字段和 AI Context 字段名按原始字段语义保持清楚，不再把 `gross_margin` 映射成 `gross_profit`，也不再把 `grossprofit_margin` 映射成 `gross_margin`。

按 Tushare `fina_indicator` 原始字段语义：

| Tushare 原始字段 | 原始含义 | 新标准字段 | 展示单位 |
| --- | --- | --- | --- |
| `gross_margin` | 毛利金额 | `gross_margin` | 亿元 |
| `grossprofit_margin` | 销售毛利率 | `grossprofit_margin` | % |

`backend/app/data/ingestors/plugins/column_mapping.json` 应改为保留原始字段名：

```json
{
  "gross_margin": "gross_margin",
  "grossprofit_margin": "grossprofit_margin"
}
```

字段名不够直观，但本轮按原始字段整改，不新增 `gross_profit_cny`、`gross_profit_margin_pct` 这类新字段，避免扩大改动范围。

用一只股票做一次测试环境同步，确认新入库记录满足：

| 字段 | 应有语义 |
| --- | --- |
| `gross_margin` | 毛利金额，元 |
| `grossprofit_margin` | 销售毛利率，百分比 |
| `q_gsprofit_margin` | 单季度销售毛利率，百分比 |

如果新同步仍出现 `grossprofit_margin` 缺失或 `gross_margin` 被改名，优先修 `ColumnMapper` 或采集字段冲突处理。

历史数据暂不迁移。由于旧记录仍可能存在 `gross_profit` 或百分比型 `gross_margin`，本轮只保证新采集口径正确。AI Context 若读取历史旧记录，可能仍出现旧字段混杂，不作为本轮验收目标。

修复新采集后建议复查新记录：

```sql
SELECT
  stock_code,
  report_date,
  data->>'gross_margin' AS gross_margin,
  data->>'grossprofit_margin' AS grossprofit_margin,
  data->>'q_gsprofit_margin' AS q_gsprofit_margin
FROM data.financial_indicator
WHERE stock_code = '<目标股票代码>'
ORDER BY report_date DESC, announcement_date DESC
LIMIT 5;
```

目标是新采集记录中 `gross_margin` 为毛利金额，`grossprofit_margin` 为销售毛利率；历史记录不作为本轮验收目标。

## financial_trend 漏单位

`history.financial_trend` 当前只格式化了增长率字段，以下趋势字段仍是裸数字：

| 路径 | 当前问题 | 建议 |
| --- | --- | --- |
| `history.financial_trend.profitability_trend.latest.roe` | 裸数字 | `%` |
| `history.financial_trend.profitability_trend.latest.gross_margin` | 裸数字 | `%`，但受 `gross_margin` 数据冲突影响 |
| `history.financial_trend.profitability_trend.latest.net_margin` | 裸数字 | `%` |
| `history.financial_trend.leverage_trend.latest.debt_to_asset` | 裸数字 | `%` |
| `history.financial_trend.recent_quarters[*].roe` | 裸数字 | `%` |
| `history.financial_trend.recent_quarters[*].gross_margin` | 裸数字 | `%`，但受 `gross_margin` 数据冲突影响 |
| `history.financial_trend.recent_quarters[*].net_margin` | 裸数字 | `%` |
| `history.financial_trend.recent_quarters[*].debt_to_asset` | 裸数字 | `%` |

### financial_trend 修复方案

给 `backend/app/data/metadata/table_field_units.json` 的 `fundamental.financial_trend` 增加趋势字段单位。这里的字段是 AI Context 虚拟表字段，不是数据库原始字段。

建议配置：

```json
{
  "fundamental.financial_trend": {
    "total_revenue_yoy": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "net_profit_yoy": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "net_profit_dedt_yoy": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "roe": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "gross_margin": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "net_margin": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "debt_to_asset": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "change_vs_oldest": {"unit": "units.percentage_points", "display_scale": 1, "precision": 2}
  }
}
```

`financial_trend` 中的 `gross_margin` 是上下文计算后的毛利率，不是原始 `data.financial_indicator.gross_margin`，所以可以在这个虚拟表里配置为 `%`。

## 其他 AI Context 旁路字段

这些字段不是本次点名主线，但同一份 AI Context 中也会让 LLM 看到无单位数字。

| 路径 | 当前问题 | 建议 |
| --- | --- | --- |
| `snapshot.flow.dragon_tiger.net_buy` | 金额裸露，样本可到十亿级 | 建议显示为 `亿元` 或 `万元` |
| `snapshot.flow.dragon_tiger.buy_amount` | 金额裸露，样本可到十亿级 | 建议显示为 `亿元` 或 `万元` |
| `snapshot.flow.dragon_tiger.sell_amount` | 金额裸露，样本可到十亿级 | 建议显示为 `亿元` 或 `万元` |
| `signals.flow.block_trade.amount` | 模型注释为 `万元`，但没有单位字符串 | 只补 `万元`，不要缩放 |
| `signals.flow.block_trade.total_amount` | 模型注释为 `万元`，但没有单位字符串 | 只补 `万元`，不要缩放 |
| `signals.flow.block_trade.premium_rate` | 折溢价率裸数字 | 补 `%` |
| `snapshot.company.industry_rank.market_cap.total_market_cap_cny` | 单位来源尚未确认 | 先确认 Tushare 行业 `total_mv` 单位，再决定是否缩放 |

`StockBlockTrade.amount` 的模型注释是 `成交额(万元)`，所以样本中的 `4324.5` 应理解为 `4324.5万元`，不应再除以 `10000`。

### 旁路字段修复方案

这些字段建议在主问题修完后处理。

`StockBlockTrade.amount` 是 `万元`，只补单位不缩放：

```json
{
  "capital_flow.block_trade": {
    "volume": {"unit": "units.ten_thousand_shares", "display_scale": 1, "precision": 2},
    "amount": {"unit": "units.ten_thousand_cny", "display_scale": 1, "precision": 2},
    "total_amount": {"unit": "units.ten_thousand_cny", "display_scale": 1, "precision": 2},
    "premium_rate": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "avg_premium": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "price": {"unit": "units.cny", "display_scale": 1, "precision": 2}
  }
}
```

`DragonTigerData` 金额字段需先确认入库单位。若确认数据库为元，建议显示为 `亿元`：

```json
{
  "capital_flow.dragon_tiger": {
    "net_buy": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
    "buy_amount": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
    "sell_amount": {"unit": "units.hundred_million_cny", "display_scale": 0.00000001, "precision": 2},
    "turnover_rate": {"unit": "units.percent", "display_scale": 1, "precision": 2},
    "price_change": {"unit": "units.percent", "display_scale": 1, "precision": 2}
  }
}
```

## 修复优先级

| 优先级 | 修复项 | 原因 |
| --- | --- | --- |
| P0 | `fundamental.valuation.total_mv/float_mv` 改为 `亿元` | 当前直接输出 12 到 13 位元值 |
| P0 | `capital_flow.northbound.net_buy_amount` 补单位并缩放 | 当前直接输出原始元值，且用户已点名 |
| P0 | 北向持股比例 `hold_ratio` 系列改 `display_scale=100` | 当前疑似小 100 倍，属于语义错误 |
| P1 | `data.financial_indicator` 金额字段补 `亿元` | 大量 10 位到 13 位元值裸露 |
| P1 | `data.financial_indicator` 每股、百分比、天数、次数字段补单位 | 覆盖面大，能显著改善 LLM 可读性 |
| P1 | `gross_margin/grossprofit_margin` 按原始字段语义修新采集口径 | 当前映射和历史字段混杂，不处理历史，只保证新口径清晰 |
| P2 | `history.financial_trend` 补 `roe/net_margin/debt_to_asset` 单位 | 趋势字段裸露但不影响数据库口径 |
| P2 | Dragon Tiger、Block Trade、Industry Rank 等旁路资金流字段 | 同样影响 AI Context，但不是本次主线 |

## 通用实现注意：格式化顺序

单位配置依赖标准字段名，不依赖中文字段名。AI Context 中财务快照应遵循这个顺序：

```text
标准字段 raw data
  -> format_payload_values("data.financial_indicator", payload)
  -> localize_financial_report_payload(...)
  -> LLM context
```

当前 `SnapshotProvider` 已经基本按这个方向处理。后续如果新增财务上下文，不要先本地化成中文 key 再格式化单位，否则 `table_field_units.json` 无法匹配标准字段名。

如果某段上下文既用于计算又用于展示，应保留两份 payload：计算链路使用原始数值，展示链路使用格式化后的值。不要让风控、交易、风险检测逻辑消费带单位字符串。

## 测试建议

建议更新 `backend/tests/test_financial_standard_keys.py` 中已有的 `format_payload_values` 测试，并新增覆盖以下口径：

| 测试点 | 期望 |
| --- | --- |
| `fundamental.valuation.total_mv=1602492105138` | `16024.92亿元` |
| `capital_flow.northbound.net_buy_amount=5342126250` | `53.42亿元` |
| `fundamental.northbound_flow.net_buy_amount_10k_cny=534212.625` | `534212.63万元` |
| `capital_flow.northbound.hold_ratio=0.0469` | `4.69%` 或按精度显示 |
| `data.financial_indicator.operating_income=44029000000` | `440.29亿元` |
| `data.financial_indicator.eps=1.09` | `1.09元` |
| `data.financial_indicator.roe=4.0826` | `4.08%` |
| `data.financial_indicator.asset_turnover=0.1115` | `0.11次` |
| `data.financial_indicator.gross_margin=7954015105` | 新口径下应显示为 `79.54亿元` |
| `data.financial_indicator.grossprofit_margin=15.5957` | 新口径下应显示为 `15.6%` |

注意：历史字段不处理，只对新采集样本做口径验证，不把历史旧映射作为失败条件。

## 验证 AI Context 输出

修复后建议在容器内重新构建同一批股票的 AI Context，人工检查关键路径：

| 路径 | 期望 |
| --- | --- |
| `snapshot.valuation.total_mv` | `xxxx.xx亿元` |
| `snapshot.valuation.float_mv` | `xxxx.xx亿元` |
| `snapshot.flow.northbound.net_buy_amount` | `xx.xx亿元` |
| `snapshot.northbound.quarter_change.net_buy_amount_10k_cny` | `xxxx.xx万元` |
| `snapshot.northbound.latest_position.hold_ratio_pct` | 百分比不再小 100 倍 |
| `snapshot.financial_statements.financial_indicator_latest.data` | 金额字段不再出现 10 位到 13 位裸数字 |
| `history.financial_trend.profitability_trend.latest.roe` | 带 `%` |
| `history.financial_trend.leverage_trend.latest.debt_to_asset` | 带 `%` |

建议运行的验证命令：

```bash
pytest backend/tests/test_financial_standard_keys.py
```

如果涉及上下文 reader 的 fake session 测试，也应补跑相关 AI Context 测试。不要新增针对 prompt 文案的断言。

## 不建议的修复方式

不要用字段名关键词自动猜单位。字段名中包含 `ratio`、`margin`、`income` 并不总能唯一决定单位，`gross_margin` 已经证明这一点。

不要在 `field_units.py` 中写特殊判断，例如“数值大于 1000 就当金额”。这会把数据质量问题隐藏到展示层。

不要把单位字符串写回数据库。数据库继续保存标准数值，LLM Context 边界再格式化。

不要为了处理中文字段名，在格式化器里匹配中文 key。正确顺序是先用标准字段名格式化，再本地化字段名。

不要对 `*_10k_cny` 再做 `display_scale=0.0001`。这些字段已经预先除以 `10000`。

## 2026-06-08 续修进展

本轮已完成：

| 项 | 处理结果 |
| --- | --- |
| `fundamental.valuation.total_mv/float_mv` | 改为 `亿元` 展示 |
| `capital_flow.northbound.net_buy_amount` | 改为 `亿元` 展示 |
| 北向 `hold_ratio` 系列 | 按数据库 ratio 口径展示为百分比 |
| `fundamental.northbound_flow.*_10k_cny` | 保持万元数值，只补 `万元` 单位 |
| `data.financial_indicator` 金额/每股/百分比/天数/次数字段 | 补齐第一批单位配置 |
| `gross_margin/grossprofit_margin` 新采集口径 | 保留原始字段语义：`gross_margin` 为毛利金额，`grossprofit_margin` 为销售毛利率 |
| `fundamental.financial_trend` | 趋势百分比字段补 `%` |
| `capital_flow.block_trade` | 大宗交易金额、价格、折溢价率补单位 |
| `capital_flow.dragon_tiger` | 龙虎榜金额补 `亿元`，涨跌/换手补 `%` |
| `fundamental.dragon_tiger_activity` | 预先除以 1 万的字段只补 `万元`，百分比字段补 `%` |

已验证命令：

```bash
python -m json.tool backend/app/data/metadata/table_field_units.json
python -m json.tool backend/app/data/metadata/table_field_labels.json
pytest backend/tests/test_financial_standard_keys.py backend/tests/test_column_mapping_config.py backend/tests/test_data_source_ingestors.py
git diff --check
```

暂未处理：

| 项 | 原因 |
| --- | --- |
| `snapshot.company.industry_rank.market_cap.total_market_cap_cny` | 当前 Tushare `dc_index.total_mv` 入库前没有明确换算；本轮不猜单位，需用真实样本或官方接口文档确认后再决定是否缩放 |
