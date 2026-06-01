import { apiClient } from './client';

export interface StockData {
  stock_code: string;
  stock_name: string;
  current_price: number;
  change_percent: number;
  timestamp?: string;
}

export interface KlineData {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
}

export interface AIContextSection {
  status?: string;
  [key: string]: unknown;
}

export interface AIContext {
  metadata: AIContextSection;
  realtime: AIContextSection;
  snapshot: AIContextSection;
  history: AIContextSection;
  signals: AIContextSection;
  events: AIContextSection;
}



export interface DetailedSnapshot {
  id: string;
  stock_code: string;
  created_at: string;
  yesterday_close: number;  // Added at root level
  market_data: {
    current_price: number; // Was trade_price
    open: number;          // Was open_price
    high: number;          // Was high_price
    low: number;           // Was low_price
    // yesterday_close is NOT here in storage.py output, but pre_close might be?
    // storage.py only explicitly puts it at root. 
    // Wait, storage.py line 953 closes market_data. 
    // And line 938: "yesterday_close": realtime.get("pre_close", 0) is at root.
    volume: number;
    turnover: number;
    change?: number;
    change_pct?: number;
  };
  technical_indicators: {
    ma5: number;
    ma10: number;
    ma20: number;
    macd: number | { dif: number; dea: number; macd: number };
    k: number;
    d: number;
    j: number;
    kdj?: { k: number; d: number; j: number };
    rsi_6: number;
  };
  fundamentals: {
    pe_ttm: number;
    pb: number;
    total_market_value: number;
    revenue_growth: number;
    profit_growth: number;
  };
  policy_news: Array<{
    title: string;
    summary: string;
    source: string;
    published_at: string;
    content?: string;
    date?: string;
  }>;
  sentiment_data: {
    score: number;
    sentiment: string;
    summary: string;
  };
  announcements?: Array<Record<string, unknown>>;
}

export type DbRecord = Record<string, unknown>;
export interface DbListResponse<T = DbRecord> {
  total: number;
  items: T[];
}

