import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AutoComplete,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  App as AntdApp,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Checkbox,
  Select,
} from 'antd';
import {
  BarChartOutlined,
  DeleteOutlined,
  FileSearchOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { sessionApi, Session } from '../../api/session';
import { marketApi } from '../../api/market';
import { stockAnalysisApi } from '../../api/stockAnalysis';
import { AsyncTaskRecord, tasksApi } from '../../api/tasks';
import { DecisionAuditLog } from '../../features/brain/DecisionAuditLog';
import { useSessionStore } from '../../store/useSessionStore';
import { getApiErrorMessage } from '../../utils/errorUtils';

const STOCK_ANALYSIS_TASK_TYPE = 'stock_analysis';
const FINISHED_TASK_STATUS = new Set(['completed', 'failed', 'cancelled']);
const STOCK_ANALYSIS_HISTORY_PAGE_SIZE = 10;
const DEBATE_SESSIONS_REFRESH_EVENT = 'debate-sessions-refresh';
const DEBATE_SESSION_AUTO_REFRESH_INTERVAL_OPTIONS = [10, 30, 60, 300] as const;
const DEFAULT_DEBATE_SESSION_AUTO_REFRESH_SECONDS = 30;

interface StockOption {
  value: string;
  label: string;
}

interface StockAnalysisResult {
  question?: string;
  answer_markdown?: string;
  completed_at?: string;
  tool_trace?: Array<Record<string, unknown>>;
}

const { Text } = Typography;
const { TextArea } = Input;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const getRecordString = (record: Record<string, unknown>, keys: string[]): string | undefined => {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
};

const toStockOption = (item: unknown): StockOption | null => {
  if (!isRecord(item)) return null;
  const stockCode = getRecordString(item, ['stock_code', 'stock_basic.stock_code', 'code']);
  const stockName = getRecordString(item, ['stock_name', 'stock_basic.name', 'name']);
  if (!stockCode) return null;
  return {
    value: stockCode,
    label: stockName ? `${stockCode} - ${stockName}` : stockCode,
  };
};

const getStockAnalysisResult = (task?: AsyncTaskRecord | null): StockAnalysisResult | null => {
  if (!task || !isRecord(task.result)) return null;
  return task.result as StockAnalysisResult;
};

const getTaskParameterString = (task: AsyncTaskRecord, keys: string[]): string | undefined => {
  if (!isRecord(task.parameters)) return undefined;
  return getRecordString(task.parameters, keys);
};

const getTaskResultString = (task: AsyncTaskRecord, keys: string[]): string | undefined => {
  if (!isRecord(task.result)) return undefined;
  return getRecordString(task.result, keys);
};

const getStockAnalysisQuestion = (task: AsyncTaskRecord): string => (
  getTaskParameterString(task, ['question']) ||
  getTaskResultString(task, ['question']) ||
  '-'
);

const formatTaskDate = (value?: string | null): string => (value ? new Date(value).toLocaleString() : '-');

const sessionSourceColor: Record<Session['source'], string> = {
  manual: 'blue',
  scheduled: 'purple',
  market_watch: 'cyan',
  stop_loss: 'red',
  take_profit: 'green',
};

interface DebateManagementPanelProps {
  isActive?: boolean;
}

export const DebateManagementPanel: React.FC<DebateManagementPanelProps> = ({ isActive = true }) => {
  const { t } = useTranslation();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [reportModalVisible, setReportModalVisible] = useState(false);
  const [reportSessionId, setReportSessionId] = useState<string | null>(null);
  const [searchText, setSearchText] = useState('');
  const [debouncedSearchText, setDebouncedSearchText] = useState('');
  const [sessionsTotal, setSessionsTotal] = useState(0);
  const [sessionsPage, setSessionsPage] = useState(1);
  const [sessionsPageSize, setSessionsPageSize] = useState(10);
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(false);
  const [autoRefreshIntervalSeconds, setAutoRefreshIntervalSeconds] = useState(
    DEFAULT_DEBATE_SESSION_AUTO_REFRESH_SECONDS,
  );
  const { setActiveSession } = useSessionStore();
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();

  const fetchSessions = useCallback(async (page: number, pageSize: number) => {
    setLoading(true);
    try {
      const response = await sessionApi.listPaginated({
        skip: (page - 1) * pageSize,
        limit: pageSize,
        q: debouncedSearchText.trim() || undefined,
      });
      setSessions(response.items);
      setSessionsTotal(response.total);
      setSessionsPage(page);
      setSessionsPageSize(pageSize);
      setSelectedRowKeys([]);
    } catch {
      message.error(t('session.load_failed'));
    } finally {
      setLoading(false);
    }
  }, [debouncedSearchText, message, t]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearchText(searchText);
      setSessionsPage(1);
    }, 300);

    return () => window.clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    if (!isActive) return;
    void fetchSessions(sessionsPage, sessionsPageSize);
  }, [fetchSessions, isActive, sessionsPage, sessionsPageSize]);

  useEffect(() => {
    if (!isActive || !autoRefreshEnabled) return undefined;

    const timer = window.setInterval(() => {
      void fetchSessions(sessionsPage, sessionsPageSize);
    }, autoRefreshIntervalSeconds * 1000);

    return () => window.clearInterval(timer);
  }, [autoRefreshEnabled, autoRefreshIntervalSeconds, fetchSessions, isActive, sessionsPage, sessionsPageSize]);

  useEffect(() => {
    const handleDebateSessionsRefresh = () => {
      void fetchSessions(sessionsPage, sessionsPageSize);
    };
    window.addEventListener(DEBATE_SESSIONS_REFRESH_EVENT, handleDebateSessionsRefresh);
    return () => {
      window.removeEventListener(DEBATE_SESSIONS_REFRESH_EVENT, handleDebateSessionsRefresh);
    };
  }, [fetchSessions, sessionsPage, sessionsPageSize]);

  const handleResume = (session: Session) => {
    setActiveSession(session);
    message.success(t('session.resumed_msg', { stock: session.stock_name }));
    navigate(`/dashboard?session_id=${encodeURIComponent(session.session_id)}`);
  };

  const handleArchive = async (sessionId: string) => {
    try {
      await sessionApi.archive(sessionId);
      message.success(t('session.archived_msg'));
      fetchSessions(sessionsPage, sessionsPageSize);
    } catch (error) {
      const errorMessage = getApiErrorMessage(error, t('common.error'));
      message.error(errorMessage);
    }
  };

  const handleDelete = async (sessionId: string) => {
    Modal.confirm({
      title: t('session.delete_confirm_title'),
      content: t('session.delete_confirm_desc'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: async () => {
        try {
          await sessionApi.delete(sessionId);
          message.success(t('session.deleted_msg'));
          fetchSessions(sessionsPage, sessionsPageSize);
        } catch (error) {
          const errorMessage = getApiErrorMessage(error, t('common.error'));
          message.error(errorMessage);
        }
      },
    });
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) return;

    Modal.confirm({
      title: t('session.batch_delete_confirm_title'),
      content: t('session.batch_delete_confirm_desc', { count: selectedRowKeys.length }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: async () => {
        try {
          const res = await sessionApi.batchDelete(selectedRowKeys as string[]);
          message.success(res.message || t('session.batch_deleted_msg'));
          setSelectedRowKeys([]);
          fetchSessions(1, sessionsPageSize);
        } catch (error) {
          const errorMessage = getApiErrorMessage(error, t('common.error'));
          message.error(errorMessage);
        }
      },
    });
  };

  const handleBatchArchive = () => {
    if (selectedRowKeys.length === 0) return;

    Modal.confirm({
      title: t('session.batch_archive_confirm_title'),
      content: t('session.batch_archive_confirm_desc', { count: selectedRowKeys.length }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await sessionApi.batchArchive(selectedRowKeys as string[]);
          message.success(res.message || t('session.batch_archived_msg'));
          setSelectedRowKeys([]);
          fetchSessions(sessionsPage, sessionsPageSize);
        } catch (error) {
          const errorMessage = getApiErrorMessage(error, t('common.error'));
          message.error(errorMessage);
        }
      },
    });
  };

  const columns = [
    { title: t('session.col_id'), dataIndex: 'session_id', width: 80, ellipsis: true },
    {
      title: t('session.col_stock'),
      dataIndex: 'stock_name',
      sorter: (a: Session, b: Session) => a.stock_name.localeCompare(b.stock_name),
      render: (value: string, record: Session) => `${value} (${record.stock_code})`,
    },
    {
      title: t('session.col_status'),
      dataIndex: 'status',
      render: (status: string) => {
        const color = status === 'active' ? 'green' : 'default';
        return <Tag color={color}>{status.toUpperCase()}</Tag>;
      },
    },
    {
      title: t('session.col_source'),
      dataIndex: 'source',
      width: 100,
      render: (source: Session['source']) => (
        <Tag color={sessionSourceColor[source] || 'default'}>{t(`session.source_${source || 'manual'}`)}</Tag>
      ),
    },
    {
      title: t('session.col_created'),
      dataIndex: 'created_at',
      sorter: (a: Session, b: Session) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      defaultSortOrder: 'descend' as const,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: t('session.col_ended'),
      dataIndex: 'ended_at',
      sorter: (a: Session, b: Session) => {
        const left = a.ended_at ? new Date(a.ended_at).getTime() : 0;
        const right = b.ended_at ? new Date(b.ended_at).getTime() : 0;
        return left - right;
      },
      render: (value?: string | null) => (value ? new Date(value).toLocaleString() : '-'),
    },
    {
      title: t('session.col_actions'),
      key: 'actions',
      width: 160,
      render: (_: unknown, record: Session) => (
        <Space size={4}>
          <Button
            type="primary"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleResume(record)}
            title={t('session.resume')}
          />
          <Button
            size="small"
            icon={<InboxOutlined />}
            onClick={() => handleArchive(record.session_id)}
            title={t('session.archive')}
          />
          <Button
            size="small"
            type="primary"
            icon={<FileSearchOutlined />}
            onClick={() => {
              setReportSessionId(record.session_id);
              setReportModalVisible(true);
            }}
            title={t('session.view_report')}
          />
          <Button
            danger
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.session_id)}
            title={t('common.delete')}
          />
        </Space>
      ),
    },
  ];

  const rowSelection = {
    selectedRowKeys,
    onChange: (newSelectedRowKeys: React.Key[]) => setSelectedRowKeys(newSelectedRowKeys),
  };

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <Space wrap>
          <Button icon={<InboxOutlined />} onClick={handleBatchArchive} disabled={selectedRowKeys.length === 0}>
            {t('session.batch_archive')}
          </Button>
          <Button danger icon={<DeleteOutlined />} onClick={handleBatchDelete} disabled={selectedRowKeys.length === 0}>
            {t('session.batch_delete')}
          </Button>
          <Button icon={<FolderOpenOutlined />} onClick={() => fetchSessions(sessionsPage, sessionsPageSize)}>{t('session.refresh')}</Button>
          <Checkbox
            checked={autoRefreshEnabled}
            onChange={(event) => setAutoRefreshEnabled(event.target.checked)}
          >
            {t('session.auto_refresh')}
          </Checkbox>
          <Select<number>
            size="small"
            value={autoRefreshIntervalSeconds}
            disabled={!autoRefreshEnabled}
            onChange={setAutoRefreshIntervalSeconds}
            style={{ width: 120 }}
            options={DEBATE_SESSION_AUTO_REFRESH_INTERVAL_OPTIONS.map((seconds) => ({
              value: seconds,
              label: seconds < 60
                ? t('session.auto_refresh_interval_seconds', { seconds })
                : t('session.auto_refresh_interval_minutes', { minutes: seconds / 60 }),
            }))}
          />
          <Input
            placeholder={t('common.input_stock_placeholder')}
            allowClear
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            style={{ width: 200 }}
          />
        </Space>
      </div>

      <Table
        rowSelection={rowSelection}
        dataSource={sessions}
        columns={columns}
        rowKey="session_id"
        loading={loading}
        pagination={{
          current: sessionsPage,
          pageSize: sessionsPageSize,
          total: sessionsTotal,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          onChange: (page, pageSize) => fetchSessions(page, pageSize),
        }}
        locale={{
          triggerDesc: t('common.sort_desc'),
          triggerAsc: t('common.sort_asc'),
          cancelSort: t('common.sort_cancel'),
        }}
      />

      <Modal
        title={t('session.report_title')}
        open={reportModalVisible}
        onCancel={() => {
          setReportModalVisible(false);
          setReportSessionId(null);
        }}
        footer={null}
        width="90%"
        styles={{ body: { padding: 0, height: '85vh' } }}
      >
        {reportSessionId && <DecisionAuditLog sessionId={reportSessionId} isActive={reportModalVisible} />}
      </Modal>
    </div>
  );
};

