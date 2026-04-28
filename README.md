# Simulación de Eventos Discretos — Panadería de Supermercado
 
**Capstone Analytics · IIC511AD · Universidad del Desarrollo · 2026-1**
 
Proyecto de simulación de eventos discretos del proceso de fabricación de pan en la panadería de un supermercado, implementado en SIMIO. El objetivo es determinar la combinación mínima de recursos (panaderos, manipuladores, hornos, mezcladoras, amasadoras) y la política de secuenciamiento de producción que garantice un nivel de servicio ≥ 95 % de demanda satisfecha por tipo de pan.
 
---
 
## Integrantes
 
| Integrante | Responsabilidad principal |
|---|---|
| Joaquín Parraud | Construcción del modelo físico en SIMIO: layout, objetos, recursos, estaciones y animación |
| Sebastián Ramírez | Diseño del flujo lógico: reglas de producción, secuencia operacional, restricciones y verificación estructural |
| Vicente Ramírez | Análisis de datos y probabilidades: demanda horaria, elección de productos, calibración y análisis experimental |
 
**Profesor:** Víctor Vera, PhD
 
---
 
## Descripción del problema
 
La panadería opera un ciclo diario de producción de 10 tipos de pan fresco para abastecer la sala de ventas de autoservicio entre 09:00 y 21:00. La jefatura detectó un alto nivel de quiebres de stock que genera pérdidas de venta directas y reclamos. La hipótesis operacional es que el problema no radica en el volumen de demanda —conocido y cuantificado— sino en la combinación y coordinación de recursos y en la política de secuenciamiento de producción.
 
### Tipos de pan
 
| Tipo | Familia de horneado | Demanda diaria (kg) |
|---|---|---|
| Marraqueta | 2 — 18 min | 2.000 |
| Hallulla | 2 — 18 min | 1.700 |
| Pan Hot Dog | 1 — 14 min | 1.200 |
| Marraqueta Integral | 3 — 21 min | 800 |
| Hallulla Integral | 2 — 18 min | 800 |
| Ciabatta | 3 — 21 min | 400 |
| Amasado | 2 — 18 min | 380 |
| Baguette | 3 — 21 min | 350 |
| Dobladita | 1 — 14 min | 350 |
| Bocado de Dama | 1 — 14 min | 220 |
| **Total** | | **8.200** |
 
---
 
## Estructura del repositorio
 
```
.
├── LaTeX Projects/
│   ├── InformeDePlanificación/
│   │   ├── informe_planificacion.tex   # Fuente LaTeX del informe de planificación (H0)
│   │   ├── informe_planificacion.pdf   # PDF compilado
│   │   └── UDD.png                     # Logo UDD utilizado por el documento
│   ├── InformeAcadémico/               # (en desarrollo)
│   └── InformeTécnico/                 # (en desarrollo)
│
├── Panadería - Data/
│   ├── parametros_proceso_panaderia.xlsx                    # Tiempos de etapas, tamaños de lote y capacidades por tipo de pan
│   ├── perfil_demanda_por_hora_panaderia.xlsx               # Demanda esperada (kg) por franja horaria y tipo de pan
│   ├── probabilidades_eleccion_por_hora.xlsx                # Probabilidad de elección por tipo de pan y franja horaria
│   ├── Simulación Capstone Analytics - Fábrica de Pan.pdf   # Enunciado oficial del proyecto
│   └── Metodología de Desarrollo.pdf                        # Metodología sugerida por la cátedra
│
├── Panadería - Model/
│   └── Modelo - Panadería.spfx   # Modelo SIMIO del proceso de panadería
│
└── README.md
```
 
---
 
## Datos de entrada
 
### `parametros_proceso_panaderia.xlsx`
Contiene, por tipo de pan: familia de proceso, etapas secuenciales con sus tiempos (min), tamaño de lote de referencia (kg), temperatura de horneado (°C) y capacidad de carga por carro (kg).
 
