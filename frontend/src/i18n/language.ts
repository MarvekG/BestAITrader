export type AppLanguage = 'zh' | 'en';

export const LANGUAGE_STORAGE_KEY = 'language';
export const SUPPORTED_LANGUAGES: AppLanguage[] = ['zh', 'en'];

export const normalizeLanguage = (language?: string | null): AppLanguage => {
  const normalizedLanguage = (language ?? '').trim().toLowerCase().replace('_', '-');

  if (normalizedLanguage.startsWith('en')) {
    return 'en';
  }

  return 'zh';
};

export const getStoredLanguage = (): AppLanguage => {
  if (typeof window === 'undefined') {
    return 'zh';
  }

  return normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY));
};

export const storeLanguage = (language: AppLanguage) => {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
};
