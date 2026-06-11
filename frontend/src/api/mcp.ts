import { apiClient } from './client';

export interface MCPServerItem {
  name: string;
  enabled: boolean;
  url: string;
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

export const mcpApi = {
  list: async (): Promise<MCPServerListResult> => {
    return apiClient.get('/mcp/servers');
  },
  create: async (payload: MCPServerItem): Promise<MCPServerMutationResult> => {
    return apiClient.post('/mcp/servers', payload);
  },
  update: async (name: string, payload: Partial<Pick<MCPServerItem, 'enabled' | 'url'>>): Promise<MCPServerMutationResult> => {
    return apiClient.put(`/mcp/servers/${encodeURIComponent(name)}`, payload);
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
};
