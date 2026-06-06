import React from 'react';
import {
  App as AntdApp,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Alert,
  Input,
  InputNumber,
  List,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  TimePicker,
  Tooltip,
  Typography,
} from 'antd';
import { ExperimentOutlined, ExclamationCircleOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTranslation } from 'react-i18next';

import {
  marketWatchApi,
  MarketWatchMarkdownDocument,
  MarketWatchEvent,
  MarketWatchSourceConfig,
  MarketWatchSettings,
  MarketWatchSettingsUpdate,
  MarketWatchWsMessage,
  WatchAiDecision,
} from '../api/marketWatch';
import { websocketTicketApi } from '../api/websocketTicket';
import { formatErrorMessage } from '../utils/errorUtils';

const { Text } = Typography;

type MarketWatchSettingsFormValues = Omit<
  MarketWatchSettingsUpdate,
  'scan_start_time' | 'scan_end_time' | 'data_sources' | 'news_sources'
> & {
  scan_start_time?: Dayjs | string | null;
  scan_end_time?: Dayjs | string | null;
};

type MarketWatchSourcePreviewFormValues = {
  source_url: string;
  content_selectors?: string[];
  cleanup_patterns?: string[];
};

type CopyableMarketWatchSettingsField = 'data_sources' | 'news_sources';
type MarketWatchSourceField = 'data_sources' | 'news_sources';

const eventStatusColor: Record<string, string> = {
  success: 'green',
  skipped: 'gold',
  failed: 'red',
};
const MAX_SOURCE_DOCUMENT_ROUNDS = 5;
const sourceDocumentMarkdownStyle: React.CSSProperties = {
  maxHeight: 260,
  overflowY: 'auto',
  marginBottom: 0,
  lineHeight: 1.65,
  wordBreak: 'break-word',
  WebkitOverflowScrolling: 'touch',
  touchAction: 'pan-y',
};
const sourceDocumentRawStyle: React.CSSProperties = {
  ...sourceDocumentMarkdownStyle,
  whiteSpace: 'pre-wrap',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
};
const sourceDocumentUrlStyle: React.CSSProperties = {
  wordBreak: 'break-all',
};
const marketWatchCardStyle: React.CSSProperties = {
  height: 'min(728px, calc(100vh - 180px))',
  minHeight: 546,
  display: 'flex',
  flexDirection: 'column',
};
const marketWatchCardBodyStyle: React.CSSProperties = {
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
};
const marketWatchScrollablePanelStyle: React.CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflowY: 'auto',
  paddingRight: 8,
};
const sourceConfigDrawerBodyStyle: React.CSSProperties = {
  overflowY: 'auto',
  paddingRight: 8,
  WebkitOverflowScrolling: 'touch',
  touchAction: 'pan-y',
};
const sourceConfigRawStyle: React.CSSProperties = {
  ...sourceDocumentRawStyle,
  maxHeight: 'calc(100vh - 360px)',
  minHeight: 360,
};
const sourceConfigListStyle: React.CSSProperties = {
  maxHeight: 220,
  overflowY: 'auto',
  paddingRight: 4,
};
const watchAiActionColor: Record<string, string> = {
  ignore: 'default',
  monitor: 'blue',
  start_debate: 'volcano',
};
const watchAiUrgencyColor: Record<string, string> = {
  low: 'green',
  medium: 'gold',
  high: 'red',
};

type WatchAiDecisionDisplayItem = {
  stockCode: string;
  stockName: string;
  action: string;
  confidence: number | null;
  urgency: string;
  triggerReason: string;
  evidenceSummary: string;
  debateParameters: WatchAiDecision['debate_parameters'] | null;
};

const formatDateTime = (value?: string | null) => {
  if (!value) return '-';
  return dayjs(value).format('YYYY-MM-DD HH:mm:ss');
};

const parseScanTime = (value?: string | null) => {
  if (!value) return undefined;
  return dayjs(`2000-01-01T${value}:00`);
};

const formatScanTime = (value?: Dayjs | string | null) => {
  if (!value) return undefined;
  return dayjs.isDayjs(value) ? value.format('HH:mm') : value;
};

