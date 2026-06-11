import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import {
  App as AntdApp,
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Upload,
} from 'antd';
import type { RcFile, UploadFile } from 'antd/es/upload/interface';
import type { ColumnsType } from 'antd/es/table';
import { sourcesApi } from '../api/settings';
import { PromptStats, UsageBreakdownEntry, promptApi } from '../api/prompt';
import { testingApi } from '../api/testing';
import type {
  AiFunctionTestResult,
  AiFunctionScenario,
  MemoryPreviewParams,
  MemoryRecallAuditPreviewParams,
  NewsTestingTool,
  ToolDocstringItem,
} from '../api/testing';
import { tasksApi } from '../api/tasks';
import type { AsyncTaskRecord } from '../api/tasks';
import { newsPluginsApi } from '../api/newsPlugins';
import type { NewsPluginBatchUploadResult, NewsPluginItem, NewsPluginMutationResult } from '../api/newsPlugins';
import { skillsApi } from '../api/skills';
import type { SkillItem } from '../api/skills';
import { mcpApi } from '../api/mcp';
import type { MCPServerItem, MCPToolItem } from '../api/mcp';
import { useTranslation } from 'react-i18next';
import { getApiErrorMessage, getApiErrorResponseData } from '../utils/errorUtils';
import { useSearchParams } from 'react-router-dom';
import { TaskCompletedMessage, WebSocketMessage } from '../services/websocket';
import { useWebSocketSubscription } from '../hooks/useWebSocketSubscription';

const AI_FUNCTION_SCENARIOS: Array<{ key: AiFunctionScenario; titleKey: string; placeholderKey: string }> = [
  {
    key: 'no_tools',
    titleKey: 'settings.ai_test_no_tools',
    placeholderKey: 'settings.ai_test_no_tools_placeholder',
  },
  {
    key: 'tools',
    titleKey: 'settings.ai_test_tools',
    placeholderKey: 'settings.ai_test_tools_placeholder',
  },
  {
    key: 'skills',
    titleKey: 'settings.ai_test_skills',
    placeholderKey: 'settings.ai_test_skills_placeholder',
  },
  {
    key: 'tools_and_skills',
    titleKey: 'settings.ai_test_tools_and_skills',
    placeholderKey: 'settings.ai_test_tools_and_skills_placeholder',
  },
  {
    key: 'thinking_tools',
    titleKey: 'settings.ai_test_thinking_tools',
    placeholderKey: 'settings.ai_test_thinking_tools_placeholder',
  },
  {
    key: 'thinking_skills',
    titleKey: 'settings.ai_test_thinking_skills',
    placeholderKey: 'settings.ai_test_thinking_skills_placeholder',
  },
];

const DEFAULT_AI_TEST_INPUT_KEYS: Record<AiFunctionScenario, string> = {
  no_tools: 'settings.ai_test_no_tools_default_input',
  tools: 'settings.ai_test_tools_default_input',
  skills: 'settings.ai_test_skills_default_input',
  tools_and_skills: 'settings.ai_test_tools_and_skills_default_input',
  thinking_tools: 'settings.ai_test_thinking_tools_default_input',
  thinking_skills: 'settings.ai_test_thinking_skills_default_input',
};

const DISABLED_DELETE_SKILL_IDS = new Set(['tushare-data']);

type AiFunctionTaskTracker = {
  scenario: AiFunctionScenario;
  userInput: string;
};

