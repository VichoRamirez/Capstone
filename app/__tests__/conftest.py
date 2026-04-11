"""
Shared pytest fixtures for the entire test suite.

DB strategy: SQLite in-memory with StaticPool so every session
shares the same underlying connection and committed data is visible
across sessions without a real MySQL server.
"""
import os
import sys
import urllib.request

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── path: make `app/` the importable root ────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.models import Base  # noqa: E402

# ── SQLite engine shared by the whole test run ───────────────────────────────
_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(bind=_TEST_ENGINE, autocommit=False, autoflush=False)


@pytest.fixture(scope="session", autouse=True)
def _setup_schema():
    """Create all ORM tables once; drop them at the end."""
    Base.metadata.create_all(_TEST_ENGINE)
    yield
    Base.metadata.drop_all(_TEST_ENGINE)


@pytest.fixture
def db_session(_setup_schema):
    """Isolated DB session; rolled back after each test."""
    session = _TestSession()
    yield session
    session.rollback()
    session.close()


def _new_test_session():
    """Factory used as side_effect for get_session patches."""
    return _TestSession()


# Modules that call get_session() directly (not via FastAPI DI)
_GET_SESSION_TARGETS = [
    "backend.services.auth_service.get_session",
    "backend.services.db_persistence.get_session",
    "backend.api.router.get_session",
]


@pytest.fixture
def patch_db(_setup_schema):
    """
    Patch every direct get_session() call in service/router modules
    to return SQLite sessions instead of MySQL sessions.
    """
    from unittest.mock import patch

    patches = [patch(t, side_effect=_new_test_session) for t in _GET_SESSION_TARGETS]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


@pytest.fixture
def api_client(patch_db):
    """
    FastAPI TestClient with DB patched to SQLite in-memory.
    External services (Nominatim, OSRM, CNE) must be mocked per-test.
    """
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ── OSRM availability ─────────────────────────────────────────────────────────
_OSRM_URL = "http://127.0.0.1:5010"


def osrm_available() -> bool:
    try:
        urllib.request.urlopen(
            f"{_OSRM_URL}/nearest/v1/driving/-70.6693,-33.4489?number=1",
            timeout=2,
        )
        return True
    except Exception:
        return False


requires_osrm = pytest.mark.skipif(
    not osrm_available(),
    reason="Local OSRM not reachable at 127.0.0.1:5010",
)
