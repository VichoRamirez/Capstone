# vrp_heuristic_alns.py
import math
import random
import time
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional, Set

# -----------------------------
# Data generation (same spirit as your toy model)
# -----------------------------
def generate_toy_data(n_customers: int = 60, n_trucks: int = 6, seed: int = 42):
    random.seed(seed)

    K = list(range(n_trucks))
    J = list(range(1, n_customers + 1))
    N = [0] + J

    coords: Dict[int, Tuple[int, int]] = {0: (50, 50)}
    for j in J:
        coords[j] = (random.randint(0, 40), random.randint(0, 40))

    p = {j: random.randint(50, 150) for j in J}              # weight demand
    v = {j: round(random.uniform(0.4, 1.5), 2) for j in J}   # volume demand
    T = {j: random.randint(8, 20) for j in J}                # service time (min)
    T[0] = 0

    # fleet (identical)
    P = 2500
    V = 10.0
    c_fixed = 120
    g = 1.3
    o = 8.0
    max_route_time = 300.0

    # distances and travel times
    d: Dict[Tuple[int, int], float] = {}
    t: Dict[Tuple[int, int], float] = {}
    u_speed = 50.0

    for i in N:
        for j in N:
            if i == j:
                d[i, j] = 0.0
                t[i, j] = 0.0
            else:
                xi, yi = coords[i]
                xj, yj = coords[j]
                dist = math.hypot(xi - xj, yi - yj) * 0.6
                d[i, j] = round(dist, 2)
                t[i, j] = round(60.0 * d[i, j] / u_speed, 2)

    # sanity: each customer must be feasible as a direct route
    for j in J:
        if t[0, j] + T[j] + t[j, 0] > max_route_time:
            raise ValueError(
                f"Cliente {j} es infeasible incluso como ruta directa 0->{j}->0 "
                f"(t[0,j]+T[j]+t[j,0]={t[0,j]+T[j]+t[j,0]:.2f} > {max_route_time})."
            )

    return K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t, max_route_time

# -----------------------------
# Route representation and utilities
# -----------------------------
@dataclass
class Route:
    nodes: List[int]     # starts and ends with 0
    load_p: float
    load_v: float
    dist: float
    time: float

def compute_route_metrics(route: List[int],
                          p: Dict[int, float],
                          v: Dict[int, float],
                          T: Dict[int, float],
                          d: Dict[Tuple[int, int], float],
                          t: Dict[Tuple[int, int], float]) -> Tuple[float, float, float, float]:
    load_p = sum(p[n] for n in route if n != 0)
    load_v = sum(v[n] for n in route if n != 0)
    dist = sum(d[route[i], route[i+1]] for i in range(len(route) - 1))
    travel_time = sum(t[route[i], route[i+1]] for i in range(len(route) - 1))
    service_time = sum(T[n] for n in route if n != 0)
    total_time = travel_time + service_time
    return load_p, load_v, dist, total_time

def make_singleton_route(j: int, p, v, T, d, t) -> Route:
    nodes = [0, j, 0]
    lp, lv, dist, tot = compute_route_metrics(nodes, p, v, T, d, t)
    return Route(nodes, lp, lv, dist, tot)

def solution_cost(routes: List[Route], c_fixed: float, g: float, o: float) -> float:
    return sum(c_fixed + r.dist * g / o for r in routes)

def reverse_route_nodes(nodes: List[int]) -> List[int]:
    # [0, a, b, c, 0] -> [0, c, b, a, 0]
    if len(nodes) <= 2:
        return nodes[:]
    middle = nodes[1:-1]
    return [0] + list(reversed(middle)) + [0]

def feasible_route(route: Route, P: float, V: float, max_route_time: float) -> bool:
    return (route.load_p <= P + 1e-9) and (route.load_v <= V + 1e-9) and (route.time <= max_route_time + 1e-9)

def insertion_delta(nodes: List[int], pos: int, customer: int, T, d, t) -> Tuple[float, float]:
    # insert 'customer' between nodes[pos] and nodes[pos+1]
    a = nodes[pos]
    b = nodes[pos+1]
    delta_dist = d[a, customer] + d[customer, b] - d[a, b]
    delta_time = t[a, customer] + t[customer, b] - t[a, b] + T[customer]
    return delta_dist, delta_time

