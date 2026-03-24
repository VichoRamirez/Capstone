"""
ProgressWorker — polls GET /progress every 2 s while a model run is active,
emitting the current stage string so the UI can display it in real time.
"""

import json
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal


class ProgressWorker(QThread):
    stage_updated = pyqtSignal(str)   # emits the current stage string
    stopped       = pyqtSignal()      # emits when the thread exits

    POLL_INTERVAL_MS = 2000

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self._base_url = base_url.rstrip("/")
        self._running  = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            try:
                url = f"{self._base_url}/progress"
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data  = json.loads(resp.read().decode("utf-8"))
                    stage = data.get("stage", "")
                self.stage_updated.emit(stage)
            except Exception:
                pass  # backend may be busy; silently ignore
            self.msleep(self.POLL_INTERVAL_MS)
        self.stopped.emit()
