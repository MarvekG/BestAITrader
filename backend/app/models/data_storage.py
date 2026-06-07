
from  sqlalchemy  import  (
  Column,  String,  Float,  Date,  DateTime,  UniqueConstraint,  BigInteger,
  Integer,  Text,  ForeignKey, Index
)
from  sqlalchemy.dialects.postgresql  import  UUID,  JSONB
import  uuid
from  datetime  import  datetime
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
  total_share  =  Column(Float)
  float_share  =  Column(Float)
  status  =  Column(String(20),  default='L')  #  L:  Listed,  D:  Delisted
  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)


class  FinancialIndicator(Base):
  """Refactored  Financial  Indicator  model  using  JSONB  for  flexibility"""
  __tablename__  =  "financial_indicator"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey("data.stock_basic.stock_code",  ondelete="CASCADE"),  nullable=False,  index=True)
  announcement_date  =  Column(Date,  index=True)  #  公告日期
  report_date  =  Column(Date,  nullable=False,  index=True)  #  报告期

  #  Generic  JSON  column  to  store  all  indicators  (mapped  to  JSONB  in  Postgres)
  data  =  Column(JSONB,  nullable=False)
  update_date  =  Column(Date,  default=datetime.now().date)  #  记录更新日期

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint(
  'stock_code',  'report_date',  'announcement_date',
  name='idx_financial_stock_date_ann_unique'
  ),
  {"schema": "data"}
  )



class StockIncomeStatement(Base):
  """Stock income statement data stored as JSONB for flexible provider coverage"""
  __tablename__ = "stock_income_statement"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  stock_code = Column(String(20), ForeignKey("data.stock_basic.stock_code", ondelete="CASCADE"), nullable=False, index=True)
  announcement_date = Column(Date, index=True)
  report_date = Column(Date, nullable=False, index=True)
  report_type = Column(String(50))
  currency = Column(String(20))
  is_audit = Column(String(20))

  data = Column(JSONB, nullable=False)
  update_date = Column(Date, default=datetime.now().date)

  data_source = Column(String(20), default='tushare')
  created_at = Column(DateTime, default=datetime.now)
  updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

  __table_args__ = (
      UniqueConstraint(
          'stock_code', 'report_date', 'announcement_date', 'report_type',
          name='idx_income_statement_stock_date_ann_unique'
      ),
      {"schema": "data"}
  )


class StockBalanceSheet(Base):
  """Stock balance sheet data stored as JSONB for flexible provider coverage"""
  __tablename__ = "stock_balance_sheet"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  stock_code = Column(String(20), ForeignKey("data.stock_basic.stock_code", ondelete="CASCADE"), nullable=False, index=True)
  announcement_date = Column(Date, index=True)
  report_date = Column(Date, nullable=False, index=True)
  report_type = Column(String(50))
  currency = Column(String(20))
  is_audit = Column(String(20))

  data = Column(JSONB, nullable=False)
  update_date = Column(Date, default=datetime.now().date)

  data_source = Column(String(20), default='tushare')
  created_at = Column(DateTime, default=datetime.now)
  updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

  __table_args__ = (
      UniqueConstraint(
          'stock_code', 'report_date', 'announcement_date', 'report_type',
          name='idx_balance_sheet_stock_date_ann_unique'
      ),
      {"schema": "data"}
  )


class StockCashflowStatement(Base):
  """Stock cashflow statement data stored as JSONB for flexible provider coverage"""
  __tablename__ = "stock_cashflow_statement"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  stock_code = Column(String(20), ForeignKey("data.stock_basic.stock_code", ondelete="CASCADE"), nullable=False, index=True)
  announcement_date = Column(Date, index=True)
  report_date = Column(Date, nullable=False, index=True)
  report_type = Column(String(50))
  currency = Column(String(20))
  is_audit = Column(String(20))

  data = Column(JSONB, nullable=False)
  update_date = Column(Date, default=datetime.now().date)

  data_source = Column(String(20), default='tushare')
  created_at = Column(DateTime, default=datetime.now)
  updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

  __table_args__ = (
      UniqueConstraint(
          'stock_code', 'report_date', 'announcement_date', 'report_type',
          name='idx_cashflow_statement_stock_date_ann_unique'
      ),
      {"schema": "data"}
  )