def remove_delta(nodes: List[int], idx: int, T, d, t) -> Tuple[float, float]:
    # remove nodes[idx] (must not be depot)
    prevn = nodes[idx - 1]
    n = nodes[idx]
    nextn = nodes[idx + 1]
    delta_dist = -(d[prevn, n] + d[n, nextn] - d[prevn, nextn])
    delta_time = -(t[prevn, n] + t[n, nextn] - t[prevn, nextn] + T[n])
    return delta_dist, delta_time

def clone_solution(routes: List[Route]) -> List[Route]:
    return [Route(r.nodes[:], r.load_p, r.load_v, r.dist, r.time) for r in routes]

# -----------------------------
# Clarke-Wright Savings initialization (parallel) with feasibility checks
# -----------------------------
def clarke_wright_initial_solution(J: List[int],
                                  p, v, T, d, t,
                                  P: float, V: float,
                                  c_fixed: float, g: float, o: float,
                                  max_route_time: float,
                                  K_max: int) -> List[Route]:
    # Start with one route per customer
    routes: List[Route] = [make_singleton_route(j, p, v, T, d, t) for j in J]
    route_of = {j: idx for idx, j in enumerate(J)}  # singleton mapping

    # Savings list (i < j)
    savings = []
    for i in J:
        for j in J:
            if i >= j:
                continue
            s = d[0, i] + d[0, j] - d[i, j]
            # variable cost saving approx + fixed cost incentive for fewer routes
            s_value = s * g / o + c_fixed
            savings.append((s_value, i, j))
    savings.sort(reverse=True, key=lambda x: x[0])

    def route_endpoints(r: Route) -> Tuple[int, int]:
        # returns (first_customer, last_customer)
        if len(r.nodes) <= 3:
            return (r.nodes[1], r.nodes[1])
        return (r.nodes[1], r.nodes[-2])

    # Try merges in savings order
    for _, i, j in savings:
        if i not in route_of or j not in route_of:
            continue
        ri = route_of[i]
        rj = route_of[j]
        if ri == rj:
            continue

        R1 = routes[ri]
        R2 = routes[rj]
        f1, l1 = route_endpoints(R1)
        f2, l2 = route_endpoints(R2)

        candidates = []

        # Four orientations: connect an end of R1 to an end of R2
        # 1) l1 -> f2 (as is)
        if l1 == i and f2 == j:
            nodes_new = R1.nodes[:-1] + R2.nodes[1:]
            candidates.append(nodes_new)
        # 2) l1 -> l2 (reverse R2)
        if l1 == i and l2 == j:
            R2r = reverse_route_nodes(R2.nodes)
            nodes_new = R1.nodes[:-1] + R2r[1:]
            candidates.append(nodes_new)
        # 3) f1 -> f2 (reverse R1)
        if f1 == i and f2 == j:
            R1r = reverse_route_nodes(R1.nodes)
            nodes_new = R1r[:-1] + R2.nodes[1:]
            candidates.append(nodes_new)
        # 4) f1 -> l2 (reverse both)
        if f1 == i and l2 == j:
            R1r = reverse_route_nodes(R1.nodes)
            R2r = reverse_route_nodes(R2.nodes)
            nodes_new = R1r[:-1] + R2r[1:]
            candidates.append(nodes_new)

        best_route = None
        best_cost = float("inf")

        for nodes_new in candidates:
            lp, lv, dist, tot = compute_route_metrics(nodes_new, p, v, T, d, t)
            rnew = Route(nodes_new, lp, lv, dist, tot)
            if not feasible_route(rnew, P, V, max_route_time):
                continue

            # compute cost of replacing two routes by one
            old_cost = (c_fixed + R1.dist * g / o) + (c_fixed + R2.dist * g / o)
            new_cost = (c_fixed + rnew.dist * g / o)
            if new_cost < old_cost and new_cost < best_cost:
                best_cost = new_cost
                best_route = rnew

        if best_route is None:
            continue

        # Apply merge: replace ri with merged route, delete rj
        # To keep indices consistent, remove higher index first
        keep = ri
        rem = rj
        if rem < keep:
            keep, rem = rem, keep
            # recompute references
            # (we'll rebuild mapping after applying changes to be safe)

        routes[keep] = best_route
        routes.pop(rem)

        # Rebuild mapping route_of from scratch (simpler, O(n))
        route_of = {}
        for idx, r in enumerate(routes):
            for node in r.nodes:
                if node != 0:
                    route_of[node] = idx

        if len(routes) <= K_max:
            # continue merging anyway; fewer vehicles often better with positive fixed cost
            pass

    # If still more routes than available vehicles, try a simple route-elimination repair
    # (greedy reinsert customers from smallest routes)
    while len(routes) > K_max:
        routes.sort(key=lambda r: len(r.nodes))  # smallest first
        victim = routes.pop(0)
        customers = [n for n in victim.nodes if n != 0]

        ok = True
        for cust in customers:
            inserted = False
            best_inc = float("inf")
            best_r = None
            best_pos = None

            for ridx, r in enumerate(routes):
                for pos in range(len(r.nodes) - 1):
                    if r.nodes[pos] == 0 and r.nodes[pos + 1] == 0:
                        continue
                    dd, dt = insertion_delta(r.nodes, pos, cust, T, d, t)
                    new_lp = r.load_p + p[cust]
                    new_lv = r.load_v + v[cust]
                    new_time = r.time + dt
                    if new_lp <= P + 1e-9 and new_lv <= V + 1e-9 and new_time <= max_route_time + 1e-9:
                        inc_cost = dd * g / o
                        if inc_cost < best_inc:
                            best_inc = inc_cost
                            best_r = ridx
                            best_pos = pos

            if best_r is None:
                ok = False
                break

            r = routes[best_r]
            dd, dt = insertion_delta(r.nodes, best_pos, cust, T, d, t)
            r.nodes.insert(best_pos + 1, cust)
            r.load_p += p[cust]
            r.load_v += v[cust]
            r.dist += dd
            r.time += dt
            inserted = True

        if not ok:
            raise RuntimeError(
                "No pude reducir el número de rutas a K (posible infactibilidad con K dado). "
                "Considera aumentar K o relajar max_route_time / service times."
            )

    return routes

