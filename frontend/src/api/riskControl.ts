import { apiClient } from './client';
import { OrderRequest } from './trading';

export interface RiskControlConfig {
  id: number;
  account_id: string | null;
  enabled: boolean;
  max_single_position_pct: number;
  max_industry_position_pct: number;
  min_cash_pct: number;
  require_stop_loss: boolean;
  stop_loss_warning_pct: number;
  rule_policies: Record<RiskControlRule, RiskControlPolicy>;
  created_at: string | null;
  updated_at: string | null;
}

export type RiskControlRule =
  | 'require_stop_loss'
  | 'max_single_position_pct'
  | 'max_industry_position_pct'
  | 'min_cash_pct'
  | 'stop_loss_warning_pct';

export type RiskControlPolicy = 'off' | 'block';

export interface RiskControlHit {
  rule: string;
  message: string;
  message_key: string;
  params: Record<string, string>;
  current_value: number | boolean | null;
  limit_value: number | boolean | null;
  stock_code: string;
  industry: string;
}

export interface RiskControlResult {
  enabled: boolean;
  passed: boolean;
  severity: 'none' | 'block';
  accepted: RiskControlHit[];
  blocks: RiskControlHit[];
  metrics: Record<string, number | string | null>;
}

export type RiskControlConfigUpdate = Pick<
  RiskControlConfig,
  | 'enabled'
  | 'max_single_position_pct'
  | 'max_industry_position_pct'
  | 'min_cash_pct'
  | 'require_stop_loss'
  | 'stop_loss_warning_pct'
  | 'rule_policies'
>;

export const riskControlApi = {
  getConfig: async () => apiClient.get<RiskControlConfig>('/risk-control/config'),

  updateConfig: async (data: RiskControlConfigUpdate) => (
    apiClient.put<RiskControlConfig, RiskControlConfigUpdate>('/risk-control/config', data)
  ),

  evaluateOrder: async (data: OrderRequest) => (
    apiClient.post<RiskControlResult, OrderRequest>('/risk-control/evaluate-order', data)
  ),
};
