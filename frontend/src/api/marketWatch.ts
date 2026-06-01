import { apiClient } from './client';

export type MarketWatchEventType = 'scan' | 'ai_decision' | 'debate_launched' | 'debate_skipped' | 'error';
export type MarketWatchEventStatus = 'success' | 'skipped' | 'failed';
export type WatchAiDecisionAction = 'ignore' | 'monitor' | 'start_debate';
export type WatchAiDecisionUrgency = 'low' | 'medium' | 'high';
export type WatchAiTradingFrequency = 'day' | 'swing' | 'position';
export type WatchAiTradingStrategy = 'value' | 'trend';

export interface MarketWatchSettings {
  id?: number | null;
  user_id: number;
  auto_scan_enabled: boolean;
  scan_interval_seconds: number;
  scan_non_trading_days: boolean;
  scan_start_time: string;
  scan_end_time: string;
  auto_launch_debate: boolean;
  recent_debate_dedup_enabled: boolean;
  cooldown_minutes: number;
  cooldown_break_confidence: number;
  data_source_urls: string[];
  news_source_urls: string[];
  clean_source_markdown: boolean;
  markdown_cleanup_patterns: string[];
  trading_frequency: string;
  trading_strategy: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export type MarketWatchSettingsUpdate = Partial<
  Omit<MarketWatchSettings, 'user_id' | 'created_at' | 'updated_at'>
>;

export interface MarketWatchMarkdownDocument {
  id: string;
  source_type: 'data' | 'news';
  url: string;
  final_url?: string | null;
  title?: string | null;
  markdown: string;
  status?: number | null;
  error?: string | null;
  captured_at: string;
}

export interface WatchAiDebateParameters {
  trading_frequency: WatchAiTradingFrequency;
  trading_strategy: WatchAiTradingStrategy;
  simplified: boolean;
  debate_focus: string[];
  risk_notes: string[];
}

export interface WatchAiDecision {
  stock_code: string;
  stock_name: string;
  action: WatchAiDecisionAction;
  confidence: number;
  urgency: WatchAiDecisionUrgency;
  trigger_reason: string;
  evidence_summary: string;
  debate_parameters?: WatchAiDebateParameters | null;
}

export interface MarketWatchEvent {
  event_id?: string | null;
  user_id: number;
  event_type: MarketWatchEventType;
  status: MarketWatchEventStatus;
  watch_ai_decision?: WatchAiDecision | WatchAiDecision[] | null;
  debate_parameters?: Record<string, unknown> | null;
  debate_session_id?: string | null;
  task_id?: string | null;
  error_message?: string | null;
  created_at?: string | null;
}

export interface MarketWatchDebateLaunch {
  status: 'not_started' | 'launched' | 'skipped' | 'failed';
  reason?: string;
  stock_code?: string;
  session_id?: string;
  task_id?: string;
  cooldown_broken?: boolean;
  error?: string;
}

export interface MarketWatchScanResponse {
  scanned_at: string;
  settings: MarketWatchSettings;
  stock_count: number;
  data_document_count: number;
  news_count: number;
  ai_evaluated: boolean;
  launched_debate_count: number;
  debate_launch: MarketWatchDebateLaunch;
  debate_launches: MarketWatchDebateLaunch[];
  watch_ai_decision?: WatchAiDecision[] | null;
  data_documents: MarketWatchMarkdownDocument[];
  news_documents: MarketWatchMarkdownDocument[];
  items: Record<string, unknown>[];
}

export interface MarketWatchEventsQuery {
  limit?: number;
  event_type?: MarketWatchEventType;
  since?: string;
}

export type MarketWatchWsMessage = {
  type: 'market_watch_event';
  event: MarketWatchEvent;
  timestamp?: string;
} | {
  type: 'market_watch_documents';
  documents: MarketWatchMarkdownDocument[];
  timestamp?: string;
};

export const marketWatchApi = {
  getSettings: async () => {
    return apiClient.get<MarketWatchSettings>('/market-watch/settings');
  },

  updateSettings: async (payload: MarketWatchSettingsUpdate) => {
    return apiClient.put<MarketWatchSettings>('/market-watch/settings', payload);
  },

  scan: async () => {
    return apiClient.post<MarketWatchScanResponse>('/market-watch/scan');
  },

  getEvents: async (params: MarketWatchEventsQuery = {}) => {
    return apiClient.get<MarketWatchEvent[]>('/market-watch/events', { params });
  },
};
