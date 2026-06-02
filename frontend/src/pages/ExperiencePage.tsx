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
  InputNumber,
  List,
  Row,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  TimePicker,
  Tooltip,
  Typography,
} from 'antd';
import { DeleteOutlined, ExclamationCircleOutlined, RobotOutlined, SettingOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';

import {
  experienceApi,
  ExperienceAnalyzeResponse,
  ExperienceDebateSession,
  ExperienceLibraryItem,
  ExperienceReviewCandidate,
  ExperienceReviewEvent,
  ExperienceReviewHorizon,
  ExperienceReviewRun,
  ExperienceReviewSchedulerConfig,
  ExperienceToolTraceItem,
} from '../api/experience';
import { ExperienceLibraryPanel } from './experience/ExperienceLibraryPanel';
import { ReviewCandidatePanel } from './experience/ReviewCandidatePanel';
import { ReviewTriadCards } from './experience/ReviewTriadCards';
import { WrittenMemoryCards } from './experience/WrittenMemoryCards';
import { ResourceSubscribedMessage, WebSocketMessage, wsManager } from '../services/websocket';
import { formatErrorMessage, getApiErrorMessage, getApiErrorResponseData } from '../utils/errorUtils';

const { Text, Title, Paragraph } = Typography;

const actionColorMap: Record<string, string> = {
  avoid: 'red',
  watch: 'default',
  buy: 'green',
  add: 'cyan',
  hold: 'blue',
  reduce: 'orange',
  sell: 'volcano',
};

const correctnessColorMap: Record<string, string> = {
  correct: 'green',
  partially_correct: 'blue',
  incorrect: 'red',
  inconclusive: 'default',
};

const longTextStyle: React.CSSProperties = {
  marginBottom: 0,
  maxWidth: '100%',
  overflowWrap: 'anywhere',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

type ExperienceReviewUpdateData = Partial<ExperienceReviewEvent> & {
  debate_session_id?: string;
  timestamp?: string;
};

type ExperienceReviewUpdateMessage = WebSocketMessage & {
  data?: ExperienceReviewUpdateData;
};

const getApiErrorDetail = (error: unknown) => {
  const responseData = getApiErrorResponseData(error) as { detail?: unknown } | null | undefined;
  return responseData?.detail;
};

const getToolName = (payload?: Record<string, unknown> | null) => {
  const toolName = payload?.tool_name;
  return typeof toolName === 'string' ? toolName : undefined;
};

export const ExperiencePage: React.FC = () => {
  const { t } = useTranslation();
  const { message, modal } = AntdApp.useApp();
  const [searchParams, setSearchParams] = useSearchParams();

  const [selectedSessionId, setSelectedSessionId] = React.useState<string | undefined>(
    () => searchParams.get('session_id') || undefined,
  );
  const [sessionsLoading, setSessionsLoading] = React.useState(false);
  const [runsLoading, setRunsLoading] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [debateSessions, setDebateSessions] = React.useState<ExperienceDebateSession[]>([]);
  const [reviewRuns, setReviewRuns] = React.useState<ExperienceReviewRun[]>([]);
  const [reviewCandidates, setReviewCandidates] = React.useState<ExperienceReviewCandidate[]>([]);
  const [analyzeResult, setAnalyzeResult] = React.useState<ExperienceAnalyzeResponse | null>(null);
  const [liveToolTrace, setLiveToolTrace] = React.useState<ExperienceToolTraceItem[]>([]);
  const [liveEvents, setLiveEvents] = React.useState<ExperienceReviewEvent[]>([]);
  const [persistedReviewEvents, setPersistedReviewEvents] = React.useState<ExperienceReviewEvent[]>([]);
  const [viewedReviewRunId, setViewedReviewRunId] = React.useState<string | null>(
    () => searchParams.get('review_run_id'),
  );
  const [liveReviewRunId, setLiveReviewRunId] = React.useState<string | null>(null);
  const [loadedReviewRunId, setLoadedReviewRunId] = React.useState<string | null>(null);
  const [selectedReviewHorizon, setSelectedReviewHorizon] = React.useState<ExperienceReviewHorizon | undefined>(undefined);
  const [runActionLoadingId, setRunActionLoadingId] = React.useState<string | null>(null);
  const [clearingRuns, setClearingRuns] = React.useState(false);
  const [candidatesLoading, setCandidatesLoading] = React.useState(false);
  const [activeTab, setActiveTab] = React.useState(() => searchParams.get('tab') || 'analysis');

  // Scheduler config state
  type SchedulerConfigFormValues = Omit<ExperienceReviewSchedulerConfig, 'schedule_hour' | 'schedule_minute'> & {
    schedule_time?: Dayjs;
  };
  const [schedulerForm] = Form.useForm<SchedulerConfigFormValues>();
  const [schedulerOpen, setSchedulerOpen] = React.useState(false);
  const [schedulerSaving, setSchedulerSaving] = React.useState(false);

  const buildScheduleTime = React.useCallback((hour: number, minute: number) => (
    dayjs().hour(hour).minute(minute).second(0)
  ), []);

  const schedulerLabel = React.useCallback((labelKey: string, tooltipKey: string) => (
    <Space size={4}>
      <span>{t(labelKey)}</span>
      <Tooltip title={t(tooltipKey)}>
        <ExclamationCircleOutlined style={{ color: '#8c8c8c' }} />
      </Tooltip>
    </Space>
  ), [t]);

  const loadSchedulerConfig = React.useCallback(async () => {
    try {
      const config = await experienceApi.getSchedulerConfig();
      schedulerForm.setFieldsValue({
        ...config,
        schedule_time: buildScheduleTime(config.schedule_hour, config.schedule_minute),
      });
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.experience_scheduler_load_failed')));
    }
  }, [buildScheduleTime, message, schedulerForm, t]);

  const handleSaveSchedulerConfig = async (values: SchedulerConfigFormValues) => {
    setSchedulerSaving(true);
    try {
      const scheduleTime = values.schedule_time || buildScheduleTime(18, 30);
      const config = await experienceApi.updateSchedulerConfig({
        enabled: Boolean(values.enabled),
        schedule_hour: scheduleTime.hour(),
        schedule_minute: scheduleTime.minute(),
        candidate_lookback: Number(values.candidate_lookback),
        max_runs_per_tick: Number(values.max_runs_per_tick),
      });
      schedulerForm.setFieldsValue({
        ...config,
        schedule_time: buildScheduleTime(config.schedule_hour, config.schedule_minute),
      });
      message.success(t('settings.experience_scheduler_saved'));
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.experience_scheduler_save_failed')));
    } finally {
      setSchedulerSaving(false);
    }
  };

  const selectedSession = React.useMemo(
    () => debateSessions.find((item) => item.session_id === selectedSessionId) ?? null,
    [debateSessions, selectedSessionId],
  );
  const activeReviewRun = React.useMemo(
    () => reviewRuns.find(
      (item) => item.session_id === selectedSessionId && ['started', 'running'].includes(item.status),
    ) ?? null,
    [reviewRuns, selectedSessionId],
  );
  const persistedToolTrace = React.useMemo(
    () => persistedReviewEvents
      .filter((item) => item.stage === 'tool_call')
      .map((item) => ({
        name: getToolName(item.payload),
        args: item.payload?.args || {},
      })),
    [persistedReviewEvents],
  );
  const hasLiveProgress = liveEvents.length > 0;
  const toolTrace: ExperienceToolTraceItem[] = hasLiveProgress ? liveToolTrace : (analyzeResult?.tool_trace || persistedToolTrace);
  const writeMemoryCount = React.useMemo(
    () => toolTrace.filter((item) => item?.name === 'write_memory').length,
    [toolTrace],
  );
  const recallMemoryCount = React.useMemo(
    () => toolTrace.filter((item) => item?.name === 'recall_memory').length,
    [toolTrace],
  );
  const externalToolCount = React.useMemo(
    () => toolTrace.filter((item) => item?.name && ['search_tavily', 'search_news'].includes(item.name)).length,
    [toolTrace],
  );

  const getStyleLabel = React.useCallback((value: string) => t(`experience.styles.${value}`), [t]);
  const getActionLabel = React.useCallback((value: string) => t(`experience.actions.${value}`), [t]);
  const getCorrectnessLabel = React.useCallback(
    (value: string) => t(`experience.correctness_statuses.${value}`),
    [t],
  );
  const getMemoSessionLabel = React.useCallback((value?: string) => {
    if (value === 'stock') {
      return t('experience.memo_session_stock');
    }
    return t('experience.memo_session_general');
  }, [t]);
  const getMemoryImportanceLabel = React.useCallback((value?: string) => {
    if (value === 'low') {
      return t('experience.memory_importance_low');
    }
    if (value === 'high') {
      return t('experience.memory_importance_high');
    }
    return t('experience.memory_importance_medium');
  }, [t]);
  const getToolColor = React.useCallback((name?: string) => {
    if (name === 'write_memory') {
      return 'green';
    }
    if (name === 'recall_memory') {
      return 'blue';
    }
    if (name === 'search_tavily' || name === 'search_news') {
      return 'cyan';
    }
    return 'default';
  }, []);
  const getAnalyzeErrorMessage = React.useCallback((detail: unknown) => {
    const raw = String(detail || '');
    if (raw.includes('Market outcome summary is unavailable')) {
      return t('experience.market_outcome_unavailable');
    }
    return formatErrorMessage(detail) || t('common.error');
  }, [t]);
  const renderEventMessage = React.useCallback((event: {
    message_key?: string | null;
    message_params?: Record<string, unknown>;
    message?: string | null;
  }) => {
    const key = event.message_key;
    const params = event?.message_params || {};
    if (key) {
      return t(key, params);
    }
    return event.message || '-';
  }, [t]);
  const getRunStatusColor = React.useCallback((value?: string) => {
    if (value === 'completed') {
      return 'green';
    }
    if (value === 'failed') {
      return 'red';
    }
    if (value === 'running') {
      return 'processing';
    }
    return 'default';
  }, []);
  const applyLiveReviewEvents = React.useCallback((events: ExperienceReviewEvent[]) => {
    setLiveReviewRunId(events[0]?.review_run_id || null);
    setLiveEvents(events);
    setLiveToolTrace(
      events
        .filter((item) => item.stage === 'tool_call')
        .map((item) => ({
          name: getToolName(item.payload),
          args: item.payload?.args || {},
        })),
    );
  }, []);

  const loadDebateSessions = React.useCallback(async () => {
    setSessionsLoading(true);
    try {
      const data = await experienceApi.listDebateSessions();
      setDebateSessions(data);
      if (selectedSessionId && data.some((item) => item.session_id === selectedSessionId)) {
        return;
      }
      if (data.length > 0) {
        setSelectedSessionId(data[0].session_id);
      }
    } catch (error) {
      const detail = getApiErrorDetail(error);
      message.error(formatErrorMessage(detail) || t('common.error'));
    } finally {
      setSessionsLoading(false);
    }
  }, [message, selectedSessionId, t]);

  React.useEffect(() => {
    void loadDebateSessions();
  }, [loadDebateSessions]);

  const loadReviewRuns = React.useCallback(async () => {
    setRunsLoading(true);
    try {
      const data = await experienceApi.listReviewRuns();
      setReviewRuns(data);
    } catch {
      setReviewRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void loadReviewRuns();
  }, [loadReviewRuns]);

  const loadReviewCandidates = React.useCallback(async () => {
    setCandidatesLoading(true);
    try {
      const data = await experienceApi.listReviewCandidates();
      setReviewCandidates(data.items || []);
    } catch {
      setReviewCandidates([]);
    } finally {
      setCandidatesLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void loadReviewCandidates();
  }, [loadReviewCandidates]);

  React.useEffect(() => {
    const nextSearchParams = new URLSearchParams(searchParams);
    if (selectedSessionId) {
      nextSearchParams.set('session_id', selectedSessionId);
    } else {
      nextSearchParams.delete('session_id');
    }
    if (viewedReviewRunId) {
      nextSearchParams.set('review_run_id', viewedReviewRunId);
    } else {
      nextSearchParams.delete('review_run_id');
    }
    if (activeTab === 'library') {
      nextSearchParams.set('tab', activeTab);
    } else {
      nextSearchParams.delete('tab');
    }
    if (nextSearchParams.toString() !== searchParams.toString()) {
      setSearchParams(nextSearchParams, { replace: true });
    }
  }, [activeTab, searchParams, selectedSessionId, setSearchParams, viewedReviewRunId]);

  React.useEffect(() => {
    const loadReviewEvents = async () => {
      if (!selectedSessionId || loading || viewedReviewRunId) {
        return;
      }
      try {
        const data = await experienceApi.listReviewEvents(selectedSessionId);
        setPersistedReviewEvents(data);
      } catch {
        setPersistedReviewEvents([]);
      }
    };
    void loadReviewEvents();
  }, [loading, selectedSessionId, viewedReviewRunId]);

  React.useEffect(() => {
    if (!selectedSessionId) {
      return undefined;
    }

    const handleExperienceReviewUpdate = (msg: WebSocketMessage) => {
      const data = (msg as ExperienceReviewUpdateMessage).data;
      if (!data) return;
      const messageSessionId = data.debate_session_id;
      const messageRunId = data.review_run_id || null;

      if (!selectedSessionId || messageSessionId !== selectedSessionId) {
        return;
      }
      if (liveReviewRunId && messageRunId && liveReviewRunId !== messageRunId) {
        return;
      }
      if (!liveReviewRunId && messageRunId) {
        setLiveReviewRunId(messageRunId);
      }
      setLiveEvents((prev) => [...prev, data as ExperienceReviewEvent]);
      void loadReviewRuns();
      if (data.stage === 'tool_call') {
        const payload = data.payload || {};
        const toolName = getToolName(payload);
        if (!toolName) {
          return;
        }
        setLiveToolTrace((prev) => [
          ...prev,
          {
            name: toolName,
            args: payload?.args || {},
          },
        ]);
      }
    };
    const handleSubscribed = (msg: WebSocketMessage) => {
      const subscribedMessage = msg as ResourceSubscribedMessage;
      if (
        subscribedMessage.event_type !== 'experience_review'
        || subscribedMessage.resource_id !== selectedSessionId
      ) {
        return;
      }
      experienceApi.listReviewEvents(selectedSessionId)
        .then((events) => {
          setPersistedReviewEvents(events);
          const fetchedRunId = events[0]?.review_run_id || null;
          if (!fetchedRunId) {
            return;
          }
          if (liveReviewRunId && fetchedRunId !== liveReviewRunId) {
            return;
          }
          if (liveEvents.length > 0 && events.length < liveEvents.length) {
            return;
          }
          applyLiveReviewEvents(events);
          void loadReviewRuns();
        })
        .catch(() => undefined);
    };

    wsManager.subscribeResource('experience_review_update', selectedSessionId, handleExperienceReviewUpdate);
    wsManager.subscribe('subscribed', handleSubscribed);
    return () => {
      wsManager.unsubscribeResource('experience_review_update', selectedSessionId, handleExperienceReviewUpdate);
      wsManager.unsubscribe('subscribed', handleSubscribed);
    };
  }, [applyLiveReviewEvents, liveEvents.length, liveReviewRunId, loadReviewRuns, selectedSessionId]);

  const handleAnalyze = async (override?: { sessionId?: string; reviewHorizon?: ExperienceReviewHorizon }) => {
    const targetSessionId = override?.sessionId || selectedSessionId;
    const targetHorizon = override?.reviewHorizon || selectedReviewHorizon;
    if (!targetSessionId) {
      message.warning(t('experience.pick_session'));
      return;
    }
    const targetActiveReviewRun = reviewRuns.find(
      (item) => item.session_id === targetSessionId && ['started', 'running'].includes(item.status),
    );
    if (targetActiveReviewRun) {
      message.warning(t('experience.active_review_exists', { review_run_id: targetActiveReviewRun.review_run_id }));
      return;
    }

    setViewedReviewRunId(null);
    setLiveReviewRunId(null);
    setLiveToolTrace([]);
    setLiveEvents([]);
    setPersistedReviewEvents([]);
    setAnalyzeResult(null);
    setLoadedReviewRunId(null);
    setLoading(true);
    try {
      const result = await experienceApi.analyze({
        session_id: targetSessionId,
        review_horizon: targetHorizon,
      });
      setAnalyzeResult(result);
      setViewedReviewRunId(result.review_run_id || null);
      setLiveReviewRunId(result.review_run_id || null);
      setLoadedReviewRunId(result.review_run_id || null);
      setLiveToolTrace(result.tool_trace || []);
      void loadReviewRuns();
      void loadDebateSessions();
      void loadReviewCandidates();
      message.success(t('experience.analyze_success'));
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setAnalyzeResult(null);
      message.error(getAnalyzeErrorMessage(detail));
    } finally {
      setLoading(false);
    }
  };

  const handleInspectReviewRun = React.useCallback(async (run: ExperienceReviewRun) => {
    setRunActionLoadingId(run.review_run_id);
    setViewedReviewRunId(run.review_run_id);
    setLiveReviewRunId(null);
    setLoadedReviewRunId(null);
    setSelectedSessionId(run.session_id);
    setAnalyzeResult(null);
    setLiveEvents([]);
    setLiveToolTrace([]);
    try {
      const [events, result] = await Promise.all([
        experienceApi.listReviewRunEvents(run.review_run_id),
        experienceApi.getReviewRunResult(run.review_run_id),
      ]);
      setPersistedReviewEvents(events);
      setAnalyzeResult(result);
      setLoadedReviewRunId(run.review_run_id);
    } catch (error) {
      const detail = getApiErrorDetail(error);
      message.error(formatErrorMessage(detail) || t('common.error'));
    } finally {
      setRunActionLoadingId(null);
    }
  }, [message, t]);

  const handleDeleteReviewRun = React.useCallback((run: ExperienceReviewRun) => {
    modal.confirm({
      title: t('experience.delete_run_confirm_title'),
      content: t('experience.delete_run_confirm_desc', { review_run_id: run.review_run_id }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: async () => {
        setRunActionLoadingId(run.review_run_id);
        try {
          const res = await experienceApi.deleteReviewRun(run.review_run_id);
          message.success(res.message || t('experience.delete_run_success'));
          if (viewedReviewRunId === run.review_run_id) {
            setViewedReviewRunId(null);
            setLiveReviewRunId(null);
            setLoadedReviewRunId(null);
            setAnalyzeResult(null);
            setPersistedReviewEvents([]);
            setLiveEvents([]);
            setLiveToolTrace([]);
          }
          await Promise.all([loadReviewRuns(), loadDebateSessions()]);
        } catch (error) {
          const detail = getApiErrorDetail(error);
          message.error(formatErrorMessage(detail) || t('common.error'));
        } finally {
          setRunActionLoadingId(null);
        }
      },
    });
  }, [loadDebateSessions, loadReviewRuns, message, modal, t, viewedReviewRunId]);

  React.useEffect(() => {
    if (!viewedReviewRunId || !reviewRuns.length || runActionLoadingId === viewedReviewRunId) {
      return;
    }
    if (loadedReviewRunId === viewedReviewRunId) {
      return;
    }
    const targetRun = reviewRuns.find((item) => item.review_run_id === viewedReviewRunId);
    if (!targetRun) {
      setViewedReviewRunId(null);
      setLoadedReviewRunId(null);
      setAnalyzeResult(null);
      setPersistedReviewEvents([]);
      return;
    }
    void handleInspectReviewRun(targetRun);
  }, [handleInspectReviewRun, loadedReviewRunId, reviewRuns, runActionLoadingId, viewedReviewRunId]);

  const handleClearReviewRuns = React.useCallback(() => {
    modal.confirm({
      title: t('experience.clear_runs_confirm_title'),
      content: t('experience.clear_runs_confirm_desc'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: async () => {
        setClearingRuns(true);
        try {
          const res = await experienceApi.clearReviewRuns();
          message.success(res.message || t('experience.clear_runs_success', { count: res.count || 0 }));
          setViewedReviewRunId(null);
          setLiveReviewRunId(null);
          setLoadedReviewRunId(null);
          setAnalyzeResult(null);
          setPersistedReviewEvents([]);
          setLiveEvents([]);
          setLiveToolTrace([]);
          await Promise.all([loadReviewRuns(), loadDebateSessions()]);
        } catch (error) {
          const detail = getApiErrorDetail(error);
          message.error(formatErrorMessage(detail) || t('common.error'));
        } finally {
          setClearingRuns(false);
        }
      },
    });
  }, [loadDebateSessions, loadReviewRuns, message, modal, t]);

  const handleOpenLibraryReview = React.useCallback((item: ExperienceLibraryItem) => {
    setActiveTab('analysis');
    setSelectedSessionId(item.session_id);
    setViewedReviewRunId(item.review_run_id);
    setLiveReviewRunId(null);
    setLoadedReviewRunId(null);
    setAnalyzeResult(null);
    setPersistedReviewEvents([]);
    setLiveEvents([]);
    setLiveToolTrace([]);
  }, []);

  const sessionOptions = debateSessions.map((item) => ({
    value: item.session_id,
    label: `${item.stock_code} - ${item.stock_name || item.stock_code} / ${item.trading_frequency} / ${item.trading_strategy}`,
  }));

  const analysisContent = (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <Title level={3} style={{ marginBottom: 4 }}>{t('experience.title')}</Title>
            <Text type="secondary">{t('experience.subtitle')}</Text>
          </div>

          <Space wrap>
            <Select
              showSearch
              value={selectedSessionId}
              options={sessionOptions}
              style={{ width: 460 }}
              loading={sessionsLoading}
              placeholder={t('experience.session_placeholder')}
              onChange={(value) => {
                setViewedReviewRunId(null);
                setLiveReviewRunId(null);
                setLoadedReviewRunId(null);
                setSelectedSessionId(value);
                setSelectedReviewHorizon(undefined);
                setAnalyzeResult(null);
                setPersistedReviewEvents([]);
                setLiveEvents([]);
                setLiveToolTrace([]);
              }}
              filterOption={(input, option) => (option?.label as string)?.toLowerCase().includes(input.toLowerCase())}
              notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('experience.no_debate_sessions')} />}
            />
            <Button
              type="primary"
              icon={<RobotOutlined />}
              loading={loading}
              disabled={activeReviewRun !== null}
              onClick={() => void handleAnalyze()}
            >
              {t('experience.run_analysis')}
            </Button>
            <Button
              icon={<SettingOutlined />}
              onClick={() => {
                void loadSchedulerConfig();
                setSchedulerOpen(true);
              }}
            >
              {t('settings.experience_scheduler_title')}
            </Button>
            {activeReviewRun ? (
              <Text type="secondary">
                {t('experience.active_review_exists', { review_run_id: activeReviewRun.review_run_id })}
              </Text>
            ) : null}
            {selectedSession ? (
              <Space wrap>
                <Tag>{selectedSession.trading_frequency}</Tag>
                <Tag>{selectedSession.trading_strategy}</Tag>
                {selectedSession.pm_decision ? (
                  <Tag color={actionColorMap[selectedSession.pm_decision] || 'default'}>
                    {t('experience.pm_decision')}: {getActionLabel(selectedSession.pm_decision)}
                  </Tag>
                ) : null}
              </Space>
            ) : null}
          </Space>
        </Space>
      </Card>

      <ReviewCandidatePanel
        candidates={reviewCandidates}
        loading={candidatesLoading}
        selectedSessionId={selectedSessionId}
        selectedHorizon={selectedReviewHorizon}
        running={loading}
        onSelect={(candidate, horizon) => {
          setSelectedSessionId(candidate.session_id);
          setSelectedReviewHorizon(horizon);
          setViewedReviewRunId(null);
          setLiveReviewRunId(null);
          setLoadedReviewRunId(null);
          setAnalyzeResult(null);
          setPersistedReviewEvents([]);
          setLiveEvents([]);
          setLiveToolTrace([]);
        }}
        onRun={(candidate, horizon) => {
          void handleAnalyze({ sessionId: candidate.session_id, reviewHorizon: horizon });
        }}
      />

      <Card
        title={t('experience.review_runs')}
        loading={runsLoading}
        extra={(
          <Button
            danger
            icon={<DeleteOutlined />}
            disabled={!reviewRuns.length}
            loading={clearingRuns}
            onClick={handleClearReviewRuns}
          >
            {t('experience.clear_runs')}
          </Button>
        )}
      >
        {reviewRuns.length ? (
          <List
            size="small"
            dataSource={reviewRuns}
            renderItem={(item) => (
              <List.Item
                actions={[
                  <Button
                    key="inspect"
                    size="small"
                    loading={runActionLoadingId === item.review_run_id}
                    onClick={() => void handleInspectReviewRun(item)}
                  >
                    {t('experience.inspect_run')}
                  </Button>,
                  <Button
                    key="delete"
                    size="small"
                    danger
                    loading={runActionLoadingId === item.review_run_id}
                    onClick={() => handleDeleteReviewRun(item)}
                  >
                    {t('experience.delete_run')}
                  </Button>,
                ]}
              >
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space wrap>
                    <Tag>{item.stock_code}</Tag>
                    {item.stock_name ? <Tag>{item.stock_name}</Tag> : null}
                    {item.recommended_action ? (
                      <Tag color={actionColorMap[item.recommended_action] || 'default'}>
                        {getActionLabel(item.recommended_action)}
                      </Tag>
                    ) : null}
                    {item.debate_correctness ? (
                      <Tag color={correctnessColorMap[item.debate_correctness] || 'default'}>
                        {getCorrectnessLabel(item.debate_correctness)}
                      </Tag>
                    ) : null}
                    <Tag color={getRunStatusColor(item.status)}>{item.status}</Tag>
                  </Space>
                  <Text type="secondary">
                    {renderEventMessage(item)} · {dayjs(item.updated_at).format('YYYY-MM-DD HH:mm')}
                  </Text>
                </Space>
              </List.Item>
            )}
          />
        ) : (
          <Empty description={t('experience.no_review_runs')} />
        )}
      </Card>

      {analyzeResult ? (
        <Card title={t('experience.analysis_result')} loading={loading}>
          <div className="experience-scroll-panel experience-analysis-result-scroll">
            <Space direction="vertical" size={16} style={{ width: '100%', marginBottom: 16 }}>
              <ReviewTriadCards triads={analyzeResult.analysis_payload?.review_triads} />
              <WrittenMemoryCards
                memories={analyzeResult.analysis_payload?.written_memories || []}
                getMemoSessionLabel={getMemoSessionLabel}
                getMemoryImportanceLabel={getMemoryImportanceLabel}
              />
            </Space>
            <Descriptions
              className="experience-analysis-descriptions"
              bordered
              size="small"
              column={1}
              items={[
              {
                key: 'session',
                label: t('experience.session'),
                children: (
                  <Space wrap>
                    <Tag>{analyzeResult.stock_code}</Tag>
                    {analyzeResult.stock_name ? <Tag>{analyzeResult.stock_name}</Tag> : null}
                    <Tag>{getStyleLabel(analyzeResult.style_bucket)}</Tag>
                    {analyzeResult.trading_frequency ? <Tag>{analyzeResult.trading_frequency}</Tag> : null}
                    {analyzeResult.trading_strategy ? <Tag>{analyzeResult.trading_strategy}</Tag> : null}
                  </Space>
                ),
              },
              {
                key: 'times',
                label: t('experience.snapshot_time'),
                children: (
                  <Space direction="vertical" size={0}>
                    <Text>{`${t('experience.analysis_time')}: ${dayjs(analyzeResult.analysis_date).format('YYYY-MM-DD HH:mm')}`}</Text>
                    <Text type="secondary">{`${t('experience.review_time')}: ${dayjs(analyzeResult.reviewed_at).format('YYYY-MM-DD HH:mm')}`}</Text>
                  </Space>
                ),
              },
              {
                key: 'action',
                label: t('experience.current_action'),
                children: (
                  <Space wrap>
                    <Tag color={actionColorMap[analyzeResult.analysis_payload?.recommended_action || ''] || 'default'}>
                      {getActionLabel(analyzeResult.analysis_payload?.recommended_action || 'watch')}
                    </Tag>
                    <Text>{`${Number(analyzeResult.analysis_payload?.confidence_score || 0).toFixed(1)}%`}</Text>
                  </Space>
                ),
              },
              {
                key: 'correctness',
                label: t('experience.debate_correctness'),
                children: (
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Tag color={correctnessColorMap[analyzeResult.analysis_payload?.debate_correctness || ''] || 'default'}>
                      {getCorrectnessLabel(analyzeResult.analysis_payload?.debate_correctness || 'inconclusive')}
                    </Tag>
                    <Paragraph style={longTextStyle}>{analyzeResult.analysis_payload?.correctness_reasoning || '-'}</Paragraph>
                  </Space>
                ),
              },
              {
                key: 'written-memories',
                label: t('experience.written_memories'),
                children: (
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {(analyzeResult.analysis_payload?.written_memories || []).length > 0
                      ? (analyzeResult.analysis_payload?.written_memories || []).map((item, index) => (
                        <Space key={`${item.content || 'memory'}-${index}`} direction="vertical" size={4} style={{ width: '100%' }}>
                          <Space wrap>
                            <Text strong>{`${index + 1}.`}</Text>
                            <Tag color={item.memo_session === 'stock' ? 'blue' : 'default'}>
                              {getMemoSessionLabel(item.memo_session)}
                            </Tag>
                            <Tag>{getMemoryImportanceLabel(item.importance)}</Tag>
                            {item.stock_code ? <Tag>{item.stock_code}</Tag> : null}
                            {item.stock_name ? <Tag>{item.stock_name}</Tag> : null}
                          </Space>
                          <Paragraph style={longTextStyle}>
                            {item.content}
                          </Paragraph>
                        </Space>
                      ))
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'thesis',
                label: t('experience.thesis_summary'),
                children: <Paragraph style={longTextStyle}>{analyzeResult.analysis_payload?.thesis_summary || '-'}</Paragraph>,
              },
              {
                key: 'market-experience-summary',
                label: t('experience.market_experience_summary'),
                children: <Paragraph style={longTextStyle}>{analyzeResult.analysis_payload?.market_experience_summary || '-'}</Paragraph>,
              },
              {
                key: 'dominant-drivers',
                label: t('experience.dominant_drivers'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.dominant_drivers || []).length > 0
                      ? (analyzeResult.analysis_payload?.dominant_drivers || []).map((item: string) => <Tag key={item} color="red">{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'rejected-drivers',
                label: t('experience.rejected_drivers'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.rejected_drivers || []).length > 0
                      ? (analyzeResult.analysis_payload?.rejected_drivers || []).map((item: string) => <Tag key={item} color="default">{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'driver-dimension-review',
                label: t('experience.driver_dimension_review'),
                children: (
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    {(analyzeResult.analysis_payload?.driver_dimension_review || []).length > 0
                      ? (analyzeResult.analysis_payload?.driver_dimension_review || []).map((item: string) => (
                        <Paragraph key={item} style={longTextStyle}>
                          {item}
                        </Paragraph>
                      ))
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'buy-sell-rules',
                label: t('experience.buy_sell_rules'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.buy_sell_rules || []).length > 0
                      ? (analyzeResult.analysis_payload?.buy_sell_rules || []).map((item: string) => <Tag key={item}>{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'process-issues',
                label: t('experience.debate_process_issues'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.debate_process_issues || []).length > 0
                      ? (analyzeResult.analysis_payload?.debate_process_issues || []).map((item: string) => <Tag key={item} color="orange">{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'optimization',
                label: t('experience.optimization_directions'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.optimization_directions || []).length > 0
                      ? (analyzeResult.analysis_payload?.optimization_directions || []).map((item: string) => <Tag key={item} color="blue">{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'rules',
                label: t('experience.improved_debate_rules'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.improved_debate_rules || []).length > 0
                      ? (analyzeResult.analysis_payload?.improved_debate_rules || []).map((item: string) => <Tag key={item}>{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'memory-used',
                label: t('experience.memory_used'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.memory_evidence_used || []).length > 0
                      ? (analyzeResult.analysis_payload?.memory_evidence_used || []).map((item: string) => <Tag key={item}>{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              {
                key: 'internet-evidence',
                label: t('experience.internet_evidence'),
                children: (
                  <Space wrap>
                    {(analyzeResult.analysis_payload?.internet_evidence_used || []).length > 0
                      ? (analyzeResult.analysis_payload?.internet_evidence_used || []).map((item: string) => <Tag key={item}>{item}</Tag>)
                      : <Text type="secondary">-</Text>}
                  </Space>
                ),
              },
              ]}
            />
          </div>
        </Card>
      ) : (
        <Card title={t('experience.analysis_result')}>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t('experience.empty_analysis')}
          />
        </Card>
      )}

      <Card title={t('experience.tool_trace')}>
        {hasLiveProgress ? (
          <Space direction="vertical" size={8} style={{ width: '100%', marginBottom: 16 }}>
            {liveEvents.map((event, index) => (
              <Tag key={`${event.stage}-${event.status}-${index}`} color={event.stage === 'tool_call' ? getToolColor(getToolName(event.payload)) : 'processing'}>
                {renderEventMessage(event)}
              </Tag>
            ))}
          </Space>
        ) : null}
        {toolTrace.length ? (
          <List
            size="small"
            header={(
              <Space wrap>
                <Tag>{t('experience.tool_call_count', { count: toolTrace.length })}</Tag>
                <Tag color={writeMemoryCount > 0 ? 'green' : 'default'}>
                  {t('experience.tool_write_memory_count', { count: writeMemoryCount })}
                </Tag>
                <Tag color={recallMemoryCount > 0 ? 'blue' : 'default'}>
                  {t('experience.tool_recall_memory_count', { count: recallMemoryCount })}
                </Tag>
                <Tag color={externalToolCount > 0 ? 'cyan' : 'default'}>
                  {t('experience.tool_external_search_count', { count: externalToolCount })}
                </Tag>
              </Space>
            )}
            dataSource={toolTrace}
            renderItem={(item, index) => (
              <List.Item>
                <Space direction="vertical" size={2} style={{ width: '100%' }}>
                  <Space wrap>
                    <Text strong>{`${index + 1}.`}</Text>
                    <Tag color={getToolColor(item.name)}>{item.name || '-'}</Tag>
                    {item.name === 'write_memory' ? (
                      <Tag color="gold">{t('experience.tool_key_step')}</Tag>
                    ) : null}
                  </Space>
                  <Paragraph code style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(item.args || {}, null, 2)}
                  </Paragraph>
                </Space>
              </List.Item>
            )}
          />
        ) : (
          <Empty description={t('experience.no_tool_trace')} />
        )}
      </Card>

      <Drawer
        title={t('settings.experience_scheduler_title')}
        width={520}
        open={schedulerOpen}
        onClose={() => setSchedulerOpen(false)}
        extra={
          <Button type="primary" loading={schedulerSaving} onClick={() => schedulerForm.submit()}>
            {t('settings.save_config')}
          </Button>
        }
      >
        <Form
          form={schedulerForm}
          layout="vertical"
          initialValues={{
            enabled: false,
            schedule_time: buildScheduleTime(18, 30),
            candidate_lookback: 200,
            max_runs_per_tick: 2,
          }}
          onFinish={handleSaveSchedulerConfig}
        >
          <Form.Item
            name="enabled"
            label={schedulerLabel(
              'settings.experience_scheduler_enabled',
              'settings.experience_scheduler_enabled_tip',
            )}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Row gutter={16}>
            <Col span={24}>
              <Form.Item
                name="schedule_time"
                label={schedulerLabel(
                  'settings.experience_scheduler_time',
                  'settings.experience_scheduler_time_tip',
                )}
                rules={[{ required: true }]}
              >
                <TimePicker format="HH:mm" style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                name="max_runs_per_tick"
                label={schedulerLabel(
                  'settings.experience_scheduler_max_runs',
                  'settings.experience_scheduler_max_runs_tip',
                )}
                rules={[{ required: true }]}
              >
                <InputNumber min={1} max={20} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="candidate_lookback"
                label={schedulerLabel(
                  'settings.experience_scheduler_lookback',
                  'settings.experience_scheduler_lookback_tip',
                )}
                rules={[{ required: true }]}
              >
                <InputNumber min={1} max={5000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <div style={{ color: '#8c8c8c', marginBottom: 16 }}>
            {t('settings.experience_scheduler_desc')}
          </div>
        </Form>
      </Drawer>
    </Space>
  );

  return (
    <Tabs
      activeKey={activeTab}
      onChange={setActiveTab}
      items={[
        {
          key: 'analysis',
          label: t('experience.analysis_tab'),
          children: analysisContent,
        },
        {
          key: 'library',
          label: t('experience.library_tab'),
          children: <ExperienceLibraryPanel onOpenReview={handleOpenLibraryReview} />,
        },
      ]}
    />
  );
};
