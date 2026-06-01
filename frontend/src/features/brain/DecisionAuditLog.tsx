import React, { useCallback, useEffect, useState } from 'react';
import { App as AntdApp, Avatar, Button, Card, Col, Descriptions, Divider, Dropdown, Empty, Row, Space, Spin, Steps, Tag, Typography } from 'antd';
import { useSessionStore } from '../../store/useSessionStore';
import { DebateThread, debateApi } from '../../api/debate';
import { sessionApi, Session } from '../../api/session';
import { AuditOutlined, BarChartOutlined, MessageOutlined, FileSearchOutlined, RobotOutlined, ReloadOutlined, ExportOutlined, DownOutlined, CopyOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getRoleConfig } from './roleConfig';

import type { MenuProps } from 'antd';

const { Text, Title } = Typography;

interface AuditMessage {
  id: string;
  session_id: string;
  round_number: number;
  speaker_role: string;
  content: string;
  reasoning_chain?: AuditAnalysis;
  prompt_input?: string;
  timestamp: string;
  agent_role?: string;
  stage?: string;
  // For Decision results
  action?: string;
  confidence?: number;
  target_position?: number;
  execution_plan?: string;
}

interface AuditAnalysis {
  decision?: string;
  confidence_score?: number;
  target_position?: number;
  stop_loss?: string | number;
  price_range?: string;
  execution_details?: string;
}

export interface DecisionAuditLogProps {
  sessionId?: string;
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
      return allMessages.filter(m => m.stage === 'portfolio_management');
    default:
      return [];
  }
};

