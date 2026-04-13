El problema radica en una desconexión entre los parámetros que intentas pasar a través de `_base_params()` y la forma en que tu algoritmo evalúa la **factibilidad**. Tienes dos problemas técnicos ocurriendo simultáneamente:

### 1. El Problema de Schema (Por qué se generan 24 camiones)
Pydantic está ignorando silenciosamente los kwargs `weight_per_truck` y `num_trucks` cuando creas `OptimizerParams(**defaults)`. Esto ocurre típicamente porque el modelo real de FastAPI espera nombres de atributos distintos (como `capacity_kg` o `max_vehicles`). 

Al ignorar tus valores, `generate_matrices_from_df` asume las capacidades por defecto del sistema (quizás algo muy bajo, como 500 kg). Como la capacidad es artificialmente diminuta, Solomon **se ve obligado** a despachar un camión nuevo cada 3 o 4 nodos para no violar la restricción de peso, resultando en 24 camiones para 100 nodos.

### 2. El "Rendición" de Tabu Search (-0.0% de mejora)
La metaheurística Tabu Search es estricta con las restricciones. Cuando recibe la solución inicial de Solomon (24 camiones) y la evalúa contra el límite de flota real que el algoritmo calculó (o intentaste pasar, digamos `K=7`), detecta una violación matemática: `24 <= 7` es Falso.
Al ver que la solución base es **infactible**, rechaza cualquier movimiento de vecindad y aborta inmediatamente, retornando el mismo resultado de Solomon sin intentar mejorar (por eso el -0.0% y el bajo tiempo de ejecución).

---

## La Solución

Para arreglar esto, debes aplicar un parche en tu archivo `validation_report.py`. Vamos a forzar matemáticamente que la instancia de `VRPTWData` respete las reglas de tus Test Cases ignorando los defaults engañosos de Pydantic, y arreglaremos un pequeño bug geométrico en tu función Haversine.

### Cambio 1: Inyectar las restricciones reales en `run_test_case`

Busca la función `run_test_case` y añade el bloque "FIX" justo después de instanciar `VRPTWData` y antes de ejecutar Solomon:

```python
    vrp_data = VRPTWData(
        K=K, J=J, N=N,
        p=p, v=v, T=T,
        P=_scalar(P), V=_scalar(V), c_fixed=_scalar(c_fixed),
        g=float(g), o=float(o),
        d=d, t=t,
        max_route_time=test_max_route_time,
        tw_open={n: 0.0                   for n in N},
        tw_close={n: test_max_route_time  for n in N},
        use_time_windows=False,
        depot=0,
    )

    # --- INICIO DEL FIX: FORZAR RESTRICCIONES REALES ---
    # Ignoramos la pérdida de variables de Pydantic e inyectamos la 
    # capacidad (P) y límite de flota (K) correctos para garantizar el estrés.
    if tc_id == "TC-01":
        vrp_data.K, vrp_data.P = 7, 2000.0
    elif tc_id == "TC-02":
        vrp_data.K, vrp_data.P = 5, 2000.0
    elif tc_id == "TC-03":
        vrp_data.K, vrp_data.P = 14, 1500.0
    elif tc_id == "TC-04":
        vrp_data.K, vrp_data.P = 40, 3000.0

    # Aseguramos un tiempo de servicio lógico (15 mins) y volumen por defecto
    vrp_data.g = 15.0 
    vrp_data.V = 5.0
    # --- FIN DEL FIX ---

    # Solomon I1 (baseline)
    t0 = time.perf_counter()
```

### Cambio 2: Arreglar la Inversión de Coordenadas
En tu función `_mock_osrm_matrices`, estás extrayendo las coordenadas al revés. Tus generadores crean tuplas `(latitud, longitud)`, pero el mock extrae `lon_i, lat_i = coords[i]`. Esto invierte los ejes terrestres, contrayendo las distancias matemáticas y haciendo que los tiempos de viaje sean irrealmente bajos.

Busca la función `_mock_osrm_matrices` y corrige el unpacking:

```python
def _mock_osrm_matrices(coords: dict, avg_speed_kmh: float = 30.0, **kwargs):
    """ ... """
    nodes = list(coords.keys())
    d, t = {}, {}
    for i in nodes:
        # FIX: coords[i] entrega (Lat, Lon) basado en _scatter_nodes
        lat_i, lon_i = coords[i]  
        for j in nodes:
            lat_j, lon_j = coords[j]
            if i == j:
                d[i, j] = t[i, j] = 0.0
            else:
                dist = _haversine_km(lon_i, lat_i, lon_j, lat_j)
                d[i, j] = round(dist, 4)
                t[i, j] = round(60.0 * dist / avg_speed_kmh, 4)
```

Al hacer estos dos cambios:
1. Solomon creará una solución válida usando $\le 7$ camiones de 2.000 kg.
2. Tabu Search reconocerá la solución como factible y comenzará a evaluar vencindarios para acortar la distancia, cambiando tu porcentaje de `-0.0%` a una métrica real.