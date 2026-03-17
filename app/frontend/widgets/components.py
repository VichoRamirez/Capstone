"""
Reusable UI components for the dispatcher frontend.
"""

import os
from PyQt6.QtWidgets import (
    QLabel, QLineEdit, QVBoxLayout, QFrame, QPushButton, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

# We use an empty QSS since styling is applied globally with ObjectNames
import frontend.resources.styles.theme as theme

def make_label(text: str, obj_name="fieldLabel") -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName(obj_name)
    return lbl


def make_input(placeholder="") -> QLineEdit:
    inp = QLineEdit()
    inp.setPlaceholderText(placeholder)
    inp.setMinimumHeight(36)
    return inp


def field_row(label_text: str, widget) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(4)
    layout.addWidget(make_label(label_text))
    layout.addWidget(widget)
    return layout


class SectionCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(16)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("sectionTitle")
        outer.addWidget(title_lbl)

        div = QFrame()
        div.setObjectName("divider")
        outer.addWidget(div)

        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body)

    def add_row(self, label: str, widget):
        self.body.addLayout(field_row(label, widget))

    def add_widget(self, widget):
        self.body.addWidget(widget)

    def add_layout(self, layout):
        self.body.addLayout(layout)


class FilePickerButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__("▸  Drop or click to select CSV file", parent)
        self.setObjectName("filePicker")
        self.setMinimumHeight(36)
        self.path = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().endswith(".csv"):
                event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        self._set_path(path)

    def _set_path(self, path: str):
        self.path = path
        self.setText(f"✓  {os.path.basename(path)}")
        self.setStyleSheet(
            f"color: {theme.ACCENT2}; border-color: {theme.ACCENT2};"
            f"background: rgba(0,201,167,0.05);"
        )

    def mousePressEvent(self, event):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Orders CSV", "", "CSV Files (*.csv)"
        )
        if path:
            self._set_path(path)


class PulsingDot(QLabel):
    """Small animated status indicator."""
    def __init__(self, parent=None):
        super().__init__("●", parent)
        self.setFont(QFont("Arial", 10))
        self._idle()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._toggle)
        self._state = False

    def _idle(self):
        self.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self._timer.stop() if hasattr(self, '_timer') else None

    def start_busy(self):
        self._timer.start(500)

    def _toggle(self):
        self._state = not self._state
        self.setStyleSheet(f"color: {theme.ACCENT if self._state else theme.TEXT_DIM};")

    def set_ok(self):
        self._timer.stop()
        self.setStyleSheet(f"color: {theme.SUCCESS};")

    def set_error(self):
        self._timer.stop()
        self.setStyleSheet(f"color: {theme.ERROR};")

    def reset(self):
        self._timer.stop()
        self._idle()
