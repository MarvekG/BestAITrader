import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from app.ai.agentic.tooling.browser_tool import browse_web_page_html
from app.ai.agentic.tooling.db_utils import coerce_filter_value_for_column
from app.ai.agentic.tooling.news_tool import search_news
from app.ai.agentic.tooling.pdf_tool import parse_pdf_to_markdown
from app.ai.agentic.tooling.python_sandbox import PY_SANDBOX_CODE_MAX_CHARS, execute_python_in_sandbox
from app.ai.agentic.tooling.stock_tools import StockTools, UnsupportedColumnsError
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.data.ingestors.manager import ingestor_manager
from app.data.metadata.field_units import get_table_field_units
from app.trading.trading_engine import TradingEngine

logger = get_logger(__name__)
trading_engine = TradingEngine()


def _get_agentic_db_models() -> Dict[str, Any]:
    """获取 Agent 数据库工具可访问的 SQLAlchemy 模型映射。"""
    import app.models.data_storage as storage_models
    import app.models.stock_indicators as indicator_models

    model_modules = (storage_models, indicator_models)
    models = {
        name: obj
        for module in model_modules
        for name, obj in vars(module).items()
        if isinstance(obj, type) and hasattr(obj, "__tablename__")
    }
    return models


def _resolve_agentic_db_model(table_name: str) -> Any:
    """按模型名或兼容别名解析 Agent 数据库工具可访问的模型。"""
    return _get_agentic_db_models().get(table_name)


@tool
async def get_current_time() -> Dict[str, Any]:
    """
    获取当前系统时间 (Get current system time).

    返回当前 UTC 时间和本地时间，包含 ISO 8601 格式、时间戳和时区信息。
    适用于需要时间基准的分析、计算或记录场景。

    Returns:
        当前时间信息字典，包含 utc_iso、local_iso、timestamp、timezone、weekday。
    """
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()
    return {
        "utc_iso": now_utc.isoformat(),
        "local_iso": now_local.isoformat(),
        "timestamp": now_utc.timestamp(),
        "timezone": str(now_local.astimezone().tzinfo),
        "weekday": now_local.strftime("%A"),
    }


def _translate_schema_info_key(value: Any) -> str:
    """翻译 SQLAlchemy Column.info 中保存的 i18n key。

    Args:
        value: 单个 i18n key，或需要顺序拼接的 i18n key 列表。

    Returns:
        当前系统语言下的翻译文本；无有效 key 时返回空字符串。
    """
    if isinstance(value, str):
        return i18n_service.t(value)
    if isinstance(value, list):
        return "".join(i18n_service.t(item) for item in value if isinstance(item, str))
    return ""


