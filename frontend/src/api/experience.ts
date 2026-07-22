import { apiClient } from './client';

export type ExperienceStyleBucket = 'short_term' | 'swing' | 'position' | 'long_term';
export type ExperienceAction = 'avoid' | 'watch' | 'buy' | 'add' | 'hold' | 'reduce' | 'sell';
export type ExperienceReviewHorizon = '5d' | '20d' | '60d';

export interface ExperienceReviewCandidate {
  session_id: string;
  stock_code: string;
  stock_name?: string | null;
  industry?: string | null;
  status: string;
  trading_frequency?: string | null;
  trading_strategy?: string | null;
  pm_decision?: string | null;
  pm_confidence?: number | null;
  pm_created_at?: string | null;
  market_day_count: number;
  eligible_horizons: ExperienceReviewHorizon[];
  latest_completed_horizons: ExperienceReviewHorizon[];
  active_horizons: ExperienceReviewHorizon[];
  failed_horizons: ExperienceReviewHorizon[];
  review_status: string;
  next_horizon?: ExperienceReviewHorizon | null;
  days_until_next_horizon?: number | null;
}

export interface ExperienceReviewCandidateListResponse {
  items: ExperienceReviewCandidate[];
  summary: Record<string, number>;
}

export interface ExperienceDebateSession {
  session_id: string;
  stock_code: string;
  stock_name?: string | null;
  status: string;
  trading_frequency: string;
  trading_strategy: string;
  created_at: string;
  updated_at: string;
  pm_decision?: string | null;
  pm_confidence?: number | null;
  has_experience_review: boolean;
}

export interface ExperienceWrittenMemory {
  content?: string;
  memo_session?: string;
  importance?: string;
  stock_code?: string;
  stock_name?: string;
  status?: string;
  memory_id?: string;
  error?: string;
  evidence_chain?: Record<string, unknown>;
}

export interface ExperienceSignalReviewItem {
  signal: string;
  evidence?: string;
  impact?: string;
  lesson?: string;
}

export interface ExperienceNoiseSignalItem {
  signal: string;
  reason?: string;
}

export interface ExperienceReviewTriads {
  original_judgment?: {
    verdict?: string;
    score?: number;
    pm_decision?: string;
    outcome_basis?: string;
    reasoning?: string;
  };
  signal_validation?: {
    validated_signals?: ExperienceSignalReviewItem[];
    invalidated_signals?: ExperienceSignalReviewItem[];
    noise_signals?: ExperienceNoiseSignalItem[];
  };
  decision_process_improvement?: {
    debate_changes?: string[];
    pm_changes?: string[];
    risk_control_changes?: string[];
  };
}

export interface ExperienceAnalysisPayload extends Record<string, unknown> {
  recommended_action?: string;
  confidence_score?: number;
  debate_correctness?: string;
  correctness_reasoning?: string;
  review_triads?: ExperienceReviewTriads;
  experience_tags?: Record<string, string[]>;
  written_memories?: ExperienceWrittenMemory[];
  thesis_summary?: string;
  market_experience_summary?: string;
  dominant_drivers?: string[];
  rejected_drivers?: string[];
  driver_dimension_review?: string[];
  buy_sell_rules?: string[];
  debate_process_issues?: string[];
  optimization_directions?: string[];
  improved_debate_rules?: string[];
  memory_evidence_used?: string[];
  internet_evidence_used?: string[];
}

export interface ExperienceToolTraceItem extends Record<string, unknown> {
  name?: string;
  args?: unknown;
}

export interface ExperienceAnalyzeResponse {
  review_run_id?: string | null;
  review_horizon?: ExperienceReviewHorizon | null;
  market_day_count?: number | null;
  session_id: string;
  stock_code: string;
  stock_name?: string | null;
  industry?: string | null;
  style_bucket: ExperienceStyleBucket;
  trading_frequency?: string | null;
  trading_strategy?: string | null;
  analysis_date: string;
  reviewed_at: string;
  analysis_payload: ExperienceAnalysisPayload;
  tool_trace: ExperienceToolTraceItem[];
}

