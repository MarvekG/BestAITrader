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

  testDataSourceConfig: async (key: DataSourceConfigTestKey) => {
    return apiClient.post<DataSourceConfigTestResult>(`/sources/config/test/${key}`);
  }
};
