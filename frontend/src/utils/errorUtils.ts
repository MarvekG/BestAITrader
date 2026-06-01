/**
 * 将 API 错误详情转换为字符串，防止 React 渲染对象导致崩溃
 * Converts API error details to string to prevent React rendering crashes
 */
export const formatErrorMessage = (detail: unknown): string => {
    if (!detail) return 'Unknown error occurred';

    if (typeof detail === 'string') {
        return detail;
    }

    if (Array.isArray(detail)) {
        // 处理 Pydantic 校验错误列表 | Handle Pydantic validation error list
        return detail
            .map((err) => {
                if (!isRecord(err)) {
                    return String(err);
                }
                const loc = Array.isArray(err.loc) ? `[${err.loc.join('.')}] ` : '';
                const message = typeof err.msg === 'string' ? err.msg : 'Invalid input';
                return `${loc}${message}`;
            })
            .join('; ');
    }

    if (typeof detail === 'object') {
        // 处理其他对象格式的错误 | Handle other object format errors
        try {
            return JSON.stringify(detail);
        } catch {
            return 'Error detail is unrenderable object';
        }
    }

    return String(detail);
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null;

export const getApiErrorResponseData = (error: unknown): unknown => {
    if (!isRecord(error) || !isRecord(error.response)) {
        return undefined;
    }
    return error.response.data;
};

export const getApiErrorMessage = (error: unknown, fallback: string = 'Unknown error occurred'): string => {
    const responseData = getApiErrorResponseData(error);
    if (isRecord(responseData) && responseData.detail) {
        return formatErrorMessage(responseData.detail);
    }
    if (typeof responseData === 'string') {
        return responseData;
    }
    if (isRecord(error) && typeof error.message === 'string') {
        return error.message;
    }
    return fallback;
};
