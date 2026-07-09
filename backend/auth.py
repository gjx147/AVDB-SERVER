"""鉴权：JWT 签发与校验、密码哈希、用户认证。

取代 AVDB 补丁层的"恒定时间 token 比较"方案，用标准 OAuth2 Password Flow。
Phase 0 新增：authenticate_user 查 User 表校验凭据。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import hashlib
import logging
import secrets

from jose import JWTError, jwt
import bcrypt
from sqlalchemy import select

from config import get_settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    minutes = expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> str | None:
    """解码 JWT，返回 subject（用户名）；无效则 None。"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def authenticate_user(db, username: str, password: str):
    """校验用户名密码，成功返回 User 对象，失败返回 None。

    Args:
        db: SQLAlchemy Session
        username: 明文用户名
        password: 明文密码
    Returns:
        User | None
    """
    from models import User
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        return None
    if not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def ensure_admin_exists() -> None:
    """首次启动时创建管理员账号（若不存在）。

    读取 ADMIN_USERNAME / ADMIN_PASSWORD 环境变量，hash 后写入 users 表。
    若环境变量值为默认值("admin"/"admin")，打印醒目告警。
    """
    from database import SessionLocal
    from models import User

    settings = get_settings()
    username = settings.ADMIN_USERNAME
    password = settings.ADMIN_PASSWORD

    # SECRET_KEY 为空时自动生成随机密钥并持久化
    if not settings.SECRET_KEY:
        import os as _os
        from pathlib import Path as _Path
        secret_file = _Path(settings.DATA_DIR) / ".secret_key"
        if secret_file.exists():
            settings.SECRET_KEY = secret_file.read_text().strip()
        else:
            new_key = _secrets.token_urlsafe(48)
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(new_key)
            settings.SECRET_KEY = new_key
            # 清除 lru_cache 使后续 get_settings() 返回新值
            get_settings.cache_clear()
            import logging as _log
            _log.getLogger("avdb.auth").info("已自动生成随机 SECRET_KEY 并持久化到 %s", secret_file)

    if password == "admin" or password == "change-me" or not password:
        import secrets as _secrets2
        import logging as _log2
        if not password:
            # 自动生成随机密码
            password = _secrets2.token_urlsafe(16)
            _log2.getLogger("avdb.auth").warning(
                "⚠️  ADMIN_PASSWORD 未设置，已生成随机密码: %s (请妥善保存)", password
            )
        else:
            _log2.getLogger("avdb.auth").warning(
                "⚠️  ADMIN_PASSWORD 为弱默认值 '%s'，请通过环境变量设置强密码。", password
            )

    db = SessionLocal()
    try:
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing:
            return  # 管理员已存在

        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
        )
        db.add(user)
        db.commit()
        import logging
        logging.getLogger("avdb.auth").info("管理员账号 %s 已创建", username)
    finally:
        db.close()