# -----------------------------
# Local search (light): intra-route 2-opt
# -----------------------------
def two_opt_route(route: Route, p, v, T, d, t, P, V, max_route_time) -> Route:
    best = Route(route.nodes[:], route.load_p, route.load_v, route.dist, route.time)
    improved = True
    while improved:
        improved = False
        n = len(best.nodes)
        # do not break depots; indices [1, n-2] are customers
        for i in range(1, n - 2):
            for k in range(i + 1, n - 1):
                cand_nodes = best.nodes[:i] + list(reversed(best.nodes[i:k+1])) + best.nodes[k+1:]
                lp, lv, dist, tot = compute_route_metrics(cand_nodes, p, v, T, d, t)
                if tot <= max_route_time + 1e-9 and dist + 1e-9 < best.dist:
                    best = Route(cand_nodes, lp, lv, dist, tot)
                    improved = True
                    break
            if improved:
                break
    return best
def relocate_one(routes: List[Route], p, v, T, d, t, P, V, max_route_time):
    routes2 = clone_solution(routes)
    best_delta = -1e-9
    best_move = None

    for r1_idx, r1 in enumerate(routes2):
        for i in range(1, len(r1.nodes) - 1):
            cust = r1.nodes[i]

            rem_dd, rem_dt = remove_delta(r1.nodes, i, T, d, t)
            new_r1_p = r1.load_p - p[cust]
            new_r1_v = r1.load_v - v[cust]
            new_r1_t = r1.time + rem_dt

            for r2_idx, r2 in enumerate(routes2):
                if r1_idx == r2_idx:
                    continue

                for pos in range(len(r2.nodes) - 1):
                    ins_dd, ins_dt = insertion_delta(r2.nodes, pos, cust, T, d, t)

                    new_r2_p = r2.load_p + p[cust]
                    new_r2_v = r2.load_v + v[cust]
                    new_r2_t = r2.time + ins_dt

                    if (
                        new_r1_p <= P + 1e-9 and
                        new_r1_v <= V + 1e-9 and
                        new_r1_t <= max_route_time + 1e-9 and
                        new_r2_p <= P + 1e-9 and
                        new_r2_v <= V + 1e-9 and
                        new_r2_t <= max_route_time + 1e-9
                    ):
                        delta = rem_dd + ins_dd
                        if delta < best_delta:
                            best_delta = delta
                            best_move = (r1_idx, i, r2_idx, pos, cust, rem_dd, rem_dt, ins_dd, ins_dt)

    if best_move is None:
        return routes2, False

    r1_idx, i, r2_idx, pos, cust, rem_dd, rem_dt, ins_dd, ins_dt = best_move
    r1 = routes2[r1_idx]
    r2 = routes2[r2_idx]

    r1.nodes.pop(i)
    r1.load_p -= p[cust]
    r1.load_v -= v[cust]
    r1.dist += rem_dd
    r1.time += rem_dt

    r2.nodes.insert(pos + 1, cust)
    r2.load_p += p[cust]
    r2.load_v += v[cust]
    r2.dist += ins_dd
    r2.time += ins_dt

    routes2 = [r for r in routes2 if len(r.nodes) > 2]
    refresh_solution(routes2, p, v, T, d, t)
    return routes2, True

