from typing import Dict, Any, List, Optional
from datetime import datetime, date
from sqlalchemy import select

from app.core import database as database_module
from app.models.data_storage import (
    StockBasic,
    KlineData,
    IndustryData,
    NorthboundData,
    DragonTigerData,
    StockRealtimeMarket,
    StockLimitUpPool,
    StockMoneyFlow,
    StockShareholder,
    StockPledge,
    StockInsider,
    StockRelease,
    StockMargin
)
from app.core.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)


class DataStorageService:
    """数据存储服务，用于处理不同类型数据的存储逻辑"""

    async def get_stock_realtime_market(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取指定股票最近一条实时行情，供交易撮合和前端推送使用。

        Args:
            stock_code: 标准股票代码，例如 ``000651.SZ``。

        Returns:
            最近一条实时行情字典；未找到记录或查询失败时返回 None。
        """
        if not stock_code:
            return None

        try:
            async with database_module.AsyncSessionLocal() as db:
                record = (await db.execute(select(StockRealtimeMarket).where(
                    StockRealtimeMarket.stock_code == stock_code
                ).order_by(
                    StockRealtimeMarket.timestamp.desc(),
                    StockRealtimeMarket.updated_at.desc(),
                    StockRealtimeMarket.created_at.desc(),
                ))).scalars().first()
                if not record:
                    return None

                data = self._to_dict(record)
                data["latest_price"] = data.get("current_price")
                data["yesterday_close"] = data.get("prev_close")
                data["update_time"] = data.get("timestamp") or data.get("updated_at")
                data["total_market_value"] = data.get("total_market_cap")
                data["circulating_market_value"] = data.get("circulating_market_cap")
                data["pb"] = data.get("pb_ratio")
                return data
        except Exception as e:
            logger.error("Failed to get realtime market data", extra={"stock_code": stock_code, "error": str(e)})
            return None

    async def save_stock_basic(self, data: Dict[str, Any]) -> bool:
        """存储股票基本信息

        Args:
            data: 股票基本信息数据

        Returns:
            是否存储成功
        """
        try:
            async with database_module.AsyncSessionLocal() as db:
                stock_code = data.get("stock_code")
                if not stock_code:
                    logger.error("Stock code is required for stock basic information")
                    return False

                # 处理list_date字段，确保为空时使用None而不是空字符串
                list_date = data.get("list_date")
                if list_date == "":
                    list_date = None
                elif isinstance(list_date, str):
                    # 尝试将字符串转换为日期
                    try:
                        list_date = datetime.strptime(list_date, "%Y-%m-%d").date()
                    except ValueError:
                        list_date = None

                # 查找现有记录
                stock_basic = (await db.execute(
                    select(StockBasic).where(StockBasic.stock_code == stock_code)
                )).scalars().first()

                if stock_basic:
                    # 更新现有记录
                    new_name = data.get("name")
                    # 只有当新名称有效（非空、非占位符）时才更新
                    if new_name and not new_name.startswith("Stock ") and new_name != "unknown":
                        stock_basic.name = new_name
                    stock_basic.industry = data.get("industry", stock_basic.industry)
                    stock_basic.sector = data.get("sector", stock_basic.sector)
                    stock_basic.market = data.get("market", stock_basic.market)
                    stock_basic.list_date = list_date if list_date is not None else stock_basic.list_date
                    stock_basic.total_share = data.get("total_share", stock_basic.total_share)
                    stock_basic.float_share = data.get("float_share", stock_basic.float_share)
                    stock_basic.data_source = data.get("data_source", stock_basic.data_source)
                    stock_basic.updated_at = datetime.now()
                else:
                    # 创建新记录
                    stock_basic = StockBasic(
                        stock_code=stock_code,
                        name=data.get("name", ""),
                        industry=data.get("industry"),
                        sector=data.get("sector"),
                        market=data.get("market"),
                        list_date=list_date,
                        total_share=data.get("total_share"),
                        float_share=data.get("float_share"),
                        data_source=data.get("data_source", "manual")
                    )
                    db.add(stock_basic)

                await db.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save stock basic info {data.get('stock_code')}: {e}")
            return False

    async def save_kline_data(
        self,
        stock_code: str,
        data: List[Dict[str, Any]],
        freq: str = "D",
        data_source: str = "unknown",
    ) -> bool:
        """存储K线数据

        Args:
            stock_code: 股票代码
            data: K线数据列表
            freq: K线频率
            data_source: 数据源

        Returns:
            是否存储成功
        """
        try:
            async with database_module.AsyncSessionLocal() as db:
                if not stock_code or not data:
                    logger.error("Stock code and data are required for kline data")
                    return False

                for kline in data:
                    date_value = kline.get("date")
                    if not date_value:
                        continue

                    # 检查日期类型，如果已经是date类型，直接使用；否则转换
                    if isinstance(date_value, date):
                        kline_date = date_value
                    else:
                        # 转换日期格式
                        date_str = str(date_value).strip()
                        try:
                            if "-" in date_str:
                                kline_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                            elif len(date_str) == 8:
                                kline_date = datetime.strptime(date_str, "%Y%m%d").date()
                            else:
                                kline_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
                        except ValueError as e:
                            logger.error(f"Invalid date format for {date_value}: {e}")
                            continue

                    # 查找现有记录
                    kline_data = (await db.execute(select(KlineData).where(
                        KlineData.stock_code == stock_code,
                        KlineData.date == kline_date,
                        KlineData.freq == freq
                    ))).scalars().first()

                    if kline_data:
                        # 更新现有记录
                        kline_data.open = kline.get("open", kline_data.open)
                        kline_data.high = kline.get("high", kline_data.high)
                        kline_data.low = kline.get("low", kline_data.low)
                        kline_data.close = kline.get("close", kline_data.close)
                        kline_data.volume = kline.get("volume", kline_data.volume)
                        kline_data.turnover = kline.get("turnover", kline_data.turnover)
                        kline_data.change = kline.get("change", kline_data.change)
                        kline_data.change_percent = kline.get("change_percent", kline_data.change_percent)
                        kline_data.data_source = data_source
                    else:
                        # 创建新记录
                        kline_data = KlineData(
                            stock_code=stock_code,
                            date=kline_date,
                            freq=freq,
                            open=kline.get("open", 0.0),
                            high=kline.get("high", 0.0),
                            low=kline.get("low", 0.0),
                            close=kline.get("close", 0.0),
                            volume=kline.get("volume", 0),
                            turnover=kline.get("turnover", 0),
                            change=kline.get("change", 0.0),
                            change_percent=kline.get("change_percent", 0.0),
                            data_source=data_source
                        )
                        db.add(kline_data)

                await db.commit()
                logger.info(f"Saved kline data for {stock_code}, frequency: {freq}")
                return True
        except Exception as e:
            logger.error(f"Failed to save kline data: {e}")
            return False

    async def save_industry_data(self, industry_name: str, data: Dict[str, Any]) -> bool:
        """存储行业数据

        Args:
            industry_name: 行业名称
            data: 行业数据

        Returns:
            是否存储成功
        """
        try:
            async with database_module.AsyncSessionLocal() as db:
                if not industry_name or not data:
                    logger.error("Industry name and data are required for industry data")
                    return False

                # 获取日期
                date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
                try:
                    datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError as e:
                    logger.error(f"Invalid date format for {date_str}: {e}")
                    return False

                # 查找现有记录 - IndustryData has unique 'board_code' or we filter by 'board_name'
                # Since we might not have board_code, we try board_name. But board_code is required for insert.
                # specific columns: board_code, board_name, rank, latest_price, change_percent, etc.

                board_name = industry_name
                # Try to get board_code from data, or fallback?
                # The model requires board_code. If not present, we can't save safely without violating unique
                # constraint if we make one up.
                # However, for now, let's assume data might have it or use simple hash/name.
                board_code = data.get("board_code", data.get("code", board_name))

                industry_data = (await db.execute(select(IndustryData).where(
                    IndustryData.board_name == board_name
                ))).scalars().first()

                if industry_data:
                    # 更新现有记录
                    industry_data.rank = data.get("rank", industry_data.rank)
                    industry_data.latest_price = data.get("latest_price", industry_data.latest_price)
                    industry_data.change_percent = data.get("change_percent", industry_data.change_percent)
                    industry_data.total_market_cap = data.get("total_market_cap", industry_data.total_market_cap)
                    industry_data.turnover_rate = data.get("turnover_rate", industry_data.turnover_rate)
                    industry_data.data_source = data.get("data_source", industry_data.data_source)
                    industry_data.timestamp = datetime.now()
                else:
                    # 创建新记录
                    industry_data = IndustryData(
                        board_code=board_code,
                        board_name=board_name,
                        rank=data.get("rank"),
                        latest_price=data.get("latest_price"),
                        change_percent=data.get("change_percent"),
                        total_market_cap=data.get("total_market_cap"),
                        turnover_rate=data.get("turnover_rate"),
                        timestamp=datetime.now(),
                        data_source=data.get("data_source", "unknown")
                    )
                    db.add(industry_data)

                await db.commit()
                logger.info(f"Saved industry data for {industry_name}")
                return True
        except Exception as e:
            logger.error(f"Failed to save industry data: {e}")
            return False

    async def save_northbound_data(self, stock_code: str, data: Dict[str, Any]) -> bool:
        """存储北向资金数据"""
        try:
            async with database_module.AsyncSessionLocal() as db:
                date_val = data.get("date")
                if not date_val:
                    date_val = datetime.now().date()
                elif isinstance(date_val, str):
                    date_val = datetime.strptime(date_val, "%Y-%m-%d").date()

                nb_data = (await db.execute(select(NorthboundData).where(
                    NorthboundData.stock_code == stock_code,
                    NorthboundData.date == date_val
                ))).scalars().first()

                if nb_data:
                    nb_data.hold_shares = data.get("hold_shares", nb_data.hold_shares)
                    nb_data.hold_value = data.get("hold_value", nb_data.hold_value)
                    nb_data.hold_ratio = data.get("hold_ratio", nb_data.hold_ratio)
                    nb_data.hold_ratio = data.get("hold_ratio", nb_data.hold_ratio)
                    nb_data.close_price = data.get("close_price", nb_data.close_price)
                    nb_data.change_percent = data.get("change_percent", nb_data.change_percent)
                    nb_data.net_buy_volume = data.get("net_buy_volume", nb_data.net_buy_volume)
                    nb_data.net_buy_amount = data.get("net_buy_amount", nb_data.net_buy_amount)
                    nb_data.hold_value_change = data.get("hold_value_change", nb_data.hold_value_change)
                    nb_data.data_source = data.get("data_source", nb_data.data_source)
                else:
                    nb_data = NorthboundData(
                        stock_code=stock_code,
                        date=date_val,
                        hold_shares=data.get("hold_shares"),
                        hold_value=data.get("hold_value"),
                        hold_ratio=data.get("hold_ratio"),
                        close_price=data.get("close_price"),
                        change_percent=data.get("change_percent"),
                        net_buy_volume=data.get("net_buy_volume"),
                        net_buy_amount=data.get("net_buy_amount"),
                        hold_value_change=data.get("hold_value_change"),
                        data_source=data.get("data_source", "unknown")
                    )
                    db.add(nb_data)
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save northbound data for {stock_code}: {e}")
            return False

    async def save_dragon_tiger_data(self, stock_code: str, data: Dict[str, Any]) -> bool:
        """存储龙虎榜数据"""
        try:
            async with database_module.AsyncSessionLocal() as db:
                records = data.get("data", [])
                if not isinstance(records, list):
                    return False

                data_source = data.get("data_source", "unknown")

                for record in records:
                    trade_date_val = record.get("trade_date")
                    if isinstance(trade_date_val, str):
                        try:
                            if "-" in trade_date_val:
                                trade_date = datetime.strptime(trade_date_val, "%Y-%m-%d").date()
                            else:
                                trade_date = datetime.strptime(trade_date_val, "%Y%m%d").date()
                        except ValueError:
                            trade_date = datetime.now().date()
                    else:
                        trade_date = trade_date_val or datetime.now().date()

                    listing_reason = record.get("listing_reason", "unknown")

                    # 查找现有记录
                    dt_data = (await db.execute(select(DragonTigerData).where(
                        DragonTigerData.stock_code == stock_code,
                        DragonTigerData.trade_date == trade_date,
                        DragonTigerData.listing_reason == listing_reason
                    ))).scalars().first()

                    fields = [
                        "sequence_number", "stock_name", "interpretation", "close_price",
                        "price_change_percent", "net_buy_amount", "buy_amount", "sell_amount",
                        "total_trade_amount", "market_total_trade_amount", "net_buy_ratio",
                        "trade_amount_ratio", "turnover_rate", "floating_market_capitalization",
                        "post_1_day_price_change_percent", "post_2_day_price_change_percent",
                        "post_5_day_price_change_percent", "post_10_day_price_change_percent"
                    ]

                    if dt_data:
                        # 更新现有记录
                        for field in fields:
                            if field in record:
                                setattr(dt_data, field, record[field])
                        dt_data.details = record.get("details", dt_data.details)
                        dt_data.data_source = record.get("data_source", data_source)
                        dt_data.updated_at = datetime.now()
                    else:
                        # 创建新记录
                        new_dt = DragonTigerData(
                            stock_code=stock_code,
                            trade_date=trade_date,
                            listing_reason=listing_reason,
                            data_source=record.get("data_source", data_source),
                            details=record.get("details")
                        )
                        for field in fields:
                            if field in record:
                                setattr(new_dt, field, record[field])
                        db.add(new_dt)

                await db.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save dragon tiger data for {stock_code}: {e}")
            return False

    async def get_northbound_data(self, stock_code: str, limit: int = 1) -> Optional[List[Dict[str, Any]]]:
        """获取北向资金数据"""
        try:
            async with database_module.AsyncSessionLocal() as db:
                data_list = (await db.execute(select(NorthboundData).where(
                    NorthboundData.stock_code == stock_code
                ).order_by(NorthboundData.date.desc()).limit(limit))).scalars().all()

                return [{
                    "stock_code": d.stock_code,
                    "date": d.date.strftime("%Y-%m-%d"),
                    "hold_shares": d.hold_shares,
                    "hold_value": d.hold_value,
                    "hold_ratio": d.hold_ratio,
                    "close_price": d.close_price,
                    "change_percent": d.change_percent,
                    "net_buy_volume": d.net_buy_volume,
                    "net_buy_amount": d.net_buy_amount,
                    "hold_value_change": d.hold_value_change,
                    "data_source": d.data_source
                } for d in data_list]
        except Exception as e:
            logger.error(f"Failed to get northbound data for {stock_code}: {e}")
            return None

    async def get_dragon_tiger_data(self, stock_code: str, limit: int = 1) -> Optional[List[Dict[str, Any]]]:
        """获取龙虎榜数据"""
        try:
            async with database_module.AsyncSessionLocal() as db:
                data_list = (await db.execute(select(DragonTigerData).where(
                    DragonTigerData.stock_code == stock_code
                ).order_by(DragonTigerData.trade_date.desc()).limit(limit))).scalars().all()

                return [{
                    "sequence_number": d.sequence_number,
                    "stock_code": d.stock_code,
                    "stock_name": d.stock_name,
                    "trade_date": d.trade_date.strftime("%Y-%m-%d") if d.trade_date else None,
                    "interpretation": d.interpretation,
                    "close_price": d.close_price,
                    "price_change_percent": d.price_change_percent,
                    "net_buy_amount": d.net_buy_amount,
                    "buy_amount": d.buy_amount,
                    "sell_amount": d.sell_amount,
                    "total_trade_amount": d.total_trade_amount,
                    "market_total_trade_amount": d.market_total_trade_amount,
                    "net_buy_ratio": d.net_buy_ratio,
                    "trade_amount_ratio": d.trade_amount_ratio,
                    "turnover_rate": d.turnover_rate,
                    "floating_market_capitalization": d.floating_market_capitalization,
                    "listing_reason": d.listing_reason,
                    "post_1_day_price_change_percent": d.post_1_day_price_change_percent,
                    "post_2_day_price_change_percent": d.post_2_day_price_change_percent,
                    "post_5_day_price_change_percent": d.post_5_day_price_change_percent,
                    "post_10_day_price_change_percent": d.post_10_day_price_change_percent,
                    "details": d.details,
                    "data_source": d.data_source
                } for d in data_list]
        except Exception as e:
            logger.error(f"Failed to get dragon tiger data for {stock_code}: {e}")
            return None

    async def get_dragon_tiger_data_by_date(self, start_date: date, end_date: date = None) -> Optional[List[Dict[str, Any]]]:
        """根据日期获取龙虎榜数据

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            龙虎榜数据列表
        """
        try:
            async with database_module.AsyncSessionLocal() as db:
                query = select(DragonTigerData).where(
                    DragonTigerData.trade_date >= start_date
                )

                if end_date:
                    query = query.where(DragonTigerData.trade_date <= end_date)
                else:
                    # 如果没有结束日期，就只查开始日期当天
                    query = query.where(DragonTigerData.trade_date <= start_date)

                data_list = (await db.execute(query.order_by(DragonTigerData.trade_date.desc()))).scalars().all()

                return [{
                    "sequence_number": d.sequence_number,
                    "stock_code": d.stock_code,
                    "stock_name": d.stock_name,
                    "trade_date": d.trade_date.strftime("%Y-%m-%d") if d.trade_date else None,
                    "interpretation": d.interpretation,
                    "close_price": d.close_price,
                    "price_change_percent": d.price_change_percent,
                    "net_buy_amount": d.net_buy_amount,
                    "buy_amount": d.buy_amount,
                    "sell_amount": d.sell_amount,
                    "total_trade_amount": d.total_trade_amount,
                    "market_total_trade_amount": d.market_total_trade_amount,
                    "net_buy_ratio": d.net_buy_ratio,
                    "trade_amount_ratio": d.trade_amount_ratio,
                    "turnover_rate": d.turnover_rate,
                    "floating_market_capitalization": d.floating_market_capitalization,
                    "listing_reason": d.listing_reason,
                    "post_1_day_price_change_percent": d.post_1_day_price_change_percent,
                    "post_2_day_price_change_percent": d.post_2_day_price_change_percent,
                    "post_5_day_price_change_percent": d.post_5_day_price_change_percent,
                    "post_10_day_price_change_percent": d.post_10_day_price_change_percent,
                    "details": d.details,
                    "data_source": d.data_source
                } for d in data_list]
        except Exception as e:
            logger.error(f"Failed to get dragon tiger data by date {start_date}: {e}")
            return None

    async def get_stock_data_from_db(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """从数据库获取完整的股票数据"""
        try:
            # 1. 获取基本信息
            basic = await self.get_stock_basic(stock_code)
            if not basic:
                return None

            # 2. 获取实时行情
            realtime = await self.get_stock_realtime_market(stock_code)
            if not realtime:
                return None

            # 3. 获取K线数据 (用于计算技术指标)
            klines = await self.get_latest_kline_data(stock_code, limit=180)
            kline_dict = {"data": klines} if klines else None

            # 4. 获取北向资金
            northbound = await self.get_northbound_data(stock_code, limit=1)
            nb_data = northbound[0] if northbound else {}

            # 5. 获取龙虎榜
            dragon_tiger = await self.get_dragon_tiger_data(stock_code, limit=1)
            dt_data = dragon_tiger[0] if dragon_tiger else {}

            # 6. 获取行业数据
            industry_name = basic.get("industry", "未知")
            async with database_module.AsyncSessionLocal() as db:
                industry_record = (await db.execute(
                    select(IndustryData)
                    .where(IndustryData.board_name == industry_name)
                    .order_by(IndustryData.timestamp.desc())
                    .limit(1)
                )).scalars().first()
                industry_data = self._to_dict(industry_record) if industry_record else None

            # 组装数据结构 (与 DataCollector.get_stock_data 返回结构一致)
            stock_data = {
                "stock_code": stock_code,
                "stock_name": basic.get("name"),
                "exchange": basic.get("market"),
                "yesterday_close": realtime.get("yesterday_close", 0),
                "market_data": {
                    "current_price": realtime.get("latest_price", 0),
                    "change": realtime.get("change_amount", 0),
                    "change_pct": realtime.get("change_percent", 0),
                    "volume": realtime.get("volume", 0),
                    "turnover": realtime.get("turnover", 0),
                    "pe_ttm": realtime.get("pe_dynamic", 0),  # Changed from pe_ttm to pe_dynamic
                    "pb": realtime.get("pb", 0),
                    "market_cap": realtime.get("total_market_value", 0),
                    "float_market_cap": realtime.get(
                        "circulating_market_value", 0
                    ),
                    "high": realtime.get("high", 0),
                    "low": realtime.get("low", 0),
                    "open": realtime.get("open", 0),
                    "last_updated": realtime.get("update_time")
                },
                "technical_indicators": {},  # 由 Collector 计算
                "fundamentals": {},  # 由 Collector 构建
                "industry": {
                    "name": industry_name,
                    "pe_avg": industry_data.get("pe_avg", 0)
                    if industry_data else 0,
                    "pb_avg": industry_data.get("pb_avg", 0)
                    if industry_data else 0,
                    "rank": industry_data.get("rank", 0)
                    if industry_data else 0,
                    "industry_data": industry_data or {}
                },
                "northbound_funds": nb_data,
                "dragon_tiger": dt_data,
                "policy_news": [],
                "company_news": [],
                "stock_related_news": [],
                "sentiment_data": {
                    "snowball_sentiment": 0,
                    "market_forum_sentiment": 0,
                    "weibo_sentiment": 0,
                    "heat_index": 0,
                    "news_sentiment": 0,
                    "comment_sentiment": 0
                },
                "announcements": []
            }

            # 补充 K-line 以便计算技术指标
            stock_data["_kline_data"] = kline_dict

            return stock_data
        except Exception as e:
            logger.error(f"Failed to get complete stock data from DB for {stock_code}: {e}")
            return None

    async def get_stock_basic(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """获取股票基本信息

        Args:
            stock_code: 股票代码

        Returns:
            股票基本信息数据
        """
        try:
            async with database_module.AsyncSessionLocal() as db:
                stock_basic = (await db.execute(
                    select(StockBasic).where(StockBasic.stock_code == stock_code)
                )).scalars().first()
                if stock_basic:
                    return {
                        "id": str(stock_basic.id),
                        "stock_code": stock_basic.stock_code,
                        "name": stock_basic.name,
                        "industry": stock_basic.industry,
                        "sector": stock_basic.sector,
                        "market": stock_basic.market,
                        "list_date": stock_basic.list_date.strftime("%Y-%m-%d") if stock_basic.list_date else None,
                        "total_share": stock_basic.total_share,
                        "float_share": stock_basic.float_share,
                        "data_source": stock_basic.data_source,
                        "last_updated": (
                            stock_basic.updated_at.strftime("%Y-%m-%d %H:%M:%S")
                            if stock_basic.updated_at
                            else None
                        )
                    }
                return None
        except Exception as e:
            logger.error(f"Failed to get stock basic information for {stock_code}: {e}")
            return None

    async def get_kline_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        freq: str = "D",
    ) -> Optional[List[Dict[str, Any]]]:
        """获取股票K线数据

        Args:
            stock_code: 股票代码
            start_date: 开始日期，格式为YYYY-MM-DD
            end_date: 结束日期，格式为YYYY-MM-DD
            freq: K线频率

        Returns:
            K线数据列表
        """
        try:
            async with database_module.AsyncSessionLocal() as db:

                # 转换日期格式，支持 YYYY-MM-DD 和 YYYYMMDD 两种格式
                try:
                    # 尝试 YYYY-MM-DD 格式
                    start = datetime.strptime(start_date, "%Y-%m-%d").date()
                except ValueError:
                    # 尝试 YYYYMMDD 格式
                    start = datetime.strptime(start_date, "%Y%m%d").date()

                try:
                    # 尝试 YYYY-MM-DD 格式
                    end = datetime.strptime(end_date, "%Y-%m-%d").date()
                except ValueError:
                    # 尝试 YYYYMMDD 格式
                    end = datetime.strptime(end_date, "%Y%m%d").date()

                # 查询K线数据
                kline_data_list = (await db.execute(select(KlineData).where(
                    KlineData.stock_code == stock_code,
                    KlineData.date >= start,
                    KlineData.date <= end,
                    KlineData.freq == freq
                ).order_by(KlineData.date))).scalars().all()

                # 转换为字典列表
                result = []
                for kline_data in kline_data_list:
                    result.append({
                        "date": kline_data.date.strftime("%Y-%m-%d"),
                        "open": kline_data.open,
                        "high": kline_data.high,
                        "low": kline_data.low,
                        "close": kline_data.close,
                        "volume": kline_data.volume,
                        "turnover": kline_data.turnover,
                        "change": kline_data.change,
                        "change_percent": kline_data.change_percent,
                        "data_source": kline_data.data_source
                    })

                return result
        except Exception as e:
            logger.error(f"Failed to get kline data for {stock_code}: {e}")
            return None

    async def get_latest_kline_data(
        self,
        stock_code: str,
        freq: str = "D",
        limit: int = 30,
    ) -> Optional[List[Dict[str, Any]]]:
        """获取股票最新K线数据

        Args:
            stock_code: 股票代码
            freq: K线频率
            limit: 返回数据条数

        Returns:
            K线数据列表
        """
        try:
            async with database_module.AsyncSessionLocal() as db:

                # 查询最新K线数据
                kline_data_list = (await db.execute(select(KlineData).where(
                    KlineData.stock_code == stock_code,
                    KlineData.freq == freq
                ).order_by(KlineData.date.desc()).limit(limit))).scalars().all()

                # 转换为字典列表，并反转顺序，使其按日期升序排列
                result = []
                for kline_data in reversed(kline_data_list):
                    result.append({
                        "date": kline_data.date.strftime("%Y-%m-%d"),
                        "open": kline_data.open,
                        "high": kline_data.high,
                        "low": kline_data.low,
                        "close": kline_data.close,
                        "volume": kline_data.volume,
                        "turnover": kline_data.turnover,
                        "change": kline_data.change,
                        "change_percent": kline_data.change_percent,
                        "data_source": kline_data.data_source
                    })

                return result
        except Exception as e:
            logger.error(f"Failed to get latest kline data for {stock_code}: {e}")
            return None

    async def check_kline_data_exists(self, stock_code: str, date: str, freq: str = "D") -> bool:
        """检查指定日期的K线数据是否存在

        Args:
            stock_code: 股票代码
            date: 日期，格式为YYYY-MM-DD
            freq: K线频率

        Returns:
            是否存在
        """
        try:
            async with database_module.AsyncSessionLocal() as db:

                # 转换日期格式
                check_date = datetime.strptime(date, "%Y-%m-%d").date()

                # 查询数据是否存在
                exists = (await db.execute(select(KlineData).where(
                    KlineData.stock_code == stock_code,
                    KlineData.date == check_date,
                    KlineData.freq == freq
                ))).scalars().first() is not None

                return exists
        except Exception as e:
            logger.error(f"Failed to check kline data existence for {stock_code} on {date}: {e}")
            return False

    async def get_stock_limit_up_pool(self, trade_date: Optional[date] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取涨停池数据"""
        async with database_module.AsyncSessionLocal() as db:
            query = select(StockLimitUpPool)
            if trade_date:
                query = query.where(StockLimitUpPool.trade_date == trade_date)
            else:
                # 获取最新交易日
                latest = (await db.execute(select(StockLimitUpPool.trade_date).order_by(StockLimitUpPool.trade_date.desc()))).first()
                if latest:
                    query = query.where(StockLimitUpPool.trade_date == latest[0])

            records = (await db.execute(query.order_by(StockLimitUpPool.limit_up_days.desc()).limit(limit))).scalars().all()
            return [self._to_dict(r) for r in records]

    async def get_stock_money_flow(self, stock_code: str, limit: int = 10) -> List[Dict[str, Any]]:
        """获取个股资金流向"""
        async with database_module.AsyncSessionLocal() as db:
            records = (await db.execute(select(StockMoneyFlow).where(
                StockMoneyFlow.stock_code == stock_code
            ).order_by(StockMoneyFlow.trade_date.desc()).limit(limit))).scalars().all()
            return [self._to_dict(r) for r in records]

    async def get_stock_shareholder_count(self, stock_code: str, limit: int = 10) -> List[Dict[str, Any]]:
        """获取股东人数变动"""
        async with database_module.AsyncSessionLocal() as db:
            records = (await db.execute(select(StockShareholder).where(
                StockShareholder.stock_code == stock_code
            ).order_by(StockShareholder.end_date.desc()).limit(limit))).scalars().all()
            return [self._to_dict(r) for r in records]

    async def get_stock_pledge_risk(self, stock_code: str) -> List[Dict[str, Any]]:
        """获取个股质押风险"""
        async with database_module.AsyncSessionLocal() as db:
            records = (await db.execute(select(StockPledge).where(
                StockPledge.stock_code == stock_code
            ).order_by(StockPledge.pledge_date.desc()))).scalars().all()
            return [self._to_dict(r) for r in records]

    async def get_stock_insider_trading(self, stock_code: str, limit: int = 20) -> List[Dict[str, Any]]:
        """获取大股东增减持"""
        async with database_module.AsyncSessionLocal() as db:
            records = (await db.execute(select(StockInsider).where(
                StockInsider.stock_code == stock_code
            ).order_by(StockInsider.ann_date.desc()).limit(limit))).scalars().all()
            return [self._to_dict(r) for r in records]

    async def get_stock_lockup_release(self, stock_code: str, limit: int = 10) -> List[Dict[str, Any]]:
        """获取个股解禁日程"""
        async with database_module.AsyncSessionLocal() as db:
            records = (await db.execute(select(StockRelease).where(
                StockRelease.stock_code == stock_code
            ).order_by(StockRelease.release_date.desc()).limit(limit))).scalars().all()
            return [self._to_dict(r) for r in records]

    async def get_stock_margin_data(self, stock_code: str, limit: int = 30) -> List[Dict[str, Any]]:
        """获取融资融券数据"""
        async with database_module.AsyncSessionLocal() as db:
            records = (await db.execute(select(StockMargin).where(
                StockMargin.stock_code == stock_code
            ).order_by(StockMargin.trade_date.desc()).limit(limit))).scalars().all()
            return [self._to_dict(r) for r in records]

    def _to_dict(self, record):
        """Helper to convert SQLAlchemy record to dict"""
        if not record:
            return None
        d = {}
        for column in record.__table__.columns:
            val = getattr(record, column.name)
            if isinstance(val, (date, datetime)):
                val = val.isoformat()
            d[column.name] = val
        return d


# 创建全局数据存储服务实例
data_storage_service = DataStorageService()
