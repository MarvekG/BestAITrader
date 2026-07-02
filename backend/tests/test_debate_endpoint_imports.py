import subprocess
import sys
from pathlib import Path


def test_api_package_import_does_not_load_endpoint_modules() -> None:
    """导入 API 包时不应提前加载具体业务路由模块。"""
    script = (
        "import sys; "
        "import app.api; "
        "print(('app.api.endpoints.debate' in sys.modules), "
        "('app.ai.experience.api' in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )

    assert result.stdout.strip().splitlines()[-1] == "False False"


def test_main_module_import_does_not_build_full_api_routes() -> None:
    """导入 app.main 模块时不应构建完整 API 路由。"""
    script = (
        "import sys; "
        "import app.main; "
        "print(('app.api.endpoints.debate' in sys.modules), "
        "('app.ai.experience.api' in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )

    assert result.stdout.strip().splitlines()[-1] == "False False"


def test_main_module_import_does_not_load_database_or_security_layers() -> None:
    """导入 app.main 模块时不应提前加载数据库和鉴权层。"""
    script = (
        "import sys; "
        "import app.main; "
        "print(('app.core.database' in sys.modules), "
        "('app.core.security' in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )

    assert result.stdout.strip().splitlines()[-1] == "False False"
