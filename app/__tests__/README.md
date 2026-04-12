# Suite de pruebas

Pruebas unitarias, de integración y de estrés para el backend de Capstone Analytics.

## Requisitos previos

```bash
# Instalar dependencias (desde la raíz del repositorio)
pip install -r app/requirements.txt

# El archivo app/config/.env debe existir con credenciales MySQL válidas
# (las pruebas de integración se conectan a la base de datos real)
```

---

## Estructura

```
__tests__/
├── conftest.py          # Fixtures compartidos (api_client, db_session, requires_osrm)
├── unit/                # Pruebas unitarias — sin red ni DB
│   ├── test_auth_service.py
│   ├── test_cleaning_service.py
│   ├── test_job_store.py
│   └── test_road_routing.py
├── integration/         # Pruebas de integración — requieren MySQL
│   ├── test_auth_endpoints.py
│   ├── test_misc_endpoints.py
│   └── test_upload_endpoints.py
├── stress/              # Benchmarks y pruebas de carga
│   ├── test_benchmarks.py   # pytest-benchmark
│   └── locustfile.py        # Locust HTTP stress test
└── results/             # Reportes generados (ignorados por git excepto .gitkeep)
    └── .gitkeep
```

---

## Ejecutar pruebas

Todos los comandos se ejecutan desde `app/`.

### Pruebas unitarias

No requieren red ni base de datos. Las llamadas a servicios externos y a la DB se mockean.

```bash
pytest __tests__/unit/ -v
```

### Pruebas de integración

Requieren MySQL corriendo con las tablas creadas. Usan la misma base de datos configurada en `app/config/.env`. Los datos de prueba usan UUIDs aleatorios para no colisionar entre corridas.

```bash
pytest __tests__/integration/ -v
```

### Benchmarks de rendimiento

```bash
pytest __tests__/stress/test_benchmarks.py -v --benchmark-sort=mean
```

Los tests marcados `@requires_osrm` se saltan automáticamente si OSRM no está corriendo en `127.0.0.1:5010`.

### Pruebas de estrés con Locust

Requiere el backend corriendo en `:8000`.

```bash
# UI interactiva
locust -f __tests__/stress/locustfile.py --host=http://127.0.0.1:8000

# Headless (CI)
locust -f __tests__/stress/locustfile.py --host=http://127.0.0.1:8000 \
  --users 50 --spawn-rate 5 --run-time 60s --headless \
  --csv=__tests__/results/locust \
  --html=__tests__/results/locust_report.html
```

### Todo junto (sin stress)

```bash
pytest -v -m "not stress"
```

---

## Resultados

Los reportes se guardan en `__tests__/results/` después de cada corrida:

| Archivo | Contenido |
|---|---|
| `junit.xml` | Reporte JUnit (compatible con CI) |
| `report.html` | Reporte HTML navegable (pytest-html) |
| `benchmarks.json` | Resultados de pytest-benchmark en JSON |
| `locust_report.html` | Reporte HTML de Locust |
| `locust_*.csv` | Estadísticas de Locust en CSV |

---

## Markers disponibles

| Marker | Descripción |
|---|---|
| `integration` | Pruebas que tocan la DB o el API completo |
| `stress` | Benchmarks y pruebas de carga |
| `osrm` | Requiere OSRM corriendo en `127.0.0.1:5010` |

```bash
# Solo integración
pytest -m integration -v

# Excluir stress y osrm
pytest -m "not stress and not osrm" -v
```

---

## Fixtures principales (conftest.py)

| Fixture | Descripción |
|---|---|
| `api_client` | `TestClient` de FastAPI conectado a MySQL real |
| `db_session` | Sesión MySQL directa; rollback automático al terminar el test |
| `requires_osrm` | Marker que salta el test si OSRM no está disponible |
