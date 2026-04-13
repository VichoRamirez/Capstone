"""
validation_heuristics.py — Solomon I1 con restricción dura de flota y tiempo.

Restricciones duras aplicadas:
  1. Flota: nunca se abren más de K rutas. Cuando K se agota, los clientes
     restantes quedan sin servicio (unserved) — NO se fuerza su inserción.
  2. Tiempo de ruta: solo se insertan clientes cuya inserción produce una
     ruta factible (route_time ≤ max_route_time). El tiempo de conducción
     máximo (5 h = 300 min) es una restricción estricta en la construcción.
  3. Capacidad: ídem — solo inserciones factibles.

  Los nodos que no caben en ninguna ruta factible quedan en la lista
  `unserved` que retorna solomon_hard_fleet como segundo elemento de la tupla.

  El Tabu Search recibe la solución parcial con HARD_FLEET_PENALTIES:
  penalidades muy altas (5 000) en tiempo y capacidad hacen que en la práctica
  ambas restricciones sean duras; una penalidad de 50 000 por nodo no
  entregado incentiva fuertemente rescatar nodos; fleet=1 000 000 impide
  abrir rutas adicionales.

Uso (desde validation_report.py):
    from validation_heuristics import solomon_hard_fleet, HARD_FLEET_PENALTIES

    sol_solomon, unserved = solomon_hard_fleet(ctx, seed=42)
    sol_tabu, ev = tabu_search_vrptw(
        data=vrp_data,
        initial_solution=sol_solomon,
        penalties=HARD_FLEET_PENALTIES,
        tabu_config=TabuConfig(),
        seed=42,
    )
"""
from __future__ import annotations

import random
from typing import List, Set, Tuple

from backend.models.routing.metaheuristics import (
    LocalSearchConfig,
    PenaltyConfig,
    SolutionEvaluation,
    SolutionRoutes,
    VRPTWData,
    evaluate_route,
)
from backend.models.routing.literature_heuristics import ProblemContext


# ---------------------------------------------------------------------------
# PenaltyConfig para la fase de Tabu Search post-Solomon-hard-fleet
#
# - cap_weight / cap_volume: penalizan sobrecarga de capacidad (suave)
# - route_duration: penaliza exceso de tiempo de ruta (suave)
# - fleet: astronomicamente alto → TS nunca considera abrir ruta nueva
# - visit: mantiene la obligacion de visitar todos los nodos
# ---------------------------------------------------------------------------
MAX_CLIENTS_PER_ROUTE = 250

# ---------------------------------------------------------------------------
# Penalidades efectivamente duras para Tabu Search:
#   cap_weight / cap_volume / route_duration: 5 000 por unidad de violación
#     → en la práctica ningún movimiento las infringe (costo prohibitivo)
#   visit: 50 000 por nodo no entregado → TS rescata nodos si hay hueco
#     bajo el límite de tiempo/capacidad, pero no a cualquier precio
#   fleet: 1 000 000 → TS nunca abre una ruta adicional
# ---------------------------------------------------------------------------
HARD_FLEET_PENALTIES = PenaltyConfig(
    cap_weight=5_000.0,
    cap_volume=5_000.0,
    route_duration=5_000.0,
    visit=50_000.0,
    fleet=1_000_000.0,
)


def solomon_hard_fleet(
    ctx: ProblemContext,
    seed: int = 42,
) -> Tuple[SolutionRoutes, List[int]]:
    """
    Solomon I1 con restricciones duras de flota, tiempo y capacidad.

    Retorna (routes, unserved):
      routes   — lista de rutas factibles, len(routes) <= K_max.
      unserved — clientes que no pudieron ser asignados a ninguna ruta
                 factible (flota agotada o ninguna ruta tiene hueco).

    Restricciones duras en la construcción:
      - Flota: cuando len(routes) == K_max, los clientes restantes
        se dejan sin servicio (no hay inserción forzada).
      - Tiempo: cand_eval.feasible ya verifica route_time <= max_route_time.
      - Capacidad: ídem.
      - Clientes por ruta: <= MAX_CLIENTS_PER_ROUTE.
    """
    rng = random.Random(seed)
    data = ctx.data
    unserved: Set[int] = set(data.J)
    routes: SolutionRoutes = []

    while unserved:
        # ── Restricción dura: K agotado → clientes restantes sin servicio ─
        if len(routes) >= data.K_max:
            break

        # ── Selección de semilla: nodo más lejano al depósito ────────────
        seed_customer = max(unserved, key=lambda j: data.d[data.depot, j])
        route: List[int] = [data.depot, seed_customer, data.depot]
        unserved.remove(seed_customer)

        # ── Relleno de la ruta actual (igual que Solomon I1 estándar) ────
        while True:
            base_eval = evaluate_route(route, data)
            best_choice = None  # (c2_score, customer, insertion_pos)

            for c in list(unserved):
                best_for_c = None  # (c1_score, pos)

                for pos in range(len(route) - 1):
                    i = route[pos]
                    j = route[pos + 1]
                    cand = route[: pos + 1] + [c] + route[pos + 1 :]
                    cand_eval = evaluate_route(cand, data)

                    if not cand_eval.feasible:
                        continue

                    # Hard per-route client limit
                    if len(cand) - 2 > MAX_CLIENTS_PER_ROUTE:
                        continue

                    # Criterio c1 de Solomon (Solomon 1987, eq. 3-4)
                    c11 = data.d[i, c] + data.d[c, j] - data.d[i, j]
                    c12 = cand_eval.route_time - base_eval.route_time
                    c1 = 0.7 * c11 + 0.3 * c12

                    if best_for_c is None or c1 < best_for_c[0]:
                        best_for_c = (c1, pos)

                if best_for_c is None:
                    continue

                # Criterio c2 de Solomon (maximizar ahorro de abrir ruta propia)
                c1_star, pos_star = best_for_c
                c2 = data.d[data.depot, c] - c1_star + rng.uniform(0.0, 1e-8)

                if best_choice is None or c2 > best_choice[0]:
                    best_choice = (c2, c, pos_star)

            if best_choice is None:
                break  # ningún cliente puede insertarse factiblemente

            _, chosen_c, chosen_pos = best_choice
            route.insert(chosen_pos + 1, chosen_c)
            unserved.remove(chosen_c)

        routes.append(route)

    return routes, list(unserved)
