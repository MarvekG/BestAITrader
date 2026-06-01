from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time

from sqlalchemy.engine import make_url

from app.core.config import PROJECT_ROOT, settings
from app.core.logger import get_logger


logger = get_logger(__name__)


@dataclass(slots=True)
class PostgresCommandConfig:
    host: str
    port: str
    username: str
    password: str
    database: str


def _postgres_config(database_url: str | None = None) -> PostgresCommandConfig:
    url = make_url(database_url or settings.DATABASE_URL)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("Database backup/import currently supports PostgreSQL only.")
    if not url.database:
        raise ValueError("DATABASE_URL is missing the database name.")

    return PostgresCommandConfig(
        host=url.host or "localhost",
        port=str(url.port or 5432),
        username=url.username or "postgres",
        password=url.password or "",
        database=url.database,
    )


def _postgres_env(config: PostgresCommandConfig) -> dict[str, str]:
    env = os.environ.copy()
    if config.password:
        env["PGPASSWORD"] = config.password
    return env


def _ensure_command(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"Required command '{name}' was not found in PATH.")
    return executable


def _safe_command_log(command: list[str], password: str) -> list[str]:
    if not password:
        return command
    return [part.replace(password, "***") for part in command]


def create_database_backup(database_url: str | None = None) -> tuple[Path, str]:
    config = _postgres_config(database_url)
    pg_dump = _ensure_command("pg_dump")

    fd, temp_path = tempfile.mkstemp(prefix="best-ai-trader-backup-", suffix=".dump")
    os.close(fd)
    backup_path = Path(temp_path)
    download_name = f"best-ai-trader-backup-{datetime.now():%Y%m%d-%H%M%S}.dump"

    command = [
        pg_dump,
        "--host",
        config.host,
        "--port",
        config.port,
        "--username",
        config.username,
        "--dbname",
        config.database,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(backup_path),
    ]
    started_at = time.perf_counter()
    logger.info(
        "Starting database backup",
        extra={
            "operation": "database_backup",
            "host": config.host,
            "port": config.port,
            "database": config.database,
            "username": config.username,
            "download_name": download_name,
            "backup_path": str(backup_path),
            "command": _safe_command_log(command, config.password),
        },
    )
    result = subprocess.run(
        command,
        env=_postgres_env(config),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        backup_path.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "pg_dump failed").strip()
        logger.error(
            "Database backup failed",
            extra={
                "operation": "database_backup",
                "returncode": result.returncode,
                "stderr": (result.stderr or "")[-4000:],
                "stdout": (result.stdout or "")[-4000:],
                "duration_seconds": round(time.perf_counter() - started_at, 3),
            },
        )
        raise RuntimeError(detail)

    logger.info(
        "Database backup completed",
        extra={
            "operation": "database_backup",
            "backup_path": str(backup_path),
            "download_name": download_name,
            "size_bytes": backup_path.stat().st_size,
            "duration_seconds": round(time.perf_counter() - started_at, 3),
        },
    )
    return backup_path, download_name


def restore_database_backup(backup_path: Path, database_url: str | None = None) -> None:
    config = _postgres_config(database_url)
    executable = _ensure_command("pg_restore")
    command = [
        executable,
        "--host",
        config.host,
        "--port",
        config.port,
        "--username",
        config.username,
        "--dbname",
        config.database,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        str(backup_path),
    ]
    started_at = time.perf_counter()
    logger.info(
        "Starting database restore",
        extra={
            "operation": "database_restore",
            "host": config.host,
            "port": config.port,
            "database": config.database,
            "username": config.username,
            "backup_path": str(backup_path),
            "size_bytes": backup_path.stat().st_size if backup_path.exists() else None,
            "command": _safe_command_log(command, config.password),
        },
    )
    result = subprocess.run(
        command,
        env=_postgres_env(config),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"{Path(executable).name} restore failed").strip()
        logger.error(
            "Database restore failed",
            extra={
                "operation": "database_restore",
                "backup_path": str(backup_path),
                "returncode": result.returncode,
                "stderr": (result.stderr or "")[-4000:],
                "stdout": (result.stdout or "")[-4000:],
                "duration_seconds": round(time.perf_counter() - started_at, 3),
            },
        )
        raise RuntimeError(detail)
    logger.info(
        "Database restore completed",
        extra={
            "operation": "database_restore",
            "backup_path": str(backup_path),
            "size_bytes": backup_path.stat().st_size if backup_path.exists() else None,
            "duration_seconds": round(time.perf_counter() - started_at, 3),
        },
    )


def schedule_backend_reload() -> Path:
    trigger_path = PROJECT_ROOT / "config" / "reload-trigger.json"
    trigger_path.write_text(
        f'{{"database_import_restart_at":"{datetime.now().isoformat()}"}}\n',
        encoding="utf-8",
    )
    logger.info(
        "Scheduled backend reload after database import",
        extra={
            "operation": "database_restore",
            "reload_trigger_path": str(trigger_path),
        },
    )
    return trigger_path
