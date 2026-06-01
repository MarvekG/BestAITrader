import axios, { AxiosInstance, AxiosRequestConfig } from 'axios';
import { logger } from '../utils/logger';
import { apiHistory } from '../utils/apiHistory';
import { formatErrorMessage } from '../utils/errorUtils';

const axiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 60000, // Increased to 60s for snapshot creation which fetches external data
  headers: {
    'Content-Type': 'application/json',
  },
});

type HistoryRequestConfig<D = unknown> = AxiosRequestConfig<D> & {
  _historyId?: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

axiosInstance.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }

    // 开始记录真正的 HTTP 请求状态 | Start recording real HTTP request status
    try {
      const url = config.url || '';
      const method = config.method || 'GET';
      let params: unknown = {};
      if (method.toUpperCase() === 'GET') {
        params = config.params;
      } else {
        if (config.data) {
          if (typeof config.data === 'string') {
            try {
              params = JSON.parse(config.data);
            } catch {
              // 如果解析 JSON 失败（比如是 form-urlencoded 字符串），直接保存原始字符串
              // (If JSON parse fails, e.g. form-urlencoded, keep raw string)
              params = config.data;
            }
          } else {
            params = config.data;
          }
        }
      }

      const historyId = apiHistory.startHttpRequest(url, method, params);
      if (historyId) {
        (config as HistoryRequestConfig)._historyId = historyId; // 挂载到 config 上供 response interceptor 使用
      }
    } catch (error) {
      console.error('Failed to start API history tracking:', error);
    }

    return config;
  },
  (error) => Promise.reject(error)
);

axiosInstance.interceptors.response.use(
  (response) => {
    // 记录 API 历史 | Record API history
    try {
      const config = response.config as HistoryRequestConfig;
      const historyId = config._historyId;

      if (historyId) {
        const method = config.method || 'GET';
        const responseData = response.data;

        // 检查响应是否包含 task_id 且不是 GET 请求（异步任务通常由 POST/PUT 触发）| Check if response contains task_id and is not a GET request
        if (
          method.toUpperCase() !== 'GET' &&
          responseData &&
          typeof responseData === 'object' &&
          'task_id' in responseData
        ) {
          // 异步任务：保持 pending，交由 websocket 更新完成 | Async task: keep pending, let websocket complete it
          const taskId = isRecord(responseData) && typeof responseData.task_id === 'string'
            ? responseData.task_id
            : undefined;
          apiHistory.finishHttpRequest(historyId, responseData, taskId);
        } else {
          // 同步请求：直接完成计算真实耗时 | Sync request: exact complete tracking
          apiHistory.finishHttpRequest(historyId, responseData);
        }
      }
    } catch (error) {
      console.error('Failed to update API history:', error);
    }

    return response.data;
  },
  (error) => {
    // Record API errors | 记录 API 错误
    const errorMeta = {
      url: error.config?.url,
      method: error.config?.method,
      status: error.response?.status,
      response: error.response?.data
    };

    // Log error (ignore 401 as it's handled below)
    if (error.response?.status !== 401) {
      logger.error(`API Error: ${error.message}`, errorMeta);

      // 记录失败的请求到历史 | Record failed request to history
      try {
        const config = error.config as HistoryRequestConfig | undefined;
        const historyId = config?._historyId;

        if (historyId) {
          const detail = error.response?.data?.detail;
          const errorMessage = formatErrorMessage(detail) || error.message || 'Request failed';
          apiHistory.failHttpRequest(historyId, errorMessage, error.response?.data);
        }
      } catch (historyError) {
        console.error('Failed to update failed API request:', historyError);
      }
    }

    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      // 如果当前不在登录页，才跳转到登录页，避免登录失败时的循环干扰
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

// Define a custom interface for our client that returns Promise<T> directly
export interface ApiClient {
  get<T = unknown, D = unknown>(url: string, config?: AxiosRequestConfig<D>): Promise<T>;
  post<T = unknown, D = unknown>(url: string, data?: D, config?: AxiosRequestConfig<D>): Promise<T>;
  put<T = unknown, D = unknown>(url: string, data?: D, config?: AxiosRequestConfig<D>): Promise<T>;
  delete<T = unknown, D = unknown>(url: string, config?: AxiosRequestConfig<D>): Promise<T>;
  defaults: AxiosInstance['defaults'];
  interceptors: AxiosInstance['interceptors'];
}

export const apiClient = axiosInstance as unknown as ApiClient;
