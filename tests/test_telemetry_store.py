from spyder_fastapi.core.telemetry import (
    NativeTelemetryStore,
    is_external_span,
    span_category,
    span_target,
)


def _server_span(
    *,
    trace_id: str,
    span_id: str,
    route: str,
    method: str = "GET",
    duration_ms: float = 10.0,
    status_code: int = 200,
    end_ns: int = 2_000_000,
    status: str = "UNSET",
):
    return {
        "signal": "span",
        "trace_id": trace_id,
        "span_id": span_id,
        "name": f"{method} {route}",
        "kind": "SERVER",
        "start_ns": end_ns - int(duration_ms * 1_000_000),
        "end_ns": end_ns,
        "duration_ms": duration_ms,
        "status": status,
        "attributes": {
            "http.route": route,
            "http.request.method": method,
            "http.response.status_code": status_code,
        },
        "scope_name": "fastapi",
    }


def test_native_telemetry_store_aggregates_route_latency_and_errors():
    store = NativeTelemetryStore()

    assert store.ingest(
        _server_span(
            trace_id="1" * 32,
            span_id="1" * 16,
            route="/items",
            duration_ms=10,
            end_ns=10_000_000,
        )
    )
    assert store.ingest(
        _server_span(
            trace_id="2" * 32,
            span_id="2" * 16,
            route="/items",
            duration_ms=30,
            status_code=500,
            status="ERROR",
            end_ns=40_000_000,
        )
    )

    summary = store.route_summaries()[0]
    assert summary.route_id == "GET /items"
    assert summary.request_count == 2
    assert summary.error_count == 1
    assert summary.error_rate == 0.5
    assert summary.average_ms == 20
    assert summary.p50_ms == 20
    assert summary.p95_ms == 29
    assert summary.min_ms == 10
    assert summary.max_ms == 30
    assert summary.last_status_code == 500

    assert store.total_requests() == 2
    assert store.error_count() == 1
    assert store.error_rate() == 0.5
    assert store.latency_percentile(0.50) == 20


def test_native_telemetry_store_keeps_operation_spans_in_trace():
    store = NativeTelemetryStore()
    trace_id = "a" * 32

    store.ingest(
        {
            "signal": "span",
            "trace_id": trace_id,
            "span_id": "2" * 16,
            "parent_span_id": "1" * 16,
            "name": "fastapi.dependencies",
            "kind": "INTERNAL",
            "start_ns": 2_000_000,
            "end_ns": 4_000_000,
            "duration_ms": 2.0,
            "attributes": {
                "code.function.name": "service.auth.current_user",
            },
            "scope_name": "fastapi",
        }
    )
    store.ingest(
        _server_span(
            trace_id=trace_id,
            span_id="1" * 16,
            route="/me",
            duration_ms=8,
            end_ns=9_000_000,
        )
    )

    spans = store.trace_spans(trace_id)
    assert [span.name for span in spans] == [
        "GET /me",
        "fastapi.dependencies",
    ]
    assert store.trace_root(trace_id).name == "GET /me"
    assert store.trace_ids() == [trace_id]


def test_native_telemetry_store_accepts_fastapi_logs_and_controls():
    store = NativeTelemetryStore()
    trace_id = "f" * 32

    assert store.ingest(
        {
            "signal": "log",
            "timestamp_ns": 100,
            "trace_id": trace_id,
            "span_id": "1" * 16,
            "severity": "WARN",
            "event_name": "fastapi.validation.failed",
            "body": "Request validation failed",
            "attributes": {
                "http.route": "/items",
                "fastapi.validation.error_count": 2,
            },
            "scope_name": "fastapi",
        }
    )
    assert store.ingest(
        {
            "signal": "control",
            "event": "capture_configured",
            "fastapi_version": "0.142.2",
            "tracing": True,
            "logs": True,
        }
    )

    assert store.logs_for_trace(trace_id)[0].event_name == "fastapi.validation.failed"
    assert store.validation_failure_count() == 1
    assert store.exception_count() == 0
    assert store.latest_control().fastapi_version == "0.142.2"


