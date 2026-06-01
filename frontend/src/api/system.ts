import type { AppLanguage } from '../i18n/language';
import { apiClient } from './client';

interface SystemLanguageResponse {
  language: AppLanguage;
  supported_languages: AppLanguage[];
}

export const systemApi = {
  getLanguage: async () => {
    return apiClient.get<SystemLanguageResponse>('/general/language');
  },

  updateLanguage: async (language: AppLanguage) => {
    return apiClient.put<SystemLanguageResponse>('/general/language', { language });
  },
};
