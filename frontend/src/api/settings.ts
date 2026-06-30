import { apiClient } from './client';

export interface DataSourceConfig {
  tushare_api_url?: string | null;
  tushare_token?: string | null;
  tavily_api_key?: string[] | null;
  news_api_key?: string[] | null;
}

export interface DataSourceConfigTestResult {
  status: 'success' | 'error' | 'completed';
  http_status?: number;
  elapsed_ms?: number;
  raw_body?: string;
  data?: unknown;
  error?: string;
}

export type DataSourceConfigTestKey = 'tushare' | 'tavily' | 'newsapi';

export interface RuntimeSettings {
  ai_debate_max_concurrent: number;
}

interface DataSourceConfigResponse {
  config: DataSourceConfig;
}

export const sourcesApi = {
  getDataSourceConfig: async () => {
    const response = await apiClient.get<DataSourceConfigResponse>('/sources/config');
    return response.config;
  },

  updateDataSourceConfig: async (config: DataSourceConfig) => {
    return apiClient.post('/sources/config', config);
  },

  testDataSourceConfig: async (key: DataSourceConfigTestKey, config: DataSourceConfig, query?: string) => {
    return apiClient.post<DataSourceConfigTestResult>(`/sources/config/test/${key}`, { query, config });
  }
};

export const runtimeSettingsApi = {
  getRuntimeSettings: async () => {
    return apiClient.get<RuntimeSettings>('/general/runtime-settings');
  },

  updateRuntimeSettings: async (settings: RuntimeSettings) => {
    return apiClient.put<RuntimeSettings>('/general/runtime-settings', settings);
  }
};
