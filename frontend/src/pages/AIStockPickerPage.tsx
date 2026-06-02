import React from 'react';
import {
  App as AntdApp,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Empty,
  Form,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  DeleteOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useTranslation } from 'react-i18next';

import { marketApi } from '../api/market';
import {
  stockPickerApi,
  StockPickerCandidate,
  StockPickerEvent,
  StockPickerQuantSupport,
  StockPickerRecommendationItem,
  StockPickerResult,
  StockPickerRun,
} from '../api/stockPicker';
import { warehouseApi } from '../api/warehouse';
import { StockPickerUpdateMessage, TaskCompletedMessage, WebSocketMessage } from '../services/websocket';
import { useWebSocketSubscription } from '../hooks/useWebSocketSubscription';
import { formatErrorMessage, getApiErrorDetail } from '../utils/errorUtils';

const { Title, Paragraph, Text } = Typography;

const statusColor = (status: string) => {
  if (status === 'completed') return 'green';
  if (status.startsWith('failed')) return 'red';
  if (status === 'running') return 'blue';
  return 'default';
};

const statusKeyMap: Record<string, string> = {
  completed: 'completed',
  running: 'running',
  created: 'created',
  failed_ai_research: 'failed_ai_research',
  failed_recommendation: 'failed_recommendation',
  failed_factor: 'failed_factor',
  failed_universe: 'failed_universe',
};

const stageKeyMap: Record<string, string> = {
  created: 'created',
  universe_built: 'universe_built',
  factor_ranked: 'factor_ranked',
  ai_researched: 'ai_researched',
  recommendations_built: 'recommendations_built',
  completed: 'completed',
  failed_ai_research: 'failed_ai_research',
  failed_recommendation: 'failed_recommendation',
  failed_factor: 'failed_factor',
  failed_universe: 'failed_universe',
};

const formatEventPayloadValue = (value: unknown) => {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : '-';
  }
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  return String(value);
};

const factorLimitCaps: Record<'warehouse' | 'core' | 'all', number> = {
  warehouse: 20,
  core: 30,
  all: 40,
};

const researchLimitCaps: Record<'warehouse' | 'core' | 'all', number> = {
  warehouse: 12,
  core: 15,
  all: 18,
};

const isFormValidationError = (error: unknown) =>
  Boolean(error && typeof error === 'object' && 'errorFields' in error);

const getDefaultCandidateLimits = (
  scope: 'warehouse' | 'core' | 'all',
  style: 'balanced' | 'momentum' | 'value' | 'growth' | 'defensive',
  recommendationCount: number,
) => {
  const isHighTurnoverStyle = style === 'momentum' || style === 'growth';
  const factorDefaults = {
    warehouse: isHighTurnoverStyle ? 12 : 10,
    core: isHighTurnoverStyle ? 20 : 16,
    all: isHighTurnoverStyle ? 24 : 20,
  };
  const researchDefaults = {
    warehouse: isHighTurnoverStyle ? 8 : 6,
    core: isHighTurnoverStyle ? 10 : 8,
    all: isHighTurnoverStyle ? 12 : 10,
  };
  return {
    factor_candidate_limit: Math.max(recommendationCount, factorDefaults[scope]),
    research_candidate_limit: Math.max(recommendationCount, researchDefaults[scope]),
    same_industry_limit: Math.min(3, recommendationCount),
  };
};

