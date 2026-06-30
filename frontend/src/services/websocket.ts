import { websocketTicketApi } from '../api/websocketTicket';

export type WebSocketMessage = {
  type: string;
  [key: string]: unknown;
};

export interface TaskCompletedData {
  task_id?: string;
  task_name?: string;
  status?: string;
  result?: {
    progress?: number;
    total?: number;
    current_step?: string;
    [key: string]: unknown;
  };
  error_message?: string;
  error?: string;
  [key: string]: unknown;
}

export type TaskCompletedMessage = WebSocketMessage & {
  data?: TaskCompletedData;
};

export interface InteractiveStockPickerUpdateData {
  run_id?: string;
  stage?: string;
  status?: string;
  message?: string;
  payload?: Record<string, unknown>;
  timestamp?: string;
}

export type InteractiveStockPickerUpdateMessage = WebSocketMessage & {
  data?: InteractiveStockPickerUpdateData;
};

type MessageHandler = (message: WebSocketMessage) => void;

export const BACKEND_EVENT_TYPES = {
  task_completed: 'task_completed',
  interactive_stock_picker_update: 'interactive_stock_picker',
  price_update: 'price',
  experience_review_update: 'experience_review',
  position_update: 'position_update',
  order_status: 'order_status',
  trade_executed: 'trade_executed',
} as const;

export type WebSocketEventType = keyof typeof BACKEND_EVENT_TYPES;

class WebSocketManager {
  private ws: WebSocket | null = null;
  private url: string = '';
  private reconnectInterval: number = 3000;
  private handlers: Map<string, Set<MessageHandler>> = new Map();
  private sessionId: string = '';
  private shouldReconnect: boolean = true;
  private heartbeatTimer: number | null = null;
  private resubmitSubscriptions: Set<string> = new Set(); // Track backend subscriptions
  private resubmitResourceSubscriptions: Map<string, Set<string>> = new Map();

  constructor() { }

  private getBackendEventType(type: WebSocketEventType): string {
    return BACKEND_EVENT_TYPES[type];
  }

  private addHandler(type: string, handler: MessageHandler) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)?.add(handler);
  }

  connect(sessionId: string) {
    if (!sessionId) return;

    // If already connected to the same session with OPEN connection, do nothing
    if (this.ws?.readyState === WebSocket.OPEN && this.sessionId === sessionId) return;

    // If already connecting/connected to the same session, don't restart
    if (this.ws && this.sessionId === sessionId &&
      (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }

    // If connected to a DIFFERENT session, disconnect first
    if (this.ws && this.sessionId !== sessionId) {
      this.disconnect();
    }

    this.sessionId = sessionId;
    this.shouldReconnect = true;

    void this.openConnection(sessionId);
  }

  private async openConnection(sessionId: string) {
    try {
      const ticketResponse = await websocketTicketApi.createGlobal(sessionId);
      if (!this.shouldReconnect || this.sessionId !== sessionId) {
        return;
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      // Through Nginx proxy or direct access, use the current host (includes port if present)
      this.url = `${protocol}//${window.location.host}/ws/${sessionId}?ticket=${encodeURIComponent(ticketResponse.ticket)}`;

      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        console.log(`WebSocket connected to session: ${sessionId}`);
        this.startHeartbeat();

        // Re-submit subscriptions after reconnection
        if (this.resubmitSubscriptions.size > 0) {
          console.log(`Re-submitting ${this.resubmitSubscriptions.size} subscriptions...`);
          this.resubmitSubscriptions.forEach(eventType => {
            this.send({ type: 'subscribe', event_type: eventType });
          });
        }
        this.resubmitResourceSubscriptions.forEach((resourceIds, eventType) => {
          resourceIds.forEach(resourceId => {
            this.send({ type: 'subscribe', event_type: eventType, resource_id: resourceId });
          });
        });
      };

      this.ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          this.dispatch(message);
        } catch (e) {
          console.error('WebSocket message parse error:', e);
        }
      };

      this.ws.onclose = () => {
        this.stopHeartbeat();
        if (this.shouldReconnect) {
          console.log('WebSocket disconnected, reconnecting...');
          setTimeout(() => {
            if (this.shouldReconnect) {
              void this.openConnection(this.sessionId);
            }
          }, this.reconnectInterval);
        } else {
          console.log('WebSocket disconnected (user initiated)');
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        this.ws?.close();
      };
    } catch (error) {
      console.error('Failed to create WebSocket ticket:', error);
      if (this.shouldReconnect && this.sessionId === sessionId) {
        setTimeout(() => {
          if (this.shouldReconnect) {
            void this.openConnection(sessionId);
          }
        }, this.reconnectInterval);
      }
    }
  }

  disconnect() {
    this.shouldReconnect = false;
    this.stopHeartbeat();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.sessionId = '';
  }

  subscribe(type: WebSocketEventType, handler: MessageHandler) {
    this.addHandler(type, handler);

    const backendEventType = this.getBackendEventType(type);
    this.resubmitSubscriptions.add(backendEventType);

    if (this.isConnected()) {
      this.send({ type: 'subscribe', event_type: backendEventType });
    }
  }

  subscribeResource(type: WebSocketEventType, resourceId: string, handler: MessageHandler) {
    this.addHandler(type, handler);

    const backendEventType = this.getBackendEventType(type);
    if (!resourceId) {
      return;
    }
    if (!this.resubmitResourceSubscriptions.has(backendEventType)) {
      this.resubmitResourceSubscriptions.set(backendEventType, new Set());
    }
    this.resubmitResourceSubscriptions.get(backendEventType)?.add(resourceId);

    if (this.isConnected()) {
      this.send({ type: 'subscribe', event_type: backendEventType, resource_id: resourceId });
    }
  }

  unsubscribe(type: WebSocketEventType, handler: MessageHandler) {
    this.handlers.get(type)?.delete(handler);
  }

  unsubscribeResource(type: WebSocketEventType, resourceId: string, handler: MessageHandler) {
    this.handlers.get(type)?.delete(handler);

    const backendEventType = this.getBackendEventType(type);
    if (!resourceId) {
      return;
    }
    const resourceIds = this.resubmitResourceSubscriptions.get(backendEventType);
    resourceIds?.delete(resourceId);
    if (resourceIds && resourceIds.size === 0) {
      this.resubmitResourceSubscriptions.delete(backendEventType);
    }

    if (this.isConnected()) {
      this.send({ type: 'unsubscribe', event_type: backendEventType, resource_id: resourceId });
    }
  }

  private dispatch(message: WebSocketMessage) {
    // Dispatch to specific handlers based on message type (e.g., 'price_update', 'debate_update')
    const handlers = this.handlers.get(message.type);
    handlers?.forEach(handler => handler(message));

  }

  private startHeartbeat() {
    this.stopHeartbeat();
    // Send a ping every 30 seconds to keep connection alive
    this.heartbeatTimer = window.setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  send(message: Record<string, unknown>) {
    if (this.isConnected()) {
      this.ws?.send(JSON.stringify(message));
    }
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  getSessionId(): string {
    return this.sessionId;
  }

  async waitForConnection(timeoutMs: number = 3000): Promise<boolean> {
    if (this.isConnected()) {
      return true;
    }

    if (this.sessionId) {
      this.connect(this.sessionId);
    }

    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
      if (this.isConnected()) {
        return true;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    return this.isConnected();
  }
}

export const wsManager = new WebSocketManager();
