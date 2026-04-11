"""Unit tests for backend.services.road_routing.

Mock-OSRM tests run always.
Real-OSRM tests are skipped if 127.0.0.1:5010 is not reachable
(the `requires_osrm` marker is defined in conftest.py).
"""
import pytest
from unittest.mock import patch, MagicMock

from backend.services.road_routing import (
    _fetch_osrm_table_single_base,
    _normalize_osrm_base,
    _is_localhost_base,
    build_real_distance_time_speed_matrices,
)

# Bring in the OSRM skip marker from conftest
from conftest import requires_osrm  # noqa: E402

# ── Santiago sample coordinates (lon, lat) ───────────────────────────────────
_COORDS = {
    0: (-70.6693, -33.4489),  # depot — Plaza de Armas
    1: (-70.6020, -33.4372),  # Las Condes
    2: (-70.7360, -33.5200),  # Maipú
    3: (-70.6520, -33.3800),  # Providencia area
}
_N = len(_COORDS)
_NODES = list(_COORDS.keys())


def _osrm_ok_payload(n: int = _N):
    """Minimal valid OSRM /table response for n nodes."""
    dists = [[float(abs(i - j) * 1000) for j in range(n)] for i in range(n)]
    durs  = [[float(abs(i - j) * 60)   for j in range(n)] for i in range(n)]
    return {"code": "Ok", "distances": dists, "durations": durs}


# ── _normalize_osrm_base ──────────────────────────────────────────────────────

class TestNormalizeOsrmBase:
    def test_strips_trailing_slash(self):
        assert _normalize_osrm_base("http://127.0.0.1:5010/") == "http://127.0.0.1:5010"

    def test_adds_http_if_missing(self):
        assert _normalize_osrm_base("127.0.0.1:5010") == "http://127.0.0.1:5010"

    def test_empty_string_stays_empty(self):
        assert _normalize_osrm_base("") == ""

    def test_https_preserved(self):
        url = "https://router.project-osrm.org"
        assert _normalize_osrm_base(url) == url


# ── _is_localhost_base ────────────────────────────────────────────────────────

class TestIsLocalhostBase:
    def test_127_is_local(self):
        assert _is_localhost_base("http://127.0.0.1:5010") is True

    def test_localhost_hostname(self):
        assert _is_localhost_base("http://localhost:5010") is True

    def test_public_url_is_not_local(self):
        assert _is_localhost_base("http://router.project-osrm.org") is False

    def test_empty_is_not_local(self):
        assert _is_localhost_base("") is False


# ── _fetch_osrm_table_single_base (mocked) ────────────────────────────────────

class TestFetchOsrmTableSingleBaseMocked:
    def test_parses_valid_response(self):
        with patch("backend.services.road_routing._fetch_json", return_value=_osrm_ok_payload()):
            dists, durs = _fetch_osrm_table_single_base(
                coords=_COORDS, nodes=_NODES, base="http://127.0.0.1:5010", timeout_sec=5.0
            )
        assert len(dists) == _N
        assert len(durs)  == _N

    def test_diagonal_is_zero(self):
        with patch("backend.services.road_routing._fetch_json", return_value=_osrm_ok_payload()):
            dists, _ = _fetch_osrm_table_single_base(
                coords=_COORDS, nodes=_NODES, base="http://127.0.0.1:5010", timeout_sec=5.0
            )
        for i in range(_N):
            assert dists[i][i] == pytest.approx(0.0)

    def test_raises_on_non_ok_code(self):
        with patch("backend.services.road_routing._fetch_json",
                   return_value={"code": "InvalidQuery", "message": "bad"}):
            with pytest.raises(RuntimeError, match="OSRM table error"):
                _fetch_osrm_table_single_base(
                    coords=_COORDS, nodes=_NODES, base="http://127.0.0.1:5010", timeout_sec=5.0
                )

    def test_raises_when_matrices_missing(self):
        with patch("backend.services.road_routing._fetch_json", return_value={"code": "Ok"}):
            with pytest.raises(RuntimeError):
                _fetch_osrm_table_single_base(
                    coords=_COORDS, nodes=_NODES, base="http://127.0.0.1:5010", timeout_sec=5.0
                )

    def test_url_contains_table_endpoint(self):
        captured = {}

        def _capture(url, **_kw):
            captured["url"] = url
            return _osrm_ok_payload()

        with patch("backend.services.road_routing._fetch_json", side_effect=_capture):
            _fetch_osrm_table_single_base(
                coords=_COORDS, nodes=_NODES, base="http://127.0.0.1:5010", timeout_sec=5.0
            )
        assert "/table/v1/driving/" in captured["url"]


# ── build_real_distance_time_speed_matrices (mocked OSRM) ────────────────────

