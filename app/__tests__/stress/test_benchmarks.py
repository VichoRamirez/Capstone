"""
pytest-benchmark stress tests for CPU/network-bound services.

Run:
    cd app/
    pytest __tests__/stress/test_benchmarks.py -v --benchmark-sort=mean

Markers:
    stress   — always tagged
    osrm     — skipped if OSRM not running at 127.0.0.1:5010
"""
import pytest
from unittest.mock import patch

from backend.services.road_routing import (
    build_real_distance_time_speed_matrices,
    _fetch_osrm_table_single_base,
)
from backend.services.cleaning_service import (
    estandarizar_rut,
    clean_ventas,
    clean_detalle,
)
from backend.services import job_store

from conftest import requires_osrm

# ── coordinate sets of different sizes ─────────────────────────────���─────────

def _santiago_coords(n: int) -> dict:
    """
    Return n (lon, lat) coordinates spread across Greater Santiago.
    The depot is always node 0 at Plaza de Armas.
    """
    base = [
        (-70.6693, -33.4489),  # Plaza de Armas (depot)
        (-70.6020, -33.4372),  # Las Condes
        (-70.7360, -33.5200),  # Maipú
        (-70.6520, -33.3800),  # Providencia
        (-70.7008, -33.4561),  # Estación Central
        (-70.6450, -33.4950),  # San Miguel
        (-70.5770, -33.4060),  # La Florida
        (-70.6890, -33.3700),  # Conchalí
        (-70.6270, -33.5130),  # La Pintana
        (-70.6600, -33.5500),  # El Bosque
    ]
    coords = {}
    for i in range(n):
        lon, lat = base[i % len(base)]
        # Add tiny jitter so duplicate coords don't collapse
        coords[i] = (lon + i * 0.0001, lat + i * 0.0001)
    return coords


def _mock_osrm_patches(coords: dict):
    n = len(coords)
    fake_dists = [[float(abs(i - j) * 500) for j in range(n)] for i in range(n)]
    fake_durs  = [[float(abs(i - j) * 30)  for j in range(n)] for i in range(n)]
    return (
        patch("backend.services.road_routing._fetch_osrm_table_single_base",
              return_value=(fake_dists, fake_durs)),
        patch("backend.services.road_routing._select_working_osrm_base",
              return_value="http://127.0.0.1:5010"),
        patch("backend.services.road_routing._maybe_autostart_local_osrm",
              return_value=("", "")),
    )


# ── haversine fallback benchmarks (always run, no network) ───────────────────

@pytest.mark.stress
@pytest.mark.parametrize("n_nodes", [5, 10, 25, 50])
def test_haversine_matrix_scaling(benchmark, n_nodes):
    """Matrix build time should scale roughly as O(n²)."""
    coords = _santiago_coords(n_nodes)

    def _run():
        return build_real_distance_time_speed_matrices(coords, use_osrm=False)

    d, t, s, meta = benchmark(_run)
    assert len(d) == n_nodes * n_nodes
    assert meta["osrm_used"] is False


@pytest.mark.stress
@pytest.mark.parametrize("n_nodes", [5, 10, 25, 50])
def test_mocked_osrm_matrix_scaling(benchmark, n_nodes):
    """Overhead of building the arc dicts (not network) for mocked OSRM."""
    coords = _santiago_coords(n_nodes)
    p1, p2, p3 = _mock_osrm_patches(coords)

    def _run():
        with p1, p2, p3:
            return build_real_distance_time_speed_matrices(
                coords, use_osrm=True, osrm_base_url="http://127.0.0.1:5010"
            )

    d, t, s, meta = benchmark(_run)
    assert len(d) == n_nodes * n_nodes
    assert meta["osrm_used"] is True


# ── cleaning service benchmarks ─────────────────────────────────────────────��─

