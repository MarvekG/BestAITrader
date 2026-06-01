import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';

import { useSessionStore } from './store/useSessionStore';

const DashboardLayout = React.lazy(() => import('./layouts/DashboardLayout').then((module) => ({ default: module.DashboardLayout })));
const Login = React.lazy(() => import('./features/auth/Login').then((module) => ({ default: module.Login })));
const DashboardPage = React.lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })));
const StockWarehousePage = React.lazy(() => import('./pages/StockWarehousePage').then((module) => ({ default: module.StockWarehousePage })));
const MarketWatchPage = React.lazy(() => import('./pages/MarketWatchPage').then((module) => ({ default: module.MarketWatchPage })));
const SimulatedTradingPage = React.lazy(() => import('./pages/SimulatedTradingPage').then((module) => ({ default: module.SimulatedTradingPage })));
const SettingsPage = React.lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })));
const DataManagerPage = React.lazy(() => import('./pages/DataManagerPage').then((module) => ({ default: module.DataManagerPage })));
const ApiHistoryPage = React.lazy(() => import('./pages/ApiHistoryPage').then((module) => ({ default: module.ApiHistoryPage })));
const AIStockPickerPage = React.lazy(() => import('./pages/AIStockPickerPage').then((module) => ({ default: module.AIStockPickerPage })));
const ExperiencePage = React.lazy(() => import('./pages/ExperiencePage').then((module) => ({ default: module.ExperiencePage })));
const TextLibraryPage = React.lazy(() => import('./texts/TextLibraryPage').then((module) => ({ default: module.TextLibraryPage })));

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isLoggedIn, token } = useSessionStore();
  if (!isLoggedIn || !token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

const App: React.FC = () => {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/login" element={<Login />} />

        <Route path="/" element={
          <ProtectedRoute>
            <DashboardLayout />
          </ProtectedRoute>
        }>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="warehouse" element={<StockWarehousePage />} />
          <Route path="market-watch" element={<MarketWatchPage />} />
          <Route path="data-manager" element={<DataManagerPage />} />
          <Route path="trading" element={<SimulatedTradingPage />} />
          <Route path="ai-stock-picker" element={<AIStockPickerPage />} />
          <Route path="experience" element={<ExperiencePage />} />
          <Route path="texts" element={<TextLibraryPage />} />
          <Route path="api-history" element={<ApiHistoryPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
};

export default App;
