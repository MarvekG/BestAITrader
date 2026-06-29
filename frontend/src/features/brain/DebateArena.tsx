import React, { useEffect, useRef } from 'react';
import { Spin } from 'antd';
import { useTranslation } from 'react-i18next';
import { AgentCard } from './AgentCard';

export interface DebateMessage {
  message_id: string;
  session_id: string;
  stage: string;
  round_number: number;
  agent_name: string;
  agent_role: string;
  reasoning: string;
  created_at: string;
}

interface DebateArenaProps {
  messages: DebateMessage[];
  sessionId?: string;
  loading?: boolean;
}

export const DebateArena: React.FC<DebateArenaProps> = ({ messages = [], sessionId, loading = false }) => {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);
  void sessionId;

  useEffect(() => {
    if (loading && messages.length > 0) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [loading, messages]);

  return (
    <div className="debate-container" style={{ height: '100%', background: 'var(--app-bg-layout)' }}>
      <div style={{ height: 'calc(100vh - 240px)', overflowY: 'auto', padding: '8px 12px' }}>
        {messages.map((msg) => {
          const content = (msg.reasoning || '').replace(/(步骤\d+[:：][^→]+)(→)/g, '$1  \n  $2');
          return (
            <AgentCard
              key={msg.message_id || `${msg.agent_role}-${msg.round_number}-${msg.created_at}`}
              role={msg.agent_role}
              content={content || t('brain.no_content')}
              timestamp={msg.created_at}
              round={msg.round_number}
            />
          );
        })}
        {loading && (
          <div style={{ textAlign: 'center', padding: 12 }}>
            <Spin tip={t('debate.loading')}>
              <div style={{ padding: 12 }} />
            </Spin>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};
