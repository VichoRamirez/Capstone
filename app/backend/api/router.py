import asyncio
import io
import json
import logging
import uuid
import pandas as pd
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
from typing import Optional, List

from backend.schemas import OptimizerParams
from backend.services.optimizer_service import run_optimization
from backend.services.cleaning_service import CleaningService, run_full_cleaning
from backend.services import auth_service
from database.repositories.venta_repository import VentaRepository
from database.repositories.detalle_repository import DetalleRepository
from database.repositories.producto_repository import ProductoRepository
from database.connection import get_session

logger = logging.getLogger(__name__)

router = APIRouter()
cleaning_service = CleaningService()

# In-memory task database
optimization_tasks = {}

async def _run_optimization_task_internal(
    task_id: str,
    ventas_bytes: Optional[bytes],
    detalle_bytes: Optional[bytes],
    ventas_filename: Optional[str],
    detalle_filename: Optional[str],
    validated_params: OptimizerParams
):
    global optimization_tasks
    try:
        df_ventas = pd.DataFrame()
        df_detalle = pd.DataFrame()
        cleaning_errors = []

        if ventas_bytes and detalle_bytes:
            optimization_tasks[task_id]["progress"] = "Estandarizando datos..."
            ventas_text = ventas_bytes.decode("utf-8")

            if detalle_filename and detalle_filename.endswith(".xlsx"):
                df_det = await asyncio.to_thread(pd.read_excel, io.BytesIO(detalle_bytes))
                detalle_text = df_det.to_csv(index=False)
            else:
                detalle_text = detalle_bytes.decode("utf-8")

            # ── Limpiar + geocodificar ──
            cleaning = await asyncio.to_thread(
                run_full_cleaning, ventas_text, detalle_text, True
            )
            df_ventas = cleaning.df_ventas
            df_detalle = cleaning.df_detalle
            cleaning_errors = cleaning.errores
        else:
            optimization_tasks[task_id]["progress"] = "Buscando pedidos pendientes en la base de datos..."
            df_ventas = await asyncio.to_thread(cleaning_service.get_pending_orders_df, validated_params.user_id)
            if df_ventas.empty:
                optimization_tasks[task_id].update({
                    "status": "completed",
                    "result": {
                        "days": [],
                        "cleaning_errors": [{"origen": "DB", "error": "No hay pedidos Pendiente en la base de datos."}],
                    }
                })
                return

        # ── Join weight/volume totals (solo si vinieron de CSV) ──
        if not df_detalle.empty and "Número de Orden" in df_detalle.columns:
            agg_cols = {}
            if "Peso_total_kg" in df_detalle.columns:
                agg_cols["Peso_total_kg"] = "sum"
            elif "Peso_unitario_kg" in df_detalle.columns and "Cantidad" in df_detalle.columns:
                df_detalle = df_detalle.copy()
                df_detalle["Peso_total_kg"] = df_detalle["Peso_unitario_kg"] * df_detalle["Cantidad"]
                agg_cols["Peso_total_kg"] = "sum"

            if "Volumen_total_m3" in df_detalle.columns:
                agg_cols["Volumen_total_m3"] = "sum"
            elif "Volumen_unitario_m3" in df_detalle.columns and "Cantidad" in df_detalle.columns:
                df_detalle = df_detalle.copy()
                df_detalle["Volumen_total_m3"] = df_detalle["Volumen_unitario_m3"] * df_detalle["Cantidad"]
                agg_cols["Volumen_total_m3"] = "sum"

            if agg_cols:
                order_totals = df_detalle.groupby("Número de Orden").agg(agg_cols).reset_index()
                order_totals = order_totals.rename(columns={
                    "Peso_total_kg": "Peso_total_pedido",
                    "Volumen_total_m3": "Volumen_total_pedido",
                })
                df_ventas = df_ventas.merge(order_totals, on="Número de Orden", how="left")

        # ── Construir batches por día ──
        from backend.api.router import _build_day_batches
        batches = await asyncio.to_thread(
            _build_day_batches, df_ventas, validated_params.deliveries_per_day
        )

        # ── Optimizar por día ──
        day_results = []
        for idx, (label, batch_df) in enumerate(batches):
            if batch_df.empty:
                continue
            optimization_tasks[task_id]["progress"] = f"Optimizando día {idx + 1}/{len(batches)}: {label} ({len(batch_df)} pedidos)..."
            try:
                result = await asyncio.to_thread(run_optimization, validated_params, batch_df)
                day_results.append({
                    "date": label,
                    "routes_csv": result.routes_csv,
                    "uncovered_csv": result.uncovered_csv,
                    "map_html": result.map_html,
                    "stats": result.stats,
                })
            except Exception as day_err:
                logger.error(f"Error optimizando día '{label}': {day_err}")
                day_results.append({
                    "date": label,
                    "routes_csv": "",
                    "uncovered_csv": "",
                    "map_html": f"<p>Error al optimizar: {day_err}</p>",
                    "stats": {},
                })

        optimization_tasks[task_id].update({
            "status": "completed",
            "result": {
                "days": day_results,
                "cleaning_errors": cleaning_errors,
            }
        })
    except Exception as e:
        logger.exception(f"Error en tarea {task_id}")
        optimization_tasks[task_id].update({
            "status": "error",
            "error": str(e)
        })

