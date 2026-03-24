import os
import uuid
import urllib.request
import urllib.error
from PyQt6.QtCore import QThread, pyqtSignal

class UploadWorker(QThread):
    finished = pyqtSignal(dict) # Full response dict
    error    = pyqtSignal(str)

    def __init__(self, url: str, csv_path: str):
        super().__init__()
        self.url = url
        self.csv_path = csv_path

    def run(self):
        if not self.csv_path:
            self.error.emit("No file selected.")
            return

        boundary = uuid.uuid4().hex
        body_parts = []

        with open(self.csv_path, "rb") as f:
            csv_bytes = f.read()
        
        fname = os.path.basename(self.csv_path)
        body_parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
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
            with urllib.request.urlopen(req, timeout=120) as resp: # Increased timeout for cleaning
                import json
                res = json.loads(resp.read().decode("utf-8"))
            self.finished.emit(res)
        except urllib.error.URLError as e:
            self.error.emit(f"Network error: {e.reason}")
        except Exception as e:
            self.error.emit(str(e))
