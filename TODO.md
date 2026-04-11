# TODO — SaaS (prioridad actual)

## Crítico

- [x] **CORS mal configurado** — `app/backend/main.py` línea 20
- [x] **Auth endpoints sin validación de body** — `app/backend/api/router.py`

---

## Mayor

- [x] **`/catalog` expone datos de todos los usuarios**
- [x] **`user_id` sin validación explícita en uploads**
- [x] **User enumeration en login**

---

## Menor

- [x] **`get_next_order_number` carga toda la tabla en memoria**
- [x] **Encoding de CSV sin fallback**
- [x] **`timedelta` importado sin uso**
