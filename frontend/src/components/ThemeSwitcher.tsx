import React from 'react';
import { Segmented, Tooltip } from 'antd';
import { Moon, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ThemeMode } from '../theme/themeContext';
import { useThemeMode } from '../theme/useThemeMode';

interface ThemeSwitcherProps {
  block?: boolean;
}

const ThemeSwitcher: React.FC<ThemeSwitcherProps> = ({ block = false }) => {
  const { i18n } = useTranslation();
  const { mode, setMode } = useThemeMode();
  const isZh = i18n.language?.toLowerCase().startsWith('zh');
  const lightLabel = isZh ? '白天' : 'Day';
  const darkLabel = isZh ? '晚上' : 'Night';

  const renderLabel = (label: string, icon: React.ReactNode) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, lineHeight: 1 }}>
      {icon}
      <span>{label}</span>
    </span>
  );

  return (
    <Tooltip title={isZh ? '切换主题' : 'Switch theme'}>
      <Segmented<ThemeMode>
        aria-label={isZh ? '主题切换' : 'Theme switcher'}
        block={block}
        size="small"
        value={mode}
        onChange={setMode}
        options={[
          {
            value: 'light',
            label: renderLabel(lightLabel, <Sun size={14} />),
          },
          {
            value: 'dark',
            label: renderLabel(darkLabel, <Moon size={14} />),
          },
        ]}
      />
    </Tooltip>
  );
};

export default ThemeSwitcher;