export interface ExperienceReviewEvent {
  event_id: string;
  review_run_id: string;
  session_id?: string | null;
  event_type: string;
  stage: string;
  status: string;
  message_key?: string | null;
  message?: string | null;
  message_params: Record<string, unknown>;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ExperienceReviewRun {
  review_run_id: string;
  review_horizon?: ExperienceReviewHorizon | null;
  market_day_count?: number | null;
  session_id: string;
  stock_code: string;
  stock_name?: string | null;
  trading_frequency?: string | null;
  trading_strategy?: string | null;
  status: string;
  stage: string;
  message_key?: string | null;
  message_params: Record<string, unknown>;
  recommended_action?: string | null;
  debate_correctness?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExperienceReviewSchedulerConfig {
  enabled: boolean;
  schedule_hour: number;
  schedule_minute: number;
  candidate_lookback: number;
  max_runs_per_tick: number;
}

export interface ExperienceLibraryItem {
  id: string;
  memory_id?: string | null;
  review_run_id: string;
  session_id: string;
  stock_code?: string | null;
  stock_name?: string | null;
  industry?: string | null;
  strategy?: string | null;
  review_horizon?: ExperienceReviewHorizon | null;
  outcome_label?: string | null;
  correctness?: string | null;
  importance?: string | null;
  summary: string;
  tags: Record<string, string[]>;
  created_at: string;
  updated_at: string;
}

export interface ExperienceLibraryListResponse {
  items: ExperienceLibraryItem[];
  total: number;
  page: number;
  page_size: number;
  summary: Record<string, number>;
}

export interface ExperienceLibraryDetail extends ExperienceLibraryItem {
  review_triads: ExperienceReviewTriads;
  market_outcome_summary: Record<string, unknown>;
  memory: ExperienceWrittenMemory;
}

export interface ExperienceLibraryFilters {
  stock_code?: string;
  industry?: string;
  strategy?: string;
  review_horizon?: ExperienceReviewHorizon;
  correctness?: string;
  importance?: string;
  tag?: string;
  keyword?: string;
  created_from?: string;
  created_to?: string;
  page?: number;
  page_size?: number;
}

export interface ExperienceLibraryRebuildResponse {
  created: number;
  updated: number;
  skipped: number;
  failed: number;
}

export const experienceApi = {
  getSchedulerConfig: async () => {
    return apiClient.get<ExperienceReviewSchedulerConfig>('/experience/scheduler-config');
  },

  updateSchedulerConfig: async (payload: ExperienceReviewSchedulerConfig) => {
    return apiClient.put<ExperienceReviewSchedulerConfig>('/experience/scheduler-config', payload);
  },

  analyze: async (payload: { session_id: string; review_horizon?: ExperienceReviewHorizon }) => {
    return apiClient.post<ExperienceAnalyzeResponse>('/experience/analyze', payload);
  },

  listReviewCandidates: async () => {
    return apiClient.get<ExperienceReviewCandidateListResponse>('/experience/review-candidates');
  },

  listDebateSessions: async () => {
    return apiClient.get<ExperienceDebateSession[]>('/experience/debate-sessions');
  },

  listReviewEvents: async (sessionId: string) => {
    return apiClient.get<ExperienceReviewEvent[]>(`/experience/review-events/${sessionId}`);
  },

  listReviewRuns: async () => {
    return apiClient.get<ExperienceReviewRun[]>('/experience/review-runs');
  },

  listReviewRunEvents: async (reviewRunId: string) => {
    return apiClient.get<ExperienceReviewEvent[]>(`/experience/review-run-events/${reviewRunId}`);
  },

  getReviewRunResult: async (reviewRunId: string) => {
    return apiClient.get<ExperienceAnalyzeResponse | null>(`/experience/review-run-result/${reviewRunId}`);
  },

  deleteReviewRun: async (reviewRunId: string) => {
    return apiClient.delete<{ message: string }>(`/experience/review-runs/${reviewRunId}`);
  },

  clearReviewRuns: async () => {
    return apiClient.delete<{ message: string; count: number }>('/experience/review-runs');
  },

  listLibrary: async (filters: ExperienceLibraryFilters = {}) => {
    return apiClient.get<ExperienceLibraryListResponse>('/experience/library', { params: filters });
  },

  getLibraryDetail: async (id: string) => {
    return apiClient.get<ExperienceLibraryDetail>(`/experience/library/${id}`);
  },

  rebuildLibrary: async () => {
    return apiClient.post<ExperienceLibraryRebuildResponse>('/experience/library/rebuild');
  },
};
