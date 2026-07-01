import json
import sys
from pathlib import Path

import pytest

from app.ai.agentic.dependency_installer import DependencyInstallError, DependencyInstallResult
from app.ai.agentic.skills_loader import loader
from app.ai.agentic.skills_loader import manager as skills_manager
from app.ai.agentic.skills_loader.loader import discover_skills
from app.ai.agentic.skills_loader.runtime import build_skills_catalog_prompt
from app.ai.agentic.skills_loader.skill_tools import load_skill, read_skill_file, run_skill_script


def _write_skill(root: Path) -> Path:
    skill_dir = root / "tushare-data"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "__pycache__").mkdir()
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "name": "tushare-data",
                "description": "Tushare data research workflows.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        """# tushare-data

Use this skill for Tushare data research.
""",
        encoding="utf-8",
    )
    (skill_dir / "references" / "guide.md").write_text("reference body", encoding="utf-8")
    (skill_dir / "scripts" / "echo_json.py").write_text(
        "import json\n"
        "import sys\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'received': payload, 'argv': sys.argv[1:]}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "echo_io.py").write_text(
        "import json\n"
        "import sys\n"
        "sys.stderr.write('stderr body')\n"
        "print(json.dumps({'stdin': sys.stdin.read(), 'args': sys.argv[1:]}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "echo_shell.sh").write_text(
        "#!/bin/sh\n"
        "printf '{\"arg\":\"%s\",\"stdin\":\"' \"$1\"\n"
        "cat\n"
        "printf '\"}\\n'\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "__pycache__" / "echo_io.cpython-312.pyc").write_bytes(b"cache")
    binary_script = skill_dir / "scripts" / "echo_binary"
    binary_script.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "import sys\n"
        "print(json.dumps({'stdin': sys.stdin.read(), 'args': sys.argv[1:]}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    binary_script.chmod(0o755)
    return skill_dir


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root)
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)
    return root


def test_discover_skills_reads_manifest(skills_root):
    skills = discover_skills()

    assert len(skills) == 1
    assert skills[0].skill_id == "tushare-data"
    assert skills[0].name == "tushare-data"
    assert skills[0].description == "Tushare data research workflows."
    assert skills[0].references == ["references/guide.md"]
    assert skills[0].scripts == [
        "scripts/echo_binary",
        "scripts/echo_io.py",
        "scripts/echo_json.py",
        "scripts/echo_shell.sh",
    ]
    assert skills[0].to_catalog_item() == {
        "skill_id": "tushare-data",
        "name": "tushare-data",
        "description": "Tushare data research workflows.",
        "references": ["references/guide.md"],
        "scripts": [
            "scripts/echo_binary",
            "scripts/echo_io.py",
            "scripts/echo_json.py",
            "scripts/echo_shell.sh",
        ],
    }


def test_discover_skills_skips_missing_required_manifest_field(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    skill_dir = root / "bad-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.json").write_text('{"name": "bad-skill"}', encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("# Body", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)

    assert discover_skills() == []


def test_discover_skills_skips_missing_manifest(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    skill_dir = root / "bad-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Body", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)

    assert discover_skills() == []


@pytest.mark.asyncio
async def test_save_uploaded_skill_writes_folder(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)
    monkeypatch.setattr(skills_manager, "SKILLS_ROOT", root)

    result = await skills_manager.save_uploaded_skill(
        [
            (
                "custom-skill/skill.json",
                json.dumps({"name": "Custom Skill", "description": "Custom workflow"}).encode("utf-8"),
            ),
            ("custom-skill/SKILL.md", b"# Custom Skill\n"),
            ("custom-skill/references/guide.md", b"reference body"),
            ("custom-skill/scripts/run.py", b"print('ok')\n"),
        ]
    )

    assert result["status"] == "success"
    assert result["skill_id"] == "custom-skill"
    assert (root / "custom-skill" / "skill.json").exists()
    assert discover_skills()[0].skill_id == "custom-skill"


@pytest.mark.asyncio
async def test_save_uploaded_skill_requires_root_skill_json(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)
    monkeypatch.setattr(skills_manager, "SKILLS_ROOT", root)

    with pytest.raises(ValueError, match="skill.json"):
        await skills_manager.save_uploaded_skill(
            [
                ("custom-skill/SKILL.md", b"# Custom Skill\n"),
                ("custom-skill/references/skill.json", b'{"name": "nested"}'),
            ]
        )


@pytest.mark.asyncio
async def test_save_uploaded_skill_installs_root_requirements(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    captured = {}
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)
    monkeypatch.setattr(skills_manager, "SKILLS_ROOT", root)

    async def fake_install(requirements_text, *, component):
        captured["requirements_text"] = requirements_text
        captured["component"] = component
        return DependencyInstallResult(
            status="success",
            requirements=["beautifulsoup4"],
            command=[sys.executable, "-m", "pip", "install", "--user"],
        )

    monkeypatch.setattr(skills_manager, "install_python_requirements", fake_install)

    result = await skills_manager.save_uploaded_skill(
        [
            (
                "custom-skill/skill.json",
                json.dumps({"name": "Custom Skill", "description": "Custom workflow"}).encode("utf-8"),
            ),
            ("custom-skill/SKILL.md", b"# Custom Skill\n"),
            ("custom-skill/requirements.txt", b"beautifulsoup4\n"),
        ]
    )

    assert result["status"] == "success"
    assert result["dependencies"]["status"] == "success"
    assert captured == {
        "requirements_text": "beautifulsoup4\n",
        "component": "skill:custom-skill",
    }
    assert (root / "custom-skill" / "requirements.txt").exists()


@pytest.mark.asyncio
async def test_save_uploaded_skill_stops_when_dependency_install_fails(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)
    monkeypatch.setattr(skills_manager, "SKILLS_ROOT", root)

    async def fail_install(requirements_text, *, component):
        result = DependencyInstallResult(
            status="error",
            requirements=[requirements_text],
            command=[sys.executable, "-m", "pip", "install", "--user"],
            exit_code=1,
            stderr="install failed",
        )
        raise DependencyInstallError("install failed", result)

    monkeypatch.setattr(skills_manager, "install_python_requirements", fail_install)

    result = await skills_manager.save_uploaded_skill(
        [
            (
                "custom-skill/skill.json",
                json.dumps({"name": "Custom Skill", "description": "Custom workflow"}).encode("utf-8"),
            ),
            ("custom-skill/SKILL.md", b"# Custom Skill\n"),
            ("custom-skill/requirements.txt", b"missing-package-for-test\n"),
        ]
    )

    assert result["status"] == "error"
    assert result["dependencies"]["status"] == "error"
    assert not (root / "custom-skill").exists()


def test_delete_managed_skill_removes_folder(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    _write_skill(root)
    monkeypatch.setattr(loader, "SKILLS_ROOT", root)
    monkeypatch.setattr(skills_manager, "SKILLS_ROOT", root)

    result = skills_manager.delete_managed_skill("tushare-data")

    assert result["status"] == "success"
    assert not (root / "tushare-data").exists()


def test_catalog_prompt_lists_available_skills(skills_root):
    prompt = build_skills_catalog_prompt()

    assert "Available External Skills" in prompt
    assert "tushare-data" in prompt
    assert "load_skill" in prompt
    assert "fill the evidence gap" in prompt


def test_load_skill_returns_full_markdown(skills_root):
    result = load_skill.invoke({"skill_id": "tushare-data"})

    assert result["success"] is True
    assert "# tushare-data" in result["content"]


def test_read_skill_file_rejects_path_escape(skills_root):
    result = read_skill_file.invoke({"skill_id": "tushare-data", "relative_path": "../secret.txt"})

    assert result["success"] is False
    assert "escapes" in result["error"]


def test_read_skill_file_reads_reference(skills_root):
    result = read_skill_file.invoke({"skill_id": "tushare-data", "relative_path": "references/guide.md"})

    assert result["success"] is True
    assert result["content"] == "reference body"


@pytest.mark.asyncio
async def test_run_skill_script_executes_python_script(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "scripts/echo_json.py", "alpha"],
            "stdin": json.dumps({"code": "600519.SH"}, ensure_ascii=False),
        }
    )

    assert result["success"] is True
    payload = json.loads(result["stdout"])
    assert payload["received"] == {"code": "600519.SH"}
    assert payload["argv"] == ["alpha"]


@pytest.mark.asyncio
async def test_run_skill_script_supports_args_stdin_and_stderr(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "scripts/echo_io.py", "--code", "600519.SH"],
            "stdin": "plain stdin body",
        }
    )

    assert result["success"] is True
    assert result["stderr"] == "stderr body"
    payload = json.loads(result["stdout"])
    assert payload["stdin"] == "plain stdin body"
    assert payload["args"] == ["--code", "600519.SH"]


@pytest.mark.asyncio
async def test_run_skill_script_executes_explicit_command(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": ["scripts/echo_binary", "--code", "600519.SH"],
            "stdin": "plain stdin body",
        }
    )

    assert result["success"] is True
    payload = json.loads(result["stdout"])
    assert payload["stdin"] == "plain stdin body"
    assert payload["args"] == ["--code", "600519.SH"]


@pytest.mark.asyncio
async def test_run_skill_script_allows_shell_to_execute_skill_script(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": ["sh", "scripts/echo_shell.sh", "alpha"],
            "stdin": "plain stdin body",
        }
    )

    assert result["success"] is True
    payload = json.loads(result["stdout"])
    assert payload["arg"] == "alpha"
    assert payload["stdin"] == "plain stdin body"


@pytest.mark.asyncio
async def test_run_skill_script_uses_whitelisted_environment(skills_root, monkeypatch):
    script = skills_root / "tushare-data" / "scripts" / "env_probe.py"
    script.write_text(
        "import json\n"
        "import os\n"
        "keys = ['PATH', 'LANG', 'TUSHARE_API', 'TUSHARE_TOKEN', 'SECRET_KEY', 'LLM_API_KEY', 'TAVILY_API_KEY']\n"
        "print(json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("TUSHARE_API", "https://env.example.invalid")
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
    monkeypatch.setenv("SECRET_KEY", "backend-secret")
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")

    def _fake_data_source_config(key: str) -> str:
        values = {
            "data_sources.tushare.api_url": "https://db.example.invalid",
            "data_sources.tushare.token": "db-token",
        }
        return values.get(key, "")

    monkeypatch.setattr(
        "app.ai.agentic.skills_loader.skill_tools.get_data_source_config_value",
        _fake_data_source_config,
    )

    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "scripts/env_probe.py"],
        }
    )

    assert result["success"] is True
    payload = json.loads(result["stdout"])
    assert payload["PATH"] == "/usr/bin"
    assert payload["LANG"] == "C.UTF-8"
    assert payload["TUSHARE_API"] == "https://db.example.invalid"
    assert payload["TUSHARE_TOKEN"] == "db-token"
    assert payload["SECRET_KEY"] is None
    assert payload["LLM_API_KEY"] is None
    assert payload["TAVILY_API_KEY"] is None


@pytest.mark.asyncio
async def test_run_skill_script_rejects_command_without_skill_script(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "-c", "print('outside skill')"],
        }
    )

    assert result["success"] is False
    assert "scripts" in result["error"]


@pytest.mark.asyncio
async def test_run_skill_script_rejects_late_script_argument_bypass(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "-c", "print('outside skill')", "scripts/echo_json.py"],
        }
    )

    assert result["success"] is False
    assert "python commands must execute" in result["error"]


@pytest.mark.asyncio
async def test_run_skill_script_rejects_python_module_mode(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "-m", "json.tool", "scripts/echo_json.py"],
        }
    )

    assert result["success"] is False
    assert "python commands must execute" in result["error"]


@pytest.mark.asyncio
async def test_run_skill_script_rejects_shell_command_with_script_argument(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": ["sh", "-c", "echo outside", "scripts/echo_json.py"],
        }
    )

    assert result["success"] is False
    assert "shell commands must execute" in result["error"]


@pytest.mark.asyncio
async def test_run_skill_script_rejects_command_injection_characters(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "scripts/echo_json.py", "alpha;uname"],
        }
    )

    assert result["success"] is False
    assert "shell metacharacters" in result["error"]


@pytest.mark.asyncio
async def test_run_skill_script_rejects_command_substitution_characters(skills_root):
    result = await run_skill_script.ainvoke(
        {
            "skill_id": "tushare-data",
            "command": [sys.executable, "scripts/echo_json.py", "$(uname)"],
        }
    )

    assert result["success"] is False
    assert "shell metacharacters" in result["error"]


@pytest.mark.asyncio
async def test_run_skill_script_rejects_non_scripts_path(skills_root):
    result = await run_skill_script.ainvoke(
        {"skill_id": "tushare-data", "command": [sys.executable, "SKILL.md"]}
    )

    assert result["success"] is False
    assert "scripts" in result["error"]
