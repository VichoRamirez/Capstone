"""
API Router — endpoints para limpieza y optimización de rutas.
"""

import asyncio
import io
import json
import logging
import os
import time
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta

import pandas as pd
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional

from backend.schemas import OptimizerParams, CleaningResponse
from backend.services.cleaning_service import (
    run_full_cleaning,
    clean_ventas,
    clean_detalle,
    estandarizar_direccion_y_comuna,
    geocodificar_direccion,
    cargar_cache,
    guardar_cache,
    NOMINATIM_URL,
)
from backend.services.fuel_price_service import (
    fetch_diesel_price_clp,
    fetch_fuel_price_clp,
    normalize_fuel_type,
    SUPPORTED_FUEL_TYPES,
)
from backend.services.optimizer_service import run_optimization

logger = logging.getLogger(__name__)
router = APIRouter()

_PUBLIC_NOMINATIM_URL = os.getenv(
    "NOMINATIM_FALLBACK_URL",
    "https://nominatim.openstreetmap.org",
)

# Current processing stage (shown in the UI via GET /progress)
_stage: str = ""


def _set_stage(msg: str):
    global _stage
    _stage = msg
    logger.info(f"[stage] {msg}")


def _depot_query_variants(raw_query: str, street: str, comuna: str) -> list[str]:
    variants: list[str] = []

    q = str(raw_query or "").strip()
    st = str(street or "").strip()
    cm = str(comuna or "").strip() or "Santiago"

    if st:
        variants.append(f"{st}, {cm}, Región Metropolitana, Chile")
        variants.append(f"{st}, {cm}, Chile")
        variants.append(f"{st}, Santiago, Chile")

    if q:
        variants.append(q)
        variants.append(f"{q}, Chile")
        variants.append(q.replace("Santiago Centro", "Santiago"))
        variants.append(q.replace("stgo centro", "santiago").replace("Stgo Centro", "Santiago"))

    dedup: list[str] = []
    seen: set[str] = set()
    for item in variants:
        val = str(item or "").strip()
        key = val.lower()
        if not val or key in seen:
            continue
        seen.add(key)
        dedup.append(val)
    return dedup


def _address_query_variants(
    address: str,
    comuna: str,
    *,
    raw_address: str = "",
    raw_comuna: str = "",
) -> list[str]:
    """Build robust free-text query variants for address geocoding."""
    addr = str(address or "").strip()
    com = str(comuna or "").strip()
    raw_addr = str(raw_address or "").strip()
    raw_com = str(raw_comuna or "").strip()

    variants: list[str] = []
    if addr and com:
        variants.append(f"{addr}, {com}, Región Metropolitana, Chile")
        variants.append(f"{addr}, {com}, Chile")
    if addr:
        variants.append(f"{addr}, Región Metropolitana, Chile")
        variants.append(f"{addr}, Chile")
        variants.append(f"{addr}, Santiago, Región Metropolitana, Chile")
    if raw_addr:
        variants.append(raw_addr)
        variants.append(f"{raw_addr}, Chile")
        if raw_com:
            variants.append(f"{raw_addr}, {raw_com}, Chile")

    dedup: list[str] = []
    seen: set[str] = set()
    for item in variants:
        q = str(item or "").strip()
        if not q:
            continue
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(q)
    return dedup


def _is_local_endpoint(endpoint: str) -> bool:
    ep = str(endpoint or "").strip().lower()
    return ep.startswith("http://localhost") or ep.startswith("http://127.0.0.1")


def _geocode_with_fallback(
    address: str,
    comuna: str,
    *,
    raw_address: str = "",
    raw_comuna: str = "",
    timeout_sec: float = 3.5,
) -> tuple[Optional[float], Optional[float], str]:
    """
    Geocode an address trying local Nominatim first, then fallback endpoint.
    Returns (lat, lon, source_label). On failure source_label may contain error tag.
    """
    addr = str(address or "").strip()
    com = str(comuna or "").strip()
    if not addr:
        return None, None, "empty_address"

    endpoints: list[str] = []
    local_ep = str(NOMINATIM_URL or "").strip().rstrip("/")
    if local_ep:
        endpoints.append(local_ep)
    fallback_ep = str(_PUBLIC_NOMINATIM_URL or "").strip().rstrip("/")
    if fallback_ep and fallback_ep not in endpoints:
        endpoints.append(fallback_ep)

    if not endpoints:
        return None, None, "no_endpoint"

    queries = _address_query_variants(
        addr,
        com,
        raw_address=raw_address,
        raw_comuna=raw_comuna,
    )

    last_error = ""
    for endpoint in endpoints:
        ep_kind = "local" if _is_local_endpoint(endpoint) else "fallback"

        # Structured search tends to perform better when comuna is reliable.
        if com:
            try:
                structured = _nominatim_search_structured(
                    endpoint,
                    street=addr,
                    city=com,
                    timeout=float(timeout_sec),
                )
                if structured:
                    return (
                        float(structured["lat"]),
                        float(structured["lon"]),
                        f"{ep_kind}_structured",
                    )
            except Exception as e:
                last_error = str(e)

        for q_try in queries:
            try:
                res = _nominatim_search(endpoint, q_try, timeout=float(timeout_sec))
                if res:
                    return (
                        float(res["lat"]),
                        float(res["lon"]),
                        f"{ep_kind}_free",
                    )
            except Exception as e:
                last_error = str(e)

    if last_error:
        return None, None, f"error:{last_error}"
    return None, None, "no_match"


def _nominatim_search(endpoint: str, query: str, timeout: float = 8.0):
    base = str(endpoint or "").strip().rstrip("/")
    if not base:
        return None

    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "cl",
            "addressdetails": 1,
        }
    )
    url = f"{base}/search?{params}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "CapstoneAnalytics/1.0 (depot-validation)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        return None

    first = payload[0]
    lat = first.get("lat")
    lon = first.get("lon")
    if lat is None or lon is None:
        return None

    return {
        "lat": float(lat),
        "lon": float(lon),
        "display_name": str(first.get("display_name") or query),
        "query_used": query,
        "endpoint_used": base,
    }


def _nominatim_search_structured(endpoint: str, street: str, city: str, timeout: float = 8.0):
    base = str(endpoint or "").strip().rstrip("/")
    st = str(street or "").strip()
    ct = str(city or "").strip()
    if not base or not st:
        return None

    params = urllib.parse.urlencode(
        {
            "street": st,
            "city": ct or "Santiago",
            "state": "Región Metropolitana",
            "country": "Chile",
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
    )
    url = f"{base}/search?{params}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "CapstoneAnalytics/1.0 (depot-validation)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        return None

    first = payload[0]
    lat = first.get("lat")
    lon = first.get("lon")
    if lat is None or lon is None:
        return None

    display = str(first.get("display_name") or f"{st}, {ct}, Chile")
    return {
        "lat": float(lat),
        "lon": float(lon),
        "display_name": display,
        "query_used": f"street={st};city={ct}",
        "endpoint_used": base,
    }


@router.get("/progress")
async def get_progress():
    """Returns the current processing stage for the frontend to poll."""
    return {"stage": _stage}


@router.get("/health")
async def health_check():
    """Simple endpoint for the frontend to verify backend connection."""
    return {"status": "ok"}


@router.get("/fuel/diesel-clp")
async def get_diesel_price_clp(force_refresh: bool = False):
    """
    Retorna precio referencial de diésel en CLP/L para Chile.
    """
    try:
        price, meta = await asyncio.to_thread(
            fetch_diesel_price_clp,
            force_refresh=bool(force_refresh),
            timeout_sec=8.0,
        )
        out_meta = meta if isinstance(meta, dict) else {}
        return {
            "diesel_price_clp": round(float(price), 4),
            "source": str(out_meta.get("source", "unknown")),
            "sample_size": int(out_meta.get("sample_size", 0) or 0),
            "updated_at": out_meta.get("updated_at"),
            "meta": out_meta,
        }
    except Exception as e:
        logger.exception("Error fetching diesel price")
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch diesel price from API: {str(e)}",
        )


