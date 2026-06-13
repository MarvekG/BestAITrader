import { apiClient } from './client';

export const sourcesApi = {
  getTushareConfig: async () => {
    return apiClient.get<{ api_url: string; token: string }>('/sources/tushare/config');
  },

  updateTushareConfig: async (config: { api_url?: string; token?: string }) => {
    return apiClient.post('/sources/tushare/config', config);
  }
};
