import { apiClient } from './client';

export interface Session {
  session_id: string;  // 后端使用session_id,不是id
  id?: string;  // 保持向后兼容
  stock_code: string;
  stock_name: string;
  status: 'active' | 'completed' | 'failed' | 'archived';
  created_at: string;
  updated_at: string;
  ended_at?: string | null;
  trading_frequency: string;
  trading_strategy: string;
}

export const sessionApi = {
  create: (data: { stock_code: string; stock_name?: string; trading_frequency: string; trading_strategy: string }) =>
    apiClient.post<Session>('/sessions/', data),

  list: (params?: { status?: string }) =>
    apiClient.get<Session[]>('/sessions/', { params }),

  get: (id: string) =>
    apiClient.get<Session>(`/sessions/${id}`),

  update: (id: string, data: Partial<Session>) =>
    apiClient.put<Session>(`/sessions/${id}`, data),

  archive: (id: string) =>
    apiClient.post<Session>(`/sessions/${id}/archive`),

  delete: (id: string) =>
    apiClient.delete<{ message?: string }>(`/sessions/${id}`),

  batchDelete: (sessionIds: string[]) =>
    apiClient.post<{ message?: string }>('/sessions/batch-delete', { session_ids: sessionIds }),

  batchArchive: (sessionIds: string[]) =>
    apiClient.post<{ message?: string }>('/sessions/batch-archive', { session_ids: sessionIds }),
};