def _format_trade_execution_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将交易服务结果整理为 Agent 工具可读的统一结构。

    Args:
        payload: 交易服务或前置校验返回的原始结果。

    Returns:
        包含执行状态、展示消息、机器可读原因和详情的工具返回值。
    """
    if payload.get("success") is True:
        order = payload.get("order")
        status = payload.get("status") or getattr(order, "status", None)
        execution_status = "executed"
        if status == "pending":
            execution_status = "pending"
        elif status == "cancelled":
            execution_status = "cancelled"
        return {
            "success": True,
            "execution_status": execution_status,
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


def _build_trade_gate_rejection(reason: str, message: str, details: Dict[str, Any]) -> Dict[str, Any]:
    """构建 PM 交易决策门禁拒绝结果。

    Args:
        reason: 机器可读的拒绝原因。
        message: 给 PM 阅读的拒绝说明。
        details: 触发门禁时的关键上下文字段。

    Returns:
        统一格式化后的工具返回值。
    """
    return _format_trade_execution_result(
        {
            "success": False,
            "reason": reason,
            "message": message,
            "gate": "pm_decision_quality_gate",
            "details": make_json_serializable(details),
        }
    )


async def _resolve_latest_stock_price(stock_code: str) -> Dict[str, Any]:
    """查询股票最新价，供 PM 下单前确定限价基准。

    Args:
        stock_code: 股票代码。

    Returns:
        包含最新价格、行情时间和查询成功状态的字典。
    """
    from app.data.storage import data_storage_service

    try:
        market_data = await data_storage_service.get_stock_realtime_market(stock_code)
    except Exception as exc:
        logger.exception("Failed to query latest stock price", extra={"stock_code": stock_code, "error": str(exc)})
        return {"success": False, "latest_price": None, "market_time": None}

    latest_price = market_data.get("latest_price") if market_data else None
    try:
        price = float(latest_price) if latest_price not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        return {
            "success": False,
            "latest_price": None,
            "market_time": market_data.get("update_time") if market_data else None,
        }
    return {
        "success": True,
        "latest_price": price,
        "market_time": market_data.get("update_time") if market_data else None,
    }


@tool
async def get_pm_order_type_guidance(stock_code: str) -> Dict[str, Any]:
    """查询 PM 当前应使用市价单还是限价单。

    Args:
        stock_code: 本轮准备交易的股票代码。

    Returns:
        当前是否交易时间、建议订单类型，以及非交易时间可直接使用的限价价格。
    """
    from app.data.market_utils import is_trading_time

    stock_code = str(stock_code or "").strip()
    if not stock_code:
        return {
            "success": False,
            "reason": "missing_stock_code",
            "message": "stock_code is required to determine order type guidance.",
        }

    trading_time = bool(is_trading_time())
    price_result = await _resolve_latest_stock_price(stock_code)
    latest_price = price_result.get("latest_price")
    recommended_order_type = "market" if trading_time else "limit"
    return {
        "success": bool(trading_time or price_result["success"]),
        "stock_code": stock_code,
        "is_trading_time": trading_time,
        "market_order_allowed": trading_time,
        "recommended_order_type": recommended_order_type,
        "latest_price": latest_price,
        "limit_price": latest_price if not trading_time else None,
        "market_time": price_result.get("market_time"),
        "message": (
            "Trading time: use a market order for immediate execution."
            if trading_time else
            "Not trading time: use a limit order with limit_price set to latest_price."
        ),
        "reason": None if trading_time or price_result["success"] else "latest_price_unavailable",
    }


def _evaluate_pm_trade_gate(
    *,
    action: str,
    target_position: float,
    current_position: float,
    price: float,
    stop_loss: float,
    take_profit: Optional[float],
    current_total_shares: int,
    current_available_shares: int,
) -> Dict[str, Any] | None:
    """校验 PM 交易工具调用是否与当前仓位和基础风控一致。

    Args:
        action: PM 调用工具时传入的动作。
        target_position: PM 设定的目标仓位比例。
        current_position: 当前目标股票仓位比例。
        price: 本次交易的价格基准。
        stop_loss: PM 设定的止损价。
        take_profit: PM 设定的止盈价；缺失时不做止盈门禁。
        current_total_shares: 当前总持仓数量。
        current_available_shares: 当前真实可卖数量。

    Returns:
        若应拒绝交易，返回统一工具结果；否则返回 None。
    """
    act = str(action or "").strip().lower()
    details = {
        "action": act,
        "target_position": target_position,
        "current_position": current_position,
        "price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "current_total_shares": current_total_shares,
        "current_available_shares": current_available_shares,
    }

    if act not in {"buy", "sell"}:
        return _build_trade_gate_rejection(
            "invalid_trade_action",
            "Trade gate rejected the order: action must be 'buy' or 'sell'. Use no trade tool for hold.",
            details,
        )

    if target_position < 0 or target_position > 1:
        return _build_trade_gate_rejection(
            "invalid_target_position",
            "Trade gate rejected the order: target_position must be between 0 and 1.",
            details,
        )

    tolerance = 1e-6
    if act == "buy" and target_position <= current_position + tolerance:
        return _build_trade_gate_rejection(
            "decision_target_mismatch",
            "Trade gate rejected the buy: target_position must be greater than current_position.",
            details,
        )

    if act == "sell" and target_position >= current_position - tolerance:
        return _build_trade_gate_rejection(
            "decision_target_mismatch",
            "Trade gate rejected the sell: target_position must be lower than current_position.",
            details,
        )

    if act == "buy" and stop_loss >= price:
        return _build_trade_gate_rejection(
            "invalid_buy_stop_loss",
            "Trade gate rejected the buy: stop_loss must be below the order price.",
            details,
        )

    if act == "buy" and take_profit is not None and take_profit <= price:
        return _build_trade_gate_rejection(
            "invalid_buy_take_profit",
            "Trade gate rejected the buy: take_profit must be above the order price.",
            details,
        )

    return None


STOCK_QUERY_HANDLERS = {
    "status": lambda stock_code, limit: StockTools.check_data_status(stock_code),
    "basic": lambda stock_code, limit: StockTools.get_stock_basic_info(stock_code),
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

FINANCIAL_FETCH_CONFIG = {
    "financial_indicator": {
        "method_name": "fetch_and_ingest_financial_indicators",
        "description": "财务指标",
    },
    "income_statement": {
        "method_name": "fetch_and_ingest_income_statement",
        "description": "利润表",
    },
    "balance_sheet": {
        "method_name": "fetch_and_ingest_balance_sheet",
        "description": "资产负债表",
    },
    "cashflow_statement": {
        "method_name": "fetch_and_ingest_cashflow_statement",
        "description": "现金流量表",
    },
}


def make_json_serializable(obj: Any) -> Any:
    """
    递归将对象转换为 JSON 可序列化的格式。

    Args:
        obj: 任意待序列化对象。

    Returns:
        JSON 兼容的数据结构。
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    if hasattr(obj, "__dict__"):
        return {
            key: make_json_serializable(value)
            for key, value in obj.__dict__.items()
            if not key.startswith("_")
        }
    return obj


