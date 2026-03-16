import gurobipy as gp
from gurobipy import GRB
import math
import random

# ============================================================
# DATOS DE JUGUETE
# ============================================================

random.seed(42)

# Conjuntos
K = list(range(4))          # camiones
J = list(range(1, 40))      # clientes
N = [0] + J                 # 0 = CD

# Coordenadas
coords = {0: (50, 50)}
for j in J:
    coords[j] = (random.randint(0, 40), random.randint(0, 40))

# Demandas y tiempos de servicio
p = {j: random.randint(50, 150) for j in J}
v = {j: round(random.uniform(0.4, 1.5), 2) for j in J}
T = {j: random.randint(8, 20) for j in J}
T[0] = 0

# Capacidades por camión
P = {k: 2500 for k in K}
V = {k: 10.0 for k in K}

# Costos
c = {k: random.choice([100, 120, 140]) for k in K}
g = 1.3
o = {k: random.choice([7.0, 8.0, 9.0]) for k in K}

# ============================================================
# PRECALCULAR DISTANCIAS Y TIEMPOS
# ============================================================

d = {}
u = {}
t = {}

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

# ============================================================
# GENERAR ARCOS FACTIBLES CON K-NEAREST
# ============================================================

A = set()
k_nearest = 4

for i in N:
    candidatos = []

    for j in N:
        if i == j:
            continue

        # filtro básico de factibilidad temporal hacia j
        if j != 0 and t[i, j] + T[j] + t[j, 0] > 300:
            continue

        candidatos.append(j)

    candidatos.sort(key=lambda j: d[i, j])
    vecinos = candidatos[:min(k_nearest, len(candidatos))]

    for j in vecinos:
        A.add((i, j))

# siempre permitir salida desde CD a clientes factibles
for j in J:
    if t[0, j] + T[j] + t[j, 0] <= 300:
        A.add((0, j))

# siempre permitir regreso al CD
for i in J:
    A.add((i, 0))

A = list(A)

# ============================================================
# PRECALCULAR ENTRADAS Y SALIDAS
# ============================================================

in_arcs = {j: [] for j in N}
out_arcs = {i: [] for i in N}

for (i, j) in A:
    out_arcs[i].append(j)
    in_arcs[j].append(i)

# chequeo opcional de conectividad
for j in J:
    if len(in_arcs[j]) == 0 or len(out_arcs[j]) == 0:
        print(f"Advertencia: cliente {j} quedó aislado en el grafo de arcos.")

# ============================================================
# MODELO
# ============================================================

m = gp.Model("VRP_toy")

# ============================================================
# VARIABLES
# ============================================================

x = m.addVars(((i, j, k) for (i, j) in A for k in K),
              vtype=GRB.BINARY, name="x")

w = m.addVars(K, vtype=GRB.BINARY, name="w")

# tiempo de inicio de servicio
y = m.addVars(((i, k) for i in N for k in K),
              lb=0.0, ub=300.0,
              vtype=GRB.CONTINUOUS, name="y")

# Big-M por arco
M_arc = {(i, j): 300.0 + T[i] + t[i, j] for (i, j) in A if j != 0}

# ============================================================
# FUNCIÓN OBJETIVO
# ============================================================

m.setObjective(
    gp.quicksum(c[k] * w[k] for k in K) +
    gp.quicksum(x[i, j, k] * d[i, j] * g / o[k] for (i, j) in A for k in K),
    GRB.MINIMIZE
)

# ============================================================
# RESTRICCIONES
# ============================================================

# R1a: si el camión k se usa, sale una vez del CD
for k in K:
    m.addConstr(
        gp.quicksum(x[0, j, k] for j in out_arcs[0] if j != 0) == w[k],
        name=f"sale_CD_{k}"
    )

# R1b: si el camión k se usa, vuelve una vez al CD
for k in K:
    m.addConstr(
        gp.quicksum(x[i, 0, k] for i in in_arcs[0] if i != 0) == w[k],
        name=f"vuelve_CD_{k}"
    )

