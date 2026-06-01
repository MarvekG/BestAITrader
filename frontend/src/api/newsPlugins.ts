import { apiClient } from './client';

export interface NewsPluginItem {
  name: string;
  plugin_id: string;
  tool_name: string;
  news_types: string[];
  keyword_examples: string[];
  module_name: string;
  qualified_module_name: string;
  can_delete: boolean;
}

export interface NewsPluginListResult {
  status: 'success' | 'error';
  count: number;
  items: NewsPluginItem[];
  message?: string;
}

export interface DependencyInstallInfo {
  status: 'success' | 'error' | 'skipped';
  requirements: string[];
  command: string[];
  exit_code?: number | null;
  stdout?: string;
  stderr?: string;
  message?: string;
}

export interface NewsPluginMutationResult {
  status: 'success' | 'error' | 'partial_success';
  message: string;
  module_name?: string;
  source?: string;
  path?: string;
  plugin?: NewsPluginItem;
  dependencies?: DependencyInstallInfo;
  filename?: string;
}

export interface NewsPluginBatchUploadResult {
  status: 'success' | 'error' | 'partial_success';
  message: string;
  success_count?: number;
  failed_count?: number;
  items?: NewsPluginMutationResult[];
}

export const newsPluginsApi = {
  list: async (): Promise<NewsPluginListResult> => {
    return apiClient.get('/news-plugins');
  },
  upload: async (files: File[]): Promise<NewsPluginBatchUploadResult | NewsPluginMutationResult> => {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    return apiClient.post('/news-plugins', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  delete: async (pluginKey: string): Promise<NewsPluginMutationResult> => {
    return apiClient.delete(`/news-plugins/${encodeURIComponent(pluginKey)}`);
  },
};
