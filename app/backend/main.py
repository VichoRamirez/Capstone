import sys
import os

# Ensure the 'app' directory is in sys.path so 'backend' module can be resolved
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api.router import router as api_router

app = FastAPI(
    title="Dispatch Optimizer API",
    description="Backend for the Fleet Management Optimizer using ALNS Heuristics",
    version="1.0.0"
)

# Configure CORS so the PyQt frontend or web app can communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the main router
app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
