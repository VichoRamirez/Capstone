"""
Shared pytest fixtures for the entire test suite.

DB strategy: uses the real MySQL database configured via .env
(same connection as the running application).
Tests are isolated through UUID-based unique identifiers, so test
data written to MySQL does not collide across runs or test functions.
"""
import os
import sys
import urllib.request

import pytest

# ── path: make `app/` the importable root ────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture
def db_session():
    """
    Real MySQL session; rolled back after each test.
    Useful for unit tests that need direct DB access.
    """
    from database.connection import get_session
    session = get_session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def api_client():
    """
    FastAPI TestClient backed by the real MySQL database.
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