type MCPServerFormValues = {
  name: string;
  enabled?: boolean;
  url: string;
  token?: string;
  allowed_tools?: string[];
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const isAiFunctionTestResult = (value: unknown): value is AiFunctionTestResult => (
  isRecord(value) &&
  typeof value.status === 'string' &&
  typeof value.scenario === 'string' &&
  isRecord(value.input) &&
  isRecord(value.output)
);

const isAiFunctionTaskTerminal = (status?: string): boolean =>
  status === 'completed' || status === 'success' || status === 'failed' || status === 'error';

const isAiFunctionTaskSuccessful = (status?: string): boolean =>
  status === 'completed' || status === 'success';

const buildAiFunctionTaskOutput = (tracker: AiFunctionTaskTracker, task: AsyncTaskRecord) => ({
  request: {
    scenario: tracker.scenario,
    user_input: tracker.userInput,
  },
  task: {
    task_id: task.task_id,
    task_name: task.task_name,
    status: task.status,
    error_message: task.error_message,
    created_at: task.created_at,
    started_at: task.started_at,
    completed_at: task.completed_at,
  },
  response: task.result,
});

const formatApiOutput = (value: unknown): string => {
  if (typeof value === 'string') {
    return value;
  }
  return JSON.stringify(value, null, 2);
};

const SETTINGS_TAB_KEYS = new Set([
  'datasources',
  'prompts',
  'news_plugins',
  'mcp',
  'skills',
  'memory-preview',
  'memory-recall-audits',
  'playground',
  'stats',
]);

const formatPercent = (value?: number): string => `${((value || 0) * 100).toFixed(2)}%`;

type UsageRoleRow = {
  role: string;
  calls: number;
  inputTokens: number;
  cachedTokens: number;
  cacheMissTokens: number;
  cacheHitRate: number;
};

const isNewsPluginBatchUploadResult = (
  result: NewsPluginBatchUploadResult | NewsPluginMutationResult,
): result is NewsPluginBatchUploadResult => Array.isArray((result as NewsPluginBatchUploadResult).items);

export const SettingsPage: React.FC = () => {
  const { t } = useTranslation();
  const { message, modal } = AntdApp.useApp();
  const [settingsSearchParams, setSettingsSearchParams] = useSearchParams();
  const diagnosticPanelStyle: React.CSSProperties = {
    backgroundColor: 'var(--app-bg-muted)',
    border: '1px solid var(--app-border)',
    borderRadius: 8,
    color: 'var(--app-text)',
    maxHeight: '20rem',
    overflow: 'auto',
    overflowWrap: 'anywhere',
    padding: '12px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  };
  const promptTextBlockStyle: React.CSSProperties = {
    maxWidth: '100%',
    minWidth: 0,
    overflow: 'auto',
    overflowWrap: 'anywhere',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
  };
  const mcpToolInstructionBlockStyle: React.CSSProperties = {
    ...promptTextBlockStyle,
    maxHeight: 360,
    overflowY: 'auto',
  };
  const activeSettingsTab = SETTINGS_TAB_KEYS.has(settingsSearchParams.get('tab') || '')
    ? settingsSearchParams.get('tab') || 'datasources'
    : 'datasources';

  const handleSettingsTabChange = (activeKey: string) => {
    const nextSearchParams = new URLSearchParams(settingsSearchParams);
    if (activeKey === 'datasources') {
      nextSearchParams.delete('tab');
    } else {
      nextSearchParams.set('tab', activeKey);
    }
    setSettingsSearchParams(nextSearchParams);
  };

  // Tushare Config State
  const [tushareForm] = Form.useForm();
  const [tushareLoading, setTushareLoading] = useState(false);

  // Prompt Config State
  const [prompts, setPrompts] = useState<Record<string, string>>({});

  // Playground State
  const [aiTestInputs, setAiTestInputs] = useState<Record<AiFunctionScenario, string>>(() => ({
    no_tools: t(DEFAULT_AI_TEST_INPUT_KEYS.no_tools),
    tools: t(DEFAULT_AI_TEST_INPUT_KEYS.tools),
    skills: t(DEFAULT_AI_TEST_INPUT_KEYS.skills),
    tools_and_skills: t(DEFAULT_AI_TEST_INPUT_KEYS.tools_and_skills),
    thinking_tools: t(DEFAULT_AI_TEST_INPUT_KEYS.thinking_tools),
    thinking_skills: t(DEFAULT_AI_TEST_INPUT_KEYS.thinking_skills),
  }));
  const [aiTestOutputs, setAiTestOutputs] = useState<Record<AiFunctionScenario, string>>({
    no_tools: '',
    tools: '',
    skills: '',
    tools_and_skills: '',
    thinking_tools: '',
    thinking_skills: '',
  });
  const [aiTestLoading, setAiTestLoading] = useState<Record<AiFunctionScenario, boolean>>({
    no_tools: false,
    tools: false,
    skills: false,
    tools_and_skills: false,
    thinking_tools: false,
    thinking_skills: false,
  });
  const aiFunctionTaskTrackersRef = useRef<Record<string, AiFunctionTaskTracker>>({});
  const aiFunctionTaskIdByScenarioRef = useRef<Partial<Record<AiFunctionScenario, string>>>({});

  // Stats State
  const [stats, setStats] = useState<PromptStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [clearStatsLoading, setClearStatsLoading] = useState(false);

  // System Testing State
  const [testRedisLoading, setTestRedisLoading] = useState(false);
  const [testDbLoading, setTestDbLoading] = useState(false);
  const [testTushareLoading, setTestTushareLoading] = useState(false);
  const [testTavilyLoading, setTestTavilyLoading] = useState(false);
  const [testPythonSandboxLoading, setTestPythonSandboxLoading] = useState(false);
  const [testSkillsLoading, setTestSkillsLoading] = useState(false);
  const [testDbSchemaLoading, setTestDbSchemaLoading] = useState(false);
  const [testQueryCalcLoading, setTestQueryCalcLoading] = useState(false);
  const [testPdfToolLoading, setTestPdfToolLoading] = useState(false);
  const [pdfToolTestUrl, setPdfToolTestUrl] = useState('');
  const [testMemoryLoading, setTestMemoryLoading] = useState(false);
  const [testMemoryReadLoading, setTestMemoryReadLoading] = useState(false);
  const [testMemoryPreviewLoading, setTestMemoryPreviewLoading] = useState(false);
  const [testMemoryRecallAuditLoading, setTestMemoryRecallAuditLoading] = useState(false);
  const [testDocstringLoading, setTestDocstringLoading] = useState(false);
  const [newsTestTools, setNewsTestTools] = useState<NewsTestingTool[]>([]);
  const [newsTestToolsLoading, setNewsTestToolsLoading] = useState(false);
  const [newsPlugins, setNewsPlugins] = useState<NewsPluginItem[]>([]);
  const [newsPluginsLoading, setNewsPluginsLoading] = useState(false);
  const [newsPluginModalOpen, setNewsPluginModalOpen] = useState(false);
  const [newsPluginSaving, setNewsPluginSaving] = useState(false);
  const [newsPluginFileList, setNewsPluginFileList] = useState<UploadFile[]>([]);
  const [newsPluginDeleting, setNewsPluginDeleting] = useState<Record<string, boolean>>({});
  const [selectedNewsPluginKeys, setSelectedNewsPluginKeys] = useState<string[]>([]);
  const [newsPluginBatchDeleting, setNewsPluginBatchDeleting] = useState(false);
  const [mcpServers, setMcpServers] = useState<MCPServerItem[]>([]);
  const [mcpServersLoading, setMcpServersLoading] = useState(false);
  const [mcpModalOpen, setMcpModalOpen] = useState(false);
  const [mcpSaving, setMcpSaving] = useState(false);
  const [editingMcpServer, setEditingMcpServer] = useState<MCPServerItem | null>(null);
  const [mcpActionLoading, setMcpActionLoading] = useState<Record<string, boolean>>({});
  const [mcpToolsModalOpen, setMcpToolsModalOpen] = useState(false);
  const [mcpToolsLoading, setMcpToolsLoading] = useState(false);
  const [mcpToolsServerName, setMcpToolsServerName] = useState('');
  const [mcpTools, setMcpTools] = useState<MCPToolItem[]>([]);
  const [mcpPreviewTools, setMcpPreviewTools] = useState<MCPToolItem[]>([]);
  const [mcpPreviewLoading, setMcpPreviewLoading] = useState(false);
  const [mcpPrompt, setMcpPrompt] = useState('');
  const [mcpPromptLoading, setMcpPromptLoading] = useState(false);
  const [mcpForm] = Form.useForm<MCPServerFormValues>();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillModalOpen, setSkillModalOpen] = useState(false);
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillFileList, setSkillFileList] = useState<UploadFile[]>([]);
  const [skillDeleting, setSkillDeleting] = useState<Record<string, boolean>>({});
  const [skillPrompt, setSkillPrompt] = useState('');
  const [skillPromptLoading, setSkillPromptLoading] = useState(false);
  const [dynamicNewsLoading, setDynamicNewsLoading] = useState<Record<string, boolean>>({});
  const [dynamicNewsKeywords, setDynamicNewsKeywords] = useState<Record<string, string>>({});
  const [newsTestResult, setNewsTestResult] = useState<Record<string, unknown> | null>(null);
  const [docstringModalOpen, setDocstringModalOpen] = useState(false);
  const [toolDocstrings, setToolDocstrings] = useState<ToolDocstringItem[]>([]);
  const [memoryPreviewItems, setMemoryPreviewItems] = useState<MemoryPreviewItem[]>([]);
  const [memoryPreviewTotal, setMemoryPreviewTotal] = useState(0);
  const [memoryPreviewPage, setMemoryPreviewPage] = useState(1);
  const [memoryPreviewPageSize, setMemoryPreviewPageSize] = useState(20);
  const [memoryPreviewFilters, setMemoryPreviewFilters] = useState<MemoryPreviewFilters>({
    userId: '',
    stockCode: '',
    status: undefined,
  });
  const [memoryRecallAuditItems, setMemoryRecallAuditItems] = useState<MemoryRecallAuditItem[]>([]);
  const [memoryRecallAuditTotal, setMemoryRecallAuditTotal] = useState(0);
  const [memoryRecallAuditPage, setMemoryRecallAuditPage] = useState(1);
  const [memoryRecallAuditPageSize, setMemoryRecallAuditPageSize] = useState(20);
  const [memoryRecallAuditFilters, setMemoryRecallAuditFilters] = useState<MemoryRecallAuditFilters>({
    userId: '',
    stockCode: '',
    status: undefined,
    errorCode: '',
  });
  const [backupLoading, setBackupLoading] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [backupFileList, setBackupFileList] = useState<UploadFile[]>([]);
  const [importConfirmOpen, setImportConfirmOpen] = useState(false);

  // System Testing Handlers
  const handleTestRedis = async () => {
    setTestRedisLoading(true);
    try {
      const res = await testingApi.testRedis();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestRedisLoading(false);
    }
  };

  const handleTestDb = async () => {
    setTestDbLoading(true);
    try {
      const res = await testingApi.testDb();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestDbLoading(false);
    }
  };

  const handleTestTuShare = async () => {
    setTestTushareLoading(true);
    try {
      const res = await testingApi.testTushare();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestTushareLoading(false);
    }
  };

  const handleTestTavily = async () => {
    setTestTavilyLoading(true);
    try {
      const res = await testingApi.testTavily();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestTavilyLoading(false);
    }
  };

  const handleTestPythonSandbox = async () => {
    setTestPythonSandboxLoading(true);
    try {
      const res = await testingApi.testPythonSandbox();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestPythonSandboxLoading(false);
    }
  };

  const handleTestSkills = async () => {
    setTestSkillsLoading(true);
    try {
      const res = await testingApi.testSkills();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestSkillsLoading(false);
    }
  };

  const handleTestDbSchema = async () => {
    setTestDbSchemaLoading(true);
    try {
      const res = await testingApi.testDbSchema();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestDbSchemaLoading(false);
    }
  };

  const handleTestQueryCalc = async () => {
    setTestQueryCalcLoading(true);
    try {
      const res = await testingApi.testQueryCalc();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestQueryCalcLoading(false);
    }
  };

  const handleTestPdfTool = async () => {
    const url = pdfToolTestUrl.trim();
    if (!url) {
      message.warning(t('settings.pdf_tool_url_required'));
      return;
    }

    setTestPdfToolLoading(true);
    try {
      const res = await testingApi.testPdfTool(url);
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestPdfToolLoading(false);
    }
  };

  const handleTestMemory = async () => {
    setTestMemoryLoading(true);
    try {
      const res = await testingApi.testMemory();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestMemoryLoading(false);
    }
  };

  const handleTestMemoryRead = async () => {
    setTestMemoryReadLoading(true);
    try {
      const res = await testingApi.testMemoryRead();
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestMemoryReadLoading(false);
    }
  };

  const buildMemoryPreviewParams = (
    page = 1,
    pageSize = 20,
    filters: MemoryPreviewFilters = memoryPreviewFilters,
  ): MemoryPreviewParams => {
    const parsedUserId = Number.parseInt(filters.userId.trim(), 10);
    return {
      user_id: Number.isInteger(parsedUserId) && parsedUserId > 0 ? parsedUserId : undefined,
      stock_code: filters.stockCode.trim() || undefined,
      status: filters.status || undefined,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    };
  };

  const fetchMemoryPreviewPage = async (
    page = 1,
    pageSize = 20,
    filters: MemoryPreviewFilters = memoryPreviewFilters,
  ) => {
    setTestMemoryPreviewLoading(true);
    try {
      const res = await testingApi.testMemoryPreview(buildMemoryPreviewParams(page, pageSize, filters));
      if (res.status === 'success') {
        const data = res.data as MemoryPreviewResultData | undefined;
        const items = Array.isArray(data?.items) ? data.items : [];
        setMemoryPreviewItems(items as MemoryPreviewItem[]);
        setMemoryPreviewTotal(typeof res.total === 'number' ? res.total : 0);
        setMemoryPreviewPage(page);
        setMemoryPreviewPageSize(pageSize);
        return res;
      }
      message.error(res.message || t('testing.memory_preview-failed'));
      return res;
    } catch (err) {
      message.error(getApiErrorMessage(err, t('testing.memory_preview-failed')));
      throw err;
    } finally {
      setTestMemoryPreviewLoading(false);
    }
  };

  const handleApplyMemoryPreviewFilters = async () => {
    try {
      await fetchMemoryPreviewPage(1, memoryPreviewPageSize);
    } catch {
      // Error is already surfaced by fetchMemoryPreviewPage.
    }
  };

  const handleResetMemoryPreviewFilters = async () => {
    const nextFilters: MemoryPreviewFilters = {
      userId: '',
      stockCode: '',
      status: undefined,
    };
    setMemoryPreviewFilters(nextFilters);
    try {
      await fetchMemoryPreviewPage(1, memoryPreviewPageSize, nextFilters);
    } catch {
      // Error is already surfaced by fetchMemoryPreviewPage.
    }
  };

  const buildMemoryRecallAuditParams = (
    page = 1,
    pageSize = 20,
    filters: MemoryRecallAuditFilters = memoryRecallAuditFilters,
  ): MemoryRecallAuditPreviewParams => {
    const parsedUserId = Number.parseInt(filters.userId.trim(), 10);
    return {
      user_id: Number.isInteger(parsedUserId) && parsedUserId > 0 ? parsedUserId : undefined,
      stock_code: filters.stockCode.trim() || undefined,
      status: filters.status || undefined,
      error_code: filters.errorCode.trim() || undefined,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    };
  };

  const fetchMemoryRecallAuditPage = async (
    page = 1,
    pageSize = 20,
    filters: MemoryRecallAuditFilters = memoryRecallAuditFilters,
  ) => {
    setTestMemoryRecallAuditLoading(true);
    try {
      const res = await testingApi.testMemoryRecallAudits(buildMemoryRecallAuditParams(page, pageSize, filters));
      if (res.status === 'success') {
        const data = res.data as MemoryRecallAuditResultData | undefined;
        const items = Array.isArray(data?.items) ? data.items : [];
        setMemoryRecallAuditItems(items as MemoryRecallAuditItem[]);
        setMemoryRecallAuditTotal(typeof res.total === 'number' ? res.total : 0);
        setMemoryRecallAuditPage(page);
        setMemoryRecallAuditPageSize(pageSize);
        return res;
      }
      message.error(res.message || t('testing.memory_recall_audits-failed'));
      return res;
    } catch (err) {
      message.error(getApiErrorMessage(err, t('testing.memory_recall_audits-failed')));
      throw err;
    } finally {
      setTestMemoryRecallAuditLoading(false);
    }
  };

  const handleApplyMemoryRecallAuditFilters = async () => {
    try {
      await fetchMemoryRecallAuditPage(1, memoryRecallAuditPageSize);
    } catch {
      // Error is already surfaced by fetchMemoryRecallAuditPage.
    }
  };

  const handleResetMemoryRecallAuditFilters = async () => {
    const nextFilters: MemoryRecallAuditFilters = {
      userId: '',
      stockCode: '',
      status: undefined,
      errorCode: '',
    };
    setMemoryRecallAuditFilters(nextFilters);
    try {
      await fetchMemoryRecallAuditPage(1, memoryRecallAuditPageSize, nextFilters);
    } catch {
      // Error is already surfaced by fetchMemoryRecallAuditPage.
    }
  };

  const memoryRecallAuditSuccessStats = useMemo(() => {
    const total = memoryRecallAuditItems.length;
    const success = memoryRecallAuditItems.filter((item) => item.status === 'ok').length;
    return {
      success,
      total,
      rate: total > 0 ? success / total : 0,
    };
  }, [memoryRecallAuditItems]);

  const compactPreviewText = (value?: string) =>
    String(value || '')
      .replace(/\r\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();

  const renderCompactPreview = (value?: string, maxHeight = 120) => (
    <div
      style={{
        ...promptTextBlockStyle,
        whiteSpace: 'pre-wrap',
        lineHeight: 1.35,
        maxHeight,
        margin: 0,
      }}
    >
      {compactPreviewText(value)}
    </div>
  );

  const fetchAiFunctionTaskResult = useCallback(async (taskId: string, tracker: AiFunctionTaskTracker) => {
    const activeTaskId = aiFunctionTaskIdByScenarioRef.current[tracker.scenario];
    if (activeTaskId !== taskId) {
      return;
    }

    try {
      const task = await tasksApi.getTask(taskId);
      if (!isAiFunctionTaskTerminal(task.status)) {
        return;
      }

      setAiTestOutputs((prev) => ({
        ...prev,
        [tracker.scenario]: formatApiOutput(buildAiFunctionTaskOutput(tracker, task)),
      }));

      if (!isAiFunctionTaskSuccessful(task.status)) {
        message.error(task.error_message || t('settings.test_failed'));
        return;
      }

      const result = task.result;
      if (isAiFunctionTestResult(result) && result.status === 'success') {
        message.success(`${result.message} (${result.elapsed_ms}ms)`);
        return;
      }

      if (isAiFunctionTestResult(result)) {
        message.error(result.message || t('settings.test_failed'));
        return;
      }

      message.success(`${t('common.task_completed')}: ${taskId}`);
    } catch (error) {
      setAiTestOutputs((prev) => ({
        ...prev,
        [tracker.scenario]: formatApiOutput({ error: getApiErrorMessage(error, t('settings.test_failed')) }),
      }));
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      const latestTaskId = aiFunctionTaskIdByScenarioRef.current[tracker.scenario];
      if (latestTaskId === taskId) {
        delete aiFunctionTaskIdByScenarioRef.current[tracker.scenario];
        delete aiFunctionTaskTrackersRef.current[taskId];
        setAiTestLoading((prev) => ({ ...prev, [tracker.scenario]: false }));
      }
    }
  }, [message, t]);

  useWebSocketSubscription('task_completed', (msg: WebSocketMessage) => {
      const data = (msg as TaskCompletedMessage).data;
      const taskId = data?.task_id;
      if (!taskId || !isAiFunctionTaskTerminal(data?.status)) {
        return;
      }

      const tracker = aiFunctionTaskTrackersRef.current[taskId];
      if (!tracker) {
        return;
      }

      void fetchAiFunctionTaskResult(taskId, tracker);
  });

  const memoryPreviewColumns: ColumnsType<MemoryPreviewItem> = [
    {
      title: t('settings.memory_column_memory_id'),
      dataIndex: 'memory_id',
      key: 'memory_id',
      width: 220,
      ellipsis: true,
    },
    {
      title: t('settings.memory_column_session'),
      dataIndex: 'session',
      key: 'session',
      width: 260,
      ellipsis: true,
      render: (value?: string) => value || '-',
    },
    {
      title: t('settings.memory_column_content'),
      dataIndex: 'content',
      key: 'content',
      width: 720,
      render: (value: string) => renderCompactPreview(value, 132),
    },
    {
      title: t('settings.memory_column_occurred_at'),
      dataIndex: 'occurred_at',
      key: 'occurred_at',
      width: 180,
      ellipsis: true,
    },
    {
      title: t('settings.memory_column_created_at'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      ellipsis: true,
    },
  ];

  const memoryRecallAuditColumns: ColumnsType<MemoryRecallAuditItem> = [
    {
      title: t('settings.memory_column_created_at'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (value: string) => value || '',
    },
    {
      title: t('settings.memory_column_status'),
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (value: string) => <Tag color={value === 'ok' ? 'green' : value === 'not_ready' ? 'gold' : 'red'}>{value}</Tag>,
    },
    {
      title: t('settings.memory_column_error_code'),
      dataIndex: 'error_code',
      key: 'error_code',
      width: 180,
      ellipsis: true,
      render: (value?: string | null) => value || '-',
    },
    {
      title: t('settings.memory_column_session'),
      dataIndex: 'session',
      key: 'session',
      width: 260,
      ellipsis: true,
    },
    {
      title: t('settings.memory_column_query'),
      dataIndex: 'query',
      key: 'query',
      width: 380,
      render: (value: string) => renderCompactPreview(value, 84),
    },
    {
      title: t('settings.memory_column_answer'),
      dataIndex: 'final_answer',
      key: 'final_answer',
      width: 520,
      render: (value: string) => renderCompactPreview(value, 112),
    },
    {
      title: t('settings.memory_column_citations'),
      dataIndex: 'selected_memory_ids',
      key: 'selected_memory_ids',
      width: 95,
      render: (value: string[]) => (Array.isArray(value) ? value.length : 0),
    },
    {
      title: t('settings.memory_column_edges'),
      dataIndex: 'retrieved',
      key: 'retrieved',
      width: 80,
      render: (value: unknown[]) => (Array.isArray(value) ? value.length : 0),
    },
    {
      title: t('settings.memory_column_answerability'),
      dataIndex: 'answerability',
      key: 'answerability',
      width: 260,
      render: (value?: string) => (value ? <Tag>{value}</Tag> : '-'),
    },
    {
      title: t('settings.memory_column_answerability_reason'),
      key: 'answerability_reason',
      width: 360,
      render: (_, record) => renderCompactPreview(record.answerability_reason || '', 48),
    },
    {
      title: t('settings.memory_column_audit_id'),
      dataIndex: 'audit_id',
      key: 'audit_id',
      width: 220,
      ellipsis: true,
    },
  ];

  const handleTestDocstrings = async () => {
    setTestDocstringLoading(true);
    try {
      const res = await testingApi.testDocstrings();
      if (res.status === 'success') {
        setToolDocstrings(res.items || []);
        setDocstringModalOpen(true);
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setTestDocstringLoading(false);
    }
  };

  const loadNewsTestTools = useCallback(async () => {
    setNewsTestToolsLoading(true);
    try {
      const res = await testingApi.getTestingCatalog();
      setNewsTestTools(res.news_tools || []);
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setNewsTestToolsLoading(false);
    }
  }, [message, t]);

  const loadNewsPlugins = useCallback(async () => {
    setNewsPluginsLoading(true);
    try {
      const res = await newsPluginsApi.list();
      if (res.status === 'success') {
        const items = res.items || [];
        setNewsPlugins(items);
        const itemKeys = new Set(items.map((plugin) => plugin.plugin_id || plugin.module_name));
        setSelectedNewsPluginKeys((prev) => prev.filter((key) => itemKeys.has(key)));
      } else {
        message.error(res.message || t('settings.news_plugin_load_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setNewsPluginsLoading(false);
    }
  }, [message, t]);

  const loadMcpServers = useCallback(async () => {
    setMcpServersLoading(true);
    try {
      const res = await mcpApi.list();
      if (res.status === 'success') {
        setMcpServers(res.items || []);
      } else {
        message.error(res.message || t('settings.mcp.load_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpServersLoading(false);
    }
  }, [message, t]);

  const loadMcpPrompt = useCallback(async () => {
    setMcpPromptLoading(true);
    try {
      const res = await mcpApi.getPrompt();
      if (res.status === 'success') {
        setMcpPrompt(res.prompt || '');
      } else {
        message.error(res.message || t('settings.mcp.prompt_load_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpPromptLoading(false);
    }
  }, [message, t]);

  const openMcpCreateModal = () => {
    setEditingMcpServer(null);
    setMcpPreviewTools([]);
    mcpForm.setFieldsValue({ name: '', enabled: false, url: '', token: '', allowed_tools: [] });
    setMcpModalOpen(true);
  };

  const openMcpEditModal = (server: MCPServerItem) => {
    setEditingMcpServer(server);
    setMcpPreviewTools((server.allowed_tools || []).map((tool) => ({
      server: server.name,
      name: tool,
      langchain_name: tool,
      description: '',
      input_schema: {},
    })));
    mcpForm.setFieldsValue({ ...server, token: '', allowed_tools: server.allowed_tools || [] });
    setMcpModalOpen(true);
  };

  const handlePreviewMcpTools = async () => {
    const values = await mcpForm.validateFields(['name', 'url', 'token']);
    setMcpPreviewLoading(true);
    try {
      const res = await mcpApi.previewTools({
        name: values.name.trim() || 'preview',
        url: values.url.trim(),
        token: values.token?.trim() || '',
      });
      if (res.status === 'success') {
        const items = res.items || [];
        setMcpPreviewTools(items);
        mcpForm.setFieldValue(
          'allowed_tools',
          (mcpForm.getFieldValue('allowed_tools') || []).filter((tool: string) =>
            items.some((item) => item.name === tool),
          ),
        );
        message.success(t('settings.mcp.test_success', { count: items.length }));
      } else {
        message.error(res.message || t('settings.mcp.tools_load_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpPreviewLoading(false);
    }
  };

  const handleSaveMcpServer = async () => {
    const values = await mcpForm.validateFields();
    setMcpSaving(true);
    try {
      const payload = {
        name: values.name.trim(),
        enabled: Boolean(values.enabled),
        url: values.url.trim(),
        token: values.token?.trim() || '',
        allowed_tools: values.allowed_tools || [],
      };
      const res = editingMcpServer
        ? await mcpApi.update(editingMcpServer.name, {
          enabled: payload.enabled,
          url: payload.url,
          token: payload.token,
          allowed_tools: payload.allowed_tools,
        })
        : await mcpApi.create(payload);
      if (res.status === 'success') {
        message.success(t('settings.mcp.saved'));
        setMcpModalOpen(false);
        setEditingMcpServer(null);
        await Promise.all([loadMcpServers(), loadMcpPrompt()]);
      } else {
        message.error(res.message || t('settings.mcp.save_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpSaving(false);
    }
  };

  const setMcpServerLoading = (name: string, loading: boolean) => {
    setMcpActionLoading((prev) => ({ ...prev, [name]: loading }));
  };

  const handleToggleMcpServer = async (server: MCPServerItem, enabled: boolean) => {
    setMcpServerLoading(server.name, true);
    try {
      const res = await mcpApi.update(server.name, { enabled });
      if (res.status === 'success') {
        message.success(t('settings.mcp.saved'));
        await Promise.all([loadMcpServers(), loadMcpPrompt()]);
      } else {
        message.error(res.message || t('settings.mcp.save_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpServerLoading(server.name, false);
    }
  };

  const handleDeleteMcpServer = async (server: MCPServerItem) => {
    setMcpServerLoading(server.name, true);
    try {
      const res = await mcpApi.delete(server.name);
      if (res.status === 'success') {
        message.success(t('settings.mcp.deleted'));
        await Promise.all([loadMcpServers(), loadMcpPrompt()]);
      } else {
        message.error(res.message || t('settings.mcp.delete_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpServerLoading(server.name, false);
    }
  };

  const handleTestMcpServer = async (server: MCPServerItem) => {
    setMcpServerLoading(server.name, true);
    try {
      const res = await mcpApi.test(server.name);
      if (res.status === 'success') {
        message.success(t('settings.mcp.test_success', { count: res.tool_count ?? res.count ?? 0 }));
      } else {
        message.error(res.message || t('settings.mcp.test_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpServerLoading(server.name, false);
    }
  };

  const handleOpenMcpTools = async (server: MCPServerItem) => {
    setMcpToolsServerName(server.name);
    setMcpTools([]);
    setMcpToolsModalOpen(true);
    setMcpToolsLoading(true);
    try {
      const res = await mcpApi.listTools(server.name);
      if (res.status === 'success') {
        setMcpTools(res.items || []);
      } else {
        message.error(res.message || t('settings.mcp.tools_load_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setMcpToolsLoading(false);
    }
  };

  const getNewsPluginUploadFiles = (): RcFile[] => {
    return newsPluginFileList
      .map((item) => item.originFileObj)
      .filter((file): file is RcFile => Boolean(file));
  };

  const handleUploadNewsPlugin = async () => {
    const files = getNewsPluginUploadFiles();
    if (files.length === 0) {
      message.warning(t('settings.news_plugin_file_required'));
      return;
    }

    setNewsPluginSaving(true);
    try {
      const res = await newsPluginsApi.upload(files);

      if (isNewsPluginBatchUploadResult(res)) {
        if (res.status === 'success') {
          message.success(t('settings.news_plugin_batch_upload_success', { count: res.success_count ?? files.length }));
          setNewsPluginModalOpen(false);
          setNewsPluginFileList([]);
          await Promise.all([loadNewsPlugins(), loadNewsTestTools()]);
          return;
        }

        if (res.status === 'partial_success') {
          message.success(t('settings.news_plugin_batch_upload_partial_success', { count: res.success_count ?? 0 }));
          setNewsPluginModalOpen(false);
          setNewsPluginFileList([]);
          await Promise.all([loadNewsPlugins(), loadNewsTestTools()]);
          showBatchNewsPluginUploadError(res.items || [], res.success_count ?? 0, res.failed_count ?? 0);
          return;
        }

        showBatchNewsPluginUploadError(
          res.items || [],
          res.success_count ?? 0,
          res.failed_count ?? (res.items || []).length,
        );
      } else if (res.status === 'success') {
        message.success(res.message || t('settings.news_plugin_saved'));
        setNewsPluginModalOpen(false);
        setNewsPluginFileList([]);
        await Promise.all([loadNewsPlugins(), loadNewsTestTools()]);
      } else {
        showDependencyInstallError(
          t('settings.news_plugin_save_failed'),
          res.message || t('settings.news_plugin_save_failed'),
          res.dependencies,
        );
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setNewsPluginSaving(false);
    }
  };

  const handleDeleteNewsPlugin = async (plugin: NewsPluginItem) => {
    const pluginKey = plugin.plugin_id || plugin.module_name;
    setNewsPluginDeleting((prev) => ({ ...prev, [pluginKey]: true }));
    try {
      const res = await newsPluginsApi.delete(pluginKey);
      if (res.status === 'success') {
        message.success(res.message || t('settings.news_plugin_deleted'));
        await Promise.all([loadNewsPlugins(), loadNewsTestTools()]);
      } else {
        message.error(res.message || t('settings.news_plugin_delete_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setNewsPluginDeleting((prev) => ({ ...prev, [pluginKey]: false }));
    }
  };

  const handleBatchDeleteNewsPlugins = async () => {
    const selectedPlugins = newsPlugins.filter((plugin) => {
      const pluginKey = plugin.plugin_id || plugin.module_name;
      return plugin.can_delete && selectedNewsPluginKeys.includes(pluginKey);
    });
    if (selectedPlugins.length === 0) {
      return;
    }

    modal.confirm({
      title: t('settings.batch_delete_news_plugins_confirm_title'),
      content: t('settings.batch_delete_news_plugins_confirm_content', { count: selectedPlugins.length }),
      okButtonProps: { danger: true },
      onOk: async () => {
        setNewsPluginBatchDeleting(true);
        const failedPlugins: string[] = [];
        let successCount = 0;

        try {
          for (const plugin of selectedPlugins) {
            const pluginKey = plugin.plugin_id || plugin.module_name;
            setNewsPluginDeleting((prev) => ({ ...prev, [pluginKey]: true }));
            try {
              const res = await newsPluginsApi.delete(pluginKey);
              if (res.status === 'success') {
                successCount += 1;
              } else {
                failedPlugins.push(plugin.name || pluginKey);
              }
            } catch {
              failedPlugins.push(plugin.name || pluginKey);
            } finally {
              setNewsPluginDeleting((prev) => ({ ...prev, [pluginKey]: false }));
            }
          }

          setSelectedNewsPluginKeys([]);
          await Promise.all([loadNewsPlugins(), loadNewsTestTools()]);

          if (failedPlugins.length === 0) {
            message.success(t('settings.batch_delete_news_plugins_success', { count: successCount }));
            return;
          }

          modal.error({
            title: t('settings.batch_delete_news_plugins_partial_fail_title'),
            content: (
              <div className="text-sm" style={diagnosticPanelStyle}>
                {[
                  t('settings.batch_delete_news_plugins_partial_fail_summary', {
                    success: successCount,
                    failed: failedPlugins.length,
                  }),
                  failedPlugins.join('\n'),
                ].join('\n\n')}
              </div>
            ),
          });
        } finally {
          setNewsPluginBatchDeleting(false);
        }
      },
    });
  };

  const toggleSelectAllNewsPlugins = (checked: boolean) => {
    setSelectedNewsPluginKeys(
      checked
        ? newsPlugins
          .filter((plugin) => plugin.can_delete)
          .map((plugin) => plugin.plugin_id || plugin.module_name)
        : [],
    );
  };

  const loadSkills = useCallback(async () => {
    setSkillsLoading(true);
    try {
      const res = await skillsApi.list();
      if (res.status === 'success') {
        setSkills(res.items || []);
      } else {
        message.error(res.message || t('settings.skill_load_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setSkillsLoading(false);
    }
  }, [message, t]);

  const loadSkillPrompt = useCallback(async () => {
    setSkillPromptLoading(true);
    try {
      const res = await skillsApi.getPrompt();
      if (res.status === 'success') {
        setSkillPrompt(res.prompt || '');
      } else {
        message.error(res.message || t('settings.skill_prompt_load_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setSkillPromptLoading(false);
    }
  }, [message, t]);

  const getSkillUploadFiles = (): RcFile[] => {
    return skillFileList
      .map((item) => item.originFileObj)
      .filter((file): file is RcFile => Boolean(file));
  };

  const handleUploadSkill = async () => {
    const files = getSkillUploadFiles();
    if (files.length === 0) {
      message.warning(t('settings.skill_folder_required'));
      return;
    }

    const hasSkillJson = files.some((file) => {
      const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      const parts = relativePath.split('/').filter(Boolean);
      return parts.length === 2 && parts[1] === 'skill.json';
    });
    if (!hasSkillJson) {
      message.warning(t('settings.skill_json_required'));
      return;
    }

    setSkillSaving(true);
    try {
      const res = await skillsApi.upload(files);
      if (res.status === 'success') {
        message.success(res.message || t('settings.skill_saved'));
        setSkillModalOpen(false);
        setSkillFileList([]);
        await Promise.all([loadSkills(), loadSkillPrompt()]);
      } else {
        showDependencyInstallError(
          t('settings.skill_save_failed'),
          res.message || t('settings.skill_save_failed'),
          res.dependencies,
        );
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setSkillSaving(false);
    }
  };

  const handleDeleteSkill = async (skill: SkillItem) => {
    setSkillDeleting((prev) => ({ ...prev, [skill.skill_id]: true }));
    try {
      const res = await skillsApi.delete(skill.skill_id);
      if (res.status === 'success') {
        message.success(res.message || t('settings.skill_deleted'));
        await Promise.all([loadSkills(), loadSkillPrompt()]);
      } else {
        message.error(res.message || t('settings.skill_delete_failed'));
      }
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setSkillDeleting((prev) => ({ ...prev, [skill.skill_id]: false }));
    }
  };

  const showDependencyInstallError = (
    title: string,
    description: string,
    dependencies?: {
      command?: string[];
      exit_code?: number | null;
      stderr?: string;
      stdout?: string;
    },
  ) => {
    if (!dependencies) {
      message.error(description);
      return;
    }

    const detailLines = [
      description,
      dependencies.exit_code !== undefined && dependencies.exit_code !== null
        ? `exit_code: ${dependencies.exit_code}`
        : '',
      dependencies.command?.length ? `command: ${dependencies.command.join(' ')}` : '',
      dependencies.stderr ? `stderr:\n${dependencies.stderr}` : '',
      !dependencies.stderr && dependencies.stdout ? `stdout:\n${dependencies.stdout}` : '',
    ].filter(Boolean);

    modal.error({
      title,
      width: 880,
      content: (
        <div className="text-sm" style={diagnosticPanelStyle}>
          {detailLines.join('\n\n')}
        </div>
      ),
    });
  };

  const showBatchNewsPluginUploadError = (
    items: Array<{
      status: string;
      filename?: string;
      module_name?: string;
      message: string;
      dependencies?: {
        exit_code?: number | null;
        stderr?: string;
        stdout?: string;
      };
    }>,
    successCount: number,
    failedCount: number,
  ) => {
    const failedItems = items.filter((item) => item.status !== 'success');
    const detailLines = [
      t('settings.news_plugin_batch_upload_partial_fail_summary', {
        success: successCount,
        failed: failedCount,
      }),
      ...failedItems.map((item) => {
        const lines = [
          item.filename || item.module_name || t('settings.news_plugin_unknown_file'),
          item.message,
        ];
        if (item.dependencies?.exit_code !== undefined && item.dependencies?.exit_code !== null) {
          lines.push(`exit_code: ${item.dependencies.exit_code}`);
        }
        if (item.dependencies?.stderr) {
          lines.push(`stderr:\n${item.dependencies.stderr}`);
        } else if (item.dependencies?.stdout) {
          lines.push(`stdout:\n${item.dependencies.stdout}`);
        }
        return lines.join('\n');
      }),
    ];

    modal.error({
      title: t('settings.news_plugin_batch_upload_partial_fail_title'),
      width: 880,
      content: (
        <div className="text-sm" style={diagnosticPanelStyle}>
          {detailLines.join('\n\n')}
        </div>
      ),
    });
  };

  const handleDynamicNewsTest = async (tool: NewsTestingTool) => {
    const keyword = dynamicNewsKeywords[tool.name]?.trim();
    setDynamicNewsLoading(prev => ({ ...prev, [tool.name]: true }));
    try {
      const res = await testingApi.runNewsTool(tool.test_route, keyword);
      setNewsTestResult({ ...res });
      if (res.status === 'success') {
        message.success(`${res.message} (${res.elapsed_ms}ms)`);
      } else {
        message.error(res.message);
      }
    } catch (error) {
      const responseData = getApiErrorResponseData(error);
      setNewsTestResult(
        responseData && typeof responseData === 'object'
          ? responseData as Record<string, unknown>
          : { error: getApiErrorMessage(error, t('settings.test_failed')) }
      );
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setDynamicNewsLoading(prev => ({ ...prev, [tool.name]: false }));
    }
  };

  const handleTestAll = async () => {
    await Promise.all([
      handleTestRedis(),
      handleTestDb(),
      handleTestTuShare(),
      handleTestTavily(),
      handleTestPythonSandbox(),
      handleTestSkills(),
      handleTestDbSchema(),
      handleTestQueryCalc(),
      handleTestMemory(),
      handleTestMemoryRead(),
      ...newsTestTools.map((tool) => handleDynamicNewsTest(tool)),
    ]);
  };

  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const data = await promptApi.getUsageStats();
      setStats(data);
    } catch (error) {
      console.error('Failed to load stats:', error);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  const buildRoleRows = (
    breakdown?: Record<string, UsageBreakdownEntry | undefined> | null,
    counts?: Record<string, number> | null,
  ): UsageRoleRow[] => {
    const roles = new Set<string>([
      ...Object.keys(breakdown || {}),
      ...Object.keys(counts || {}),
    ]);

    return Array.from(roles)
      .map((role) => {
        const payload = breakdown?.[role];
        return {
          role,
          calls: payload?.calls ?? counts?.[role] ?? 0,
          inputTokens: payload?.input_tokens || 0,
          cachedTokens: payload?.cached_tokens || 0,
          cacheMissTokens: payload?.cache_miss_tokens || 0,
          cacheHitRate: payload?.cache_hit_rate || 0,
        };
      })
      .sort((left, right) => (
        right.inputTokens - left.inputTokens
        || right.calls - left.calls
        || left.role.localeCompare(right.role)
      ));
  };

  const renderRoleStatsTable = (rows: UsageRoleRow[]) => {
    const columns: ColumnsType<UsageRoleRow> = [
      {
        title: t('settings.usage_role'),
        dataIndex: 'role',
        key: 'role',
      },
      {
        title: t('settings.total_calls'),
        dataIndex: 'calls',
        key: 'calls',
        align: 'right',
      },
      {
        title: t('settings.input_tokens'),
        dataIndex: 'inputTokens',
        key: 'inputTokens',
        align: 'right',
      },
      {
        title: t('settings.cached_tokens'),
        dataIndex: 'cachedTokens',
        key: 'cachedTokens',
        align: 'right',
      },
      {
        title: t('settings.cache_miss_tokens'),
        dataIndex: 'cacheMissTokens',
        key: 'cacheMissTokens',
        align: 'right',
      },
      {
        title: t('settings.cache_hit_rate'),
        dataIndex: 'cacheHitRate',
        key: 'cacheHitRate',
        align: 'right',
        render: (value: number) => formatPercent(value),
      },
    ];

    return (
      <Table<UsageRoleRow>
        size="small"
        rowKey="role"
        dataSource={rows}
        columns={columns}
        pagination={false}
        locale={{ emptyText: t('common.no_data') }}
        scroll={{ x: 720 }}
      />
    );
  };

  const renderCacheLanePanel = (title: string, payload?: UsageBreakdownEntry | null) => {
    const hitRate = payload?.cache_hit_rate || 0;
    const hasUsage = Boolean(payload?.calls || payload?.input_tokens || payload?.cached_tokens);
    const statusOk = hasUsage && hitRate >= 0.8;
    const statusColor = !hasUsage ? 'default' : statusOk ? 'green' : 'orange';
    const statusText = !hasUsage
      ? t('common.no_data')
      : statusOk
        ? t('settings.cache_hit_status_ok')
        : t('settings.cache_hit_status_low');

    return (
      <Col xs={24} md={12}>
        <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 16, height: '100%' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} align="center">
            <span style={{ fontWeight: 600 }}>{title}</span>
            <Tag color={statusColor}>{statusText}</Tag>
          </Space>
          <Statistic
            title={t('settings.cache_hit_rate')}
            value={formatPercent(hitRate)}
            valueStyle={{ fontSize: 28 }}
          />
          <Row gutter={[12, 12]} style={{ marginTop: 16 }}>
            <Col span={12}>
              <Statistic title={t('settings.total_calls')} value={payload?.calls || 0} valueStyle={{ fontSize: 16 }} />
            </Col>
            <Col span={12}>
              <Statistic
                title={t('settings.input_tokens')}
                value={payload?.input_tokens || 0}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
            <Col span={12}>
              <Statistic
                title={t('settings.cached_tokens')}
                value={payload?.cached_tokens || 0}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
            <Col span={12}>
              <Statistic
                title={t('settings.cache_miss_tokens')}
                value={payload?.cache_miss_tokens || 0}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
          </Row>
        </div>
      </Col>
    );
  };

  const getBusinessCacheUsage = (
    backendStats: PromptStats['backend'],
    workflow: string,
    callKind: string,
  ) => backendStats?.by_workflow_call_kind?.[`${workflow}/${callKind}`];

  const renderMemoryStatsPanel = (memoryStats: PromptStats['memory']) => (
    <Col span={24}>
      <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>{t('settings.memory_system_stats')}</div>
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.memory_llm_runs')}
              value={memoryStats?.llm_runs || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.total_calls')}
              value={memoryStats?.total_calls || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.input_tokens')}
              value={memoryStats?.input_tokens || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.cache_miss_tokens')}
              value={memoryStats?.cache_miss_tokens || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.cache_hit_rate')}
              value={formatPercent(memoryStats?.cache_hit_rate)}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
        </Row>
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{t('settings.role_granularity_stats')}</div>
          {renderRoleStatsTable(buildRoleRows(memoryStats?.by_operation))}
        </div>
      </div>
    </Col>
  );

  const renderMainSystemStatsPanel = (backendStats: PromptStats['backend']) => (
    <Col span={24}>
      <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>{t('settings.main_system_stats')}</div>
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.total_calls')}
              value={backendStats?.total_calls || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.total_tokens')}
              value={backendStats?.total_tokens || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.input_tokens')}
              value={backendStats?.input_tokens || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.reasoning_tokens')}
              value={backendStats?.reasoning_tokens || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.cached_tokens')}
              value={backendStats?.cached_tokens || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.cache_miss_tokens')}
              value={backendStats?.cache_miss_tokens || 0}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
          <Col xs={12} md={6}>
            <Statistic
              title={t('settings.cache_hit_rate')}
              value={formatPercent(backendStats?.cache_hit_rate)}
              valueStyle={{ fontSize: 16 }}
            />
          </Col>
        </Row>
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{t('settings.role_granularity_stats')}</div>
          {renderRoleStatsTable(buildRoleRows(backendStats?.by_role_detail, backendStats?.by_role))}
        </div>
      </div>
    </Col>
  );

  const handleClearUsageStats = async () => {
    setClearStatsLoading(true);
    try {
      await promptApi.clearUsageStats();
      message.success(t('settings.clear_usage_stats_success'));
      await loadStats();
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.clear_usage_stats_failed')));
    } finally {
      setClearStatsLoading(false);
    }
  };

  const loadTushareConfig = useCallback(async () => {
    try {
      const config = await sourcesApi.getTushareConfig();
      tushareForm.setFieldsValue(config);
    } catch {
      // Ignore error if config not found
    }
  }, [tushareForm]);

  const loadPrompts = useCallback(async () => {
    try {
      const data = await promptApi.getAllPrompts();
      setPrompts(data);
    } catch {
      message.error(t('common.error'));
    }
  }, [message, t]);

  useEffect(() => {
    loadTushareConfig();
    loadPrompts();
    loadStats();
    loadNewsPlugins();
    loadNewsTestTools();
    loadMcpServers();
    loadMcpPrompt();
    loadSkills();
    loadSkillPrompt();
  }, [
    loadMcpPrompt,
    loadMcpServers,
    loadNewsPlugins,
    loadNewsTestTools,
    loadPrompts,
    loadSkillPrompt,
    loadSkills,
    loadStats,
    loadTushareConfig,
  ]);

  const handleSaveTushare = async (values: Record<string, unknown>) => {
    setTushareLoading(true);
    try {
      await sourcesApi.updateTushareConfig(values);
      message.success(t('common.success'));
    } catch {
      message.error(t('common.error'));
    } finally {
      setTushareLoading(false);
    }
  };

  const handleAiFunctionTest = async (scenario: AiFunctionScenario) => {
    const userInput = aiTestInputs[scenario].trim();
    if (!userInput) {
      message.error(t('settings.ai_test_input_required'));
      return;
    }
    setAiTestLoading((prev) => ({ ...prev, [scenario]: true }));
    try {
      const res = await testingApi.runAiFunctionTest({
        scenario,
        user_input: userInput,
      });
      aiFunctionTaskTrackersRef.current[res.task_id] = { scenario, userInput };
      aiFunctionTaskIdByScenarioRef.current[scenario] = res.task_id;
      setAiTestOutputs((prev) => ({
        ...prev,
        [scenario]: formatApiOutput({
          request: {
            scenario,
            user_input: userInput,
          },
          task: {
            task_id: res.task_id,
            task_name: res.task_name,
            status: res.status,
            scenario_label: res.scenario_label,
          },
          response: res,
        }),
      }));
      message.success(`${t('common.task_submitted')}: ${res.task_id}`);
    } catch (error) {
      const responseData = getApiErrorResponseData(error);
      setAiTestOutputs((prev) => ({
        ...prev,
        [scenario]: formatApiOutput(responseData || { error: getApiErrorMessage(error, t('common.error')) }),
      }));
      message.error(t('common.error'));
      setAiTestLoading((prev) => ({ ...prev, [scenario]: false }));
    }
  };

  const handleDownloadDatabaseBackup = async () => {
    setBackupLoading(true);
    try {
      const anchor = document.createElement('a');
      anchor.href = '/api/v1/sources/database/backup';
      anchor.rel = 'noopener';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      message.success(t('settings.database_backup_success'));
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setBackupLoading(false);
    }
  };

  const doImportDatabaseBackup = async () => {
    const file = backupFileList[0]?.originFileObj;
    if (!file) {
      message.warning(t('settings.select_backup_file_first'));
      return;
    }

    setImportLoading(true);
    try {
      const res = await sourcesApi.importDatabaseBackup(file);
      message.success(res.message || t('settings.database_import_success'));
      setBackupFileList([]);
      setImportConfirmOpen(false);
    } catch (error) {
      message.error(getApiErrorMessage(error, t('settings.test_failed')));
    } finally {
      setImportLoading(false);
    }
  };

  const handleImportDatabaseBackup = () => {
    setImportConfirmOpen(true);
  };

  const deletableNewsPluginKeys = newsPlugins
    .filter((plugin) => plugin.can_delete)
    .map((plugin) => plugin.plugin_id || plugin.module_name);
  const allDeletableNewsPluginsSelected =
    deletableNewsPluginKeys.length > 0 && selectedNewsPluginKeys.length === deletableNewsPluginKeys.length;
  const someDeletableNewsPluginsSelected =
    selectedNewsPluginKeys.length > 0 && selectedNewsPluginKeys.length < deletableNewsPluginKeys.length;

  const mcpServerColumns: ColumnsType<MCPServerItem> = [
    {
      title: t('settings.mcp.name'),
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (value: string) => <Tag>{value}</Tag>,
    },
    {
      title: t('settings.mcp.enabled'),
      dataIndex: 'enabled',
      key: 'enabled',
      width: 120,
      render: (enabled: boolean, record) => (
        <Switch
          checked={enabled}
          loading={!!mcpActionLoading[record.name]}
          onChange={(checked) => void handleToggleMcpServer(record, checked)}
        />
      ),
    },
    {
      title: t('settings.mcp.url'),
      dataIndex: 'url',
      key: 'url',
      ellipsis: true,
      render: (value: string) => <Tooltip title={value}>{value}</Tooltip>,
    },
    {
      title: t('settings.mcp.allowed_tools'),
      dataIndex: 'allowed_tools',
      key: 'allowed_tools',
      width: 220,
      render: (value?: string[]) => (
        <Space wrap size={[4, 4]}>
          {(value || []).map((tool) => <Tag key={tool}>{tool}</Tag>)}
        </Space>
      ),
    },
    {
      title: t('settings.mcp.actions'),
      key: 'actions',
      width: 340,
      render: (_, record) => (
        <Space wrap>
          <Button size="small" onClick={() => openMcpEditModal(record)}>
            {t('settings.edit')}
          </Button>
          <Button size="small" loading={!!mcpActionLoading[record.name]} onClick={() => void handleTestMcpServer(record)}>
            {t('settings.mcp.test')}
          </Button>
          <Button size="small" onClick={() => void handleOpenMcpTools(record)}>
            {t('settings.mcp.view_tools')}
          </Button>
          <Popconfirm
            title={t('settings.mcp.delete_confirm')}
            okText={t('common.confirm')}
            cancelText={t('common.cancel')}
            onConfirm={() => void handleDeleteMcpServer(record)}
          >
            <Button danger size="small" loading={!!mcpActionLoading[record.name]}>
              {t('settings.mcp.delete_server')}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const mcpToolColumns: ColumnsType<MCPToolItem> = [
    {
      title: t('settings.mcp.tool_name'),
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (value: string) => <Tag>{value}</Tag>,
    },
    {
      title: t('settings.mcp.langchain_name'),
      dataIndex: 'langchain_name',
      key: 'langchain_name',
      width: 220,
      ellipsis: true,
    },
    {
      title: t('settings.mcp.tool_description'),
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h1 className="text-2xl font-bold text-white mb-6">{t('settings.title')}</h1>

      <Tabs activeKey={activeSettingsTab} onChange={handleSettingsTabChange} items={[
        {
          key: 'datasources',
          label: t('settings.data_sources'),
          children: (
            <div className="flex flex-col gap-4">
              <Card title={t('settings.tushare_config')}>
                <Form
                  form={tushareForm}
                  layout="vertical"
                  onFinish={handleSaveTushare}
                >
                  <Form.Item label={t('settings.api_url')} name="api_url">
                    <Input placeholder="https://api.tushare.pro" />
                  </Form.Item>
                  <Form.Item label={t('settings.api_token')} name="token">
                    <Input.Password placeholder={t('settings.enter_tushare_token')} />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" loading={tushareLoading}>
                      {t('settings.save_config')}
                    </Button>
                  </Form.Item>
                </Form>
              </Card>

              <Card title={t('settings.database_maintenance')}>
                <div className="flex flex-col gap-4">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <div className="text-white font-medium">{t('settings.database_backup')}: {t('settings.database_backup_desc')}</div>
                    </div>
                    <Button type="primary" onClick={handleDownloadDatabaseBackup} loading={backupLoading}>
                      {t('settings.download_backup')}
                    </Button>
                  </div>
                  <br />
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex-1">
                      <div className="text-white font-medium">{t('settings.database_import')}: {t('settings.database_import_desc')}</div>
                      <Upload
                        accept=".dump"
                        beforeUpload={() => false}
                        maxCount={1}
                        fileList={backupFileList}
                        onChange={({ fileList }) => setBackupFileList(fileList.slice(-1))}
                      >
                        <Button>{t('settings.select_backup_file')}</Button>
                      </Upload>
                    </div>
                    <Button
                      danger
                      type="primary"
                      onClick={handleImportDatabaseBackup}
                      loading={importLoading}
                      disabled={backupFileList.length === 0}
                    >
                      {t('settings.import_backup')}
                    </Button>
                  </div>
                </div>
              </Card>
            </div>
          )
        },
        {
          key: 'prompts',
          label: t('settings.ai_prompts'),
          children: (
            <Card title={t('settings.agent_prompts')}>
              <List
                dataSource={Object.entries(prompts)}
                renderItem={([role, content]) => (
                  <List.Item>
                    <div style={{ width: '100%', minWidth: 0 }}>
                      <div className="flex justify-between items-center mb-2">
                        <h3 className="text-lg font-bold capitalize">{role}</h3>
                      </div>
                      <pre
                        className="bg-gray-800 p-3 rounded text-sm text-gray-300 max-h-40"
                        style={promptTextBlockStyle}
                      >
                        {content}
                      </pre>
                    </div>
                  </List.Item>
                )}
              />
            </Card>
          )
        },
        {
          key: 'news_plugins',
          label: t('settings.news_plugin_management'),
          children: (
            <Card
              title={t('settings.news_plugin_management')}
              extra={
                <Space>
                  <Checkbox
                    checked={allDeletableNewsPluginsSelected}
                    indeterminate={someDeletableNewsPluginsSelected}
                    disabled={deletableNewsPluginKeys.length === 0}
                    onChange={(event) => toggleSelectAllNewsPlugins(event.target.checked)}
                  >
                    {t('settings.select_all_news_plugins')}
                  </Checkbox>
                  <Button
                    danger
                    onClick={handleBatchDeleteNewsPlugins}
                    disabled={selectedNewsPluginKeys.length === 0}
                    loading={newsPluginBatchDeleting}
                  >
                    {t('settings.batch_delete_news_plugins')}
                  </Button>
                  <Button type="primary" onClick={() => setNewsPluginModalOpen(true)}>
                    {t('settings.add_news_plugin')}
                  </Button>
                </Space>
              }
            >
              <List
                loading={newsPluginsLoading}
                dataSource={newsPlugins}
                locale={{ emptyText: t('settings.news_plugin_empty') }}
                renderItem={(plugin) => {
                  const pluginKey = plugin.plugin_id || plugin.module_name;
                  return (
                    <List.Item
                      actions={[
                        plugin.can_delete ? (
                          <Popconfirm
                            key="delete"
                            title={t('settings.delete_news_plugin_confirm')}
                            onConfirm={() => handleDeleteNewsPlugin(plugin)}
                          >
                            <Button danger size="small" loading={!!newsPluginDeleting[pluginKey]}>
                              {t('settings.delete_news_plugin')}
                            </Button>
                          </Popconfirm>
                        ) : (
                          <Tag key="builtin">{t('settings.news_plugin_builtin')}</Tag>
                        ),
                      ]}
                    >
                      <List.Item.Meta
                        title={
                          <Space wrap>
                            {plugin.can_delete ? (
                              <Checkbox
                                checked={selectedNewsPluginKeys.includes(pluginKey)}
                                onChange={(event) => {
                                  const checked = event.target.checked;
                                  setSelectedNewsPluginKeys((prev) => (
                                    checked
                                      ? [...prev, pluginKey]
                                      : prev.filter((item) => item !== pluginKey)
                                  ));
                                }}
                              />
                            ) : null}
                            <span>{plugin.name}</span>
                            <Tag>{plugin.plugin_id}</Tag>
                            <Tag>{plugin.module_name}</Tag>
                          </Space>
                        }
                        description={
                          <Space direction="vertical" size={4}>
                            <span>{plugin.tool_name}</span>
                            <span>{plugin.news_types.join(' / ')}</span>
                          </Space>
                        }
                      />
                    </List.Item>
                  );
                }}
              />
            </Card>
          )
        },
        {
          key: 'mcp',
          label: t('settings.mcp.management'),
          children: (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Card
                title={t('settings.mcp.management')}
                extra={
                  <Space>
                    <Button onClick={() => void loadMcpPrompt()} loading={mcpPromptLoading}>
                      {t('settings.mcp.refresh_prompt')}
                    </Button>
                    <Button onClick={() => void loadMcpServers()} loading={mcpServersLoading}>
                      {t('settings.mcp.refresh')}
                    </Button>
                    <Button type="primary" onClick={openMcpCreateModal}>
                      {t('settings.mcp.add_server')}
                    </Button>
                  </Space>
                }
              >
                <Table<MCPServerItem>
                  rowKey="name"
                  dataSource={mcpServers}
                  columns={mcpServerColumns}
                  loading={mcpServersLoading}
                  pagination={false}
                  locale={{ emptyText: t('settings.mcp.empty') }}
                  scroll={{ x: 860 }}
                  size="small"
                />
              </Card>
              <Card title={t('settings.mcp.prompt_title')}>
                <pre
                  className="bg-gray-800 p-3 rounded text-sm text-gray-300"
                  style={mcpToolInstructionBlockStyle}
                >
                  {mcpPromptLoading ? t('common.loading_stats') : (mcpPrompt || t('settings.mcp.prompt_empty'))}
                </pre>
              </Card>
            </Space>
          )
        },
        {
          key: 'skills',
          label: t('settings.skill_management'),
          children: (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Card
                title={t('settings.skill_management')}
                extra={
                  <Button type="primary" onClick={() => setSkillModalOpen(true)}>
                    {t('settings.add_skill')}
                  </Button>
                }
              >
                <List
                  loading={skillsLoading}
                  dataSource={skills}
                  locale={{ emptyText: t('settings.skill_empty') }}
                  renderItem={(skill) => (
                    <List.Item
                      actions={[
                        skill.can_delete && DISABLED_DELETE_SKILL_IDS.has(skill.skill_id) ? (
                          <Button key="delete" danger size="small" disabled>
                            {t('settings.delete_skill')}
                          </Button>
                        ) : skill.can_delete ? (
                          <Popconfirm
                            key="delete"
                            title={t('settings.delete_skill_confirm')}
                            onConfirm={() => handleDeleteSkill(skill)}
                          >
                            <Button danger size="small" loading={!!skillDeleting[skill.skill_id]}>
                              {t('settings.delete_skill')}
                            </Button>
                          </Popconfirm>
                        ) : (
                          <Tag key="builtin">{t('settings.skill_builtin')}</Tag>
                        ),
                      ]}
                    >
                      <List.Item.Meta
                        title={
                          <Space wrap>
                            <span>{skill.name}</span>
                            <Tag>{skill.skill_id}</Tag>
                          </Space>
                        }
                        description={
                          <Space direction="vertical" size={4}>
                            <span>{skill.description}</span>
                            <Space wrap>
                              {skill.references.length > 0 && (
                                <Tag>{t('settings.skill_references_count', { count: skill.references.length })}</Tag>
                              )}
                              {skill.scripts.length > 0 && (
                                <Tag>{t('settings.skill_scripts_count', { count: skill.scripts.length })}</Tag>
                              )}
                            </Space>
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              </Card>
              <Card title={t('settings.skill_llm_prompt_title')}>
                <pre
                  className="bg-gray-800 p-3 rounded text-sm text-gray-300 max-h-60"
                  style={promptTextBlockStyle}
                >
                  {skillPromptLoading ? t('common.loading_stats') : (skillPrompt || t('settings.skill_llm_prompt_empty'))}
                </pre>
              </Card>
            </Space>
          )
        },
        {
          key: 'memory-preview',
          label: t('settings.memory_preview_test_title'),
          children: (
            <Card title={`${t('settings.memory_preview_test_title')} (${memoryPreviewTotal})`}>
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 12,
                  marginBottom: 16,
                  alignItems: 'center',
                }}
              >
                <Input
                  placeholder={t('settings.memory_filter_user_id')}
                  value={memoryPreviewFilters.userId}
                  onChange={(e) =>
                    setMemoryPreviewFilters((prev) => ({ ...prev, userId: e.target.value }))
                  }
                  onPressEnter={() => void handleApplyMemoryPreviewFilters()}
                  style={{ width: 140 }}
                  allowClear
                />
                <Input
                  placeholder={t('settings.memory_filter_stock_code')}
                  value={memoryPreviewFilters.stockCode}
                  onChange={(e) =>
                    setMemoryPreviewFilters((prev) => ({ ...prev, stockCode: e.target.value }))
                  }
                  onPressEnter={() => void handleApplyMemoryPreviewFilters()}
                  style={{ width: 160 }}
                  allowClear
                />
                <Select
                  placeholder={t('settings.memory_filter_status')}
                  value={memoryPreviewFilters.status}
                  onChange={(value) =>
                    setMemoryPreviewFilters((prev) => ({ ...prev, status: value }))
                  }
                  options={MEMORY_PREVIEW_STATUS_OPTIONS}
                  style={{ width: 140 }}
                  allowClear
                />
                <Button type="primary" onClick={() => void handleApplyMemoryPreviewFilters()}>
                  {t('settings.memory_search')}
                </Button>
                <Button onClick={() => void handleResetMemoryPreviewFilters()}>
                  {t('settings.memory_reset')}
                </Button>
              </div>
              <Table
                rowKey="memory_id"
                dataSource={memoryPreviewItems}
                columns={memoryPreviewColumns}
                loading={testMemoryPreviewLoading}
                pagination={{
                  current: memoryPreviewPage,
                  pageSize: memoryPreviewPageSize,
                  total: memoryPreviewTotal,
                  showSizeChanger: true,
                  pageSizeOptions: ['10', '20', '50', '100'],
                  onChange: (page, pageSize) => {
                    void fetchMemoryPreviewPage(page, pageSize);
                  },
                }}
                scroll={{ x: 'max-content' }}
                size="small"
              />
            </Card>
          )
        },
        {
          key: 'memory-recall-audits',
          label: t('settings.memory_audit_title'),
          children: (
            <Card title={t('settings.memory_audit_title')}>
              <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                <Col xs={24} sm={8} md={6}>
                  <Statistic
                    title={t('settings.memory_audit_success_rate')}
                    value={memoryRecallAuditSuccessStats.rate * 100}
                    precision={1}
                    suffix="%"
                  />
                </Col>
                <Col xs={24} sm={8} md={6}>
                  <Statistic
                    title={t('settings.memory_audit_ok_rows')}
                    value={`${memoryRecallAuditSuccessStats.success}/${memoryRecallAuditSuccessStats.total}`}
                  />
                </Col>
                <Col xs={24} sm={8} md={6}>
                  <Statistic title={t('settings.memory_audit_total')} value={memoryRecallAuditTotal} />
                </Col>
              </Row>
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 12,
                  marginBottom: 16,
                  alignItems: 'center',
                }}
              >
                <Input
                  placeholder={t('settings.memory_filter_user_id')}
                  value={memoryRecallAuditFilters.userId}
                  onChange={(e) =>
                    setMemoryRecallAuditFilters((prev) => ({ ...prev, userId: e.target.value }))
                  }
                  onPressEnter={() => void handleApplyMemoryRecallAuditFilters()}
                  style={{ width: 140 }}
                  allowClear
                />
                <Input
                  placeholder={t('settings.memory_filter_stock_code')}
                  value={memoryRecallAuditFilters.stockCode}
                  onChange={(e) =>
                    setMemoryRecallAuditFilters((prev) => ({ ...prev, stockCode: e.target.value }))
                  }
                  onPressEnter={() => void handleApplyMemoryRecallAuditFilters()}
                  style={{ width: 160 }}
                  allowClear
                />
                <Select
                  placeholder={t('settings.memory_filter_status')}
                  value={memoryRecallAuditFilters.status}
                  onChange={(value) =>
                    setMemoryRecallAuditFilters((prev) => ({ ...prev, status: value }))
                  }
                  options={MEMORY_RECALL_AUDIT_STATUS_OPTIONS}
                  style={{ width: 140 }}
                  allowClear
                />
                <Input
                  placeholder={t('settings.memory_filter_error_code')}
                  value={memoryRecallAuditFilters.errorCode}
                  onChange={(e) =>
                    setMemoryRecallAuditFilters((prev) => ({ ...prev, errorCode: e.target.value }))
                  }
                  onPressEnter={() => void handleApplyMemoryRecallAuditFilters()}
                  style={{ width: 220 }}
                  allowClear
                />
                <Button type="primary" onClick={() => void handleApplyMemoryRecallAuditFilters()}>
                  {t('settings.memory_search')}
                </Button>
                <Button onClick={() => void handleResetMemoryRecallAuditFilters()}>
                  {t('settings.memory_reset')}
                </Button>
              </div>
              <Table
                rowKey="audit_id"
                dataSource={memoryRecallAuditItems}
                columns={memoryRecallAuditColumns}
                loading={testMemoryRecallAuditLoading}
                pagination={{
                  current: memoryRecallAuditPage,
                  pageSize: memoryRecallAuditPageSize,
                  total: memoryRecallAuditTotal,
                  showSizeChanger: true,
                  pageSizeOptions: ['10', '20', '50', '100'],
                  onChange: (page, pageSize) => {
                    void fetchMemoryRecallAuditPage(page, pageSize);
                  },
                }}
                scroll={{ x: 'max-content' }}
                size="small"
              />
            </Card>
          )
        },
        {
          key: 'playground',
          label: t('settings.system_test'),
          children: (
            <div className="flex flex-col gap-4">
              <Card
                title={t('settings.system_test')}
                extra={
                  <Button
                    type="primary"
                    onClick={handleTestAll}
                    loading={
                      testRedisLoading || testDbLoading ||
                      testTushareLoading || testTavilyLoading || testPythonSandboxLoading ||
                      testSkillsLoading ||
                      testDbSchemaLoading ||
                      testQueryCalcLoading ||
                      testMemoryLoading ||
                      testMemoryReadLoading ||
                      newsTestToolsLoading ||
                      Object.values(dynamicNewsLoading).some(Boolean)
                    }
                  >
                    {t('settings.test_all')}
                  </Button>
                }
              >
                <Row gutter={[16, 16]}>
                  <Col span={4}>
                    <Card size="small" title={t('settings.redis_test_title')}>
                      <Button type="default" onClick={handleTestRedis} loading={testRedisLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.db_test_title')}>
                      <Button type="default" onClick={handleTestDb} loading={testDbLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.tushare_test_title')}>
                      <Button type="default" onClick={handleTestTuShare} loading={testTushareLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.tavily_test_title')}>
                      <Button type="default" onClick={handleTestTavily} loading={testTavilyLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.python_sandbox_test_title')}>
                      <Button type="default" onClick={handleTestPythonSandbox} loading={testPythonSandboxLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.skills_test_title')}>
                      <Button type="default" onClick={handleTestSkills} loading={testSkillsLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.db_schema_test_title')}>
                      <Button type="default" onClick={handleTestDbSchema} loading={testDbSchemaLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.query_calc_test_title')}>
                      <Button type="default" onClick={handleTestQueryCalc} loading={testQueryCalcLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.pdf_tool_test_title')}>
                      <Input
                        size="small"
                        placeholder={t('settings.pdf_tool_url_placeholder')}
                        value={pdfToolTestUrl}
                        onChange={(e) => setPdfToolTestUrl(e.target.value)}
                        onPressEnter={() => void handleTestPdfTool()}
                        style={{ marginBottom: 8 }}
                        allowClear
                      />
                      <Button type="default" onClick={handleTestPdfTool} loading={testPdfToolLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.memory_write_test_title')}>
                      <Button type="default" onClick={handleTestMemory} loading={testMemoryLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.memory_read_test_title')}>
                      <Button type="default" onClick={handleTestMemoryRead} loading={testMemoryReadLoading} block>
                        {t('settings.execute_test')}
                      </Button>
                    </Card>
                  </Col>
                  <Col span={4}>
                    <Card size="small" title={t('settings.docstring_test_title')}>
                      <Button
                        type="default"
                        onClick={() => void handleTestDocstrings()}
                        loading={testDocstringLoading}
                        block
                      >
                        {t('settings.view_docstring')}
                      </Button>
                    </Card>
                  </Col>
                </Row>
                <Card size="small" title={t('settings.dynamic_news_tools')} style={{ marginTop: 16 }}>
                  {newsTestToolsLoading ? (
                    <div style={{ display: 'flex', justifyContent: 'center', padding: '24px 0' }}>
                      <Spin />
                    </div>
                  ) : (
                    <Row gutter={[16, 16]}>
                      {newsTestTools.map((tool) => (
                        <Col span={4} key={tool.name}>
                          <Card size="small" title={tool.name}>
                            <Input
                              size="small"
                              placeholder={tool.default_keyword || t('settings.search_keyword')}
                              value={dynamicNewsKeywords[tool.name] || ''}
                              onChange={(e) =>
                                setDynamicNewsKeywords((prev) => ({ ...prev, [tool.name]: e.target.value }))
                              }
                              onPressEnter={() => handleDynamicNewsTest(tool)}
                              style={{ marginBottom: 8 }}
                              allowClear
                            />
                            <Button
                              type="default"
                              onClick={() => handleDynamicNewsTest(tool)}
                              loading={!!dynamicNewsLoading[tool.name]}
                              block
                            >
                              {t('settings.execute_test')}
                            </Button>
                          </Card>
                        </Col>
                      ))}
                    </Row>
                  )}
                </Card>
                {newsTestResult && (
                  <Card size="small" title={t('settings.test_result')} style={{ marginTop: 16 }}>
                    <pre
                      style={{
                        ...promptTextBlockStyle,
                        margin: 0,
                        padding: 12,
                        background: '#1f2937',
                        color: '#e5e7eb',
                        borderRadius: 6,
                        fontSize: 12,
                        maxHeight: 480,
                      }}
                    >
                      {JSON.stringify(newsTestResult, null, 2)}
                    </pre>
                  </Card>
                )}
              </Card>

              <Modal
                open={docstringModalOpen}
                title={t('settings.docstring_modal_title')}
                onCancel={() => setDocstringModalOpen(false)}
                footer={null}
                width={960}
              >
                <List
                  dataSource={toolDocstrings}
                  renderItem={(item) => (
                    <List.Item>
                      <div style={{ width: '100%', minWidth: 0 }}>
                        <div className="text-white font-bold mb-2">{item.name}</div>
                        <pre
                          className="bg-gray-800 p-3 rounded text-sm text-gray-300"
                          style={promptTextBlockStyle}
                        >
                          {item.description || t('settings.empty_docstring')}
                        </pre>
                      </div>
                    </List.Item>
                  )}
                />
              </Modal>

              <Card title={t('settings.test_prompt_gen')}>
                <Row gutter={[16, 16]}>
                  {AI_FUNCTION_SCENARIOS.map((scenario) => (
                    <Col xs={24} lg={12} key={scenario.key}>
                      <div
                        style={{
                          border: '1px solid #374151',
                          borderRadius: 6,
                          padding: 16,
                          height: '100%',
                        }}
                      >
                        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                          <div className="text-white font-bold">{t(scenario.titleKey)}</div>
                          <Input.TextArea
                            rows={4}
                            placeholder={t(scenario.placeholderKey)}
                            value={aiTestInputs[scenario.key]}
                            onChange={(event) =>
                              setAiTestInputs((prev) => ({
                                ...prev,
                                [scenario.key]: event.target.value,
                              }))
                            }
                          />
                          <Button
                            type="primary"
                            onClick={() => handleAiFunctionTest(scenario.key)}
                            loading={!!aiTestLoading[scenario.key]}
                            block
                          >
                            {t('settings.execute_test')}
                          </Button>
                          <div className="bg-gray-800 p-4 rounded h-full">
                            <h4 className="text-white mb-2">{t('settings.api_response')}:</h4>
                            <pre
                              className="text-gray-300"
                              style={{
                                ...promptTextBlockStyle,
                                margin: 0,
                                maxHeight: 420,
                              }}
                            >
                              {aiTestOutputs[scenario.key]}
                            </pre>
                          </div>
                        </Space>
                      </div>
                    </Col>
                  ))}
                </Row>
              </Card>
            </div>
          )
        },
        {
          key: 'stats',
          label: t('settings.usage_stats'),
          children: (
            <Card
              title={t('settings.usage_stats')}
              extra={
                <Space>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={statsLoading}
                    onClick={loadStats}
                  >
                    {t('settings.refresh_usage_stats')}
                  </Button>
                  <Popconfirm
                    title={t('settings.clear_usage_stats_confirm')}
                    okText={t('common.confirm')}
                    cancelText={t('common.cancel')}
                    onConfirm={handleClearUsageStats}
                  >
                    <Button danger size="small" loading={clearStatsLoading}>
                      {t('settings.clear_usage_stats')}
                    </Button>
                  </Popconfirm>
                </Space>
              }
            >
              {stats ? (
                <Row gutter={16}>
                  <Col span={8}>
                    <Statistic title={t('settings.total_calls')} value={stats.total_calls} />
                  </Col>
                  <Col span={8}>
                    <Statistic title={t('settings.total_tokens')} value={stats.total_tokens} />
                  </Col>
                  <Col span={8}>
                    <Statistic title={t('settings.cache_hit_rate')} value={formatPercent(stats.cache_hit_rate)} />
                  </Col>
                  <Col span={8}>
                    <Statistic title={t('settings.input_tokens')} value={stats.input_tokens || 0} />
                  </Col>
                  <Col span={8}>
                    <Statistic title={t('settings.cached_tokens')} value={stats.cached_tokens || 0} />
                  </Col>
                  <Col span={8}>
                    <Statistic title={t('settings.cache_miss_tokens')} value={stats.cache_miss_tokens || 0} />
                  </Col>
                  <Col span={8}>
                    <Statistic title={t('settings.reasoning_tokens')} value={stats.reasoning_tokens || 0} />
                  </Col>
                  <Col span={24} style={{ marginTop: 24 }}>
                    <Row gutter={[16, 16]}>
                      {renderCacheLanePanel(
                        t('settings.stock_analysis_cache_hit_rate'),
                        getBusinessCacheUsage(stats.backend, 'debate_analysis', 'agent'),
                      )}
                      {renderCacheLanePanel(
                        t('settings.news_summary_cache_hit_rate'),
                        getBusinessCacheUsage(stats.backend, 'debate_analysis', 'tool_summary'),
                      )}
                      {renderMainSystemStatsPanel(stats.backend)}
                      {renderMemoryStatsPanel(stats.memory)}
                    </Row>
                  </Col>
                </Row>
              ) : (
                <div className="text-gray-500">{t('common.loading_stats')}</div>
              )}
            </Card>
          )
        }
      ]} />

      <Modal
        open={mcpModalOpen}
        title={editingMcpServer ? t('settings.mcp.edit_server') : t('settings.mcp.add_server')}
        onCancel={() => {
          setMcpModalOpen(false);
          setEditingMcpServer(null);
        }}
        onOk={() => void handleSaveMcpServer()}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        confirmLoading={mcpSaving}
        destroyOnHidden
      >
        <Form<MCPServerFormValues> form={mcpForm} layout="vertical">
          <Form.Item
            label={t('settings.mcp.name')}
            name="name"
            rules={[
              { required: true, message: t('settings.mcp.name_required') },
              { max: 64, message: t('settings.mcp.name_invalid') },
            ]}
          >
            <Input disabled={!!editingMcpServer} placeholder="网页抓取" />
          </Form.Item>
          <Form.Item
            label={t('settings.mcp.url')}
            name="url"
            rules={[
              { required: true, message: t('settings.mcp.url_required') },
              { type: 'url', message: t('settings.mcp.url_invalid') },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item label={t('settings.mcp.token')} name="token">
            <Input.Password autoComplete="off" />
          </Form.Item>
          <Form.Item>
            <Button onClick={() => void handlePreviewMcpTools()} loading={mcpPreviewLoading}>
              {t('settings.mcp.refresh_tools')}
            </Button>
          </Form.Item>
          <Form.Item
            label={t('settings.mcp.allowed_tools')}
            name="allowed_tools"
            rules={[{ required: true, message: t('settings.mcp.allowed_tools_required') }]}
          >
            <Select
              mode="multiple"
              options={mcpPreviewTools.map((tool) => ({ value: tool.name, label: tool.name }))}
              placeholder={t('settings.mcp.allowed_tools_placeholder')}
              disabled={mcpPreviewTools.length === 0}
            />
          </Form.Item>
          <Form.Item label={t('settings.mcp.enabled')} name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={mcpToolsModalOpen}
        title={t('settings.mcp.tools_title', { name: mcpToolsServerName })}
        onCancel={() => setMcpToolsModalOpen(false)}
        footer={null}
        width={900}
      >
        <Table<MCPToolItem>
          rowKey={(record) => `${record.server}:${record.name}`}
          dataSource={mcpTools}
          columns={mcpToolColumns}
          loading={mcpToolsLoading}
          pagination={false}
          locale={{ emptyText: t('settings.mcp.tools_empty') }}
          scroll={{ x: 760 }}
          size="small"
        />
      </Modal>

      <Modal
        open={newsPluginModalOpen}
        title={t('settings.add_news_plugin')}
        onCancel={() => {
          setNewsPluginModalOpen(false);
          setNewsPluginFileList([]);
        }}
        onOk={handleUploadNewsPlugin}
        okText={t('settings.upload_news_plugin')}
        cancelText={t('common.cancel')}
        confirmLoading={newsPluginSaving}
        okButtonProps={{ disabled: newsPluginFileList.length === 0 }}
        width={900}
        destroyOnHidden
      >
        <Upload
          accept=".py"
          beforeUpload={() => false}
          multiple
          fileList={newsPluginFileList}
          onChange={({ fileList }) => setNewsPluginFileList(fileList)}
        >
          <Button>{t('settings.select_news_plugin_file')}</Button>
        </Upload>
        <a
          href="https://github.com/MarvekG/BestAITrader/blob/main/backend/app/ai/agentic/tooling/news_plugins/README.md"
          target="_blank"
          rel="noopener noreferrer"
          style={{ display: 'inline-block', marginTop: 12 }}
        >
          {t('settings.news_plugin_spec_link')}
        </a>
      </Modal>

      <Modal
        open={skillModalOpen}
        title={t('settings.add_skill')}
        onCancel={() => {
          setSkillModalOpen(false);
          setSkillFileList([]);
        }}
        onOk={handleUploadSkill}
        okText={t('settings.upload_skill')}
        cancelText={t('common.cancel')}
        confirmLoading={skillSaving}
        okButtonProps={{ disabled: skillFileList.length === 0 }}
        width={900}
        destroyOnHidden
      >
        <Upload
          directory
          beforeUpload={() => false}
          fileList={skillFileList}
          onChange={({ fileList }) => setSkillFileList(fileList)}
        >
          <Button>{t('settings.select_skill_folder')}</Button>
        </Upload>
      </Modal>

      <Modal
        open={importConfirmOpen}
        title={t('settings.database_import_confirm_title')}
        onCancel={() => {
          if (!importLoading) {
            setImportConfirmOpen(false);
          }
        }}
        onOk={doImportDatabaseBackup}
        okText={t('settings.confirm_import')}
        cancelText={t('settings.cancel')}
        okButtonProps={{ danger: true, loading: importLoading, disabled: importLoading }}
        cancelButtonProps={{ disabled: importLoading }}
        maskClosable={!importLoading}
        closable={!importLoading}
        keyboard={!importLoading}
        confirmLoading={importLoading}
      >
        {t('settings.database_import_confirm_text')}
      </Modal>
    </div>
  );
};
type MemoryPreviewFilters = {
  userId: string;
  stockCode: string;
  status?: string;
};

type MemoryRecallAuditFilters = MemoryPreviewFilters & {
  errorCode: string;
};

const MEMORY_PREVIEW_STATUS_OPTIONS = [
  { value: 'active', label: 'active' },
  { value: 'stale', label: 'stale' },
  { value: 'superseded', label: 'superseded' },
  { value: 'archived', label: 'archived' },
];

const MEMORY_RECALL_AUDIT_STATUS_OPTIONS = [
  { value: 'ok', label: 'ok' },
  { value: 'partial', label: 'partial' },
  { value: 'rejected', label: 'rejected' },
  { value: 'not_ready', label: 'not_ready' },
];

type MemoryPreviewItem = {
  memory_id: string;
  session?: string;
  content: string;
  occurred_at?: string;
  created_at: string;
};

type MemoryPreviewResultData = {
  items?: MemoryPreviewItem[];
  next_cursor?: string | null;
};

type MemoryRecallAuditItem = {
  audit_id?: string;
  audit_type?: string;
  query_id?: string;
  delete_id?: string;
  session: string;
  query: string;
  status: string;
  error_code?: string | null;
  error_stage?: string | null;
  error_message?: string | null;
  final_answer?: string;
  selected_memory_ids?: string[];
  retrieved?: unknown[];
  answerability?: string;
  answerability_reason?: string;
  created_at: string;
};

type MemoryRecallAuditResultData = {
  items?: MemoryRecallAuditItem[];
  next_cursor?: string | null;
};
