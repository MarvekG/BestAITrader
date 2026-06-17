# Ingestor Plugins

本目录只放具体数据源插件。框架文件放在上一级目录：

- `../base_ingestor.py`
- `../plugin_loader.py`
- `../manager.py`

新增插件只需要继承 `BaseIngestor`。插件加载器会扫描当前目录下的 `*_ingestor.py` 文件，发现其中继承
`BaseIngestor` 的类并实例化。

## 放置规则

- 新插件必须放在 `./`。
- 文件名必须是 `*_ingestor.py`，例如 `example_ingestor.py`。
- 类必须继承 `BaseIngestor`。
- 类必须声明唯一的 `source_name`。
- 不要修改 `../plugin_loader.py` 或 `../manager.py` 来注册新插件。
- 模板文件不要以 `_ingestor.py` 结尾，避免被自动加载；当前模板是 `./example_plugin_template.py`。

## 插件边界

新插件应当是自包含的数据源适配器。

- 新插件不得依赖项目配置系统，不要读取 `app.core.config.settings`。
- 新插件不得修改 `app/core/config.py` 或 `.env` 来新增 provider 配置。
- provider 的 API 地址、默认参数、headers、默认配置直接写在插件文件内。
- 不要硬编码真实密钥；需要密钥的数据源先在插件内留空常量或实现插件级读取方案。
- `./tushare_ingestor.py` 是历史实现，暂时保留其对项目配置的依赖。

`ColumnMapper` 位于 `./column_mapping.py`，映射配置位于 `./column_mapping.json`，只允许
`./tushare_ingestor.py` 使用。新插件不得导入或调用 `ColumnMapper`，应直接产出目标表字段名。

## 调用与 Failover

`../manager.py` 会用方法名动态调用插件：

1. 按默认数据源、Tushare、其他插件的顺序尝试。
2. 如果插件不可用，跳过。
3. 如果插件没有覆盖对应方法，会调用 `BaseIngestor` 的默认实现并返回 `False`。
4. 如果方法返回 `False` 或 `None`，继续尝试下一个插件。
5. 如果方法返回非 `False`、非 `None` 的结果，认为成功并停止。

因此，新插件只实现自己能支持的方法即可，不需要声明能力列表。

## 最小骨架

```python
import pandas as pd

from app.core.logger import get_logger
from app.data.ingestors.base_ingestor import BaseIngestor
from app.data.ingestion.service import DataIngestionService
from app.core.utils.formatters import StockCodeStandardizer

logger = get_logger(__name__)


class ExampleIngestor(BaseIngestor):
    """Example local data-source plugin."""

    source_name = "example"
    display_name = "Example"
    required_settings = ()

    API_BASE_URL = "https://api.example.com"
    API_TOKEN = ""
    DEFAULT_TIMEOUT = 15

    def __init__(self) -> None:
        self.ingestion_service = DataIngestionService()
        self.source = self.get_source_name()
        self.client = self._build_client()

    def _build_client(self):
        """Create provider client without reading project settings."""
        return object()

    async def fetch_and_ingest_realtime_market(self, stock_code: str) -> bool:
        standardized_code = StockCodeStandardizer.standardize(stock_code)

        df = pd.DataFrame([
            {
                "stock_code": standardized_code,
                "current_price": None,
                "change_percent": None,
                "turnover_rate": None,
                "volume_ratio": None,
                "amplitude": None,
                "pb_ratio": None,
                "pe_dynamic": None,
                "turnover": None,
                "volume": None,
                "total_market_cap": None,
                "circulating_market_cap": None,
                "timestamp": None,
                "data_source": self.source,
            }
        ])
        if df.empty:
            return False

        await self._run_in_executor(
            self.ingestion_service.write_dataframe,
            "example_realtime",
            df,
            source=self.source,
            target_table="stock_realtime_market",
        )
        return True
```

## 实现顺序

每个采集方法建议保持这个顺序：

