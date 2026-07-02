import React from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  theme,
} from 'antd';
import {
  ArrowsAltOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  DeleteOutlined,
  FullscreenOutlined,
  MessageOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import ReactMarkdown from 'react-markdown';
import { useTranslation } from 'react-i18next';
import remarkGfm from 'remark-gfm';

import {
  InteractiveResearchAction,
  InteractiveResearchMessage,
  InteractiveResearchRunCreatePayload,
  InteractiveResearchRunSummary,
  interactiveStockPickerApi,
} from '../../api/stockPicker';
import { useWebSocketSubscription } from '../../hooks/useWebSocketSubscription';
import { InteractiveStockPickerUpdateMessage, WebSocketMessage } from '../../services/websocket';
import { formatErrorMessage, getApiErrorDetail } from '../../utils/errorUtils';
import { useFeedback } from '../../hooks/useFeedback';
import './InteractiveResearchTab.css';

const { Text } = Typography;

interface TextFormValues {
  content: string;
  max_iterations: number;
}

const activeStatuses = new Set([
  'drafting_plan',
  'awaiting_plan_approval',
  'researching',
  'awaiting_user_input',
  'reflecting',
  'synthesizing',
]);

const terminalStatuses = new Set(['completed', 'cancelled', 'failed']);
const WEBSOCKET_REFRESH_DEBOUNCE_MS = 1000;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const asRecord = (value: unknown): Record<string, unknown> => (isRecord(value) ? value : {});

const stringifyJsonValue = (value: unknown): string | null => {
  if (!Array.isArray(value) && !isRecord(value)) {
    return null;
  }
  return JSON.stringify(value, null, 2);
};

const parseJsonText = (value: string): string | null => {
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }
  try {
    return stringifyJsonValue(JSON.parse(normalized));
  } catch {
    return null;
  }
};

const formatJsonLikeText = (value: string): string => {
  let indent = 0;
  let inString = false;
  let escaped = false;
  const lines: string[] = [];
  let current = '';

  const pushLine = () => {
    const line = current.trim();
    if (line) {
      lines.push(`${'  '.repeat(Math.max(indent, 0))}${line}`);
    }
    current = '';
  };

  for (const char of value) {
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }
    if (char === '\\') {
      current += char;
      escaped = inString;
      continue;
    }
    if (char === '"') {
      inString = !inString;
      current += char;
      continue;
    }
    if (inString) {
      current += char;
      continue;
    }
    if (char === '{' || char === '[') {
      current += char;
      pushLine();
      indent += 1;
      continue;
    }
    if (char === '}' || char === ']') {
      pushLine();
      indent -= 1;
      current += char;
      continue;
    }
    if (char === ',') {
      current += char;
      pushLine();
      continue;
    }
    if (char === ':') {
      current += ': ';
      continue;
    }
    current += char;
  }
  pushLine();
  return lines.join('\n');
};

const formatToolResultPreview = (value: unknown): string | null => {
  if (Array.isArray(value) || isRecord(value)) {
    return JSON.stringify(value, null, 2);
  }
  if (typeof value !== 'string') {
    return null;
  }
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }
  const parsed = parseJsonText(normalized);
  if (parsed) {
    return parsed;
  }
  if (normalized.startsWith('{') || normalized.startsWith('[')) {
    return formatJsonLikeText(normalized);
  }
  return normalized;
};

const getToolResultPreview = (item: InteractiveResearchMessage): string | null => {
  const payload = asRecord(item.payload);
  return formatToolResultPreview(payload.result_preview);
};

const getToolStartArguments = (item: InteractiveResearchMessage): string | null => {
  const payload = asRecord(item.payload);
  return formatToolResultPreview(payload.arguments);
};

const getToolName = (item: InteractiveResearchMessage): string | null => {
  const payload = asRecord(item.payload);
  const toolName = payload.tool_name;
  return typeof toolName === 'string' && toolName.trim() ? toolName.trim() : null;
};

