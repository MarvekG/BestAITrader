from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)

class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    def __init__(self, model: Type[ModelType]):
        """CRUD对象的初始化，需要提供模型类"""
        self.model = model

    def get(self, db: Session, id: Any) -> Optional[ModelType]:
        """根据ID获取单个对象"""
        # 默认假设主键是 id，如果模型主键不是 id (例如 session_id)，子类应该覆盖此方法
        if hasattr(self.model, "id"):
            return db.query(self.model).filter(self.model.id == id).first()
        # 回退逻辑：尝试 session_id
        if hasattr(self.model, "session_id"):
            return db.query(self.model).filter(self.model.session_id == id).first()
        return db.query(self.model).get(id)

    def get_multi(
        self, db: Session, *, skip: int = 0, limit: int = 100, **filters
    ) -> List[ModelType]:
        """获取多个对象，支持分页和过滤"""
        query = db.query(self.model)
        for key, value in filters.items():
            if hasattr(self.model, key):
                query = query.filter(getattr(self.model, key) == value)
        return query.offset(skip).limit(limit).all()

    def create(self, db: Session, *, obj_in: Union[CreateSchemaType, Dict[str, Any]]) -> ModelType:
        """创建新对象"""
        # 如果是字典类型，直接使用
        if isinstance(obj_in, dict):
            obj_in_data = obj_in
        else:
            # 否则使用jsonable_encoder转换
            obj_in_data = jsonable_encoder(obj_in)
        
        # 创建数据库对象
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
        self, 
        db: Session, 
        *, 
        db_obj: ModelType, 
        obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """更新对象"""
        obj_data = jsonable_encoder(db_obj)
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.dict(exclude_unset=True)
        
        for field in obj_data:
            if field in update_data:
                setattr(db_obj, field, update_data[field])
        
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, *, id: Any) -> ModelType:
        """删除对象"""
        obj = self.get(db, id)
        if obj:
            db.delete(obj)
            db.commit()
        return obj
