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
    """Worker para el endpoint /optimize en patrón asíncrono (crea job, hace polling, obtiene resultado)."""
    finished = pyqtSignal(dict)   # JSON response completa
    error    = pyqtSignal(str)

    def __init__(self, url: str, params: dict,
                 ventas_path: str, detalle_path: str):
        super().__init__()
        self.url = url
        self.params = params
        self.ventas_path = ventas_path
        self.detalle_path = detalle_path
        self._running = True
        
        # self.url will be something like "http://localhost:8000/api/optimize"
        # We need the base URL to poll the job status
        self.base_url = self.url.rsplit("/optimize", 1)[0]

    def _should_stop(self) -> bool:
        return (not self._running) or self.isInterruptionRequested()

    def stop(self):
        self._running = False
        self.requestInterruption()

    def run(self):
        if self._should_stop():
            return

        # 1. Start the job by POSTing to /optimize
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
            with urllib.request.urlopen(req, timeout=60) as resp:
                if self._should_stop():
                    return
                raw_response = resp.read().decode("utf-8")
                start_data = json.loads(raw_response)
                job_id = start_data.get("job_id")
                
            if not job_id:
                self.error.emit("El backend no retornó un ID de trabajo válido.")
                return
                
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                detail = err_body.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"Error al iniciar optimización {e.code}: {detail}")
            return
        except Exception as e:
            self.error.emit(f"Error de red al iniciar: {str(e)}")
            return

        # 2. Poll for completion
        status_url = f"{self.base_url}/jobs/{job_id}/status"
        while not self._should_stop():
            self.msleep(1000)  # Wait 1 second between polls
            if self._should_stop():
                return
            try:
                req_status = urllib.request.Request(status_url, method="GET")
                with urllib.request.urlopen(req_status, timeout=10) as resp:
                    if self._should_stop():
                        return
                    status_data = json.loads(resp.read().decode("utf-8"))
                    
                status = status_data.get("status")
                if status == "error":
                    # Will be caught by result fetch, but we can fast-fail
                    break
                elif status == "done":
                    break
            except Exception as e:
                # Log non-fatal polling errors privately or ignore
                pass

        if self._should_stop():
            return

        # 3. Fetch the final result
        result_url = f"{self.base_url}/jobs/{job_id}/result"
        try:
            req_result = urllib.request.Request(result_url, method="GET")
            with urllib.request.urlopen(req_result, timeout=60) as resp:
                if self._should_stop():
                    return
                result_data = json.loads(resp.read().decode("utf-8"))
                self.finished.emit(result_data)
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                detail = err_body.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"Error en el trabajo {e.code}: {detail}")
        except Exception as e:
            self.error.emit(f"Error al obtener resultados: {str(e)}")


class CleanWorker(QThread):
    """Worker para el endpoint /clean — retorna JSON de preview."""
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, url: str, ventas_path: str, detalle_path: str):
        super().__init__()
        self.url = url
        self.ventas_path = ventas_path
        self.detalle_path = detalle_path
        self._running = True

    def _should_stop(self) -> bool:
        return (not self._running) or self.isInterruptionRequested()

    def stop(self):
        self._running = False
        self.requestInterruption()

    def run(self):
        if self._should_stop():
            return
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
                if self._should_stop():
                    return
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            self.finished.emit(data)
        except urllib.error.URLError as e:
            self.error.emit(f"Error de red: {e.reason}")
        except Exception as e:
            self.error.emit(str(e))
