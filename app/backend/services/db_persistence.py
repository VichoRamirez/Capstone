"""
Servicio de persistencia DB para el SaaS.

Puente entre los DataFrames limpiados (codex pipeline) y los repositorios
SQLAlchemy del SaaS. Mantiene cleaning_service.py libre de dependencias DB.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from database.connection import get_session
from database.repositories.venta_repository import VentaRepository
from database.repositories.detalle_repository import DetalleRepository

logger = logging.getLogger(__name__)


def _to_date(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        ts = pd.to_datetime(val, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def _to_float(val):
    try:
        if val is None or pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def _to_int(val, default: int = 0) -> int:
    try:
        if val is None or pd.isna(val):
            return default
        return int(val)
    except Exception:
        return default


def persist_ventas_df(df: pd.DataFrame, user_id: int) -> dict:
    """
    Upsert cada fila del DataFrame de ventas en la tabla `ventas`.
    user_id es obligatorio para la PK compuesta (Número de Orden, id_usuario).
    """
    if df is None or df.empty:
        return {"success_count": 0, "error_count": 0, "errors": []}

    if user_id is None:
        return {"success_count": 0, "error_count": 1, "errors": [{"error": "user_id requerido"}]}

    success = 0
    errors: list[dict] = []

    session = get_session()
    try:
        repo = VentaRepository(session)
        for idx, row in df.iterrows():
            try:
                numero_orden = str(row.get("Número de Orden", "")).strip()
                if not numero_orden:
                    raise ValueError("Número de Orden vacío")
                payload = {
                    "numero_orden": numero_orden,
                    "rut": row.get("RUT"),
                    "nombre_cliente": row.get("Nombre cliente"),
                    "direccion_cliente": row.get("Dirección cliente"),
                    "comuna": row.get("Comuna"),
                    "fecha_pedido": _to_date(row.get("Fecha de Pedido")),
                    "estado": row.get("Estado") or "Pendiente",
                    "monto_pedido": _to_int(row.get("Monto Pedido"), 0),
                    "fecha_despacho_solicitada": _to_date(row.get("Fecha de despacho Solicitada")),
                    "latitud": _to_float(row.get("Latitud")),
                    "longitud": _to_float(row.get("Longitud")),
                }
                repo.upsert(payload, user_id)
                success += 1
            except Exception as e:
                errors.append({
                    "fila": int(idx),
                    "numero_orden": str(row.get("Número de Orden", "")),
                    "error": str(e),
                })
    finally:
        session.close()

    return {"success_count": success, "error_count": len(errors), "errors": errors}


def persist_detalle_df(df: pd.DataFrame, user_id: int) -> dict:
    """
    Upsert cada fila del DataFrame de detalle en la tabla `detalle`.
    user_id es obligatorio para la PK compuesta (Número de Orden, SKU, id_usuario).
    """
    if df is None or df.empty:
        return {"success_count": 0, "error_count": 0, "errors": []}

    if user_id is None:
        return {"success_count": 0, "error_count": 1, "errors": [{"error": "user_id requerido"}]}

    items: list[dict] = []
    errors: list[dict] = []

    for idx, row in df.iterrows():
        try:
            items.append({
                "numero_orden": str(row.get("Número de Orden", "")).strip(),
                "sku": str(row.get("SKU", "")).strip(),
                "descripcion_sku": row.get("Descripción SKU"),
                "cantidad": _to_int(row.get("Cantidad"), 0),
                "largo_cm": _to_float(row.get("Largo_cm")),
                "ancho_cm": _to_float(row.get("Ancho_cm")),
                "alto_cm": _to_float(row.get("Alto_cm")),
                "volumen_unitario_m3": _to_float(row.get("Volumen_unitario_m3")),
                "peso_unitario_kg": _to_float(row.get("Peso_unitario_kg")),
                "volumen_total_m3": _to_float(row.get("Volumen_total_m3")),
                "peso_total_kg": _to_float(row.get("Peso_total_kg")),
            })
        except Exception as e:
            errors.append({"fila": int(idx), "error": str(e)})

    if not items:
        return {"success_count": 0, "error_count": len(errors), "errors": errors}

    session = get_session()
    try:
        repo = DetalleRepository(session)
        repo.add_items(items, user_id)
    except Exception as e:
        logger.exception("add_items batch failed")
        errors.append({"fila": -1, "error": str(e)})
        return {"success_count": 0, "error_count": len(errors), "errors": errors}
    finally:
        session.close()

    return {"success_count": len(items), "error_count": len(errors), "errors": errors}


def get_dashboard_dfs(user_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Devuelve (df_ventas, df_detalle) desde MySQL para el dashboard de Operations.
    Los nombres de columnas coinciden con los que espera el cálculo de KPIs.
    """
    from database.models import Venta, Detalle

    session = get_session()
    try:
        ventas_rows = session.query(Venta).filter(Venta.id_usuario == user_id).all()
        detalle_rows = session.query(Detalle).filter(Detalle.id_usuario == user_id).all()
    finally:
        session.close()

    ventas_data = [
        {
            "Número de Orden": v.numero_orden,
            "RUT": v.rut,
            "Nombre cliente": v.nombre_cliente,
            "Dirección cliente": v.direccion_cliente,
            "Comuna": v.comuna,
            "Fecha de Pedido": v.fecha_pedido,
            "Estado": v.estado,
            "Monto Pedido": v.monto_pedido,
            "Fecha de despacho Solicitada": v.fecha_despacho_solicitada,
            "Latitud": v.latitud,
            "Longitud": v.longitud,
        }
        for v in ventas_rows
    ]
    detalle_data = [
        {
            "Número de Orden": d.numero_orden,
            "SKU": d.sku,
            "Cantidad": d.cantidad,
        }
        for d in detalle_rows
    ]

    df_ventas = pd.DataFrame(ventas_data) if ventas_data else pd.DataFrame()
    df_detalle = pd.DataFrame(detalle_data) if detalle_data else pd.DataFrame()
    return df_ventas, df_detalle


