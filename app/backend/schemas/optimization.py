from pydantic import BaseModel, Field, EmailStr
from typing import Optional


# ── Auth schemas ──────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    identifier: str
    password: str

class ResetPasswordRequest(BaseModel):
    username: str
    email: str
    new_password: str


class OptimizerParams(BaseModel):
    num_trucks: int = Field(..., gt=0, description="Total number of trucks available")
    km_per_liter: float = Field(6.4, gt=0, description="Truck fuel efficiency")
    fuel_type: str = Field(
        "diesel",
        description="Selected fuel type: diesel | gasoline_93 | gasoline_95 | gasoline_97",
    )
    truck_fixed_cost_clp: float = Field(
        20000.0,
        gt=0,
        description="Fixed operating cost per truck in CLP",
    )
    diesel_price_clp: Optional[float] = Field(
        None,
        gt=0,
        description="Selected fuel price in CLP/L; if omitted backend may fetch from API",
    )
    use_time_dependent_traffic: bool = Field(
        False,
        description="Apply postoptimal time-dependent traffic profile by zone/hour",
    )
    space_per_truck: Optional[float] = Field(None, gt=0, description="Max volume capacity per truck (m³)")
    weight_per_truck: Optional[float] = Field(None, gt=0, description="Max weight capacity per truck (kg)")
    alternatives: int = Field(1, ge=1, description="Number of alternatives/runs")
    model_runtime: Optional[int] = Field(None, description="Time limit for the model in seconds")
    worktime_windows: Optional[str] = Field(None, description="Shift limits, e.g. '09:00-17:00'")
    depot_address: list[float] = Field(..., description="Coordinates [lat, lon] of the distribution center")
    deliveries_per_day: int = Field(150, gt=0, description="Max deliveries to assign per calendar day")
    user_id: Optional[int] = Field(None, description="ID of the user running the optimization (SaaS DB filtering)")
    use_tabu_search: bool = Field(True, description="Whether to run Tabu Search improvement after Solomon I1")
    tabu_seconds: float = Field(20.0, gt=0, description="Time limit for Tabu Search in seconds")


class CleaningError(BaseModel):
    origen: str
    fila: int
    campo: str
    valor_original: str
    error: str


class CleaningResponse(BaseModel):
    total_ventas: int
    total_detalle: int
    errores: list[CleaningError]
    preview_ventas: list[dict]
    preview_detalle: list[dict]


class DayResult(BaseModel):
    date: str
    routes_csv: str
    uncovered_csv: str
    map_html: str
    stats: dict


class MultiDayOptimizationResponse(BaseModel):
    days: list[DayResult]
    cleaning_errors: list
    global_stats: dict = Field(default_factory=dict)
