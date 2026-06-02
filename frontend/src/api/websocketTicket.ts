import { getAuthToken } from '../services/authSession';

type WebSocketTicketResponse = {
  ticket: string;
  expires_in: number;
};

const postRootTicket = async (path: string): Promise<WebSocketTicketResponse> => {
  const token = getAuthToken() || '';
  const response = await fetch(path, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!response.ok) {
    throw new Error(`Failed to create WebSocket ticket: ${response.status}`);
  }
  return response.json() as Promise<WebSocketTicketResponse>;
};

export const websocketTicketApi = {
  createGlobal: (sessionId: string) =>
    postRootTicket(`/ws-ticket/${encodeURIComponent(sessionId)}`),

  createDebate: (sessionId: string) =>
    postRootTicket(`/api/v1/debate/ws-ticket/${encodeURIComponent(sessionId)}`),

  createMarketWatch: () =>
    postRootTicket('/api/v1/market-watch/ws-ticket'),
};
