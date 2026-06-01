import { apiClient } from './client';

export const sourcesApi = {
  getTushareConfig: async () => {
    return apiClient.get<{ api_url: string; token: string }>('/sources/tushare/config');
  },

  updateTushareConfig: async (config: { api_url?: string; token?: string }) => {
    return apiClient.post('/sources/tushare/config', config);
  },

  importDatabaseBackup: async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiClient.post<{ status: string; message: string; filename: string }>(
      '/sources/database/import',
      formData,
      {
        timeout: 0,
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    );
  }
};
