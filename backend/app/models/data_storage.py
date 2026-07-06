
from  datetime  import  datetime
import uuid

from  sqlalchemy  import  (
  Column,  String,  Float,  Date,  DateTime,  UniqueConstraint,  BigInteger,
  Integer,  Text,  ForeignKey, Index
)
from  sqlalchemy.dialects.postgresql  import  UUID,  JSONB

from  app.core.database  import  Base



class  StockBasic(Base):
  """Stock  basic  information"""
  __tablename__  =  "stock_basic"
  __table_args__ = {"schema": "data"}

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  unique=True,  index=True,  nullable=False)
  name  =  Column(String(100),  nullable=False)
  industry  =  Column(String(100))
  sector  =  Column(String(100))
  area  =  Column(String(100))
  list_date  =  Column(Date)
  market  =  Column(String(50))
  total_share  =  Column(Float, info={"name": "stock_basic.total_share", "unit": "units.shares"})
  float_share  =  Column(Float, info={"name": "stock_basic.float_share", "unit": "units.shares"})
  status  =  Column(String(20),  default='L')  #  L:  Listed,  D:  Delisted
  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)


class KlineData(Base):
  """Kline  data  for  stocks"""
  __tablename__  =  "kline_data"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  date  =  Column(Date,  nullable=False,  index=True)
  open  =  Column(Float, info={"name": "kline_data.open", "unit": "units.cny"})
  close  =  Column(Float, info={"name": "kline_data.close", "unit": "units.cny"})
  high  =  Column(Float, info={"name": "kline_data.high", "unit": "units.cny"})
  low  =  Column(Float, info={"name": "kline_data.low", "unit": "units.cny"})
  volume  =  Column(Float, info={"name": "kline_data.volume", "unit": "units.lots"})
  turnover  =  Column(Float, info={"name": "kline_data.turnover", "unit": "units.cny"})
  change  =  Column(Float, info={"name": "kline_data.change", "unit": "units.cny"})
  change_percent  =  Column(Float, info={"name": "kline_data.change_percent", "unit": "units.percent"})
  freq  =  Column(String(10),  default='D')  #  D:  Daily,  W:  Weekly,  M:  Monthly
  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint(
  'stock_code',  'date',  'freq',
  name='idx_kline_stock_date_freq_unique'
  ),
  {"schema": "data"}
  )


class StockHotRank(Base):
    """
    个股人气榜排名数据 (来自外部热度榜等社交媒体维度)
    Stock Hot Rank / Popularity ranking from social media sources
    """
    __tablename__ = "stock_hot_rank"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    
    rank = Column(Integer, nullable=False, info={"name": "stock_hot_rank.rank", "unit": "units.rank"})  # 当前排名
    rank_change = Column(Integer, info={"name": "stock_hot_rank.rank_change", "unit": "units.rank"})  # 排名变化 (如有)
    hot_value = Column(Float, info={"name": "stock_hot_rank.hot_value", "unit": "units.score"})  # 热度值 (如有)
    rank_type = Column(String(20), default='hot') # 排名类型: 'hot' (人气榜), 'rising' (飙升榜)
    current_rank = Column(Integer, info={"name": "stock_hot_rank.current_rank", "unit": "units.rank"})  # 在人气总榜中的当前排名
    
    # 新增行情与粉丝特征字段
    new_fans = Column(Float, info={"name": "stock_hot_rank.new_fans", "unit": "units.percent"})  # 新晋粉丝占比(%)
    irons_fans = Column(Float, info={"name": "stock_hot_rank.irons_fans", "unit": "units.percent"})  # 铁杆粉丝占比(%)
    
    timestamp = Column(DateTime, default=datetime.now, index=True)
    data_source = Column(String(20), default='external')
    
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_hot_rank_query', 'stock_code', 'timestamp', 'rank_type'),
        {"schema": "data"}
    )


class  NorthboundData(Base):
  """Northbound  (HK-Mainland)  money  flow  data"""
  __tablename__  =  "northbound_data"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  index=True)
  date  =  Column(Date,  nullable=False,  index=True)
  hold_shares  =  Column(Float, info={"name": "northbound_data.hold_shares", "unit": "units.shares"})
  hold_value  =  Column(Float, info={"name": "northbound_data.hold_value", "unit": "units.cny"})
  hold_ratio  =  Column(Float, info={"name": "northbound_data.hold_ratio", "unit": "units.ratio"})

  #  New  Fields  (2024-02  Update)
  close_price  =  Column(Float, info={"name": "northbound_data.close_price", "unit": "units.cny"})  #  当日收盘价
  change_percent  =  Column(Float, info={"name": "northbound_data.change_percent", "unit": "units.percent"})  #  当日涨跌幅
  net_buy_volume  =  Column(Float, info={"name": "northbound_data.net_buy_volume", "unit": "units.shares"})  #  今日增持股数
  net_buy_amount  =  Column(Float, info={"name": "northbound_data.net_buy_amount", "unit": "units.cny"})  #  今日增持资金
  hold_value_change  =  Column(Float, info={"name": "northbound_data.hold_value_change", "unit": "units.cny"})  #  今日持股市值变化
  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint(
  'stock_code',  'date',  name='idx_northbound_stock_date_unique'
  ),
  {"schema": "data"}
  )


