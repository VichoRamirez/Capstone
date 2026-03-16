"""
Vista principal de la aplicación.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHBoxLayout, QMessageBox
)
from PyQt6.QtCore import Qt


class MainView(QWidget):
    """Vista principal con tabla de datos y controles básicos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Título
        title = QLabel("Capstone Analytics")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold; margin: 10px;")
        layout.addWidget(title)

        # Barra de botones
        btn_layout = QHBoxLayout()
        self.btn_load = QPushButton("Cargar datos")
        self.btn_refresh = QPushButton("Actualizar")
        btn_layout.addWidget(self.btn_load)
        btn_layout.addWidget(self.btn_refresh)
        layout.addLayout(btn_layout)

        # Tabla de datos
        self.table = QTableWidget()
        self.table.setColumnCount(0)
        self.table.setRowCount(0)
        layout.addWidget(self.table)

    def populate_table(self, headers: list[str], rows: list[list]):
        """Rellena la tabla con datos provenientes del backend."""
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                self.table.setItem(i, j, QTableWidgetItem(str(value)))

    def show_error(self, message: str):
        QMessageBox.critical(self, "Error", message)

    def show_info(self, message: str):
        QMessageBox.information(self, "Información", message)
