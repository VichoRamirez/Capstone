# TODO — SaaS (prioridad actual)

## Crítico

- [ ] **CORS mal configurado** — `app/backend/main.py` línea 20
  - `allow_origins=["*"]` + `allow_credentials=True` está prohibido por el estándar y permite CSRF.
  - Fijar: lista explícita de orígenes (`["http://localhost:8000"]`) en producción.

- [ ] **Auth endpoints sin validación de body** — `app/backend/api/router.py` líneas 1552–1581
  - `/auth/register`, `/auth/login` y `/auth/reset-password` usan `payload: dict` sin esquema.
  - Sin validación: campos faltantes o de tipo incorrecto no se rechazan en la capa HTTP.
  - Fijar: definir modelos Pydantic para cada endpoint de auth.

---

## Mayor

- [ ] **`/catalog` expone datos de todos los usuarios** — `app/backend/api/router.py` línea 1592
  - `repo.get_all()` no filtra por `user_id` → una empresa puede ver el catálogo de otra.
  - Fijar: aceptar `user_id: int` como query param y filtrar en `ProductoRepository`.

- [ ] **`user_id` sin validación explícita en uploads** — `app/backend/api/router.py` líneas 1618 y 1657
  - `/upload` y `/upload-detalle` no retornan `HTTPException(400)` si `user_id` es `None` antes
    de llamar al servicio. El error queda opaco para el cliente.
  - Fijar: validar `user_id is None` al inicio del endpoint.

- [ ] **User enumeration en login** — `app/backend/services/auth_service.py` líneas 87 y 91
  - Mensajes distintos según si el usuario existe o la contraseña es incorrecta.
  - Fijar: usar mensaje genérico `"Credenciales incorrectas."` en ambos casos.

---

## Menor

- [ ] **`get_next_order_number` carga toda la tabla en memoria** — `app/database/repositories/venta_repository.py` línea 47
  - Hace `query().all()` para encontrar el máximo numérico.
  - Fijar: `session.query(func.max(...)).scalar()`.

- [ ] **Encoding de CSV sin fallback** — `app/backend/api/router.py` línea 1631
  - `content.decode("utf-8")` falla con archivos Latin-1 (común en Excel español).
  - Fijar: intentar `utf-8`, luego `latin-1` como fallback.

- [ ] **`timedelta` importado sin uso** — `app/backend/api/router.py` línea 15
  - Eliminar de la línea de imports.
