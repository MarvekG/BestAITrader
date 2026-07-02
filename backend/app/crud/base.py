from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)

class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    def __init__(self, model: Type[ModelType]):
        """CRUD对象的初始化，需要提供模型类"""
        self.model = model

    async def get(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        """根据 ID 获取单个对象。

        Args:
            db: 异步数据库会话。
            id: 对象主键或业务 ID。

        Returns:
            匹配的模型对象；不存在时返回 None。
        """
        # 默认假设主键是 id，如果模型主键不是 id (例如 session_id)，子类应该覆盖此方法
        if hasattr(self.model, "id"):
            result = await db.execute(select(self.model).where(self.model.id == id))
            return result.scalar_one_or_none()
        # 回退逻辑：尝试 session_id
        if hasattr(self.model, "session_id"):
            result = await db.execute(select(self.model).where(self.model.session_id == id))
            return result.scalar_one_or_none()
        return await db.get(self.model, id)

    async def get_multi(
        self, db: AsyncSession, *, skip: int = 0, limit: int = 100, **filters
    ) -> List[ModelType]:
        """获取多个对象，支持分页和过滤。

        Args:
            db: 异步数据库会话。
            skip: 跳过的记录数。
            limit: 返回的最大记录数。
            **filters: 字段等值过滤条件。

        Returns:
            匹配的模型对象列表。
        """
        stmt = select(self.model)
        for key, value in filters.items():
            if hasattr(self.model, key):
                stmt = stmt.where(getattr(self.model, key) == value)
        result = await db.execute(stmt.offset(skip).limit(limit))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, *, obj_in: Union[CreateSchemaType, Dict[str, Any]]) -> ModelType:
        """创建新对象。

        Args:
            db: 异步数据库会话。
            obj_in: 创建对象所需的数据。

        Returns:
            已持久化的模型对象。
        """
        # 如果是字典类型，直接使用
        if isinstance(obj_in, dict):
            obj_in_data = obj_in
        else:
            # 否则使用jsonable_encoder转换
            obj_in_data = jsonable_encoder(obj_in)

        # 创建数据库对象
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def update(
        self,
        db: AsyncSession,
        *,
        db_obj: ModelType,
        obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """更新对象。

        Args:
            db: 异步数据库会话。
            db_obj: 待更新的模型对象。
            obj_in: 更新字段数据。

        Returns:
            更新后的模型对象。
        """
        obj_data = jsonable_encoder(db_obj)
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.dict(exclude_unset=True)

        for field in obj_data:
            if field in update_data:
                setattr(db_obj, field, update_data[field])

        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def remove(self, db: AsyncSession, *, id: Any) -> Optional[ModelType]:
        """删除对象。

        Args:
            db: 异步数据库会话。
            id: 对象主键或业务 ID。

        Returns:
            被删除的模型对象；不存在时返回 None。
        """
        obj = await self.get(db, id)
        if obj:
            await db.delete(obj)
            await db.commit()
        return obj