def swap_one(routes: List[Route], p, v, T, d, t, P, V, max_route_time):
    routes2 = clone_solution(routes)
    best_delta = -1e-9
    best_move = None

    for r1_idx, r1 in enumerate(routes2):
        for r2_idx, r2 in enumerate(routes2):
            if r2_idx <= r1_idx:
                continue

            for i in range(1, len(r1.nodes) - 1):
                a = r1.nodes[i]
                for j in range(1, len(r2.nodes) - 1):
                    b = r2.nodes[j]

                    cand1 = r1.nodes[:]
                    cand2 = r2.nodes[:]
                    cand1[i] = b
                    cand2[j] = a

                    lp1, lv1, dist1, tot1 = compute_route_metrics(cand1, p, v, T, d, t)
                    lp2, lv2, dist2, tot2 = compute_route_metrics(cand2, p, v, T, d, t)

                    if (
                        lp1 <= P + 1e-9 and lv1 <= V + 1e-9 and tot1 <= max_route_time + 1e-9 and
                        lp2 <= P + 1e-9 and lv2 <= V + 1e-9 and tot2 <= max_route_time + 1e-9
                    ):
                        delta = (dist1 - r1.dist) + (dist2 - r2.dist)
                        if delta < best_delta:
                            best_delta = delta
                            best_move = (r1_idx, r2_idx, cand1, cand2, lp1, lv1, dist1, tot1, lp2, lv2, dist2, tot2)

    if best_move is None:
        return routes2, False

    r1_idx, r2_idx, cand1, cand2, lp1, lv1, dist1, tot1, lp2, lv2, dist2, tot2 = best_move
    routes2[r1_idx] = Route(cand1, lp1, lv1, dist1, tot1)
    routes2[r2_idx] = Route(cand2, lp2, lv2, dist2, tot2)
    return routes2, True

def local_search(routes: List[Route], p, v, T, d, t, P, V, max_route_time) -> List[Route]:
    routes2 = clone_solution(routes)
    routes2 = [two_opt_route(r, p, v, T, d, t, P, V, max_route_time) for r in routes2]

    max_passes = 20
    for _ in range(max_passes):
        improved_any = False

        routes2, improved = relocate_one(routes2, p, v, T, d, t, P, V, max_route_time)
        if improved:
            improved_any = True
            routes2 = [two_opt_route(r, p, v, T, d, t, P, V, max_route_time) for r in routes2]

        routes2, improved = swap_one(routes2, p, v, T, d, t, P, V, max_route_time)
        if improved:
            improved_any = True
            routes2 = [two_opt_route(r, p, v, T, d, t, P, V, max_route_time) for r in routes2]

        if not improved_any:
            break

    return routes2
# -----------------------------
# ALNS operators
# -----------------------------
def get_all_customers(routes: List[Route]) -> List[int]:
    customers = []
    for r in routes:
        customers.extend([n for n in r.nodes if n != 0])
    return customers

def refresh_route(route: Route, p, v, T, d, t):
    lp, lv, dist, tot = compute_route_metrics(route.nodes, p, v, T, d, t)
    route.load_p = lp
    route.load_v = lv
    route.dist = dist
    route.time = tot

def refresh_solution(routes: List[Route], p, v, T, d, t):
    for r in routes:
        refresh_route(r, p, v, T, d, t)

def destroy_random(routes: List[Route], q: int, rng: random.Random, p, v, T, d, t):
    routes2 = clone_solution(routes)
    allcust = get_all_customers(routes2)
    q = min(q, len(allcust))
    removed = rng.sample(allcust, q) if q > 0 else []

    removed_set = set(removed)
    for r in routes2:
        r.nodes = [n for n in r.nodes if n not in removed_set]
        if r.nodes[0] != 0:
            r.nodes.insert(0, 0)
        if r.nodes[-1] != 0:
            r.nodes.append(0)

    routes2 = [r for r in routes2 if len(r.nodes) > 2]
    refresh_solution(routes2, p, v, T, d, t)
    return routes2, removed

