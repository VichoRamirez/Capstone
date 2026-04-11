"""Unit tests for backend.services.job_store.

Pure in-memory module — no DB or network required.
"""
import pytest

from backend.services import job_store


@pytest.fixture(autouse=True)
def _clear_jobs():
    """Reset the in-memory store before and after every test."""
    job_store._jobs.clear()
    yield
    job_store._jobs.clear()


# ── create_job ────────────────────────────────────────────────────────────────

class TestCreateJob:
    def test_returns_job_entry(self):
        job = job_store.create_job("j1")
        assert job is not None

    def test_initial_status_is_pending(self):
        job = job_store.create_job("j1")
        assert job.status == "pending"

    def test_initial_stage_is_set(self):
        job = job_store.create_job("j1")
        assert isinstance(job.stage, str) and job.stage != ""

    def test_initial_result_is_none(self):
        job = job_store.create_job("j1")
        assert job.result is None

    def test_initial_error_is_none(self):
        job = job_store.create_job("j1")
        assert job.error is None

    def test_stored_in_internal_dict(self):
        job_store.create_job("j1")
        assert "j1" in job_store._jobs

    def test_multiple_jobs_stored_independently(self):
        job_store.create_job("j1")
        job_store.create_job("j2")
        assert job_store._jobs["j1"] is not job_store._jobs["j2"]


# ── get_job ───────────────────────────────────────────────────────────────────

class TestGetJob:
    def test_returns_created_job(self):
        created = job_store.create_job("j2")
        assert job_store.get_job("j2") is created

    def test_returns_none_for_unknown_id(self):
        assert job_store.get_job("nonexistent") is None


# ── update_job_stage ──────────────────────────────────────────────────────────

class TestUpdateJobStage:
    def test_updates_stage_field(self):
        job_store.create_job("j3")
        job_store.update_job_stage("j3", "Geocodificando...")
        assert job_store.get_job("j3").stage == "Geocodificando..."

    def test_noop_on_unknown_job_no_exception(self):
        job_store.update_job_stage("nonexistent", "anything")  # must not raise


# ── mark_job_running ──────────────────────────────────────────────────────────

class TestMarkJobRunning:
    def test_sets_status_running(self):
        job_store.create_job("j4")
        job_store.mark_job_running("j4")
        assert job_store.get_job("j4").status == "running"

    def test_noop_on_unknown_job_no_exception(self):
        job_store.mark_job_running("nonexistent")


# ── mark_job_done ─────────────────────────────────────────────────────────────

class TestMarkJobDone:
    def test_sets_status_done(self):
        job_store.create_job("j5")
        job_store.mark_job_done("j5", {"output": 42})
        assert job_store.get_job("j5").status == "done"

    def test_stores_result(self):
        job_store.create_job("j5")
        job_store.mark_job_done("j5", {"output": 42})
        assert job_store.get_job("j5").result == {"output": 42}

    def test_stage_set_to_completado(self):
        job_store.create_job("j5")
        job_store.mark_job_done("j5", {})
        assert job_store.get_job("j5").stage == "Completado"

    def test_noop_on_unknown_job_no_exception(self):
        job_store.mark_job_done("nonexistent", {})


# ── mark_job_error ────────────────────────────────────────────────────────────

class TestMarkJobError:
    def test_sets_status_error(self):
        job_store.create_job("j6")
        job_store.mark_job_error("j6", "Something blew up")
        assert job_store.get_job("j6").status == "error"

    def test_stores_error_message(self):
        job_store.create_job("j6")
        job_store.mark_job_error("j6", "Something blew up")
        assert job_store.get_job("j6").error == "Something blew up"

    def test_stage_set_to_error(self):
        job_store.create_job("j6")
        job_store.mark_job_error("j6", "boom")
        assert job_store.get_job("j6").stage == "Error"

    def test_noop_on_unknown_job_no_exception(self):
        job_store.mark_job_error("nonexistent", "msg")


# ── lifecycle integration ─────────────────────────────────────────────────────

class TestJobLifecycle:
    def test_full_happy_path(self):
        job_store.create_job("lifecycle")
        assert job_store.get_job("lifecycle").status == "pending"

        job_store.mark_job_running("lifecycle")
        assert job_store.get_job("lifecycle").status == "running"

        job_store.update_job_stage("lifecycle", "Optimizando rutas...")
        assert job_store.get_job("lifecycle").stage == "Optimizando rutas..."

        job_store.mark_job_done("lifecycle", {"routes": []})
        assert job_store.get_job("lifecycle").status == "done"
        assert job_store.get_job("lifecycle").result == {"routes": []}

    def test_full_error_path(self):
        job_store.create_job("err-job")
        job_store.mark_job_running("err-job")
        job_store.mark_job_error("err-job", "Timeout")
        job = job_store.get_job("err-job")
        assert job.status == "error"
        assert job.error == "Timeout"