def _normalize_date_arg(value: Optional[str]) -> Optional[str]:
    """规范化工具入参日期。

    Args:
        value: 用户传入的日期字符串，支持 ``YYYYMMDD`` 或 ``YYYY-MM-DD``。

    Returns:
        ``YYYYMMDD`` 格式日期；未传入时返回 None。

    Raises:
        ValueError: 日期格式不符合要求。
    """
    if value is None:
        return None
    normalized = str(value).strip().replace("-", "")
    if not normalized:
        return None
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("date must use YYYYMMDD or YYYY-MM-DD format") from exc
    return normalized


def _sort_financial_records(records: Any) -> List[Dict[str, Any]]:
    """按报告期倒序整理财务采集结果。

    Args:
        records: 数据源返回的原始记录列表。

    Returns:
        仅保留字典记录并按报告期、公告日和报告类型倒序排列。
    """
    normalized = [record for record in records or [] if isinstance(record, dict)]
    return sorted(
        normalized,
        key=lambda item: (
            str(item.get("report_date") or ""),
            str(item.get("announcement_date") or ""),
            str(item.get("report_type") or ""),
        ),
        reverse=True,
    )


def _compact_order_id(order_id: Any) -> str:
    """
    生成便于比较的紧凑订单 ID。

    Args:
        order_id: 订单 UUID 或字符串。

    Returns:
        去除短横线后的订单 ID。
    """
    return str(order_id).replace("-", "")


async def _resolve_order_by_llm_id(db: Any, order_model: Any, account_id: Any, raw_order_id: str) -> Any:
    """
    按订单 ID 解析当前账户待撤订单。

    Args:
        db: 数据库会话。
        order_model: 订单模型类。
        account_id: 当前账户 ID。
        raw_order_id: LLM 传入的订单 ID。

    Returns:
        匹配到的订单模型；未匹配或存在多个候选时返回 None。
    """
    from uuid import UUID

    from sqlalchemy import select

    cleaned_order_id = str(raw_order_id or "").strip()
    try:
        order_uuid = UUID(cleaned_order_id)
    except Exception:
        compact_order_id = cleaned_order_id.replace("-", "").lower()
        if not compact_order_id:
            return None
        candidates = (await db.execute(
            select(order_model).where(
                order_model.account_id == account_id,
                order_model.status == "pending",
            )
        )).scalars().all()
        matches = [
            order
            for order in candidates
            if _compact_order_id(order.order_id).lower().startswith(compact_order_id)
        ]
        return matches[0] if len(matches) == 1 else None

    return (await db.execute(
        select(order_model).where(
            order_model.order_id == order_uuid,
            order_model.account_id == account_id,
        )
    )).scalar_one_or_none()


