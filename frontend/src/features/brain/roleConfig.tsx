import { ArrowDownOutlined, ArrowUpOutlined, AuditOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';
import type { TFunction } from 'i18next';
import type { ReactNode } from 'react';

interface RoleConfig {
  color: string;
  icon: ReactNode;
  title: string;
}

export const getRoleConfig = (role: string, t: TFunction): RoleConfig => {
  const r = role.toLowerCase();
  if (r.includes('fundamental')) return { color: '#faad14', icon: <RobotOutlined />, title: t('ai_analyst.agents.fundamental') };
  if (r.includes('technical')) return { color: '#13c2c2', icon: <ThunderboltOutlined />, title: t('ai_analyst.agents.technical') };
  if (r.includes('capital_flow')) return { color: '#722ed1', icon: <ThunderboltOutlined />, title: t('ai_analyst.agents.capital_flow') };
  // news_analyst 必须在通配的 sentiment/news 之前判断，否则会被误匹配为"情绪专家"
  if (r.includes('news_analyst') || r === 'news') return { color: '#1677ff', icon: <RobotOutlined />, title: t('ai_analyst.agents.news_analyst') };
  if (r.includes('policy_analyst') || r === 'policy') return { color: '#2f54eb', icon: <RobotOutlined />, title: t('ai_analyst.agents.policy_analyst') };
  if (r.includes('sentiment')) return { color: '#eb2f96', icon: <RobotOutlined />, title: t('ai_analyst.agents.sentiment') };
  if (r.includes('risk')) return { color: '#fa8c16', icon: <ThunderboltOutlined />, title: t('ai_analyst.agents.risk') };
  if (r.includes('bull')) return { color: '#cf1322', icon: <ArrowUpOutlined />, title: t('ai_analyst.agents.bull') };
  if (r.includes('bear')) return { color: '#3f8600', icon: <ArrowDownOutlined />, title: t('ai_analyst.agents.bear') };
  if (r.includes('aggressive')) return { color: '#f5222d', icon: <ThunderboltOutlined />, title: t('ai_analyst.agents.aggressive') };
  if (r.includes('conservative')) return { color: '#52c41a', icon: <RobotOutlined />, title: t('ai_analyst.agents.conservative') };
  if (r.includes('neutral')) return { color: '#1890ff', icon: <RobotOutlined />, title: t('ai_analyst.agents.neutral') };
  if (r.includes('fact_arbitration')) return { color: '#531dab', icon: <AuditOutlined />, title: t('ai_analyst.agents.fact_arbitrator', { defaultValue: '事实仲裁员' }) };
  if (r.includes('pm') || r.includes('portfolio_manager')) return { color: '#1890ff', icon: <ThunderboltOutlined />, title: t('ai_analyst.agents.portfolio_manager') };
  return { color: '#8c8c8c', icon: <RobotOutlined />, title: role };
};