def destroy_worst(routes: List[Route], q: int, rng: random.Random, T, d, t, p, v):
    routes2 = clone_solution(routes)
    contrib = []

    for ridx, r in enumerate(routes2):
        for idx in range(1, len(r.nodes) - 1):
            node = r.nodes[idx]
            prevn = r.nodes[idx - 1]
            nextn = r.nodes[idx + 1]
            delta_dist = d[prevn, node] + d[node, nextn] - d[prevn, nextn]
            contrib.append((delta_dist, ridx, node))

    contrib.sort(reverse=True, key=lambda x: x[0])
    removed = [node for _, _, node in contrib[:min(q, len(contrib))]]
    removed_set = set(removed)

    for r in routes2:
        r.nodes = [n for n in r.nodes if n not in removed_set]
        if r.nodes[0] != 0:
            r.nodes.insert(0, 0)
        if r.nodes[-1] != 0:
            r.nodes.append(0)

    routes2 = [r for r in routes2 if len(r.nodes) > 2]
    refresh_solution(routes2, p, v, T, d, t)
    return routes2, removed

def destroy_related(routes: List[Route], q: int, rng: random.Random, d, p, v, T, t):
    routes2 = clone_solution(routes)
    allcust = get_all_customers(routes2)

    if not allcust:
        return routes2, []

    seed = rng.choice(allcust)
    allcust_sorted = sorted(allcust, key=lambda x: d[seed, x])
    removed = allcust_sorted[:min(q, len(allcust_sorted))]
    removed_set = set(removed)

    for r in routes2:
        r.nodes = [n for n in r.nodes if n not in removed_set]
        if r.nodes[0] != 0:
            r.nodes.insert(0, 0)
        if r.nodes[-1] != 0:
            r.nodes.append(0)

    routes2 = [r for r in routes2 if len(r.nodes) > 2]
    refresh_solution(routes2, p, v, T, d, t)
    return routes2, removed

def destroy_route_removal(routes: List[Route], rng: random.Random, p, v, T, d, t):
    routes2 = clone_solution(routes)
    if len(routes2) <= 1:
        return routes2, []

    # priorizar rutas con pocos clientes y poca carga
    score_idx = []
    for i, r in enumerate(routes2):
        n_customers = len([x for x in r.nodes if x != 0])
        score = (n_customers, r.load_p + r.load_v)
        score_idx.append((score, i))

    score_idx.sort(key=lambda x: x[0])
    top = [idx for _, idx in score_idx[:min(3, len(score_idx))]]
    ridx = rng.choice(top)

    victim = routes2.pop(ridx)
    removed = [n for n in victim.nodes if n != 0]

    refresh_solution(routes2, p, v, T, d, t)
    return routes2, removed

def repair_greedy(routes: List[Route], removed: List[int],
                  p, v, T, d, t,
                  P, V, max_route_time,
                  K_max: int,
                  rng: random.Random) -> Optional[List[Route]]:
    routes2 = clone_solution(routes)

    removed = removed[:]
    rng.shuffle(removed)

    for cust in removed:
        best_r = None
        best_pos = None
        best_dd = None
        best_dt = None
        best_inc = float("inf")

        # try insert into existing routes
        for ridx, r in enumerate(routes2):
            for pos in range(len(r.nodes) - 1):
                dd, dt = insertion_delta(r.nodes, pos, cust, T, d, t)
                new_lp = r.load_p + p[cust]
                new_lv = r.load_v + v[cust]
                new_time = r.time + dt
                if new_lp <= P + 1e-9 and new_lv <= V + 1e-9 and new_time <= max_route_time + 1e-9:
                    noise = rng.uniform(0.0, 0.05) * max(1.0, abs(dd))
                    score = dd + noise
                    if score < best_inc:
                        best_inc = score
                        best_r, best_pos = ridx, pos
                        best_dd, best_dt = dd, dt

        if best_r is not None:
            r = routes2[best_r]
            r.nodes.insert(best_pos + 1, cust)
            r.load_p += p[cust]
            r.load_v += v[cust]
            r.dist += best_dd
            r.time += best_dt
        else:
            # open a new route if allowed
            if len(routes2) >= K_max:
                return None
            newr = make_singleton_route(cust, p, v, T, d, t)
            if not feasible_route(newr, P, V, max_route_time):
                return None
            routes2.append(newr)

    # recompute metrics accurately (safety)
    for r in routes2:
        lp, lv, dist, tot = compute_route_metrics(r.nodes, p, v, T, d, t)
        r.load_p, r.load_v, r.dist, r.time = lp, lv, dist, tot

    return routes2

