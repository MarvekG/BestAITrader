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
}
FORBIDDEN_SQLALCHEMY_ORM_IMPORTS = {"Session", "sessionmaker"}
FORBIDDEN_DBAPI_MODULES = {"psycopg2", "sqlite3"}
FORBIDDEN_PANDAS_CALLS = {"read_sql", "read_sql_query", "read_sql_table"}
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


def _is_call_to(function_name: str | None, aliases: set[str], names: set[str]) -> bool:
    if not function_name:
        return False
    if function_name in aliases:
        return True
    return any(function_name == f"{alias}.{name}" for alias in aliases for name in names)


def _ast_offenders(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    relative_path = str(path.relative_to(REPO_ROOT))
    offenders = []
    sqlalchemy_aliases = {"sqlalchemy"}
    orm_aliases: set[str] = set()
    sync_create_engine_aliases = {"create_engine"}
    sync_sessionmaker_aliases = {"sessionmaker"}
    dbapi_aliases: dict[str, str] = {}
    dbapi_connect_aliases: set[str] = set()
    pandas_aliases = {"pandas"}
    pandas_sql_aliases = set(FORBIDDEN_PANDAS_CALLS)
    sync_engine_names: set[str] = set()

    nodes = list(ast.walk(tree))

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                if alias.name == "sqlalchemy":
                    sqlalchemy_aliases.add(local_name)
                if alias.name == "sqlalchemy.orm":
                    orm_aliases.add(local_name)
                if alias.name in FORBIDDEN_DBAPI_MODULES:
                    dbapi_aliases[local_name] = alias.name
                if alias.name == "pandas":
                    pandas_aliases.add(local_name)

        if isinstance(node, ast.ImportFrom):
            imported_names = {alias.name for alias in node.names}
            imported_local_names = {alias.asname or alias.name for alias in node.names}
            if node.module == "sqlalchemy":
                for alias in node.names:
                    if alias.name == "create_engine":
                        sync_create_engine_aliases.add(alias.asname or alias.name)
            if node.module == "sqlalchemy.orm":
                for name in sorted(imported_names & FORBIDDEN_SQLALCHEMY_ORM_IMPORTS):
                    offenders.append((node.lineno, f"from sqlalchemy.orm import {name}"))
                for alias in node.names:
                    if alias.name == "sessionmaker":
                        sync_sessionmaker_aliases.add(alias.asname or alias.name)
            if node.module in FORBIDDEN_DBAPI_MODULES:
                for alias in node.names:
                    if alias.name == "connect":
                        dbapi_connect_aliases.add(alias.asname or alias.name)
            if node.module == "pandas":
                pandas_sql_aliases.update(imported_local_names & FORBIDDEN_PANDAS_CALLS)
            if node.module == "app.core.database" and "AsyncSessionLocal" in imported_names:
                offenders.append((node.lineno, "direct AsyncSessionLocal import"))

        if isinstance(node, ast.Assign):
            value_function_name = _attribute_name(node.value.func) if isinstance(node.value, ast.Call) else None
            if isinstance(node.value, ast.Call) and _is_call_to(
                value_function_name,
                sqlalchemy_aliases | orm_aliases | sync_create_engine_aliases,
                {"create_engine"},
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        sync_engine_names.add(target.id)

        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAME_REFERENCES:
            offenders.append((node.lineno, node.id))

        if isinstance(node, ast.Call):
            function_name = _attribute_name(node.func)
            if _is_call_to(
                function_name,
                sqlalchemy_aliases | sync_create_engine_aliases,
                {"create_engine"},
            ):
                offenders.append((node.lineno, "create_engine"))
            if _is_call_to(function_name, orm_aliases | sync_sessionmaker_aliases, {"sessionmaker"}):
                offenders.append((node.lineno, "sessionmaker"))
            if _is_call_to(function_name, set(dbapi_aliases) | dbapi_connect_aliases, {"connect"}):
                offenders.append((node.lineno, "dbapi connect"))
            if _is_call_to(function_name, pandas_aliases | pandas_sql_aliases, FORBIDDEN_PANDAS_CALLS):
                offenders.append((node.lineno, "pandas read_sql"))
            if isinstance(node.func, ast.Attribute) and node.func.attr == "to_sql":
                offenders.append((node.lineno, "pandas to_sql"))
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"begin", "connect"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in sync_engine_names
            ):
                offenders.append((node.lineno, f"sync engine.{node.func.attr}()"))
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
