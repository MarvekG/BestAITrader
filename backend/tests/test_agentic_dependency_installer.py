import sys
from unittest.mock import AsyncMock

import pytest

from app.ai.agentic import dependency_installer
from app.ai.agentic.dependency_installer import (
    DependencyInstallError,
    install_python_requirements,
    normalize_requirements_text,
)


def test_normalize_requirements_text_keeps_requirements_file_lines():
    requirements = normalize_requirements_text(
        """
        # parser dependencies
        beautifulsoup4
        feedparser  # rss parser
        httpx[http2]; python_version >= "3.12"
        pkg --find-links /tmp/wheels
        """
    )

    assert requirements == [
        "beautifulsoup4",
        "feedparser",
        'httpx[http2]; python_version >= "3.12"',
        "pkg --find-links /tmp/wheels",
    ]


def test_normalize_requirements_text_rejects_too_long_lines():
    with pytest.raises(ValueError):
        normalize_requirements_text("a" * 301)


@pytest.mark.asyncio
async def test_install_python_requirements_uses_runtime_extensions_switch(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_RUNTIME_EXTENSIONS", False)

    with pytest.raises(DependencyInstallError) as exc_info:
        await install_python_requirements("example-package==1.0.0", component="test")

    assert "Runtime extension installation is disabled" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_pip_install_uses_unprivileged_user_install(monkeypatch, tmp_path):
    requirements_file = tmp_path / "requirements.txt"
    requirements_file.write_text("example-package==1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(dependency_installer, "_write_temporary_requirements", lambda requirements: requirements_file)

    process = AsyncMock()
    process.communicate.return_value = (b"installed", b"")
    process.returncode = 0
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(dependency_installer.asyncio, "create_subprocess_exec", create_process)

    result = await dependency_installer._run_pip_install(["example-package==1.0.0"], component="test")

    command = list(create_process.await_args.args)
    assert result.status == "success"
    assert command[0] == sys.executable
    assert "sudo" not in command
    assert "--user" in command
