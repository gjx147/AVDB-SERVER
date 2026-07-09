"""pytest fixtures —— 临时文件 SQLite + 测试会话。"""
import os
import sys
import tempfile

# 在任何 backend 模块被 import 之前设置环境变量
_db_file = os.path.join(tempfile.gettempdir(), "avdb_test.db")
# 清理上次残留
if os.path.exists(_db_file):
    os.remove(_db_file)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ["AUTH_DISABLED"] = "true"
os.environ["SECRET_KEY"] = "test-secret"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# 预建表（在 app import 之前）
from database import Base, engine
import models  # noqa: F401  注册所有表
Base.metadata.create_all(engine)

import pytest  # noqa: E402


@pytest.fixture
def db():
    """每个测试函数独立的数据库会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

from database import SessionLocal  # noqa: E402


@pytest.fixture
def client():
    """FastAPI TestClient（复用预建表的 engine）。"""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)
