"""测试 auth.py JWT + 用户认证。"""
import os
os.environ["SECRET_KEY"] = "test-secret"

from auth import hash_password, verify_password, create_access_token, decode_token, authenticate_user


def test_hash_and_verify():
    pw = "test123"
    hashed = hash_password(pw)
    assert verify_password(pw, hashed) is True
    assert verify_password("wrong", hashed) is False


def test_jwt_roundtrip():
    token = create_access_token("admin", expires_minutes=60)
    sub = decode_token(token)
    assert sub == "admin"


def test_jwt_expired():
    token = create_access_token("admin", expires_minutes=-1)  # 立刻过期
    assert decode_token(token) is None


def test_jwt_invalid():
    assert decode_token("not.a.jwt") is None


def test_authenticate_user_valid(db):
    from models import User
    pw = "secret"
    user = User(username="testuser", password_hash=hash_password(pw), is_admin=True)
    db.add(user); db.commit()

    result = authenticate_user(db, "testuser", pw)
    assert result is not None
    assert result.username == "testuser"

    result2 = authenticate_user(db, "testuser", "wrong")
    assert result2 is None


def test_authenticate_user_notfound(db):
    assert authenticate_user(db, "ghost", "pw") is None
