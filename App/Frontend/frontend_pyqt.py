"""
Dispatcher Frontend — PyQt5
Sends fleet parameters (JSON) + orders (CSV) to a backend,
then displays the returned route schedule (Truck | Point | Hour).
"""

import sys
import json
import csv
import os
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame, QSplitter, QMessageBox,
    QScrollArea, QSizePolicy, QProgressBar, QStatusBar
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve,
    QTimer, QSize
)
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QPixmap, QIcon, QPainter,
    QLinearGradient, QBrush, QPen, QFontDatabase
)

import urllib.request
import urllib.error


# ─── THEME ────────────────────────────────────────────────────────────────────

BG         = "#0d0f14"
SURFACE    = "#151821"
CARD       = "#1c2030"
BORDER     = "#2a3050"
ACCENT     = "#f0a500"
ACCENT2    = "#00c9a7"
TEXT       = "#e8eaf0"
TEXT_DIM   = "#6b7399"
ERROR      = "#ff4d6d"
SUCCESS    = "#00c9a7"
MONO       = "Courier New"

QSS = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
    font-size: 13px;
}}

/* ── Cards ── */
#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* ── Section titles ── */
#sectionTitle {{
    color: {ACCENT};
    font-family: {MONO};
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 3px;
    text-transform: uppercase;
}}

/* ── Labels ── */
QLabel#fieldLabel {{
    color: {TEXT_DIM};
    font-size: 11px;
    letter-spacing: 1px;
}}

/* ── Inputs ── */
QLineEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 8px 12px;
    color: {TEXT};
    font-family: {MONO};
    font-size: 13px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:hover {{
    border: 1px solid #3d4a70;
}}

/* ── File picker ── */
#filePicker {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 8px 12px;
    color: {TEXT_DIM};
    font-family: {MONO};
    font-size: 12px;
    text-align: left;
}}
#filePicker:hover {{
    border: 1px solid {ACCENT};
    color: {TEXT};
}}

/* ── Primary button ── */
#btnPrimary {{
    background: {ACCENT};
    color: #0d0f14;
    border: none;
    border-radius: 4px;
    padding: 12px 32px;
    font-size: 13px;
    font-weight: bold;
    font-family: {MONO};
    letter-spacing: 2px;
}}
#btnPrimary:hover {{
    background: #ffbb2e;
}}
#btnPrimary:pressed {{
    background: #c98800;
}}
#btnPrimary:disabled {{
    background: #2a3050;
    color: {TEXT_DIM};
}}

/* ── Secondary button ── */
#btnSecondary {{
    background: transparent;
    color: {ACCENT2};
    border: 1px solid {ACCENT2};
    border-radius: 4px;
    padding: 8px 20px;
    font-size: 12px;
    font-family: {MONO};
    letter-spacing: 1px;
}}
#btnSecondary:hover {{
    background: rgba(0,201,167,0.1);
}}

