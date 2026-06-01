// 定义日志级别
type LogLevel = 'info' | 'warn' | 'error';

interface LogEntry {
  level: LogLevel;
  message: string;
  timestamp: string;
  url: string;
  userAgent: string;
  meta?: unknown; // 堆栈信息、组件名等
}

class LoggerService {
  private buffer: LogEntry[] = [];
  private flushInterval: number = 5000; // 5秒批量发送一次
  private isSending: boolean = false;

  constructor() {
    // 定时发送日志，避免频繁请求
    setInterval(() => this.flush(), this.flushInterval);
  }

  private addLog(level: LogLevel, message: string, meta?: unknown) {
    const entry: LogEntry = {
      level,
      message,
      timestamp: new Date().toISOString(),
      url: window.location.href,
      userAgent: navigator.userAgent,
      meta,
    };
    this.buffer.push(entry);
    
    // 如果是严重错误，立即发送
    if (level === 'error') {
      this.flush();
    }
  }

  public info(message: string, meta?: unknown) { this.addLog('info', message, meta); }
  public warn(message: string, meta?: unknown) { this.addLog('warn', message, meta); }
  public error(message: string, meta?: unknown) { this.addLog('error', message, meta); }

  private async flush() {
    if (this.buffer.length === 0 || this.isSending) return;

    this.isSending = true;
    const logsToSend = [...this.buffer];
    this.buffer = [];

    try {
      // 删除了 /api/v1/logs/batch 接口调用
      console.log('Logs would be sent to backend (feature removed):', logsToSend);
    } catch (e) {
      console.error('Failed to process logs', e);
    } finally {
      this.isSending = false;
    }
  }
}

export const logger = new LoggerService();