class KlineData(Base):
  """Kline  data  for  stocks"""
  __tablename__  =  "kline_data"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  date  =  Column(Date,  nullable=False,  index=True)
  open  =  Column(Float)
  close  =  Column(Float)
  high  =  Column(Float)
  low  =  Column(Float)
  volume  =  Column(Float)
  turnover  =  Column(Float)
  change  =  Column(Float)
  change_percent  =  Column(Float)
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
    
    rank = Column(Integer, nullable=False)        # 当前排名
    rank_change = Column(Integer)                 # 排名变化 (如有)
    hot_value = Column(Float)                     # 热度值 (如有)
    rank_type = Column(String(20), default='hot') # 排名类型: 'hot' (人气榜), 'rising' (飙升榜)
    current_rank = Column(Integer)                # 在人气总榜中的当前排名
    
    # 新增行情与粉丝特征字段
    new_fans = Column(Float)                      # 新晋粉丝占比(%)
    irons_fans = Column(Float)                    # 铁杆粉丝占比(%)
    
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
  hold_shares  =  Column(Float)
  hold_value  =  Column(Float)
  hold_ratio  =  Column(Float)

  #  New  Fields  (2024-02  Update)
  close_price  =  Column(Float)  #  当日收盘价
  change_percent  =  Column(Float)  #  当日涨跌幅
  net_buy_volume  =  Column(Float)  #  今日增持股数
  net_buy_amount  =  Column(Float)  #  今日增持资金
  hold_value_change  =  Column(Float)  #  今日持股市值变化
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
  net_buy_amount  =  Column(Float)
  buy_amount  =  Column(Float)
  sell_amount  =  Column(Float)
  price_change_percent  =  Column(Float)
  listing_reason  =  Column(String(500))

  #  Missing  Columns  added
  sequence_number  =  Column(Integer)
  interpretation  =  Column(Text)
  close_price  =  Column(Float)
  total_trade_amount  =  Column(Float)
  market_total_trade_amount  =  Column(Float)
  net_buy_ratio  =  Column(Float)
  trade_amount_ratio  =  Column(Float)
  turnover_rate  =  Column(Float)
  floating_market_capitalization  =  Column(Float)

  post_1_day_price_change_percent  =  Column(Float)
  post_2_day_price_change_percent  =  Column(Float)
  post_5_day_price_change_percent  =  Column(Float)
  post_10_day_price_change_percent  =  Column(Float)

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
  current_price  =  Column(Float)  #  最新价
  change_percent  =  Column(Float)  #  涨跌幅
  change_amount  =  Column(Float)  #  涨跌额
  volume  =  Column(Float)  #  成交量
  turnover  =  Column(Float)  #  成交额
  amplitude  =  Column(Float)  #  振幅
  high  =  Column(Float)  #  最高
  low  =  Column(Float)  #  最低
  open  =  Column(Float)  #  今开
  prev_close  =  Column(Float)  #  昨收
  volume_ratio  =  Column(Float)  #  量比
  turnover_rate  =  Column(Float)  #  换手率
  pe_dynamic  =  Column(Float)  #  市盈率-动态
  pb_ratio  =  Column(Float)  #  市净率
  total_market_cap  =  Column(Float)  #  总市值
  circulating_market_cap  =  Column(Float)  #  流通市值
  speed_increase  =  Column(Float)  #  涨速
  change_5min  =  Column(Float)  #  5分钟涨跌
  change_60days  =  Column(Float)  #  60日涨跌幅
  change_ytd  =  Column(Float)  #  年初至今涨跌幅

  # 外部增强字段: 资金流与排名
  # 今日 (1日)
  main_net_inflow_today = Column(Float)         # f137
  super_big_inflow_today = Column(Float)        # f140
  big_inflow_today = Column(Float)              # f143
  mid_inflow_today = Column(Float)              # f146
  small_inflow_today = Column(Float)            # f149
  main_net_inflow_rank_today = Column(Integer)  # f469

  # 5日
  main_net_inflow_5d = Column(Float)            # f434
  super_big_inflow_5d = Column(Float)           # f435
  big_inflow_5d = Column(Float)                 # f436
  mid_inflow_5d = Column(Float)                 # f437
  small_inflow_5d = Column(Float)               # f438
  main_net_inflow_rank_5d = Column(Integer)     # f470

  # 10日
  main_net_inflow_10d = Column(Float)           # f459
  super_big_inflow_10d = Column(Float)          # f461
  big_inflow_10d = Column(Float)                # f463
  mid_inflow_10d = Column(Float)                # f465
  small_inflow_10d = Column(Float)              # f467
  main_net_inflow_rank_10d = Column(Integer)    # f471

  timestamp  =  Column(DateTime,  default=datetime.now)
  data_source  =  Column(String(20),  nullable=False,  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)



