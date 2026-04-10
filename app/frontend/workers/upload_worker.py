import os
import uuid
import urllib.request
import urllib.error
from PyQt6.QtCore import QThread, pyqtSignal

class UploadWorker(QThread):
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, url: str, file_path: str, extra_fields: dict = None):
        super().__init__()
        self.url = url
        self.file_path = file_path
        self.extra_fields = extra_fields or {}

    def run(self):
        if not self.file_path:
            self.error.emit("No file selected.")
            return

        boundary = uuid.uuid4().hex
        body_parts = []

        # Campos extra (ej: user_id)
        for field_name, field_value in self.extra_fields.items():
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'
                f'{field_value}\r\n'.encode()
            )

        with open(self.file_path, "rb") as f:
            file_bytes = f.read()

        fname = os.path.basename(self.file_path)
        body_parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            f'Content-Type: text/csv\r\n\r\n'.encode() + file_bytes + b'\r\n'
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
                import json
                res = json.loads(resp.read().decode("utf-8"))
            self.finished.emit(res)
        except urllib.error.URLError as e:
            self.error.emit(f"Network error: {e.reason}")
        except Exception as e:
            self.error.emit(str(e))
