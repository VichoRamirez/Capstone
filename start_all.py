import subprocess
import time
import sys
import os

def start_all():
    # Detect root directory
    root = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Start Backend (FastAPI)
    print("🚀 Starting Backend (FastAPI)...")
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=root
    )

    # Give the backend a few seconds to initialize
    time.sleep(3)

    # 2. Start SaaS Frontend
    print("🎨 Starting SaaS Frontend...")
    frontend_proc = subprocess.Popen(
        [sys.executable, "app/frontend/app.py"],
        cwd=root
    )

    # 3. Start Order Simulation
    print("🛒 Starting Order Simulation...")
    sim_proc = subprocess.Popen(
        [sys.executable, "app/simulation/main.py"],
        cwd=root
    )

    print("\n✅ All systems are running!")
    print("Press Ctrl+C to stop all processes.")

    try:
        # Keep the script alive while processes are running
        while True:
            time.sleep(1)
            if backend_proc.poll() is not None:
                print("Backend stopped unexpectedly.")
                break
            if frontend_proc.poll() is not None:
                print("Frontend stopped.")
                break
            if sim_proc.poll() is not None:
                print("Simulation stopped.")
                break
    except KeyboardInterrupt:
        print("\n🛑 Stopping all systems...")
    finally:
        # Terminate everything on exit
        backend_proc.terminate()
        frontend_proc.terminate()
        sim_proc.terminate()
        print("Done.")

if __name__ == "__main__":
    start_all()
