"""
Benchmark de heuristicas de literatura para VRP/VRPTW del proyecto Capstone.

Este módulo contiene implementaciones de referencia de las principales heurísticas
constructivas y de mejora para el Problema de Ruteo de Vehículos con Ventanas de
Tiempo (VRPTW). Sirve tanto como benchmark comparativo para el informe técnico como
fuente de la heurística usada en producción: ``heuristic_solomon_i1_style``.

Parámetros por defecto del proyecto:
- n_customers = 150  (pedidos por día típico)
- n_trucks = 15      (flota máxima)
- seed = 42          (reproducibilidad)

Heurísticas incluidas:
1) ACTUAL_TEXTUAL     : Clarke-Wright + local search (flujo actual del sistema).
2) CW_MULTISTART_MEJORADA: Clarke-Wright con multistart y perturbación aleatoria.
3) SOLOMON_I1_STYLE   : Inserción secuencial inspirada en Solomon (1987).
                        Es la heurística constructiva usada en producción.
4) REGRET2_PARALLEL   : Inserción paralela con criterio regret-2 (evita decisiones
                        que cierran opciones futuras).
5) SWEEP_GM74         : Barrido angular (Gillett & Miller, 1974) + mejora local.
6) ALNS_LITE_RP       : ALNS simplificada con destroy/repair (Ropke & Pisinger, 2006).

La clase ``ProblemContext`` es el contenedor de datos del problema usado tanto aquí
como en ``validation_heuristics.py``.

Referencias:
- Clarke & Wright (1964): https://doi.org/10.1287/opre.12.4.568
- Solomon (1987): https://doi.org/10.1287/opre.35.2.254
- Gillett & Miller (1974): https://EconPapers.repec.org/RePEc:inm:oropre:v:22:y:1974:i:2:p:340-349
- Ropke & Pisinger (2006): https://doi.org/10.1287/trsc.1050.0135
- Ropke & Pisinger (2007): https://doi.org/10.1016/j.cor.2005.09.012
- Vidal HGS-CVRP (2022): https://doi.org/10.1016/j.cor.2021.105643
"""

from __future__ import annotations

import argparse
import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from backend.models.routing.heuristics import generate_toy_data
from backend.models.routing.metaheuristics import (
    EPS,
    GAConfig,
    LocalSearchConfig,
    PenaltyConfig,
    RouteNodes,
    SAConfig,
    SolutionEvaluation,
    SolutionRoutes,
    TabuConfig,
    VRPTWData,
    build_initial_solution_clarke_wright,
    clone_solution,
    evaluate_route,
    evaluate_solution,
    local_search_vrptw,
    random_neighbor,
    repair_solution_vrptw,
)


@dataclass
class ProblemContext:
    """Contenedor de datos del problema VRP para las heurísticas de literatura.

    Agrupa los datos de optimización (VRPTWData) junto con las coordenadas
    geográficas de los nodos, que son necesarias para algoritmos como el
    barrido angular de Gillett-Miller.
    """

    data: VRPTWData       # Parámetros del VRP: flota, clientes, distancias, tiempos
    coords: Dict[int, Tuple[int, int]]  # Coordenadas (x, y) de cada nodo (0=depósito)


def _to_scalar(x) -> float:
    """Convierte un valor o diccionario de valores en un único float.

    Si x es un diccionario (e.g., capacidad por tipo de camión), devuelve
    el promedio de sus valores. Esto permite trabajar con configuraciones
    heterogéneas de flota como si fueran un único parámetro escalar.
    """
    if isinstance(x, dict):
        vals = list(x.values())
        return float(sum(vals) / len(vals))
    return float(x)


