import gurobipy as gp
from gurobipy import GRB
import math
import random

# ============================================================
# DATOS DE JUGUETE
# ============================================================

random.seed(42)

# Conjuntos
K = list(range(4))         # 10 camiones: 0,...,9
J = list(range(1, 40))      # 50 clientes: 1,...,50
N = [0] + J                 # 0 = CD

# Coordenadas (solo para generar distancias de juguete)

coords = {0: (50, 50)}  # CD
for j in J:
    coords[j] = (random.randint(0, 40), random.randint(0, 40))

# Demandas
p = {j: random.randint(50, 150) for j in J}
v = {j: round(random.uniform(0.4, 1.5), 2) for j in J}
T = {j: random.randint(8, 20) for j in J}
T[0] = 0
T[0] = 0  # en CD no hay servicio

# Capacidades por camión
P = {k: 2500 for k in K}
V = {k: 10.0 for k in K}

# Costos
c = {k: random.choice([100, 120, 140]) for k in K}
g = 1.3                # $ / litro
o = {k: random.choice([7.0, 8.0, 9.0]) for k in K}
# Velocidades promedio (km/h) y distancias (km)
# ============================================================
# MEJORA 3: precalcular t_ij
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
            dist = math.hypot(xi - xj, yi - yj) * 0.6   # escala simple
            d[i, j] = round(dist, 2)
            u[i, j] = 50.0                             # km/h promedio
            t[i, j] = round(60.0 * d[i, j] / u[i, j], 2)  # min

# ============================================================
# MEJORA 1 + adicional útil: eliminar arcos inviables
# Definimos solo arcos factibles A
# ============================================================

A = set()
k_nearest = 5

for i in N:
    candidatos = []

    for j in N:
        if i == j:
            continue

        if j != 0 and t[i, j] + T[j] + t[j, 0] > 300:
            continue

        candidatos.append(j)

    candidatos.sort(key=lambda j: d[i, j])

    vecinos = candidatos[:min(k_nearest, len(candidatos))]
    for j in vecinos:
        A.add((i, j))

# siempre permitir salida desde CD a todo cliente factible
for j in J:
    if t[0, j] + T[j] + t[j, 0] <= 300:
        A.add((0, j))

# siempre permitir regreso al CD
for i in J:
    A.add((i, 0))

# opcional: permitir también el cliente más lejano factible de cada nodo
for i in N:
    factibles = [j for j in N if i != j and (j == 0 or t[i, j] + T[j] + t[j, 0] <= 300)]
    if factibles:
        j_far = max(factibles, key=lambda j: d[i, j])
        A.add((i, j_far))

A = list(A)
# ============================================================
# MODELO
# ============================================================

m = gp.Model("VRP_toy")

# Variables
x = m.addVars(((i, j, k) for (i, j) in A for k in K),
              vtype=GRB.BINARY, name="x")

w = m.addVars(K, vtype=GRB.BINARY, name="w")

# y_i = tiempo de inicio de servicio en el nodo i
# Como el tiempo depende de qué camión hace la ruta,
# es más robusto usar y[j,k] para el cliente j en el camión k.
y = m.addVars(((i, k) for i in N for k in K),
              lb=0.0, vtype=GRB.CONTINUOUS, name="y")

# ============================================================
# MEJORA 4: Big-M pequeño
# Como la ruta no puede exceder 300 min, usamos M=300
# ============================================================

M = 300.0

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
        gp.quicksum(x[0, j, k] for j in J if (0, j) in A) == w[k],
        name=f"sale_CD_{k}"
    )

# R1b: si el camión k se usa, vuelve una vez al CD
for k in K:
    m.addConstr(
        gp.quicksum(x[i, 0, k] for i in J if (i, 0) in A) == w[k],
        name=f"vuelve_CD_{k}"
    )

# R2: capacidad en volumen
for k in K:
    m.addConstr(
        gp.quicksum(v[j] * gp.quicksum(x[i, j, k] for i in N if (i, j) in A)
                    for j in J) <= V[k],
        name=f"cap_vol_{k}"
    )

# R3: capacidad en peso
for k in K:
    m.addConstr(
        gp.quicksum(p[j] * gp.quicksum(x[i, j, k] for i in N if (i, j) in A)
                    for j in J) <= P[k],
        name=f"cap_peso_{k}"
    )

# R4: cada cliente debe ser visitado exactamente una vez
for j in J:
    m.addConstr(
        gp.quicksum(x[i, j, k] for k in K for i in N if (i, j) in A) == 1,
        name=f"entra_cliente_{j}"
    )

# Conservación de flujo por camión
for k in K:
    for j in J:
        m.addConstr(
            gp.quicksum(x[i, j, k] for i in N if (i, j) in A) ==
            gp.quicksum(x[j, i, k] for i in N if (j, i) in A),
            name=f"flujo_{j}_{k}"
        )

# Tiempo en el CD = 0
for k in K:
    m.addConstr(y[0, k] == 0, name=f"tiempo_CD_{k}")

# ============================================================
# MEJORA 2: usar restricciones temporales para romper subtours
# ============================================================
for k in K:
    for (i, j) in A:
        if j != 0:
            m.addConstr(
                y[j, k] >= y[i, k] + T[i] + t[i, j] - M * (1 - x[i, j, k]),
                name=f"tiempo_{i}_{j}_{k}"
            )

# Duración máxima de ruta:
# si el camión sale desde i al CD, entonces debe cumplirse
# y_i + T_i + t_i0 <= 300
for k in K:
    for i in J:
        if (i, 0) in A:
            m.addConstr(
                y[i, k] + T[i] + t[i, 0] <= 300 + M * (1 - x[i, 0, k]),
                name=f"duracion_max_{i}_{k}"
            )

# ============================================================
# MEJORA 5: romper simetría
# obliga a usar primero el camión 0 y luego el 1
# ============================================================
for k in range(len(K) - 1):
    m.addConstr(w[K[k]] >= w[K[k + 1]], name=f"simetria_{k}")

# Eliminar autoarcos (realmente ya no aparecen porque no los pusimos en A)
# pero si quieres dejar la idea explícita:
# for k in K:
#     for i in N:
#         if (i, i) in A:
#             m.addConstr(x[i, i, k] == 0, name=f"autoarco_{i}_{k}")

# ============================================================
# SUBTOURS EXPLÍCITOS (NO USAR; SOLO COMO REFERENCIA)
# Los dejamos comentados, tal como pediste
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

m.params.MIPFocus = 1
m.Params.OutputFlag = 1
m.Params.TimeLimit = 300
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
            while True:
                siguientes = [j for (i, j) in arcs_k if i == actual]
                if not siguientes:
                    break
                nxt = siguientes[0]
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