"""Example tests intentionally discoverable by FastAPI Studio."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_ROOT))

from app import app  # noqa: E402


HEADERS = {
    "x-api-key": "demo-secret",
    "x-tenant-id": "contoso",
}


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_create_ingestion():
    client = TestClient(app)
    response = client.post(
        "/v1/ingestions",
        headers=HEADERS,
        json={
            "source": "crm",
            "record_count": 10,
            "schema_version": 1,
        },
    )
    assert response.status_code == 202
    assert response.json()["tenant_id"] == "contoso"


def test_create_ingestion_rejects_bad_record_count():
    client = TestClient(app)
    response = client.post(
        "/v1/ingestions",
        headers=HEADERS,
        json={
            "source": "crm",
            "record_count": 0,
            "schema_version": 1,
        },
    )
    assert response.status_code == 422


def test_get_job_template():
    client = TestClient(app)
    job_id = "job-42"
    response = client.get(
        f"/v1/jobs/{job_id}",
        headers=HEADERS,
    )
    assert response.status_code == 200


def test_retry_job_template():
    client = TestClient(app)
    job_id = "job-42"
    response = client.post(
        f"/v1/jobs/{job_id}/retry",
        headers=HEADERS,
    )
    assert response.status_code == 200


def test_api_key_is_required():
    client = TestClient(app)
    response = client.get(
        "/v1/jobs/job-42",
        headers={"x-tenant-id": "contoso"},
    )
    assert response.status_code == 422 or response.status_code == 401