def load_context(
    n_customers: int = 150,
    n_trucks: int = 15,
    seed: int = 42,
    use_time_windows: bool = False,
) -> ProblemContext:
    """Carga el contexto del problema usando el generador de datos del proyecto.

    Construye un ProblemContext listo para usar con cualquiera de las heurísticas
    de este módulo. Opcionalmente genera ventanas de tiempo sintéticas coherentes
    con la duración máxima de ruta (por defecto desactivadas para reproducir el
    problema base del proyecto).
    """
    # Reusa exactamente el generador de datos del proyecto.
    K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t, max_route_time = generate_toy_data(
        n_customers=n_customers,
        n_trucks=n_trucks,
        seed=seed,
    )

    if use_time_windows:
        # Ventanas ajustadas: la apertura más temprana es el tiempo de viaje
        # desde el depósito; el cierre más tardío garantiza que el camión
        # pueda regresar al depósito antes de agotar la jornada.
        tw_open = {0: 0.0}
        tw_close = {0: float(max_route_time)}
        for j in J:
            earliest = t[0, j]
            latest = max(earliest, max_route_time - T[j] - t[j, 0])
            tw_open[j] = earliest
            tw_close[j] = latest
    else:
        # Sin ventanas de tiempo: todos los clientes pueden ser visitados
        # en cualquier momento dentro de la jornada completa.
        tw_open = {0: 0.0}
        tw_close = {0: float(max_route_time)}
        for j in J:
            tw_open[j] = 0.0
            tw_close[j] = float(max_route_time)

    data = VRPTWData(
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
        use_time_windows=use_time_windows,
        depot=0,
    )
    return ProblemContext(data=data, coords=coords)


def _all_customers(routes: Sequence[Sequence[int]], depot: int = 0) -> List[int]:
    """Extrae la lista plana de todos los clientes (sin el depósito) de una solución."""
    out: List[int] = []
    for r in routes:
        out.extend([n for n in r if n != depot])
    return out


def _assert_complete_solution(solution: SolutionRoutes, data: VRPTWData) -> bool:
    """Verifica que la solución cubre exactamente el conjunto de clientes del problema."""
    seen = _all_customers(solution, depot=data.depot)
    return sorted(seen) == sorted(data.J)


def _best_feasible_insertion(
    routes: SolutionRoutes,
    customer: int,
    data: VRPTWData,
    rng: random.Random,
) -> Optional[Tuple[int, int, float]]:
    """Busca la mejor posición factible para insertar un cliente en cualquier ruta existente.

    Evalúa todas las posiciones de inserción en todas las rutas y retorna la
    combinación (índice de ruta, posición) que minimiza el incremento de distancia,
    usando el tiempo de ruta como criterio de desempate y ruido uniforme para
    romper empates exactos de forma estocástica.

    Retorna None si no existe ninguna posición factible en ninguna ruta.
    """
    best: Optional[Tuple[int, int, float]] = None
    for ridx, route in enumerate(routes):
        base_eval = evaluate_route(route, data)
        for pos in range(len(route) - 1):
            cand = route[: pos + 1] + [customer] + route[pos + 1 :]
            cand_eval = evaluate_route(cand, data)
            if not cand_eval.feasible:
                continue
            delta = cand_eval.distance - base_eval.distance
            tie = cand_eval.route_time - base_eval.route_time  # desempate por tiempo
            score = delta + 1e-3 * tie + rng.uniform(0.0, 1e-8)
            if best is None or score < best[2]:
                best = (ridx, pos, score)
    return best


def _insert_or_open_route(
    routes: SolutionRoutes,
    customer: int,
    data: VRPTWData,
    rng: random.Random,
) -> bool:
    """Inserta un cliente en la mejor posición factible o abre una ruta nueva si es necesario.

    Primero intenta insertar el cliente en una ruta existente. Si no cabe en ninguna
    y aún hay camiones disponibles, abre una ruta singleton para ese cliente.
    Retorna True si logró asignar el cliente, False si no fue posible.
    """
    best = _best_feasible_insertion(routes, customer, data, rng)
    if best is not None:
        ridx, pos, _ = best
        routes[ridx].insert(pos + 1, customer)
        return True

    # Si no cabe en ninguna ruta existente, intentar abrir una ruta nueva
    if len(routes) < data.K_max:
        singleton = [data.depot, customer, data.depot]
        if evaluate_route(singleton, data).feasible:
            routes.append(singleton)
            return True
    return False


