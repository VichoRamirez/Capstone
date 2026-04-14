"""
Metaheurísticas para VRP/VRPTW — núcleo del motor de optimización del proyecto.

Este módulo contiene toda la infraestructura de optimización combinatorial usada
en producción: estructuras de datos, evaluación de soluciones, operadores de
vecindad, búsqueda local y las tres metaheurísticas de mejora (AG, SA, Tabu Search).

La función principal en producción es ``tabu_search_vrptw``, invocada desde
``optimizer_service.py`` para mejorar la solución inicial construida por Solomon I1.

Supuestos y adaptación al proyecto:
- Se reutiliza ``generate_toy_data`` y ``clarke_wright_initial_solution`` de heuristics.py.
- El problema base es VRP con capacidad (peso + volumen) + duración máxima de ruta.
- Las ventanas de tiempo son opcionales (desactivadas por defecto para el problema base).
- Función de costo base: sum(c_fixed por ruta usada) + sum(distancia * g / o).
  Donde g = costo de combustible por km y o = rendimiento del camión (km/l o similar).
- No se usa Gurobi en este módulo; todo es heurístico.

Operadores de vecindad implementados:
- Relocate    : mover un cliente de una ruta a otra posición.
- Swap        : intercambiar dos clientes entre rutas (o dentro de la misma).
- 2-opt intra : revertir un segmento dentro de una misma ruta.
- 2-opt inter : intercambiar sufijos entre dos rutas distintas.
- Or-opt      : mover un segmento de 1, 2 o 3 clientes consecutivos a otra posición.
"""

from __future__ import annotations

import argparse
import math
import random
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backend.models.routing.heuristics import clarke_wright_initial_solution, generate_toy_data


# Tolerancia numérica para comparaciones de punto flotante
EPS = 1e-9
RouteNodes = List[int]         # Lista de nodos de una ruta (incluye depósito al inicio y al final)
SolutionRoutes = List[RouteNodes]  # Colección de rutas que forma una solución completa


@dataclass
class VRPTWData:
    """Datos completos del problema VRP/VRPTW.

    Contiene todos los parámetros del problema: flota, clientes, demandas,
    tiempos de servicio, distancias, tiempos de viaje, capacidades y ventanas
    de tiempo. Es la estructura central pasada a todos los algoritmos de este módulo.
    """

    K: List[int]               # Índices de los camiones disponibles
    J: List[int]               # Índices de los clientes a entregar
    N: List[int]               # Todos los nodos (depósito + clientes)
    p: Dict[int, float]        # Demanda de peso por cliente (kg)
    v: Dict[int, float]        # Demanda de volumen por cliente (m³)
    T: Dict[int, float]        # Tiempo de servicio en cada cliente (minutos)
    P: float                   # Capacidad máxima de peso por camión (kg)
    V: float                   # Capacidad máxima de volumen por camión (m³)
    c_fixed: float             # Costo fijo por ruta usada (apertura de camión)
    g: float                   # Costo de combustible por unidad de distancia
    o: float                   # Rendimiento del camión (km/l o equivalente)
    d: Dict[Tuple[int, int], float]  # Distancias entre pares de nodos (km)
    t: Dict[Tuple[int, int], float]  # Tiempos de viaje entre pares de nodos (min)
    max_route_time: float      # Duración máxima de jornada por camión (min)
    tw_open: Dict[int, float]  # Tiempo mínimo de llegada aceptado por cliente
    tw_close: Dict[int, float] # Tiempo máximo de llegada aceptado por cliente
    use_time_windows: bool = False  # Activar restricciones de ventana de tiempo
    depot: int = 0             # Nodo depósito (siempre 0 en este proyecto)

    @property
    def K_max(self) -> int:
        """Número máximo de camiones disponibles (límite de flota)."""
        return len(self.K)


@dataclass
class PenaltyConfig:
    """Coeficientes de penalización para restricciones blandas en la función objetivo.

    Permite controlar cuán "dura" es cada restricción. Valores muy altos (>1 000)
    hacen que la restricción sea efectivamente dura (ningún movimiento la viola en
    la práctica). Los valores por defecto son 0 para capacidad y tiempo (restricciones
    no penalizadas en la búsqueda local estándar) y 1 000 000 para visita y flota
    (se fuerza cobertura completa y respeto del límite de camiones).
    """

    cap_weight: float = 0.0         # Penalidad por kg sobre capacidad de peso
    cap_volume: float = 0.0         # Penalidad por m³ sobre capacidad volumétrica
    time_window: float = 0.0        # Penalidad por minuto fuera de ventana de tiempo
    route_duration: float = 0.0     # Penalidad por minuto de jornada excedida
    visit: float = 1_000_000.0      # Penalidad por cada cliente no visitado
    fleet: float = 1_000_000.0      # Penalidad por cada ruta sobre el límite K_max
    unknown_node: float = 1_000_000.0  # Penalidad por nodo desconocido en solución


@dataclass
class LocalSearchConfig:
    """Parámetros de control para la búsqueda local multi-operador."""

    max_passes: int = 8                  # Número máximo de pasadas completas sobre los operadores
    max_neighbors_per_operator: int = 900  # Vecinos evaluados por operador antes de parar
    first_improvement: bool = False       # Si True, acepta el primer vecino que mejora
    restart_from_first_operator_on_improve: bool = True  # Reinicia desde 2-opt al mejorar


@dataclass
class GAConfig:
    """Parámetros del Algoritmo Genético para VRP/VRPTW."""

    population_size: int = 24         # Tamaño de la población en cada generación
    generations: int = 40             # Número máximo de generaciones
    elite_size: int = 3               # Individuos élite que pasan directamente a la siguiente generación
    crossover_rate: float = 0.9       # Probabilidad de cruce entre dos padres
    mutation_rate: float = 0.4        # Probabilidad de mutar un individuo
    mutation_strength_min: int = 1    # Mínimo número de movimientos aleatorios en mutación
    mutation_strength_max: int = 3    # Máximo número de movimientos aleatorios en mutación
    tournament_size: int = 3          # Tamaño del torneo para selección de padres
    local_search_probability: float = 0.35  # Probabilidad de aplicar LS a cada hijo
    max_seconds: float = 25.0         # Presupuesto de tiempo total del AG


@dataclass
class SAConfig:
    """Parámetros del Recocido Simulado (Simulated Annealing) para VRP/VRPTW."""

    initial_temp: float = 250.0       # Temperatura inicial (controla aceptación de empeoramientos)
    cooling: float = 0.995            # Factor de enfriamiento geométrico por nivel de temperatura
    final_temp: float = 0.5           # Temperatura de parada del loop
    iters_per_temp: int = 70          # Iteraciones del loop interno por nivel de temperatura
    restart_stagnation: int = 350     # Iteraciones sin mejora antes de reiniciar desde la mejor solución
    local_search_every: int = 180     # Cada cuántas iteraciones aplicar búsqueda local
    max_seconds: float = 20.0         # Presupuesto de tiempo total del SA


@dataclass
class TabuConfig:
    """Parámetros del Tabu Search para VRP/VRPTW.

    El Tabu Search es la metaheurística principal usada en producción.
    Estos valores fueron calibrados en los casos de prueba del informe técnico.
    """

    iterations: int = 240             # Número máximo de iteraciones del loop principal
    neighborhood_size: int = 55       # Vecinos generados aleatoriamente por iteración
    tabu_tenure: int = 18             # Iteraciones que un movimiento permanece en la lista tabú
    diversification_gap: int = 45     # Iteraciones sin mejora antes de aplicar diversificación
    intensify_every: int = 35         # Cada cuántas iteraciones aplicar búsqueda local (intensificación)
    max_seconds: float = 20.0         # Presupuesto de tiempo máximo (por día de optimización)


@dataclass
class RouteEvaluation:
    """Resultado de evaluar una ruta individual: cargas, distancias, tiempos y violaciones."""

    route: List[int]               # Secuencia de nodos de la ruta (con depósito al inicio y final)
    load_p: float                  # Carga total de peso acumulada en la ruta (kg)
    load_v: float                  # Carga total de volumen acumulada en la ruta (m³)
    distance: float                # Distancia total recorrida (km)
    travel_time: float             # Tiempo total de conducción (min)
    service_time: float            # Tiempo total de atención a clientes (min)
    wait_time: float               # Tiempo total de espera por ventanas de tiempo (min)
    route_time: float              # Duración total de la ruta (travel + service + wait)
    cap_violation_p: float         # Exceso de peso sobre la capacidad del camión (kg)
    cap_violation_v: float         # Exceso de volumen sobre la capacidad del camión (m³)
    tw_violation: float            # Violación acumulada de ventanas de tiempo (min)
    duration_violation: float      # Exceso de duración sobre la jornada máxima (min)
    feasible: bool                 # True si la ruta no viola ninguna restricción
    # (cliente, llegada, inicio_servicio, espera, tardanza)
    visit_sequence: List[Tuple[int, float, float, float, float]]


@dataclass
class SolutionEvaluation:
    """Resultado de evaluar una solución completa: métricas agregadas y penalizaciones."""

    routes: List[RouteEvaluation]      # Evaluaciones individuales de cada ruta
    route_count: int                   # Número de rutas usadas (camiones activos)
    total_distance: float              # Distancia total de todas las rutas (km)
    total_travel_time: float           # Tiempo total de conducción (min)
    total_service_time: float          # Tiempo total de atención a clientes (min)
    total_wait_time: float             # Tiempo total de espera (min)
    total_route_time: float            # Suma de duraciones de todas las rutas (min)
    cost_base: float                   # Costo base: c_fixed*rutas + distancia*g/o
    cap_violation: float               # Violación total de capacidad (peso + volumen)
    tw_violation: float                # Violación total de ventanas de tiempo (min)
    duration_violation: float          # Violación total de duración de jornada (min)
    fleet_violation: float             # Rutas en exceso sobre el límite K_max
    visit_violation: float             # Clientes no visitados + duplicados
    unknown_node_violation: float      # Nodos desconocidos en la solución
    total_penalty: float               # Penalidad total ponderada
    penalized_objective: float         # cost_base + total_penalty (función objetivo)
    feasible: bool                     # True si toda la solución es factible
    missing_customers: List[int]       # Clientes que no aparecen en ninguna ruta
    duplicate_customers: List[int]     # Clientes que aparecen en más de una ruta
    arrival_by_customer: Dict[int, float]   # Tiempo de llegada por cliente
    start_by_customer: Dict[int, float]     # Inicio de servicio por cliente
    wait_by_customer: Dict[int, float]      # Tiempo de espera por cliente
    lateness_by_customer: Dict[int, float]  # Tardanza por cliente (0 si a tiempo)


