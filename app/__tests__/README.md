cd app/

# Instalar dependencias de prueba
pip install pytest httpx pytest-asyncio locust pytest-benchmark

# Unit tests (sin red ni DB real)
pytest __tests__/unit/ -v

# Integration tests (SQLite in-memory)
pytest __tests__/integration/ -v

# Benchmarks (+ OSRM si está levantado)
pytest __tests__/stress/test_benchmarks.py -v --benchmark-sort=mean

# Stress con Locust (requiere app corriendo en :8000)
locust -f __tests__/stress/locustfile.py --host=http://127.0.0.1:8000

# Todo junto
pytest -v -m "not stress"