def _improve_with_ls(solution: SolutionRoutes, data: VRPTWData, seed: int) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Aplica reparación y búsqueda local a una solución para mejorar su calidad.

    Primero repara la solución (garantiza cobertura completa y unicidad de clientes),
    luego ejecuta la búsqueda local con múltiples operadores de vecindad (2-opt,
    relocate, swap, or-opt) hasta convergencia o el límite de pasadas configurado.
    """
    penalties = PenaltyConfig()
    ls_cfg = LocalSearchConfig(max_passes=4, max_neighbors_per_operator=450)
    repaired = repair_solution_vrptw(solution, data, penalties=penalties, seed=seed)
    improved, ev = local_search_vrptw(repaired, data, penalties=penalties, config=ls_cfg, seed=seed)
    return improved, ev


def heuristic_actual_textual(ctx: ProblemContext, seed: int = 42) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Heurística baseline: Clarke-Wright con búsqueda local (flujo actual del sistema).

    Replica exactamente el flujo de optimización que usa el sistema en producción
    antes de la fase de Tabu Search. Se usa como punto de comparación base en el
    benchmark para cuantificar el valor añadido de las demás heurísticas.
    """
    # "Actual textual": exactamente el flujo actual del archivo Metaheuristicas.
    penalties = PenaltyConfig()
    ls_cfg = LocalSearchConfig(max_passes=4, max_neighbors_per_operator=450)
    sol, ev = build_initial_solution_clarke_wright(
        ctx.data,
        penalties=penalties,
        seed=seed,
        run_local_search=True,
        ls_config=ls_cfg,
    )
    return sol, ev


