import { apiClient } from './client';

export interface MemoryDocumentSummary {
  id: string;
  stock_code: string;
  stock_name?: string | null;
  size_chars: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface MemoryDocumentDetail extends MemoryDocumentSummary {
  content: string;
  max_chars: number;
}

export interface MemoryDocumentListResponse {
  items: MemoryDocumentSummary[];
  total: number;
  page: number;
  page_size: number;
  max_chars: number;
}

export interface MemoryDocumentListParams {
  stock_code?: string;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export const memoryDocumentsApi = {
  list: async (params: MemoryDocumentListParams = {}) => (
    apiClient.get<MemoryDocumentListResponse>('/memory-documents', { params })
  ),

  get: async (stockCode: string) => (
    apiClient.get<MemoryDocumentDetail>(`/memory-documents/${encodeURIComponent(stockCode)}`)
  ),
};