const formatConfidence = (value: number | null) => {
  if (value === null || Number.isNaN(value)) {
    return '-';
  }
  return `${Math.round(value * 100)}%`;
};

const isWatchAiDecision = (value: WatchAiDecision | null | undefined): value is WatchAiDecision => {
  return typeof value?.stock_code === 'string' && value.stock_code.trim().length > 0;
};

const normalizeWatchAiDecisions = (
  value: MarketWatchEvent['watch_ai_decision'],
): WatchAiDecisionDisplayItem[] => {
  const decisions = Array.isArray(value) ? value : value ? [value] : [];

  return decisions.filter(isWatchAiDecision).map((decision) => ({
    stockCode: decision.stock_code,
    stockName: decision.stock_name || decision.stock_code,
    action: decision.action || '-',
    confidence: typeof decision.confidence === 'number' ? decision.confidence : null,
    urgency: decision.urgency || '-',
    triggerReason: decision.trigger_reason || '-',
    evidenceSummary: decision.evidence_summary || '-',
    debateParameters: decision.debate_parameters ?? null,
  }));
};

const dedupeSources = (sources: MarketWatchSourceConfig[]) => {
  const seen = new Set<string>();
  return sources.filter((source) => {
    const normalizedSource = {
      ...source,
      content_selectors: source.content_selectors ?? [],
      cleanup_patterns: Array.from(new Set((source.cleanup_patterns ?? []).map((item) => item.trim()).filter(Boolean))),
    };
    const key = normalizedSource.url;
    if (!normalizedSource.url || seen.has(key)) {
      return false;
    }
    seen.add(key);
    Object.assign(source, normalizedSource);
    return true;
  });
};

const formatSourcePreviewConfig = (values: MarketWatchSourcePreviewFormValues) => {
  return [values.source_url, ...(values.content_selectors ?? [])]
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' @@ ');
};

const sourceKey = (source: MarketWatchSourceConfig) => {
  return source.url;
};

const sourceFormValuesToSource = (values: MarketWatchSourcePreviewFormValues): MarketWatchSourceConfig => {
  return {
    url: values.source_url.trim(),
    content_selectors: Array.from(new Set((values.content_selectors ?? []).map((item) => item.trim()).filter(Boolean))),
    cleanup_patterns: Array.from(new Set((values.cleanup_patterns ?? []).map((item) => item.trim()).filter(Boolean))),
  };
};

const settingsToFormValues = (settings: MarketWatchSettings): MarketWatchSettingsFormValues => {
  return {
    ...settings,
    scan_start_time: parseScanTime(settings.scan_start_time),
    scan_end_time: parseScanTime(settings.scan_end_time),
  };
};

const dedupeSettingsValues = (values: MarketWatchSettingsFormValues): MarketWatchSettingsFormValues => {
  return values;
};

const settingsFormValuesToPayload = (values: MarketWatchSettingsFormValues): MarketWatchSettingsUpdate => {
  const dedupedValues = dedupeSettingsValues(values);
  return {
    ...dedupedValues,
    scan_start_time: formatScanTime(values.scan_start_time),
    scan_end_time: formatScanTime(values.scan_end_time),
  };
};