class  StockValuationHistory(Base):
  """Stock  valuation  history  data  from  stock_value_em  interface"""
  __tablename__  =  "stock_valuation_history"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  data_date  =  Column(Date,  nullable=False,  index=True)  #  数据日期
  close_price  =  Column(Float)  #  当日收盘价  (元)
  change_percent  =  Column(Float)  #  当日涨跌幅  (%)
  total_market_value  =  Column(BigInteger)  #  总市值  (元)
  circulating_market_value  =  Column(BigInteger)  #  流通市值  (元)
  pe_ttm  =  Column(Float)  #  市盈率  (TTM)
  pe_static  =  Column(Float)  #  市盈率  (静)
  pb  =  Column(Float)  #  市净率
  ps_ttm  =  Column(Float)  #  市销率  (TTM)
  ps_static  =  Column(Float)  #  市销率  (静)
  peg  =  Column(Float)  #  PEG
  dividend_yield  =  Column(Float)  #  股息率  (%)
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
  rank  =  Column(Integer)
  latest_price  =  Column(Float)
  change_amount  =  Column(Float)
  change_percent  =  Column(Float)
  total_market_cap  =  Column(Float)
  turnover_rate  =  Column(Float)
  rising_stocks_count  =  Column(Integer)
  falling_stocks_count  =  Column(Integer)
  leading_stock_name  =  Column(String(100))
  leading_stock_change_percent  =  Column(Float)
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
  limit_up_price  =  Column(Float)
  pct_chg  =  Column(Float)
  turnover  =  Column(Float)
  circ_mv  =  Column(Float)
  total_mv  =  Column(Float)

  #  情绪指标
  first_limit_up_time  =  Column(String(20))  #  首次封板时间
  last_limit_up_time  =  Column(String(20))  #  最后封板时间
  limit_up_type  =  Column(String(100))  #  涨停形态  (e.g.  一字板,  T字板)
  limit_up_days  =  Column(String(50),  default="1")  #  连板天数
  limit_up_stats  =  Column(String(50))  #  涨停统计  (x/y)
  limit_up_reason  =  Column(Text)  #  涨停原因  (所属题材)

  #  补充字段
  turnover_rate  =  Column(Float)  #  换手率
  fund_amount  =  Column(Float)  #  封板资金
  open_times  =  Column(Integer)  #  炸板次数

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
  latest_price  =  Column(Float)  #  最新价
  limit_up_price  =  Column(Float)  #  涨停价
  pct_chg  =  Column(Float)  #  涨跌幅
  turnover  =  Column(Float)  #  成交额
  circ_mv  =  Column(Float)  #  流通市值
  total_mv  =  Column(Float)  #  总市值

  #  情绪指标
  first_limit_up_time  =  Column(String(20))  #  首次封板时间
  last_limit_up_time  =  Column(String(20))  #  炸板时间  (Last  limit  up  or  break  time)
  limit_up_type  =  Column(String(100))  #  涨停形态  (Optional)

  #  补充字段
  turnover_rate  =  Column(Float)  #  换手率
  swing  =  Column(Float)  #  振幅
  open_times  =  Column(Integer)  #  炸板次数
  limit_up_stats  =  Column(String(50))  #  涨停统计  (x/y)
  limit_up_reason  =  Column(Text)  #  所属行业/涨停原因
  speed_increase  =  Column(Float)  #  涨速

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
  limit_down_price  =  Column(Float)
  pct_chg  =  Column(Float)
  turnover  =  Column(Float)
  circ_mv  =  Column(Float)
  total_mv  =  Column(Float)

  #  情绪指标
  first_limit_down_time  =  Column(String(20))  #  首次封板时间
  last_limit_down_time  =  Column(String(20))  #  最后封板时间
  limit_down_type  =  Column(String(100))  #  跌停形态
  limit_down_days  =  Column(String(50),  default="1")  #  连板天数
  limit_down_stats  =  Column(String(50))  #  跌停统计
  limit_down_reason  =  Column(Text)  #  跌停原因

  #  补充字段
  turnover_rate  =  Column(Float)  #  换手率
  fund_amount  =  Column(Float)  #  封单资金
  open_times  =  Column(Integer)  #  炸板次数
  board_turnover  =  Column(Float)  #  板上成交额
  dynamic_pe  =  Column(Float)  #  动态市盈率

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
  net_inflow_small  =  Column(Float)  #  小单净流入
  net_inflow_medium  =  Column(Float)  #  中单净流入
  net_inflow_large  =  Column(Float)  #  大单净流入
  net_inflow_huge  =  Column(Float)  #  特大单净流入
  net_inflow_main  =  Column(Float)  #  主力净流入  (大单+特大单)


  #  占比  (%)
  net_inflow_ratio_main  =  Column(Float)

  #  扩展字段
  close_price  =  Column(Float)  #  收盘价
  change_pct  =  Column(Float)  #  涨跌幅

  net_inflow_ratio_huge  =  Column(Float)  #  超大单净流入占比
  net_inflow_ratio_large  =  Column(Float)  #  大单净流入占比
  net_inflow_ratio_medium  =  Column(Float)  #  中单净流入占比
  net_inflow_ratio_small  =  Column(Float)  #  小单净流入占比

  #  累计天数流入  (可选存储)
  net_inflow_main_3d  =  Column(Float)
  net_inflow_main_5d  =  Column(Float)
  net_inflow_main_10d  =  Column(Float)

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
  holder_count  =  Column(BigInteger)  #  股东户数
  holder_count_prev  =  Column(BigInteger)  #  上期股东户数
  holder_count_change  =  Column(Float)  #  户数变动  (绝对值)
  holder_count_change_ratio  =  Column(Float)  #  户数变动  (%)

  avg_hold_shares  =  Column(Float)  #  户均持股数
  avg_hold_shares_prev  =  Column(Float)  #  上期户均持股数
  avg_hold_shares_change_ratio  =  Column(Float)  #  户均持股变动  (%)
  avg_hold_value  =  Column(Float)  #  户均持股市值

  total_mv  =  Column(Float)  #  总市值
  total_share  =  Column(Float)  #  总股本
  share_change  =  Column(Float)  #  股本变动
  share_change_reason  =  Column(String(255))  #  股本变动原因

  price_at_end  =  Column(Float)  #  截止日收盘价
  price_change_ratio  =  Column(Float)  #  区间涨跌幅  (%)

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

  pledge_shares  =  Column(BigInteger)  #  质押股数
  pledge_ratio_to_total  =  Column(Float)  #  占总股本比  (%)
  pledge_ratio_to_holder  =  Column(Float)  #  占其持股比  (%)

  pledge_date  =  Column(Date)  #  质押起始日
  ann_date  =  Column(Date)  #  公告日期
  release_date  =  Column(Date)  #  质押解除日  (如有)

  pledge_price  =  Column(Float)  #  质押当日股价
  current_price  =  Column(Float)  #  最新价  (接口返回)
  liquidate_price  =  Column(Float)  #  平仓线  (估算)

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
    
    pledge_ratio = Column(Float)           # 质押比例 (%)
    pledge_shares = Column(Float)          # 质押股数 (万股)
    pledge_market_value = Column(Float)    # 质押市值 (万元)
    pledge_count = Column(Integer)         # 质押笔数
    
    unrestricted_pledge_shares = Column(Float) # 无限售股质押数 (万股)
    restricted_pledge_shares = Column(Float)   # 限售股质押数 (万股)
    total_share = Column(Float)                # 总股本 (万股)

    
    price_change_1y = Column(Float)        # 近一年涨跌幅 (%)
    
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
  change_shares  =  Column(BigInteger)  #  变动股数
  change_ratio  =  Column(Float)  #  变动占总股本比  (%)
  change_avg_price  =  Column(Float)  #  变动均价

  trade_date  =  Column(Date,  index=True)  #  变动日期
  ann_date  =  Column(Date,  index=True)  #  公告日期

  shares_after_change  =  Column(BigInteger)  #  变动后持股数
  ratio_after_change  =  Column(Float)  #  变动后持股比  (%)

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

  release_shares  =  Column(BigInteger)  #  解禁数量
  release_market_value  =  Column(Float)  #  解禁市值  (万元)
  ratio_to_total  =  Column(Float)  #  占总股本比  (%)
  ratio_to_float  =  Column(Float)  #  占流通股比  (%)

  release_type  =  Column(String(100))  #  解禁限售股类型  (如:  首发原股东,  定增)

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'release_date',  name='idx_release_unique'),
  {"schema": "data"}
  )