export const marketApi = {
  getStock: async (code: string) => {
    return apiClient.get<StockData>(`/data/stocks/${code}`);
  },



  getKline: async (code: string) => {
    return apiClient.get<KlineData[]>(`/data/kline/${code}`);
  },

  getStockData: async (code: string) => {
    return apiClient.get<StockData>(`/data/stocks/${code}`);
  },

  getKlineData: async (code: string, freq: string = 'D', limit: number = 100) => {
    return apiClient.get<KlineData[]>(`/data/kline/${code}`, { params: { freq, limit } });
  },

  getDbStocks: async (params: { stock_code?: string; query?: string; skip?: number; limit?: number }) => {
    return apiClient.get<DbListResponse>('/data/db/stocks', { params });
  },

  getDbData: async (type: string, params: { stock_code?: string; skip?: number; limit?: number; date?: string; update_date?: string; sort_by?: string; order?: string }) => {
    return apiClient.get<DbListResponse>(`/data/db/data/${type}`, { params });
  },

  syncDbData: async (stock_code?: string, start_date?: string, end_date?: string) => {
    const params: { stock_code?: string; start_date?: string; end_date?: string } = stock_code ? { stock_code } : {};
    if (start_date) params.start_date = start_date;
    if (end_date) params.end_date = end_date;
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync', null, { params });
  },

  syncDailyDbData: async (stock_code: string, start_date: string, end_date: string, adjust: string = "qfq") => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/daily', null, {
      params: {
        stock_code,
        start_date,
        end_date,
        adjust
      }
    });
  },

  syncIndexDaily: async (index_code: string, start_date: string, end_date: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/index-daily', null, {
      params: {
        index_code,
        start_date,
        end_date
      }
    });
  },

  getDataSources: async () => {
    return apiClient.get<{
      status: string;
      sources: string[];
      default_source: string;
      priority_order: string[];
    }>('/sources/');
  },

  setDefaultDataSource: async (source_name: string) => {
    return apiClient.post<{
      status: string;
      message: string;
      default_source: string;
    }>('/sources/default', null, { params: { source_name } });
  },

  syncStockBasic: async (stockCode?: string, resume: boolean = false) => {
    const params: { stock_code?: string; resume?: boolean } = {};
    if (stockCode) params.stock_code = stockCode;
    if (resume) params.resume = true;

    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/stock-basic', null, { params });
  },

  syncDragonTiger: async (startDate: string, endDate?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/dragon-tiger', null, {
      params: {
        start_date: startDate,
        end_date: endDate
      }
    });
  },

  syncNorthboundData: async (stockCode?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/northbound', null, { params: stockCode ? { stock_code: stockCode } : {} });
  },

  getDbStockDetail: async (stockCode: string) => {
    return apiClient.get<DetailedSnapshot>(`/data/db/stock_detail/${stockCode}`);
  },

  getAIContext: async (stockCode: string) => {
    return apiClient.get<AIContext>(`/data/ai-context/${stockCode}`);
  },

  // Real-time market data APIs
  getRealtimeMarket: async (params?: {
    skip?: number;
    limit?: number;
    stock_code?: string;
    sort_by?: string;
    order?: 'asc' | 'desc';
  }) => {
    const res = await apiClient.get<{ total: number; items: RealtimeMarketData[] }>('/data/market/realtime', { params });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_realtime_market')) };
  },

  getStockValuation: async (stockCode: string, params?: { skip?: number; limit?: number }) => {
    const res = await apiClient.get<{ stock_code: string; total: number; items: ValuationHistoryData[] }>(
      `/data/market/valuation/${stockCode}`,
      { params }
    );
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_valuation_history')) };
  },

  syncRealtimeMarket: async (stockCode: string) => {
    return apiClient.post<{ success: boolean; message: string; task_id: string }>(`/data/market/sync/realtime/${stockCode}`);
  },

  syncStockValuation: async (stockCode?: string, startDate?: string, endDate?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/valuation', null, {
      params: {
        stock_code: stockCode,
        start_date: startDate,
        end_date: endDate
      }
    });
  },

  // Industry market data APIs
  getIndustryMarket: async (params?: {
    skip?: number;
    limit?: number;
    sort_by?: string;
    order?: 'asc' | 'desc';
  }) => {
    return apiClient.get<{ total: number; items: IndustryMarketData[] }>('/data/market/industry', { params });
  },

  syncIndustryMarket: async () => {
    return apiClient.post<{ success: boolean; message: string; task_id?: string }>('/data/market/sync/industry');
  },

  syncSectorMoneyFlow: async (stockCode: string) => {
    return apiClient.post<{ success: boolean; message: string; task_id?: string }>('/data/market/sync/sector-money-flow', null, {
      params: { stock_code: stockCode }
    });
  },

  syncFinancialData: async (stockCode: string, startDate: string, endDate: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/financial', null, {
      params: {
        stock_code: stockCode,
        start_date: startDate,
        end_date: endDate,
      }
    });
  },

  syncIncomeStatementData: async (stockCode: string, startDate: string, endDate: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/income-statement', null, {
      params: {
        stock_code: stockCode,
        start_date: startDate,
        end_date: endDate,
      }
    });
  },

  syncBalanceSheetData: async (stockCode: string, startDate: string, endDate: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/balance-sheet', null, {
      params: {
        stock_code: stockCode,
        start_date: startDate,
        end_date: endDate,
      }
    });
  },

  syncCashflowStatementData: async (stockCode: string, startDate: string, endDate: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/cashflow-statement', null, {
      params: {
        stock_code: stockCode,
        start_date: startDate,
        end_date: endDate,
      }
    });
  },

  syncLimitUpPool: async (date?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/limit-up-pool', null, { params: { date } });
  },

  syncLimitDownPool: async (date?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/limit-down-pool', null, { params: { date } });
  },

  syncZhabanPool: async (date?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/zhaban-pool', null, { params: { date } });
  },

  syncBulkData: async (tables: string[], startDate: string, endDate: string, stockCodes?: string, stockScope?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/bulk', {
      tables,
      start_date: startDate,
      end_date: endDate,
      stock_codes: stockCodes,
      stock_scope: stockScope || 'warehouse'
    });
  },

  syncGranularData: async (stockCode: string, dataType: string, startDate?: string, endDate?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>(`/data/db/sync/granular/${dataType}`, null, {
      params: {
        stock_code: stockCode,
        start_date: startDate,
        end_date: endDate
      }
    });
  },

  deleteStockData: async (stockCode: string) => {
    return apiClient.delete<{ message: string; deleted_counts: Record<string, number> }>(`/data/db/stock/${stockCode}`);
  },

  // Specific getters for new A-share dimensions with prefix stripping
  getMoneyFlow: async (stockCode: string, limit: number = 20) => {
    const res = await marketApi.getDbData('stock_money_flow', { stock_code: stockCode, limit });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_money_flow')) };
  },

  getShareholders: async (stockCode: string, limit: number = 20) => {
    const res = await marketApi.getDbData('stock_shareholder_count', { stock_code: stockCode, limit });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_shareholder_count')) };
  },

  getLimitUpPool: async (params: { skip?: number; limit?: number } = {}) => {
    const res = await marketApi.getDbData('stock_limit_up_pool', params);
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_limit_up_pool')) };
  },

  getLimitDownPool: async (params: { skip?: number; limit?: number } = {}) => {
    const res = await marketApi.getDbData('stock_limit_down_pool', params);
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_limit_down_pool')) };
  },

  getPledgeRisk: async (stockCode: string) => {
    const res = await marketApi.getDbData('stock_pledge_risk', { stock_code: stockCode });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_pledge_risk')) };
  },

  getInsiderTrading: async (stockCode: string) => {
    const res = await marketApi.getDbData('stock_insider_trading', { stock_code: stockCode });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_insider_trading')) };
  },

  getLockupRelease: async (stockCode: string) => {
    const res = await marketApi.getDbData('stock_lockup_release', { stock_code: stockCode });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_lockup_release')) };
  },

  getEarningsForecast: async (stockCode: string) => {
    const res = await marketApi.getDbData('stock_earnings_forecast', { stock_code: stockCode });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_earnings_forecast')) };
  },

  getMarginData: async (stockCode: string) => {
    const res = await marketApi.getDbData('stock_margin_data', { stock_code: stockCode });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_margin_data')) };
  },

  getDbTables: async () => {
    return apiClient.get<string[]>('/data/db/tables');
  },

  clearDbTable: async (tableName: string, confirmation: string) => {
    return apiClient.post<{ status: string; message: string }>('/data/db/clear', {
      table_name: tableName,
      confirmation_text: confirmation
    });
  },

  syncIndicators: async (stockCode?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/calculate/indicators', null, { params: stockCode ? { stock_code: stockCode } : {} });
  },

  syncInteractiveQA: async (stockCode: string, startDate?: string, endDate?: string) => {
    const params: { stock_code: string; start_date?: string; end_date?: string } = { stock_code: stockCode };
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;

    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/interactive-qa', null, { params });
  },
  syncPledgeSummary: async (stockCode?: string) => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string;
    }>(`/data/db/sync/pledge-summary${stockCode ? `?stock_code=${stockCode}` : ''}`);
  },

  // 获取十大股东数据 | Get top 10 shareholders data
  getTopHolders: async (stockCode: string, limit: number = 20) => {
    const res = await marketApi.getDbData('stock_top_holders', { stock_code: stockCode, limit });
    return { ...res, items: res.items.map(item => stripPrefix(item, 'stock_top_holders')) };
  },

  // 同步十大股东数据 | Sync top 10 shareholders data
  syncTopHolders: async (stockCode: string) => {
    const response = await apiClient.post<{ message: string }>('/data/db/sync/top_holders', null, {
      params: { stock_code: stockCode }
    });
    return response;
  },

  // 一键全量同步股票基础信息 | One-click full base information sync
  syncBaseInfo: async (stockCode?: string, resume: boolean = false, scope: 'all' | 'warehouse' | 'core' = 'all') => {
    return apiClient.post<{
      task_id: string;
      task_name: string;
      status: string;
      message: string
    }>('/data/db/sync/base-info', null, {
      params: {
        stock_code: stockCode,
        resume: resume,
        scope: scope
      }
    });
  },
};

