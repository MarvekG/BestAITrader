import { apiClient } from './client';

export interface StockWarehouse {
  id: number;
  stock_code: string;
  stock_name: string;
  industry: string;
  market: string;
  added_at: string;
  created_at?: string;
  is_active: boolean;
  is_default: boolean;
  auto_analysis_enabled: boolean;
  auto_analysis_frequency: 'daily' | 'weekly' | 'monthly';
  auto_analysis_time: string;
  auto_analysis_trading_frequency: string;
  auto_analysis_trading_strategy: string;
  auto_analysis_run_immediately: boolean;
  last_auto_analysis_at?: string | null;
  last_auto_analysis_session_id?: string | null;
  last_auto_analysis_task_id?: string | null;
  last_auto_analysis_error?: string | null;
}

export type StockInfo = StockWarehouse;

export interface StockWarehouseUpdate {
  is_active?: boolean;
  is_default?: boolean;
  auto_analysis_enabled?: boolean;
  auto_analysis_frequency?: 'daily' | 'weekly' | 'monthly';
  auto_analysis_time?: string;
  auto_analysis_trading_frequency?: string;
  auto_analysis_trading_strategy?: string;
  auto_analysis_run_immediately?: boolean;
}

export const warehouseApi = {
  list: async () => {
    return apiClient.get<StockInfo[]>('/stock-warehouse/');
  },

  getStocks: async () => {
    return apiClient.get<StockWarehouse[]>('/stock-warehouse/');
  },

  getStock: async (stockCode: string) => {
    return apiClient.get<StockWarehouse>(`/stock-warehouse/${stockCode}`);
  },

  update: async (stockCode: string, data: StockWarehouseUpdate) => {
    return apiClient.put<StockWarehouse>(`/stock-warehouse/${stockCode}`, data);
  },

  add: async (data: { stock_code: string; stock_name?: string; industry?: string }) => {
    return apiClient.post<StockWarehouse>('/stock-warehouse/', data);
  },

  addStock: async (data: { stock_code: string; stock_name: string; industry?: string }) => {
    return apiClient.post<StockWarehouse>('/stock-warehouse/', data);
  },

  delete: async (stockCode: string) => {
    return apiClient.delete(`/stock-warehouse/${stockCode}`);
  },

  deleteStock: async (stockCode: string) => {
    return apiClient.delete(`/stock-warehouse/${stockCode}`);
  },

  initShanghai50: async () => {
    return apiClient.post('/stock-warehouse/init-shanghai50');
  },

};
