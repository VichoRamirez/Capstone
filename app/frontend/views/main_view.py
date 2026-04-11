"""
Main view (DispatcherWindow) logic and layout.
Two-file upload (ventas + detalle), embedded map, route table, and export.
Results are shown per calendar day in outer tabs.
"""

import os
import csv
import json
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QSplitter, QMessageBox, QScrollArea, QProgressBar, QGridLayout,
    QStatusBar, QFileDialog, QTabWidget, QComboBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

import frontend.resources.styles.theme as theme
from frontend.widgets.components import (
    make_input, SectionCard, PulsingDot
)
from frontend.workers.request_worker import RequestWorker
from frontend.workers.health_worker import HealthWorker
from frontend.workers.progress_worker import ProgressWorker
from frontend.workers.data_hub_worker import DataHubDashboardWorker, DataHubDashboardDbWorker, DataHubAddressValidationWorker


# ── Per-Day result widget ─────────────────────────────────────────────────

class DayResultWidget(QWidget):
    """Shows one day's optimisation results: Route Schedule, Map, Uncovered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._route_rows = []
        self._uncovered_rows = []
        self._map_file_path = None
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
            ws = self.web_view.settings()
            ws.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                True,
            )
            ws.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                True,
            )
            self._set_map_placeholder()
            map_layout.addWidget(self.web_view)
        else:
            no_web = QLabel("PyQt6-WebEngine not installed.")
            no_web.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_web.setStyleSheet(f"color: {theme.TEXT_DIM}; font-family: {theme.MONO};")
            map_layout.addWidget(no_web)
        self.tabs.addTab(self.map_container, "🗺️ Map")

        # Tab 3: Uncovered
        self.table_uncovered = QTableWidget(0, 5)
        self.table_uncovered.setHorizontalHeaderLabels(
            ["RUT", "CLIENTE", "DIRECCIÓN", "ORDEN", "MOTIVO"]
        )
        for col in range(5):
            mode = (QHeaderView.ResizeMode.Stretch if col == 2
                    else QHeaderView.ResizeMode.ResizeToContents)
            self.table_uncovered.horizontalHeader().setSectionResizeMode(col, mode)
        self.table_uncovered.verticalHeader().setVisible(False)
        self.table_uncovered.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_uncovered.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_uncovered.setShowGrid(False)
        self.table_uncovered.setAlternatingRowColors(True)
        self.tabs.addTab(self.table_uncovered, "⚠️ Uncovered")

        # Tab 4: KPI summary
        self.kpi_tab = QWidget()
        kpi_layout = QVBoxLayout(self.kpi_tab)
        kpi_layout.setContentsMargins(0, 0, 0, 0)
        kpi_layout.setSpacing(8)

        self.kpi_table = QTableWidget(0, 2)
        self.kpi_table.setHorizontalHeaderLabels(["METRIC", "VALUE"])
        self.kpi_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.kpi_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.kpi_table.verticalHeader().setVisible(False)
        self.kpi_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.kpi_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.kpi_table.setShowGrid(False)
        self.kpi_table.setAlternatingRowColors(True)
        self.kpi_table.setStyleSheet(
            self.kpi_table.styleSheet()
            + f"QTableWidget {{ alternate-background-color: rgba(42,48,80,0.3); }}"
        )
        kpi_layout.addWidget(self.kpi_table, 1)

        self.kpi_truck_table = QTableWidget(0, 11)
        self.kpi_truck_table.setHorizontalHeaderLabels(
            [
                "TRUCK",
                "STOPS",
                "DIST (KM)",
                "TRAVEL (MIN)",
                "SERVICE (MIN)",
                "ROUTE (MIN)",
                "LOAD (KG)",
                "LOAD (M³)",
                "FIXED COST",
                "VAR COST",
                "TOTAL COST",
            ]
        )
        for col in range(11):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if col == 0
                else QHeaderView.ResizeMode.ResizeToContents
            )
            self.kpi_truck_table.horizontalHeader().setSectionResizeMode(col, mode)
        self.kpi_truck_table.verticalHeader().setVisible(False)
        self.kpi_truck_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.kpi_truck_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.kpi_truck_table.setShowGrid(False)
        self.kpi_truck_table.setAlternatingRowColors(True)
        self.kpi_truck_table.setStyleSheet(
            self.kpi_truck_table.styleSheet()
            + f"QTableWidget {{ alternate-background-color: rgba(42,48,80,0.3); }}"
        )
        kpi_layout.addWidget(self.kpi_truck_table, 2)

        self.tabs.addTab(self.kpi_tab, "📊 KPI")

        layout.addWidget(self.tabs, 1)

    def _set_map_placeholder(self):
        if HAS_WEBENGINE:
            self.web_view.setHtml(
                "<html><body style='background:#0d1117;color:#8b949e;display:flex;"
                "align-items:center;justify-content:center;height:100vh;font-family:monospace;'>"
                "<p>Run the model to see the route map.</p></body></html>"
            )

    def _cleanup_map_file(self):
        if not self._map_file_path:
            return
        try:
            p = Path(self._map_file_path)
            if p.exists():
                p.unlink()
        except Exception:
            pass
        self._map_file_path = None

    def _load_map_html(self, map_html: str):
        if not HAS_WEBENGINE:
            return
        if not map_html:
            self._set_map_placeholder()
            return

        # For large Folium pages, loading via local file avoids setHtml size limits.
        try:
            from PyQt6.QtCore import QUrl

            maps_dir = Path(tempfile.gettempdir()) / "dispatch_route_maps"
            maps_dir.mkdir(parents=True, exist_ok=True)

            self._cleanup_map_file()
            map_path = maps_dir / f"map_{id(self)}.html"
            map_path.write_text(map_html, encoding="utf-8")
            self._map_file_path = str(map_path)
            self.web_view.load(QUrl.fromLocalFile(str(map_path)))
        except Exception:
            # Fallback for environments where local file loading is restricted.
            self.web_view.setHtml(map_html)

    def closeEvent(self, event):
        self._cleanup_map_file()
        super().closeEvent(event)

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
            self._populate_kpis({})
            return

        self.lbl_stats.setText(
            f"{stats.get('cubiertos', 0)} covered  ·  "
            f"{stats.get('no_cubiertos', 0)} uncovered  ·  "
            f"{stats.get('camiones_usados', 0)} trucks"
        )
        routing_matrix = stats.get("routing_matrix", {}) if isinstance(stats, dict) else {}
        if isinstance(routing_matrix, dict) and routing_matrix:
            src = "OSRM" if bool(routing_matrix.get("osrm_used")) else "fallback"
            self.lbl_stats.setText(self.lbl_stats.text() + f"  ·  matrix: {src}")
            search_meta = routing_matrix.get("search_matrix", {})
            if isinstance(search_meta, dict) and bool(search_meta.get("search_enabled")):
                k = int(search_meta.get("k_nearest", 5) or 5)
                self.lbl_stats.setText(self.lbl_stats.text() + f"  ·  search: Dijkstra(k={k}) + A*->CD")
        self._populate_routes(day_data.get("routes_csv", ""))
        self._populate_uncovered(day_data.get("uncovered_csv", ""))
        self._populate_kpis(stats)
        map_html = day_data.get("map_html", "")
        if HAS_WEBENGINE:
            self._load_map_html(map_html)
        self.btn_export_routes.setEnabled(True)
        self.btn_export_uncovered.setEnabled(True)

    @staticmethod
    def _fmt_float(value, decimals: int = 2) -> str:
        try:
            return f"{float(value):,.{decimals}f}"
        except Exception:
            return "0.00"

    @staticmethod
    def _read_metric_block(block: dict) -> tuple[float, float, float, float]:
        if not isinstance(block, dict):
            return (0.0, 0.0, 0.0, 0.0)
        return (
            float(block.get("total", 0.0) or 0.0),
            float(block.get("min", 0.0) or 0.0),
            float(block.get("max", 0.0) or 0.0),
            float(block.get("avg", 0.0) or 0.0),
        )

    def _set_kpi_summary_rows(self, rows: list[tuple[str, str]]):
        self.kpi_table.setRowCount(0)
        for metric, value in rows:
            r = self.kpi_table.rowCount()
            self.kpi_table.insertRow(r)
            metric_item = QTableWidgetItem(str(metric))
            metric_item.setForeground(QColor(theme.ACCENT))
            metric_item.setFont(QFont(theme.MONO, 11, QFont.Weight.Bold))
            value_item = QTableWidgetItem(str(value))
            value_item.setForeground(QColor(theme.TEXT))
            value_item.setFont(QFont(theme.MONO, 11))
            self.kpi_table.setItem(r, 0, metric_item)
            self.kpi_table.setItem(r, 1, value_item)

    def _populate_kpis(self, stats: dict):
        self.kpi_table.setRowCount(0)
        self.kpi_truck_table.setRowCount(0)
        kpis = stats.get("route_kpis", {}) if isinstance(stats, dict) else {}
        fleet = kpis.get("fleet", {}) if isinstance(kpis, dict) else {}
        by_truck = kpis.get("by_truck", []) if isinstance(kpis, dict) else []

        if not isinstance(fleet, dict) or not fleet:
            self._set_kpi_summary_rows(
                [
                    ("Route KPI", "No detailed KPI data available for this run."),
                ]
            )
            return

        dist_total, dist_min, dist_max, dist_avg = self._read_metric_block(
            fleet.get("distance_km", {})
        )
        route_total, route_min, route_max, route_avg = self._read_metric_block(
            fleet.get("route_time_min", {})
        )
        cost_total, cost_min, cost_max, cost_avg = self._read_metric_block(
            fleet.get("cost_total", {})
        )
        fixed_total, _, _, _ = self._read_metric_block(fleet.get("cost_fixed", {}))
        var_total, _, _, _ = self._read_metric_block(fleet.get("cost_variable", {}))
        trips_total, trips_min, trips_max, trips_avg = self._read_metric_block(
            fleet.get("trips_per_truck", {})
        )
        turnaround_total, turnaround_min, turnaround_max, turnaround_avg = self._read_metric_block(
            fleet.get("turnaround_time_min", {})
        )
        _, traffic_min, traffic_max, traffic_avg = self._read_metric_block(
            fleet.get("traffic_factor_avg", {})
        )
        stops_total, stops_min, stops_max, stops_avg = self._read_metric_block(
            fleet.get("stops_per_truck", {})
        )

        rows = [
            ("Trucks used", f"{int(fleet.get('trucks_used', 0) or 0)}"),
            ("Trips total", f"{self._fmt_float(trips_total, 0)}"),
            ("Trips / truck (min · max · avg)", f"{self._fmt_float(trips_min, 0)} · {self._fmt_float(trips_max, 0)} · {self._fmt_float(trips_avg, 2)}"),
            ("Stops total", f"{self._fmt_float(stops_total, 0)}"),
            ("Stops / truck (min · max · avg)", f"{self._fmt_float(stops_min, 0)} · {self._fmt_float(stops_max, 0)} · {self._fmt_float(stops_avg, 2)}"),
            ("Distance total (km)", self._fmt_float(dist_total, 2)),
            ("Distance / truck (min · max · avg)", f"{self._fmt_float(dist_min, 2)} · {self._fmt_float(dist_max, 2)} · {self._fmt_float(dist_avg, 2)}"),
            ("Traffic factor / truck (min · max · avg)", f"{self._fmt_float(traffic_min, 3)} · {self._fmt_float(traffic_max, 3)} · {self._fmt_float(traffic_avg, 3)}"),
            ("Route time total (min)", self._fmt_float(route_total, 2)),
            ("Route time / truck (min · max · avg)", f"{self._fmt_float(route_min, 2)} · {self._fmt_float(route_max, 2)} · {self._fmt_float(route_avg, 2)}"),
            ("Turnaround total (min)", self._fmt_float(turnaround_total, 2)),
            ("Turnaround / truck (min · max · avg)", f"{self._fmt_float(turnaround_min, 2)} · {self._fmt_float(turnaround_max, 2)} · {self._fmt_float(turnaround_avg, 2)}"),
            ("Cost total", self._fmt_float(cost_total, 2)),
            ("Cost fixed total", self._fmt_float(fixed_total, 2)),
            ("Cost variable total", self._fmt_float(var_total, 2)),
            ("Cost / truck (min · max · avg)", f"{self._fmt_float(cost_min, 2)} · {self._fmt_float(cost_max, 2)} · {self._fmt_float(cost_avg, 2)}"),
        ]
        self._set_kpi_summary_rows(rows)

        if not isinstance(by_truck, list):
            return
        for row in by_truck:
            if not isinstance(row, dict):
                continue
            r = self.kpi_truck_table.rowCount()
            self.kpi_truck_table.insertRow(r)
            vals = [
                str(row.get("truck", "")),
                self._fmt_float(row.get("stops", 0), 0),
                self._fmt_float(row.get("distance_km", 0.0), 2),
                self._fmt_float(row.get("travel_time_min", 0.0), 2),
                self._fmt_float(row.get("service_time_min", 0.0), 2),
                self._fmt_float(row.get("route_time_min", 0.0), 2),
                self._fmt_float(row.get("load_kg", 0.0), 2),
                self._fmt_float(row.get("load_m3", 0.0), 3),
                self._fmt_float(row.get("cost_fixed", 0.0), 2),
                self._fmt_float(row.get("cost_variable", 0.0), 2),
                self._fmt_float(row.get("cost_total", 0.0), 2),
            ]
            for c, value in enumerate(vals):
                item = QTableWidgetItem(value)
                if c == 0:
                    item.setForeground(QColor(theme.ACCENT))
                    item.setFont(QFont(theme.MONO, 11, QFont.Weight.Bold))
                else:
                    item.setForeground(QColor(theme.TEXT))
                    item.setFont(QFont(theme.MONO, 11))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.kpi_truck_table.setItem(r, c, item)

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
                      "Número de Orden", "Motivo"]
            for col_idx, field in enumerate(fields):
                val = str(row.get(field, ""))
                item = QTableWidgetItem(val)
                if col_idx == 4:
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


class GlobalSummaryWidget(QWidget):
    """Shows global aggregated metrics across all optimized days."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self.lbl_head = QLabel("Global summary for current optimization run")
        self.lbl_head.setStyleSheet(
            f"color: {theme.ACCENT2}; font-family: {theme.MONO}; font-size: 11px; padding-left: 4px;"
        )
        layout.addWidget(self.lbl_head)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["METRIC", "VALUE"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
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

    @staticmethod
    def _fmt_num(value, decimals: int = 2) -> str:
        try:
            return f"{float(value):,.{decimals}f}"
        except Exception:
            return "0.00"

    @staticmethod
    def _fmt_int(value) -> str:
        try:
            return f"{int(value):,d}"
        except Exception:
            return "0"

    @staticmethod
    def _fmt_sec(value) -> str:
        try:
            return f"{float(value):.2f} s"
        except Exception:
            return "0.00 s"

    def _set_rows(self, rows: list[tuple[str, str]]):
        self.table.setRowCount(0)
        for metric, value in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            metric_item = QTableWidgetItem(str(metric))
            metric_item.setForeground(QColor(theme.ACCENT))
            metric_item.setFont(QFont(theme.MONO, 11, QFont.Weight.Bold))
            value_item = QTableWidgetItem(str(value))
            value_item.setForeground(QColor(theme.TEXT))
            value_item.setFont(QFont(theme.MONO, 11))
            self.table.setItem(r, 0, metric_item)
            self.table.setItem(r, 1, value_item)

    def populate(self, payload: dict, days: list[dict], cleaning_errors: list):
        gs = payload if isinstance(payload, dict) else {}
        if not gs:
            # Fallback if backend does not provide global stats.
            day_stats = [d.get("stats", {}) for d in (days or []) if isinstance(d, dict)]
            day_stats = [s for s in day_stats if isinstance(s, dict)]
            total_orders = sum(int(s.get("total_puntos", 0) or 0) for s in day_stats if not s.get("error"))
            covered_total = sum(int(s.get("cubiertos", 0) or 0) for s in day_stats if not s.get("error"))
            uncovered_total = sum(int(s.get("no_cubiertos", 0) or 0) for s in day_stats if not s.get("error"))
            cost_values = [float(s.get("costo_base", 0.0) or 0.0) for s in day_stats if not s.get("error") and s.get("costo_base") is not None]
            gs = {
                "days_processed": len(days or []),
                "orders_total": total_orders,
                "covered_total": covered_total,
                "uncovered_total": uncovered_total,
                "coverage_rate_pct": round((100.0 * covered_total / total_orders), 2) if total_orders else 0.0,
                "cost_total": sum(cost_values),
                "cost_avg_per_day": (sum(cost_values) / len(cost_values)) if cost_values else 0.0,
                "geocode_failed_addresses": sum(1 for e in (cleaning_errors or []) if isinstance(e, dict) and str(e.get("origen", "")).lower() == "geocodificación"),
                "cleaning_errors_total": len(cleaning_errors or []),
                "execution_time_sec": {},
            }

        exec_meta = gs.get("execution_time_sec", {}) if isinstance(gs.get("execution_time_sec"), dict) else {}
        pipeline = exec_meta.get("pipeline_phase", {}) if isinstance(exec_meta.get("pipeline_phase"), dict) else {}
        opt_phase = exec_meta.get("optimization_phase_aggregated", {}) if isinstance(exec_meta.get("optimization_phase_aggregated"), dict) else {}

        rows = [
            ("Days processed", self._fmt_int(gs.get("days_processed", 0))),
            ("Days with errors", self._fmt_int(gs.get("days_error", 0))),
            ("Orders total", self._fmt_int(gs.get("orders_total", 0))),
            ("Covered total", self._fmt_int(gs.get("covered_total", 0))),
            ("Uncovered total", self._fmt_int(gs.get("uncovered_total", 0))),
            ("Coverage rate", f"{self._fmt_num(gs.get('coverage_rate_pct', 0.0), 2)}%"),
            ("Trucks peak (day)", self._fmt_int(gs.get("trucks_peak", 0))),
            ("Cost total", self._fmt_num(gs.get("cost_total", 0.0), 2)),
            ("Cost average / day", self._fmt_num(gs.get("cost_avg_per_day", 0.0), 2)),
            ("Failed addresses (geocoding)", self._fmt_int(gs.get("geocode_failed_addresses", 0))),
            ("Cleaning errors (total)", self._fmt_int(gs.get("cleaning_errors_total", 0))),
            ("Execution total", self._fmt_sec(exec_meta.get("total", 0.0))),
            ("Phase - cleaning+geocoding", self._fmt_sec(pipeline.get("cleaning_geocoding_sec", 0.0))),
            ("Phase - detalle merge", self._fmt_sec(pipeline.get("detalle_merge_sec", 0.0))),
            ("Phase - batch build", self._fmt_sec(pipeline.get("batch_build_sec", 0.0))),
            ("Phase - optimization days", self._fmt_sec(pipeline.get("optimization_days_sec", 0.0))),
            ("Phase - summary", self._fmt_sec(pipeline.get("summary_sec", 0.0))),
            ("Optimization - matrix generation", self._fmt_sec(opt_phase.get("matrix_generation_sec", 0.0))),
            ("Optimization - model preparation", self._fmt_sec(opt_phase.get("model_preparation_sec", 0.0))),
            ("Optimization - solver", self._fmt_sec(opt_phase.get("solver_sec", 0.0))),
            ("Optimization - postprocess", self._fmt_sec(opt_phase.get("postprocess_sec", 0.0))),
            ("Optimization - output generation", self._fmt_sec(opt_phase.get("output_generation_sec", 0.0))),
        ]

        self._set_rows(rows)


# ── Main view ─────────────────────────────────────────────────────────────

class MainView(QWidget):
    logout_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.health_worker = None
        self.progress_worker = None
        self.datahub_worker = None
        self.datahub_db_worker = None
        self.datahub_validation_worker = None
        self._validated_depot_coords = None
        self._validated_depot_query = ""
        self._last_form_config = None
        self._last_form_config_path = (
            Path(__file__).resolve().parents[2] / "cache" / "last_form_config.json"
        )
        self._project_root = Path(__file__).resolve().parents[3]
        self._clean_data_dir = self._project_root / "CLEAN_DATA"
        self._raw_data_dir = self._project_root / "RAW_DATA"
        self._test_data_dir = self._project_root / "TEST_DATA"
        self.ventas_path: str | None = None
        self.detalle_path: str | None = None
        self._dataset_combo_internal_change = False
        self._dataset_dialog_open = False
        self._dataset_dialog_cooldown_until = 0.0
        self._dataset_import_armed_until = {"ventas": 0.0, "detalle": 0.0}
        self._diesel_fetch_in_progress = False
        self._datahub_validation_rows: list[dict] = []
        self.datahub_ventas_path: str | None = None
        self.datahub_detalle_path: str | None = None
        self._result_data = None
        self.user_id: int | None = None
        self.username: str | None = None
        self._build_ui()
        self._load_last_form_config()
        self._start_health_check()
        QTimer.singleShot(700, lambda: self._fetch_diesel_price_from_api(silent=True))

    def set_user(self, user_id: int, username: str):
        """Recibe la sesión iniciada desde LoginView."""
        self.user_id = user_id
        self.username = username
        if hasattr(self, "user_label") and self.user_label is not None:
            self.user_label.setText(f"Sesión: {username}")

    def _stop_health_worker(self):
        if self.health_worker:
            self.health_worker.stop()
            self.health_worker = None

    def _stop_request_worker(self):
        if not self.worker:
            return
        if self.worker.isRunning():
            try:
                if hasattr(self.worker, "stop"):
                    self.worker.stop()
            except Exception:
                pass
            self.worker.wait(3000)
        self.worker = None

    def shutdown(self):
        self._stop_datahub_validation_worker()
        self._stop_datahub_worker()
        self._stop_progress_worker()
        self._stop_request_worker()
        self._stop_health_worker()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

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

    def _stop_datahub_worker(self):
        if not self.datahub_worker:
            return
        if self.datahub_worker.isRunning():
            try:
                if hasattr(self.datahub_worker, "stop"):
                    self.datahub_worker.stop()
            except Exception:
                pass
            self.datahub_worker.wait(3000)
        self.datahub_worker = None

    def _stop_datahub_db_worker(self):
        if not self.datahub_db_worker:
            return
        if self.datahub_db_worker.isRunning():
            try:
                if hasattr(self.datahub_db_worker, "stop"):
                    self.datahub_db_worker.stop()
            except Exception:
                pass
            self.datahub_db_worker.wait(3000)
        self.datahub_db_worker = None

    def _stop_datahub_validation_worker(self):
        if not self.datahub_validation_worker:
            return
        if self.datahub_validation_worker.isRunning():
            try:
                if hasattr(self.datahub_validation_worker, "stop"):
                    self.datahub_validation_worker.stop()
            except Exception:
                pass
            self.datahub_validation_worker.wait(3000)
        self.datahub_validation_worker = None

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

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("workspaceTabs")
        self.workspace_tabs.setStyleSheet(f"""
            QTabWidget#workspaceTabs::pane {{
                border-top: 1px solid {theme.BORDER};
                background: {theme.BG};
            }}
            QTabWidget#workspaceTabs QTabBar::tab {{
                background: {theme.SURFACE};
                color: {theme.TEXT_DIM};
                border: 1px solid {theme.BORDER};
                border-bottom: none;
                padding: 10px 16px;
                min-width: 190px;
                font-family: {theme.MONO};
                font-size: 12px;
            }}
            QTabWidget#workspaceTabs QTabBar::tab:selected {{
                color: {theme.ACCENT2};
                background: {theme.BG};
            }}
            QTabWidget#workspaceTabs QTabBar::tab:hover {{
                color: {theme.TEXT};
            }}
        """)
        self.workspace_tabs.currentChanged.connect(self._on_workspace_tab_changed)

        self.data_hub_page = self._data_hub_workspace()
        self.route_optimizer_page = self._route_optimizer_workspace()
        self.history_wip_page = self._historical_results_wip_workspace()

        self.workspace_tabs.addTab(self.data_hub_page, "📥 Data & Operations")
        self.workspace_tabs.addTab(self.route_optimizer_page, "🧭 Route Optimizer")
        self.workspace_tabs.addTab(self.history_wip_page, "🕘 Resultados previos (WIP)")
        self.workspace_tabs.setCurrentIndex(1)

        root_layout.addWidget(self.workspace_tabs, 1)
        self._on_workspace_tab_changed(self.workspace_tabs.currentIndex())

    def _route_optimizer_workspace(self) -> QWidget:
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
        return content

    def _on_workspace_tab_changed(self, index: int):
        label_map = {
            0: "Data & Operations  /  Current Orders Control",
            1: "Route Optimizer  /  Fleet Management",
            2: "Resultados previos  /  Historical Analytics (WIP)",
        }
        text = label_map.get(int(index), "DISPATCH")
        if hasattr(self, "lbl_header_subtitle"):
            self.lbl_header_subtitle.setText(text)
        if index != 1:
            self.lbl_stage.setText("")

    def _data_hub_workspace(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {theme.BG}; border: none; }}")

        panel = QWidget()
        panel.setStyleSheet(f"background: {theme.BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        route_map_card = SectionCard("00 — Implementation Route Map")
        self.tbl_datahub_route_map = QTableWidget(4, 3)
        self.tbl_datahub_route_map.setHorizontalHeaderLabels(["FASE", "OBJETIVO", "ESTADO"])
        self.tbl_datahub_route_map.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_route_map.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl_datahub_route_map.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_route_map.verticalHeader().setVisible(False)
        self.tbl_datahub_route_map.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_datahub_route_map.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.tbl_datahub_route_map.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        route_map_rows = [
            ("Fase 1", "Carga manual + dashboard actual + cola de revisión", "EN CURSO"),
            ("Fase 2", "Validación masiva de direcciones + reparación asistida", "EN CURSO"),
            ("Fase 3", "Edición masiva de ventanas de entrega", "WIP"),
            ("Fase 4", "Conector CRM + corrida nocturna automática", "WIP"),
        ]
        for r, (fase, objetivo, estado) in enumerate(route_map_rows):
            self.tbl_datahub_route_map.setItem(r, 0, QTableWidgetItem(fase))
            self.tbl_datahub_route_map.setItem(r, 1, QTableWidgetItem(objetivo))
            state_item = QTableWidgetItem(estado)
            if estado == "EN CURSO":
                state_item.setForeground(QColor(theme.ACCENT2))
            else:
                state_item.setForeground(QColor(theme.TEXT_DIM))
            self.tbl_datahub_route_map.setItem(r, 2, state_item)
        self.tbl_datahub_route_map.setMinimumHeight(180)
        route_map_card.add_widget(self.tbl_datahub_route_map)
        layout.addWidget(route_map_card)

        ingest_card = SectionCard("01 — Data Intake & Sync")
        source_row = QHBoxLayout()
        self.cmb_datahub_source = QComboBox()
        self.cmb_datahub_source.setObjectName("datasetCombo")
        self.cmb_datahub_source.addItem("Manual CSV / XLSX upload")
        self.cmb_datahub_source.addItem("CRM realtime connector (WIP)")
        self.cmb_datahub_source.addItem("API pull per schedule (WIP)")
        source_row.addWidget(self.cmb_datahub_source, 2)
        self.btn_datahub_connect_crm = QPushButton("CONNECT CRM (WIP)")
        self.btn_datahub_connect_crm.setObjectName("btnSecondary")
        self.btn_datahub_connect_crm.setEnabled(False)
        source_row.addWidget(self.btn_datahub_connect_crm, 1)
        ingest_card.add_row("Data source", self._with_layout(source_row))

        files_row = QHBoxLayout()
        self.btn_datahub_upload_orders = QPushButton("UPLOAD CURRENT ORDERS")
        self.btn_datahub_upload_orders.setObjectName("btnSecondary")
        self.btn_datahub_upload_orders.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_datahub_upload_orders.clicked.connect(
            lambda: self._pick_datahub_file("ventas")
        )
        self.btn_datahub_upload_detail = QPushButton("UPLOAD ORDER DETAILS")
        self.btn_datahub_upload_detail.setObjectName("btnSecondary")
        self.btn_datahub_upload_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_datahub_upload_detail.clicked.connect(
            lambda: self._pick_datahub_file("detalle")
        )
        files_row.addWidget(self.btn_datahub_upload_orders)
        files_row.addWidget(self.btn_datahub_upload_detail)
        ingest_card.add_row("Manual load", self._with_layout(files_row))

        self.lbl_datahub_orders_file = QLabel("Ventas file: not selected.")
        self.lbl_datahub_orders_file.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )
        ingest_card.add_widget(self.lbl_datahub_orders_file)

        self.lbl_datahub_detail_file = QLabel("Detalle file: not selected.")
        self.lbl_datahub_detail_file.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )
        ingest_card.add_widget(self.lbl_datahub_detail_file)

        action_row = QHBoxLayout()
        self.btn_datahub_load_db = QPushButton("CARGAR DESDE BD")
        self.btn_datahub_load_db.setObjectName("btnPrimary")
        self.btn_datahub_load_db.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_datahub_load_db.clicked.connect(self._run_datahub_dashboard_db)
        action_row.addWidget(self.btn_datahub_load_db)

        self.btn_datahub_analyze = QPushButton("ANALYZE CURRENT DATA")
        self.btn_datahub_analyze.setObjectName("btnSecondary")
        self.btn_datahub_analyze.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_datahub_analyze.clicked.connect(self._run_datahub_dashboard)
        action_row.addWidget(self.btn_datahub_analyze)

        self.btn_datahub_validate_addresses = QPushButton("VERIFY ADDRESSES")
        self.btn_datahub_validate_addresses.setObjectName("btnSecondary")
        self.btn_datahub_validate_addresses.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_datahub_validate_addresses.clicked.connect(self._run_datahub_address_validation)
        action_row.addWidget(self.btn_datahub_validate_addresses)

        self.btn_datahub_apply_repairs = QPushButton("APPLY SUGGESTED REPAIRS")
        self.btn_datahub_apply_repairs.setObjectName("btnSecondary")
        self.btn_datahub_apply_repairs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_datahub_apply_repairs.setEnabled(False)
        self.btn_datahub_apply_repairs.clicked.connect(self._apply_datahub_suggested_repairs)
        action_row.addWidget(self.btn_datahub_apply_repairs)
        ingest_card.add_layout(action_row)

        self.lbl_datahub_status = QLabel("Ready. Load files and run analysis.")
        self.lbl_datahub_status.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )
        ingest_card.add_widget(self.lbl_datahub_status)

        ingest_note = QLabel(
            "This workspace manages current data before optimization: load files, inspect quality, "
            "check deadlines, and prepare routing input."
        )
        ingest_note.setWordWrap(True)
        ingest_note.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; font-family: {theme.MONO};"
        )
        ingest_card.add_widget(ingest_note)
        layout.addWidget(ingest_card)

        kpi_card = SectionCard("02 — Current Orders Dashboard")
        kpi_grid = QGridLayout()
        kpi_grid.setHorizontalSpacing(10)
        kpi_grid.setVerticalSpacing(10)
        self._datahub_kpi_labels = []
        kpis = [
            ("Pedidos actuales", "0", theme.ACCENT2),
            ("En deadline hoy", "0", theme.ACCENT2),
            ("Comunas activas", "0", theme.ACCENT2),
            ("Monto agregado (CLP)", "0", theme.ACCENT),
            ("Pedidos atrasados", "0", theme.ERROR),
            ("Clientes únicos", "0", theme.ACCENT2),
        ]
        for idx, (label, value, color) in enumerate(kpis):
            box = QFrame()
            box.setObjectName("card")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(12, 10, 12, 10)
            box_layout.setSpacing(4)
            lbl = QLabel(label.upper())
            lbl.setStyleSheet(
                f"color: {theme.TEXT_DIM}; font-size: 10px; letter-spacing: 1px; font-family: {theme.MONO};"
            )
            val = QLabel(value)
            val.setStyleSheet(
                f"color: {color}; font-size: 22px; font-weight: bold; font-family: {theme.MONO};"
            )
            box_layout.addWidget(lbl)
            box_layout.addWidget(val)
            self._datahub_kpi_labels.append(val)
            row = idx // 3
            col = idx % 3
            kpi_grid.addWidget(box, row, col)
        kpi_card.add_layout(kpi_grid)
        layout.addWidget(kpi_card)

        dist_card = SectionCard("03 — Client Distribution & Address Health")
        dist_row = QHBoxLayout()

        self.tbl_datahub_comuna = QTableWidget(0, 3)
        self.tbl_datahub_comuna.setHorizontalHeaderLabels(["COMUNA", "PEDIDOS", "MONTO CLP"])
        self.tbl_datahub_comuna.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_datahub_comuna.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_comuna.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_comuna.verticalHeader().setVisible(False)
        self.tbl_datahub_comuna.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_datahub_comuna.setMinimumHeight(220)

        map_placeholder = QFrame()
        map_placeholder.setObjectName("card")
        map_placeholder_layout = QVBoxLayout(map_placeholder)
        map_placeholder_layout.setContentsMargins(12, 12, 12, 12)
        map_placeholder_layout.setSpacing(8)
        map_title = QLabel("ROUTEMAP / DEADLINE SNAPSHOT")
        map_title.setObjectName("sectionTitle")
        self.lbl_datahub_deadline = QLabel(
            "No analysis yet."
        )
        self.lbl_datahub_deadline.setWordWrap(True)
        self.lbl_datahub_deadline.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; font-family: {theme.MONO};"
        )
        map_placeholder_layout.addWidget(map_title)
        map_placeholder_layout.addWidget(self.lbl_datahub_deadline)
        map_placeholder_layout.addStretch()

        dist_row.addWidget(self.tbl_datahub_comuna, 1)
        dist_row.addWidget(map_placeholder, 1)
        dist_card.add_layout(dist_row)
        layout.addWidget(dist_card)

        edit_card = SectionCard("04 — Massive Delivery Window Reassignment")
        rules_row = QHBoxLayout()
        from_slot = make_input("From (e.g. 09:00-13:00)")
        to_slot = make_input("To (e.g. 11:00-15:00)")
        rules_row.addWidget(from_slot)
        rules_row.addWidget(to_slot)
        edit_card.add_row("Bulk window rule", self._with_layout(rules_row))

        filters_row = QHBoxLayout()
        comuna_filter = QComboBox()
        comuna_filter.setObjectName("datasetCombo")
        comuna_filter.addItem("All comunas (WIP)")
        comuna_filter.addItem("Las Condes (WIP)")
        comuna_filter.addItem("Santiago (WIP)")
        comuna_filter.addItem("La Florida (WIP)")
        deadline_filter = QComboBox()
        deadline_filter.setObjectName("datasetCombo")
        deadline_filter.addItem("All deadlines")
        deadline_filter.addItem("Only urgent today")
        deadline_filter.addItem("Only overdue")
        filters_row.addWidget(comuna_filter)
        filters_row.addWidget(deadline_filter)
        edit_card.add_row("Filter scope", self._with_layout(filters_row))

        bulk_actions = QHBoxLayout()
        btn_preview = QPushButton("PREVIEW CHANGES (WIP)")
        btn_preview.setObjectName("btnSecondary")
        btn_preview.setEnabled(False)
        btn_apply = QPushButton("APPLY MASSIVE EDIT (WIP)")
        btn_apply.setObjectName("btnSecondary")
        btn_apply.setEnabled(False)
        bulk_actions.addWidget(btn_preview)
        bulk_actions.addWidget(btn_apply)
        edit_card.add_layout(bulk_actions)
        layout.addWidget(edit_card)

        review_card = SectionCard("05 — Data Review & Address Repair Queue")
        self.tbl_datahub_review = QTableWidget(0, 9)
        self.tbl_datahub_review.setHorizontalHeaderLabels(
            ["ORDEN", "CLIENTE", "COMUNA", "DIRECCIÓN", "FECHA", "DEADLINE", "ESTADO", "MONTO", "ACCIÓN"]
        )
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_datahub_review.verticalHeader().setVisible(False)
        self.tbl_datahub_review.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_datahub_review.setMinimumHeight(260)
        review_card.add_widget(self.tbl_datahub_review)
        layout.addWidget(review_card)

        layout.addStretch()
        scroll.setWidget(panel)

        # Sync initial state with optimizer selected files, if any.
        self._sync_datahub_from_optimizer_paths()
        return scroll

    def _historical_results_wip_workspace(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {theme.BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        top = SectionCard("01 — Resultados Previos (WIP)")
        note = QLabel(
            "This section will host historical dashboards: service level trends, "
            "coverage, costs, truck utilization, and route-performance evolution."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; font-family: {theme.MONO};"
        )
        top.add_widget(note)

        controls = QHBoxLayout()
        period = QComboBox()
        period.setObjectName("datasetCombo")
        period.addItem("Last 7 days (WIP)")
        period.addItem("Last 30 days (WIP)")
        period.addItem("Custom range (WIP)")
        metric = QComboBox()
        metric.setObjectName("datasetCombo")
        metric.addItem("Coverage trend (WIP)")
        metric.addItem("Costs trend (WIP)")
        metric.addItem("Deadline compliance (WIP)")
        controls.addWidget(period)
        controls.addWidget(metric)
        top.add_layout(controls)
        layout.addWidget(top)

        board = QFrame()
        board.setObjectName("card")
        board_layout = QVBoxLayout(board)
        board_layout.setContentsMargins(16, 16, 16, 16)
        board_layout.setSpacing(10)
        title = QLabel("Historical KPI canvas and charts will appear here.")
        title.setStyleSheet(
            f"color: {theme.ACCENT2}; font-size: 14px; font-family: {theme.MONO};"
        )
        sub = QLabel(
            "WIP placeholder: trend charts, day-level table, and drill-down by comuna/client."
        )
        sub.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; font-family: {theme.MONO};"
        )
        sub.setWordWrap(True)
        board_layout.addWidget(title)
        board_layout.addWidget(sub)
        board_layout.addStretch()
        layout.addWidget(board, 1)

        return container

    @staticmethod
    def _with_layout(layout_obj):
        w = QWidget()
        w.setLayout(layout_obj)
        w.layout().setContentsMargins(0, 0, 0, 0)
        return w

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

        self.lbl_header_subtitle = QLabel("Route Optimizer  /  Fleet Management")
        self.lbl_header_subtitle.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 12px; font-family: {theme.MONO};"
        )

        self.dot = PulsingDot()

        self.user_label = QLabel("")
        self.user_label.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; font-family: {theme.MONO};"
        )

        btn_logout = QPushButton("Cerrar sesión")
        btn_logout.setObjectName("btnSecondary")
        btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_logout.setFixedHeight(28)
        btn_logout.clicked.connect(self.logout_requested.emit)

        hl.addWidget(brand)
        hl.addSpacing(20)
        hl.addWidget(self.lbl_header_subtitle)
        hl.addStretch()
        hl.addWidget(self.user_label)
        hl.addSpacing(12)
        hl.addWidget(btn_logout)
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
        self.inp_depot_query = make_input(
            "e.g. Av. Libertador Bernardo O'Higgins 1234, Santiago"
        )
        self.inp_depot_query.textChanged.connect(self._on_depot_query_changed)
        origin_card.add_row("CD Address", self.inp_depot_query)

        depot_coords_row = QHBoxLayout()
        self.inp_address = make_input("Coordinates will be filled after validation")
        self.inp_address.setReadOnly(True)
        self.btn_validate_depot = QPushButton("SEARCH ADDRESS")
        self.btn_validate_depot.setObjectName("btnSecondary")
        self.btn_validate_depot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_validate_depot.clicked.connect(self._validate_depot_address)
        depot_coords_row.addWidget(self.inp_address, 1)
        depot_coords_row.addWidget(self.btn_validate_depot, 0)
        depot_coords_widget = QWidget()
        depot_coords_widget.setLayout(depot_coords_row)
        depot_coords_widget.layout().setContentsMargins(0, 0, 0, 0)
        origin_card.add_row("Coordinates", depot_coords_widget)

        self.lbl_depot_validation = QLabel("Address not validated yet.")
        self.lbl_depot_validation.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )
        origin_card.add_widget(self.lbl_depot_validation)
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

        self.cmb_ventas = QComboBox()
        self.cmb_ventas.setObjectName("datasetCombo")
        self.cmb_ventas.activated.connect(
            lambda _idx: self._on_dataset_source_changed("ventas")
        )
        self.cmb_ventas.view().pressed.connect(
            lambda model_idx: self._arm_dataset_import("ventas", model_idx.row())
        )
        self.cmb_ventas.view().activated.connect(
            lambda model_idx: self._arm_dataset_import("ventas", model_idx.row())
        )
        csv_card.add_row("Archivo de Ventas", self.cmb_ventas)

        self.lbl_ventas_file = QLabel("No file selected.")
        self.lbl_ventas_file.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )
        csv_card.add_widget(self.lbl_ventas_file)

        self.cmb_detalle = QComboBox()
        self.cmb_detalle.setObjectName("datasetCombo")
        self.cmb_detalle.activated.connect(
            lambda _idx: self._on_dataset_source_changed("detalle")
        )
        self.cmb_detalle.view().pressed.connect(
            lambda model_idx: self._arm_dataset_import("detalle", model_idx.row())
        )
        self.cmb_detalle.view().activated.connect(
            lambda model_idx: self._arm_dataset_import("detalle", model_idx.row())
        )
        csv_card.add_row("Archivo de Detalle", self.cmb_detalle)

        self.lbl_detalle_file = QLabel("No file selected.")
        self.lbl_detalle_file.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )
        csv_card.add_widget(self.lbl_detalle_file)

        self.btn_refresh_clean_data = QPushButton("REFRESH DATA FILE LIST")
        self.btn_refresh_clean_data.setObjectName("btnSecondary")
        self.btn_refresh_clean_data.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh_clean_data.clicked.connect(self._refresh_dataset_sources)
        csv_card.add_widget(self.btn_refresh_clean_data)

        self._refresh_dataset_sources()

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

        self.inp_truck_fixed_cost = make_input("e.g. 20000")
        self.inp_truck_fixed_cost.setText("20000")
        adv_card.add_row("Fixed cost per truck (CLP)", self.inp_truck_fixed_cost)

        self.cmb_fuel_type = QComboBox()
        self.cmb_fuel_type.setObjectName("datasetCombo")
        self.cmb_fuel_type.addItem("Diesel", "diesel")
        self.cmb_fuel_type.addItem("Gasoline 93", "gasoline_93")
        self.cmb_fuel_type.addItem("Gasoline 95", "gasoline_95")
        self.cmb_fuel_type.addItem("Gasoline 97", "gasoline_97")
        self.cmb_fuel_type.currentIndexChanged.connect(self._on_fuel_type_changed)
        adv_card.add_row("Fuel type", self.cmb_fuel_type)

        diesel_row = QHBoxLayout()
        self.inp_diesel_price = make_input("CLP/L from API (e.g. 1600)")
        self.btn_fetch_diesel = QPushButton("GET FUEL API")
        self.btn_fetch_diesel.setObjectName("btnSecondary")
        self.btn_fetch_diesel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fetch_diesel.clicked.connect(
            lambda: self._fetch_diesel_price_from_api(silent=False, force_refresh=True)
        )
        diesel_row.addWidget(self.inp_diesel_price, 1)
        diesel_row.addWidget(self.btn_fetch_diesel, 0)
        diesel_widget = QWidget()
        diesel_widget.setLayout(diesel_row)
        diesel_widget.layout().setContentsMargins(0, 0, 0, 0)
        adv_card.add_row("Fuel price (CLP/L)", diesel_widget)

        self.lbl_diesel_api_status = QLabel("Fuel price: waiting API fetch…")
        self.lbl_diesel_api_status.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )
        adv_card.add_widget(self.lbl_diesel_api_status)

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

        self.btn_use_last = QPushButton("USE LAST CONFIG")
        self.btn_use_last.setObjectName("btnSecondary")
        self.btn_use_last.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_use_last.setEnabled(False)
        self.btn_use_last.clicked.connect(self._use_last_config)

        self.btn_run = QPushButton("RUN THE MODEL")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.setMinimumHeight(44)
        self.btn_run.setStyleSheet(
            f"background-color: {theme.SUCCESS}; color: white; border: none; font-weight: bold; border-radius: 4px;"
        )
        self.btn_run.clicked.connect(self._submit)

        btn_row.addWidget(self.btn_clear, 1)
        btn_row.addWidget(self.btn_use_last, 1)
        btn_row.addWidget(self.btn_run, 2)
        layout.addLayout(btn_row)

        # ── SaaS: subir CSVs seleccionados a la base de datos ──────────────
        db_row = QHBoxLayout()
        self.btn_db_upload_ventas = QPushButton("⇪ Sync Ventas → DB")
        self.btn_db_upload_ventas.setObjectName("btnSecondary")
        self.btn_db_upload_ventas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_db_upload_ventas.clicked.connect(self._db_upload_ventas)

        self.btn_db_upload_detalle = QPushButton("⇪ Sync Detalle → DB")
        self.btn_db_upload_detalle.setObjectName("btnSecondary")
        self.btn_db_upload_detalle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_db_upload_detalle.clicked.connect(self._db_upload_detalle)

        db_row.addWidget(self.btn_db_upload_ventas, 1)
        db_row.addWidget(self.btn_db_upload_detalle, 1)
        layout.addLayout(db_row)

        # ── Catálogo ────────────────────────────────────────────────────────
        cat_row = QHBoxLayout()
        self.btn_db_upload_catalogo = QPushButton("⇪ Actualizar Catálogo → DB")
        self.btn_db_upload_catalogo.setObjectName("btnSecondary")
        self.btn_db_upload_catalogo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_db_upload_catalogo.clicked.connect(self._db_upload_catalogo)
        cat_row.addWidget(self.btn_db_upload_catalogo)
        layout.addLayout(cat_row)

        scroll.setWidget(panel)
        return scroll

    # ── DB upload helpers (SaaS) ──────────────────────────────────────────────

    def _db_upload_ventas(self):
        if not self.ventas_path:
            QMessageBox.warning(self, "Error", "Selecciona primero un CSV de ventas.")
            return
        base = self._backend_base_url()
        url = f"{base}/upload"
        from frontend.workers.upload_worker import UploadWorker
        self._db_upload_worker_v = UploadWorker(url, self.ventas_path, {"user_id": self.user_id})
        self._db_upload_worker_v.finished.connect(
            lambda res: self._set_global_status(res.get("message", "Ventas subidas a DB."), "ok")
        )
        self._db_upload_worker_v.error.connect(
            lambda msg: self._set_global_status(f"DB upload ventas falló: {msg}", "err")
        )
        self._set_global_status("Subiendo ventas a la base de datos…", "busy")
        self._db_upload_worker_v.start()

    def _db_upload_detalle(self):
        if not self.detalle_path:
            QMessageBox.warning(self, "Error", "Selecciona primero un archivo de detalle.")
            return
        base = self._backend_base_url()
        url = f"{base}/upload-detalle"
        from frontend.workers.upload_worker import UploadWorker
        self._db_upload_worker_d = UploadWorker(url, self.detalle_path, {"user_id": self.user_id})
        self._db_upload_worker_d.finished.connect(
            lambda res: self._set_global_status(res.get("message", "Detalle subido a DB."), "ok")
        )
        self._db_upload_worker_d.error.connect(
            lambda msg: self._set_global_status(f"DB upload detalle falló: {msg}", "err")
        )
        self._set_global_status("Subiendo detalle a la base de datos…", "busy")
        self._db_upload_worker_d.start()

    def _db_upload_catalogo(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar catálogo", "", "CSV (*.csv)"
        )
        if not path:
            return
        base = self._backend_base_url()
        url = f"{base}/upload-catalogo"
        from frontend.workers.upload_worker import UploadWorker
        self._db_upload_worker_c = UploadWorker(url, path, {"user_id": self.user_id})
        self._db_upload_worker_c.finished.connect(
            lambda res: self._set_global_status(res.get("message", "Catálogo actualizado."), "ok")
        )
        self._db_upload_worker_c.error.connect(
            lambda msg: self._set_global_status(f"Upload catálogo falló: {msg}", "err")
        )
        self._set_global_status("Actualizando catálogo en la base de datos…", "busy")
        self._db_upload_worker_c.start()

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

    @staticmethod
    def _parse_numeric_input(text: str) -> float:
        s = str(text or "").strip().replace(" ", "")
        if not s:
            raise ValueError("empty")
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "")
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            tail = s.split(",")[-1]
            if len(tail) <= 2:
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "." in s:
            tail = s.split(".")[-1]
            if len(tail) > 2:
                s = s.replace(".", "")
        return float(s)

    def _set_diesel_api_status(self, msg: str, kind: str = "info"):
        color = {
            "ok": theme.SUCCESS,
            "error": theme.ERROR,
            "warn": theme.ACCENT,
        }.get(kind, theme.TEXT_DIM)
        self.lbl_diesel_api_status.setText(msg)
        self.lbl_diesel_api_status.setStyleSheet(
            f"color: {color}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )

    def _selected_fuel_type(self) -> str:
        try:
            return str(self.cmb_fuel_type.currentData() or "diesel")
        except Exception:
            return "diesel"

    def _selected_fuel_label(self) -> str:
        fuel_type = self._selected_fuel_type()
        labels = {
            "diesel": "Diesel",
            "gasoline_93": "Gasoline 93",
            "gasoline_95": "Gasoline 95",
            "gasoline_97": "Gasoline 97",
        }
        return labels.get(fuel_type, fuel_type)

    def _on_fuel_type_changed(self, _idx: int):
        self._set_diesel_api_status(
            f"{self._selected_fuel_label()} selected. Fetching API price…",
            "warn",
        )
        QTimer.singleShot(50, lambda: self._fetch_diesel_price_from_api(silent=True))

    def _fetch_diesel_price_from_api(self, silent: bool = False, force_refresh: bool = False) -> bool:
        if self._diesel_fetch_in_progress:
            return False

        base_url = self._backend_base_url()
        if not base_url:
            self._set_diesel_api_status("Fuel API: backend URL missing.", "error")
            if not silent:
                QMessageBox.warning(self, "Fuel API", "Backend URL is required.")
            return False

        fuel_type = urllib.parse.quote(self._selected_fuel_type(), safe="")
        url = f"{base_url}/fuel/prices-clp?fuel_type={fuel_type}"
        if force_refresh:
            url = f"{url}&force_refresh=true"

        self._diesel_fetch_in_progress = True
        self.btn_fetch_diesel.setEnabled(False)
        self.btn_fetch_diesel.setText("FETCHING…")
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            price = payload.get("fuel_price_clp", payload.get("diesel_price_clp"))
            if price is None:
                raise ValueError("No fuel_price_clp in API response.")
            price_num = float(price)
            if price_num <= 0:
                raise ValueError("Fuel price must be positive.")

            self.inp_diesel_price.setText(str(int(round(price_num))))
            source = str(payload.get("source", "api"))
            sample = int(payload.get("sample_size", 0) or 0)
            fuel_label = self._selected_fuel_label()
            if sample > 0:
                self._set_diesel_api_status(
                    f"{fuel_label} API ({source}): {int(round(price_num))} CLP/L · muestras {sample}",
                    "ok",
                )
            else:
                self._set_diesel_api_status(
                    f"{fuel_label} API ({source}): {int(round(price_num))} CLP/L",
                    "ok",
                )
            return True
        except urllib.error.HTTPError as e:
            detail = str(e)
            try:
                data = json.loads(e.read().decode("utf-8"))
                detail = str(data.get("detail", detail))
            except Exception:
                pass
            self._set_diesel_api_status(f"Fuel API failed: {detail}", "error")
            if not silent:
                QMessageBox.warning(self, "Fuel API", f"Could not fetch fuel price.\n\n{detail}")
            return False
        except Exception as e:
            self._set_diesel_api_status(f"Fuel API failed: {e}", "error")
            if not silent:
                QMessageBox.warning(self, "Fuel API", f"Could not fetch fuel price.\n\n{e}")
            return False
        finally:
            self._diesel_fetch_in_progress = False
            self.btn_fetch_diesel.setEnabled(True)
            self.btn_fetch_diesel.setText("GET FUEL API")

    def _get_dataset_path(self, kind: str) -> str | None:
        return self.ventas_path if kind == "ventas" else self.detalle_path

    def _set_dataset_path(self, kind: str, path: str | None):
        path_norm = os.path.abspath(path) if path else None
        if kind == "ventas":
            self.ventas_path = path_norm
            label = self.lbl_ventas_file
        else:
            self.detalle_path = path_norm
            label = self.lbl_detalle_file

        if path_norm:
            source = self._dataset_source_for_path(path_norm)
            label.setText(f"✓ {os.path.basename(path_norm)}  ({source})")
            label.setStyleSheet(
                f"color: {theme.ACCENT2}; font-size: 10px; font-family: {theme.MONO};"
                "padding-top: 2px;"
            )
        else:
            label.setText("No file selected.")
            label.setStyleSheet(
                f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
                "padding-top: 2px;"
            )

        # Keep Data Hub file chips synced with optimizer dataset selections.
        self._sync_datahub_from_optimizer_paths()

    def _is_in_data_dir(self, path: str, data_dir: Path) -> bool:
        try:
            p = Path(path).resolve()
            root = data_dir.resolve()
            return root == p.parent or root in p.parents
        except Exception:
            return False

    def _is_in_clean_data(self, path: str) -> bool:
        return self._is_in_data_dir(path, self._clean_data_dir)

    def _is_in_raw_data(self, path: str) -> bool:
        return self._is_in_data_dir(path, self._raw_data_dir)

    def _is_in_test_data(self, path: str) -> bool:
        return self._is_in_data_dir(path, self._test_data_dir)

    def _dataset_source_for_path(self, path: str) -> str:
        if self._is_in_clean_data(path):
            return "CLEAN_DATA"
        if self._is_in_test_data(path):
            return "TEST_DATA"
        if self._is_in_raw_data(path):
            return "RAW_DATA"
        return "external"

    def _dataset_files_in_directory(self, data_dir: Path, kind: str) -> list[Path]:
        if not data_dir.exists():
            return []

        exts = {".csv"} if kind == "ventas" else {".csv", ".xlsx"}
        files = [
            p
            for p in data_dir.iterdir()
            if p.is_file() and p.suffix.lower() in exts
        ]
        files.sort(key=lambda p: p.name.lower())
        return files

    def _dataset_files_for_kind(self, kind: str) -> list[tuple[str, Path]]:
        files_clean = [("CLEAN_DATA", p) for p in self._dataset_files_in_directory(self._clean_data_dir, kind)]
        files_test = [("TEST_DATA", p) for p in self._dataset_files_in_directory(self._test_data_dir, kind)]
        files_raw = [("RAW_DATA", p) for p in self._dataset_files_in_directory(self._raw_data_dir, kind)]
        files = files_clean + files_test + files_raw

        # Priorizar nombres afines al tipo y luego la fuente.
        if kind == "ventas":
            files.sort(
                key=lambda item: (
                    0 if "venta" in item[1].name.lower() else 1,
                    0 if item[0] == "CLEAN_DATA" else (1 if item[0] == "TEST_DATA" else 2),
                    item[1].name.lower(),
                )
            )
        else:
            files.sort(
                key=lambda item: (
                    0 if ("detalle" in item[1].name.lower() or "pedido" in item[1].name.lower()) else 1,
                    0 if item[0] == "CLEAN_DATA" else (1 if item[0] == "TEST_DATA" else 2),
                    item[1].name.lower(),
                )
            )
        return files

    def _combo_for_kind(self, kind: str):
        return self.cmb_ventas if kind == "ventas" else self.cmb_detalle

    def _dialog_filter_for_kind(self, kind: str) -> str:
        if kind == "ventas":
            return "CSV Files (*.csv)"
        return "CSV/XLSX Files (*.csv *.xlsx)"

    def _store_dataset_file(self, kind: str, source_path: str) -> str:
        src = Path(source_path).expanduser().resolve()
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"File not found: {source_path}")

        allowed_ext = {".csv"} if kind == "ventas" else {".csv", ".xlsx"}
        if src.suffix.lower() not in allowed_ext:
            raise ValueError(f"Invalid file extension: {src.suffix}")

        # Keep files in project data folders when already placed there.
        if self._is_in_clean_data(str(src)) or self._is_in_raw_data(str(src)) or self._is_in_test_data(str(src)):
            return str(src)

        # External imports are stored in TEST_DATA to keep experiments isolated.
        self._test_data_dir.mkdir(parents=True, exist_ok=True)
        target = self._test_data_dir / src.name

        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = self._test_data_dir / f"{src.stem}_{stamp}{src.suffix.lower()}"
        shutil.copy2(src, target)
        return str(target.resolve())

    def _set_datahub_status(self, msg: str, kind: str = "info"):
        if not hasattr(self, "lbl_datahub_status"):
            return
        color = {
            "ok": theme.SUCCESS,
            "warn": theme.ACCENT,
            "error": theme.ERROR,
        }.get(kind, theme.TEXT_DIM)
        self.lbl_datahub_status.setText(str(msg))
        self.lbl_datahub_status.setStyleSheet(
            f"color: {color}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )

    def _sync_datahub_from_optimizer_paths(self):
        self.datahub_ventas_path = self.ventas_path
        self.datahub_detalle_path = self.detalle_path

        if hasattr(self, "lbl_datahub_orders_file"):
            if self.datahub_ventas_path:
                source = self._dataset_source_for_path(self.datahub_ventas_path)
                self.lbl_datahub_orders_file.setText(
                    f"Ventas file: ✓ {os.path.basename(self.datahub_ventas_path)} ({source})"
                )
                self.lbl_datahub_orders_file.setStyleSheet(
                    f"color: {theme.ACCENT2}; font-size: 10px; font-family: {theme.MONO};"
                    "padding-top: 2px;"
                )
            else:
                self.lbl_datahub_orders_file.setText("Ventas file: not selected.")
                self.lbl_datahub_orders_file.setStyleSheet(
                    f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
                    "padding-top: 2px;"
                )

        if hasattr(self, "lbl_datahub_detail_file"):
            if self.datahub_detalle_path:
                source = self._dataset_source_for_path(self.datahub_detalle_path)
                self.lbl_datahub_detail_file.setText(
                    f"Detalle file: ✓ {os.path.basename(self.datahub_detalle_path)} ({source})"
                )
                self.lbl_datahub_detail_file.setStyleSheet(
                    f"color: {theme.ACCENT2}; font-size: 10px; font-family: {theme.MONO};"
                    "padding-top: 2px;"
                )
            else:
                self.lbl_datahub_detail_file.setText("Detalle file: not selected.")
                self.lbl_datahub_detail_file.setStyleSheet(
                    f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
                    "padding-top: 2px;"
                )

    def _pick_datahub_file(self, kind: str):
        title = "Select Ventas File" if kind == "ventas" else "Select Detalle File"
        if self._test_data_dir.exists():
            start_dir = str(self._test_data_dir)
        elif self._raw_data_dir.exists():
            start_dir = str(self._raw_data_dir)
        elif self._clean_data_dir.exists():
            start_dir = str(self._clean_data_dir)
        else:
            start_dir = str(self._project_root)
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            start_dir,
            self._dialog_filter_for_kind(kind),
        )
        if not path:
            return
        try:
            stored = self._store_dataset_file(kind, path)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Data Hub",
                f"No se pudo preparar el archivo en carpetas de datos.\n\n{e}",
            )
            return
        self._set_dataset_path(kind, stored)
        self._populate_dataset_combo(kind, stored)
        self._set_datahub_status(
            f"{'Ventas' if kind == 'ventas' else 'Detalle'} file loaded ({self._dataset_source_for_path(stored)}).",
            "ok",
        )

    @staticmethod
    def _fmt_money(value) -> str:
        try:
            return f"{float(value):,.0f}".replace(",", ".")
        except Exception:
            return "0"

    @staticmethod
    def _fmt_int_text(value) -> str:
        try:
            return f"{int(value):,}".replace(",", ".")
        except Exception:
            return "0"

    def _reset_datahub_dashboard_state(self):
        for lbl in getattr(self, "_datahub_kpi_labels", []):
            lbl.setText("0")
        if hasattr(self, "tbl_datahub_comuna"):
            self.tbl_datahub_comuna.setRowCount(0)
        if hasattr(self, "tbl_datahub_review"):
            self.tbl_datahub_review.setRowCount(0)
        self._datahub_validation_rows = []
        if hasattr(self, "btn_datahub_apply_repairs"):
            self.btn_datahub_apply_repairs.setEnabled(False)
        if hasattr(self, "lbl_datahub_deadline"):
            self.lbl_datahub_deadline.setText("No analysis yet.")
        self._set_datahub_status("Ready. Load files and run analysis.", "info")

    def _run_datahub_dashboard_db(self):
        if not self.user_id:
            QMessageBox.warning(self, "Data Hub", "No hay sesión activa. Inicia sesión primero.")
            return

        base_url = self._backend_base_url() or "http://localhost:8000"
        url = f"{base_url}/data/dashboard-db"

        self._stop_datahub_db_worker()
        self._stop_datahub_worker()
        self.btn_datahub_load_db.setEnabled(False)
        self.btn_datahub_load_db.setText("CARGANDO…")
        self.btn_datahub_analyze.setEnabled(False)
        self._set_datahub_status("Cargando datos desde la base de datos…", "warn")

        self.datahub_db_worker = DataHubDashboardDbWorker(url=url, user_id=self.user_id)
        self.datahub_db_worker.finished.connect(self._on_datahub_dashboard_result)
        self.datahub_db_worker.error.connect(self._on_datahub_dashboard_error)
        self.datahub_db_worker.start()

    def _run_datahub_dashboard(self):
        self._sync_datahub_from_optimizer_paths()
        if not self.datahub_ventas_path or not self.datahub_detalle_path:
            QMessageBox.warning(
                self,
                "Data Hub",
                "Debes cargar archivo de ventas y detalle antes de analizar.",
            )
            return

        base_url = self._backend_base_url() or "http://localhost:8000"
        url = f"{base_url}/data/dashboard"

        self._stop_datahub_validation_worker()
        self._stop_datahub_worker()
        self.btn_datahub_analyze.setEnabled(False)
        self.btn_datahub_analyze.setText("ANALYZING…")
        self._set_datahub_status("Running dashboard analysis…", "warn")

        self.datahub_worker = DataHubDashboardWorker(
            url=url,
            ventas_path=self.datahub_ventas_path,
            detalle_path=self.datahub_detalle_path,
        )
        self.datahub_worker.finished.connect(self._on_datahub_dashboard_result)
        self.datahub_worker.error.connect(self._on_datahub_dashboard_error)
        self.datahub_worker.start()

    def _on_datahub_dashboard_result(self, payload: dict):
        self._stop_datahub_worker()
        self._stop_datahub_db_worker()
        self.btn_datahub_analyze.setEnabled(True)
        self.btn_datahub_analyze.setText("ANALYZE CURRENT DATA")
        self.btn_datahub_load_db.setEnabled(True)
        self.btn_datahub_load_db.setText("CARGAR DESDE BD")

        if not isinstance(payload, dict):
            self._set_datahub_status("Invalid dashboard payload.", "error")
            return

        kpis = payload.get("kpis", {}) if isinstance(payload.get("kpis"), dict) else {}
        kpi_values = [
            self._fmt_int_text(kpis.get("orders_total", 0)),
            self._fmt_int_text(kpis.get("orders_deadline_today", 0)),
            self._fmt_int_text(kpis.get("comunas_active", 0)),
            self._fmt_money(kpis.get("monetary_total_clp", 0.0)),
            self._fmt_int_text(kpis.get("orders_overdue", 0)),
            self._fmt_int_text(kpis.get("customers_total", 0)),
        ]
        for lbl, val in zip(getattr(self, "_datahub_kpi_labels", []), kpi_values):
            lbl.setText(val)

        overdue = int(kpis.get("orders_overdue", 0) or 0)
        today_due = int(kpis.get("orders_deadline_today", 0) or 0)
        next24 = int(kpis.get("orders_next_24h", 0) or 0)
        week = int(kpis.get("orders_week_horizon", 0) or 0)
        if hasattr(self, "lbl_datahub_deadline"):
            self.lbl_datahub_deadline.setText(
                f"Overdue: {overdue}\n"
                f"Today deadline: {today_due}\n"
                f"Next 24h: {next24}\n"
                f"Week horizon: {week}"
            )

        comuna_rows = payload.get("comuna_distribution", [])
        if not isinstance(comuna_rows, list):
            comuna_rows = []
        self.tbl_datahub_comuna.setRowCount(0)
        for row in comuna_rows[:80]:
            if not isinstance(row, dict):
                continue
            r = self.tbl_datahub_comuna.rowCount()
            self.tbl_datahub_comuna.insertRow(r)
            self.tbl_datahub_comuna.setItem(r, 0, QTableWidgetItem(str(row.get("comuna", ""))))
            self.tbl_datahub_comuna.setItem(r, 1, QTableWidgetItem(self._fmt_int_text(row.get("pedidos", 0))))
            self.tbl_datahub_comuna.setItem(r, 2, QTableWidgetItem(self._fmt_money(row.get("monto_clp", 0.0))))

        review_rows = payload.get("review_rows", [])
        if not isinstance(review_rows, list):
            review_rows = []
        self._render_datahub_review_rows(review_rows)
        self._datahub_validation_rows = []
        if hasattr(self, "btn_datahub_apply_repairs"):
            self.btn_datahub_apply_repairs.setEnabled(False)

        meta = payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {}
        elapsed = float(meta.get("elapsed_sec", 0.0) or 0.0)
        self._set_datahub_status(
            f"Dashboard ready: {self.tbl_datahub_comuna.rowCount()} comunas, "
            f"{self.tbl_datahub_review.rowCount()} rows in review queue, {elapsed:.2f}s.",
            "ok",
        )

    def _on_datahub_dashboard_error(self, msg: str):
        self._stop_datahub_worker()
        self._stop_datahub_db_worker()
        self.btn_datahub_analyze.setEnabled(True)
        self.btn_datahub_analyze.setText("ANALYZE CURRENT DATA")
        self.btn_datahub_load_db.setEnabled(True)
        self.btn_datahub_load_db.setText("CARGAR DESDE BD")
        self._set_datahub_status(f"Dashboard failed: {msg}", "error")
        QMessageBox.warning(self, "Data Hub", f"No se pudo analizar la data.\n\n{msg}")

    def _render_datahub_review_rows(self, rows: list[dict]):
        self.tbl_datahub_review.setRowCount(0)
        for row in rows[:200]:
            if not isinstance(row, dict):
                continue
            r = self.tbl_datahub_review.rowCount()
            self.tbl_datahub_review.insertRow(r)
            self.tbl_datahub_review.setItem(r, 0, QTableWidgetItem(str(row.get("order_id", ""))))
            self.tbl_datahub_review.setItem(r, 1, QTableWidgetItem(str(row.get("customer", ""))))
            self.tbl_datahub_review.setItem(r, 2, QTableWidgetItem(str(row.get("comuna", ""))))
            self.tbl_datahub_review.setItem(r, 3, QTableWidgetItem(str(row.get("address", ""))))
            self.tbl_datahub_review.setItem(r, 4, QTableWidgetItem(str(row.get("dispatch_date", ""))))
            self.tbl_datahub_review.setItem(r, 5, QTableWidgetItem(str(row.get("deadline_status", ""))))
            state_txt = str(row.get("quality_status", ""))
            state_item = QTableWidgetItem(state_txt)
            if state_txt in {"missing_address", "missing_comuna", "missing_data"}:
                state_item.setForeground(QColor(theme.ERROR))
            elif state_txt in {"low_confidence", "repair_suggested"}:
                state_item.setForeground(QColor(theme.ACCENT))
            elif state_txt in {"validated", "repaired_validated"}:
                state_item.setForeground(QColor(theme.ACCENT2))
            else:
                state_item.setForeground(QColor(theme.TEXT_DIM))
            self.tbl_datahub_review.setItem(r, 6, state_item)
            self.tbl_datahub_review.setItem(r, 7, QTableWidgetItem(self._fmt_money(row.get("monto_clp", 0.0))))
            self.tbl_datahub_review.setItem(r, 8, QTableWidgetItem(str(row.get("action_suggestion", ""))))

    def _run_datahub_address_validation(self):
        self._sync_datahub_from_optimizer_paths()
        if not self.datahub_ventas_path:
            QMessageBox.warning(
                self,
                "Data Hub",
                "Debes cargar archivo de ventas antes de validar direcciones.",
            )
            return

        base_url = self._backend_base_url() or "http://localhost:8000"
        url = f"{base_url}/data/validate-addresses"

        self._stop_datahub_validation_worker()
        self.btn_datahub_validate_addresses.setEnabled(False)
        self.btn_datahub_validate_addresses.setText("VERIFYING…")
        validation_cap = 10000
        self._set_datahub_status(
            f"Running massive address validation (max {validation_cap} rows/probes)…",
            "warn",
        )

        self.datahub_validation_worker = DataHubAddressValidationWorker(
            url=url,
            ventas_path=self.datahub_ventas_path,
            max_rows=validation_cap,
            max_api_probes=validation_cap,
        )
        self.datahub_validation_worker.finished.connect(self._on_datahub_validation_result)
        self.datahub_validation_worker.error.connect(self._on_datahub_validation_error)
        self.datahub_validation_worker.start()

    def _on_datahub_validation_result(self, payload: dict):
        self._stop_datahub_validation_worker()
        self.btn_datahub_validate_addresses.setEnabled(True)
        self.btn_datahub_validate_addresses.setText("VERIFY ADDRESSES")

        if not isinstance(payload, dict):
            self._set_datahub_status("Invalid validation payload.", "error")
            return

        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            rows = []
        self._datahub_validation_rows = [r for r in rows if isinstance(r, dict)]
        self._render_datahub_review_rows(self._datahub_validation_rows)

        can_apply = any(bool(r.get("can_auto_repair")) for r in self._datahub_validation_rows)
        self.btn_datahub_apply_repairs.setEnabled(bool(can_apply))

        summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
        processed = int(summary.get("rows_processed", len(self._datahub_validation_rows)) or 0)
        validated = int(summary.get("validated", 0) or 0)
        repaired = int(summary.get("repaired_validated", 0) or 0)
        suggested = int(summary.get("repair_suggested", 0) or 0)
        unresolved = int(summary.get("unverified", 0) or 0) + int(summary.get("missing_data", 0) or 0)
        probes_used = int(summary.get("api_probes_used", 0) or 0)
        probes_budget = int(summary.get("api_probe_budget", 0) or 0)
        repeat_skips = int(summary.get("probe_repeat_skips", 0) or 0)
        fail_map = summary.get("geocode_fail_source_hits", {}) if isinstance(summary.get("geocode_fail_source_hits"), dict) else {}
        top_fail = ""
        if fail_map:
            try:
                top_key, top_val = max(fail_map.items(), key=lambda kv: int(kv[1] or 0))
                top_fail = f"{str(top_key)[:80]} ({int(top_val or 0)})"
            except Exception:
                top_fail = ""
        elapsed = float((payload.get("meta") or {}).get("elapsed_sec", 0.0) or 0.0)
        self._set_datahub_status(
            (
                f"Validation ready: rows={processed}, ok={validated}, repaired={repaired}, "
                f"suggested={suggested}, unresolved={unresolved}, probes={probes_used}/{probes_budget}, "
                f"repeat-skips={repeat_skips}"
                f"{', top-fail=' + top_fail if top_fail else ''} ({elapsed:.2f}s)."
            ),
            "ok",
        )

    def _on_datahub_validation_error(self, msg: str):
        self._stop_datahub_validation_worker()
        self.btn_datahub_validate_addresses.setEnabled(True)
        self.btn_datahub_validate_addresses.setText("VERIFY ADDRESSES")
        self._set_datahub_status(f"Address validation failed: {msg}", "error")
        QMessageBox.warning(self, "Data Hub", f"No se pudo validar direcciones.\n\n{msg}")

    def _apply_datahub_suggested_repairs(self):
        if not self.datahub_ventas_path or not self._datahub_validation_rows:
            QMessageBox.information(
                self,
                "Data Hub",
                "Primero ejecuta Verify Addresses para generar sugerencias.",
            )
            return

        try:
            src = Path(self.datahub_ventas_path)
            df = pd.read_csv(src)
            if "Número de Orden" not in df.columns:
                raise ValueError("El archivo de ventas no contiene 'Número de Orden'.")

            order_series = df["Número de Orden"].astype(str).str.strip()
            repairs = 0
            for row in self._datahub_validation_rows:
                if not bool(row.get("can_auto_repair")):
                    continue
                order_id = str(row.get("order_id", "") or "").strip()
                if not order_id:
                    continue
                mask = order_series == order_id
                if not bool(mask.any()):
                    continue

                suggested_address = str(row.get("suggested_address", "") or "").strip()
                suggested_comuna = str(row.get("suggested_comuna", "") or "").strip()
                changed_mask = pd.Series(False, index=df.index)

                if suggested_address and "Dirección cliente" in df.columns:
                    addr_change_mask = mask & (df["Dirección cliente"].astype(str).str.strip() != suggested_address)
                    if bool(addr_change_mask.any()):
                        df.loc[addr_change_mask, "Dirección cliente"] = suggested_address
                        changed_mask = changed_mask | addr_change_mask

                if suggested_comuna and "Comuna" in df.columns:
                    comuna_change_mask = mask & (df["Comuna"].astype(str).str.strip() != suggested_comuna)
                    if bool(comuna_change_mask.any()):
                        df.loc[comuna_change_mask, "Comuna"] = suggested_comuna
                        changed_mask = changed_mask | comuna_change_mask

                repairs += int(changed_mask.sum())

            if repairs <= 0:
                QMessageBox.information(
                    self,
                    "Data Hub",
                    "No se encontraron cambios automáticos para aplicar.",
                )
                return

            self._test_data_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = self._test_data_dir / f"{src.stem}_phase2_repaired_{stamp}.csv"
            df.to_csv(out_path, index=False)

            self._set_dataset_path("ventas", str(out_path.resolve()))
            self._populate_dataset_combo("ventas", self.ventas_path)
            self._set_datahub_status(
                f"Repairs applied: {repairs} rows updated. Saved to {out_path.name}.",
                "ok",
            )
            QMessageBox.information(
                self,
                "Data Hub",
                f"Reparaciones aplicadas.\n\nArchivo generado:\n{out_path}",
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Data Hub",
                f"No se pudieron aplicar reparaciones.\n\n{e}",
            )

    def _populate_dataset_combo(self, kind: str, current_path: str | None = None):
        combo = self._combo_for_kind(kind)
        files = self._dataset_files_for_kind(kind)
        selected = os.path.abspath(current_path) if current_path else None

        self._dataset_combo_internal_change = True
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Select from CLEAN_DATA / TEST_DATA / RAW_DATA...", "")

        values = [""]
        for source, p in files:
            path_s = str(p.resolve())
            combo.addItem(f"{p.name}  ({source})", path_s)
            values.append(path_s)

        if selected and selected not in values:
            combo.addItem(f"{Path(selected).name}  ({self._dataset_source_for_path(selected)})", selected)
            values.append(selected)

        combo.addItem("Import from computer (copy to TEST_DATA)...", "__import__")
        values.append("__import__")

        if selected and selected in values:
            combo.setCurrentIndex(values.index(selected))
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self._dataset_combo_internal_change = False

    def _refresh_dataset_sources(self):
        self._populate_dataset_combo("ventas", self.ventas_path)
        self._populate_dataset_combo("detalle", self.detalle_path)
        self._sync_datahub_from_optimizer_paths()

    def _set_dataset_combo_to_path(self, kind: str, path: str | None):
        combo = self._combo_for_kind(kind)
        target = os.path.abspath(path) if path else ""
        idx = combo.findData(target)
        if idx < 0:
            idx = 0
        self._dataset_combo_internal_change = True
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        self._dataset_combo_internal_change = False

    def _on_dataset_source_changed(self, kind: str):
        if self._dataset_combo_internal_change or self._dataset_dialog_open:
            return

        combo = self._combo_for_kind(kind)
        selected = combo.currentData()

        if selected == "__import__":
            previous_path = self._get_dataset_path(kind)

            # Never open file dialog while the model run is active.
            if self.worker and self.worker.isRunning():
                self._set_dataset_combo_to_path(kind, previous_path)
                return

            # Only allow import dialog when "__import__" was explicitly picked
            # from the combo popup list (prevents random/spurious activations).
            now = time.monotonic()
            armed_until = float(self._dataset_import_armed_until.get(kind, 0.0))
            if now > armed_until:
                self._set_dataset_combo_to_path(kind, previous_path)
                return
            self._dataset_import_armed_until[kind] = 0.0

            # Guard against duplicate queued events reopening the dialog.
            if now < float(self._dataset_dialog_cooldown_until):
                self._set_dataset_combo_to_path(kind, previous_path)
                return

            # Revert immediately to the last valid selection so cancel cannot
            # leave the combo pinned to "__import__" (which can retrigger loops).
            self._set_dataset_combo_to_path(kind, previous_path)

            self._dataset_dialog_open = True
            self._dataset_dialog_cooldown_until = now + 0.8
            QTimer.singleShot(
                0,
                lambda k=kind, prev=previous_path: self._open_dataset_import_dialog(k, prev),
            )
            return

        if selected:
            self._set_dataset_path(kind, str(selected))
        else:
            self._set_dataset_path(kind, None)

    def _arm_dataset_import(self, kind: str, row: int):
        combo = self._combo_for_kind(kind)
        data = combo.itemData(int(row))
        if data == "__import__":
            # Small time window where selecting "__import__" is considered intentional.
            self._dataset_import_armed_until[kind] = time.monotonic() + 1.5
        else:
            self._dataset_import_armed_until[kind] = 0.0

    def _open_dataset_import_dialog(self, kind: str, previous_path: str | None):
        path = ""
        try:
            title = "Select Ventas File" if kind == "ventas" else "Select Detalle File"
            if self._test_data_dir.exists():
                start_dir = str(self._test_data_dir)
            elif self._raw_data_dir.exists():
                start_dir = str(self._raw_data_dir)
            elif self._clean_data_dir.exists():
                start_dir = str(self._clean_data_dir)
            else:
                start_dir = str(self._project_root)
            path, _ = QFileDialog.getOpenFileName(
                self,
                title,
                start_dir,
                self._dialog_filter_for_kind(kind),
            )
        finally:
            self._dataset_dialog_open = False
            self._dataset_dialog_cooldown_until = time.monotonic() + 0.6

        if path:
            try:
                stored = self._store_dataset_file(kind, path)
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Dataset import",
                    f"No se pudo preparar el archivo en carpetas de datos.\n\n{e}",
                )
                self._populate_dataset_combo(kind, previous_path)
                return
            self._set_dataset_path(kind, stored)
            self._populate_dataset_combo(kind, stored)
        else:
            # Keep last valid selection after cancel.
            self._populate_dataset_combo(kind, previous_path)

    def _collect_form_config(self) -> dict:
        coords = []
        if (
            isinstance(self._validated_depot_coords, tuple)
            and len(self._validated_depot_coords) == 2
        ):
            coords = [
                float(self._validated_depot_coords[0]),
                float(self._validated_depot_coords[1]),
            ]

        return {
            "num_trucks": self.inp_trucks.text().strip(),
            "depot_query": self.inp_depot_query.text().strip(),
            "validated_depot_query": self._validated_depot_query,
            "validated_depot_coords": coords,
            "api_url": self.inp_url.text().strip(),
            "runtime": self.inp_runtime.text().strip(),
            "worktime_start": self.inp_worktime_start.text().strip(),
            "worktime_end": self.inp_worktime_end.text().strip(),
            "km_per_liter": self.inp_kml.text().strip(),
            "fuel_type": self._selected_fuel_type(),
            "truck_fixed_cost_clp": self.inp_truck_fixed_cost.text().strip(),
            "diesel_price_clp": self.inp_diesel_price.text().strip(),
            "space_per_truck": self.inp_space.text().strip(),
            "weight_per_truck": self.inp_weight.text().strip(),
            "deliveries_per_day": self.inp_deliveries.text().strip(),
            "ventas_path": self.ventas_path or "",
            "detalle_path": self.detalle_path or "",
        }

    def _save_last_form_config(self):
        try:
            data = self._collect_form_config()
            self._last_form_config_path.parent.mkdir(parents=True, exist_ok=True)
            self._last_form_config_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._last_form_config = data
            self.btn_use_last.setEnabled(True)
        except Exception as e:
            # Non-blocking: saving convenience state should never break the run.
            print(f"[MainView] Could not save last form config: {e}")

    def _apply_last_form_config(self, data: dict):
        self.inp_trucks.setText(str(data.get("num_trucks", "") or ""))
        self.inp_depot_query.setText(str(data.get("depot_query", "") or ""))
        self.inp_url.setText(str(data.get("api_url", "") or "http://localhost:8000/optimize"))
        self.inp_runtime.setText(str(data.get("runtime", "") or ""))
        self.inp_worktime_start.setText(str(data.get("worktime_start", "") or "09:00"))
        self.inp_worktime_end.setText(str(data.get("worktime_end", "") or "17:00"))
        self.inp_kml.setText(str(data.get("km_per_liter", "") or "6.4"))
        saved_fuel_type = str(data.get("fuel_type", "") or "diesel")
        fuel_idx = self.cmb_fuel_type.findData(saved_fuel_type)
        self.cmb_fuel_type.setCurrentIndex(fuel_idx if fuel_idx >= 0 else 0)
        self.inp_truck_fixed_cost.setText(str(data.get("truck_fixed_cost_clp", "") or "20000"))
        self.inp_diesel_price.setText(str(data.get("diesel_price_clp", "") or ""))
        self.inp_space.setText(str(data.get("space_per_truck", "") or "9"))
        self.inp_weight.setText(str(data.get("weight_per_truck", "") or "2000"))
        self.inp_deliveries.setText(str(data.get("deliveries_per_day", "") or "150"))
        if self.inp_diesel_price.text().strip():
            self._set_diesel_api_status(
                f"{self._selected_fuel_label()} loaded from last config: {self.inp_diesel_price.text().strip()} CLP/L",
                "ok",
            )
        else:
            self._set_diesel_api_status(f"{self._selected_fuel_label()} price empty, fetching from API…", "warn")
            QTimer.singleShot(200, lambda: self._fetch_diesel_price_from_api(silent=True))

        validated_query = str(data.get("validated_depot_query", "") or "").strip()
        validated_coords = data.get("validated_depot_coords", [])
        if (
            validated_query
            and isinstance(validated_coords, list)
            and len(validated_coords) == 2
        ):
            try:
                lat = float(validated_coords[0])
                lon = float(validated_coords[1])
                self._validated_depot_query = validated_query
                self._validated_depot_coords = (lat, lon)
                self.inp_address.setText(f"{lat:.6f}, {lon:.6f}")
                self._set_depot_validation_status(
                    "Loaded previous validated CD address.",
                    is_valid=True,
                )
            except Exception:
                self._validated_depot_query = ""
                self._validated_depot_coords = None
                self.inp_address.clear()
                self._set_depot_validation_status(
                    "Loaded config, but CD must be validated again.",
                    is_valid=False,
                )
        else:
            self._validated_depot_query = ""
            self._validated_depot_coords = None
            self.inp_address.clear()
            self._set_depot_validation_status(
                "Loaded config, but CD must be validated again.",
                is_valid=False,
            )

        missing_files = []
        ventas_path = str(data.get("ventas_path", "") or "").strip()
        if ventas_path and os.path.exists(ventas_path):
            self._set_dataset_path("ventas", ventas_path)
        else:
            self._set_dataset_path("ventas", None)
            if ventas_path:
                missing_files.append(os.path.basename(ventas_path))
        self._populate_dataset_combo("ventas", self.ventas_path)

        detalle_path = str(data.get("detalle_path", "") or "").strip()
        if detalle_path and os.path.exists(detalle_path):
            self._set_dataset_path("detalle", detalle_path)
        else:
            self._set_dataset_path("detalle", None)
            if detalle_path:
                missing_files.append(os.path.basename(detalle_path))
        self._populate_dataset_combo("detalle", self.detalle_path)

        if missing_files:
            QMessageBox.information(
                self,
                "Last Config Loaded",
                "Configuration loaded. Some files were not found:\n- " + "\n- ".join(missing_files),
            )
        else:
            self._set_global_status("Previous configuration loaded.", "ok")

    def _load_last_form_config(self):
        try:
            if not self._last_form_config_path.exists():
                self.btn_use_last.setEnabled(False)
                return
            data = json.loads(self._last_form_config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                self._last_form_config = data
                self.btn_use_last.setEnabled(True)
        except Exception as e:
            print(f"[MainView] Could not load last form config: {e}")
            self.btn_use_last.setEnabled(False)

    def _use_last_config(self):
        if not isinstance(self._last_form_config, dict) or not self._last_form_config:
            QMessageBox.information(
                self,
                "Last Config",
                "No previous configuration found yet.",
            )
            self.btn_use_last.setEnabled(False)
            return
        self._apply_last_form_config(self._last_form_config)

    def _backend_base_url(self) -> str:
        url = self.inp_url.text().strip()
        if not url:
            return ""
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme and parsed.netloc:
            root = f"{parsed.scheme}://{parsed.netloc}"
            path = parsed.path.rstrip("/")
            if path.endswith("/optimize"):
                base_path = path[: -len("/optimize")].rstrip("/")
                return f"{root}{base_path}" if base_path else root
            return f"{root}{path}" if path else root
        if "/optimize" in url:
            return url.rsplit("/optimize", 1)[0].rstrip("/")
        return url.rstrip("/")

    def _on_depot_query_changed(self, _text: str):
        current_query = self.inp_depot_query.text().strip()
        if (
            current_query
            and self._validated_depot_coords is not None
            and current_query == self._validated_depot_query
        ):
            return
        self._validated_depot_coords = None
        self._validated_depot_query = ""
        self.inp_address.clear()
        if current_query:
            self.lbl_depot_validation.setText("Address changed. Validate again.")
            self.lbl_depot_validation.setStyleSheet(
                f"color: {theme.ACCENT}; font-size: 10px; font-family: {theme.MONO};"
                "padding-top: 2px;"
            )
        else:
            self.lbl_depot_validation.setText("Address not validated yet.")
            self.lbl_depot_validation.setStyleSheet(
                f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
                "padding-top: 2px;"
            )

    def _set_depot_validation_status(self, msg: str, is_valid: bool):
        self.lbl_depot_validation.setText(msg)
        color = theme.SUCCESS if is_valid else theme.ERROR
        self.lbl_depot_validation.setStyleSheet(
            f"color: {color}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )

    def _validate_depot_address(self, silent: bool = False) -> bool:
        address_query = self.inp_depot_query.text().strip()
        if not address_query:
            self._validated_depot_coords = None
            self._validated_depot_query = ""
            self.inp_address.clear()
            self._set_depot_validation_status(
                "Enter a CD address before validating.",
                is_valid=False,
            )
            if not silent:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "Distribution center address is required.",
                )
            return False

        base_url = self._backend_base_url()
        if not base_url:
            self._set_depot_validation_status(
                "Backend URL is required to validate the CD address.",
                is_valid=False,
            )
            if not silent:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "Backend URL is required to validate the CD address.",
                )
            return False

        validate_url = (
            f"{base_url}/validate-depot?"
            f"{urllib.parse.urlencode({'address': address_query})}"
        )

        self.btn_validate_depot.setEnabled(False)
        self.btn_validate_depot.setText("VALIDATING…")
        self._set_global_status("Validating CD address…", "busy")
        try:
            req = urllib.request.Request(validate_url, method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = str(e)
            try:
                err_data = json.loads(e.read().decode("utf-8"))
                detail = err_data.get("detail", detail)
            except Exception:
                pass
            self._validated_depot_coords = None
            self._validated_depot_query = ""
            self.inp_address.clear()
            self._set_depot_validation_status(
                f"CD validation failed: {detail}",
                is_valid=False,
            )
            self._set_global_status("CD address validation failed.", "error")
            if not silent:
                QMessageBox.warning(
                    self,
                    "CD Validation",
                    f"Could not validate CD address.\n\n{detail}",
                )
            return False
        except Exception as e:
            self._validated_depot_coords = None
            self._validated_depot_query = ""
            self.inp_address.clear()
            self._set_depot_validation_status(
                f"CD validation failed: {e}",
                is_valid=False,
            )
            self._set_global_status("CD address validation failed.", "error")
            if not silent:
                QMessageBox.warning(
                    self,
                    "CD Validation",
                    f"Could not validate CD address.\n\n{e}",
                )
            return False
        finally:
            self.btn_validate_depot.setEnabled(True)
            self.btn_validate_depot.setText("SEARCH ADDRESS")

        is_valid = bool(payload.get("is_valid"))
        lat = payload.get("lat")
        lon = payload.get("lon")
        normalized = str(payload.get("normalized_address") or address_query)

        if not is_valid or lat is None or lon is None:
            reason = payload.get("reason") or "Address could not be geocoded."
            self._validated_depot_coords = None
            self._validated_depot_query = ""
            self.inp_address.clear()
            self._set_depot_validation_status(
                f"Invalid CD address: {reason}",
                is_valid=False,
            )
            self._set_global_status("CD address is invalid.", "error")
            if not silent:
                QMessageBox.warning(
                    self,
                    "CD Validation",
                    f"Invalid CD address.\n\n{reason}",
                )
            return False

        lat = float(lat)
        lon = float(lon)
        self._validated_depot_coords = (lat, lon)
        self._validated_depot_query = address_query
        self.inp_address.setText(f"{lat:.6f}, {lon:.6f}")
        self._set_depot_validation_status(
            f"Validated: {normalized}",
            is_valid=True,
        )
        self._set_global_status("CD address validated.", "ok")
        return True

    # ── Clear ─────────────────────────────────────────────────────────────────

    def _clear(self):
        for w in (
            self.inp_trucks,
            self.inp_depot_query,
            self.inp_address,
            self.inp_runtime,
        ):
            w.clear()

        self.inp_worktime_start.setText("09:00")
        self.inp_worktime_end.setText("17:00")
        self.inp_kml.setText("6.4")
        diesel_idx = self.cmb_fuel_type.findData("diesel")
        self.cmb_fuel_type.setCurrentIndex(diesel_idx if diesel_idx >= 0 else 0)
        self.inp_truck_fixed_cost.setText("20000")
        self.inp_diesel_price.clear()
        self._set_diesel_api_status("Fuel price: fetching from API…", "warn")
        self.inp_space.setText("9")
        self.inp_weight.setText("2000")
        self.inp_deliveries.setText("150")

        self._set_dataset_path("ventas", None)
        self._set_dataset_path("detalle", None)
        self._refresh_dataset_sources()
        self._reset_datahub_dashboard_state()

        self._validated_depot_coords = None
        self._validated_depot_query = ""
        self.lbl_depot_validation.setText("Address not validated yet.")
        self.lbl_depot_validation.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 10px; font-family: {theme.MONO};"
            "padding-top: 2px;"
        )

        # Clear day tabs
        self.day_tabs.clear()
        self.empty_lbl.show()
        self.lbl_count.setText("")
        self.dot.reset()
        self._result_data = None
        self._set_global_status("Cleared", "idle")
        QTimer.singleShot(200, lambda: self._fetch_diesel_price_from_api(silent=True))

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

        fixed_cost_txt = self.inp_truck_fixed_cost.text().strip()
        try:
            truck_fixed_cost_clp = self._parse_numeric_input(fixed_cost_txt)
            if truck_fixed_cost_clp <= 0:
                raise ValueError
        except Exception:
            truck_fixed_cost_clp = None
            errors.append("Fixed cost per truck (CLP) must be a positive number.")

        diesel_txt = self.inp_diesel_price.text().strip()
        if not diesel_txt:
            self._fetch_diesel_price_from_api(silent=True)
            diesel_txt = self.inp_diesel_price.text().strip()
        try:
            diesel_price_clp = self._parse_numeric_input(diesel_txt)
            if diesel_price_clp <= 0:
                raise ValueError
        except Exception:
            diesel_price_clp = None
            errors.append("Fuel price (CLP/L) must be a positive number (or fetched from API).")

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

        depot_query = self.inp_depot_query.text().strip()
        if not depot_query:
            errors.append("Distribution center address is required.")
            lat, lon = None, None
        else:
            if (
                self._validated_depot_coords is None
                or self._validated_depot_query != depot_query
            ):
                self._validate_depot_address(silent=True)
            if self._validated_depot_coords is None:
                errors.append(
                    "Distribution center address is not valid. Validate it before running."
                )
                lat, lon = None, None
            else:
                lat, lon = self._validated_depot_coords

        deliveries_txt = self.inp_deliveries.text().strip()
        try:
            deliveries_per_day = int(deliveries_txt)
            if deliveries_per_day < 1:
                raise ValueError
        except ValueError:
            deliveries_per_day = 150
            errors.append("Max deliveries / day must be a positive integer.")

        # En SaaS los archivos son opcionales: el backend cae a la DB si faltan.

        if errors:
            QMessageBox.warning(self, "Validation Error", "\n".join(errors))
            return None

        return {
            "num_trucks":        int(trucks_txt),
            "km_per_liter":      kml,
            "fuel_type":         self._selected_fuel_type(),
            "truck_fixed_cost_clp": truck_fixed_cost_clp,
            "diesel_price_clp":  diesel_price_clp,
            "space_per_truck":   space,
            "weight_per_truck":  weight,
            "model_runtime":     int(runtime_txt) if runtime_txt else None,
            "worktime_windows":  f"{self.inp_worktime_start.text().strip()}-{self.inp_worktime_end.text().strip()}",
            "depot_address":     [lat, lon],
            "deliveries_per_day": deliveries_per_day,
            "user_id":           self.user_id,
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

        self._save_last_form_config()

        self.btn_run.setEnabled(False)
        self.btn_run.setText("RUNNING…")
        self.progress.show()
        self.dot.start_busy()
        self._set_global_status("Sending request to backend…", "busy")
        self.lbl_stage.setText("⟳ Connecting…")

        self.worker = RequestWorker(
            url, params,
            self.ventas_path,
            self.detalle_path,
        )
        self.worker.finished.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.start()

        # Start progress poller
        base_url = url.rsplit("/", 1)[0]  # strip the endpoint path
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
        self.worker = None
        self.lbl_stage.setText("")
        self.progress.hide()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("RUN THE MODEL")
        self.dot.set_ok()
        self._result_data = data

        days = data.get("days", [])
        global_stats = data.get("global_stats", {}) if isinstance(data, dict) else {}
        cleaning_errors = data.get("cleaning_errors", []) if isinstance(data, dict) else []

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

        global_widget = GlobalSummaryWidget(parent=self)
        global_widget.populate(global_stats, days, cleaning_errors)
        self.day_tabs.addTab(global_widget, "🌐 Global")

        for day_data in days:
            label = day_data.get("date", "?")
            widget = DayResultWidget(parent=self)
            widget.populate(day_data)
            self.day_tabs.addTab(widget, f"📅 {label}")

    def _on_error(self, msg: str):
        self._stop_progress_worker()
        self.worker = None
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
