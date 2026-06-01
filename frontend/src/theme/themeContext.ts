import React from 'react';

export type ThemeMode = 'light' | 'dark';

export interface ThemeContextValue {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggleMode: () => void;
}

export const ThemeContext = React.createContext<ThemeContextValue | undefined>(undefined);
