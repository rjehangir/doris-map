"""Shared fixtures for backend tests.

Uses an in-memory SQLite database so the production DB is never touched.
"""

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure `backend/` is importable without installing as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Configure the webhook shared secret BEFORE `main` (and `security`) are
# imported so the fail-closed guard in security.py is satisfied for tests.
os.environ.setdefault("WEBHOOK_SHARED_SECRET", "test-webhook-secret")

from database import Base
from main import app, get_db

WEBHOOK_AUTH_HEADERS = {"Authorization": "Bearer test-webhook-secret"}

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_database():
    """Create all tables before each test, seed devices, and drop afterwards."""
    Base.metadata.create_all(bind=engine)
    from models import Device
    session = TestingSessionLocal()
    session.add(Device(imei="301434061119510", name="DORIS 2"))
    session.commit()
    session.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session(setup_database):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    """FastAPI TestClient wired to the in-memory test database."""
    from fastapi.testclient import TestClient

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