def heuristic_cw_multistart_mejorada(
    ctx: ProblemContext,
    seed: int = 42,
    starts: int = 16,
    perturb_moves: int = 6,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Clarke-Wright con multistart y perturbación aleatoria.

    Ejecuta múltiples instancias de la constructiva Clarke-Wright con semillas
    distintas, aplica un número aleatorio de movimientos de perturbación a cada
    solución y luego la mejora con búsqueda local. Retiene la mejor solución
    factible entre todos los arranques. La diversificación por perturbación ayuda
    a escapar de mínimos locales que una sola ejecución de Clarke-Wright no evitaría.

    Parámetros:
        starts       -- número de arranques independientes.
        perturb_moves -- máximo de movimientos aleatorios de perturbación por arranque.
    """
    rng = random.Random(seed)
    penalties = PenaltyConfig()
    best_sol: Optional[SolutionRoutes] = None
    best_ev: Optional[SolutionEvaluation] = None

    for s in range(starts):
        sol, _ = build_initial_solution_clarke_wright(
            ctx.data,
            penalties=penalties,
            seed=seed + s,
            run_local_search=False,
        )
        cand = clone_solution(sol)
        for _ in range(rng.randint(1, perturb_moves)):
            cand, _ = random_neighbor(cand, rng)
        cand, ev = _improve_with_ls(cand, ctx.data, seed + s)
        if best_ev is None or ev.cost_base + EPS < best_ev.cost_base:
            best_sol, best_ev = cand, ev

    assert best_sol is not None and best_ev is not None
    return best_sol, best_ev


def heuristic_solomon_i1_style(ctx: ProblemContext, seed: int = 42) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Heurística constructiva Solomon I1 (versión estándar con fallback).

    Implementa el algoritmo I1 de Solomon (1987) para VRPTW. A diferencia de
    ``solomon_hard_fleet`` en validation_heuristics.py, esta versión no abandona
    clientes no asignados: si se agota la flota antes de cubrir todos los clientes,
    aplica un repair() para forzar la cobertura completa (posiblemente infactible).

    El algoritmo opera en dos niveles:
      - Nivel externo: abre una nueva ruta por iteración, inicializada con el cliente
        más lejano al depósito (semilla de máxima distancia).
      - Nivel interno: rellena la ruta insertando en cada paso el cliente que maximiza
        c2 = d(depósito, c) - c1*, donde c1* es el costo mínimo de inserción de c.

    La intuición detrás de c2: un cliente con c2 alto es aquel que, si no entrara en
    esta ruta, tendría que abrir una ruta propia muy costosa — por eso merece prioridad.
    """
    rng = random.Random(seed)
    data = ctx.data
    unserved: Set[int] = set(data.J)  # Clientes aún sin asignar
    routes: SolutionRoutes = []

    while unserved:
        if len(routes) >= data.K_max:
            # Fallback robusto: completar con repair en caso de atasco.
            # A diferencia de solomon_hard_fleet, aquí se fuerza la cobertura completa.
            partial = routes + [[data.depot, j, data.depot] for j in sorted(unserved)]
            repaired = repair_solution_vrptw(partial, data, penalties=PenaltyConfig(), seed=seed)
            return _improve_with_ls(repaired, data, seed)

        # Semilla: cliente más lejano al depósito — maximiza aprovechamiento del camión
        seed_customer = max(unserved, key=lambda j: data.d[data.depot, j])
        route = [data.depot, seed_customer, data.depot]
        unserved.remove(seed_customer)

        # Relleno iterativo de la ruta con el criterio I1 de Solomon
        while True:
            base_eval = evaluate_route(route, data)
            best_choice = None  # (c2_score, customer, mejor_posicion)

            for c in list(unserved):
                best_for_c = None  # (c1_minimo, posicion)
                for pos in range(len(route) - 1):
                    i = route[pos]
                    j = route[pos + 1]
                    cand = route[: pos + 1] + [c] + route[pos + 1 :]
                    cand_eval = evaluate_route(cand, data)
                    if not cand_eval.feasible:
                        continue

                    # c11: desvío adicional de distancia al insertar c entre i y j
                    c11 = data.d[i, c] + data.d[c, j] - data.d[i, j]
                    # c12: incremento del tiempo total de ruta
                    c12 = cand_eval.route_time - base_eval.route_time
                    # c1: criterio de costo de inserción ponderado (Solomon eq. 3)
                    c1 = 0.7 * c11 + 0.3 * c12
                    if best_for_c is None or c1 < best_for_c[0]:
                        best_for_c = (c1, pos)

                if best_for_c is None:
                    continue

                # Solomon I1: maximizamos c2 = lambda * d(0,c) - c1
                # lambda=1.0 por defecto; el ruido evita empates deterministas
                c1_star, pos_star = best_for_c
                c2 = 1.0 * data.d[data.depot, c] - c1_star + rng.uniform(0.0, 1e-8)
                if best_choice is None or c2 > best_choice[0]:
                    best_choice = (c2, c, pos_star)

            if best_choice is None:
                break  # La ruta no puede absorber más clientes factiblemente

            _, chosen_c, chosen_pos = best_choice
            route.insert(chosen_pos + 1, chosen_c)
            unserved.remove(chosen_c)

        routes.append(route)

    # Mejorar la solución constructiva con búsqueda local antes de retornar
    return _improve_with_ls(routes, data, seed)


def heuristic_regret2_parallel(ctx: ProblemContext, seed: int = 42) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Inserción paralela con criterio regret-2.

    A diferencia de Solomon I1 (que construye ruta a ruta), esta heurística
    trabaja en paralelo sobre todas las rutas abiertas. En cada paso elige el
    cliente con mayor "regret": la diferencia entre su segundo mejor costo de
    inserción y el primero. Un regret alto indica que postergar la inserción de
    ese cliente incrementaría mucho su costo futuro, por lo que se le da prioridad.

    Esta estrategia reduce el riesgo de dejar para el final clientes difíciles
    de insertar, lo que tiende a producir soluciones con menos rutas abiertas.
    """
    rng = random.Random(seed)
    data = ctx.data
    unserved: Set[int] = set(data.J)
    routes: SolutionRoutes = []

    # Inicializacion ligera con una semilla: el cliente más lejano abre la primera ruta.
    first_seed = max(unserved, key=lambda j: data.d[data.depot, j])
    routes.append([data.depot, first_seed, data.depot])
    unserved.remove(first_seed)

    while unserved:
        best_global = None  # (regret, best_delta, customer, action, ridx, pos)

        for c in list(unserved):
            options: List[Tuple[float, str, int, int]] = []  # (delta_dist, action, ridx, pos)

            for ridx, route in enumerate(routes):
                base_eval = evaluate_route(route, data)
                for pos in range(len(route) - 1):
                    cand = route[: pos + 1] + [c] + route[pos + 1 :]
                    cand_eval = evaluate_route(cand, data)
                    if not cand_eval.feasible:
                        continue
                    delta = cand_eval.distance - base_eval.distance
                    options.append((delta, "insert", ridx, pos))

            if len(routes) < data.K_max:
                singleton = [data.depot, c, data.depot]
                if evaluate_route(singleton, data).feasible:
                    delta_single = evaluate_route(singleton, data).distance
                    options.append((delta_single, "new", -1, -1))

            if not options:
                continue

            options.sort(key=lambda x: x[0])
            best = options[0]
            # Si solo hay una opción, el "segundo mejor" se penaliza con 1e6
            # para darle alta prioridad (no tiene alternativa)
            second = options[1] if len(options) > 1 else (best[0] + 1e6, best[1], best[2], best[3])
            # regret = diferencia entre el 2do y 1er costo de inserción
            regret = (second[0] - best[0]) + rng.uniform(0.0, 1e-8)
            candidate = (regret, best[0], c, best[1], best[2], best[3])
            if best_global is None or candidate[0] > best_global[0]:
                best_global = candidate

        if best_global is None:
            # Sin inserciones factibles; fallback robusto.
            partial = routes + [[data.depot, j, data.depot] for j in sorted(unserved)]
            repaired = repair_solution_vrptw(partial, data, penalties=PenaltyConfig(), seed=seed)
            return _improve_with_ls(repaired, data, seed)

        _, _, c_star, action, ridx, pos = best_global
        if action == "new":
            routes.append([data.depot, c_star, data.depot])
        else:
            routes[ridx].insert(pos + 1, c_star)
        unserved.remove(c_star)

    return _improve_with_ls(routes, data, seed)


def heuristic_sweep_gm74(ctx: ProblemContext, seed: int = 42) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """Heurística de barrido angular de Gillett & Miller (1974).

    Ordena los clientes por su ángulo polar respecto al depósito y los asigna
    secuencialmente a rutas: cuando añadir el siguiente cliente haría infactible la
    ruta actual, se cierra esa ruta y se abre una nueva. La idea intuitiva es que
    clientes en la misma dirección geográfica tienden a compartir ruta eficientemente.

    Ventaja: extremadamente rápida (O(n log n) en el ordenamiento).
    Limitación: no considera distancias intra-ruta, solo ángulos.
    Tras la construcción se aplica búsqueda local para compensar.
    """
    # Sweep (Gillett-Miller): ordenar por angulo y llenar rutas.
    data = ctx.data
    coords = ctx.coords
    rng = random.Random(seed)

    x0, y0 = coords[data.depot]
    # Ordenar clientes por ángulo polar respecto al depósito (barrido 360°)
    by_angle = sorted(
        data.J,
        key=lambda j: math.atan2(coords[j][1] - y0, coords[j][0] - x0),
    )

    routes: SolutionRoutes = []
    current: RouteNodes = [data.depot, data.depot]

    for cust in by_angle:
        try_end = current[:-1] + [cust, data.depot]
        if evaluate_route(try_end, data).feasible:
            current = try_end
            continue

        if len(current) > 2:
            routes.append(current)
        current = [data.depot, cust, data.depot]

        if not evaluate_route(current, data).feasible:
            # Si ni siquiera entra solo, delegar a repair.
            partial = routes + [[data.depot, j, data.depot] for j in by_angle if j not in _all_customers(routes)]
            repaired = repair_solution_vrptw(partial, data, penalties=PenaltyConfig(), seed=seed)
            return _improve_with_ls(repaired, data, seed)

    if len(current) > 2:
        routes.append(current)

    # Si excede camiones, repara con inserciones factibles.
    if len(routes) > data.K_max:
        routes = repair_solution_vrptw(routes, data, penalties=PenaltyConfig(), seed=seed)

    # Garantiza cobertura completa.
    seen = set(_all_customers(routes, depot=data.depot))
    missing = [j for j in data.J if j not in seen]
    for cust in missing:
        if not _insert_or_open_route(routes, cust, data, rng):
            partial = routes + [[data.depot, cust, data.depot]]
            routes = repair_solution_vrptw(partial, data, penalties=PenaltyConfig(), seed=seed)
            break

    return _improve_with_ls(routes, data, seed)


def _destroy_random(solution: SolutionRoutes, q: int, rng: random.Random, depot: int) -> Tuple[SolutionRoutes, List[int]]:
    """Operador de destrucción aleatoria para ALNS: elimina q clientes al azar de la solución."""
    cand = clone_solution(solution)
    all_customers = _all_customers(cand, depot=depot)
    if not all_customers:
        return cand, []
    q = max(1, min(q, len(all_customers)))
    removed = set(rng.sample(all_customers, q))
    for r in cand:
        kept = [n for n in r if n == depot or n not in removed]
        if kept[0] != depot:
            kept.insert(0, depot)
        if kept[-1] != depot:
            kept.append(depot)
        r[:] = kept
    cand = [r for r in cand if len(r) > 2]
    return cand, list(removed)


def _destroy_related(
    solution: SolutionRoutes,
    q: int,
    rng: random.Random,
    data: VRPTWData,
) -> Tuple[SolutionRoutes, List[int]]:
    """Operador de destrucción por proximidad para ALNS: elimina q clientes cercanos entre sí.

    Elige un cliente semilla al azar y elimina los q clientes más cercanos a él.
    La destrucción por cercanía tiende a generar soluciones parciales más fáciles
    de reparar porque los clientes eliminados son geográficamente coherentes.
    """
    all_customers = _all_customers(solution, depot=data.depot)
    if not all_customers:
        return clone_solution(solution), []
    seed = rng.choice(all_customers)
    related = sorted(all_customers, key=lambda j: data.d[seed, j])
    removed = set(related[: max(1, min(q, len(related)))])
    cand = []
    for r in solution:
        kept = [n for n in r if n == data.depot or n not in removed]
        if kept[0] != data.depot:
            kept.insert(0, data.depot)
        if kept[-1] != data.depot:
            kept.append(data.depot)
        if len(kept) > 2:
            cand.append(kept)
    return cand, list(removed)


def _repair_removed(
    partial: SolutionRoutes,
    removed: Iterable[int],
    data: VRPTWData,
    rng: random.Random,
) -> Optional[SolutionRoutes]:
    """Operador de reparación para ALNS: reinserta los clientes eliminados.

    Intenta insertar cada cliente removido en la mejor posición factible disponible.
    Retorna None si algún cliente no puede ser reinsertado (la solución destruida
    es irrecuperable con la flota disponible).
    """
    routes = clone_solution(partial)
    for cust in removed:
        if _insert_or_open_route(routes, cust, data, rng):
            continue
        return None
    return routes


def heuristic_alns_lite_rp(
    ctx: ProblemContext,
    seed: int = 42,
    time_limit_sec: float = 35.0,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    """ALNS simplificada con destroy/repair y aceptación tipo Simulated Annealing.

    Implementa una versión ligera del Adaptive Large Neighborhood Search (ALNS) de
    Ropke & Pisinger (2006). El loop principal alterna entre dos operadores de
    destrucción (aleatorio o por proximidad) y un operador de reparación greedy.
    La aceptación de soluciones peores sigue el criterio de Simulated Annealing con
    temperatura decreciente, lo que permite escapar de mínimos locales al inicio
    y converge a explotar el mejor vecindario hacia el final del tiempo disponible.

    Cada 12 iteraciones se aplica búsqueda local completa sobre la solución reparada
    para intensificar la explotación del mejor resultado parcial encontrado.

    Parámetros:
        time_limit_sec -- presupuesto de tiempo en segundos para el loop ALNS.
    """
    rng = random.Random(seed)
    data = ctx.data
    penalties = PenaltyConfig()

    # Solución inicial: Clarke-Wright multistart con 8 arranques
    current, current_ev = heuristic_cw_multistart_mejorada(ctx, seed=seed, starts=8, perturb_moves=4)
    best = clone_solution(current)
    best_ev = current_ev

    t0 = time.time()
    # Temperatura inicial proporcional al costo de la solución inicial (1%)
    temp = max(1.0, best_ev.cost_base * 0.01)
    cooling = 0.997  # Factor de enfriamiento geométrico por iteración
    it = 0

    while time.time() - t0 < time_limit_sec:
        it += 1
        q = rng.randint(3, 12)  # Número de clientes a destruir en esta iteración

        # Alternar aleatoriamente entre destrucción aleatoria y por proximidad
        if rng.random() < 0.5:
            partial, removed = _destroy_random(current, q, rng, data.depot)
        else:
            partial, removed = _destroy_related(current, q, rng, data)

        repaired = _repair_removed(partial, removed, data, rng)
        if repaired is None:
            # La solución no pudo repararse; solo enfriar temperatura y continuar
            temp *= cooling
            continue

        # Cada 12 iteraciones aplicar búsqueda local completa (intensificación)
        if it % 12 == 0:
            repaired, cand_ev = _improve_with_ls(repaired, data, seed + it)
        else:
            cand_ev = evaluate_solution(repaired, data, penalties)

        if not cand_ev.feasible:
            temp *= cooling
            continue

        # Criterio de aceptación SA: siempre acepta mejoras, acepta empeoramientos
        # con probabilidad exp(-delta/T) — decrece con temperatura y con delta
        delta = cand_ev.cost_base - current_ev.cost_base
        accept = delta <= 0 or rng.random() < math.exp(-delta / max(EPS, temp))
        if accept:
            current, current_ev = repaired, cand_ev

        # Actualizar el mejor global si esta candidata es mejor
        if cand_ev.cost_base + EPS < best_ev.cost_base:
            best, best_ev = repaired, cand_ev

        temp *= cooling

    # Refinamiento final con búsqueda local sobre la mejor solución encontrada
    best, best_ev = _improve_with_ls(best, data, seed + 999)
    return best, best_ev


def _row(name: str, ev: SolutionEvaluation, elapsed: float) -> str:
    return (
        f"{name:>22} | obj={ev.cost_base:10.2f} | rutas={ev.route_count:3d} "
        f"| factible={str(ev.feasible):5s} | t={elapsed:7.2f}s"
    )


def benchmark(ctx: ProblemContext, seed: int = 42, alns_seconds: float = 35.0) -> None:
    """Ejecuta todas las heurísticas sobre la misma instancia e imprime un ranking comparativo.

    Corre cada heurística midiendo tiempo de ejecución y calidad de solución
    (objetivo penalizado, factibilidad, número de rutas). Al finalizar imprime
    un ranking de las soluciones factibles ordenadas por costo base.
    """
    heuristics: List[Tuple[str, Callable[[], Tuple[SolutionRoutes, SolutionEvaluation]]]] = [
        ("ACTUAL_TEXTUAL", lambda: heuristic_actual_textual(ctx, seed=seed)),
        ("CW_MULTISTART_MEJORADA", lambda: heuristic_cw_multistart_mejorada(ctx, seed=seed)),
        ("SOLOMON_I1_STYLE", lambda: heuristic_solomon_i1_style(ctx, seed=seed)),
        ("REGRET2_PARALLEL", lambda: heuristic_regret2_parallel(ctx, seed=seed)),
        ("SWEEP_GM74", lambda: heuristic_sweep_gm74(ctx, seed=seed)),
        ("ALNS_LITE_RP", lambda: heuristic_alns_lite_rp(ctx, seed=seed, time_limit_sec=alns_seconds)),
    ]

    print("\n=== BENCHMARK HEURISTICAS (misma instancia del proyecto) ===")
    print(f"Clientes={len(ctx.data.J)} | Camiones={len(ctx.data.K)} | Seed={seed}")

    results: List[Tuple[str, SolutionEvaluation, float]] = []
    best_name = None
    best_ev = None

    for name, fn in heuristics:
        t0 = time.time()
        try:
            sol, ev = fn()
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"{name:>22} | ERROR: {exc} | t={elapsed:7.2f}s")
            continue
        elapsed = time.time() - t0

        # Verificacion de cobertura: re-evaluar si la solución no cubre todos los clientes
        if not _assert_complete_solution(sol, ctx.data):
            ev = evaluate_solution(sol, ctx.data, PenaltyConfig())

        print(_row(name, ev, elapsed))
        results.append((name, ev, elapsed))
        if ev.feasible and (best_ev is None or ev.cost_base + EPS < best_ev.cost_base):
            best_name, best_ev = name, ev

    if best_name is None:
        print("\nNo hubo soluciones factibles.")
        return

    print(f"\nMejor heuristica: {best_name} | obj={best_ev.cost_base:.2f} | rutas={best_ev.route_count}")

    print("\n--- Ranking (factibles) ---")
    rank = sorted([r for r in results if r[1].feasible], key=lambda x: x[1].cost_base)
    for i, (name, ev, elapsed) in enumerate(rank, start=1):
        print(f"{i:2d}. {name:22s} obj={ev.cost_base:10.2f} rutas={ev.route_count:3d} t={elapsed:7.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark de heuristicas de literatura para VRP del proyecto")
    parser.add_argument("--n_customers", type=int, default=150)
    parser.add_argument("--n_trucks", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--use_time_windows", action="store_true")
    parser.add_argument("--alns_seconds", type=float, default=35.0)
    args = parser.parse_args()

    random.seed(args.seed)
    ctx = load_context(
        n_customers=args.n_customers,
        n_trucks=args.n_trucks,
        seed=args.seed,
        use_time_windows=args.use_time_windows,
    )
    benchmark(ctx, seed=args.seed, alns_seconds=args.alns_seconds)


if __name__ == "__main__":
    main()
