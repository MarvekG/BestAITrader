import React, { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Avatar, Button, Card, Descriptions, Dropdown, Empty, Space, Spin, Steps, Tag, Typography } from 'antd';
import { useSessionStore } from '../../store/useSessionStore';
import { DebateThread, PMDecisionRecord, debateApi } from '../../api/debate';
import { sessionApi, Session } from '../../api/session';
import { AuditOutlined, BarChartOutlined, MessageOutlined, FileSearchOutlined, RobotOutlined, ReloadOutlined, ExportOutlined, DownOutlined, CopyOutlined, LeftOutlined, RightOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { getRoleConfig } from './roleConfig';
import { DebateMarkdown } from './DebateMarkdown';

import type { MenuProps } from 'antd';

const { Text } = Typography;

interface AuditMessage {
  id: string;
  session_id: string;
  round_number: number;
  speaker_role: string;
  content: string;
  prompt_input?: string;
  timestamp: string;
  agent_role?: string;
  stage?: string;
  pmDecision?: PMDecisionRecord;
}

export interface DecisionAuditLogProps {
  sessionId?: string;
  isActive?: boolean;
}

const isInfoAnalystMessage = (msg: AuditMessage) =>
  msg.stage === 'news_analysis' ||
  msg.stage === 'policy_analysis' ||
  msg.stage === 'sentiment_analysis';

const getStepMessagesFor = (allMessages: AuditMessage[], stepIndex: number) => {
  switch (stepIndex) {
    case 0:
      return allMessages.filter(isInfoAnalystMessage);
    case 1:
      return allMessages.filter(m =>
        m.stage === 'vertical_analysis'
      );
    case 2:
      return allMessages.filter(m => m.stage === 'strategic_round_1');
    case 3:
      return allMessages.filter(m =>
        m.stage === 'strategic_round_2_1' || m.stage === 'strategic_round_2_2'
      );
    case 4:
      return allMessages.filter(m => m.stage === 'fact_arbitration');
    case 5:
      return allMessages.filter(m => m.stage === 'portfolio_management');
    default:
      return [];
  }
};

const formatPercent = (value: number) => `${(value * 100).toFixed(0)}%`;
const formatPrice = (value?: number | null) => (value && value > 0 ? `¥${value.toFixed(2)}` : 'N/A');

export const DecisionAuditLog: React.FC<DecisionAuditLogProps> = ({ sessionId, isActive = true }) => {
  const { t } = useTranslation();
  const { message } = AntdApp.useApp();
  const { activeSession } = useSessionStore();
  const [localSession, setLocalSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<AuditMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [activeCardIndex, setActiveCardIndex] = useState(0);

  const effectiveSessionId = sessionId || activeSession?.session_id;

  const fetchData = useCallback(async () => {
    if (!effectiveSessionId) return;
    setLoading(true);
    try {
      // Determine which session object to use for metadata
      if (activeSession && activeSession.session_id === effectiveSessionId) {
        setLocalSession(activeSession);
      } else if (effectiveSessionId) {
        try {
          const sessionData = await sessionApi.get(effectiveSessionId);
          setLocalSession(sessionData);
        } catch (e) {
          console.error("Failed to fetch session details:", e);
        }
      }

      const [threads, pmDecisions] = await Promise.all([
        debateApi.getHistory(effectiveSessionId),
        debateApi.getDecisions(effectiveSessionId),
      ]);

      const auditMessages: AuditMessage[] = threads.map((msg: DebateThread) => ({
        id: msg.id || Math.random().toString(),
        session_id: effectiveSessionId,
        round_number: msg.round_number,
        speaker_role: msg.role || msg.agent_role || msg.speaker_role || 'unknown',
        agent_role: msg.agent_role || msg.role || msg.speaker_role || 'unknown',
        content: msg.content || msg.reasoning,
        prompt_input: msg.prompt_input || "",
        timestamp: msg.timestamp,
        stage: msg.stage || 'unknown',
      }));

      const pmMessages = auditMessages.filter(msg => msg.stage === 'portfolio_management');
      pmMessages.forEach((msg, index) => {
        msg.pmDecision = (pmDecisions || [])[index];
      });
      for (const decision of pmDecisions || []) {
        if (pmMessages.some(msg => msg.pmDecision?.id === decision.id)) continue;
        auditMessages.push({
          id: decision.id,
          session_id: decision.session_id,
          round_number: 0,
          speaker_role: decision.agent_role || 'portfolio_manager',
          agent_role: decision.agent_role || 'portfolio_manager',
          content: decision.reasoning || '',
          timestamp: decision.created_at,
          stage: 'portfolio_management',
          pmDecision: decision,
        });
      }

      setMessages(auditMessages);

      // 默认选中最新的有数据的步骤
      if (getStepMessagesFor(auditMessages, 5).length > 0) setCurrentStep(5);
      else if (getStepMessagesFor(auditMessages, 4).length > 0) setCurrentStep(4);
      else if (getStepMessagesFor(auditMessages, 3).length > 0) setCurrentStep(3);
      else if (getStepMessagesFor(auditMessages, 2).length > 0) setCurrentStep(2);
      else if (getStepMessagesFor(auditMessages, 1).length > 0) setCurrentStep(1);
      else if (getStepMessagesFor(auditMessages, 0).length > 0) setCurrentStep(0);

    } catch (error) {
      console.error('Failed to fetch audit data:', error);
    } finally {
      setLoading(false);
    }
  }, [activeSession, effectiveSessionId]);

  useEffect(() => {
    if (!isActive) return;
    void fetchData();
  }, [fetchData, isActive]);

  const generateMarkdown = (exportType: 'decision' | 'all') => {
    let reportTitle = t('session.report_title');
    const sessionToUse = localSession || activeSession;
    if (sessionToUse?.stock_name || sessionToUse?.stock_code) {
      reportTitle += ` - ${sessionToUse?.stock_name || ''} (${sessionToUse?.stock_code || ''})`;
    }
    let mdContent = `# ${reportTitle}\n\n`;
    mdContent += `**Date:** ${new Date().toLocaleString()}\n`;
    mdContent += `\n---\n\n`;

    const msgsToExport = exportType === 'decision'
      ? messages.filter(m => m.stage === 'portfolio_management')
      : messages;

    msgsToExport.forEach(msg => {
      const roleConfig = getRoleConfig(msg.agent_role || 'pm', t);
      mdContent += `## ${roleConfig.title} (Round ${msg.round_number})\n\n`;
      mdContent += `*Time: ${new Date(msg.timestamp).toLocaleString()}*\n\n`;

      if (msg.content) {
        mdContent += `${msg.content}\n\n`;
      }
      if (msg.pmDecision) {
        mdContent += `### ${t('debate.decision_tab')}\n\n`;
        mdContent += `- **${t('debate.confidence')}:** ${formatPercent(msg.pmDecision.confidence)}\n`;
        mdContent += `- **${t('debate.target_position')}:** ${formatPercent(msg.pmDecision.target_position)}\n`;
        mdContent += `- **${t('debate.stop_loss')}:** ${formatPrice(msg.pmDecision.stop_loss)}\n`;
        mdContent += `- **${t('debate.take_profit')}:** ${formatPrice(msg.pmDecision.take_profit)}\n`;
        mdContent += `- **${t('debate.analysis.holding_period')}:** ${msg.pmDecision.holding_horizon_days || 'N/A'}\n\n`;
      }

      mdContent += `---\n\n`;
    });

    return mdContent;
  };

  const handleExport = (exportType: 'decision' | 'all') => {
    const content = generateMarkdown(exportType);
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    const sessionToUse = localSession || activeSession;
    const stockCode = sessionToUse?.stock_code || 'Report';
    const stockName = sessionToUse?.stock_name ? `${sessionToUse.stock_name}_` : '';
    const dateStr = new Date().toISOString().split('T')[0];
    const fileName = `Decision_${stockName}${stockCode}_${dateStr}.md`;
    link.setAttribute('download', fileName);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const exportMenuItems: MenuProps['items'] = [
    {
      key: 'decision',
      label: t('debate.export_decision_only'),
      onClick: () => handleExport('decision'),
    },
    {
      key: 'all',
      label: t('debate.export_all_debate'),
      onClick: () => handleExport('all'),
    },
  ];

  const STAGES = [
    { key: 'news', title: t('debate.analysis.sentiment_surface'), icon: <FileSearchOutlined />, stages: ['news_analysis', 'policy_analysis', 'sentiment_analysis'] },
    { key: 'expert', title: t('debate.technical'), icon: <RobotOutlined />, stages: ['vertical_analysis'] },
    { key: 'strategic', title: t('debate.analysis.synthesis_title'), icon: <BarChartOutlined />, stages: ['strategic_round_1'] },
    { key: 'cross', title: t('debate.analysis.cross_analysis_title'), icon: <MessageOutlined />, stages: ['strategic_round_2_1', 'strategic_round_2_2'] },
    { key: 'fact_arbitration', title: t('debate.analysis.fact_arbitration_title', { defaultValue: '事实仲裁' }), icon: <AuditOutlined />, stages: ['fact_arbitration'] },
    { key: 'decision', title: t('debate.decision_tab'), icon: <AuditOutlined />, stages: ['portfolio_management'] },
  ];

  const getStepMessages = (stepIndex: number) => {
    return getStepMessagesFor(messages, stepIndex);
  };

  const currentStepMessages = getStepMessages(currentStep);

  useEffect(() => {
    setActiveCardIndex(0);
  }, [currentStep, messages]);

  const scrollToCard = (cardIndex: number) => {
    setActiveCardIndex(cardIndex);
    requestAnimationFrame(() => {
      document.getElementById(`audit-message-${currentStep}-${cardIndex}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    });
  };

  const handlePreviousCard = () => {
    scrollToCard(Math.max(activeCardIndex - 1, 0));
  };

  const handleNextCard = () => {
    scrollToCard(Math.min(activeCardIndex + 1, currentStepMessages.length - 1));
  };

  const handleCopyMessage = (msg: AuditMessage) => {
    const roleConfig = getRoleConfig(msg.agent_role || 'pm', t);
    let mdContent = `## ${roleConfig.title} (Round ${msg.round_number})\n`;
    mdContent += `*Time: ${new Date(msg.timestamp).toLocaleString()}*\n\n`;

    if (msg.content) {
      mdContent += `${msg.content}\n\n`;
    }
    if (msg.pmDecision) {
      mdContent += `### ${t('debate.decision_tab')}\n\n`;
      mdContent += `- **${t('debate.confidence')}:** ${formatPercent(msg.pmDecision.confidence)}\n`;
      mdContent += `- **${t('debate.target_position')}:** ${formatPercent(msg.pmDecision.target_position)}\n`;
      mdContent += `- **${t('debate.stop_loss')}:** ${formatPrice(msg.pmDecision.stop_loss)}\n`;
      mdContent += `- **${t('debate.take_profit')}:** ${formatPrice(msg.pmDecision.take_profit)}\n`;
      mdContent += `- **${t('debate.analysis.holding_period')}:** ${msg.pmDecision.holding_horizon_days || 'N/A'}\n\n`;
    }

    navigator.clipboard.writeText(mdContent)
      .then(() => {
        message.success(t('common.copy_success'));
      })
      .catch(err => console.error('Failed to copy text: ', err));
  };

  const renderMessageCard = (msg: AuditMessage, index: number) => {
    const roleConfig = getRoleConfig(msg.agent_role || 'pm', t);

    return (
      <Card
        key={msg.id}
        id={`audit-message-${currentStep}-${index}`}
        size="small"
        className="bg-gray-800 border-gray-700 mb-4"
        title={
          <div className="flex justify-between items-center">
            <Space>
              <Avatar size="small" icon={roleConfig.icon} style={{ backgroundColor: roleConfig.color }} />
              <Text strong style={{ color: roleConfig.color }}>{roleConfig.title}</Text>
              <Tag color="blue">Round {msg.round_number}</Tag>
              <Button
                type="text"
                size="small"
                icon={<CopyOutlined style={{ color: '#8c8c8c' }} />}
                onClick={() => handleCopyMessage(msg)}
                title={t('common.copy')}
              />
            </Space>
            <Text type="secondary" style={{ fontSize: '12px' }}>
              {new Date(msg.timestamp).toLocaleTimeString()}
            </Text>
          </div>
        }
      >
        <DebateMarkdown
          content={msg.content || t('brain.no_content')}
          className="markdown-content"
          style={{ color: 'var(--app-text)' }}
        />

        {msg.pmDecision && (
          <div className="mt-4 p-3 bg-gray-900 rounded border border-blue-900/30">
            <Text strong style={{ color: '#1677ff' }}>
              <AuditOutlined style={{ marginRight: 8 }} />
              {t('debate.decision_tab')}
            </Text>
            <Descriptions column={2} size="small" bordered style={{ marginTop: 12 }}>
              <Descriptions.Item label={t('debate.confidence')}>
                {formatPercent(msg.pmDecision.confidence)}
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.target_position')}>
                {formatPercent(msg.pmDecision.target_position)}
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.stop_loss')}>
                {formatPrice(msg.pmDecision.stop_loss)}
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.take_profit')}>
                {formatPrice(msg.pmDecision.take_profit)}
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.analysis.holding_period')} span={2}>
                {msg.pmDecision.holding_horizon_days ? `${msg.pmDecision.holding_horizon_days}D` : 'N/A'}
              </Descriptions.Item>
            </Descriptions>
          </div>
        )}

        {msg.prompt_input && (
          <details className="mt-2 border-t border-gray-700 pt-2">
            <summary className="text-[10px] text-gray-500 cursor-pointer">{t('brain.ai_input')}</summary>
            <pre className="mt-2 text-[10px] text-gray-400 bg-black/50 p-2 rounded overflow-x-auto border border-gray-900 whitespace-pre-wrap">
              {msg.prompt_input}
            </pre>
          </details>
        )}

      </Card>
    );
  };

  const renderStackedMessages = (msgs: AuditMessage[]) => {
    return (
      <div style={{ width: '100%' }}>
        {msgs.map((msg: AuditMessage, index: number) => renderMessageCard(msg, index))}
      </div>
    );
  };

  const renderContent = () => {
    const stepMsgs = currentStepMessages;

    if (stepMsgs.length === 0) {
      return (
        <div style={{ padding: '40px 0', textAlign: 'center' }}>
          <Empty description={t('brain.no_audit_log')} />
        </div>
      );
    }

    return renderStackedMessages(stepMsgs);
  };

  if (!effectiveSessionId) return <Empty description={t('brain.select_session')} />;

  return (
    <div className="audit-log-shell">
      <div className="audit-toolbar">
        <div style={{ flex: 1 }}>
          <Steps
            current={currentStep}
            onChange={setCurrentStep}
            size="small"
            items={STAGES.map(s => ({
              title: s.title,
              icon: s.icon,
              disabled: getStepMessages(STAGES.indexOf(s)).length === 0
            }))}
          />
        </div>
        <Space style={{ marginLeft: 16 }} wrap>
          <Button
            icon={<LeftOutlined />}
            disabled={activeCardIndex <= 0 || currentStepMessages.length <= 1}
            onClick={handlePreviousCard}
          >
            {t('common.previous', { defaultValue: '上一个' })}
          </Button>
          <Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
            {currentStepMessages.length > 0 ? `${activeCardIndex + 1}/${currentStepMessages.length}` : '0/0'}
          </Text>
          <Button
            icon={<RightOutlined />}
            disabled={activeCardIndex >= currentStepMessages.length - 1 || currentStepMessages.length <= 1}
            onClick={handleNextCard}
          >
            {t('common.next', { defaultValue: '下一个' })}
          </Button>
          <Button
            type="primary"
            ghost
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={fetchData}
          >
            {t('common.sync')}
          </Button>
          <Dropdown.Button
            icon={<DownOutlined />}
            menu={{ items: exportMenuItems }}
            onClick={() => handleExport('decision')}
            style={{ minWidth: 120 }}
          >
            <ExportOutlined /> {t('debate.export_report')}
          </Dropdown.Button>
        </Space>
      </div>

      <div className="audit-scroll-area">
        {loading && messages.length === 0 ? (
          <div className="text-center p-12"><Spin size="large" /></div>
        ) : (
          <div className="audit-content-area">
            {renderContent()}
          </div>
        )}
      </div>

      <style>{`
        .audit-log-shell {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: var(--app-bg-layout);
          overflow: hidden;
        }
        .audit-toolbar {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 16px;
          padding: 16px 24px;
          background: color-mix(in srgb, var(--app-bg-layout) 94%, transparent);
          border-bottom: 1px solid var(--app-border);
          backdrop-filter: blur(8px);
          flex: 0 0 auto;
        }
        .audit-scroll-area {
          flex: 1 1 auto;
          min-height: 0;
          overflow-y: auto;
          padding: 24px;
        }
        .audit-content-area .ant-card-head {
          border-bottom: 1px solid var(--app-border) !important;
          background: var(--app-bg-container) !important;
        }
        .audit-content-area .ant-card {
          box-shadow: 0 4px 12px color-mix(in srgb, var(--app-bg-layout) 70%, transparent);
        }
        .markdown-content ul, .markdown-content ol {
          padding-left: 20px;
          margin-bottom: 12px;
        }
        .markdown-content p {
          margin-bottom: 8px;
        }
        .markdown-content h1, .markdown-content h2, .markdown-content h3 {
          color: var(--app-primary);
          margin: 16px 0 8px;
        }
      `}</style>
    </div>
  );
};
