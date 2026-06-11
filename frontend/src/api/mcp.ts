import { apiClient } from './client';

export interface MCPServerItem {
  name: string;
  enabled: boolean;
  url: string;
  allowed_tools: string[];
}

export interface MCPServerSavePayload extends MCPServerItem {
  token?: string;
}

export interface MCPServerListResult {
  status: 'success' | 'error';
  count: number;
  items: MCPServerItem[];
  message?: string;
}

export interface MCPServerMutationResult {
  status: 'success' | 'error';
  server?: MCPServerItem;
  name?: string;
  message?: string;
}

export interface MCPToolItem {
  server: string;
  name: string;
  langchain_name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface MCPToolsResult {
  status: 'success' | 'error';
  name?: string;
  count?: number;
  items?: MCPToolItem[];
  tool_count?: number;
  tools?: MCPToolItem[];
  message?: string;
}

export interface MCPPromptResult {
  status: 'success' | 'error';
  prompt: string;
  message?: string;
}

export interface MCPToolInvokeResult {
  status: 'success' | 'error';
  name?: string;
  tool_name?: string;
  result?: unknown;
  message?: string;
}

export const mcpApi = {
  list: async (): Promise<MCPServerListResult> => {
    return apiClient.get('/mcp/servers');
  },
  create: async (payload: MCPServerSavePayload): Promise<MCPServerMutationResult> => {
    return apiClient.post('/mcp/servers', payload);
  },
  update: async (name: string, payload: Partial<Pick<MCPServerSavePayload, 'enabled' | 'url' | 'token' | 'allowed_tools'>>): Promise<MCPServerMutationResult> => {
    return apiClient.put(`/mcp/servers/${encodeURIComponent(name)}`, payload);
  },
  previewTools: async (payload: Pick<MCPServerSavePayload, 'name' | 'url' | 'token'>): Promise<MCPToolsResult> => {
    return apiClient.post('/mcp/tools/preview', payload);
  },
  delete: async (name: string): Promise<MCPServerMutationResult> => {
    return apiClient.delete(`/mcp/servers/${encodeURIComponent(name)}`);
  },
  test: async (name: string): Promise<MCPToolsResult> => {
    return apiClient.post(`/mcp/servers/${encodeURIComponent(name)}/test`);
  },
  listTools: async (name: string): Promise<MCPToolsResult> => {
    return apiClient.get(`/mcp/servers/${encodeURIComponent(name)}/tools`);
  },
  getPrompt: async (): Promise<MCPPromptResult> => {
    return apiClient.get('/mcp/prompt');
  },
  invokeTool: async (name: string, toolName: string, argumentsPayload: Record<string, unknown>): Promise<MCPToolInvokeResult> => {
    return apiClient.post(`/mcp/servers/${encodeURIComponent(name)}/tools/${encodeURIComponent(toolName)}/invoke`, {
      arguments: argumentsPayload,
    });
  },
};
