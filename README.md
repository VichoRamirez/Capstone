# Capstone Analytics — Dispatch Optimizer

Aplicación de escritorio SaaS para **optimización de rutas de reparto (VRP/VRPTW)**. Las empresas suben sus órdenes de venta en CSV, el sistema geocodifica las direcciones, agrupa por día de despacho y ejecuta una heurística ALNS para producir rutas optimizadas de camiones, visualizadas en un mapa interactivo.

---

## Índice

1. [Estructura del repositorio](#estructura-del-repositorio)
2. [Arquitectura de la app](#arquitectura-de-la-app)
3. [Formato de datos de entrada](#formato-de-datos-de-entrada)
4. [Instalación paso a paso (local)](#instalación-paso-a-paso-local)
5. [Servicios externos requeridos](#servicios-externos-requeridos)
6. [Variables de entorno](#variables-de-entorno)
7. [Cómo ejecutar la app](#cómo-ejecutar-la-app)
8. [Tests](#tests)

---

## Estructura del repositorio

```
Capstone/
│
├── app/                        # Código fuente de la aplicación
│   ├── main.py                 # Punto de entrada (lanza backend + frontend)
│   ├── qt_runtime.py           # Configuración del runtime Qt
│   ├── requirements.txt        # Dependencias Python de la app
│   ├── pytest.ini              # Configuración de pytest
│   │
│   ├── backend/                # Servidor FastAPI (puerto 8000)
│   │   ├── main.py             # Punto de entrada del backend
│   │   ├── api/
│   │   │   └── router.py       # Todos los endpoints REST
│   │   ├── controllers/
│   │   │   └── data_controller.py
│   │   ├── models/             # Modelos de optimización VRP
│   │   │   ├── routing/        # Heurísticas activas (ALNS, Clarke-Wright, Tabu Search)
│   │   │   ├── Modelo*.py      # Formulaciones exactas (requieren Gurobi)
│   │   │   └── SoluciónHeurística.py
│   │   ├── schemas/
│   │   │   └── optimization.py # OptimizerParams + schemas de auth
│   │   └── services/           # Lógica de negocio
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
│   │   ├── settings.py         # Lee .env y expone configuración global
│   │   ├── .env                # Variables de entorno (NO se sube a git)
│   │   └── .env.example        # Plantilla de variables de entorno
│   │
│   ├── database/
│   │   ├── connection.py       # Engine SQLAlchemy + get_session()
│   │   ├── models.py           # Modelos ORM (Usuario, Producto, Venta, Detalle)
│   │   └── repositories/       # CRUD por tabla
│   │       ├── base_repository.py
│   │       ├── usuario_repository.py
│   │       ├── producto_repository.py
│   │       ├── venta_repository.py
│   │       └── detalle_repository.py
│   │
│   ├── frontend/               # Interfaz de usuario PyQt6
│   │   ├── app.py              # QMainWindow + navegación entre pantallas
│   │   ├── views/
│   │   │   ├── login_view.py
│   │   │   └── main_view.py
│   │   ├── widgets/
│   │   │   └── components.py
│   │   ├── resources/
│   │   │   └── styles/
│   │   └── workers/
│   │       ├── health_worker.py
│   │       ├── upload_worker.py
│   │       ├── data_hub_worker.py
│   │       ├── progress_worker.py
│   │       └── request_worker.py
│   │
│   └── __tests__/              # Suite de pruebas
│       ├── conftest.py
│       ├── unit/
│       ├── integration/
│       ├── stress/
│       └── results/            # Reportes generados por pytest y locust
│
├── RAW_DATA/                   # Datasets de ejemplo (datos ficticios de Santiago)
├── CLEAN_DATA/                 # Versiones pre-limpias de los datasets
├── SCRIPTS/                    # Notebooks y scripts de exploración
├── Pruebas/                    # Scripts de pruebas de instancias VRP (benchmark)
├── cache/                      # Caché de resultados OSRM (generado automáticamente)
├── geocache.json               # Caché persistente de geocodificación Nominatim
└── TODO.md                     # Deuda técnica y trabajo pendiente
```

### Carpetas que no forman parte del flujo principal

| Carpeta / Archivo | Por qué existe |
|---|---|
| `SCRIPTS/` | Exploración de datos durante el desarrollo. No es parte de la app. |
| `RAW_DATA/` | Datasets ficticios para pruebas. Se pueden subir directamente desde la app. |
| `CLEAN_DATA/` | Versiones ya limpias, para saltar el pipeline de limpieza en pruebas. |
| `Pruebas/` | Scripts de benchmarking de instancias VRP (local search vs tabu search). |
| `cache/` | Caché automático de respuestas OSRM para no recalcular matrices de distancia. |
| `geocache.json` | Caché de geocodificación. Evita llamadas repetidas a Nominatim. |
| `app/backend/models/Modelo*.py` | Formulaciones exactas con Gurobi. Solo funcionan con licencia instalada. |

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
    ├── Optimizer Service   → ALNS + matrices OSRM → mapa folium
    ├── DB Persistence      → SQLAlchemy + MySQL
    ├── Road Routing        → OSRM local (matrices distancia/tiempo)
    ├── Fuel Price Service  → precios CNE en tiempo real
    └── Job Store           → tracking de jobs asincrónicos (en memoria)
```

**Regla de capas:** el frontend nunca accede a la base de datos directamente. Las operaciones largas (optimización, subida de archivos) corren en `QThread` workers para no bloquear la interfaz.

Ver también: [app/backend/README.md](app/backend/README.md) · [app/frontend/README.md](app/frontend/README.md)

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

> SKUs ya existentes para el mismo usuario son ignorados (no se sobreescriben).

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
git checkout saas-integration
```

---

### 2. Entorno virtual Python

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

---

### 3. Instalar dependencias

```bash
pip install -r app/requirements.txt
```

---

### 4. Variables de entorno

Copiar la plantilla y completar con tus credenciales:

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

---

## Servicios externos requeridos

La app necesita dos servicios en Docker para funcionar completamente.

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

**Primera vez** — crear carpeta y descargar el PBF:

```bash
# Linux / macOS
mkdir -p ~/osrm_data
wget -P ~/osrm_data https://download.geofabrik.de/south-america/chile-latest.osm.pbf
```

**Preprocesar** (una sola vez, ~5-10 min):

```bash
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
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows

# 3. Iniciar el backend (terminal 1)
cd app
python backend/main.py

# 4. Iniciar el frontend (terminal 2)
cd app
python main.py
```

---

## Flujo de uso típico

1. **Registrarse** o iniciar sesión en la pantalla de login.
2. **Subir el catálogo** de productos (botón "Actualizar Catálogo → DB").
3. **Subir ventas** (CSV) y **detalle** (CSV/XLSX) usando los botones "Sync → DB".
4. En el panel de optimización, ingresar:
   - Dirección del Centro de Distribución
   - Número de camiones disponibles
   - Parámetros de ventana horaria y capacidad
5. Ejecutar la optimización. El sistema geocodifica, calcula matrices de distancia vía OSRM y retorna rutas por día con mapa interactivo.

---

## Tests

Ver [app/__tests__/README.md](app/__tests__/README.md) para instrucciones completas.

```bash
cd app

# Pruebas unitarias (sin red ni DB)
pytest __tests__/unit/ -v

# Pruebas de integración (requiere MySQL)
pytest __tests__/integration/ -v

# Benchmarks de rendimiento
pytest __tests__/stress/test_benchmarks.py -v --benchmark-sort=mean

# Todo (excepto stress)
pytest -v -m "not stress"
```

Los resultados se guardan en `app/__tests__/results/`.
