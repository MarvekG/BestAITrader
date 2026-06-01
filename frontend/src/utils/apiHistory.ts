// API 历史记录管理服务
// API History Management Service

const STORAGE_KEY = 'api_history_records';
const MAX_RECORDS = 100; // 最大保存记录数 | Maximum number of records to keep
const REDACTED_VALUE = '[REDACTED]';
const SENSITIVE_KEY_FRAGMENTS = [
    'token',
    'api_key',
    'apikey',
    'authorization',
    'password',
    'secret',
    'cookie',
    'credential',
];
const SKIPPED_HISTORY_PATHS = [
    '/api/v1/auth/login',
    '/api/v1/auth/register',
    '/api/v1/auth/reset-password',
];

/**
 * API 历史记录条目接口
 * API History Record Entry Interface
 */
export interface ApiHistoryRecord {
    id: string;                    // 唯一标识 | Unique identifier
    task_id?: string;              // 后端任务 ID（用于 WebSocket 响应匹配）| Backend task ID for WebSocket response matching
    url: string;                   // 请求 URL | Request URL
    method: string;                // HTTP 方法 | HTTP method
    params?: unknown;              // 请求参数 | Request parameters
    requestTime: number;           // 请求时间戳 | Request timestamp
    responseTime?: number;         // 响应时间戳 | Response timestamp
    status: 'pending' | 'completed' | 'failed';  // 状态 | Status
    response?: unknown;            // 响应数据 | Response data
    error?: string;                // 错误信息 | Error message
    duration?: number;             // 耗时（ms）| Duration in milliseconds
}

/**
 * API 历史记录管理类
 * API History Manager Class
 */
class ApiHistoryManager {
    shouldSkipHistory(url: string): boolean {
        const normalizedUrl = this.normalizeUrl(url);
        return SKIPPED_HISTORY_PATHS.some(path => normalizedUrl.startsWith(path));
    }

    sanitizeForHistory(data: unknown): unknown {
        if (Array.isArray(data)) {
            return data.map(item => this.sanitizeForHistory(item));
        }
        if (data && typeof data === 'object') {
            return Object.fromEntries(
                Object.entries(data as Record<string, unknown>).map(([key, value]) => [
                    key,
                    this.isSensitiveKey(key) ? REDACTED_VALUE : this.sanitizeForHistory(value),
                ])
            );
        }
        if (typeof data === 'string') {
            return this.redactSensitiveText(data);
        }
        return data;
    }

    private normalizeUrl(url: string): string {
        if (!url) return '';
        try {
            return new URL(url, window.location.origin).pathname;
        } catch {
            return url.split('?')[0] || url;
        }
    }

    private isSensitiveKey(key: string): boolean {
        const lowered = key.toLowerCase();
        return SENSITIVE_KEY_FRAGMENTS.some(fragment => lowered.includes(fragment));
    }

