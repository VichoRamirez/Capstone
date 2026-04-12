# Base de datos — Capstone Analytics

Motor **MySQL** accedido mediante **SQLAlchemy (ORM)**. Las credenciales se leen desde `app/config/.env`; la URL de conexión se construye en `app/config/settings.py`.

---

## Índice

1. [Configuración y conexión](#configuración-y-conexión)
2. [Crear las tablas](#crear-las-tablas)
3. [Diagrama entidad-relación](#diagrama-entidad-relación)
4. [Tablas](#tablas)
   - [usuarios](#usuarios)
   - [catalogo](#catalogo)
   - [ventas](#ventas)
   - [detalle](#detalle)
5. [Relaciones y restricciones](#relaciones-y-restricciones)
6. [Repositorios](#repositorios)
7. [Patrón de sesión](#patrón-de-sesión)
8. [Comportamiento de upsert](#comportamiento-de-upsert)

---

## Configuración y conexión

Variables de entorno requeridas en `app/config/.env`:

| Variable | Descripción | Default |
|---|---|---|
| `DB_HOST` | Host del servidor MySQL | `localhost` |
| `DB_PORT` | Puerto | `3306` |
| `DB_USER` | Usuario MySQL | `root` |
| `DB_PASSWORD` | Contraseña (soporta caracteres especiales) | — |
| `DB_NAME` | Nombre de la base de datos | `capstone_db` |

El engine se crea una sola vez en `database/connection.py` con `pool_pre_ping=True` (reconecta automáticamente si la conexión cae).

```python
from database.connection import get_session

session = get_session()
try:
    # ... operaciones
finally:
    session.close()
```

---

## Crear las tablas

Las tablas se crean desde los modelos ORM. Ejecutar una sola vez al inicializar el entorno:

```bash
cd app
python -c "from database.models import Base; from database.connection import engine; Base.metadata.create_all(engine)"
```

> No hay migraciones automáticas (Alembic). Si se modifica un modelo, la tabla debe actualizarse manualmente con `ALTER TABLE` o recrearse.

---

## Diagrama entidad-relación

```
┌─────────────────────────────┐
│          usuarios           │
├─────────────────────────────┤
│ PK  id            INTEGER   │
│     username      VARCHAR   │◄──────────────┐
│     email         VARCHAR   │               │ id_usuario (lógico,
│     password      VARCHAR   │               │ sin FK declarada)
│     tipo_usuario  VARCHAR   │               │
│     fecha_creacion DATETIME │               │
└─────────────────────────────┘               │
                                              │
┌─────────────────────────────┐               │
│          catalogo           │               │
├─────────────────────────────┤               │
│ PK  SKU           VARCHAR   │               │
│ PK  id_usuario    INTEGER   │───────────────┤
│     Descripción SKU  TEXT   │               │
│     Largo_cm      FLOAT     │               │
│     Ancho_cm      FLOAT     │               │
│     Alto_cm       FLOAT     │               │
│     Volumen_unitario_m3 FL  │               │
│     Peso_unitario_kg    FL  │               │
│     Tipo_embalaje   TEXT    │               │
└─────────────────────────────┘               │
                                              │
┌─────────────────────────────┐               │
│           ventas            │               │
├─────────────────────────────┤               │
│ PK  Número de Orden VARCHAR │               │
│ PK  id_usuario    INTEGER   │───────────────┤
│     RUT           TEXT      │               │
│     Nombre cliente   TEXT   │               │
│     Dirección cliente TEXT  │               │
│     Comuna        TEXT      │               │
│     Fecha de Pedido  DATE   │               │
│     Estado        VARCHAR   │               │
│     Monto Pedido  BIGINT    │               │
│     Fecha de despacho DATE  │               │
│     Latitud       FLOAT     │               │
│     Longitud      FLOAT     │               │
└────────────────┬────────────┘               │
                 │ Número de Orden            │
                 │ (join lógico)              │
┌────────────────▼────────────┐               │
│           detalle           │               │
├─────────────────────────────┤               │
│ PK  Número de Orden VARCHAR │               │
│ PK  SKU           VARCHAR   │               │
│ PK  id_usuario    INTEGER   │───────────────┘
│     Descripción SKU  TEXT   │
│     Cantidad      BIGINT    │
│     Largo_cm      FLOAT     │
│     Ancho_cm      FLOAT     │
│     Alto_cm       FLOAT     │
│     Volumen_unitario_m3 FL  │
│     Peso_unitario_kg    FL  │
│     Volumen_total_m3    FL  │
│     Peso_total_kg       FL  │
└─────────────────────────────┘
```

> Las relaciones entre `id_usuario` y `usuarios.id` son **lógicas** — no hay `FOREIGN KEY` declarada en el esquema SQL. El aislamiento por usuario se garantiza a nivel de aplicación filtrando siempre por `id_usuario` en las consultas.

---

## Tablas

### `usuarios`

Modelo ORM: `database/models.py → class Usuario`  
Repositorio: `database/repositories/usuario_repository.py`

| Columna | Tipo SQL | Restricciones | Descripción |
|---|---|---|---|
| `id` | `INTEGER` | PK, autoincrement | Identificador único del usuario |
| `username` | `VARCHAR(50)` | NOT NULL, UNIQUE | Nombre de usuario para login |
| `email` | `VARCHAR(100)` | NOT NULL, UNIQUE | Correo electrónico |
| `password` | `VARCHAR(100)` | NOT NULL | Hash bcrypt de la contraseña |
| `tipo_usuario` | `VARCHAR(255)` | NOT NULL, default `"Free"` | Tipo de cuenta (`"Free"` / `"Premium"`) |
| `fecha_creacion` | `DATETIME` | default `utcnow` | Fecha de registro |

---

### `catalogo`

Modelo ORM: `database/models.py → class Producto`  
Repositorio: `database/repositories/producto_repository.py`

**PK compuesta: `(SKU, id_usuario)`** — cada usuario tiene su propio catálogo de productos.

| Columna | Tipo SQL | Restricciones | Descripción |
|---|---|---|---|
| `SKU` | `VARCHAR(10)` | PK | Código de producto |
| `id_usuario` | `INTEGER` | PK | Referencia lógica a `usuarios.id` |
| `Descripción SKU` | `TEXT` | nullable | Nombre o descripción del producto |
| `Largo_cm` | `FLOAT` | nullable | Dimensión largo en centímetros |
| `Ancho_cm` | `FLOAT` | nullable | Dimensión ancho en centímetros |
| `Alto_cm` | `FLOAT` | nullable | Dimensión alto en centímetros |
| `Volumen_unitario_m3` | `FLOAT` | nullable | Volumen de una unidad en m³ |
| `Peso_unitario_kg` | `FLOAT` | nullable | Peso de una unidad en kg |
| `Tipo_embalaje` | `TEXT` | nullable | Tipo de embalaje del producto |

**Comportamiento de insert:** solo inserta si el SKU no existe para ese `id_usuario`. No actualiza registros existentes (skips on conflict).

---

### `ventas`

Modelo ORM: `database/models.py → class Venta`  
Repositorio: `database/repositories/venta_repository.py`

**PK compuesta: `(Número de Orden, id_usuario)`** — permite que distintos usuarios tengan números de orden iguales sin colisión.

| Columna | Tipo SQL | Restricciones | Descripción |
|---|---|---|---|
| `Número de Orden` | `VARCHAR(50)` | PK | Identificador único de la orden |
| `id_usuario` | `INTEGER` | PK | Referencia lógica a `usuarios.id` |
| `RUT` | `TEXT` | nullable | RUT del cliente (formato `XX.XXX.XXX-X`) |
| `Nombre cliente` | `TEXT` | nullable | Nombre completo del destinatario |
| `Dirección cliente` | `TEXT` | nullable | Dirección de entrega |
| `Comuna` | `TEXT` | nullable | Comuna (normalizada a Región Metropolitana) |
| `Fecha de Pedido` | `DATE` | nullable | Fecha en que se generó el pedido |
| `Estado` | `VARCHAR(15)` | nullable | `"Pendiente"` / `"Entregado"` / `"No entregado"` |
| `Monto Pedido` | `BIGINT` | nullable | Monto total del pedido en CLP |
| `Fecha de despacho Solicitada` | `DATE` | nullable | Fecha solicitada de despacho |
| `Latitud` | `FLOAT` | nullable | Coordenada geográfica (geocodificada) |
| `Longitud` | `FLOAT` | nullable | Coordenada geográfica (geocodificada) |

**Valores válidos de `Estado`:**
- `"Pendiente"` — orden activa, entra al optimizador
- `"Entregado"` — entrega confirmada
- `"No entregado"` — intento fallido

> **Importante:** el optimizador filtra exclusivamente por `Estado = "Pendiente"`. Usar otro capitalizado (`"PENDIENTE"`, `"pendiente"`) excluirá la orden del ruteo.

**Comportamiento de upsert:** si la orden ya existe para ese `id_usuario`, actualiza todos sus campos. Si no existe, la inserta.

---

### `detalle`

Modelo ORM: `database/models.py → class Detalle`  
Repositorio: `database/repositories/detalle_repository.py`

**PK compuesta: `(Número de Orden, SKU, id_usuario)`** — cada línea de producto dentro de una orden es única por usuario.

| Columna | Tipo SQL | Restricciones | Descripción |
|---|---|---|---|
| `Número de Orden` | `VARCHAR(50)` | PK | Referencia lógica a `ventas` |
| `SKU` | `VARCHAR(10)` | PK | Código del producto entregado |
| `id_usuario` | `INTEGER` | PK | Referencia lógica a `usuarios.id` |
| `Descripción SKU` | `TEXT` | nullable | Nombre del producto |
| `Cantidad` | `BIGINT` | nullable | Unidades del SKU en esta orden |
| `Largo_cm` | `FLOAT` | nullable | Dimensión del producto |
| `Ancho_cm` | `FLOAT` | nullable | Dimensión del producto |
| `Alto_cm` | `FLOAT` | nullable | Dimensión del producto |
| `Volumen_unitario_m3` | `FLOAT` | nullable | Volumen por unidad en m³ |
| `Peso_unitario_kg` | `FLOAT` | nullable | Peso por unidad en kg |
| `Volumen_total_m3` | `FLOAT` | nullable | `Volumen_unitario_m3 × Cantidad` |
| `Peso_total_kg` | `FLOAT` | nullable | `Peso_unitario_kg × Cantidad` |

**Comportamiento de upsert:** si la combinación `(Número de Orden, SKU, id_usuario)` ya existe, actualiza todos sus campos. Si no existe, inserta.

---

## Relaciones y restricciones

### Multi-tenancy por `id_usuario`

Toda tabla de datos (`catalogo`, `ventas`, `detalle`) incluye `id_usuario` como parte de su clave primaria. Esto garantiza que:

- Los datos de cada cliente están completamente aislados.
- No existe colisión entre órdenes de distintos usuarios con el mismo número.
- Todas las consultas deben filtrar por `id_usuario` — hacerlo a nivel de repositorio, nunca omitirlo.

### Sin FK declaradas

Las relaciones `id_usuario → usuarios.id` y `detalle.Número de Orden → ventas.Número de Orden` son **lógicas**: existen como convención de código pero no como `FOREIGN KEY` en el DDL. Esto es intencional para simplificar el upsert masivo de CSVs sin gestionar dependencias de orden de inserción.

### Join entre `ventas` y `detalle`

El optimizador necesita peso y volumen por orden. El join se hace en `db_persistence.get_pending_orders_df()`:

```python
# Agrega peso y volumen total desde detalle, join con ventas Pendiente
stats = session.query(
    Detalle.numero_orden,
    Detalle.id_usuario,
    func.sum(Detalle.peso_total_kg).label("Peso_total_pedido"),
    func.sum(Detalle.volumen_total_m3).label("Volumen_total_pedido"),
).group_by(Detalle.numero_orden, Detalle.id_usuario).subquery()
```

---

## Repositorios

Todos los repositorios heredan de `BaseRepository` (`database/repositories/base_repository.py`), que provee `get_all`, `get_by_id`, `create`, `update`, `delete`.

| Repositorio | Modelo | Métodos clave |
|---|---|---|
| `UsuarioRepository` | `Usuario` | `get_by_username`, `get_by_email`, `create_user`, `update_password` |
| `ProductoRepository` | `Producto` | `get_all_by_user`, `get_by_sku_and_user`, `upsert_for_user` |
| `VentaRepository` | `Venta` | `get_by_numero_orden`, `upsert`, `get_next_order_number` |
| `DetalleRepository` | `Detalle` | `get_by_numero_orden`, `add_items` |

### `get_next_order_number` — deuda técnica conocida

`VentaRepository.get_next_order_number()` hace `MAX(Número de Orden)` sobre **todas** las ventas, sin filtrar por `id_usuario`. El número retornado puede verse afectado por órdenes de otros usuarios. Pendiente de corrección.

---

## Patrón de sesión

Cada operación que accede a la BD crea su propia sesión y la cierra en `finally`:

```python
from database.connection import get_session
from database.repositories.venta_repository import VentaRepository

session = get_session()
try:
    repo = VentaRepository(session)
    repo.upsert(payload, user_id)
finally:
    session.close()
```

> No se usa el patrón context manager (`with`) ni dependencias de FastAPI (`Depends`) — la sesión se gestiona manualmente en cada servicio.

---

## Comportamiento de upsert

Cada tabla tiene su propia estrategia para manejar registros duplicados:

| Tabla | Estrategia | Error aislado |
|---|---|---|
| `ventas` | Upsert fila por fila: actualiza si existe, inserta si no | Sí — error en una fila no afecta las demás |
| `detalle` | Upsert fila por fila dentro de un batch | Sí — `session.rollback()` por fila con error |
| `catalogo` | Skip si ya existe `(SKU, id_usuario)` | Sí — `session.rollback()` por fila con error |

Todos los endpoints de upload retornan `200 OK` con `success_count` y `error_count`, aunque haya fallos parciales. Revisar `errors[]` en la respuesta para detectar filas problemáticas.
