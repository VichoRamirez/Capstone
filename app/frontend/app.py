"""
Ventana principal de la aplicación PyQt.
"""
from PyQt6.QtWidgets import QMainWindow, QStackedWidget
from config.settings import APP_NAME
from frontend.views.main_view import MainView


class MainApp(QMainWindow):
    """Ventana principal que contiene todas las vistas."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1000, 650)

        # Stack de vistas para navegar entre pantallas
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self._init_views()

    def _init_views(self):
        """Inicializa y registra las vistas en el stack."""
        self.main_view = MainView(parent=self)
        self.stack.addWidget(self.main_view)

    def navigate_to(self, index: int):
        """Cambia la vista visible en el stack."""
        self.stack.setCurrentIndex(index)