class  DragonTigerData(Base):
  """Dragon  Tiger  List  (Top  players)  data"""
  __tablename__  =  "dragon_tiger_data"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey("data.stock_basic.stock_code",  ondelete="CASCADE"),  nullable=False,  index=True)
  stock_name  =  Column(String(100))
  trade_date  =  Column(Date,  nullable=False,  index=True)
  net_buy_amount  =  Column(Float, info={"name": "dragon_tiger_data.net_buy_amount", "unit": "units.cny"})
  buy_amount  =  Column(Float, info={"name": "dragon_tiger_data.buy_amount", "unit": "units.cny"})
  sell_amount  =  Column(Float, info={"name": "dragon_tiger_data.sell_amount", "unit": "units.cny"})
  price_change_percent  =  Column(Float, info={"name": "dragon_tiger_data.price_change_percent", "unit": "units.percent"})
  listing_reason  =  Column(String(500))

  #  Missing  Columns  added
  sequence_number  =  Column(Integer, info={"name": "dragon_tiger_data.sequence_number", "unit": "units.sequence"})
  interpretation  =  Column(Text)
  close_price  =  Column(Float, info={"name": "dragon_tiger_data.close_price", "unit": "units.cny"})
  total_trade_amount  =  Column(Float, info={"name": "dragon_tiger_data.total_trade_amount", "unit": "units.cny"})
  market_total_trade_amount  =  Column(Float, info={"name": "dragon_tiger_data.market_total_trade_amount", "unit": "units.cny"})
  net_buy_ratio  =  Column(Float, info={"name": "dragon_tiger_data.net_buy_ratio", "unit": "units.percent"})
  trade_amount_ratio  =  Column(Float, info={"name": "dragon_tiger_data.trade_amount_ratio", "unit": "units.percent"})
  turnover_rate  =  Column(Float, info={"name": "dragon_tiger_data.turnover_rate", "unit": "units.percent"})
  floating_market_capitalization  =  Column(Float, info={"name": "dragon_tiger_data.floating_market_capitalization", "unit": "units.cny"})

  post_1_day_price_change_percent  =  Column(Float, info={"name": "dragon_tiger_data.post_1_day_price_change_percent", "unit": "units.percent"})
  post_2_day_price_change_percent  =  Column(Float, info={"name": "dragon_tiger_data.post_2_day_price_change_percent", "unit": "units.percent"})
  post_5_day_price_change_percent  =  Column(Float, info={"name": "dragon_tiger_data.post_5_day_price_change_percent", "unit": "units.percent"})
  post_10_day_price_change_percent  =  Column(Float, info={"name": "dragon_tiger_data.post_10_day_price_change_percent", "unit": "units.percent"})

  details  =  Column(JSONB)  #  For  nested  details  if  any

  data_source  =  Column(String(20),  nullable=False,  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint(
  'stock_code',  'trade_date',  'listing_reason',
  name='idx_dragon_tiger_unique'
  ),
  {"schema": "data"}
  )


class  StockRealtimeMarket(Base):
  """Real-time  market  data  from  Tushare"""
  __tablename__  =  "stock_realtime_market"
  __table_args__ = {"schema": "data"}

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  current_price  =  Column(Float, info={"name": "stock_realtime_market.current_price", "unit": "units.cny"})  #  最新价
  change_percent  =  Column(Float, info={"name": "stock_realtime_market.change_percent", "unit": "units.percent"})  #  涨跌幅
  change_amount  =  Column(Float, info={"name": "stock_realtime_market.change_amount", "unit": "units.cny"})  #  涨跌额
  volume  =  Column(Float, info={"name": "stock_realtime_market.volume", "unit": "units.shares"})  #  成交量
  turnover  =  Column(Float, info={"name": "stock_realtime_market.turnover", "unit": "units.cny"})  #  成交额
  amplitude  =  Column(Float, info={"name": "stock_realtime_market.amplitude", "unit": "units.percent"})  #  振幅
  high  =  Column(Float, info={"name": "stock_realtime_market.high", "unit": "units.cny"})  #  最高
  low  =  Column(Float, info={"name": "stock_realtime_market.low", "unit": "units.cny"})  #  最低
  open  =  Column(Float, info={"name": "stock_realtime_market.open", "unit": "units.cny"})  #  今开
  prev_close  =  Column(Float, info={"name": "stock_realtime_market.prev_close", "unit": "units.cny"})  #  昨收
  volume_ratio  =  Column(Float, info={"name": "stock_realtime_market.volume_ratio", "unit": "units.multiple"})  #  量比
  turnover_rate  =  Column(Float, info={"name": "stock_realtime_market.turnover_rate", "unit": "units.percent"})  #  换手率
  pe_dynamic  =  Column(Float, info={"name": "stock_realtime_market.pe_dynamic", "unit": "units.multiple"})  #  市盈率-动态
  pb_ratio  =  Column(Float, info={"name": "stock_realtime_market.pb_ratio", "unit": "units.multiple"})  #  市净率
  total_market_cap  =  Column(Float, info={"name": "stock_realtime_market.total_market_cap", "unit": "units.cny"})  #  总市值
  circulating_market_cap  =  Column(Float, info={"name": "stock_realtime_market.circulating_market_cap", "unit": "units.cny"})  #  流通市值
  speed_increase  =  Column(Float, info={"name": "stock_realtime_market.speed_increase", "unit": "units.percent"})  #  涨速
  change_5min  =  Column(Float, info={"name": "stock_realtime_market.change_5min", "unit": "units.percent"})  #  5分钟涨跌
  change_60days  =  Column(Float, info={"name": "stock_realtime_market.change_60days", "unit": "units.percent"})  #  60日涨跌幅
  change_ytd  =  Column(Float, info={"name": "stock_realtime_market.change_ytd", "unit": "units.percent"})  #  年初至今涨跌幅

  # 外部增强字段: 资金流与排名
  # 今日 (1日)
  main_net_inflow_today = Column(Float, info={"name": "stock_realtime_market.main_net_inflow_today", "unit": "units.cny"})  # f137
  super_big_inflow_today = Column(Float, info={"name": "stock_realtime_market.super_big_inflow_today", "unit": "units.cny"})  # f140
  big_inflow_today = Column(Float, info={"name": "stock_realtime_market.big_inflow_today", "unit": "units.cny"})  # f143
  mid_inflow_today = Column(Float, info={"name": "stock_realtime_market.mid_inflow_today", "unit": "units.cny"})  # f146
  small_inflow_today = Column(Float, info={"name": "stock_realtime_market.small_inflow_today", "unit": "units.cny"})  # f149
  main_net_inflow_rank_today = Column(Integer, info={"name": "stock_realtime_market.main_net_inflow_rank_today", "unit": "units.rank"})  # f469

  # 5日
  main_net_inflow_5d = Column(Float, info={"name": "stock_realtime_market.main_net_inflow_5d", "unit": "units.cny"})  # f434
  super_big_inflow_5d = Column(Float, info={"name": "stock_realtime_market.super_big_inflow_5d", "unit": "units.cny"})  # f435
  big_inflow_5d = Column(Float, info={"name": "stock_realtime_market.big_inflow_5d", "unit": "units.cny"})  # f436
  mid_inflow_5d = Column(Float, info={"name": "stock_realtime_market.mid_inflow_5d", "unit": "units.cny"})  # f437
  small_inflow_5d = Column(Float, info={"name": "stock_realtime_market.small_inflow_5d", "unit": "units.cny"})  # f438
  main_net_inflow_rank_5d = Column(Integer, info={"name": "stock_realtime_market.main_net_inflow_rank_5d", "unit": "units.rank"})  # f470

  # 10日
  main_net_inflow_10d = Column(Float, info={"name": "stock_realtime_market.main_net_inflow_10d", "unit": "units.cny"})  # f459
  super_big_inflow_10d = Column(Float, info={"name": "stock_realtime_market.super_big_inflow_10d", "unit": "units.cny"})  # f461
  big_inflow_10d = Column(Float, info={"name": "stock_realtime_market.big_inflow_10d", "unit": "units.cny"})  # f463
  mid_inflow_10d = Column(Float, info={"name": "stock_realtime_market.mid_inflow_10d", "unit": "units.cny"})  # f465
  small_inflow_10d = Column(Float, info={"name": "stock_realtime_market.small_inflow_10d", "unit": "units.cny"})  # f467
  main_net_inflow_rank_10d = Column(Integer, info={"name": "stock_realtime_market.main_net_inflow_rank_10d", "unit": "units.rank"})  # f471

  timestamp  =  Column(DateTime)  # 数据源返回的行情时间
  data_source  =  Column(String(20),  nullable=False,  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)



