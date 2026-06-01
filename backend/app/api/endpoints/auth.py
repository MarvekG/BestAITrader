from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta

from app.core.database import get_db
from app.crud.user import authenticate_user, update_user
from app.schemas.user import User, Token, PasswordResetRequest
from app.core.security import create_access_token, get_current_user
from app.core.config import settings

router = APIRouter()


@router.post("/register", status_code=status.HTTP_403_FORBIDDEN)
async def register_user():
    """Reject public user registration."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="User registration is disabled",
    )


@router.post("/login", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """用户登录获取访问令牌"""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 生成访问令牌
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


@router.post("/reset-password")
async def reset_password(
    request: PasswordResetRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """无需旧密码，直接重置/修改当前用户的密码"""
    from app.schemas.user import UserUpdate

    updated_user = update_user(
        db,
        current_user.id,
        UserUpdate(
            username=current_user.username,
            email=current_user.email,
            password=request.new_password
        )
    )
    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update password"
        )

    return {"message": "Password updated successfully"}
