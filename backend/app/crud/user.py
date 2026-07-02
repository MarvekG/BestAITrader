from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate

pwd_hasher = PasswordHasher()


def get_password_hash(password: str) -> str:
    """
    使用 Argon2 生成密码哈希。

    Args:
        password: 明文密码。

    Returns:
        Argon2 密码哈希字符串。
    """
    return pwd_hasher.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证明文密码是否匹配 Argon2 密码哈希。

    Args:
        plain_password: 待验证的明文密码。
        hashed_password: 数据库存储的 Argon2 密码哈希。

    Returns:
        密码匹配时返回 True，否则返回 False。
    """
    try:
        return pwd_hasher.verify(hashed_password, plain_password)
    except (Argon2Error, InvalidHashError):
        return False

async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """通过用户名获取用户。"""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """通过邮箱获取用户。"""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """通过 ID 获取用户。"""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

async def create_user(db: AsyncSession, user: UserCreate) -> User | None:
    """创建新用户。"""
    # 检查用户名是否已存在
    result = await db.execute(select(User).where(User.username == user.username))
    db_user = result.scalar_one_or_none()
    if db_user:
        return None

    # 检查邮箱是否已存在
    db_user = await get_user_by_email(db, email=user.email)
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
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def update_user(db: AsyncSession, user_id: int, user_update: UserUpdate) -> Optional[User]:
    """更新用户信息。"""
    db_user = await get_user_by_id(db, user_id)
    if not db_user:
        return None
    
    # 更新用户信息
    update_data = user_update.dict(exclude_unset=True)
    if "password" in update_data:
        update_data["password_hash"] = get_password_hash(update_data.pop("password"))
    
    for field, value in update_data.items():
        setattr(db_user, field, value)
    
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
    """验证用户身份。"""
    user = await get_user_by_username(db, username)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user
