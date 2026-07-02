from typing import Optional, Any, Dict
from sqlalchemy import select

from app.core import database as database_module
from app.crud.base import CRUDBase
from app.models.system_setting import SystemSetting


class CRUDSystemSetting(CRUDBase[SystemSetting, Dict[str, Any], Dict[str, Any]]):
    """系统设置的 CRUD 操作"""

    async def get_by_key(self, key: str, user_id: Optional[int] = None) -> Optional[SystemSetting]:
        """按 key 和可选用户归属异步获取设置。"""
        async with database_module.AsyncSessionLocal() as db:
            user_filter = self.model.user_id.is_(None) if user_id is None else self.model.user_id == user_id
            result = await db.execute(select(self.model).where(self.model.key == key, user_filter))
            return result.scalar_one_or_none()

    async def get_value(self, key: str, default: Any = None, user_id: Optional[int] = None) -> Any:
        """异步获取设置的值，提供默认值。"""
        setting = await self.get_by_key(key, user_id=user_id)
        if setting:
            return setting.value
        return default

    async def set_value(
        self,
        key: str,
        value: Any,
        description: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> SystemSetting:
        """异步设置或更新值。"""
        async with database_module.AsyncSessionLocal() as db:
            user_filter = self.model.user_id.is_(None) if user_id is None else self.model.user_id == user_id
            result = await db.execute(select(self.model).where(self.model.key == key, user_filter))
            db_obj = result.scalar_one_or_none()
            if db_obj:
                db_obj.value = value
                if description:
                    db_obj.description = description
            else:
                db_obj = self.model(key=key, value=value, description=description, user_id=user_id)
                db.add(db_obj)
            await db.commit()
            await db.refresh(db_obj)
            return db_obj


system_setting = CRUDSystemSetting(SystemSetting)


async def read_system_setting(key: str, default: Any = None, user_id: Optional[int] = None) -> Any:
    """读取系统设置值。

    Args:
        key: 系统设置 key。
        default: 未找到设置时返回的默认值。
        user_id: 可选用户归属；为空时读取全局设置。

    Returns:
        系统设置值或默认值。
    """
    return await system_setting.get_value(key, default=default, user_id=user_id)


async def save_system_setting(
    key: str,
    value: Any,
    description: Optional[str] = None,
    user_id: Optional[int] = None,
) -> SystemSetting:
    """保存系统设置值。

    Args:
        key: 系统设置 key。
        value: 需要保存的 JSON 兼容值。
        description: 可选设置说明。
        user_id: 可选用户归属；为空时保存为全局设置。

    Returns:
        已保存的系统设置记录。
    """
    return await system_setting.set_value(key, value, description=description, user_id=user_id)
