from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
import json
import pandas as pd
import io

from backend.schemas import OptimizerParams
from backend.services.optimizer_service import run_optimization
from backend.services.cleaning_service import CleaningService
from database.repositories.venta_repository import VentaRepository
from database.repositories.detalle_repository import DetalleRepository
from database.repositories.producto_repository import ProductoRepository
from database.connection import get_session

from typing import Optional, List

router = APIRouter()
cleaning_service = CleaningService()

@router.get("/health")
async def health_check():
    """Simple endpoint for the frontend to verify backend connection."""
    return {"status": "ok"}

@router.get("/catalog")
async def get_catalog():
    """Returns the list of products in the catalog."""
    session = get_session()
    try:
        repo = ProductoRepository(session)
        products = repo.get_all()
        return [
            {
                "sku": p.sku,
                "descripcion": p.descripcion_sku,
                "largo": p.largo_cm,
                "ancho": p.ancho_cm,
                "alto": p.alto_cm,
                "volumen": p.volumen_unitario_m3,
                "peso": p.peso_unitario_kg,
                "tipo_embalaje": p.tipo_embalaje
            } for p in products
        ]
    finally:
        session.close()

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

@router.post("/upload")
async def upload_and_clean(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """
    Receives a CSV file, cleans it (standardization + geocoding), 
    and saves it to the database.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV.")
    
    try:
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content))
        
        # Start cleaning in background
        background_tasks.add_task(cleaning_service.process_dataframe, df)
        
        return JSONResponse(
            content={"message": "File uploaded and cleaning started in background.", 
                     "rows": len(df)},
            status_code=202
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/next-order-number")
async def get_next_order_number():
    """Returns the next sequential order number."""
    session = get_session()
    try:
        repo = VentaRepository(session)
        return {"next": repo.get_next_order_number()}
    finally:
        session.close()

@router.post("/simulation/order")
async def create_simulated_order(
    payload: dict, 
    background_tasks: BackgroundTasks
):
    """
    Receives a full order (header + items), saves to DB, and starts geocoding.
    """
    venta_data = payload.get("venta")
    items_data = payload.get("items")
    
    if not venta_data or not items_data:
        raise HTTPException(status_code=400, detail="Missing order header or items.")
    
    session = get_session()
    try:
        # 1. Save Detalle items first (requires numero_orden which is in the payload)
        d_repo = DetalleRepository(session)
        for item in items_data:
            item["numero_orden"] = venta_data["numero_orden"]
        d_repo.add_items(items_data)
        
        # 2. Trigger cleaning & geocoding in background (header will be saved here)
        print(f"🚀 Simulation: Backgrounding cleaning for Order {venta_data['numero_orden']}")
        background_tasks.add_task(cleaning_service.process_single_order, venta_data)
        
        return {"message": "Simulated order received. Cleaning & persist in progress."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create simulated order: {str(e)}")
    finally:
        session.close()
