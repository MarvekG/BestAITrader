import { apiClient } from './client';

export interface Token {
  access_token: string;
  token_type: string;
}

export const authApi = {
  login: async (data: URLSearchParams) => {
    return apiClient.post<Token>('/auth/login', data, {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      }
    });
  },

  resetPassword: async (data: { new_password: string }) => {
    return apiClient.post<{ message: string }>('/auth/reset-password', data);
  }
};
