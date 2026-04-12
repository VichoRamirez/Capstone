# Backend — FastAPI Server

Servidor REST construido con **FastAPI** y **SQLAlchemy**, que expone todos los servicios de la aplicación: autenticación, limpieza de datos, geocodificación, optimización VRP y persistencia en MySQL.

---

## Inicio rápido

```bash
cd app
python backend/main.py
```

El servidor queda disponible en `http://127.0.0.1:8000`.  
Documentación interactiva: `http://127.0.0.1:8000/docs`

---

## Endpoints

### Salud y monitoreo

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Verifica que el backend está corriendo |
| `GET` | `/progress` | Estado actual del procesamiento (polling del frontend) |

### Precios de combustible

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/fuel/diesel-clp` | Precio del diésel en CLP/L (fuente: CNE) |
| `GET` | `/fuel/prices-clp?fuel_type=<tipo>` | Precio por tipo: `diesel`, `gasoline_93`, `gasoline_95`, `gasoline_97` |

### Validación de dirección

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/validate-depot?address=<dir>` | Geocodifica la dirección del CD vía Nominatim; retorna lat/lon |

### Limpieza y validación de datos

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/clean` | Limpia CSVs de ventas y detalle sin geocodificar; retorna preview y stats |
| `POST` | `/data/dashboard` | Perfil rápido de órdenes cargadas (sin geocodificación completa) |
| `GET` | `/data/dashboard-db?user_id=<id>` | Dashboard de órdenes desde MySQL para un usuario |
| `POST` | `/data/validate-addresses` | Valida por lotes las direcciones de las órdenes |

### Subida de archivos

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/upload` | Sube CSV de ventas → limpia → geocodifica → persiste en MySQL |
| `POST` | `/upload-detalle` | Sube CSV/XLSX de detalle → limpia → persiste en MySQL |
| `POST` | `/upload-catalogo` | Sube CSV de catálogo de productos; ignora SKUs duplicados |

### Catálogo

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/catalog?user_id=<id>` | Lista los productos del catálogo de un usuario |

### Optimización (asíncrona)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/optimize` | Encola un job de optimización VRP; retorna `job_id` inmediatamente |
| `GET` | `/jobs/{job_id}/status` | Estado del job y etapa actual |
| `GET` | `/jobs/{job_id}/result` | Resultado completo del job (rutas + mapa) |

### Autenticación

| Método | Ruta | Body | Descripción |
|---|---|---|---|
| `POST` | `/auth/register` | `{username, email, password}` | Crea una cuenta nueva |
| `POST` | `/auth/login` | `{identifier, password}` | Login por username o email |
| `POST` | `/auth/reset-password` | `{username, email, new_password}` | Resetea la contraseña |

### Utilidades

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/next-order-number` | Retorna el siguiente número de orden disponible |

---

## Servicios

### `auth_service.py`
Registro, login y reset de contraseña. Usa **bcrypt** para el hash de contraseñas. Sin JWT — el `user_id` se pasa en cada request.

### `cleaning_service.py`
Normalización de CSVs de ventas y detalle:
- Estandariza RUTs chilenos (cualquier formato → `XX.XXX.XXX-X`)
- Detecta y normaliza comunas de la Región Metropolitana
- Geocodifica direcciones vía Nominatim con caché en `geocache.json`
- Acepta encoding UTF-8 o Latin-1 automáticamente

### `data_service.py`
Capa de negocio para órdenes y productos: agrega, cruza ventas con detalle, calcula volúmenes y pesos totales.

### `db_persistence.py`
Escritura y lectura de DataFrames hacia/desde MySQL usando SQLAlchemy. Maneja conflictos de clave primaria (órdenes duplicadas).

### `optimizer_service.py`
Orquestador del pipeline de optimización VRP:
1. Geocodifica direcciones pendientes
2. Construye matrices de distancia/tiempo vía OSRM
3. Ejecuta ALNS con ventanas horarias (VRPTW)
4. Genera mapa interactivo con folium
5. Retorna rutas por camión y órdenes no cubiertas

### `road_routing.py`
Interfaz con OSRM local (`127.0.0.1:5010`):
- Tablas de distancia/tiempo (`/table/v1/driving`)
- Polilíneas de rutas (`/route/v1/driving`)
- Fallback haversine si OSRM no está disponible

### `fuel_price_service.py`
Consulta el precio del diésel y bencinas en tiempo real desde CNE y fuentes alternativas. Incluye caché en memoria con TTL.

### `job_store.py`
Diccionario en memoria (`_jobs`) para trackear jobs asincrónicos de optimización. Cada job tiene: `status`, `stage`, `result`, `error`.

### `traffic_factor_dataset_builder.py`
Modela factores de tráfico por comuna y hora del día para ajustar tiempos de viaje en el optimizador.

---

## Modelos de optimización

Ubicados en `backend/models/routing/`:

| Archivo | Contenido |
|---|---|
| `heuristics.py` | Clarke-Wright savings, ALNS, mejora de rutas |
| `literature_heuristics.py` | Solomon I1, constructivas de literatura VRP |
| `metaheuristics.py` | Tabu Search, búsqueda local |

Los archivos `Modelo*.py` implementan formulaciones exactas con **Gurobi** y requieren licencia instalada. No se usan en producción; el optimizador activo usa las heurísticas.

---

## Schemas Pydantic

`backend/schemas/optimization.py` define:
- `OptimizerParams` — parámetros completos de una corrida de optimización
- `CleaningResponse` — respuesta del endpoint `/clean`
- Schemas de auth: `RegisterRequest`, `LoginRequest`, `ResetPasswordRequest`

---

## Base de datos

Ver [../../database/](../../database/) para modelos ORM y repositorios.

Las tablas son: `usuarios`, `catalogo`, `ventas`, `detalle`.  
Creación inicial: `python -c "from database.models import Base; from database.connection import engine; Base.metadata.create_all(engine)"`