1. 标准化输入，例如 `StockCodeStandardizer.standardize(stock_code)`。
2. 调用上游接口；同步 SDK 必须通过 `_run_in_executor(...)` 调用。
3. 上游无数据时返回 `False`，让 manager 继续 failover。
4. 直接构造目标表字段名的 DataFrame。
5. 补齐 `stock_code`、日期字段、`data_source` 等公共字段。
6. 调用 `DataIngestionService.write_dataframe(...)` 落库。
7. 成功返回 `True`。

## DataFrame 字段原则

下面的“必选字段”来自当前 AI-Context 实际读取字段。缺少这些字段不一定导致落库失败，但会直接影响分析上下文质量。
“推荐字段”是落库、排查、后续扩展时应尽量补齐的字段。

通用要求：

- `stock_code` 必须使用项目标准格式。
- 日期字段使用真实日期或时间类型，不要只存不可解析字符串。
- 除财务报表这类 JSON 结构表外，不要把业务字段全塞进 `data`。
- 新插件直接产出目标字段名，不经过 `ColumnMapper`。

## 行情与基础资料

### `fetch_and_ingest_stock_kline` -> `kline_data`

必选字段：`stock_code`, `freq`, `date`, `open`, `high`, `low`, `close`, `volume`, `change_percent`

推荐字段：`turnover`, `change`, `data_source`

说明：AI-Context 只读取 `freq = 'D'` 的日线数据；日线插件应写入 `freq = 'D'`。

### `fetch_and_ingest_index_daily` -> `index_daily`

必选字段：`index_code`, `trade_date`, `close`, `pct_chg`, `volume`, `amount`

推荐字段：`open`, `high`, `low`, `pre_close`, `change`, `data_source`

### `fetch_and_ingest_realtime_market` -> `stock_realtime_market`

必选字段：`stock_code`, `timestamp`, `current_price`, `change_percent`, `turnover_rate`, `volume_ratio`,
`amplitude`, `pb_ratio`, `pe_dynamic`, `turnover`, `volume`, `total_market_cap`, `circulating_market_cap`

推荐字段：`change_amount`, `high`, `low`, `open`, `prev_close`, `speed_increase`, `change_5min`,
`change_60days`, `change_ytd`, `data_source`

### `fetch_and_ingest_stock_info`, `fetch_and_ingest_company_profile`, `fetch_and_ingest_all_stock_basic` -> `stock_basic`

必选字段：`stock_code`, `name`, `industry`, `area`, `list_date`, `total_share`, `float_share`

推荐字段：`sector`, `market`, `status`, `exchange`, `list_status`, `data_source`

说明：`industry` 会被资金流上下文用于匹配板块资金流。

### `fetch_and_ingest_stock_valuation` -> `stock_valuation_history`

必选字段：`stock_code`, `data_date`, `pe_ttm`, `pb`, `ps_ttm`, `peg`, `dividend_yield`,
`total_market_value`, `circulating_market_value`

推荐字段：`close_price`, `change_percent`, `pe_static`, `ps_static`, `data_source`

## 资金流、榜单、板块

### `fetch_and_ingest_stock_money_flow` -> `stock_money_flow`

必选字段：`stock_code`, `trade_date`, `net_inflow_main`, `net_inflow_small`, `net_inflow_medium`,
`net_inflow_ratio_main`, `net_inflow_huge`, `net_inflow_main_3d`, `net_inflow_main_5d`,
`net_inflow_main_10d`, `close_price`, `change_pct`

推荐字段：`net_inflow_large`, `net_inflow_ratio_huge`, `net_inflow_ratio_large`,
`net_inflow_ratio_medium`, `net_inflow_ratio_small`, `data_source`

### `fetch_and_ingest_northbound` -> `northbound_data`

必选字段：`stock_code`, `date`, `hold_shares`, `hold_value`, `hold_ratio`, `close_price`,
`change_percent`, `net_buy_volume`, `net_buy_amount`, `hold_value_change`

推荐字段：`data_source`