class  StockValuationHistory(Base):
  """Stock  valuation  history  data  from  stock_value_em  interface"""
  __tablename__  =  "stock_valuation_history"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  data_date  =  Column(Date,  nullable=False,  index=True)  #  数据日期
  close_price  =  Column(Float, info={"name": "stock_valuation_history.close_price", "unit": "units.cny"})  #  当日收盘价  (元)
  change_percent  =  Column(Float, info={"name": "stock_valuation_history.change_percent", "unit": "units.percent"})  #  当日涨跌幅  (%)
  total_market_value  =  Column(BigInteger, info={"name": "stock_valuation_history.total_market_value", "unit": "units.cny"})  #  总市值  (元)
  circulating_market_value  =  Column(BigInteger, info={"name": "stock_valuation_history.circulating_market_value", "unit": "units.cny"})  #  流通市值  (元)
  total_share  =  Column(Float, info={"name": "stock_valuation_history.total_share", "unit": "units.shares"})  #  总股本  (股)
  float_share  =  Column(Float, info={"name": "stock_valuation_history.float_share", "unit": "units.shares"})  #  流通股本  (股)
  free_share  =  Column(Float, info={"name": "stock_valuation_history.free_share", "unit": "units.shares"})  #  自由流通股本  (股)
  pe_ttm  =  Column(Float, info={"name": "stock_valuation_history.pe_ttm", "unit": "units.multiple"})  #  市盈率  (TTM)
  pe_static  =  Column(Float, info={"name": "stock_valuation_history.pe_static", "unit": "units.multiple"})  #  市盈率  (静)
  pb  =  Column(Float, info={"name": "stock_valuation_history.pb", "unit": "units.multiple"})  #  市净率
  ps_ttm  =  Column(Float, info={"name": "stock_valuation_history.ps_ttm", "unit": "units.multiple"})  #  市销率  (TTM)
  ps_static  =  Column(Float, info={"name": "stock_valuation_history.ps_static", "unit": "units.multiple"})  #  市销率  (静)
  peg  =  Column(Float, info={"name": "stock_valuation_history.peg", "unit": "units.multiple"})  #  PEG
  dividend_yield  =  Column(Float, info={"name": "stock_valuation_history.dividend_yield", "unit": "units.percent"})  #  股息率  (%)
  data_source  =  Column(String(20),  nullable=False,  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint(
  'stock_code',  'data_date',
  name='idx_valuation_stock_date_unique'
  ),
  {"schema": "data"}
  )


class  IndustryData(Base):
  """Industry  board  data  from  Tushare"""
  __tablename__  =  "industry_data"
  __table_args__ = {"schema": "data"}

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  board_code  =  Column(String(20),  nullable=False,  unique=True,  index=True)
  board_name  =  Column(String(100),  index=True)
  rank  =  Column(Integer, info={"name": "industry_data.rank", "unit": "units.rank"})
  latest_price  =  Column(Float, info={"name": "industry_data.latest_price", "unit": "units.points"})
  change_amount  =  Column(Float, info={"name": "industry_data.change_amount", "unit": "units.points"})
  change_percent  =  Column(Float, info={"name": "industry_data.change_percent", "unit": "units.percent"})
  total_market_cap  =  Column(Float, info={"name": "industry_data.total_market_cap", "unit": ["units.ten_thousand", "units.cny"]})
  turnover_rate  =  Column(Float, info={"name": "industry_data.turnover_rate", "unit": "units.percent"})
  rising_stocks_count  =  Column(Integer, info={"name": "industry_data.rising_stocks_count", "unit": "units.stocks"})
  falling_stocks_count  =  Column(Integer, info={"name": "industry_data.falling_stocks_count", "unit": "units.stocks"})
  leading_stock_name  =  Column(String(100))
  leading_stock_change_percent  =  Column(Float, info={"name": "industry_data.leading_stock_change_percent", "unit": "units.percent"})
  timestamp  =  Column(DateTime,  default=datetime.utcnow)
  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.utcnow)
  updated_at  =  Column(DateTime,  default=datetime.utcnow,  onupdate=datetime.now)