class  StockForecast(Base):
  """业绩预告/快报数据"""
  __tablename__  =  "stock_earnings_forecast"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  report_date  =  Column(Date,  nullable=False,  index=True)  #  报告期末
  ann_date  =  Column(Date)  #  公告日

  forecast_type  =  Column(String(50))  #  预告类型  (如:  略增,  预增,  亏损)
  net_profit_min  =  Column(Float)  #  净利润下限  (万元)
  net_profit_max  =  Column(Float)  #  净利润上限  (万元)
  prev_year_profit  =  Column(Float)  #  上年同期净利润

  growth_min  =  Column(Float)  #  增长下限  (%)
  growth_max  =  Column(Float)  #  增长上限  (%)
  forecast_content  =  Column(Text)  #  业绩变动原因说明

  data_source  =  Column(String(20),  default='tushare')
  created_at  =  Column(DateTime,  default=datetime.now)
  updated_at  =  Column(DateTime,  default=datetime.now,  onupdate=datetime.now)

  __table_args__  =  (
  UniqueConstraint('stock_code',  'report_date',  'ann_date',  name='idx_forecast_unique'),
  {"schema": "data"}
  )


class  StockMargin(Base):
  """融资融券数据  (两融博弈)"""
  __tablename__  =  "stock_margin_data"

  id  =  Column(UUID(as_uuid=True),  primary_key=True,  default=uuid.uuid4)
  stock_code  =  Column(String(20),  ForeignKey('data.stock_basic.stock_code',  ondelete='CASCADE'),  nullable=False,  index=True)
  trade_date  =  Column(Date,  nullable=False,  index=True)

  #  融资数据
  margin_balance  =  Column(Float)  #  融资余额  (元)
  margin_buy_amount  =  Column(Float)  #  融资买入额  (元)
  margin_repay_amount  =  Column(Float)  #  融资偿还额  (元)

  #  融券数据
  short_balance  =  Column(Float)  #  融券余额  (元)
  short_volume  =  Column(Float)  #  融券余量  (股)
  short_sell_volume  =  Column(Float)  #  融券卖出量  (股)
  short_repay_volume  =  Column(Float)  #  融券偿还量  (股)

  #  综合
  margin_short_balance  =  Column(Float)  #  融资融券余额  (元)

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

    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)  # 成交额

    change = Column(Float)
    pct_chg = Column(Float)

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
    
    price = Column(Float)              # 成交价
    volume = Column(Float)             # 成交量(万股)
    amount = Column(Float)             # 成交额(万元)
    premium_rate = Column(Float)       # 折溢价率
    
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
    
    net_inflow = Column(Float)         # 净流入(万元/亿元)
    net_inflow_rate = Column(Float)    # 净流入率
    
    # 扩展字段 (主力=超大+大)
    main_net_inflow = Column(Float) # 主力净流入
    
    huge_net_inflow = Column(Float) # 超大单
    huge_net_inflow_rate = Column(Float) # 超大单占比
    large_net_inflow = Column(Float) # 大单
    large_net_inflow_rate = Column(Float) # 大单占比
    medium_net_inflow = Column(Float) # 中单
    medium_net_inflow_rate = Column(Float) # 中单占比
    small_net_inflow = Column(Float) # 小单
    small_net_inflow_rate = Column(Float) # 小单占比
    
    close_price = Column(Float) # 收盘价 (部分接口提供)
    change_percent = Column(Float) # 涨跌幅 (部分接口提供)
    
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
    
    hold_amount = Column(Float)        # 持股数
    hold_ratio = Column(Float)         # 持股比例
    
    change = Column(String(50))        # 变动情况 (新进/增加/减少/不变)
    change_ratio = Column(Float)       # 变动比例
    
    holder_rank = Column(Integer)      # 排名 (1-10)
    
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
    
    hold_amount = Column(Float)        # 持股数
    hold_market_value = Column(Float)  # 持股市值
    hold_ratio_stock = Column(Float)   # 占流通股比
    hold_ratio_fund = Column(Float)    # 占净值比
    
    data_source = Column(String(20), default='tushare')
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'report_date', 'fund_code', name='idx_fund_holding_unique'),
        {"schema": "data"}
    )


class StockInteractiveQA(Base):
    """互动问答记录"""
    __tablename__ = "stock_interactive_qa"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)

    question_id = Column(String(100))
    answer_id = Column(String(100))
    question = Column(Text, nullable=False)
    answer = Column(Text)

    answerer = Column(String(500))

    question_time = Column(DateTime, index=True)
    answer_time = Column(DateTime, index=True)
    trade_date = Column(Date, index=True)

    content_hash = Column(String(64))

    data_source = Column(String(50), default='tushare')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index('idx_interactive_qa_query', 'stock_code', 'answer_time'),
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
    
    issue_price = Column(Float) # 发行价格
    issue_volume = Column(Float) # 发行数量
    raise_amount = Column(Float) # 募资总额
    
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
    
    sentiment_score = Column(Float) # -1.0 to 1.0
    confidence = Column(Float) # 0.0 to 1.0
    
    article_count = Column(Integer) # Number of articles analyzed
    positive_count = Column(Integer)
    negative_count = Column(Integer)
    neutral_count = Column(Integer)
    
    data_source = Column(String(20), default='calculated') # or 'provider_x'
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'trade_date', name='idx_sentiment_unique'),
        {"schema": "data"}
    )
