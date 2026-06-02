import { useEffect, useRef } from 'react';

import { WebSocketEventType, WebSocketMessage, wsManager } from '../services/websocket';

type WebSocketMessageHandler = (message: WebSocketMessage) => void;

export function useWebSocketSubscription(type: WebSocketEventType, handler: WebSocketMessageHandler) {
  const handlerRef = useRef(handler);

  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  useEffect(() => {
    const stableHandler: WebSocketMessageHandler = (message) => {
      handlerRef.current(message);
    };

    wsManager.subscribe(type, stableHandler);
    return () => {
      wsManager.unsubscribe(type, stableHandler);
    };
  }, [type]);
}

export function useResourceSubscription(
  type: WebSocketEventType,
  resourceId: string | null | undefined,
  handler: WebSocketMessageHandler,
) {
  const handlerRef = useRef(handler);

  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  useEffect(() => {
    if (!resourceId) {
      return undefined;
    }

    const stableHandler: WebSocketMessageHandler = (message) => {
      handlerRef.current(message);
    };

    wsManager.subscribeResource(type, resourceId, stableHandler);
    return () => {
      wsManager.unsubscribeResource(type, resourceId, stableHandler);
    };
  }, [resourceId, type]);
}
