import { apiClient } from './client';

interface ApiEnvelope<T> {
  data?: T;
  error?: unknown;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const unwrapApiData = <T>(payload: T | ApiEnvelope<T>): T => {
  if (isRecord(payload) && 'data' in payload && isRecord(payload.data)) {
    return payload.data as T;
  }
  return payload as T;
};

export interface PromptTemplate {
  role: string;
  content: string;
  version: string;
}

export interface UsageBreakdownEntry {
  calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cached_tokens?: number;
  cache_miss_tokens?: number;
  reasoning_tokens?: number;
  cache_hit_rate?: number;
  iteration_indexes?: number[];
}

export type UsageBreakdown = Record<string, UsageBreakdownEntry>;

export interface PromptStats {
  total_calls: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens: number;
  cached_tokens?: number;
  cache_miss_tokens?: number;
  reasoning_tokens?: number;
  cache_hit_rate?: number;
  by_role: Record<string, number>;
  by_role_detail?: UsageBreakdown;
  by_workflow?: UsageBreakdown;
  by_stage?: UsageBreakdown;
  by_workflow_stage?: UsageBreakdown;
  by_workflow_call_kind?: UsageBreakdown;
  by_call_kind?: UsageBreakdown;
  by_cache_lane?: UsageBreakdown;
  by_api_key_alias?: UsageBreakdown;
  backend?: {
    total_calls?: number;
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    cached_tokens?: number;
    cache_miss_tokens?: number;
    reasoning_tokens?: number;
    cache_hit_rate?: number;
    by_role?: Record<string, number>;
    by_role_detail?: UsageBreakdown;
    by_workflow?: UsageBreakdown;
    by_stage?: UsageBreakdown;
    by_workflow_stage?: UsageBreakdown;
    by_workflow_call_kind?: UsageBreakdown;
    by_call_kind?: UsageBreakdown;
    by_cache_lane?: UsageBreakdown;
    by_api_key_alias?: UsageBreakdown;
  } | null;
  memory?: {
    llm_runs?: number;
    total_calls?: number;
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    cached_tokens?: number;
    cache_miss_tokens?: number;
    reasoning_tokens?: number;
    cache_hit_rate?: number;
    by_operation?: Record<
      string,
      {
        calls?: number;
        input_tokens?: number;
        output_tokens?: number;
        total_tokens?: number;
        cached_tokens?: number;
        cache_miss_tokens?: number;
        reasoning_tokens?: number;
        cache_hit_rate?: number;
      }
    >;
  } | null;
  combined?: {
    total_calls?: number;
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    cached_tokens?: number;
    cache_miss_tokens?: number;
    reasoning_tokens?: number;
    cache_hit_rate?: number;
    by_role?: Record<string, number>;
    by_role_detail?: UsageBreakdown;
    by_workflow?: UsageBreakdown;
    by_stage?: UsageBreakdown;
    by_workflow_stage?: UsageBreakdown;
    by_workflow_call_kind?: UsageBreakdown;
    by_call_kind?: UsageBreakdown;
    by_cache_lane?: UsageBreakdown;
    by_api_key_alias?: UsageBreakdown;
  } | null;
}

export interface ClearUsageStatsResult {
  status: string;
  backend?: { deleted?: number } | null;
  memory?: { status?: string; deleted?: number; error?: Record<string, unknown> } | null;
  total_deleted: number;
}

export const promptApi = {
  /**
   * Get all prompt templates
   */
  getAllPrompts: async (): Promise<Record<string, string>> => {
    return apiClient.get('/prompt/');
  },

  /**
   * Get a specific prompt template by role
   */
  getPrompt: async (role: string): Promise<PromptTemplate> => {
    return apiClient.get(`/prompt/${role}`);
  },

  /**
   * Get prompt usage statistics
   */
  getUsageStats: async (): Promise<PromptStats> => {
    const response = await apiClient.get<PromptStats | ApiEnvelope<PromptStats>>('/llm/usage-stats');
    return unwrapApiData(response);
  },

  /**
   * Clear prompt usage statistics
   */
  clearUsageStats: async (): Promise<ClearUsageStatsResult> => {
    const response = await apiClient.delete<ClearUsageStatsResult | ApiEnvelope<ClearUsageStatsResult>>('/llm/usage-stats');
    return unwrapApiData(response);
  }
};
