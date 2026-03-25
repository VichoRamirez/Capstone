"""
Vista de Login / Registro para la aplicación de despachos.
Permite crear cuenta (registro) e iniciar sesión.
"""

import json
import urllib.request
import urllib.error

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QMessageBox, QStackedWidget, QSpacerItem,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

import frontend.resources.styles.theme as theme


class LoginView(QWidget):
    """Pantalla de autenticación con registro e inicio de sesión."""

    # Señal emitida al autenticarse: (user_id, username)
    login_successful = pyqtSignal(int, str)

    BASE_URL = "http://localhost:8000"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Background
        self.setStyleSheet(f"background: {theme.BG};")

        # Card container
        card = QFrame()
        card.setFixedWidth(420)
        card.setStyleSheet(f"""
            QFrame {{
                background: {theme.SURFACE};
                border: 1px solid {theme.BORDER};
                border-radius: 12px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(36, 36, 36, 36)
        card_layout.setSpacing(20)

        # Title
        title = QLabel("◈  DISPATCH")
        title.setFont(QFont(theme.MONO, 18, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {theme.ACCENT}; letter-spacing: 4px; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)

        subtitle = QLabel("Fleet Management & Route Optimizer")
        subtitle.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; font-family: {theme.MONO}; border: none;"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(subtitle)

        card_layout.addSpacing(10)

        # ── Stacked forms ─────────────────────────────────────────────
        self.form_stack = QStackedWidget()
        self.form_stack.setStyleSheet("border: none;")

        # -- Login form (index 0) --
        login_page = QWidget()
        login_page.setStyleSheet("border: none;")
        ll = QVBoxLayout(login_page)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(14)

        section_lbl = QLabel("INICIAR SESIÓN")
        section_lbl.setStyleSheet(
            f"color: {theme.ACCENT2}; font-size: 12px; font-family: {theme.MONO}; "
            f"font-weight: bold; letter-spacing: 2px; border: none;"
        )
        section_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(section_lbl)

        self.login_identifier = self._make_input("Usuario o Correo electrónico")
        ll.addWidget(self._field("Usuario / Email", self.login_identifier))

        self.login_password = self._make_input("Contraseña", password=True)
        ll.addWidget(self._field("Contraseña", self.login_password))

        self.btn_login = QPushButton("INICIAR SESIÓN")
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.setMinimumHeight(44)
        self.btn_login.setStyleSheet(self._btn_style(theme.ACCENT))
        self.btn_login.clicked.connect(self._do_login)
        ll.addWidget(self.btn_login)

        self.btn_to_register = QPushButton("¿No tienes cuenta? Crear una")
        self.btn_to_register.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_to_register.setStyleSheet(self._link_style())
        self.btn_to_register.clicked.connect(lambda: self.form_stack.setCurrentIndex(1))
        ll.addWidget(self.btn_to_register)

        self.form_stack.addWidget(login_page)

        # -- Register form (index 1) --
        reg_page = QWidget()
        reg_page.setStyleSheet("border: none;")
        rl = QVBoxLayout(reg_page)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(14)

        reg_lbl = QLabel("CREAR CUENTA")
        reg_lbl.setStyleSheet(
            f"color: {theme.ACCENT2}; font-size: 12px; font-family: {theme.MONO}; "
            f"font-weight: bold; letter-spacing: 2px; border: none;"
        )
        reg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rl.addWidget(reg_lbl)

        self.reg_username = self._make_input("Nombre de usuario")
        rl.addWidget(self._field("Usuario", self.reg_username))

        self.reg_email = self._make_input("Correo electrónico")
        rl.addWidget(self._field("Email", self.reg_email))

        self.reg_password = self._make_input("Contraseña", password=True)
        rl.addWidget(self._field("Contraseña", self.reg_password))

        self.reg_confirm = self._make_input("Repetir contraseña", password=True)
        rl.addWidget(self._field("Confirmar Contraseña", self.reg_confirm))

        self.btn_register = QPushButton("CREAR CUENTA")
        self.btn_register.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_register.setMinimumHeight(44)
        self.btn_register.setStyleSheet(self._btn_style(theme.ACCENT2))
        self.btn_register.clicked.connect(self._do_register)
        rl.addWidget(self.btn_register)

        self.btn_to_login = QPushButton("¿Ya tienes cuenta? Iniciar sesión")
        self.btn_to_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_to_login.setStyleSheet(self._link_style())
        self.btn_to_login.clicked.connect(lambda: self.form_stack.setCurrentIndex(0))
        rl.addWidget(self.btn_to_login)

        self.form_stack.addWidget(reg_page)

        card_layout.addWidget(self.form_stack)

        # Status label
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(
            f"color: {theme.ERROR}; font-size: 11px; font-family: {theme.MONO}; border: none;"
        )
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setWordWrap(True)
        card_layout.addWidget(self.lbl_status)

        outer.addWidget(card)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _make_input(self, placeholder: str, password: bool = False) -> QLineEdit:
        inp = QLineEdit()
        inp.setPlaceholderText(placeholder)
        inp.setMinimumHeight(38)
        inp.setStyleSheet(f"""
            QLineEdit {{
                background: {theme.BG};
                color: {theme.TEXT};
                border: 1px solid {theme.BORDER};
                border-radius: 6px;
                padding: 0 12px;
                font-family: {theme.MONO};
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {theme.ACCENT};
            }}
        """)
        if password:
            inp.setEchoMode(QLineEdit.EchoMode.Password)
        return inp

    def _field(self, label_text: str, widget: QLineEdit) -> QWidget:
        container = QWidget()
        container.setStyleSheet("border: none;")
        vl = QVBoxLayout(container)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(4)
        lbl = QLabel(label_text.upper())
        lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO}; "
            f"font-weight: bold; letter-spacing: 1px; border: none;"
        )
        vl.addWidget(lbl)
        vl.addWidget(widget)
        return container

    def _btn_style(self, color: str) -> str:
        return (
            f"QPushButton {{ background: {color}; color: #0d0f14; border: none; "
            f"border-radius: 6px; font-weight: bold; font-family: {theme.MONO}; "
            f"font-size: 13px; letter-spacing: 2px; }}"
            f"QPushButton:hover {{ opacity: 0.9; }}"
            f"QPushButton:disabled {{ background: {theme.BORDER}; color: {theme.TEXT_DIM}; }}"
        )

    def _link_style(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {theme.TEXT_DIM}; border: none; "
            f"font-size: 11px; font-family: {theme.MONO}; text-decoration: underline; }}"
            f"QPushButton:hover {{ color: {theme.ACCENT}; }}"
        )

    # ── API calls ─────────────────────────────────────────────────────────

    def _api_call(self, endpoint: str, payload: dict) -> dict:
        """Makes a POST request to the backend API."""
        url = f"{self.BASE_URL}{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
                return {"error": body.get("detail", str(e))}
            except Exception:
                return {"error": f"Error HTTP {e.code}"}
        except urllib.error.URLError as e:
            return {"error": f"No se pudo conectar al servidor: {e.reason}"}
        except Exception as e:
            return {"error": str(e)}

    # ── Actions ───────────────────────────────────────────────────────────

    def _do_register(self):
        username = self.reg_username.text().strip()
        email = self.reg_email.text().strip()
        password = self.reg_password.text()
        confirm = self.reg_confirm.text()

        if not username or not email or not password:
            self._show_status("Todos los campos son obligatorios.", error=True)
            return

        if password != confirm:
            self._show_status("Las contraseñas no coinciden.", error=True)
            return

        if len(password) < 6:
            self._show_status("La contraseña debe tener al menos 6 caracteres.", error=True)
            return

        self.btn_register.setEnabled(False)
        self.btn_register.setText("CREANDO...")

        result = self._api_call("/auth/register", {
            "username": username,
            "email": email,
            "password": password,
        })

        self.btn_register.setEnabled(True)
        self.btn_register.setText("CREAR CUENTA")

        if "error" in result:
            self._show_status(result["error"], error=True)
        else:
            self._show_status("¡Cuenta creada! Ahora inicia sesión.", error=False)
            # Switch to login form
            self.login_identifier.setText(username)
            self.form_stack.setCurrentIndex(0)

    def _do_login(self):
        identifier = self.login_identifier.text().strip()
        password = self.login_password.text()

        if not identifier or not password:
            self._show_status("Ingresa usuario/correo y contraseña.", error=True)
            return

        self.btn_login.setEnabled(False)
        self.btn_login.setText("VERIFICANDO...")

        result = self._api_call("/auth/login", {
            "identifier": identifier,
            "password": password,
        })

        self.btn_login.setEnabled(True)
        self.btn_login.setText("INICIAR SESIÓN")

        if "error" in result:
            self._show_status(result["error"], error=True)
        else:
            user_id = result["user_id"]
            username = result["username"]
            self.login_successful.emit(user_id, username)

    def _show_status(self, msg: str, error: bool = True):
        color = theme.ERROR if error else theme.SUCCESS
        self.lbl_status.setStyleSheet(
            f"color: {color}; font-size: 11px; font-family: {theme.MONO}; border: none;"
        )
        self.lbl_status.setText(msg)
