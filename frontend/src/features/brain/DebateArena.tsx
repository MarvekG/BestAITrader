import React, { useEffect, useRef, useState } from 'react';
import { AgentCard } from './AgentCard';
import { Empty, Spin, Card, Tabs, Descriptions, Space, Row, Col, Badge, Tag, Avatar } from 'antd';
import { debateApi, PMDecision } from '../../api/debate';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getRoleConfig } from './roleConfig';

type LooseValue = number & {
  (...args: unknown[]): LooseValue;
  [key: string]: LooseValue;
  length: LooseValue;
};

type LooseRecord = Record<string, LooseValue>;

const asLooseRecord = (value: unknown): LooseRecord => value as LooseRecord;
const isLooseRecord = (value: unknown): value is LooseRecord =>
  typeof value === 'object' && value !== null;

const getLooseString = (record: LooseRecord, key: string) => {
  const value = record[key] as unknown;
  return typeof value === 'string' ? value : undefined;
};

export interface DebateMessage {
  message_id: string;
  session_id: string;
  stage: string;
  round_number: number;
  agent_name: string;
  agent_role: string;
  decision: string;
  confidence: number;
  reasoning: string;
  analysis: LooseRecord | null;
  created_at: string;
}

interface DebateArenaProps {
  messages: DebateMessage[];
  loading?: boolean;
}

/**
 * 规范化 action 字段，兼容后端枚举序列化产生的历史脏数据
 * 例如: "AnalystSignal.SELL" => "sell", "SELL" => "sell"
 */
const normalizeAction = (raw: string | undefined): 'buy' | 'sell' | 'hold' => {
  if (!raw) return 'hold';
  // 处理 "AnalystSignal.SELL" 格式
  const val = raw.includes('.') ? raw.split('.').pop()! : raw;
  const lower = val.trim().toLowerCase();

  // 更加鲁棒的匹配逻辑 | More robust matching logic
  if (lower === 'buy' || lower === 'strong_buy' || lower.includes('buy')) return 'buy';
  if (lower === 'sell' || lower === 'reduce' || lower.includes('sell')) return 'sell';
  return 'hold';
};

