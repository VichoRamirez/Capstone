"""
Main view (DispatcherWindow) logic and layout.
"""

import os
import csv
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, 
    QSplitter, QMessageBox, QScrollArea, QProgressBar, 
    QStatusBar, QFileDialog
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

import frontend.resources.styles.theme as theme
from frontend.widgets.components import (
    make_input, SectionCard, FilePickerButton, PulsingDot
)
from frontend.workers.request_worker import RequestWorker

class MainView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._result_rows = []
        self._build_ui()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
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
        splitter = QSplitter(Qt.Orientation.Horizontal)
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
            f"background: {theme.SURFACE}; border-bottom: 1px solid {theme.BORDER};"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 0, 24, 0)

        brand = QLabel("◈  DISPATCH")
        brand.setFont(QFont(theme.MONO, 15, QFont.Weight.Bold))
        brand.setStyleSheet(f"color: {theme.ACCENT}; letter-spacing: 4px;")

        subtitle = QLabel("Route Optimizer  /  Fleet Management")
        subtitle.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px; font-family: {theme.MONO};")

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
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {theme.BG}; border: none; }}")

        panel = QWidget()
        panel.setFixedWidth(400)
        panel.setStyleSheet(f"background: {theme.BG};")
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
            f"color: {theme.TEXT_DIM}; font-size: 11px; font-family: {theme.MONO};"
            "padding: 4px 0 0 0;"
        )
        hint.setWordWrap(True)
        csv_card.add_widget(hint)
        layout.addWidget(csv_card)

        # ── Advanced Params Toggle ──
        self.btn_advanced = QPushButton("Advanced parameters ▼")
        self.btn_advanced.setObjectName("btnSecondary")
        self.btn_advanced.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)

        self.btn_run = QPushButton("RUN OPTIMIZER")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
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
        panel.setStyleSheet(f"background: {theme.BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 20, 20, 20)
        layout.setSpacing(12)

        # Results header row
        hdr = QHBoxLayout()
        results_title = QLabel("    ROUTE SCHEDULE")
        results_title.setObjectName("sectionTitle")

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(
            f"color: {theme.ACCENT2}; font-family: {theme.MONO}; font-size: 11px;"
        )

        self.btn_export = QPushButton("EXPORT CSV")
        self.btn_export.setObjectName("btnSecondary")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            self.table.styleSheet() +
            f"QTableWidget {{ alternate-background-color: rgba(42,48,80,0.3); }}"
        )

        layout.addWidget(self.table, 1)

        # Empty state
        self.empty_lbl = QLabel("No results yet.\nConfigure parameters and run the optimizer.")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-family: {theme.MONO}; font-size: 13px; line-height: 2;"
        )
        layout.addWidget(self.empty_lbl)

        return panel

    def _set_global_status(self, msg: str, kind: str = "idle"):
        # We need a reference to the main window's status bar setter
        if self.parent() and hasattr(self.parent(), "_set_status"):
            self.parent()._set_status(msg, kind)

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
        self._set_global_status("Cleared", "idle")

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
        self._set_global_status("Sending request to backend…", "busy")

        self.worker = RequestWorker(url, params, self.file_btn.path)
        self.worker.finished.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_result(self, rows: list):
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("RUN OPTIMIZER")
        self.dot.set_ok()
        self._set_global_status(f"Optimization complete — {len(rows)} stops returned.", "ok")

        self.empty_lbl.hide()
        self.table.setRowCount(0)

        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)

            truck_item = QTableWidgetItem(str(row["Truck"]))
            truck_item.setForeground(QColor(theme.ACCENT))
            truck_item.setFont(QFont(theme.MONO, 12, QFont.Weight.Bold))
            truck_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            point_item = QTableWidgetItem(str(row["Point"]))
            point_item.setForeground(QColor(theme.TEXT))

            hour_item  = QTableWidgetItem(str(row["Hour"]))
            hour_item.setForeground(QColor(theme.ACCENT2))
            hour_item.setFont(QFont(theme.MONO, 12))
            hour_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

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
        self._set_global_status(f"Error: {msg}", "err")
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
        self._set_global_status(f"Results exported → {os.path.basename(path)}", "ok")