const getMessageDisplayContent = (item: InteractiveResearchMessage): { content: string; isToolMessage: boolean } => {
  const isToolMessage = item.message_type === 'tool_result' || item.message_type === 'tool_start';
  if (item.message_type === 'tool_result') {
    return { content: getToolResultPreview(item) || item.content || '-', isToolMessage };
  }
  if (item.message_type === 'tool_start') {
    return { content: getToolStartArguments(item) || item.content || '-', isToolMessage };
  }
  return { content: item.markdown || item.content || '-', isToolMessage };
};

const shouldShowFooterExpandAction = (content: string, isToolMessage: boolean): boolean => {
  return !isToolMessage && content.length > 700;
};

const getStatusColor = (status: string) => {
  if (status === 'completed') return 'green';
  if (status === 'cancelled') return 'default';
  if (status === 'failed') return 'red';
  if (status === 'awaiting_plan_approval' || status === 'awaiting_user_input') {
    return 'orange';
  }
  if (status === 'researching' || status === 'reflecting' || status === 'synthesizing') return 'blue';
  return 'default';
};

const getRoleColor = (role: string) => {
  if (role === 'user') return 'blue';
  if (role === 'assistant') return 'green';
  if (role === 'tool') return 'purple';
  return 'default';
};

const getExecutionStatusColor = (status: string) => {
  if (status === 'completed') return 'green';
  if (status === 'failed' || status === 'error') return 'red';
  if (status === 'running' || status === 'started') return 'blue';
  return 'orange';
};

