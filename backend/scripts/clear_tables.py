import argparse
import asyncio
import os
import sys

from sqlalchemy import inspect, text

# Add backend directory to sys.path
sys.path.append(os.getcwd())

from app.core.database import async_engine

# Import all models to ensure they are registered in Base.metadata if needed,
# though for simple TRUNCATE we might just inspect the DB.
import app.models  # noqa: F401,E402

SUPPORTED_SCHEMAS = ["public", "data", "stock_picker_interactive"]


async def get_tables() -> list[str]:
    """读取支持 schema 下的数据库表名。"""
    tables = []
    async with async_engine.connect() as conn:
        if "sqlite" in async_engine.dialect.name:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

        for schema in SUPPORTED_SCHEMAS:
            schema_tables = await conn.run_sync(
                lambda sync_conn, schema=schema: inspect(sync_conn).get_table_names(schema=schema)
            )
            for table in schema_tables:
                tables.append(f"{schema}.{table}")
    return tables


async def clear_table(table_name: str) -> None:
    """清空指定数据库表。"""
    async with async_engine.connect() as conn:
        try:
            if "sqlite" in async_engine.dialect.name:
                await conn.execute(text(f"DELETE FROM {table_name}"))
            else:
                await conn.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))

            await conn.commit()
            print(f"Table '{table_name}' cleared.")
        except Exception as e:
            print(f"Failed to clear table '{table_name}': {e}")


async def main() -> None:
    """解析 CLI 参数并清空目标表。"""
    parser = argparse.ArgumentParser(description="Clear database tables.")
    parser.add_argument("--table", "-t", help="Specific table to clear")
    parser.add_argument("--all", "-a", action="store_true", help="Clear ALL tables")
    parser.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    tables = await get_tables()

    if args.table:
        if args.table not in tables:
            print(f"Table '{args.table}' not found.")
            return
        target_tables = [args.table]
    elif args.all:
        target_tables = tables
    else:
        print("Available tables:")
        for i, table in enumerate(tables):
            print(f"{i + 1}. {table}")

        selection = input("\nEnter table numbers to clear (comma separated, or 'all'): ")
        if selection.strip().lower() == "all":
            target_tables = tables
        else:
            try:
                indices = [int(item.strip()) - 1 for item in selection.split(",")]
                target_tables = [tables[i] for i in indices if 0 <= i < len(tables)]
            except ValueError:
                print("Invalid selection.")
                return

    if not target_tables:
        print("No tables selected.")
        return

    print(f"\nWARNING: You are about to DELETE ALL DATA from: {', '.join(target_tables)}")
    if not args.force:
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            return

    for table in target_tables:
        await clear_table(table)


if __name__ == "__main__":
    # CLI-only asyncio.run bridge.
    asyncio.run(main())
