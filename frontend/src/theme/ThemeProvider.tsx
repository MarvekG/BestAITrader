import React from 'react';
import { App as AntdApp, ConfigProvider, theme } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import i18n from '../i18n/config';
import { normalizeLanguage } from '../i18n/language';
import { NotificationCanvas } from '../components/NotificationCanvas';
import { ThemeContext } from './themeContext';
import type { ThemeMode } from './themeContext';

const THEME_STORAGE_KEY = 'ai-trader-theme';
const DEFAULT_THEME_MODE: ThemeMode = 'dark';
const DARK_THEME_TOKENS = {
  colorBgBase: '#0d1117',
  colorBgLayout: '#10151c',
  colorBgContainer: '#171c24',
  colorBgElevated: '#1f2530',
  colorBorder: '#343c49',
  colorBorderSecondary: '#2b3340',
  colorTextBase: '#f5f7fa',
};

const readStoredThemeMode = (): ThemeMode => {
  if (typeof window === 'undefined') {
    return DEFAULT_THEME_MODE;
  }

  const storedMode = window.localStorage.getItem(THEME_STORAGE_KEY);
  return storedMode === 'light' || storedMode === 'dark' ? storedMode : DEFAULT_THEME_MODE;
};

export const AppThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [mode, setMode] = React.useState<ThemeMode>(readStoredThemeMode);
  const [language, setLanguage] = React.useState(() =>
    normalizeLanguage(i18n.resolvedLanguage || i18n.language)
  );

  React.useEffect(() => {
    document.documentElement.dataset.theme = mode;
    document.documentElement.style.colorScheme = mode === 'dark' ? 'dark' : 'light';
    window.localStorage.setItem(THEME_STORAGE_KEY, mode);
  }, [mode]);

  React.useEffect(() => {
    const handleLanguageChanged = (nextLanguage: string) => {
      setLanguage(normalizeLanguage(nextLanguage));
    };

    i18n.on('languageChanged', handleLanguageChanged);

    return () => {
      i18n.off('languageChanged', handleLanguageChanged);
    };
  }, []);

  const toggleMode = React.useCallback(() => {
    setMode((currentMode) => (currentMode === 'dark' ? 'light' : 'dark'));
  }, []);

  const contextValue = React.useMemo(
    () => ({
      mode,
      setMode,
      toggleMode,
    }),
    [mode, toggleMode]
  );

  const themeConfig = React.useMemo(
    () => ({
      algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
      token: {
        colorPrimary: '#1890ff',
        borderRadius: 6,
        ...(mode === 'dark' ? DARK_THEME_TOKENS : {}),
      },
      components: {
        Layout: {
          bodyBg: mode === 'dark' ? '#10151c' : '#f0f2f5',
          siderBg: mode === 'dark' ? '#102033' : '#ffffff',
        },
      },
    }),
    [mode]
  );
  const antdLocale = language === 'zh' ? zhCN : enUS;

  return (
    <ThemeContext.Provider value={contextValue}>
      <ConfigProvider locale={antdLocale} theme={themeConfig}>
        <AntdApp>
          <NotificationCanvas />
          {children}
        </AntdApp>
      </ConfigProvider>
    </ThemeContext.Provider>
  );
};