Secuencia de etapas (todas las variedades): **Pesado y dosificación → Amasado → Reposo en masa → Dividido/formado → Fermentación final → Horneado → Enfriado y traslado**.
 
Los tiempos de horneado son fijos según familia: Familia 1 = 14 min, Familia 2 = 18 min, Familia 3 = 21 min.
 
### `perfil_demanda_por_hora_panaderia.xlsx`
Demanda esperada en kg por franja horaria (09:00–21:00, 12 franjas de 1 hora) para cada uno de los 10 tipos de pan. Los peaks de demanda se concentran en 12:00–14:00 y 18:00–20:00.
 
### `probabilidades_eleccion_por_hora.xlsx`
Probabilidad de que un cliente elija cada tipo de pan como primera selección, por franja horaria. Las probabilidades suman 1,0 en cada franja. Se usan también para la selección del segundo y tercer tipo de pan (mismas proporciones relativas, excluyendo los ya elegidos).
 
---
 
## Modelo de demanda
 
- Un cliente compra **1 tipo** (50 %), **2 tipos distintos** (35 %) o **3 tipos distintos** (15 %).
- La cantidad comprada por tipo sigue una **distribución triangular** (mín = 0,3 kg; moda y máximo varían por tipo).
- Las llegadas de clientes siguen el perfil horario del archivo de demanda.
---
 
## Lógica del horno
 
| Parámetro | Valor |
|---|---|
| Capacidad máxima | 6 carros × 18 bandejas = 108 bandejas/corrida |
| Carga mínima para disparar corrida | 600 kg (50 % de capacidad) |
| Tiempo máximo de espera | 15 min |
| Carga/descarga | 5 min c/u, requiere 1 manipulador |
| Setup misma familia | 0 min |
| Setup cambio de familia | 5 min |
| Restricción de compatibilidad | Solo una familia por corrida |
 
---
 
## Plan de trabajo
 
| Fase | Nombre | Período | Entregable / Hito |
|---|---|---|---|
| 0 | Planificación | 20–27 Abr | Documento de planificación (H0) |
| 1 | Análisis del caso y diagramas | 27 Abr–2 May | Tabla maestra de parámetros, supuestos, modelo conceptual (H1) |
| 2 | Construcción en SIMIO | 3–10 May | Modelo integrado, ejecutable, 20 réplicas sin errores (H2) |
| 3 | Verificación y Validación | 10–12 May | Lista de chequeo completada, validación lógica aprobada (H3) |
| 4 | Experimentos y Análisis | 13–17 May | Tabla comparativa de escenarios + análisis de sensibilidad (H4) |
| 5 | Documentación y Entrega | 17–20 May | Informe técnico + Informe académico + Modelo SIMIO |
 
---
 
## KPIs del modelo
 
El modelo reporta automáticamente, como mínimo:
 
- Quiebres de stock por tipo de pan
- Kg no vendidos por quiebre
- % demanda satisfecha por tipo
- Sobrantes al final del día
- Producción total por tipo (kg y lotes)
- Inventario promedio y mínimo en sala
- Utilización de hornos, mezcladoras, amasadoras, panaderos y manipuladores
- Tiempo promedio de ciclo por lote
- Tiempo de espera promedio por recurso
- Frecuencia de setups de horno
---
 
## Alcance
 
**Dentro del alcance:** proceso productivo completo (pesado → reposición en sala), gestión de hornos batch con restricción de familias, recursos humanos con turnos traslapados y descansos escalonados, inventario en sala, generación de demanda estocástica, políticas de secuenciamiento y análisis de sensibilidad.
 
**Fuera del alcance:** compras y gestión de materias primas, transporte externo, fallas de equipos, ventas de otros productos, deterioro por sobreproducción dentro del horizonte diario.
 
---
 
## Requerimientos
 
- **SIMIO** (licencia estudiantil UDD) — solo Windows
- Los archivos `.xlsx` de datos no requieren software adicional para ser consultados (Excel o LibreOffice)
- LaTeX (distribución completa con `pgfgantt`) para compilar el informe de planificación