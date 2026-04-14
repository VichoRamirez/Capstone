"""
Schemas Pydantic para la API de optimización y autenticación.

Define los modelos de entrada/salida utilizados por los endpoints de FastAPI:
autenticación de usuarios, parámetros del optimizador VRP/VRPTW, respuestas
de limpieza de datos y resultados de optimización multi-día.
"""
from pydantic import BaseModel, Field, EmailStr
from typing import Optional


# ── Schemas de autenticación ──────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    """Datos requeridos para registrar un nuevo usuario en el sistema."""

    username: str   # Nombre de usuario único
    email: str      # Correo electrónico del usuario
    password: str   # Contraseña en texto plano (el servicio la hashea)


class LoginRequest(BaseModel):
    """Credenciales para iniciar sesión; identifier puede ser username o email."""

    identifier: str  # Nombre de usuario o correo electrónico
    password: str    # Contraseña en texto plano


class ResetPasswordRequest(BaseModel):
    """Datos necesarios para restablecer la contraseña de un usuario existente."""

    username: str       # Nombre de usuario a verificar
    email: str          # Correo asociado a la cuenta (validación doble)
    new_password: str   # Nueva contraseña en texto plano


# ── Schema principal del optimizador ─────────────────────────────────────────

class OptimizerParams(BaseModel):
    """
    Parámetros de entrada para el optimizador de rutas VRP/VRPTW.

    Controla la flota, capacidades, ventanas de tiempo, costos operacionales
    y las opciones de la metaheurística Tabu Search.
    """

    # --- Flota ---
    num_trucks: int = Field(..., gt=0, description="Total number of trucks available")
    # Límite duro de camiones disponibles; el optimizador no puede superar este valor

    # --- Combustible y costos ---
    km_per_liter: float = Field(6.4, gt=0, description="Truck fuel efficiency")
    # Rendimiento promedio del camión; usado para calcular costo de combustible por ruta

    fuel_type: str = Field(
        "diesel",
        description="Selected fuel type: diesel | gasoline_93 | gasoline_95 | gasoline_97",
    )
    # Tipo de combustible seleccionado; determina qué precio/litro se aplica

    truck_fixed_cost_clp: float = Field(
        20000.0,
        gt=0,
        description="Fixed operating cost per truck in CLP",
    )
    # Costo fijo por camión utilizado (peajes, conductor, depreciación diaria) en CLP

    diesel_price_clp: Optional[float] = Field(
        None,
        gt=0,
        description="Selected fuel price in CLP/L; if omitted backend may fetch from API",
    )
    # Precio del combustible en CLP/L; si es None, el backend lo consulta desde la CNE

    # --- Tráfico ---
    use_time_dependent_traffic: bool = Field(
        False,
        description="Apply postoptimal time-dependent traffic profile by zone/hour",
    )
    # Activa ajuste post-óptimo de tiempos según perfil de tráfico por zona y hora

    # --- Capacidades del camión ---
    space_per_truck: Optional[float] = Field(None, gt=0, description="Max volume capacity per truck (m³)")
    # Capacidad volumétrica máxima por camión en metros cúbicos; None = sin restricción de volumen

    weight_per_truck: Optional[float] = Field(None, gt=0, description="Max weight capacity per truck (kg)")
    # Capacidad de peso máxima por camión en kilogramos; None = sin restricción de peso

    # --- Opciones del modelo ---
    alternatives: int = Field(1, ge=1, description="Number of alternatives/runs")
    # Número de soluciones alternativas a generar (actualmente reservado para uso futuro)

    model_runtime: Optional[int] = Field(None, description="Time limit for the model in seconds")
    # Tiempo máximo de ejecución del modelo en segundos; None = sin límite explícito

    worktime_windows: Optional[str] = Field(None, description="Shift limits, e.g. '09:00-17:00'")
    # Ventana de turno de los conductores en formato 'HH:MM-HH:MM'; None usa el default del servicio

    # --- Depósito y planificación diaria ---
    depot_address: list[float] = Field(..., description="Coordinates [lat, lon] of the distribution center")
    # Coordenadas [latitud, longitud] del centro de distribución (punto de origen/retorno)

    deliveries_per_day: int = Field(150, gt=0, description="Max deliveries to assign per calendar day")
    # Cantidad máxima de órdenes que se asignan por día calendario antes de diferir al día siguiente

    # --- SaaS multi-tenant ---
    user_id: Optional[int] = Field(None, description="ID of the user running the optimization (SaaS DB filtering)")
    # ID del usuario autenticado; filtra ventas y catálogo desde la base de datos MySQL

    # --- Tabu Search ---
    use_tabu_search: bool = Field(True, description="Whether to run Tabu Search improvement after Solomon I1")
    # Habilita la fase de mejora Tabu Search tras la construcción inicial con Solomon I1

    tabu_seconds: float = Field(20.0, gt=0, description="Time limit for Tabu Search in seconds")
    # Presupuesto de tiempo para Tabu Search por día calendario; a mayor tiempo, mejor solución potencial


# ── Schemas de limpieza de datos ──────────────────────────────────────────────

class CleaningError(BaseModel):
    """Representa un error detectado durante la validación/limpieza de un CSV."""

    origen: str          # Archivo de origen del error ('ventas' o 'detalle')
    fila: int            # Número de fila donde se encontró el error (base 0)
    campo: str           # Nombre de la columna con el valor inválido
    valor_original: str  # Valor tal como llegó en el CSV antes de limpiar
    error: str           # Descripción legible del error encontrado


class CleaningResponse(BaseModel):
    """Resultado del endpoint /clean: resumen de filas procesadas, errores y previsualizaciones."""

    total_ventas: int           # Total de filas procesadas en el archivo de ventas
    total_detalle: int          # Total de filas procesadas en el archivo de detalle
    errores: list[CleaningError]      # Lista de todos los errores encontrados en ambos archivos
    preview_ventas: list[dict]        # Primeras filas del CSV de ventas ya limpias (para previsualización)
    preview_detalle: list[dict]       # Primeras filas del CSV de detalle ya limpias (para previsualización)


# ── Schemas de resultado de optimización ─────────────────────────────────────

class DayResult(BaseModel):
    """Resultado de optimización para un día calendario específico."""

    date: str           # Fecha del día optimizado en formato 'YYYY-MM-DD'
    routes_csv: str     # Contenido CSV con las rutas asignadas para ese día
    uncovered_csv: str  # Contenido CSV con las órdenes no cubiertas (diferidas al día siguiente)
    map_html: str       # Mapa Folium en HTML con las rutas visualizadas
    stats: dict         # Estadísticas del día: distancia total, costo, camiones usados, etc.


class MultiDayOptimizationResponse(BaseModel):
    """Respuesta completa del endpoint /optimize: resultados por día y estadísticas globales."""

    days: list[DayResult]          # Lista ordenada de resultados por cada día optimizado
    cleaning_errors: list          # Errores de limpieza detectados antes de la optimización
    global_stats: dict = Field(default_factory=dict)
    # Métricas agregadas de toda la corrida: distancia total, costo total, órdenes cubiertas, etc.