### `fetch_and_ingest_dragon_tiger` -> `dragon_tiger_data`

必选字段：`stock_code`, `stock_name`, `trade_date`, `listing_reason`, `net_buy_amount`,
`buy_amount`, `sell_amount`, `turnover_rate`, `price_change_percent`, `interpretation`,
`net_buy_ratio`, `post_1_day_price_change_percent`, `post_5_day_price_change_percent`,
`post_10_day_price_change_percent`

推荐字段：`sequence_number`, `close_price`, `total_trade_amount`, `market_total_trade_amount`,
`trade_amount_ratio`, `floating_market_capitalization`, `details`, `data_source`

说明：AI-Context 同时读取个股最新记录、近 20 条历史记录和近 3 日全市场记录。

### `fetch_and_ingest_board_industry` -> `industry_data`

必选字段：`board_name`, `rank`, `latest_price`, `change_percent`, `rising_stocks_count`,
`falling_stocks_count`, `leading_stock_name`, `leading_stock_change_percent`, `total_market_cap`,
`timestamp`

推荐字段：`board_code`, `change_amount`, `turnover_rate`, `updated_at`, `data_source`

### `fetch_and_ingest_sector_money_flow` -> `sector_money_flow`

必选字段：`sector_name`, `trade_date`, `net_inflow`, `net_inflow_rate`, `main_net_inflow`, `leading_stock`

推荐字段：`huge_net_inflow`, `huge_net_inflow_rate`, `large_net_inflow`, `large_net_inflow_rate`,
`medium_net_inflow`, `medium_net_inflow_rate`, `small_net_inflow`, `small_net_inflow_rate`,
`close_price`, `change_percent`, `data_source`

### `fetch_and_ingest_stock_margin_data` -> `stock_margin_data`

必选字段：`stock_code`, `trade_date`, `margin_balance`, `margin_buy_amount`, `short_balance`,
`short_sell_volume`

推荐字段：`data_source`

### `fetch_and_ingest_stock_block_trade` -> `stock_block_trade`

必选字段：`stock_code`, `trade_date`, `price`, `volume`, `amount`, `premium_rate`, `buyer`, `seller`

推荐字段：`data_source`

## 股东、质押、限售、事件

### `fetch_and_ingest_stock_shareholder_count` -> `stock_shareholder_count`

必选字段：`stock_code`, `end_date`, `holder_count`, `avg_hold_shares`, `holder_count_change_ratio`

推荐字段：`ann_date`, `holder_count_prev`, `holder_count_change`, `avg_hold_shares_prev`,
`avg_hold_shares_change_ratio`, `avg_hold_value`, `total_mv`, `total_share`, `share_change`,
`share_change_reason`, `price_at_end`, `price_change_ratio`, `data_source`

### `fetch_and_ingest_stock_top_holders` -> `stock_top_holders`

必选字段：`stock_code`, `report_date`, `holder_rank`, `hold_amount`, `holder_name`, `holder_type`,
`hold_ratio`, `change`

推荐字段：`change_ratio`, `data_source`

### `fetch_and_ingest_stock_pledge_risk` -> `stock_pledge_risk`

必选字段：`stock_code`, `pledgor_name`, `pledge_ratio_to_total`, `pledge_ratio_to_holder`,
`pledge_date`, `pledge_price`, `current_price`, `liquidate_price`, `ann_date`

推荐字段：`pledgee_name`, `pledge_shares`, `release_date`, `data_source`

### `fetch_and_ingest_all_pledge_summary` -> `stock_pledge_summary`

必选字段：`stock_code`, `trade_date`, `pledge_ratio`, `pledge_shares`, `pledge_market_value`,
`pledge_count`

推荐字段：`industry`, `industry_code`, `unrestricted_pledge_shares`, `restricted_pledge_shares`,
`total_share`, `price_change_1y`, `data_source`

### `fetch_and_ingest_stock_insider_trading` -> `stock_insider_trading`

