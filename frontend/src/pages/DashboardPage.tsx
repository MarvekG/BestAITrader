import React, { useState, useEffect, useRef } from 'react';
import { Row, Col, Card, Button, App as AntdApp, Tabs, Badge, Tooltip, theme, Spin } from 'antd';
import { DebateArena, DebateMessage } from '../features/brain/DebateArena';
import { DecisionAuditLog } from '../features/brain/DecisionAuditLog';

import { useSessionStore } from '../store/useSessionStore';
import { debateApi } from '../api/debate';
import { sessionApi } from '../api/session';
import { websocketTicketApi } from '../api/websocketTicket';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getApiErrorMessage } from '../utils/errorUtils';

const debateProgressMessageKey = 'dashboard_debate_progress';
const debateSessionsRefreshEvent = 'debate-sessions-refresh';

export const DashboardPage: React.FC = () => {
  const { t } = useTranslation();
  const {
    token: { colorText, colorTextSecondary },
  } = theme.useToken();
  const { activeSession, setActiveSession, clearActiveSession } = useSessionStore();
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const routeSessionId = searchParams.get('session_id');
  const [loading, setLoading] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionLoadFailed, setSessionLoadFailed] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [messageCount, setMessageCount] = useState(0);
  const [debateMessages, setDebateMessages] = useState<DebateMessage[]>([]);
  const [isDebateCompleted, setIsDebateCompleted] = useState(false);
  const [activeDashboardTab, setActiveDashboardTab] = useState('1');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const maxReconnectAttempts = 5;
  const heartbeatIntervalRef = useRef<ReturnType<typeof window.setInterval> | null>(null);
  const activeSessionId = activeSession?.session_id;
  const isRouteSessionActive = Boolean(routeSessionId && activeSessionId === routeSessionId);

  useEffect(() => {
    if (!routeSessionId) {
      clearActiveSession();
      setSessionLoadFailed(false);
      setSessionLoading(false);
      return;
    }

    if (activeSessionId === routeSessionId) {
      setSessionLoadFailed(false);
      setSessionLoading(false);
      return;
    }

    let cancelled = false;

    const loadSessionFromRoute = async () => {
      setSessionLoading(true);
      setSessionLoadFailed(false);
      try {
        const session = await sessionApi.get(routeSessionId);
        if (!cancelled) {
          setActiveSession(session);
        }
      } catch (error) {
        if (!cancelled) {
          clearActiveSession();
          setSessionLoadFailed(true);
          const errorMsg = getApiErrorMessage(error, t('session.load_failed'));
          message.error(errorMsg);
        }
      } finally {
        if (!cancelled) {
          setSessionLoading(false);
        }
      }
    };

    loadSessionFromRoute();

    return () => {
      cancelled = true;
    };
  }, [activeSessionId, clearActiveSession, message, routeSessionId, setActiveSession, t]);

  useEffect(() => {
    setLoading(false);
    setWsConnected(false);
    setMessageCount(0);
    setDebateMessages([]);
    setIsDebateCompleted(false);
  }, [activeSessionId]);

  const refreshActiveSession = React.useCallback(async () => {
    if (!activeSessionId) {
      return;
    }
    try {
      const session = await sessionApi.get(activeSessionId);
      setActiveSession(session);
      window.dispatchEvent(new CustomEvent(debateSessionsRefreshEvent));
    } catch (error) {
      console.error('Failed to refresh active session:', error);
    }
  }, [activeSessionId, setActiveSession]);

  // WebSocket连接（增强版：支持自动重连和心跳）
  useEffect(() => {
    if (!isRouteSessionActive || !activeSessionId) return;

    let ws: WebSocket | null = null;
    let shouldReconnect = true;

    // 清除心跳定时器
    const clearHeartbeat = () => {
      if (heartbeatIntervalRef.current) {
        clearInterval(heartbeatIntervalRef.current);
        heartbeatIntervalRef.current = null;
      }
    };

    // 启动心跳
    const startHeartbeat = () => {
      clearHeartbeat();
      heartbeatIntervalRef.current = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send('ping');
          console.log('💓 Heartbeat sent');
        }
      }, 30000); // 每30秒发送一次心跳
    };

    const connect = async () => {
      try {
        const ticketResponse = await websocketTicketApi.createDebate(activeSessionId);
        if (!shouldReconnect) {
          return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/v1/debate/ws/${activeSessionId}?ticket=${encodeURIComponent(ticketResponse.ticket)}`;
        console.log(`🔌 Attempting WebSocket connection (attempt ${reconnectAttemptsRef.current + 1}):`, wsUrl);
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          console.log('✅ WebSocket connected successfully');
          setWsConnected(true);
          reconnectAttemptsRef.current = 0; // 重置重连次数
          startHeartbeat(); // 启动心跳
          if (reconnectAttemptsRef.current === 0) {
            message.success(t('common.success'));
          } else {
            message.success('Reconnected successfully');
          }
        };

        ws.onmessage = (event) => {
          const data = JSON.parse(event.data);

          if (data.type === 'debate_message') {
            setLoading(true);
            setMessageCount(prev => prev + 1);
            setDebateMessages(prev => [...prev, data.data]);
          } else if (data.type === 'history') {
            if (data.messages && Array.isArray(data.messages)) {
              setDebateMessages(data.messages);
              setMessageCount(data.messages.length);
            }
          } else if (data.type === 'debate_status') {
            if (data.status === 'started') {
              setLoading(true);
              setIsDebateCompleted(false);
              message.loading({ content: t('dashboard.messages.debate_started'), key: debateProgressMessageKey, duration: 0 });
              setDebateMessages([]);
              setMessageCount(0);
            } else if (data.status === 'completed') {
              message.success({ content: t('dashboard.messages.debate_completed'), key: debateProgressMessageKey });
              setLoading(false);
              setIsDebateCompleted(true);
              void refreshActiveSession();
            } else if (data.status === 'error') {
              message.error({ content: t('dashboard.messages.debate_error'), key: debateProgressMessageKey });
              setLoading(false);
              void refreshActiveSession();
            }
          } else if (data.type === 'connection') {
            console.log('Connection established:', data.message);
          } else if (data.type === 'pong') {
            console.log('💓 Heartbeat received');
          }
        };

        ws.onerror = (error) => {
          if (ws?.readyState === WebSocket.CLOSING || ws?.readyState === WebSocket.CLOSED) {
            return;
          }
          console.error('❌ WebSocket error:', error);
          setWsConnected(false);
        };

        ws.onclose = (event) => {
          console.log(`🔌 WebSocket closed. Code: ${event.code}, Reason: ${event.reason || 'No reason'}`);
          setWsConnected(false);
          clearHeartbeat();

          // 自动重连（除非是正常关闭或达到最大重连次数）
          if (shouldReconnect && event.code !== 1000 && reconnectAttemptsRef.current < maxReconnectAttempts) {
            const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 10000);
            console.log(`⏳ Reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current + 1}/${maxReconnectAttempts})`);

            setTimeout(() => {
              if (shouldReconnect) {
                reconnectAttemptsRef.current += 1;
                void connect();
              }
            }, delay);
          } else if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
            console.error('❌ Max reconnection attempts reached');
            message.error(t('common.error'));
          }
        };

        wsRef.current = ws;

      } catch (error) {
        console.error('Failed to create WebSocket:', error);
        setWsConnected(false);
      }
    };

    // Debounce connection to avoid React Strict Mode double-mount race condition
    const connectTimer = setTimeout(() => {
      void connect();
    }, 300);

    return () => {
      shouldReconnect = false;
      clearTimeout(connectTimer);
      clearHeartbeat();
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      }
    };
  }, [activeSessionId, isRouteSessionActive, message, refreshActiveSession, t]);

  // Check if debate is already completed when session changes
  useEffect(() => {
    const checkDebateStatus = async () => {
      if (isRouteSessionActive && activeSession) {
        try {
          const decisions = await debateApi.getDecisions(activeSession.session_id);
          setIsDebateCompleted(decisions && decisions.length > 0);
        } catch (error) {
          console.error("Failed to check debate status:", error);
        }
      }
    };
    checkDebateStatus();
  }, [activeSession, isRouteSessionActive]);

  const handleStartDebate = async () => {
    if (!activeSession) return;

    // 检查 WebSocket 连接状态
    if (!wsConnected || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      message.warning('WebSocket not connected. Please wait for connection to establish...');
      console.warn('⚠️ WebSocket not ready. State:', wsRef.current?.readyState);
      return;
    }

    setLoading(true);
    try {
      await debateApi.run({
        session_id: activeSession.session_id,
        stock_code: activeSession.stock_code,
        simplified: false,
        trading_frequency: activeSession.trading_frequency,
        trading_strategy: activeSession.trading_strategy,
      });

      message.loading({ content: t('dashboard.messages.request_sent'), key: debateProgressMessageKey, duration: 0 });
      // 注意: 这里不设置setLoading(false), 而是等待WebSocket通知完成
    } catch (error) {
      console.error(error);
      const errorMsg = getApiErrorMessage(error, t('common.error'));
      message.error({ content: errorMsg, key: debateProgressMessageKey });
      setLoading(false); // 只有API调用失败时才在这里重置
    }
  };

  if (routeSessionId && !sessionLoadFailed && (sessionLoading || !activeSession || !isRouteSessionActive)) {
    return (
      <div style={{
        height: '100%',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
      }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!isRouteSessionActive || !activeSession) {
    return (
      <div style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        color: colorText
      }}>
        <h1 className="text-3xl font-bold mb-4">{t('dashboard.welcome_title')}</h1>
        <p className="text-gray-400 mb-8" style={{ color: colorTextSecondary }}>{t('dashboard.welcome_desc')}</p>
        <Button type="primary" size="large" onClick={() => navigate('/warehouse')}>
          {t('dashboard.go_to_warehouse')}
        </Button>
      </div>
    );
  }

  return (
    <div style={{ height: '100vh', padding: 16 }}>
      <Row gutter={16} style={{ height: '100%' }}>
        {/* AI Brain - Full Width */}
        <Col span={24} style={{ height: '100%' }}>
          <Card
            title={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>{`${t('dashboard.ai_decision_hall')} - ${activeSession.stock_name} (${activeSession.stock_code})`}</span>
                <Badge
                  status={wsConnected ? "success" : "default"}
                  text={wsConnected ? "Live" : "Offline"}
                />
                <Badge
                  status={activeSession.status === 'active' ? 'processing' : 'default'}
                  text={activeSession.status === 'active' ? t('dashboard.status_active') : t('dashboard.status_completed')}
                  style={{ marginLeft: 8, fontSize: 12, opacity: 0.8 }}
                />
              </div>
            }
            extra={
              <Tooltip title={isDebateCompleted ? t('dashboard.debate_completed') : (activeSession.status !== 'active' ? t('dashboard.session_not_active') : t('dashboard.start_debate'))}>
                <Button
                  type="primary"
                  loading={loading}
                  disabled={activeSession.status !== 'active' || isDebateCompleted}
                  onClick={handleStartDebate}
                >
                  {t('dashboard.start_debate')}
                </Button>
              </Tooltip>
            }
            style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
            styles={{ body: { flex: 1, padding: 0, minHeight: 0 } }}
          >
            <Tabs
              activeKey={activeDashboardTab}
              onChange={setActiveDashboardTab}
              size="large"
              style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
              tabBarStyle={{ paddingLeft: 16 }}
              items={[
                {
                  key: '1',
                  label: messageCount > 0 ? `${t('dashboard.live_debate')} (${messageCount})` : t('dashboard.live_debate'),
                  children: (
                    <div style={{ minHeight: 'calc(100vh - 300px)' }}>
                      <DebateArena
                        messages={debateMessages}
                        sessionId={activeSession.session_id}
                        loading={loading}
                      />
                    </div>
                  )
                },
                { key: '2', label: t('dashboard.decision_audit'), children: <div style={{ minHeight: 'calc(100vh - 300px)' }}><DecisionAuditLog isActive={activeDashboardTab === '2'} /></div> }
              ]}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};
