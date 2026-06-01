import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import './i18n/config';
import { AppThemeProvider } from './theme/ThemeProvider';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppThemeProvider>
      <React.Suspense fallback={<div className="app-loading">Loading...</div>}>
        <App />
      </React.Suspense>
    </AppThemeProvider>
  </React.StrictMode>
);
