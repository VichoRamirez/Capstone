# Frontend — PyQt6 Desktop App

Interfaz de escritorio construida con **PyQt6**. Se comunica exclusivamente con el backend FastAPI vía HTTP usando workers en `QThread` para no bloquear la UI.

---

## Inicio rápido

El backend debe estar corriendo en `http://127.0.0.1:8000` antes de iniciar el frontend.

```bash
cd app
python main.py
```

---

## Estructura

```
frontend/
├── app.py              # QMainWindow principal, gestiona la navegación entre vistas
├── views/
│   ├── login_view.py   # Pantalla de login, registro y reset de contraseña
│   └── main_view.py    # Workspace principal (data hub, optimizador, resultados)
├── widgets/
│   └── components.py   # Componentes Qt reutilizables (formularios, botones, diálogos)
├── resources/
│   └── styles/
│       ├── style.qss   # Hoja de estilos Qt para el tema visual
│       └── theme.py    # Constantes de colores del tema
└── workers/            # QThreads para operaciones largas
    ├── health_worker.py
    ├── upload_worker.py
    ├── data_hub_worker.py
    ├── progress_worker.py
    └── request_worker.py
```

---

## Vistas

### `login_view.py`
Pantalla inicial. Permite:
- **Iniciar sesión** por username o email
- **Crear cuenta** nueva
- **Resetear contraseña** (requiere username + email registrado)

Al hacer login exitoso, navega automáticamente a `main_view`.

### `main_view.py`
Workspace principal con tres paneles:

1. **Data Hub** — Vista de órdenes y productos del usuario. Botones para subir archivos CSV/XLSX a la base de datos.
2. **Optimizador** — Formulario de parámetros (dirección CD, número de camiones, ventanas horarias, capacidad). Inicia la optimización y muestra el progreso en tiempo real.
3. **Resultados** — Mapa interactivo folium embebido (`QWebEngineView`) con las rutas optimizadas por día, tabla de rutas y detalle de órdenes no cubiertas.

---

## Workers (QThreads)

Todas las operaciones que involucran red o I/O se ejecutan en threads separados para no bloquear la interfaz.

### `health_worker.py`
Verifica periódicamente que el backend esté disponible (`GET /health`). Muestra un indicador de estado en la barra superior de la app.

### `upload_worker.py`
Gestiona la subida de archivos al backend (`POST /upload`, `/upload-detalle`, `/upload-catalogo`). Emite señales de progreso y resultado al thread principal.

### `data_hub_worker.py`
Carga el dashboard de datos del usuario desde la base de datos (`GET /data/dashboard-db`). Alimenta las tablas del panel Data Hub.

### `progress_worker.py`
Hace polling del estado de un job de optimización (`GET /jobs/{job_id}/status`) hasta que el job termina o falla. Emite señales por cada cambio de etapa.

### `request_worker.py`
Worker genérico de HTTP. Ejecuta cualquier request configurable (método, URL, body, headers) en background y emite la respuesta al caller.

---

## Flujo de datos

```
Usuario ingresa parámetros
    → main_view crea un request_worker
    → request_worker POST /optimize
    → backend retorna job_id
    → progress_worker hace polling de /jobs/{job_id}/status
    → cuando status == "done", fetcha /jobs/{job_id}/result
    → main_view renderiza el mapa HTML en QWebEngineView
```

---

## Dependencias Qt

```
PyQt6>=6.6.0
PyQt6-WebEngine>=6.6.0   # necesario para el mapa embebido
```

Instaladas como parte de `app/requirements.txt`.
