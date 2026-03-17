# vrp_exact_gurobi.py
import gurobipy as gp
from gurobipy import GRB
import math
import random
from typing import Dict, Tuple, List, Set

def generate_toy_data(n_customers: int = 60, n_trucks: int = 6, seed: int = 42):
    random.seed(seed)

    K = list(range(n_trucks))
    J = list(range(1, n_customers + 1))
    N = [0] + J

    # Coordinates
    coords: Dict[int, Tuple[int, int]] = {0: (50, 50)}
    for j in J:
        coords[j] = (random.randint(0, 40), random.randint(0, 40))

    # Demands and service times
    p = {j: random.randint(50, 150) for j in J}              # weight
    v = {j: round(random.uniform(0.4, 1.5), 2) for j in J}   # volume
    T = {j: random.randint(8, 20) for j in J}                # service time (min)
    T[0] = 0

    # Identical vehicles (interchangeable)
    P = {k: 2500 for k in K}   # weight capacity
    V = {k: 10.0 for k in K}   # volume capacity
    c = {k: 120 for k in K}    # fixed cost if vehicle used
    g = 1.3                    # distance cost multiplier
    o = {k: 8.0 for k in K}    # "efficiency" divisor (as in your code)

    # Distances and travel times
    d: Dict[Tuple[int, int], float] = {}
    t: Dict[Tuple[int, int], float] = {}
    u: Dict[Tuple[int, int], float] = {}

    for i in N:
        for j in N:
            if i == j:
                d[i, j] = 0.0
                u[i, j] = 50.0
                t[i, j] = 0.0
            else:
                xi, yi = coords[i]
                xj, yj = coords[j]
                dist = math.hypot(xi - xj, yi - yj) * 0.6
                d[i, j] = round(dist, 2)
                u[i, j] = 50.0
                t[i, j] = round(60.0 * d[i, j] / u[i, j], 2)

    return K, J, N, coords, p, v, T, P, V, c, g, o, d, t

def build_candidate_arcs(
    N: List[int],
    J: List[int],
    d: Dict[Tuple[int, int], float],
    t: Dict[Tuple[int, int], float],
    T: Dict[int, float],
    max_route_time: float = 300.0,
    k_nearest: int = 7
) -> List[Tuple[int, int]]:
    A: Set[Tuple[int, int]] = set()

    # K-nearest candidate arcs per node (prune arcs that can never fit in remaining time)
    for i in N:
        candidates = []
        for j in N:
            if i == j:
                continue
            if j != 0:
                # Necessary condition: even if going directly j->0 after service, must fit 300
                if t[i, j] + T[j] + t[j, 0] > max_route_time:
                    continue
            candidates.append(j)

        candidates.sort(key=lambda jj: d[i, jj])
        for j in candidates[:min(k_nearest, len(candidates))]:
            A.add((i, j))

    # Always allow depot -> client if client is feasible at all
    for j in J:
        if t[0, j] + T[j] + t[j, 0] <= max_route_time:
            A.add((0, j))
        else:
            raise ValueError(
                f"Cliente {j} es infeasible incluso como ruta directa 0->{j}->0 "
                f"(t[0,j]+T[j]+t[j,0]={t[0,j]+T[j]+t[j,0]:.2f} > {max_route_time})."
            )

    # Always allow client -> depot return
    for i in J:
        A.add((i, 0))

    return list(A)

def build_in_out_arcs(N: List[int], A: List[Tuple[int, int]]):
    in_arcs = {j: [] for j in N}
    out_arcs = {i: [] for i in N}
    for (i, j) in A:
        out_arcs[i].append(j)
        in_arcs[j].append(i)
    return in_arcs, out_arcs

def extract_route_from_arcs(arcs_k: List[Tuple[int, int]]) -> List[int]:
    # Build successor map
    succ = {}
    for (i, j) in arcs_k:
        succ[i] = j

    route = [0]
    cur = 0
    visited = set([0])
    while True:
        if cur not in succ:
            break
        nxt = succ[cur]
        route.append(nxt)
        if nxt == 0:
            break
        if nxt in visited:
            # loop detected (should not happen if model is correct and solved)
            break
        visited.add(nxt)
        cur = nxt
    return route