def repair_regret2(routes: List[Route], removed: List[int],
                   p, v, T, d, t,
                   P, V, max_route_time,
                   K_max: int,
                   rng: random.Random) -> Optional[List[Route]]:
    routes2 = clone_solution(routes)
    remaining = removed[:]

    while remaining:
        best_choice = None  # (regret, cust, best_r, best_pos, dd, dt)
        for cust in remaining:
            options = []
            for ridx, r in enumerate(routes2):
                for pos in range(len(r.nodes) - 1):
                    dd, dt = insertion_delta(r.nodes, pos, cust, T, d, t)
                    if r.load_p + p[cust] <= P + 1e-9 and r.load_v + v[cust] <= V + 1e-9 and r.time + dt <= max_route_time + 1e-9:
                        options.append((dd, ridx, pos, dt))
            if options:
                options.sort(key=lambda x: x[0] + rng.uniform(0.0, 0.10) * max(1.0, abs(x[0])))
                best1 = options[0]
                best2 = options[1] if len(options) > 1 else (best1[0] + 1e6, best1[1], best1[2], best1[3])
                regret = best2[0] - best1[0]
                cand = (regret, cust, best1[1], best1[2], best1[0], best1[3])
                if best_choice is None or cand[0] > best_choice[0]:
                    best_choice = cand
            else:
                # no insertion into existing routes; maybe open new route
                if len(routes2) < K_max:
                    newr = make_singleton_route(cust, p, v, T, d, t)
                    if feasible_route(newr, P, V, max_route_time):
                        # treat it as an option with dd=route dist to ease selection
                        cand = (0.0, cust, None, None, None, None)
                        # keep as fallback; still prefer feasible insertions elsewhere
                        if best_choice is None:
                            best_choice = cand

        if best_choice is None:
            return None

        _, cust, ridx, pos, dd, dt = best_choice

        if ridx is None:
            # open new route
            if len(routes2) >= K_max:
                return None
            newr = make_singleton_route(cust, p, v, T, d, t)
            if not feasible_route(newr, P, V, max_route_time):
                return None
            routes2.append(newr)
        else:
            r = routes2[ridx]
            r.nodes.insert(pos + 1, cust)
            r.load_p += p[cust]
            r.load_v += v[cust]
            r.dist += dd
            r.time += dt

        remaining.remove(cust)

    # recompute metrics accurately
    for r in routes2:
        lp, lv, dist, tot = compute_route_metrics(r.nodes, p, v, T, d, t)
        r.load_p, r.load_v, r.dist, r.time = lp, lv, dist, tot

    return routes2

