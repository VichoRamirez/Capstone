"""
Main view (DispatcherWindow) logic and layout.
Two-file upload (ventas + detalle), embedded map, route table, and export.
Results are shown per calendar day in outer tabs.
Includes SaaS database upload integration.
"""

import os
import csv
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QSplitter, QMessageBox, QScrollArea, QProgressBar,
    QStatusBar, QFileDialog, QTabWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

import frontend.resources.styles.theme as theme
from frontend.widgets.components import (
    make_input, SectionCard, FilePickerButton, PulsingDot
)
from frontend.workers.request_worker import RequestWorker
from frontend.workers.health_worker import HealthWorker
from frontend.workers.progress_worker import ProgressWorker


# ── Per-Day result widget ─────────────────────────────────────────────────

class DayResultWidget(QWidget):
    """Shows one day's optimisation results: Route Schedule, Map, Uncovered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._route_rows = []
        self._uncovered_rows = []
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # Stats + export row
        top = QHBoxLayout()
        self.lbl_stats = QLabel("")
        self.lbl_stats.setStyleSheet(
            f"color: {theme.ACCENT2}; font-family: {theme.MONO}; font-size: 11px; padding-left: 4px;"
        )
        self.btn_export_routes = QPushButton("EXPORT ROUTES")
        self.btn_export_routes.setObjectName("btnSecondary")
        self.btn_export_routes.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_routes.setFixedHeight(28)
        self.btn_export_routes.setEnabled(False)
        self.btn_export_routes.clicked.connect(self._export_routes)

        self.btn_export_uncovered = QPushButton("EXPORT UNCOVERED")
        self.btn_export_uncovered.setObjectName("btnSecondary")
        self.btn_export_uncovered.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_uncovered.setFixedHeight(28)
        self.btn_export_uncovered.setEnabled(False)
        self.btn_export_uncovered.clicked.connect(self._export_uncovered)

        top.addWidget(self.lbl_stats)
        top.addStretch()
        top.addWidget(self.btn_export_routes)
        top.addSpacing(6)
        top.addWidget(self.btn_export_uncovered)
        layout.addLayout(top)

        # Inner tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {theme.BORDER}; background: {theme.BG}; }}
            QTabBar::tab {{ background: {theme.SURFACE}; color: {theme.TEXT_DIM};
                           padding: 6px 16px; border: 1px solid {theme.BORDER};
                           font-family: {theme.MONO}; font-size: 11px; }}
            QTabBar::tab:selected {{ background: {theme.BG}; color: {theme.ACCENT};
                                    border-bottom: 2px solid {theme.ACCENT}; }}
        """)

        # Tab 1: Routes table
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["CAMIÓN", "ORDEN", "RUT", "CLIENTE", "DIRECCIÓN", "COMUNA", "HORA"]
        )
        for col in range(7):
            mode = (QHeaderView.ResizeMode.Stretch if col == 4
                    else QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(col, mode)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            self.table.styleSheet() +
            f"QTableWidget {{ alternate-background-color: rgba(42,48,80,0.3); }}"
        )
        self.tabs.addTab(self.table, "📋 Route Schedule")

        # Tab 2: Map
        self.map_container = QWidget()
        map_layout = QVBoxLayout(self.map_container)
        map_layout.setContentsMargins(0, 0, 0, 0)
        if HAS_WEBENGINE:
            self.web_view = QWebEngineView()
            self._set_map_placeholder()
            map_layout.addWidget(self.web_view)
        else:
            no_web = QLabel("PyQt6-WebEngine not installed.")
            no_web.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_web.setStyleSheet(f"color: {theme.TEXT_DIM}; font-family: {theme.MONO};")
            map_layout.addWidget(no_web)
        self.tabs.addTab(self.map_container, "🗺️ Map")

        # Tab 3: Uncovered
        self.table_uncovered = QTableWidget(0, 6)
        self.table_uncovered.setHorizontalHeaderLabels(
            ["RUT", "CLIENTE", "DIRECCIÓN", "ORDEN", "MONTO", "MOTIVO"]
        )
        for col in range(6):
            mode = (QHeaderView.ResizeMode.Stretch if col == 2
                    else QHeaderView.ResizeMode.ResizeToContents)
            self.table_uncovered.horizontalHeader().setSectionResizeMode(col, mode)
        self.table_uncovered.verticalHeader().setVisible(False)
        self.table_uncovered.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_uncovered.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_uncovered.setShowGrid(False)
        self.table_uncovered.setAlternatingRowColors(True)
        self.tabs.addTab(self.table_uncovered, "⚠️ Uncovered")

        layout.addWidget(self.tabs, 1)

    def _set_map_placeholder(self):
        if HAS_WEBENGINE:
            self.web_view.setHtml(
                "<html><body style='background:#0d1117;color:#8b949e;display:flex;"
                "align-items:center;justify-content:center;height:100vh;font-family:monospace;'>"
                "<p>Run the model to see the route map.</p></body></html>"
            )

    def populate(self, day_data: dict):
        """Fill all three inner tabs from a day result dict."""
        self._result = day_data
        stats = day_data.get("stats", {})

        error_msg = stats.get("error")
        if error_msg:
            self.lbl_stats.setText(f"❌ {error_msg}")
            self.lbl_stats.setStyleSheet(
                f"color: {theme.ERROR}; font-family: {theme.MONO}; font-size: 11px;"
            )
            return

        self.lbl_stats.setText(
            f"{stats.get('cubiertos', 0)} covered  ·  "
            f"{stats.get('no_cubiertos', 0)} uncovered  ·  "
            f"{stats.get('camiones_usados', 0)} trucks"
        )
        self._populate_routes(day_data.get("routes_csv", ""))
        self._populate_uncovered(day_data.get("uncovered_csv", ""))
        map_html = day_data.get("map_html", "")
        if HAS_WEBENGINE and map_html:
            self.web_view.setHtml(map_html)
        self.btn_export_routes.setEnabled(True)
        self.btn_export_uncovered.setEnabled(True)

    def _populate_routes(self, routes_csv: str):
        import io as _io
        self.table.setRowCount(0)
        self._route_rows = []
        if not routes_csv:
            return
        reader = csv.DictReader(_io.StringIO(routes_csv))
        for row in reader:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._route_rows.append(row)
            fields = ["Camión", "Número de Orden", "RUT", "Nombre cliente",
                      "Dirección cliente", "Comuna", "Hora estimada"]
            for col_idx, field in enumerate(fields):
                val = str(row.get(field, ""))
                item = QTableWidgetItem(val)
                if col_idx == 0:
                    item.setForeground(QColor(theme.ACCENT))
                    item.setFont(QFont(theme.MONO, 12, QFont.Weight.Bold))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                elif col_idx == 6:
                    item.setForeground(QColor(theme.ACCENT2))
                    item.setFont(QFont(theme.MONO, 12))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    item.setForeground(QColor(theme.TEXT))
                self.table.setItem(r, col_idx, item)

    def _populate_uncovered(self, uncovered_csv: str):
        import io as _io
        self.table_uncovered.setRowCount(0)
        self._uncovered_rows = []
        if not uncovered_csv:
            return
        reader = csv.DictReader(_io.StringIO(uncovered_csv))
        for row in reader:
            r = self.table_uncovered.rowCount()
            self.table_uncovered.insertRow(r)
            self._uncovered_rows.append(row)
            fields = ["RUT", "Nombre cliente", "Dirección cliente",
                      "Número de Orden", "Monto Pedido", "Motivo"]
            for col_idx, field in enumerate(fields):
                val = str(row.get(field, ""))
                item = QTableWidgetItem(val)
                if col_idx == 5: # Motivo is now 5
                    item.setForeground(QColor(theme.ERROR))
                else:
                    item.setForeground(QColor(theme.TEXT))
                self.table_uncovered.setItem(r, col_idx, item)

    def _export_routes(self):
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Routes", "routes.csv", "CSV Files (*.csv)"
        )
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write(self._result.get("routes_csv", ""))

    def _export_uncovered(self):
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Uncovered", "uncovered.csv", "CSV Files (*.csv)"
        )
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write(self._result.get("uncovered_csv", ""))


