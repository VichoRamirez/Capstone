# Capstone Analytics

Proyecto con dos componentes separados:
- **Análisis de datos**: notebooks y scripts de exploración (raíz del proyecto)
- **Aplicación de escritorio** (`app/`): frontend **PyQt6**, backend Python y base de datos **MySQL** (via SQLAlchemy)

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
├── requirements.txt                # Deps de análisis (pandas, jupyter, etc.)
├── CapstoneEnv/                    # Entorno virtual (gitignored)
├── SCRIPTS/                        # Notebooks de exploración
├── RAW_DATA/                       # Datos crudos
├── CLEAN_DATA/                     # Datos limpios
│
└── app/                            # Aplicación de escritorio (separada)
    ├── main.py                     # Punto de entrada
    ├── requirements.txt            # Deps de la app (PyQt6, SQLAlchemy, etc.)
    ├── .env                        # Variables de entorno (NO se sube a git)
    ├── config/
    │   ├── settings.py             # Lee .env y expone configuración
    │   └── .env.example            # Plantilla de variables de entorno
    │
    ├── frontend/                   # Capa de presentación (PyQt6)
    │   ├── app.py                  # Ventana principal (QMainWindow)
    │   ├── views/                  # Pantallas / vistas
    │   │   └── main_view.py
    │   ├── widgets/                # Widgets reutilizables
    │   └── resources/
    │       └── styles/
    │           └── style.qss       # Estilos Qt
    │
    ├── backend/                    # Capa de lógica de negocio
    │   ├── controllers/            # Orquestadores (frontend ↔ servicios)
    │   │   └── data_controller.py
    │   └── services/               # Lógica de negocio pura
    │       └── data_service.py
    │
    └── database/                   # Capa de acceso a datos (MySQL)
        ├── connection.py           # Engine y sesiones de SQLAlchemy
        ├── models/                 # Modelos ORM
        │   ├── base.py
        │   └── producto.py
        └── repositories/           # Consultas y CRUD
            ├── base_repository.py
            └── producto_repository.py
```

## Flujo de datos (app)

```
Frontend (PyQt6)  →  Controller  →  Service  →  Repository  →  MySQL
      ↑                                                          |
      └──────────────────── datos ──────────────────────────────-┘
```

## Instalación

```bash
# 1. Crear y activar entorno virtual
python -m venv CapstoneEnv
CapstoneEnv\Scripts\activate

# 2. Instalar dependencias de análisis
pip install -r requirements.txt

# 3. Instalar dependencias de la app
pip install -r app\requirements.txt

# 4. Configurar variables de entorno
copy app\config\.env.example app\.env
# Editar app\.env con tus credenciales de MySQL

# 5. Ejecutar la aplicación
cd app
python main.py
```