"""
Ventana principal de la aplicación PyQt.
"""

import sys
import os

# Ensure the 'app' directory is in sys.path so 'frontend' module can be resolved
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime
from PyQt6.QtWidgets import QApplication, QMainWindow, QStackedWidget, QStatusBar
from PyQt6.QtGui import QFontDatabase, QFont

from frontend.views.main_view import MainView
import frontend.resources.styles.theme as theme

class MainApp(QMainWindow):
    """Ventana principal que contiene todas las vistas."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DISPATCH — Route Optimizer")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)

        # Load dynamic stylesheet
        self._load_stylesheet()

        # Stack de vistas para navegar entre pantallas
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self._init_views()
        self._status_bar()

    def closeEvent(self, event):
        """Clean up background threads on exit to prevent core dumps."""
        if hasattr(self, 'main_view') and hasattr(self.main_view, 'health_worker'):
            if self.main_view.health_worker:
                self.main_view.health_worker.stop()
        super().closeEvent(event)

    def _load_stylesheet(self):
        """Loads and formats the custom styles/style.qss file."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        qss_path = os.path.join(script_dir, "resources", "styles", "style.qss")
        if os.path.exists(qss_path):
            with open(qss_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()
            # Interpolate theme colors defined in double brackets handling
            # In style.qss, we expect direct substitutions if we `.format()` it,
            # but since QSS syntax contains curly braces `{}`, we must carefully
            # replace format-string `{VAR}` by injecting theme attributes.
            # To work effectively, the `.qss` file should escape native braces as `{{` and `}}`
            formatted_qss = stylesheet.format(
                BG=theme.BG,
                SURFACE=theme.SURFACE,
                CARD=theme.CARD,
                BORDER=theme.BORDER,
                ACCENT=theme.ACCENT,
                ACCENT2=theme.ACCENT2,
                TEXT=theme.TEXT,
                TEXT_DIM=theme.TEXT_DIM,
                ERROR=theme.ERROR,
                SUCCESS=theme.SUCCESS,
                MONO=theme.MONO
            )
            self.setStyleSheet(formatted_qss)

    def _init_views(self):
        """Inicializa y registra las vistas en el stack."""
        self.main_view = MainView(parent=self)
        self.stack.addWidget(self.main_view)

    def navigate_to(self, index: int):
        """Cambia la vista visible en el stack."""
        self.stack.setCurrentIndex(index)

    def _status_bar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._set_status("Ready", "idle")

    def _set_status(self, msg: str, kind: str = "idle"):
        colors = {"idle": theme.TEXT_DIM, "busy": theme.ACCENT, "ok": theme.SUCCESS, "err": theme.ERROR}
        color  = colors.get(kind, theme.TEXT_DIM)
        ts     = datetime.now().strftime("%H:%M:%S")
        self.status.showMessage(f"[{ts}]  {msg}")
        self.status.setStyleSheet(
            f"QStatusBar {{ color: {color}; background: {theme.SURFACE};"
            f"border-top: 1px solid {theme.BORDER}; font-family: {theme.MONO}; font-size: 11px; }}"
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Dispatcher")

    window = MainApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