# ── Main view ─────────────────────────────────────────────────────────────

class MainView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.health_worker = None
        self.progress_worker = None
        self._result_data = None
        self.user_id = None
        self.username = None
        self._build_ui()
        self._start_health_check()

    def set_user(self, user_id: int, username: str):
        """Establece el usuario autenticado y actualiza el header."""
        self.user_id = user_id
        self.username = username
        if hasattr(self, 'lbl_user'):
            self.lbl_user.setText(f"👤 {username}")
            self.lbl_user.show()

    def _start_health_check(self):
        url = "http://localhost:8000"
        self.health_worker = HealthWorker(url, parent=self)
        self.health_worker.connected.connect(self._update_connection_status)
        self.health_worker.start()

    def _update_connection_status(self, is_connected: bool):
        if is_connected:
            self.dot.set_ok()
        else:
            self.dot.set_error()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._header())

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        root_layout.addWidget(self.progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        left_results = self._results_panel()
        right_inputs = self._input_panel()

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

        self.lbl_user = QLabel("")
        self.lbl_user.setStyleSheet(
            f"color: {theme.ACCENT2}; font-size: 12px; font-family: {theme.MONO}; font-weight: bold;"
        )
        self.lbl_user.hide()

        hl.addWidget(brand)
        hl.addSpacing(20)
        hl.addWidget(subtitle)
        hl.addStretch()
        hl.addWidget(self.lbl_user)
        hl.addSpacing(12)
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
        self.inp_address = make_input("lat, lon  (e.g. -33.4489, -70.6693)")
        origin_card.add_row("Coordinates", self.inp_address)
        layout.addWidget(origin_card)

        # ── General Setup ──
        general_card = SectionCard("03 — General Parameters")

        self.inp_url = make_input("http://localhost:8000/optimize")
        self.inp_url.setText("http://localhost:8000/optimize")
        general_card.add_row("API URL", self.inp_url)

        self.inp_runtime = make_input("e.g. 10")
        general_card.add_row("Model Runtime (seconds)", self.inp_runtime)

        wt_layout = QHBoxLayout()
        self.inp_worktime_start = make_input("Start (e.g. 09:00)")
        self.inp_worktime_start.setText("09:00")
        self.inp_worktime_end = make_input("End (e.g. 17:00)")
        self.inp_worktime_end.setText("17:00")
        wt_layout.addWidget(self.inp_worktime_start)
        wt_layout.addWidget(self.inp_worktime_end)
        wl = QWidget()
        wl.setLayout(wt_layout)
        wl.layout().setContentsMargins(0, 0, 0, 0)
        general_card.add_row("WorkTime windows", wl)

        layout.addWidget(general_card)

        # ── Datasets (two files) ──
        csv_card = SectionCard("04 — Datasets")

        self.file_ventas = FilePickerButton()
        self.file_ventas.setText("▸  Ventas CSV (click to select)")
        csv_card.add_row("Archivo de Ventas", self.file_ventas)

        self.file_detalle = FilePickerButton()
        self.file_detalle.setText("▸  Detalle CSV/XLSX (click to select)")
        csv_card.add_row("Archivo de Detalle", self.file_detalle)

        hint = QLabel(
            "Ventas: RUT, Nombre cliente, Dirección cliente, Fecha de Pedido, "
            "Número de Orden, Monto Pedido, Fecha de despacho Solicitada\n"
            "Detalle: Número de Orden, SKU, Cantidad, dimensiones, pesos"
        )
        hint.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding: 4px 0 0 0;"
        )
        hint.setWordWrap(True)
        csv_card.add_widget(hint)
        
        # Restoration of SaaS "CLEAN & UPDATE DB" buttons
        upload_btn_layout = QHBoxLayout()
        
        self.btn_upload = QPushButton("UPLOAD VENTAS")
        self.btn_upload.setObjectName("btnSecondary")
        self.btn_upload.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_upload.setMinimumHeight(36)
        self.btn_upload.clicked.connect(self._upload)
        upload_btn_layout.addWidget(self.btn_upload)
        
        self.btn_upload_det = QPushButton("UPLOAD DETALLE")
        self.btn_upload_det.setObjectName("btnSecondary")
        self.btn_upload_det.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_upload_det.setMinimumHeight(36)
        self.btn_upload_det.clicked.connect(self._upload_detalle)
        upload_btn_layout.addWidget(self.btn_upload_det)
        
        csv_card.add_layout(upload_btn_layout)

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
        adv_card.add_row("Weight per truck (kg)", self.inp_weight)

        self.inp_deliveries = make_input("e.g. 150")
        self.inp_deliveries.setText("150")
        adv_card.add_row("Max deliveries / day", self.inp_deliveries)

        self.advanced_layout.addWidget(adv_card)
        layout.addWidget(self.advanced_container)

        layout.addStretch()

        # ── Submit ──
        btn_row = QHBoxLayout()
        self.btn_clear = QPushButton("CLEAR")
        self.btn_clear.setObjectName("btnSecondary")
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setStyleSheet(
            f"background-color: {theme.ERROR}; color: white; border: none; font-weight: bold; border-radius: 4px;"
        )
        self.btn_clear.clicked.connect(self._clear)

        self.btn_run = QPushButton("RUN THE MODEL")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.setMinimumHeight(44)
        self.btn_run.setStyleSheet(
            f"background-color: {theme.SUCCESS}; color: white; border: none; font-weight: bold; border-radius: 4px;"
        )
        self.btn_run.clicked.connect(self._submit)

        btn_row.addWidget(self.btn_clear, 1)
        btn_row.addWidget(self.btn_run, 2)
        layout.addLayout(btn_row)

        scroll.setWidget(panel)
        return scroll

    def _toggle_advanced(self):
        is_visible = self.advanced_container.isVisible()
        self.advanced_container.setVisible(not is_visible)
        self.btn_advanced.setText(
            "Advanced parameters ▲" if not is_visible else "Advanced parameters ▼"
        )

    # ── Results panel (outer day tabs) ────────────────────────────────────────

    def _results_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {theme.BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 20, 20, 20)
        layout.setSpacing(12)

        # Header row
        hdr = QHBoxLayout()
        results_title = QLabel("    RESULTS")
        results_title.setObjectName("sectionTitle")
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(
            f"color: {theme.ACCENT2}; font-family: {theme.MONO}; font-size: 11px;"
        )
        hdr.addWidget(results_title)
        hdr.addSpacing(16)
        hdr.addWidget(self.lbl_count)
        hdr.addStretch()
        layout.addLayout(hdr)

        # Live stage label
        self.lbl_stage = QLabel("")
        self.lbl_stage.setStyleSheet(
            f"color: {theme.ACCENT}; font-family: {theme.MONO}; font-size: 11px; padding-left: 4px;"
        )
        layout.addWidget(self.lbl_stage)

        # Outer day tabs
        self.day_tabs = QTabWidget()
        self.day_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {theme.BORDER}; background: {theme.BG}; }}
            QTabBar::tab {{ background: {theme.SURFACE}; color: {theme.TEXT_DIM};
                           padding: 8px 18px; border: 1px solid {theme.BORDER};
                           font-family: {theme.MONO}; font-size: 11px; }}
            QTabBar::tab:selected {{ background: {theme.BG}; color: {theme.ACCENT2};
                                    border-bottom: 2px solid {theme.ACCENT2}; }}
        """)
        layout.addWidget(self.day_tabs, 1)

        # Empty state (shown until first result)
        self.empty_lbl = QLabel("No results yet.\nConfigure parameters and run the optimizer.")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-family: {theme.MONO}; font-size: 13px; line-height: 2;"
        )
        layout.addWidget(self.empty_lbl)

        return panel

    # ── Status ────────────────────────────────────────────────────────────────

    def _set_global_status(self, msg: str, kind: str = "idle"):
        if self.parent() and hasattr(self.parent(), "_set_status"):
            self.parent()._set_status(msg, kind)

    # ── SaaS DB Upload Methods ───────────────────────────────────────────────

    def _upload(self):
        if not self.file_ventas.path:
            QMessageBox.warning(self, "No file", "Please select a Ventas CSV file first.")
            return
            
        url = "http://localhost:8000/upload" # Hardcoded for now
        self.btn_upload.setEnabled(False)
        self.btn_upload.setText("CLEANING…")
        self._set_global_status("Uploading and cleaning dataset…", "busy")
        
        from frontend.workers.upload_worker import UploadWorker
        self.upload_worker = UploadWorker(url, self.file_ventas.path)
        self.upload_worker.finished.connect(self._on_upload_done)
        self.upload_worker.error.connect(self._on_upload_error)
        self.upload_worker.start()

    def _on_upload_done(self, res: dict):
        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("UPLOAD VENTAS")
        
        msg = res.get("message", "Upload complete")
        self._set_global_status(msg, "ok")
        
        success = res.get("success_count", 0)
        errors = res.get("error_count", 0)
        details = res.get("details", [])
        
        if errors > 0:
            err_msg = "\n".join([f"• Order {d['numero_orden']}: {d['error']}" for d in details if d['status'] == 'error'][:10])
            if len([d for d in details if d['status'] == 'error']) > 10:
                err_msg += "\n... (and more)"
            
            QMessageBox.warning(self, "Upload Summary", 
                               f"{msg}\n\nSome errors occurred:\n{err_msg}")
        else:
            QMessageBox.information(self, "Success", msg)

    def _on_upload_error(self, msg: str):
        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("UPLOAD VENTAS")
        self._set_global_status(f"Error: {msg}", "err")
        QMessageBox.critical(self, "Upload Error", msg)

    def _upload_detalle(self):
        if not self.file_detalle.path:
            QMessageBox.warning(self, "No file", "Please select a Detalle CSV/XLSX file first.")
            return
            
        url = "http://localhost:8000/upload-detalle"
        self.btn_upload_det.setEnabled(False)
        self.btn_upload_det.setText("CLEANING…")
        self._set_global_status("Uploading and cleaning details…", "busy")
        
        from frontend.workers.upload_worker import UploadWorker
        self.upload_worker_det = UploadWorker(url, self.file_detalle.path)
        self.upload_worker_det.finished.connect(self._on_upload_det_done)
        self.upload_worker_det.error.connect(self._on_upload_det_error)
        self.upload_worker_det.start()

    def _on_upload_det_done(self, res: dict):
        self.btn_upload_det.setEnabled(True)
        self.btn_upload_det.setText("UPLOAD DETALLE")
        msg = res.get("message", "Upload complete")
        self._set_global_status(msg, "ok")
        QMessageBox.information(self, "Success", msg)

    def _on_upload_det_error(self, msg: str):
        self.btn_upload_det.setEnabled(True)
        self.btn_upload_det.setText("UPLOAD DETALLE")
        self._set_global_status(f"Error: {msg}", "err")
        QMessageBox.critical(self, "Upload Error", msg)

    # ── Clear ─────────────────────────────────────────────────────────────────

    def _clear(self):
        for w in (self.inp_trucks, self.inp_address, self.inp_runtime):
            w.clear()

        self.inp_worktime_start.setText("09:00")
        self.inp_worktime_end.setText("17:00")
        self.inp_kml.setText("6.4")
        self.inp_space.setText("9")
        self.inp_weight.setText("2000")
        self.inp_deliveries.setText("150")

        self.file_ventas.path = None
        self.file_ventas.setText("▸  Ventas CSV (click to select)")
        self.file_ventas.setStyleSheet("")
        self.file_detalle.path = None
        self.file_detalle.setText("▸  Detalle CSV/XLSX (click to select)")
        self.file_detalle.setStyleSheet("")

        # Clear day tabs
        self.day_tabs.clear()
        self.empty_lbl.show()
        self.lbl_count.setText("")
        self.dot.reset()
        self._result_data = None
        self._set_global_status("Cleared", "idle")

    # ── Validate ──────────────────────────────────────────────────────────────

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

        runtime_txt = self.inp_runtime.text().strip()
        if runtime_txt and not runtime_txt.isdigit():
            errors.append("Runtime must be an integer.")

        address = self.inp_address.text().strip()
        try:
            lat, lon = address.split(",")
            lat = float(lat.strip())
            lon = float(lon.strip())
        except ValueError:
            lat, lon = None, None
            errors.append("Coordinates must be 'latitude, longitude'.")

        if not address:
            errors.append("Distribution center coordinates are required.")

        deliveries_txt = self.inp_deliveries.text().strip()
        try:
            deliveries_per_day = int(deliveries_txt)
            if deliveries_per_day < 1:
                raise ValueError
        except ValueError:
            deliveries_per_day = 150
            errors.append("Max deliveries / day must be a positive integer.")

        # SaaS change: Files are optional if the user wants to use database data
        using_db = False
        if not self.file_ventas.path and not self.file_detalle.path:
            using_db = True
        elif not self.file_ventas.path or not self.file_detalle.path:
            errors.append("You must select BOTH Ventas and Detalle files, or leave BOTH empty to use database data.")

        if errors:
            QMessageBox.warning(self, "Validation Error", "\n".join(errors))
            return None

        return {
            "num_trucks":        int(trucks_txt),
            "km_per_liter":      kml,
            "space_per_truck":   space,
            "weight_per_truck":  weight,
            "model_runtime":     int(runtime_txt) if runtime_txt else None,
            "worktime_windows":  f"{self.inp_worktime_start.text().strip()}-{self.inp_worktime_end.text().strip()}",
            "depot_address":     [lat, lon],
            "deliveries_per_day": deliveries_per_day,
        }

    # ── Submit ────────────────────────────────────────────────────────────────

    def _submit(self):
        params = self._validate()
        if params is None:
            return

        if self.user_id is not None:
            params["user_id"] = self.user_id

        url = self.inp_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Backend URL is required.")
            return

        self.btn_run.setEnabled(False)
        self.btn_run.setText("RUNNING…")
        self.progress.show()
        self.dot.start_busy()
        self._set_global_status("Sending request to backend…", "busy")
        self.lbl_stage.setText("⟳ Connecting…")

        self.worker = RequestWorker(
            url, params,
            self.file_ventas.path,
            self.file_detalle.path,
        )
        self.worker.finished.connect(self._on_result)
        self.worker.progress_update.connect(self._on_stage_update) # New signal connection
        self.worker.error.connect(self._on_error)
        self.worker.start()

        # We can keep ProgressWorker for other global stages if needed, 
        # but for optimization, RequestWorker now handles its own polling.
        base_url = url.rsplit("/", 1)[0]
        self.progress_worker = ProgressWorker(base_url, parent=self)
        self.progress_worker.stage_updated.connect(self._on_stage_update)
        self.progress_worker.start()

    def _on_stage_update(self, stage: str):
        if stage:
            self.lbl_stage.setText(f"⟳ {stage}")
        else:
            self.lbl_stage.setText("")

    def _stop_progress_worker(self):
        if self.progress_worker:
            self.progress_worker.stop()
            self.progress_worker.wait(3000)
            self.progress_worker = None

    def _on_result(self, data: dict):
        self._stop_progress_worker()
        self.lbl_stage.setText("")
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("RUN THE MODEL")
        self.dot.set_ok()
        self._result_data = data

        days = data.get("days", [])

        # Aggregate stats across all days
        total_covered   = sum(d.get("stats", {}).get("cubiertos", 0) for d in days)
        total_uncovered = sum(d.get("stats", {}).get("no_cubiertos", 0) for d in days)
        total_trucks    = max((d.get("stats", {}).get("camiones_usados", 0) for d in days), default=0)

        self._set_global_status(
            f"Complete — {len(days)} day(s), {total_covered} covered, "
            f"{total_uncovered} uncovered, {total_trucks} trucks max.",
            "ok"
        )
        self.lbl_count.setText(
            f"{len(days)} days  ·  {total_covered} covered  ·  {total_uncovered} uncovered"
        )

        # Clear and rebuild day tabs
        self.day_tabs.clear()
        self.empty_lbl.hide()

        for day_data in days:
            label = day_data.get("date", "?")
            widget = DayResultWidget(parent=self)
            widget.populate(day_data)
            self.day_tabs.addTab(widget, f"📅 {label}")

    def _on_error(self, msg: str):
        self._stop_progress_worker()
        self.lbl_stage.setText("")
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("RUN THE MODEL")
        self.dot.set_error()
        self._set_global_status(f"Error: {msg}", "err")
        QMessageBox.critical(self, "Backend Error", msg)


def main():
    from PyQt6.QtWidgets import QApplication
    import sys
    app = QApplication(sys.argv)
    app.setApplicationName("Dispatcher")
    window = MainView()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