export const InteractiveResearchTab: React.FC = () => {
  const { t } = useTranslation();
  const message = useFeedback();
  const { token } = theme.useToken();
  const [messageForm] = Form.useForm<TextFormValues>();
  const [runs, setRuns] = React.useState<InteractiveResearchRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null);
  const [selectedRun, setSelectedRun] = React.useState<InteractiveResearchRunSummary | null>(null);
  const [messages, setMessages] = React.useState<InteractiveResearchMessage[]>([]);
  const [loadingRuns, setLoadingRuns] = React.useState(false);
  const [loadingDetails, setLoadingDetails] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [actionLoading, setActionLoading] = React.useState<InteractiveResearchAction | null>(null);
  const [deletingRun, setDeletingRun] = React.useState(false);
  const [chatFullscreenOpen, setChatFullscreenOpen] = React.useState(false);
  const [expandedMessage, setExpandedMessage] = React.useState<InteractiveResearchMessage | null>(null);
  const selectedRunIdRef = React.useRef<string | null>(null);
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);
  const websocketRefreshTimerRef = React.useRef<number | null>(null);
  const websocketRefreshInFlightRef = React.useRef(false);
  const websocketRefreshPendingRef = React.useRef(false);

  const getStatusLabel = React.useCallback(
    (status: string) => t(`ai_stock_picker.interactive.statuses.${status}`, { defaultValue: status }),
    [t],
  );

  const getPhaseLabel = React.useCallback(
    (phase: string) => t(`ai_stock_picker.interactive.phases.${phase}`, { defaultValue: phase }),
    [t],
  );

  const activeRun = React.useMemo(
    () => runs.find((item) => activeStatuses.has(item.status)) || null,
    [runs],
  );

  const sortedMessages = React.useMemo(
    () => [...messages].sort((left, right) => left.sequence_no - right.sequence_no),
    [messages],
  );

  const canSendMessage = Boolean(selectedRun && !terminalStatuses.has(selectedRun.status));
  const canApprovePlan = selectedRun?.status === 'awaiting_plan_approval';
  const canCancelRun = Boolean(selectedRun && activeStatuses.has(selectedRun.status));

  React.useEffect(() => {
    selectedRunIdRef.current = selectedRunId;
  }, [selectedRunId]);

  const loadRuns = React.useCallback(async () => {
    setLoadingRuns(true);
    try {
      const data = await interactiveStockPickerApi.listRuns();
      setRuns(data);
      const currentId = selectedRunIdRef.current;
      const preferredRun = data.find((item) => activeStatuses.has(item.status)) || data[0];
      if (!currentId || !data.some((item) => item.run_id === currentId)) {
        setSelectedRunId(preferredRun?.run_id ?? null);
      }
    } finally {
      setLoadingRuns(false);
    }
  }, []);

  const loadRunDetails = React.useCallback(async (runId: string, options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoadingDetails(true);
    }
    try {
      const [runData, messageData] = await Promise.all([
        interactiveStockPickerApi.getRun(runId),
        interactiveStockPickerApi.getMessages(runId),
      ]);
      setSelectedRun(runData);
      setMessages(messageData);
    } finally {
      if (!options?.silent) {
        setLoadingDetails(false);
      }
    }
  }, []);

  const refreshSelectedRun = React.useCallback(async () => {
    await loadRuns();
    if (selectedRunIdRef.current) {
      await loadRunDetails(selectedRunIdRef.current, { silent: true });
    }
  }, [loadRunDetails, loadRuns]);

  const refreshSelectedRunDetails = React.useCallback(async () => {
    const runId = selectedRunIdRef.current;
    if (!runId) {
      return;
    }
    await loadRunDetails(runId, { silent: true });
  }, [loadRunDetails]);

  const flushWebSocketRefresh = React.useCallback(async () => {
    if (websocketRefreshInFlightRef.current) {
      websocketRefreshPendingRef.current = true;
      return;
    }
    websocketRefreshInFlightRef.current = true;
    try {
      await refreshSelectedRunDetails();
    } finally {
      websocketRefreshInFlightRef.current = false;
      if (websocketRefreshPendingRef.current) {
        websocketRefreshPendingRef.current = false;
        window.setTimeout(() => {
          flushWebSocketRefresh().catch(() => undefined);
        }, WEBSOCKET_REFRESH_DEBOUNCE_MS);
      }
    }
  }, [refreshSelectedRunDetails]);

  const scheduleWebSocketRefresh = React.useCallback(() => {
    if (websocketRefreshTimerRef.current !== null) {
      window.clearTimeout(websocketRefreshTimerRef.current);
    }
    websocketRefreshTimerRef.current = window.setTimeout(() => {
      websocketRefreshTimerRef.current = null;
      flushWebSocketRefresh().catch(() => undefined);
    }, WEBSOCKET_REFRESH_DEBOUNCE_MS);
  }, [flushWebSocketRefresh]);

  React.useEffect(() => {
    return () => {
      if (websocketRefreshTimerRef.current !== null) {
        window.clearTimeout(websocketRefreshTimerRef.current);
      }
    };
  }, []);

  React.useEffect(() => {
    loadRuns().catch(() => {
      message.error(t('ai_stock_picker.interactive.messages.load_runs_failed'));
    });
  }, [loadRuns, message, t]);

  React.useEffect(() => {
    if (!selectedRunId) {
      setSelectedRun(null);
      setMessages([]);
      return;
    }
    loadRunDetails(selectedRunId).catch(() => {
      message.error(t('ai_stock_picker.interactive.messages.load_details_failed'));
    });
  }, [loadRunDetails, message, selectedRunId, t]);

  React.useEffect(() => {
    if (!selectedRunId || !selectedRun || !activeStatuses.has(selectedRun.status)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      refreshSelectedRun().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshSelectedRun, selectedRun, selectedRunId]);

  useWebSocketSubscription('interactive_stock_picker_update', (msg: WebSocketMessage) => {
    const update = msg as InteractiveStockPickerUpdateMessage;
    const data = update.data;
    const payload = asRecord(data?.payload);
    if (payload.domain !== 'interactive_research') {
      return;
    }
    if (data?.run_id && data.run_id !== selectedRunIdRef.current) {
      return;
    }
    scheduleWebSocketRefresh();
  });

  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: 'end' });
  }, [sortedMessages.length]);

  const updateDetailsFromResponse = React.useCallback((response: {
    run: InteractiveResearchRunSummary;
    messages: InteractiveResearchMessage[];
  }) => {
    setSelectedRun(response.run);
    setSelectedRunId(response.run.run_id);
    setMessages(response.messages);
  }, []);

  const createRunFromInput = React.useCallback(
    async (content: string, maxIterations: number) => {
      if (activeRun && activeRun.run_id !== selectedRun?.run_id) {
        setSelectedRunId(activeRun.run_id);
        message.warning(t('ai_stock_picker.interactive.messages.active_run_exists', { run_id: activeRun.run_id }));
        return;
      }
      const payload: InteractiveResearchRunCreatePayload = {
        requirement: content,
        max_iterations: maxIterations,
      };
      const response = await interactiveStockPickerApi.createRun(payload);
      updateDetailsFromResponse(response);
      await loadRuns();
      message.success(t('ai_stock_picker.interactive.messages.run_created'));
    },
    [activeRun, loadRuns, message, selectedRun?.run_id, t, updateDetailsFromResponse],
  );

  const appendMessageFromInput = React.useCallback(
    async (content: string) => {
      if (!selectedRun) {
        return;
      }
      const response = await interactiveStockPickerApi.appendMessage(selectedRun.run_id, { content });
      setSelectedRun(response.run);
      setMessages((prev) => [...prev, response.message]);
      await refreshSelectedRun();
      message.success(t('ai_stock_picker.interactive.messages.message_sent'));
    },
    [message, refreshSelectedRun, selectedRun, t],
  );

  const handleSubmitInput = React.useCallback(async () => {
    try {
      const values = await messageForm.validateFields();
      const content = values.content.trim();
      const maxIterations = Math.max(10, Number(values.max_iterations || 60));
      setSubmitting(true);
      if (canSendMessage) {
        await appendMessageFromInput(content);
      } else {
        await createRunFromInput(content, maxIterations);
      }
      messageForm.resetFields();
    } catch (error) {
      if (isRecord(error) && 'errorFields' in error) {
        return;
      }
      const detail = getApiErrorDetail(error);
      message.error(formatErrorMessage(detail) || t('ai_stock_picker.interactive.messages.message_failed'));
    } finally {
      setSubmitting(false);
    }
  }, [appendMessageFromInput, canSendMessage, createRunFromInput, message, messageForm, t]);

  const handleRunAction = React.useCallback(
    async (action: InteractiveResearchAction, content?: string) => {
      if (!selectedRun) {
        return;
      }
      setActionLoading(action);
      try {
        const response = await interactiveStockPickerApi.runAction(selectedRun.run_id, { action, content });
        updateDetailsFromResponse(response);
        await loadRuns();
        message.success(t(`ai_stock_picker.interactive.messages.${action}_success`));
      } catch (error) {
        const detail = getApiErrorDetail(error);
        message.error(formatErrorMessage(detail) || t(`ai_stock_picker.interactive.messages.${action}_failed`));
      } finally {
        setActionLoading(null);
      }
    },
    [loadRuns, message, selectedRun, t, updateDetailsFromResponse],
  );

  const handleDeleteRun = React.useCallback(async () => {
    if (!selectedRun) {
      return;
    }
    setDeletingRun(true);
    try {
      await interactiveStockPickerApi.deleteRun(selectedRun.run_id);
      selectedRunIdRef.current = null;
      setSelectedRunId(null);
      setSelectedRun(null);
      setMessages([]);
      await loadRuns();
      message.success(t('ai_stock_picker.interactive.messages.delete_success'));
    } catch (error) {
      const detail = getApiErrorDetail(error);
      message.error(formatErrorMessage(detail) || t('ai_stock_picker.interactive.messages.delete_failed'));
    } finally {
      setDeletingRun(false);
    }
  }, [loadRuns, message, selectedRun, t]);

  const handleCopyMessage = React.useCallback(async (item: InteractiveResearchMessage) => {
    const { content } = getMessageDisplayContent(item);
    try {
      await navigator.clipboard.writeText(content);
      message.success(t('common.copy_success'));
    } catch (error) {
      message.error(formatErrorMessage(error) || t('common.copy'));
    }
  }, [message, t]);

  const renderMessageItem = React.useCallback(
    (item: InteractiveResearchMessage) => {
      const displayType = item.display_type || item.role;
      const markdown = item.markdown || item.content || '-';
      const executionStatus = item.execution_status || item.status;
      const isToolResult = item.message_type === 'tool_result';
      const isToolStart = item.message_type === 'tool_start';
      const isToolMessage = isToolResult || isToolStart;
      const toolName = isToolMessage ? getToolName(item) : null;
      let toolJsonPreview: string | null = null;
      if (isToolResult) {
        toolJsonPreview = getToolResultPreview(item);
      } else if (isToolStart) {
        toolJsonPreview = getToolStartArguments(item);
      }
      const displayContent = getMessageDisplayContent(item);
      const showFooterExpandAction = shouldShowFooterExpandAction(displayContent.content, isToolMessage);
      const isUser = displayType === 'user';
      return (
        <div
          key={item.message_id}
          style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginBottom: 12 }}
        >
          <div
            style={{
              maxWidth: isUser ? '78%' : '92%',
              minWidth: 220,
              border: `1px solid ${token.colorBorderSecondary}`,
              background: isUser ? token.colorPrimaryBg : token.colorFillAlter,
              borderRadius: token.borderRadius,
              padding: 12,
            }}
          >
            <div className="interactive-research-message-header">
              <Space size={6} wrap>
                <Tag color={getRoleColor(displayType)}>
                  {t(`ai_stock_picker.interactive.roles.${displayType}`, { defaultValue: displayType })}
                </Tag>
                {toolName && <Tag color="blue">{toolName}</Tag>}
                {executionStatus && (isToolMessage || executionStatus !== 'completed') && (
                  <Tag color={getExecutionStatusColor(executionStatus)}>
                    {t(`ai_stock_picker.interactive.execution_statuses.${executionStatus}`, {
                      defaultValue: executionStatus,
                    })}
                  </Tag>
                )}
                <Text type="secondary">{dayjs(item.created_at).format('MM-DD HH:mm:ss')}</Text>
              </Space>
              <Space size={2}>
                <Button
                  type="text"
                  size="small"
                  icon={<CopyOutlined />}
                  title={t('common.copy')}
                  onClick={() => handleCopyMessage(item)}
                />
                <Button
                  type="text"
                  size="small"
                  icon={<ArrowsAltOutlined />}
                  title={t('ai_stock_picker.interactive.actions.fullscreen')}
                  onClick={() => setExpandedMessage(item)}
                />
              </Space>
            </div>
            {isToolMessage ? (
              toolJsonPreview && <pre className="interactive-research-json-result">{toolJsonPreview}</pre>
            ) : (
              <>
                <div className="interactive-research-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
                </div>
              </>
            )}
            {showFooterExpandAction && (
              <div className="interactive-research-message-footer-actions">
                <Button
                  type="text"
                  size="small"
                  icon={<ArrowsAltOutlined />}
                  title={t('ai_stock_picker.interactive.actions.fullscreen')}
                  onClick={() => setExpandedMessage(item)}
                />
              </div>
            )}
          </div>
        </div>
      );
    },
    [
      token.borderRadius,
      token.colorBorderSecondary,
      token.colorFillAlter,
      token.colorPrimaryBg,
      handleCopyMessage,
      t,
    ],
  );

  const expandedMessageContent = React.useMemo(
    () => (expandedMessage ? getMessageDisplayContent(expandedMessage) : null),
    [expandedMessage],
  );

  const renderRunActions = React.useCallback(() => {
    if (!selectedRun) {
      return null;
    }
    return (
      <Space wrap>
        {canApprovePlan && (
          <Button
            type="primary"
            icon={<CheckCircleOutlined />}
            loading={actionLoading === 'approve'}
            onClick={() => handleRunAction('approve')}
          >
            {t('ai_stock_picker.interactive.actions.approve')}
          </Button>
        )}
        {canCancelRun && (
          <Popconfirm
            title={t('ai_stock_picker.interactive.confirmations.cancel_run')}
            okText={t('common.confirm')}
            cancelText={t('common.cancel')}
            onConfirm={() => handleRunAction('cancel', t('ai_stock_picker.interactive.messages.cancel_reason'))}
          >
            <Button danger icon={<CloseCircleOutlined />} loading={actionLoading === 'cancel'}>
              {t('ai_stock_picker.interactive.actions.cancel')}
            </Button>
          </Popconfirm>
        )}
        <Popconfirm
          title={t('ai_stock_picker.interactive.confirmations.delete_run')}
          okText={t('common.confirm')}
          cancelText={t('common.cancel')}
          onConfirm={() => handleDeleteRun()}
        >
          <Button danger icon={<DeleteOutlined />} loading={deletingRun}>
            {t('ai_stock_picker.interactive.actions.delete')}
          </Button>
        </Popconfirm>
      </Space>
    );
  }, [actionLoading, canApprovePlan, canCancelRun, deletingRun, handleDeleteRun, handleRunAction, selectedRun, t]);

  const renderMessagesPanel = React.useCallback(
    (className?: string) => (
      <Spin spinning={loadingDetails}>
        <div
          className={['interactive-research-message-panel', className].filter(Boolean).join(' ')}
          style={{
            border: `1px solid ${token.colorBorderSecondary}`,
            borderRadius: token.borderRadius,
            background: token.colorBgContainer,
          }}
        >
          {sortedMessages.length === 0 ? (
            <Empty description={t('ai_stock_picker.interactive.empty.no_messages')} />
          ) : (
            sortedMessages.map(renderMessageItem)
          )}
          <div ref={messagesEndRef} />
        </div>
      </Spin>
    ),
    [loadingDetails, renderMessageItem, sortedMessages, t, token.borderRadius, token.colorBgContainer, token.colorBorderSecondary],
  );

  const runOptions = React.useMemo(
    () =>
      runs.map((run) => ({
        value: run.run_id,
        label: (
          <Tooltip
            title={(
              <div className="interactive-research-run-tooltip-content">
                {getStatusLabel(run.status)} · {run.raw_requirement || run.title}
              </div>
            )}
            classNames={{ root: 'interactive-research-run-tooltip' }}
          >
            <span className="interactive-research-run-option">
              {getStatusLabel(run.status)} · {run.title || run.raw_requirement}
            </span>
          </Tooltip>
        ),
      })),
    [getStatusLabel, runs],
  );

  return (
    <Card
      className="interactive-research-card"
      title={
        <Space>
          <MessageOutlined />
          {t('ai_stock_picker.interactive.cards.chat')}
        </Space>
      }
      extra={
        <Space wrap>
          <Select
            allowClear
            loading={loadingRuns}
            placeholder={t('ai_stock_picker.interactive.empty.select_run')}
            className="interactive-research-run-select"
            value={selectedRunId ?? undefined}
            options={runOptions}
            onChange={(value) => setSelectedRunId(value ?? null)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => refreshSelectedRun()} loading={loadingRuns}>
            {t('warehouse.refresh')}
          </Button>
          <Button icon={<FullscreenOutlined />} onClick={() => setChatFullscreenOpen(true)}>
            {t('ai_stock_picker.interactive.actions.fullscreen')}
          </Button>
          {renderRunActions()}
        </Space>
      }
    >
      <div className="interactive-research-content">
        {activeRun && activeRun.run_id !== selectedRun?.run_id && (
          <Alert
            type="info"
            showIcon
            message={t('ai_stock_picker.interactive.messages.active_run_exists', { run_id: activeRun.run_id })}
            action={
              <Button size="small" onClick={() => setSelectedRunId(activeRun.run_id)}>
                {t('ai_stock_picker.interactive.actions.open_active_run')}
              </Button>
            }
          />
        )}

        {selectedRun && (
          <Descriptions size="small" column={3}>
            <Descriptions.Item label={t('ai_stock_picker.interactive.fields.status')}>
              <Tag color={getStatusColor(selectedRun.status)}>{getStatusLabel(selectedRun.status)}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.interactive.fields.phase')}>
              {getPhaseLabel(selectedRun.current_phase)}
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.interactive.fields.updated_at')}>
              {dayjs(selectedRun.updated_at).format('YYYY-MM-DD HH:mm:ss')}
            </Descriptions.Item>
          </Descriptions>
        )}
        {selectedRun?.error_message && <Alert type="error" showIcon message={selectedRun.error_message} />}

        <div className="interactive-research-message-panel-shell">{renderMessagesPanel()}</div>

        <Form className="interactive-research-input-form" form={messageForm} layout="vertical" initialValues={{ max_iterations: 60 }}>
          <Form.Item
            name="content"
            rules={[
              { required: true, whitespace: true, message: t('ai_stock_picker.interactive.validations.message_required') },
              {
                max: 20000,
                message: t('ai_stock_picker.interactive.validations.message_max_length'),
              },
            ]}
            style={{ marginBottom: 8 }}
          >
            <Input.TextArea
              rows={2}
              maxLength={20000}
              showCount
              placeholder={t('ai_stock_picker.interactive.placeholders.message')}
              onPressEnter={(event) => {
                if (event.shiftKey) {
                  return;
                }
                event.preventDefault();
                handleSubmitInput().catch(() => undefined);
              }}
            />
          </Form.Item>
          <Space align="center" wrap>
            {!canSendMessage && (
              <Space size={8} align="center">
                <Text type="secondary">{t('ai_stock_picker.interactive.fields.max_iterations')}</Text>
                <Form.Item
                  noStyle
                  name="max_iterations"
                  rules={[
                    { type: 'number', min: 10, message: t('ai_stock_picker.interactive.validations.max_iterations_min') },
                  ]}
                >
                  <InputNumber min={10} precision={0} style={{ width: 104 }} />
                </Form.Item>
              </Space>
            )}
            <Button type="primary" icon={<SendOutlined />} loading={submitting} onClick={handleSubmitInput}>
              {canSendMessage
                ? t('ai_stock_picker.interactive.actions.send_message')
                : t('ai_stock_picker.interactive.actions.create_run')}
            </Button>
          </Space>
        </Form>
      </div>
      <Modal
        className="interactive-research-fullscreen-modal"
        footer={null}
        open={chatFullscreenOpen}
        title={t('ai_stock_picker.interactive.cards.chat')}
        style={{ top: 0, maxWidth: '100vw', paddingBottom: 0 }}
        width="100vw"
        onCancel={() => setChatFullscreenOpen(false)}
      >
        {renderMessagesPanel('interactive-research-message-panel-fullscreen')}
      </Modal>
      <Modal
        className="interactive-research-message-expanded-modal"
        footer={null}
        open={expandedMessage !== null}
        title={t('ai_stock_picker.interactive.actions.fullscreen')}
        width="74vw"
        style={{ top: '6vh', maxWidth: '74vw' }}
        onCancel={() => setExpandedMessage(null)}
      >
        {expandedMessageContent && (
          expandedMessageContent.isToolMessage ? (
            <pre className="interactive-research-expanded-json-result">{expandedMessageContent.content}</pre>
          ) : (
            <div className="interactive-research-markdown interactive-research-expanded-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{expandedMessageContent.content}</ReactMarkdown>
            </div>
          )
        )}
      </Modal>
    </Card>
  );
};