# Fortalecimiento: si se usa un arco con camión k, entonces el camión k está activo
for (i, j) in A:
    for k in K:
        m.addConstr(
            x[i, j, k] <= w[k],
            name=f"activa_camion_{i}_{j}_{k}"
        )

# R2: capacidad en volumen
for k in K:
    m.addConstr(
        gp.quicksum(
            v[j] * gp.quicksum(x[i, j, k] for i in in_arcs[j])
            for j in J
        ) <= V[k],
        name=f"cap_vol_{k}"
    )

# R3: capacidad en peso
for k in K:
    m.addConstr(
        gp.quicksum(
            p[j] * gp.quicksum(x[i, j, k] for i in in_arcs[j])
            for j in J
        ) <= P[k],
        name=f"cap_peso_{k}"
    )

# R4: cada cliente debe ser visitado exactamente una vez
for j in J:
    m.addConstr(
        gp.quicksum(x[i, j, k] for k in K for i in in_arcs[j]) == 1,
        name=f"entra_cliente_{j}"
    )

# Conservación de flujo por camión
for k in K:
    for j in J:
        m.addConstr(
            gp.quicksum(x[i, j, k] for i in in_arcs[j]) ==
            gp.quicksum(x[j, i, k] for i in out_arcs[j]),
            name=f"flujo_{j}_{k}"
        )

# Tiempo en el CD = 0
for k in K:
    m.addConstr(y[0, k] == 0, name=f"tiempo_CD_{k}")

# Restricciones temporales
for k in K:
    for (i, j) in A:
        if j != 0:
            m.addConstr(
                y[j, k] >= y[i, k] + T[i] + t[i, j] - M_arc[i, j] * (1 - x[i, j, k]),
                name=f"tiempo_{i}_{j}_{k}"
            )

# Duración máxima de ruta
for k in K:
    for i in J:
        if (i, 0) in A:
            m.addConstr(
                y[i, k] + T[i] + t[i, 0] <= 300 + 300 * (1 - x[i, 0, k]),
                name=f"duracion_max_{i}_{k}"
            )

# Romper simetría
for k in range(len(K) - 1):
    m.addConstr(
        w[K[k]] >= w[K[k + 1]],
        name=f"simetria_{k}"
    )

# ============================================================
# SUBTOURS EXPLÍCITOS (NO USAR; SOLO REFERENCIA)
# ============================================================
#
# from itertools import combinations
# for k in K:
#     for r in range(2, len(J) + 1):
#         for S in combinations(J, r):
#             m.addConstr(
#                 gp.quicksum(x[i, j, k] for i in S for j in S if i != j and (i, j) in A)
#                 <= len(S) - 1,
#                 name=f"subtour_{k}_{'_'.join(map(str,S))}"
#             )
#

# ============================================================
# PARÁMETROS DEL SOLVER
# ============================================================

m.Params.MIPFocus = 1
m.Params.OutputFlag = 1
m.Params.TimeLimit = 60
m.Params.Heuristics = 0.3
# ============================================================
# OPTIMIZAR
# ============================================================

m.optimize()

# ============================================================
# RESULTADOS
# ============================================================

if m.status in [GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL]:
    print("\n=== SOLUCIÓN ===")
    print(f"Valor objetivo: {m.ObjVal:.2f}\n")

    for k in K:
        if w[k].X > 0.5:
            print(f"Camión {k} usado")

            arcs_k = [(i, j) for (i, j) in A if x[i, j, k].X > 0.5]
            print("Arcos:", arcs_k)

            # reconstrucción simple de ruta
            ruta = [0]
            actual = 0
            visitados = set()

            while True:
                siguientes = [j for (i, j) in arcs_k if i == actual]
                if not siguientes:
                    break

                nxt = siguientes[0]

                # evitar loops raros en la reconstrucción
                if (actual, nxt) in visitados:
                    break

                visitados.add((actual, nxt))
                ruta.append(nxt)
                actual = nxt

                if actual == 0:
                    break

            print("Ruta:", ruta)

            for i in ruta:
                if i != 0:
                    print(f"  Cliente {i}: inicio servicio = {y[i, k].X:.2f} min")
            print()
else:
    print("No se encontró solución factible.")