export const AIStockPickerPage: React.FC = () => {
  const { t } = useTranslation();
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = React.useState(false);
  const [runs, setRuns] = React.useState<StockPickerRun[]>([]);
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null);
  const [events, setEvents] = React.useState<StockPickerEvent[]>([]);
  const [candidates, setCandidates] = React.useState<StockPickerCandidate[]>([]);
  const [result, setResult] = React.useState<StockPickerResult | null>(null);
  const [industryOptions, setIndustryOptions] = React.useState<Array<{ label: string; value: string }>>([]);
  const [warehouseStockCodes, setWarehouseStockCodes] = React.useState<Set<string>>(new Set());
  const [addingWarehouseCodes, setAddingWarehouseCodes] = React.useState<Record<string, boolean>>({});
  const [baseInfoSyncing, setBaseInfoSyncing] = React.useState(false);
  const [resumeSync, setResumeSync] = React.useState(false);
  const [isDataSyncModalOpen, setIsDataSyncModalOpen] = React.useState(false);
  const [dataSyncScope, setDataSyncScope] = React.useState<'all' | 'warehouse' | 'core'>('core');
  const baseInfoSyncTaskIdRef = React.useRef<string | null>(null);
  const [limitOverrides, setLimitOverrides] = React.useState({
    factor_candidate_limit: false,
    research_candidate_limit: false,
    same_industry_limit: false,
  });
  const watchedScope = Form.useWatch('scope', form) as 'warehouse' | 'core' | 'all' | undefined;
  const watchedStyle = Form.useWatch('style', form) as
    | 'balanced'
    | 'momentum'
    | 'value'
    | 'growth'
    | 'defensive'
    | undefined;
  const watchedRecommendationCount = Form.useWatch('recommendation_count', form) as number | undefined;
  const watchedFactorCandidateLimit = Form.useWatch('factor_candidate_limit', form) as number | undefined;

  const scopeOptions = React.useMemo(
    () => [
      { label: t('ai_stock_picker.scope_options.warehouse'), value: 'warehouse' },
      { label: t('ai_stock_picker.scope_options.core'), value: 'core' },
      { label: t('ai_stock_picker.scope_options.all'), value: 'all' },
    ],
    [t],
  );
  const styleOptions = React.useMemo(
    () => [
      { label: t('ai_stock_picker.style_options.balanced'), value: 'balanced' },
      { label: t('ai_stock_picker.style_options.momentum'), value: 'momentum' },
      { label: t('ai_stock_picker.style_options.value'), value: 'value' },
      { label: t('ai_stock_picker.style_options.growth'), value: 'growth' },
      { label: t('ai_stock_picker.style_options.defensive'), value: 'defensive' },
    ],
    [t],
  );
  const riskOptions = React.useMemo(
    () => [
      { label: t('ai_stock_picker.risk_options.low'), value: 'low' },
      { label: t('ai_stock_picker.risk_options.medium'), value: 'medium' },
      { label: t('ai_stock_picker.risk_options.high'), value: 'high' },
    ],
    [t],
  );

  const scopeLabelMap = React.useMemo(
    () => Object.fromEntries(scopeOptions.map((item) => [item.value, item.label])),
    [scopeOptions],
  );
  const styleLabelMap = React.useMemo(
    () => Object.fromEntries(styleOptions.map((item) => [item.value, item.label])),
    [styleOptions],
  );
  const riskLabelMap = React.useMemo(
    () => Object.fromEntries(riskOptions.map((item) => [item.value, item.label])),
    [riskOptions],
  );

  const getStatusLabel = React.useCallback(
    (status: string) => {
      const key = statusKeyMap[status];
      return key ? t(`ai_stock_picker.statuses.${key}`) : status;
    },
    [t],
  );

  const getStageLabel = React.useCallback(
    (stage: string) => {
      const key = stageKeyMap[stage];
      return key ? t(`ai_stock_picker.stages.${key}`) : stage;
    },
    [t],
  );

  const getFailureHint = React.useCallback(
    (status?: string | null) => {
      if (status === 'failed_ai_research') return t('ai_stock_picker.failure_hints.failed_ai_research');
      if (status === 'failed_recommendation') {
        return t('ai_stock_picker.failure_hints.failed_recommendation');
      }
      if (status === 'failed_factor') return t('ai_stock_picker.failure_hints.failed_factor');
      if (status === 'failed_universe') return t('ai_stock_picker.failure_hints.failed_universe');
      return null;
    },
    [t],
  );

  const formatQuantSupport = React.useCallback(
    (quantSupport?: StockPickerQuantSupport | null) => {
      if (!quantSupport) return '-';
      return [
        `${t('ai_stock_picker.quant_support.style_fit')} ${quantSupport.style_fit_score}`,
        `${t('ai_stock_picker.quant_support.liquidity')} ${quantSupport.liquidity_score}`,
        `${t('ai_stock_picker.quant_support.risk_penalty')} ${quantSupport.risk_penalty}`,
      ].join(' / ');
    },
    [t],
  );

  const getEventPayloadItems = React.useCallback(
    (event: StockPickerEvent) => {
      const payload = event.payload;
      if (!payload || typeof payload !== 'object') return [];

      const items: Array<{ key: string; label: string; value: unknown }> = [];
      if ('count' in payload) {
        items.push({
          key: 'count',
          label: t('ai_stock_picker.timeline.count'),
          value: payload.count,
        });
      }
      if ('source' in payload) {
        items.push({
          key: 'source',
          label: t('ai_stock_picker.timeline.source'),
          value: scopeLabelMap[String(payload.source)] || payload.source,
        });
      }
      if ('style' in payload) {
        items.push({
          key: 'style',
          label: t('ai_stock_picker.timeline.style'),
          value: styleLabelMap[String(payload.style)] || payload.style,
        });
      }
      if ('mode' in payload) {
        items.push({
          key: 'mode',
          label: t('ai_stock_picker.timeline.mode'),
          value: payload.mode,
        });
      }
      if ('recommended_stock_codes' in payload) {
        items.push({
          key: 'recommended_stock_codes',
          label: t('ai_stock_picker.timeline.recommended_stock_codes'),
          value: payload.recommended_stock_codes,
        });
      }
      if ('factor_candidate_count' in payload) {
        items.push({
          key: 'factor_candidate_count',
          label: t('ai_stock_picker.timeline.factor_candidate_count'),
          value: payload.factor_candidate_count,
        });
      }
      if ('factor_candidate_limit' in payload) {
        items.push({
          key: 'factor_candidate_limit',
          label: t('ai_stock_picker.timeline.factor_candidate_limit'),
          value: payload.factor_candidate_limit,
        });
      }
      if ('research_candidate_count' in payload) {
        items.push({
          key: 'research_candidate_count',
          label: t('ai_stock_picker.timeline.research_candidate_count'),
          value: payload.research_candidate_count,
        });
      }
      if ('same_industry_limit' in payload) {
        items.push({
          key: 'same_industry_limit',
          label: t('ai_stock_picker.timeline.same_industry_limit'),
          value: payload.same_industry_limit,
        });
      }
      if ('industry_filter_count' in payload) {
        items.push({
          key: 'industry_filter_count',
          label: t('ai_stock_picker.timeline.industry_filter_count'),
          value: payload.industry_filter_count,
        });
      }
      if ('allowed_industries' in payload) {
        items.push({
          key: 'allowed_industries',
          label: t('ai_stock_picker.timeline.allowed_industries'),
          value: payload.allowed_industries,
        });
      }
      return items;
    },
    [scopeLabelMap, styleLabelMap, t],
  );

  const loadRuns = React.useCallback(async () => {
    const data = await stockPickerApi.listRuns();
    setRuns(data);
    if (!selectedRunId && data.length > 0) {
      setSelectedRunId(data[0].run_id);
    }
  }, [selectedRunId]);

  const loadRunDetails = React.useCallback(async (runId: string) => {
    const [eventData, candidateData, resultData] = await Promise.all([
      stockPickerApi.getEvents(runId),
      stockPickerApi.getCandidates(runId),
      stockPickerApi.getResult(runId),
    ]);
    setEvents(eventData);
    setCandidates(candidateData);
    setResult(resultData);
  }, []);

  const loadIndustries = React.useCallback(async () => {
    const data = await stockPickerApi.listIndustries();
    setIndustryOptions(data.map((item) => ({ label: item, value: item })));
  }, []);

  const loadWarehouseStocks = React.useCallback(async () => {
    const data = await warehouseApi.list();
    setWarehouseStockCodes(new Set(data.map((item) => item.stock_code)));
  }, []);

  const validateFactorCandidateLimit = React.useCallback(
    async (_: unknown, value: number | undefined) => {
      if (value == null || !watchedScope || !watchedRecommendationCount) return;
      if (value < watchedRecommendationCount) {
        throw new Error(
          t('ai_stock_picker.validations.factor_candidate_limit_min', {
            recommendation_count: watchedRecommendationCount,
          }),
        );
      }
      const maxLimit = factorLimitCaps[watchedScope];
      if (value > maxLimit) {
        throw new Error(
          t('ai_stock_picker.validations.factor_candidate_limit_max', {
            max_limit: maxLimit,
          }),
        );
      }
    },
    [t, watchedRecommendationCount, watchedScope],
  );

  const validateResearchCandidateLimit = React.useCallback(
    async (_: unknown, value: number | undefined) => {
      if (value == null || !watchedScope || !watchedRecommendationCount) return;
      if (value < watchedRecommendationCount) {
        throw new Error(
          t('ai_stock_picker.validations.research_candidate_limit_min', {
            recommendation_count: watchedRecommendationCount,
          }),
        );
      }
      const maxLimit = researchLimitCaps[watchedScope];
      if (value > maxLimit) {
        throw new Error(
          t('ai_stock_picker.validations.research_candidate_limit_max', {
            max_limit: maxLimit,
          }),
        );
      }
      if (watchedFactorCandidateLimit && value > watchedFactorCandidateLimit) {
        throw new Error(
          t('ai_stock_picker.validations.research_candidate_limit_factor', {
            factor_limit: watchedFactorCandidateLimit,
          }),
        );
      }
    },
    [t, watchedFactorCandidateLimit, watchedRecommendationCount, watchedScope],
  );

  const validateSameIndustryLimit = React.useCallback(
    async (_: unknown, value: number | undefined) => {
      if (value == null || !watchedRecommendationCount) return;
      if (value > watchedRecommendationCount) {
        throw new Error(
          t('ai_stock_picker.validations.same_industry_limit_max', {
            recommendation_count: watchedRecommendationCount,
          }),
        );
      }
    },
    [t, watchedRecommendationCount],
  );

  React.useEffect(() => {
    const initialRecommendationCount = 5;
    const defaults = getDefaultCandidateLimits('core', 'balanced', initialRecommendationCount);
    form.setFieldsValue({
      scope: 'core',
      style: 'balanced',
      recommendation_count: initialRecommendationCount,
      risk_level: 'medium',
      factor_candidate_limit: defaults.factor_candidate_limit,
      research_candidate_limit: defaults.research_candidate_limit,
      same_industry_limit: defaults.same_industry_limit,
      allowed_industries: [],
    });
    loadRuns().catch(() => {
      message.error(t('ai_stock_picker.messages.load_runs_failed'));
    });
    loadIndustries().catch(() => {
      message.error(t('ai_stock_picker.messages.load_industries_failed'));
    });
    loadWarehouseStocks().catch(() => {
      message.error(t('common.error'));
    });
  }, [form, loadIndustries, loadRuns, loadWarehouseStocks, message, t]);

  React.useEffect(() => {
    if (!watchedScope || !watchedStyle || !watchedRecommendationCount) return;
    const defaults = getDefaultCandidateLimits(watchedScope, watchedStyle, watchedRecommendationCount);
    const currentValues = form.getFieldsValue([
      'factor_candidate_limit',
      'research_candidate_limit',
      'same_industry_limit',
    ]);
    const nextFactorLimit = Math.max(
      watchedRecommendationCount,
      Math.min(
        limitOverrides.factor_candidate_limit
          ? Number(currentValues.factor_candidate_limit || defaults.factor_candidate_limit)
          : defaults.factor_candidate_limit,
        factorLimitCaps[watchedScope],
      ),
    );
    const nextResearchLimit = Math.max(
      watchedRecommendationCount,
      Math.min(
        limitOverrides.research_candidate_limit
          ? Number(currentValues.research_candidate_limit || defaults.research_candidate_limit)
          : defaults.research_candidate_limit,
        researchLimitCaps[watchedScope],
        nextFactorLimit,
      ),
    );
    const nextSameIndustryLimit = Math.max(
      1,
      Math.min(
        limitOverrides.same_industry_limit
          ? Number(currentValues.same_industry_limit || defaults.same_industry_limit)
          : defaults.same_industry_limit,
        watchedRecommendationCount,
      ),
    );

    const updates: Record<string, number> = {};
    if (Number(currentValues.factor_candidate_limit) !== nextFactorLimit) {
      updates.factor_candidate_limit = nextFactorLimit;
    }
    if (Number(currentValues.research_candidate_limit) !== nextResearchLimit) {
      updates.research_candidate_limit = nextResearchLimit;
    }
    if (Number(currentValues.same_industry_limit) !== nextSameIndustryLimit) {
      updates.same_industry_limit = nextSameIndustryLimit;
    }
    if (Object.keys(updates).length > 0) {
      form.setFieldsValue(updates);
    }
  }, [form, limitOverrides, watchedRecommendationCount, watchedScope, watchedStyle]);

  React.useEffect(() => {
    if (!selectedRunId) return;
    loadRunDetails(selectedRunId).catch(() => {
      message.error(t('ai_stock_picker.messages.load_result_failed'));
    });
  }, [selectedRunId, loadRunDetails, message, t]);

  useWebSocketSubscription('stock_picker_update', (msg: WebSocketMessage) => {
      const data = (msg as StockPickerUpdateMessage).data;
      const runId = data?.run_id;
      if (!runId) return;
      loadRuns().catch(() => undefined);
      if (runId === selectedRunId) {
        loadRunDetails(runId).catch(() => undefined);
      }
  });

  useWebSocketSubscription('task_completed', (msg: WebSocketMessage) => {
      const data = (msg as TaskCompletedMessage).data;
      if (!data?.task_id || baseInfoSyncTaskIdRef.current !== data.task_id) return;

      if (data.status === 'completed' || data.status === 'success' || data.status === 'failed' || data.status === 'error') {
        setBaseInfoSyncing(false);
        baseInfoSyncTaskIdRef.current = null;
      }
  });

  const handleBaseInfoSync = React.useCallback(
    async (scope: 'all' | 'warehouse' | 'core') => {
      let successKey: 'common.sync_base_info' | 'common.sync_warehouse_base_info' | 'common.sync_core_base_info' =
        'common.sync_base_info';

      if (scope === 'warehouse') {
        successKey = 'common.sync_warehouse_base_info';
      } else if (scope === 'core') {
        successKey = 'common.sync_core_base_info';
      }

      setBaseInfoSyncing(true);
      try {
        const res = await marketApi.syncBaseInfo(undefined, resumeSync, scope);
        setIsDataSyncModalOpen(false);
        if (res.task_id) {
          baseInfoSyncTaskIdRef.current = res.task_id;
          message.success(`${t(successKey)} ${t('common.task_submitted')}: ${res.task_id}`);
          return;
        }
        setBaseInfoSyncing(false);
        message.success(res.message);
      } catch (error) {
        const detail = getApiErrorDetail(error);
        message.error(formatErrorMessage(detail) || t('common.sync_failed'));
        setBaseInfoSyncing(false);
        baseInfoSyncTaskIdRef.current = null;
      }
    },
    [message, resumeSync, t],
  );

  const handleStart = React.useCallback(async () => {
    const activeRun = runs.find((item) => item.status === 'created' || item.status === 'running');
    if (activeRun) {
      message.warning(t('ai_stock_picker.messages.active_run_exists', { run_id: activeRun.run_id }));
      return;
    }
    try {
      const values = await form.validateFields();
      setLoading(true);
      const res = await stockPickerApi.createRun(values);
      setSelectedRunId(res.run_id);
      await loadRuns();
      message.success(t('common.task_submitted'));
    } catch (error) {
      if (!isFormValidationError(error)) {
        const detail = getApiErrorDetail(error);
        message.error(formatErrorMessage(detail) || t('ai_stock_picker.messages.start_failed'));
      }
    } finally {
      setLoading(false);
    }
  }, [form, loadRuns, message, runs, t]);

  const handleDeleteRun = React.useCallback(async (runId: string) => {
    await stockPickerApi.deleteRun(runId);
    if (selectedRunId === runId) {
      setSelectedRunId(null);
      setEvents([]);
      setCandidates([]);
      setResult(null);
    }
    await loadRuns();
    message.success(t('ai_stock_picker.messages.run_deleted'));
  }, [loadRuns, message, selectedRunId, t]);

  const handleClearRuns = React.useCallback(async () => {
    await stockPickerApi.clearRuns();
    setSelectedRunId(null);
    setEvents([]);
    setCandidates([]);
    setResult(null);
    await loadRuns();
    message.success(t('ai_stock_picker.messages.runs_cleared'));
  }, [loadRuns, message, t]);

  const handleAddToWarehouse = React.useCallback(async (record: StockPickerRecommendationItem) => {
    const stockCode = record.stock_code;
    setAddingWarehouseCodes((prev) => ({ ...prev, [stockCode]: true }));
    try {
      await warehouseApi.add({
        stock_code: stockCode,
        stock_name: record.stock_name || undefined,
      });
      setWarehouseStockCodes((prev) => new Set([...prev, stockCode]));
      message.success(t('ai_stock_picker.messages.added_to_warehouse', { stock_code: stockCode }));
    } catch (error) {
      const detail = getApiErrorDetail(error);
      const errorMessage = formatErrorMessage(detail) || t('common.error');
      message.error(errorMessage);
    } finally {
      setAddingWarehouseCodes((prev) => {
        const next = { ...prev };
        delete next[stockCode];
        return next;
      });
    }
  }, [message, t]);

  const selectedRun = runs.find((item) => item.run_id === selectedRunId) || result?.run || null;
  const activeRun = runs.find((item) => item.status === 'created' || item.status === 'running') || null;
  const summary = result?.summary || {};
  const decisionBreakdown = summary.decision_breakdown || {};
  const topCandidates = Array.isArray(summary.top_candidates) ? summary.top_candidates : [];
  const dataSyncTipMap = {
    all: t('common.sync_base_info_tip'),
    warehouse: t('common.sync_warehouse_base_info_tip'),
    core: t('common.sync_core_base_info_tip'),
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space direction="vertical" size={4}>
          <Title level={3} style={{ margin: 0 }}>
            <RobotOutlined /> {t('ai_stock_picker.title')}
          </Title>
          <Paragraph type="secondary" style={{ margin: 0 }}>
            {t('ai_stock_picker.description')}
          </Paragraph>
        </Space>
      </Card>

      <Modal
        title={t('ai_stock_picker.cards.data_sync')}
        open={isDataSyncModalOpen}
        onCancel={() => setIsDataSyncModalOpen(false)}
        onOk={() => handleBaseInfoSync(dataSyncScope)}
        confirmLoading={baseInfoSyncing}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Select
            value={dataSyncScope}
            onChange={setDataSyncScope}
            style={{ width: '100%' }}
            options={[
              { label: t('common.sync_core_base_info'), value: 'core' },
              { label: t('common.sync_warehouse_base_info'), value: 'warehouse' },
              { label: t('common.sync_base_info'), value: 'all' },
            ]}
          />
          <Text type="secondary">{dataSyncTipMap[dataSyncScope]}</Text>
          <Checkbox checked={resumeSync} onChange={(event) => setResumeSync(event.target.checked)}>
            {t('market.data_manager.resume_sync')}
          </Checkbox>
        </Space>
      </Modal>

      <Row gutter={[16, 16]} align="stretch">
        <Col xs={24} lg={8}>
          <Card
            title={t('ai_stock_picker.cards.config')}
            extra={
              <Space size={8}>
                <Button
                  icon={<SyncOutlined spin={baseInfoSyncing} />}
                  loading={baseInfoSyncing}
                  onClick={() => setIsDataSyncModalOpen(true)}
                >
                  {t('ai_stock_picker.cards.data_sync')}
                </Button>
                <Button icon={<ReloadOutlined />} onClick={() => loadRuns()}>{t('warehouse.refresh')}</Button>
              </Space>
            }
            style={{ height: '100%' }}
          >
            <Form
              form={form}
              layout="vertical"
              onValuesChange={(changedValues) => {
                const nextOverrides = { ...limitOverrides };
                if ('factor_candidate_limit' in changedValues) nextOverrides.factor_candidate_limit = true;
                if ('research_candidate_limit' in changedValues) nextOverrides.research_candidate_limit = true;
                if ('same_industry_limit' in changedValues) nextOverrides.same_industry_limit = true;
                if (
                  nextOverrides.factor_candidate_limit !== limitOverrides.factor_candidate_limit ||
                  nextOverrides.research_candidate_limit !== limitOverrides.research_candidate_limit ||
                  nextOverrides.same_industry_limit !== limitOverrides.same_industry_limit
                ) {
                  setLimitOverrides(nextOverrides);
                }
              }}
            >
              <Form.Item
                name="scope"
                label={t('ai_stock_picker.fields.scope')}
                extra={t('ai_stock_picker.field_tips.scope')}
                rules={[{ required: true }]}
              >
                <Select options={scopeOptions} />
              </Form.Item>
              <Form.Item
                name="style"
                label={t('ai_stock_picker.fields.style')}
                extra={t('ai_stock_picker.field_tips.style')}
                rules={[{ required: true }]}
              >
                <Select options={styleOptions} />
              </Form.Item>
              <Form.Item
                name="recommendation_count"
                label={t('ai_stock_picker.fields.recommendation_count')}
                extra={t('ai_stock_picker.field_tips.recommendation_count')}
                rules={[{ required: true }]}
              >
                <InputNumber min={4} max={8} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item
                name="factor_candidate_limit"
                label={t('ai_stock_picker.fields.factor_candidate_limit')}
                extra={t('ai_stock_picker.field_tips.factor_candidate_limit')}
                dependencies={['scope', 'recommendation_count']}
                rules={[{ required: true }, { validator: validateFactorCandidateLimit }]}
              >
                <InputNumber min={4} max={watchedScope ? factorLimitCaps[watchedScope] : 40} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item
                name="research_candidate_limit"
                label={t('ai_stock_picker.fields.research_candidate_limit')}
                extra={t('ai_stock_picker.field_tips.research_candidate_limit')}
                dependencies={['scope', 'recommendation_count', 'factor_candidate_limit']}
                rules={[{ required: true }, { validator: validateResearchCandidateLimit }]}
              >
                <InputNumber min={4} max={watchedScope ? researchLimitCaps[watchedScope] : 18} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item
                name="same_industry_limit"
                label={t('ai_stock_picker.fields.same_industry_limit')}
                extra={t('ai_stock_picker.field_tips.same_industry_limit')}
                dependencies={['recommendation_count']}
                rules={[{ required: true }, { validator: validateSameIndustryLimit }]}
              >
                <InputNumber min={1} max={watchedRecommendationCount || 8} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item
                name="allowed_industries"
                label={t('ai_stock_picker.fields.allowed_industries')}
                extra={t('ai_stock_picker.field_tips.allowed_industries')}
              >
                <Select mode="multiple" options={industryOptions} allowClear />
              </Form.Item>
              <Form.Item
                name="risk_level"
                label={t('ai_stock_picker.fields.risk_level')}
                extra={t('ai_stock_picker.field_tips.risk_level')}
                rules={[{ required: true }]}
              >
                <Select options={riskOptions} />
              </Form.Item>
              <Button
                type="primary"
                block
                icon={<PlayCircleOutlined />}
                loading={loading}
                disabled={activeRun !== null}
                onClick={handleStart}
              >
                {t('ai_stock_picker.actions.start')}
              </Button>
              {activeRun && (
                <Text type="secondary">
                  {t('ai_stock_picker.messages.active_run_exists', { run_id: activeRun.run_id })}
                </Text>
              )}
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={16}>
          <Card title={t('ai_stock_picker.cards.timeline')} style={{ height: '100%' }}>
            {events.length === 0 ? (
              <Empty description={t('ai_stock_picker.empty.events')} />
            ) : (
              <List
                size="small"
                dataSource={events}
                renderItem={(item) => {
                  const payloadItems = getEventPayloadItems(item);
                  return (
                    <List.Item>
                      <Space direction="vertical" size={0}>
                        <Space>
                          <Tag>{getStageLabel(item.stage)}</Tag>
                          <Tag color="blue">{item.event_type}</Tag>
                          <Text type="secondary">{dayjs(item.created_at).format('HH:mm:ss')}</Text>
                        </Space>
                        <Text>{item.message}</Text>
                        {payloadItems.length > 0 && (
                          <Space direction="vertical" size={0}>
                            {payloadItems.map((payloadItem) => (
                              <Text type="secondary" key={`${item.id}-${payloadItem.key}`}>
                                {`${payloadItem.label}: ${formatEventPayloadValue(payloadItem.value)}`}
                              </Text>
                            ))}
                          </Space>
                        )}
                      </Space>
                    </List.Item>
                  );
                }}
              />
            )}
          </Card>
        </Col>
      </Row>

      <Card
        title={t('ai_stock_picker.cards.history')}
        extra={
          <Popconfirm
            title={t('ai_stock_picker.confirmations.clear_runs')}
            okText={t('common.confirm')}
            cancelText={t('common.cancel')}
            onConfirm={handleClearRuns}
          >
            <Button danger icon={<DeleteOutlined />}>{t('ai_stock_picker.actions.clear_history')}</Button>
          </Popconfirm>
        }
      >
        <Table
          size="small"
          rowKey="run_id"
          dataSource={runs}
          pagination={false}
          locale={{ emptyText: t('ai_stock_picker.empty.runs') }}
          onRow={(record) => ({
            onClick: () => setSelectedRunId(record.run_id),
          })}
          rowClassName={(record) => (record.run_id === selectedRunId ? 'ant-table-row-selected' : '')}
          columns={[
            {
              title: t('ai_stock_picker.table.created_at'),
              dataIndex: 'created_at',
              render: (value: string) => dayjs(value).format('YYYY-MM-DD HH:mm:ss'),
            },
            {
              title: t('ai_stock_picker.table.scope'),
              dataIndex: 'scope',
              render: (value: string) => scopeLabelMap[value] || value,
            },
            {
              title: t('ai_stock_picker.table.style'),
              dataIndex: 'style',
              render: (value: string) => styleLabelMap[value] || value,
            },
            {
              title: t('ai_stock_picker.fields.recommendation_count'),
              dataIndex: 'recommendation_count',
            },
            {
              title: t('ai_stock_picker.fields.factor_candidate_limit'),
              dataIndex: 'factor_candidate_limit',
            },
            {
              title: t('ai_stock_picker.fields.research_candidate_limit'),
              dataIndex: 'research_candidate_limit',
            },
            {
              title: t('ai_stock_picker.table.status'),
              dataIndex: 'status',
              render: (value: string) => <Tag color={statusColor(value)}>{getStatusLabel(value)}</Tag>,
            },
            {
              title: t('ai_stock_picker.table.actions'),
              key: 'actions',
              render: (_, record: StockPickerRun) => (
                <Popconfirm
                  title={t('ai_stock_picker.confirmations.delete_run')}
                  okText={t('common.confirm')}
                  cancelText={t('common.cancel')}
                  onConfirm={(e) => {
                    e?.stopPropagation();
                    handleDeleteRun(record.run_id).catch(() => {
                      message.error(t('ai_stock_picker.messages.delete_failed'));
                    });
                  }}
                >
                  <Button
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={(e) => e.stopPropagation()}
                  />
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>

      {selectedRun && (
        <Card title={t('ai_stock_picker.cards.run_status')}>
          <Descriptions size="small" column={4}>
            <Descriptions.Item label={t('ai_stock_picker.run_details.run_id')}>{selectedRun.run_id}</Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.run_details.current_stage')}>{getStageLabel(selectedRun.current_stage)}</Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.run_details.status')}>
              <Tag color={statusColor(selectedRun.status)}>{getStatusLabel(selectedRun.status)}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.fields.recommendation_count')}>
              {selectedRun.recommendation_count}
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.fields.factor_candidate_limit')}>
              {selectedRun.factor_candidate_limit}
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.fields.research_candidate_limit')}>
              {selectedRun.research_candidate_limit}
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.fields.same_industry_limit')}>
              {selectedRun.same_industry_limit}
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.run_details.risk_level')}>
              {riskLabelMap[selectedRun.risk_level] || selectedRun.risk_level}
            </Descriptions.Item>
            <Descriptions.Item label={t('ai_stock_picker.fields.allowed_industries')} span={4}>
              {selectedRun.allowed_industries?.length ? selectedRun.allowed_industries.join(' / ') : '-'}
            </Descriptions.Item>
          </Descriptions>
          {getFailureHint(selectedRun.status) && (
            <Paragraph type="warning" style={{ marginTop: 12, marginBottom: 0 }}>
              {getFailureHint(selectedRun.status)}
            </Paragraph>
          )}
          {selectedRun.error_message && (
            <Paragraph type="danger" style={{ marginTop: 12, marginBottom: 0 }}>
              {selectedRun.error_message}
            </Paragraph>
          )}
        </Card>
      )}

      <Card title={t('ai_stock_picker.cards.recommendations')}>
        {!result ? (
          <Empty description={t('ai_stock_picker.empty.result')} />
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.style')}>
                {styleLabelMap[result.recommendations.style] || result.recommendations.style}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.scope')}>
                {scopeLabelMap[result.recommendations.scope] || result.recommendations.scope}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.generated_at')}>
                {result.recommendations.generated_at ? dayjs(result.recommendations.generated_at).format('YYYY-MM-DD HH:mm:ss') : '-'}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.research_mode')}>
                {summary.research_mode || '-'}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.decision_breakdown')}>
                {`${t('ai_stock_picker.decisions.keep')} ${decisionBreakdown.keep || 0} / ${t('ai_stock_picker.decisions.watch')} ${decisionBreakdown.watch || 0} / ${t('ai_stock_picker.decisions.drop')} ${decisionBreakdown.drop || 0}`}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.candidate_count')}>
                {summary.candidate_count ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.universe_count')}>
                {summary.universe_count ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.factor_candidate_count')}>
                {summary.factor_candidate_count ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.research_candidate_count')}>
                {summary.research_candidate_count ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label={t('ai_stock_picker.recommendations.same_industry_limit')}>
                {summary.same_industry_limit ?? '-'}
              </Descriptions.Item>
            </Descriptions>
            <Paragraph style={{ marginBottom: 0 }}>
              {result.recommendations.recommendation_logic || t('ai_stock_picker.empty.recommendation_logic')}
            </Paragraph>
            <Table
              size="small"
              rowKey="stock_code"
              dataSource={result.recommendations.stocks}
              pagination={false}
              locale={{ emptyText: t('ai_stock_picker.empty.recommendations') }}
              columns={[
                { title: t('ai_stock_picker.recommendations_table.rank'), dataIndex: 'rank', width: 70 },
                {
                  title: t('ai_stock_picker.recommendations_table.stock'),
                  key: 'stock',
                  render: (_, record: StockPickerResult['recommendations']['stocks'][number]) => (
                    <Space direction="vertical" size={0}>
                      <Text>{record.stock_name || '-'}</Text>
                      <Text type="secondary">{record.stock_code}</Text>
                    </Space>
                  ),
                },
                { title: t('ai_stock_picker.recommendations_table.conviction_score'), dataIndex: 'conviction_score' },
                { title: t('ai_stock_picker.recommendations_table.holding_horizon'), dataIndex: 'holding_horizon' },
                { title: t('ai_stock_picker.recommendations_table.entry_logic'), dataIndex: 'recommendation_reason', ellipsis: true },
                {
                  title: t('ai_stock_picker.fields.status'),
                  dataIndex: 'decision',
                  render: (value: string) => (
                    <Tag color={value === 'keep' ? 'green' : value === 'watch' ? 'blue' : 'default'}>
                      {t(`ai_stock_picker.decisions.${value}`)}
                    </Tag>
                  ),
                },
                {
                  title: t('ai_stock_picker.recommendations_table.risk_flags'),
                  dataIndex: 'risk_flags',
                  render: (value: string[]) => value?.length ? value.join(' / ') : '-',
                },
                {
                  title: t('common.action'),
                  key: 'actions',
                  width: 150,
                  render: (_, record: StockPickerRecommendationItem) => {
                    const isInWarehouse = warehouseStockCodes.has(record.stock_code);
                    return (
                      <Button
                        size="small"
                        type={isInWarehouse ? 'default' : 'primary'}
                        disabled={isInWarehouse}
                        loading={Boolean(addingWarehouseCodes[record.stock_code])}
                        onClick={() => handleAddToWarehouse(record)}
                      >
                        {isInWarehouse
                          ? t('ai_stock_picker.actions.in_warehouse')
                          : t('ai_stock_picker.actions.add_to_warehouse')}
                      </Button>
                    );
                  },
                },
              ]}
            />
          </Space>
        )}
      </Card>

      <Card title={t('ai_stock_picker.cards.candidates')}>
        {candidates.length === 0 ? (
          <Empty description={t('ai_stock_picker.empty.candidates')} />
        ) : (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {topCandidates.length > 0 && (
              <Card size="small" title={t('ai_stock_picker.cards.top_candidates')}>
                <Space wrap>
                  {topCandidates.map((item) => (
                    <Tag key={item.stock_code} color={item.decision === 'keep' ? 'green' : item.decision === 'watch' ? 'blue' : 'default'}>
                      {`${item.stock_name || item.stock_code} AI ${item.ai_score} / ${t('ai_stock_picker.top_candidates.quant')} ${item.factor_score} / ${t('ai_stock_picker.top_candidates.final')} ${item.final_score}`}
                    </Tag>
                  ))}
                </Space>
              </Card>
            )}
            <Table
              size="small"
              rowKey="stock_code"
              dataSource={candidates}
              pagination={false}
              locale={{ emptyText: t('ai_stock_picker.empty.candidates') }}
              columns={[
                {
                  title: t('ai_stock_picker.candidates_table.stock'),
                  key: 'stock',
                  render: (_, record: StockPickerCandidate) => (
                    <Space direction="vertical" size={0}>
                      <Text>{record.stock_name || '-'}</Text>
                      <Text type="secondary">{record.stock_code}</Text>
                    </Space>
                  ),
                },
                {
                  title: t('ai_stock_picker.candidates_table.industry'),
                  dataIndex: 'industry',
                  render: (value?: string | null) => value || '-',
                },
                { title: t('ai_stock_picker.candidates_table.factor_score'), dataIndex: 'factor_score' },
                { title: t('ai_stock_picker.candidates_table.ai_score'), dataIndex: 'ai_score' },
                { title: t('ai_stock_picker.candidates_table.final_score'), dataIndex: 'final_score' },
                {
                  title: t('ai_stock_picker.candidates_table.quant_support'),
                  render: (_, record: StockPickerCandidate) => formatQuantSupport(record.quant_support),
                },
                {
                  title: t('ai_stock_picker.candidates_table.decision'),
                  dataIndex: 'decision',
                  render: (value: string) => <Tag>{t(`ai_stock_picker.decisions.${value}`)}</Tag>,
                },
                {
                  title: t('ai_stock_picker.candidates_table.thesis'),
                  render: (_, record: StockPickerCandidate) => record.research_payload?.thesis || '-',
                },
                { title: t('ai_stock_picker.candidates_table.eliminated_reason'), dataIndex: 'eliminated_reason' },
              ]}
            />
          </Space>
        )}
      </Card>
    </Space>
  );
};
