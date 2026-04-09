"""
Worker thread to request Data Hub dashboard metrics from backend.
"""

import json
import os
import uuid
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal


def _append_text_part(parts: list[bytes], boundary: str, name: str, value: str):
    parts.append(
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n".encode("utf-8")
    )


def _append_file_part(parts: list[bytes], boundary: str, field: str, file_path: str, ctype_hint: str = "text/csv"):
    with open(file_path, "rb") as f:
        payload = f.read()
    filename = os.path.basename(file_path)
    ctype = ctype_hint
    if filename.lower().endswith(".xlsx"):
        ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    parts.append(
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
        + payload
        + b"\r\n"
    )


def _build_multipart_body(
    *,
    ventas_path: str,
    detalle_path: str | None = None,
    extra_fields: dict[str, str] | None = None,
) -> tuple[str, bytes]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    _append_file_part(parts, boundary, "ventas", ventas_path, "text/csv")
    if detalle_path:
        _append_file_part(parts, boundary, "detalle", detalle_path, "text/csv")
    if extra_fields:
        for key, value in extra_fields.items():
            _append_text_part(parts, boundary, str(key), str(value))
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


class DataHubDashboardWorker(QThread):
    """POST /data/dashboard with ventas+detalle and emit parsed JSON."""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, url: str, ventas_path: str, detalle_path: str):
        super().__init__()
        self.url = str(url or "").strip()
        self.ventas_path = str(ventas_path or "").strip()
        self.detalle_path = str(detalle_path or "").strip()
        self._running = True

    def _should_stop(self) -> bool:
        return (not self._running) or self.isInterruptionRequested()

    def stop(self):
        self._running = False
        self.requestInterruption()

    def run(self):
        if self._should_stop():
            return
        if not self.ventas_path or not self.detalle_path:
            self.error.emit("Ventas y detalle son requeridos para Data Hub.")
            return

        boundary, body = _build_multipart_body(
            ventas_path=self.ventas_path,
            detalle_path=self.detalle_path,
        )

        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                if self._should_stop():
                    return
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Respuesta inválida de Data Hub.")
            self.finished.emit(payload)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = err.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"Data Hub HTTP {e.code}: {detail}")
        except Exception as e:
            self.error.emit(str(e))


class DataHubAddressValidationWorker(QThread):
    """POST /data/validate-addresses with ventas (+optional params)."""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        ventas_path: str,
        *,
        max_rows: int = 0,
        max_api_probes: int = 0,
    ):
        super().__init__()
        self.url = str(url or "").strip()
        self.ventas_path = str(ventas_path or "").strip()
        self.max_rows = int(max_rows)
        self.max_api_probes = int(max_api_probes)
        self._running = True

    def _should_stop(self) -> bool:
        return (not self._running) or self.isInterruptionRequested()

    def stop(self):
        self._running = False
        self.requestInterruption()

    def run(self):
        if self._should_stop():
            return
        if not self.ventas_path:
            self.error.emit("Ventas file is required for address validation.")
            return

        boundary, body = _build_multipart_body(
            ventas_path=self.ventas_path,
            extra_fields={
                "max_rows": str(max(0, self.max_rows)),
                "max_api_probes": str(max(0, self.max_api_probes)),
            },
        )

        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                if self._should_stop():
                    return
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Respuesta inválida de validación.")
            self.finished.emit(payload)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = err.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"Validation HTTP {e.code}: {detail}")
        except Exception as e:
            self.error.emit(str(e))