# Current processing stage (shown in the UI via GET /progress)
_stage: str = ""

def _set_stage(msg: str):
    global _stage
    _stage = msg
    logger.info(f"[stage] {msg}")

@router.get("/progress")
async def get_progress():
    """Returns the current processing stage for the frontend to poll."""
    return {"stage": _stage}

@router.get("/health")
async def health_check():
    """Simple endpoint for the frontend to verify backend connection."""
    return {"status": "ok"}

# ── Auth endpoints ────────────────────────────────────────────────────────

@router.post("/auth/register")
async def register(payload: dict):
    """
    Registra un nuevo usuario.
    Espera: {username, email, password}
    """
    username = payload.get("username", "").strip()
    email = payload.get("email", "").strip()
    password = payload.get("password", "")

    result = auth_service.register_user(username, email, password)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/auth/login")
async def login(payload: dict):
    """
    Inicia sesión por username o email.
    Espera: {identifier, password}
    """
    identifier = payload.get("identifier", "").strip()
    password = payload.get("password", "")

    result = auth_service.login_user(identifier, password)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


@router.post("/auth/reset-password")
async def reset_password(payload: dict):
    """
    Restaura la contraseña de un usuario mediante username y correo validado.
    Espera: {username, email, new_password}
    """
    username = payload.get("username", "").strip()
    email = payload.get("email", "").strip()
    new_password = payload.get("new_password", "")

    result = auth_service.reset_password(username, email, new_password)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

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

@router.post("/clean")
async def clean_data(
    ventas: UploadFile = File(..., description="CSV de ventas"),
    detalle: UploadFile = File(..., description="CSV o XLSX de detalle de pedidos"),
):
    """
    Limpia los datos de ventas y detalle sin ejecutar el modelo.
    Retorna estadísticas, errores de estandarización, y una previsualización.
    """
    try:
        ventas_bytes = await ventas.read()
        ventas_text = ventas_bytes.decode("utf-8")

        detalle_bytes = await detalle.read()
        if detalle.filename and detalle.filename.endswith(".xlsx"):
            df_det = pd.read_excel(io.BytesIO(detalle_bytes))
            detalle_text = df_det.to_csv(index=False)
        else:
            detalle_text = detalle_bytes.decode("utf-8")

        result = run_full_cleaning(ventas_text, detalle_text, geocode=False)

        return {
            "total_ventas": len(result.df_ventas),
            "total_detalle": len(result.df_detalle),
            "errores": result.errores,
            "preview_ventas": json.loads(
                result.df_ventas.head(20).to_json(orient="records", date_format="iso")
            ),
            "preview_detalle": json.loads(
                result.df_detalle.head(20).to_json(orient="records")
            ),
        }

    except Exception as e:
        logger.exception("Error en limpieza")
        raise HTTPException(status_code=500, detail=f"Error limpiando datos: {str(e)}")

# ── Day-batching helper ───────────────────────────────────────────────────