class  StockLimitUpPool(Base):
  """每日涨停池数据  (A股情绪监控核心)"""
  __tablename__  =  "stock_limit_up_pool"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  stock_name  =  Column(String(100))
  update_date  =  Column(Date,  nullable=False,  index=True)

  #  涨停详情
  limit_up_price  =  Column(Float, info={"name": "stock_limit_up_pool.limit_up_price", "unit": "units.cny"})
  pct_chg  =  Column(Float, info={"name": "stock_limit_up_pool.pct_chg", "unit": "units.percent"})
  turnover  =  Column(Float, info={"name": "stock_limit_up_pool.turnover", "unit": "units.cny"})
  circ_mv  =  Column(Float, info={"name": "stock_limit_up_pool.circ_mv", "unit": "units.cny"})
  total_mv  =  Column(Float, info={"name": "stock_limit_up_pool.total_mv", "unit": "units.cny"})

  #  情绪指标
  first_limit_up_time  =  Column(String(20))  #  首次封板时间
  last_limit_up_time  =  Column(String(20))  #  最后封板时间
  limit_up_type  =  Column(String(100))  #  涨停形态  (e.g.  一字板,  T字板)
  limit_up_days  =  Column(String(50),  default="1")  #  连板天数
  limit_up_stats  =  Column(String(50))  #  涨停统计  (x/y)
  limit_up_reason  =  Column(Text)  #  涨停原因  (所属题材)

  #  补充字段
  turnover_rate  =  Column(Float, info={"name": "stock_limit_up_pool.turnover_rate", "unit": "units.percent"})  #  换手率
  fund_amount  =  Column(Float, info={"name": "stock_limit_up_pool.fund_amount", "unit": "units.cny"})  #  封板资金
  open_times  =  Column(Integer, info={"name": "stock_limit_up_pool.open_times", "unit": "units.times"})  #  炸板次数

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'update_date',  name='idx_limit_up_stock_date_unique'),
  {"schema": "data"}
  )


class  StockZhabanPool(Base):
  """每日炸板池数据  (A股情绪监控核心)"""
  __tablename__  =  "stock_zhaban_pool"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  stock_name  =  Column(String(100))
  update_date  =  Column(Date,  nullable=False,  index=True)

  #  炸板详情
  latest_price  =  Column(Float, info={"name": "stock_zhaban_pool.latest_price", "unit": "units.cny"})  #  最新价
  limit_up_price  =  Column(Float, info={"name": "stock_zhaban_pool.limit_up_price", "unit": "units.cny"})  #  涨停价
  pct_chg  =  Column(Float, info={"name": "stock_zhaban_pool.pct_chg", "unit": "units.percent"})  #  涨跌幅
  turnover  =  Column(Float, info={"name": "stock_zhaban_pool.turnover", "unit": "units.cny"})  #  成交额
  circ_mv  =  Column(Float, info={"name": "stock_zhaban_pool.circ_mv", "unit": "units.cny"})  #  流通市值
  total_mv  =  Column(Float, info={"name": "stock_zhaban_pool.total_mv", "unit": "units.cny"})  #  总市值

  #  情绪指标
  first_limit_up_time  =  Column(String(20))  #  首次封板时间
  last_limit_up_time  =  Column(String(20))  #  炸板时间  (Last  limit  up  or  break  time)
  limit_up_type  =  Column(String(100))  #  涨停形态  (Optional)

  #  补充字段
  turnover_rate  =  Column(Float, info={"name": "stock_zhaban_pool.turnover_rate", "unit": "units.percent"})  #  换手率
  swing  =  Column(Float, info={"name": "stock_zhaban_pool.swing", "unit": "units.percent"})  #  振幅
  open_times  =  Column(Integer, info={"name": "stock_zhaban_pool.open_times", "unit": "units.times"})  #  炸板次数
  limit_up_stats  =  Column(String(50))  #  涨停统计  (x/y)
  limit_up_reason  =  Column(Text)  #  所属行业/涨停原因
  speed_increase  =  Column(Float, info={"name": "stock_zhaban_pool.speed_increase", "unit": "units.percent"})  #  涨速

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'update_date',  name='idx_zhaban_stock_date_unique'),
  {"schema": "data"}
  )


