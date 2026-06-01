import { apiClient } from './client';

export interface DebateThread {
  id: string; // Map from message_id in backend
  message_id?: string;
  session_id: string;
  stage?: string;
  round_number: number;
  role?: string;
  agent_role: string;
  agent_name?: string;
  speaker_role?: string; // Legacy
  content?: string; // Legacy
  reasoning: string;
  reasoning_chain?: Record<string, unknown>;
  analysis?: Record<string, unknown>;
  prompt_input?: string;
  timestamp: string; // Map from created_at
  created_at?: string;
}

export interface PMDecision {
  id: string;
  session_id: string;
  action: 'buy' | 'sell' | 'hold';
  confidence: number;
  target_position: number;
  reasoning: string;
  execution_plan?: {
    entry_strategy: string;
    exit_strategy: string;
    risk_mitigation: string;
  };
  status: 'pending' | 'executed' | 'rejected';
  agent_role?: string;
  created_at: string;
}

export type Decision = PMDecision;

export const debateApi = {
  run: (data: {
    session_id: string;
    stock_code: string;
    simplified?: boolean;
    trading_frequency: string;
    trading_strategy: string;
  }) =>
    apiClient.post('/debate/run', data, { timeout: 1800000 }),

  getHistory: (sessionId: string) =>
    apiClient.get<DebateThread[]>(`/debate/threads/${sessionId}`),

  getDecisions: (sessionId: string) =>
    apiClient.get<PMDecision[]>(`/debate/decisions/${sessionId}`),
};
