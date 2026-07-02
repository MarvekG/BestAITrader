from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database as database_module
from app.models.account import Account
from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.models.system_setting import SystemSetting
from app.portfolio.valuation import build_portfolio_valuation


DEFAULT_MAX_SINGLE_POSITION_PCT = Decimal("0.20")
DEFAULT_MAX_INDUSTRY_POSITION_PCT = Decimal("0.35")
DEFAULT_MIN_CASH_PCT = Decimal("0.10")
DEFAULT_STOP_LOSS_WARNING_PCT = Decimal("0.10")
RISK_CONTROL_SETTING_KEY = "portfolio_risk_control"
POLICY_OFF = "off"
POLICY_BLOCK = "block"
SUPPORTED_RULE_POLICIES = {POLICY_OFF, POLICY_BLOCK}
DEFAULT_RULE_POLICIES = {
    "require_stop_loss": POLICY_BLOCK,
    "max_single_position_pct": POLICY_BLOCK,
    "max_industry_position_pct": POLICY_BLOCK,
    "min_cash_pct": POLICY_BLOCK,
    "stop_loss_warning_pct": POLICY_BLOCK,
}


def _policy_to_str(policy: Any) -> str:
    """
    将枚举或字符串形式的规则策略转换为字符串值。

    Args:
        policy: 规则策略输入。

    Returns:
        策略字符串。
    """
    return str(getattr(policy, "value", policy))


def _to_decimal(value: Any) -> Decimal:
    """
    将外部数值转换为 Decimal。

    Args:
        value: 可转换为 Decimal 的输入值。

    Returns:
        转换后的 Decimal 值。
    """
    return Decimal(str(value))


def _default_config_value() -> dict[str, Any]:
    """
    构建默认组合风控配置值。

    Returns:
        默认配置字典。
    """
    return {
        "enabled": True,
        "max_single_position_pct": float(DEFAULT_MAX_SINGLE_POSITION_PCT),
        "max_industry_position_pct": float(DEFAULT_MAX_INDUSTRY_POSITION_PCT),
        "min_cash_pct": float(DEFAULT_MIN_CASH_PCT),
        "require_stop_loss": True,
        "stop_loss_warning_pct": float(DEFAULT_STOP_LOSS_WARNING_PCT),
        "rule_policies": DEFAULT_RULE_POLICIES.copy(),
    }


def normalize_rule_policies(raw_policies: Any) -> dict[str, str]:
    """
    归一化组合风控规则执行策略。

    Args:
        raw_policies: 外部传入或持久化的规则策略。

    Returns:
        补齐默认值后的规则策略字典。
    """
    policies = DEFAULT_RULE_POLICIES.copy()
    if not isinstance(raw_policies, dict):
        return policies

    for rule, policy in raw_policies.items():
        if rule not in DEFAULT_RULE_POLICIES:
            continue
        normalized_policy = _policy_to_str(policy)
        if normalized_policy in SUPPORTED_RULE_POLICIES:
            policies[rule] = normalized_policy
    return policies