class  StockLimitDownPool(Base):
  """每日跌停池数据  (A股情绪监控核心)"""
  __tablename__  =  "stock_limit_down_pool"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  stock_name  =  Column(String(100))
  update_date  =  Column(Date,  nullable=False,  index=True)

  #  跌停详情
  limit_down_price  =  Column(Float, info={"name": "stock_limit_down_pool.limit_down_price", "unit": "units.cny"})
  pct_chg  =  Column(Float, info={"name": "stock_limit_down_pool.pct_chg", "unit": "units.percent"})
  turnover  =  Column(Float, info={"name": "stock_limit_down_pool.turnover", "unit": "units.cny"})
  circ_mv  =  Column(Float, info={"name": "stock_limit_down_pool.circ_mv", "unit": "units.cny"})
  total_mv  =  Column(Float, info={"name": "stock_limit_down_pool.total_mv", "unit": "units.cny"})

  #  情绪指标
  first_limit_down_time  =  Column(String(20))  #  首次封板时间
  last_limit_down_time  =  Column(String(20))  #  最后封板时间
  limit_down_type  =  Column(String(100))  #  跌停形态
  limit_down_days  =  Column(String(50),  default="1")  #  连板天数
  limit_down_stats  =  Column(String(50))  #  跌停统计
  limit_down_reason  =  Column(Text)  #  跌停原因

  #  补充字段
  turnover_rate  =  Column(Float, info={"name": "stock_limit_down_pool.turnover_rate", "unit": "units.percent"})  #  换手率
  fund_amount  =  Column(Float, info={"name": "stock_limit_down_pool.fund_amount", "unit": "units.cny"})  #  封单资金
  open_times  =  Column(Integer, info={"name": "stock_limit_down_pool.open_times", "unit": "units.times"})  #  炸板次数
  board_turnover  =  Column(Float, info={"name": "stock_limit_down_pool.board_turnover", "unit": "units.cny"})  #  板上成交额
  dynamic_pe  =  Column(Float, info={"name": "stock_limit_down_pool.dynamic_pe", "unit": "units.multiple"})  #  动态市盈率

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'update_date',  name='idx_limit_down_stock_date_unique'),
  {"schema": "data"}
  )


class  StockMoneyFlow(Base):
  """个股资金流向数据  (主力  vs  散户博弈)"""
  __tablename__  =  "stock_money_flow"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  trade_date  =  Column(Date,  nullable=False,  index=True)

  #  五档资金流向  (单位:  万元)
  net_inflow_small  =  Column(Float, info={"name": "stock_money_flow.net_inflow_small", "unit": "units.cny"})  #  小单净流入
  net_inflow_medium  =  Column(Float, info={"name": "stock_money_flow.net_inflow_medium", "unit": "units.cny"})  #  中单净流入
  net_inflow_large  =  Column(Float, info={"name": "stock_money_flow.net_inflow_large", "unit": "units.cny"})  #  大单净流入
  net_inflow_huge  =  Column(Float, info={"name": "stock_money_flow.net_inflow_huge", "unit": "units.cny"})  #  特大单净流入
  net_inflow_main  =  Column(Float, info={"name": "stock_money_flow.net_inflow_main", "unit": "units.cny"})  #  主力净流入  (大单+特大单)


  #  占比  (%)
  net_inflow_ratio_main  =  Column(Float, info={"name": "stock_money_flow.net_inflow_ratio_main", "unit": "units.percent"})

  #  扩展字段
  close_price  =  Column(Float, info={"name": "stock_money_flow.close_price", "unit": "units.cny"})  #  收盘价
  change_pct  =  Column(Float, info={"name": "stock_money_flow.change_pct", "unit": "units.percent"})  #  涨跌幅

  net_inflow_ratio_huge  =  Column(Float, info={"name": "stock_money_flow.net_inflow_ratio_huge", "unit": "units.percent"})  #  超大单净流入占比
  net_inflow_ratio_large  =  Column(Float, info={"name": "stock_money_flow.net_inflow_ratio_large", "unit": "units.percent"})  #  大单净流入占比
  net_inflow_ratio_medium  =  Column(Float, info={"name": "stock_money_flow.net_inflow_ratio_medium", "unit": "units.percent"})  #  中单净流入占比
  net_inflow_ratio_small  =  Column(Float, info={"name": "stock_money_flow.net_inflow_ratio_small", "unit": "units.percent"})  #  小单净流入占比

  #  累计天数流入  (可选存储)
  net_inflow_main_3d  =  Column(Float, info={"name": "stock_money_flow.net_inflow_main_3d", "unit": "units.cny"})
  net_inflow_main_5d  =  Column(Float, info={"name": "stock_money_flow.net_inflow_main_5d", "unit": "units.cny"})
  net_inflow_main_10d  =  Column(Float, info={"name": "stock_money_flow.net_inflow_main_10d", "unit": "units.cny"})

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'trade_date',  name='idx_money_flow_stock_date_unique'),
  {"schema": "data"}
  )


class  StockShareholder(Base):
  """股东人数变动  (筹码集中度评价)"""
  __tablename__  =  "stock_shareholder_count"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  end_date  =  Column(Date,  nullable=False,  index=True)  #  披露截止日期
  ann_date  =  Column(Date,  index=True)  #  公告日期
  holder_count  =  Column(BigInteger, info={"name": "stock_shareholder_count.holder_count", "unit": "units.households"})  #  股东户数
  holder_count_prev  =  Column(BigInteger, info={"name": "stock_shareholder_count.holder_count_prev", "unit": "units.households"})  #  上期股东户数
  holder_count_change  =  Column(Float, info={"name": "stock_shareholder_count.holder_count_change", "unit": "units.households"})  #  户数变动  (绝对值)
  holder_count_change_ratio  =  Column(Float, info={"name": "stock_shareholder_count.holder_count_change_ratio", "unit": "units.percent"})  #  户数变动  (%)

  avg_hold_shares  =  Column(Float, info={"name": "stock_shareholder_count.avg_hold_shares", "unit": "units.shares"})  #  户均持股数
  avg_hold_shares_prev  =  Column(Float, info={"name": "stock_shareholder_count.avg_hold_shares_prev", "unit": "units.shares"})  #  上期户均持股数
  avg_hold_shares_change_ratio  =  Column(Float, info={"name": "stock_shareholder_count.avg_hold_shares_change_ratio", "unit": "units.percent"})  #  户均持股变动  (%)
  avg_hold_value  =  Column(Float, info={"name": "stock_shareholder_count.avg_hold_value", "unit": "units.cny"})  #  户均持股市值

  total_mv  =  Column(Float, info={"name": "stock_shareholder_count.total_mv", "unit": "units.cny"})  #  总市值
  total_share  =  Column(Float, info={"name": "stock_shareholder_count.total_share", "unit": "units.shares"})  #  总股本
  share_change  =  Column(Float, info={"name": "stock_shareholder_count.share_change", "unit": "units.shares"})  #  股本变动
  share_change_reason  =  Column(String(255))  #  股本变动原因

  price_at_end  =  Column(Float, info={"name": "stock_shareholder_count.price_at_end", "unit": "units.cny"})  #  截止日收盘价
  price_change_ratio  =  Column(Float, info={"name": "stock_shareholder_count.price_change_ratio", "unit": "units.percent"})  #  区间涨跌幅  (%)

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'end_date',  name='idx_shareholder_stock_date_unique'),
  {"schema": "data"}
  )


