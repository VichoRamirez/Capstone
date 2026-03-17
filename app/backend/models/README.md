# Base de datos en `app/models`

Este README documenta la base de datos usada por `app/models/SoluciónHeurística.py`.

## Que base de datos es

- Motor: `SQLite`
- Archivo por defecto: `app/models/solucion_heuristica.db`
- Creacion/uso: en `ensure_database()` dentro de `app/models/SoluciónHeurística.py`
- Importante: esta BD es independiente de la BD `MySQL` definida en `app/database/`

## Para que se usa

La BD guarda todo lo necesario para ejecutar el orquestador de combinaciones:

- configuracion general del experimento
- configuracion por heuristica
- configuracion por metaheuristica
- prioridad de ejecucion de combinaciones heuristica + metaheuristica
- instancia VRP/VRPTW serializada como JSON

## Esquema de tablas

`ensure_database()` crea estas tablas si no existen:

1. `general_config`
- `key` (TEXT, PK)
- `value` (TEXT, JSON serializado)

2. `heuristic_config`
- `name` (TEXT, PK)
- `config_json` (TEXT, JSON serializado)

3. `metaheuristic_config`
- `name` (TEXT, PK)
- `config_json` (TEXT, JSON serializado)

4. `combination_priority`
- `rank` (INTEGER, PK)
- `heuristic_name` (TEXT)
- `meta_name` (TEXT)

5. `instance_store`
- `instance_id` (TEXT, PK)
- `payload_json` (TEXT, JSON serializado)
- `created_at` (TEXT, default `CURRENT_TIMESTAMP`)

## Flujo de funcionamiento

1. Se ejecuta `ensure_database(db_path, force_reseed=False)`.
2. Se crean tablas si faltan.
3. Se insertan valores por defecto (solo si no existen).
4. Si `force_reseed=True`, se limpian tablas y se vuelven a sembrar.
5. Se guarda (si no existe) la instancia base en `instance_store` usando:
   - `instance_id` por defecto: `default_150_15_42`
   - parametros por defecto: `n_customers=150`, `n_trucks=15`, `seed=42`
6. `load_all_configuration()` carga toda la configuracion + payload de instancia.
7. `build_context_from_payload()` reconstruye el contexto para ejecutar heuristicas/metaheuristicas.

## Claves de configuracion general por defecto

En `general_config` se guardan (en JSON):

- `instance_id`
- `n_customers`
- `n_trucks`
- `seed`
- `use_time_windows`
- `top_n_default`
- `penalty_config`
- `local_search_config`

## Prioridad de combinaciones

La tabla `combination_priority` almacena 18 combinaciones (6 heuristicas x 3 metaheuristicas).

La prioridad #1 es fija:
- `SOLOMON_I1_STYLE + TABU_SEARCH`

## Como ejecutar

Desde la carpeta `app/models`:

```bash
python SoluciónHeurística.py
```

Opciones utiles:

```bash
# Listar prioridad sin ejecutar
python SoluciónHeurística.py --list_only

# Ejecutar solo top N combinaciones
python SoluciónHeurística.py --top_n 5

# Re-sembrar la BD (borra datos de tablas y vuelve a cargar defaults)
python SoluciónHeurística.py --reseed_db

# Usar otra ruta de archivo SQLite
python SoluciónHeurística.py --db_path /ruta/mi_bd.db
```

## Consultas rapidas de ejemplo

```sql
-- Ver configuracion general
SELECT key, value FROM general_config;

-- Ver prioridad de ejecucion
SELECT rank, heuristic_name, meta_name
FROM combination_priority
ORDER BY rank;

-- Ver instancias guardadas
SELECT instance_id, created_at FROM instance_store;
```

## Notas de mantenimiento

- Los valores se guardan como JSON en columnas `TEXT`; cualquier cambio de estructura debe ser compatible con `json.loads`.
- Si agregas nuevas heuristicas/metaheuristicas, actualiza:
  - listas `HEURISTICS` y `METAHEURISTICS`
  - defaults en `heuristic_config` y/o `metaheuristic_config`
  - orden en `COMBINATION_PRIORITY`
- Si cambias el formato de `payload_json`, revisa los metodos de encode/decode para evitar errores de reconstruccion.