def _build_day_batches(
    df: pd.DataFrame,
    deliveries_per_day: int,
    date_col: str = "Fecha de despacho Solicitada",
) -> list[tuple[str, pd.DataFrame]]:
    """
    Sort df by date_col, group by calendar day, and build batches of at most
    deliveries_per_day rows using a carry-over queue.
    """
    if date_col not in df.columns:
        return [("all", df.copy())]

    df = df.copy()
    df["_dispatch_day"] = pd.to_datetime(df[date_col], errors="coerce").dt.date

    # Separate rows with valid vs null dispatch date
    df_dated = df.dropna(subset=["_dispatch_day"]).sort_values("_dispatch_day")
    df_null  = df[df["_dispatch_day"].isna()].drop(columns=["_dispatch_day"])

    queue = pd.DataFrame(columns=df_dated.columns)
    batches: list[tuple[str, pd.DataFrame]] = []

    for day, day_df in df_dated.groupby("_dispatch_day"):
        queue = pd.concat([queue, day_df], ignore_index=True)

        batch = queue.iloc[:deliveries_per_day].copy().drop(columns=["_dispatch_day"])
        queue = queue.iloc[deliveries_per_day:].copy()

        label = str(day)
        batches.append((label, batch))

    # Remaining queue after all calendar days
    if len(queue) > 0:
        overflow = queue.drop(columns=["_dispatch_day"])
        batches.append(("overflow", overflow))

    # Rows with no dispatch date go last
    if len(df_null) > 0:
        batches.append(("sin fecha", df_null))

    return batches

@router.post("/optimize")
async def optimize_route(
    background_tasks: BackgroundTasks,
    params: str = Form(..., description="JSON string containing OptimizerParams"),
    ventas: Optional[UploadFile] = File(None, description="CSV de ventas"),
    detalle: Optional[UploadFile] = File(None, description="CSV o XLSX de detalle de pedidos"),
):
    """
    Inicia el proceso de optimización en segundo plano y devuelve un task_id.
    """
    try:
        params_dict = json.loads(params)
        validated_params = OptimizerParams(**params_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Parámetros JSON inválidos: {str(e)}")

    task_id = str(uuid.uuid4())
    optimization_tasks[task_id] = {"status": "running", "progress": "Iniciando...", "result": None}

    # Leer bytes antes de que se cierre el archivo
    ventas_bytes = await ventas.read() if ventas else None
    detalle_bytes = await detalle.read() if detalle else None
    
    background_tasks.add_task(
        _run_optimization_task_internal,
        task_id,
        ventas_bytes,
        detalle_bytes,
        ventas.filename if ventas else None,
        detalle.filename if detalle else None,
        validated_params
    )

    return {"task_id": task_id}

@router.get("/optimize/status/{task_id}")
async def get_optimization_status(task_id: str):
    """
    Consulta el estado de una tarea de optimización.
    """
    task = optimization_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return task

@router.get("/optimize/result/{task_id}")
async def get_optimization_result(task_id: str):
    """
    Obtiene el resultado final de la optimización.
    """
    task = optimization_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="La tarea aún no ha terminado")
    return task["result"]

@router.post("/upload")
async def upload_ventas(
    file: UploadFile = File(...)
):
    """
    Recibe un archivo CSV de VENTAS, lo limpia y lo guarda en la base de datos.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un CSV.")
    
    try:
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content))
        results = await asyncio.to_thread(cleaning_service.process_dataframe, df)
        success_count = sum(1 for r in results if r["status"] == "ok")
        error_count = len(results) - success_count
        return {
            "message": f"Ventas procesadas: {success_count} exitosos, {error_count} con errores.",
            "success_count": success_count,
            "error_count": error_count,
            "details": results
        }
    except Exception as e:
        logger.exception("Error en subida de ventas")
        raise HTTPException(status_code=500, detail=f"Fallo en la subida: {str(e)}")

@router.post("/upload-detalle")
async def upload_detalle(
    file: UploadFile = File(...)
):
    """
    Recibe un archivo CSV de DETALLE, lo limpia y lo guarda en la base de datos.
    """
    try:
        content = await file.read()
        if file.filename.endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            df = pd.read_csv(io.BytesIO(content))
        
        # Limpieza básica de detalle (agregación y tipos)
        from backend.services.cleaning_service import CleaningService
        svc = CleaningService()
        df_clean, errs = svc.clean_detalle(df.to_csv(index=False))
        
        if errs:
            return {"message": "Error en formato de detalle", "errors": errs}

        # Guardar en DB usando DetalleRepository
        from database.repositories.detalle_repository import DetalleRepository
        session = get_session()
        try:
            repo = DetalleRepository(session)
            # Primero borrar detalles existentes para estas órdenes (SaaS logic logic)
            # O simplemente add_items si confiamos en el upsert
            repo.add_items(df_clean.to_dict("records"))
        finally:
            session.close()

        return {
            "message": f"Detalle procesado: {len(df_clean)} registros únicos guardados.",
            "count": len(df_clean)
        }
    except Exception as e:
        logger.exception("Error en subida de detalle")
        raise HTTPException(status_code=500, detail=f"Fallo en la subida: {str(e)}")

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
