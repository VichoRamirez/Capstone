# Formatos de archivos de entrada

La aplicación acepta tres tipos de archivos: **ventas**, **detalle de pedidos** y **catálogo de productos**. Esta página describe las columnas requeridas y opcionales, los formatos aceptados, las transformaciones que aplica el sistema y los errores más comunes.

---

## Índice

1. [CSV de ventas](#csv-de-ventas)
2. [CSV/XLSX de detalle](#csvxlsx-de-detalle)
3. [CSV de catálogo](#csv-de-catálogo)
4. [Consideraciones generales](#consideraciones-generales)

---

## CSV de ventas

**Endpoint:** `POST /upload`  
**Formato:** CSV (UTF-8 o Latin-1 — detectado automáticamente)  
**Separador:** coma `,`

### Columnas

| Columna | Tipo | Requerida | Descripción |
|---|---|---|---|
| `Número de Orden` | texto | Sí | Identificador único de la orden. Forma parte de la PK en BD |
| `RUT` | texto | Sí | RUT del cliente. Cualquier formato es válido — el sistema lo normaliza |
| `Nombre cliente` | texto | Sí | Nombre completo del destinatario |
| `Dirección cliente` | texto | Sí | Dirección de entrega. El sistema la estandariza y geocodifica |
| `Comuna` | texto | Sí | Comuna de la RM. El sistema la canonicaliza |
| `Fecha de Pedido` | fecha | Sí | Fecha en que se generó el pedido. Formatos aceptados: `YYYY-MM-DD`, `DD-MM-YYYY`, `DD/MM/YYYY` |
| `Fecha de despacho Solicitada` | fecha | Sí | Fecha solicitada de entrega. Mismo formato que `Fecha de Pedido` |
| `Estado` | texto | No | Estado de la orden. Default: `"Pendiente"` si no se incluye |
| `Monto Pedido` | entero | No | Monto total en CLP |

### Valores válidos de `Estado`

| Valor | Efecto |
|---|---|
| `Pendiente` | La orden entra al optimizador de rutas |
| `Entregado` | La orden se excluye del optimizador |
| `No entregado` | La orden se excluye del optimizador |

> **Importante:** el valor es case-sensitive. `"PENDIENTE"` o `"pendiente"` no son reconocidos y la orden quedará fuera del ruteo.

### Transformaciones automáticas

**RUT** — se normaliza al formato `XX.XXX.XXX-D`:
```
14512240-4  →  14.512.240-4
145122404   →  14.512.240-4
14.512.240-4  →  (sin cambio)
```

**Dirección** — se aplica una cadena de transformaciones:
- Espacios múltiples → espacio simple, Title Case
- Abreviaturas expandidas: `Av.` → `Avenida`, `Pje.` → `Pasaje`, `Gral.` → `General`, etc.
- Información de departamento eliminada: `, Depto 4B` → (vacío)
- `Stgo` → `Santiago`, `Santiago De Chile` → `Santiago`
- Comas y referencias a "Chile" al final → eliminadas

**Comuna** — se extrae de la dirección si no se especifica, y se canonicaliza a los nombres oficiales de la Región Metropolitana. Alias soportados:

| Alias en CSV | Canon |
|---|---|
| `Estacion Central`, `estación central` | `Estación Central` |
| `Nunoa`, `ñuñoa` | `Ñuñoa` |
| `Penalolen`, `peñalolén` | `Peñalolén` |
| `Conchali`, `conchalí` | `Conchalí` |
| `Maipu`, `maipú` | `Maipú` |
| `Santiago centro`, `Stgo`, `Stgo Centro` | `Santiago` |
| `Lo Barnechea`, `lobarnechea` | `Lo Barnechea` |

**Geocodificación** — tras la limpieza, el sistema llama a Nominatim para obtener `Latitud` y `Longitud`. Usa caché en `geocache.json` para evitar consultas repetidas.

### Ejemplo mínimo

```csv
Número de Orden,RUT,Nombre cliente,Dirección cliente,Comuna,Fecha de Pedido,Fecha de despacho Solicitada,Estado,Monto Pedido
1001,12345678-9,Juan Pérez,Av. Providencia 1234,Providencia,2024-03-01,2024-03-05,Pendiente,45000
1002,98765432-1,María López,Los Leones 567 Las Condes,Las Condes,2024-03-01,2024-03-05,Pendiente,32000
```

---

## CSV/XLSX de detalle

**Endpoint:** `POST /upload-detalle`  
**Formatos aceptados:** CSV (UTF-8 o Latin-1) o XLSX  
**Separador CSV:** coma `,`

### Columnas

| Columna | Tipo | Requerida | Descripción |
|---|---|---|---|
| `Número de Orden` | texto | Sí | Referencia a la orden en `ventas`. Sin FK declarada — debe coincidir manualmente |
| `SKU` | texto | Sí | Código del producto |
| `Cantidad` | entero | Sí | Unidades del SKU en esta orden |
| `Descripción SKU` | texto | No | Nombre del producto |
| `Largo_cm` | decimal | No | Dimensión del producto en cm |
| `Ancho_cm` | decimal | No | Dimensión del producto en cm |
| `Alto_cm` | decimal | No | Dimensión del producto en cm |
| `Volumen_unitario_m3` | decimal | No | Volumen de una unidad en m³ |
| `Peso_unitario_kg` | decimal | No | Peso de una unidad en kg |
| `Volumen_total_m3` | decimal | No | `Volumen_unitario_m3 × Cantidad`. Calculado externamente o incluido en el archivo |
| `Peso_total_kg` | decimal | No | `Peso_unitario_kg × Cantidad`. Calculado externamente o incluido en el archivo |

> `Volumen_total_m3` y `Peso_total_kg` son las columnas que usa el optimizador para asignar carga a los camiones. Si no se incluyen, las órdenes se optimizarán sin restricción de peso/volumen.

### Transformaciones automáticas

**Deduplicación** — si el mismo `(Número de Orden, SKU)` aparece varias veces, se colapsan en una fila sumando `Cantidad`, `Volumen_total_m3` y `Peso_total_kg`. Las demás columnas toman el valor de la primera aparición.

### Ejemplo mínimo

```csv
Número de Orden,SKU,Cantidad,Descripción SKU,Peso_unitario_kg,Volumen_unitario_m3,Peso_total_kg,Volumen_total_m3
1001,SKU-A,2,Silla de oficina,8.5,0.12,17.0,0.24
1001,SKU-B,1,Monitor 24",3.2,0.04,3.2,0.04
1002,SKU-A,3,Silla de oficina,8.5,0.12,25.5,0.36
```

---

## CSV de catálogo

**Endpoint:** `POST /upload-catalogo`  
**Formato:** solo CSV (UTF-8 o UTF-8-BOM)  
**Separador:** coma `,`

El catálogo define las dimensiones y pesos de los productos. Se usa para enriquecer `detalle` cuando ese archivo no incluye columnas de medidas.

### Columnas

| Columna | Tipo | Requerida | Descripción |
|---|---|---|---|
| `SKU` | texto | Sí | Código único del producto |
| `Descripcion SKU` | texto | Sí | Nombre del producto. También se acepta `Descripción SKU` (con tilde) — el sistema lo normaliza |
| `Largo_cm` | decimal | Sí | Largo del producto en cm |
| `Ancho_cm` | decimal | Sí | Ancho del producto en cm |
| `Alto_cm` | decimal | Sí | Alto del producto en cm |
| `Volumen_unitario_m3` | decimal | Sí | Volumen calculado en m³ |
| `Peso_unitario_kg` | decimal | Sí | Peso en kg |
| `Tipo_embalaje` | texto | Sí | Tipo de embalaje (p. ej. `"Caja"`, `"Bolsa"`, `"Pallet"`) |

> Todas las columnas son técnicamente requeridas por el validador. Si falta alguna, el endpoint retorna `400` con detalle de las columnas faltantes.

### Comportamiento de carga

- Si un `SKU` ya existe para ese `user_id`, la fila se **omite** (no actualiza).
- Si un `SKU` es nuevo, se inserta.
- La respuesta incluye `inserted`, `skipped` y `errors`.

### Ejemplo

```csv
SKU,Descripcion SKU,Largo_cm,Ancho_cm,Alto_cm,Volumen_unitario_m3,Peso_unitario_kg,Tipo_embalaje
SKU-A,Silla de oficina,60,60,90,0.12,8.5,Caja
SKU-B,Monitor 24",58,20,42,0.04,3.2,Caja
SKU-C,Teclado USB,45,15,5,0.003,0.6,Bolsa
```

---

## Consideraciones generales

### Encoding

| Archivo | Encodings aceptados |
|---|---|
| Ventas CSV | UTF-8, Latin-1 (detectado automáticamente) |
| Detalle CSV | UTF-8, Latin-1 |
| Detalle XLSX | N/A (binario) |
| Catálogo CSV | UTF-8, UTF-8-BOM (`utf-8-sig`) |

Si el CSV tiene BOM (`\ufeff` al inicio), usar UTF-8-BOM. Excel exporta habitualmente en este formato.

### Nombres de columnas

Los nombres de columnas son **exactos y case-sensitive**. `Número de Orden` es distinto de `numero_orden` o `Numero de Orden`. Los únicos alias aceptados son:

- `Descripción SKU` ↔ `Descripcion SKU` (solo en catálogo — el sistema renombra automáticamente)

### Errores parciales

Los endpoints de upload no fallan completamente si hay filas con errores. Retornan `200 OK` con:

```json
{
  "success_count": 47,
  "error_count": 3,
  "errors": [
    {"fila": 12, "numero_orden": "1042", "error": "Número de Orden vacío"},
    ...
  ]
}
```

Revisar siempre `error_count` y `errors` en la respuesta para detectar filas que no se persistieron.

### `geocache.json`

Cada dirección geocodificada exitosamente se guarda en `geocache.json` en la raíz del repositorio. En la siguiente carga, si la dirección ya fue geocodificada, se reutiliza sin llamar a Nominatim.

- Si una dirección cambia (corrección de typo), borrar su entrada del cache o eliminar el archivo completo para forzar re-geocodificación.
- El archivo puede crecer indefinidamente. Es seguro borrarlo — solo implica re-geocodificar todo en la siguiente carga.