class  StockPledge(Base):
  """股权质押风险监控"""
  __tablename__  =  "stock_pledge_risk"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  pledgor_name  =  Column(String(200))  #  出质人  (大股东名称)
  pledgee_name  =  Column(String(200))  #  质权人  (银行/证券公司)

  pledge_shares  =  Column(BigInteger, info={"name": "stock_pledge_risk.pledge_shares", "unit": "units.shares"})  #  质押股数
  pledge_ratio_to_total  =  Column(Float, info={"name": "stock_pledge_risk.pledge_ratio_to_total", "unit": "units.percent"})  #  占总股本比  (%)
  pledge_ratio_to_holder  =  Column(Float, info={"name": "stock_pledge_risk.pledge_ratio_to_holder", "unit": "units.percent"})  #  占其持股比  (%)

  pledge_date  =  Column(Date)  #  质押起始日
  ann_date  =  Column(Date)  #  公告日期
  release_date  =  Column(Date)  #  质押解除日  (如有)

  pledge_price  =  Column(Float, info={"name": "stock_pledge_risk.pledge_price", "unit": "units.cny"})  #  质押当日股价
  current_price  =  Column(Float, info={"name": "stock_pledge_risk.current_price", "unit": "units.cny"})  #  最新价  (接口返回)
  liquidate_price  =  Column(Float, info={"name": "stock_pledge_risk.liquidate_price", "unit": "units.cny"})  #  平仓线  (估算)

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'pledgor_name',  'pledge_date',  name='idx_pledge_unique'),
  {"schema": "data"}
  )


class StockPledgeSummary(Base):
    """上市公司汇总质押比例 (汇总数据比明细数据更全且采集更快)"""
    __tablename__ = "stock_pledge_summary"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    
    industry = Column(String(100))
    industry_code = Column(String(50))
    
    pledge_ratio = Column(Float, info={"name": "stock_pledge_summary.pledge_ratio", "unit": "units.percent"})  # 质押比例 (%)
    pledge_shares = Column(Float, info={"name": "stock_pledge_summary.pledge_shares", "unit": ["units.ten_thousand", "units.shares"]})  # 质押股数 (万股)
    pledge_market_value = Column(Float, info={"name": "stock_pledge_summary.pledge_market_value", "unit": ["units.ten_thousand", "units.cny"]})  # 质押市值 (万元)
    pledge_count = Column(Integer, info={"name": "stock_pledge_summary.pledge_count", "unit": "units.entries"})  # 质押笔数
    
    unrestricted_pledge_shares = Column(Float, info={"name": "stock_pledge_summary.unrestricted_pledge_shares", "unit": ["units.ten_thousand", "units.shares"]})  # 无限售股质押数 (万股)
    restricted_pledge_shares = Column(Float, info={"name": "stock_pledge_summary.restricted_pledge_shares", "unit": ["units.ten_thousand", "units.shares"]})  # 限售股质押数 (万股)
    total_share = Column(Float, info={"name": "stock_pledge_summary.total_share", "unit": ["units.ten_thousand", "units.shares"]})  # 总股本 (万股)

    
    price_change_1y = Column(Float, info={"name": "stock_pledge_summary.price_change_1y", "unit": "units.percent"})  # 近一年涨跌幅 (%)
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'trade_date', name='idx_pledge_summary_unique'),
        {"schema": "data"}
    )


class  StockInsider(Base):
  """大股东/董监高增减持"""
  __tablename__  =  "stock_insider_trading"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  insider_name  =  Column(String(100))  #  变动人姓名
  relationship  =  Column(String(100))  #  关系  (如:  控股股东,  董事)

  change_type  =  Column(String(20))  #  变动类型  (增持/减持)
  change_shares  =  Column(BigInteger, info={"name": "stock_insider_trading.change_shares", "unit": "units.shares"})  #  变动股数
  change_ratio  =  Column(Float, info={"name": "stock_insider_trading.change_ratio", "unit": "units.percent"})  #  变动占总股本比  (%)
  change_avg_price  =  Column(Float, info={"name": "stock_insider_trading.change_avg_price", "unit": "units.cny"})  #  变动均价

  trade_date  =  Column(Date,  index=True)  #  变动日期
  ann_date  =  Column(Date,  index=True)  #  公告日期

  shares_after_change  =  Column(BigInteger, info={"name": "stock_insider_trading.shares_after_change", "unit": "units.shares"})  #  变动后持股数
  ratio_after_change  =  Column(Float, info={"name": "stock_insider_trading.ratio_after_change", "unit": "units.percent"})  #  变动后持股比  (%)

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'insider_name',  'trade_date',  'ann_date',  name='idx_insider_unique'),
  {"schema": "data"}
  )


