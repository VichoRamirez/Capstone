"""
Servicio de optimización de rutas.

Recibe datos limpios (con coordenadas), genera matrices de distancia/tiempo
priorizando OSRM local y con fallback seguro, ejecuta Solomon I1 Style, y produce:
  - CSV de rutas asignadas (camión → puntos)
  - CSV de puntos no cubiertos
  - Mapa HTML interactivo (folium)
"""

import csv
import html
import io
import math
import os
import random
import tempfile
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import folium
import pandas as pd

from backend.models.routing.heuristics import (
    clarke_wright_initial_solution,
    alns,
    Route,
)
from backend.schemas import OptimizerParams
from backend.services.fuel_price_service import fetch_fuel_price_clp, normalize_fuel_type
from backend.services.road_routing import (
    DEFAULT_LOCAL_OSRM_BASE,
    build_real_distance_time_speed_matrices,
    build_search_distance_time_matrices,
    build_route_polylines,
)


# ── Haversine ─────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula la distancia en km entre dos puntos geográficos."""
    R = 6371.0  # Radio de la Tierra en km
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


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


def _metric_summary(values: list[float]) -> dict:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"total": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    total = float(sum(vals))
    return {
        "total": round(total, 4),
        "min": round(float(min(vals)), 4),
        "max": round(float(max(vals)), 4),
        "avg": round(float(total / len(vals)), 4),
    }


@dataclass
class PostTripAssignment:
    route_index: int
    truck_id: int
    trip_number: int
    depart_min: float
    return_min: float
    route_duration_min: float = 0.0
    traffic_factor: float = 1.0
    hour_factor: float = 1.0
    zone_tag: str = "metropolitana"


@dataclass
class RouteTrafficContext:
    route_index: int
    zone_tag: str
    zone_factor: float
    travel_base_min: float
    service_base_min: float
    base_duration_min: float


_TRAFFIC_HOUR_FACTORS = [
    (0.0, 6.0, 0.78),
    (6.0, 7.0, 0.98),
    (7.0, 9.0, 1.42),
    (9.0, 11.0, 1.18),
    (11.0, 13.0, 1.02),
    (13.0, 15.0, 1.18),
    (15.0, 17.0, 1.12),
    (17.0, 20.0, 1.48),
    (20.0, 22.0, 1.12),
    (22.0, 24.0, 0.86),
]

_ZONE_FACTOR_BY_TAG = {
    "centro": 1.18,
    "oriente": 1.12,
    "poniente": 1.08,
    "sur": 1.10,
    "norte": 1.07,
    "metropolitana": 1.00,
}

_ZONE_COMUNAS = {
    "centro": {
        "santiago",
        "estacion central",
        "recoleta",
        "independencia",
        "quinta normal",
    },
    "oriente": {
        "las condes",
        "vitacura",
        "lo barnechea",
        "providencia",
        "nunoa",
        "la reina",
        "penalolen",
        "macul",
    },
    "poniente": {
        "maipu",
        "pudahuel",
        "cerrillos",
        "lo prado",
        "cerro navia",
        "renca",
        "quinta normal",
    },
    "sur": {
        "san miguel",
        "san joaquin",
        "la florida",
        "la granja",
        "san ramon",
        "la cisterna",
        "el bosque",
        "pedro aguirre cerda",
        "puente alto",
    },
    "norte": {
        "quilicura",
        "huechuraba",
        "conchali",
        "colina",
        "lampa",
        "tiltil",
    },
}

_OSRM_MATRIX_RETRY_COOLDOWN_SEC = 180.0
_OSRM_MATRIX_RETRY_AFTER = 0.0


def _normalize_text(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in normalized)
    return " ".join(normalized.split())


def _infer_zone_from_comuna(comuna_raw: str) -> str:
    comuna = _normalize_text(comuna_raw)
    if not comuna:
        return "metropolitana"
    for zone_tag, comunas in _ZONE_COMUNAS.items():
        if comuna in comunas:
            return zone_tag
    return "metropolitana"


def _hour_traffic_factor(abs_minutes: float) -> float:
    mins = float(abs_minutes) % 1440.0
    hour = mins / 60.0
    for start_h, end_h, factor in _TRAFFIC_HOUR_FACTORS:
        if start_h <= hour < end_h:
            return float(factor)
    return 1.0


def _route_duration_with_traffic(
    ctx: RouteTrafficContext,
    *,
    depart_abs_min: float,
    enabled: bool,
) -> tuple[float, float, float]:
    if not enabled:
        return float(ctx.base_duration_min), 1.0, 1.0
    hour_factor = _hour_traffic_factor(float(depart_abs_min))
    traffic_factor = max(0.72, min(2.35, float(ctx.zone_factor) * float(hour_factor)))
    adjusted_travel = float(ctx.travel_base_min) * float(traffic_factor)
    duration = float(ctx.service_base_min + adjusted_travel)
    duration = max(float(ctx.service_base_min), duration)
    return float(duration), float(traffic_factor), float(hour_factor)


def _build_route_traffic_context(
    routes: list,
    node_info: dict,
    T: dict,
    t: dict,
) -> list[RouteTrafficContext]:
    out: list[RouteTrafficContext] = []
    for route_idx, route in enumerate(routes):
        nodes = list(route.nodes) if hasattr(route, "nodes") else []
        customers = [int(n) for n in nodes if int(n) != 0]
        travel_base = 0.0
        for i in range(len(nodes) - 1):
            travel_base += _safe_float(t.get((nodes[i], nodes[i + 1]), 0.0), 0.0)
        service_base = sum(_safe_float(T.get(n, 0.0), 0.0) for n in customers)
        base_duration = _safe_float(getattr(route, "time", 0.0), 0.0)
        if base_duration <= 0.0:
            base_duration = float(travel_base + service_base)
        if travel_base <= 1e-9 and service_base <= 1e-9 and base_duration > 0.0:
            travel_base = float(base_duration)

        route_zones = []
        for node in customers:
            info = node_info.get(node, {}) if isinstance(node_info, dict) else {}
            zone_tag = _infer_zone_from_comuna(str(info.get("Comuna", "") or ""))
            route_zones.append(zone_tag)
        route_zones = [z for z in route_zones if z]
        zone_tag = (
            Counter(route_zones).most_common(1)[0][0]
            if route_zones
            else "metropolitana"
        )
        zone_factor = _safe_float(_ZONE_FACTOR_BY_TAG.get(zone_tag, 1.0), 1.0)

        out.append(
            RouteTrafficContext(
                route_index=int(route_idx),
                zone_tag=str(zone_tag),
                zone_factor=float(zone_factor),
                travel_base_min=float(travel_base),
                service_base_min=float(service_base),
                base_duration_min=float(base_duration),
            )
        )
    return out


