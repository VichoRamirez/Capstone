# Capstone Analytics — Dispatch Optimizer

Aplicación de escritorio SaaS para **optimización de rutas de reparto (VRP/VRPTW)**. Las empresas suben sus órdenes de venta en CSV, el sistema geocodifica las direcciones, agrupa por día de despacho y ejecuta una heurística Solomon I1 + Tabu Search para producir rutas optimizadas de camiones, visualizadas en un mapa interactivo.

**Rama activa:** `saas-newModel` (base estable: `main`)

---

## Índice

1. [Estructura del repositorio](#estructura-del-repositorio)
2. [Arquitectura de la app](#arquitectura-de-la-app)
3. [Pipeline de optimización](#pipeline-de-optimización)
4. [Formato de datos de entrada](#formato-de-datos-de-entrada)
5. [Instalación paso a paso (local)](#instalación-paso-a-paso-local)
6. [Servicios externos requeridos](#servicios-externos-requeridos)
7. [Variables de entorno](#variables-de-entorno)
8. [Cómo ejecutar la app](#cómo-ejecutar-la-app)
9. [Tests](#tests)

---

## Estructura del repositorio

```
Capstone/
│
├── app/                          # Código fuente de la aplicación
│   ├── main.py                   # Punto de entrada del frontend (PyQt6)
│   ├── qt_runtime.py             # Configuración del runtime Qt
│   ├── validation_heuristics.py  # solomon_hard_fleet + HARD_FLEET_PENALTIES
│   ├── validation_report.py      # Generador de informe de validación del modelo
│   │
│   ├── backend/                  # Servidor FastAPI (puerto 8000)
│   │   ├── main.py               # Punto de entrada del backend
│   │   ├── api/
│   │   │   └── router.py         # Todos los endpoints REST + loop multi-día
│   │   ├── models/               # Modelos de optimización VRP
│   │   │   ├── routing/
│   │   │   │   ├── heuristics.py           # Clarke-Wright savings + ALNS
│   │   │   │   ├── literature_heuristics.py # Solomon I1 (heurística activa)
│   │   │   │   └── metaheuristics.py        # Tabu Search (mejora activa)
│   │   │   ├── Modelo*.py        # Formulaciones exactas con Gurobi (requieren licencia)
│   │   │   └── SoluciónHeurística.py
│   │   ├── schemas/
│   │   │   └── optimization.py   # OptimizerParams + schemas de auth
│   │   └── services/             # Lógica de negocio
│   │       ├── auth_service.py
│   │       ├── cleaning_service.py
│   │       ├── data_service.py
│   │       ├── db_persistence.py
│   │       ├── fuel_price_service.py
│   │       ├── job_store.py
│   │       ├── optimizer_service.py
│   │       ├── road_routing.py
│   │       └── traffic_factor_dataset_builder.py
│   │
│   ├── config/
│   │   ├── settings.py           # Lee .env y expone configuración global
│   │   ├── .env                  # Variables de entorno (NO se sube a git)
│   │   └── .env.example          # Plantilla de variables de entorno
│   │
│   ├── database/
│   │   ├── connection.py         # Engine SQLAlchemy + get_session()
│   │   ├── models.py             # Modelos ORM (Usuario, Producto, Venta, Detalle)
│   │   └── repositories/         # CRUD por tabla
│   │       ├── base_repository.py
│   │       ├── usuario_repository.py
│   │       ├── producto_repository.py
│   │       ├── venta_repository.py
│   │       └── detalle_repository.py
│   │
│   ├── frontend/                 # Interfaz de usuario PyQt6
│   │   ├── app.py                # QMainWindow + navegación entre pantallas
│   │   ├── views/
│   │   │   ├── login_view.py     # Login, registro y reset de contraseña
│   │   │   └── main_view.py      # Workspace principal (Data Hub, Optimizador, Resultados)
│   │   ├── widgets/
│   │   │   └── components.py     # Componentes Qt reutilizables
│   │   ├── resources/
│   │   │   └── styles/
│   │   └── workers/              # QThreads para operaciones largas
│   │       ├── health_worker.py
│   │       ├── upload_worker.py
│   │       ├── data_hub_worker.py
│   │       ├── progress_worker.py
│   │       └── request_worker.py
│   │
│   └── __tests__/                # Suite de pruebas
│       ├── conftest.py
│       ├── unit/
│       ├── integration/
│       ├── stress/
│       └── results/              # Reportes generados por pytest y locust
│
├── RAW_DATA/                     # Datasets de ejemplo (datos ficticios de Santiago)
├── CLEAN_DATA/                   # Versiones pre-limpias de los datasets
├── SCRIPTS/                      # Notebooks y scripts de exploración
├── test_cases/                   # Exportaciones CSV de corridas de prueba (informe técnico)
├── cache/                        # Caché de resultados OSRM (generado automáticamente)
├── geocache.json                 # Caché persistente de geocodificación Nominatim
└── TODO.md                       # Deuda técnica y trabajo pendiente
```

### Carpetas que no forman parte del flujo principal

| Carpeta / Archivo | Por qué existe |
|---|---|
| `SCRIPTS/` | Exploración de datos durante el desarrollo. No es parte de la app. |
| `RAW_DATA/` | Datasets ficticios para pruebas. Se pueden subir directamente desde la app. |
| `CLEAN_DATA/` | Versiones ya limpias, para saltar el pipeline de limpieza en pruebas. |
| `test_cases/` | Exportaciones CSV de corridas reales usadas en el informe técnico (secciones 12.1–12.3). |
| `cache/` | Caché automático de respuestas OSRM para no recalcular matrices de distancia. |
| `geocache.json` | Caché de geocodificación. Evita llamadas repetidas a Nominatim. |
| `app/backend/models/Modelo*.py` | Formulaciones exactas con Gurobi. Solo funcionan con licencia instalada. |
| `app/validation_report.py` | Ejecuta casos de prueba parametrizados y genera el informe de validación del modelo. |

---

## Arquitectura de la app

```
Frontend (PyQt6)
    │  HTTP requests (localhost:8000)
    ▼
FastAPI Router
    │
    ├── Auth Service        → bcrypt + MySQL (usuarios)
    ├── Cleaning Service    → normalización CSV + geocodificación Nominatim
    ├── Data Service        → lectura/escritura de órdenes y productos
    ├── Optimizer Service   → Solomon I1 + Tabu Search + matrices OSRM → mapa folium
    ├── DB Persistence      → SQLAlchemy + MySQL (upsert multi-tenant)
    ├── Road Routing        → OSRM local (matrices distancia/tiempo) con fallback Haversine
    ├── Fuel Price Service  → precios CNE en tiempo real
    └── Job Store           → tracking de jobs asíncronos (en memoria)
```

**Regla de capas:** el frontend nunca accede a la base de datos directamente. Las operaciones largas (optimización, subida de archivos) corren en `QThread` workers para no bloquear la interfaz.

### Capas y responsabilidades

| Capa | Carpeta | Responsabilidad |
|---|---|---|
| Frontend | `app/frontend/` | Interfaz Qt, formularios, mapa embebido |
| Workers | `app/frontend/workers/` | Operaciones de red en background (QThread) |
| Router | `app/backend/api/router.py` | Validación de input, orquestación multi-día, delegación a servicios |
| Services | `app/backend/services/` | Toda la lógica de negocio |
| Repositories | `app/database/repositories/` | Solo consultas y CRUD a la BD |
| Models | `app/database/models.py` | Definición ORM de tablas MySQL |

Ver también: [app/backend/README.md](app/backend/README.md) · [app/frontend/README.md](app/frontend/README.md) · [app/database/README.md](app/database/README.md)

---

## Pipeline de optimización

### Algoritmo

El optimizador usa un pipeline de dos fases sobre cada día de despacho:

1. **Solomon I1** (`literature_heuristics.heuristic_solomon_i1_style`) — heurística constructiva. Inserta pedidos uno a uno en la ruta donde mejor encajan, usando el criterio c1 (costo de inserción) + c2 (beneficio de urgencia temporal). Genera una solución inicial.

2. **Tabu Search** (`metaheuristics.tabu_search_vrptw`) — fase de mejora opcional. Explora la vecindad de la solución aplicando operadores (relocate, swap, 2-opt intra/inter, or-opt), mantiene una lista tabú de movimientos recientes y aplica criterio de aspiración. Controlado por `use_tabu_search` (bool) y `tabu_seconds` (tiempo máximo por día).

3. **Fleet-aware fallback** — si Tabu respeta el límite de camiones (`K_max`) pero Solomon no, se prefiere la solución de Tabu aunque la distancia total sea mayor.

4. **Fleet cap** — si la solución final todavía supera `K_max`, se eliminan las rutas más pequeñas hasta alcanzar el límite. Los pedidos eliminados quedan como no cubiertos.

5. **Asignación multi-viaje** — las rutas se asignan a camiones físicos. Un mismo camión puede hacer múltiples viajes en el día respetando `turnaround_min = 60 min` (descanso mínimo legal entre viajes).

6. **Exportación** — se genera un CSV de rutas, un CSV de pedidos no cubiertos, y un mapa HTML interactivo con folium.

### Parámetros del optimizador (`OptimizerParams`)

| Parámetro | Default | Descripción |
|---|---|---|
| `num_trucks` | requerido | Límite duro de camiones disponibles |
| `deliveries_per_day` | 150 | Máximo de pedidos intentados por día calendario |
| `use_tabu_search` | `True` | Activar/desactivar la fase de Tabu Search |
| `tabu_seconds` | 20.0 | Tiempo máximo de Tabu Search **por día** (segundos) |
| `space_per_truck` | — | Capacidad volumétrica por camión (m³) |
| `weight_per_truck` | — | Capacidad de peso por camión (kg) |
| `worktime_windows` | `"09:00-17:00"` | Ventana horaria del turno |
| `turnaround_min` | 60.0 | Descanso mínimo entre viajes del mismo camión (legal) |

### Penalizaciones duras (`HARD_FLEET_PENALTIES`)

El Tabu Search usa penalizaciones duras para forzar soluciones factibles:

| Penalización | Valor | Qué viola |
|---|---|---|
| `fleet` | 1 000 000 | Superar el número máximo de camiones |
| `cap` | 5 000 | Superar la capacidad volumétrica/peso |
| `time` | 5 000 | Superar la ventana horaria del turno |

### Sistema multi-día con carry-over

El router organiza todos los pedidos en una cola de días y gestiona los no cubiertos automáticamente:

- **`priority_df`** — pedidos geocodificados que el optimizador no pudo asignar (por capacidad, tiempo o límite de flota). Se anteponen al primer lugar del día siguiente, con prioridad máxima.
- **`overflow_df`** — pedidos en exceso del límite `deliveries_per_day`. Se posponen al día siguiente detrás de los priority.
- **Días extra** — si al terminar los días del calendario quedan pedidos en `priority_df` u `overflow_df`, se generan días adicionales automáticamente (máximo 30). Si un día extra no logra cubrir ningún pedido nuevo, los restantes se descartan como infactibles.

### Estadísticas globales deduplicadas

Al finalizar todos los días, `_build_global_stats` consolida los resultados:
- Parsea los archivos `routes_csv` y `uncovered_csv` de cada día
- Deduplica por número de orden (un pedido cubierto en cualquier día no se cuenta como no cubierto)
- Filtra marcadores de depósito (`"-"`, vacíos) para no inflar los totales
- Produce conteos precisos de `total_cubiertos` y `total_no_cubiertos`

---

## Interfaz de usuario

### Pantalla de login (`login_view.py`)

Tres modos:
- **Login** — por username o email
- **Registro** — crea una cuenta nueva (username, email, contraseña)
- **Reset de contraseña** — requiere username + email registrado

### Workspace principal (`main_view.py`)

**Pestaña Data Hub**
- Subir CSV de ventas y detalle directamente a MySQL ("Sync → DB")
- Cargar catálogo de productos ("Actualizar Catálogo → DB")
- Dashboard con métricas de órdenes desde la BD por usuario
- Validación de direcciones en lote

**Pestaña Optimizador**
- Formulario completo de parámetros: dirección CD, camiones, capacidad, ventanas horarias
- Toggle **Activar Tabu Search** con selector de segundos (5s / 10s / 20s / 30s / 60s) y tooltip con tiempo estimado según número de días
- Barra de progreso en tiempo real (polling al backend)

**Pestaña Resultados**
- Pestañas por día: tabla de rutas, mapa interactivo embebido (folium), tabla de pedidos no cubiertos
- **Pestaña Global**: resumen consolidado de todos los días
- Botones de exportación:
  - **EXPORT ROUTES** — CSV de rutas del día seleccionado
  - **EXPORT UNCOVERED** — CSV de pedidos no cubiertos del día
  - **EXPORT GLOBAL** — resumen global en CSV
  - Todos usan `DontUseNativeDialog` para compatibilidad con Windows (permite nombres de archivo con números)

---

## Formato de datos de entrada

### Dirección del Centro de Distribución (CD)

Se ingresa como texto libre en la interfaz. El sistema la valida contra Nominatim antes de optimizar.

**Ejemplos válidos:**
```
Av. Américo Vespucio 1000, Pudahuel
Ruta 68 km 12, Pudahuel, Santiago
```

> La dirección debe ser geocodificable dentro de la Región Metropolitana de Chile.

---

### CSV de Ventas (`ventas`)

Encoding: **UTF-8** o **Latin-1** (detectado automáticamente).

| Columna | Tipo | Descripción | Ejemplo |
|---|---|---|---|
| `RUT` | texto | RUT del cliente (cualquier formato) | `12.345.678-9` |
| `Nombre cliente` | texto | Nombre o razón social | `Ferretería El Martillo` |
| `Dirección cliente` | texto | Dirección completa con número | `Av. Grecia 750` |
| `Comuna` | texto | Comuna dentro de la Región Metropolitana | `Ñuñoa` |
| `Fecha de Pedido` | fecha | Fecha del pedido (`YYYY-MM-DD`) | `2026-12-01` |
| `Número de Orden` | texto | Código único de la orden | `ORD-TEST-000001` |
| `Monto Pedido` | entero | Monto en CLP | `88632` |
| `Fecha de despacho Solicitada` | fecha | Fecha límite de entrega (`YYYY-MM-DD`) | `2026-12-03` |

> El pipeline de limpieza normaliza automáticamente RUTs, estandariza comunas y separa la dirección de la comuna si vienen en el mismo campo.

---

### CSV de Detalle de Órdenes (`detalle`)

Acepta **CSV** o **XLSX**.

| Columna | Tipo | Descripción |
|---|---|---|
| `Número de Orden` | texto | Debe coincidir con una orden en ventas |
| `SKU` | texto | Código de producto del catálogo |
| `Cantidad` | entero | Unidades del producto en esta orden |

> Las columnas de dimensiones y pesos se completan automáticamente cruzando con el catálogo.

---

### CSV de Catálogo (`catalogo`)

| Columna | Tipo | Descripción |
|---|---|---|
| `SKU` | texto | Código único del producto (max 10 chars) |
| `Descripcion SKU` | texto | Nombre del producto |
| `Largo_cm` | decimal | Largo en cm |
| `Ancho_cm` | decimal | Ancho en cm |
| `Alto_cm` | decimal | Alto en cm |
| `Volumen_unitario_m3` | decimal | Volumen en m³ |
| `Peso_unitario_kg` | decimal | Peso en kg |
| `Tipo_embalaje` | texto | Tipo de embalaje |

> SKUs ya existentes para el mismo usuario se omiten (no se sobreescriben).

---

## Instalación paso a paso (local)

### Prerrequisitos

- Python 3.11+
- MySQL 8.0+
- Docker (para Nominatim y OSRM)

---

### 1. Clonar el repositorio

```bash
git clone https://github.com/VichoRamirez/Capstone.git
cd Capstone
```

---

### 2. Entorno virtual Python

```bash
python -m venv CapstoneEnv

# Windows
CapstoneEnv\Scripts\activate

# macOS / Linux
source CapstoneEnv/bin/activate
```

---

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

---

### 4. Variables de entorno

Copiar la plantilla y completar con las credenciales:

```bash
cp app/config/.env.example app/config/.env   # macOS / Linux
copy app\config\.env.example app\config\.env  # Windows
```

Editar `app/config/.env`:

```env
DB_HOST=localhost
DB_PORT=3306
DB_USER=tu_usuario
DB_PASSWORD=tu_contraseña
DB_NAME=capstone_db

APP_NAME=Capstone Analytics
DEBUG=True

OSRM_LOCAL_BASE_URL=http://127.0.0.1:5010
```

---

### 5. Base de datos MySQL

Crear la base de datos (solo la primera vez):

```sql
CREATE DATABASE capstone_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Crear las tablas con SQLAlchemy (desde `app/`):

```bash
cd app
python -c "from database.models import Base; from database.connection import engine; Base.metadata.create_all(engine); print('Tablas creadas')"
```

> MySQL 8 usa `caching_sha2_password` por defecto. El paquete `cryptography` (incluido en `requirements.txt`) es necesario para que `pymysql` pueda autenticarse.

---

## Servicios externos requeridos

La app necesita dos servicios Docker para funcionar completamente.

### Nominatim (geocodificación de direcciones)

**Primera vez** (~20-30 min, descarga el índice de Chile):

```bash
docker run -it \
  -e PBF_URL=https://download.geofabrik.de/south-america/chile-latest.osm.pbf \
  -e REPLICATION_URL=https://download.geofabrik.de/south-america/chile-updates/ \
  -p 8088:8080 --name nominatim \
  mediagis/nominatim:4.4
```

Esperar hasta ver `Server started` en los logs.

**Usos posteriores:**
```bash
docker start nominatim
```

---

### OSRM (routing por calles reales)

**Primera vez** — descargar y preprocesar el mapa de Chile:

```bash
# Linux / macOS
mkdir -p ~/osrm_data
wget -P ~/osrm_data https://download.geofabrik.de/south-america/chile-latest.osm.pbf

docker run -t -v ~/osrm_data:/data ghcr.io/project-osrm/osrm-backend \
  osrm-extract -p /opt/car.lua /data/chile-latest.osm.pbf

docker run -t -v ~/osrm_data:/data ghcr.io/project-osrm/osrm-backend \
  osrm-partition /data/chile-latest.osrm

docker run -t -v ~/osrm_data:/data ghcr.io/project-osrm/osrm-backend \
  osrm-customize /data/chile-latest.osrm
```

**Levantar el servidor:**

```bash
docker run -d -p 5010:5000 -v ~/osrm_data:/data \
  --name osrm ghcr.io/project-osrm/osrm-backend \
  osrm-routed --algorithm mld /data/chile-latest.osrm
```

**Usos posteriores:**
```bash
docker start osrm
```

> Si OSRM no está disponible, el optimizador usa automáticamente **distancia Haversine** como fallback (línea recta entre coordenadas). Las rutas serán subóptimas pero el sistema no falla.

---

### Verificar servicios activos

```bash
docker ps
# Deben aparecer: nominatim (8088) y osrm (5010)
```

---

## Cómo ejecutar la app

```bash
# 1. Levantar servicios Docker
docker start nominatim
docker start osrm

# 2. Activar entorno virtual
CapstoneEnv\Scripts\activate        # Windows
source CapstoneEnv/bin/activate     # macOS / Linux

# 3. Iniciar el backend (terminal 1)
cd app
python backend/main.py

# 4. Iniciar el frontend (terminal 2)
cd app
python main.py
```

El backend queda disponible en `http://127.0.0.1:8000`.  
Documentación interactiva de la API: `http://127.0.0.1:8000/docs`

---

## Flujo de uso típico

1. **Registrarse** o iniciar sesión en la pantalla de login.
2. **Subir el catálogo** de productos (botón "Actualizar Catálogo → DB").
3. **Subir ventas** (CSV) y **detalle** (CSV/XLSX) usando los botones "Sync → DB".
4. En el **panel de optimización**, configurar:
   - Dirección del Centro de Distribución
   - Número de camiones disponibles y capacidades
   - Ventana horaria del turno
   - Activar Tabu Search y seleccionar tiempo (opcional — mejora la calidad de las rutas)
5. **Ejecutar** la optimización. El sistema:
   - Geocodifica direcciones pendientes
   - Genera matrices de distancia/tiempo vía OSRM
   - Corre Solomon I1 + Tabu Search por cada día de despacho
   - Distribuye pedidos no cubiertos al día siguiente automáticamente
6. Ver los **resultados por día** (rutas, mapa, no cubiertos) y el **resumen global**.
7. **Exportar** los resultados con los botones EXPORT ROUTES / EXPORT UNCOVERED / EXPORT GLOBAL.

---

## API endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Verifica que el backend está corriendo |
| `GET` | `/progress` | Etapa actual del procesamiento (polling) |
| `POST` | `/auth/register` | `{username, email, password}` — crea cuenta |
| `POST` | `/auth/login` | `{identifier, password}` — login por username o email |
| `POST` | `/auth/reset-password` | `{username, email, new_password}` — reset de contraseña |
| `GET` | `/catalog?user_id=` | Lista productos del catálogo de un usuario |
| `POST` | `/upload-catalogo` | Sube CSV de catálogo de productos |
| `POST` | `/clean` | Valida y previsualiza CSVs sin optimizar |
| `POST` | `/upload` | Sube + persiste CSV de ventas |
| `POST` | `/upload-detalle` | Sube + persiste CSV/XLSX de detalle |
| `POST` | `/data/dashboard` | Métricas de órdenes desde CSVs cargados |
| `GET` | `/data/dashboard-db?user_id=` | Métricas de órdenes desde MySQL (por usuario) |
| `POST` | `/data/validate-addresses` | Validación de direcciones en lote |
| `GET` | `/fuel/diesel-clp` | Precio del diésel en CLP/L (fuente CNE) |
| `GET` | `/fuel/prices-clp?fuel_type=` | Precio por tipo de combustible |
| `POST` | `/optimize` | Inicia optimización asíncrona → retorna `{job_id}` |
| `GET` | `/jobs/{job_id}/status` | Estado del job (`pending`/`running`/`done`/`error`) |
| `GET` | `/jobs/{job_id}/result` | Resultado completo del job |
| `GET` | `/next-order-number` | Siguiente número de orden disponible |
| `POST` | `/simulation/order` | Crea una orden simulada (desarrollo/testing) |

---

## Tests

Ver [app/__tests__/README.md](app/__tests__/README.md) para instrucciones completas.

```bash
cd app

# Pruebas unitarias (sin red ni DB)
pytest __tests__/unit/ -v

# Pruebas de integración (requiere MySQL corriendo)
pytest __tests__/integration/ -v

# Benchmarks de rendimiento
pytest __tests__/stress/test_benchmarks.py -v --benchmark-sort=mean

# Todo excepto stress
pytest -v -m "not stress"
```

Los resultados se guardan en `app/__tests__/results/`.
