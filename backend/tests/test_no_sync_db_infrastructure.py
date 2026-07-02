import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "backend" / "scripts",
    REPO_ROOT / "backend" / "tests",
]

FORBIDDEN_NAME_REFERENCES = {
    "SessionLocal",
    "get_db",
    "get_db_session",
    "create_engine",
}
FORBIDDEN_SQLALCHEMY_ORM_IMPORTS = {"Session", "sessionmaker"}
QUERY_CALL_ALLOWLIST = {
    # Tushare SDK exposes a non-database pro_client.query(interface, **params) fallback.
    ("backend/app/ai/agentic/skills_loader/skills/tushare-data/scripts/call_tushare.py", 139),
}

ALLOWLIST = {
    # This guard has to mention the forbidden names it is looking for.
    "backend/tests/test_no_sync_db_infrastructure.py",
}


def _python_files() -> list[Path]:
    return [
        path
        for source_root in SOURCE_ROOTS
        for path in source_root.rglob("*.py")
        if str(path.relative_to(REPO_ROOT)) not in ALLOWLIST
    ]


def _attribute_name(node: ast.AST) -> str | None:
    """把 ast.Attribute/Name 转成点分名称。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _ast_offenders(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    relative_path = str(path.relative_to(REPO_ROOT))
    offenders = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names = {alias.name for alias in node.names}
            if node.module == "sqlalchemy.orm":
                for name in sorted(imported_names & FORBIDDEN_SQLALCHEMY_ORM_IMPORTS):
                    offenders.append((node.lineno, f"from sqlalchemy.orm import {name}"))
            if node.module == "app.core.database" and "AsyncSessionLocal" in imported_names:
                offenders.append((node.lineno, "direct AsyncSessionLocal import"))

        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAME_REFERENCES:
            offenders.append((node.lineno, node.id))

        if isinstance(node, ast.Call):
            function_name = _attribute_name(node.func)
            if function_name and (function_name == "sessionmaker" or function_name.endswith(".sessionmaker")):
                offenders.append((node.lineno, "sessionmaker"))
            if isinstance(node.func, ast.Attribute) and node.func.attr == "query":
                if (relative_path, node.lineno) not in QUERY_CALL_ALLOWLIST:
                    offenders.append((node.lineno, ".query()"))

    return offenders


def test_sync_db_infrastructure_does_not_return() -> None:
    offenders = []

    for path in _python_files():
        relative_path = path.relative_to(REPO_ROOT)
        for line_no, pattern_name in _ast_offenders(path):
            offenders.append(f"{relative_path}:{line_no}: {pattern_name}")

    assert offenders == []