@router.get("/fuel/prices-clp")
async def get_fuel_price_clp(
    fuel_type: str = "diesel",
    force_refresh: bool = False,
):
    """
    Retorna precio referencial de combustible en CLP/L para Chile.
    fuel_type soportado: diesel, gasoline_93, gasoline_95, gasoline_97
    """
    selected = normalize_fuel_type(fuel_type)
    try:
        price, meta = await asyncio.to_thread(
            fetch_fuel_price_clp,
            selected,
            force_refresh=bool(force_refresh),
            timeout_sec=8.0,
        )
        out_meta = meta if isinstance(meta, dict) else {}
        resp = {
            "fuel_type": selected,
            "fuel_types_supported": list(SUPPORTED_FUEL_TYPES),
            "fuel_price_clp": round(float(price), 4),
            "source": str(out_meta.get("source", "unknown")),
            "sample_size": int(out_meta.get("sample_size", 0) or 0),
            "updated_at": out_meta.get("updated_at"),
            "meta": out_meta,
        }
        if selected == "diesel":
            # Backward compatibility with old frontend payload key.
            resp["diesel_price_clp"] = round(float(price), 4)
        return resp
    except Exception as e:
        logger.exception("Error fetching fuel price")
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch fuel price from API: {str(e)}",
        )