# -----------------------------
# ALNS main loop
# -----------------------------
def alns(routes: List[Route],
         p, v, T, d, t,
         P, V, c_fixed, g, o,
         max_route_time: float,
         K_max: int,
         time_limit_sec: float = 10.0,
         seed: int = 123,
         q_min: int = 5,
         q_max: int = 20,
         temp_factor: float = 0.005,
         cooling: float = 0.995,
         route_removal_weight: float = 2.5,
         regret2_weight: float = 2.0) -> List[Route]:
    rng = random.Random(seed)

    destroy_ops = [
        ("random",  lambda rts, q: destroy_random(rts, q, rng, p, v, T, d, t)),
        ("worst",   lambda rts, q: destroy_worst(rts, q, rng, T, d, t, p, v)),
        ("related", lambda rts, q: destroy_related(rts, q, rng, d, p, v, T, t)),
        ("route_removal", lambda rts, q: destroy_route_removal(rts, rng, p, v, T, d, t)),
    ]
    repair_ops = [
        ("greedy",  lambda rts, rem: repair_greedy(rts, rem, p, v, T, d, t, P, V, max_route_time, K_max, rng)),
        ("regret2", lambda rts, rem: repair_regret2(rts, rem, p, v, T, d, t, P, V, max_route_time, K_max, rng)),
    ]

    # weights (simple adaptive)
    destroy_w = [1.0] * len(destroy_ops)
    repair_w = [1.0] * len(repair_ops)

    for idx, (name, _) in enumerate(destroy_ops):
        if name == "route_removal":
            destroy_w[idx] = route_removal_weight

    for idx, (name, _) in enumerate(repair_ops):
        if name == "regret2":
            repair_w[idx] = regret2_weight
    

    def pick_index(weights):
        s = sum(weights)
        r = rng.random() * s
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r <= acc:
                return i
        return len(weights) - 1

    best = clone_solution(routes)
    best = local_search(best, p, v, T, d, t, P, V, max_route_time)
    best_cost = solution_cost(best, c_fixed, g, o)

    current = clone_solution(best)
    current_cost = best_cost
    n_fail = 0
    n_accept = 0
    n_improve = 0
    n_equal = 0
    n_worse = 0
    n_better_than_current = 0
    # Simulated annealing-style acceptance
    temp = temp_factor * best_cost if best_cost > 0 else 1.0

    t0 = time.time()
    it = 0

    while time.time() - t0 < time_limit_sec:
        it += 1
        q = rng.randint(q_min, q_max)

        di = pick_index(destroy_w)
        ri = pick_index(repair_w)

        partial, removed = destroy_ops[di][1](current, q)
        repaired = repair_ops[ri][1](partial, removed)
        if repaired is None:
            n_fail += 1
            destroy_w[di] *= 0.995
            repair_w[ri] *= 0.995
            temp *= cooling
            continue

        # local improvement
        repaired = local_search(repaired, p, v, T, d, t, P, V, max_route_time)
        cand_cost = solution_cost(repaired, c_fixed, g, o)
        if abs(cand_cost - current_cost) <= 1e-9:
            n_equal += 1
        elif cand_cost < current_cost - 1e-9:
            n_better_than_current += 1
        else:
            n_worse += 1
        

        accept = False
        if cand_cost <= current_cost + 1e-9:
            accept = True
        else:
            prob = math.exp(-(cand_cost - current_cost) / max(1e-9, temp))
            if rng.random() < prob:
                accept = True

        if accept:
            n_accept += 1
            current = repaired
            current_cost = cand_cost

        # update best
        if cand_cost < best_cost - 1e-9:
            n_improve += 1
            best = repaired
            best_cost = cand_cost
            destroy_w[di] *= 1.05
            repair_w[ri] *= 1.05
        else:
            # mild decay
            destroy_w[di] *= 0.999
            repair_w[ri] *= 0.999
        if it % 200 == 0:
            print(
                f"it={it} | best={best_cost:.2f} | current={current_cost:.2f} | "
                f"temp={temp:.2f} | fail={n_fail} | accept={n_accept} | "
                f"improve={n_improve} | equal={n_equal} | "
                f"better_cur={n_better_than_current} | worse={n_worse}"
            )
        temp *= cooling

    return best

# -----------------------------
# Reporting: schedule (service start times)
# -----------------------------
def service_start_times(route_nodes: List[int], T, t) -> Dict[int, float]:
    # earliest schedule starting at depot time 0
    cur = 0.0
    starts = {}
    for idx in range(1, len(route_nodes) - 1):
        prevn = route_nodes[idx - 1]
        n = route_nodes[idx]
        cur += t[prevn, n]
        starts[n] = cur
        cur += T[n]
    return starts