def _ventas_csv(n_rows: int) -> str:
    header = (
        "RUT,Nombre cliente,Numero de Orden,Direccion cliente,Comuna,"
        "Fecha de Pedido,Estado,Monto Pedido,Fecha de despacho Solicitada\n"
    )
    rows = "\n".join(
        f"14.512.240-{i % 10},"
        f"Cliente {i},"
        f"ORD-{i:04d},"
        f"Av. Providencia {i * 10},"
        f"Providencia,"
        f"2024-01-15,"
        f"Pendiente,"
        f"{50000 + i},"
        f"2024-01-20"
        for i in range(n_rows)
    )
    return header + rows


def _detalle_csv(n_rows: int) -> str:
    header = "Numero de Orden,SKU,Cantidad\n"
    rows = "\n".join(
        f"ORD-{i:04d},SKU{i % 10:03d},{(i % 5) + 1}"
        for i in range(n_rows)
    )
    return header + rows


@pytest.mark.stress
@pytest.mark.parametrize("n_rows", [10, 50, 200, 500])
def test_clean_ventas_scaling(benchmark, n_rows):
    """clean_ventas should handle hundreds of rows without significant slowdown."""
    csv_text = _ventas_csv(n_rows)
    df, errors = benchmark(clean_ventas, csv_text)
    assert len(df) == n_rows


@pytest.mark.stress
@pytest.mark.parametrize("n_rows", [10, 50, 200, 500])
def test_clean_detalle_scaling(benchmark, n_rows):
    csv_text = _detalle_csv(n_rows)
    df, errors = benchmark(clean_detalle, csv_text)
    # After deduplication, rows ≤ n_rows
    assert len(df) <= n_rows


@pytest.mark.stress
@pytest.mark.parametrize("n", [100, 500, 2000, 10_000])
def test_estandarizar_rut_throughput(benchmark, n):
    """RUT normalization of a batch — should be essentially free."""
    ruts = [f"{14_000_000 + i}-{i % 10}" for i in range(n)]

    def _run():
        return [estandarizar_rut(r) for r in ruts]

    results = benchmark(_run)
    assert len(results) == n


# ── job store benchmarks ─────────────────────────────���────────────────────────

@pytest.mark.stress
def test_job_store_create_throughput(benchmark):
    """In-memory job creation must be fast enough for concurrent optimization requests."""
    import uuid as _uuid

    def _create_1000():
        for _ in range(1000):
            job_store.create_job(_uuid.uuid4().hex)

    benchmark(_create_1000)
    job_store._jobs.clear()


# ── Real OSRM benchmarks (skipped if server down) ────────────────────────────

@requires_osrm
@pytest.mark.stress
@pytest.mark.parametrize("n_nodes", [4, 8, 16, 32])
def test_real_osrm_table_latency(benchmark, n_nodes):
    """
    Benchmark actual OSRM /table API latency for increasing node counts.
    Expected: < 200 ms for ≤ 32 nodes on a local server.
    """
    coords = _santiago_coords(n_nodes)
    nodes = list(coords.keys())

    def _run():
        return _fetch_osrm_table_single_base(
            coords=coords, nodes=nodes,
            base="http://127.0.0.1:5010",
            timeout_sec=15.0,
        )

    dists, durs = benchmark(_run)
    assert len(dists) == n_nodes
    assert len(durs) == n_nodes


@requires_osrm
@pytest.mark.stress
@pytest.mark.parametrize("n_nodes", [4, 8, 16])
def test_real_osrm_full_matrix_build(benchmark, n_nodes):
    """End-to-end matrix build including OSRM call + arc dict construction."""
    coords = _santiago_coords(n_nodes)

    d, t, s, meta = benchmark(
        build_real_distance_time_speed_matrices,
        coords,
        use_osrm=True,
        osrm_base_url="http://127.0.0.1:5010",
        timeout_sec=15.0,
    )
    assert meta["osrm_used"] is True
    assert len(d) == n_nodes * n_nodes