/* ── Table ── */
QTableWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
    color: {TEXT};
    font-family: {MONO};
    font-size: 12px;
    selection-background-color: rgba(240,165,0,0.2);
}}
QTableWidget::item {{
    padding: 6px 12px;
    border-bottom: 1px solid {BORDER};
}}
QTableWidget::item:selected {{
    background: rgba(240,165,0,0.15);
    color: {ACCENT};
}}
QHeaderView::section {{
    background: {CARD};
    color: {ACCENT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 12px;
    font-family: {MONO};
    font-size: 11px;
    letter-spacing: 2px;
    font-weight: bold;
}}

/* ── Scrollbar ── */
QScrollBar:vertical {{
    background: {SURFACE};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* ── Progress bar ── */
QProgressBar {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 3px;
    height: 4px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}

/* ── Status bar ── */
QStatusBar {{
    background: {SURFACE};
    color: {TEXT_DIM};
    border-top: 1px solid {BORDER};
    font-family: {MONO};
    font-size: 11px;
    padding: 4px 12px;
}}

/* ── Divider ── */
#divider {{
    background: {BORDER};
    max-height: 1px;
    min-height: 1px;
}}

/* ── Splitter ── */
QSplitter::handle {{
    background: {BORDER};
    width: 1px;
}}
"""


# ─── WORKER THREAD ────────────────────────────────────────────────────────────

class RequestWorker(QThread):
    finished  = pyqtSignal(list)   # list of dicts: Truck, Point, Hour
    error     = pyqtSignal(str)

    def __init__(self, url: str, params: dict, csv_path: str):
        super().__init__()
        self.url      = url
        self.params   = params
        self.csv_path = csv_path

    def run(self):
        import io, mimetypes, uuid

        # ── Build multipart/form-data manually (no third-party deps) ──
        boundary = uuid.uuid4().hex
        body_parts = []

        # JSON params field
        json_bytes = json.dumps(self.params).encode("utf-8")
        body_parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="params"; filename="params.json"\r\n'
            f'Content-Type: application/json\r\n\r\n'.encode() + json_bytes + b'\r\n'
        )

        # CSV file field
        with open(self.csv_path, "rb") as f:
            csv_bytes = f.read()
        fname = os.path.basename(self.csv_path)
        body_parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="orders"; filename="{fname}"\r\n'
            f'Content-Type: text/csv\r\n\r\n'.encode() + csv_bytes + b'\r\n'
        )

        body_parts.append(f'--{boundary}--\r\n'.encode())
        body = b''.join(body_parts)

        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
            rows = self._parse_response(raw)
            self.finished.emit(rows)
        except urllib.error.URLError as e:
            self.error.emit(f"Network error: {e.reason}")
        except Exception as e:
            self.error.emit(str(e))

    def _parse_response(self, raw: str) -> list:
        reader = csv.DictReader(raw.splitlines())
        rows = []
        for row in reader:
            rows.append({
                "Truck": row.get("Truck", ""),
                "Point": row.get("Point", ""),
                "Hour":  row.get("Hour",  ""),
            })
        return rows


# ─── WIDGETS ──────────────────────────────────────────────────────────────────

def make_label(text: str, obj_name="fieldLabel") -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName(obj_name)
    return lbl


def make_input(placeholder="") -> QLineEdit:
    inp = QLineEdit()
    inp.setPlaceholderText(placeholder)
    inp.setMinimumHeight(36)
    return inp


def field_row(label_text: str, widget: QWidget) -> QVBoxLayout:
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

    def add_row(self, label: str, widget: QWidget):
        self.body.addLayout(field_row(label, widget))

    def add_widget(self, widget: QWidget):
        self.body.addWidget(widget)

    def add_layout(self, layout):
        self.body.addLayout(layout)


class FilePickerButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__("▸  Drop or click to select CSV file", parent)
        self.setObjectName("filePicker")
        self.setMinimumHeight(36)
        self.path = None
        self.setCursor(Qt.PointingHandCursor)
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
            f"color: {ACCENT2}; border-color: {ACCENT2};"
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
        self.setStyleSheet(f"color: {TEXT_DIM};")
        self._timer.stop() if hasattr(self, '_timer') else None

    def start_busy(self):
        self._timer.start(500)

    def _toggle(self):
        self._state = not self._state
        self.setStyleSheet(f"color: {'#f0a500' if self._state else TEXT_DIM};")

    def set_ok(self):
        self._timer.stop()
        self.setStyleSheet(f"color: {SUCCESS};")

    def set_error(self):
        self._timer.stop()
        self.setStyleSheet(f"color: {ERROR};")

    def reset(self):
        self._timer.stop()
        self._idle()


# ─── MAIN WINDOW ──────────────────────────────────────────────────────────────

class DispatcherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DISPATCH — Route Optimizer")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)
        self.setStyleSheet(QSS)

        self._build_ui()
        self._status_bar()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Header
        root_layout.addWidget(self._header())

        # Progress bar (hidden until running)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        root_layout.addWidget(self.progress)

        # Main splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        left_results = self._results_panel()
        right_inputs = self._input_panel()
        
        # User requested inputs on the right
        splitter.addWidget(left_results)
        splitter.addWidget(right_inputs)
        splitter.setSizes([860, 420])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(splitter)

        root_layout.addWidget(content, 1)

    def _header(self) -> QWidget:
        header = QFrame()
        header.setFixedHeight(56)
        header.setStyleSheet(
            f"background: {SURFACE}; border-bottom: 1px solid {BORDER};"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 0, 24, 0)

        brand = QLabel("◈  DISPATCH")
        brand.setFont(QFont(MONO, 15, QFont.Bold))
        brand.setStyleSheet(f"color: {ACCENT}; letter-spacing: 4px;")

        subtitle = QLabel("Route Optimizer  /  Fleet Management")
        subtitle.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px; font-family: {MONO};")

        self.dot = PulsingDot()

        hl.addWidget(brand)
        hl.addSpacing(20)
        hl.addWidget(subtitle)
        hl.addStretch()
        hl.addWidget(self.dot)

        return header

    def _input_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {BG}; border: none; }}")

        panel = QWidget()
        panel.setFixedWidth(400)
        panel.setStyleSheet(f"background: {BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # ── Fleet params ──
        fleet_card = SectionCard("01 — Fleet Parameters")

        self.inp_trucks = make_input("e.g. 5")
        fleet_card.add_row("Number of Trucks", self.inp_trucks)

        layout.addWidget(fleet_card)

        # ── Origin ──
        origin_card = SectionCard("02 — Distribution Center")
        self.inp_address = make_input("Full address of the depot")
        origin_card.add_row("Address", self.inp_address)
        layout.addWidget(origin_card)

        # ── General Setup ──
        general_card = SectionCard("03 — General Parameters")
        
        self.inp_url = make_input("http://localhost:8000/optimize")
        self.inp_url.setText("http://localhost:8000/optimize")
        general_card.add_row("API URL", self.inp_url)

        self.inp_runtime = make_input("e.g. 10")
        general_card.add_row("Model Runtime (seconds)", self.inp_runtime)

        self.inp_worktime = make_input("e.g. 10:30") 
        general_card.add_row("WorkTime windows", self.inp_worktime)

        layout.addWidget(general_card)

        # ── CSV ──
        csv_card = SectionCard("04 — Orders Dataset")
        self.file_btn = FilePickerButton()
        csv_card.add_widget(self.file_btn)

        hint = QLabel("CSV must include: id, address, demand, time_window (optional)")
        hint.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; font-family: {MONO};"
            "padding: 4px 0 0 0;"
        )
        hint.setWordWrap(True)
        csv_card.add_widget(hint)
        layout.addWidget(csv_card)

        # ── Advanced Params Toggle ──
        self.btn_advanced = QPushButton("Advanced parameters ▼")
        self.btn_advanced.setObjectName("btnSecondary")
        self.btn_advanced.setCursor(Qt.PointingHandCursor)
        self.btn_advanced.clicked.connect(self._toggle_advanced)
        layout.addWidget(self.btn_advanced)

        self.advanced_container = QWidget()
        self.advanced_layout = QVBoxLayout(self.advanced_container)
        self.advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_layout.setSpacing(16)
        self.advanced_container.hide()

        adv_card = SectionCard("05 — Advanced Parameters")

        self.inp_kml = make_input("e.g. 6.4")
        self.inp_kml.setText("6.4")
        adv_card.add_row("Km/L of the trucks", self.inp_kml)

        self.inp_space = make_input("e.g. 9")
        self.inp_space.setText("9")
        adv_card.add_row("Space per truck (m³)", self.inp_space)

        self.inp_weight = make_input("e.g. 2000")
        self.inp_weight.setText("2000")
        adv_card.add_row("Weight of the truck (kg)", self.inp_weight)

        self.inp_alt = make_input("e.g. 1")
        self.inp_alt.setText("1")
        adv_card.add_row("Alternatives", self.inp_alt)

        self.inp_budget = make_input("e.g. 1000")
        adv_card.add_row("Max Budget", self.inp_budget)

        self.advanced_layout.addWidget(adv_card)
        layout.addWidget(self.advanced_container)

        layout.addStretch()

        # ── Submit ──
        btn_row = QHBoxLayout()
        self.btn_clear = QPushButton("CLEAR")
        self.btn_clear.setObjectName("btnSecondary")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)

        self.btn_run = QPushButton("RUN OPTIMIZER")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setMinimumHeight(44)
        self.btn_run.clicked.connect(self._submit)

        btn_row.addWidget(self.btn_clear, 1)
        btn_row.addWidget(self.btn_run, 2)
        layout.addLayout(btn_row)

        scroll.setWidget(panel)
        return scroll

    def _toggle_advanced(self):
        is_visible = self.advanced_container.isVisible()
        self.advanced_container.setVisible(not is_visible)
        if is_visible:
            self.btn_advanced.setText("Advanced parameters ▼")
        else:
            self.btn_advanced.setText("Advanced parameters ▲")

    def _results_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 20, 20, 20)
        layout.setSpacing(12)

        # Results header row
        hdr = QHBoxLayout()
        results_title = QLabel("ROUTE SCHEDULE")
        results_title.setObjectName("sectionTitle")

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(
            f"color: {ACCENT2}; font-family: {MONO}; font-size: 11px;"
        )

        self.btn_export = QPushButton("EXPORT CSV")
        self.btn_export.setObjectName("btnSecondary")
        self.btn_export.setCursor(Qt.PointingHandCursor)
        self.btn_export.clicked.connect(self._export)
        self.btn_export.setEnabled(False)
        self.btn_export.setFixedHeight(30)

        hdr.addWidget(results_title)
        hdr.addSpacing(16)
        hdr.addWidget(self.lbl_count)
        hdr.addStretch()
        hdr.addWidget(self.btn_export)
        layout.addLayout(hdr)

        # Table
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["TRUCK", "POINT", "HOUR"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            self.table.styleSheet() +
            f"QTableWidget {{ alternate-background-color: rgba(42,48,80,0.3); }}"
        )

        layout.addWidget(self.table, 1)

        # Empty state
        self.empty_lbl = QLabel("No results yet.\nConfigure parameters and run the optimizer.")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-family: {MONO}; font-size: 13px; line-height: 2;"
        )
        layout.addWidget(self.empty_lbl)

        return panel

    def _status_bar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._set_status("Ready", "idle")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, kind: str = "idle"):
        colors = {"idle": TEXT_DIM, "busy": ACCENT, "ok": SUCCESS, "err": ERROR}
        color  = colors.get(kind, TEXT_DIM)
        ts     = datetime.now().strftime("%H:%M:%S")
        self.status.showMessage(f"[{ts}]  {msg}")
        self.status.setStyleSheet(
            f"QStatusBar {{ color: {color}; background: {SURFACE};"
            f"border-top: 1px solid {BORDER}; font-family: {MONO}; font-size: 11px; }}"
        )

    def _clear(self):
        for w in (self.inp_trucks, self.inp_address, self.inp_runtime, self.inp_worktime, self.inp_budget):
            w.clear()
        
        self.inp_kml.setText("6.4")
        self.inp_space.setText("9")
        self.inp_weight.setText("2000")
        self.inp_alt.setText("1")

        self.file_btn.path = None
        self.file_btn.setText("▸  Drop or click to select CSV file")
        self.file_btn.setStyleSheet("")
        self.table.setRowCount(0)
        self.empty_lbl.show()
        self.lbl_count.setText("")
        self.btn_export.setEnabled(False)
        self.dot.reset()
        self._set_status("Cleared", "idle")

    def _validate(self) -> dict | None:
        errors = []

        trucks_txt = self.inp_trucks.text().strip()
        if not trucks_txt.isdigit() or int(trucks_txt) < 1:
            errors.append("Number of trucks must be a positive integer.")

        kml_txt = self.inp_kml.text().strip()
        try:
            kml = float(kml_txt)
            if kml <= 0:
                raise ValueError
        except ValueError:
            kml = None
            errors.append("Km/L must be a positive number.")

        space_txt = self.inp_space.text().strip()
        try:
            space = float(space_txt)
            if space <= 0:
                raise ValueError
        except ValueError:
            space = None
            errors.append("Space must be a positive number.")

        weight_txt = self.inp_weight.text().strip()
        if weight_txt:
            try:
                weight = float(weight_txt)
                if weight <= 0:
                    raise ValueError
            except ValueError:
                weight = None
                errors.append("Weight must be a positive number.")
        else:
            weight = None

        alt_txt = self.inp_alt.text().strip()
        if alt_txt and not alt_txt.isdigit():
            errors.append("Alternatives must be an integer.")

        runtime_txt = self.inp_runtime.text().strip()
        if runtime_txt and not runtime_txt.isdigit():
            errors.append("Runtime must be an integer.")

        budget_txt = self.inp_budget.text().strip()
        if budget_txt:
            try:
                float(budget_txt)
            except ValueError:
                errors.append("Budget must be a number.")

        address = self.inp_address.text().strip()
        if not address:
            errors.append("Distribution center address is required.")

        if not self.file_btn.path:
            errors.append("Please select an orders CSV file.")

        if errors:
            QMessageBox.warning(self, "Validation Error", "\n".join(errors))
            return None

        return {
            "num_trucks":         int(trucks_txt),
            "km_per_liter":       kml,
            "space_per_truck":    space,
            "capacity_per_truck": space,
            "weight_per_truck":   weight,
            "alternatives":       int(alt_txt) if alt_txt else 1,
            "budget":             float(budget_txt) if budget_txt else None,
            "model_runtime":      int(runtime_txt) if runtime_txt else None,
            "worktime_windows":   self.inp_worktime.text().strip(),
            "depot_address":      address,
        }

    # ── Submit ────────────────────────────────────────────────────────────────

    def _submit(self):
        params = self._validate()
        if params is None:
            return

        url = self.inp_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Backend URL is required.")
            return

        self.btn_run.setEnabled(False)
        self.btn_run.setText("RUNNING…")
        self.progress.show()
        self.dot.start_busy()
        self._set_status("Sending request to backend…", "busy")

        self.worker = RequestWorker(url, params, self.file_btn.path)
        self.worker.finished.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_result(self, rows: list):
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("RUN OPTIMIZER")
        self.dot.set_ok()
        self._set_status(f"Optimization complete — {len(rows)} stops returned.", "ok")

        self.empty_lbl.hide()
        self.table.setRowCount(0)

        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)

            truck_item = QTableWidgetItem(str(row["Truck"]))
            truck_item.setForeground(QColor(ACCENT))
            truck_item.setFont(QFont(MONO, 12, QFont.Bold))
            truck_item.setTextAlignment(Qt.AlignCenter)

            point_item = QTableWidgetItem(str(row["Point"]))
            point_item.setForeground(QColor(TEXT))

            hour_item  = QTableWidgetItem(str(row["Hour"]))
            hour_item.setForeground(QColor(ACCENT2))
            hour_item.setFont(QFont(MONO, 12))
            hour_item.setTextAlignment(Qt.AlignCenter)

            self.table.setItem(r, 0, truck_item)
            self.table.setItem(r, 1, point_item)
            self.table.setItem(r, 2, hour_item)

        self.lbl_count.setText(f"{len(rows)} stops  ·  {rows[-1]['Truck'] if rows else 0} trucks")
        self.btn_export.setEnabled(True)
        self._result_rows = rows

    def _on_error(self, msg: str):
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("RUN OPTIMIZER")
        self.dot.set_error()
        self._set_status(f"Error: {msg}", "err")
        QMessageBox.critical(self, "Backend Error", msg)

    # ── Export ────────────────────────────────────────────────────────────────

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Results", "routes.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Truck", "Point", "Hour"])
            writer.writeheader()
            writer.writerows(self._result_rows)
        self._set_status(f"Results exported → {os.path.basename(path)}", "ok")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Dispatcher")

    # Smooth font rendering
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    window = DispatcherWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()