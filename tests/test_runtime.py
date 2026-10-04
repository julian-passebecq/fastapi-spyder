import pytest

from spyder_fastapi.core import clear_runtime_evidence, record_route_execution
from spyder_fastapi.models import RequestExecution, RuntimeEvidence


def test_runtime_evidence_aggregates_client_elapsed_time():
    evidence = RuntimeEvidence()

    first = record_route_execution(
        evidence,
        "GET /items",
        RequestExecution(
            url="http://127.0.0.1:8000/items",
            status_code=200,
            elapsed_ms=10.0,
        ),
    )
    second = record_route_execution(
        evidence,
        "GET /items",
        RequestExecution(
            url="http://127.0.0.1:8000/items",
            status_code=200,
            elapsed_ms=30.0,
        ),
    )

    assert first is second
    assert second.request_count == 2
    assert second.timed_count == 2
    assert second.last_elapsed_ms == 30.0
    assert second.average_elapsed_ms == pytest.approx(20.0)
    assert second.min_elapsed_ms == 10.0
    assert second.max_elapsed_ms == 30.0
    assert second.last_status_code == 200
    assert second.transport_error_count == 0


def test_runtime_evidence_tracks_transport_errors_without_fake_timing():
    evidence = RuntimeEvidence()

    stats = record_route_execution(
        evidence,
        "POST /items",
        RequestExecution(
            url="http://127.0.0.1:8000/items",
            error="connection refused",
        ),
    )

    assert stats.request_count == 1
    assert stats.timed_count == 0
    assert stats.last_elapsed_ms is None
    assert stats.average_elapsed_ms is None
    assert stats.last_status_code is None
    assert stats.transport_error_count == 1


def test_runtime_evidence_is_route_scoped_and_clearable():
    evidence = RuntimeEvidence()

    record_route_execution(
        evidence,
        "GET /one",
        RequestExecution(url="http://test/one", status_code=200, elapsed_ms=5),
    )
    record_route_execution(
        evidence,
        "GET /two",
        RequestExecution(url="http://test/two", status_code=404, elapsed_ms=8),
    )

    assert set(evidence.routes) == {"GET /one", "GET /two"}

    clear_runtime_evidence(evidence)

    assert evidence.routes == {}
