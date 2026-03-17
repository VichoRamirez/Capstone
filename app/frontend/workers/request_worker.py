"""
Worker thread for making requests to the optimization backend.
"""

import json
import csv
import os
import urllib.request
import urllib.error
from PyQt6.QtCore import QThread, pyqtSignal

class RequestWorker(QThread):
    finished  = pyqtSignal(list)   # list of dicts: Truck, Point, Hour
    error     = pyqtSignal(str)

    def __init__(self, url: str, params: dict, csv_path: str):
        super().__init__()
        self.url      = url
        self.params   = params
        self.csv_path = csv_path

    def run(self):
        import uuid

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

        # CSV file field (Optional)
        if self.csv_path:
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
