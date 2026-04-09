"""
Punto de entrada principal de la aplicación.
Launches the FastAPI backend (uvicorn) in a background thread and then
starts the PyQt6 frontend. The backend thread is daemonized so it stops
automatically when the application exits.
"""
import sys
import os
import threading

import uvicorn
from PyQt6.QtWidgets import QApplication

# Ensure the 'app' directory is in sys.path so backend/frontend modules resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qt_runtime import prepare_qt_platform_plugins
from frontend.app import MainApp


def _run_backend():
    """Start the uvicorn server for the FastAPI backend."""
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,   # reload=True is not safe inside a thread
        log_level="info",
    )


def main():
    prepare_qt_platform_plugins()

    # --- Backend ---
    backend_thread = threading.Thread(target=_run_backend, daemon=True)
    backend_thread.start()

    # --- Frontend ---
    app = QApplication(sys.argv)
    window = MainApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
