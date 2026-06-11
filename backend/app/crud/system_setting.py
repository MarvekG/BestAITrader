from typing import Optional, Any, Dict
from sqlalchemy.orm import Session

from app.core import database as database_module
from app.crud.base import CRUDBase
from app.models.system_setting import SystemSetting


class CRUDSystemSetting(CRUDBase[SystemSetting, Dict[str, Any], Dict[str, Any]]):
    """系统设置的 CRUD 操作"""
    
    def get_by_key(self, db: Session, key: str, user_id: Optional[int] = None) -> Optional[SystemSetting]:
        """按 key 和可选用户归属获取设置"""
        query = db.query(self.model).filter(self.model.key == key)
        if user_id is None:
            query = query.filter(self.model.user_id.is_(None))
        else:
            query = query.filter(self.model.user_id == user_id)
        return query.first()
    
    def get_value(self, db: Session, key: str, default: Any = None, user_id: Optional[int] = None) -> Any:
        """获取设置的值，提供默认值"""
        setting = self.get_by_key(db, key, user_id=user_id)
        if setting:
            return setting.value
        return default
    
    def set_value(
        self,
        db: Session,
        key: str,
        value: Any,
        description: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> SystemSetting:
        """设置或更新值"""
        db_obj = self.get_by_key(db, key, user_id=user_id)
        if db_obj:
            db_obj.value = value
            if description:
                db_obj.description = description
            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)
            return db_obj
        else:
            db_obj = self.model(key=key, value=value, description=description, user_id=user_id)
            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)
            return db_obj


system_setting = CRUDSystemSetting(SystemSetting)


def read_system_setting(key: str, default: Any = None, user_id: Optional[int] = None) -> Any:
    """读取系统设置值。

    Args:
        key: 系统设置 key。
        default: 未找到设置时返回的默认值。
        user_id: 可选用户归属；为空时读取全局设置。

    Returns:
        系统设置值或默认值。
    """
    with database_module.SessionLocal() as db:
        return system_setting.get_value(db, key, default=default, user_id=user_id)


def save_system_setting(
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
    with database_module.SessionLocal() as db:
        return system_setting.set_value(db, key, value, description=description, user_id=user_id)
