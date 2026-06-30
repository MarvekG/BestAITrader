import { apiClient } from './client';

export interface TestResult {
  status: 'success' | 'error';
  message: string;
  error?: string;
  elapsed_ms?: number;
  total?: number;
  limit?: number;
  offset?: number;
  items?: unknown[];
  data?: unknown;
}

export interface MemoryPreviewParams {
  user_id?: number;
  stock_code?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface MemoryRecallAuditPreviewParams extends MemoryPreviewParams {
  error_code?: string;
}

export interface ToolDocstringItem {
  name: string;
  description: string;
}

export interface ToolDocstringResult extends TestResult {
  items?: ToolDocstringItem[];
}

export interface NewsTestingTool {
  name: string;
  category: 'news';
  route_slug: string;
  test_route: string;
  success_key: string;
  failure_key: string;
  source: string;
  default_keyword?: string;
}

export interface TestingCatalog {
  status: 'success';
  count: number;
  fixed_tools: Array<Record<string, unknown>>;
  news_tools: NewsTestingTool[];
}

export type AiFunctionScenario =
  | 'no_tools'
  | 'tools'
  | 'skills'
  | 'tools_and_skills'
  | 'thinking_tools'
  | 'thinking_skills';

export interface AiFunctionTestRequest {
  scenario: AiFunctionScenario;
  user_input: string;
}

export interface AiFunctionTestResult extends TestResult {
  scenario: AiFunctionScenario;
  scenario_label: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  model: string;
  provider: string;
}

export interface AiFunctionTestSubmission {
  task_id: string;
  task_name: string;
  status: string;
  message: string;
  new_task: boolean;
  scenario: AiFunctionScenario;
  scenario_label: string;
}

export const testingApi = {
  testRedis: async (): Promise<TestResult> => {
    return apiClient.get('/testing/redis');
  },
  testDb: async (): Promise<TestResult> => {
    return apiClient.get('/testing/db');
  },
  testTushare: async (): Promise<TestResult> => {
    return apiClient.get('/testing/tushare');
  },
  testPythonSandbox: async (): Promise<TestResult> => {
    return apiClient.get('/testing/python_sandbox');
  },
  testSkills: async (): Promise<TestResult> => {
    return apiClient.get('/testing/skills');
  },
  testLlm: async (): Promise<TestResult> => {
    return apiClient.get('/llm/probe', { timeout: 180000 });
  },
  runAiFunctionTest: async (payload: AiFunctionTestRequest): Promise<AiFunctionTestSubmission> => {
    return apiClient.post('/llm/function-test', payload);
  },
  testSinaNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/sina_news');
  },
  testStcnNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/stcn_news');
  },
  testCsNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/cs_news');
  },
  testJqkaNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/jqka_news');
  },
  testYicaiNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/yicai_news');
  },
  testZqrbNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/zqrb_news');
  },
  testNbdNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/nbd_news');
  },
  test21JingjiNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/21jingji_news');
  },
  testThepaperNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/thepaper_news');
  },
  testEeoNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/eeo_news');
  },
  testJiemianNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/jiemian_news');
  },
  testIfengNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/ifeng_news');
  },
  testChinaComFinanceNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/china_com_finance_news');
  },
  testSohuFinanceNews: async (): Promise<TestResult> => {
    return apiClient.get('/testing/sohu_finance_news');
  },
  testCninfoAnnouncements: async (): Promise<TestResult> => {
    return apiClient.get('/testing/cninfo_announcements');
  },
  testExchangeOfficialAnnouncements: async (): Promise<TestResult> => {
    return apiClient.get('/testing/exchange_official_announcements');
  },
  testDbSchema: async (): Promise<TestResult> => {
    return apiClient.get('/testing/db_schema');
  },
  testQueryCalc: async (): Promise<TestResult> => {
    return apiClient.get('/testing/query_calc');
  },
  testPdfTool: async (url: string): Promise<TestResult> => {
    return apiClient.get('/testing/pdf_tool', { params: { url }, timeout: 300000 });
  },
  testMemory: async (): Promise<TestResult> => {
    return apiClient.get('/testing/memory');
  },
  testMemoryRead: async (): Promise<TestResult> => {
    return apiClient.get('/testing/memory_read');
  },
  testMemoryPreview: async (params?: MemoryPreviewParams): Promise<TestResult> => {
    return apiClient.get('/testing/memory_preview', { params });
  },
  testMemoryRecallAudits: async (params?: MemoryRecallAuditPreviewParams): Promise<TestResult> => {
    return apiClient.get('/testing/memory_recall_audits', { params });
  },
  testDocstrings: async (): Promise<ToolDocstringResult> => {
    return apiClient.get('/testing/docstrings');
  },
  getTestingCatalog: async (): Promise<TestingCatalog> => {
    return apiClient.get('/testing/tools');
  },
  runNewsTool: async (testRoute: string, keyword?: string): Promise<TestResult> => {
    const params = keyword ? { keyword } : undefined;
    return apiClient.get(testRoute, { params });
  },
};
