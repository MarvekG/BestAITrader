import sys
from pathlib import Path

import pytest

from app.ai.agentic.dependency_installer import DependencyInstallError, DependencyInstallResult
from app.ai.agentic.tooling.news_plugins import manager
from app.ai.agentic.tooling.news_plugins.manager import NewsPluginCreateRequest
from app.ai.agentic.tooling.news_plugins.registry import NewsPlugin
from app.ai.agentic.tooling.news_tool import _compact_news_result, _search_news_impl


class _FakePluginRegistry:
    def __init__(self, external_dir: Path):
        self.external_dir = external_dir
        self.cache_cleared = False
        self.search_kwargs = None

    def __call__(self):
        plugin_path = self.external_dir / "custom_news.py"
        if not plugin_path.exists():
            return {}

        async def search(**kwargs):
            self.search_kwargs = kwargs
            return [{"title": "AI market news"}]

        return {
            "custom_source": NewsPlugin(
                name="Custom News",
                plugin_id="custom_source",
                tool_name="Custom News Tool",
                news_types=("market",),
                keyword_examples=("AI",),
                search=search,
                module_name="app.ai.agentic.tooling.news_plugins.external.custom_news",
            )
        }

    def cache_clear(self):
        self.cache_cleared = True


@pytest.fixture
def isolated_news_plugin_manager(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "news_plugins"
    external_dir = plugin_dir / "external"
    registry = _FakePluginRegistry(external_dir)

    monkeypatch.setattr(manager, "NEWS_PLUGIN_DIR", plugin_dir)
    monkeypatch.setattr(manager, "NEWS_PLUGIN_EXTERNAL_DIR", external_dir)
    monkeypatch.setattr(manager, "get_news_plugins", registry)
    yield external_dir, registry


@pytest.mark.asyncio
async def test_create_news_plugin_writes_file_and_probes_search(isolated_news_plugin_manager):
    external_dir, registry = isolated_news_plugin_manager
    request = NewsPluginCreateRequest(
        module_name="custom_news.py",
        content=(
            'NAME = "Custom News"\n'
            'PLUGIN_ID = "custom_source"\n'
            'TOOL_NAME = "Custom News Tool"\n'
            'NEWS_TYPES = ["market"]\n'
            'KEYWORD_EXAMPLES = ["AI"]\n'
            "def search(**kwargs):\n"
            "    return []\n"
        ),
    )

    result = await manager.create_news_plugin(request)

    assert result["status"] == "success"
    assert result["source"] == "custom_source"
    assert result["probe"]["status"] == "success"
    assert registry.search_kwargs["keyword"] == "AI"
    assert registry.search_kwargs["limit"] == 3
    assert registry.search_kwargs["from_date"]
    assert registry.search_kwargs["to_date"]
    assert (external_dir / "custom_news.py").exists()
    assert registry.cache_cleared is True


@pytest.mark.asyncio
async def test_create_news_plugin_rolls_back_when_probe_fails(isolated_news_plugin_manager, monkeypatch):
    external_dir, _ = isolated_news_plugin_manager
    request = NewsPluginCreateRequest(
        module_name="custom_news",
        content=(
            'NAME = "Custom News"\n'
            'PLUGIN_ID = "custom_source"\n'
            'TOOL_NAME = "Custom News Tool"\n'
            'NEWS_TYPES = ["market"]\n'
            'KEYWORD_EXAMPLES = ["AI"]\n'
            "def search(**kwargs):\n"
            "    return []\n"
        ),
    )

    async def fail_probe(_plugin):
        return {"status": "error", "message": "probe failed"}

    monkeypatch.setattr(manager, "probe_news_plugin_search", fail_probe)

    result = await manager.create_news_plugin(request)

    assert result["status"] == "error"
    assert not (external_dir / "custom_news.py").exists()


def test_delete_news_plugin_removes_external_file(isolated_news_plugin_manager):
    external_dir, registry = isolated_news_plugin_manager
    external_dir.mkdir(parents=True)
    (external_dir / "custom_news.py").write_text("NAME = 'Custom News'\n", encoding="utf-8")

    result = manager.delete_news_plugin("custom_source")

    assert result["status"] == "success"
    assert not (external_dir / "custom_news.py").exists()
    assert registry.cache_cleared is True


def test_list_news_plugins_marks_external_plugins_deletable(isolated_news_plugin_manager):
    external_dir, _ = isolated_news_plugin_manager
    external_dir.mkdir(parents=True)
    (external_dir / "custom_news.py").write_text("NAME = 'Custom News'\n", encoding="utf-8")

    result = manager.list_news_plugins()

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["items"][0]["plugin_id"] == "custom_source"
    assert result["items"][0]["can_delete"] is True


def test_validate_news_plugin_content_rejects_invalid_python():
    with pytest.raises(ValueError):
        manager.validate_news_plugin_content("def broken(:\n    pass\n")


def test_extract_news_plugin_requirements_from_constant():
    requirements = manager.extract_news_plugin_requirements(
        "PYTHON_REQUIREMENTS = ['beautifulsoup4', 'feedparser']\n"
    )

    assert requirements.splitlines() == ["beautifulsoup4", "feedparser"]


def test_extract_news_plugin_requirements_from_annotated_constant():
    requirements = manager.extract_news_plugin_requirements(
        "PYTHON_REQUIREMENTS: list[str] = ['beautifulsoup4', 'feedparser']\n"
    )

    assert requirements.splitlines() == ["beautifulsoup4", "feedparser"]


@pytest.mark.asyncio
async def test_search_news_deduplicates_and_caps_compacted_results(monkeypatch):
    rows = []
    for index in range(25):
        rows.append({
            "title": f"重复标题 {index}",
            "content": "正文" * 1000,
            "url": f"https://example.com/news/{index}",
            "published_at": "2026-05-20",
            "publisher": "Example",
            "extra": f"kept-{index}",
        })
    rows.extend([
        {
            "title": "重复标题 0",
            "content": "重复标题正文",
            "url": "https://example.com/another-url",
            "published_at": "2026-05-20",
            "publisher": "Example",
        },
        {
            "title": "URL 重复",
            "content": "URL 重复正文",
            "url": "https://example.com/news/1",
            "published_at": "2026-05-20",
            "publisher": "Example",
        },
    ])

    async def fake_invoke_news_plugin(**kwargs):
        assert kwargs["limit"] == 20
        return rows

    monkeypatch.setattr("app.ai.agentic.tooling.news_tool.invoke_news_plugin", fake_invoke_news_plugin)

    result = await _search_news_impl(
        keyword="贵州茅台 业绩",
        source="fake",
        limit=999,
        from_date="2026-05-01",
        to_date="2026-05-20",
    )

    assert len(result) == 20
    assert len({item["url"] for item in result}) == 20
    assert len({item["title"] for item in result}) == 20
    assert result[0]["extra"] == "kept-0"
    assert result[0]["content"] == "正文" * 1000


def test_compact_news_result_preserves_error_payload():
    result = _compact_news_result([{"error": "source failed", "source": "fake"}])

    assert result == [{"error": "source failed", "source": "fake"}]


@pytest.mark.asyncio
async def test_create_news_plugin_installs_declared_requirements(isolated_news_plugin_manager, monkeypatch):
    external_dir, _ = isolated_news_plugin_manager
    captured = {}

    async def fake_install(requirements_text, *, component):
        captured["requirements_text"] = requirements_text
        captured["component"] = component
        return DependencyInstallResult(
            status="success",
            requirements=["beautifulsoup4"],
            command=[sys.executable, "-m", "pip", "install", "--user"],
        )

    monkeypatch.setattr(manager, "install_python_requirements", fake_install)
    request = NewsPluginCreateRequest(
        module_name="custom_news",
        content=(
            "PYTHON_REQUIREMENTS: list[str] = ['beautifulsoup4']\n"
            'NAME = "Custom News"\n'
            'PLUGIN_ID = "custom_source"\n'
            'TOOL_NAME = "Custom News Tool"\n'
            'NEWS_TYPES = ["market"]\n'
            'KEYWORD_EXAMPLES = ["AI"]\n'
            "def search(**kwargs):\n"
            "    return []\n"
        ),
    )

    result = await manager.create_news_plugin(request)

    assert result["status"] == "success"
    assert result["dependencies"]["status"] == "success"
    assert captured == {
        "requirements_text": "beautifulsoup4",
        "component": "news_plugin:custom_news",
    }
    assert (external_dir / "custom_news.py").exists()


@pytest.mark.asyncio
async def test_create_news_plugin_stops_when_dependency_install_fails(isolated_news_plugin_manager, monkeypatch):
    external_dir, _ = isolated_news_plugin_manager

    async def fail_install(requirements_text, *, component):
        result = DependencyInstallResult(
            status="error",
            requirements=[requirements_text],
            command=[sys.executable, "-m", "pip", "install", "--user"],
            exit_code=1,
            stderr="install failed",
        )
        raise DependencyInstallError("install failed", result)

    monkeypatch.setattr(manager, "install_python_requirements", fail_install)
    request = NewsPluginCreateRequest(
        module_name="custom_news",
        content=(
            "PYTHON_REQUIREMENTS = ['missing-package-for-test']\n"
            'NAME = "Custom News"\n'
            'PLUGIN_ID = "custom_source"\n'
            'TOOL_NAME = "Custom News Tool"\n'
            'NEWS_TYPES = ["market"]\n'
            'KEYWORD_EXAMPLES = ["AI"]\n'
            "def search(**kwargs):\n"
            "    return []\n"
        ),
    )

    result = await manager.create_news_plugin(request)

    assert result["status"] == "error"
    assert result["dependencies"]["status"] == "error"
    assert not (external_dir / "custom_news.py").exists()
