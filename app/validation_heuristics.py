"""
validation_heuristics.py — Solomon I1 con restricción dura de flota y tiempo.

Este módulo implementa una variante estricta de la heurística constructiva
Solomon I1 donde las restricciones de flota, tiempo y capacidad se tratan como
duras (hard constraints): ningún cliente se inserta si viola alguna de ellas.
Los clientes que no caben en ninguna ruta factible se devuelven en una lista
``unserved`` y son reagendados al día siguiente por el optimizador principal.

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

# Límite máximo de clientes por ruta para evitar rutas imprácticamente largas.
# Este valor actúa como restricción dura adicional durante la construcción.
MAX_CLIENTS_PER_ROUTE = 250

# ---------------------------------------------------------------------------
# Penalidades efectivamente duras para Tabu Search:
#   cap_weight / cap_volume / route_duration: 5 000 por unidad de violación
#     → en la práctica ningún movimiento las infringe (costo prohibitivo)
#   visit: 50 000 por nodo no entregado → TS rescata nodos si hay hueco
#     bajo el límite de tiempo/capacidad, pero no a cualquier precio
#   fleet: 1 000 000 → TS nunca abre una ruta adicional
# ---------------------------------------------------------------------------

# Configuración de penalidades usada por el Tabu Search cuando opera sobre
# la solución construida por solomon_hard_fleet. Los valores extremadamente
# altos simulan restricciones duras dentro de un marco de penalidades suaves.
HARD_FLEET_PENALTIES = PenaltyConfig(
    cap_weight=5_000.0,    # Penalidad por kg sobre la capacidad del camión
    cap_volume=5_000.0,    # Penalidad por m³ sobre la capacidad volumétrica
    route_duration=5_000.0,  # Penalidad por minuto de exceso en jornada laboral
    visit=50_000.0,        # Penalidad por cada cliente no atendido
    fleet=1_000_000.0,     # Penalidad por abrir una ruta adicional (prohibitivo)
)


def solomon_hard_fleet(
    ctx: ProblemContext,
    seed: int = 42,
) -> Tuple[SolutionRoutes, List[int]]:
    """
    Solomon I1 con restricciones duras de flota, tiempo y capacidad.

    Implementa la heurística constructiva de Solomon (1987) donde en cada
    iteración se abre una nueva ruta inicializada con el cliente más lejano
    al depósito y luego se van insertando los clientes restantes en la posición
    de menor costo c1, priorizando al cliente con mayor beneficio c2 (aquel que
    más perdería si tuviese que abrir su propia ruta). A diferencia de la versión
    estándar, esta variante no fuerza ninguna inserción que viole las restricciones:
    si un cliente no cabe en ninguna ruta factible y el límite de flota está
    agotado, queda en ``unserved`` para ser reagendado al día siguiente.

    Parámetros:
        ctx  -- contexto del problema (VRPTWData + coordenadas).
        seed -- semilla aleatoria para desempate estocástico entre candidatos.

    Retorna (routes, unserved):
      routes   -- lista de rutas factibles, len(routes) <= K_max.
      unserved -- clientes que no pudieron ser asignados a ninguna ruta
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
    # Todos los clientes parten sin asignar
    unserved: Set[int] = set(data.J)
    routes: SolutionRoutes = []

    while unserved:
        # ── Restricción dura: K agotado → clientes restantes sin servicio ─
        # Una vez que se usan todos los camiones disponibles, se detiene la
        # construcción y los clientes no asignados se devuelven como unserved.
        if len(routes) >= data.K_max:
            break

        # ── Selección de semilla: nodo más lejano al depósito ────────────
        # Solomon propone iniciar cada ruta con el cliente más alejado porque
        # es el que tiene menos oportunidad de ser absorbido por otras rutas.
        seed_customer = max(unserved, key=lambda j: data.d[data.depot, j])
        route: List[int] = [data.depot, seed_customer, data.depot]
        unserved.remove(seed_customer)

        # ── Relleno de la ruta actual (igual que Solomon I1 estándar) ────
        # Se sigue insertando clientes en la ruta hasta que ninguno más quepa
        # de forma factible (tiempo, capacidad, límite de clientes por ruta).
        while True:
            base_eval = evaluate_route(route, data)
            best_choice = None  # (c2_score, customer, insertion_pos)

            for c in list(unserved):
                best_for_c = None  # (c1_score, pos): mejor posición de inserción para c

                for pos in range(len(route) - 1):
                    i = route[pos]
                    j = route[pos + 1]
                    # Ruta candidata con c insertado entre i y j
                    cand = route[: pos + 1] + [c] + route[pos + 1 :]
                    cand_eval = evaluate_route(cand, data)

                    # Solo se consideran inserciones estrictamente factibles
                    if not cand_eval.feasible:
                        continue

                    # Hard per-route client limit
                    if len(cand) - 2 > MAX_CLIENTS_PER_ROUTE:
                        continue

                    # Criterio c1 de Solomon (Solomon 1987, eq. 3-4):
                    # c11 mide el desvío de distancia al insertar c entre i y j.
                    # c12 mide el incremento de tiempo total de ruta.
                    # La combinación ponderada 0.7/0.3 equilibra ambos factores.
                    c11 = data.d[i, c] + data.d[c, j] - data.d[i, j]
                    c12 = cand_eval.route_time - base_eval.route_time
                    c1 = 0.7 * c11 + 0.3 * c12

                    if best_for_c is None or c1 < best_for_c[0]:
                        best_for_c = (c1, pos)

                if best_for_c is None:
                    # El cliente c no cabe en ninguna posición de esta ruta
                    continue

                # Criterio c2 de Solomon (maximizar ahorro de abrir ruta propia):
                # c2 = distancia(depósito, c) - c1_star
                # Un c2 alto significa que c pierde mucho si no entra aquí.
                # El ruido uniform evita empates deterministas entre candidatos.
                c1_star, pos_star = best_for_c
                c2 = data.d[data.depot, c] - c1_star + rng.uniform(0.0, 1e-8)

                if best_choice is None or c2 > best_choice[0]:
                    best_choice = (c2, c, pos_star)

            if best_choice is None:
                break  # ningún cliente puede insertarse factiblemente en esta ruta

            # Insertar el cliente con mayor beneficio c2 en su mejor posición
            _, chosen_c, chosen_pos = best_choice
            route.insert(chosen_pos + 1, chosen_c)
            unserved.remove(chosen_c)

        routes.append(route)

    # Los clientes que permanecen en unserved serán reagendados al día siguiente
    return routes, list(unserved)
