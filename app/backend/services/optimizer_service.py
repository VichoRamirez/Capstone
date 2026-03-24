"""
Servicio de optimización de rutas.

Recibe datos limpios (con coordenadas), genera matrices de distancia/tiempo
usando Haversine, ejecuta Clarke-Wright + ALNS, y produce:
  - CSV de rutas asignadas (camión → puntos)
  - CSV de puntos no cubiertos
  - Mapa HTML interactivo (folium)
"""

import csv
import io
import math
import os
import random
import tempfile
from datetime import datetime
from typing import Optional

import folium
import pandas as pd

from backend.models.Heuristica import (
    clarke_wright_initial_solution,
    alns,
    service_start_times,
    Route,
)
from backend.schemas import OptimizerParams


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


# ── Generador de matrices desde DataFrame real ────────────────────────────

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
        p[j] = float(row["Peso_total_pedido"]) if pd.notna(row.get("Peso_total_pedido")) else 100.0
        v[j] = float(row["Volumen_total_pedido"]) if pd.notna(row.get("Volumen_total_pedido")) else 1.0
        T[j] = 15  # Tiempo de servicio por defecto: 15 min

        node_info[j] = {
            "RUT": row.get("RUT", ""),
            "Nombre cliente": row.get("Nombre cliente", ""),
            "Dirección cliente": row.get("Dirección cliente", ""),
            "Comuna": row.get("Comuna", ""),
            "Número de Orden": row.get("Número de Orden", ""),
            "Latitud": row["Latitud"],
            "Longitud": row["Longitud"],
            "Monto Pedido": row.get("Monto Pedido", 0),
        }

    P = params.weight_per_truck or 2500
    V = params.space_per_truck or 10.0
    c_fixed = 120
    g = 1.3
    o = params.km_per_liter or 8.0
    
    # Dynamic worktime from "HH:MM-HH:MM"
    max_route_time = 300.0
    if params.worktime_windows:
        try:
            start_str, end_str = params.worktime_windows.split("-")
            h1, m1 = map(int, start_str.strip().split(":"))
            h2, m2 = map(int, end_str.strip().split(":"))
            max_route_time = float((h2 * 60 + m2) - (h1 * 60 + m1))
            if max_route_time <= 0: max_route_time = 300.0
        except:
            logger.warning(f"Could not parse worktime_windows: {params.worktime_windows}")
            max_route_time = 300.0

    # Matrices de distancia y tiempo
    d = {}
    t = {}
    u_speed = 30.0  # km/h promedio urbano Santiago

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
                t[i, j] = round(60.0 * dist / u_speed, 2)  # minutos

    return K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t, max_route_time, node_info, df_invalid


# ── Generador de mapa folium ──────────────────────────────────────────────

TRUCK_COLORS = [
    "blue", "red", "green", "purple", "orange", "darkred",
    "cadetblue", "darkgreen", "darkpurple", "pink", "gray",
    "lightblue", "lightgreen", "lightred", "beige", "black",
]


def generate_map_html(
    coords: dict,
    routes: list,
    node_info: dict,
    depot_address: list[float],
) -> str:
    """Genera un mapa HTML interactivo con folium mostrando las rutas."""
    center_lat, center_lon = depot_address
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles="OpenStreetMap")

    # Depósito
    folium.Marker(
        location=[center_lat, center_lon],
        popup="<b>Centro de Distribución</b>",
        icon=folium.Icon(color="black", icon="home", prefix="fa"),
    ).add_to(m)

    for truck_idx, route in enumerate(routes):
        color = TRUCK_COLORS[truck_idx % len(TRUCK_COLORS)]
        route_coords = []

        for node in route.nodes:
            if node == 0:
                route_coords.append([center_lat, center_lon])
            else:
                info = node_info.get(node, {})
                lat, lon = coords[node]
                route_coords.append([lat, lon])
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=6,
                    color=color,
                    fill=True,
                    fill_opacity=0.8,
                    popup=f"<b>Truck-{truck_idx + 1}</b><br>"
                          f"Orden: {info.get('Número de Orden', 'N/A')}<br>"
                          f"Dir: {info.get('Dirección cliente', 'N/A')}<br>"
                          f"Cliente: {info.get('Nombre cliente', 'N/A')}",
                ).add_to(m)

        # Línea de ruta
        if len(route_coords) > 1:
            folium.PolyLine(
                route_coords,
                color=color,
                weight=3,
                opacity=0.7,
                tooltip=f"Truck-{truck_idx + 1}",
            ).add_to(m)

    return m._repr_html_()