class  StockRelease(Base):
  """限售股解禁日程表"""
  __tablename__  =  "stock_lockup_release"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  release_date  =  Column(Date,  nullable=False,  index=True)

  release_shares  =  Column(BigInteger, info={"name": "stock_lockup_release.release_shares", "unit": "units.shares"})  #  解禁数量
  release_market_value  =  Column(Float, info={"name": "stock_lockup_release.release_market_value", "unit": ["units.ten_thousand", "units.cny"]})  #  解禁市值  (万元)
  ratio_to_total  =  Column(Float, info={"name": "stock_lockup_release.ratio_to_total", "unit": "units.percent"})  #  占总股本比  (%)
  ratio_to_float  =  Column(Float, info={"name": "stock_lockup_release.ratio_to_float", "unit": "units.percent"})  #  占流通股比  (%)

  release_type  =  Column(String(100))  #  解禁限售股类型  (如:  首发原股东,  定增)

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'release_date',  name='idx_release_unique'),
  {"schema": "data"}
  )


class  StockMargin(Base):
  """融资融券数据  (两融博弈)"""
  __tablename__  =  "stock_margin_data"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  trade_date  =  Column(Date,  nullable=False,  index=True)

  #  融资数据
  margin_balance  =  Column(Float, info={"name": "stock_margin_data.margin_balance", "unit": "units.cny"})  #  融资余额  (元)
  margin_buy_amount  =  Column(Float, info={"name": "stock_margin_data.margin_buy_amount", "unit": "units.cny"})  #  融资买入额  (元)
  margin_repay_amount  =  Column(Float, info={"name": "stock_margin_data.margin_repay_amount", "unit": "units.cny"})  #  融资偿还额  (元)

  #  融券数据
  short_balance  =  Column(Float, info={"name": "stock_margin_data.short_balance", "unit": "units.cny"})  #  融券余额  (元)
  short_volume  =  Column(Float, info={"name": "stock_margin_data.short_volume", "unit": "units.shares"})  #  融券余量  (股)
  short_sell_volume  =  Column(Float, info={"name": "stock_margin_data.short_sell_volume", "unit": "units.shares"})  #  融券卖出量  (股)
  short_repay_volume  =  Column(Float, info={"name": "stock_margin_data.short_repay_volume", "unit": "units.shares"})  #  融券偿还量  (股)

  #  综合
  margin_short_balance  =  Column(Float, info={"name": "stock_margin_data.margin_short_balance", "unit": "units.cny"})  #  融资融券余额  (元)

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'trade_date',  name='idx_margin_unique'),
  {"schema": "data"}
  )


class  CommonData(Base):
  """通用数据存储表，用于存储无需专用表结构的原始数据或临时数据"""
  __tablename__  =  "common_data"
  __table_args__ = {"schema": "data"}

  api_name  =  Column(String(100),  primary_key=True)
  stock_code  =  Column(String(20),  primary_key=True)
  update_date  =  Column(Date,  primary_key=True)
  data_payload  =  Column(JSONB)
  data_source  =  Column(String(50))  #  e.g.  'tushare'
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

class IndexDaily(Base):
    """大盘指数日线数据"""
    __tablename__ = "index_daily"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    index_code = Column(String(20), nullable=False, index=True)  # e.g. sh000001
    trade_date = Column(Date, nullable=False, index=True)

    open = Column(Float, info={"name": "index_daily.open", "unit": "units.points"})
    high = Column(Float, info={"name": "index_daily.high", "unit": "units.points"})
    low = Column(Float, info={"name": "index_daily.low", "unit": "units.points"})
    close = Column(Float, info={"name": "index_daily.close", "unit": "units.points"})
    volume = Column(Float, info={"name": "index_daily.volume", "unit": "units.lots"})
    amount = Column(Float, info={"name": "index_daily.amount", "unit": ["units.thousand", "units.cny"]})  # 成交额

    change = Column(Float, info={"name": "index_daily.change", "unit": "units.points"})
    pct_chg = Column(Float, info={"name": "index_daily.pct_chg", "unit": "units.percent"})

    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint('index_code', 'trade_date', name='idx_index_daily_unique'),
        {"schema": "data"}
    )


class StockBlockTrade(Base):
    """大宗交易数据"""
    __tablename__ = "stock_block_trade"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    
    price = Column(Float, info={"name": "stock_block_trade.price", "unit": "units.cny"})  # 成交价
    volume = Column(Float, info={"name": "stock_block_trade.volume", "unit": ["units.ten_thousand", "units.shares"]})  # 成交量(万股)
    amount = Column(Float, info={"name": "stock_block_trade.amount", "unit": ["units.ten_thousand", "units.cny"]})  # 成交额(万元)
    premium_rate = Column(Float, info={"name": "stock_block_trade.premium_rate", "unit": "units.percent"})  # 折溢价率
    
    buyer = Column(String(200))        # 买方营业部
    seller = Column(String(200))       # 卖方营业部
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'trade_date', 'buyer', 'seller', 'price', 'volume', name='idx_block_trade_unique'),
        {"schema": "data"}
    )


