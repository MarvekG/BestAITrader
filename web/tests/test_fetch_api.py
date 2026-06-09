from fastapi.testclient import TestClient

from app.engines.base import RenderedPage
from app.main import app, engine_registry
from app.schemas import EngineType


class FakeEngine:
    """测试用网页渲染引擎。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def render(
        self,
        url: str,
        selectors: list[str],
        timeout_ms: int,
        wait_after_ms: int,
    ) -> RenderedPage:
        """
        返回固定渲染结果并记录调用参数。

        Args:
            url: 已规范化 URL。
            selectors: CSS selector 列表。
            timeout_ms: 页面导航超时时间。
            wait_after_ms: 导航后等待时间。

        Returns:
            固定渲染页面。
        """
        self.calls.append(
            {
                "url": url,
                "selectors": selectors,
                "timeout_ms": timeout_ms,
                "wait_after_ms": wait_after_ms,
            }
        )
        return RenderedPage(
            final_url="https://example.com/final",
            status=200,
            title="Example",
            html="<main><h1>Example</h1><p>正文</p><p>广告内容结束</p></main>",
            selected_element_count=len(selectors) if selectors else None,
        )

    async def close(self) -> None:
        """关闭测试引擎。"""


def test_fetch_returns_markdown_and_applies_clean_regex() -> None:
    fake_engine = FakeEngine()
    original_engine = engine_registry._engines[EngineType.CLOAKBROWSER]
    engine_registry._engines[EngineType.CLOAKBROWSER] = fake_engine
    try:
        with TestClient(app) as client:
            response = client.post(
                "/fetch",
                json={
                    "url": "example.com/page",
                    "selectors": [" main "],
                    "markdown_clean_regexes": ["广告.*?结束"],
                    "engine": "cloakbrowser",
                    "return_type": "markdown",
                    "timeout_ms": 10_000,
                    "wait_after_ms": 0,
                },
            )
    finally:
        engine_registry._engines[EngineType.CLOAKBROWSER] = original_engine

    payload = response.json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["url"] == "https://example.com/page"
    assert payload["final_url"] == "https://example.com/final"
    assert payload["return_type"] == "markdown"
    assert payload["selectors"] == ["main"]
    assert "Source URL: https://example.com/final" in payload["content"]
    assert "广告" not in payload["content"]
    assert fake_engine.calls == [
        {
            "url": "https://example.com/page",
            "selectors": ["main"],
            "timeout_ms": 10_000,
            "wait_after_ms": 0,
        }
    ]


def test_fetch_returns_html() -> None:
    fake_engine = FakeEngine()
    original_engine = engine_registry._engines[EngineType.PATCHRIGHT]
    engine_registry._engines[EngineType.PATCHRIGHT] = fake_engine
    try:
        with TestClient(app) as client:
            response = client.post(
                "/fetch",
                json={
                    "url": "https://example.com/page",
                    "engine": "patchright",
                    "return_type": "html",
                    "wait_after_ms": 0,
                },
            )
    finally:
        engine_registry._engines[EngineType.PATCHRIGHT] = original_engine

    payload = response.json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["content_source"] == "rendered_dom_html"
    assert payload["content"] == "<main><h1>Example</h1><p>正文</p><p>广告内容结束</p></main>"


def test_fetch_rejects_invalid_regex() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/fetch",
            json={
                "url": "https://example.com/page",
                "return_type": "markdown",
                "markdown_clean_regexes": ["["],
            },
        )

    assert response.status_code == 400
    assert "invalid markdown_clean_regexes" in response.json()["detail"]


def test_fetch_rejects_unknown_engine() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/fetch",
            json={
                "url": "https://example.com/page",
                "engine": "unknown",
            },
        )

    assert response.status_code == 422


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
