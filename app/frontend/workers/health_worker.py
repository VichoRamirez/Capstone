import time
import urllib.request
import urllib.error
from PyQt6.QtCore import QThread, pyqtSignal

class HealthWorker(QThread):
    connected = pyqtSignal(bool)   # True if connected to backend, False otherwise

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url.rstrip('/')  # remove trailing slash
        self._is_running = True

    def run(self):
        while self._is_running:
            try:
                # Ping the health endpoint
                req = urllib.request.Request(
                    f"{self.url}/health",
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        self.connected.emit(True)
                        print("[HealthWorker] Connected to backend.")
                    else:
                        print(f"[HealthWorker] Bad status: {resp.status}")
                        self.connected.emit(False)
            except Exception as e:
                print(f"[HealthWorker] Exception: {e}")
                self.connected.emit(False)
            
            # Wait 5 seconds before checking again
            for _ in range(10): # polling frequently for exit conditions
                if not self._is_running: break
                time.sleep(0.5)

    def stop(self):
        self._is_running = False
        self.wait()