def benchmark_parameter_sets(
    routes0, p, v, T, d, tt,
    P, V, c_fixed, g, o,
    max_route_time, K_max
):
    param_sets = [
        {"q_min": 3, "q_max": 8,  "temp_factor": 0.003, "cooling": 0.995,  "route_removal_weight": 2.0, "regret2_weight": 2.0},
        {"q_min": 3, "q_max": 8,  "temp_factor": 0.005, "cooling": 0.995,  "route_removal_weight": 2.5, "regret2_weight": 2.0},
        {"q_min": 4, "q_max": 10, "temp_factor": 0.005, "cooling": 0.997,  "route_removal_weight": 2.5, "regret2_weight": 2.0},
        {"q_min": 5, "q_max": 12, "temp_factor": 0.005, "cooling": 0.998,  "route_removal_weight": 3.0, "regret2_weight": 2.0},
        {"q_min": 5, "q_max": 15, "temp_factor": 0.008, "cooling": 0.998,  "route_removal_weight": 3.0, "regret2_weight": 2.5},
        {"q_min": 2, "q_max": 6,  "temp_factor": 0.003, "cooling": 0.994,  "route_removal_weight": 2.0, "regret2_weight": 1.5},
        {"q_min": 2, "q_max": 8,  "temp_factor": 0.004, "cooling": 0.996,  "route_removal_weight": 2.5, "regret2_weight": 2.5},
        {"q_min": 4, "q_max": 12, "temp_factor": 0.006, "cooling": 0.997,  "route_removal_weight": 3.5, "regret2_weight": 2.5},
        {"q_min": 6, "q_max": 18, "temp_factor": 0.010, "cooling": 0.9985, "route_removal_weight": 4.0, "regret2_weight": 3.0},
        {"q_min": 8, "q_max": 20, "temp_factor": 0.012, "cooling": 0.999,  "route_removal_weight": 4.0, "regret2_weight": 3.0},
    ]

    results = []

    print("\n=== BENCHMARK DE PARÁMETROS ===\n")

    for idx, params in enumerate(param_sets, start=1):
        print(f"\n--- Configuración {idx}/10 ---")
        print(params)

        t_ini = time.time()
        routes_best = alns(
            routes0, p, v, T, d, tt,
            P, V, c_fixed, g, o,
            max_route_time=max_route_time,
            K_max=K_max,
            time_limit_sec=600.0,   # 10 minutos
            seed=123,
            q_min=params["q_min"],
            q_max=params["q_max"],
            temp_factor=params["temp_factor"],
            cooling=params["cooling"],
            route_removal_weight=params["route_removal_weight"],
            regret2_weight=params["regret2_weight"]
        )

        elapsed = time.time() - t_ini
        best_cost = solution_cost(routes_best, c_fixed, g, o)

        results.append({
            "config_id": idx,
            "cost": best_cost,
            "routes": len(routes_best),
            "time_sec": elapsed,
            **params
        })

        print(f"Resultado config {idx}: costo={best_cost:.2f}, rutas={len(routes_best)}, tiempo={elapsed:.1f}s")

    results.sort(key=lambda x: (x["cost"], x["routes"]))

    print("\n=== RANKING FINAL ===\n")
    for r in results:
        print(
            f"config={r['config_id']} | cost={r['cost']:.2f} | rutas={r['routes']} | "
            f"tiempo={r['time_sec']:.1f}s | q=({r['q_min']},{r['q_max']}) | "
            f"temp_factor={r['temp_factor']} | cooling={r['cooling']} | "
            f"route_removal_weight={r['route_removal_weight']} | regret2_weight={r['regret2_weight']}"
        )

    return results
def main():
    # Change n_customers to 140-160 to test scaling (watch feasibility with 6 trucks and current T)
    n_customers = 150
    n_trucks = 30

    K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, tt, max_route_time = generate_toy_data(
        n_customers=n_customers, n_trucks=n_trucks, seed=42
    )

    # 1) Initial solution via Savings
    routes0 = clarke_wright_initial_solution(
        J, p, v, T, d, tt,
        P, V,
        c_fixed, g, o,
        max_route_time,
        K_max=len(K)
    )
    routes0 = local_search(routes0, p, v, T, d, tt, P, V, max_route_time)
    cost0 = solution_cost(routes0, c_fixed, g, o)

    print("=== HEURÍSTICA: SOLUCIÓN INICIAL ===")
    print(f"Rutas: {len(routes0)} | Costo: {cost0:.2f}")

    # 2) Improve with ALNS

    routes_best = alns(
        routes0, p, v, T, d, tt,
        P, V, c_fixed, g, o,
        max_route_time=max_route_time,
        K_max=len(K),
        time_limit_sec=10.0,
        seed=123,
        q_min=3,
        q_max=8
    )
    best_cost = solution_cost(routes_best, c_fixed, g, o)

    print("\n=== HEURÍSTICA: SOLUCIÓN MEJORADA (ALNS) ===")
    print(f"Rutas: {len(routes_best)} | Costo: {best_cost:.2f}\n")

    for idx, r in enumerate(routes_best, start=1):
        print(f"Vehículo {idx} | carga_p={r.load_p:.1f} | carga_v={r.load_v:.2f} | dist={r.dist:.2f} | tiempo={r.time:.2f}")
        print("  Ruta:", r.nodes)
        starts = service_start_times(r.nodes, T, tt)
        for n in r.nodes:
            if n != 0:
                print(f"    Cliente {n}: inicio servicio = {starts[n]:.2f} min")
        print()

if __name__ == "__main__":
    main()
