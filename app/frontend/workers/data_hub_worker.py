"""
Workers QThread para las operaciones del Data Hub.

Contiene tres workers independientes:
- DataHubDashboardWorker: envía archivos CSV al endpoint /data/dashboard y retorna métricas.
- DataHubDashboardDbWorker: consulta el endpoint /data/dashboard-db para obtener métricas
  desde la base de datos MySQL del usuario autenticado.
- DataHubAddressValidationWorker: envía el CSV de ventas al endpoint /data/validate-addresses
  para verificar y geolocalizar las direcciones de entrega.

Todos heredan de QThread para no bloquear el hilo principal de Qt.
"""

import json
import os
import uuid
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal


def _append_text_part(parts: list[bytes], boundary: str, name: str, value: str):
    """Agrega un campo de texto plano al cuerpo multipart."""
    parts.append(
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n".encode("utf-8")
    )


def _append_file_part(parts: list[bytes], boundary: str, field: str, file_path: str, ctype_hint: str = "text/csv"):
    """
    Lee un archivo del disco y lo agrega como parte de un cuerpo multipart.
    Detecta automáticamente el Content-Type para archivos .xlsx.
    """
    with open(file_path, "rb") as f:
        payload = f.read()
    filename = os.path.basename(file_path)
    ctype = ctype_hint
    # Si el archivo es Excel, se usa el MIME type correspondiente
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
    """
    Construye el cuerpo completo de una petición multipart/form-data.
    Retorna una tupla (boundary, body_bytes) lista para adjuntar a un Request.
    """
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    # Archivo de ventas (obligatorio)
    _append_file_part(parts, boundary, "ventas", ventas_path, "text/csv")
    # Archivo de detalle (opcional)
    if detalle_path:
        _append_file_part(parts, boundary, "detalle", detalle_path, "text/csv")
    # Campos de texto adicionales (ej: parámetros de filtrado)
    if extra_fields:
        for key, value in extra_fields.items():
            _append_text_part(parts, boundary, str(key), str(value))
    # Delimitador de cierre del cuerpo multipart
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


class DataHubDashboardWorker(QThread):
    """
    Worker que realiza POST /data/dashboard enviando los archivos de ventas y detalle.
    Emite el JSON de métricas parseado cuando la petición es exitosa.
    """

    # Emitido con el diccionario de métricas del dashboard al completar con éxito
    finished = pyqtSignal(dict)
    # Emitido con el mensaje de error si la petición falla
    error = pyqtSignal(str)

    def __init__(self, url: str, ventas_path: str, detalle_path: str):
        super().__init__()
        self.url = str(url or "").strip()
        self.ventas_path = str(ventas_path or "").strip()
        self.detalle_path = str(detalle_path or "").strip()
        self._running = True

    def _should_stop(self) -> bool:
        """Verifica si el worker fue detenido externamente o interrumpido por Qt."""
        return (not self._running) or self.isInterruptionRequested()

    def stop(self):
        """Señala al worker que debe terminar en la próxima iteración."""
        self._running = False
        self.requestInterruption()

    def run(self):
        """Ejecuta la petición HTTP en el hilo secundario."""
        if self._should_stop():
            return
        # Ambos archivos son obligatorios para calcular métricas del dashboard
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
            # Timeout generoso (180 s) porque el backend puede tardar en procesar
            with urllib.request.urlopen(req, timeout=180) as resp:
                if self._should_stop():
                    return
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Respuesta inválida de Data Hub.")
            # Emite el diccionario de métricas para que la vista lo procese
            self.finished.emit(payload)
        except urllib.error.HTTPError as e:
            # Intenta extraer el detalle de error del cuerpo JSON de la respuesta
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = err.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"Data Hub HTTP {e.code}: {detail}")
        except Exception as e:
            self.error.emit(str(e))


class DataHubDashboardDbWorker(QThread):
    """
    Worker que realiza GET /data/dashboard-db?user_id={id} para obtener métricas
    directamente desde la base de datos MySQL del usuario autenticado.
    """

    # Emitido con el diccionario de métricas al completar con éxito
    finished = pyqtSignal(dict)
    # Emitido con el mensaje de error si la petición falla
    error = pyqtSignal(str)

    def __init__(self, url: str, user_id: int):
        super().__init__()
        self.url = str(url or "").strip()
        self.user_id = int(user_id)
        self._running = True

    def stop(self):
        """Señala al worker que debe terminar."""
        self._running = False
        self.requestInterruption()

    def run(self):
        """Ejecuta la consulta GET al endpoint del dashboard en el hilo secundario."""
        if not self._running or self.isInterruptionRequested():
            return
        try:
            # Adjunta el user_id como query parameter para filtrar por usuario
            req = urllib.request.Request(
                f"{self.url}?user_id={self.user_id}",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                if not self._running or self.isInterruptionRequested():
                    return
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Respuesta inválida del dashboard DB.")
            # Emite el diccionario de métricas para que la vista lo renderice
            self.finished.emit(payload)
        except urllib.error.HTTPError as e:
            # Intenta extraer el mensaje de error estructurado del backend
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = err.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"HTTP {e.code}: {detail}")
        except Exception as e:
            self.error.emit(str(e))


class DataHubAddressValidationWorker(QThread):
    """
    Worker que realiza POST /data/validate-addresses enviando el CSV de ventas.
    Opcionalmente limita el número de filas procesadas y de llamadas a la API de geocodificación.
    """

    # Emitido con el diccionario de resultados de validación al completar con éxito
    finished = pyqtSignal(dict)
    # Emitido con el mensaje de error si la petición falla
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
        # 0 significa sin límite en ambos parámetros
        self.max_rows = int(max_rows)
        self.max_api_probes = int(max_api_probes)
        self._running = True

    def _should_stop(self) -> bool:
        """Verifica si el worker fue detenido externamente o interrumpido por Qt."""
        return (not self._running) or self.isInterruptionRequested()

    def stop(self):
        """Señala al worker que debe terminar."""
        self._running = False
        self.requestInterruption()

    def run(self):
        """Ejecuta la validación de direcciones en el hilo secundario."""
        if self._should_stop():
            return
        if not self.ventas_path:
            self.error.emit("Ventas file is required for address validation.")
            return

        boundary, body = _build_multipart_body(
            ventas_path=self.ventas_path,
            # Los límites se envían como campos de texto en el multipart
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
            # Timeout de 300 s porque la geocodificación masiva puede ser lenta
            with urllib.request.urlopen(req, timeout=300) as resp:
                if self._should_stop():
                    return
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Respuesta inválida de validación.")
            # Emite el resultado de validación para que la vista lo muestre
            self.finished.emit(payload)
        except urllib.error.HTTPError as e:
            # Extrae el detalle de error del cuerpo JSON si está disponible
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = err.get("detail", str(e))
            except Exception:
                detail = str(e)
            self.error.emit(f"Validation HTTP {e.code}: {detail}")
        except Exception as e:
            self.error.emit(str(e))
