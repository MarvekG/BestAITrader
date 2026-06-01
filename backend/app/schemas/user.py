from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserUpdate(UserBase):
    password: Optional[str] = Field(None, min_length=6)
    is_active: Optional[bool] = None


class UserInDB(UserBase):
    id: int
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class User(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class PasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=6)
