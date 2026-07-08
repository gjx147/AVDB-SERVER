"""pytest fixtures —— 内存 SQLite + 测试会话。"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# 强制使用内存 SQLite
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


@pytest.fixture
def db():
    """每个测试函数独立的数据库会话（内存 SQLite）。"""
    from database import Base, engine, SessionLocal
    import models  # noqa: F401  注册所有表
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    """FastAPI TestClient。"""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)
