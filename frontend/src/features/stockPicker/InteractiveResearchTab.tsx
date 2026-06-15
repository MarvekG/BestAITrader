import React from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  theme,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
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
import { StockPickerUpdateMessage, WebSocketMessage } from '../../services/websocket';
import { formatErrorMessage, getApiErrorDetail } from '../../utils/errorUtils';
import './InteractiveResearchTab.css';

const { Text } = Typography;

interface TextFormValues {
  content: string;
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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const asRecord = (value: unknown): Record<string, unknown> => (isRecord(value) ? value : {});

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

export const InteractiveResearchTab: React.FC = () => {
  const { t } = useTranslation();
  const { message } = AntdApp.useApp();
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
  const selectedRunIdRef = React.useRef<string | null>(null);
  const messagesEndRef = React.useRef<HTMLDivElement | null>(null);

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

  useWebSocketSubscription('stock_picker_update', (msg: WebSocketMessage) => {
    const update = msg as StockPickerUpdateMessage;
    const data = update.data;
    const payload = asRecord(data?.payload);
    if (payload.domain !== 'interactive_research') {
      return;
    }
    if (data?.run_id && data.run_id !== selectedRunIdRef.current) {
      return;
    }
    refreshSelectedRun().catch(() => undefined);
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
    async (content: string) => {
      if (activeRun && activeRun.run_id !== selectedRun?.run_id) {
        setSelectedRunId(activeRun.run_id);
        message.warning(t('ai_stock_picker.interactive.messages.active_run_exists', { run_id: activeRun.run_id }));
        return;
      }
      const payload: InteractiveResearchRunCreatePayload = { requirement: content };
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
      setSubmitting(true);
      if (canSendMessage) {
        await appendMessageFromInput(content);
      } else {
        await createRunFromInput(content);
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

  const renderMessageItem = React.useCallback(
    (item: InteractiveResearchMessage) => {
      const displayType = item.display_type || item.role;
      const markdown = item.markdown || item.content || '-';
      const executionStatus = item.execution_status || item.status;
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
            <Space size={6} wrap>
              <Tag color={getRoleColor(displayType)}>
                {t(`ai_stock_picker.interactive.roles.${displayType}`, { defaultValue: displayType })}
              </Tag>
              {executionStatus && executionStatus !== 'completed' && (
                <Tag color="orange">
                  {t(`ai_stock_picker.interactive.execution_statuses.${executionStatus}`, {
                    defaultValue: executionStatus,
                  })}
                </Tag>
              )}
              <Text type="secondary">{dayjs(item.created_at).format('MM-DD HH:mm:ss')}</Text>
            </Space>
            <div className="interactive-research-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
            </div>
          </div>
        </div>
      );
    },
    [
      token.borderRadius,
      token.colorBorderSecondary,
      token.colorFillAlter,
      token.colorPrimaryBg,
      t,
    ],
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
      </Space>
    );
  }, [actionLoading, canApprovePlan, canCancelRun, handleRunAction, selectedRun, t]);

  const runOptions = React.useMemo(
    () =>
      runs.map((run) => ({
        value: run.run_id,
        label: `${getStatusLabel(run.status)} · ${run.title || run.raw_requirement}`,
      })),
    [getStatusLabel, runs],
  );

  return (
    <Card
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
            style={{ minWidth: 280 }}
            value={selectedRunId ?? undefined}
            options={runOptions}
            onChange={(value) => setSelectedRunId(value ?? null)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => refreshSelectedRun()} loading={loadingRuns}>
            {t('warehouse.refresh')}
          </Button>
          {renderRunActions()}
        </Space>
      }
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
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

        <Spin spinning={loadingDetails}>
          <div
            style={{
              minHeight: '40vh',
              maxHeight: '50vh',
              overflow: 'auto',
              padding: 12,
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

        <Form form={messageForm} layout="vertical">
          <Form.Item
            name="content"
            rules={[{ required: true, message: t('ai_stock_picker.interactive.validations.message_required') }]}
            style={{ marginBottom: 8 }}
          >
            <Input.TextArea
              rows={3}
              maxLength={4000}
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
          <Button type="primary" icon={<SendOutlined />} loading={submitting} onClick={handleSubmitInput}>
            {canSendMessage
              ? t('ai_stock_picker.interactive.actions.send_message')
              : t('ai_stock_picker.interactive.actions.create_run')}
          </Button>
        </Form>
      </Space>
    </Card>
  );
};
