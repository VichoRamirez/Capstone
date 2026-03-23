from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import PlainTextResponse
import json

from backend.schemas import OptimizerParams
from backend.services.optimizer_service import run_optimization

from typing import Optional

router = APIRouter()

@router.get("/health")
async def health_check():
    """Simple endpoint for the frontend to verify backend connection."""
    return {"status": "ok"}

@router.post("/optimize", response_class=PlainTextResponse)
async def optimize_route(
    params: str = Form(..., description="JSON string containing OptimizerParams"),
    orders: Optional[UploadFile] = File(None, description="Optional CSV file of orders")
):
    """
    Receives optimization parameters and a CSV file, parses them, 
    runs the heuristic optimizer, and returns a CSV response containing Route Schedule.
    """
    try:
        # Parse params string into Pydantic model
        params_dict = json.loads(params)
        validated_params = OptimizerParams(**params_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON params: {str(e)}")

    if orders is not None and not orders.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a CSV.")

    try:
        if orders is not None:
            # Read the uploaded CSV data
            csv_content = await orders.read()
            csv_text = csv_content.decode("utf-8")
        else:
            csv_text = None
        
        # Run optimizer service which returns exactly the expected CSV string
        
        result_csv = run_optimization(validated_params, csv_text)
        
        return result_csv
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimization failed: {str(e)}")
