import { apiClient } from './client';

export interface StockAnalysisRequest {
  stock_code?: string | null;
  question: string;
}

export interface StockAnalysisTaskResponse {
  task_id: string;
  task_name: string;
  status: string;
  message: string;
  new_task: boolean;
}

export const stockAnalysisApi = {
  run: async (payload: StockAnalysisRequest): Promise<StockAnalysisTaskResponse> => {
    return apiClient.post('/stock-analysis/run', payload);
  },
};
