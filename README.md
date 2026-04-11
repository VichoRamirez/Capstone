# Capstone Analytics — Dispatch Optimizer

Aplicación de escritorio SaaS para **optimización de rutas de reparto (VRP/VRPTW)**. Las empresas suben sus órdenes de venta en CSV, el sistema geocodifica las direcciones, agrupa por día de despacho y ejecuta una heurística ALNS para producir rutas optimizadas de camiones, visualizadas en un mapa interactivo.

---

## Índice

1. [Estructura del repositorio](#estructura-del-repositorio)
2. [Arquitectura de la app](#arquitectura-de-la-app)
3. [Formato de datos de entrada](#formato-de-datos-de-entrada)
4. [Instalación paso a paso (local)](#instalación-paso-a-paso-local)
5. [Servicios externos requeridos](#servicios-externos-requeridos)
6. [Esquema mínimo de la base de datos](#esquema-mínimo-de-la-base-de-datos)
7. [Variables de entorno](#variables-de-entorno)
8. [Cómo ejecutar la app](#cómo-ejecutar-la-app)

---

## Estructura del repositorio

```
Capstone analytics/
│
├── app/                        # Código fuente de la aplicación
│   ├── main.py                 # Punto de entrada del frontend PyQt6
│   ├── requirements.txt        # Dependencias Python de la app
│   ├── .env                    # Variables de entorno (NO se sube a git)
│   │
│   ├── backend/                # Servidor FastAPI (puerto 8000)
│   │   ├── main.py             # Punto de entrada del backend
│   │   ├── api/
│   │   │   └── router.py       # Todos los endpoints REST
│   │   ├── models/             # Modelos de optimización VRP
│   │   │   ├── routing/        # Heurísticas activas (ALNS, metaheurísticas)
│   │   │   ├── Modelo.py       # Formulación exacta VRP (requiere Gurobi)
│   │   │   ├── Modelo2-4.py    # Variantes del modelo exacto (requieren Gurobi)
│   │   │   └── SoluciónHeurística.py
│   │   ├── schemas/            # Modelos Pydantic para validación de requests
│   │   │   └── optimization.py # OptimizerParams + schemas de auth
│   │   └── services/           # Lógica de negocio
│   │       ├── auth_service.py          # Registro, login, reset de contraseña
│   │       ├── cleaning_service.py      # Limpieza y normalización de CSVs
│   │       ├── db_persistence.py        # Escritura de DataFrames a MySQL
│   │       ├── fuel_price_service.py    # Consulta precio del diésel (API externa)
│   │       ├── job_store.py             # Almacén de jobs asincrónicos
│   │       ├── optimizer_service.py     # Orquesta el pipeline de optimización
│   │       ├── road_routing.py          # Matrices de distancia/tiempo vía OSRM
│   │       └── traffic_factor_dataset_builder.py
│   │
│   ├── config/
│   │   ├── settings.py         # Lee .env y expone configuración global
│   │   └── .env.example        # Plantilla de variables de entorno
│   │
│   ├── database/
│   │   ├── connection.py       # Engine SQLAlchemy + get_session()
│   │   ├── models.py           # Modelos ORM (Usuario, Producto, Venta, Detalle)
│   │   └── repositories/       # CRUD por tabla (sin lógica de negocio)
│   │       ├── base_repository.py
│   │       ├── usuario_repository.py
│   │       ├── producto_repository.py
│   │       ├── venta_repository.py
│   │       └── detalle_repository.py
│   │
│   └── frontend/               # Interfaz de usuario PyQt6
│       ├── app.py              # QMainWindow + navegación entre pantallas
│       ├── views/
│       │   ├── login_view.py   # Pantalla de login, registro y reset de contraseña
│       │   └── main_view.py    # Workspace principal (optimizer, data hub, resultados)
│       ├── widgets/            # Componentes visuales reutilizables
│       ├── resources/          # Estilos QSS y tema de colores
│       └── workers/            # QThreads para operaciones largas (sin bloquear UI)
│           ├── upload_worker.py      # Subida de CSVs al backend
│           ├── request_worker.py     # Polling de jobs asincrónicos
│           ├── data_hub_worker.py    # Carga del dashboard de datos
│           ├── health_worker.py      # Verificación de conectividad con el backend
│           └── progress_worker.py    # Polling de progreso de optimización
│
├── RAW_DATA/                   # Datasets de ejemplo (datos ficticios de Santiago)
│   ├── catalogo_productos.csv  # 900 productos con dimensiones y pesos
│   ├── ventas_ficticias_santiago_202612.csv  # 4180 órdenes de venta
│   └── detalle_pedidos_santiago_202612.xlsx  # Detalle de líneas por orden
│
├── CLEAN_DATA/                 # Versiones pre-limpias de los datasets anteriores
│   └── ...                     # Útiles para pruebas rápidas sin pasar por el pipeline
│
├── SCRIPTS/                    # Notebooks y scripts de exploración y análisis
│   ├── Explorar.ipynb          # Análisis exploratorio de los datos
│   └── update_coords.py        # Script auxiliar para actualizar coordenadas en BD
│
├── cache/                      # Caché de resultados de OSRM (generado automáticamente)
├── geocache.json               # Caché persistente de geocodificación Nominatim
├── TODO.md                     # Deuda técnica y trabajo pendiente
└── requirements.txt            # Dependencias mínimas (sin PyQt6, solo backend)
```

### Carpetas que no forman parte del flujo principal

| Carpeta / Archivo | Por qué existe |
|---|---|
| `SCRIPTS/` | Exploración de datos durante el desarrollo. No es parte de la app. |
| `RAW_DATA/` | Datasets ficticios para pruebas. Se pueden subir directamente desde la app. |
| `CLEAN_DATA/` | Versiones ya limpias de los mismos datos, para saltar el pipeline de limpieza en pruebas. |
| `cache/` | Caché automático de respuestas de OSRM para no recalcular matrices de distancia. |
| `geocache.json` | Caché de geocodificación. Evita llamadas repetidas a Nominatim para las mismas direcciones. |
| `requirements.txt` (raíz) | Dependencias mínimas del backend, sin Qt. Mantenido para entornos de servidor. |
| `app/backend/models/Modelo*.py` | Formulaciones exactas con Gurobi. Solo funcionan con licencia de Gurobi instalada. El optimizador activo usa las heurísticas en `routing/`. |

---

## Arquitectura de la app

```
Frontend (PyQt6)
    │  HTTP requests
    ▼
FastAPI Router (puerto 8000)
    │
    ├── Auth Service       → bcrypt + MySQL
    ├── Cleaning Service   → normalización CSV + geocodificación Nominatim
    ├── Optimizer Service  → ALNS + matrices OSRM
    ├── DB Persistence     → SQLAlchemy + MySQL
    └── Road Routing       → OSRM local (matrices distancia/tiempo)
```

**Regla de capas:** el frontend nunca accede a la base de datos directamente. Las operaciones largas (optimización, subida de archivos) corren en `QThread` workers para no bloquear la interfaz.

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

| Columna | Tipo | Descripción | Ejemplo |
|---|---|---|---|
| `Número de Orden` | texto | Debe coincidir con un orden en ventas | `ORD-TEST-000001` |
| `SKU` | texto | Código de producto del catálogo | `SKU-00229` |
| `Descripción SKU` | texto | Nombre del producto | `Notebook 15 pulgadas` |
| `Cantidad` | entero | Unidades del producto en esta orden | `5` |
| `Largo_cm` | decimal | Largo unitario en cm | `41.0` |
| `Ancho_cm` | decimal | Ancho unitario en cm | `29.5` |
| `Alto_cm` | decimal | Alto unitario en cm | `5.8` |
| `Volumen_unitario_m3` | decimal | Volumen de una unidad en m³ | `0.007015` |
| `Peso_unitario_kg` | decimal | Peso de una unidad en kg | `2.002` |
| `Volumen_total_m3` | decimal | `Volumen_unitario_m3 × Cantidad` | `0.035075` |
| `Peso_total_kg` | decimal | `Peso_unitario_kg × Cantidad` | `10.010` |

---

### CSV de Catálogo (`catalogo`)

| Columna | Tipo | Descripción | Ejemplo |
|---|---|---|---|
| `SKU` | texto | Código único del producto (max 10 chars) | `SKU-00229` |
| `Descripción SKU` | texto | Nombre del producto | `Notebook 15 pulgadas` |
| `Largo_cm` | decimal | Largo en cm | `41.0` |
| `Ancho_cm` | decimal | Ancho en cm | `29.5` |
| `Alto_cm` | decimal | Alto en cm | `5.8` |
| `Volumen_unitario_m3` | decimal | Volumen en m³ | `0.007015` |
| `Peso_unitario_kg` | decimal | Peso en kg | `2.002` |
| `Tipo_embalaje` | texto | Tipo de embalaje | `A granel` / `Embalaje personalizado` |

> SKUs ya existentes para el mismo usuario son ignorados (no se sobreescriben).

---

## Instalación paso a paso (local)

### Prerrequisitos

- Python 3.11+
- MySQL 8.0+
- Docker Desktop (para Nominatim y OSRM)

---

### 1. Clonar el repositorio

```bash
git clone https://github.com/VichoRamirez/Capstone.git
cd Capstone
git checkout saas-integration
```

---

### 2. Entorno virtual Python (recomendado)

```bash
python -m venv CapstoneEnv

# Windows
CapstoneEnv\Scripts\activate

# macOS / Linux
source CapstoneEnv/bin/activate
```

También puedes instalar las dependencias en tu entorno global si prefieres, saltándote la creación del venv.

---

### 3. Instalar dependencias

```bash
pip install -r app/requirements.txt
```

---

### 4. Base de datos MySQL

Crear la base de datos y las tablas con la siguiente estructura mínima:

```sql
CREATE DATABASE capstone_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE capstone_db;

CREATE TABLE usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fecha_creacion DATETIME,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(100) NOT NULL,
    tipo_usuario VARCHAR(255) NOT NULL DEFAULT 'Free'
);

CREATE TABLE catalogo (
    SKU VARCHAR(10) NOT NULL,
    `Descripción SKU` TEXT,
    Largo_cm DOUBLE,
    Ancho_cm DOUBLE,
    Alto_cm DOUBLE,
    Volumen_unitario_m3 DOUBLE,
    Peso_unitario_kg DOUBLE,
    Tipo_embalaje TEXT,
    id_usuario INT NOT NULL,
    PRIMARY KEY (SKU, id_usuario)
);

CREATE TABLE ventas (
    `Número de Orden` VARCHAR(50) NOT NULL,
    RUT TEXT,
    `Nombre cliente` TEXT,
    `Dirección cliente` TEXT,
    Comuna TEXT,
    `Fecha de Pedido` DATE,
    Estado VARCHAR(15) DEFAULT 'Pendiente',
    `Monto Pedido` BIGINT,
    `Fecha de despacho Solicitada` DATE,
    Latitud FLOAT,
    Longitud FLOAT,
    id_usuario INT NOT NULL,
    PRIMARY KEY (`Número de Orden`, id_usuario)
);

CREATE TABLE detalle (
    `Número de Orden` VARCHAR(50) NOT NULL,
    SKU VARCHAR(10) NOT NULL,
    `Descripción SKU` TEXT,
    Cantidad BIGINT,
    Largo_cm DOUBLE,
    Ancho_cm DOUBLE,
    Alto_cm DOUBLE,
    Volumen_unitario_m3 DOUBLE,
    Peso_unitario_kg DOUBLE,
    Volumen_total_m3 DOUBLE,
    Peso_total_kg DOUBLE,
    id_usuario INT NOT NULL,
    PRIMARY KEY (`Número de Orden`, SKU, id_usuario)
);
```

---

### 5. Variables de entorno

Copiar la plantilla y completar con tus credenciales:

```bash
copy app\config\.env.example app\.env   # Windows
cp app/config/.env.example app/.env     # macOS / Linux
```

Editar `app/.env`:

```env
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=tu_contraseña
DB_NAME=capstone_db

APP_NAME=Capstone Analytics
DEBUG=True

NOMINATIM_URL=http://localhost:8088
OSRM_LOCAL_BASE_URL=http://127.0.0.1:5010
OSRM_DATASET_PATH=C:/osrm_data/chile-latest.osrm
OSRM_LOCAL_AUTOSTART=false
```

---

## Servicios externos requeridos

La app necesita dos servicios corriendo en Docker para funcionar completamente.

### Nominatim (geocodificación de direcciones)

Convierte texto de dirección en coordenadas (latitud/longitud).

**Primera vez** (descarga y construye el índice de Chile, ~20-30 min):

```powershell
docker run -it -e "PBF_URL=https://download.geofabrik.de/south-america/chile-latest.osm.pbf" -e "REPLICATION_URL=https://download.geofabrik.de/south-america/chile-updates/" -p 8088:8080 --name nominatim mediagis/nominatim:4.4
```

Esperar hasta ver `Server started` en los logs.

**Usos posteriores:**

```powershell
docker start nominatim
```

---

### OSRM (routing por calles reales)

Calcula rutas reales por calles entre coordenadas (necesario para que el mapa muestre calles, no líneas rectas).

**Primera vez** — crear carpeta de datos y descargar el PBF:

```powershell
mkdir C:\osrm_data
Invoke-WebRequest -Uri "https://download.geofabrik.de/south-america/chile-latest.osm.pbf" -OutFile "C:\osrm_data\chile-latest.osm.pbf"
```

**Preprocesar** (una sola vez, ~5-10 min en total):

```powershell
docker run -t -v "C:/osrm_data:/data" ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/chile-latest.osm.pbf

docker run -t -v "C:/osrm_data:/data" ghcr.io/project-osrm/osrm-backend osrm-partition /data/chile-latest.osrm

docker run -t -v "C:/osrm_data:/data" ghcr.io/project-osrm/osrm-backend osrm-customize /data/chile-latest.osrm
```

**Levantar el servidor** (primera vez crea el contenedor):

```powershell
docker run -d -p 5010:5000 -v "C:/osrm_data:/data" --name osrm ghcr.io/project-osrm/osrm-backend osrm-routed --algorithm mld /data/chile-latest.osrm
```

**Usos posteriores:**

```powershell
docker start osrm
```

---

### Verificar que ambos servicios están activos

```powershell
docker ps
```

Deben aparecer `nominatim` (puerto 8088) y `osrm` (puerto 5010).

---

## Cómo ejecutar la app

Cada vez que quieras usar la app, en este orden:

```powershell
# 1. Levantar servicios Docker
docker start nominatim
docker start osrm

# 2. Activar el entorno virtual (si usas uno)
CapstoneEnv\Scripts\activate

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
