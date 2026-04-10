# TODO — Problemas encontrados en revisión de código

## Crítico

- [ ] **CORS mal configurado** — `app/backend/main.py` ~línea 18
  - `allow_origins=["*"]` + `allow_credentials=True` está prohibido por el estándar y permite CSRF.
  - Fijar: usar una lista explícita de orígenes permitidos en producción.

- [ ] **Auth endpoints sin body tipado** — `app/backend/api/router.py` ~líneas 1551–1586
  - `/auth/register`, `/auth/login` y `/auth/reset-password` declaran `payload: dict`.
  - FastAPI interpreta `dict` como query param, no como body JSON. Usar un modelo Pydantic o `Body(...)`.

---

## Mayor

- [ ] **`user_id` opcional en uploads sin rechazo HTTP correcto** — `app/backend/api/router.py` ~líneas 1618–1660
  - Acepta `user_id: Optional[int] = Form(None)` pero lo pasa a funciones que fallan si es `None`.
  - Fijar: retornar `HTTPException(status_code=400)` si `user_id` es `None`.

- [ ] **Inconsistencia en Big-M entre modelos** — `app/backend/models/Modelo2.py` / `Modelo3.py`
  - Usan `300 + 300 * (1 - x[...])` en lugar de la variable `M` definida para eso.
  - Si cambia `max_route_time`, los otros modelos (`Modelo.py`, `Modelo4.py`) se actualizan pero estos no.

- [ ] **Mapeado SQLAlchemy ambiguo en `Producto`** — `app/database/models.py` ~líneas 15–29
  - Columna definida como `"SKU"` (mayúsculas) pero atributo Python es `sku` (minúsculas).
  - Puede fallar o comportarse distinto según el motor y collation de MySQL.

---

## Menor

- [ ] **Encoding de CSV sin manejo de errores** — `app/backend/api/router.py` ~línea 1631
  - `content.decode("utf-8")` lanza excepción si el archivo viene en Latin-1 (común en Excel español).
  - Fijar: usar `decode("utf-8", errors="replace")` o detectar encoding.

- [ ] **Reset de contraseña débil** — `app/backend/services/auth_service.py` ~línea 119
  - Valida username y email por separado, no atómicamente.
  - Fijar: una sola query que verifique que ambos pertenecen al mismo registro.

- [ ] **Import no usado** — `app/backend/api/router.py` línea 10
  - `timedelta` está importado pero no se usa en ninguna parte del archivo.