export const DebateArena: React.FC<DebateArenaProps> = ({ messages = [], loading = false }) => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('debate');
  const [decisions, setDecisions] = useState<PMDecision[]>([]);
  const [decisionsLoading, setDecisionsLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (loading && activeTab === 'debate' && messages && messages.length > 0) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [loading, messages, activeTab]);

  // Fetch decisions when Decision tab is activated
  useEffect(() => {
    const fetchDecisions = async () => {
      if (activeTab === 'decision' && messages.length > 0) {
        const sessionId = messages[0]?.session_id;
        if (sessionId) {
          setDecisionsLoading(true);
          try {
            const decisions = await debateApi.getDecisions(sessionId);
            setDecisions(decisions || []);
          } catch (error) {
            console.error('Failed to fetch decisions:', error);
          } finally {
            setDecisionsLoading(false);
          }
        }
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

    if (!decisions || decisions.length === 0) {
      return <Empty description={t('debate.no_decision_data')} />;
    }

    return (
      <div className="p-4 overflow-y-auto" style={{ height: 'calc(100vh - 350px)' }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          {decisions.map((decision) => {
            const action = normalizeAction(decision.action);
            const actionColor = action === 'buy' ? '#52c41a' : (action === 'sell' ? '#f5222d' : '#faad14');
            const actionText = action === 'buy' ? `📈 ${t('trading.buy')}` : (action === 'sell' ? `📉 ${t('trading.sell')}` : `⏸️ ${t('trading.hold_action')}`);
            const confidenceColor = decision.confidence >= 0.7 ? '#52c41a' : (decision.confidence >= 0.5 ? '#faad14' : '#f5222d');
            const roleConfig = getRoleConfig(decision.agent_role || 'pm', t);

            return (
              <Card
                key={decision.id}
                title={
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <Avatar
                        size="small"
                        icon={roleConfig.icon}
                        style={{ backgroundColor: roleConfig.color }}
                      />
                      <span style={{ color: roleConfig.color, fontWeight: 'bold' }}>{roleConfig.title}</span>
                      <Tag color={actionColor} style={{ fontSize: '14px', padding: '2px 8px', marginLeft: 8 }}>
                        {actionText}
                      </Tag>
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
                style={{ borderLeft: `4px solid ${actionColor}` }}
              >
                <Descriptions column={2} size="small" bordered>
                  <Descriptions.Item label={t('debate.target_position')} span={2}>
                    {(decision.target_position * 100).toFixed(0)}%
                  </Descriptions.Item>

                  <Descriptions.Item label={t('debate.decision_details')} span={2}>
                    {(() => {
                      let parsedReasoning: LooseRecord | null = null;
                      try {
                        if (typeof decision.reasoning === 'string') {
                          // 尝试清理 markdown 代码块标记
                          const cleaned = decision.reasoning.replace(/```json\n|\n```/g, '');
                          const parsed = JSON.parse(cleaned);
                          parsedReasoning = isLooseRecord(parsed) ? parsed : null;
                        } else {
                          parsedReasoning = null;
                        }
                      } catch {
                        parsedReasoning = null;
                      }

                      if (!parsedReasoning || typeof parsedReasoning !== 'object') {
                        return (
                          <div className="decision-markdown-container">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {typeof decision.reasoning === 'string'
                                ? decision.reasoning
                                : JSON.stringify(decision.reasoning, null, 2)}
                            </ReactMarkdown>
                          </div>
                        );
                      }

                      const { final_decision, consensus_summary, execution_plan } = parsedReasoning;

                      return (
                        <Space direction="vertical" style={{ width: '100%' }} size="middle">

                          {/* 核心逻辑 */}
                          {final_decision?.reasoning && (
                            <div>
                              <div style={{ fontWeight: 'bold', marginBottom: 4 }}>{t('debate.core_logic')}:</div>
                              <div style={{ whiteSpace: 'pre-wrap', color: 'var(--app-text)', background: 'var(--app-bg-muted)', padding: '8px', borderRadius: '4px' }}>
                                {final_decision.reasoning}
                              </div>
                            </div>
                          )}

                          {/* 风险管理 */}
                          {final_decision?.risk_management && (
                            <Descriptions size="small" bordered column={2} title={t('debate.risk_mgmt')}>
                              <Descriptions.Item label={t('debate.stop_loss')}>{final_decision.risk_management.stop_loss}%</Descriptions.Item>
                              <Descriptions.Item label={t('debate.take_profit')}>{final_decision.risk_management.take_profit}%</Descriptions.Item>
                              <Descriptions.Item label={t('debate.trailing_stop')}>{final_decision.risk_management.trailing_stop ? '启用' : '禁用'}</Descriptions.Item>
                              <Descriptions.Item label={t('debate.position_ctrl')}>{final_decision.risk_management.position_sizing}</Descriptions.Item>
                            </Descriptions>
                          )}

                          {/* 共识总结 - 看多/看空因素 */}
                          {consensus_summary && (
                            <Row gutter={16}>
                              <Col span={12}>
                                <Card size="small" title={<span style={{ color: '#ff4d4f' }}>📉 {t('debate.bearish_factors')}</span>} className="bg-gray-800 border-gray-700">
                                  <ul style={{ paddingLeft: 20, margin: 0 }}>
                                    {consensus_summary.bearish_factors?.map((factor: string, idx: number) => (
                                      <li key={idx} style={{ color: '#ffa39e' }}>{factor}</li>
                                    )) || <li>无显著风险</li>}
                                  </ul>
                                </Card>
                              </Col>
                              <Col span={12}>
                                <Card size="small" title={<span style={{ color: '#52c41a' }}>📈 {t('debate.bullish_factors')}</span>} className="bg-gray-800 border-gray-700">
                                  <ul style={{ paddingLeft: 20, margin: 0 }}>
                                    {consensus_summary.bullish_factors?.map((factor: string, idx: number) => (
                                      <li key={idx} style={{ color: '#b7eb8f' }}>{factor}</li>
                                    )) || <li>无显著利好</li>}
                                  </ul>
                                </Card>
                              </Col>
                            </Row>
                          )}

                          {/* 决定性因素 */}
                          {consensus_summary?.decisive_factors && (
                            <div>
                              <div style={{ fontWeight: 'bold', marginBottom: 4, color: '#1677ff' }}>🔔 {t('debate.decisive_factors')}:</div>
                              <ul style={{ paddingLeft: 20 }}>
                                {consensus_summary.decisive_factors.map((f: string, i: number) => <li key={i}>{f}</li>)}
                              </ul>
                            </div>
                          )}

                          {/* 执行方案 (优先显示 parsedReasoning 中的，如果不存在则显示 decision.execution_plan) */}
                          {(execution_plan || decision.execution_plan) && (
                            <Descriptions size="small" bordered column={1} title={t('debate.execution_plan')}>
                              <Descriptions.Item label={t('debate.entry_strategy')}>{execution_plan?.entry_strategy || decision.execution_plan?.entry_strategy || 'N/A'}</Descriptions.Item>
                              <Descriptions.Item label={t('debate.exit_strategy')}>{execution_plan?.exit_strategy || decision.execution_plan?.exit_strategy || 'N/A'}</Descriptions.Item>
                              <Descriptions.Item label={t('debate.risk_mitigation')}>{execution_plan?.risk_mitigation || decision.execution_plan?.risk_mitigation || 'N/A'}</Descriptions.Item>
                            </Descriptions>
                          )}

                        </Space>
                      );
                    })()}
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
            let content = msg.reasoning;
            let analysisData = msg.analysis;

            if (content && typeof content === 'string' && content.trim().startsWith('{')) {
              try {
                const parsed = JSON.parse(content);
                if (parsed.final_decision || parsed.recommendation || parsed.analysis) {
                  analysisData = asLooseRecord({ ...analysisData, ...parsed });
                  content = '';
                }
              } catch {
                // Ignore non-JSON reasoning content.
              }
            }

            // Always process analysisData if available, even if content exists
            if (analysisData) {
              // Priority: If the new engine provides a full markdown report, use it.
              if (analysisData.markdown) {
                content = String(analysisData.markdown);
              } else {
                // Legacy structured parsing for backward compatibility
                // 移除 _prompt_input 字段，这是内部使用的字段，不应该在 Live Debate 中显示
                const cleanAnalysisData = { ...analysisData };
                delete cleanAnalysisData._prompt_input;
                analysisData = cleanAnalysisData;

                // Deduplicate content for Analysts:
                // If we have a structured reasoning chain, the text 'content' is often just a raw dump of that chain.
                // We want to hide this raw 'content' for Analysts, but keep it for the Investment Manager (who writes a summary paragraph).
                const r = msg.agent_role?.toLowerCase() || '';
                const isAnalyst = r.includes('analyst') || r.includes('bull') || r.includes('bear') || r.includes('researcher');
                const isContext = r === 'context';
                const hasReasoningChain = analysisData.reasoning_chain && Array.isArray(analysisData.reasoning_chain) && analysisData.reasoning_chain.length > 0;

                if (isContext) {
                  const parts = [];
                  const s = analysisData; // shorthand

                  // 1. Market Condition
                  if (s.price) {
                    const changeColor = (s.price.change_pct || 0) >= 0 ? '🔴' : '🟢'; // Red for up (CN), Green for down
                    parts.push(`### ${t('debate.market_snapshot')}`);
                    parts.push(`**${s.stock_name} (${s.stock_code})**`);
                    parts.push(`- ${t('debate.current_price')}: ${s.price.current} (${changeColor} ${(s.price.change_pct || 0).toFixed(2)}%)`);
                    parts.push(`- ${t('debate.volume')}: ${((s.price.volume || 0) / 100).toFixed(0)}${t('units.hand')} | ${t('debate.turnover')}: ${((s.price.turnover || 0) / 100000000).toFixed(2)}${t('units.billion')}`);
                  }

                  // 2. Valuation
                  if (s.valuation) {
                    parts.push(`#### ${t('debate.valuation')}`);
                    parts.push(`- PE(TTM): ${s.valuation.pe_ttm} | PE(Dyn): ${s.valuation.pe_dynamic || 'N/A'}`);
                    parts.push(`- PB: ${s.valuation.pb || 'N/A'} | PEG: ${s.valuation.peg || 'N/A'}`);
                    parts.push(`- ${t('debate.total_cap')}: ${((s.valuation.market_cap || 0) / 100000000).toFixed(2)}${t('units.billion')}`);
                  }

                  // 3. Fundamentals
                  if (s.fundamentals) {
                    parts.push(`#### ${t('debate.fundamentals')}`);
                    parts.push(`- ROE: ${Number(s.fundamentals.roe).toFixed(2)}%`);
                    parts.push(`- ${t('debate.rev_growth')}: ${Number(s.fundamentals.revenue_growth).toFixed(2)}% | ${t('debate.profit_growth')}: ${Number(s.fundamentals.profit_growth).toFixed(2)}%`);
                  }


                  // 4. Technical
                  if (s.technical) {
                    parts.push(`#### ${t('debate.technical')}`);
                    parts.push(`- MA: 5(${s.technical.ma5}) | 20(${s.technical.ma20})`);
                    if (s.technical.rsi) parts.push(`- RSI(6): ${s.technical.rsi.rsi_6?.toFixed(2) || s.technical.rsi}`);
                    if (s.technical.macd) parts.push(`- MACD: DIF=${s.technical.macd.dif} | DEA=${s.technical.macd.dea}`);
                  }

                  // 5. Capital Flow
                  if (s.capital_flow) {
                    parts.push(`#### ${t('debate.capital_flow')}`);
                    if (s.capital_flow.northbound) {
                      parts.push(`- ${t('debate.northbound')}: ${s.capital_flow.northbound.net_buy_value || 'N/A'}`);
                    }
                    if (s.capital_flow.sector_flow) {
                      parts.push(`- ${t('debate.industry_inflow')}: ${((s.capital_flow.sector_flow.net_inflow || 0) / 100000000).toFixed(2)}${t('units.billion')}`);
                    }
                  }

                  // 6. News
                  if (s.news && Array.isArray(s.news) && s.news.length > 0) {
                    parts.push(`#### ${t('debate.news_brief')}`);
                    s.news.slice(0, 3).forEach((n: unknown) => {
                      if (!isLooseRecord(n)) return;
                      parts.push(`- ${getLooseString(n, 'title') || ''} (${getLooseString(n, 'date') || ''})`);
                    });
                  }

                  content = parts.join('\n\n');
                }

                if (isAnalyst && hasReasoningChain) {
                  content = '';
                }

                const parts = content && content.trim() !== '' ? [content] : [];
                const {
                  recommendation,
                  reasoning_chain,
                  key_arguments,
                  risk_warnings,
                  final_decision,
                  consensus_summary,
                  weighted_analysis,
                  execution_plan,
                  // Add new fields for Neutral Analyst / Cross Debate
                  cross_debate_response,
                  strategy,
                  balanced_scorecard,
                  synthesis,
                  balanced_recommendation,
                  key_insights,
                  cross_debate_analysis // Add this new field
                } = analysisData;

                if (final_decision) {
                  const { action, confidence, reasoning, holding_period, target_position, risk_management } = final_decision;
                  const actionValue = String(action || '').toLowerCase();
                  const actionKey = actionValue === 'buy' ? 'debate.actions.buy' :
                    (actionValue === 'sell' ? 'debate.actions.sell' : 'debate.actions.hold');
                  const actionZh = t(actionKey);

                  parts.push(t('debate.analysis.decision_title', { action: actionZh }));
                  if (confidence) parts.push(`- **${t('debate.analysis.confidence')}:** ${(confidence * 100).toFixed(0)}%`);
                  if (target_position !== undefined) parts.push(`- **${t('debate.analysis.suggested_position')}:** ${(target_position * 100).toFixed(0)}%`);
                  if (holding_period) parts.push(`- **${t('debate.analysis.holding_period')}:** ${holding_period}`);
                  if (reasoning) parts.push(`- **${t('debate.analysis.reasoning')}:** ${reasoning}`);

                  if (risk_management) {
                    const { stop_loss, take_profit, trailing_stop, position_sizing } = risk_management;
                    // Better: `trailing_stop ? 'Enable' : 'Disable'` but we want translated. 
                    // Let's use simple logic: trailing_stop ? '✅' : '❌' to avoid words, or just stick to simple text.
                    // I'll stick to hardcoded 'On/Off' for boolean to save complexity or generic 'Enable/Disable' if appropriate.
                    // Actually, let's keep it defined in code: trailing_stop ? 'Open' : 'Close'
                    const tsText = Number(trailing_stop) ? '✅' : '❌';

                    parts.push(`- **${t('debate.analysis.risk_mgmt')}** (${t('debate.analysis.entry')}:${stop_loss || 'N/A'}% | ${t('debate.analysis.exit')}:${take_profit || 'N/A'}% | ${t('debate.trailing_stop')}:${tsText} | ${t('debate.analysis.risk_control')}:${position_sizing || 'N/A'})`);
                  }
                }

                // Special handling for Strategy Analyst (Neutral) top-level strategy
                if (strategy) {
                  const { stance, conviction, target_position, holding_period } = strategy;
                  const stanceMap: Record<string, string> = {
                    'buy': 'debate.actions.buy', 'sell': 'debate.actions.sell', 'hold': 'debate.actions.hold',
                    'strong_buy': 'debate.actions.strong_buy', 'cautious_buy': 'debate.actions.cautious_buy',
                    'cautious_hold': 'debate.actions.cautious_hold', 'reduce': 'debate.actions.reduce'
                  };
                  const stanceText = t(stanceMap[String(stance || '').toLowerCase()] || 'debate.actions.hold');

                  parts.push(t('debate.analysis.strategy_title', { strategy: stanceText }));
                  if (conviction) parts.push(`- **${t('debate.analysis.confidence')}:** ${(conviction * 100).toFixed(0)}%`);
                  if (target_position) parts.push(`- **${t('debate.analysis.target_position')}:** ${(target_position * 100).toFixed(0)}%`);
                  if (holding_period) parts.push(`- **${t('debate.analysis.holding_period')}:** ${holding_period}`);
                }

                if (consensus_summary) {
                  const { bullish_factors, bearish_factors, decisive_factors } = consensus_summary;
                  parts.push(t('debate.analysis.consensus_title'));
                  if (Array.isArray(bullish_factors) && bullish_factors.length > 0) parts.push(`- **${t('debate.analysis.bullish')}:** ${bullish_factors.join('、')}`);
                  if (Array.isArray(bearish_factors) && bearish_factors.length > 0) parts.push(`- **${t('debate.analysis.bearish')}:** ${bearish_factors.join('、')}`);
                  if (Array.isArray(decisive_factors) && decisive_factors.length > 0) parts.push(`- **${t('debate.analysis.core_impact')}:** ${decisive_factors.join('、')}`);
                }

                // Handle Cross Debate Response
                if (cross_debate_response) {
                  parts.push(t('debate.analysis.cross_response_title'));
                  // Extract nested fields that shouldn't be treated as peer responses
                  const { balanced_adjustment, key_insights: innerKeyInsights, ...responses } = asLooseRecord(cross_debate_response);

                  Object.entries(responses).forEach(([target, response]) => {
                    if (!isLooseRecord(response)) return;
                    const targetName = target.replace('to_', '').replace('_', ' ').toUpperCase();
                    parts.push(`#### ${t('debate.analysis.dialogue_to', { target: targetName })}:`);
                    if (response.acknowledge) parts.push(`- **${t('debate.analysis.acknowledge')}:** ${response.acknowledge}`);
                    if (response.challenge) parts.push(`- **${t('debate.analysis.challenge')}:** ${response.challenge}`);
                    if (response.disagreement) parts.push(`- **${t('debate.analysis.disagreement')}:** ${response.disagreement}`);
                    if (response.suggestion) parts.push(`- **${t('debate.analysis.suggestion')}:** ${response.suggestion}`);
                    if (response.compromise) parts.push(`- **${t('debate.analysis.compromise')}:** ${response.compromise}`);
                    if (response.risk_appetite) parts.push(`- **${t('debate.analysis.risk_appetite')}:** ${response.risk_appetite}`);
                  });

                  // Handle nested balanced_adjustment if present
                  if (balanced_adjustment) {
                    parts.push(t('debate.analysis.score_adjustment_title'));
                    if (balanced_adjustment.scorecard_revision) {
                      const rev = balanced_adjustment.scorecard_revision;
                      parts.push(`- **${t('debate.analysis.total_score')}:** ${rev.total_score} (${rev.rating})`);
                      if (rev.fundamentals) parts.push(`- **${t('debate.analysis.fundamentals')}:** ${typeof rev.fundamentals === 'string' ? rev.fundamentals : JSON.stringify(rev.fundamentals)}`);
                    }
                    if (balanced_adjustment.strategy_refinement) {
                      const ref_strat = balanced_adjustment.strategy_refinement;
                      const stanceMap: Record<string, string> = {
                        'buy': 'debate.actions.buy', 'sell': 'debate.actions.sell', 'hold': 'debate.actions.hold',
                        'strong_buy': 'debate.actions.strong_buy', 'cautious_buy': 'debate.actions.cautious_buy',
                        'cautious_hold': 'debate.actions.cautious_hold', 'reduce': 'debate.actions.reduce',
                        'wait': 'debate.actions.hold'
                      };
                      const stanceText = t(stanceMap[ref_strat.stance?.toLowerCase()] || 'debate.actions.hold');
                      parts.push(`- **${t('debate.analysis.strategy_refinement')}:** ${stanceText} (${t('debate.analysis.target_position')}: ${(ref_strat.target_position * 100).toFixed(0)}%)`);
                      if (ref_strat.rationale) parts.push(`- **${t('debate.analysis.rationale')}:** ${ref_strat.rationale}`);
                    }
                  }

                  // Handle nested key_insights if present
                  if (innerKeyInsights && Array.isArray(innerKeyInsights)) {
                    parts.push(`**${t('debate.analysis.key_insights')}:**`);
                    innerKeyInsights.forEach((k: string) => parts.push(`- ${k}`));
                  }
                }

                // Handle Neutral Analyst Cross Debate Analysis (Special Format)
                if (cross_debate_analysis) {
                  const { overall_assessment, critique_of_other_roles, balanced_adjustments, mediation_summary } = cross_debate_analysis;

                  parts.push(t('debate.analysis.cross_analysis_title'));
                  if (overall_assessment) parts.push(`**${t('debate.analysis.overall_assessment')}:** ${overall_assessment}`);

                  if (critique_of_other_roles) {
                    parts.push(`#### ${t('debate.analysis.role_critique')}`);
                    Object.entries(critique_of_other_roles).forEach(([role, critique]) => {
                      if (!isLooseRecord(critique)) return;
                      const roleName = role.replace(/_/g, ' ').toUpperCase();
                      parts.push(`**${roleName}:**`);
                      if (critique.valid_points && Array.isArray(critique.valid_points)) {
                        parts.push(`- ${t('debate.analysis.valid_points')}: ${critique.valid_points.join('; ')}`);
                      }
                      if (critique.overly_optimistic && Array.isArray(critique.overly_optimistic)) {
                        parts.push(`- ${t('debate.analysis.overly_optimistic')}: ${critique.overly_optimistic.join('; ')}`);
                      }
                      if (critique.overly_pessimistic && Array.isArray(critique.overly_pessimistic)) {
                        parts.push(`- ${t('debate.analysis.overly_pessimistic')}: ${critique.overly_pessimistic.join('; ')}`);
                      }
                      if (critique.overly_aggressive && Array.isArray(critique.overly_aggressive)) {
                        parts.push(`- ${t('debate.analysis.overly_aggressive')}: ${critique.overly_aggressive.join('; ')}`);
                      }
                      if (critique.overly_conservative && Array.isArray(critique.overly_conservative)) {
                        parts.push(`- ${t('debate.analysis.overly_conservative')}: ${critique.overly_conservative.join('; ')}`);
                      }
                      if (critique.suggested_adjustment) {
                        parts.push(`- ${t('debate.analysis.suggested_adjustment')}: ${critique.suggested_adjustment}`);
                      }
                    });
                  }

                  if (balanced_adjustments) {
                    parts.push(`#### ${t('debate.analysis.score_adjustment_title')}`); // Reuse title key? Or specific 'Balanced Adjustment'
                    // Using 'score_adjustment_title' as it fits '平衡调整' close enough for now or use hardcoded if specific needed.
                    // I'll assume 'score_adjustment_title' is fine or plain text fallback.
                    if (balanced_adjustments.scorecard_revision) {
                      const rev = balanced_adjustments.scorecard_revision;
                      parts.push(`- **${t('debate.analysis.rating')}:** ${t('debate.analysis.total_score')}${rev.total_score} (${rev.rating})`);
                      if (rev.change_from_previous) parts.push(`  *${t('debate.analysis.change')}:* ${rev.change_from_previous}`);
                    }
                    if (balanced_adjustments.strategy_update) {
                      const s_update = balanced_adjustments.strategy_update;
                      const stanceMap: Record<string, string> = {
                        'buy': 'debate.actions.buy', 'sell': 'debate.actions.sell', 'hold': 'debate.actions.hold',
                        'strong_buy': 'debate.actions.strong_buy', 'cautious_buy': 'debate.actions.cautious_buy',
                        'cautious_hold': 'debate.actions.cautious_hold', 'reduce': 'debate.actions.reduce',
                        'wait': 'debate.actions.hold'
                      };
                      const stanceText = t(stanceMap[s_update.stance?.toLowerCase()] || 'debate.actions.hold');
                      parts.push(`- **${t('debate.analysis.strategy_refinement')}:** ${stanceText} (${t('debate.analysis.target_position')}:${(s_update.target_position * 100).toFixed(0)}%)`);
                      if (s_update.rationale) parts.push(`  *${t('debate.analysis.rationale')}:* ${s_update.rationale}`);
                    }
                  }

                  if (mediation_summary) {
                    parts.push(`#### ${t('debate.analysis.mediation_summary')}`);
                    if (mediation_summary.core_compromise) parts.push(`- **${t('debate.analysis.core_compromise')}:** ${mediation_summary.core_compromise}`);
                    if (mediation_summary.to_all_parties) parts.push(`- **${t('debate.analysis.to_all_parties')}:** ${mediation_summary.to_all_parties}`);
                  }
                }

                if (weighted_analysis) {
                  // ... (simplified loop for weighted analysis)
                  parts.push(t('debate.analysis.weighted_score_title'));
                  // Or keep original logic if possible, but map keys.
                  // Given complexity, I will keep the original logic but update labels using `t`.
                  // "多头", "空头" etc.
                  const scoresTrans = Object.entries(weighted_analysis)
                    .filter(([key]) => key.endsWith('_score') || key === 'weighted_total')
                    .map(([key, val]) => {
                      let label = key.replace(/_/g, ' ').toUpperCase();
                      if (key.includes('bull')) label = t('debate.roles.bull_researcher');
                      else if (key.includes('bear')) label = t('debate.roles.bear_researcher');
                      else if (key.includes('aggressive')) label = t('debate.analysis.overly_aggressive').replace('⚠️ ', ''); // Hacky but works
                      else if (key.includes('conservative')) label = t('debate.analysis.overly_conservative').replace('⚠️ ', '');
                      else if (key === 'weighted_total') label = t('debate.analysis.total_score');
                      return `${label}:${val}`;
                    });
                  parts.push(`- ${scoresTrans.join(' | ')}`);
                }

                if (balanced_scorecard) {
                  parts.push(t('debate.analysis.balanced_scorecard_title'));
                  parts.push(`- **${t('debate.analysis.total_score')}:** ${balanced_scorecard.total_score} (${t('debate.analysis.rating')}: ${balanced_scorecard.rating})`);
                  if (balanced_scorecard.fundamentals) parts.push(`- **${t('debate.analysis.fundamentals')}:** ${balanced_scorecard.fundamentals.subtotal}`);
                  if (balanced_scorecard.technical) parts.push(`- **${t('debate.analysis.technical')}:** ${balanced_scorecard.technical.subtotal}`);
                  if (balanced_scorecard.valuation) parts.push(`- **${t('debate.analysis.valuation')}:** ${balanced_scorecard.valuation.subtotal}`);
                }

                if (synthesis) {
                  parts.push(t('debate.analysis.synthesis_title'));
                  if (synthesis.decisive_factors && Array.isArray(synthesis.decisive_factors)) {
                    parts.push(`- **${t('debate.analysis.decisive_factors')}:** ${synthesis.decisive_factors.join('; ')}`);
                  }
                }

                if (execution_plan) {
                  const { entry_strategy, exit_strategy, risk_mitigation } = execution_plan;
                  parts.push(t('debate.analysis.execution_title'));
                  const entryStr = isLooseRecord(entry_strategy) ?
                    `${entry_strategy.method || ''} (${entry_strategy.first_batch || (isLooseRecord(entry_strategy.batch1) ? entry_strategy.batch1.ratio : undefined) || 'N/A'})` : entry_strategy;

                  parts.push(`- ${t('debate.analysis.entry')}: ${entryStr || 'N/A'}`);
                  parts.push(`- ${t('debate.analysis.exit')}: ${exit_strategy || 'N/A'}`);
                  parts.push(`- ${t('debate.analysis.risk_control')}: ${risk_mitigation || 'N/A'}`);
                }

                if (balanced_recommendation) {
                  parts.push(t('debate.analysis.final_rec_title'));
                  const actionMap: Record<string, string> = {
                    'buy': 'debate.actions.buy', 'sell': 'debate.actions.sell', 'hold': 'debate.actions.hold',
                    'strong_buy': 'debate.actions.strong_buy', 'cautious_buy': 'debate.actions.cautious_buy',
                    'cautious_hold': 'debate.actions.cautious_hold', 'reduce': 'debate.actions.reduce',
                    'wait': 'debate.actions.hold'
                  };

                  const actionText = t(actionMap[balanced_recommendation.action?.toLowerCase()] || 'debate.actions.hold');
                  parts.push(`- **${t('debate.analysis.action')}:** ${actionText}`);
                  parts.push(`- **${t('debate.analysis.rationale')}:** ${balanced_recommendation.rationale}`);
                }

                if (recommendation && !final_decision && !strategy && !balanced_recommendation) {
                  const { action, suggested_entry, stop_loss, take_profit, holding_period } = recommendation;
                  const recMap: Record<string, string> = {
                    'buy': 'debate.actions.suggested_buy', 'sell': 'debate.actions.suggested_sell',
                    'hold': 'debate.actions.suggested_view', 'wait': 'debate.actions.suggested_view'
                  };
                  const recommendationAction = String(action || '').toLowerCase();
                  const actionZh = t(recMap[recommendationAction] || 'debate.actions.suggested_view');
                  parts.push(t('debate.analysis.rec_title', { action: actionZh }));

                  const isHold = recommendationAction === 'hold' || recommendationAction === 'wait';
                  const hasDetails = Boolean(suggested_entry || stop_loss || take_profit || holding_period);

                  if (!isHold || hasDetails) {
                    parts.push(`- **${t('debate.analysis.expectation')}:** ${t('debate.analysis.entry')}:${suggested_entry || 'N/A'} | ${t('debate.analysis.risk_details', { sl: stop_loss || 'N/A', tp: take_profit || 'N/A', ts: '', ps: '' }).split('|').slice(0, 2).join('|')} | ${t('debate.analysis.holding_period')}:${holding_period || 'N/A'}`);
                  }
                }

                // Key Insights (Top level)
                if (key_insights && Array.isArray(key_insights)) {
                  parts.push(t('debate.analysis.insights_title'));
                  key_insights.forEach((k: string) => parts.push(`- ${k}`));
                }

                if (reasoning_chain && Array.isArray(reasoning_chain)) {
                  const formattedChain = reasoning_chain.map((step: LooseValue) => {
                    const sParts = step.split(' → ');
                    if (sParts.length > 1) {
                      return `**${sParts[0]}**  \n  → ${sParts.slice(1).join(' → ')}`;
                    }
                    return `**${step}**`;
                  }).join('\n\n');
                  parts.push(`${t('debate.analysis.chain_title')}\n\n${formattedChain}`);
                } else if (key_arguments && Array.isArray(key_arguments)) {
                  parts.push(`- **${t('debate.analysis.key_arguments')}:** ${key_arguments.map((k: unknown) => typeof k === 'string' ? k : (isLooseRecord(k) ? getLooseString(k, 'point') || '' : '')).join(' | ')}`);
                }

                if (risk_warnings && Array.isArray(risk_warnings)) parts.push(`- **${t('debate.analysis.risk_warnings')}:** ${risk_warnings.map((r: unknown) => {
                  if (!isLooseRecord(r)) return '';
                  return `${getLooseString(r, 'risk') || ''}(${getLooseString(r, 'severity') || ''})`;
                }).join(' | ')}`);

                content = parts.length > 0 ? parts.join('\n') : JSON.stringify(analysisData, null, 2);
              }
            }

            // Post-process: Format any "步骤X" patterns for better readability
            if (content && typeof content === 'string') {
              content = content.replace(/(步骤\d+[:：][^→]+)(→)/g, '$1  \n  $2');
            }

            return (
              <AgentCard
                key={msg.message_id || Math.random().toString()}
                role={msg.agent_role}
                content={content || t('brain.no_content')}
                timestamp={msg.created_at}
                rawAnalysis={msg.analysis}
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
      )
    },
    {
      key: 'decision',
      label: (
        <Space>
          {t('debate.decision_tab')}
          {decisions.length > 0 && <Badge count={decisions.length} style={{ backgroundColor: '#52c41a' }} />}
        </Space>
      ),
      children: renderDecisionContent()
    }
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
          padding-left: 12px;
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
