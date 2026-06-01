import { apiClient } from './client';

export interface StockPickerRun {
  run_id: string;
  scope: 'warehouse' | 'core' | 'all';
  style: 'balanced' | 'momentum' | 'value' | 'growth' | 'defensive';
  risk_level: 'low' | 'medium' | 'high';
  recommendation_count: number;
  factor_candidate_limit: number;
  research_candidate_limit: number;
  allowed_industries: string[];
  same_industry_limit: number;
  status: string;
  current_stage: string;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  summary_payload?: Record<string, unknown> | null;
}

export interface StockPickerEvent {
  id: number;
  run_id: string;
  stage: string;
  event_type: string;
  message: string;
  payload?: Record<string, unknown> | null;
  created_at: string;
}

export interface StockPickerRecommendationItem {
  stock_code: string;
  stock_name?: string | null;
  rank: number;
  conviction_score: number;
  recommendation_reason: string;
  risk_flags: string[];
  holding_horizon: string;
  decision: string;
}

export interface StockPickerQuantSupport {
  style_fit_score: number;
  liquidity_score: number;
  risk_penalty: number;
  final_quant_score: number;
}

export interface StockPickerCandidate {
  stock_code: string;
  stock_name?: string | null;
  industry?: string | null;
  market?: string | null;
  factor_score: number;
  ai_score: number;
  final_score: number;
  quant_support?: StockPickerQuantSupport | null;
  decision: string;
  eliminated_stage?: string | null;
  eliminated_reason?: string | null;
  research_payload?: Record<string, unknown> | null;
}

export interface StockPickerSummary extends Record<string, unknown> {
  research_mode?: string;
  candidate_count?: number;
  universe_count?: number;
  factor_candidate_count?: number;
  research_candidate_count?: number;
  same_industry_limit?: number;
  decision_breakdown?: {
    keep?: number;
    watch?: number;
    drop?: number;
  };
}

export interface StockPickerResult {
  run: StockPickerRun;
  summary: StockPickerSummary;
  recommendations: {
    stocks: StockPickerRecommendationItem[];
    recommendation_logic: string;
    style: string;
    scope: string;
    generated_at: string;
  };
  alternatives: StockPickerCandidate[];
  risk_summary: Record<string, unknown>;
}

export const stockPickerApi = {
  createRun: async (payload: {
    scope: 'warehouse' | 'core' | 'all';
    style: 'balanced' | 'momentum' | 'value' | 'growth' | 'defensive';
    recommendation_count: number;
    risk_level: 'low' | 'medium' | 'high';
    factor_candidate_limit?: number;
    research_candidate_limit?: number;
    allowed_industries?: string[];
    same_industry_limit?: number;
  }) => {
    return apiClient.post<{ run_id: string; status: string; message: string }>('/ai-stock-picker/runs', payload);
  },

  listIndustries: async () => {
    return apiClient.get<string[]>('/ai-stock-picker/industries');
  },

  listRuns: async () => {
    return apiClient.get<StockPickerRun[]>('/ai-stock-picker/runs');
  },

  getRun: async (runId: string) => {
    return apiClient.get<StockPickerRun>(`/ai-stock-picker/runs/${runId}`);
  },

  getEvents: async (runId: string) => {
    return apiClient.get<StockPickerEvent[]>(`/ai-stock-picker/runs/${runId}/events`);
  },

  getCandidates: async (runId: string) => {
    return apiClient.get<StockPickerCandidate[]>(`/ai-stock-picker/runs/${runId}/candidates`);
  },

  getResult: async (runId: string) => {
    return apiClient.get<StockPickerResult>(`/ai-stock-picker/runs/${runId}/result`);
  },

  deleteRun: async (runId: string) => {
    return apiClient.delete<{ message: string }>(`/ai-stock-picker/runs/${runId}`);
  },

  clearRuns: async () => {
    return apiClient.delete<{ message: string; count: number }>('/ai-stock-picker/runs');
  },
};
