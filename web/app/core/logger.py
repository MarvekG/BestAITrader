import json
import logging


class JsonFormatter(logging.Formatter):
    """Web 服务 JSON 日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        """
        将日志记录格式化为 JSON 字符串。

        Args:
            record: 标准日志记录。

        Returns:
            JSON 格式日志。
        """
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "process_id": record.process,
            "source": getattr(record, "source", "web"),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)


def get_logger(name: str = "web_fetch") -> logging.Logger:
    """
    获取 Web 服务日志记录器。

    Args:
        name: 日志记录器名称。

    Returns:
        标准日志记录器。
    """
    return logging.getLogger(name)


logger = get_logger()
