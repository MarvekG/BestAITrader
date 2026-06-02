import json
from typing import List, Dict, Any, Optional
from langchain.tools import tool
from app.ai.agentic.tooling.browser_tool import browse_web_page_html
from app.ai.agentic.tooling.news_tool import search_news
from app.ai.agentic.tooling.pdf_tool import parse_pdf_to_markdown
from app.ai.agentic.tooling.python_sandbox import execute_python_in_sandbox
from app.ai.agentic.tooling.stock_tools import StockTools, UnsupportedColumnsError
from app.data.ingestors.manager import ingestor_manager
from app.core.logger import get_logger
from app.trading.trading_engine import TradingEngine
from app.data.metadata.financial_report_localizer import localize_financial_report_data_field

from datetime import date, datetime
import uuid

logger = get_logger(__name__)
trading_engine = TradingEngine()

FINANCIAL_REPORT_QUERY_MODELS = {
    "financial": ("FinancialIndicator", "data.financial_indicator"),
    "income_statement": ("StockIncomeStatement", "data.stock_income_statement"),
    "balance_sheet": ("StockBalanceSheet", "data.stock_balance_sheet"),
    "cashflow_statement": ("StockCashflowStatement", "data.stock_cashflow_statement"),
}


def _format_trade_execution_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将交易服务结果整理为 Agent 工具可读的统一结构。

    Args:
        payload: 交易服务或前置校验返回的原始结果。

    Returns:
        包含执行状态、展示消息、机器可读原因和详情的工具返回值。
    """
    if payload.get("success") is True:
        return {
            "success": True,
            "execution_status": "executed",
            "message": payload.get("message", "Trade executed successfully."),
            "reason": None,
            "details": make_json_serializable(payload),
        }

    message = payload.get("message") or payload.get("error") or "Trade execution failed."
    result = {
        "success": False,
        "execution_status": "failed",
        "message": message,
        "reason": payload.get("reason") or message,
        "details": make_json_serializable(payload),
    }
    if "risk_control" in payload:
        result["risk_control"] = make_json_serializable(payload["risk_control"])
    return result

STOCK_QUERY_HANDLERS = {
    "status": lambda stock_code, limit: StockTools.check_data_status(stock_code),
    "basic": lambda stock_code, limit: StockTools.get_stock_basic_info(stock_code),
    "financial": lambda stock_code, limit: StockTools.get_latest_indicators(stock_code),
    "income_statement": (
        lambda stock_code, limit: StockTools.get_generic_db_data("StockIncomeStatement", stock_code, limit)
    ),
    "balance_sheet": (
        lambda stock_code, limit: StockTools.get_generic_db_data("StockBalanceSheet", stock_code, limit)
    ),
    "cashflow_statement": (
        lambda stock_code, limit: StockTools.get_generic_db_data("StockCashflowStatement", stock_code, limit)
    ),
    "valuation": lambda stock_code, limit: StockTools.get_valuation_history(stock_code, limit),
    "kline": lambda stock_code, limit: StockTools.get_recent_kline(stock_code, limit),
    "top_holders": lambda stock_code, limit: StockTools.get_top_holders(stock_code),
    "money_flow": lambda stock_code, limit: StockTools.get_generic_db_data("StockMoneyFlow", stock_code, limit),
    "northbound": lambda stock_code, limit: StockTools.get_generic_db_data("NorthboundData", stock_code, limit),
    "dragon_tiger": lambda stock_code, limit: StockTools.get_generic_db_data("DragonTigerData", stock_code, limit),
    "hot_rank": lambda stock_code, limit: StockTools.get_generic_db_data("StockHotRank", stock_code, limit),
    "insider": lambda stock_code, limit: StockTools.get_generic_db_data("StockInsider", stock_code, limit),
    "pledge": lambda stock_code, limit: StockTools.get_generic_db_data("StockPledge", stock_code, limit),
    "shareholder": lambda stock_code, limit: StockTools.get_generic_db_data("StockShareholder", stock_code, limit),
    "forecast": lambda stock_code, limit: StockTools.get_generic_db_data("StockForecast", stock_code, limit),
    "technical": lambda stock_code, limit: StockTools.get_generic_db_data("StockIndicators", stock_code, limit),
    "block_trade": lambda stock_code, limit: StockTools.get_generic_db_data("StockBlockTrade", stock_code, limit),
    "fund_holding": lambda stock_code, limit: StockTools.get_generic_db_data("StockFundHolding", stock_code, limit),
    "margin": lambda stock_code, limit: StockTools.get_generic_db_data("StockMargin", stock_code, limit),
    "sentiment": lambda stock_code, limit: StockTools.get_generic_db_data("StockSentiment", stock_code, limit),
    "lockup_release": lambda stock_code, limit: StockTools.get_generic_db_data("StockRelease", stock_code, limit),
}

MARKET_QUERY_CONFIG = {
    "index_daily": {"model_name": "IndexDaily"},
    "sector_money_flow": {"model_name": "SectorMoneyFlow"},
    "limit_pool": {"custom": "limit_pool"},
    "futures": {"custom": "futures"},
}

SYNC_TASK_CONFIG = {
    "stock_basic": {
        "method_name": "fetch_and_ingest_stock_info",
        "target_param": "stock_code",
    },
    "daily_kline": {
        "method_name": "fetch_and_ingest_stock_kline",
        "target_param": "stock_code",
        "required_params": ["start_date", "end_date"],
        "extra_params": ["adjust"],
    },
    "financial": {
        "method_name": "fetch_and_ingest_financial_indicators",
        "target_param": "stock_code",
    },
    "income_statement": {
        "method_name": "fetch_and_ingest_income_statement",
        "target_param": "stock_code",
    },
    "valuation": {
        "method_name": "fetch_and_ingest_stock_valuation",
        "target_param": "stock_code",
        "optional_params": ["start_date", "end_date"],
    },
    "realtime": {
        "method_name": "fetch_and_ingest_realtime_market",
        "target_param": "stock_code",
    },
    "northbound": {
        "method_name": "fetch_and_ingest_northbound",
        "target_param": "stock_code",
    },
    "money_flow": {
        "method_name": "fetch_and_ingest_stock_money_flow",
        "target_param": "stock_code",
    },
    "shareholders": {
        "method_name": "fetch_and_ingest_stock_shareholder_count",
        "target_param": "stock_code",
    },
    "limit_pools": {
        "custom": "limit_pools",
        "optional_params": ["start_date"],
    },
    "block_trade": {
        "method_name": "fetch_and_ingest_stock_block_trade",
        "target_param": "stock_code",
        "optional_params": ["start_date", "end_date"],
    },
    "sector_flow": {
        "method_name": "fetch_and_ingest_sector_money_flow",
        "target_param": "stock_code",
    },
    "margin": {
        "method_name": "fetch_and_ingest_stock_margin_data",
        "target_param": "stock_code",
    },
    "lockup_release": {
        "method_name": "fetch_and_ingest_stock_lockup_release",
        "target_param": "stock_code",
    },
    "index_daily": {
        "method_name": "fetch_and_ingest_index_daily",
        "target_param": "index_code",
        "required_params": ["start_date", "end_date"],
    },
}


def _normalize_list_arg(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value).strip()]


SUPPORTED_STOCK_QUERY_TYPES = sorted(STOCK_QUERY_HANDLERS.keys())


def make_json_serializable(obj: Any) -> Any:
    """递归将对象转换为 JSON 可序列化的格式 (Recursively convert to JSON serializable)"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    return obj


