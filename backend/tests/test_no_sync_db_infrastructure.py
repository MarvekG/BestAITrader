import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "backend" / "scripts",
    REPO_ROOT / "backend" / "tests",
]

TEXT_PATTERNS = [
    ("SessionLocal", re.compile(r"\bSessionLocal\b")),
    ("get_db", re.compile(r"\bget_db\b")),
    ("get_db_session", re.compile(r"\bget_db_session\b")),
    ("create_engine", re.compile(r"\bcreate_engine\b")),
    (
        "from sqlalchemy.orm import Session",
        re.compile(r"^\s*from\s+sqlalchemy\.orm\s+import\s+.*\bSession\b", re.MULTILINE),
    ),
    (
        "from sqlalchemy.orm import sessionmaker",
        re.compile(r"^\s*from\s+sqlalchemy\.orm\s+import\s+.*\bsessionmaker\b", re.MULTILINE),
    ),
    ("sqlalchemy.orm.sessionmaker", re.compile(r"\bsqlalchemy\.orm\.sessionmaker\b")),
    ("sessionmaker", re.compile(r"\bsessionmaker\s*\(")),
    (
        "direct AsyncSessionLocal import",
        re.compile(r"^\s*from\s+app\.core\.database\s+import\s+.*\bAsyncSessionLocal\b", re.MULTILINE),
    ),
]
DB_QUERY_RECEIVER_NAMES = {"db", "db_session", "session"}

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


def _db_query_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "query":
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id in DB_QUERY_RECEIVER_NAMES:
            lines.append(node.lineno)

    return lines


def test_sync_db_infrastructure_does_not_return() -> None:
    offenders = []

    for path in _python_files():
        relative_path = path.relative_to(REPO_ROOT)
        text = path.read_text(encoding="utf-8")
        for pattern_name, pattern in TEXT_PATTERNS:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{relative_path}:{line_no}: {pattern_name}")

        for line_no in _db_query_lines(path):
            offenders.append(f"{relative_path}:{line_no}: db.query")

    assert offenders == []