def _to_scalar(value, name: str) -> float:
    """Convierte un valor o diccionario heterogéneo en un escalar float.

    Usado para normalizar parámetros de flota que pueden venir como
    diccionarios por tipo de camión (e.g., capacidad diferenciada por vehículo).
    Devuelve el promedio si es dict, o el valor directo si ya es numérico.
    """
    if isinstance(value, dict):
        if not value:
            raise ValueError(f"{name} no puede ser dict vacio.")
        vals = list(value.values())
        avg = sum(float(x) for x in vals) / len(vals)
        return float(avg)
    return float(value)


def build_synthetic_time_windows(
    J: Sequence[int],
    t: Dict[Tuple[int, int], float],
    T: Dict[int, float],
    max_route_time: float,
    seed: int = 777,
    width_ratio: float = 0.35,
    shift_ratio: float = 0.70,
) -> Tuple[Dict[int, float], Dict[int, float]]:
    """Genera ventanas de tiempo sintéticas [a_i, b_i] coherentes con la jornada máxima.

    Para cada cliente j, calcula la ventana más amplia posible compatible con la
    duración máxima de ruta y luego la restringe con width_ratio y shift_ratio para
    generar instancias de distinta dificultad. Garantiza a_i <= b_i en todo caso.

    Parámetros:
        width_ratio  -- fracción del slack disponible que se usa como ancho de ventana.
        shift_ratio  -- fracción máxima del slack que puede desplazar la ventana.
    """
    rng = random.Random(seed)
    width_ratio = max(0.05, min(1.0, width_ratio))
    shift_ratio = max(0.0, min(1.0, shift_ratio))

    tw_open = {0: 0.0}
    tw_close = {0: float(max_route_time)}

    for j in J:
        earliest = t[0, j]
        latest = max(earliest, max_route_time - T[j] - t[j, 0])
        slack = max(0.0, latest - earliest)

        if slack <= EPS:
            a_j = earliest
            b_j = latest
        else:
            win = max(12.0, slack * width_ratio)
            win = min(win, slack)
            max_shift = max(0.0, slack - win)
            shift = rng.uniform(0.0, max_shift * shift_ratio) if max_shift > EPS else 0.0
            a_j = earliest + shift
            b_j = min(latest, a_j + win)
            if b_j < a_j:
                b_j = a_j

        tw_open[j] = round(a_j, 2)
        tw_close[j] = round(b_j, 2)

    return tw_open, tw_close


def load_project_vrptw_data(
    n_customers: int = 150,
    n_trucks: int = 15,
    seed: int = 42,
    tw_seed: int = 777,
    tw_width_ratio: float = 0.35,
    tw_shift_ratio: float = 0.70,
    use_time_windows: bool = False,
) -> VRPTWData:
    """Carga y construye el VRPTWData usando el generador de datos del proyecto.

    Reutiliza el generador del repo (Heuristica.py) y agrega ventanas de tiempo
    sintéticas opcionales para experimentación. Por defecto, ventanas abiertas
    (todo el horizonte de jornada) para reproducir el problema base del proyecto.
    """
    K, J, N, _coords, p, v, T, P, V, c_fixed, g, o, d, t, max_rt = generate_toy_data(
        n_customers=n_customers, n_trucks=n_trucks, seed=seed
    )

    if use_time_windows:
        tw_open, tw_close = build_synthetic_time_windows(
            J=J,
            t=t,
            T=T,
            max_route_time=max_rt,
            seed=tw_seed,
            width_ratio=tw_width_ratio,
            shift_ratio=tw_shift_ratio,
        )
    else:
        tw_open = {0: 0.0}
        tw_close = {0: float(max_rt)}
        for j in J:
            tw_open[j] = 0.0
            tw_close[j] = float(max_rt)

    return VRPTWData(
        K=K,
        J=J,
        N=N,
        p=p,
        v=v,
        T=T,
        P=_to_scalar(P, "P"),
        V=_to_scalar(V, "V"),
        c_fixed=_to_scalar(c_fixed, "c_fixed"),
        g=float(g),
        o=_to_scalar(o, "o"),
        d=d,
        t=t,
        max_route_time=float(max_rt),
        tw_open=tw_open,
        tw_close=tw_close,
        use_time_windows=use_time_windows,
        depot=0,
    )


def normalize_route(route: Sequence[int], depot: int = 0) -> RouteNodes:
    """Normaliza una ruta garantizando que empiece y termine con el depósito.

    Elimina bloques consecutivos de depósito (artefactos de operadores de vecindad)
    y asegura que la ruta tenga la forma canónica [depot, c1, c2, ..., cn, depot].
    """
    if not route:
        return [depot, depot]

    nodes = list(route)
    if nodes[0] != depot:
        nodes = [depot] + nodes
    if nodes[-1] != depot:
        nodes = nodes + [depot]

    # Eliminar bloques accidentales de depósito consecutivo (artefactos de operadores)
    cleaned = [nodes[0]]
    for node in nodes[1:]:
        if node == depot and cleaned[-1] == depot:
            continue
        cleaned.append(node)

    if cleaned[-1] != depot:
        cleaned.append(depot)
    if cleaned[0] != depot:
        cleaned.insert(0, depot)
    return cleaned


def sanitize_solution(routes: Sequence[Sequence[int]], depot: int = 0) -> SolutionRoutes:
    """Normaliza todas las rutas de una solución y elimina las rutas vacías.

    Filtra rutas que solo contienen el depósito (longitud <= 2) pues no
    aportan clientes y generarían costos fijos innecesarios.
    """
    out: SolutionRoutes = []
    for route in routes:
        nr = normalize_route(route, depot=depot)
        if len(nr) > 2:
            out.append(nr)
    return out


def clone_solution(routes: Sequence[Sequence[int]]) -> SolutionRoutes:
    """Crea una copia profunda de la solución para modificarla sin afectar el original."""
    return [list(route) for route in routes]


def _route_penalty_score(
    r_eval: RouteEvaluation,
    penalties: PenaltyConfig,
) -> float:
    """Calcula la penalidad de una ruta por violaciones de restricciones blandas.

    Combina las violaciones de capacidad (peso y volumen), ventanas de tiempo y
    duración máxima de ruta, cada una multiplicada por su coeficiente de penalidad.
    No incluye penalidades de flota ni de clientes no visitados (son a nivel solución).
    """
    return (
        penalties.cap_weight * r_eval.cap_violation_p
        + penalties.cap_volume * r_eval.cap_violation_v
        + penalties.time_window * r_eval.tw_violation
        + penalties.route_duration * r_eval.duration_violation
    )


def _route_cost_component(
    r_eval: RouteEvaluation,
    data: VRPTWData,
    penalties: PenaltyConfig,
    include_fixed_cost: bool = False,
) -> float:
    """Calcula el costo total de una ruta incluyendo combustible y penalidades.

    El costo de combustible sigue la convención del proyecto: distancia * g / o,
    donde g es el precio del combustible y o el rendimiento del camión.
    """
    score = r_eval.distance * data.g / max(EPS, data.o) + _route_penalty_score(r_eval, penalties)
    if include_fixed_cost:
        score += data.c_fixed
    return score


def evaluate_route(route: Sequence[int], data: VRPTWData) -> RouteEvaluation:
    """Evalúa una ruta individual calculando cargas, tiempos, distancias y violaciones.

    Simula el recorrido del camión desde el depósito visitando cada cliente en orden
    y regresando al depósito. Para cada cliente registra la llegada, espera (si hay
    ventana de tiempo), inicio de servicio y tardanza. Calcula si la ruta es factible
    respecto a capacidad de peso, capacidad de volumen, ventanas de tiempo y duración.
    """
    route_n = normalize_route(route, depot=data.depot)

    load_p = 0.0
    load_v = 0.0
    distance = 0.0
    travel_time = 0.0
    service_time = 0.0
    wait_time = 0.0
    tw_violation = 0.0
    visit_sequence: List[Tuple[int, float, float, float, float]] = []

    current_time = 0.0
    for idx in range(len(route_n) - 1):
        i = route_n[idx]
        j = route_n[idx + 1]

        dist_ij = data.d[i, j]
        t_ij = data.t[i, j]
        distance += dist_ij
        travel_time += t_ij

        arrival = current_time + t_ij
        if j == data.depot:
            # Al llegar al depósito solo se acumula el tiempo de regreso
            current_time = arrival
            continue

        if data.use_time_windows:
            tw_open = data.tw_open.get(j, 0.0)
            tw_close = data.tw_close.get(j, data.max_route_time)
            # Espera si se llega antes de que abra la ventana
            wait = max(0.0, tw_open - arrival)
            start = arrival + wait
            # Tardanza si se empieza a servir después del cierre de ventana
            late = max(0.0, start - tw_close)
        else:
            # Sin ventanas de tiempo: no hay espera ni tardanza
            wait = 0.0
            start = arrival
            late = 0.0

        wait_time += wait
        tw_violation += late
        service = data.T[j]
        service_time += service

        load_p += data.p[j]
        load_v += data.v[j]
        current_time = start + service

        visit_sequence.append((j, arrival, start, wait, late))

    route_time = current_time
    # Exceso de capacidad sobre el límite del camión (0 si no hay violación)
    cap_violation_p = max(0.0, load_p - data.P)
    cap_violation_v = max(0.0, load_v - data.V)
    # Exceso de tiempo de ruta sobre la jornada máxima permitida
    duration_violation = max(0.0, route_time - data.max_route_time)

    # La ruta es factible si no hay ninguna violación en ninguna restricción
    feasible = (
        cap_violation_p <= EPS
        and cap_violation_v <= EPS
        and tw_violation <= EPS
        and duration_violation <= EPS
    )

    return RouteEvaluation(
        route=route_n,
        load_p=load_p,
        load_v=load_v,
        distance=distance,
        travel_time=travel_time,
        service_time=service_time,
        wait_time=wait_time,
        route_time=route_time,
        cap_violation_p=cap_violation_p,
        cap_violation_v=cap_violation_v,
        tw_violation=tw_violation,
        duration_violation=duration_violation,
        feasible=feasible,
        visit_sequence=visit_sequence,
    )