const buildMarketWatchWsUrl = (ticket: string) => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/v1/market-watch/ws?ticket=${encodeURIComponent(ticket)}`;
};

const settingLabel = (label: string, tooltip: string) => (
  <Space size={4}>
    <span>{label}</span>
    <Tooltip title={tooltip}>
      <ExclamationCircleOutlined style={{ color: '#8c8c8c' }} />
    </Tooltip>
  </Space>
);

export const MarketWatchPage: React.FC = () => {
  const { message } = AntdApp.useApp();
  const { t } = useTranslation();
  const [settingsForm] = Form.useForm<MarketWatchSettingsFormValues>();
  const [sourcePreviewForm] = Form.useForm<MarketWatchSourcePreviewFormValues>();
  const [settings, setSettings] = React.useState<MarketWatchSettings | null>(null);
  const [sourceDocumentRounds, setSourceDocumentRounds] = React.useState<MarketWatchMarkdownDocument[][]>([]);
  const [sourcePreviewDocument, setSourcePreviewDocument] = React.useState<MarketWatchMarkdownDocument | null>(null);
  const [events, setEvents] = React.useState<MarketWatchEvent[]>([]);
  const [eventsLoading, setEventsLoading] = React.useState(false);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [savingSettings, setSavingSettings] = React.useState(false);
  const [sourcePreviewOpen, setSourcePreviewOpen] = React.useState(false);
  const [sourcePreviewLoading, setSourcePreviewLoading] = React.useState(false);
  const [renderSourceMarkdown, setRenderSourceMarkdown] = React.useState(false);
  const [editingSourceField, setEditingSourceField] = React.useState<MarketWatchSourceField | null>(null);
  const [editingSourceKey, setEditingSourceKey] = React.useState<string | null>(null);
  const socketRef = React.useRef<WebSocket | null>(null);
  const reconnectTimerRef = React.useRef<number | null>(null);
  const sourceDocuments = React.useMemo(() => sourceDocumentRounds.flat(), [sourceDocumentRounds]);

  const loadEvents = React.useCallback(async () => {
    setEventsLoading(true);
    try {
      const latestEvents = await marketWatchApi.getEvents();
      setEvents(latestEvents);
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.load_failed'));
    } finally {
      setEventsLoading(false);
    }
  }, [message, t]);

  const loadSettings = React.useCallback(async () => {
    try {
      const nextSettings = await marketWatchApi.getSettings();
      setSettings(nextSettings);
      settingsForm.setFieldsValue(settingsToFormValues(nextSettings));
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.load_failed'));
    }
  }, [message, settingsForm, t]);

  const loadDashboard = React.useCallback(async () => {
    setEventsLoading(true);
    try {
      const [nextSettings, latestEvents] = await Promise.all([
        marketWatchApi.getSettings(),
        marketWatchApi.getEvents(),
      ]);
      setSettings(nextSettings);
      settingsForm.setFieldsValue(settingsToFormValues(nextSettings));
      setEvents(latestEvents);
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.load_failed'));
    } finally {
      setEventsLoading(false);
    }
  }, [message, settingsForm, t]);

  React.useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  React.useEffect(() => {
    let closedByEffect = false;

    const connect = async () => {
      try {
        const ticketResponse = await websocketTicketApi.createMarketWatch();
        if (closedByEffect) {
          return;
        }

        const socket = new WebSocket(buildMarketWatchWsUrl(ticketResponse.ticket));
        socketRef.current = socket;

        socket.onopen = () => {
          void loadEvents();
        };

        socket.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data) as MarketWatchWsMessage | { type?: string };
            if (payload.type === 'market_watch_documents' && 'documents' in payload) {
              setSourceDocumentRounds((currentRounds) => {
                return [payload.documents, ...currentRounds].slice(0, MAX_SOURCE_DOCUMENT_ROUNDS);
              });
              return;
            }
            if (payload.type !== 'market_watch_event' || !('event' in payload)) {
              return;
            }
            setEvents((currentEvents) => [payload.event, ...currentEvents].slice(0, 50));
          } catch {
            // Ignore malformed real-time messages; historical events remain queryable.
          }
        };

        socket.onclose = () => {
          if (closedByEffect) {
            return;
          }
          reconnectTimerRef.current = window.setTimeout(() => {
            void connect();
          }, 3000);
          void loadEvents();
        };
      } catch {
        if (closedByEffect) {
          return;
        }
        reconnectTimerRef.current = window.setTimeout(() => {
          void connect();
        }, 3000);
      }
    };

    void connect();

    return () => {
      closedByEffect = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      socketRef.current?.close();
    };
  }, [loadEvents]);

  const handleSaveSettings = async () => {
    try {
      const values = await settingsForm.validateFields();
      setSavingSettings(true);
      const dedupedValues = dedupeSettingsValues(values);
      const updated = await marketWatchApi.updateSettings(settingsFormValuesToPayload(dedupedValues));
      setSettings(updated);
      settingsForm.setFieldsValue(settingsToFormValues(updated));
      setSettingsOpen(false);
      message.success(t('market_watch.settings_saved'));
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.settings_save_failed'));
    } finally {
      setSavingSettings(false);
    }
  };

  const copySettingValues = async (fieldName: CopyableMarketWatchSettingsField) => {
    const text = JSON.stringify(settings?.[fieldName] ?? [], null, 2);
    if (!text) {
      message.warning(t('market_watch.copy_empty'));
      return;
    }

    try {
      await navigator.clipboard.writeText(text);
      message.success(t('market_watch.copy_success'));
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.copy_failed'));
    }
  };

  const loadSourceIntoForm = (fieldName: MarketWatchSourceField, source: MarketWatchSourceConfig) => {
    sourcePreviewForm.setFieldsValue({
      source_url: source.url,
      content_selectors: source.content_selectors ?? [],
      cleanup_patterns: source.cleanup_patterns ?? [],
    });
    setEditingSourceField(fieldName);
    setEditingSourceKey(sourceKey(source));
    setSourcePreviewDocument(null);
  };

  const resetSourceEditor = () => {
    sourcePreviewForm.resetFields();
    setEditingSourceField(null);
    setEditingSourceKey(null);
    setSourcePreviewDocument(null);
  };

  const handleSourcePreview = async () => {
    try {
      const values = await sourcePreviewForm.validateFields();
      setSourcePreviewLoading(true);
      setSourcePreviewDocument(null);
      const document = await marketWatchApi.previewSource({
        source_config: formatSourcePreviewConfig(values),
        cleanup_patterns: values.cleanup_patterns ?? [],
      });
      setSourcePreviewDocument(document);
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.source_config_preview_failed'));
    } finally {
      setSourcePreviewLoading(false);
    }
  };

  const handleAddSourceConfig = async (fieldName: 'data_sources' | 'news_sources') => {
    try {
      const values = await sourcePreviewForm.validateFields();
      setSavingSettings(true);
      const currentSettings = await marketWatchApi.getSettings();
      const currentValues = currentSettings[fieldName] ?? [];
      const nextSource = sourceFormValuesToSource(values);
      const withoutCurrent = currentValues.filter((source) => {
        if (source.url === nextSource.url) {
          return false;
        }
        return !(editingSourceField === fieldName && editingSourceKey && sourceKey(source) === editingSourceKey);
      });
      const nextPayload = {
        [fieldName]: dedupeSources([...withoutCurrent, nextSource]),
      };
      const updated = await marketWatchApi.updateSettings(nextPayload);
      setSettings(updated);
      settingsForm.setFieldsValue(settingsToFormValues(updated));
      loadSourceIntoForm(fieldName, nextSource);
      message.success(t(`market_watch.${fieldName === 'data_sources' ? 'data_source_saved' : 'news_source_saved'}`));
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.source_config_save_failed'));
    } finally {
      setSavingSettings(false);
    }
  };

  const handleDeleteSourceConfig = async () => {
    if (!editingSourceField || !editingSourceKey) {
      message.warning(t('market_watch.source_config_delete_empty'));
      return;
    }
    try {
      setSavingSettings(true);
      const currentSettings = await marketWatchApi.getSettings();
      const nextSources = (currentSettings[editingSourceField] ?? []).filter((source) => sourceKey(source) !== editingSourceKey);
      const updated = await marketWatchApi.updateSettings({ [editingSourceField]: nextSources });
      setSettings(updated);
      settingsForm.setFieldsValue(settingsToFormValues(updated));
      resetSourceEditor();
      message.success(t('market_watch.source_config_deleted'));
    } catch (error) {
      message.error(formatErrorMessage(error) || t('market_watch.source_config_delete_failed'));
    } finally {
      setSavingSettings(false);
    }
  };

  const sourcePreviewStatus = React.useMemo(() => {
    if (!sourcePreviewDocument) {
      return null;
    }
    if (sourcePreviewDocument.error) {
      return {
        type: 'error' as const,
        message: t('market_watch.source_config_preview_error'),
        description: sourcePreviewDocument.error,
      };
    }
    if (sourcePreviewDocument.markdown.trim()) {
      return {
        type: 'success' as const,
        message: t('market_watch.source_config_preview_success'),
      };
    }
    return {
      type: 'warning' as const,
      message: t('market_watch.source_config_preview_empty'),
      description: t('market_watch.source_config_preview_empty_desc'),
    };
  }, [sourcePreviewDocument, t]);

  const eventColumns: ColumnsType<MarketWatchEvent> = [
    {
      title: t('market_watch.columns.time'),
      dataIndex: 'created_at',
      width: 180,
      render: formatDateTime,
    },
    {
      title: t('market_watch.columns.event'),
      dataIndex: 'event_type',
      width: 140,
      render: (value: string) => <Tag color="blue">{value}</Tag>,
    },
    {
      title: t('market_watch.columns.status'),
      dataIndex: 'status',
      width: 100,
      render: (value: string) => <Tag color={eventStatusColor[value] || 'default'}>{value}</Tag>,
    },
  ];

  const latestWatchAiDecision = React.useMemo(() => {
    for (const event of events) {
      if (event.event_type !== 'ai_decision') {
        continue;
      }
      const decisions = normalizeWatchAiDecisions(event.watch_ai_decision);
      if (decisions.length > 0) {
        return { event, decisions };
      }
    }

    return null;
  }, [events]);

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
        <Button
          icon={<ExperimentOutlined />}
          onClick={() => {
            setSourcePreviewOpen(true);
          }}
        >
          {t('market_watch.source_config_action')}
        </Button>
        <Button icon={<ReloadOutlined />} loading={eventsLoading} onClick={loadDashboard}>
          {t('market_watch.refresh')}
        </Button>
        <Button
          icon={<SettingOutlined />}
          onClick={() => {
            void loadSettings();
            setSettingsOpen(true);
          }}
        >
          {t('market_watch.settings')}
        </Button>
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card
            title={t('market_watch.source_documents')}
            style={marketWatchCardStyle}
            styles={{ body: marketWatchCardBodyStyle }}
            extra={
              <Space size={8}>
                <Text type="secondary">{t('market_watch.render_markdown')}</Text>
                <Switch size="small" checked={renderSourceMarkdown} onChange={setRenderSourceMarkdown} />
              </Space>
            }
          >
            <div style={marketWatchScrollablePanelStyle}>
              <List
                dataSource={sourceDocuments}
                locale={{ emptyText: t('market_watch.no_source_documents') }}
                renderItem={(item) => (
                  <List.Item>
                    <Space direction="vertical" size={6} style={{ width: '100%' }}>
                      <Space size={[8, 4]} wrap>
                        <Tag color={item.source_type === 'news' ? 'blue' : 'green'}>
                          {t(`market_watch.source_types.${item.source_type}`)}
                        </Tag>
                        <Text type="secondary">{formatDateTime(item.captured_at)}</Text>
                        {item.status ? <Text type="secondary">HTTP {item.status}</Text> : null}
                      </Space>
                      <Text strong>{item.title || item.final_url || item.url}</Text>
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Text type="secondary">
                          {t('market_watch.source_url')}:{' '}
                          <Text copyable={{ text: item.url }} style={sourceDocumentUrlStyle}>
                            {item.url}
                          </Text>
                        </Text>
                        {item.final_url && item.final_url !== item.url ? (
                          <Text type="secondary">
                            {t('market_watch.final_url')}:{' '}
                            <Text copyable={{ text: item.final_url }} style={sourceDocumentUrlStyle}>
                              {item.final_url}
                            </Text>
                          </Text>
                        ) : null}
                      </Space>
                      {item.error ? <Text type="danger">{item.error}</Text> : null}
                      {renderSourceMarkdown ? (
                        <div style={sourceDocumentMarkdownStyle}>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {item.markdown || ''}
                          </ReactMarkdown>
                        </div>
                      ) : (
                        <pre style={sourceDocumentRawStyle}>{item.markdown || ''}</pre>
                      )}
                    </Space>
                  </List.Item>
                )}
              />
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card
            title={t('market_watch.decision_result')}
            style={marketWatchCardStyle}
            styles={{ body: marketWatchCardBodyStyle }}
            extra={
              latestWatchAiDecision ? (
                <Text type="secondary">
                  {t('market_watch.decision_updated_at', {
                    time: formatDateTime(latestWatchAiDecision.event.created_at),
                  })}
                </Text>
              ) : null
            }
            data-testid="watch-ai-decision-card"
          >
            <div style={marketWatchScrollablePanelStyle}>
              {latestWatchAiDecision ? (
                <List
                  dataSource={latestWatchAiDecision.decisions}
                  renderItem={(decision) => (
                    <List.Item>
                      <Space direction="vertical" size={8} style={{ width: '100%' }}>
                        <Space size={[8, 4]} wrap>
                          <Text strong>
                            {decision.stockName} ({decision.stockCode})
                          </Text>
                          <Tag color={watchAiActionColor[decision.action] || 'default'}>
                            {t(`market_watch.ai_actions.${decision.action}`, {
                              defaultValue: decision.action,
                            })}
                          </Tag>
                          <Tag color={watchAiUrgencyColor[decision.urgency] || 'default'}>
                            {t(`market_watch.urgencies.${decision.urgency}`, {
                              defaultValue: decision.urgency,
                            })}
                          </Tag>
                          <Text type="secondary">
                            {t('market_watch.confidence')}: {formatConfidence(decision.confidence)}
                          </Text>
                        </Space>
                        <Descriptions column={1} size="small" colon={false}>
                          <Descriptions.Item label={t('market_watch.trigger_reason')}>
                            <Text style={{ whiteSpace: 'normal' }}>{decision.triggerReason}</Text>
                          </Descriptions.Item>
                          <Descriptions.Item label={t('market_watch.evidence_summary')}>
                            <Text style={{ whiteSpace: 'normal' }}>{decision.evidenceSummary}</Text>
                          </Descriptions.Item>
                        </Descriptions>
                        {decision.debateParameters ? (
                          <Space size={[8, 4]} wrap>
                            <Tag>
                              {t('market_watch.debate_fields.frequency')}:&nbsp;
                              {decision.debateParameters.trading_frequency}
                            </Tag>
                            <Tag>
                              {t('market_watch.debate_fields.strategy')}:&nbsp;
                              {decision.debateParameters.trading_strategy}
                            </Tag>
                          </Space>
                        ) : null}
                      </Space>
                    </List.Item>
                  )}
                />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('market_watch.no_ai_decision')} />
              )}
            </div>
          </Card>
        </Col>
      </Row>

      <Card title={t('market_watch.events')}>
        <Table
          rowKey={(record) => record.event_id || `${record.created_at}-${record.event_type}-${record.status}`}
          columns={eventColumns}
          dataSource={events}
          loading={eventsLoading}
          pagination={{ pageSize: 10 }}
          size="small"
        />
      </Card>

      <Drawer
        title={t('market_watch.settings_title')}
        width={520}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        extra={
          <Button type="primary" loading={savingSettings} onClick={handleSaveSettings}>
            {t('market_watch.save')}
          </Button>
        }
      >
        <Form form={settingsForm} layout="vertical" initialValues={settings ? settingsToFormValues(settings) : undefined}>
          <Descriptions column={1} size="small" style={{ marginBottom: 16 }}>
            <Descriptions.Item label={t('market_watch.current_user')}>{settings?.user_id ?? '-'}</Descriptions.Item>
            <Descriptions.Item label={t('market_watch.last_updated')}>
              {formatDateTime(settings?.updated_at)}
            </Descriptions.Item>
          </Descriptions>

          <Form.Item
            name="auto_scan_enabled"
            label={settingLabel(t('market_watch.settings_fields.auto_scan_enabled'), t('market_watch.help.auto_scan_enabled'))}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="scan_interval_seconds"
            label={settingLabel(t('market_watch.settings_fields.scan_interval_seconds'), t('market_watch.help.scan_interval_seconds'))}
            rules={[{ required: true }]}
          >
            <InputNumber min={30} max={3600} step={30} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="scan_non_trading_days"
            label={settingLabel(t('market_watch.settings_fields.scan_non_trading_days'), t('market_watch.help.scan_non_trading_days'))}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item
                name="scan_start_time"
                label={settingLabel(t('market_watch.settings_fields.scan_start_time'), t('market_watch.help.scan_start_time'))}
                rules={[{ required: true }]}
              >
                <TimePicker format="HH:mm" minuteStep={5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="scan_end_time"
                label={settingLabel(t('market_watch.settings_fields.scan_end_time'), t('market_watch.help.scan_end_time'))}
                rules={[{ required: true }]}
              >
                <TimePicker format="HH:mm" minuteStep={5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="auto_launch_debate"
            label={settingLabel(
              t('market_watch.settings_fields.auto_launch_debate'),
              t('market_watch.help.auto_launch_debate'),
            )}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="recent_debate_dedup_enabled"
            label={settingLabel(
              t('market_watch.settings_fields.recent_debate_dedup_enabled'),
              t('market_watch.help.recent_debate_dedup_enabled'),
            )}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="recent_debate_lookback_hours"
            label={settingLabel(
              t('market_watch.settings_fields.recent_debate_lookback_hours'),
              t('market_watch.help.recent_debate_lookback_hours'),
            )}
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={168} step={1} addonAfter={t('market_watch.units.hours')} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="cooldown_minutes"
            label={settingLabel(t('market_watch.settings_fields.cooldown_minutes'), t('market_watch.help.cooldown_minutes'))}
            rules={[{ required: true }]}
          >
            <InputNumber min={0} max={1440} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="cooldown_break_confidence"
            label={settingLabel(
              t('market_watch.settings_fields.cooldown_break_confidence'),
              t('market_watch.help.cooldown_break_confidence'),
            )}
            rules={[{ required: true }]}
          >
            <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="trading_frequency"
            label={settingLabel(t('market_watch.settings_fields.trading_frequency'), t('market_watch.help.trading_frequency'))}
            rules={[{ required: true }]}
          >
            <Select>
              <Select.Option value={t('warehouse.freq_day_trading')}>{t('warehouse.freq_day_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_swing_trading')}>{t('warehouse.freq_swing_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_position_trading')}>{t('warehouse.freq_position_trading')}</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item
            name="trading_strategy"
            label={settingLabel(t('market_watch.settings_fields.trading_strategy'), t('market_watch.help.trading_strategy'))}
            rules={[{ required: true }]}
          >
            <Select>
              <Select.Option value={t('warehouse.strategy_value')}>{t('warehouse.strategy_value')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_growth')}>{t('warehouse.strategy_growth')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_trend')}>{t('warehouse.strategy_trend')}</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Drawer>

      <Drawer
        title={t('market_watch.source_config_title')}
        width="min(960px, 92vw)"
        open={sourcePreviewOpen}
        onClose={() => setSourcePreviewOpen(false)}
        styles={{ body: sourceConfigDrawerBodyStyle }}
        footer={(
          <Space size={8} wrap style={{ width: '100%', justifyContent: 'flex-end' }}>
            <Button onClick={() => setSourcePreviewOpen(false)}>{t('common.cancel')}</Button>
            <Button onClick={resetSourceEditor}>{t('market_watch.source_config_clear_editor')}</Button>
            <Button danger loading={savingSettings} disabled={!editingSourceField} onClick={() => void handleDeleteSourceConfig()}>
              {t('common.delete')}
            </Button>
            <Button type="primary" loading={sourcePreviewLoading} onClick={handleSourcePreview}>
              {t('market_watch.source_config_preview_run')}
            </Button>
            <Button type="primary" ghost loading={savingSettings} onClick={() => void handleAddSourceConfig('data_sources')}>
              {t('market_watch.add_data_source')}
            </Button>
            <Button
              loading={savingSettings}
              style={{ borderColor: '#52c41a', color: '#389e0d' }}
              onClick={() => void handleAddSourceConfig('news_sources')}
            >
              {t('market_watch.add_news_source')}
            </Button>
          </Space>
        )}
      >
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={24} md={12}>
            <Card
              size="small"
              title={t('market_watch.settings_fields.data_sources')}
              extra={<Button size="small" type="link" onClick={() => void copySettingValues('data_sources')}>{t('common.copy')}</Button>}
            >
              <div style={sourceConfigListStyle}>
                <List
                  size="small"
                  dataSource={settings?.data_sources ?? []}
                  locale={{ emptyText: t('market_watch.source_config_empty_list') }}
                  renderItem={(source) => (
                    <List.Item
                      actions={[
                        <Button key="load" size="small" onClick={() => loadSourceIntoForm('data_sources', source)}>
                          {t('market_watch.source_config_load')}
                        </Button>,
                      ]}
                    >
                      <List.Item.Meta
                        title={<Text style={sourceDocumentUrlStyle}>{source.url}</Text>}
                      />
                      <Tag>{t('market_watch.source_config_cleanup_count', { count: source.cleanup_patterns?.length ?? 0 })}</Tag>
                    </List.Item>
                  )}
                />
              </div>
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card
              size="small"
              title={t('market_watch.settings_fields.news_sources')}
              extra={<Button size="small" type="link" onClick={() => void copySettingValues('news_sources')}>{t('common.copy')}</Button>}
            >
              <div style={sourceConfigListStyle}>
                <List
                  size="small"
                  dataSource={settings?.news_sources ?? []}
                  locale={{ emptyText: t('market_watch.source_config_empty_list') }}
                  renderItem={(source) => (
                    <List.Item
                      actions={[
                        <Button key="load" size="small" onClick={() => loadSourceIntoForm('news_sources', source)}>
                          {t('market_watch.source_config_load')}
                        </Button>,
                      ]}
                    >
                      <List.Item.Meta
                        title={<Text style={sourceDocumentUrlStyle}>{source.url}</Text>}
                      />
                      <Tag>{t('market_watch.source_config_cleanup_count', { count: source.cleanup_patterns?.length ?? 0 })}</Tag>
                    </List.Item>
                  )}
                />
              </div>
            </Card>
          </Col>
        </Row>
        <Form form={sourcePreviewForm} layout="vertical">
          <Form.Item
            name="source_url"
            label={t('market_watch.source_url')}
            rules={[{ required: true, message: t('market_watch.validation.source_url_required') }]}
          >
            <Input placeholder={t('market_watch.placeholders.source_url')} />
          </Form.Item>
          <Form.Item
            name="content_selectors"
            label={t('market_watch.source_selectors')}
            tooltip={t('market_watch.help.source_selectors')}
          >
            <Select
              mode="tags"
              allowClear
              tokenSeparators={['\n']}
              placeholder={t('market_watch.placeholders.source_selectors')}
            />
          </Form.Item>
          <Form.Item
            name="cleanup_patterns"
            label={t('market_watch.source_cleanup_patterns')}
            tooltip={t('market_watch.help.source_cleanup_patterns')}
          >
            <Select
              mode="tags"
              allowClear
              tokenSeparators={['\n']}
              placeholder={t('market_watch.placeholders.source_cleanup_patterns')}
            />
          </Form.Item>
        </Form>

        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {sourcePreviewStatus ? (
            <Alert
              showIcon
              type={sourcePreviewStatus.type}
              message={sourcePreviewStatus.message}
              description={sourcePreviewStatus.description}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('market_watch.source_config_no_preview_result')} />
          )}
          {sourcePreviewDocument ? (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space size={[8, 4]} wrap>
                {sourcePreviewDocument.status ? <Text type="secondary">HTTP {sourcePreviewDocument.status}</Text> : null}
                <Text type="secondary">{formatDateTime(sourcePreviewDocument.captured_at)}</Text>
              </Space>
              <Text strong>{sourcePreviewDocument.title || sourcePreviewDocument.final_url || sourcePreviewDocument.url}</Text>
              {sourcePreviewDocument.final_url ? (
                <Text type="secondary">
                  {t('market_watch.final_url')}:{' '}
                  <Text copyable={{ text: sourcePreviewDocument.final_url }} style={sourceDocumentUrlStyle}>
                    {sourcePreviewDocument.final_url}
                  </Text>
                </Text>
              ) : null}
              <pre style={sourceConfigRawStyle}>{sourcePreviewDocument.markdown || ''}</pre>
            </Space>
          ) : null}
        </Space>
      </Drawer>
    </Space>
  );
};
