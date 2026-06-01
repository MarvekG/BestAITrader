import { apiClient } from './client';

export interface AsyncTaskRecord {
  task_id: string;
  task_name: string;
  task_type: string;
  status: string;
  allow_concurrent: boolean;
  parameters?: Record<string, unknown>;
  result?: unknown;
  error_message?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export const tasksApi = {
  getTask: async (taskId: string): Promise<AsyncTaskRecord> => {
    return apiClient.get(`/tasks/${encodeURIComponent(taskId)}`);
  },
  listTasks: async (params: {
    status?: string;
    task_type?: string;
    limit?: number;
    skip?: number;
  }): Promise<{ total: number; items: AsyncTaskRecord[]; limit: number; skip: number }> => {
    return apiClient.get('/tasks', { params });
  },
};
