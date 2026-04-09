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


def persist_ventas_df(df: pd.DataFrame, user_id: Optional[int] = None) -> dict:
    """
    Upsert each row of a cleaned ventas DataFrame into the `ventas` table.

    Expects the standard codex column names (`Número de Orden`, `RUT`,
    `Nombre cliente`, `Dirección cliente`, `Comuna`, `Fecha de Pedido`,
    `Estado`, `Monto Pedido`, `Fecha de despacho Solicitada`, `Latitud`,
    `Longitud`).
    """
    if df is None or df.empty:
        return {"success_count": 0, "error_count": 0, "errors": []}

    success = 0
    errors: list[dict] = []

    session = get_session()
    try:
        repo = VentaRepository(session)
        for idx, row in df.iterrows():
            try:
                payload = {
                    "numero_orden": str(row.get("Número de Orden", "")).strip(),
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
                if not payload["numero_orden"]:
                    raise ValueError("Número de Orden vacío")
                obj = repo.upsert(payload)
                if user_id is not None and hasattr(obj, "id_usuario"):
                    obj.id_usuario = user_id
                    session.commit()
                success += 1
            except Exception as e:
                errors.append(
                    {
                        "fila": int(idx),
                        "numero_orden": str(row.get("Número de Orden", "")),
                        "error": str(e),
                    }
                )
    finally:
        session.close()

    return {"success_count": success, "error_count": len(errors), "errors": errors}


def persist_detalle_df(df: pd.DataFrame, user_id: Optional[int] = None) -> dict:
    """
    Insert each row of a cleaned detalle DataFrame into the `detalle` table.
    """
    if df is None or df.empty:
        return {"success_count": 0, "error_count": 0, "errors": []}

    items: list[dict] = []
    errors: list[dict] = []
    for idx, row in df.iterrows():
        try:
            items.append(
                {
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
                }
            )
        except Exception as e:
            errors.append({"fila": int(idx), "error": str(e)})

    if not items:
        return {"success_count": 0, "error_count": len(errors), "errors": errors}

    session = get_session()
    try:
        repo = DetalleRepository(session)
        try:
            repo.add_items(items)
        except Exception as e:
            logger.exception("add_items batch failed")
            errors.append({"fila": -1, "error": str(e)})
            return {"success_count": 0, "error_count": len(errors), "errors": errors}

        if user_id is not None:
            try:
                from database.models import Detalle

                ordenes = list({it["numero_orden"] for it in items})
                if ordenes:
                    session.query(Detalle).filter(
                        Detalle.numero_orden.in_(ordenes)
                    ).update({Detalle.id_usuario: user_id}, synchronize_session=False)
                    session.commit()
            except Exception:
                logger.exception("No se pudo asignar id_usuario al detalle")
    finally:
        session.close()

    return {"success_count": len(items), "error_count": len(errors), "errors": errors}


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
                func.sum(Detalle.peso_total_kg).label("Peso_total_pedido"),
                func.sum(Detalle.volumen_total_m3).label("Volumen_total_pedido"),
            )
            .group_by(Detalle.numero_orden)
            .subquery()
        )

        q = (
            session.query(
                Venta,
                stats.c.Peso_total_pedido,
                stats.c.Volumen_total_pedido,
            )
            .outerjoin(stats, Venta.numero_orden == stats.c.numero_orden)
            .filter(Venta.estado == "Pendiente")
        )
        if user_id is not None:
            q = q.filter(Venta.id_usuario == user_id)

        rows = q.all()
        data = []
        for v, peso, vol in rows:
            data.append(
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
                    "Peso_total_pedido": float(peso) if peso is not None else 0.0,
                    "Volumen_total_pedido": float(vol) if vol is not None else 0.0,
                }
            )
        return pd.DataFrame(data)
    finally:
        session.close()