class SectorMoneyFlow(Base):
    """板块资金流向 (行业/概念)"""
    __tablename__ = "sector_money_flow"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sector_name = Column(String(100), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    
    net_inflow = Column(Float, info={"name": "sector_money_flow.net_inflow", "unit": "units.cny"})  # 净流入(元)
    net_inflow_rate = Column(Float, info={"name": "sector_money_flow.net_inflow_rate", "unit": "units.percent"})  # 净流入率
    
    # 扩展字段 (主力=超大+大)
    main_net_inflow = Column(Float, info={"name": "sector_money_flow.main_net_inflow", "unit": "units.cny"})  # 主力净流入
    
    huge_net_inflow = Column(Float, info={"name": "sector_money_flow.huge_net_inflow", "unit": "units.cny"})  # 超大单
    huge_net_inflow_rate = Column(Float, info={"name": "sector_money_flow.huge_net_inflow_rate", "unit": "units.percent"})  # 超大单占比
    large_net_inflow = Column(Float, info={"name": "sector_money_flow.large_net_inflow", "unit": "units.cny"})  # 大单
    large_net_inflow_rate = Column(Float, info={"name": "sector_money_flow.large_net_inflow_rate", "unit": "units.percent"})  # 大单占比
    medium_net_inflow = Column(Float, info={"name": "sector_money_flow.medium_net_inflow", "unit": "units.cny"})  # 中单
    medium_net_inflow_rate = Column(Float, info={"name": "sector_money_flow.medium_net_inflow_rate", "unit": "units.percent"})  # 中单占比
    small_net_inflow = Column(Float, info={"name": "sector_money_flow.small_net_inflow", "unit": "units.cny"})  # 小单
    small_net_inflow_rate = Column(Float, info={"name": "sector_money_flow.small_net_inflow_rate", "unit": "units.percent"})  # 小单占比
    
    close_price = Column(Float, info={"name": "sector_money_flow.close_price", "unit": "units.points"})  # 板块最新指数
    change_percent = Column(Float, info={"name": "sector_money_flow.change_percent", "unit": "units.percent"})  # 涨跌幅 (部分接口提供)
    
    leading_stock = Column(String(100)) # 领涨股
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('sector_name', 'trade_date', name='idx_sector_flow_unique'),
        {"schema": "data"}
    )


class StockTopHolders(Base):
    """十大股东持仓"""
    __tablename__ = "stock_top_holders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True) # 报告期
    
    holder_name = Column(String(200))  # 股东名称
    holder_type = Column(String(50))   # 股东性质 (个人/机构/基金)
    
    hold_amount = Column(Float, info={"name": "stock_top_holders.hold_amount", "unit": "units.shares"})  # 持股数
    hold_ratio = Column(Float, info={"name": "stock_top_holders.hold_ratio", "unit": "units.percent"})  # 持股比例
    
    change = Column(String(50))        # 变动情况 (新进/增加/减少/不变)
    change_ratio = Column(Float, info={"name": "stock_top_holders.change_ratio", "unit": "units.percent"})  # 变动比例
    
    holder_rank = Column(Integer, info={"name": "stock_top_holders.holder_rank", "unit": "units.rank"})  # 排名 (1-10)
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'report_date', 'holder_name', name='idx_top_holder_unique'),
        {"schema": "data"}
    )


class StockFundHolding(Base):
    """公募基金持仓明细"""
    __tablename__ = "stock_fund_holding"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True) # 报告期
    
    fund_code = Column(String(20))     # 基金代码
    fund_name = Column(String(200))    # 基金名称
    
    hold_amount = Column(Float, info={"name": "stock_fund_holding.hold_amount", "unit": "units.shares"})  # 持股数
    hold_market_value = Column(Float, info={"name": "stock_fund_holding.hold_market_value", "unit": "units.cny"})  # 持股市值
    hold_ratio_stock = Column(Float, info={"name": "stock_fund_holding.hold_ratio_stock", "unit": "units.percent"})  # 占流通股比
    hold_ratio_fund = Column(Float, info={"name": "stock_fund_holding.hold_ratio_fund", "unit": "units.percent"})  # 占净值比
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'report_date', 'fund_code', name='idx_fund_holding_unique'),
        {"schema": "data"}
    )


class FinancialCalendar(Base):
    """
    财报预约披露日历
    Financial Report Disclosure Calendar
    """
    __tablename__ = "financial_calendar"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    report_period = Column(String(20), nullable=False) # e.g. '20231231' or '2023-Year'
    
    first_book_date = Column(Date) # 首次预约
    second_book_date = Column(Date) # 二次预约
    third_book_date = Column(Date) # 三次预约
    actual_date = Column(Date, index=True) # 实际披露日期
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'report_period', name='idx_fin_cal_unique'),
        {"schema": "data"}
    )


class StockSEO(Base):
    """
    定增/增发数据 (Seasoned Equity Offering)
    """
    __tablename__ = "stock_seo"
    __table_args__ = {"schema": "data"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    
    issue_date = Column(Date, index=True) # 增发上市日
    announce_date = Column(Date) # 公告日
    
    issue_price = Column(Float, info={"name": "stock_seo.issue_price", "unit": "units.cny"})  # 发行价格
    issue_volume = Column(Float, info={"name": "stock_seo.issue_volume", "unit": "units.shares"})  # 发行数量
    raise_amount = Column(Float, info={"name": "stock_seo.raise_amount", "unit": "units.cny"})  # 募资总额
    
    issue_object = Column(Text) # 发行对象
    lock_period = Column(String(50)) # 限售期
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)


class StockSentiment(Base):
    """
    个股舆情情感评分 (聚合)
    Stock News Sentiment Score
    """
    __tablename__ = "stock_sentiment"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    
    sentiment_score = Column(Float, info={"name": "stock_sentiment.sentiment_score", "unit": "units.score"})  # -1.0 to 1.0
    confidence = Column(Float, info={"name": "stock_sentiment.confidence", "unit": "units.ratio"})  # 0.0 to 1.0
    
    article_count = Column(Integer, info={"name": "stock_sentiment.article_count", "unit": "units.articles"})  # Number of articles analyzed
    positive_count = Column(Integer, info={"name": "stock_sentiment.positive_count", "unit": "units.articles"})
    negative_count = Column(Integer, info={"name": "stock_sentiment.negative_count", "unit": "units.articles"})
    neutral_count = Column(Integer, info={"name": "stock_sentiment.neutral_count", "unit": "units.articles"})
    
    data_source = Column(String(20), default='calculated') # or 'provider_x'
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'trade_date', name='idx_sentiment_unique'),
        {"schema": "data"}
    )
