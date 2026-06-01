import { apiClient } from './client';

export interface OrderRequest {
    session_id?: string;
    stock_code: string;
    stock_name: string;
    action: 'buy' | 'sell';
    order_type: 'limit' | 'market';
    price: number;
    shares: number;
    stop_loss?: number;
}

export interface TradeRecord {
    id: string;
    order_id: string;
    session_id: string | null;
    stock_code: string;
    stock_name: string;
    action: 'buy' | 'sell';
    price: number;
    shares: number;
    turnover: number;
    commission: number;
    stamp_duty: number;
    transfer_fee: number;
    total_fee: number;
    created_at: string;
}

export interface OrderHistory {
    id: string;
    session_id: string | null;
    stock_code: string;
    stock_name: string;
    action: 'buy' | 'sell';
    order_type: 'limit' | 'market';
    price: number;
    shares: number;
    filled_shares: number;
    avg_fill_price: number | null;
    realized_pnl?: number;
    status: 'pending' | 'filled' | 'cancelled' | 'rejected';
    remark?: string | null;
    source?: string | null;
    created_at: string;
    updated_at: string;
}

export const tradingApi = {
    placeOrder: async (data: OrderRequest) => {
        return apiClient.post<{ success: boolean; message?: string }>('/trading/orders', data);
    },

    cancelOrder: async (orderId: string) => {
        return apiClient.post<{ success: boolean; message?: string }>(`/trading/orders/${orderId}/cancel`);
    },

    getMyOrders: async (skip: number = 0, limit: number = 100) => {
        return apiClient.get<OrderHistory[]>('/trading/my-orders', { params: { skip, limit } });
    }
};
