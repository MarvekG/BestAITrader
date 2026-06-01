from sqlalchemy.orm import Session
from typing import Optional
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from passlib.context import CryptContext

# 使用argon2算法，同时支持bcrypt和sha256作为后备，这样可以兼容旧的密码哈希
pwd_context = CryptContext(schemes=["argon2", "bcrypt", "sha256_crypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    try:
        # 先尝试使用Passlib验证
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        # 如果Passlib验证失败，检查是否是简单的SHA-256哈希
        import hashlib
        if len(hashed_password) == 64:
            # 尝试直接比较SHA-256哈希
            return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password
        # 其他情况返回False
        return False

def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """通过用户名获取用户"""
    return db.query(User).filter(User.username == username).first()

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """通过邮箱获取用户"""
    return db.query(User).filter(User.email == email).first()

def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """通过ID获取用户"""
    return db.query(User).filter(User.id == user_id).first()

def create_user(db: Session, user: UserCreate) -> User:
    """创建新用户"""
    # 检查用户名是否已存在
    db_user = get_user_by_username(db, username=user.username)
    if db_user:
        return None
    
    # 检查邮箱是否已存在
    db_user = get_user_by_email(db, email=user.email)
    if db_user:
        return None
    
    # 创建新用户
    hashed_password = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        password_hash=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user(db: Session, user_id: int, user_update: UserUpdate) -> Optional[User]:
    """更新用户信息"""
    db_user = get_user_by_id(db, user_id)
    if not db_user:
        return None
    
    # 更新用户信息
    update_data = user_update.dict(exclude_unset=True)
    if "password" in update_data:
        update_data["password_hash"] = get_password_hash(update_data.pop("password"))
    
    for field, value in update_data.items():
        setattr(db_user, field, value)
    
    db.commit()
    db.refresh(db_user)
    return db_user

def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """验证用户身份"""
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user