// Helper to strip table prefix from keys
const stripPrefix = (item: object, prefix: string): DbRecord => {
  const newItem: DbRecord = {};
  const prefixDot = prefix + '.';
  Object.entries(item as Record<string, unknown>).forEach(([key, value]) => {
    if (key.startsWith(prefixDot)) {
      newItem[key.substring(prefixDot.length)] = value;
    } else {
      newItem[key] = value;
    }
  });
  return newItem;
};

// Top 10 shareholders interface
export interface TopHoldersData {
  id: string;
  stock_code: string;
  report_date: string;
  holder_name: string;
  holder_type?: string;
  hold_amount?: number;
  hold_ratio?: number;
  change?: string;
  change_ratio?: number;
  holder_rank?: number;
}

// Real-time market data interface
export interface RealtimeMarketData {
  id: string;
  stock_code: string;
  stock_name: string;
  current_price?: number;
  change_percent?: number;
  change_amount?: number;
  volume?: number;
  turnover?: number;
  amplitude?: number;
  high?: number;
  low?: number;
  open?: number;
  prev_close?: number;
  volume_ratio?: number;
  turnover_rate?: number;
  pe_dynamic?: number;
  pb_ratio?: number;
  total_market_cap?: number;
  circulating_market_cap?: number;
  speed_increase?: number;
  change_5min?: number;
  change_60days?: number;
  change_ytd?: number;
  timestamp?: string;
  main_net_inflow_today?: number;
  main_net_inflow_rank_today?: number;
  super_big_inflow_today?: number;
  big_inflow_today?: number;
  mid_inflow_today?: number;
  small_inflow_today?: number;
  main_net_inflow_5d?: number;
  main_net_inflow_rank_5d?: number;
  super_big_inflow_5d?: number;
  big_inflow_5d?: number;
  mid_inflow_5d?: number;
  small_inflow_5d?: number;
  main_net_inflow_10d?: number;
  main_net_inflow_rank_10d?: number;
  super_big_inflow_10d?: number;
  big_inflow_10d?: number;
  mid_inflow_10d?: number;
  small_inflow_10d?: number;
}

