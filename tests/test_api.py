"""
tests/test_api.py
API integration tests using FastAPI's TestClient.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def future_deadline(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# ── Health ─────────────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "jobs_in_queue" in body


# ── Jobs CRUD ──────────────────────────────────────────────────────────────────

def test_submit_job(client):
    payload = {
        "job_id": "test-job-001",
        "name": "Test training run",
        "gpu_hours": 5.0,
        "deadline": future_deadline(24),
        "priority": "standard",
        "regions": [],
    }
    resp = client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["job_id"] == "test-job-001"
    assert body["status"] in ("scheduled", "deferred")


def test_list_jobs(client):
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_job(client):
    resp = client.get("/api/v1/jobs/test-job-001")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "test-job-001"


def test_get_nonexistent_job(client):
    resp = client.get("/api/v1/jobs/does-not-exist")
    assert resp.status_code == 404


def test_cancel_job(client):
    # Submit a fresh job to cancel
    payload = {
        "job_id": "cancel-me-001",
        "gpu_hours": 2.0,
        "deadline": future_deadline(12),
        "priority": "low",
    }
    client.post("/api/v1/jobs", json=payload)
    resp = client.delete("/api/v1/jobs/cancel-me-001")
    assert resp.status_code == 204
    # Confirm it's gone
    assert client.get("/api/v1/jobs/cancel-me-001").status_code == 404


def test_cancel_nonexistent_job(client):
    resp = client.delete("/api/v1/jobs/ghost-job")
    assert resp.status_code == 404


# ── Schedule endpoint ──────────────────────────────────────────────────────────

def test_get_schedule(client):
    resp = client.get("/api/v1/schedule/test-job-001")
    assert resp.status_code == 200
    body = resp.json()
    assert "feasible" in body
    assert "explanation" in body
    assert "carbon_saved_pct" in body


def test_get_schedule_not_found(client):
    resp = client.get("/api/v1/schedule/ghost")
    assert resp.status_code == 404


# ── Environment endpoint ───────────────────────────────────────────────────────

def test_get_environment_valid(client):
    resp = client.get("/api/v1/environment/us-west-2")
    assert resp.status_code == 200
    body = resp.json()
    assert "carbon_intensity_gco2_kwh" in body
    assert "renewable_fraction" in body
    assert "water_stress" in body
    assert "drought_alert" in body


def test_get_environment_invalid_region(client):
    resp = client.get("/api/v1/environment/mars-dc-1")
    assert resp.status_code == 404


# ── Input validation ───────────────────────────────────────────────────────────

def test_submit_job_negative_gpu_hours(client):
    payload = {"gpu_hours": -5.0, "deadline": future_deadline(24)}
    resp = client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 422


def test_submit_job_past_deadline(client):
    payload = {
        "gpu_hours": 2.0,
        "deadline": "2020-01-01T00:00:00Z",
    }
    resp = client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 422