class TestBuildMatricesMocked:
    def _mock_osrm(self):
        """Patch the three internal helpers called during matrix building."""
        n = _N
        fake_dists = [[float(abs(i - j) * 1000) for j in range(n)] for i in range(n)]
        fake_durs  = [[float(abs(i - j) * 60)   for j in range(n)] for i in range(n)]
        return (
            patch("backend.services.road_routing._fetch_osrm_table_single_base",
                  return_value=(fake_dists, fake_durs)),
            patch("backend.services.road_routing._select_working_osrm_base",
                  return_value="http://127.0.0.1:5010"),
            patch("backend.services.road_routing._maybe_autostart_local_osrm",
                  return_value=("", "")),
        )

    def test_returns_four_dicts(self):
        p1, p2, p3 = self._mock_osrm()
        with p1, p2, p3:
            d, t, s, meta = build_real_distance_time_speed_matrices(
                _COORDS, use_osrm=True, osrm_base_url="http://127.0.0.1:5010"
            )
        assert isinstance(d, dict)
        assert isinstance(t, dict)
        assert isinstance(s, dict)
        assert isinstance(meta, dict)

    def test_osrm_used_true_in_meta(self):
        p1, p2, p3 = self._mock_osrm()
        with p1, p2, p3:
            _, _, _, meta = build_real_distance_time_speed_matrices(
                _COORDS, use_osrm=True, osrm_base_url="http://127.0.0.1:5010"
            )
        assert meta.get("osrm_used") is True

    def test_arc_count_matches_n_squared(self):
        p1, p2, p3 = self._mock_osrm()
        with p1, p2, p3:
            d, _, _, _ = build_real_distance_time_speed_matrices(
                _COORDS, use_osrm=True, osrm_base_url="http://127.0.0.1:5010"
            )
        # n*n arcs (including self-loops)
        assert len(d) == _N * _N

    def test_self_loops_have_zero_distance(self):
        p1, p2, p3 = self._mock_osrm()
        with p1, p2, p3:
            d, _, _, _ = build_real_distance_time_speed_matrices(
                _COORDS, use_osrm=True, osrm_base_url="http://127.0.0.1:5010"
            )
        for node in _COORDS:
            assert d.get((node, node), 0.0) == pytest.approx(0.0, abs=1e-6)

    def test_distances_in_km(self):
        """OSRM returns meters; the function must convert to km."""
        p1, p2, p3 = self._mock_osrm()
        with p1, p2, p3:
            d, _, _, _ = build_real_distance_time_speed_matrices(
                _COORDS, use_osrm=True, osrm_base_url="http://127.0.0.1:5010"
            )
        # The mock uses 1000 m between nodes → expect ~1 km
        non_diag = {k: v for k, v in d.items() if k[0] != k[1]}
        for v in non_diag.values():
            assert v < 500, f"Distance {v} km seems too large (not converted from m?)"

    def test_fallback_when_use_osrm_false(self):
        _, _, _, meta = build_real_distance_time_speed_matrices(
            _COORDS, use_osrm=False
        )
        assert meta.get("osrm_used") is False

    def test_fallback_produces_valid_dicts(self):
        d, t, s, _ = build_real_distance_time_speed_matrices(
            _COORDS, use_osrm=False
        )
        assert len(d) == _N * _N
        for v in d.values():
            assert v >= 0.0


# ── Real OSRM tests (skipped when server is down) ────────────────────────────

@requires_osrm
class TestRealOsrm:
    _BASE = "http://127.0.0.1:5010"

    def test_table_returns_correct_shape(self):
        dists, durs = _fetch_osrm_table_single_base(
            coords=_COORDS, nodes=_NODES, base=self._BASE, timeout_sec=10.0
        )
        assert len(dists) == _N
        assert len(durs) == _N

    def test_table_values_non_negative(self):
        dists, durs = _fetch_osrm_table_single_base(
            coords=_COORDS, nodes=_NODES, base=self._BASE, timeout_sec=10.0
        )
        for row in dists:
            for val in row:
                if val is not None:
                    assert val >= 0.0
        for row in durs:
            for val in row:
                if val is not None:
                    assert val >= 0.0

    def test_diagonal_is_zero_real(self):
        dists, _ = _fetch_osrm_table_single_base(
            coords=_COORDS, nodes=_NODES, base=self._BASE, timeout_sec=10.0
        )
        for i in range(_N):
            assert dists[i][i] == pytest.approx(0.0, abs=1.0)

    def test_build_matrices_with_real_osrm(self):
        d, t, s, meta = build_real_distance_time_speed_matrices(
            _COORDS,
            use_osrm=True,
            osrm_base_url=self._BASE,
            timeout_sec=10.0,
        )
        assert meta.get("osrm_used") is True
        assert len(d) == _N * _N

    def test_real_distances_in_reasonable_range(self):
        """Santiago inter-node distances should be between 0.1 and 100 km."""
        d, _, _, _ = build_real_distance_time_speed_matrices(
            _COORDS,
            use_osrm=True,
            osrm_base_url=self._BASE,
            timeout_sec=10.0,
        )
        non_diag = {k: v for k, v in d.items() if k[0] != k[1]}
        for (i, j), dist in non_diag.items():
            assert 0.1 <= dist <= 100.0, f"d[{i},{j}]={dist} km outside expected range"

    def test_real_durations_in_reasonable_range(self):
        """Santiago travel times should be between 1 and 180 minutes."""
        _, t, _, _ = build_real_distance_time_speed_matrices(
            _COORDS,
            use_osrm=True,
            osrm_base_url=self._BASE,
            timeout_sec=10.0,
        )
        non_diag = {k: v for k, v in t.items() if k[0] != k[1]}
        for (i, j), dur in non_diag.items():
            assert 1.0 <= dur <= 180.0, f"t[{i},{j}]={dur} min outside expected range"