@tool
async def execute_python_sandboxed(code: str) -> Dict[str, Any]:
    """
    在受限沙箱中执行 Python 计算代码 (Execute Python code in a compute-only sandbox).
    适合一次性数值计算、日期处理、JSON 解析、列表/字典处理、numpy/pandas 分析。
    使用纪律:
    - 可以充分利用 Python 做计算、数据处理、JSON/文本解析、聚合、校验和逻辑判断。
    - 代码和 stdout 不允许包含叙事性 print、Markdown、emoji、核验过程长文或报告式结论文字。
    - 不允许把已知结论硬编码成多行说明。
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
      columns 必须使用 SQLAlchemy/数据库模型的真实列名；不接受展示层或数据源原始别名。
      例如 kline 使用 change_percent 和 turnover，不使用 pct_chg 或 amount。
      示例:
      {"kline": {"limit": 100, "start_time": "2024-01-01 00:00:00",
      "end_time": "2024-03-01 23:59:59", "columns": ["date", "close", "change_percent", "turnover"]}}
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
                "valuation": "StockValuationHistory",
                "kline": "KlineData",
                "top_holders": "StockTopHolders",
                "money_flow": "StockMoneyFlow",
                "northbound": "NorthboundData",
                "dragon_tiger": "DragonTigerData",
                "hot_rank": "StockHotRank",
                "insider": "StockInsider",
                "pledge": "StockPledge",
                "shareholder": "StockShareholder",
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
                raw_data = await StockTools.get_generic_db_data(
                    model_map[data_type],
                    stock_code,
                    limit,
                    **generic_query_kwargs,
                )
            else:
                # 最后的兜底：调用原始 handler (注意：旧 handler 不一定支持 time 过滤)
                handler = STOCK_QUERY_HANDLERS[data_type]
                raw_data = await handler(stock_code, limit)

            serial_data = make_json_serializable(raw_data)
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
async def query_market_data(
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
        - columns: 可选，返回列名列表；传入时仅查询并返回这些列。必须使用数据库模型真实列名。
        - extra_params: 可选扩展参数字典，如 limit_pool 使用 {"pool_type": "up"}

    注意: 所有子查询均强制要求 start_time 和 end_time 契约。
    示例:
    [{"data_type": "index_daily", "identifier": "000001.SH",
      "start_time": "2024-03-01 00:00:00", "end_time": "2024-03-16 23:59:59"}]
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
                data = await StockTools.get_generic_db_data(
                    model_name,
                    "",
                    limit=max(limit, 1),
                    **generic_query_kwargs,
                )
                serial_data = make_json_serializable(data)
                logger.info(
                    "query_market_data: limit pool result",
                    extra={
                        "model_name": model_name,
                        "limit": limit,
                        "char_length": len(str(serial_data)),
                    },
                )
                final_data = serial_data
            elif config.get("custom") == "futures":
                futures_type = params.get("futures_type", "internal")
                model_name = "InternalFuturesData" if futures_type == "internal" else "GlobalFuturesData"
                data = await StockTools.get_generic_db_data(
                    model_name,
                    identifier or "",
                    limit,
                    **generic_query_kwargs,
                )
                serial_data = make_json_serializable(data)
                logger.info(
                    "query_market_data: futures result",
                    extra={
                        "model_name": model_name,
                        "identifier": identifier,
                        "limit": limit,
                        "char_length": len(str(serial_data)),
                    },
                )
                final_data = serial_data
            else:
                needs_identifier = config.get("needs_identifier", True)
                if needs_identifier and not identifier:
                    responses.append({
                        "error": "identifier is required for this market data type.",
                        "data_type": data_type,
                    })
                    continue
                data = await StockTools.get_generic_db_data(
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
            logger.exception(
                "query_market_data failed",
                extra={"data_type": data_type, "identifier": identifier, "error": str(exc)},
            )
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
    财务数据不要使用该工具；需要财务指标、利润表、资产负债表或现金流量表时调用 fetch_financial_data。
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
    - data: 同步的数据列表
    - count: 数据行数
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

            # 合并三个池子的数据
            all_data = []
            total_count = 0
            if res1 and res1.get("success"):
                all_data.extend(res1.get("data", []))
                total_count += res1.get("count", 0)
            if res2 and res2.get("success"):
                all_data.extend(res2.get("data", []))
                total_count += res2.get("count", 0)
            if res3 and res3.get("success"):
                all_data.extend(res3.get("data", []))
                total_count += res3.get("count", 0)

            success = any([res1, res2, res3])
            resolved_method = "fetch_and_ingest_stock_limit_*_pool"
            result_data = all_data
            result_count = total_count
        else:
            target_param = config.get("target_param")
            if target_param and not target:
                if config.get("allow_empty_target") and config.get("all_method_name"):
                    method = getattr(ingestor_manager, config["all_method_name"])
                    result = await method()
                    resolved_method = config["all_method_name"]

                    # 处理返回值
                    if isinstance(result, dict):
                        success = result.get("success", False)
                        result_data = result.get("data", [])
                        result_count = result.get("count", 0)
                    else:
                        success = bool(result)
                        result_data = []
                        result_count = 0
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

                result = await method(**call_kwargs)
                resolved_method = config["method_name"]

                # 处理返回值
                if isinstance(result, dict):
                    success = result.get("success", False)
                    result_data = result.get("data", [])
                    result_count = result.get("count", 0)
                else:
                    success = bool(result)
                    result_data = []
                    result_count = 0

        return make_json_serializable({
            "success": bool(success),
            "task_type": task_type,
            "target": target,
            "resolved_method": resolved_method,
            "data": result_data,
            "count": result_count,
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
async def fetch_financial_data(
    stock_code: str,
    table_type: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """实时抓取单只股票的一类财务数据。

    财务数据按季度披露，抓取日期范围应至少覆盖半年以上，避免窗口过窄导致没有返回数据。

    Args:
        stock_code: 股票代码，如 ``600519.SH``。
        table_type: 财务数据类型，支持 financial_indicator、income_statement、balance_sheet、cashflow_statement。
        start_date: 开始日期，支持 ``YYYYMMDD`` 或 ``YYYY-MM-DD``；不传则由数据源默认处理。
        end_date: 结束日期，支持 ``YYYYMMDD`` 或 ``YYYY-MM-DD``；不传则由数据源默认处理。

    Returns:
        实时采集结果，包含数据源返回记录、记录数、日期范围和实际调用的方法名。
    """
    normalized_stock_code = str(stock_code or "").strip()
    normalized_table_type = str(table_type or "").strip()
    config = FINANCIAL_FETCH_CONFIG.get(normalized_table_type)
    if not normalized_stock_code:
        return {
            "success": False,
            "error": "stock_code is required.",
            "supported_table_types": sorted(FINANCIAL_FETCH_CONFIG.keys()),
        }
    if config is None:
        return {
            "success": False,
            "error": "Unsupported table_type.",
            "table_type": table_type,
            "supported_table_types": sorted(FINANCIAL_FETCH_CONFIG.keys()),
        }

    try:
        normalized_start_date = _normalize_date_arg(start_date)
        normalized_end_date = _normalize_date_arg(end_date)
    except ValueError as exc:
        return {
            "success": False,
            "error": str(exc),
            "stock_code": normalized_stock_code,
            "table_type": normalized_table_type,
        }

    method_name = config["method_name"]
    try:
        method = getattr(ingestor_manager, method_name)
        result = await method(normalized_stock_code, normalized_start_date, normalized_end_date)
        if isinstance(result, dict):
            success = bool(result.get("success"))
            raw_records = result.get("data") or []
            source_count = result.get("count")
            error = result.get("error")
        else:
            success = bool(result)
            raw_records = []
            source_count = None
            error = None

        records = _sort_financial_records(raw_records)
        return {
            "success": success,
            "stock_code": normalized_stock_code,
            "table_type": normalized_table_type,
            "table_description": config["description"],
            "start_date": normalized_start_date,
            "end_date": normalized_end_date,
            "resolved_method": method_name,
            "source_count": source_count if source_count is not None else len(raw_records),
            "count": len(records),
            "data": make_json_serializable(records),
            "error": error,
        }
    except Exception as exc:
        logger.exception(
            "fetch_financial_data failed",
            extra={
                "stock_code": normalized_stock_code,
                "table_type": normalized_table_type,
                "error": str(exc),
            },
        )
        return {
            "success": False,
            "error": str(exc),
            "stock_code": normalized_stock_code,
            "table_type": normalized_table_type,
            "resolved_method": method_name,
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
        - `display_name`: SQLAlchemy Column.info.name 对应的字段名称翻译
        - `unit`: SQLAlchemy Column.info.unit 对应的字段单位翻译

    返回结果还包含 `field_units` 字典，用于展示 JSONB payload 或未展开指标字段的单位。

    使用场景：在规划复杂的跨表查询、确认字段名称或理解数据维度之前调用。
    """
    from sqlalchemy import inspect

    try:
        available_models = _get_agentic_db_models()

        all_schemas = {}
        all_field_units = {}
        for table_name, model in available_models.items():
            mapper = inspect(model)
            table = getattr(model, "__table__", None)
            db_table_name = getattr(model, "__tablename__", table_name)
            schema_name = getattr(table, "schema", None)
            unit_table_name = f"{schema_name}.{db_table_name}" if schema_name else db_table_name
            columns = []
            for column in mapper.attrs:
                if hasattr(column, "columns"):
                    col_obj = column.columns[0]
                    column_schema = {
                        "name": column.key,
                        "type": str(col_obj.type),
                        "display_name": _translate_schema_info_key(col_obj.info.get("name")),
                    }
                    unit = _translate_schema_info_key(col_obj.info.get("unit"))
                    if unit:
                        column_schema["unit"] = unit
                    columns.append(column_schema)
            all_schemas[table_name] = columns
            table_unit_metadata = {
                column["name"]: {"unit": column["unit"]}
                for column in columns
                if "unit" in column
            }
            payload_unit_metadata = get_table_field_units(unit_table_name)
            for field_name, metadata in payload_unit_metadata.items():
                table_unit_metadata.setdefault(field_name, metadata)
            if table_unit_metadata:
                all_field_units[table_name] = table_unit_metadata

        return {"schemas": all_schemas, "field_units": all_field_units}
    except Exception as exc:
        logger.exception("get_database_schema failed: %s", exc)
        return {"error": str(exc)}


