import { apiClient } from './client';

export interface Order {
  id: string;
  session_id?: string | null;
  stock_code: string;
  action: 'buy' | 'sell';
  price: number;
  shares: number;
  filled_shares: number;
  status: 'pending' | 'filled' | 'rejected' | 'cancelled';
  created_at: string;
}

export interface Position {
  id: string;
  session_id?: string | null;
  stock_code: string;
  stock_name: string;
  current_shares: number;
  available_shares: number;
  avg_cost: number;
  market_value: number;
  unrealized_pnl: number;
}

export interface AccountAssets {
  cash_balance: number;
  market_value: number;
  total_assets: number;
}

export interface Trade {
  id: string;
  order_id: string;
  session_id?: string | null;
  stock_code: string;
  action: 'buy' | 'sell';
  price: number;
  shares: number;
  amount: number;
  commission: number;
  created_at: string;
}

export const tradeApi = {
  placeOrder: (data: {
    session_id?: string;
    stock_code: string;
    stock_name: string;
    action: 'buy' | 'sell';
    order_type: string;
    price: number;
    shares: number;
    stop_loss?: number;
  }) => apiClient.post('/trading/orders', data),

  cancelOrder: (orderId: string) =>
    apiClient.post(`/trading/orders/${orderId}/cancel`),

  getOrders: (stockCode: string, status?: string) =>
    apiClient.get<Order[]>(`/trading/my-orders`, { params: { stock_code: stockCode, status } }),

  getPositions: (stockCode: string) =>
    apiClient.get<Position[]>(`/accounts/my-positions`, { params: { stock_code: stockCode } }),

  getTrades: (stockCode: string) =>
    apiClient.get<Trade[]>(`/trading/my-trades`, { params: { stock_code: stockCode } }),

  executeDecision: (decisionId: string) =>
    apiClient.post(`/trading/execute/${decisionId}`),

  updateOrder: async (order_id: string, data: { price?: number; shares?: number }) => {
    return apiClient.put<Order>(`/trading/orders/${order_id}`, data);
  },
  // 获取账户资产信息 (新的基于用户的端点)
  getAccountAssets: async (_sessionId?: string) => {
    // try new independent endpoint
    try {
      return apiClient.get<AccountAssets>('/accounts/my-assets');
    } catch (e) {
      console.error("Failed to fetch my-assets, using session fallback", e);
      if (_sessionId) {
        return apiClient.get<AccountAssets>(`/accounts/assets/${_sessionId}`);
      }
      throw e;
    }
  },

  // 设置总资金 (新的基于用户的端点)
  setTotalFunds: async (total_funds: number, _sessionId?: string) => {
    try {
      return apiClient.put<AccountAssets>('/accounts/my-total-funds', { total_funds });
    } catch (e) {
      console.error("Failed to set my-total-funds, using session fallback", e);
      if (_sessionId) {
        return apiClient.put<AccountAssets>(`/accounts/total-funds/${_sessionId}`, { total_funds });
      }
      throw e;
    }
  },
};
