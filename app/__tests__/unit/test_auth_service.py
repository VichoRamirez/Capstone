"""Unit tests for backend.services.auth_service.

All DB calls are mocked via unittest.mock — no real DB required.
"""
import pytest
from unittest.mock import patch, MagicMock

from backend.services.auth_service import (
    hash_password,
    verify_password,
    register_user,
    login_user,
    reset_password,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(id=1, username="alice", email="alice@test.com", tipo="Free", plain_pw="password123"):
    user = MagicMock()
    user.id = id
    user.username = username
    user.email = email
    user.tipo_usuario = tipo
    user.password = hash_password(plain_pw)
    return user


def _repo(*, by_username=None, by_email=None, created_user=None):
    repo = MagicMock()
    repo.get_by_username.return_value = by_username
    repo.get_by_email.return_value = by_email
    if created_user is not None:
        repo.create_user.return_value = created_user
    return repo


@pytest.fixture
def mock_session():
    return MagicMock()


# ── hash_password ─────────────────────────────────────────────────────────────

class TestHashPassword:
    def test_returns_string(self):
        assert isinstance(hash_password("abc"), str)

    def test_bcrypt_prefix(self):
        assert hash_password("abc").startswith("$2b$")

    def test_different_from_plain(self):
        assert hash_password("abc") != "abc"

    def test_salts_are_unique(self):
        # Two hashes of the same password must differ (different salts)
        assert hash_password("abc") != hash_password("abc")


# ── verify_password ───────────────────────────────────────────────────────────

class TestVerifyPassword:
    def test_correct_password_returns_true(self):
        hashed = hash_password("correct")
        assert verify_password("correct", hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_empty_plain_does_not_match(self):
        hashed = hash_password("nonempty")
        assert verify_password("", hashed) is False

    def test_case_sensitive(self):
        hashed = hash_password("Password")
        assert verify_password("password", hashed) is False


# ── register_user ─────────────────────────────────────────────────────────────

class TestRegisterUser:
    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_success(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        user = _make_user()
        MockRepo.return_value = _repo(created_user=user)

        result = register_user("alice", "alice@test.com", "password123")

        assert "error" not in result
        assert result["user_id"] == 1
        assert result["username"] == "alice"
        assert result["email"] == "alice@test.com"

    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_duplicate_username_returns_error(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        MockRepo.return_value = _repo(by_username=_make_user())

        result = register_user("alice", "other@test.com", "password123")

        assert "error" in result
        assert "alice" in result["error"]

    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_duplicate_email_returns_error(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        MockRepo.return_value = _repo(by_email=_make_user())

        result = register_user("bob", "alice@test.com", "password123")

        assert "error" in result
        assert "alice@test.com" in result["error"]

    @pytest.mark.parametrize("username,email,password", [
        ("", "alice@test.com", "pw"),
        ("alice", "", "pw"),
        ("alice", "alice@test.com", ""),
    ])
    def test_missing_field_returns_error(self, username, email, password):
        result = register_user(username, email, password)
        assert "error" in result


# ── login_user ────────────────────────────────────────────────────────────────

class TestLoginUser:
    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_login_by_username(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        user = _make_user(plain_pw="pw123")
        MockRepo.return_value = _repo(by_username=user)

        result = login_user("alice", "pw123")

        assert "error" not in result
        assert result["username"] == "alice"

    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_login_by_email_fallback(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        user = _make_user(plain_pw="pw123")
        # username lookup returns None → falls back to email lookup
        MockRepo.return_value = _repo(by_username=None, by_email=user)

        result = login_user("alice@test.com", "pw123")

        assert "error" not in result

    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_wrong_password_returns_error(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        user = _make_user(plain_pw="correct")
        MockRepo.return_value = _repo(by_username=user)

        result = login_user("alice", "wrong")

        assert "error" in result

    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_nonexistent_user_returns_error(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        MockRepo.return_value = _repo(by_username=None, by_email=None)

        result = login_user("ghost", "pw123")

        assert "error" in result

    @pytest.mark.parametrize("identifier,password", [
        ("", "pw"),
        ("alice", ""),
    ])
    def test_missing_field_returns_error(self, identifier, password):
        assert "error" in login_user(identifier, password)


# ── reset_password ────────────────────────────────────────────────────────────

class TestResetPassword:
    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_success(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        user = _make_user(email="alice@test.com")
        repo = _repo(by_username=user)
        MockRepo.return_value = repo

        result = reset_password("alice", "alice@test.com", "new_pw")

        assert "error" not in result
        assert "message" in result
        repo.update_password.assert_called_once()

    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_email_mismatch_returns_error(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        user = _make_user(email="real@test.com")
        repo = _repo(by_username=user)
        MockRepo.return_value = repo

        result = reset_password("alice", "wrong@test.com", "new_pw")

        assert "error" in result
        repo.update_password.assert_not_called()

    @patch("backend.services.auth_service.UsuarioRepository")
    @patch("backend.services.auth_service.get_session")
    def test_nonexistent_user_returns_error(self, mock_gs, MockRepo, mock_session):
        mock_gs.return_value = mock_session
        MockRepo.return_value = _repo(by_username=None)

        result = reset_password("ghost", "ghost@test.com", "new_pw")

        assert "error" in result

    @pytest.mark.parametrize("username,email,pw", [
        ("", "alice@test.com", "new_pw"),
        ("alice", "", "new_pw"),
        ("alice", "alice@test.com", ""),
    ])
    def test_missing_field_returns_error(self, username, email, pw):
        assert "error" in reset_password(username, email, pw)
