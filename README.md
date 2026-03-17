# Capstone Analytics

Proyecto con dos componentes separados:
- **Análisis de datos**: notebooks, scripts de exploración y datasets en la raíz del proyecto
- **Aplicación de escritorio** (`app/`): frontend **PyQt6**, backend Python, base de datos **MySQL** (via SQLAlchemy) y modelos de optimización/heurísticas para ruteo

## Arquitectura
### Requisitos
* Interfaz de progreso
* Time limit
* Ingreso de variables:
  * Cantidad de camiones
  * Ventana de tiempo
  * Centro de Distribución
* Mostrar ruta óptima
* Retornar CSV con la info (qué camión cubre que puntos)
* Actualización automática de la base de datos
  * Tabla Ventas -> entregado y no entregado.

### Restricciones
* Python.
* Free Optimizer. 
* DB relacional.
* No hay un sistema de coordenadas.


## Estructura del proyecto

```
__Capstone analytics/
├── SCRIPTS/                        # Notebooks de exploración
│   ├── Explorar.ipynb
│   └── sql_insert.ipynb            # Carga de datos a MySQL
├── RAW_DATA/                       # Datos crudos
│   ├── catalogo_productos.csv
│   ├── catalogo_productos.xlsx
│   ├── detalle_pedidos_santiago_202612.xlsx
│   ├── ventas_ficticias_santiago_202612.csv
│   └── ventas_ficticias_santiago_202612.zip
├── CLEAN_DATA/                     # Datos limpios
│   ├── catalogo_productos.csv
│   ├── detalle_ventas.csv
│   └── ventas_direcciones.csv
│
└── app/                            # Aplicación (separada)
    ├── main.py                     # Punto de entrada (PyQt6)
    ├── requirements.txt            # Deps de la app (PyQt6, FastAPI, SQLAlchemy, etc.)
    ├── config/
    │   ├── settings.py             # Lee .env y expone configuración
    │   └── .env.example            # Plantilla de variables de entorno
    │
    ├── frontend/                   # Capa de presentación (PyQt6)
    │   ├── app.py                  # Ventana principal (QMainWindow)
    │   ├── views/                  # Pantallas / vistas
    │   │   └── main_view.py
    │   ├── widgets/                # Widgets reutilizables
    │   │   └── components.py
    │   ├── workers/                # Hilos de trabajo (QThread)
    │   │   ├── health_worker.py
    │   │   └── request_worker.py
    │   └── resources/
    │       └── styles/             # Tema visual
    │           ├── style.qss
    │           └── theme.py
    │
    ├── backend/                    # Capa de lógica de negocio (FastAPI)
    │   ├── main.py                 # Servidor FastAPI (uvicorn)
    │   ├── api/                    # Endpoints REST
    │   │   └── router.py
    │   ├── controllers/            # Orquestadores (frontend ↔ servicios)
    │   │   └── data_controller.py
    │   ├── services/               # Lógica de negocio pura
    │   │   ├── data_service.py
    │   │   └── optimizer_service.py
    │   ├── schemas/                # Esquemas Pydantic de request/response
    │   │   └── optimization.py
    │   └── models/                 # Modelos analíticos y de optimización
    │       ├── Modelo.py               # Modelo exacto VRP con Gurobi
    │       ├── Modelo2.py              # Variante del modelo exacto
    │       ├── Modelo3.py              # Variante del modelo exacto
    │       ├── Modelo4.py              # Variante del modelo exacto
    │       ├── Heuristica.py           # Heurística constructiva / ALNS para VRP
    │       ├── Metaheuristicas.py      # GA, SA y Tabu Search para VRP/VRPTW
    │       ├── SoluciónHeurística.py   # Solución heurística persistida
    │       └── HeuristicasLiteraturaBenchmark.py  # Benchmarks de heurísticas
    │
    └── database/                   # Capa de acceso a datos (MySQL)
        ├── connection.py           # Engine y sesiones de SQLAlchemy
        └── repositories/           # Consultas y CRUD
            ├── base_repository.py
            └── producto_repository.py
```

## Flujo de datos (app)

```
Frontend (PyQt6)  →  HTTP  →  FastAPI (backend)  →  Service  →  Repository  →  MySQL
      ↑                                                                         |
      └──────────────────────────── datos ──────────────────────────────────────┘
```

## Modelos de optimización

Dentro de `app/backend/models/` se incluyen implementaciones para problemas de ruteo de vehículos (VRP/VRPTW):
- **Modelos exactos** (`Modelo.py`, `Modelo2.py`, `Modelo3.py`, `Modelo4.py`): formulaciones con **Gurobi**
- **Heurísticas y metaheurísticas** (`Heuristica.py`, `Metaheuristicas.py`): construcción inicial, búsqueda local, ALNS, algoritmos genéticos, simulated annealing y tabu search
- **Benchmarks** (`HeuristicasLiteraturaBenchmark.py`): variantes para comparar desempeño de heurísticas reportadas en literatura

## Instalación

```bash
# 1. Crear y activar entorno virtual
python -m venv CapstoneEnv
CapstoneEnv\Scripts\activate

# 2. Instalar dependencias de la app
pip install -r app\requirements.txt

# 3. Configurar variables de entorno
copy app\config\.env.example app\.env
# Editar app\.env con tus credenciales de MySQL

# 4. Ejecutar la aplicación
cd app
python main.py
```
