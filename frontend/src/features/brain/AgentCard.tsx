import React from 'react';
import { Card, Typography, Avatar, Tag, theme } from 'antd';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTranslation } from 'react-i18next';
import { getRoleConfig } from './roleConfig';
import './debateMarkdown.css';

const { Text } = Typography;

type MarkdownTableProps = React.ComponentPropsWithoutRef<'table'> & {
  node?: unknown;
};

const markdownComponents: Components = {
  table: ({ children, node, ...props }: MarkdownTableProps) => {
    void node;
    return (
      <div className="debate-markdown-table-scroll">
        <table {...props}>{children}</table>
      </div>
    );
  },
};

interface AgentCardProps {
  role: string;
  content: string;
  timestamp: string;
  round?: number;
}

export const AgentCard: React.FC<AgentCardProps> = ({ role, content, timestamp, round }) => {
  const { t } = useTranslation();
  const {
    token: {
      boxShadowSecondary,
      colorBgContainer,
      colorFillQuaternary,
      colorBorderSecondary,
      colorPrimary,
      colorText,
      colorTextSecondary,
    },
  } = theme.useToken();

  const config = getRoleConfig(role, t);

  // Simple heuristic to extract CoT/Thinking part
  const hasThinking = content.includes('<think>') && content.includes('</think>');
  let mainContent = content;
  let thinkingContent = '';

  if (hasThinking) {
    const thinkStart = content.indexOf('<think>');
    const thinkEnd = content.indexOf('</think>');
    thinkingContent = content.substring(thinkStart + 7, thinkEnd);
    mainContent = content.substring(0, thinkStart) + content.substring(thinkEnd + 8);
  }

  return (
    <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
      <Avatar
        icon={config.icon}
        style={{ backgroundColor: config.color, flexShrink: 0, marginTop: 4 }}
        size="large"
      />
      <div style={{ flex: 1, maxWidth: '90%', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Text strong style={{ color: config.color, fontSize: 14 }}>{config.title}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>{new Date(timestamp).toLocaleTimeString()}</Text>
          {round !== undefined && round > 0 && (
            <Tag color="#108ee9" style={{ border: 0, fontSize: 10, margin: 0, marginLeft: 4, lineHeight: '16px', padding: '0 4px' }}>
              R{round}
            </Tag>
          )}
          <div style={{
            fontSize: 10, padding: '0 6px', borderRadius: 4,
            background: `${config.color}20`, color: config.color, border: `1px solid ${config.color}40`
          }}>
            {role.toUpperCase()}
          </div>
        </div>

        <Card
          size="small"
          style={{
            borderRadius: '4px 16px 16px 16px',
            background: colorBgContainer,
            border: `1px solid ${colorBorderSecondary}`,
            boxShadow: boxShadowSecondary
          }}
          styles={{ body: { padding: '8px 12px' } }}
        >
          {hasThinking && (
            <details style={{ marginBottom: 8, fontSize: 13, color: colorTextSecondary, borderLeft: `3px solid ${colorPrimary}`, paddingLeft: 10, background: colorFillQuaternary, borderRadius: 4, padding: '8px' }}>
              <summary style={{ cursor: 'pointer', userSelect: 'none', fontStyle: 'italic', color: colorPrimary }}>{t('debate.thinking_process')}</summary>
              <div style={{ marginTop: 8, whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 12 }}>
                {thinkingContent}
              </div>
            </details>
          )}
          <div className="debate-markdown" style={{ color: colorText, fontSize: 14, lineHeight: 1.6 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{mainContent.trim()}</ReactMarkdown>
          </div>
        </Card>
      </div>
    </div>
  );
};