# ── Formateo de resultados ────────────────────────────────────────────────

def format_time(minutes_after_start: float, start_hour: int = 8) -> str:
    """Formatea minutos desde inicio de jornada como HH:MM."""
    h = int(start_hour + (minutes_after_start // 60))
    m = int(minutes_after_start % 60)
    return f"{h:02d}:{m:02d}"


def build_routes_csv(routes: list, node_info: dict, T: dict, t: dict,
                     depot_label: str = "Centro de Distribución") -> str:
    """Construye el CSV de rutas asignadas."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Camión", "Número de Orden", "RUT", "Nombre cliente",
        "Dirección cliente", "Comuna", "Hora estimada"
    ])

    for truck_idx, route in enumerate(routes):
        truck_name = f"Truck-{truck_idx + 1}"
        starts = service_start_times(route.nodes, T, t)

        # Depot salida
        writer.writerow([truck_name, "-", "-", "-", depot_label, "-", "08:00"])

        # Clientes
        for i in range(1, len(route.nodes) - 1):
            node = route.nodes[i]
            info = node_info.get(node, {})
            start_min = starts.get(node, 0)
            writer.writerow([
                truck_name,
                info.get("Número de Orden", ""),
                info.get("RUT", ""),
                info.get("Nombre cliente", ""),
                info.get("Dirección cliente", ""),
                info.get("Comuna", ""),
                format_time(start_min),
            ])

        # Depot regreso
        writer.writerow([truck_name, "-", "-", "-", depot_label, "-",
                         format_time(route.time)])

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

class OptimizationResult:
    """Encapsula los resultados de la optimización."""

    def __init__(self, routes_csv: str, uncovered_csv: str, map_html: str,
                 stats: dict):
        self.routes_csv = routes_csv
        self.uncovered_csv = uncovered_csv
        self.map_html = map_html
        self.stats = stats


def run_optimization(params: OptimizerParams,
                     df_ventas: pd.DataFrame) -> OptimizationResult:
    """
    Ejecuta la optimización completa:
    1. Genera matrices desde datos reales
    2. Clarke-Wright → solución inicial
    3. ALNS → mejora
    4. Genera CSV de rutas, CSV no cubiertos, mapa HTML
    """
    # 1. Generar matrices
    (K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t,
     max_route_time, node_info, df_invalid) = generate_matrices_from_df(df_ventas, params)

    if not J:
        return OptimizationResult(
            routes_csv="",
            uncovered_csv=build_uncovered_csv(df_invalid),
            map_html="<p>No hay puntos válidos para optimizar.</p>",
            stats={"total_puntos": 0, "cubiertos": 0, "no_cubiertos": len(df_invalid)},
        )

    # 2. Solución inicial
    initial_routes = clarke_wright_initial_solution(
        J=J, p=p, v=v, T=T, d=d, t=t,
        P=P, V=V, c_fixed=c_fixed, g=g, o=o,
        max_route_time=max_route_time,
        K_max=len(K),
    )

    # 3. Mejora ALNS
    time_limit = params.model_runtime if params.model_runtime is not None else 10.0
    optimized_routes = alns(
        routes=initial_routes,
        p=p, v=v, T=T, d=d, t=t,
        P=P, V=V, c_fixed=c_fixed, g=g, o=o,
        max_route_time=max_route_time,
        K_max=len(K),
        time_limit_sec=time_limit,
    )

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
            "Monto Pedido": info.get("Monto Pedido", ""),
        }])
        df_invalid = pd.concat([df_invalid, new_row], ignore_index=True)
        # Actualizar Motivo
        df_invalid.iloc[-1, df_invalid.columns.get_loc("Motivo") if "Motivo" in df_invalid.columns else -1] = "Capacidad insuficiente"

    # 4. Generar outputs
    routes_csv = build_routes_csv(optimized_routes, node_info, T, t)
    uncovered_csv = build_uncovered_csv(df_invalid)
    map_html = generate_map_html(coords, optimized_routes, node_info, params.depot_address)

    stats = {
        "total_puntos": len(df_ventas),
        "cubiertos": len(assigned_nodes),
        "no_cubiertos": len(df_invalid),
        "camiones_usados": len([r for r in optimized_routes if len(r.nodes) > 2]),
    }

    return OptimizationResult(
        routes_csv=routes_csv,
        uncovered_csv=uncovered_csv,
        map_html=map_html,
        stats=stats,
    )
