from sqlalchemy import Column, String, Text, DateTime, Date, ARRAY, JSON, Boolean
from sqlalchemy.sql import func
from app.core.database import Base

class ApiRegistry(Base):
    __tablename__ = "api_registry"
    __table_args__ = {"schema": "data"}

    api_name = Column(String(100), primary_key=True)
    source = Column(String(50), nullable=False)
    storage_mode = Column(String(50), default='jsonb')
    target_table = Column(String(100))
    update_strategy = Column(String(50), default='replace')
    dedup_keys = Column(ARRAY(Text)) # SQLAlchemy doesn't strictly support ARRAY on all DBs, but works for PG
    description = Column(Text)
    last_updated_at = Column(DateTime)
    status = Column(String(50), default='active')
    sync_enabled = Column(Boolean, default=False)

# 对于动态的分区表，通常我们使用 Table 对象或者动态映射，
# 但为了代码方便，我们可以定义一个 Mixin 或 Base Class，
# 但实际写入时如果是动态表名 (Partition child table)，往往直接操作 parent table 即可 (Postgres 特性)。
# 下面定义通用的 CommonData 结构供 Core 使用 (不直接用于 ORM mapping，除非指定 __tablename__)

from sqlalchemy import Table, MetaData

metadata = MetaData()

def get_common_data_table(source: str):
    """
    Reflected or manually defined Table object for common_data_{source} in data schema
    """
    schema = "data" 
    table_name = f"common_data_{source}"
    
    # 手动定义比 reflect 更快且可控
    return Table(
        table_name,
        metadata,
        Column('api_name', String(100), primary_key=True),
        Column('stock_code', String(50), primary_key=True),
        Column('update_date', Date, primary_key=True),
        Column('data_payload', JSON), # JSONB in PG
        Column('updated_at', DateTime, default=func.now(), onupdate=func.now()),
        schema=schema,
        extend_existing=True
    )