@tool
async def execute_python_sandboxed(code: str) -> Dict[str, Any]:
    """
    在受限沙箱中执行 Python 计算代码 (Execute Python code in a compute-only sandbox).
    适合一次性数值计算、日期处理、JSON 解析、列表/字典处理、numpy/pandas 分析。
    允许:
    - 默认允许大多数纯计算、纯数据处理 Python 代码；除明确列出的禁止项外，不要把本说明理解成白名单
    - 大多数不触达宿主环境的标准库和数据分析模块都可以尝试，例如 json、datetime、math、
      statistics、decimal、fractions、random、re、collections、itertools、functools、
      asyncio、threading、signal、numpy、pandas
    - 常见安全内建函数可直接使用，例如 abs/all/any/bool/dict/enumerate/filter/float/hasattr/
      int/isinstance/issubclass/len/list/map/max/min/pow/print/range/repr/reversed/round/set/
      sorted/str/sum/tuple/type/zip
    - 常见控制流和计算语法都可以使用，例如 if/for/while/try/except、列表/字典推导式、def、
      lambda、del、global、nonlocal、yield、yield from
    - 如果某个模块不在禁止列表里，也可能因为当前 Pyodide 运行时未提供而在执行期失败；这种情况可以直接尝试，再根据报错调整
    禁止:
    - 会触达宿主环境或破坏隔离边界的模块: os、sys、subprocess、socket、pathlib、shutil、
      tempfile、ctypes、importlib、builtins、multiprocessing
    - 危险调用: open、exec、eval、compile、__import__、input、help、dir、globals、
      locals、vars、getattr、setattr、delattr、breakpoint
    - 双下划线属性链，如 __class__、__mro__、__subclasses__、__globals__、__code__、__closure__
    - 当前不支持的语法: AsyncFunctionDef、AsyncFor、AsyncWith、Await、ClassDef、With、match
    请不要读写文件、访问网络、启动子进程、操作环境变量。
    真实已验证可用: json、datetime、signal、numpy、pandas、hasattr、isinstance。
    真实已验证不可用/会失败: os (被沙箱拦截), resource (当前 Pyodide 运行时中不存在)。
    注意: 不在禁止列表中不代表 Pyodide 一定提供该模块；例如 resource 在当前运行时中并不存在。
    工具会返回沙箱执行报告，包含 success、stdout、stderr、error、metadata 等字段。
    提示：务必确认代码正确性，避免计算出错误的数据。
    """
    return make_json_serializable(await execute_python_in_sandbox(code))