interface StockResearchAnalysisPanelProps {
  isActive?: boolean;
}

export const StockResearchAnalysisPanel: React.FC<StockResearchAnalysisPanelProps> = ({ isActive = true }) => {
  const { t } = useTranslation();
  const [stockOptions, setStockOptions] = useState<StockOption[]>([]);
  const [selectedStockCode, setSelectedStockCode] = useState('');
  const [analysisQuestion, setAnalysisQuestion] = useState('');
  const [submittingAnalysis, setSubmittingAnalysis] = useState(false);
  const [loadingLatestAnalysis, setLoadingLatestAnalysis] = useState(false);
  const [currentAnalysisTask, setCurrentAnalysisTask] = useState<AsyncTaskRecord | null>(null);
  const [analysisHistory, setAnalysisHistory] = useState<AsyncTaskRecord[]>([]);
  const [analysisHistoryTotal, setAnalysisHistoryTotal] = useState(0);
  const [analysisHistoryPage, setAnalysisHistoryPage] = useState(1);
  const [analysisHistoryPageSize, setAnalysisHistoryPageSize] = useState(STOCK_ANALYSIS_HISTORY_PAGE_SIZE);
  const { message } = AntdApp.useApp();

  const loadAnalysisHistory = useCallback(async (page: number, pageSize: number, preferredTaskId?: string) => {
    setLoadingLatestAnalysis(true);
    try {
      const response = await tasksApi.listTasks({
        task_type: STOCK_ANALYSIS_TASK_TYPE,
        limit: pageSize,
        skip: (page - 1) * pageSize,
      });
      setAnalysisHistory(response.items);
      setAnalysisHistoryTotal(response.total);
      setAnalysisHistoryPage(page);
      setAnalysisHistoryPageSize(pageSize);

      const preferredTask = preferredTaskId
        ? response.items.find((task) => task.task_id === preferredTaskId)
        : null;
      setCurrentAnalysisTask((previousTask) => {
        if (preferredTask) return preferredTask;
        if (!previousTask || preferredTaskId === undefined) return response.items[0] || null;
        return previousTask;
      });
    } catch (error) {
      const errorMessage = getApiErrorMessage(error, t('session.stock_analysis_load_failed'));
      message.error(errorMessage);
    } finally {
      setLoadingLatestAnalysis(false);
    }
  }, [message, t]);

  useEffect(() => {
    if (!isActive) return;
    void loadAnalysisHistory(1, STOCK_ANALYSIS_HISTORY_PAGE_SIZE);
  }, [isActive, loadAnalysisHistory]);

  useEffect(() => {
    if (!currentAnalysisTask || FINISHED_TASK_STATUS.has(currentAnalysisTask.status)) return undefined;

    const timer = window.setInterval(async () => {
      try {
        const task = await tasksApi.getTask(currentAnalysisTask.task_id);
        setCurrentAnalysisTask(task);
        setAnalysisHistory((previousTasks) => previousTasks.map((item) => (
          item.task_id === task.task_id ? task : item
        )));
      } catch (error) {
        const errorMessage = getApiErrorMessage(error, t('session.stock_analysis_load_failed'));
        message.error(errorMessage);
        window.clearInterval(timer);
      }
    }, 3000);

    return () => window.clearInterval(timer);
  }, [currentAnalysisTask, message, t]);

  const searchStocks = useCallback(
    async (keyword: string) => {
      const query = keyword.trim();
      if (!query) {
        setStockOptions([]);
        return;
      }
      try {
        const response = await marketApi.getDbStocks({ query, limit: 20 });
        setStockOptions(response.items.map(toStockOption).filter((option): option is StockOption => Boolean(option)));
      } catch (error) {
        const errorMessage = getApiErrorMessage(error, t('session.stock_search_failed'));
        message.error(errorMessage);
      }
    },
    [message, t],
  );

  const submitStockAnalysis = async () => {
    const stockCode = selectedStockCode.trim();
    const question = analysisQuestion.trim() || t('session.stock_analysis_default_question');
    setSubmittingAnalysis(true);
    try {
      const response = await stockAnalysisApi.run({
        stock_code: stockCode || undefined,
        question,
      });
      const submittedTask = {
        task_id: response.task_id,
        task_name: response.task_name,
        task_type: STOCK_ANALYSIS_TASK_TYPE,
        status: response.status,
        allow_concurrent: true,
        parameters: { stock_code: stockCode || null, question },
      };
      setCurrentAnalysisTask({
        ...submittedTask,
      });
      setAnalysisQuestion(question);
      await loadAnalysisHistory(1, analysisHistoryPageSize, response.task_id);
      message.success(t('session.stock_analysis_submitted'));
    } catch (error) {
      const errorMessage = getApiErrorMessage(error, t('session.stock_analysis_submit_failed'));
      message.error(errorMessage);
    } finally {
      setSubmittingAnalysis(false);
    }
  };

  const analysisResult = getStockAnalysisResult(currentAnalysisTask);
  const analysisStatusColor = useMemo(() => {
    if (!currentAnalysisTask) return 'default';
    if (currentAnalysisTask.status === 'completed') return 'green';
    if (currentAnalysisTask.status === 'failed') return 'red';
    if (currentAnalysisTask.status === 'running') return 'blue';
    return 'gold';
  }, [currentAnalysisTask]);

  const historyColumns = [
    {
      title: t('session.stock_analysis_history_created'),
      dataIndex: 'created_at',
      width: 170,
      render: formatTaskDate,
    },
    {
      title: t('session.stock_analysis_history_question'),
      ellipsis: true,
      render: (_: unknown, record: AsyncTaskRecord) => getStockAnalysisQuestion(record),
    },
    {
      title: t('session.col_status'),
      dataIndex: 'status',
      width: 110,
      render: (status: string) => {
        const color = status === 'completed' ? 'green' : status === 'failed' ? 'red' : 'blue';
        return <Tag color={color}>{status.toUpperCase()}</Tag>;
      },
    },
    {
      title: t('session.stock_analysis_history_completed'),
      dataIndex: 'completed_at',
      width: 170,
      render: formatTaskDate,
    },
    {
      title: t('session.col_actions'),
      width: 90,
      render: (_: unknown, record: AsyncTaskRecord) => (
        <Button size="small" onClick={() => setCurrentAnalysisTask(record)}>
          {t('session.stock_analysis_history_view')}
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap align="start">
            <AutoComplete
              allowClear
              value={selectedStockCode}
              options={stockOptions}
              onSearch={searchStocks}
              onSelect={(value) => setSelectedStockCode(value)}
              onChange={(value) => setSelectedStockCode(value)}
              placeholder={t('session.stock_analysis_stock_placeholder')}
              style={{ width: 320 }}
            />
            <Button
              type="primary"
              icon={<BarChartOutlined />}
              loading={submittingAnalysis}
              onClick={submitStockAnalysis}
            >
              {t('session.stock_analysis_run')}
            </Button>
            <Button
              icon={<FolderOpenOutlined />}
              onClick={() => loadAnalysisHistory(1, analysisHistoryPageSize)}
              loading={loadingLatestAnalysis}
            >
              {t('session.stock_analysis_refresh_latest')}
            </Button>
          </Space>
          <TextArea
            value={analysisQuestion}
            onChange={(event) => setAnalysisQuestion(event.target.value)}
            maxLength={100000}
            showCount
            autoSize={{ minRows: 4, maxRows: 10 }}
            placeholder={t('session.stock_analysis_default_question')}
          />
        </Space>
      </Card>

      <Card
        size="small"
        title={t('session.stock_analysis_history')}
        extra={
          <Button
            icon={<FolderOpenOutlined />}
            onClick={() => loadAnalysisHistory(analysisHistoryPage, analysisHistoryPageSize, currentAnalysisTask?.task_id)}
            loading={loadingLatestAnalysis}
          >
            {t('session.stock_analysis_refresh_history')}
          </Button>
        }
      >
        <Table
          size="small"
          rowKey="task_id"
          columns={historyColumns}
          dataSource={analysisHistory}
          loading={loadingLatestAnalysis}
          scroll={{ y: 220 }}
          rowClassName={(record) => (record.task_id === currentAnalysisTask?.task_id ? 'ant-table-row-selected' : '')}
          onRow={(record) => ({
            onClick: () => setCurrentAnalysisTask(record),
          })}
          pagination={{
            current: analysisHistoryPage,
            pageSize: analysisHistoryPageSize,
            total: analysisHistoryTotal,
            showSizeChanger: true,
            pageSizeOptions: ['10', '20', '50'],
            onChange: (page, pageSize) => loadAnalysisHistory(page, pageSize, currentAnalysisTask?.task_id),
          }}
          locale={{ emptyText: t('session.stock_analysis_history_empty') }}
        />
      </Card>

      <Card
        size="small"
        title={t('session.stock_analysis_result')}
        extra={
          currentAnalysisTask ? (
            <Space size={8}>
              <Tag color={analysisStatusColor}>{currentAnalysisTask.status.toUpperCase()}</Tag>
              <Text type="secondary">{currentAnalysisTask.task_id}</Text>
            </Space>
          ) : null
        }
      >
        <Spin spinning={loadingLatestAnalysis || Boolean(currentAnalysisTask && !FINISHED_TASK_STATUS.has(currentAnalysisTask.status))}>
          {!currentAnalysisTask ? (
            <Empty description={t('session.stock_analysis_empty')} />
          ) : currentAnalysisTask.status === 'failed' ? (
            <Text type="danger">{currentAnalysisTask.error_message || t('common.error')}</Text>
          ) : analysisResult?.answer_markdown ? (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap>
                {analysisResult.completed_at ? (
                  <Text type="secondary">{formatTaskDate(analysisResult.completed_at)}</Text>
                ) : null}
              </Space>
              {analysisResult.question ? <Text type="secondary">{analysisResult.question}</Text> : null}
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{analysisResult.answer_markdown}</ReactMarkdown>
              </div>
            </Space>
          ) : (
            <Empty description={t('session.stock_analysis_waiting')} />
          )}
        </Spin>
      </Card>
    </Space>
  );
};