@tool
async def query_and_calculate(
    table_name: str,
    filters: List[Dict[str, Any]],
    compute_code: str,
    limit: int = 100,
    columns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    通用数据库查询与动态计算工具 (Generic DB Query and Dynamic Calculation Tool).

    **重要指令：在调用此工具前，除非你已经非常确定目标表的字段名称和类型，
    否则必须先调用 `get_database_schema` 获取表结构。严禁凭空猜测字段名。**

    该工具首先根据过滤条件从数据库拉取数据，然后在受限的 Python 沙箱中执行计算逻辑（具体限制条件参见 `execute_python_sandboxed` 说明）。
    **注意：该工具不返回原始行数据，仅返回沙箱执行报告。**

    参数:
    - table_name: 字符串。目标 SQLAlchemy 模型名（如 'KlineData', 'StockBasic', 'StockValuationHistory'）。
    - filters: 列表。过滤条件字典的集合。格式示例:
      `[{"column": "stock_code", "op": "==", "value": "000001.SZ"},
      {"column": "date", "op": ">=", "value": "2024-01-01"}]`
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
    - columns: 可选列表。只返回计算所需列；大表或长周期查询必须传 columns，避免超过沙箱请求大小上限。

    计算示例 (compute_code):
    ```python
    import numpy as np
    # 系统已自动注入 data 变量 (List[Dict])，并 import json
    # data 结构说明：每个字典代表数据库的一行，Key 为表的列名 (如 d['close'], d['stock_code'])
    closes = [float(d['close']) for d in data if d.get('close') is not None]
    avg_close = float(np.mean(closes)) if closes else None
    ```

    推荐的可审计结果结构（数据计算结果能明确单位的，尽量在输出字段或文本中直接带上单位）:
    ```python
    {
        "scope": {
            "stock_code": "000001.SZ",
            "table": "KlineData",
            "date_range": [start_date, end_date],
        },
        "row_count": len(data),
        "avg_close_元每股": avg_close,
    }
    ```
    字段要求:
    - 最低限度必填: `scope`, `row_count`。
    - `scope` 的内部字段按任务类型调整，不强制固定键名。
    - 不限制数据输出形式；可以按任务需要输出额外字段。
    - 数据计算结果能明确单位的，尽量在输出字段名或文本中直接带上单位，
      例如 `avg_close_元每股`、`volume_股`、`amount_元`、`return_pct`。
    使用这种结构可以让模型不查看全量 K 线、资金流或财务明细，也能知道计算范围、样本数量、
    以及按任务需要返回的计算结果。

    提示：
    1. 你可以使用 numpy (np), pandas (pd), math, datetime 等常用库。
    2. 系统会自动定义 `data` 变量，其内容为数据库查询出的原始行（已转为字典列表）。
    3. 工具将返回完整的计算执行报告（包含 success、stdout、stderr、error、metadata 等状态）。
    4. 大表/长周期计算必须传 `columns`，例如估值百分位只传 `["data_date", "pe_ttm", "pb"]`。
    5. 注意：数据库中的某些数值可能为 null，请务必在代码中处理空值，确保健壮性。
    """
    import app.core.database as database_module
    from sqlalchemy import and_, select

    model = _resolve_agentic_db_model(table_name)
    if not model:
        return {"error": f"Table '{table_name}' not found."}

    try:
        selected_columns = StockTools._normalize_selected_columns(model, _normalize_list_arg(columns))
    except UnsupportedColumnsError as exc:
        return exc.to_dict()

    try:
        async with database_module.AsyncSessionLocal() as db:
            query = StockTools._build_model_query(model, selected_columns)

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
                try:
                    coerced_val = coerce_filter_value_for_column(col_attr.property.columns[0], val)
                except ValueError:
                    return {"error": f"Invalid value for column '{col_name}': {val}"}
                filter_clauses.append(ops[op](col_attr, coerced_val))

            if filter_clauses:
                query = query.where(and_(*filter_clauses))

            # 执行查询
            records = StockTools._query_result_records(await db.execute(query.limit(limit)), selected_columns)
            # 序列化结果供计算使用
            data_for_calc = StockTools._serialize_query_results(records, selected_columns)

        # 准备注入沙箱的代码。关闭数据库会话后再执行沙箱，避免长任务占用连接。
        data_json = json.dumps(make_json_serializable(data_for_calc), ensure_ascii=False)
        # Use Python repr for the JSON payload so backslashes/quotes survive code injection intact.
        full_code = f"import json\ndata = json.loads({data_json!r})\n{compute_code}"

        if len(full_code) > PY_SANDBOX_CODE_MAX_CHARS:
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": (
                    "Query result too large for Python sandbox: generated code length "
                    f"{len(full_code)} exceeds max {PY_SANDBOX_CODE_MAX_CHARS}."
                ),
                "execution_time_ms": 0,
                "timed_out": False,
                "truncated": False,
                "metadata": {
                    "error_type": "sandbox_request_too_large",
                    "table_name": table_name,
                    "row_count": len(data_for_calc),
                    "limit": limit,
                    "columns": selected_columns,
                    "code_chars": len(full_code),
                    "max_code_chars": PY_SANDBOX_CODE_MAX_CHARS,
                },
                "hint": "Reduce limit/date range or pass columns with only the fields needed for compute_code.",
            }

        # 直接返回完整的执行报告，包含 success, stdout, stderr 等
        return await execute_python_in_sandbox(full_code)

    except Exception as exc:
        logger.exception("query_and_calculate failed: %s", exc)
        return {"error": str(exc)}


async def execute_trading_order(
    stock_code: str = "",
    action: str = "buy",
    target_position: float = 0.0,
    session_id: str = "",
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    operation: str = "place",
    order_type: str = "market",
    limit_price: Optional[float] = None,
    order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行股票交易下单工具 (Execute stock trading order).

    该工具由投资经理(Portfolio Manager)在做出决策后调用，用于将决策转化为模拟交易订单。

    参数:
    - stock_code: 股票代码，如 '600519.SH'
    - action: 'buy' 或 'sell'
    - target_position: 目标仓位比例 (0.0 - 1.0)。
      - 如果是 'buy'，按当前市场参考价计算达到目标比例所需买入的股数。
      - 如果是 'sell'，按当前市场参考价计算达到目标比例所需卖出的股数。
      - target_position = 0 即为全额清仓。
    - session_id: 当前会话 ID (必须从 Context 中准确获取)，用于关联账户和持久化。
    - stop_loss: 止损价，必填。成交后会直接写入持仓 `purchase_details.stop_loss`。
    - take_profit: PM 本轮止盈价或目标价，必填。买入时必须高于当前价。
    - operation: `place` 下单或 `cancel` 撤单，默认 `place`。
    - order_type: `market` 或 `limit`，默认 `market`。
    - limit_price: 限价挂单委托价，`order_type=limit` 时必填。
    - order_id: 撤单目标订单 ID，`operation=cancel` 时必填。

    注意:
    1. 自动执行 A 股交易规则：买入必须是 100 的整数倍；卖出减仓时尽量取 100 倍数。
    2. 自动检查 T+1 可用持仓限制，卖出数量不会超过可用股数。
    3. 仅在系统配置的 ENABLE_AUTO_TRADE 为开启状态时写入模拟交易账本。
    4. `stop_loss` 为必填字段，必须传入明确的正数止损价。
    5. 若返回 `success=false`，你必须先阅读 `reason`，再决定是否调整 `target_position`
       继续调用本工具，或停止交易并输出最终报告。
    6. 限价单不会按限价重新计算目标股数；限价仅用于委托金额、费用和资金校验。
    """
    from app.trading.service import trading_service
    from app.models.session import Session as DbSession
    from app.models.user import User
    from app.models.account import Account
    from app.models.position import Position
    from app.models.order import Order
    from app.ai.llm_engine.position_plan_service import (
        build_executable_position_plan,
        load_reference_price,
        load_pending_order_shares,
    )
    import app.core.database as database_module
    from sqlalchemy import select
    import app.core.config as config

    if not config.settings.ENABLE_AUTO_TRADE:
        return _format_trade_execution_result({"error": "Auto-trade is disabled in system settings."})

    normalized_operation = str(operation or "place").lower()
    normalized_order_type = str(order_type or "market").lower()
    if normalized_operation not in {"place", "cancel"}:
        return _format_trade_execution_result(
            {"error": f"Invalid operation: {operation}", "reason": "invalid_operation"}
        )

    if normalized_order_type not in {"market", "limit"}:
        return _format_trade_execution_result(
            {"error": f"Invalid order_type: {order_type}", "reason": "invalid_order_type"}
        )

    if normalized_operation == "place":
        if stop_loss <= 0:
            return _format_trade_execution_result(
                {"error": f"Invalid stop_loss: {stop_loss}. stop_loss must be greater than 0."}
            )

        if take_profit <= 0:
            return _format_trade_execution_result(
                {"error": f"Invalid take_profit: {take_profit}. take_profit must be greater than 0."}
            )

        if normalized_order_type == "limit" and (limit_price is None or limit_price <= 0):
            return _format_trade_execution_result({
                "error": "limit_price must be greater than 0 for limit orders.",
                "reason": "invalid_limit_price",
            })

    if normalized_operation == "cancel" and not order_id:
        return _format_trade_execution_result(
            {
                "error": "order_id is required for cancel operation",
                "reason": "missing_order_id",
            }
        )

    session_uuid = None
    try:
        from uuid import UUID
        session_uuid = UUID(session_id)
    except Exception:
        return _format_trade_execution_result({"error": f"Invalid session_id format: {session_id}"})

    try:
        async with database_module.AsyncSessionLocal() as db:
            # 1. 获取会话和用户信息
            session_model = (await db.execute(
                select(DbSession).where(DbSession.session_id == session_uuid)
            )).scalar_one_or_none()
            if not session_model:
                return _format_trade_execution_result({"error": f"Session {session_id} not found in database."})

            user = (await db.execute(select(User).where(User.id == session_model.user_id))).scalar_one_or_none()
            if not user:
                return _format_trade_execution_result({"error": "Associated User or Account not found."})

            account = (await db.execute(select(Account).where(Account.user_id == user.id))).scalar_one_or_none()
            if not account:
                return _format_trade_execution_result({"error": "Associated User or Account not found."})

            if normalized_operation == "cancel":
                order = await _resolve_order_by_llm_id(db, Order, account.account_id, str(order_id))
                if not order:
                    return _format_trade_execution_result({
                        "error": f"Order {order_id} not found or ambiguous.",
                        "reason": "order_not_found_or_ambiguous",
                    })
                cancel_order_id = order.order_id
                cancel_user_id = user.id

            if normalized_operation != "cancel":
                total_assets = float(account.total_assets or 0)

                # 2. 使用与仓位工具一致的行情来源确定参考价。
                price, _, _ = await load_reference_price(db, stock_code)

                if price <= 0:
                    return _format_trade_execution_result(
                        {"error": f"Could not determine a valid price for {stock_code}. Trade aborted."}
                    )

                order_price = float(limit_price) if normalized_order_type == "limit" else price

                # 3. 获取当前持仓
                pos = (await db.execute(
                    select(Position).where(
                        Position.account_id == account.account_id,
                        Position.stock_code == stock_code,
                    )
                )).scalar_one_or_none()
                current_total_shares = pos.total_shares if pos else 0
                current_available_shares = (
                    trading_engine.build_position_snapshot(pos)["available_shares"]
                    if pos else 0
                )
                pending_buy_shares, pending_sell_shares = await load_pending_order_shares(
                    db,
                    account.account_id,
                    stock_code,
                )

                current_position = (
                    (current_total_shares * price) / total_assets if total_assets > 0 else 0.0
                )
                gate_result = _evaluate_pm_trade_gate(
                    action=action,
                    target_position=target_position,
                    current_position=current_position,
                    price=order_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    current_total_shares=current_total_shares,
                    current_available_shares=current_available_shares,
                )
                if gate_result is not None:
                    return gate_result

                act = action.lower()
                position_plan = build_executable_position_plan(
                    target_position=target_position,
                    price=price,
                    total_assets=total_assets,
                    available_cash=float(account.available_cash or 0),
                    current_total_shares=current_total_shares,
                    current_available_shares=current_available_shares,
                    pending_buy_shares=pending_buy_shares,
                    pending_sell_shares=pending_sell_shares,
                    order_price=order_price,
                )
                if not position_plan["success"]:
                    return _format_trade_execution_result({
                        "success": False,
                        "message": "Trade skipped: the executable position plan could not be calculated.",
                        "reason": position_plan["reason"],
                        "position_plan": position_plan,
                    })
                if position_plan["action"] != act:
                    return _format_trade_execution_result({
                        "success": False,
                        "message": "Trade skipped: action conflicts with the executable position plan.",
                        "reason": "decision_target_mismatch",
                        "position_plan": position_plan,
                    })
                if not position_plan["executable"]:
                    return _format_trade_execution_result({
                        "success": False,
                        "message": "Trade skipped: the target position is not currently executable.",
                        "reason": position_plan["reason"],
                        "position_plan": position_plan,
                    })
                suggested_shares = position_plan["order_shares"]

                service_account_id = account.account_id

        if normalized_operation == "cancel":
            cancel_result = await trading_service.cancel_order(cancel_order_id, user_id=cancel_user_id)
            return _format_trade_execution_result(cancel_result)

        # 4. 调用交易服务。此处不得持有上方查询会话，交易服务会自行管理写库会话。
        trade_result = await trading_service.execute_order_and_update_db(
            session_id=session_uuid,
            account_id=service_account_id,
            stock_code=stock_code,
            action=act,
            shares=suggested_shares,
            price=order_price,
            order_type=normalized_order_type,
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
        get_current_time,
        query_stock_data,
        query_market_data,
        sync_market_data,
        fetch_financial_data,
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
        get_current_time,
        execute_python_sandboxed,
        browse_web_page_html,
        parse_pdf_to_markdown,
        search_news,
    ]
