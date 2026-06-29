import { apiClient } from './client';

export interface Session {
  session_id: string;  // 后端使用session_id,不是id
  id?: string;  // 保持向后兼容
  stock_code: string;
  stock_name: string;
  status: 'active' | 'completed' | 'failed';
  created_at: string;
  updated_at: string;
  ended_at?: string | null;
  trading_frequency: string;
  trading_strategy: string;
  source: 'manual' | 'scheduled' | 'market_watch' | 'stop_loss' | 'take_profit';
}

export interface SessionListResponse {
  total: number;
  items: Session[];
  limit: number;
  skip: number;
}

export interface SessionListParams {
  status?: string;
  source?: Session['source'];
  q?: string;
  skip?: number;
  limit?: number;
}

export const sessionApi = {
  create: (data: {
    stock_code: string;
    stock_name?: string;
    trading_frequency: string;
    trading_strategy: string;
    source?: Session['source'];
  }) =>
    apiClient.post<Session>('/sessions/', data),

  list: (params?: SessionListParams) =>
    apiClient.get<Session[]>('/sessions/', { params }),

  listPaginated: async (params?: SessionListParams): Promise<SessionListResponse> => {
    const response = await apiClient.get<SessionListResponse | Session[]>('/sessions/', {
      params: { ...params, paginated: true },
    });
    if (Array.isArray(response)) {
      return {
        total: response.length,
        items: response,
        limit: params?.limit ?? response.length,
        skip: params?.skip ?? 0,
      };
    }
    return response;
  },

  get: (id: string) =>
    apiClient.get<Session>(`/sessions/${id}`),

  update: (id: string, data: Partial<Session>) =>
    apiClient.put<Session>(`/sessions/${id}`, data),

  delete: (id: string) =>
    apiClient.delete<{ message?: string }>(`/sessions/${id}`),

  batchDelete: (sessionIds: string[]) =>
    apiClient.post<{ message?: string }>('/sessions/batch-delete', { session_ids: sessionIds }),
};