def serialize_config(config: SystemSetting) -> dict[str, Any]:
    """
    将组合风控配置转换为 API 响应结构。

    Args:
        config: 系统设置模型。

    Returns:
        可 JSON 序列化的配置字典。
    """
    value = _default_config_value()
    if isinstance(config.value, dict):
        value.update(config.value)

    return {
        "id": config.id,
        "account_id": None,
        "enabled": bool(value["enabled"]),
        "max_single_position_pct": float(value["max_single_position_pct"]),
        "max_industry_position_pct": float(value["max_industry_position_pct"]),
        "min_cash_pct": float(value["min_cash_pct"]),
        "require_stop_loss": bool(value["require_stop_loss"]),
        "stop_loss_warning_pct": float(value["stop_loss_warning_pct"]),
        "rule_policies": normalize_rule_policies(value.get("rule_policies")),
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


class PortfolioRiskControlService:
    """组合风控配置和评估服务。"""

    def _apply_config_payload(self, config: SystemSetting, payload: dict[str, Any]) -> None:
        """把风控配置更新字段应用到系统设置对象。"""
        value = serialize_config(config)
        for field_name in (
            "enabled",
            "max_single_position_pct",
            "max_industry_position_pct",
            "min_cash_pct",
            "require_stop_loss",
            "stop_loss_warning_pct",
        ):
            if field_name not in payload:
                continue
            field_value = payload[field_name]
            if field_name in {"enabled", "require_stop_loss"}:
                value[field_name] = bool(field_value)
            else:
                value[field_name] = float(_to_decimal(field_value))

        value["rule_policies"] = normalize_rule_policies(value.get("rule_policies"))
        if "rule_policies" in payload:
            for rule, policy in payload["rule_policies"].items():
                normalized_policy = _policy_to_str(policy)
                if rule in DEFAULT_RULE_POLICIES and normalized_policy in SUPPORTED_RULE_POLICIES:
                    value["rule_policies"][rule] = normalized_policy

        config.value = {
            "enabled": value["enabled"],
            "max_single_position_pct": value["max_single_position_pct"],
            "max_industry_position_pct": value["max_industry_position_pct"],
            "min_cash_pct": value["min_cash_pct"],
            "require_stop_loss": value["require_stop_loss"],
            "stop_loss_warning_pct": value["stop_loss_warning_pct"],
            "rule_policies": value["rule_policies"],
        }

    async def get_or_create_config_for_user(self, user_id: int) -> SystemSetting:
        """异步获取用户风控配置，不存在时创建默认配置。

        Args:
            user_id: 当前用户 ID。

        Returns:
            用户对应的组合风控系统设置。
        """
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemSetting).where(
                    SystemSetting.user_id == user_id,
                    SystemSetting.key == RISK_CONTROL_SETTING_KEY,
                )
            )
            config = result.scalars().first()
            if config:
                return config

            config = SystemSetting(
                user_id=user_id,
                key=RISK_CONTROL_SETTING_KEY,
                value=_default_config_value(),
                description="Portfolio risk control configuration",
            )
            db.add(config)
            await db.commit()
            await db.refresh(config)
            return config

    async def update_config_for_user(self, user_id: int, payload: dict[str, Any]) -> SystemSetting:
        """异步更新用户风控配置。

        Args:
            user_id: 当前用户 ID。
            payload: 风控配置更新字段。

        Returns:
            更新后的组合风控系统设置。
        """
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemSetting).where(
                    SystemSetting.user_id == user_id,
                    SystemSetting.key == RISK_CONTROL_SETTING_KEY,
                )
            )
            config = result.scalars().first()
            if config is None:
                config = SystemSetting(
                    user_id=user_id,
                    key=RISK_CONTROL_SETTING_KEY,
                    value=_default_config_value(),
                    description="Portfolio risk control configuration",
                )
                db.add(config)
                await db.flush()
            self._apply_config_payload(config, payload)
            await db.commit()
            await db.refresh(config)
            return config

    async def evaluate_order(
        self,
        *,
        user_id: int,
        stock_code: str,
        action: str,
        shares: int,
        price: float,
        order_type: str,
        stop_loss: float | None = None,
        estimated_fee: float | None = None,
    ) -> dict[str, Any]:
        """
        评估订单执行后的组合风控结果。

        Args:
            user_id: 当前用户 ID。
            stock_code: 股票代码。
            action: 交易方向。
            shares: 交易股数。
            price: 交易价格。
            order_type: 订单类型。
            stop_loss: 止损价。
            estimated_fee: 交易服务已按同一执行价格估算出的交易费用。

        Returns:
            风控评估结果，包含是否通过、拦截命中和关键指标。
        """
        config = serialize_config(await self.get_or_create_config_for_user(user_id))
        if not config["enabled"]:
            return {
                "enabled": False,
                "passed": True,
                "severity": "none",
                "accepted": [],
                "blocks": [],
                "metrics": {},
            }

        async with database_module.AsyncSessionLocal() as db:
            account_result = await db.execute(select(Account).where(Account.user_id == user_id))
            account = account_result.scalars().first()
            if account is None:
                return {
                    "enabled": config["enabled"],
                    "passed": False,
                    "severity": "block",
                    "accepted": [],
                    "blocks": [{"rule": "account_missing", "message": "Account not found"}],
                    "metrics": {},
                }

        action = str(action or "").lower()
        price_decimal = _to_decimal(price or 0)
        if price_decimal <= 0:
            price_decimal = await self._get_latest_price(stock_code)
        shares_decimal = _to_decimal(shares or 0)
        trade_value = price_decimal * shares_decimal
        estimated_fee_decimal = _to_decimal(estimated_fee or 0)
        valuation = await build_portfolio_valuation(account)
        total_assets = valuation["summary"]["total_assets_decimal"]
        available_cash = valuation["summary"]["available_cash_decimal"]
        current_position_value = self._get_position_value(valuation, stock_code)
        industry = await self._get_stock_industry(stock_code)
        current_industry_value = self._get_industry_value(valuation, industry)

        if action == "buy":
            post_single_value = current_position_value + trade_value
            post_industry_value = current_industry_value + trade_value
            post_cash = available_cash - trade_value - estimated_fee_decimal
        elif action == "sell":
            post_single_value = max(current_position_value - trade_value, Decimal("0"))
            post_industry_value = max(current_industry_value - trade_value, Decimal("0"))
            post_cash = available_cash + trade_value
        else:
            post_single_value = current_position_value
            post_industry_value = current_industry_value
            post_cash = available_cash

        denominator = total_assets if total_assets > 0 else Decimal("1")
        post_single_pct = post_single_value / denominator
        post_industry_pct = post_industry_value / denominator
        post_cash_pct = post_cash / denominator
        stop_loss_drawdown_pct = self._calculate_stop_loss_drawdown_pct(price_decimal, stop_loss)
        blocks: list[dict[str, Any]] = []
        rule_policies = normalize_rule_policies(config.get("rule_policies"))

        if action == "buy" and config["require_stop_loss"] and stop_loss in (None, ""):
            self._apply_rule_hit(
                rule_policies,
                blocks,
                self._rule_hit(
                    "require_stop_loss",
                    None,
                    True,
                    stock_code,
                    industry,
                ),
            )

        if action == "buy" and post_single_pct > _to_decimal(config["max_single_position_pct"]):
            self._apply_rule_hit(
                rule_policies,
                blocks,
                self._rule_hit(
                    "max_single_position_pct",
                    post_single_pct,
                    config["max_single_position_pct"],
                    stock_code,
                    industry,
                ),
            )

        if action == "buy" and post_industry_pct > _to_decimal(config["max_industry_position_pct"]):
            self._apply_rule_hit(
                rule_policies,
                blocks,
                self._rule_hit(
                    "max_industry_position_pct",
                    post_industry_pct,
                    config["max_industry_position_pct"],
                    stock_code,
                    industry,
                ),
            )

        if action == "buy" and post_cash_pct < _to_decimal(config["min_cash_pct"]):
            self._apply_rule_hit(
                rule_policies,
                blocks,
                self._rule_hit(
                    "min_cash_pct",
                    post_cash_pct,
                    config["min_cash_pct"],
                    stock_code,
                    industry,
                ),
            )

        if (
            action == "buy"
            and stop_loss_drawdown_pct is not None
            and stop_loss_drawdown_pct > _to_decimal(config["stop_loss_warning_pct"])
        ):
            self._apply_rule_hit(
                rule_policies,
                blocks,
                self._rule_hit(
                    "stop_loss_warning_pct",
                    stop_loss_drawdown_pct,
                    config["stop_loss_warning_pct"],
                    stock_code,
                    industry,
                ),
            )

        severity = "block" if blocks else "none"
        return {
            "enabled": True,
            "passed": len(blocks) == 0,
            "severity": severity,
            "accepted": [],
            "blocks": blocks,
            "metrics": {
                "total_assets": self._to_float(total_assets),
                "trade_value": self._to_float(trade_value),
                "estimated_fee": self._to_float(estimated_fee_decimal),
                "current_single_position_value": self._to_float(current_position_value),
                "current_industry_position_value": self._to_float(current_industry_value),
                "post_single_position_pct": self._to_float(post_single_pct),
                "post_industry_position_pct": self._to_float(post_industry_pct),
                "post_cash_pct": self._to_float(post_cash_pct),
                "stop_loss_drawdown_pct": self._to_float(stop_loss_drawdown_pct),
                "industry": industry,
                "order_type": order_type,
            },
        }

    def _get_position_value(self, valuation: dict[str, Any], stock_code: str) -> Decimal:
        """
        从统一估值中获取指定股票的当前持仓市值。

        Args:
            valuation: 统一组合估值结构。
            stock_code: 股票代码。

        Returns:
            当前持仓市值。
        """
        for position in valuation["positions"]:
            if position["stock_code"] == stock_code:
                return position["market_value_decimal"]
        return Decimal("0")

    async def _get_stock_industry(self, stock_code: str) -> str:
        """
        获取股票所属行业，缺失时返回未知行业。

        Args:
            stock_code: 股票代码。

        Returns:
            股票行业名称。
        """
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(select(StockBasic.industry).where(StockBasic.stock_code == stock_code))
            industry = result.scalar_one_or_none()
        return str(industry or "未知行业")

    async def _get_latest_price(self, stock_code: str) -> Decimal:
        """
        获取股票最近有效行情价格。

        Args:
            stock_code: 股票代码。

        Returns:
            最近有效价格；缺失时返回 0。
        """
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(StockRealtimeMarket.current_price)
                .where(
                    StockRealtimeMarket.stock_code == stock_code,
                    StockRealtimeMarket.current_price.isnot(None),
                    StockRealtimeMarket.current_price > 0,
                )
                .order_by(StockRealtimeMarket.timestamp.desc())
            )
            price = result.scalar_one_or_none()
        return _to_decimal(price or 0)

    def _get_industry_value(self, valuation: dict[str, Any], industry: str) -> Decimal:
        """
        从统一估值中获取指定行业的当前持仓市值。

        Args:
            valuation: 统一组合估值结构。
            industry: 行业名称。

        Returns:
            当前行业持仓市值。
        """
        for allocation in valuation["industry_allocations"]:
            if allocation["industry"] == industry:
                return allocation["market_value_decimal"]
        return Decimal("0")

    def _apply_rule_hit(
        self,
        rule_policies: dict[str, str],
        blocks: list[dict[str, Any]],
        hit: dict[str, Any],
    ) -> None:
        """
        按规则策略归类命中结果。

        Args:
            rule_policies: 规则到执行策略的映射。
            blocks: 拦截命中列表。
            hit: 当前规则命中。
        """
        policy = rule_policies.get(hit["rule"], POLICY_BLOCK)
        if policy == POLICY_OFF:
            return
        blocks.append(hit)

    def _calculate_stop_loss_drawdown_pct(self, price: Decimal, stop_loss: float | None) -> Decimal | None:
        """
        计算止损价相对买入价的跌幅。

        Args:
            price: 买入价格。
            stop_loss: 止损价。

        Returns:
            止损跌幅；无法计算时返回 None。
        """
        if price <= 0 or stop_loss in (None, ""):
            return None
        stop_loss_decimal = _to_decimal(stop_loss)
        if stop_loss_decimal <= 0:
            return None
        return max((price - stop_loss_decimal) / price, Decimal("0"))

    def _rule_hit(
        self,
        rule: str,
        current_value: Decimal | float | bool | None,
        limit_value: Decimal | float | bool | None,
        stock_code: str,
        industry: str,
    ) -> dict[str, Any]:
        """
        构造风控规则命中结构。

        Args:
            rule: 规则编码。
            current_value: 当前指标值。
            limit_value: 配置阈值。
            stock_code: 股票代码。
            industry: 行业名称。

        Returns:
            风控规则命中字典。
        """
        formatted_current = self._format_rule_value(current_value)
        formatted_limit = self._format_rule_value(limit_value)
        return {
            "rule": rule,
            "message_key": f"trading_center.risk_control.messages.{rule}",
            "message": f"{rule}: {formatted_current}/{formatted_limit}",
            "params": {
                "current": formatted_current,
                "limit": formatted_limit,
                "stock_code": stock_code,
                "industry": industry,
            },
            "current_value": self._to_float(current_value),
            "limit_value": self._to_float(limit_value),
            "stock_code": stock_code,
            "industry": industry,
        }

    def _format_rule_value(self, value: Decimal | float | bool | None) -> str:
        """
        将规则指标格式化为可展示字符串。

        Args:
            value: 原始规则指标。

        Returns:
            展示用字符串。
        """
        if value is None:
            return "not_set"
        if isinstance(value, bool):
            return "required" if value else "not_required"
        return f"{float(value) * 100:.2f}%"

    def _to_float(self, value: Decimal | float | bool | None) -> float | bool | None:
        """
        将指标值转换为 JSON 友好值。

        Args:
            value: 指标值。

        Returns:
            可序列化的指标值。
        """
        if value is None or isinstance(value, bool):
            return value
        return round(float(value), 6)

portfolio_risk_control_service = PortfolioRiskControlService()
