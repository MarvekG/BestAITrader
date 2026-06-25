import React, { useEffect, useRef, useState } from 'react';
import { AgentCard } from './AgentCard';
import { Empty, Spin, Card, Tabs, Descriptions, Space, Badge, Tag, Avatar } from 'antd';
import { debateApi, PMDecisionRecord } from '../../api/debate';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getRoleConfig } from './roleConfig';

const formatNumber = (value: unknown, precision = 2) => {
  if (typeof value !== 'number') return '0';
  return value.toLocaleString(undefined, {
    maximumFractionDigits: precision,
    minimumFractionDigits: 0,
  });
};

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
  loading?: boolean;
}

export const DebateArena: React.FC<DebateArenaProps> = ({ messages = [], loading = false }) => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('debate');
  const [decisions, setDecisions] = useState<PMDecisionRecord[]>([]);
  const [decisionsLoading, setDecisionsLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (loading && activeTab === 'debate' && messages.length > 0) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [loading, messages, activeTab]);

  useEffect(() => {
    const fetchDecisions = async () => {
      if (activeTab !== 'decision' || messages.length === 0) return;
      const sessionId = messages[0]?.session_id;
      if (!sessionId) return;

      setDecisionsLoading(true);
      try {
        const decisions = await debateApi.getDecisions(sessionId);
        setDecisions(decisions || []);
      } catch (error) {
        console.error('Failed to fetch decisions:', error);
      } finally {
        setDecisionsLoading(false);
      }
    };

    fetchDecisions();
  }, [activeTab, messages]);

  const renderDecisionContent = () => {
    if (decisionsLoading) {
      return (
        <Spin tip={t('debate.loading')}>
          <div className="p-12" />
        </Spin>
      );
    }

    if (decisions.length === 0) {
      return <Empty description={t('debate.no_decision_data')} />;
    }

    return (
      <div className="p-4 overflow-y-auto" style={{ height: 'calc(100vh - 350px)' }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          {decisions.map((decision) => {
            const confidenceColor = decision.confidence >= 0.7
              ? '#52c41a'
              : decision.confidence >= 0.5 ? '#faad14' : '#f5222d';
            const roleConfig = getRoleConfig(decision.agent_role || 'pm', t);
            const formatPrice = (value?: number | null) => (value && value > 0 ? `¥${formatNumber(value)}` : 'N/A');

            return (
              <Card
                key={decision.id}
                title={
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <Avatar size="small" icon={roleConfig.icon} style={{ backgroundColor: roleConfig.color }} />
                      <span style={{ color: roleConfig.color, fontWeight: 'bold' }}>{roleConfig.title}</span>
                      <span style={{ marginLeft: 4, color: 'var(--app-text-secondary)', fontSize: '12px' }}>
                        {new Date(decision.created_at).toLocaleString('zh-CN')}
                      </span>
                    </div>
                    <Tag color={confidenceColor}>
                      {t('debate.confidence')}: {(decision.confidence * 100).toFixed(0)}%
                    </Tag>
                  </div>
                }
                className="bg-gray-800 border-gray-700"
                style={{ borderLeft: `4px solid ${roleConfig.color}` }}
              >
                <Descriptions column={2} size="small" bordered>
                  <Descriptions.Item label={t('debate.target_position')} span={2}>
                    {(decision.target_position * 100).toFixed(0)}%
                  </Descriptions.Item>
                  <Descriptions.Item label={t('debate.decision_details')} span={2}>
                    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                      <Descriptions column={3} size="small" bordered>
                        <Descriptions.Item label={t('debate.stop_loss')}>
                          {formatPrice(decision.stop_loss)}
                        </Descriptions.Item>
                        <Descriptions.Item label={t('debate.take_profit')}>
                          {formatPrice(decision.take_profit)}
                        </Descriptions.Item>
                        <Descriptions.Item label={t('debate.analysis.holding_period')}>
                          {decision.holding_horizon_days ? `${decision.holding_horizon_days}D` : 'N/A'}
                        </Descriptions.Item>
                      </Descriptions>
                      <div className="decision-markdown-container">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {decision.reasoning || ''}
                        </ReactMarkdown>
                      </div>
                    </Space>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            );
          })}
        </Space>
      </div>
    );
  };

  const items = [
    {
      key: 'debate',
      label: t('debate.realtime_debate'),
      children: (
        <div style={{ height: 'calc(100vh - 300px)', overflowY: 'auto', padding: 16 }}>
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
            <div style={{ textAlign: 'center', padding: 20 }}>
              <Spin tip={t('debate.loading')}>
                <div style={{ padding: 20 }} />
              </Spin>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      ),
    },
    {
      key: 'decision',
      label: (
        <Space>
          {t('debate.decision_tab')}
          {decisions.length > 0 && <Badge count={decisions.length} style={{ backgroundColor: '#52c41a' }} />}
        </Space>
      ),
      children: renderDecisionContent(),
    },
  ];

  return (
    <div className="debate-container" style={{ height: '100%', background: 'var(--app-bg-layout)' }}>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={items}
        centered
        className="custom-debate-tabs"
        style={{ color: 'var(--app-text)' }}
      />

      <style>{`
        .custom-debate-tabs .ant-tabs-nav {
          margin-bottom: 0 !important;
          background: var(--app-bg-container) !important;
          border-bottom: 1px solid var(--app-border);
        }
        .custom-debate-tabs .ant-tabs-tab {
          padding: 12px 24px !important;
          margin-left: 0 !important;
        }
        .custom-debate-tabs .ant-tabs-tab-active .ant-tabs-tab-btn {
          color: var(--app-primary) !important;
          font-weight: bold;
        }
        .decision-markdown-container {
          color: var(--app-text);
          background: var(--app-bg-elevated);
          padding: 16px;
          border-radius: 8px;
          font-size: 14px;
          line-height: 1.6;
        }
        .decision-markdown-container h1, .decision-markdown-container h2, .decision-markdown-container h3 {
          color: var(--app-primary);
          margin-top: 16px;
          margin-bottom: 8px;
        }
        .decision-markdown-container p {
          margin-bottom: 12px;
        }
        .decision-markdown-container ul, .decision-markdown-container ol {
          padding-left: 20px;
          margin-bottom: 12px;
        }
        .decision-markdown-container blockquote {
          border-left: 4px solid var(--app-primary);
          color: var(--app-text-secondary);
          font-style: italic;
          background: var(--app-bg-muted);
          margin: 12px 0;
          padding: 8px 12px;
        }
      `}</style>
    </div>
  );
};