@tool
async def query_stock_data(
    stock_code: str, data_configs: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    统一查询单只股票的多张数据库表 (Unified stock multi-table DB query tool).
    参数:
    - stock_code: 股票代码，如 '000001.SZ'
    - data_configs: 字典形式，Key 为数据类型，Value 为包含 limit, start_time, end_time, columns 的子字典。
      示例:
      {"kline": {"limit": 100, "start_time": "2024-01-01 00:00:00",
      "end_time": "2024-03-01 23:59:59", "columns": ["date", "close"]}}
      注意: 所有子查询均强制要求 start_time 和 end_time。

    支持的 Key (data_type):
    - status: 数据完整性状态
    - basic: 股票基础信息
    - financial: 财务指标
    - income_statement: 利润表
    - balance_sheet: 资产负债表
    - cashflow_statement: 现金流量表
    - valuation: 历史估值序列
    - kline: 最近日线
    - top_holders: 十大股东
    - money_flow: 资金流
    - northbound: 北向资金
    - dragon_tiger: 龙虎榜
    - hot_rank: 个股热度
    - insider: 内幕/高管交易
    - pledge: 股权质押
    - shareholder: 股东人数
    - forecast: 业绩预告
    - technical: 技术指标
    - block_trade: 大宗交易
    - fund_holding: 基金持仓
    - margin: 两融数据
    - sentiment: 舆情分数
    - lockup_release: 解禁数据
    """
    results: Dict[str, Any] = {
        "stock_code": stock_code,
        "results": {},
    }
    for data_type, config_val in data_configs.items():
        if data_type not in STOCK_QUERY_HANDLERS:
            results["results"][data_type] = {"error": f"Unsupported data type: {data_type}"}
            continue
        
        # 解析配置 (Parse configuration - Mandatory fields)
        if not isinstance(config_val, dict):
            results["results"][data_type] = {"error": "Config must be a dict containing start_time and end_time"}
            continue

        limit = config_val.get("limit", 20)
        start_t = config_val.get("start_time")
        end_t = config_val.get("end_time")
        columns = _normalize_list_arg(config_val.get("columns"))

        if not start_t or not end_t:
            results["results"][data_type] = {"error": "start_time and end_time are required"}
            continue

        try:
            # 优先使用通用的 get_generic_db_data 以支持日期过滤
            model_map = {
                "financial": "FinancialIndicator",
                "income_statement": "StockIncomeStatement",
                "balance_sheet": "StockBalanceSheet",
                "cashflow_statement": "StockCashflowStatement",
                "valuation": "StockValuationHistory",
                "kline": "KlineData",
                "top_holders": "StockTopHolders",
                "money_flow": "StockMoneyFlow",
                "northbound": "NorthboundData",
                "dragon_tiger": "DragonTigerData",
                "hot_rank": "StockHotRank",
                "insider": "StockInsiderTrading",
                "pledge": "StockPledge",
                "shareholder": "StockShareholder",
                "forecast": "StockForecast",
                "technical": "StockIndicators",
                "block_trade": "StockBlockTrade",
                "fund_holding": "StockFundHolding",
                "margin": "StockMargin",
                "sentiment": "StockSentiment",
                "lockup_release": "StockRelease",
            }
            
            if data_type in model_map:
                generic_query_kwargs = {"start_time": start_t, "end_time": end_t}
                if columns:
                    generic_query_kwargs["columns"] = columns
                raw_data = StockTools.get_generic_db_data(
                    model_map[data_type],
                    stock_code,
                    limit,
                    **generic_query_kwargs,
                )
            else:
                # 最后的兜底：调用原始 handler (注意：旧 handler 不一定支持 time 过滤)
                handler = STOCK_QUERY_HANDLERS[data_type]
                raw_data = handler(stock_code, limit)

            serial_data = make_json_serializable(raw_data)
            if data_type in FINANCIAL_REPORT_QUERY_MODELS:
                _, table_label = FINANCIAL_REPORT_QUERY_MODELS[data_type]
                if isinstance(serial_data, list):
                    serial_data = [
                        localize_financial_report_data_field(item, table_label)
                        for item in serial_data
                    ]
                elif isinstance(serial_data, dict):
                    serial_data = localize_financial_report_data_field(serial_data, table_label)
            char_len = len(str(serial_data))
            logger.info(
                f"query_stock_data: sub-table '{data_type}' for {stock_code}, "
                f"limit: {limit}, range: [{start_t} to {end_t}], "
                f"char length: {char_len}"
            )
            results["results"][data_type] = serial_data
        except UnsupportedColumnsError as exc:
            results["results"][data_type] = exc.to_dict()
        except Exception as exc:
            logger.exception("query_stock_data failed: stock_code=%s data_type=%s error=%s", stock_code, data_type, exc)
            results["results"][data_type] = {"error": str(exc)}

    return results


@tool
def query_market_data(
    queries: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    批量查询市场级数据库数据 (Batch market-level DB query tool).
    参数:
    - queries: 查询列表 (List of dicts)，每个查询字典必须包含:
        - data_type: 必填，支持 index_daily, sector_money_flow, limit_pool, futures
        - start_time: 必填，开始时间，格式 'YYYY-MM-DD HH:MM:SS'
        - end_time: 必填，结束时间，格式 'YYYY-MM-DD HH:MM:SS'
        - identifier: 可选，对应 index_code / symbol / sector_name
        - limit: 可选，最大返回条数，默认为 20
        - columns: 可选，返回列名列表；传入时仅查询并返回这些列
        - extra_params: 可选扩展参数字典，如 limit_pool 使用 {"pool_type": "up"}

    注意: 所有子查询均强制要求 start_time 和 end_time 契约。
    示例: [{"data_type": "index_daily", "identifier": "000001.SH", "start_time": "2024-03-01 00:00:00", "end_time": "2024-03-16 23:59:59"}]
    """
    responses = []
    for query in queries:
        data_type = query.get("data_type")
        identifier = query.get("identifier")
        limit = query.get("limit", 20)
        start_t = query.get("start_time")
        end_t = query.get("end_time")
        columns = _normalize_list_arg(query.get("columns"))
        
        if not start_t or not end_t:
            responses.append({
                "error": "start_time and end_time are required for each query",
                "data_type": data_type,
            })
            continue

        params = query.get("extra_params") or {}

        config = MARKET_QUERY_CONFIG.get(data_type)
        if not config:
            responses.append({
                "error": "Unsupported market data type.",
                "unsupported_data_type": data_type,
            })
            continue

        model_name = config.get("model_name")
        try:
            generic_query_kwargs = {"start_time": start_t, "end_time": end_t}
            if columns:
                generic_query_kwargs["columns"] = columns

            if config.get("custom") == "limit_pool":
                pool_type = params.get("pool_type", "up")
                model_name = {
                    "up": "StockLimitUpPool",
                    "down": "StockLimitDownPool",
                    "zhaban": "StockZhabanPool",
                }.get(pool_type, "StockLimitUpPool")
                data = StockTools.get_generic_db_data(
                    model_name,
                    "",
                    limit=max(limit, 1),
                    **generic_query_kwargs,
                )
                serial_data = make_json_serializable(data)
                logger.info(f"query_market_data: limit pool table '{model_name}', limit: {limit}, char length: {len(str(serial_data))}")
                final_data = serial_data
            elif config.get("custom") == "futures":
                futures_type = params.get("futures_type", "internal")
                model_name = "InternalFuturesData" if futures_type == "internal" else "GlobalFuturesData"
                data = StockTools.get_generic_db_data(
                    model_name,
                    identifier or "",
                    limit,
                    **generic_query_kwargs,
                )
                serial_data = make_json_serializable(data)
                logger.info(f"query_market_data: futures table '{model_name}' for {identifier}, limit: {limit}, char length: {len(str(serial_data))}")
                final_data = serial_data
            else:
                needs_identifier = config.get("needs_identifier", True)
                if needs_identifier and not identifier:
                    responses.append({
                        "error": "identifier is required for this market data type.",
                        "data_type": data_type,
                    })
                    continue
                data = StockTools.get_generic_db_data(
                    model_name,
                    identifier or "",
                    limit,
                    **generic_query_kwargs,
                )
                serial_data = make_json_serializable(data)
                logger.info(
                    f"query_market_data: table '{config['model_name']}' for {identifier}, "
                    f"limit: {limit}, range: [{start_t} to {end_t}], "
                    f"char length: {len(str(serial_data))}"
                )
                final_data = serial_data

            responses.append({
                "data_type": data_type,
                "identifier": identifier,
                "limit": limit,
                "start_time": start_t,
                "end_time": end_t,
                "results": final_data,
            })
        except UnsupportedColumnsError as exc:
            responses.append({
                **exc.to_dict(),
                "data_type": data_type,
                "identifier": identifier,
            })
        except Exception as exc:
            logger.exception("query_market_data failed: data_type=%s identifier=%s error=%s", data_type, identifier, exc)
            responses.append({
                "error": str(exc),
                "data_type": data_type,
                "identifier": identifier,
            })

    return responses


@tool
async def sync_market_data(
    task_type: str,
    target: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    统一同步数据库数据 (Unified DB sync tool).
    优先使用该工具替代多个 sync_source_* 工具，以减少工具 schema 数量。
    参数:
    - task_type: 同步任务类型
    - target: 统一承载 stock_code / index_code / symbol
    - start_date: 开始日期，格式 YYYY-MM-DD；仅部分 task_type 需要
    - end_date: 结束日期，格式 YYYY-MM-DD；仅部分 task_type 需要
    - limit: 仅部分任务使用
    - extra_params: 少量扩展参数，如 daily_kline 的 adjust

    支持的 task_type 与参数要求:
    - stock_basic:
      - target 必填，表示 stock_code
      - 仅同步单只股票基础信息，不执行全量同步
    - daily_kline:
      - target 必填，表示 stock_code
      - start_date 必填
      - end_date 必填
      - extra_params 可选: adjust='qfq'|'hfq'
    - financial:
      - target 必填，表示 stock_code
    - valuation:
      - target 必填，表示 stock_code
      - start_date/end_date 可选
    - realtime:
      - target 必填，表示 stock_code
    - northbound:
      - target 必填，表示 stock_code
    - money_flow:
      - target 必填，表示 stock_code
    - shareholders:
      - target 必填，表示 stock_code
    - limit_pools:
      - target 不需要
      - start_date 可选；当前会被当作交易日传给涨停/跌停/炸板池同步
    - block_trade:
      - target 必填，表示 stock_code
      - start_date/end_date 可选
    - sector_flow:
      - target 必填，表示 stock_code
    - margin:
      - target 必填，表示 stock_code
    - lockup_release:
      - target 必填，表示 stock_code
    - index_daily:
      - target 必填，表示 index_code
      - start_date 必填
      - end_date 必填

    返回:
    - success: 是否成功
    - task_type: 本次执行的任务类型
    - target: 传入目标
    - resolved_method: 最终调用的 ingestor_manager 方法名
    - message / error: 执行结果说明
    """
    config = SYNC_TASK_CONFIG.get(task_type)
    if not config:
        return {
            "success": False,
            "error": "Unsupported sync task type.",
            "task_type": task_type,
            "supported_task_types": sorted(SYNC_TASK_CONFIG.keys()),
        }

    params = dict(extra_params or {})
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    if limit is not None:
        params["limit"] = limit

    try:
        if config.get("custom") == "limit_pools":
            trade_date = params.get("start_date")
            res1 = await ingestor_manager.fetch_and_ingest_stock_limit_up_pool(trade_date)
            res2 = await ingestor_manager.fetch_and_ingest_stock_limit_down_pool(trade_date)
            res3 = await ingestor_manager.fetch_and_ingest_stock_zhaban_pool(trade_date)
            success = any([res1, res2, res3])
            resolved_method = "fetch_and_ingest_stock_limit_*_pool"
        else:
            target_param = config.get("target_param")
            if target_param and not target:
                if config.get("allow_empty_target") and config.get("all_method_name"):
                    method = getattr(ingestor_manager, config["all_method_name"])
                    success = await method()
                    resolved_method = config["all_method_name"]
                else:
                    return {
                        "success": False,
                        "error": f"target is required for task_type={task_type}",
                        "task_type": task_type,
                    }
            else:
                method = getattr(ingestor_manager, config["method_name"], None)
                if not method:
                    return {
                        "success": False,
                        "error": f"Sync method {config['method_name']} not found.",
                        "task_type": task_type,
                    }

                call_kwargs: Dict[str, Any] = {}
                if target_param:
                    call_kwargs[target_param] = target

                required_params = config.get("required_params", [])
                missing_params = [name for name in required_params if not params.get(name)]
                if missing_params:
                    return {
                        "success": False,
                        "error": f"Missing required params: {', '.join(missing_params)}",
                        "task_type": task_type,
                    }

                for name in required_params + config.get("optional_params", []) + config.get("extra_params", []):
                    if name in params and params[name] is not None:
                        call_kwargs[name] = params[name]

                success = await method(**call_kwargs)
                resolved_method = config["method_name"]

        return make_json_serializable({
            "success": bool(success),
            "task_type": task_type,
            "target": target,
            "resolved_method": resolved_method,
            "message": "sync completed" if success else "sync completed but returned False or None",
        })
    except Exception as exc:
        logger.exception("sync_market_data failed: task_type=%s target=%s error=%s", task_type, target, exc)
        return {
            "success": False,
            "task_type": task_type,
            "target": target,
            "error": str(exc),
        }


@tool
async def get_database_schema() -> Dict[str, Any]:
    """
    获取数据库所有表的完整结构信息 (Get full schema information for all database tables).

    该工具用于让 LLM 探索现有的数据表定义。它不需要任何参数。
    它会动态扫描 `app.models.data_storage.py` 中定义的 50+ 个 SQLAlchemy 模型。

    返回结果是一个包含 `schemas` 字典的对象：
    - Key: 表名 (Table Name)，如 'KlineData'
    - Value: 列定义列表 (List of Column Definitions)，每列包含：
        - `name`: 字段名称
        - `type`: 数据库类型 (如 INTEGER, FLOAT, DATE, JSONB)
        - `nullable`: 是否允许为空
        - `primary_key`: 是否为主键
        - `doc`: 字段的说明文档（如有）

    使用场景：在规划复杂的跨表查询、确认字段名称或理解数据维度之前调用。
    """
    import app.models.data_storage as models
    from sqlalchemy import inspect

    try:
        # 识别所有定义了 __tablename__ 的模型类
        available_models = {
            name: obj for name, obj in vars(models).items()
            if isinstance(obj, type) and hasattr(obj, "__tablename__")
        }
        
        all_schemas = {}
        for table_name, model in available_models.items():
            mapper = inspect(model)
            columns = []
            for column in mapper.attrs:
                if hasattr(column, "columns"):
                    col_obj = column.columns[0]
                    columns.append({
                        "name": column.key,
                        "type": str(col_obj.type),
                        "nullable": col_obj.nullable,
                        "primary_key": col_obj.primary_key,
                        "doc": getattr(column, "doc", "") or ""
                    })
            all_schemas[table_name] = columns

        return {"schemas": all_schemas}
    except Exception as exc:
        logger.exception("get_database_schema failed: %s", exc)
        return {"error": str(exc)}


@tool
async def query_and_calculate(
    table_name: str,
    filters: List[Dict[str, Any]],
    compute_code: str,
    limit: int = 100
) -> Dict[str, Any]:
    """
    通用数据库查询与动态计算工具 (Generic DB Query and Dynamic Calculation Tool).
    
    **重要指令：在调用此工具前，除非你已经非常确定目标表的字段名称和类型，否则必须先调用 `get_database_schema` 获取表结构。严禁凭空猜测字段名。**

    该工具首先根据过滤条件从数据库拉取数据，然后在受限的 Python 沙箱中执行计算逻辑（具体限制条件参见 `execute_python_sandboxed` 说明）。
    **注意：该工具不返回原始行数据，仅返回沙箱执行报告。**

    参数:
    - table_name: 字符串。目标 SQLAlchemy 模型名（如 'KlineData', 'StockBasic', 'FinancialIndicator'）。
    - filters: 列表。过滤条件字典的集合。格式示例:
      `[{"column": "stock_code", "op": "==", "value": "000001.SZ"}, {"column": "date", "op": ">=", "value": "2024-01-01"}]`
      支持的操作符 (op):
      - `==`: 等于
      - `!=`: 不等于
      - `>`: 大于
      - `<`: 小于
      - `>=`: 大于等于
      - `<=`: 小于等于
      - `in`: 包含在列表中 (value 应为列表)
      - `like`: 模糊匹配 (如 '600%')
    - compute_code: 字符串。要在沙箱中运行的 Python 代码。
      - **数据注入方式**：系统会自动在你的代码前添加 `import json` 并定义 `data` 变量。
      - `data` 是通过 `json.loads()` 解析自数据库查询结果的列表 (List[Dict])。
      - 常用库（numpy, pandas, math, datetime, json）已在沙箱中可用。
    - limit: 整数。为了防止内存溢出，数据库查询的最大行数限制。默认 100。

    计算示例 (compute_code):
    ```python
    import numpy as np
    # 系统已自动注入 data 变量 (List[Dict])，并 import json
    # data 结构说明：每个字典代表数据库的一行，Key 为表的列名 (如 d['close'], d['stock_code'])
    closes = [float(d['close']) for d in data if d.get('close') is not None]
    avg_close = float(np.mean(closes)) if closes else None
    ```

    推荐的可审计结果结构:
    ```python
    {
        "scope": {
            "stock_code": "000001.SZ",
            "table": "KlineData",
            "date_range": [start_date, end_date],
        },
        "row_count": len(data),
    }
    ```
    字段要求:
    - 最低限度必填: `scope`, `row_count`。
    - `scope` 的内部字段按任务类型调整，不强制固定键名。
    - 不限制数据输出形式；可以按任务需要输出额外字段。
    使用这种结构可以让模型不查看全量 K 线、资金流或财务明细，也能知道计算范围、样本数量、
    以及按任务需要返回的计算结果。

    提示：
    1. 你可以使用 numpy (np), pandas (pd), math, datetime 等常用库。
    2. 系统会自动定义 `data` 变量，其内容为数据库查询出的原始行（已转为字典列表）。
    3. 工具将返回完整的计算执行报告（包含 success、stdout、stderr、error、metadata 等状态）。
    4. 注意：数据库中的某些数值可能为 null，请务必在代码中处理空值，确保健壮性。
    """
    import app.models.data_storage as models
    from app.core.database import SessionLocal
    from sqlalchemy import and_, or_
    import json

    model = getattr(models, table_name, None)
    if not model:
        return {"error": f"Table '{table_name}' not found."}

    try:
        with SessionLocal() as db:
            query = db.query(model)
            
            # 构建过滤条件
            ops = {
                "==": lambda col, val: col == val,
                "!=": lambda col, val: col != val,
                ">": lambda col, val: col > val,
                "<": lambda col, val: col < val,
                ">=": lambda col, val: col >= val,
                "<=": lambda col, val: col <= val,
                "in": lambda col, val: col.in_(val),
                "like": lambda col, val: col.like(val)
            }

            filter_clauses = []
            for f in filters:
                col_name = f.get("column")
                op = f.get("op")
                val = f.get("value")
                
                if not hasattr(model, col_name):
                    return {"error": f"Column '{col_name}' not found in table '{table_name}'"}
                
                if op not in ops:
                    return {"error": f"Unsupported operator: {op}"}
                
                col_attr = getattr(model, col_name)
                filter_clauses.append(ops[op](col_attr, val))
            
            if filter_clauses:
                query = query.filter(and_(*filter_clauses))
            
            # 执行查询
            records = query.limit(limit).all()
            # 序列化结果供计算使用
            data_for_calc = [
                {k: v for k, v in r.__dict__.items() if not k.startswith('_')}
                for r in records
            ]

            # 准备注入沙箱的代码
            # 将 data 以 JSON 字符串形式定义在代码头部
            data_json = json.dumps(make_json_serializable(data_for_calc), ensure_ascii=False)
            # Use Python repr for the JSON payload so backslashes/quotes survive code injection intact.
            full_code = f"import json\ndata = json.loads({data_json!r})\n{compute_code}"

            # 执行计算
            raw_calc_res = await execute_python_in_sandbox(full_code)

            # 直接返回完整的执行报告，包含 success, stdout, stderr 等
            return raw_calc_res

    except Exception as exc:
        logger.exception("query_and_calculate failed: %s", exc)
        return {"error": str(exc)}


async def execute_trading_order(
    stock_code: str,
    action: str,
    target_position: float,
    session_id: str,
    stop_loss: float,
) -> Dict[str, Any]:
    """
    执行股票交易下单工具 (Execute stock trading order).
    
    该工具由投资经理(Portfolio Manager)在做出决策后调用，用于将决策转化为模拟交易订单。

    参数:
    - stock_code: 股票代码，如 '600519.SH'
    - action: 'buy' 或 'sell'
    - target_position: 目标仓位比例 (0.0 - 1.0)。
      - 如果是 'buy'，则计算达到目标比例所需买入的股数。
      - 如果是 'sell'，则计算达到目标比例所需卖出的股数。
      - target_position = 0 即为全额清仓。
    - session_id: 当前会话 ID (必须从 Context 中准确获取)，用于关联账户和持久化。
    - stop_loss: 止损价，必填。成交后会直接写入持仓 `purchase_details.stop_loss`。

    注意:
    1. 自动执行 A 股交易规则：买入必须是 100 的整数倍；卖出减仓时尽量取 100 倍数。
    2. 自动检查 T+1 可用持仓限制，卖出数量不会超过可用股数。
    3. 仅在系统配置的 ENABLE_AUTO_TRADE 为开启状态时写入模拟交易账本。
    4. `stop_loss` 为必填字段，必须传入明确的正数止损价。
    5. 若返回 `success=false`，你必须先阅读 `reason`，再决定是否调整 `target_position`
       继续调用本工具，或停止交易并输出最终报告。
    """
    from app.trading.service import trading_service
    from app.models.session import Session as DbSession
    from app.models.user import User
    from app.models.position import Position
    from app.core.database import SessionLocal
    from app.models.data_storage import StockRealtimeMarket
    from sqlalchemy import desc
    import app.core.config as config

    if not config.settings.ENABLE_AUTO_TRADE:
        return _format_trade_execution_result({"error": "Auto-trade is disabled in system settings."})

    if stop_loss <= 0:
        return _format_trade_execution_result({"error": f"Invalid stop_loss: {stop_loss}. stop_loss must be greater than 0."})

    session_uuid = None
    try:
        from uuid import UUID
        session_uuid = UUID(session_id)
    except Exception:
        return _format_trade_execution_result({"error": f"Invalid session_id format: {session_id}"})

    try:
        with SessionLocal() as db:
            # 1. 获取会话和用户信息
            session_model = db.query(DbSession).filter(DbSession.session_id == session_uuid).first()
            if not session_model:
                return _format_trade_execution_result({"error": f"Session {session_id} not found in database."})
            
            user = db.query(User).filter(User.id == session_model.user_id).first()
            if not user or not user.account:
                return _format_trade_execution_result({"error": "Associated User or Account not found."})
            
            account = user.account
            total_assets = float(account.total_assets or 0)
            
            # 2. 获取最新价格
            price = 0.0
            latest_market = db.query(StockRealtimeMarket).filter(
                StockRealtimeMarket.stock_code == stock_code
            ).order_by(desc(StockRealtimeMarket.timestamp)).first()
            
            if latest_market:
                price = float(latest_market.current_price)
            
            if price <= 0:
                # 尝试从 Kline 补全
                from app.models.data_storage import KlineData
                latest_kline = db.query(KlineData).filter(
                    KlineData.stock_code == stock_code
                ).order_by(desc(KlineData.date)).first()
                if latest_kline:
                    price = float(latest_kline.close)

            if price <= 0:
                return _format_trade_execution_result({"error": f"Could not determine a valid price for {stock_code}. Trade aborted."})

            # 3. 计算目标总股数
            target_total_shares = (total_assets * target_position) / price
            
            # 4. 获取当前持仓
            pos = db.query(Position).filter(
                Position.account_id == account.account_id,
                Position.stock_code == stock_code
            ).first()
            current_total_shares = pos.total_shares if pos else 0
            current_available_shares = (
                trading_engine.build_position_snapshot(pos)["available_shares"]
                if pos else 0
            )
            
            # 5. 计算差额
            diff_shares = target_total_shares - current_total_shares
            suggested_shares = 0
            
            # 6. 执行 A 股规则
            act = action.lower()
            if act == "buy" and diff_shares > 0:
                # 买入：向下取整到 100 的倍数
                suggested_shares = (int(diff_shares) // 100) * 100
            elif act == "sell" and diff_shares < 0:
                if target_position == 0:
                    # 清仓：卖出所有可用
                    suggested_shares = (int(current_available_shares) // 100) * 100
                else:
                    # 减仓：尽量取 100 倍数，不超可用
                    abs_diff = abs(diff_shares)
                    suggested_shares = min((int(abs_diff) // 100) * 100, current_available_shares)
            
            if suggested_shares <= 0 and not (act == "sell" and target_position == 0 and current_available_shares > 0):
                # 构建更详细的拒绝理由
                reason = "Rounding (less than 100 shares)" if abs(diff_shares) > 0 else "Target position already met"
                if act == "sell" and current_available_shares == 0 and current_total_shares > 0:
                    reason = "No available shares (T+1 lock)"
                
                return {
                    **_format_trade_execution_result({
                        "success": False,
                        "message": f"Trade skipped: {reason}. Suggested shares is 0.",
                        "details": {
                            "action": act,
                            "stock_code": stock_code,
                            "price": price,
                            "target_position": target_position,
                            "stop_loss": stop_loss,
                            "current_total_shares": current_total_shares,
                            "current_available_shares": current_available_shares,
                            "available_cash": float(account.available_cash or 0),
                            "diff_shares_raw": float(diff_shares),
                            "suggested_shares": suggested_shares,
                            "total_assets": total_assets
                        }
                    }),
                    "details": {
                        "action": act,
                        "stock_code": stock_code,
                        "price": price,
                        "target_position": target_position,
                        "stop_loss": stop_loss,
                        "current_total_shares": current_total_shares,
                        "current_available_shares": current_available_shares,
                        "available_cash": float(account.available_cash or 0),
                        "diff_shares_raw": float(diff_shares),
                        "suggested_shares": suggested_shares,
                        "total_assets": total_assets
                    }
                }

            # 7. 调用交易服务
            trade_result = await trading_service.execute_order_and_update_db(
                db=db,
                session_id=session_uuid,
                account=account,
                stock_code=stock_code,
                action=act,
                shares=suggested_shares,
                price=price,
                order_type="market",
                stop_loss=stop_loss,
            )
            
            return _format_trade_execution_result(trade_result)

    except Exception as exc:
        logger.exception("execute_trading_order failed: %s", exc)
        return _format_trade_execution_result({"error": str(exc)})


def get_all_tools() -> List[Any]:
    """
    获取所有已定义的 AI Agent 工具列表 (Get all defined AI Agent tools).
    以少数统一入口暴露查询与同步能力，减少 tool schema 和 token 消耗。
    """
    return [
        query_stock_data,
        query_market_data,
        sync_market_data,
        execute_python_sandboxed,
        browse_web_page_html,
        parse_pdf_to_markdown,
        search_news,
        get_database_schema,
        query_and_calculate,
    ]


def get_stock_analysis_tools() -> List[Any]:
    """
    获取 AI 投研分析允许调用的基础工具列表。

    Returns:
        仅包含网页、新闻、PDF 和 Python 沙箱能力的工具列表。
    """
    return [
        execute_python_sandboxed,
        browse_web_page_html,
        parse_pdf_to_markdown,
        search_news,
    ]
