from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from app.core.config import settings
from app.core.logger import get_logger
from app.core.request_context import get_or_create_request_id

logger = get_logger(__name__)
GENERAL_SESSION_TYPE = "general"
STOCK_SESSION_TYPE = "stock"


def _build_user_session(
    *,
    user_id: int,
    stock_code: str | None = None,
) -> str:
    """构建 MemoFlux 会话标识。

    Args:
        user_id: 当前用户 ID。
        stock_code: 可选股票代码；存在时构建股票维度会话。

    Returns:
        MemoFlux session 字符串。
    """
    normalized_stock_code = str(stock_code or "").strip()
    if normalized_stock_code:
        return f"user:{user_id}:{STOCK_SESSION_TYPE}:{normalized_stock_code}"
    return f"user:{user_id}:general"


def _resolve_memo_session(stock_code: str | None) -> tuple[str | None, str]:
    """根据股票代码解析记忆会话类型。

    Args:
        stock_code: 可选股票代码。

    Returns:
        标准化股票代码和 memo session 类型。
    """

    normalized_stock_code = str(stock_code or "").strip() or None
    if normalized_stock_code:
        return normalized_stock_code, STOCK_SESSION_TYPE
    return None, GENERAL_SESSION_TYPE


def _utc_now_iso() -> str:
    """生成 MemoFlux 写入要求的 UTC 发生时间。

    Returns:
        以 Z 结尾的 UTC ISO 时间字符串。
    """

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MemoryServiceClient:
    def __init__(self) -> None:
        """初始化 MemoFlux 客户端状态。"""

        self._last_errors: dict[str, dict[str, Any]] = {}
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(settings.MEMORY_SERVICE_ENABLED and settings.MEMORY_SERVICE_BASE_URL)

    def get_last_error(self, operation: str) -> dict[str, Any] | None:
        error = self._last_errors.get(operation)
        return dict(error) if isinstance(error, dict) else None

    def clear_last_error(self, operation: str) -> None:
        self._last_errors.pop(operation, None)

    def _get_client(self) -> httpx.AsyncClient:
        """获取复用的 MemoFlux HTTP 客户端。

        Returns:
            可复用的异步 HTTP 客户端。
        """

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=settings.MEMORY_SERVICE_TIMEOUT_SECONDS)
        return self._client

    async def close(self) -> None:
        """关闭复用的 MemoFlux HTTP 客户端连接池。"""

        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def check_embedding_health(self) -> dict[str, Any]:
        if not self.enabled:
            self.clear_last_error("embedding_health")
            logger.info("Memory service health check skipped: enabled=%s", self.enabled)
            return {}
        response = await self._get(
            "/v1/health",
            timeout_seconds=max(30.0, settings.MEMORY_SERVICE_TIMEOUT_SECONDS),
            operation="embedding_health",
        )
        if not isinstance(response, dict):
            return {}
        return {
            "status": response.get("status"),
            "provider": response.get("retrieval"),
            "model": response.get("llm"),
            "dimension": None,
        }

    async def get_usage_stats(self, *, hours: int | None = None) -> dict[str, Any]:
        if not self.enabled:
            self.clear_last_error("usage_stats")
            logger.info("Memory usage stats skipped: enabled=%s", self.enabled)
            return {}
        return await self._get("/v1/usage/stats", operation="usage_stats")

    async def clear_usage_stats(self) -> dict[str, Any]:
        if not self.enabled:
            self.clear_last_error("clear_usage_stats")
            logger.info("Memory usage clear skipped: enabled=%s", self.enabled)
            return {}
        return await self._delete("/v1/usage/stats", operation="clear_usage_stats")

    async def _ingest_scope(
        self,
        *,
        session: str,
        context: str,
        operation: str = "ingest",
    ) -> dict[str, Any]:
        normalized_session = str(session or "").strip()
        normalized_context = str(context or "").strip()
        if not self.enabled or not normalized_session or not normalized_context:
            self.clear_last_error(operation)
            logger.info(
                "Memory ingest skipped: enabled=%s session=%s has_content=%s",
                self.enabled,
                normalized_session,
                bool(normalized_context),
            )
            return {}
        return await self._post(
            "/v1/ingest",
            {
                "session": normalized_session,
                "content": normalized_context,
                "occurred_at": _utc_now_iso(),
            },
            operation=operation,
        )

    async def _recall_scope(
        self,
        *,
        session: str,
        query: str,
        stock_code: str | None = None,
        operation: str = "recall",
    ) -> dict[str, Any]:
        normalized_session = str(session or "").strip()
        normalized_query = str(query or "").strip()
        resolved_stock_code, _memo_session = _resolve_memo_session(stock_code)
        if not self.enabled or not normalized_session or not normalized_query:
            self.clear_last_error(operation)
            logger.info(
                "Memory recall skipped: enabled=%s session=%s has_query=%s",
                self.enabled,
                normalized_session,
                bool(normalized_query),
            )
            return {}
        response = await self._post(
            "/v1/recall",
            {
                "session": normalized_session,
                "query": normalized_query,
            },
            timeout_seconds=max(30.0, settings.MEMORY_SERVICE_TIMEOUT_SECONDS),
            operation=operation,
        )
        if not isinstance(response, dict):
            return {}
        data = self._response_data(response)
        if not data:
            return {}
        data.setdefault("session", normalized_session)
        data.setdefault("stock_code", resolved_stock_code)
        return data

    @staticmethod
    def _response_data(response: dict[str, Any]) -> dict[str, Any]:
        """提取 MemoFlux 标准响应中的 data 对象。

        Args:
            response: HTTP 返回 JSON。

        Returns:
            data 字段对象；若不存在则返回空对象。
        """

        data = response.get("data")
        return data if isinstance(data, dict) else {}

    async def recall(
        self,
        *,
        user_id: int | None,
        stock_code: str | None,
        query: str,
    ) -> dict[str, Any]:
        if not self.enabled or user_id is None or not str(query or "").strip():
            self.clear_last_error("recall")
            logger.info(
                "Memory recall skipped: enabled=%s user_id_present=%s stock_code=%s has_query=%s",
                self.enabled,
                user_id is not None,
                stock_code,
                bool(str(query or "").strip()),
            )
            return {}
        session = _build_user_session(
            user_id=user_id,
            stock_code=stock_code,
        )
        return await self._recall_scope(
            session=session,
            query=query,
            stock_code=stock_code,
            operation="recall",
        )

    async def write_memory(
        self,
        *,
        user_id: int | None,
        stock_code: str | None,
        content: str,
    ) -> dict[str, Any]:
        if not self.enabled or user_id is None or not str(content or "").strip():
            self.clear_last_error("ingest")
            logger.info(
                "Memory write skipped: enabled=%s user_id_present=%s stock_code=%s has_content=%s",
                self.enabled,
                user_id is not None,
                stock_code,
                bool(str(content or "").strip()),
            )
            return {}
        session = _build_user_session(
            user_id=user_id,
            stock_code=stock_code,
        )
        return await self._ingest_scope(
            session=session,
            context=content,
            operation="ingest",
        )

    async def preview_memories(
        self,
        *,
        user_id: int | None = None,
        stock_code: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not self.enabled:
            self.clear_last_error("preview")
            logger.info("Memory preview skipped: enabled=%s", self.enabled)
            return {}
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit), 200)),
            "offset": max(0, int(offset)),
        }
        normalized_stock_code = str(stock_code or "").strip()
        if user_id is not None:
            if normalized_stock_code:
                params["session"] = _build_user_session(
                    user_id=user_id,
                    stock_code=normalized_stock_code,
                )
            else:
                params["session"] = f"user:{user_id}:general"
        return await self._get(
            "/v1/preview",
            params=params,
            operation="preview",
            timeout_seconds=max(30.0, float(settings.MEMORY_SERVICE_TIMEOUT_SECONDS)),
        )

    async def preview_recall_audits(
        self,
        *,
        user_id: int | None = None,
        stock_code: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not self.enabled:
            self.clear_last_error("recall_audit_preview")
            logger.info("Memory recall audit preview skipped: enabled=%s", self.enabled)
            return {}
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit), 200)),
            "offset": max(0, int(offset)),
        }
        normalized_stock_code = str(stock_code or "").strip()
        if user_id is not None:
            if normalized_stock_code:
                params["session"] = _build_user_session(
                    user_id=user_id,
                    stock_code=normalized_stock_code,
                )
            else:
                params["session"] = f"user:{user_id}:general"
        return await self._get(
            "/v1/audits",
            params=params,
            operation="recall_audit_preview",
            timeout_seconds=max(30.0, float(settings.MEMORY_SERVICE_TIMEOUT_SECONDS)),
        )

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        timeout_seconds: float | None = None,
        *,
        operation: str,
    ) -> dict[str, Any]:
        url = f"{settings.MEMORY_SERVICE_BASE_URL.rstrip('/')}{path}"
        started = perf_counter()
        request_id = get_or_create_request_id()
        logger.info(
            "Memory service request started: operation=%s path=%s payload=%s",
            operation,
            path,
            self._summarize_payload(payload),
        )
        try:
            client = self._get_client()
            response = await client.post(
                url,
                json=payload,
                headers={"x-request-id": request_id},
                timeout=timeout_seconds or settings.MEMORY_SERVICE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            logger.info(
                "Memory service request succeeded: operation=%s path=%s status_code=%s elapsed_ms=%s response=%s",
                operation,
                path,
                response.status_code,
                round((perf_counter() - started) * 1000, 2),
                self._summarize_response(body),
            )
            self.clear_last_error(operation)
            return body
        except Exception as exc:
            self._record_error(operation, path, exc)
            logger.warning(
                "Memory service request failed: operation=%s path=%s elapsed_ms=%s error=%s payload=%s",
                operation,
                path,
                round((perf_counter() - started) * 1000, 2),
                exc,
                self._summarize_payload(payload),
            )
            return {}

    async def _get(
        self,
        path: str,
        timeout_seconds: float | None = None,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{settings.MEMORY_SERVICE_BASE_URL.rstrip('/')}{path}"
        started = perf_counter()
        request_id = get_or_create_request_id()
        logger.info("Memory service request started: operation=%s path=%s params=%s", operation, path, params or {})
        try:
            client = self._get_client()
            response = await client.get(
                url,
                params=params,
                headers={"x-request-id": request_id},
                timeout=timeout_seconds or settings.MEMORY_SERVICE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            logger.info(
                "Memory service request succeeded: operation=%s path=%s status_code=%s elapsed_ms=%s response=%s",
                operation,
                path,
                response.status_code,
                round((perf_counter() - started) * 1000, 2),
                self._summarize_response(body),
            )
            self.clear_last_error(operation)
            return body
        except Exception as exc:
            self._record_error(operation, path, exc)
            logger.warning(
                "Memory service request failed: operation=%s path=%s elapsed_ms=%s error=%s",
                operation,
                path,
                round((perf_counter() - started) * 1000, 2),
                exc,
            )
            return {}

    async def _delete(
        self,
        path: str,
        timeout_seconds: float | None = None,
        *,
        operation: str,
    ) -> dict[str, Any]:
        url = f"{settings.MEMORY_SERVICE_BASE_URL.rstrip('/')}{path}"
        started = perf_counter()
        request_id = get_or_create_request_id()
        logger.info("Memory service request started: operation=%s path=%s", operation, path)
        try:
            client = self._get_client()
            response = await client.delete(
                url,
                headers={"x-request-id": request_id},
                timeout=timeout_seconds or settings.MEMORY_SERVICE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            logger.info(
                "Memory service request succeeded: operation=%s path=%s status_code=%s elapsed_ms=%s response=%s",
                operation,
                path,
                response.status_code,
                round((perf_counter() - started) * 1000, 2),
                self._summarize_response(body),
            )
            self.clear_last_error(operation)
            return body
        except Exception as exc:
            self._record_error(operation, path, exc)
            logger.warning(
                "Memory service request failed: operation=%s path=%s elapsed_ms=%s error=%s",
                operation,
                path,
                round((perf_counter() - started) * 1000, 2),
                exc,
            )
            return {}

    def _record_error(self, operation: str, path: str, exc: Exception) -> None:
        """记录最近一次 MemoFlux 调用错误。

        Args:
            operation: 业务操作名称。
            path: MemoFlux API 路径。
            exc: 请求过程中捕获的异常。
        """

        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        if status_code is not None:
            message = f"Memory service HTTP {status_code}: {self._response_error_detail(exc.response)}"
        else:
            message = str(exc).strip() or type(exc).__name__
        error = {
            "operation": operation,
            "path": path,
            "message": message,
            "error_type": type(exc).__name__,
        }
        if status_code is not None:
            error["status_code"] = status_code
        self._last_errors[operation] = error

    @staticmethod
    def _response_error_detail(response: httpx.Response) -> str:
        """提取 HTTP 错误响应中的可读错误详情。

        Args:
            response: MemoFlux 返回的错误响应。

        Returns:
            可用于日志和工具提示的错误说明。
        """

        try:
            body = response.json()
        except ValueError:
            return response.text.strip() or "unexpected response"
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("message") or body.get("error")
            if detail:
                return str(detail)
        return str(body)[:200]

    def _summarize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if "session" in payload:
            summary["session"] = payload.get("session")
        if "query" in payload:
            query_text = str(payload.get("query") or "").replace("\n", " ").strip()
            summary["query_preview"] = query_text[:120]
            summary["query_length"] = len(query_text)
        if "context" in payload:
            summary["context_length"] = len(str(payload.get("context") or ""))
        if "content" in payload:
            summary["content_length"] = len(str(payload.get("content") or ""))
        return summary

    @staticmethod
    def _summarize_response(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            summary: dict[str, Any] = {}
            for key in ("observation_id", "status", "provider", "model", "dimension"):
                value = response.get(key)
                if value is not None:
                    summary[key] = value
            if "answer" in response:
                answer = str(response.get("answer") or "")
                summary["answer_length"] = len(answer)
            data = response.get("data")
            if isinstance(data, dict) and "answer" in data:
                summary["answer_length"] = len(str(data.get("answer") or ""))
            return summary
        if isinstance(response, list):
            return {"item_count": len(response)}
        return {"response_type": type(response).__name__}


memory_client = MemoryServiceClient()