    private redactSensitiveText(value: string): string {
        return value.replace(
            /\b(token|api_key|apikey|authorization|password|secret|cookie|credential)(\s*[:=]\s*)([^,\s&|}]+)/gi,
            (_match, key: string, separator: string) => `${key}${separator}${REDACTED_VALUE}`
        );
    }

    /**
     * 从 localStorage 获取所有记录
     * Get all records from localStorage
     */
    getRecords(): ApiHistoryRecord[] {
        try {
            const data = localStorage.getItem(STORAGE_KEY);
            if (!data) return [];
            return JSON.parse(data);
        } catch (error) {
            console.error('Failed to load API history records:', error);
            return [];
        }
    }

    /**
     * 清理过大的对象 (Clean up over-sized objects)
     */
    private trimLargeData(data: unknown, maxSizeStr: number = 100000): unknown {
        if (!data) return data;
        try {
            const str = typeof data === 'string' ? data : JSON.stringify(data);
            if (str && str.length > maxSizeStr) {
                return '[Data too large to display]';
            }
            return data;
        } catch {
            return '[Failed to stringify data]';
        }
    }

    /**
     * 保存记录到 localStorage
     * Save records to localStorage
     */
    private saveRecords(records: ApiHistoryRecord[]): void {
        try {
            // 在保存前，清理掉特别庞大的 params 或 response，防止爆掉 localStorage 的 5MB 配额
            // Before saving, trim extremely large params or responses to prevent QuotaExceededError
            const safeRecords = records.map(record => ({
                ...record,
                params: this.trimLargeData(this.sanitizeForHistory(record.params), 50000), // 参数最大允许约 50KB 长度
                response: this.trimLargeData(this.sanitizeForHistory(record.response), 100000), // 响应体最大允许约 100KB 长度
                error: typeof record.error === 'string' ? this.redactSensitiveText(record.error) : record.error,
            }));

            localStorage.setItem(STORAGE_KEY, JSON.stringify(safeRecords));
        } catch (error) {
            console.error('Failed to save API history records:', error);
            // 如果清理后仍然超限（比如积少成多），强制减少记录数量再试一次
            // If quota still exceeded, forcefully drop half the records and try again
            if (error instanceof DOMException && error.name === 'QuotaExceededError') {
                try {
                    console.warn('LocalStorage quota exceeded, clearing half of API history.');
                    const reducedRecords = records.slice(0, Math.floor(records.length / 2));
                    localStorage.setItem(STORAGE_KEY, JSON.stringify(reducedRecords));
                } catch (fallbackError) {
                    console.error('Failed to save even after reduction:', fallbackError);
                }
            }
        }
    }

    /**
     * 添加请求记录（创建 pending 状态记录）
     * Add request record (create pending state record)
     * 
     * @param url - 请求 URL | Request URL
     * @param method - HTTP 方法 | HTTP method
     * @param params - 请求参数 | Request parameters
     * @param task_id - 任务 ID（可选）| Task ID (optional)
     * @returns 记录 ID | Record ID
     */
    addRequest(url: string, method: string, params?: unknown, task_id?: string): string {
        if (this.shouldSkipHistory(url)) {
            return '';
        }
        const records = this.getRecords();
        const id = `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

        const newRecord: ApiHistoryRecord = {
            id,
            task_id,
            url,
            method: method.toUpperCase(),
            params: this.sanitizeForHistory(params),
            requestTime: Date.now(),
            status: task_id ? 'pending' : 'completed', // 有 task_id 则为 pending，否则为同步请求已完成
        };

        records.unshift(newRecord); // 添加到开头 | Add to beginning

        // 限制最大记录数 | Limit max records
        if (records.length > MAX_RECORDS) {
            records.splice(MAX_RECORDS);
        }

        this.saveRecords(records);
        return id;
    }

    /**
     * 开始跟踪一个新的真正的 HTTP 请求 (Start tracking a real HTTP request)
     */
    startHttpRequest(url: string, method: string, params?: unknown): string {
        if (this.shouldSkipHistory(url)) {
            return '';
        }
        const records = this.getRecords();
        const id = `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

        const newRecord: ApiHistoryRecord = {
            id,
            url,
            method: method.toUpperCase(),
            params: this.sanitizeForHistory(params),
            requestTime: Date.now(),
            status: 'pending', // 刚发出的 HTTP 请求处于 pending 状态
        };

        records.unshift(newRecord);

        if (records.length > MAX_RECORDS) {
            records.splice(MAX_RECORDS);
        }

        this.saveRecords(records);
        return id;
    }

    /**
     * 结束一个 HTTP 请求 (Finish tracking a real HTTP request)
     */
    finishHttpRequest(id: string, response: unknown, task_id?: string): void {
        if (!id) {
            return;
        }
        const records = this.getRecords();
        const recordIndex = records.findIndex(r => r.id === id);

        if (recordIndex === -1) {
            return; // 找不到记录，可能被清除
        }

        const record = records[recordIndex];
        const responseTime = Date.now();

        records[recordIndex] = {
            ...record,
            responseTime,
            duration: responseTime - record.requestTime,
            status: task_id ? 'pending' : 'completed', // 如果有 task_id，说明后台任务仍在运行，状态保持 pending
            task_id: task_id || record.task_id,
            response: task_id ? record.response : this.sanitizeForHistory(response), // 异步任务的 response 会在 websocket 处更新
        };

        this.saveRecords(records);
    }

    /**
     * 标记 HTTP 请求失败 (Mark HTTP request as failed)
     */
    failHttpRequest(id: string, errorMsg: string, response?: unknown): void {
        if (!id) {
            return;
        }
        const records = this.getRecords();
        const recordIndex = records.findIndex(r => r.id === id);

        if (recordIndex === -1) {
            return;
        }

        const record = records[recordIndex];
        const responseTime = Date.now();

        records[recordIndex] = {
            ...record,
            responseTime,
            duration: responseTime - record.requestTime,
            status: 'failed',
            error: this.redactSensitiveText(errorMsg),
            response: this.sanitizeForHistory(response),
        };

        this.saveRecords(records);
    }

    /**
     * 更新请求记录（添加响应数据）
     * Update request record (add response data)
     * 
     * @param task_id - 任务 ID | Task ID
     * @param status - 状态 | Status
     * @param response - 响应数据 | Response data
     * @param error - 错误信息（可选）| Error message (optional)
     */
    updateResponse(task_id: string, status: 'completed' | 'failed', response?: unknown, error?: string): void {
        const records = this.getRecords();
        const recordIndex = records.findIndex(r => r.task_id === task_id);

        if (recordIndex === -1) {
            console.warn(`API history record not found for task_id: ${task_id}`);
            return;
        }

        const record = records[recordIndex];
        const responseTime = Date.now();

        records[recordIndex] = {
            ...record,
            status,
            response: this.sanitizeForHistory(response),
            error: error ? this.redactSensitiveText(error) : error,
            responseTime,
            duration: responseTime - record.requestTime,
        };

        this.saveRecords(records);
    }

    /**
     * 直接添加完整记录（用于同步请求）
     * Add complete record directly (for synchronous requests)
     * 
     * @param url - 请求 URL | Request URL
     * @param method - HTTP 方法 | HTTP method
     * @param params - 请求参数 | Request parameters
     * @param response - 响应数据 | Response data
     * @param status - 状态 | Status
     * @param error - 错误信息（可选）| Error message (optional)
     */
    addCompleteRecord(
        url: string,
        method: string,
        params?: unknown,
        response?: unknown,
        status: 'completed' | 'failed' = 'completed',
        error?: string
    ): void {
        if (this.shouldSkipHistory(url)) {
            return;
        }
        const records = this.getRecords();
        const id = `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const requestTime = Date.now();

        const newRecord: ApiHistoryRecord = {
            id,
            url,
            method: method.toUpperCase(),
            params: this.sanitizeForHistory(params),
            requestTime,
            responseTime: requestTime,
            status,
            response: this.sanitizeForHistory(response),
            error: error ? this.redactSensitiveText(error) : error,
            duration: 0,
        };

        records.unshift(newRecord);

        // 限制最大记录数 | Limit max records
        if (records.length > MAX_RECORDS) {
            records.splice(MAX_RECORDS);
        }

        this.saveRecords(records);
    }

    /**
     * 清空所有记录
     * Clear all records
     */
    clearRecords(): void {
        try {
            localStorage.removeItem(STORAGE_KEY);
        } catch (error) {
            console.error('Failed to clear API history records:', error);
        }
    }

    /**
     * 根据 ID 获取单条记录
     * Get single record by ID
     */
    getRecordById(id: string): ApiHistoryRecord | undefined {
        const records = this.getRecords();
        return records.find(r => r.id === id);
    }
}

// 导出单例实例 | Export singleton instance
export const apiHistory = new ApiHistoryManager();
