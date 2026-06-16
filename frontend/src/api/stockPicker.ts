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
  profit_logic?: string;
  trend_evidence?: string[];
  risk_evidence?: string[];
  risk_flags: string[];
  invalidation_conditions?: string[];
  holding_horizon: string;
  decision: string;
}

export interface StockPickerQuantSupport {
  style_fit_score: number;
  trend_quality_score?: number | null;
  liquidity_score: number;
  valuation_safety_score?: number | null;
  volatility_score?: number | null;
  risk_penalty: number;
  profit_condition_score?: number | null;
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

export type StockPickerScope = 'warehouse' | 'core' | 'all';
export type StockPickerStyle = 'balanced' | 'momentum' | 'value' | 'growth' | 'defensive';
export type StockPickerRiskLevel = 'low' | 'medium' | 'high';
export type InteractiveResearchDepth = 'light' | 'standard' | 'deep';
export type InteractiveResearchAction = 'approve' | 'cancel';

export interface InteractiveResearchRunCreatePayload {
  requirement: string;
  scope?: StockPickerScope;
  research_depth?: InteractiveResearchDepth;
  expected_count?: number;
  risk_level?: StockPickerRiskLevel;
  style?: StockPickerStyle | null;
  allowed_industries?: string[];
  excluded_industries?: string[];
  exclude_recent_ipos?: boolean;
  min_listing_days?: number | null;
  max_iterations?: number;
}

export interface InteractiveResearchRunSummary {
  run_id: string;
  user_id: number;
  status: string;
  current_stage: string;
  current_phase: string;
  title: string;
  raw_requirement: string;
  pending_message_id?: string | null;
  checkpoint_payload: Record<string, unknown>;
  llm_usage?: Record<string, unknown>;
  cache_context_version: string;
  version: number;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
}

export interface InteractiveResearchMessage {
  message_id: string;
  run_id: string;
  role: string;
  message_type: string;
  content: string;
  display_type: string;
  markdown: string;
  execution_status?: string | null;
  payload: Record<string, unknown>;
  parent_message_id?: string | null;
  sequence_no: number;
  status: string;
  visible_to_user: boolean;
  created_at: string;
}

export interface InteractiveResearchRunResponse {
  run: InteractiveResearchRunSummary;
  messages: InteractiveResearchMessage[];
}

export interface InteractiveResearchMessageAppendResponse {
  run: InteractiveResearchRunSummary;
  message: InteractiveResearchMessage;
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

export const interactiveStockPickerApi = {
  createRun: async (payload: InteractiveResearchRunCreatePayload) => {
    return apiClient.post<InteractiveResearchRunResponse>('/ai-stock-picker/interactive/runs', payload);
  },

  listRuns: async () => {
    return apiClient.get<InteractiveResearchRunSummary[]>('/ai-stock-picker/interactive/runs');
  },

  getRun: async (runId: string) => {
    return apiClient.get<InteractiveResearchRunSummary>(`/ai-stock-picker/interactive/runs/${runId}`);
  },

  getMessages: async (runId: string) => {
    return apiClient.get<InteractiveResearchMessage[]>(`/ai-stock-picker/interactive/runs/${runId}/messages`);
  },

  appendMessage: async (runId: string, payload: { content: string; payload?: Record<string, unknown> }) => {
    return apiClient.post<InteractiveResearchMessageAppendResponse>(
      `/ai-stock-picker/interactive/runs/${runId}/messages`,
      payload,
    );
  },

  runAction: async (
    runId: string,
    payload: { action: InteractiveResearchAction; content?: string; payload?: Record<string, unknown> },
  ) => {
    return apiClient.post<InteractiveResearchRunResponse>(
      `/ai-stock-picker/interactive/runs/${runId}/actions`,
      payload,
    );
  },

  deleteRun: async (runId: string) => {
    return apiClient.delete<{ message: string }>(`/ai-stock-picker/interactive/runs/${runId}`);
  },

};
