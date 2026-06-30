
import sys
import os
import argparse
from sqlalchemy import text, inspect

# Add backend directory to sys.path
sys.path.append(os.getcwd())

from app.core.database import engine
# Import all models to ensure they are registered in Base.metadata if needed, 
# though for simple TRUNCATE we might just inspect the DB.
# However, importing them is good practice to ensure metadata is populated if we used it.
import app.models  # noqa: F401,E402

SUPPORTED_SCHEMAS = ["public", "data", "stock_picker_interactive"]


def get_tables():
    inspector = inspect(engine)
    tables = []
    if "sqlite" in engine.dialect.name:
        return inspector.get_table_names()

    for schema in SUPPORTED_SCHEMAS:
        for table in inspector.get_table_names(schema=schema):
            tables.append(f"{schema}.{table}")
    return tables

def clear_table(table_name):
    with engine.connect() as conn:
        try:
            # Use TRUNCATE with CASCADE to handle foreign keys if supported, 
            # otherwise DELETE FROM.
            # SQLite doesn't support TRUNCATE. PostgreSQL does.
            # Assuming Postgres based on user context usually, but code should be robust.
            
            if 'sqlite' in engine.dialect.name:
                 conn.execute(text(f"DELETE FROM {table_name}"))
            else:
                 conn.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))
            
            conn.commit()
            print(f"✅ Table '{table_name}' cleared.")
        except Exception as e:
            print(f"❌ Failed to clear table '{table_name}': {e}")

def main():
    parser = argparse.ArgumentParser(description="Clear database tables.")
    parser.add_argument("--table", "-t", help="Specific table to clear")
    parser.add_argument("--all", "-a", action="store_true", help="Clear ALL tables")
    parser.add_argument("--force", "-f", action="store_true", help="Skip confirmation")
    
    args = parser.parse_args()
    
    tables = get_tables()
    
    if args.table:
        if args.table not in tables:
            print(f"❌ Table '{args.table}' not found.")
            return
        target_tables = [args.table]
    elif args.all:
        target_tables = tables
    else:
        # Interactive mode
        print("Available tables:")
        for i, t in enumerate(tables):
            print(f"{i + 1}. {t}")
        
        selection = input("\nEnter table numbers to clear (comma separated, or 'all'): ")
        if selection.strip().lower() == 'all':
             target_tables = tables
        else:
             try:
                 indices = [int(s.strip()) - 1 for s in selection.split(',')]
                 target_tables = [tables[i] for i in indices if 0 <= i < len(tables)]
             except:
                 print("Invalid selection.")
                 return

    if not target_tables:
        print("No tables selected.")
        return

    print(f"\n⚠️  WARNING: You are about to DELETE ALL DATA from: {', '.join(target_tables)}")
    if not args.force:
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            return

    for table in target_tables:
        clear_table(table)

if __name__ == "__main__":
    main()
