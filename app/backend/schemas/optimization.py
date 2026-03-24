from pydantic import BaseModel, Field
from typing import Optional

class OptimizerParams(BaseModel):
    num_trucks: int = Field(..., gt=0, description="Total number of trucks available")
    km_per_liter: float = Field(6.4, gt=0, description="Truck fuel efficiency")
    space_per_truck: Optional[float] = Field(None, gt=0, description="Max volume capacity per truck")
    capacity_per_truck: Optional[float] = Field(None, gt=0, description="Max volume capacity per truck (alias)")
    weight_per_truck: Optional[float] = Field(None, gt=0, description="Max weight capacity per truck")
    alternatives: int = Field(1, ge=1, description="Number of alternatives/runs")
    budget: Optional[float] = Field(None, description="Max budget constraint")
    model_runtime: Optional[int] = Field(None, description="Time limit for the model in seconds")
    worktime_windows: Optional[str] = Field(None, description="Shift limits/worktime windows")
    depot_address: list[float] = Field(..., description="Coordinates [lat, lon] of the distribution center")
    deliveries_per_day: Optional[int] = Field(150, description="Max deliveries per day")