def test_native_telemetry_store_rejects_unknown_or_invalid_events():
    store = NativeTelemetryStore()

    assert store.ingest({"signal": "unknown"}) is False
    assert store.ingest({"signal": "span", "name": "missing fields"}) is False
    assert store.invalid_lines == 2


def test_native_telemetry_store_is_bounded():
    store = NativeTelemetryStore(max_spans=2)

    for index in range(3):
        store.ingest(
            _server_span(
                trace_id=f"{index + 1:032x}",
                span_id=f"{index + 1:016x}",
                route=f"/{index}",
                end_ns=(index + 1) * 10_000_000,
            )
        )

    assert len(store.spans) == 2
    assert [span.attributes["http.route"] for span in store.spans] == ["/1", "/2"]

def test_validation_failures_are_not_counted_as_server_errors():
    store = NativeTelemetryStore()
    trace_id = "b" * 32

    store.ingest(
        _server_span(
            trace_id=trace_id,
            span_id="3" * 16,
            route="/items",
            method="POST",
            duration_ms=7,
            status_code=422,
        )
    )
    store.ingest(
        {
            "signal": "log",
            "timestamp_ns": 100,
            "trace_id": trace_id,
            "span_id": "3" * 16,
            "severity": "WARN",
            "event_name": "fastapi.validation.failed",
            "body": "Request validation failed",
            "attributes": {
                "http.route": "/items",
                "fastapi.validation.error_count": 1,
            },
            "scope_name": "fastapi",
        }
    )

    summary = store.route_summaries()[0]
    assert summary.route_id == "POST /items"
    assert summary.error_count == 0
    assert summary.error_rate == 0
    assert summary.validation_failure_count == 1
    assert store.validation_failure_count() == 1

def test_external_span_classification_uses_otel_semantic_attributes():
    store = NativeTelemetryStore()
    trace_id = "c" * 32

    database = {
        "signal": "span",
        "trace_id": trace_id,
        "span_id": "4" * 16,
        "parent_span_id": "1" * 16,
        "name": "SELECT telemetry_demo.jobs",
        "kind": "CLIENT",
        "start_ns": 2_000_000,
        "end_ns": 6_000_000,
        "duration_ms": 4.0,
        "attributes": {
            "db.system.name": "postgresql",
            "db.namespace": "telemetry_demo",
            "db.operation.name": "SELECT",
        },
        "scope_name": "demo.database",
    }
    outbound = {
        "signal": "span",
        "trace_id": trace_id,
        "span_id": "5" * 16,
        "parent_span_id": "1" * 16,
        "name": "POST",
        "kind": "CLIENT",
        "start_ns": 6_000_000,
        "end_ns": 9_000_000,
        "duration_ms": 3.0,
        "attributes": {
            "http.request.method": "POST",
            "server.address": "events.internal",
        },
        "scope_name": "demo.http",
    }
    messaging = {
        "signal": "span",
        "trace_id": trace_id,
        "span_id": "6" * 16,
        "parent_span_id": "1" * 16,
        "name": "publish ingestion.accepted",
        "kind": "PRODUCER",
        "start_ns": 9_000_000,
        "end_ns": 10_000_000,
        "duration_ms": 1.0,
        "attributes": {
            "messaging.system": "kafka",
            "messaging.destination.name": "ingestion.accepted",
        },
        "scope_name": "demo.messaging",
    }

    for payload in (database, outbound, messaging):
        assert store.ingest(payload)

    db_span, http_span, message_span = store.spans
    assert span_category(db_span) == "database"
    assert span_target(db_span) == "postgresql:telemetry_demo"
    assert span_category(http_span) == "http-client"
    assert span_target(http_span) == "events.internal"
    assert span_category(message_span) == "messaging"
    assert span_target(message_span) == "ingestion.accepted"
    assert all(is_external_span(span) for span in store.spans)
    assert store.external_span_count() == 3
    assert store.external_span_categories() == {
        "database": 1,
        "http-client": 1,
        "messaging": 1,
    }