// Stock valuation history interface
export interface ValuationHistoryData {
  id: string;
  stock_code: string;
  data_date: string;
  close_price?: number;
  change_percent?: number;
  total_market_value?: number;
  circulating_market_value?: number;
  total_share?: number;
  circulating_share?: number;
  pe_ttm?: number;
  pe_static?: number;
  pb?: number;
  peg?: number;
  pcf?: number;
  ps?: number;
}

export interface IndustryMarketData {
  id: string;
  rank: number;
  board_name: string;
  board_code: string;
  latest_price?: number;
  change_amount?: number;
  change_percent?: number;
  total_market_cap?: number;
  turnover_rate?: number;
  rising_stocks_count?: number;
  falling_stocks_count?: number;
  leading_stock_name?: string;
  leading_stock_change_percent?: number;
  timestamp?: string;
}
export interface MoneyFlowData {
  id: string;
  stock_code: string;
  trade_date: string;
  net_inflow_small: number;
  net_inflow_medium: number;
  net_inflow_large: number;
  net_inflow_huge: number;
  net_inflow_main: number;
  net_inflow_ratio_main: number;
}

export interface ShareholderData {
  id: string;
  stock_code: string;
  end_date: string;
  holder_count: number;
  holder_count_prev: number;
  holder_count_change: number;
  avg_hold_shares: number;
  price_at_end: number;
  price_change_ratio: number;
}

export interface LimitUpData {
  id: string;
  stock_code: string;
  stock_name: string;
  update_date: string;
  limit_up_price: number;
  pct_chg: number;
  limit_up_days: number;
  limit_up_reason: string;
  first_limit_up_time: string;
  last_limit_up_time: string;
}

export interface PledgeRiskData {
  id: string;
  stock_code: string;
  pledgor_name: string;
  pledgee_name: string;
  pledge_shares: number;
  pledge_ratio_to_total: number;
  pledge_date: string;
  alert_price: number;
  liquidate_price: number;
}

export interface InsiderTradingData {
  id: string;
  stock_code: string;
  insider_name: string;
  relationship: string;
  change_type: string;
  change_shares: number;
  trade_date: string;
  ann_date: string;
}

export interface LockupReleaseData {
  id: string;
  stock_code: string;
  release_date: string;
  release_shares: number;
  release_market_value: number;
  ratio_to_total: number;
  release_type: string;
}

export interface EarningsForecastData {
  id: string;
  stock_code: string;
  report_date: string;
  ann_date: string;
  forecast_type: string;
  growth_min: number;
  growth_max: number;
  forecast_content: string;
}

export interface MarginData {
  id: string;
  stock_code: string;
  trade_date: string;
  margin_balance: number;
  margin_buy_amount: number;
  short_balance: number;
  margin_short_balance: number;
}

export interface RegulatoryData {
  id: string;
  stock_code: string;
  announcement_date: string;
  type: string;
  title: string;
  pdf_link: string;
  content?: string;
  short_content?: string;
  parse_status?: string;
  parse_error?: string;
  content_parsed_at?: string;
}

export interface HotRankData {
  id: string;
  stock_code: string;
  name: string;
  rank: number;
  change: string;
  hot_value?: number;
  update_date: string;
}
