import React from 'react';

import { ThemeContext } from './themeContext';
import type { ThemeContextValue } from './themeContext';

export const useThemeMode = (): ThemeContextValue => {
  const context = React.useContext(ThemeContext);

  if (!context) {
    throw new Error('useThemeMode must be used within AppThemeProvider');
  }

  return context;
};