export const DecisionAuditLog: React.FC<DecisionAuditLogProps> = ({ sessionId }) => {
  const { t } = useTranslation();
  const { message } = AntdApp.useApp();
  const { activeSession } = useSessionStore();
  const [localSession, setLocalSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<AuditMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);

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

      // 统一从 getThreads 获取所有 DebateMessage，后端已经包含了所有 stage 信息
      const threads = await debateApi.getHistory(effectiveSessionId);

      // 转换为 AuditMessage 格式，注意映射后端字段
      // 后端 getThreads 返回: speaker_role, content, timestamp, round_number, reasoning_chain (analysis), prompt_input, stage
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
        reasoning_chain: (msg.analysis || msg.reasoning_chain) as AuditAnalysis | undefined, // 兼顾不同接口的分析数据键名
      }));

      // 决策和执行通常在最后，如果需要合并 getDecisions 的精简数据也可以
      // 但其实 DebateMessage 已经包含了 PM 的详细分报告 (包含执行详情)
      setMessages(auditMessages);

      // 默认选中最新的有数据的步骤
      if (getStepMessagesFor(auditMessages, 4).length > 0) setCurrentStep(4);
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
    fetchData();
  }, [fetchData]);

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

      if (msg.stage === 'portfolio_management' && msg.reasoning_chain?.decision) {
        const analysis = msg.reasoning_chain;
        mdContent += `### ${t('debate.decision_tab')}\n\n`;
        mdContent += `- **${t('debate.analysis.action')}:** ${(analysis.decision || 'HOLD').toUpperCase()}\n`;
        mdContent += `- **${t('debate.analysis.confidence')}:** ${(analysis.confidence_score || 0).toFixed(0)}%\n`;
        mdContent += `- **${t('debate.analysis.target_position')}:** ${((analysis.target_position || 0) * 100).toFixed(0)}%\n`;
        mdContent += `- **${t('debate.analysis.stop_loss')}:** ¥${analysis.stop_loss || 'N/A'}\n`;
        mdContent += `- **${t('debate.analysis.price_range')}:** ${analysis.price_range || 'N/A'}\n`;
        mdContent += `- **${t('debate.execution_plan')}:** ${analysis.execution_details || 'N/A'}\n\n`;
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
    { key: 'decision', title: t('debate.decision_tab'), icon: <AuditOutlined />, stages: ['portfolio_management'] },
  ];

  const getStepMessages = (stepIndex: number) => {
    return getStepMessagesFor(messages, stepIndex);
  };

  const handleCopyMessage = (msg: AuditMessage) => {
    const roleConfig = getRoleConfig(msg.agent_role || 'pm', t);
    let mdContent = `## ${roleConfig.title} (Round ${msg.round_number})\n`;
    mdContent += `*Time: ${new Date(msg.timestamp).toLocaleString()}*\n\n`;

    if (msg.content) {
      mdContent += `${msg.content}\n\n`;
    }

    if (msg.stage === 'portfolio_management' && msg.reasoning_chain?.decision) {
      const analysis = msg.reasoning_chain;
      mdContent += `### ${t('debate.decision_tab')}\n\n`;
      mdContent += `- **${t('debate.analysis.action')}:** ${(analysis.decision || 'HOLD').toUpperCase()}\n`;
      mdContent += `- **${t('debate.analysis.confidence')}:** ${(analysis.confidence_score || 0).toFixed(0)}%\n`;
      mdContent += `- **${t('debate.analysis.target_position')}:** ${((analysis.target_position || 0) * 100).toFixed(0)}%\n`;
      mdContent += `- **${t('debate.analysis.stop_loss')}:** ¥${analysis.stop_loss || 'N/A'}\n`;
      mdContent += `- **${t('debate.analysis.price_range')}:** ${analysis.price_range || 'N/A'}\n`;
      mdContent += `- **${t('debate.execution_plan')}:** ${analysis.execution_details || 'N/A'}\n\n`;
    }

    navigator.clipboard.writeText(mdContent)
      .then(() => {
        message.success(t('common.copy_success'));
      })
      .catch(err => console.error('Failed to copy text: ', err));
  };

  const renderMessageCard = (msg: AuditMessage) => {
    const roleConfig = getRoleConfig(msg.agent_role || 'pm', t);
    const analysis = msg.reasoning_chain || {};

    return (
      <Card
        key={msg.id}
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
        <div className="markdown-content" style={{ color: 'var(--app-text)' }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {msg.content || t('brain.no_content')}
          </ReactMarkdown>
        </div>

        {/* 对于决策和执行阶段，尝试解析并展示结构化数据 */}
        {msg.stage === 'portfolio_management' && analysis.decision && (
          <div className="mt-4 p-3 bg-gray-900 rounded border border-blue-900/30">
            <Title level={5} style={{ fontSize: 14, color: '#1677ff', marginBottom: 8 }}>
              <AuditOutlined style={{ marginRight: 8 }} />
              {t('debate.decision_tab')}
            </Title>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label={t('debate.analysis.action')}>
                <Tag color={analysis.decision === 'buy' ? '#cf1322' : (analysis.decision === 'sell' ? '#3f8600' : 'gray')}>
                  {(analysis.decision || 'HOLD').toUpperCase()}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.analysis.confidence')}>
                {(analysis.confidence_score || 0).toFixed(0)}%
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.analysis.target_position')}>
                {((analysis.target_position || 0) * 100).toFixed(0)}%
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.analysis.stop_loss')}>
                ¥{analysis.stop_loss || 'N/A'}
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.analysis.price_range')} span={2}>
                {analysis.price_range || 'N/A'}
              </Descriptions.Item>
              <Descriptions.Item label={t('debate.execution_plan')} span={2}>
                {analysis.execution_details || 'N/A'}
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

        <details className="mt-2 border-t border-gray-700 pt-2">
          <summary className="text-[10px] text-gray-500 cursor-pointer">{t('brain.ai_output')}</summary>
          <pre className="mt-2 text-[10px] text-gray-400 bg-black/50 p-2 rounded overflow-x-auto border border-gray-900">
            {JSON.stringify(analysis, null, 2)}
          </pre>
        </details>
      </Card>
    );
  };

  const renderExpertAnalysis = (msgs: AuditMessage[]) => {
    // 专家分析阶段通常有 5 个 Agent，适合网格展示
    return (
      <Row gutter={[16, 16]}>
        {msgs.map((msg: AuditMessage) => (
          <Col xs={24} lg={12} key={msg.id}>
            {renderMessageCard(msg)}
          </Col>
        ))}
      </Row>
    );
  };

  const renderCrossDebate = (msgs: AuditMessage[]) => {
    // 交叉辩论可以分轮次展示，或者按角色分组
    return (
      <div>
        {msgs.map((msg: AuditMessage) => renderMessageCard(msg))}
      </div>
    );
  };

  const renderContent = () => {
    const stepMsgs = getStepMessages(currentStep);

    if (stepMsgs.length === 0) {
      return (
        <div style={{ padding: '40px 0', textAlign: 'center' }}>
          <Empty description={t('brain.no_audit_log')} />
        </div>
      );
    }

    switch (currentStep) {
      case 0: // News / Policy / Sentiment
      case 1: // Expert Analysis
        return renderExpertAnalysis(stepMsgs);
      case 3: // Cross Debate
        return renderCrossDebate(stepMsgs);
      default:
        return (
          <div style={{ maxWidth: 800, margin: '0 auto' }}>
            {stepMsgs.map(msg => renderMessageCard(msg))}
          </div>
        );
    }
  };

  if (!effectiveSessionId) return <Empty description={t('brain.select_session')} />;

  return (
    <div style={{ padding: '24px', height: '100%', overflowY: 'auto', background: 'var(--app-bg-layout)' }}>
      <div style={{ marginBottom: 32, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
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
        <Space style={{ marginLeft: 16 }}>
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

      <Divider style={{ borderColor: 'var(--app-border)' }} />

      {loading && messages.length === 0 ? (
        <div className="text-center p-12"><Spin size="large" /></div>
      ) : (
        <div className="audit-content-area">
          {renderContent()}
        </div>
      )}

      <style>{`
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
