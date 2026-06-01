import { apiClient } from './client';

export interface PerformanceSummary {
  snapshot_date: string | null;
  benchmark_code: string;
  available_cash: number | null;
  market_value: number | null;
  position_count: number;
  cumulative_return: number | null;
  benchmark_cumulative_return: number | null;
  excess_return: number | null;
  max_drawdown: number | null;
  total_trades: number;
}

export interface EquityCurveItem {
  snapshot_date: string;
  daily_return: number | null;
  cumulative_return: number | null;
  benchmark_close: number | null;
  benchmark_daily_return: number | null;
  benchmark_cumulative_return: number | null;
  excess_return: number | null;
  max_drawdown: number | null;
}

export interface EquityCurveResponse {
  benchmark_code: string;
  items: EquityCurveItem[];
}

export const performanceApi = {
  getSummary: async () => {
    return apiClient.get<PerformanceSummary>('/performance/summary');
  },

  getEquityCurve: async () => {
    return apiClient.get<EquityCurveResponse>('/performance/equity-curve');
  },
};
