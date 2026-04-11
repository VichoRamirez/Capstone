"""Integration tests for /auth/* endpoints.

Uses `api_client` from conftest.py (FastAPI TestClient + real MySQL).
Tests cover the full HTTP layer: status codes, response shape, and DB state.
Test isolation is achieved via UUID-suffixed usernames and emails.
"""
import uuid
import pytest


def _unique(prefix: str) -> str:
    """Generate a test-unique string to avoid cross-test DB collisions."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestRegisterEndpoint:
    def test_register_success(self, api_client):
        payload = {
            "username": _unique("user"),
            "email": f"{_unique('u')}@test.com",
            "password": "secure_pw_123",
        }
        resp = api_client.post("/auth/register", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert "user_id" in body
        assert body["username"] == payload["username"]
        assert body["email"] == payload["email"]
        assert "error" not in body

    def test_register_missing_username_returns_400(self, api_client):
        resp = api_client.post("/auth/register", json={
            "username": "",
            "email": f"{_unique('u')}@test.com",
            "password": "pw123",
        })
        assert resp.status_code == 400

    def test_register_missing_email_returns_400(self, api_client):
        resp = api_client.post("/auth/register", json={
            "username": _unique("user"),
            "email": "",
            "password": "pw123",
        })
        assert resp.status_code == 400

    def test_register_missing_password_returns_400(self, api_client):
        resp = api_client.post("/auth/register", json={
            "username": _unique("user"),
            "email": f"{_unique('u')}@test.com",
            "password": "",
        })
        assert resp.status_code == 400

    def test_register_duplicate_username_returns_400(self, api_client):
        username = _unique("dup")
        email1 = f"{_unique('e')}@test.com"
        email2 = f"{_unique('e')}@test.com"

        api_client.post("/auth/register", json={
            "username": username, "email": email1, "password": "pw123"
        })
        resp = api_client.post("/auth/register", json={
            "username": username, "email": email2, "password": "pw123"
        })
        assert resp.status_code == 400

    def test_register_duplicate_email_returns_400(self, api_client):
        email = f"{_unique('e')}@test.com"
        api_client.post("/auth/register", json={
            "username": _unique("u"), "email": email, "password": "pw123"
        })
        resp = api_client.post("/auth/register", json={
            "username": _unique("u2"), "email": email, "password": "pw123"
        })
        assert resp.status_code == 400

    def test_register_response_has_tipo_usuario(self, api_client):
        resp = api_client.post("/auth/register", json={
            "username": _unique("u"),
            "email": f"{_unique('e')}@test.com",
            "password": "pw123",
        })
        assert "tipo_usuario" in resp.json()


@pytest.mark.integration
class TestLoginEndpoint:
    def _register(self, client, username, email, password):
        client.post("/auth/register", json={
            "username": username, "email": email, "password": password
        })

    def test_login_by_username_success(self, api_client):
        u, e, pw = _unique("usr"), f"{_unique('e')}@test.com", "pw123"
        self._register(api_client, u, e, pw)

        resp = api_client.post("/auth/login", json={"identifier": u, "password": pw})
        assert resp.status_code == 200
        assert resp.json()["username"] == u

    def test_login_by_email_success(self, api_client):
        u, e, pw = _unique("usr"), f"{_unique('e')}@test.com", "pw123"
        self._register(api_client, u, e, pw)

        resp = api_client.post("/auth/login", json={"identifier": e, "password": pw})
        assert resp.status_code == 200

    def test_login_wrong_password_returns_401(self, api_client):
        u, e, pw = _unique("usr"), f"{_unique('e')}@test.com", "correct"
        self._register(api_client, u, e, pw)

        resp = api_client.post("/auth/login", json={"identifier": u, "password": "wrong"})
        assert resp.status_code == 401

    def test_login_nonexistent_user_returns_401(self, api_client):
        resp = api_client.post("/auth/login", json={
            "identifier": "totally_unknown_xyz", "password": "pw"
        })
        assert resp.status_code == 401

    def test_login_missing_fields_returns_400(self, api_client):
        resp = api_client.post("/auth/login", json={"identifier": "", "password": ""})
        assert resp.status_code == 400

    def test_login_response_has_user_id(self, api_client):
        u, e, pw = _unique("usr"), f"{_unique('e')}@test.com", "pw123"
        self._register(api_client, u, e, pw)

        resp = api_client.post("/auth/login", json={"identifier": u, "password": pw})
        assert "user_id" in resp.json()


@pytest.mark.integration
class TestResetPasswordEndpoint:
    def _register(self, client, username, email, password="original"):
        client.post("/auth/register", json={
            "username": username, "email": email, "password": password
        })

    def test_reset_success(self, api_client):
        u, e = _unique("usr"), f"{_unique('e')}@test.com"
        self._register(api_client, u, e, "original")

        resp = api_client.post("/auth/reset-password", json={
            "username": u, "email": e, "new_password": "new_secure_pw"
        })
        assert resp.status_code == 200
        assert "message" in resp.json()

    def test_reset_then_login_with_new_password(self, api_client):
        u, e = _unique("usr"), f"{_unique('e')}@test.com"
        self._register(api_client, u, e, "original")

        api_client.post("/auth/reset-password", json={
            "username": u, "email": e, "new_password": "brand_new"
        })
        login_resp = api_client.post("/auth/login", json={
            "identifier": u, "password": "brand_new"
        })
        assert login_resp.status_code == 200

    def test_reset_old_password_invalid_after_reset(self, api_client):
        u, e = _unique("usr"), f"{_unique('e')}@test.com"
        self._register(api_client, u, e, "original")

        api_client.post("/auth/reset-password", json={
            "username": u, "email": e, "new_password": "brand_new"
        })
        login_resp = api_client.post("/auth/login", json={
            "identifier": u, "password": "original"
        })
        assert login_resp.status_code == 401

    def test_reset_wrong_email_returns_400(self, api_client):
        u, e = _unique("usr"), f"{_unique('e')}@test.com"
        self._register(api_client, u, e)

        resp = api_client.post("/auth/reset-password", json={
            "username": u, "email": "wrong@test.com", "new_password": "new_pw"
        })
        assert resp.status_code == 400

    def test_reset_unknown_user_returns_400(self, api_client):
        resp = api_client.post("/auth/reset-password", json={
            "username": "ghost_xyz", "email": "ghost@test.com", "new_password": "pw"
        })
        assert resp.status_code == 400

    def test_reset_missing_fields_returns_400(self, api_client):
        resp = api_client.post("/auth/reset-password", json={
            "username": "", "email": "", "new_password": ""
        })
        assert resp.status_code == 400
