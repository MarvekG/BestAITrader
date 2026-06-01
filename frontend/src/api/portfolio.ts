import { apiClient } from './client';

export interface PortfolioSummary {
  total_assets: number;
  available_cash: number;
  frozen_cash: number;
  market_value: number;
  cash_ratio: number;
  position_ratio: number;
  position_count: number;
}

export interface PortfolioPosition {
  stock_code: string;
  stock_name: string;
  industry: string;
  total_shares: number;
  available_shares: number;
  frozen_shares: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  weight: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
}

export interface IndustryAllocation {
  industry: string;
  market_value: number;
  weight: number;
  position_count: number;
  stock_codes: string[];
}

export interface PortfolioRiskMetrics {
  top_single_position_pct: number;
  top_single_position_stock_code: string | null;
  top_industry_position_pct: number;
  top_industry: string | null;
  position_hhi: number;
  industry_hhi: number;
  max_unrealized_loss_pct: number;
  max_unrealized_loss_stock_code: string | null;
  stop_loss_coverage_pct: number;
  estimated_volatility_20d: number | null;
  estimated_volatility_60d: number | null;
}

export interface PortfolioOverview {
  summary: PortfolioSummary;
  positions: PortfolioPosition[];
  industry_allocations: IndustryAllocation[];
  risk_metrics: PortfolioRiskMetrics;
  top_weights: PortfolioPosition[];
  top_gainers: PortfolioPosition[];
  top_losers: PortfolioPosition[];
}

export const portfolioApi = {
  getOverview: async () => {
    return apiClient.get<PortfolioOverview>('/portfolio/overview');
  },
};
