import csv
import io
import math
import random
from datetime import datetime
from typing import Optional
from backend.models.Heuristica import (
    clarke_wright_initial_solution, 
    alns,
    service_start_times,
    generate_toy_data,
    Route
)
from backend.schemas import OptimizerParams

def generate_matrices(orders_data: list, params: OptimizerParams):
    """
    Generates data structures identical to `generate_toy_data` based on the CSV input.
    Assuming the CSV has columns: id, address, demand, time_window (optional).
    If coordinate/distance info is missing, this will generate dummy coords similar to the toy model.
    """
    # For now, since the actual coords are missing in a standard prompt, 
    # we mimic the 'generate_toy_data' logic but tied to the CSV rows.
    
    n_customers = len(orders_data)
    K = list(range(params.num_trucks))
    J = list(range(1, n_customers + 1))
    N = [0] + J

    random.seed(42) # Keep deterministic for testing
    
    coords = {0: (50, 50)}
    p = {} # Demands
    v = {} # Volumes
    T = {} # Service times
    
    T[0] = 0
    
    for idx, row in enumerate(orders_data):
        j = idx + 1
        coords[j] = (random.randint(0, 40), random.randint(0, 40))
        
        # Parse demand from CSV if exists, else assign toy
        try:
            p[j] = float(row.get('demand', random.randint(50, 150)))
        except ValueError:
            p[j] = random.randint(50, 150)
            
        v[j] = round(random.uniform(0.4, 1.5), 2)
        T[j] = random.randint(8, 20)
        
    P = params.weight_per_truck or 2500
    V = params.space_per_truck or 10.0
    c_fixed = 120
    g = 1.3
    o = params.km_per_liter or 8.0
    max_route_time = params.model_runtime or 300.0 # Time limit in minutes
    
    d = {}
    t = {}
    u_speed = 50.0

    for i in N:
        for j in N:
            if i == j:
                d[i, j] = 0.0
                t[i, j] = 0.0
            else:
                xi, yi = coords[i]
                xj, yj = coords[j]
                dist = math.hypot(xi - xj, yi - yj) * 0.6
                d[i, j] = round(dist, 2)
                t[i, j] = round(60.0 * d[i, j] / u_speed, 2)
                
    return K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t, max_route_time


def run_optimization(params: OptimizerParams, csv_text: str) -> str:
    """
    Parses the input CSV, creates the Distance/Time matrices, runs the Heuristic model,
    and returns the schedule formatted as a CSV block for the frontend.
    """
    if not csv_text:
        # Fallback to toy data
        n_customers = 60
        K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t, max_route_time = generate_toy_data(
            n_customers=n_customers,
            n_trucks=params.num_trucks
        )
        orders_data = [{"address": f"Toy Customer {j}"} for j in J]
    else:
        # 1. Parse the CSV
        reader = csv.DictReader(io.StringIO(csv_text))
        orders_data = list(reader)
        
        # Check if empty
        if not orders_data:
            raise ValueError("The orders CSV is empty.")
            
        # 2. Extract Data using Heuristica formatting logic
        K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t, max_route_time = generate_matrices(orders_data, params)
    
    # 3. Get Initial Solution
    initial_routes = clarke_wright_initial_solution(
        J=J, p=p, v=v, T=T, d=d, t=t,
        P=P, V=V, c_fixed=c_fixed, g=g, o=o,
        max_route_time=max_route_time,
        K_max=len(K)
    )
    
    # 4. Optimize via ALNS
    time_limit = params.model_runtime if params.model_runtime is not None else 10.0
    optimized_routes = alns(
        routes=initial_routes,
        p=p, v=v, T=T, d=d, t=t,
        P=P, V=V, c_fixed=c_fixed, g=g, o=o,
        max_route_time=max_route_time,
        K_max=len(K),
        time_limit_sec=time_limit
    )
    
    # 5. Build result CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Truck", "Point", "Hour"])
    
    # Time formatting helpers
    def format_time(minutes_after_midnight):
        # Starts at arbitrary 08:00 AM as per common dispatch logic, or Midnight.
        # Format as HH:MM
        h = int(8 + (minutes_after_midnight // 60))
        m = int(minutes_after_midnight % 60)
        return f"{h:02d}:{m:02d}"

    for truck_idx, route in enumerate(optimized_routes):
        starts = service_start_times(route.nodes, T, t)
        
        # Write depot start
        writer.writerow([f"Truck-{truck_idx+1}", params.depot_address, "08:00"])
        
        # Write customers
        for idx in range(1, len(route.nodes)-1):
            customer_node = route.nodes[idx]
            # Map node index back to CSV data address
            address = orders_data[customer_node - 1].get('address', f'Customer {customer_node}') 
            start_min = starts[customer_node]
            writer.writerow([f"Truck-{truck_idx+1}", address, format_time(start_min)])
            
        # Write depot end
        total_trip_mins = route.time
        writer.writerow([f"Truck-{truck_idx+1}", params.depot_address, format_time(total_trip_mins)])

    return output.getvalue()