def evaluate_solution(
    routes: Sequence[Sequence[int]],
    data: VRPTWData,
    penalties: Optional[PenaltyConfig] = None,
) -> SolutionEvaluation:
    """Evalúa una solución completa agregando las métricas de todas las rutas.

    Calcula el costo base (costos fijos + combustible), penalidades por violaciones
    (capacidad, tiempo, flota, visita) y verifica factibilidad global. También
    detecta clientes duplicados, faltantes y nodos desconocidos en la solución.
    """
    penalties = penalties or PenaltyConfig()
    routes_n = sanitize_solution(routes, depot=data.depot)

    route_evals: List[RouteEvaluation] = []
    total_distance = 0.0
    total_travel = 0.0
    total_service = 0.0
    total_wait = 0.0
    total_route_time = 0.0
    cap_violation = 0.0
    tw_violation = 0.0
    duration_violation = 0.0

    visit_count = {j: 0 for j in data.J}
    unknown_node_count = 0.0
    arrival_by_customer: Dict[int, float] = {}
    start_by_customer: Dict[int, float] = {}
    wait_by_customer: Dict[int, float] = {}
    lateness_by_customer: Dict[int, float] = {}

    for route in routes_n:
        r_eval = evaluate_route(route, data)
        route_evals.append(r_eval)

        total_distance += r_eval.distance
        total_travel += r_eval.travel_time
        total_service += r_eval.service_time
        total_wait += r_eval.wait_time
        total_route_time += r_eval.route_time
        cap_violation += r_eval.cap_violation_p + r_eval.cap_violation_v
        tw_violation += r_eval.tw_violation
        duration_violation += r_eval.duration_violation

        for cust, arr, start, wait, late in r_eval.visit_sequence:
            if cust in visit_count:
                visit_count[cust] += 1
                if cust not in arrival_by_customer:
                    arrival_by_customer[cust] = arr
                    start_by_customer[cust] = start
                    wait_by_customer[cust] = wait
                    lateness_by_customer[cust] = late
            else:
                unknown_node_count += 1.0

    missing = [j for j, c in visit_count.items() if c == 0]
    duplicate = [j for j, c in visit_count.items() if c > 1]
    # Cada cliente duplicado cuenta como una violación adicional (más allá de la primera visita)
    duplicate_count = sum(max(0, visit_count[j] - 1) for j in duplicate)
    visit_violation = float(len(missing) + duplicate_count)
    # Rutas sobre el límite de flota (penalizadas por fleet en PenaltyConfig)
    fleet_violation = float(max(0, len(routes_n) - data.K_max))

    # Costo base: cargo fijo por cada camión usado + costo de combustible proporcional a distancia
    base_cost = len(routes_n) * data.c_fixed + total_distance * data.g / max(EPS, data.o)
    total_penalty = (
        penalties.cap_weight * cap_violation
        + penalties.time_window * tw_violation
        + penalties.route_duration * duration_violation
        + penalties.visit * visit_violation
        + penalties.fleet * fleet_violation
        + penalties.unknown_node * unknown_node_count
    )
    penalized = base_cost + total_penalty

    feasible = (
        cap_violation <= EPS
        and tw_violation <= EPS
        and duration_violation <= EPS
        and visit_violation <= EPS
        and fleet_violation <= EPS
        and unknown_node_count <= EPS
    )

    return SolutionEvaluation(
        routes=route_evals,
        route_count=len(routes_n),
        total_distance=total_distance,
        total_travel_time=total_travel,
        total_service_time=total_service,
        total_wait_time=total_wait,
        total_route_time=total_route_time,
        cost_base=base_cost,
        cap_violation=cap_violation,
        tw_violation=tw_violation,
        duration_violation=duration_violation,
        fleet_violation=fleet_violation,
        visit_violation=visit_violation,
        unknown_node_violation=unknown_node_count,
        total_penalty=total_penalty,
        penalized_objective=penalized,
        feasible=feasible,
        missing_customers=missing,
        duplicate_customers=duplicate,
        arrival_by_customer=arrival_by_customer,
        start_by_customer=start_by_customer,
        wait_by_customer=wait_by_customer,
        lateness_by_customer=lateness_by_customer,
    )


def _extract_unique_customer_order(routes: Sequence[Sequence[int]], data: VRPTWData) -> List[int]:
    """Extrae la secuencia única de clientes de una solución (sin depósito, sin duplicados).

    Preserva el orden de visita de la solución existente y añade al final los
    clientes del problema que no estaban en ninguna ruta (para garantizar cobertura
    completa durante el repair). Filtra clientes desconocidos (no en data.p).
    """
    seen = set()
    order: List[int] = []
    for route in routes:
        for node in route:
            if node in seen or node == data.depot:
                continue
            if node in data.p:
                seen.add(node)
                order.append(node)
    # Agregar clientes faltantes al final para garantizar cobertura completa
    for j in data.J:
        if j not in seen:
            order.append(j)
    return order


def _best_insertion_for_customer(
    routes: SolutionRoutes,
    customer: int,
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
    require_feasible: bool,
) -> Optional[Tuple[int, int, RouteEvaluation]]:
    """Encuentra la posición de menor costo para insertar un cliente en cualquier ruta.

    Evalúa todas las posiciones de inserción en todas las rutas y retorna la tripleta
    (índice de ruta, posición, evaluación candidata) que minimiza el incremento de
    costo penalizado. Si require_feasible=True, solo considera inserciones que no
    violen ninguna restricción; de lo contrario acepta inserciones infactibles
    (con penalidad).
    """
    best: Optional[Tuple[int, int, RouteEvaluation, float]] = None

    for ridx, route in enumerate(routes):
        old_eval = evaluate_route(route, data)
        old_score = _route_cost_component(old_eval, data, penalties, include_fixed_cost=False)

        for pos in range(len(route) - 1):
            cand_route = route[: pos + 1] + [customer] + route[pos + 1 :]
            cand_eval = evaluate_route(cand_route, data)

            if require_feasible and not cand_eval.feasible:
                continue

            new_score = _route_cost_component(cand_eval, data, penalties, include_fixed_cost=False)
            noise = rng.uniform(0.0, 1e-6)
            delta = new_score - old_score + noise
            if best is None or delta < best[3]:
                best = (ridx, pos, cand_eval, delta)

    if best is None:
        return None
    return best[0], best[1], best[2]


def _insert_customer_in_place(routes: SolutionRoutes, ridx: int, pos: int, customer: int) -> None:
    """Inserta un cliente en la posición especificada de la ruta indicada (in-place)."""
    routes[ridx].insert(pos + 1, customer)


def _force_insert_least_damage(
    routes: SolutionRoutes,
    customer: int,
    data: VRPTWData,
    penalties: PenaltyConfig,
) -> None:
    """Inserta un cliente en la posición de menor incremento de costo, sin importar factibilidad.

    Usado como último recurso en el repair cuando no hay ninguna inserción factible
    disponible. Elige la posición que minimiza el daño (incremento de costo penalizado),
    aunque esto viole alguna restricción. Garantiza que el cliente quede en la solución.
    """
    if not routes:
        routes.append([data.depot, customer, data.depot])
        return

    best_r = 0
    best_p = 0
    best_delta = float("inf")
    for ridx, route in enumerate(routes):
        old_eval = evaluate_route(route, data)
        old_score = _route_cost_component(old_eval, data, penalties, include_fixed_cost=False)
        for pos in range(len(route) - 1):
            cand = route[: pos + 1] + [customer] + route[pos + 1 :]
            cand_eval = evaluate_route(cand, data)
            new_score = _route_cost_component(cand_eval, data, penalties, include_fixed_cost=False)
            delta = new_score - old_score
            if delta < best_delta:
                best_delta = delta
                best_r = ridx
                best_p = pos

    routes[best_r].insert(best_p + 1, customer)


def _greedy_construct_from_order(
    customer_order: Sequence[int],
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
) -> SolutionRoutes:
    """Construye una solución insertando clientes en el orden dado, de forma greedy factible.

    Para cada cliente en la secuencia: primero busca la mejor inserción factible en
    rutas existentes; si no hay, abre una ruta nueva (si la flota lo permite). Si
    ninguna opción factible existe, lanza RuntimeError — el llamador debe manejar esto
    con un fallback a inserción penalizada o shuffle de orden.
    """
    routes: SolutionRoutes = []

    for cust in customer_order:
        if not routes:
            routes.append([data.depot, cust, data.depot])
            continue

        best_feas = _best_insertion_for_customer(
            routes, cust, data, penalties, rng, require_feasible=True
        )
        if best_feas is not None:
            ridx, pos, _ = best_feas
            _insert_customer_in_place(routes, ridx, pos, cust)
            continue

        if len(routes) < data.K_max:
            singleton = [data.depot, cust, data.depot]
            if evaluate_route(singleton, data).feasible:
                routes.append(singleton)
                continue

        raise RuntimeError(
            f"No se pudo insertar cliente {cust} de forma factible con K={data.K_max}."
        )

    return sanitize_solution(routes, depot=data.depot)


