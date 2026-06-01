import { apiClient } from './client';

export interface AccountAssets {
    id: string;
    user_id: string;
    cash_balance: number;
    market_value: number;
    total_assets: number;
    frozen_cash: number;
    total_profit_loss: number;
    floating_pnl: number;
    starting_capital: number;
    profit_loss_pct: number;
    total_trades: number;
    win_rate: number;
    created_at: string;
    updated_at: string;
}

export interface Position {
    id: string;
    session_id: string | null;
    stock_code: string;
    stock_name: string;
    current_shares: number;
    available_shares: number;
    frozen_shares: number;
    avg_cost: number;
    current_price: number;
    stop_loss?: number | null;
    market_value: number;
    unrealized_pnl: number;
    created_at: string;
    updated_at: string;
}

export const accountsApi = {
    getMyAssets: async () => {
        return apiClient.get<AccountAssets>('/accounts/my-assets');
    },

    getMyPositions: async () => {
        return apiClient.get<Position[]>('/accounts/my-positions');
    },

    resetAccount: async () => {
        return apiClient.post<{ success: boolean; message: string; cash_balance: number }>('/accounts/reset-account');
    }
};