@router.get("/validate-depot")
async def validate_depot_address(address: str):
    """
    Valida una dirección de CD mediante geocodificación y retorna coordenadas.
    """
    query = str(address or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Depot address is required.")

    try:
        street, comuna = estandarizar_direccion_y_comuna(query)
        street = str(street or "").strip()
        comuna = str(comuna or "Santiago").strip() or "Santiago"

        local_lat, local_lon = geocodificar_direccion(street, comuna)
        if local_lat is not None and local_lon is not None:
            return {
                "is_valid": True,
                "query": query,
                "normalized_address": f"{street}, {comuna}, Región Metropolitana, Chile",
                "lat": float(local_lat),
                "lon": float(local_lon),
                "reason": None,
            }

        queries = _depot_query_variants(query, street, comuna)
        endpoints = []
        if NOMINATIM_URL:
            endpoints.append(str(NOMINATIM_URL).strip().rstrip("/"))
        fallback = str(_PUBLIC_NOMINATIM_URL or "").strip().rstrip("/")
        if fallback and fallback not in endpoints:
            endpoints.append(fallback)

        last_error = None
        for endpoint in endpoints:
            try:
                structured = _nominatim_search_structured(
                    endpoint,
                    street=street,
                    city=comuna,
                    timeout=8.0,
                )
                if structured:
                    return {
                        "is_valid": True,
                        "query": query,
                        "normalized_address": structured["display_name"],
                        "lat": float(structured["lat"]),
                        "lon": float(structured["lon"]),
                        "reason": None,
                    }
            except Exception as search_err:
                last_error = str(search_err)

            for q_try in queries:
                try:
                    res = _nominatim_search(endpoint, q_try, timeout=8.0)
                    if not res:
                        continue
                    return {
                        "is_valid": True,
                        "query": query,
                        "normalized_address": res["display_name"],
                        "lat": float(res["lat"]),
                        "lon": float(res["lon"]),
                        "reason": None,
                    }
                except Exception as search_err:
                    last_error = str(search_err)
                    continue

        reason = "Address could not be geocoded."
        if last_error:
            reason = f"{reason} Last geocoder error: {last_error}"

        return {
            "is_valid": False,
            "query": query,
            "normalized_address": "",
            "lat": None,
            "lon": None,
            "reason": reason,
        }
    except Exception as e:
        logger.exception("Error validating depot address")
        raise HTTPException(
            status_code=500,
            detail=f"Error validating depot address: {str(e)}",
        )


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


@router.post("/data/dashboard")
async def data_dashboard(
    ventas: UploadFile = File(..., description="CSV de ventas"),
    detalle: UploadFile = File(..., description="CSV o XLSX de detalle"),
):
    """
    Data Hub dashboard payload for current orders.
    Fast profile: cleans/normalizes data without geocoding full dataset.
    """
    t0 = time.perf_counter()
    try:
        ventas_bytes = await ventas.read()
        ventas_text = ventas_bytes.decode("utf-8")

        detalle_bytes = await detalle.read()
        if detalle.filename and detalle.filename.lower().endswith(".xlsx"):
            df_det = await asyncio.to_thread(pd.read_excel, io.BytesIO(detalle_bytes))
            detalle_text = df_det.to_csv(index=False)
        else:
            detalle_text = detalle_bytes.decode("utf-8")

        cleaning = await asyncio.to_thread(run_full_cleaning, ventas_text, detalle_text, False)
        df_ventas = cleaning.df_ventas.copy()
        df_detalle = cleaning.df_detalle.copy()

        if df_ventas.empty:
            return {
                "kpis": {
                    "orders_total": 0,
                    "orders_deadline_today": 0,
                    "orders_overdue": 0,
                    "comunas_active": 0,
                    "monetary_total_clp": 0.0,
                    "avg_order_value_clp": 0.0,
                    "customers_total": 0,
                },
                "deadline_buckets": [],
                "deadlines_timeline": [],
                "comuna_distribution": [],
                "review_rows": [],
                "cleaning_errors": cleaning_errors,
                "meta": {"elapsed_sec": round(float(time.perf_counter() - t0), 4)},
            }

        # Normalize base columns.
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        next_week = today + timedelta(days=7)

        if "Monto Pedido" in df_ventas.columns:
            df_ventas["_monto_clp"] = df_ventas["Monto Pedido"].apply(_parse_clp_number)
        else:
            df_ventas["_monto_clp"] = 0.0

        if "Comuna" in df_ventas.columns:
            df_ventas["_comuna"] = (
                df_ventas["Comuna"].astype(str).fillna("").str.strip().replace({"nan": "", "None": ""})
            )
        else:
            df_ventas["_comuna"] = ""
        df_ventas["_comuna"] = df_ventas["_comuna"].apply(lambda x: x if x else "Sin comuna")

        if "Fecha de despacho Solicitada" in df_ventas.columns:
            dispatch_ts = pd.to_datetime(df_ventas["Fecha de despacho Solicitada"], errors="coerce")
            df_ventas["_dispatch_ts"] = dispatch_ts
            df_ventas["_dispatch_date"] = dispatch_ts.dt.date
        else:
            df_ventas["_dispatch_ts"] = pd.NaT
            df_ventas["_dispatch_date"] = None

        # Optional detail aggregation for richer review rows.
        if not df_detalle.empty and "Número de Orden" in df_detalle.columns:
            if "Cantidad" in df_detalle.columns:
                qty = pd.to_numeric(df_detalle["Cantidad"], errors="coerce").fillna(0.0)
                detail_qty = (
                    pd.DataFrame({"Número de Orden": df_detalle["Número de Orden"], "_qty": qty})
                    .groupby("Número de Orden", as_index=False)["_qty"]
                    .sum()
                    .rename(columns={"_qty": "items_total"})
                )
                df_ventas = df_ventas.merge(detail_qty, on="Número de Orden", how="left")
            if "SKU" in df_detalle.columns:
                skus = (
                    df_detalle.assign(_sku=df_detalle["SKU"].astype(str))
                    .groupby("Número de Orden", as_index=False)["_sku"]
                    .nunique()
                    .rename(columns={"_sku": "sku_count"})
                )
                df_ventas = df_ventas.merge(skus, on="Número de Orden", how="left")
        if "items_total" not in df_ventas.columns:
            df_ventas["items_total"] = 0.0
        if "sku_count" not in df_ventas.columns:
            df_ventas["sku_count"] = 0

        dispatch_ts = pd.to_datetime(df_ventas["_dispatch_ts"], errors="coerce")
        today_ts = pd.Timestamp(today)
        tomorrow_ts = pd.Timestamp(tomorrow)
        next_week_ts = pd.Timestamp(next_week)
        deadline_today = int((dispatch_ts.dt.date == today).sum())
        deadline_overdue = int((dispatch_ts < today_ts).sum())
        deadline_next_24h = int((dispatch_ts.dt.date == tomorrow).sum())
        deadline_week = int(((dispatch_ts >= today_ts) & (dispatch_ts <= next_week_ts)).sum())

        monetary_total = float(df_ventas["_monto_clp"].sum())
        orders_total = int(len(df_ventas))
        avg_order = float(monetary_total / orders_total) if orders_total > 0 else 0.0
        customers_total = int(df_ventas["RUT"].astype(str).nunique()) if "RUT" in df_ventas.columns else orders_total
        comunas_active = int(df_ventas["_comuna"].astype(str).nunique())

        comuna_group = (
            df_ventas.groupby("_comuna", as_index=False)
            .agg(pedidos=("Número de Orden", "count"), monto_clp=("_monto_clp", "sum"))
            .sort_values(["pedidos", "monto_clp"], ascending=[False, False])
        )
        comuna_distribution = [
            {
                "comuna": str(r["_comuna"]),
                "pedidos": int(r["pedidos"]),
                "monto_clp": round(float(r["monto_clp"]), 2),
            }
            for _, r in comuna_group.head(60).iterrows()
        ]

        deadlines_timeline = []
        if dispatch_ts is not None:
            timeline = (
                df_ventas.dropna(subset=["_dispatch_ts"])
                .groupby("_dispatch_date", as_index=False)
                .size()
                .sort_values("_dispatch_date")
            )
            for _, r in timeline.head(30).iterrows():
                deadlines_timeline.append(
                    {
                        "date": str(r["_dispatch_date"]),
                        "orders": int(r["size"]),
                    }
                )

        # Build review queue rows (focus on risk/problematic orders first).
        review_rows = []
        for _, row in df_ventas.iterrows():
            order_id = str(row.get("Número de Orden", "") or "")
            customer = str(row.get("Nombre cliente", "") or "")
            comuna = str(row.get("_comuna", "") or "")
            address = str(row.get("Dirección cliente", "") or "")
            dispatch_date = row.get("_dispatch_date")
            monto = _safe_float(row.get("_monto_clp"), 0.0)
            items_total = _safe_float(row.get("items_total"), 0.0)
            sku_count = _safe_int(row.get("sku_count"), 0)

            quality_status, action = _address_quality_status(address, comuna)
            if dispatch_date is None:
                deadline_status = "no_deadline"
                deadline_priority = 3
            elif dispatch_date < today:
                deadline_status = "overdue"
                deadline_priority = 0
            elif dispatch_date == today:
                deadline_status = "today"
                deadline_priority = 1
            elif dispatch_date == tomorrow:
                deadline_status = "next_24h"
                deadline_priority = 2
            else:
                deadline_status = "future"
                deadline_priority = 3

            quality_priority = {
                "missing_address": 0,
                "missing_comuna": 0,
                "low_confidence": 1,
                "pending_validation": 2,
            }.get(quality_status, 3)

            review_rows.append(
                {
                    "order_id": order_id,
                    "customer": customer,
                    "comuna": comuna,
                    "address": address,
                    "dispatch_date": str(dispatch_date) if dispatch_date else "",
                    "deadline_status": deadline_status,
                    "quality_status": quality_status,
                    "action_suggestion": action,
                    "monto_clp": round(float(monto), 2),
                    "items_total": round(float(items_total), 2),
                    "sku_count": int(sku_count),
                    "_sort_deadline": int(deadline_priority),
                    "_sort_quality": int(quality_priority),
                }
            )

        review_rows = sorted(
            review_rows,
            key=lambda r: (
                int(r.get("_sort_deadline", 9)),
                int(r.get("_sort_quality", 9)),
                str(r.get("dispatch_date", "")),
                str(r.get("order_id", "")),
            ),
        )
        for row in review_rows:
            row.pop("_sort_deadline", None)
            row.pop("_sort_quality", None)
        review_rows = review_rows[:200]

        return {
            "kpis": {
                "orders_total": int(orders_total),
                "orders_deadline_today": int(deadline_today),
                "orders_overdue": int(deadline_overdue),
                "orders_next_24h": int(deadline_next_24h),
                "orders_week_horizon": int(deadline_week),
                "comunas_active": int(comunas_active),
                "monetary_total_clp": round(float(monetary_total), 2),
                "avg_order_value_clp": round(float(avg_order), 2),
                "customers_total": int(customers_total),
            },
            "deadline_buckets": [
                {"bucket": "overdue", "orders": int(deadline_overdue)},
                {"bucket": "today", "orders": int(deadline_today)},
                {"bucket": "next_24h", "orders": int(deadline_next_24h)},
                {"bucket": "week_horizon", "orders": int(deadline_week)},
            ],
            "deadlines_timeline": deadlines_timeline,
            "comuna_distribution": comuna_distribution,
            "review_rows": review_rows,
            "cleaning_errors": cleaning_errors[:250] if isinstance(cleaning_errors, list) else [],
            "meta": {
                "elapsed_sec": round(float(time.perf_counter() - t0), 4),
                "ventas_rows": int(len(df_ventas)),
                "detalle_rows": int(len(df_detalle)),
            },
        }
    except Exception as e:
        logger.exception("Error building Data Hub dashboard")
        raise HTTPException(status_code=500, detail=f"Error construyendo dashboard: {str(e)}")


@router.post("/data/validate-addresses")
async def data_validate_addresses(
    ventas: UploadFile = File(..., description="CSV de ventas"),
    max_rows: int = Form(0),
    max_api_probes: int = Form(0),
):
    """
    Fase 2: validación masiva de direcciones y sugerencias de reparación asistida.
    """
    t0 = time.perf_counter()
    try:
        ventas_bytes = await ventas.read()
        ventas_text = _decode_uploaded_text(ventas_bytes)

        # Keep raw values to suggest repairs against original data.
        df_raw = await asyncio.to_thread(
            pd.read_csv,
            io.StringIO(ventas_text),
            dtype={"RUT": object, "Nombre cliente": object, "Número de Orden": object},
        )
        df_clean, clean_errors = await asyncio.to_thread(clean_ventas, ventas_text)

        if df_clean.empty:
            return {
                "rows": [],
                "summary": {
                    "rows_requested": int(max_rows),
                    "rows_processed": 0,
                    "validated": 0,
                    "repaired_validated": 0,
                    "repair_suggested": 0,
                    "unverified": 0,
                    "missing_data": 0,
                    "api_probes_used": 0,
                    "api_probe_budget": 0,
                    "cache_hits": 0,
                    "auto_repair_candidates": 0,
                },
                "errors": clean_errors,
                "meta": {"elapsed_sec": round(float(time.perf_counter() - t0), 4)},
            }

        requested_rows = int(max_rows)
        if requested_rows <= 0:
            rows_total = int(len(df_clean))
        else:
            rows_total = int(min(requested_rows, len(df_clean)))

        requested_probes = int(max_api_probes)
        if requested_probes <= 0:
            # Full-pass mode by default (bounded for safety).
            probe_budget = int(min(max(rows_total * 2, 200), 50000))
        else:
            probe_budget = int(min(max(requested_probes, 0), 50000))

        cache = await asyncio.to_thread(cargar_cache)
        cache_dirty = False
        api_probes_used = 0
        cache_hits = 0
        probe_cache: dict[str, tuple[Optional[float], Optional[float], str]] = {}
        probe_repeat_skips = 0
        unique_candidate_keys: set[str] = set()
        geocode_source_hits: dict[str, int] = {}
        geocode_fail_source_hits: dict[str, int] = {}

        out_rows = []
        status_counts = {
            "validated": 0,
            "repaired_validated": 0,
            "repair_suggested": 0,
            "unverified": 0,
            "missing_data": 0,
        }

        today = datetime.now().date()

        for pos in range(rows_total):
            row = df_clean.iloc[pos]
            row_raw = df_raw.iloc[pos] if pos < len(df_raw) else row

            order_id = str(row.get("Número de Orden", "") or "").strip()
            customer = str(row.get("Nombre cliente", "") or "").strip()

            raw_address = str(row_raw.get("Dirección cliente", "") or "").strip()
            raw_comuna = str(row_raw.get("Comuna", "") or "").strip()
            clean_address = str(row.get("Dirección cliente", "") or "").strip()
            clean_comuna = str(row.get("Comuna", "") or "").strip()
            if raw_comuna.lower() in {"nan", "none", "null"}:
                raw_comuna = ""
            if clean_comuna.lower() in {"nan", "none", "null"}:
                clean_comuna = ""

            suggested_address, detected_comuna = estandarizar_direccion_y_comuna(raw_address)
            suggested_address = str(suggested_address or "").strip() or clean_address
            suggested_comuna = str(detected_comuna or "").strip() or clean_comuna or raw_comuna
            if suggested_comuna.lower() in {"nan", "none", "null"}:
                suggested_comuna = ""

            repair_fields = []
            if suggested_address and suggested_address != clean_address:
                repair_fields.append("address")
            if suggested_comuna and suggested_comuna != clean_comuna:
                repair_fields.append("comuna")
            can_auto_repair = bool(repair_fields)

            base_quality, _base_action = _address_quality_status(clean_address, clean_comuna)

            lat = None
            lon = None
            validation_source = ""
            validation_status = "unverified"

            candidates: list[tuple[str, str, str]] = []
            if clean_address and clean_comuna:
                candidates.append((clean_address, clean_comuna, "clean"))
            if (
                suggested_address
                and suggested_comuna
                and (suggested_address != clean_address or suggested_comuna != clean_comuna)
            ):
                candidates.append((suggested_address, suggested_comuna, "suggested"))

            for cand_address, cand_comuna, cand_tag in candidates:
                key = f"{cand_address}|{cand_comuna}"
                unique_candidate_keys.add(key)
                hit_lat, hit_lon = _cache_coord_pair(cache, key)
                if hit_lat is not None and hit_lon is not None:
                    lat, lon = hit_lat, hit_lon
                    cache_hits += 1
                    validation_source = f"cache:{cand_tag}"
                    validation_status = "validated" if cand_tag == "clean" else "repaired_validated"
                    break

                cached_probe = probe_cache.get(key)
                if cached_probe is not None:
                    cp_lat, cp_lon, cp_source = cached_probe
                    if cp_lat is not None and cp_lon is not None:
                        lat, lon = cp_lat, cp_lon
                        validation_source = f"probe-cache:{cp_source}:{cand_tag}"
                        validation_status = "validated" if cand_tag == "clean" else "repaired_validated"
                        break
                    probe_repeat_skips += 1
                    continue

                if api_probes_used >= probe_budget:
                    continue

                api_probes_used += 1
                g_lat, g_lon, g_source = await asyncio.to_thread(
                    _geocode_with_fallback,
                    cand_address,
                    cand_comuna,
                    raw_address=raw_address,
                    raw_comuna=raw_comuna,
                    timeout_sec=3.5,
                )
                probe_cache[key] = (g_lat, g_lon, g_source)
                if g_lat is not None and g_lon is not None:
                    lat, lon = float(g_lat), float(g_lon)
                    cache[key] = [lat, lon]
                    cache_dirty = True
                    source_key = str(g_source or "api_unknown")
                    geocode_source_hits[source_key] = int(geocode_source_hits.get(source_key, 0)) + 1
                    validation_source = f"api:{source_key}:{cand_tag}"
                    validation_status = "validated" if cand_tag == "clean" else "repaired_validated"
                    break
                fail_key = str(g_source or "unknown")
                geocode_fail_source_hits[fail_key] = int(geocode_fail_source_hits.get(fail_key, 0)) + 1

            if base_quality in {"missing_address", "missing_comuna"}:
                if can_auto_repair:
                    quality_status = "repair_suggested"
                    action = "Aplicar reparación sugerida y volver a validar."
                else:
                    quality_status = "missing_data"
                    action = "Completar datos base (dirección/comuna)."
            elif validation_status == "validated":
                quality_status = "validated"
                action = "Dirección validada."
            elif validation_status == "repaired_validated":
                quality_status = "repaired_validated"
                action = "Aplicar reparación sugerida (validada)."
            elif can_auto_repair:
                quality_status = "repair_suggested"
                action = "Aplicar reparación sugerida y volver a validar."
            else:
                quality_status = "unverified"
                if base_quality == "low_confidence":
                    action = "Revisar formato y número de calle."
                else:
                    action = "Validar manualmente."

            dispatch_date = pd.to_datetime(row.get("Fecha de despacho Solicitada"), errors="coerce")
            if pd.isna(dispatch_date):
                deadline_status = "no_deadline"
            elif dispatch_date.date() < today:
                deadline_status = "overdue"
            elif dispatch_date.date() == today:
                deadline_status = "today"
            else:
                deadline_status = "future"

            status_counts[quality_status] = status_counts.get(quality_status, 0) + 1
            monto = _parse_clp_number(row.get("Monto Pedido"))
            out_rows.append(
                {
                    "row_index": int(pos),
                    "order_id": order_id,
                    "customer": customer,
                    "comuna": clean_comuna,
                    "address": clean_address,
                    "dispatch_date": "" if pd.isna(dispatch_date) else str(dispatch_date.date()),
                    "deadline_status": deadline_status,
                    "quality_status": quality_status,
                    "action_suggestion": action,
                    "monto_clp": round(float(monto), 2),
                    "suggested_address": suggested_address,
                    "suggested_comuna": suggested_comuna,
                    "can_auto_repair": bool(can_auto_repair),
                    "repair_fields": ",".join(repair_fields),
                    "validation_source": validation_source,
                    "lat": lat,
                    "lon": lon,
                    "raw_address": raw_address,
                    "raw_comuna": raw_comuna,
                }
            )

        if cache_dirty:
            await asyncio.to_thread(guardar_cache, cache)

        priority = {
            "missing_data": 0,
            "repair_suggested": 1,
            "unverified": 2,
            "repaired_validated": 3,
            "validated": 4,
        }
        out_rows = sorted(
            out_rows,
            key=lambda r: (
                int(priority.get(str(r.get("quality_status", "")), 9)),
                str(r.get("deadline_status", "")),
                str(r.get("order_id", "")),
            ),
        )

        return {
            "rows": out_rows,
            "summary": {
                "rows_requested": int(max_rows),
                "rows_processed": int(rows_total),
                "validated": int(status_counts.get("validated", 0)),
                "repaired_validated": int(status_counts.get("repaired_validated", 0)),
                "repair_suggested": int(status_counts.get("repair_suggested", 0)),
                "unverified": int(status_counts.get("unverified", 0)),
                "missing_data": int(status_counts.get("missing_data", 0)),
                "api_probes_used": int(api_probes_used),
                "api_probe_budget": int(probe_budget),
                "cache_hits": int(cache_hits),
                "auto_repair_candidates": int(sum(1 for r in out_rows if bool(r.get("can_auto_repair")))),
                "unique_candidate_keys": int(len(unique_candidate_keys)),
                "probe_cache_size": int(len(probe_cache)),
                "probe_repeat_skips": int(probe_repeat_skips),
                "geocode_source_hits": geocode_source_hits,
                "geocode_fail_source_hits": geocode_fail_source_hits,
                "nominatim_local_url": str(NOMINATIM_URL or ""),
                "nominatim_fallback_url": str(_PUBLIC_NOMINATIM_URL or ""),
            },
            "errors": clean_errors[:250] if isinstance(clean_errors, list) else [],
            "meta": {"elapsed_sec": round(float(time.perf_counter() - t0), 4)},
        }
    except Exception as e:
        logger.exception("Error validating addresses")
        raise HTTPException(status_code=500, detail=f"Error validando direcciones: {str(e)}")


# ── Day-batching helper ───────────────────────────────────────────────────

def _build_day_batches(
    df: pd.DataFrame,
    deliveries_per_day: int,
    date_col: str = "Fecha de despacho Solicitada",
) -> list[tuple[str, pd.DataFrame]]:
    """
    Sort df by date_col, group by calendar day, and build batches of at most
    deliveries_per_day rows using a carry-over queue.

    Returns a list of (label, sub_dataframe) pairs.
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


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _parse_clp_number(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return 0.0
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return 0.0

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, tail = text.rsplit(",", 1)
        if len(tail) <= 2:
            text = f"{head}.{tail}"
        else:
            text = f"{head}{tail}"
    elif "." in text:
        head, tail = text.rsplit(".", 1)
        if len(tail) > 2:
            text = f"{head}{tail}"

    try:
        return float(text)
    except Exception:
        return 0.0


def _address_quality_status(address: str, comuna: str) -> tuple[str, str]:
    addr = str(address or "").strip()
    com = str(comuna or "").strip()

    if not addr:
        return "missing_address", "Completar dirección"
    if not com:
        return "missing_comuna", "Completar comuna"
    if len(addr) < 8:
        return "low_confidence", "Revisar formato de dirección"
    if not any(ch.isdigit() for ch in addr):
        return "low_confidence", "Agregar número de calle"
    return "pending_validation", "Validar geocodificación"


def _decode_uploaded_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def _cache_coord_pair(cache: dict, key: str) -> tuple[Optional[float], Optional[float]]:
    val = cache.get(key)
    if not isinstance(val, (list, tuple)) or len(val) < 2:
        return None, None
    try:
        lat = float(val[0]) if val[0] is not None else None
        lon = float(val[1]) if val[1] is not None else None
    except Exception:
        return None, None
    if lat is None or lon is None:
        return None, None
    return lat, lon


def _build_global_stats(
    day_results: list[dict],
    cleaning_errors: list,
    *,
    pipeline_phase_times: dict,
    total_elapsed_sec: float,
) -> dict:
    stats_list = [d.get("stats", {}) for d in day_results if isinstance(d, dict)]
    stats_list = [s for s in stats_list if isinstance(s, dict)]
    ok_stats = [s for s in stats_list if not s.get("error")]
    error_days = sum(1 for s in stats_list if s.get("error"))

    total_orders = sum(_safe_int(s.get("total_puntos", 0)) for s in ok_stats)
    total_covered = sum(_safe_int(s.get("cubiertos", 0)) for s in ok_stats)
    total_uncovered = sum(_safe_int(s.get("no_cubiertos", 0)) for s in ok_stats)
    trucks_peak = max((_safe_int(s.get("camiones_usados", 0)) for s in ok_stats), default=0)

    cost_values = []
    for s in ok_stats:
        if s.get("costo_base") is None:
            continue
        cost_values.append(_safe_float(s.get("costo_base"), 0.0))
    total_cost = float(sum(cost_values))
    avg_cost = float(total_cost / len(cost_values)) if cost_values else 0.0

    geocode_failed = 0
    if isinstance(cleaning_errors, list):
        for err in cleaning_errors:
            if not isinstance(err, dict):
                continue
            if str(err.get("origen", "")).strip().lower() == "geocodificación":
                geocode_failed += 1

    opt_phase_keys = [
        "wall_sec",
        "matrix_generation_sec",
        "model_preparation_sec",
        "solver_sec",
        "postprocess_sec",
        "output_generation_sec",
        "total_sec",
    ]
    optimization_phase_agg = {}
    for key in opt_phase_keys:
        optimization_phase_agg[key] = round(
            float(
                sum(
                    _safe_float(
                        (s.get("timing", {}) if isinstance(s.get("timing"), dict) else {}).get(key, 0.0),
                        0.0,
                    )
                    for s in ok_stats
                )
            ),
            4,
        )

    coverage_rate_pct = 0.0
    if total_orders > 0:
        coverage_rate_pct = round((100.0 * float(total_covered) / float(total_orders)), 2)

    return {
        "days_processed": len(day_results),
        "days_ok": len(ok_stats),
        "days_error": int(error_days),
        "orders_total": int(total_orders),
        "covered_total": int(total_covered),
        "uncovered_total": int(total_uncovered),
        "coverage_rate_pct": float(coverage_rate_pct),
        "trucks_peak": int(trucks_peak),
        "cost_total": round(float(total_cost), 4),
        "cost_avg_per_day": round(float(avg_cost), 4),
        "geocode_failed_addresses": int(geocode_failed),
        "cleaning_errors_total": len(cleaning_errors) if isinstance(cleaning_errors, list) else 0,
        "execution_time_sec": {
            "total": round(float(total_elapsed_sec), 4),
            "pipeline_phase": {
                "cleaning_geocoding_sec": round(_safe_float(pipeline_phase_times.get("cleaning_geocoding_sec"), 0.0), 4),
                "detalle_merge_sec": round(_safe_float(pipeline_phase_times.get("detalle_merge_sec"), 0.0), 4),
                "batch_build_sec": round(_safe_float(pipeline_phase_times.get("batch_build_sec"), 0.0), 4),
                "optimization_days_sec": round(_safe_float(pipeline_phase_times.get("optimization_days_sec"), 0.0), 4),
                "summary_sec": round(_safe_float(pipeline_phase_times.get("summary_sec"), 0.0), 4),
            },
            "optimization_phase_aggregated": optimization_phase_agg,
        },
    }


from backend.services.job_store import create_job, get_job, update_job_stage, mark_job_running, mark_job_done, mark_job_error
import uuid
import contextvars

# Context marker for job ID so _set_stage knows which job to update
_current_job_id = contextvars.ContextVar("current_job_id", default=None)

def _set_stage(msg: str):
    job_id = _current_job_id.get()
    if job_id:
        update_job_stage(job_id, msg)
    else:
        global _stage
        _stage = msg
    logger.info(f"[stage] {msg}")


@router.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": job.status, "stage": job.stage}


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "error":
        raise HTTPException(status_code=400, detail=job.error)
    if job.status != "done":
        raise HTTPException(status_code=400, detail="Job not finished yet")
    return job.result


async def _run_optimization_job(
    job_id: str,
    params_dict: dict,
    ventas_text: Optional[str],
    detalle_text: Optional[str],
    detalle_filename: Optional[str],
):
    _current_job_id.set(job_id)
    mark_job_running(job_id)
    try:
        t_job_0 = time.perf_counter()
        pipeline_phase_times = {}
        validated_params = OptimizerParams(**params_dict)
        cleaning_errors: list = []

        # ── Limpiar + geocodificar (CSV) o cargar pendientes desde DB ──
        t_clean_0 = time.perf_counter()
        if ventas_text is not None and detalle_text is not None:
            _set_stage("Limpiando datos...")
            cleaning = await asyncio.to_thread(
                run_full_cleaning, ventas_text, detalle_text, True
            )
            df_ventas = cleaning.df_ventas
            df_detalle = cleaning.df_detalle
            cleaning_errors = cleaning_errors
        else:
            _set_stage("Buscando pedidos pendientes en la base de datos...")
            from backend.services.db_persistence import get_pending_orders_df
            df_ventas = await asyncio.to_thread(
                get_pending_orders_df, validated_params.user_id
            )
            df_detalle = pd.DataFrame()
            if df_ventas.empty:
                _set_stage("")
                mark_job_done(
                    job_id,
                    {
                        "days": [],
                        "cleaning_errors": [
                            {"origen": "DB", "error": "No hay pedidos Pendiente en la base de datos."}
                        ],
                        "global_stats": {},
                    },
                )
                return
        pipeline_phase_times["cleaning_geocoding_sec"] = time.perf_counter() - t_clean_0

        # ── Join weight/volume totals from detalle into ventas ──────────────
        # Aggregate detalle to one row per order with total kg and m³
        t_merge_0 = time.perf_counter()
        if not df_detalle.empty and "Número de Orden" in df_detalle.columns:
            df_detalle = df_detalle.copy()
            agg_cols = {}
            if "Peso_total_kg" in df_detalle.columns:
                agg_cols["Peso_total_kg"] = "sum"
            elif "Peso_unitario_kg" in df_detalle.columns and "Cantidad" in df_detalle.columns:
                df_detalle["Peso_total_kg"] = df_detalle["Peso_unitario_kg"] * df_detalle["Cantidad"]
                agg_cols["Peso_total_kg"] = "sum"

            if "Volumen_total_m3" in df_detalle.columns:
                agg_cols["Volumen_total_m3"] = "sum"
            elif "Volumen_unitario_m3" in df_detalle.columns and "Cantidad" in df_detalle.columns:
                df_detalle["Volumen_total_m3"] = df_detalle["Volumen_unitario_m3"] * df_detalle["Cantidad"]
                agg_cols["Volumen_total_m3"] = "sum"

            merges: list[pd.DataFrame] = []

            if agg_cols:
                order_totals = df_detalle.groupby("Número de Orden").agg(agg_cols).reset_index()
                order_totals = order_totals.rename(columns={
                    "Peso_total_kg": "Peso_total_pedido",
                    "Volumen_total_m3": "Volumen_total_pedido",
                })
                merges.append(order_totals)

            # Resumen de detalle por orden para mostrar en UX de mapa.
            if "Cantidad" in df_detalle.columns:
                df_detalle["_cantidad_num"] = pd.to_numeric(df_detalle["Cantidad"], errors="coerce").fillna(0.0)
                items_total = (
                    df_detalle.groupby("Número de Orden", as_index=False)["_cantidad_num"]
                    .sum()
                    .rename(columns={"_cantidad_num": "Items_total"})
                )
                merges.append(items_total)

            if "SKU" in df_detalle.columns:
                sku_df = df_detalle.copy()
                sku_df["_sku"] = sku_df["SKU"].astype(str).str.strip()
                sku_df = sku_df[sku_df["_sku"].ne("") & sku_df["_sku"].ne("nan")]
                if not sku_df.empty:
                    sku_count = (
                        sku_df.groupby("Número de Orden", as_index=False)["_sku"]
                        .nunique()
                        .rename(columns={"_sku": "SKU_count"})
                    )
                    merges.append(sku_count)

                    sku_preview = (
                        sku_df.groupby("Número de Orden")["_sku"]
                        .agg(lambda s: ", ".join(list(dict.fromkeys([str(v) for v in s.tolist()]))[:5]))
                        .reset_index(name="SKU_preview")
                    )
                    merges.append(sku_preview)

            if merges:
                merged_detail = merges[0]
                for extra_df in merges[1:]:
                    merged_detail = merged_detail.merge(extra_df, on="Número de Orden", how="outer")
                df_ventas = df_ventas.merge(merged_detail, on="Número de Orden", how="left")
        pipeline_phase_times["detalle_merge_sec"] = time.perf_counter() - t_merge_0

        # Logging pre-optimización
        n_total = len(df_ventas)
        n_ok = df_ventas["Latitud"].notna().sum() if "Latitud" in df_ventas.columns else 0
        logger.info(f"Pre-optimización — total: {n_total}, con coords: {n_ok}, sin coords: {n_total - n_ok}")

        # ── Construir batches por día ──
        t_batch_0 = time.perf_counter()
        _set_stage("Construyendo batches por día...")
        batches = await asyncio.to_thread(
            _build_day_batches, df_ventas, validated_params.deliveries_per_day
        )
        pipeline_phase_times["batch_build_sec"] = time.perf_counter() - t_batch_0
        logger.info(f"Batches generados: {[f'{label}({len(df)})' for label, df in batches]}")

        # ── Optimizar por día ──
        t_opt_days_0 = time.perf_counter()
        day_results = []
        for idx, (label, batch_df) in enumerate(batches):
            if batch_df.empty:
                continue
            t_day_0 = time.perf_counter()
            _set_stage(f"Optimizando día {idx + 1}/{len(batches)}: {label} ({len(batch_df)} pedidos)...")
            logger.info(f"Optimizando día '{label}' con {len(batch_df)} pedidos...")
            try:
                result = await asyncio.to_thread(run_optimization, validated_params, batch_df)
                day_elapsed = time.perf_counter() - t_day_0
                day_stats = result.stats if isinstance(result.stats, dict) else {}
                day_timing = day_stats.get("timing", {}) if isinstance(day_stats.get("timing", {}), dict) else {}
                day_timing["wall_sec"] = round(float(day_elapsed), 4)
                day_stats["timing"] = day_timing
                day_results.append({
                    "date": label,
                    "routes_csv": result.routes_csv,
                    "uncovered_csv": result.uncovered_csv,
                    "map_html": result.map_html,
                    "stats": day_stats,
                })
            except Exception as day_err:
                day_elapsed = time.perf_counter() - t_day_0
                logger.error(f"Error optimizando día '{label}': {day_err}")
                day_results.append({
                    "date": label,
                    "routes_csv": "",
                    "uncovered_csv": "",
                    "map_html": f"<p>Error al optimizar: {day_err}</p>",
                    "stats": {
                        "error": str(day_err),
                        "total_puntos": len(batch_df),
                        "timing": {"wall_sec": round(float(day_elapsed), 4)},
                    },
                })
        pipeline_phase_times["optimization_days_sec"] = time.perf_counter() - t_opt_days_0

        t_summary_0 = time.perf_counter()
        total_elapsed_sec = time.perf_counter() - t_job_0
        global_stats = _build_global_stats(
            day_results=day_results,
            cleaning_errors=cleaning_errors,
            pipeline_phase_times=pipeline_phase_times,
            total_elapsed_sec=total_elapsed_sec,
        )
        pipeline_phase_times["summary_sec"] = time.perf_counter() - t_summary_0
        global_stats.setdefault("execution_time_sec", {})
        if isinstance(global_stats["execution_time_sec"], dict):
            global_stats["execution_time_sec"]["pipeline_phase"] = {
                "cleaning_geocoding_sec": round(_safe_float(pipeline_phase_times.get("cleaning_geocoding_sec"), 0.0), 4),
                "detalle_merge_sec": round(_safe_float(pipeline_phase_times.get("detalle_merge_sec"), 0.0), 4),
                "batch_build_sec": round(_safe_float(pipeline_phase_times.get("batch_build_sec"), 0.0), 4),
                "optimization_days_sec": round(_safe_float(pipeline_phase_times.get("optimization_days_sec"), 0.0), 4),
                "summary_sec": round(_safe_float(pipeline_phase_times.get("summary_sec"), 0.0), 4),
            }
            global_stats["execution_time_sec"]["total"] = round(float(time.perf_counter() - t_job_0), 4)

        _set_stage("")
        final_result = {
            "days": day_results,
            "cleaning_errors": cleaning_errors,
            "global_stats": global_stats,
        }
        mark_job_done(job_id, final_result)

    except Exception as e:
        _set_stage("")
        error_msg = str(e)
        logger.exception("Error en optimización")
        mark_job_error(job_id, error_msg)


# ── Optimization endpoint ─────────────────────────────────────────────────

@router.post("/optimize")
async def optimize_route(
    params: str = Form(..., description="JSON string containing OptimizerParams"),
    ventas: Optional[UploadFile] = File(None, description="CSV de ventas (opcional, fallback a DB)"),
    detalle: Optional[UploadFile] = File(None, description="CSV o XLSX de detalle (opcional, fallback a DB)"),
):
    """
    Encola la optimización y retorna un job_id inmediatamente.

    Si no se entregan archivos, el job carga los pedidos Pendiente desde la DB
    (filtrados por user_id si viene en los params).
    """
    try:
        params_dict = json.loads(params)
        # Validate early so we error out synchronously if bad
        OptimizerParams(**params_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Parámetros JSON inválidos: {str(e)}")

    try:
        ventas_text: Optional[str] = None
        detalle_text: Optional[str] = None
        detalle_filename: Optional[str] = None

        if ventas is not None and detalle is not None:
            ventas_bytes = await ventas.read()
            ventas_text = ventas_bytes.decode("utf-8")

            detalle_bytes = await detalle.read()
            if detalle.filename and detalle.filename.endswith(".xlsx"):
                df_det = await asyncio.to_thread(pd.read_excel, io.BytesIO(detalle_bytes))
                detalle_text = df_det.to_csv(index=False)
            else:
                detalle_text = detalle_bytes.decode("utf-8")

            detalle_filename = detalle.filename or "detalle.csv"

        job_id = str(uuid.uuid4())
        create_job(job_id)

        # Start background task
        asyncio.create_task(_run_optimization_job(job_id, params_dict, ventas_text, detalle_text, detalle_filename))

        return {"job_id": job_id}

    except Exception as e:
        logger.exception("Error en lectura inicial para optimización")
        raise HTTPException(status_code=500, detail=f"Error leyendo archivos: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════
#  SaaS endpoints (auth, catalog, upload, simulation)
# ══════════════════════════════════════════════════════════════════════════

from fastapi import BackgroundTasks  # noqa: E402
from backend.services import auth_service  # noqa: E402
from backend.services.db_persistence import (  # noqa: E402
    persist_ventas_df,
    persist_detalle_df,
)
from database.connection import get_session  # noqa: E402
from database.repositories.venta_repository import VentaRepository  # noqa: E402
from database.repositories.detalle_repository import DetalleRepository  # noqa: E402
from database.repositories.producto_repository import ProductoRepository  # noqa: E402


# ── Auth ──────────────────────────────────────────────────────────────────

@router.post("/auth/register")
async def register(payload: dict):
    """Registra un usuario. Espera: {username, email, password}"""
    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""

    result = auth_service.register_user(username, email, password)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/auth/login")
async def login(payload: dict):
    """Login por username o email. Espera: {identifier, password}"""
    identifier = (payload.get("identifier") or "").strip()
    password = payload.get("password") or ""

    result = auth_service.login_user(identifier, password)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


@router.post("/auth/reset-password")
async def reset_password(payload: dict):
    """Reset de password. Espera: {username, email, new_password}"""
    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip()
    new_password = payload.get("new_password") or ""

    result = auth_service.reset_password(username, email, new_password)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Catalog ───────────────────────────────────────────────────────────────

@router.get("/catalog")
async def get_catalog():
    """Lista productos del catálogo."""
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
                "tipo_embalaje": p.tipo_embalaje,
            }
            for p in products
        ]
    finally:
        session.close()


# ── Upload (clean + persist a la DB) ──────────────────────────────────────

@router.post("/upload")
async def upload_ventas(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
):
    """
    Recibe un CSV de VENTAS, lo limpia/geocodifica con el pipeline avanzado
    y lo persiste en la tabla `ventas`.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un CSV.")

    try:
        content = await file.read()
        text = content.decode("utf-8")
        df_ventas, errores = await asyncio.to_thread(clean_ventas, text)

        # Geocodificar in-thread
        from backend.services.cleaning_service import geocodificar_dataframe
        df_ventas = await asyncio.to_thread(geocodificar_dataframe, df_ventas)

        result = await asyncio.to_thread(persist_ventas_df, df_ventas, user_id)
        return {
            "message": (
                f"Ventas procesadas: {result['success_count']} exitosas, "
                f"{result['error_count']} con errores."
            ),
            "success_count": result["success_count"],
            "error_count": result["error_count"],
            "cleaning_errors": errores,
            "persistence_errors": result["errors"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en subida de ventas")
        raise HTTPException(status_code=500, detail=f"Fallo en la subida: {str(e)}")


@router.post("/upload-detalle")
async def upload_detalle(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
):
    """
    Recibe un CSV/XLSX de DETALLE, lo limpia y lo persiste en la tabla `detalle`.
    """
    try:
        content = await file.read()
        if file.filename and file.filename.lower().endswith(".xlsx"):
            df_in = await asyncio.to_thread(pd.read_excel, io.BytesIO(content))
            text = df_in.to_csv(index=False)
        else:
            text = content.decode("utf-8")

        df_det, errs = await asyncio.to_thread(clean_detalle, text)
        if errs:
            return {"message": "Errores en formato de detalle", "errors": errs}

        result = await asyncio.to_thread(persist_detalle_df, df_det, user_id)
        return {
            "message": f"Detalle persistido: {result['success_count']} ítems.",
            "count": result["success_count"],
            "errors": result["errors"],
        }
    except Exception as e:
        logger.exception("Error en subida de detalle")
        raise HTTPException(status_code=500, detail=f"Fallo en la subida: {str(e)}")


@router.post("/upload-catalogo")
async def upload_catalogo(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
):
    """
    Recibe un CSV de CATÁLOGO, rechaza los SKUs que ya existen para el usuario
    e inserta los nuevos. Requiere user_id.
    """
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id es requerido.")
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un CSV.")

    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(text))

        col_map = {
            "SKU": "SKU",
            "Descripción SKU": "Descripción SKU",
            "Largo_cm": "Largo_cm",
            "Ancho_cm": "Ancho_cm",
            "Alto_cm": "Alto_cm",
            "Volumen_unitario_m3": "Volumen_unitario_m3",
            "Peso_unitario_kg": "Peso_unitario_kg",
            "Tipo_embalaje": "Tipo_embalaje",
        }
        missing = [c for c in col_map if c not in df.columns]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Columnas faltantes en el CSV: {missing}",
            )

        rows = df.to_dict(orient="records")

        from database.connection import get_session
        from database.repositories.producto_repository import ProductoRepository

        def _persist():
            session = get_session()
            try:
                repo = ProductoRepository(session)
                return repo.upsert_for_user(rows, user_id)
            finally:
                session.close()

        result = await asyncio.to_thread(_persist)
        return {
            "message": (
                f"Catálogo actualizado: {result['inserted']} insertados, "
                f"{result['skipped']} omitidos (ya existían)."
            ),
            "inserted": result["inserted"],
            "skipped": result["skipped"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en subida de catálogo")
        raise HTTPException(status_code=500, detail=f"Fallo en la subida: {str(e)}")


# ── Order numbering & simulation ──────────────────────────────────────────

@router.get("/next-order-number")
async def get_next_order_number():
    """Siguiente número de orden secuencial."""
    session = get_session()
    try:
        repo = VentaRepository(session)
        return {"next": repo.get_next_order_number()}
    finally:
        session.close()


def _process_simulated_order_sync(venta_data: dict):
    """Limpia + geocodifica una sola venta y la persiste."""
    try:
        from backend.services.cleaning_service import (
            estandarizar_direccion_y_comuna,
            geocodificar_direccion,
        )

        raw_addr = venta_data.get("direccion_cliente") or venta_data.get("Dirección cliente", "")
        raw_comuna = venta_data.get("comuna") or venta_data.get("Comuna", "")
        street, comuna = estandarizar_direccion_y_comuna(str(raw_addr))
        final_comuna = comuna or raw_comuna or "Santiago"
        lat, lon = geocodificar_direccion(street, final_comuna)

        payload = {
            "numero_orden": str(
                venta_data.get("numero_orden") or venta_data.get("Número de Orden", "")
            ),
            "rut": venta_data.get("rut") or venta_data.get("RUT"),
            "nombre_cliente": venta_data.get("nombre_cliente")
            or venta_data.get("Nombre cliente"),
            "direccion_cliente": f"{street}, {final_comuna}, Chile",
            "comuna": final_comuna,
            "fecha_pedido": pd.to_datetime(
                venta_data.get("fecha_pedido")
                or venta_data.get("Fecha de Pedido"),
                errors="coerce",
            ).date()
            if (venta_data.get("fecha_pedido") or venta_data.get("Fecha de Pedido"))
            else None,
            "estado": venta_data.get("estado") or venta_data.get("Estado", "Pendiente"),
            "monto_pedido": int(
                venta_data.get("monto_pedido")
                or venta_data.get("Monto Pedido", 0)
                or 0
            ),
            "fecha_despacho_solicitada": pd.to_datetime(
                venta_data.get("fecha_despacho_solicitada")
                or venta_data.get("Fecha de despacho Solicitada"),
                errors="coerce",
            ).date()
            if (
                venta_data.get("fecha_despacho_solicitada")
                or venta_data.get("Fecha de despacho Solicitada")
            )
            else None,
            "latitud": lat,
            "longitud": lon,
        }

        session = get_session()
        try:
            repo = VentaRepository(session)
            repo.upsert(payload)
        finally:
            session.close()
    except Exception as exc:
        logger.exception(f"Error procesando pedido simulado: {exc}")


@router.post("/simulation/order")
async def create_simulated_order(payload: dict, background_tasks: BackgroundTasks):
    """
    Recibe una orden completa (header + items), inserta los detalles y agenda
    la limpieza/geocodificación + persistencia del header en background.
    """
    venta_data = payload.get("venta")
    items_data = payload.get("items")

    if not venta_data or not items_data:
        raise HTTPException(status_code=400, detail="Missing order header or items.")

    session = get_session()
    try:
        d_repo = DetalleRepository(session)
        for item in items_data:
            item["numero_orden"] = venta_data["numero_orden"]
        d_repo.add_items(items_data)

        background_tasks.add_task(_process_simulated_order_sync, venta_data)

        return {"message": "Simulated order received. Cleaning & persist in progress."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create simulated order: {str(e)}")
    finally:
        session.close()
