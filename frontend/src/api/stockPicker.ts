import { apiClient } from './client';

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
