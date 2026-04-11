"""Integration tests for miscellaneous endpoints.

Covers: /health, /progress, /fuel/*, /validate-depot, /jobs/*
External HTTP calls (CNE API, Nominatim) are mocked.
"""
import pytest
from unittest.mock import patch


@pytest.mark.integration
class TestHealthEndpoint:
    def test_returns_200(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200

    def test_body_has_status_ok(self, api_client):
        body = api_client.get("/health").json()
        assert body.get("status") == "ok"


@pytest.mark.integration
class TestProgressEndpoint:
    def test_returns_200(self, api_client):
        resp = api_client.get("/progress")
        assert resp.status_code == 200

    def test_body_has_stage_key(self, api_client):
        body = api_client.get("/progress").json()
        assert "stage" in body


@pytest.mark.integration
class TestFuelEndpoints:
    _MOCK_PRICE = 1050.0
    _MOCK_META = {
        "source": "mock",
        "sample_size": 1,
        "price_range": [_MOCK_PRICE, _MOCK_PRICE],
        "from_cache": False,
    }

    def _patch_fuel(self, fuel_type="diesel"):
        return patch(
            "backend.api.router.fetch_fuel_price_clp",
            return_value=(self._MOCK_PRICE, self._MOCK_META),
        )

    def test_diesel_clp_returns_200(self, api_client):
        with self._patch_fuel():
            resp = api_client.get("/fuel/diesel-clp")
        assert resp.status_code == 200

    def test_diesel_clp_has_price_field(self, api_client):
        with self._patch_fuel():
            body = api_client.get("/fuel/diesel-clp").json()
        assert "diesel_price_clp" in body
        assert body["diesel_price_clp"] == self._MOCK_PRICE

    def test_prices_clp_diesel(self, api_client):
        with self._patch_fuel("diesel"):
            resp = api_client.get("/fuel/prices-clp?fuel_type=diesel")
        assert resp.status_code == 200
        assert resp.json()["fuel_price_clp"] == self._MOCK_PRICE

    def test_prices_clp_gasoline_95(self, api_client):
        with self._patch_fuel("gasoline_95"):
            resp = api_client.get("/fuel/prices-clp?fuel_type=gasoline_95")
        assert resp.status_code == 200

    def test_prices_clp_has_fuel_type_field(self, api_client):
        with self._patch_fuel():
            body = api_client.get("/fuel/prices-clp?fuel_type=diesel").json()
        assert "fuel_type" in body

    def test_prices_clp_has_supported_types(self, api_client):
        with self._patch_fuel():
            body = api_client.get("/fuel/prices-clp?fuel_type=diesel").json()
        assert "fuel_types_supported" in body
        assert isinstance(body["fuel_types_supported"], list)


@pytest.mark.integration
class TestValidateDepotEndpoint:
    _NOMINATIM_OK = [{"lat": "-33.4489", "lon": "-70.6693", "display_name": "Santiago"}]
    _NOMINATIM_EMPTY = []

    def _patch_nominatim(self, results):
        return patch(
            "backend.api.router._nominatim_search",
            return_value=results,
        )

    def test_valid_address_returns_200(self, api_client):
        with self._patch_nominatim(self._NOMINATIM_OK):
            resp = api_client.get("/validate-depot?address=Av+Libertador+Bernardo+O'Higgins+1234")
        assert resp.status_code == 200

    def test_valid_address_is_valid_true(self, api_client):
        with self._patch_nominatim(self._NOMINATIM_OK):
            body = api_client.get(
                "/validate-depot?address=Av+Libertador+Bernardo+O'Higgins+1234"
            ).json()
        assert body.get("is_valid") is True

    def test_valid_address_has_lat_lon(self, api_client):
        with self._patch_nominatim(self._NOMINATIM_OK):
            body = api_client.get("/validate-depot?address=Plaza+de+Armas").json()
        assert "lat" in body and "lon" in body

    def test_missing_address_param_returns_422(self, api_client):
        resp = api_client.get("/validate-depot")
        assert resp.status_code == 422

    def test_unresolvable_address_is_valid_false(self, api_client):
        with self._patch_nominatim(self._NOMINATIM_EMPTY):
            body = api_client.get("/validate-depot?address=xyzzy_no_such_place").json()
        assert body.get("is_valid") is False


@pytest.mark.integration
class TestJobsEndpoints:
    def test_status_unknown_job_returns_404(self, api_client):
        resp = api_client.get("/jobs/nonexistent-job-id/status")
        assert resp.status_code == 404

    def test_result_unknown_job_returns_404(self, api_client):
        resp = api_client.get("/jobs/nonexistent-job-id/result")
        assert resp.status_code == 404

    def test_status_known_pending_job(self, api_client):
        from backend.services.job_store import create_job
        create_job("test-job-123")
        resp = api_client.get("/jobs/test-job-123/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_result_not_ready_returns_400(self, api_client):
        from backend.services.job_store import create_job, mark_job_running
        create_job("running-job-xyz")
        mark_job_running("running-job-xyz")
        resp = api_client.get("/jobs/running-job-xyz/result")
        assert resp.status_code == 400

    def test_result_done_job_returns_200(self, api_client):
        from backend.services.job_store import create_job, mark_job_done
        create_job("done-job-abc")
        mark_job_done("done-job-abc", {"routes": [], "stats": {}})
        resp = api_client.get("/jobs/done-job-abc/result")
        assert resp.status_code == 200
