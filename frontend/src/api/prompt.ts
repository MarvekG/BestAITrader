import { apiClient } from './client';

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
    entities?: number;
    memories?: number;
    observations?: number;
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
    return apiClient.get('/llm/usage-stats');
  },

  /**
   * Clear prompt usage statistics
   */
  clearUsageStats: async (): Promise<ClearUsageStatsResult> => {
    return apiClient.delete('/llm/usage-stats');
  }
};