def repair_solution_vrptw(
    routes: Sequence[Sequence[int]],
    data: VRPTWData,
    penalties: Optional[PenaltyConfig] = None,
    seed: int = 123,
) -> SolutionRoutes:
    """
    Repair robusto:
    1) fuerza unicidad de clientes
    2) reconstuye via insercion greedy factible primero
    3) permite insercion penalizada si no hay opcion estrictamente factible
    """
    penalties = penalties or PenaltyConfig()
    rng = random.Random(seed)
    order = _extract_unique_customer_order(routes, data)
    try:
        repaired = _greedy_construct_from_order(order, data, penalties, rng)
    except RuntimeError:
        fallback = sanitize_solution(routes, depot=data.depot)
        ev = evaluate_solution(fallback, data, penalties)
        if ev.feasible:
            return fallback
        rng.shuffle(order)
        try:
            repaired = _greedy_construct_from_order(order, data, penalties, rng)
        except RuntimeError:
            return fallback
    return repaired


def build_initial_solution_clarke_wright(
    data: VRPTWData,
    penalties: Optional[PenaltyConfig] = None,
    seed: int = 123,
    run_local_search: bool = True,
    ls_config: Optional[LocalSearchConfig] = None,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    penalties = penalties or PenaltyConfig()
    ls_config = ls_config or LocalSearchConfig()

    try:
        # Reuso directo de la constructiva Clarke-Wright del repo (sin ventanas de tiempo).
        cw_routes = clarke_wright_initial_solution(
            data.J,
            data.p,
            data.v,
            data.T,
            data.d,
            data.t,
            data.P,
            data.V,
            data.c_fixed,
            data.g,
            data.o,
            data.max_route_time,
            data.K_max,
        )
        seed_routes = [list(r.nodes) for r in cw_routes]
    except Exception:
        seed_routes = [[data.depot, j, data.depot] for j in data.J]

    repaired = repair_solution_vrptw(seed_routes, data, penalties=penalties, seed=seed)
    eval0 = evaluate_solution(repaired, data, penalties)

    if run_local_search:
        improved, eval_improved = local_search_vrptw(
            repaired,
            data,
            penalties=penalties,
            config=ls_config,
            seed=seed,
        )
        return improved, eval_improved

    return repaired, eval0


def apply_relocate(
    solution: SolutionRoutes,
    r_from: int,
    i_from: int,
    r_to: int,
    insert_after: int,
) -> Tuple[SolutionRoutes, int]:
    """Operador Relocate: mueve un cliente de una ruta a otra posición.

    Extrae el cliente en la posición i_from de la ruta r_from y lo inserta
    después de insert_after en la ruta r_to. El movimiento puede ser intra-ruta
    o inter-ruta. Retorna la nueva solución y el cliente movido.
    """
    cand = clone_solution(solution)
    cust = cand[r_from].pop(i_from)

    if r_from == r_to and insert_after >= i_from:
        insert_after -= 1

    cand[r_to].insert(insert_after + 1, cust)
    return sanitize_solution(cand), cust


def apply_swap(
    solution: SolutionRoutes,
    r1: int,
    i1: int,
    r2: int,
    i2: int,
) -> Tuple[SolutionRoutes, Tuple[int, int]]:
    """Operador Swap: intercambia dos clientes entre rutas (o dentro de la misma ruta).

    Intercambia el cliente en posición i1 de la ruta r1 con el cliente en posición i2
    de la ruta r2. Si r1 == r2 e i1 == i2, el movimiento no produce cambio.
    Retorna la nueva solución y el par (cliente_a, cliente_b) intercambiado.
    """
    cand = clone_solution(solution)
    a = cand[r1][i1]
    b = cand[r2][i2]
    cand[r1][i1], cand[r2][i2] = b, a
    return sanitize_solution(cand), (a, b)


def apply_two_opt_intra(solution: SolutionRoutes, ridx: int, i: int, k: int) -> SolutionRoutes:
    """Operador 2-opt intra-ruta: invierte el segmento [i, k] dentro de una misma ruta.

    Reordena los clientes entre las posiciones i y k en la ruta ridx revirtiendo
    su orden de visita. Elimina el cruce de arcos que ocurre cuando dos arcos de
    la misma ruta se intersectan geométricamente.
    """
    cand = clone_solution(solution)
    route = cand[ridx]
    cand[ridx] = route[:i] + list(reversed(route[i : k + 1])) + route[k + 1 :]
    return sanitize_solution(cand)


def apply_two_opt_inter(solution: SolutionRoutes, r1: int, cut1: int, r2: int, cut2: int) -> SolutionRoutes:
    """Operador 2-opt inter-ruta: intercambia los sufijos de dos rutas distintas.

    Corta la ruta r1 en cut1 y la ruta r2 en cut2, luego intercambia las colas.
    Permite reorganizar grandes bloques de clientes entre rutas de una sola operación,
    lo que puede reducir el número de rutas necesarias si un sufijo encaja mejor
    en la otra ruta.
    """
    cand = clone_solution(solution)
    route1 = cand[r1]
    route2 = cand[r2]

    tail1 = route1[cut1:-1]
    tail2 = route2[cut2:-1]

    cand[r1] = route1[:cut1] + tail2 + [route1[-1]]
    cand[r2] = route2[:cut2] + tail1 + [route2[-1]]
    return sanitize_solution(cand)


def apply_or_opt(
    solution: SolutionRoutes,
    r_from: int,
    start: int,
    length: int,
    r_to: int,
    insert_after: int,
) -> Tuple[SolutionRoutes, Tuple[int, ...]]:
    """Operador Or-opt: mueve un segmento contiguo de 1, 2 o 3 clientes a otra posición.

    Es una generalización del Relocate que permite mover grupos de clientes juntos,
    preservando su orden relativo. Esto es útil cuando un subgrupo de clientes tiene
    más sentido visitarse consecutivamente en otra ruta o en otra parte de la misma ruta.
    Retorna la nueva solución y la tupla con los clientes del segmento movido.
    """
    cand = clone_solution(solution)
    segment = cand[r_from][start : start + length]
    del cand[r_from][start : start + length]

    if r_from == r_to:
        if insert_after < start - 1:
            new_insert = insert_after
        elif insert_after > start + length - 1:
            new_insert = insert_after - length
        else:
            return sanitize_solution(solution), tuple(segment)
        cand[r_to][new_insert + 1 : new_insert + 1] = segment
    else:
        cand[r_to][insert_after + 1 : insert_after + 1] = segment

    return sanitize_solution(cand), tuple(segment)


def _iterate_shuffled(values: Iterable[int], rng: random.Random) -> List[int]:
    """Devuelve una lista mezclada aleatoriamente para explorar vecinos en orden aleatorio.

    El orden aleatorio de exploración evita sesgos hacia rutas/posiciones de índice bajo
    y ayuda a diversificar la búsqueda en cada pasada de los operadores de vecindad.
    """
    vals = list(values)
    rng.shuffle(vals)
    return vals


def _try_best_relocate(
    solution: SolutionRoutes,
    current_eval: SolutionEvaluation,
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
    max_neighbors: int,
    first_improvement: bool,
) -> Tuple[Optional[SolutionRoutes], Optional[SolutionEvaluation], Optional[Tuple]]:
    """Búsqueda del mejor movimiento Relocate en la vecindad de la solución actual.

    Explora sistemáticamente (en orden aleatorio) todos los posibles movimientos
    de tipo Relocate hasta encontrar el mejor que mejore el objetivo penalizado,
    o hasta alcanzar el límite max_neighbors. Solo considera vecinos factibles.
    Retorna (None, None, None) si ningún vecino supera la solución actual.
    """
    best_sol = None
    best_eval = None
    best_move = None
    checked = 0
    best_score = current_eval.penalized_objective
    route_ids = _iterate_shuffled(range(len(solution)), rng)

    for r_from in route_ids:
        route_from = solution[r_from]
        if len(route_from) <= 3 and len(solution) == 1:
            continue
        for i_from in _iterate_shuffled(range(1, len(route_from) - 1), rng):
            for r_to in route_ids:
                route_to = solution[r_to]
                for ins_after in _iterate_shuffled(range(0, len(route_to) - 1), rng):
                    if r_from == r_to and (ins_after == i_from or ins_after == i_from - 1):
                        continue
                    cand, moved = apply_relocate(solution, r_from, i_from, r_to, ins_after)
                    checked += 1
                    cand_eval = evaluate_solution(cand, data, penalties)
                    if not cand_eval.feasible:
                        if checked >= max_neighbors:
                            return best_sol, best_eval, best_move
                        continue
                    score = cand_eval.penalized_objective
                    if score + EPS < best_score:
                        best_score = score
                        best_sol = cand
                        best_eval = cand_eval
                        best_move = ("relocate", moved, r_from, r_to)
                        if first_improvement:
                            return best_sol, best_eval, best_move
                    if checked >= max_neighbors:
                        return best_sol, best_eval, best_move
    return best_sol, best_eval, best_move


def _try_best_swap(
    solution: SolutionRoutes,
    current_eval: SolutionEvaluation,
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
    max_neighbors: int,
    first_improvement: bool,
) -> Tuple[Optional[SolutionRoutes], Optional[SolutionEvaluation], Optional[Tuple]]:
    """Búsqueda del mejor movimiento Swap en la vecindad de la solución actual.

    Análogo a _try_best_relocate pero para el operador Swap (intercambio de clientes).
    Solo evalúa pares no redundantes (r1, r2) con r1 <= r2 para evitar duplicados.
    """
    best_sol = None
    best_eval = None
    best_move = None
    checked = 0
    best_score = current_eval.penalized_objective
    route_ids = _iterate_shuffled(range(len(solution)), rng)

    for pos_r1, r1 in enumerate(route_ids):
        for r2 in route_ids[pos_r1:]:
            route1 = solution[r1]
            route2 = solution[r2]
            for i1 in _iterate_shuffled(range(1, len(route1) - 1), rng):
                for i2 in _iterate_shuffled(range(1, len(route2) - 1), rng):
                    if r1 == r2 and i1 == i2:
                        continue
                    cand, swapped = apply_swap(solution, r1, i1, r2, i2)
                    checked += 1
                    cand_eval = evaluate_solution(cand, data, penalties)
                    if not cand_eval.feasible:
                        if checked >= max_neighbors:
                            return best_sol, best_eval, best_move
                        continue
                    score = cand_eval.penalized_objective
                    if score + EPS < best_score:
                        best_score = score
                        best_sol = cand
                        best_eval = cand_eval
                        best_move = ("swap", min(swapped), max(swapped))
                        if first_improvement:
                            return best_sol, best_eval, best_move
                    if checked >= max_neighbors:
                        return best_sol, best_eval, best_move
    return best_sol, best_eval, best_move


def _try_best_two_opt_intra(
    solution: SolutionRoutes,
    current_eval: SolutionEvaluation,
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
    max_neighbors: int,
    first_improvement: bool,
) -> Tuple[Optional[SolutionRoutes], Optional[SolutionEvaluation], Optional[Tuple]]:
    """Búsqueda del mejor movimiento 2-opt intra-ruta en la vecindad actual.

    Solo aplica a rutas con al menos 5 nodos (depósito + 3 clientes + depósito)
    porque con menos clientes no hay par (i, k) con i < k válido para invertir.
    """
    best_sol = None
    best_eval = None
    best_move = None
    checked = 0
    best_score = current_eval.penalized_objective
    route_ids = _iterate_shuffled(range(len(solution)), rng)

    for ridx in route_ids:
        route = solution[ridx]
        if len(route) < 5:  # Mínimo 3 clientes para que haya un par (i,k) invertible
            continue
        for i in _iterate_shuffled(range(1, len(route) - 2), rng):
            for k in _iterate_shuffled(range(i + 1, len(route) - 1), rng):
                cand = apply_two_opt_intra(solution, ridx, i, k)
                checked += 1
                cand_eval = evaluate_solution(cand, data, penalties)
                if not cand_eval.feasible:
                    if checked >= max_neighbors:
                        return best_sol, best_eval, best_move
                    continue
                score = cand_eval.penalized_objective
                if score + EPS < best_score:
                    best_score = score
                    best_sol = cand
                    best_eval = cand_eval
                    best_move = ("2opt_intra", ridx, i, k)
                    if first_improvement:
                        return best_sol, best_eval, best_move
                if checked >= max_neighbors:
                    return best_sol, best_eval, best_move
    return best_sol, best_eval, best_move


def _try_best_two_opt_inter(
    solution: SolutionRoutes,
    current_eval: SolutionEvaluation,
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
    max_neighbors: int,
    first_improvement: bool,
) -> Tuple[Optional[SolutionRoutes], Optional[SolutionEvaluation], Optional[Tuple]]:
    """Búsqueda del mejor movimiento 2-opt inter-ruta en la vecindad actual.

    Requiere al menos dos rutas con al menos 4 nodos cada una para que el
    intercambio de sufijos produzca rutas distintas y no vacías.
    """
    best_sol = None
    best_eval = None
    best_move = None
    checked = 0
    best_score = current_eval.penalized_objective
    route_ids = _iterate_shuffled(range(len(solution)), rng)

    for p1, r1 in enumerate(route_ids):
        route1 = solution[r1]
        if len(route1) < 4:
            continue
        for r2 in route_ids[p1 + 1 :]:
            route2 = solution[r2]
            if len(route2) < 4:
                continue
            cuts1 = _iterate_shuffled(range(1, len(route1) - 1), rng)
            cuts2 = _iterate_shuffled(range(1, len(route2) - 1), rng)
            for cut1 in cuts1:
                for cut2 in cuts2:
                    cand = apply_two_opt_inter(solution, r1, cut1, r2, cut2)
                    checked += 1
                    cand_eval = evaluate_solution(cand, data, penalties)
                    if not cand_eval.feasible:
                        if checked >= max_neighbors:
                            return best_sol, best_eval, best_move
                        continue
                    score = cand_eval.penalized_objective
                    if score + EPS < best_score:
                        best_score = score
                        best_sol = cand
                        best_eval = cand_eval
                        best_move = ("2opt_inter", r1, r2, cut1, cut2)
                        if first_improvement:
                            return best_sol, best_eval, best_move
                    if checked >= max_neighbors:
                        return best_sol, best_eval, best_move
    return best_sol, best_eval, best_move


def _try_best_or_opt(
    solution: SolutionRoutes,
    current_eval: SolutionEvaluation,
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
    max_neighbors: int,
    first_improvement: bool,
) -> Tuple[Optional[SolutionRoutes], Optional[SolutionEvaluation], Optional[Tuple]]:
    """Búsqueda del mejor movimiento Or-opt en la vecindad actual.

    Prueba segmentos de longitud 1, 2 y 3 clientes consecutivos. Es el operador
    de mayor riqueza de vecindad en la búsqueda local: cubre Relocate (chain_len=1)
    y movimientos de cadena que los otros operadores no exploran.
    """
    best_sol = None
    best_eval = None
    best_move = None
    checked = 0
    best_score = current_eval.penalized_objective
    route_ids = _iterate_shuffled(range(len(solution)), rng)

    for r_from in route_ids:
        route_from = solution[r_from]
        max_chain = min(3, len(route_from) - 2)
        if max_chain <= 0:
            continue

        for chain_len in range(1, max_chain + 1):
            starts = _iterate_shuffled(range(1, len(route_from) - chain_len), rng)
            for start in starts:
                for r_to in route_ids:
                    route_to = solution[r_to]
                    for insert_after in _iterate_shuffled(range(0, len(route_to) - 1), rng):
                        if r_from == r_to:
                            if insert_after >= start - 1 and insert_after <= start + chain_len - 1:
                                continue
                        cand, seg = apply_or_opt(solution, r_from, start, chain_len, r_to, insert_after)
                        checked += 1
                        cand_eval = evaluate_solution(cand, data, penalties)
                        if not cand_eval.feasible:
                            if checked >= max_neighbors:
                                return best_sol, best_eval, best_move
                            continue
                        score = cand_eval.penalized_objective
                        if score + EPS < best_score:
                            best_score = score
                            best_sol = cand
                            best_eval = cand_eval
                            best_move = ("or_opt", seg, r_from, r_to)
                            if first_improvement:
                                return best_sol, best_eval, best_move
                        if checked >= max_neighbors:
                            return best_sol, best_eval, best_move
    return best_sol, best_eval, best_move


def local_search_vrptw(
    solution: Sequence[Sequence[int]],
    data: VRPTWData,
    penalties: Optional[PenaltyConfig] = None,
    config: Optional[LocalSearchConfig] = None,
    seed: int = 123,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Búsqueda local multi-operador para mejorar una solución VRP/VRPTW.

    Aplica iterativamente cinco operadores de vecindad en orden: 2-opt intra,
    Relocate, Swap, Or-opt y 2-opt inter. Si algún operador mejora la solución,
    reinicia desde el primer operador (estrategia de reinicio). El proceso
    termina cuando ningún operador produce mejora en una pasada completa,
    o cuando se alcanza el límite de pasadas (config.max_passes).

    Si la solución de entrada no es factible, primero se repara antes de buscar.
    """
    penalties = penalties or PenaltyConfig()
    config = config or LocalSearchConfig()
    rng = random.Random(seed)

    current = sanitize_solution(solution, depot=data.depot)
    current_eval = evaluate_solution(current, data, penalties)
    if not current_eval.feasible:
        # Reparar antes de buscar: la búsqueda local solo opera sobre soluciones factibles
        current = repair_solution_vrptw(current, data, penalties=penalties, seed=seed)
        current_eval = evaluate_solution(current, data, penalties)

    # Orden de operadores: primero los más rápidos, luego los de mayor vecindad
    operators = [
        _try_best_two_opt_intra,
        _try_best_relocate,
        _try_best_swap,
        _try_best_or_opt,
        _try_best_two_opt_inter,
    ]

    for _ in range(config.max_passes):
        improved = False
        for op in operators:
            cand, cand_eval, _move = op(
                current,
                current_eval,
                data,
                penalties,
                rng,
                config.max_neighbors_per_operator,
                config.first_improvement,
            )
            if cand is not None and cand_eval is not None:
                if cand_eval.penalized_objective + EPS < current_eval.penalized_objective:
                    current = cand
                    current_eval = cand_eval
                    improved = True
                    if config.restart_from_first_operator_on_improve:
                        break
        if not improved:
            break

    return current, current_eval


def _random_relocate(solution: SolutionRoutes, rng: random.Random) -> Optional[Tuple[SolutionRoutes, Tuple]]:
    if not solution:
        return None

    r_from = rng.randrange(len(solution))
    if len(solution[r_from]) <= 2:
        return None
    if len(solution[r_from]) <= 3 and len(solution) == 1:
        return None

    i_from = rng.randrange(1, len(solution[r_from]) - 1)
    r_to = rng.randrange(len(solution))
    insert_after = rng.randrange(0, len(solution[r_to]) - 1)
    if r_from == r_to and (insert_after == i_from or insert_after == i_from - 1):
        return None

    cand, cust = apply_relocate(solution, r_from, i_from, r_to, insert_after)
    return cand, ("relocate", cust, r_to)


def _random_swap(solution: SolutionRoutes, rng: random.Random) -> Optional[Tuple[SolutionRoutes, Tuple]]:
    if not solution:
        return None

    r1 = rng.randrange(len(solution))
    r2 = rng.randrange(len(solution))
    if len(solution[r1]) <= 2 or len(solution[r2]) <= 2:
        return None

    i1 = rng.randrange(1, len(solution[r1]) - 1)
    i2 = rng.randrange(1, len(solution[r2]) - 1)
    if r1 == r2 and i1 == i2:
        return None

    cand, swapped = apply_swap(solution, r1, i1, r2, i2)
    a, b = swapped
    return cand, ("swap", min(a, b), max(a, b))


def _random_two_opt_intra(solution: SolutionRoutes, rng: random.Random) -> Optional[Tuple[SolutionRoutes, Tuple]]:
    valid = [idx for idx, r in enumerate(solution) if len(r) >= 5]
    if not valid:
        return None
    ridx = rng.choice(valid)
    route = solution[ridx]
    i = rng.randrange(1, len(route) - 2)
    k = rng.randrange(i + 1, len(route) - 1)
    cand = apply_two_opt_intra(solution, ridx, i, k)
    return cand, ("2opt_intra", ridx, i, k)


def _random_two_opt_inter(solution: SolutionRoutes, rng: random.Random) -> Optional[Tuple[SolutionRoutes, Tuple]]:
    if len(solution) < 2:
        return None
    r1, r2 = rng.sample(range(len(solution)), 2)
    if len(solution[r1]) < 4 or len(solution[r2]) < 4:
        return None
    cut1 = rng.randrange(1, len(solution[r1]) - 1)
    cut2 = rng.randrange(1, len(solution[r2]) - 1)
    cand = apply_two_opt_inter(solution, r1, cut1, r2, cut2)
    return cand, ("2opt_inter", r1, r2, cut1, cut2)


def _random_or_opt(solution: SolutionRoutes, rng: random.Random) -> Optional[Tuple[SolutionRoutes, Tuple]]:
    if not solution:
        return None
    r_from = rng.randrange(len(solution))
    route_from = solution[r_from]
    max_chain = min(3, len(route_from) - 2)
    if max_chain <= 0:
        return None
    chain_len = rng.randint(1, max_chain)
    start = rng.randrange(1, len(route_from) - chain_len)
    r_to = rng.randrange(len(solution))
    insert_after = rng.randrange(0, len(solution[r_to]) - 1)
    if r_from == r_to and insert_after >= start - 1 and insert_after <= start + chain_len - 1:
        return None
    cand, seg = apply_or_opt(solution, r_from, start, chain_len, r_to, insert_after)
    return cand, ("or_opt", seg, r_to)


def random_neighbor(solution: SolutionRoutes, rng: random.Random) -> Tuple[SolutionRoutes, Tuple]:
    """Genera un vecino aleatorio aplicando un operador de vecindad al azar.

    Elige aleatoriamente entre los cinco operadores disponibles e intenta aplicarlo.
    Si el operador falla (por restricciones de tamaño de ruta) o no produce cambio,
    reintenta hasta 45 veces. Si no logra generar un vecino distinto, retorna un
    clon de la solución con el movimiento "none". Este diseño se usa en las
    metaheurísticas (Tabu Search, SA, AG) para explorar la vecindad de forma estocástica.
    """
    ops = [
        _random_relocate,
        _random_swap,
        _random_two_opt_intra,
        _random_two_opt_inter,
        _random_or_opt,
    ]
    for _ in range(45):
        op = rng.choice(ops)
        out = op(solution, rng)
        if out is None:
            continue
        cand, move = out
        if cand != solution:
            return cand, move
    # Si tras 45 intentos no se generó un vecino diferente, retornar sin cambio
    return clone_solution(solution), ("none",)


def perturb_solution(
    solution: Sequence[Sequence[int]],
    rng: random.Random,
    strength: int = 2,
) -> SolutionRoutes:
    """Perturba una solución aplicando movimientos aleatorios para escapar de mínimos locales.

    Aplica ``strength`` movimientos aleatorios consecutivos. Usado en Tabu Search y SA
    como mecanismo de diversificación cuando el algoritmo se estanca (no mejora por
    ``diversification_gap`` iteraciones). A mayor strength, mayor perturbación.
    """
    cand = sanitize_solution(solution)
    for _ in range(max(1, strength)):
        cand, _ = random_neighbor(cand, rng)
    return cand


def _complete_partial_solution(
    partial_routes: Sequence[Sequence[int]],
    customer_sequence: Sequence[int],
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
) -> SolutionRoutes:
    routes = sanitize_solution(partial_routes, depot=data.depot)
    used = set()
    cleaned_routes: SolutionRoutes = []
    for route in routes:
        nr = [data.depot]
        for node in route[1:-1]:
            if node == data.depot:
                continue
            if node in used:
                continue
            if node in data.p:
                used.add(node)
                nr.append(node)
        nr.append(data.depot)
        if len(nr) > 2:
            cleaned_routes.append(nr)
    routes = cleaned_routes

    sequence = [c for c in customer_sequence if c not in used and c in data.p]
    for c in data.J:
        if c not in used and c not in sequence:
            sequence.append(c)

    for cust in sequence:
        best_feas = _best_insertion_for_customer(routes, cust, data, penalties, rng, require_feasible=True)
        if best_feas is not None:
            ridx, pos, _ = best_feas
            routes[ridx].insert(pos + 1, cust)
            used.add(cust)
            continue

        if len(routes) < data.K_max:
            singleton = [data.depot, cust, data.depot]
            if evaluate_route(singleton, data).feasible:
                routes.append(singleton)
                used.add(cust)
                continue

        best_soft = _best_insertion_for_customer(routes, cust, data, penalties, rng, require_feasible=False)
        if best_soft is not None:
            ridx, pos, _ = best_soft
            routes[ridx].insert(pos + 1, cust)
            used.add(cust)
            continue

        if len(routes) < data.K_max:
            routes.append([data.depot, cust, data.depot])
        else:
            _force_insert_least_damage(routes, cust, data, penalties)
        used.add(cust)

    return sanitize_solution(routes, depot=data.depot)


def route_based_crossover(
    parent1: Sequence[Sequence[int]],
    parent2: Sequence[Sequence[int]],
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
) -> SolutionRoutes:
    """Cruce de rutas para el Algoritmo Genético (Route-Based Crossover).

    Hereda un subconjunto aleatorio de rutas del padre 1 (preservando la estructura
    de esas rutas), luego completa los clientes faltantes siguiendo el orden de
    visita del padre 2. Finalmente repara la solución hijo para garantizar cobertura
    completa. Esta estrategia combina las fortalezas de ambos padres preservando
    secuencias geográficamente coherentes.
    """
    p1 = sanitize_solution(parent1, depot=data.depot)
    p2 = sanitize_solution(parent2, depot=data.depot)

    if not p1:
        return repair_solution_vrptw(p2, data, penalties=penalties, seed=rng.randint(1, 10**6))
    if not p2:
        return repair_solution_vrptw(p1, data, penalties=penalties, seed=rng.randint(1, 10**6))

    keep_count = rng.randint(1, max(1, len(p1) // 2))
    keep_idx = set(rng.sample(range(len(p1)), min(keep_count, len(p1))))
    partial = [p1[i] for i in range(len(p1)) if i in keep_idx]

    used = set()
    for r in partial:
        for node in r:
            if node != data.depot:
                used.add(node)

    sequence = []
    for r in p2:
        for node in r:
            if node != data.depot and node not in used:
                sequence.append(node)
                used.add(node)

    child = _complete_partial_solution(partial, sequence, data, penalties, rng)
    child = repair_solution_vrptw(child, data, penalties=penalties, seed=rng.randint(1, 10**6))
    return child


def mutate_solution(
    solution: Sequence[Sequence[int]],
    data: VRPTWData,
    penalties: PenaltyConfig,
    rng: random.Random,
    strength: int = 2,
) -> SolutionRoutes:
    """Muta una solución aplicando movimientos aleatorios y luego reparando.

    Operador de mutación para el Algoritmo Genético. Aplica ``strength`` movimientos
    aleatorios para diversificar el individuo y luego repara para garantizar que
    el hijo sea una solución válida (cobertura completa, sin duplicados).
    """
    cand = sanitize_solution(solution, depot=data.depot)
    for _ in range(max(1, strength)):
        cand, _ = random_neighbor(cand, rng)
    cand = repair_solution_vrptw(cand, data, penalties=penalties, seed=rng.randint(1, 10**6))
    return cand


def _tournament_pick(
    scored_population: Sequence[Tuple[float, SolutionRoutes, SolutionEvaluation]],
    rng: random.Random,
    size: int,
) -> SolutionRoutes:
    """Selección por torneo: elige el mejor individuo de un subconjunto aleatorio.

    Selecciona aleatoriamente ``size`` individuos de la población y retorna el de
    menor costo. La presión selectiva aumenta con el tamaño del torneo: torneos
    grandes favorecen más a los mejores individuos.
    """
    sample = rng.sample(list(scored_population), min(size, len(scored_population)))
    sample.sort(key=lambda x: x[0])
    return clone_solution(sample[0][1])


def genetic_algorithm_vrptw(
    data: VRPTWData,
    initial_solution: Optional[Sequence[Sequence[int]]] = None,
    penalties: Optional[PenaltyConfig] = None,
    ga_config: Optional[GAConfig] = None,
    ls_config: Optional[LocalSearchConfig] = None,
    seed: int = 123,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Algoritmo Genético para mejorar soluciones VRP/VRPTW.

    Mantiene una población de soluciones que evoluciona mediante selección por torneo,
    cruce de rutas (route-based crossover) y mutación aleatoria. Los ``elite_size``
    mejores individuos se preservan directamente en cada generación (elitismo).
    Periódicamente aplica búsqueda local a los hijos para intensificar la explotación.

    El AG termina por número de generaciones o por límite de tiempo (ga_config.max_seconds).
    Una búsqueda local final refina la mejor solución encontrada.
    """
    penalties = penalties or PenaltyConfig()
    ga_config = ga_config or GAConfig()
    ls_config = ls_config or LocalSearchConfig(max_passes=3, max_neighbors_per_operator=350)
    rng = random.Random(seed)

    if initial_solution is None:
        initial_solution, _ = build_initial_solution_clarke_wright(
            data,
            penalties=penalties,
            seed=seed,
            run_local_search=True,
            ls_config=ls_config,
        )
    else:
        initial_solution = repair_solution_vrptw(initial_solution, data, penalties=penalties, seed=seed)

    base, base_eval = local_search_vrptw(
        initial_solution, data, penalties=penalties, config=ls_config, seed=seed
    )

    # Generar la población inicial: base + perturbaciones del base
    population: List[SolutionRoutes] = [base]
    while len(population) < ga_config.population_size:
        cand = perturb_solution(base, rng, strength=rng.randint(1, 3))
        cand = repair_solution_vrptw(cand, data, penalties=penalties, seed=rng.randint(1, 10**6))
        # Con probabilidad 55% aplicar LS al candidato inicial para diversidad de calidad
        if rng.random() < 0.55:
            cand, _ = local_search_vrptw(cand, data, penalties=penalties, config=ls_config, seed=rng.randint(1, 10**6))
        population.append(cand)

    best_solution = clone_solution(base)
    best_eval = base_eval
    t0 = time.time()

    for _gen in range(ga_config.generations):
        if time.time() - t0 > ga_config.max_seconds:
            break

        # Evaluar y ordenar la población: los infactibles van al final (costo=inf)
        scored: List[Tuple[float, SolutionRoutes, SolutionEvaluation]] = []
        for ind in population:
            ev = evaluate_solution(ind, data, penalties)
            score = ev.cost_base if ev.feasible else float("inf")
            scored.append((score, ind, ev))
        scored.sort(key=lambda x: x[0])

        # Actualizar mejor global si el mejor de esta generación supera el histórico
        if scored[0][2].feasible and scored[0][2].cost_base + EPS < best_eval.cost_base:
            best_solution = clone_solution(scored[0][1])
            best_eval = scored[0][2]

        new_population: List[SolutionRoutes] = []
        # Elitismo: los mejores pasan directamente a la siguiente generación sin modificación
        elites = min(ga_config.elite_size, len(scored))
        for i in range(elites):
            new_population.append(clone_solution(scored[i][1]))

        while len(new_population) < ga_config.population_size:
            p1 = _tournament_pick(scored, rng, ga_config.tournament_size)
            if rng.random() < ga_config.crossover_rate:
                p2 = _tournament_pick(scored, rng, ga_config.tournament_size)
                child = route_based_crossover(p1, p2, data, penalties, rng)
            else:
                child = clone_solution(p1)

            if rng.random() < ga_config.mutation_rate:
                strength = rng.randint(ga_config.mutation_strength_min, ga_config.mutation_strength_max)
                child = mutate_solution(child, data, penalties, rng, strength=strength)

            child = repair_solution_vrptw(child, data, penalties=penalties, seed=rng.randint(1, 10**6))
            if rng.random() < ga_config.local_search_probability:
                child, _ = local_search_vrptw(
                    child,
                    data,
                    penalties=penalties,
                    config=ls_config,
                    seed=rng.randint(1, 10**6),
                )
            new_population.append(child)

        population = new_population

    best_solution, best_eval = local_search_vrptw(
        best_solution,
        data,
        penalties=penalties,
        config=ls_config,
        seed=seed + 999,
    )
    return best_solution, best_eval


def simulated_annealing_vrptw(
    data: VRPTWData,
    initial_solution: Optional[Sequence[Sequence[int]]] = None,
    penalties: Optional[PenaltyConfig] = None,
    sa_config: Optional[SAConfig] = None,
    ls_config: Optional[LocalSearchConfig] = None,
    seed: int = 123,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Recocido Simulado (Simulated Annealing) para mejorar soluciones VRP/VRPTW.

    Explora la vecindad aceptando movimientos que empeoran el objetivo con probabilidad
    exp(-delta/T), donde delta es el incremento de costo y T la temperatura actual.
    La temperatura decrece geométricamente (cooling) permitiendo mayor exploración al
    inicio y convergiendo a búsqueda local hacia el final. Incluye reinicio desde la
    mejor solución tras ``restart_stagnation`` iteraciones sin mejora.

    El SA termina cuando T < final_temp o se agota max_seconds.
    Una búsqueda local final refina la mejor solución encontrada.
    """
    penalties = penalties or PenaltyConfig()
    sa_config = sa_config or SAConfig()
    ls_config = ls_config or LocalSearchConfig(max_passes=3, max_neighbors_per_operator=300)
    rng = random.Random(seed)

    if initial_solution is None:
        initial_solution, _ = build_initial_solution_clarke_wright(
            data,
            penalties=penalties,
            seed=seed,
            run_local_search=True,
            ls_config=ls_config,
        )
    else:
        initial_solution = repair_solution_vrptw(initial_solution, data, penalties=penalties, seed=seed)

    current, current_eval = local_search_vrptw(
        initial_solution, data, penalties=penalties, config=ls_config, seed=seed
    )
    best = clone_solution(current)
    best_eval = current_eval

    temp = sa_config.initial_temp
    iteration = 0
    no_improve = 0
    t0 = time.time()

    while temp > sa_config.final_temp and (time.time() - t0) < sa_config.max_seconds:
        for _ in range(sa_config.iters_per_temp):
            if (time.time() - t0) >= sa_config.max_seconds:
                break

            iteration += 1
            cand, _move = random_neighbor(current, rng)
            if cand == current:
                continue
            # Con probabilidad 20% reparar el vecino para mantener factibilidad
            if rng.random() < 0.20:
                cand = repair_solution_vrptw(cand, data, penalties=penalties, seed=rng.randint(1, 10**6))

            cand_eval = evaluate_solution(cand, data, penalties)
            if not cand_eval.feasible:
                continue
            delta = cand_eval.penalized_objective - current_eval.penalized_objective

            # Criterio de aceptación de Metropolis: siempre acepta mejoras,
            # acepta empeoramientos con probabilidad exp(-delta/T)
            accept = False
            if delta <= 0:
                accept = True
            else:
                prob = math.exp(-delta / max(EPS, temp))
                if rng.random() < prob:
                    accept = True

            if accept:
                current = cand
                current_eval = cand_eval

            if current_eval.penalized_objective + EPS < best_eval.penalized_objective:
                best = clone_solution(current)
                best_eval = current_eval
                no_improve = 0
            else:
                no_improve += 1

            # Intensificación periódica: aplicar LS completa cada cierto número de iteraciones
            if iteration % sa_config.local_search_every == 0:
                current, current_eval = local_search_vrptw(
                    current,
                    data,
                    penalties=penalties,
                    config=ls_config,
                    seed=rng.randint(1, 10**6),
                )
                if current_eval.penalized_objective + EPS < best_eval.penalized_objective:
                    best = clone_solution(current)
                    best_eval = current_eval
                    no_improve = 0

            if no_improve >= sa_config.restart_stagnation:
                # Diversificación: reiniciar desde la mejor solución con perturbación fuerte
                current = perturb_solution(best, rng, strength=5)
                current = repair_solution_vrptw(
                    current, data, penalties=penalties, seed=rng.randint(1, 10**6)
                )
                current_eval = evaluate_solution(current, data, penalties)
                no_improve = 0

        # Enfriar la temperatura al final de cada nivel de temperatura
        temp *= sa_config.cooling

    best, best_eval = local_search_vrptw(
        best,
        data,
        penalties=penalties,
        config=ls_config,
        seed=seed + 501,
    )
    return best, best_eval


def tabu_search_vrptw(
    data: VRPTWData,
    initial_solution: Optional[Sequence[Sequence[int]]] = None,
    penalties: Optional[PenaltyConfig] = None,
    tabu_config: Optional[TabuConfig] = None,
    ls_config: Optional[LocalSearchConfig] = None,
    seed: int = 123,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Búsqueda Tabú para mejorar soluciones VRP/VRPTW — metaheurística principal en producción.

    Es el algoritmo de mejora usado por ``optimizer_service.py`` tras la fase constructiva
    Solomon I1. En cada iteración genera aleatoriamente ``neighborhood_size`` vecinos,
    los ordena por costo y elige el mejor que no esté prohibido por la lista tabú.

    Mecanismos clave:
      - Lista tabú: registra qué movimientos estuvieron activos recientemente para
        evitar ciclos (tabu_tenure iteraciones de prohibición + ruido ±5).
      - Criterio de aspiración: ignora la prohibición tabú si el vecino supera
        el mejor global conocido (es demasiado bueno para rechazarlo).
      - Intensificación (cada ``intensify_every`` iteraciones): aplica búsqueda
        local completa al estado actual para explotar el vecindario.
      - Diversificación (tras ``diversification_gap`` sin mejora): perturba la
        solución actual con 5 movimientos aleatorios para escapar del mínimo local.

    El TS termina por número de iteraciones o por límite de tiempo (tabu_config.max_seconds).
    Una búsqueda local final refina la mejor solución encontrada antes de retornar.

    Parámetros:
        data              -- datos completos del problema VRP/VRPTW.
        initial_solution  -- solución de arranque (None = construir con Clarke-Wright).
        penalties         -- coeficientes de penalización para restricciones blandas.
        tabu_config       -- hiperparámetros del Tabu Search.
        ls_config         -- configuración de la búsqueda local usada en intensificación.
        seed              -- semilla para reproducibilidad.
    """
    penalties = penalties or PenaltyConfig()
    tabu_config = tabu_config or TabuConfig()
    ls_config = ls_config or LocalSearchConfig(max_passes=3, max_neighbors_per_operator=320)
    rng = random.Random(seed)

    if initial_solution is None:
        # Si no se proporciona solución inicial, construir con Clarke-Wright + LS
        initial_solution, _ = build_initial_solution_clarke_wright(
            data,
            penalties=penalties,
            seed=seed,
            run_local_search=True,
            ls_config=ls_config,
        )
    else:
        # Reparar la solución de entrada para garantizar cobertura completa
        initial_solution = repair_solution_vrptw(initial_solution, data, penalties=penalties, seed=seed)

    # Mejorar la solución inicial con búsqueda local antes de iniciar el TS
    current, current_eval = local_search_vrptw(
        initial_solution, data, penalties=penalties, config=ls_config, seed=seed
    )
    best = clone_solution(current)
    best_eval = current_eval

    # Lista tabú: move_key → número de iteración hasta la que está prohibido
    tabu_until: Dict[Tuple, int] = {}
    no_improve = 0
    t0 = time.time()

    for it in range(1, tabu_config.iterations + 1):
        if (time.time() - t0) > tabu_config.max_seconds:
            break

        # Generar vecinos aleatorios y filtrar los infactibles
        candidates: List[Tuple[float, SolutionRoutes, SolutionEvaluation, Tuple]] = []
        for _ in range(tabu_config.neighborhood_size):
            cand, move = random_neighbor(current, rng)
            if move[0] == "none":
                continue
            # Con probabilidad 20% reparar el vecino para mantener factibilidad
            if rng.random() < 0.20:
                cand = repair_solution_vrptw(cand, data, penalties=penalties, seed=rng.randint(1, 10**6))
            ev = evaluate_solution(cand, data, penalties)
            if not ev.feasible:
                continue
            candidates.append((ev.penalized_objective, cand, ev, move))

        if not candidates:
            # Sin vecinos factibles: perturbar para escapar del área infactible
            current = perturb_solution(current, rng, strength=3)
            current = repair_solution_vrptw(current, data, penalties=penalties, seed=rng.randint(1, 10**6))
            current_eval = evaluate_solution(current, data, penalties)
            continue

        # Ordenar candidatos por costo penalizado: el primero es el mejor vecino
        candidates.sort(key=lambda x: x[0])

        # Seleccionar el mejor vecino no tabú (o tabú si supera el criterio de aspiración)
        chosen = None
        for _, cand, ev, move in candidates:
            expiry = tabu_until.get(move, -1)
            is_tabu = expiry >= it  # El movimiento sigue prohibido en esta iteración
            # Criterio de aspiración: aceptar movimiento tabú si supera el mejor global
            aspiration = ev.penalized_objective + EPS < best_eval.penalized_objective
            if (not is_tabu) or aspiration:
                chosen = (cand, ev, move)
                break

        if chosen is None:
            # Todos los vecinos son tabú y ninguno pasa la aspiración: tomar el mejor de todos
            cand, ev, move = candidates[0][1], candidates[0][2], candidates[0][3]
        else:
            cand, ev, move = chosen

        # Actualizar estado actual y registrar movimiento en la lista tabú
        current = cand
        current_eval = ev
        # La tenencia tabú incluye un componente aleatorio ±5 para evitar ciclos deterministas
        tabu_until[move] = it + tabu_config.tabu_tenure + rng.randint(0, 5)

        if current_eval.penalized_objective + EPS < best_eval.penalized_objective:
            # Nuevo mejor global encontrado
            best = clone_solution(current)
            best_eval = current_eval
            no_improve = 0
        else:
            no_improve += 1

        # Intensificación: aplicar búsqueda local completa cada ``intensify_every`` iteraciones
        if it % tabu_config.intensify_every == 0:
            current, current_eval = local_search_vrptw(
                current,
                data,
                penalties=penalties,
                config=ls_config,
                seed=rng.randint(1, 10**6),
            )
            if current_eval.penalized_objective + EPS < best_eval.penalized_objective:
                best = clone_solution(current)
                best_eval = current_eval
                no_improve = 0

        if no_improve >= tabu_config.diversification_gap:
            # Diversificación: reiniciar desde la mejor solución con fuerte perturbación
            current = perturb_solution(best, rng, strength=5)
            current = repair_solution_vrptw(current, data, penalties=penalties, seed=rng.randint(1, 10**6))
            current_eval = evaluate_solution(current, data, penalties)
            no_improve = 0

    # Refinamiento final: búsqueda local sobre la mejor solución histórica
    best, best_eval = local_search_vrptw(
        best,
        data,
        penalties=penalties,
        config=ls_config,
        seed=seed + 913,
    )
    return best, best_eval


def _format_eval_summary(name: str, ev: SolutionEvaluation, elapsed: float) -> str:
    return (
        f"{name:>10} | obj={ev.penalized_objective:10.2f} | base={ev.cost_base:10.2f} | "
        f"rutas={ev.route_count:3d} | factible={str(ev.feasible):5s} | "
        f"cap_v={ev.cap_violation:8.2f} | tw_v={ev.tw_violation:8.2f} | "
        f"dur_v={ev.duration_violation:8.2f} | t={elapsed:6.2f}s"
    )


def benchmark_metaheuristics(
    data: VRPTWData,
    seed: int = 123,
    ga_cfg: Optional[GAConfig] = None,
    sa_cfg: Optional[SAConfig] = None,
    tabu_cfg: Optional[TabuConfig] = None,
) -> Dict[str, Dict[str, object]]:
    """Ejecuta y compara las tres metaheurísticas sobre la misma instancia.

    Construye una solución inicial con Clarke-Wright y la usa como punto de partida
    para AG, SA y Tabu Search. Imprime una tabla comparativa con objetivo penalizado,
    costo base, número de rutas, factibilidad y tiempo de ejecución de cada método.
    Retorna un diccionario con las soluciones y evaluaciones para análisis posterior.
    """
    penalties = PenaltyConfig()
    ls_init = LocalSearchConfig(max_passes=4, max_neighbors_per_operator=450)
    ls_run = LocalSearchConfig(max_passes=3, max_neighbors_per_operator=320)

    init_t0 = time.time()
    initial_solution, initial_eval = build_initial_solution_clarke_wright(
        data,
        penalties=penalties,
        seed=seed,
        run_local_search=True,
        ls_config=ls_init,
    )
    init_elapsed = time.time() - init_t0

    results: Dict[str, Dict[str, object]] = {
        "initial": {
            "solution": initial_solution,
            "evaluation": initial_eval,
            "time_sec": init_elapsed,
        }
    }

    t0 = time.time()
    ga_sol, ga_eval = genetic_algorithm_vrptw(
        data,
        initial_solution=initial_solution,
        penalties=penalties,
        ga_config=ga_cfg or GAConfig(),
        ls_config=ls_run,
        seed=seed + 11,
    )
    ga_elapsed = time.time() - t0
    results["ga"] = {"solution": ga_sol, "evaluation": ga_eval, "time_sec": ga_elapsed}

    t0 = time.time()
    sa_sol, sa_eval = simulated_annealing_vrptw(
        data,
        initial_solution=initial_solution,
        penalties=penalties,
        sa_config=sa_cfg or SAConfig(),
        ls_config=ls_run,
        seed=seed + 22,
    )
    sa_elapsed = time.time() - t0
    results["sa"] = {"solution": sa_sol, "evaluation": sa_eval, "time_sec": sa_elapsed}

    t0 = time.time()
    tabu_sol, tabu_eval = tabu_search_vrptw(
        data,
        initial_solution=initial_solution,
        penalties=penalties,
        tabu_config=tabu_cfg or TabuConfig(),
        ls_config=ls_run,
        seed=seed + 33,
    )
    tabu_elapsed = time.time() - t0
    results["tabu"] = {"solution": tabu_sol, "evaluation": tabu_eval, "time_sec": tabu_elapsed}

    print("\n=== Benchmark VRPTW (metaheuristicas) ===")
    print(_format_eval_summary("INITIAL", initial_eval, init_elapsed))
    print(_format_eval_summary("GA", ga_eval, ga_elapsed))
    print(_format_eval_summary("SA", sa_eval, sa_elapsed))
    print(_format_eval_summary("TABU", tabu_eval, tabu_elapsed))

    ranked = sorted(
        [(k, v["evaluation"]) for k, v in results.items()],
        key=lambda kv: kv[1].penalized_objective,
    )
    best_name, best_eval = ranked[0]
    print(
        f"\nMejor metodo: {best_name.upper()} | obj={best_eval.penalized_objective:.2f} "
        f"| base={best_eval.cost_base:.2f} | factible={best_eval.feasible}"
    )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Metaheuristicas VRPTW para el proyecto Capstone")
    parser.add_argument("--n_customers", type=int, default=150)
    parser.add_argument("--n_trucks", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tw_seed", type=int, default=777)
    parser.add_argument("--tw_width", type=float, default=0.35)
    parser.add_argument("--tw_shift", type=float, default=0.70)
    parser.add_argument("--use_time_windows", action="store_true")
    parser.add_argument("--ga_seconds", type=float, default=300.0)
    parser.add_argument("--sa_seconds", type=float, default=300.0)
    parser.add_argument("--tabu_seconds", type=float, default=300.0)
    args = parser.parse_args()

    random.seed(args.seed)

    data = load_project_vrptw_data(
        n_customers=args.n_customers,
        n_trucks=args.n_trucks,
        seed=args.seed,
        tw_seed=args.tw_seed,
        tw_width_ratio=args.tw_width,
        tw_shift_ratio=args.tw_shift,
        use_time_windows=args.use_time_windows,
    )

    ga_cfg = GAConfig(max_seconds=args.ga_seconds)
    sa_cfg = SAConfig(max_seconds=args.sa_seconds)
    tabu_cfg = TabuConfig(max_seconds=args.tabu_seconds)

    benchmark_metaheuristics(
        data,
        seed=args.seed,
        ga_cfg=ga_cfg,
        sa_cfg=sa_cfg,
        tabu_cfg=tabu_cfg,
    )


if __name__ == "__main__":
    main()
