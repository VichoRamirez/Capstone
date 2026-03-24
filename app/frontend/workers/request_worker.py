"""
Worker thread for making requests to the optimization backend.
Sends two files (ventas + detalle) and params, receives JSON response.
"""

import json
import os
import uuid
import urllib.request
import urllib.error
from PyQt6.QtCore import QThread, pyqtSignal


class RequestWorker(QThread):
    """Worker para el endpoint /optimize — retorna JSON con rutas, mapa y no cubiertos."""
    finished = pyqtSignal(dict)   # JSON response completa
    error    = pyqtSignal(str)

    def __init__(self, url: str, params: dict,
                 ventas_path: str, detalle_path: str):
        super().__init__()
        self.url = url
        self.params = params
        self.ventas_path = ventas_path
        self.detalle_path = detalle_path

    def run(self):
        boundary = uuid.uuid4().hex
        body_parts = []

        # JSON params field
        json_bytes = json.dumps(self.params).encode("utf-8")
        body_parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="params"\r\n'
            f'Content-Type: application/json\r\n\r\n'.encode() + json_bytes + b'\r\n'
        )

        # Ventas CSV
        if self.ventas_path:
            with open(self.ventas_path, "rb") as f:
                ventas_bytes = f.read()
            fname = os.path.basename(self.ventas_path)
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="ventas"; filename="{fname}"\r\n'
                f'Content-Type: text/csv\r\n\r\n'.encode() + ventas_bytes + b'\r\n'
            )

        # Detalle CSV/XLSX
        if self.detalle_path:
            with open(self.detalle_path, "rb") as f:
                detalle_bytes = f.read()
            fname = os.path.basename(self.detalle_path)
            ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" \
                if fname.endswith(".xlsx") else "text/csv"
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="detalle"; filename="{fname}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n'.encode() + detalle_bytes + b'\r\n'
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
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            self.finished.emit(data)
        except urllib.error.HTTPError as e:
            # Read the response body to get the actual FastAPI error detail
            try:
                body = json.loads(e.read().decode("utf-8"))
                detail = body.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"Error {e.code}: {detail}")
        except urllib.error.URLError as e:
            self.error.emit(f"Error de red: {e.reason}")
        except Exception as e:
            self.error.emit(str(e))


class CleanWorker(QThread):
    """Worker para el endpoint /clean — retorna JSON de preview."""
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, url: str, ventas_path: str, detalle_path: str):
        super().__init__()
        self.url = url
        self.ventas_path = ventas_path
        self.detalle_path = detalle_path

    def run(self):
        boundary = uuid.uuid4().hex
        body_parts = []

        # Ventas
        if self.ventas_path:
            with open(self.ventas_path, "rb") as f:
                ventas_bytes = f.read()
            fname = os.path.basename(self.ventas_path)
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="ventas"; filename="{fname}"\r\n'
                f'Content-Type: text/csv\r\n\r\n'.encode() + ventas_bytes + b'\r\n'
            )

        # Detalle
        if self.detalle_path:
            with open(self.detalle_path, "rb") as f:
                detalle_bytes = f.read()
            fname = os.path.basename(self.detalle_path)
            ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" \
                if fname.endswith(".xlsx") else "text/csv"
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="detalle"; filename="{fname}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n'.encode() + detalle_bytes + b'\r\n'
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
            data = json.loads(raw)
            self.finished.emit(data)
        except urllib.error.URLError as e:
            self.error.emit(f"Error de red: {e.reason}")
        except Exception as e:
            self.error.emit(str(e))
