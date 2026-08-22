from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import Base

import app.models  # noqa: F401


BASELINE_REVISION = "a5513c3e027a"
MIGRATION_LOCK_KEY = 865421907331


def _alembic_config() -> Config:
    """加载项目 Alembic 配置。"""
    return Config(str(Path(__file__).resolve().parent / "alembic.ini"))


def _metadata_table_keys() -> set[str]:
    """返回当前 ORM metadata 的标准化表名集合。"""
    return set(Base.metadata.tables)


def _database_table_keys(connection) -> set[str]:
    """读取主数据库中三个业务 schema 的表名集合。"""
    inspector = inspect(connection)
    table_keys: set[str] = set()
    for schema in (None, "data", "stock_picker_interactive"):
        for table_name in inspector.get_table_names(schema=schema):
            if schema is None and table_name == "alembic_version":
                continue
            table_keys.add(table_name if schema is None else f"{schema}.{table_name}")
    return table_keys


def _current_revision(connection) -> str | None:
    """读取 Alembic 当前版本，版本表不存在时返回空值。"""
    version_table_exists = connection.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
    if not version_table_exists:
        return None

    revisions = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
    if len(revisions) > 1:
        raise RuntimeError("public.alembic_version contains multiple revisions")
    return revisions[0] if revisions else None


def _include_object(object_, name: str, type_: str, reflected: bool, compare_to) -> bool:
    """排除由 Alembic 自身管理的版本表。"""
    if type_ == "table" and reflected and name == "alembic_version":
        return False
    return True


def _schema_differences(connection) -> list[object]:
    """比较数据库结构与当前 ORM metadata。"""
    migration_context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": Base.metadata,
            "include_schemas": True,
            "compare_type": True,
            "compare_server_default": True,
            "include_object": _include_object,
            "version_table_schema": "public",
        },
    )
    return compare_metadata(migration_context, Base.metadata)


def _ensure_schema_matches_metadata(connection) -> None:
    """确认已升级数据库与当前 ORM metadata 一致。"""
    schema_differences = _schema_differences(connection)
    if schema_differences:
        raise RuntimeError(
            "Database schema differs from the ORM metadata after migration: "
            f"{schema_differences!r}"
        )


def _adopt_existing_database(alembic_config: Config, connection) -> None:
    """将已存在的数据库接入 baseline 并升级到当前 head。"""
    command.stamp(alembic_config, BASELINE_REVISION)
    command.upgrade(alembic_config, "head")
    _ensure_schema_matches_metadata(connection)


def _bootstrap_database(engine) -> None:
    """按数据库当前状态执行升级或首次接管。"""
    alembic_config = _alembic_config()
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            current_revision = _current_revision(connection)
            connection.commit()
            if current_revision:
                command.upgrade(alembic_config, "head")
                _ensure_schema_matches_metadata(connection)
                return

            table_keys = _database_table_keys(connection)
            connection.commit()
            expected_table_keys = _metadata_table_keys()
            if not table_keys:
                command.upgrade(alembic_config, "head")
                _ensure_schema_matches_metadata(connection)
                return

            missing_tables = expected_table_keys - table_keys
            unexpected_tables = table_keys - expected_table_keys
            if missing_tables or unexpected_tables:
                raise RuntimeError(
                    "Cannot stamp database with incomplete or unexpected schema: "
                    f"missing={sorted(missing_tables)}, unexpected={sorted(unexpected_tables)}"
                )

            _adopt_existing_database(alembic_config, connection)
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": MIGRATION_LOCK_KEY})
            connection.commit()


def main() -> None:
    """在应用进程启动前完成数据库迁移。"""
    engine = create_engine(str(settings.DATABASE_URL), poolclass=NullPool, pool_pre_ping=True)
    try:
        _bootstrap_database(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
