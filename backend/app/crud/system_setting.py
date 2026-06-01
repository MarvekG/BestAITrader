from typing import Optional, Any, Dict
from sqlalchemy.orm import Session
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