def _parse_hhmm_to_minutes(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    try:
        hh_s, mm_s = text.split(":", 1)
        hh = int(hh_s)
        mm = int(mm_s)
    except Exception:
        return None
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return int(hh * 60 + mm)


def _parse_worktime_window_minutes(worktime_windows: Optional[str]) -> tuple[int, int]:
    default_start = 9 * 60   # 09:00
    default_end = 17 * 60    # 17:00
    raw = str(worktime_windows or "").strip()
    if not raw or "-" not in raw:
        return default_start, default_end
    start_raw, end_raw = raw.split("-", 1)
    start_min = _parse_hhmm_to_minutes(start_raw)
    end_min = _parse_hhmm_to_minutes(end_raw)
    if start_min is None or end_min is None:
        return default_start, default_end
    if end_min <= start_min:
        end_min = start_min + 8 * 60
    return int(start_min), int(end_min)


def _default_trip_plan(route_context: list[RouteTrafficContext]) -> list[PostTripAssignment]:
    out: list[PostTripAssignment] = []
    for ctx in route_context:
        route_time = max(0.0, _safe_float(ctx.base_duration_min, 0.0))
        out.append(
            PostTripAssignment(
                route_index=int(ctx.route_index),
                truck_id=int(ctx.route_index + 1),
                trip_number=1,
                depart_min=0.0,
                return_min=float(route_time),
                route_duration_min=float(route_time),
                traffic_factor=1.0,
                hour_factor=1.0,
                zone_tag=str(ctx.zone_tag),
            )
        )
    return out


def _compress_routes_postoptimal(
    routes: list,
    *,
    shift_minutes: float,
    day_start_minutes: int = 9 * 60,
    turnaround_min: float = 60.0,
    route_context: Optional[list[RouteTrafficContext]] = None,
    use_time_dependent_traffic: bool = True,
) -> tuple[list[PostTripAssignment], dict]:
    if not routes:
        return [], {
            "enabled": True,
            "reason": "no_routes",
            "trucks_used": 0,
            "routes_total": 0,
            "turnaround_min": round(float(turnaround_min), 4),
            "shift_minutes": round(float(shift_minutes), 4),
            "time_dependent_traffic_enabled": bool(use_time_dependent_traffic),
        }

    shift = max(1.0, _safe_float(shift_minutes, 480.0))
    setup = max(0.0, _safe_float(turnaround_min, 60.0))
    day_start = int(max(0, _safe_int(day_start_minutes, 9 * 60)))

    contexts = (
        list(route_context)
        if isinstance(route_context, list) and route_context
        else _build_route_traffic_context(routes, node_info={}, T={}, t={})
    )
    if not contexts:
        return [], {
            "enabled": True,
            "reason": "no_route_context",
            "trucks_used": 0,
            "routes_total": 0,
            "turnaround_min": round(setup, 4),
            "shift_minutes": round(shift, 4),
            "time_dependent_traffic_enabled": bool(use_time_dependent_traffic),
        }
    ctx_by_idx = {int(c.route_index): c for c in contexts}

    max_route_duration = max(float(c.base_duration_min) for c in contexts)
    if max_route_duration > shift + 1e-9:
        fallback = _default_trip_plan(contexts)
        trucks_used = len({int(x.truck_id) for x in fallback})
        return fallback, {
            "enabled": False,
            "reason": "route_exceeds_shift_window",
            "trucks_used": int(trucks_used),
            "routes_total": int(len(fallback)),
            "turnaround_min": round(setup, 4),
            "shift_minutes": round(shift, 4),
            "max_route_duration_min": round(float(max_route_duration), 4),
            "time_dependent_traffic_enabled": False,
        }

    traffic_enabled = bool(use_time_dependent_traffic)
    traffic_fallback_reason = ""
    if traffic_enabled:
        max_start_duration = 0.0
        for ctx in contexts:
            start_dur, _, _ = _route_duration_with_traffic(
                ctx,
                depart_abs_min=float(day_start),
                enabled=True,
            )
            max_start_duration = max(max_start_duration, float(start_dur))
        if max_start_duration > shift + 1e-9:
            traffic_enabled = False
            traffic_fallback_reason = "traffic_profile_exceeds_shift_at_start"

    jobs = [
        {
            "route_index": int(ctx.route_index),
            "base_duration": float(ctx.base_duration_min),
        }
        for ctx in contexts
    ]
    jobs = sorted(
        jobs,
        key=lambda j: (-float(j["base_duration"]), int(j["route_index"])),
    )

    truck_states: list[dict] = []
    next_truck_id = 1
    for job in jobs:
        route_idx = int(job["route_index"])
        ctx = ctx_by_idx.get(route_idx)
        if ctx is None:
            continue

        feasible = []
        for state in truck_states:
            depart_rel = (
                float(state["last_return"]) + setup
                if state["trips"]
                else 0.0
            )
            dur, traffic_factor, hour_factor = _route_duration_with_traffic(
                ctx,
                depart_abs_min=float(day_start + depart_rel),
                enabled=bool(traffic_enabled),
            )
            ret = float(depart_rel + dur)
            if ret <= shift + 1e-9:
                feasible.append(
                    (
                        int(len(state["trips"])),
                        float(state["last_return"]),
                        float(ret),
                        state,
                        float(depart_rel),
                        float(dur),
                        float(traffic_factor),
                        float(hour_factor),
                    )
                )

        if feasible:
            feasible.sort(key=lambda x: (x[0], x[1], x[2], int(x[3]["truck_id"])))
            _, _, ret, selected, depart_rel, dur, traffic_factor, hour_factor = feasible[0]
        else:
            depart_rel = 0.0
            dur, traffic_factor, hour_factor = _route_duration_with_traffic(
                ctx,
                depart_abs_min=float(day_start),
                enabled=bool(traffic_enabled),
            )
            if float(dur) > shift + 1e-9:
                fallback = _default_trip_plan(contexts)
                trucks_used = len({int(x.truck_id) for x in fallback})
                return fallback, {
                    "enabled": False,
                    "reason": "route_exceeds_shift_under_time_profile",
                    "trucks_used": int(trucks_used),
                    "routes_total": int(len(fallback)),
                    "turnaround_min": round(setup, 4),
                    "shift_minutes": round(shift, 4),
                    "time_dependent_traffic_enabled": False,
                }
            selected = {
                "truck_id": int(next_truck_id),
                "last_return": 0.0,
                "trips": [],
            }
            truck_states.append(selected)
            next_truck_id += 1
            ret = float(depart_rel + dur)

        trip_number = int(len(selected["trips"]) + 1)
        assignment = PostTripAssignment(
            route_index=int(route_idx),
            truck_id=int(selected["truck_id"]),
            trip_number=int(trip_number),
            depart_min=float(depart_rel),
            return_min=float(ret),
            route_duration_min=float(dur),
            traffic_factor=float(traffic_factor),
            hour_factor=float(hour_factor),
            zone_tag=str(ctx.zone_tag),
        )
        selected["trips"].append(assignment)
        selected["last_return"] = float(ret)

    assignments: list[PostTripAssignment] = []
    for state in sorted(truck_states, key=lambda s: int(s["truck_id"])):
        assignments.extend(list(state.get("trips", [])))

    covered = {int(a.route_index) for a in assignments}
    if len(covered) < len(routes):
        extra_truck = max((int(a.truck_id) for a in assignments), default=0) + 1
        for ridx in range(len(routes)):
            if ridx in covered:
                continue
            ctx = ctx_by_idx.get(
                int(ridx),
                RouteTrafficContext(
                    route_index=int(ridx),
                    zone_tag="metropolitana",
                    zone_factor=1.0,
                    travel_base_min=_safe_float(getattr(routes[ridx], "time", 0.0), 0.0),
                    service_base_min=0.0,
                    base_duration_min=_safe_float(getattr(routes[ridx], "time", 0.0), 0.0),
                ),
            )
            dur = max(0.0, _safe_float(ctx.base_duration_min, 0.0))
            assignments.append(
                PostTripAssignment(
                    route_index=int(ridx),
                    truck_id=int(extra_truck),
                    trip_number=1,
                    depart_min=0.0,
                    return_min=float(dur),
                    route_duration_min=float(dur),
                    traffic_factor=1.0,
                    hour_factor=1.0,
                    zone_tag=str(ctx.zone_tag),
                )
            )
            extra_truck += 1

    assignments.sort(
        key=lambda x: (
            int(x.truck_id),
            int(x.trip_number),
            float(x.depart_min),
            int(x.route_index),
        )
    )

    trucks_used = len({int(a.truck_id) for a in assignments})
    routes_per_truck = []
    busy_vals = []
    traffic_vals = []
    hour_vals = []
    for truck_id in sorted({int(a.truck_id) for a in assignments}):
        trips = [a for a in assignments if int(a.truck_id) == truck_id]
        if not trips:
            continue
        routes_per_truck.append(float(len(trips)))
        span = max(float(t.return_min) for t in trips) - min(float(t.depart_min) for t in trips)
        busy_vals.append(float(span))
        traffic_vals.append(
            float(sum(_safe_float(t.traffic_factor, 1.0) for t in trips) / len(trips))
        )
        hour_vals.append(
            float(sum(_safe_float(t.hour_factor, 1.0) for t in trips) / len(trips))
        )
    zone_counter = Counter(str(a.zone_tag) for a in assignments)

    return assignments, {
        "enabled": True,
        "reason": "ok",
        "trucks_used": int(trucks_used),
        "routes_total": int(len(assignments)),
        "trucks_saved_vs_routes": int(max(0, len(assignments) - trucks_used)),
        "compression_ratio_routes_per_truck": round(float(len(assignments) / max(1, trucks_used)), 4),
        "routes_per_truck": _metric_summary(routes_per_truck),
        "occupied_time_min_per_truck": _metric_summary(busy_vals),
        "traffic_factor_per_truck_avg": _metric_summary(traffic_vals),
        "hour_factor_per_truck_avg": _metric_summary(hour_vals),
        "zone_distribution": dict(zone_counter),
        "turnaround_min": round(setup, 4),
        "shift_minutes": round(shift, 4),
        "day_start_minutes": int(day_start),
        "assignment_strategy": "greedy_min_trucks_balanced_time_dependent",
        "time_dependent_traffic_requested": bool(use_time_dependent_traffic),
        "time_dependent_traffic_enabled": bool(traffic_enabled),
        "time_dependent_traffic_profile": "santiago_reference_v1",
        "time_dependent_traffic_fallback_reason": traffic_fallback_reason or None,
    }


def _build_route_kpis(
    routes: list,
    *,
    p: dict,
    v: dict,
    T: dict,
    t: dict,
    c_fixed: float,
    g: float,
    o: float,
    trip_plan: Optional[list[PostTripAssignment]] = None,
    turnaround_min: float = 60.0,
) -> dict:
    den = max(1e-9, float(o))
    fixed_cost = float(c_fixed)
    g_val = float(g)
    turnaround = max(0.0, _safe_float(turnaround_min, 60.0))

    route_metrics: dict[int, dict] = {}
    for route_idx, route in enumerate(routes):
        nodes = list(route.nodes) if hasattr(route, "nodes") else []
        if len(nodes) < 2:
            continue

        customers = [int(n) for n in nodes if int(n) != 0]
        travel_time = 0.0
        for i in range(len(nodes) - 1):
            travel_time += _safe_float(t.get((nodes[i], nodes[i + 1]), 0.0), 0.0)
        service_time = sum(_safe_float(T.get(n, 0.0), 0.0) for n in customers)

        distance_km = _safe_float(getattr(route, "dist", 0.0), 0.0)
        route_time_min = _safe_float(getattr(route, "time", 0.0), 0.0)
        if route_time_min <= 0.0:
            route_time_min = float(travel_time + service_time)

        load_kg = sum(_safe_float(p.get(n, 0.0), 0.0) for n in customers)
        load_m3 = sum(_safe_float(v.get(n, 0.0), 0.0) for n in customers)
        route_metrics[int(route_idx)] = {
            "stops": int(len(customers)),
            "distance_km": float(distance_km),
            "travel_time_min": float(travel_time),
            "service_time_min": float(service_time),
            "route_time_min": float(route_time_min),
            "load_kg": float(load_kg),
            "load_m3": float(load_m3),
        }

    by_truck = []
    if trip_plan:
        grouped: dict[int, list[PostTripAssignment]] = {}
        for item in trip_plan:
            if isinstance(item, PostTripAssignment):
                entry = item
            elif isinstance(item, dict):
                entry = PostTripAssignment(
                    route_index=_safe_int(item.get("route_index"), -1),
                    truck_id=max(1, _safe_int(item.get("truck_id"), 1)),
                    trip_number=max(1, _safe_int(item.get("trip_number"), 1)),
                    depart_min=_safe_float(item.get("depart_min"), 0.0),
                    return_min=_safe_float(item.get("return_min"), 0.0),
                    route_duration_min=_safe_float(item.get("route_duration_min"), 0.0),
                    traffic_factor=_safe_float(item.get("traffic_factor"), 1.0),
                    hour_factor=_safe_float(item.get("hour_factor"), 1.0),
                    zone_tag=str(item.get("zone_tag", "metropolitana")),
                )
            else:
                continue
            if int(entry.route_index) not in route_metrics:
                continue
            grouped.setdefault(int(entry.truck_id), []).append(entry)

        for truck_id in sorted(grouped.keys()):
            entries = sorted(
                grouped[truck_id],
                key=lambda x: (int(x.trip_number), float(x.depart_min), int(x.route_index)),
            )
            if not entries:
                continue

            stops = 0.0
            distance = 0.0
            travel = 0.0
            service = 0.0
            route_time_sum = 0.0
            load_kg = 0.0
            load_m3 = 0.0
            traffic_factor_vals = []
            for e in entries:
                m = route_metrics.get(int(e.route_index), {})
                stops += _safe_float(m.get("stops"), 0.0)
                distance += _safe_float(m.get("distance_km"), 0.0)
                traffic_factor = max(0.72, _safe_float(getattr(e, "traffic_factor", 1.0), 1.0))
                travel += _safe_float(m.get("travel_time_min"), 0.0) * traffic_factor
                service += _safe_float(m.get("service_time_min"), 0.0)
                route_duration = _safe_float(getattr(e, "route_duration_min", 0.0), 0.0)
                if route_duration <= 0.0:
                    route_duration = _safe_float(m.get("route_time_min"), 0.0)
                route_time_sum += route_duration
                load_kg += _safe_float(m.get("load_kg"), 0.0)
                load_m3 += _safe_float(m.get("load_m3"), 0.0)
                traffic_factor_vals.append(float(traffic_factor))

            trips = int(len(entries))
            turnaround_time = float(max(0, trips - 1) * turnaround)
            occupied_time = float(route_time_sum + turnaround_time)
            variable_cost = float(distance * g_val / den)
            total_cost = float(fixed_cost + variable_cost)
            traffic_factor_avg = (
                float(sum(traffic_factor_vals) / len(traffic_factor_vals))
                if traffic_factor_vals
                else 1.0
            )

            by_truck.append(
                {
                    "truck": f"Truck-{truck_id}",
                    "trips": int(trips),
                    "stops": int(round(stops)),
                    "distance_km": round(distance, 4),
                    "travel_time_min": round(travel, 4),
                    "service_time_min": round(service, 4),
                    "route_time_min": round(occupied_time, 4),
                    "turnaround_time_min": round(turnaround_time, 4),
                    "traffic_factor_avg": round(float(traffic_factor_avg), 4),
                    "load_kg": round(load_kg, 4),
                    "load_m3": round(load_m3, 4),
                    "cost_fixed": round(float(fixed_cost), 4),
                    "cost_variable": round(float(variable_cost), 4),
                    "cost_total": round(float(total_cost), 4),
                }
            )
    else:
        for route_idx in sorted(route_metrics.keys()):
            m = route_metrics[route_idx]
            variable_cost = float(_safe_float(m.get("distance_km"), 0.0) * g_val / den)
            total_cost = float(fixed_cost + variable_cost)
            by_truck.append(
                {
                    "truck": f"Truck-{route_idx + 1}",
                    "trips": 1,
                    "stops": int(_safe_int(m.get("stops"), 0)),
                    "distance_km": round(_safe_float(m.get("distance_km"), 0.0), 4),
                    "travel_time_min": round(_safe_float(m.get("travel_time_min"), 0.0), 4),
                    "service_time_min": round(_safe_float(m.get("service_time_min"), 0.0), 4),
                    "route_time_min": round(_safe_float(m.get("route_time_min"), 0.0), 4),
                    "turnaround_time_min": 0.0,
                    "traffic_factor_avg": 1.0,
                    "load_kg": round(_safe_float(m.get("load_kg"), 0.0), 4),
                    "load_m3": round(_safe_float(m.get("load_m3"), 0.0), 4),
                    "cost_fixed": round(float(fixed_cost), 4),
                    "cost_variable": round(float(variable_cost), 4),
                    "cost_total": round(float(total_cost), 4),
                }
            )

    stops_vals = [float(x["stops"]) for x in by_truck]
    trips_vals = [float(x.get("trips", 1)) for x in by_truck]
    dist_vals = [float(x["distance_km"]) for x in by_truck]
    travel_vals = [float(x["travel_time_min"]) for x in by_truck]
    service_vals = [float(x["service_time_min"]) for x in by_truck]
    route_time_vals = [float(x["route_time_min"]) for x in by_truck]
    turnaround_vals = [float(x.get("turnaround_time_min", 0.0)) for x in by_truck]
    traffic_factor_vals = [float(x.get("traffic_factor_avg", 1.0)) for x in by_truck]
    load_kg_vals = [float(x["load_kg"]) for x in by_truck]
    load_m3_vals = [float(x["load_m3"]) for x in by_truck]
    fixed_vals = [float(x["cost_fixed"]) for x in by_truck]
    variable_vals = [float(x["cost_variable"]) for x in by_truck]
    total_cost_vals = [float(x["cost_total"]) for x in by_truck]

    return {
        "fleet": {
            "trucks_used": int(len(by_truck)),
            "customers_total": int(sum(int(x["stops"]) for x in by_truck)),
            "trips_per_truck": _metric_summary(trips_vals),
            "stops_per_truck": _metric_summary(stops_vals),
            "distance_km": _metric_summary(dist_vals),
            "travel_time_min": _metric_summary(travel_vals),
            "service_time_min": _metric_summary(service_vals),
            "route_time_min": _metric_summary(route_time_vals),
            "turnaround_time_min": _metric_summary(turnaround_vals),
            "traffic_factor_avg": _metric_summary(traffic_factor_vals),
            "load_kg": _metric_summary(load_kg_vals),
            "load_m3": _metric_summary(load_m3_vals),
            "cost_fixed": _metric_summary(fixed_vals),
            "cost_variable": _metric_summary(variable_vals),
            "cost_total": _metric_summary(total_cost_vals),
        },
        "by_truck": by_truck,
    }


def service_time_minutes_from_weight(weight_kg: float) -> float:
    """Misma fórmula logarítmica usada en la rama `rescate-cambios`.

    y = (27 / ln(800)) * ln(x) + 3, con x en kg.
    """
    x = max(1e-6, float(weight_kg))
    return (27.0 / math.log(800.0)) * math.log(x) + 3.0


# ── Generador de matrices desde DataFrame real ────────────────────────────

def _runtime_osrm_base_url() -> str:
    return (
        os.getenv("OSRM_BASE_URL", "").strip()
        or os.getenv("OSRM_LOCAL_BASE_URL", "").strip()
        or DEFAULT_LOCAL_OSRM_BASE
    )


def _osrm_matrix_allowed_now() -> bool:
    global _OSRM_MATRIX_RETRY_AFTER
    return time.monotonic() >= float(_OSRM_MATRIX_RETRY_AFTER)


def _mark_osrm_matrix_failure_cooldown():
    global _OSRM_MATRIX_RETRY_AFTER
    _OSRM_MATRIX_RETRY_AFTER = time.monotonic() + float(_OSRM_MATRIX_RETRY_COOLDOWN_SEC)


def _clear_osrm_matrix_failure_cooldown():
    global _OSRM_MATRIX_RETRY_AFTER
    _OSRM_MATRIX_RETRY_AFTER = 0.0


def generate_matrices_from_df(df_ventas: pd.DataFrame, params: OptimizerParams):
    """
    Genera las matrices de distancia/tiempo a partir de un DataFrame con
    columnas Latitud, Longitud, y la demanda por pedido.
    El nodo 0 es el depósito (depot_address).
    """
    # Filtrar filas sin coordenadas
    df_valid = df_ventas.dropna(subset=["Latitud", "Longitud"]).copy()
    df_invalid = df_ventas[df_ventas["Latitud"].isna() | df_ventas["Longitud"].isna()].copy()

    n_customers = len(df_valid)
    K = list(range(params.num_trucks))
    J = list(range(1, n_customers + 1))
    N = [0] + J

    # Coordenadas: nodo 0 = depósito
    depot_lat, depot_lon = params.depot_address
    coords = {0: (depot_lat, depot_lon)}

    p = {}   # Demanda (peso total del pedido)
    v = {}   # Volumen total del pedido
    T = {0: 0}  # Tiempo de servicio

    # Mapeo de nodos a info del pedido
    node_info = {}

    for idx, (_, row) in enumerate(df_valid.iterrows()):
        j = idx + 1
        coords[j] = (row["Latitud"], row["Longitud"])

        # Demanda = peso total o monto como proxy
        p_j = float(row["Peso_total_pedido"]) if pd.notna(row.get("Peso_total_pedido")) else 100.0
        v_j = float(row["Volumen_total_pedido"]) if pd.notna(row.get("Volumen_total_pedido")) else 1.0
        p[j] = p_j
        v[j] = v_j
        # Tiempo de servicio desde peso (misma fórmula logarítmica solicitada).
        T[j] = service_time_minutes_from_weight(p_j)

        sku_count = _safe_int(row.get("SKU_count", 0), 0) if pd.notna(row.get("SKU_count")) else 0
        items_total = _safe_float(row.get("Items_total", 0.0), 0.0) if pd.notna(row.get("Items_total")) else 0.0
        sku_preview = ""
        if pd.notna(row.get("SKU_preview")):
            sku_preview = str(row.get("SKU_preview", "")).strip()

        node_info[j] = {
            "RUT": row.get("RUT", ""),
            "Nombre cliente": row.get("Nombre cliente", ""),
            "Dirección cliente": row.get("Dirección cliente", ""),
            "Comuna": row.get("Comuna", ""),
            "Número de Orden": row.get("Número de Orden", ""),
            "Latitud": row["Latitud"],
            "Longitud": row["Longitud"],
            "Peso_total_pedido": round(float(p_j), 4),
            "Volumen_total_pedido": round(float(v_j), 4),
            "SKU_count": int(sku_count),
            "Items_total": round(float(items_total), 4),
            "SKU_preview": sku_preview,
        }

    P = params.weight_per_truck or 2500
    V = params.space_per_truck or 10.0
    c_fixed = _safe_float(getattr(params, "truck_fixed_cost_clp", 20000.0), 20000.0)
    g = _safe_float(getattr(params, "diesel_price_clp", None), 0.0)
    if g <= 0:
        # Defensivo: el flujo normal resuelve esto antes de llegar aquí.
        g = 1600.0
    o = params.km_per_liter or 8.0
    max_route_time = 300.0  # Jornada = 300 min = 5h (fixed, independent of ALNS time limit)

    # Matrices de distancia y tiempo:
    # 1) Intentar OSRM local/public
    # 2) Si falla, el módulo de ruteo aplica fallback robusto automáticamente
    d = {}
    t = {}
    routing_meta = {}
    coords_lonlat = {node: (float(lon), float(lat)) for node, (lat, lon) in coords.items()}
    osrm_try_matrix = _osrm_matrix_allowed_now()
    if not osrm_try_matrix:
        routing_meta = {
            "osrm_used": False,
            "osrm_error": "matrix_osrm_retry_cooldown_active",
            "fallback": "cooldown_skip",
        }
    try:
        d, t, _speed, routing_meta = build_real_distance_time_speed_matrices(
            coords_lonlat,
            avg_speed_kmh=30.0,
            use_osrm=bool(osrm_try_matrix),
            osrm_base_url=_runtime_osrm_base_url(),
            timeout_sec=3.0,
        )
        if not osrm_try_matrix and isinstance(routing_meta, dict):
            routing_meta["osrm_error"] = "matrix_osrm_retry_cooldown_active"
            routing_meta["fallback"] = "cooldown_skip"
        if isinstance(routing_meta, dict) and bool(routing_meta.get("osrm_used")):
            _clear_osrm_matrix_failure_cooldown()
        else:
            _mark_osrm_matrix_failure_cooldown()
    except Exception as exc:
        # Ultimo fallback local para no cortar el pipeline.
        u_speed = 30.0
        for i in N:
            for j in N:
                if i == j:
                    d[i, j] = 0.0
                    t[i, j] = 0.0
                else:
                    lat_i, lon_i = coords[i]
                    lat_j, lon_j = coords[j]
                    dist = haversine_km(lat_i, lon_i, lat_j, lon_j)
                    d[i, j] = round(dist, 2)
                    t[i, j] = round(60.0 * dist / u_speed, 2)
        routing_meta = {
            "osrm_used": False,
            "osrm_error": str(exc),
            "fallback": "haversine_local",
        }
        _mark_osrm_matrix_failure_cooldown()

    # Refinar la matriz con búsqueda en grafo:
    # - Dijkstra: 5 nodos más cercanos por nodo.
    # - A*: costo hacia el CD (nodo 0).
    try:
        d, t, search_meta = build_search_distance_time_matrices(
            coords=coords_lonlat,
            d=d,
            t=t,
            k_nearest=5,
            depot=0,
        )
    except Exception as exc:  # pragma: no cover - defensivo
        search_meta = {"search_enabled": False, "error": str(exc)}

    if not isinstance(routing_meta, dict):
        routing_meta = {}
    routing_meta["search_matrix"] = search_meta
    routing_meta["matrix_strategy"] = "dijkstra_k5_plus_astar_to_depot"

    return (
        K,
        J,
        N,
        coords,
        p,
        v,
        T,
        P,
        V,
        c_fixed,
        g,
        o,
        d,
        t,
        max_route_time,
        node_info,
        df_invalid,
        routing_meta,
    )


# ── Generador de mapa folium ──────────────────────────────────────────────

TRUCK_COLORS = [
    "blue", "red", "green", "purple", "orange", "darkred",
    "cadetblue", "darkgreen", "darkpurple", "pink", "gray",
    "lightblue", "lightgreen", "lightred", "beige", "black",
]


def _pin_size_px(weight_kg: float, min_weight_kg: float, max_weight_kg: float) -> float:
    if weight_kg <= 0:
        return 28.0
    if max_weight_kg <= min_weight_kg + 1e-9:
        return 36.0
    normalized = (float(weight_kg) - float(min_weight_kg)) / max(1e-9, float(max_weight_kg - min_weight_kg))
    normalized = max(0.0, min(1.0, normalized))
    return 28.0 + 18.0 * normalized


def _pin_fill_color(weight_kg: float, min_weight_kg: float, max_weight_kg: float) -> str:
    if weight_kg <= 0:
        return "#00d4ff"
    if max_weight_kg <= min_weight_kg + 1e-9:
        return "#22c55e"
    normalized = (float(weight_kg) - float(min_weight_kg)) / max(1e-9, float(max_weight_kg - min_weight_kg))
    normalized = max(0.0, min(1.0, normalized))
    if normalized < 0.33:
        return "#00d4ff"
    if normalized < 0.66:
        return "#22c55e"
    return "#f59e0b"


def _pin_html(color: str, size_px: float) -> str:
    size = max(16, int(round(float(size_px))))
    ring = max(3, int(round(size * 0.28)))
    stroke = max(1, int(round(size * 0.06)))
    return (
        f"<div style='width:{size}px;height:{size}px;pointer-events:auto;'>"
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 64 64'>"
        f"<path d='M32 62 L18 33 H46 Z' fill='{color}' stroke='#0b1326' stroke-width='{stroke}'/>"
        f"<circle cx='32' cy='22' r='18' fill='{color}' stroke='#0b1326' stroke-width='{stroke}'/>"
        f"<circle cx='32' cy='22' r='{ring}' fill='#ffffff'/>"
        "</svg></div>"
    )


def _tooltip_client_text(
    truck_label: str,
    trip_number: int,
    pass_min: float,
    service_min: float,
    order_id: str,
) -> str:
    return (
        f"{truck_label} | Trip {int(trip_number)} | Orden {order_id or '-'} | "
        f"Pasa min {pass_min:.1f} | Atención {service_min:.1f} min"
    )


def _popup_client_html(
    *,
    truck_label: str,
    trip_number: int,
    info: dict,
    pass_min: float,
    pass_hhmm: str,
    service_min: float,
    weight_kg: float,
) -> str:
    order_id = str(info.get("Número de Orden", "")).strip() or "-"
    rut = str(info.get("RUT", "")).strip() or "-"
    name = str(info.get("Nombre cliente", "")).strip() or "-"
    comuna = str(info.get("Comuna", "")).strip() or "-"
    address = str(info.get("Dirección cliente", "")).strip() or "-"
    sku_count = _safe_int(info.get("SKU_count", 0), 0)
    items_total = _safe_float(info.get("Items_total", 0.0), 0.0)
    sku_preview = str(info.get("SKU_preview", "")).strip() or "-"
    return (
        "<div style='min-width:280px;max-width:340px;font-family:Menlo,Monaco,Consolas,monospace;font-size:12px;'>"
        f"<b>{html.escape(name)}</b><br>"
        f"Truck: {html.escape(truck_label)}<br>"
        f"Trip: {int(trip_number)}<br>"
        f"Orden: {html.escape(order_id)}<br>"
        f"RUT: {html.escape(rut)}<br>"
        f"Comuna: {html.escape(comuna)}<br>"
        f"Dirección: {html.escape(address)}<br>"
        f"Peso total: {weight_kg:.2f} kg<br>"
        f"Pasa: min {pass_min:.1f} ({html.escape(pass_hhmm)})<br>"
        f"Atención: {service_min:.1f} min<br>"
        f"SKUs: {sku_count} | Items: {items_total:.0f}<br>"
        f"Detalle SKUs: {html.escape(sku_preview)}"
        "</div>"
    )


def _service_start_times_scaled(
    route_nodes: list[int],
    T: dict,
    t: dict,
    *,
    travel_factor: float = 1.0,
) -> dict[int, float]:
    starts: dict[int, float] = {}
    current = 0.0
    tf = max(0.72, _safe_float(travel_factor, 1.0))
    for idx in range(1, max(1, len(route_nodes) - 1)):
        prevn = route_nodes[idx - 1]
        node = route_nodes[idx]
        current += _safe_float(t.get((prevn, node), 0.0), 0.0) * tf
        starts[int(node)] = float(current)
        current += _safe_float(T.get(node, 0.0), 0.0)
    return starts


def generate_map_html(
    coords: dict,
    routes: list,
    node_info: dict,
    depot_address: list[float],
    T: dict,
    t: dict,
    trip_plan: Optional[list[PostTripAssignment]] = None,
    day_start_minutes: int = 8 * 60,
    use_osrm_polylines: bool = True,
    polyline_timeout_sec: float = 1.6,
) -> tuple[str, dict]:
    """Genera un mapa HTML interactivo con folium mostrando las rutas."""
    center_lat, center_lon = depot_address
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles=None)
    folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="Mapa base de rutas",
        overlay=False,
        control=True,
    ).add_to(m)
    map_name = m.get_name()

    # Depósito
    folium.Marker(
        location=[center_lat, center_lon],
        popup="<b>Centro de Distribución</b>",
        icon=folium.Icon(color="black", icon="home", prefix="fa"),
    ).add_to(m)

    # Adapt coordinates to [lon, lat] format for OSRM
    coord_lonlat = {node: (c[1], c[0]) for node, c in coords.items()}
    coord_lonlat[0] = (depot_address[1], depot_address[0])
    
    # Extraer nodos de las rutas
    routes_nodes = [route.nodes for route in routes]
    
    # Generar polylines reales usando OSRM
    polylines, meta = build_route_polylines(
        routes=routes_nodes,
        coords=coord_lonlat,
        use_osrm=bool(use_osrm_polylines),
        osrm_base_url=_runtime_osrm_base_url(),
        timeout_sec=float(max(0.8, polyline_timeout_sec)),
    )

    all_weights = []
    for info in node_info.values():
        w = _safe_float(info.get("Peso_total_pedido"), 0.0)
        if w > 0:
            all_weights.append(w)
    min_weight = min(all_weights) if all_weights else 0.0
    max_weight = max(all_weights) if all_weights else 0.0

    start_hour = int(max(0, int(day_start_minutes) // 60))
    start_minute = int(max(0, int(day_start_minutes) % 60))

    plan_rows = []
    if trip_plan:
        for row in trip_plan:
            if isinstance(row, PostTripAssignment):
                ridx = int(row.route_index)
                plan_rows.append(
                    {
                        "route_index": ridx,
                        "truck_id": max(1, int(row.truck_id)),
                        "trip_number": max(1, int(row.trip_number)),
                        "depart_min": float(row.depart_min),
                        "return_min": float(row.return_min),
                        "route_duration_min": float(row.route_duration_min),
                        "traffic_factor": float(row.traffic_factor),
                        "hour_factor": float(row.hour_factor),
                        "zone_tag": str(row.zone_tag),
                    }
                )
            elif isinstance(row, dict):
                ridx = _safe_int(row.get("route_index"), -1)
                plan_rows.append(
                    {
                        "route_index": ridx,
                        "truck_id": max(1, _safe_int(row.get("truck_id"), 1)),
                        "trip_number": max(1, _safe_int(row.get("trip_number"), 1)),
                        "depart_min": _safe_float(row.get("depart_min"), 0.0),
                        "return_min": _safe_float(row.get("return_min"), 0.0),
                        "route_duration_min": _safe_float(row.get("route_duration_min"), 0.0),
                        "traffic_factor": _safe_float(row.get("traffic_factor"), 1.0),
                        "hour_factor": _safe_float(row.get("hour_factor"), 1.0),
                        "zone_tag": str(row.get("zone_tag", "metropolitana")),
                    }
                )

    if not plan_rows:
        for route_idx, route in enumerate(routes):
            duration = max(0.0, _safe_float(getattr(route, "time", 0.0), 0.0))
            plan_rows.append(
                {
                    "route_index": int(route_idx),
                    "truck_id": int(route_idx + 1),
                    "trip_number": 1,
                    "depart_min": 0.0,
                    "return_min": float(duration),
                    "route_duration_min": float(duration),
                    "traffic_factor": 1.0,
                    "hour_factor": 1.0,
                    "zone_tag": "metropolitana",
                }
            )

    plan_rows = [
        r for r in plan_rows
        if 0 <= int(r.get("route_index", -1)) < len(routes)
    ]

    grouped_by_truck: dict[int, list[dict]] = {}
    for row in plan_rows:
        grouped_by_truck.setdefault(int(row["truck_id"]), []).append(row)
    for truck_id in grouped_by_truck.keys():
        grouped_by_truck[truck_id].sort(
            key=lambda x: (int(x["trip_number"]), float(x["depart_min"]), int(x["route_index"]))
        )

    for truck_id in sorted(grouped_by_truck.keys()):
        truck_name = f"Truck-{truck_id}"
        trips = grouped_by_truck[truck_id]
        total_stops = 0
        for trip in trips:
            ridx = int(trip["route_index"])
            route = routes[ridx]
            total_stops += max(0, len(route.nodes) - 2)

        color = TRUCK_COLORS[(truck_id - 1) % len(TRUCK_COLORS)]
        layer_name = (
            "<span style='display:inline-flex;align-items:center;gap:7px;'>"
            f"<span style='width:10px;height:10px;border-radius:50%;background:{color};"
            "display:inline-block;border:1px solid rgba(11,19,38,0.95);'></span>"
            f"<span>{truck_name}</span>"
            f"<span style='opacity:0.72;font-size:11px;'>(trips {len(trips)} · stops {total_stops})</span>"
            "</span>"
        )
        truck_layer = folium.FeatureGroup(name=layer_name, show=True)

        for trip in trips:
            ridx = int(trip["route_index"])
            trip_number = max(1, int(trip.get("trip_number", 1)))
            depart_min = float(trip.get("depart_min", 0.0))
            traffic_factor = max(0.72, _safe_float(trip.get("traffic_factor"), 1.0))
            route = routes[ridx]
            starts = _service_start_times_scaled(
                route.nodes,
                T,
                t,
                travel_factor=traffic_factor,
            )
            polyline = polylines[ridx] if ridx < len(polylines) else []

            for node in route.nodes:
                if node == 0:
                    continue
                info = node_info.get(node, {})
                lat, lon = coords[node]
                order_id = str(info.get("Número de Orden", "")).strip()
                pass_min = float(depart_min + _safe_float(starts.get(node, 0.0), 0.0))
                pass_hhmm = format_time(
                    pass_min,
                    start_hour=start_hour,
                    start_minute=start_minute,
                )
                service_min = _safe_float(T.get(node, 0.0), 0.0)
                weight_kg = _safe_float(info.get("Peso_total_pedido", 0.0), 0.0)
                pin_size = _pin_size_px(weight_kg, min_weight, max_weight)
                fill_color = _pin_fill_color(weight_kg, min_weight, max_weight)

                popup_html = _popup_client_html(
                    truck_label=truck_name,
                    trip_number=trip_number,
                    info=info,
                    pass_min=pass_min,
                    pass_hhmm=pass_hhmm,
                    service_min=service_min,
                    weight_kg=weight_kg,
                )
                tooltip_text = _tooltip_client_text(
                    truck_label=truck_name,
                    trip_number=trip_number,
                    pass_min=pass_min,
                    service_min=service_min,
                    order_id=order_id,
                )

                size_i = max(16, int(round(pin_size)))
                marker = folium.Marker(
                    location=[lat, lon],
                    icon=folium.DivIcon(
                        class_name="dispatch-pin-div",
                        html=_pin_html(fill_color, size_i),
                        icon_size=(size_i, size_i),
                        icon_anchor=(size_i // 2, size_i),
                        popup_anchor=(0, -int(round(size_i * 0.92))),
                    ),
                    popup=folium.Popup(popup_html, max_width=360),
                    tooltip=tooltip_text,
                )
                marker.add_to(truck_layer)

            folium_polyline = [[lat, lon] for lon, lat in polyline]
            if len(folium_polyline) > 1:
                folium.PolyLine(
                    folium_polyline,
                    color=color,
                    weight=3,
                    opacity=0.7,
                    tooltip=f"{truck_name} / Trip-{trip_number}",
                ).add_to(truck_layer)

        truck_layer.add_to(m)

    # Filtro por camión.
    folium.LayerControl(collapsed=False).add_to(m)

    control_css = """
    <style>
      .leaflet-control-layers {
        background: rgba(8, 14, 28, 0.92) !important;
        border: 1px solid #23314f !important;
        border-radius: 10px !important;
        color: #dbe7ff !important;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.42) !important;
      }
      .leaflet-control-layers-expanded {
        padding: 10px 12px 10px 12px !important;
      }
      .leaflet-control-layers label {
        display: flex !important;
        align-items: center !important;
        gap: 6px !important;
        margin: 3px 0 !important;
        line-height: 1.2 !important;
        font-family: Menlo, Monaco, Consolas, monospace !important;
        font-size: 12px !important;
      }
      .leaflet-control-layers-selector {
        accent-color: #00c9a7 !important;
      }
      .leaflet-control-layers-overlays {
        max-height: 280px;
        overflow-y: auto;
        padding-right: 4px;
      }
      .dispatch-truck-tools {
        position: fixed;
        top: 18px;
        right: 250px;
        z-index: 9999;
        display: flex;
        gap: 6px;
      }
      .dispatch-truck-tools button {
        border: 1px solid #27416a;
        background: rgba(8, 14, 28, 0.92);
        color: #dbe7ff;
        border-radius: 7px;
        padding: 4px 8px;
        font-size: 11px;
        font-family: Menlo, Monaco, Consolas, monospace;
        cursor: pointer;
      }
      .dispatch-truck-tools button:hover {
        border-color: #00c9a7;
        color: #00e2bd;
      }
    </style>
    """
    m.get_root().html.add_child(folium.Element(control_css))

    control_tools_html = (
        "<div class='dispatch-truck-tools'>"
        "<button type='button' onclick='dispatchToggleTruckLayers(true)'>Show all</button>"
        "<button type='button' onclick='dispatchToggleTruckLayers(false)'>Hide all</button>"
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(control_tools_html))

    control_script = f"""
    <script>
      function dispatchToggleTruckLayers(targetChecked) {{
        const root = document.querySelector("#{map_name}");
        if (!root) return;
        const boxes = root.querySelectorAll(
          ".leaflet-control-layers-overlays input.leaflet-control-layers-selector[type='checkbox']"
        );
        boxes.forEach((box) => {{
          if (!!box.checked !== !!targetChecked) {{
            box.click();
          }}
        }});
      }}
    </script>
    """
    m.get_root().html.add_child(folium.Element(control_script))

    if all_weights:
        legend_text = (
            f"Pins por peso: min {min_weight:.1f} kg · max {max_weight:.1f} kg"
        )
    else:
        legend_text = "Pins por peso: sin datos de peso, tamaño uniforme."
    legend_html = (
        "<div style='position:fixed;bottom:18px;left:18px;z-index:9999;"
        "background:rgba(10,14,28,0.92);color:#dbe9ff;border:1px solid #1f2a44;"
        "border-radius:8px;padding:6px 10px;font-family:Menlo,Monaco,Consolas,monospace;"
        f"font-size:11px;'>{html.escape(legend_text)}</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))

    meta_out = dict(meta) if isinstance(meta, dict) else {}
    meta_out["weighted_pins"] = bool(all_weights)
    meta_out["pin_weight_min_kg"] = round(float(min_weight), 4) if all_weights else 0.0
    meta_out["pin_weight_max_kg"] = round(float(max_weight), 4) if all_weights else 0.0
    meta_out["truck_filter_enabled"] = True
    meta_out["truck_layers_total"] = int(len(grouped_by_truck))
    meta_out["trips_total"] = int(len(plan_rows))
    meta_out["map_use_osrm_polylines"] = bool(use_osrm_polylines)
    meta_out["map_polyline_timeout_sec"] = round(float(max(0.8, polyline_timeout_sec)), 3)
    traffic_vals = [
        _safe_float(r.get("traffic_factor"), 1.0)
        for r in plan_rows
        if isinstance(r, dict)
    ]
    if traffic_vals:
        meta_out["traffic_factor_avg"] = round(float(sum(traffic_vals) / len(traffic_vals)), 4)
        meta_out["time_dependent_traffic_used"] = bool(any(abs(v - 1.0) > 1e-6 for v in traffic_vals))

    # Standalone HTML (not Jupyter repr) so PyQt WebEngine can render it.
    return m.get_root().render(), meta_out


# ── Formateo de resultados ────────────────────────────────────────────────

def format_time(
    minutes_after_start: float,
    start_hour: int = 8,
    start_minute: int = 0,
) -> str:
    """Formatea minutos desde inicio de jornada como HH:MM."""
    base = int(start_hour) * 60 + int(start_minute)
    total = base + int(round(_safe_float(minutes_after_start, 0.0)))
    total %= 24 * 60
    h = int(total // 60)
    m = int(total % 60)
    return f"{h:02d}:{m:02d}"


def build_routes_csv(
    routes: list,
    node_info: dict,
    T: dict,
    t: dict,
    depot_label: str = "Centro de Distribución",
    trip_plan: Optional[list[PostTripAssignment]] = None,
    day_start_minutes: int = 8 * 60,
) -> str:
    """Construye el CSV de rutas asignadas."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Camión", "Número de Orden", "RUT", "Nombre cliente",
        "Dirección cliente", "Comuna", "Hora estimada"
    ])

    start_hour = int(max(0, int(day_start_minutes) // 60))
    start_minute = int(max(0, int(day_start_minutes) % 60))

    plan_rows = []
    if trip_plan:
        for row in trip_plan:
            if isinstance(row, PostTripAssignment):
                plan_rows.append(
                    {
                        "route_index": int(row.route_index),
                        "truck_id": max(1, int(row.truck_id)),
                        "trip_number": max(1, int(row.trip_number)),
                        "depart_min": float(row.depart_min),
                        "return_min": float(row.return_min),
                        "route_duration_min": float(row.route_duration_min),
                        "traffic_factor": float(row.traffic_factor),
                        "hour_factor": float(row.hour_factor),
                        "zone_tag": str(row.zone_tag),
                    }
                )
            elif isinstance(row, dict):
                plan_rows.append(
                    {
                        "route_index": _safe_int(row.get("route_index"), -1),
                        "truck_id": max(1, _safe_int(row.get("truck_id"), 1)),
                        "trip_number": max(1, _safe_int(row.get("trip_number"), 1)),
                        "depart_min": _safe_float(row.get("depart_min"), 0.0),
                        "return_min": _safe_float(row.get("return_min"), 0.0),
                        "route_duration_min": _safe_float(row.get("route_duration_min"), 0.0),
                        "traffic_factor": _safe_float(row.get("traffic_factor"), 1.0),
                        "hour_factor": _safe_float(row.get("hour_factor"), 1.0),
                        "zone_tag": str(row.get("zone_tag", "metropolitana")),
                    }
                )
    if not plan_rows:
        for route_idx, route in enumerate(routes):
            route_time = max(0.0, _safe_float(getattr(route, "time", 0.0), 0.0))
            plan_rows.append(
                {
                    "route_index": int(route_idx),
                    "truck_id": int(route_idx + 1),
                    "trip_number": 1,
                    "depart_min": 0.0,
                    "return_min": float(route_time),
                    "route_duration_min": float(route_time),
                    "traffic_factor": 1.0,
                    "hour_factor": 1.0,
                    "zone_tag": "metropolitana",
                }
            )

    plan_rows = [
        p for p in plan_rows
        if 0 <= int(p.get("route_index", -1)) < len(routes)
    ]
    plan_rows.sort(
        key=lambda x: (
            int(x.get("truck_id", 0)),
            int(x.get("trip_number", 0)),
            float(x.get("depart_min", 0.0)),
            int(x.get("route_index", 0)),
        )
    )

    for row in plan_rows:
        route_idx = int(row["route_index"])
        route = routes[route_idx]
        truck_name = f"Truck-{int(row['truck_id'])}"
        trip_number = int(row.get("trip_number", 1))
        truck_trip_label = f"{truck_name} · Trip-{trip_number}"
        depart_min = float(row.get("depart_min", 0.0))
        traffic_factor = max(0.72, _safe_float(row.get("traffic_factor"), 1.0))
        return_min = float(
            row.get(
                "return_min",
                depart_min + _safe_float(
                    row.get("route_duration_min"),
                    _safe_float(getattr(route, "time", 0.0), 0.0),
                ),
            )
        )
        starts = _service_start_times_scaled(
            route.nodes,
            T,
            t,
            travel_factor=traffic_factor,
        )

        # Depot salida
        writer.writerow([
            truck_trip_label,
            "-",
            "-",
            "-",
            depot_label,
            "-",
            format_time(depart_min, start_hour=start_hour, start_minute=start_minute),
        ])

        # Clientes
        for i in range(1, len(route.nodes) - 1):
            node = route.nodes[i]
            info = node_info.get(node, {})
            start_min = float(depart_min + _safe_float(starts.get(node, 0.0), 0.0))
            writer.writerow([
                truck_trip_label,
                info.get("Número de Orden", ""),
                info.get("RUT", ""),
                info.get("Nombre cliente", ""),
                info.get("Dirección cliente", ""),
                info.get("Comuna", ""),
                format_time(start_min, start_hour=start_hour, start_minute=start_minute),
            ])

        # Depot regreso
        writer.writerow([
            truck_trip_label,
            "-",
            "-",
            "-",
            depot_label,
            "-",
            format_time(return_min, start_hour=start_hour, start_minute=start_minute),
        ])

    return output.getvalue()


def build_uncovered_csv(df_invalid: pd.DataFrame) -> str:
    """Construye el CSV de puntos no cubiertos (sin coordenadas o sin asignar)."""
    output = io.StringIO()
    cols = ["RUT", "Nombre cliente", "Dirección cliente", "Número de Orden",
            "Monto Pedido", "Motivo"]

    writer = csv.writer(output)
    writer.writerow(cols)

    for _, row in df_invalid.iterrows():
        writer.writerow([
            row.get("RUT", ""),
            row.get("Nombre cliente", ""),
            row.get("Dirección cliente", ""),
            row.get("Número de Orden", ""),
            row.get("Monto Pedido", ""),
            "Sin coordenadas (geocodificación fallida)",
        ])

    return output.getvalue()


# ── Función principal ─────────────────────────────────────────────────────
from backend.models.routing.metaheuristics import (
    VRPTWData,
    PenaltyConfig,
    LocalSearchConfig,
    TabuConfig,
    tabu_search_vrptw,
)
from backend.models.routing.literature_heuristics import heuristic_solomon_i1_style, ProblemContext
from backend.models.routing.heuristics import Route

class OptimizationResult:
    """Encapsula los resultados de la optimización."""

    def __init__(self, routes_csv: str, uncovered_csv: str, map_html: str,
                 stats: dict):
        self.routes_csv = routes_csv
        self.uncovered_csv = uncovered_csv
        self.map_html = map_html
        self.stats = stats


def _to_scalar(x) -> float:
    if isinstance(x, dict):
        vals = list(x.values())
        return float(sum(vals) / len(vals)) if vals else 0.0
    return float(x)


def run_optimization(params: OptimizerParams,
                     df_ventas: pd.DataFrame) -> OptimizationResult:
    """
    Ejecuta la optimización completa:
    1. Genera matrices desde datos reales
    2. Construye objeto VRPTWData
    3. Ejecuta heurística Solomon I1 Style (solución inicial)
    4. Mejora la solución con Tabu Search
    5. Genera CSV de rutas, CSV no cubiertos, mapa HTML
    """
    t_total_0 = time.perf_counter()

    # Resolver precio del combustible seleccionado usado por el modelo.
    params_payload = params.model_dump() if hasattr(params, "model_dump") else params.dict()
    fuel_meta: dict = {}
    fuel_type = normalize_fuel_type(str(params_payload.get("fuel_type", "diesel") or "diesel"))
    params_payload["fuel_type"] = fuel_type
    fuel_price_clp = _safe_float(params_payload.get("diesel_price_clp"), 0.0)
    if fuel_price_clp <= 0.0:
        try:
            fuel_price_clp, fuel_meta = fetch_fuel_price_clp(
                fuel_type,
                force_refresh=False,
                timeout_sec=8.0,
            )
        except Exception as fuel_exc:
            fuel_price_clp = 1600.0
            fuel_meta = {
                "source": "fallback_default",
                "fuel_type": fuel_type,
                "reason": str(fuel_exc),
            }
    params_payload["diesel_price_clp"] = float(fuel_price_clp)
    if _safe_float(params_payload.get("truck_fixed_cost_clp"), 0.0) <= 0.0:
        params_payload["truck_fixed_cost_clp"] = 20000.0
    params_effective = OptimizerParams(**params_payload)

    # 1. Generar matrices
    t_matrix_0 = time.perf_counter()
    (
        K,
        J,
        N,
        coords,
        p,
        v,
        T,
        P,
        V,
        c_fixed,
        g,
        o,
        d,
        t,
        max_route_time,
        node_info,
        df_invalid,
        routing_meta,
    ) = generate_matrices_from_df(df_ventas, params_effective)
    matrix_generation_sec = time.perf_counter() - t_matrix_0

    if not J:
        total_sec = time.perf_counter() - t_total_0
        return OptimizationResult(
            routes_csv="",
            uncovered_csv=build_uncovered_csv(df_invalid),
            map_html="<p>No hay puntos válidos para optimizar.</p>",
            stats={
                "total_puntos": 0,
                "cubiertos": 0,
                "no_cubiertos": len(df_invalid),
                "timing": {
                    "matrix_generation_sec": round(float(matrix_generation_sec), 4),
                    "model_preparation_sec": 0.0,
                    "solver_sec": 0.0,
                    "postprocess_sec": 0.0,
                    "output_generation_sec": 0.0,
                    "total_sec": round(float(total_sec), 4),
                },
            },
        )

    # 2. Adaptar al formato VRPTWData para Solomon I1 Style
    t_model_prep_0 = time.perf_counter()
    tw_open = {node: 0.0 for node in N}
    tw_close = {node: float(max_route_time) for node in N}
    
    vrp_data = VRPTWData(
        K=K,
        J=J,
        N=N,
        p=p,
        v=v,
        T=T,
        P=_to_scalar(P),
        V=_to_scalar(V),
        c_fixed=_to_scalar(c_fixed),
        g=float(g),
        o=_to_scalar(o),
        d=d,
        t=t,
        max_route_time=float(max_route_time),
        tw_open=tw_open,
        tw_close=tw_close,
        use_time_windows=False,
        depot=0,
    )
    model_preparation_sec = time.perf_counter() - t_model_prep_0

    # 3. Ejecutar Solomon I1 Style (solución inicial)
    t_solver_0 = time.perf_counter()
    seed = 42

    ctx = ProblemContext(data=vrp_data, coords=coords)
    sol_nodes, ev = heuristic_solomon_i1_style(ctx, seed=seed)
    solomon_sec = time.perf_counter() - t_solver_0

    # 4. Mejorar con Tabu Search (opcional)
    t_tabu_0 = time.perf_counter()
    use_tabu = bool(getattr(params_effective, "use_tabu_search", True))
    tabu_seconds = max(1.0, _safe_float(getattr(params_effective, "tabu_seconds", 20.0), 20.0))
    if use_tabu:
        sol_nodes, ev = tabu_search_vrptw(
            data=vrp_data,
            initial_solution=sol_nodes,
            tabu_config=TabuConfig(max_seconds=tabu_seconds),
            seed=seed,
        )
    tabu_sec = time.perf_counter() - t_tabu_0
    solver_sec = solomon_sec + tabu_sec

    # Adaptar la solucion de lista de listas a objetos Route para compatibilidad con el resto del pipeline
    t_post_0 = time.perf_counter()
    optimized_routes = []
    for r_nodes in sol_nodes:
        if len(r_nodes) > 2:
            # Calcular métricas básicas para compatibilidad
            load_p = sum(p[n] for n in r_nodes if n != 0)
            load_v = sum(v[n] for n in r_nodes if n != 0)
            dist = sum(d[r_nodes[i], r_nodes[i+1]] for i in range(len(r_nodes) - 1))
            travel_time = sum(t[r_nodes[i], r_nodes[i+1]] for i in range(len(r_nodes) - 1))
            service_time = sum(T[n] for n in r_nodes if n != 0)
            tot_time = travel_time + service_time
            route_obj = Route(r_nodes, load_p, load_v, dist, tot_time)
            optimized_routes.append(route_obj)

    # Identificar nodos asignados vs no asignados
    assigned_nodes = set()
    for route in optimized_routes:
        for node in route.nodes:
            if node != 0:
                assigned_nodes.add(node)

    unassigned_nodes = set(J) - assigned_nodes
    # Agregar nodos no asignados al df_invalid
    for node in unassigned_nodes:
        info = node_info.get(node, {})
        new_row = pd.DataFrame([{
            "RUT": info.get("RUT", ""),
            "Nombre cliente": info.get("Nombre cliente", ""),
            "Dirección cliente": info.get("Dirección cliente", ""),
            "Número de Orden": info.get("Número de Orden", ""),
            "Monto Pedido": "",
        }])
        df_invalid = pd.concat([df_invalid, new_row], ignore_index=True)
        # Actualizar Motivo
        df_invalid.iloc[-1, df_invalid.columns.get_loc("Motivo") if "Motivo" in df_invalid.columns else -1] = "Infactible (Capacidad/Tiempo)"
    postprocess_sec = time.perf_counter() - t_post_0

    # 4. Reasignación post-óptima de rutas a camiones físicos (múltiples viajes)
    shift_start_min, shift_end_min = _parse_worktime_window_minutes(params_effective.worktime_windows)
    shift_minutes = max(1.0, float(shift_end_min - shift_start_min))
    turnaround_between_trips_min = 60.0
    use_time_dependent_traffic = bool(getattr(params_effective, "use_time_dependent_traffic", True))
    traffic_context = _build_route_traffic_context(
        optimized_routes,
        node_info=node_info,
        T=T,
        t=t,
    )
    trip_plan, compression_meta = _compress_routes_postoptimal(
        optimized_routes,
        shift_minutes=shift_minutes,
        day_start_minutes=shift_start_min,
        turnaround_min=turnaround_between_trips_min,
        route_context=traffic_context,
        use_time_dependent_traffic=use_time_dependent_traffic,
    )
    trucks_used_post = len({int(x.truck_id) for x in trip_plan}) if trip_plan else len(optimized_routes)

    route_kpis = _build_route_kpis(
        optimized_routes,
        p=p,
        v=v,
        T=T,
        t=t,
        c_fixed=_to_scalar(c_fixed),
        g=float(g),
        o=float(o),
        trip_plan=trip_plan,
        turnaround_min=turnaround_between_trips_min,
    )
    fleet_cost_total = (
        _safe_float(
            (
                (route_kpis.get("fleet", {}) if isinstance(route_kpis, dict) else {})
                .get("cost_total", {})
                .get("total", 0.0)
            ),
            0.0,
        )
        if isinstance(route_kpis, dict)
        else 0.0
    )

    # 5. Generar outputs
    t_output_0 = time.perf_counter()
    map_use_osrm_polylines = bool(
        (routing_meta if isinstance(routing_meta, dict) else {}).get("osrm_used", False)
    )
    env_map_osrm = os.getenv("MAP_USE_OSRM_POLYLINES")
    if env_map_osrm is not None:
        map_use_osrm_polylines = str(env_map_osrm).strip().lower() not in {
            "",
            "0",
            "false",
            "no",
            "off",
        }

    map_polyline_timeout = 1.6 if map_use_osrm_polylines else 0.8
    routes_csv = build_routes_csv(
        optimized_routes,
        node_info,
        T,
        t,
        trip_plan=trip_plan,
        day_start_minutes=shift_start_min,
    )
    uncovered_csv = build_uncovered_csv(df_invalid)
    map_html, map_meta = generate_map_html(
        coords,
        optimized_routes,
        node_info,
        params.depot_address,
        T,
        t,
        trip_plan=trip_plan,
        day_start_minutes=shift_start_min,
        use_osrm_polylines=map_use_osrm_polylines,
        polyline_timeout_sec=map_polyline_timeout,
    )
    output_generation_sec = time.perf_counter() - t_output_0
    total_sec = time.perf_counter() - t_total_0

    stats = {
        "total_puntos": len(df_ventas),
        "cubiertos": len(assigned_nodes),
        "no_cubiertos": len(df_invalid),
        "camiones_usados": int(trucks_used_post),
        "rutas_totales": int(len(optimized_routes)),
        "factible": ev.feasible,
        "costo_base_heuristic": ev.cost_base,
        "costo_base": round(float(fleet_cost_total if fleet_cost_total > 0 else ev.cost_base), 4),
        "fuel_type": str(params_effective.fuel_type),
        "fuel_price_clp": round(_safe_float(params_effective.diesel_price_clp, 0.0), 4),
        "truck_fixed_cost_clp": round(_safe_float(params_effective.truck_fixed_cost_clp, 20000.0), 4),
        "diesel_price_clp": round(_safe_float(params_effective.diesel_price_clp, 0.0), 4),
        "fuel_price_meta": fuel_meta if isinstance(fuel_meta, dict) else {},
        "diesel_price_meta": fuel_meta if isinstance(fuel_meta, dict) else {},
        "route_kpis": route_kpis if isinstance(route_kpis, dict) else {},
        "postoptimal_compression": compression_meta if isinstance(compression_meta, dict) else {},
        "routing_matrix": routing_meta if isinstance(routing_meta, dict) else {},
        "routing_map": map_meta if isinstance(map_meta, dict) else {},
        "timing": {
            "matrix_generation_sec": round(float(matrix_generation_sec), 4),
            "model_preparation_sec": round(float(model_preparation_sec), 4),
            "solomon_sec": round(float(solomon_sec), 4),
            "tabu_search_sec": round(float(tabu_sec), 4),
            "solver_sec": round(float(solver_sec), 4),
            "postprocess_sec": round(float(postprocess_sec), 4),
            "output_generation_sec": round(float(output_generation_sec), 4),
            "total_sec": round(float(total_sec), 4),
        },
    }

    return OptimizationResult(
        routes_csv=routes_csv,
        uncovered_csv=uncovered_csv,
        map_html=map_html,
        stats=stats,
    )
