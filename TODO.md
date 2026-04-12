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

---

## Trabajo futuro (si se implementa sistema de pedidos)

- [ ] **`get_next_order_number` no filtra por usuario** — `app/database/repositories/venta_repository.py`
  - El número de orden máximo se calcula sobre toda la tabla, sin distinción por empresa.
  - Si se crea un flujo para generar órdenes desde la app, el contador debe ser por `user_id`
    para que cada empresa tenga su propia secuencia independiente.

## Pruebas unitarias e integrales

- [x] Implementar pruebas unitarias para los servicios
- [x] Implementar pruebas integrales para la API
- [x] Implementar pruebas de integración para la API

## Cambios que hay que hacer (antes de la presentación final)

- [ ] Perfeccionar la UI. Actualmente los textos en los botones se ven cortados, haciendo que sea muy complicado de usar.
- [ ] Quitar el Roadmap, agregar lo razonable y quitar lo que no lo sea.
- [ ] Quitar el botón de "Work in Progress" de la UI. (si no lo tenemos, no necesitamos mostrarlo. podemos trabajarlo en una rama del git)
- [ ] Quitar el input para poner el link de la API. (es un detalle técnico que no aporta nada al usuario)
