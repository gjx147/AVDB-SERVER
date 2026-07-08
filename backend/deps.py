"""依赖注入：数据库会话、分页参数、当前用户。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_token


DbSession = Annotated[Session, Depends(get_db)]


def get_pagination(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
) -> tuple[int, int]:
    """分页参数，返回 (offset, limit)。"""
    return (page - 1) * page_size, page_size


Pagination = Annotated[tuple[int, int], Depends(get_pagination)]


def get_current_user(
    authorization: str | None = Header(default=None),
) -> str:
    """从 Authorization: Bearer <token> 解析当前用户名。

    AUTH_DISABLED 时直接放行。
    """
    from config import get_settings

    settings = get_settings()
    if settings.AUTH_DISABLED:
        return "anonymous"

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少有效的 Authorization 头",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    username = decode_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


CurrentUser = Annotated[str, Depends(get_current_user)]


def get_current_admin(
    current_user: CurrentUser,
    db: DbSession,
) -> str:
    """要求管理员权限。非管理员返回 403。"""
    from auth import decode_token
    # current_user 已经是 username(anonymous 表示未认证)
    if current_user == "anonymous":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # 查用户表确认 admin 身份
    from models import User
    from sqlalchemy import select
    user = db.execute(select(User).where(User.username == current_user)).scalar_one_or_none()
    if user is None or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user


CurrentAdmin = Annotated[str, Depends(get_current_admin)]