def get_pending_orders_df(user_id: Optional[int] = None) -> pd.DataFrame:
    """
    Fetch all Pendiente orders joined with weight/volume totals from `detalle`.
    Returns a DataFrame with the same column conventions the optimizer expects.
    """
    from database.models import Venta, Detalle
    from sqlalchemy import func

    session = get_session()
    try:
        stats = (
            session.query(
                Detalle.numero_orden,
                Detalle.id_usuario,
                func.sum(Detalle.peso_total_kg).label("Peso_total_pedido"),
                func.sum(Detalle.volumen_total_m3).label("Volumen_total_pedido"),
            )
            .group_by(Detalle.numero_orden, Detalle.id_usuario)
            .subquery()
        )

        q = (
            session.query(
                Venta,
                stats.c.Peso_total_pedido,
                stats.c.Volumen_total_pedido,
            )
            .outerjoin(
                stats,
                (Venta.numero_orden == stats.c.numero_orden) &
                (Venta.id_usuario == stats.c.id_usuario),
            )
            .filter(Venta.estado == "Pendiente")
        )
        if user_id is not None:
            q = q.filter(Venta.id_usuario == user_id)

        rows = q.all()
        data = []
        for v, peso, vol in rows:
            data.append({
                "Número de Orden": v.numero_orden,
                "RUT": v.rut,
                "Nombre cliente": v.nombre_cliente,
                "Dirección cliente": v.direccion_cliente,
                "Comuna": v.comuna,
                "Fecha de Pedido": v.fecha_pedido,
                "Estado": v.estado,
                "Monto Pedido": v.monto_pedido,
                "Fecha de despacho Solicitada": v.fecha_despacho_solicitada,
                "Latitud": v.latitud,
                "Longitud": v.longitud,
                "Peso_total_pedido": float(peso) if peso is not None else 0.0,
                "Volumen_total_pedido": float(vol) if vol is not None else 0.0,
            })
        return pd.DataFrame(data)
    finally:
        session.close()
