import math
from copy import deepcopy
from datetime import datetime
from typing import Dict, Any, Optional
from uuid import UUID
import pytz
from app.core.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)

class TradingEngine:
    LEDGER_KEY = "ledger"
    MIN_TRANSACTION_SHARES = 100
    PRICE_PRECISION = 2
    SHARES_PRECISION = 0
    
    COMMISSION_RATE = 0.0002
    MIN_COMMISSION = 5
    STAMP_DUTY_RATE = 0.001
    TRANSFER_FEE_RATE = 0.00002
    
    def __init__(self):
        pass

    def get_market_timezone(self):
        return pytz.timezone("Asia/Shanghai")

    def get_market_now(self) -> datetime:
        return datetime.now(self.get_market_timezone())

    def to_market_datetime(self, value: datetime) -> datetime:
        market_tz = self.get_market_timezone()
        if value.tzinfo is None:
            return market_tz.localize(value)
        return value.astimezone(market_tz)

    def _read_position_value(self, position: Any, *field_names: str, default: Any = None) -> Any:
        if isinstance(position, dict):
            for field_name in field_names:
                if field_name in position:
                    return position[field_name]
            return default

        raw_values = getattr(position, "__dict__", {})
        for field_name in field_names:
            if field_name in raw_values:
                return raw_values[field_name]

        for field_name in field_names:
            if hasattr(position, field_name):
                return getattr(position, field_name)
        return default

    def _extract_position_payload(self, position: Any) -> Dict[str, Any]:
        if isinstance(position, dict):
            return deepcopy(position)

        payload: Dict[str, Any] = {}
        field_map = {
            "id": ("id", "position_id"),
            "stock_code": ("stock_code",),
            "stock_name": ("stock_name",),
            "current_shares": ("current_shares", "total_shares"),
            "available_shares": ("available_shares",),
            "frozen_shares": ("frozen_shares",),
            "avg_cost": ("avg_cost",),
            "current_price": ("current_price",),
            "market_value": ("market_value",),
            "unrealized_pnl": ("unrealized_pnl", "profit_loss"),
            "purchase_details": ("purchase_details",),
        }

        for target_field, source_fields in field_map.items():
            value = self._read_position_value(position, *source_fields, default=None)
            if value is not None:
                payload[target_field] = value

        return payload

    def normalize_purchase_details(self, purchase_details: Any) -> Dict[str, Any]:
        details = deepcopy(purchase_details) if isinstance(purchase_details, dict) else {}
        raw_ledger = details.get(self.LEDGER_KEY)
        normalized_ledger = []

        if isinstance(raw_ledger, list):
            for entry in raw_ledger:
                if not isinstance(entry, dict):
                    continue

                entry_time = entry.get("time")
                if not entry_time:
                    continue

                try:
                    shares = max(int(entry.get("shares", 0) or 0), 0)
                except (TypeError, ValueError):
                    continue

                if shares <= 0:
                    continue

                normalized_entry = dict(entry)
                normalized_entry["time"] = str(entry_time)
                normalized_entry["shares"] = shares
                cost_basis = entry.get("cost_basis")
                if cost_basis is not None:
                    try:
                        normalized_entry["cost_basis"] = round(float(cost_basis), 4)
                    except (TypeError, ValueError):
                        normalized_entry.pop("cost_basis", None)
                normalized_ledger.append(normalized_entry)

        normalized_ledger.sort(key=lambda item: item["time"])
        details[self.LEDGER_KEY] = normalized_ledger
        return details

    def derive_share_fields(self, current_shares: int, purchase_details: Any, fallback_available_shares: Any = None) -> Dict[str, int]:
        normalized_current = max(int(current_shares or 0), 0)
        normalized_details = self.normalize_purchase_details(purchase_details)

        if normalized_details.get(self.LEDGER_KEY):
            available_shares = min(
                normalized_current,
                max(int(self.get_sellable_shares(normalized_details) or 0), 0),
            )
        elif fallback_available_shares is None:
            available_shares = normalized_current
        else:
            available_shares = min(normalized_current, max(int(fallback_available_shares or 0), 0))

        return {
            "current_shares": normalized_current,
            "available_shares": available_shares,
            "frozen_shares": max(normalized_current - available_shares, 0),
        }

    def build_position_snapshot(self, position: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not position:
            return None

        snapshot = self._extract_position_payload(position)
        share_fields = self.derive_share_fields(
            snapshot.get("current_shares", snapshot.get("total_shares", 0)),
            snapshot.get("purchase_details"),
            snapshot.get("available_shares"),
        )
        snapshot.update(share_fields)
        snapshot["purchase_details"] = self.normalize_purchase_details(snapshot.get("purchase_details"))
        for field_name in ("avg_cost", "current_price", "market_value", "unrealized_pnl"):
            if snapshot.get(field_name) is None:
                continue
            try:
                snapshot[field_name] = float(snapshot[field_name])
            except (TypeError, ValueError):
                continue
        return snapshot

    def build_account_snapshot(self, account: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not account:
            return None

        snapshot = deepcopy(account)
        for field_name in ("cash_balance", "total_assets", "market_value", "total_profit_loss"):
            if snapshot.get(field_name) is None:
                continue
            try:
                snapshot[field_name] = float(snapshot[field_name])
            except (TypeError, ValueError):
                continue
        return snapshot

    def append_purchase_lot(
        self,
        purchase_details: Any,
        *,
        shares: int,
        price: float,
        cost_basis: Optional[float] = None,
        purchased_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        details = self.normalize_purchase_details(purchase_details)
        details.setdefault(self.LEDGER_KEY, [])
        lot = {
            "time": (purchased_at or datetime.now()).isoformat(),
            "shares": max(int(shares or 0), 0),
            "price": price,
        }
        if cost_basis is not None:
            lot["cost_basis"] = round(float(cost_basis), 4)
        details[self.LEDGER_KEY].append(lot)
        details[self.LEDGER_KEY].sort(key=lambda item: item["time"])
        return details

    def get_executable_sell_shares(self, position: Optional[Dict[str, Any]]) -> int:
        snapshot = self.build_position_snapshot(position)
        if not snapshot:
            return 0
        return snapshot["available_shares"]

    def sync_position_share_fields(self, position: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        snapshot = self.build_position_snapshot(position)
        if not position or not snapshot:
            return position

        position.update(snapshot)
        return position

    def get_sellable_shares(self, purchase_details: Dict[str, Any]) -> int:
        """
        根据买入详情计算当前可卖出的股份（T+1 逻辑）
        Calculate sellable shares based on purchase details (T+1 logic)
        """
        normalized_details = self.normalize_purchase_details(purchase_details)
        if not normalized_details or self.LEDGER_KEY not in normalized_details:
            return 0

        today_date = self.get_market_now().date()

        sellable = 0
        for entry in normalized_details.get(self.LEDGER_KEY, []):
            try:
                # 假设时间戳格式为 ISO 格式
                # Assume timestamp is in ISO format
                purchase_time = self.to_market_datetime(datetime.fromisoformat(entry["time"]))
                if purchase_time.date() < today_date:
                    sellable += entry["shares"]
            except (ValueError, KeyError):
                continue

        return sellable

    def deduct_shares_fifo(self, purchase_details: Dict[str, Any], shares_to_deduct: int) -> Dict[str, Any]:
        """
        按先进先出（FIFO）原则从买入详情中扣除股份
        Deduct shares from purchase details using FIFO principle
        """
        updated_details, _matched_cost = self.consume_purchase_lots_fifo(purchase_details, shares_to_deduct)
        return updated_details

    def consume_purchase_lots_fifo(self, purchase_details: Dict[str, Any], shares_to_deduct: int) -> tuple[Dict[str, Any], float]:
        """
        按 FIFO 扣减批次，同时返回被卖出股份的匹配成本。
        Deduct shares using FIFO and return matched cost for realized PnL calculation.
        """
        purchase_details = self.normalize_purchase_details(purchase_details)
        if self.LEDGER_KEY not in purchase_details:
            return purchase_details, 0.0

        new_ledger = []
        remaining = shares_to_deduct
        matched_cost = 0.0

        # 按时间排序（理论上已经是按顺序添加的，但为了安全重新排序）
        # Sort by time (should already be ordered, but for safety...)
        ledger = sorted(purchase_details[self.LEDGER_KEY], key=lambda x: x["time"])

        for entry in ledger:
            if remaining <= 0:
                new_ledger.append(entry)
                continue

            per_share_cost = float(entry.get("cost_basis", entry.get("price", 0.0)) or 0.0)
            if entry["shares"] > remaining:
                matched_cost += per_share_cost * remaining
                entry["shares"] -= remaining
                remaining = 0
                new_ledger.append(entry)
            else:
                matched_cost += per_share_cost * entry["shares"]
                remaining -= entry["shares"]
                # 完全扣除该笔记录，不加入新账本
                # Fully deducted, don't add to new ledger

        purchase_details[self.LEDGER_KEY] = new_ledger
        return purchase_details, round(matched_cost, 4)
    
    def calculate_fee(self, price: float, shares: int, is_buy: bool) -> Dict[str, float]:
        turnover = price * shares
        
        commission = turnover * self.COMMISSION_RATE
        commission = max(commission, self.MIN_COMMISSION)
        
        stamp_duty = turnover * self.STAMP_DUTY_RATE if not is_buy else 0.0
        
        transfer_fee = turnover * self.TRANSFER_FEE_RATE
        transfer_fee = max(transfer_fee, 0.01)
        
        total_fee = commission + stamp_duty + transfer_fee
        
        commission = round(commission, self.PRICE_PRECISION)
        stamp_duty = round(stamp_duty, self.PRICE_PRECISION)
        transfer_fee = round(transfer_fee, self.PRICE_PRECISION)
        total_fee = round(total_fee, self.PRICE_PRECISION)
        
        return {
            "commission": commission,
            "stamp_duty": stamp_duty,
            "transfer_fee": transfer_fee,
            "total_fee": total_fee
        }
    
    def check_order_validity(self, order: Dict[str, Any], account: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        order_type = order.get("order_type", "")
        action = order.get("action", "")
        shares = order.get("shares", 0)
        try:
            price = float(order.get("price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return {"is_valid": False, "message": "Invalid transaction price"}
        account_snapshot = self.build_account_snapshot(account) or {}
        
        if action not in ["buy", "sell"]:
            return {"is_valid": False, "message": "Invalid transaction direction"}
        
        if order_type not in ["market", "limit"]:
            return {"is_valid": False, "message": "Invalid order type"}
        
        if shares <= 0:
            return {"is_valid": False, "message": "Number of shares must be greater than 0"}
        
        if shares % self.MIN_TRANSACTION_SHARES != 0:
            return {"is_valid": False, "message": f"Number of shares must be a multiple of {self.MIN_TRANSACTION_SHARES}"}
        
        if order_type == "limit" and price <= 0:
            return {"is_valid": False, "message": "Transaction price must be greater than 0 for limit orders"}
        
        # 对于已经确定价格的订单（包括已经获取到行情后的市价单），进行资金/持仓校验
        if price > 0:
            if action == "buy":
                estimated_cost = price * shares
                fee = self.calculate_fee(price, shares, True)
                total_required = estimated_cost + fee["total_fee"]
                
                if float(account_snapshot.get("cash_balance", 0.0) or 0.0) < total_required:
                    return {"is_valid": False, "message": "Insufficient funds"}
            
            if action == "sell":
                if not position:
                    return {"is_valid": False, "message": "No position for this stock"}

                executable_shares = self.get_executable_sell_shares(position)
                if executable_shares <= 0:
                    return {"is_valid": False, "message": "Insufficient available shares (T+1 rule)"}

                if shares > executable_shares:
                    return {"is_valid": False, "message": "Insufficient shares"}
        
        return {"is_valid": True, "message": "Order is valid"}
    
    def calculate_stop_loss_take_profit(self, entry_price: float, stop_loss_pct: float = 0.05, take_profit_pct: float = 0.1) -> Dict[str, float]:
        stop_loss_price = round(entry_price * (1 - stop_loss_pct), self.PRICE_PRECISION)
        take_profit_price = round(entry_price * (1 + take_profit_pct), self.PRICE_PRECISION)
        
        return {
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "stop_loss_pct": stop_loss_pct * 100,
            "take_profit_pct": take_profit_pct * 100
        }
    
    def check_price_alert(self, current_price: float, position: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not position or "avg_cost" not in position:
            return None
        
        avg_cost = float(position["avg_cost"])
        if avg_cost <= 0:
            return None
        
        price_change_pct = (current_price - avg_cost) / avg_cost * 100
        
        if price_change_pct >= 5:
            return {
                "type": "price_rally",
                "message": f"Price rallied {price_change_pct:.2f}%",
                "price_change_pct": price_change_pct
            }
        elif price_change_pct <= -5:
            return {
                "type": "price_drop",
                "message": f"Price dropped {abs(price_change_pct):.2f}%",
                "price_change_pct": price_change_pct
            }
        
        return None
    
    def should_auto_sell(self, current_price: float, position: Dict[str, Any], stop_loss_pct: float = 0.05, take_profit_pct: float = 0.1) -> Dict[str, Any]:
        snapshot = self.build_position_snapshot(position) if position else None
        if not snapshot or "avg_cost" not in snapshot:
            return {"should_sell": False, "reason": None}

        avg_cost = float(snapshot["avg_cost"])
        if avg_cost <= 0:
            return {"should_sell": False, "reason": None}

        purchase_details = snapshot.get("purchase_details") if isinstance(snapshot.get("purchase_details"), dict) else {}
        explicit_stop_loss = purchase_details.get("stop_loss")
        if explicit_stop_loss not in (None, ""):
            try:
                stop_loss_price = float(explicit_stop_loss)
            except (TypeError, ValueError):
                stop_loss_price = None
            else:
                if stop_loss_price > 0 and current_price <= stop_loss_price:
                    return {
                        "should_sell": True,
                        "reason": "stop_loss",
                        "message": f"Stop loss triggered at {round(stop_loss_price, self.PRICE_PRECISION)}"
                    }

        price_change_pct = (current_price - avg_cost) / avg_cost

        if price_change_pct <= -stop_loss_pct:
            return {
                "should_sell": True,
                "reason": "stop_loss",
                "message": f"Stop loss triggered ({stop_loss_pct * 100}% loss)"
            }
        
        if price_change_pct >= take_profit_pct:
            return {
                "should_sell": True,
                "reason": "take_profit",
                "message": f"Take profit triggered ({take_profit_pct * 100}% profit)"
            }
        
        return {"should_sell": False, "reason": None}
    
    async def execute_order(self, order: Dict[str, Any], account: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        account_snapshot = self.build_account_snapshot(account) or {}
        # 首先获取基本参数进行初次校验（市价单在此阶段 price 可能为 0）
        init_validity = self.check_order_validity(order, account_snapshot, position)
        if not init_validity["is_valid"]:
            return {"success": False, "message": init_validity["message"], "order": order}

        action = order["action"]
        requested_shares = order["shares"]
        order_type = order.get("order_type", "limit")
        stock_code = order["stock_code"]
        
        # 获取价格：如果是市价单，使用实时市场价
        if order_type == "market":
            from app.data.storage import data_storage_service
            try:
                realtime_data = data_storage_service.get_stock_realtime_market(stock_code)
                price = realtime_data.get("latest_price") if realtime_data else None

                if price:
                    logger.debug(f"Successfully obtained market price from DB, stock code: {stock_code}, price: {price}")
                else:
                    logger.warning(f"Failed to obtain market data for stock {stock_code} from DB")
            except Exception as e:
                logger.error(f"Failed to obtain market price for stock {stock_code}: {e}", exc_info=True)
                price = None

            if price is None or price <= 0:
                return {"success": False, "message": "Market price unavailable, cannot execute market order", "order": order}
        else:
            price = float(order["price"])
        
        # 更新价格后重新校验有效性（第二次校验：针对获取价格后的市价单进行资金校验）
        temp_order = order.copy()
        temp_order["price"] = price
        validity = self.check_order_validity(temp_order, account_snapshot, position)
        if not validity["is_valid"]:
            return {"success": False, "message": validity["message"], "order": order}
        
        updated_account = account_snapshot.copy()
        updated_position = self.build_position_snapshot(position) if position else None
        
        # In simulated trading, successful orders are fully filled.
        executed_shares = requested_shares
        turnover = price * executed_shares
        fee = self.calculate_fee(price, executed_shares, action == "buy")
        
        if action == "buy":
            total_cost = turnover + fee["total_fee"]
            
            # 更新现金和市值
            updated_account["cash_balance"] -= total_cost
            updated_account["market_value"] += turnover
            updated_account["total_assets"] = updated_account["cash_balance"] + updated_account["market_value"]
            
            if updated_position:
                avg_cost = (updated_position["avg_cost"] * updated_position["current_shares"] + total_cost) / (updated_position["current_shares"] + executed_shares)
                updated_position["avg_cost"] = round(avg_cost, 4)
                updated_position["current_shares"] += executed_shares
                # Shares bought today are frozen, can only be sold next trading day
                updated_position["purchase_details"] = self.append_purchase_lot(
                    updated_position.get("purchase_details"),
                    shares=executed_shares,
                    price=price,
                    cost_basis=round(total_cost / executed_shares, 4),
                )
                self.sync_position_share_fields(updated_position)

                updated_position["market_value"] = updated_position["current_shares"] * price
                updated_position["unrealized_pnl"] = (price - updated_position["avg_cost"]) * updated_position["current_shares"]
                updated_position["updated_at"] = datetime.now().isoformat()
            else:
                updated_position = {
                    "id": UUID(int=0),
                    "stock_code": stock_code,
                    "stock_name": order.get("stock_name", ""),
                    "current_shares": executed_shares,
                    "available_shares": 0,  # Shares bought today are not available today
                    "frozen_shares": executed_shares,  # All bought shares are frozen
                    "avg_cost": round(total_cost / executed_shares, 4),
                    "market_value": turnover,
                    "unrealized_pnl": 0.0,
                    "purchase_details": self.append_purchase_lot(
                        {},
                        shares=executed_shares,
                        price=price,
                        cost_basis=round(total_cost / executed_shares, 4),
                    ),
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat()
                }
                self.sync_position_share_fields(updated_position)
        
        elif action == "sell":
            total_proceeds = turnover - fee["total_fee"]
            
            # 更新现金和市值
            updated_account["cash_balance"] += total_proceeds
            updated_account["market_value"] -= turnover
            updated_account["total_assets"] = updated_account["cash_balance"] + updated_account["market_value"]
            
            if updated_position:
                updated_position = self.build_position_snapshot(updated_position)
                available_shares = self.get_executable_sell_shares(updated_position)
                if executed_shares > available_shares:
                    # If available shares are insufficient, only fill available part
                    executed_shares = available_shares
                    if executed_shares < self.MIN_TRANSACTION_SHARES:
                        return {"success": False, "message": "Insufficient available shares (T+1 rule)", "order": order}
                    # Recalculate turnover and fees
                    turnover = price * executed_shares
                    fee = self.calculate_fee(price, executed_shares, action == "buy")
                    total_proceeds = turnover - fee["total_fee"]
                
                updated_purchase_details, matched_cost = self.consume_purchase_lots_fifo(
                    updated_position.get("purchase_details", {}),
                    executed_shares,
                )
                realized_pnl = turnover - matched_cost - fee["total_fee"]
                updated_account["total_profit_loss"] = float(updated_account.get("total_profit_loss", 0.0)) + realized_pnl
                
                updated_position["current_shares"] -= executed_shares
                # 扣除明细
                # Deduct from ledger
                updated_position["purchase_details"] = updated_purchase_details
                self.sync_position_share_fields(updated_position)
                
                updated_position["market_value"] = updated_position["current_shares"] * price
                updated_position["unrealized_pnl"] = (price - updated_position["avg_cost"]) * updated_position["current_shares"]
                updated_position["updated_at"] = datetime.now().isoformat()
                
                if updated_position["current_shares"] <= 0:
                    updated_position = None
        
        # Calculate net amount
        if action == "buy":
            net_amount = turnover + fee["total_fee"]
        else:
            net_amount = turnover - fee["total_fee"]
        
        trade_record = {
            "id": UUID(int=0),
            "order_id": order.get("id", UUID(int=0)),
            "session_id": order.get("session_id"),
            "stock_code": stock_code,
            "stock_name": order.get("stock_name", ""),
            "action": action,
            "price": price,
            "shares": executed_shares,
            "turnover": turnover,
            "commission": fee["commission"],
            "stamp_duty": fee["stamp_duty"],
            "transfer_fee": fee["transfer_fee"],
            "total_fee": fee["total_fee"],
            "net_amount": net_amount,
            "created_at": datetime.now().isoformat()
        }
        
        # Construct order execution result
        result = {
            "success": True,
            "message": "Order executed successfully",
            "trade_record": trade_record,
            "updated_account": updated_account,
            "updated_position": updated_position,
            "executed_shares": executed_shares,
            "remaining_shares": 0,
            "realized_pnl": realized_pnl if action == "sell" else 0.0
        }
        result["order_status"] = "filled"
        
        return result
    
    def calculate_account_assets(self, account: Dict[str, Any], positions: list) -> Dict[str, Any]:
        total_market_value = sum(pos["market_value"] for pos in positions if pos["market_value"] > 0)
        
        updated_account = account.copy()
        updated_account["total_market_value"] = total_market_value
        updated_account["total_assets"] = account["cash_balance"] + total_market_value
        
        return updated_account
    
    def update_position_availability(self, position: Dict[str, Any]) -> Dict[str, Any]:
        updated_position = position.copy()
        
        frozen_shares = updated_position.get("frozen_shares", 0)
        if frozen_shares > 0:
            updated_position["available_shares"] += frozen_shares
            updated_position["frozen_shares"] = 0
        
        return updated_position