必选字段：`stock_code`, `trade_date`, `ann_date`, `insider_name`, `relationship`, `change_type`,
`change_shares`, `change_ratio`, `change_avg_price`, `shares_after_change`, `ratio_after_change`

推荐字段：`data_source`

### `fetch_and_ingest_stock_lockup_release` -> `stock_lockup_release`

必选字段：`stock_code`, `release_date`, `release_shares`, `release_market_value`, `ratio_to_total`,
`ratio_to_float`, `release_type`

推荐字段：`data_source`

### `fetch_and_ingest_stock_interactive_qa` -> `stock_interactive_qa`

必选字段：`stock_code`, `question`, `answer`, `question_time`, `answer_time`, `trade_date`

推荐字段：`question_id`, `answer_id`, `answerer`, `content_hash`, `data_source`

## 财务报表

财务类上下文读取 `data` JSON，同时使用 `report_date`、`announcement_date`、`updated_at` 排序。
`data` 中应尽量使用英文标准 key；如果 provider 只能提供中文 key，也可以保留中文 key，但要保证关键风险字段存在。

### `fetch_and_ingest_financial_indicators` -> `financial_indicator`

必选字段：`stock_code`, `report_date`, `announcement_date`, `updated_at`, `data`

`data` 必选 key：`total_revenue`, `operating_income`, `total_revenue_yoy`, `net_profit_yoy`, `roe`,
`gross_margin`, `cogs_ratio`, `operating_cost`, `net_profit_margin`, `debt_to_assets_ratio`, `eps`,
`net_profit_dedt_yoy`, `ocf_to_debt`, `ocfps`, `total_share`

推荐字段：`update_date`, `data_source`

### `fetch_and_ingest_income_statement` -> `stock_income_statement`

必选字段：`stock_code`, `report_date`, `announcement_date`, `updated_at`, `report_type`, `currency`,
`is_audit`, `data`

`data` 推荐 key：`total_revenue`, `operating_income`, `operating_cost`, `net_profit`,
`net_profit_attributable_to_parent`

推荐字段：`update_date`, `data_source`

### `fetch_and_ingest_balance_sheet` -> `stock_balance_sheet`

必选字段：`stock_code`, `report_date`, `announcement_date`, `updated_at`, `report_type`, `currency`,
`is_audit`, `data`

`data` 必选 key：`goodwill`, `total_assets`, `total_liabilities`, `debt_to_assets_ratio`,
`monetary_funds`, `short_term_borrowing`, `long_term_borrowing`

推荐字段：`update_date`, `data_source`

### `fetch_and_ingest_cashflow_statement` -> `stock_cashflow_statement`

必选字段：`stock_code`, `report_date`, `announcement_date`, `updated_at`, `report_type`, `currency`,
`is_audit`, `data`

`data` 必选 key：`net_cash_flow_from_operating_activities`, `n_cashflow_act`, `n_cash_flows_oper`

推荐字段：`update_date`, `data_source`

## 当前不建议优先实现的接口

下面接口不是简单的单表 DataFrame 写入模式，首次接入新数据源时不建议优先实现：

- `ingest_basic_macro_indicators`
- `ingest_global_macro_indicators`
- `fetch_global_macro_intelligence`

建议优先从这些接口开始：

- `fetch_and_ingest_stock_kline`
- `fetch_and_ingest_realtime_market`
- `fetch_and_ingest_stock_info`
- `fetch_and_ingest_stock_valuation`
- `fetch_and_ingest_stock_money_flow`

## 常见坑

- 文件不在当前目录：不会被发现。
- 文件名不是 `*_ingestor.py`：不会被扫描。
- `source_name` 冲突：注册会失败。
- 插件缺方法：没问题，默认返回 `False`，manager 会尝试下一个插件。
- 上游无数据但返回 `True`：会阻断 failover。
- async 方法里直接调用同步 SDK：会阻塞事件循环。
- 新插件使用 `ColumnMapper`：违反插件边界。
- DataFrame 只给数据库最低字段：AI-Context 会缺关键上下文。
