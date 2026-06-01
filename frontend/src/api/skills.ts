import { apiClient } from './client';

export interface SkillItem {
  skill_id: string;
  name: string;
  description: string;
  references: string[];
  scripts: string[];
  can_delete: boolean;
}

export interface SkillListResult {
  status: 'success' | 'error';
  count: number;
  items: SkillItem[];
  message?: string;
}

export interface DependencyInstallInfo {
  status: 'success' | 'error' | 'skipped';
  requirements: string[];
  command: string[];
  exit_code?: number | null;
  stdout?: string;
  stderr?: string;
  message?: string;
}

export interface SkillMutationResult {
  status: 'success' | 'error';
  message: string;
  skill_id?: string;
  skill?: SkillItem;
  dependencies?: DependencyInstallInfo;
}

export interface SkillPromptResult {
  status: 'success' | 'error';
  prompt: string;
  message?: string;
}

export const skillsApi = {
  list: async (): Promise<SkillListResult> => {
    return apiClient.get('/skills');
  },
  getPrompt: async (): Promise<SkillPromptResult> => {
    return apiClient.get('/skills/prompt');
  },
  upload: async (files: File[]): Promise<SkillMutationResult> => {
    const formData = new FormData();
    files.forEach((file) => {
      const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      formData.append('files', file, relativePath);
    });
    return apiClient.post('/skills', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  delete: async (skillId: string): Promise<SkillMutationResult> => {
    return apiClient.delete(`/skills/${encodeURIComponent(skillId)}`);
  },
};