def solve_vrp_exact(
    n_customers: int = 60,
    n_trucks: int = 6,
    seed: int = 42,
    time_limit_sec: int = 20,
    mip_gap: float = 0.01,
    k_nearest: int = 5,
    max_route_time: float = 300.0
):
    K, J, N, coords, p, v, T, P, V, c, g, o, d, tt = generate_toy_data(
        n_customers=n_customers, n_trucks=n_trucks, seed=seed
    )

    A = build_candidate_arcs(N, J, d, tt, T, max_route_time=max_route_time, k_nearest=k_nearest)
    in_arcs, out_arcs = build_in_out_arcs(N, A)

    # Quick connectivity sanity check
    for j in J:
        if len(in_arcs[j]) == 0 or len(out_arcs[j]) == 0:
            raise RuntimeError(f"Cliente {j} quedó aislado en el grafo candidato. Sube k_nearest.")

    m = gp.Model("VRP_multiattr_duration")

    # Decision variables
    x = m.addVars(((i, j, k) for (i, j) in A for k in K),
                  vtype=GRB.BINARY, name="x")

    w = m.addVars(K, vtype=GRB.BINARY, name="w")

    y = m.addVars(((i, k) for i in N for k in K),
                  lb=0.0, ub=max_route_time,
                  vtype=GRB.CONTINUOUS, name="y")

    # Helper: whether vehicle k visits client j
    visit = {(j, k): gp.quicksum(x[i, j, k] for i in in_arcs[j]) for j in J for k in K}

    # Big-M values for temporal arcs (as in your approach)
    M_arc = {(i, j): max_route_time + T[i] + tt[i, j] for (i, j) in A if j != 0}

    # Objective
    m.setObjective(
        gp.quicksum(c[k] * w[k] for k in K) +
        gp.quicksum(x[i, j, k] * d[i, j] * g / o[k] for (i, j) in A for k in K),
        GRB.MINIMIZE
    )

    # Depot degree constraints (if vehicle used -> exactly one departure and one return)
    for k in K:
        m.addConstr(
            gp.quicksum(x[0, j, k] for j in out_arcs[0] if j != 0) == w[k],
            name=f"depart_{k}"
        )
        m.addConstr(
            gp.quicksum(x[i, 0, k] for i in in_arcs[0] if i != 0) == w[k],
            name=f"return_{k}"
        )

    # Capacity constraints (multi-dimensional)
    for k in K:
        m.addConstr(
            gp.quicksum(v[j] * visit[j, k] for j in J) <= V[k],
            name=f"cap_volume_{k}"
        )
        m.addConstr(
            gp.quicksum(p[j] * visit[j, k] for j in J) <= P[k],
            name=f"cap_weight_{k}"
        )

    # Each client visited exactly once (across all vehicles)
    for j in J:
        m.addConstr(
            gp.quicksum(x[i, j, k] for k in K for i in in_arcs[j]) == 1,
            name=f"visit_once_{j}"
        )

    # Flow conservation for each client and vehicle
    for k in K:
        for j in J:
            m.addConstr(
                gp.quicksum(x[i, j, k] for i in in_arcs[j]) ==
                gp.quicksum(x[j, i, k] for i in out_arcs[j]),
                name=f"flow_{j}_{k}"
            )

    # Time anchoring at depot
    for k in K:
        m.addConstr(y[0, k] == 0, name=f"time_depot_{k}")

    # Tighten time variable bounds conditional on being visited (helps performance)
    # Earliest possible start at j is at least direct travel from depot (lower bound)
    # Latest possible start at j must allow direct return to depot after service (upper bound)
    for k in K:
        for j in J:
            earliest = tt[0, j]
            latest = max_route_time - T[j] - tt[j, 0]
            m.addConstr(y[j, k] >= earliest * visit[j, k], name=f"time_lb_{j}_{k}")
            m.addConstr(y[j, k] <= latest + max_route_time * (1 - visit[j, k]), name=f"time_ub_{j}_{k}")

    # Time propagation along active arcs (subtour elimination via temporal consistency)
    for k in K:
        for (i, j) in A:
            if j != 0:
                m.addConstr(
                    y[j, k] >= y[i, k] + T[i] + tt[i, j] - M_arc[i, j] * (1 - x[i, j, k]),
                    name=f"timeprop_{i}_{j}_{k}"
                )

    # Route duration: if arc i->0 is used, must get back within horizon
    for k in K:
        for i in J:
            if (i, 0) in A:
                m.addConstr(
                    y[i, k] + T[i] + tt[i, 0] <= max_route_time + max_route_time * (1 - x[i, 0, k]),
                    name=f"max_duration_{i}_{k}"
                )

    # Symmetry breaking: use vehicles in order
    for kk in range(len(K) - 1):
        m.addConstr(w[K[kk]] >= w[K[kk + 1]], name=f"sym_{kk}")

    # Lower bound on number of vehicles from total demands
    LB_p = math.ceil(sum(p[j] for j in J) / max(P[k] for k in K))
    LB_v = math.ceil(sum(v[j] for j in J) / max(V[k] for k in K))
    LB = max(LB_p, LB_v)
    m.addConstr(gp.quicksum(w[k] for k in K) >= LB, name="lb_vehicles")

    # Parameters (close to your settings)
    m.Params.MIPFocus = 1
    m.Params.OutputFlag = 1
    m.Params.TimeLimit = time_limit_sec
    m.Params.Heuristics = 0.6
    m.Params.Presolve = 2
    m.Params.Cuts = 1
    m.Params.NoRelHeurTime = min(10, time_limit_sec)
    m.Params.MIPGap = mip_gap

    m.optimize()

    if m.status not in [GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL]:
        print("No se encontró solución factible.")
        return

    print("\n=== SOLUCIÓN ===")
    print(f"Status: {m.Status}")
    print(f"Valor objetivo: {m.ObjVal:.2f}\n")

    for k in K:
        if w[k].X > 0.5:
            arcs_k = [(i, j) for (i, j) in A if x[i, j, k].X > 0.5]
            route = extract_route_from_arcs(arcs_k)
            print(f"Camión {k+1} usado")
            print("Ruta:", route)

            for node in route:
                if node != 0:
                    print(f"  Cliente {node}: inicio servicio = {y[node, k].X:.2f} min")
            print()

if __name__ == "__main__":
    # Ejemplo: reproduce el caso con 60 nodos, 6 camiones, 20s
    solve_vrp_exact(
        n_customers=60,
        n_trucks=6,
        seed=42,
        time_limit_sec=20,
        mip_gap=0.01,
        k_nearest=5,
        max_route_time=300.0
    )

    # Para escalar (ojo con factibilidad por tiempos de servicio):
    # solve_vrp_exact(n_customers=160, n_trucks=6, time_limit_sec=60, k_nearest=12)
