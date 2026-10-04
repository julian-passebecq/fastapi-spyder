"""Headless aggregation for FastAPI's native OpenTelemetry events."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from spyder_fastapi.models import (
    NativeRouteTelemetry,
    NativeTelemetryControl,
    NativeTelemetryLog,
    NativeTelemetrySpan,
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None

    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]

    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _route_id(span: NativeTelemetrySpan) -> str | None:
    route = span.attributes.get("http.route")
    method = span.attributes.get("http.request.method")
    if not isinstance(route, str) or not route:
        return None
    if not isinstance(method, str) or not method:
        method = span.name.split(" ", 1)[0] if " " in span.name else "HTTP"
    return f"{method} {route}"


def span_route_id(span: NativeTelemetrySpan) -> str | None:
    """Return the FastAPI route identifier carried by an HTTP server span."""

    return _route_id(span)


def _status_code(span: NativeTelemetrySpan) -> int | None:
    value = span.attributes.get("http.response.status_code")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_server_span(span: NativeTelemetrySpan) -> bool:
    return span.kind.upper() == "SERVER" and _route_id(span) is not None


def span_category(span: NativeTelemetrySpan) -> str:
    """Classify an observed span from OpenTelemetry semantic attributes."""

    attributes = span.attributes
    kind = span.kind.upper()

    if kind == "SERVER" and (
        "http.route" in attributes
        or "http.request.method" in attributes
        or "http.method" in attributes
    ):
        return "http-server"

    if span.scope_name == "fastapi" or span.name.startswith("fastapi."):
        return "fastapi"

    if (
        "db.system.name" in attributes
        or "db.system" in attributes
        or "db.namespace" in attributes
    ):
        return "database"

    if (
        "messaging.system" in attributes
        or "messaging.operation.type" in attributes
        or "messaging.destination.name" in attributes
    ):
        return "messaging"

    if "rpc.system" in attributes:
        return "rpc"

    if kind == "CLIENT" and (
        "http.request.method" in attributes
        or "http.method" in attributes
        or "url.full" in attributes
        or "http.url" in attributes
    ):
        return "http-client"

    if kind in {"CLIENT", "PRODUCER"}:
        return "external"

    return "application"


def span_target(span: NativeTelemetrySpan) -> str | None:
    """Return a compact remote/DB target when semantic attributes expose one."""

    attributes = span.attributes
    category = span_category(span)

    if category == "database":
        system = (
            attributes.get("db.system.name")
            or attributes.get("db.system")
            or "database"
        )
        namespace = (
            attributes.get("db.namespace")
            or attributes.get("db.name")
        )
        return (
            f"{system}:{namespace}"
            if namespace
            else str(system)
        )

    if category == "http-client":
        target = (
            attributes.get("server.address")
            or attributes.get("net.peer.name")
            or attributes.get("url.full")
            or attributes.get("http.url")
        )
        return str(target) if target is not None else None

    if category == "messaging":
        target = (
            attributes.get("messaging.destination.name")
            or attributes.get("messaging.destination")
            or attributes.get("messaging.system")
        )
        return str(target) if target is not None else None

    if category == "rpc":
        system = attributes.get("rpc.system")
        service = attributes.get("rpc.service")
        if system and service:
            return f"{system}:{service}"
        return str(service or system) if (service or system) else None

    if category == "external":
        target = (
            attributes.get("server.address")
            or attributes.get("net.peer.name")
        )
        return str(target) if target is not None else None

    return None


def is_external_span(span: NativeTelemetrySpan) -> bool:
    return span_category(span) in {
        "database",
        "http-client",
        "messaging",
        "rpc",
        "external",
    }


class NativeTelemetryStore:
    """Bounded in-memory store for native FastAPI telemetry.

    This layer knows nothing about Qt. The Spyder UI can replace or re-render
    itself freely without changing how OpenTelemetry events are normalized.
    """

    def __init__(
        self,
        *,
        max_spans: int = 10_000,
        max_logs: int = 2_000,
        max_controls: int = 100,
    ) -> None:
        self.max_spans = max_spans
        self.max_logs = max_logs
        self.max_controls = max_controls
        self.spans: list[NativeTelemetrySpan] = []
        self.logs: list[NativeTelemetryLog] = []
        self.controls: list[NativeTelemetryControl] = []
        self.invalid_lines = 0

    def clear(self) -> None:
        self.spans.clear()
        self.logs.clear()
        self.controls.clear()
        self.invalid_lines = 0

    def ingest(self, payload: dict[str, Any]) -> bool:
        signal = payload.get("signal")
        try:
            if signal == "span":
                self.spans.append(NativeTelemetrySpan.model_validate(payload))
                if len(self.spans) > self.max_spans:
                    del self.spans[: len(self.spans) - self.max_spans]
                return True

            if signal == "log":
                self.logs.append(NativeTelemetryLog.model_validate(payload))
                if len(self.logs) > self.max_logs:
                    del self.logs[: len(self.logs) - self.max_logs]
                return True

            if signal == "control":
                self.controls.append(NativeTelemetryControl.model_validate(payload))
                if len(self.controls) > self.max_controls:
                    del self.controls[: len(self.controls) - self.max_controls]
                return True
        except ValidationError:
            self.invalid_lines += 1
            return False

        self.invalid_lines += 1
        return False

    def server_spans(self) -> list[NativeTelemetrySpan]:
        return [span for span in self.spans if _is_server_span(span)]

    def route_summaries(self) -> list[NativeRouteTelemetry]:
        grouped: dict[str, list[NativeTelemetrySpan]] = defaultdict(list)
        for span in self.server_spans():
            route_id = _route_id(span)
            if route_id is not None:
                grouped[route_id].append(span)

        validation_by_route: dict[str, int] = defaultdict(int)
        for log in self.logs:
            if log.event_name != "fastapi.validation.failed":
                continue
            route = log.attributes.get("http.route")
            if not isinstance(route, str) or not route:
                continue
            # Validation logs do not carry the HTTP method. Match them to the
            # route summaries by path without turning 422 into a server error.
            for route_id in grouped:
                if route_id.endswith(f" {route}"):
                    validation_by_route[route_id] += 1

        summaries: list[NativeRouteTelemetry] = []
        for route_id, spans in grouped.items():
            spans = sorted(spans, key=lambda span: span.end_ns)
            durations = [span.duration_ms for span in spans]
            errors = [
                span
                for span in spans
                if (
                    (_status_code(span) or 0) >= 500
                    or span.status == "ERROR"
                    or "error.type" in span.attributes
                )
            ]
            last = spans[-1]
            summaries.append(
                NativeRouteTelemetry(
                    route_id=route_id,
                    request_count=len(spans),
                    error_count=len(errors),
                    error_rate=(len(errors) / len(spans)) if spans else 0.0,
                    validation_failure_count=validation_by_route.get(
                        route_id,
                        0,
                    ),
                    average_ms=sum(durations) / len(durations),
                    p50_ms=_percentile(durations, 0.50),
                    p95_ms=_percentile(durations, 0.95),
                    min_ms=min(durations),
                    max_ms=max(durations),
                    last_ms=last.duration_ms,
                    last_status_code=_status_code(last),
                )
            )

        return sorted(
            summaries,
            key=lambda summary: (
                -(summary.p95_ms or 0.0),
                summary.route_id,
            ),
        )

    def total_requests(self) -> int:
        return len(self.server_spans())

    def error_count(self) -> int:
        return sum(
            1
            for span in self.server_spans()
            if (
                (_status_code(span) or 0) >= 500
                or span.status == "ERROR"
                or "error.type" in span.attributes
            )
        )

    def error_rate(self) -> float:
        total = self.total_requests()
        return self.error_count() / total if total else 0.0

    def external_span_count(self) -> int:
        return sum(1 for span in self.spans if is_external_span(span))

    def external_span_categories(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for span in self.spans:
            if is_external_span(span):
                counts[span_category(span)] += 1
        return dict(sorted(counts.items()))

    def external_spans_for_trace(
        self,
        trace_id: str,
    ) -> list[NativeTelemetrySpan]:
        return [
            span
            for span in self.trace_spans(trace_id)
            if is_external_span(span)
        ]

    def validation_failure_count(self) -> int:
        return sum(
            1
            for log in self.logs
            if log.event_name == "fastapi.validation.failed"
        )

    def exception_count(self) -> int:
        return sum(
            1
            for log in self.logs
            if log.event_name
            in {
                "http.server.request.exception",
                "fastapi.websocket.exception",
            }
        )

    def latency_percentile(self, percentile: float) -> float | None:
        return _percentile(
            [span.duration_ms for span in self.server_spans()],
            percentile,
        )

    def average_latency_ms(self) -> float | None:
        spans = self.server_spans()
        if not spans:
            return None
        return sum(span.duration_ms for span in spans) / len(spans)

    def trace_ids(self) -> list[str]:
        roots = sorted(
            self.server_spans(),
            key=lambda span: span.end_ns,
            reverse=True,
        )
        seen: set[str] = set()
        result: list[str] = []
        for span in roots:
            if span.trace_id in seen:
                continue
            seen.add(span.trace_id)
            result.append(span.trace_id)
        return result

    def trace_spans(self, trace_id: str) -> list[NativeTelemetrySpan]:
        return sorted(
            [span for span in self.spans if span.trace_id == trace_id],
            key=lambda span: (span.start_ns, span.end_ns, span.name),
        )

    def trace_root(self, trace_id: str) -> NativeTelemetrySpan | None:
        spans = self.trace_spans(trace_id)
        server = [span for span in spans if _is_server_span(span)]
        if server:
            return min(server, key=lambda span: span.start_ns)
        return min(spans, key=lambda span: span.start_ns) if spans else None

    def preferred_source_function(self, trace_id: str) -> str | None:
        """Return the best source-backed FastAPI function for one trace.

        Native exception logs intentionally do not persist traceback frames.
        For source navigation, reuse the already captured FastAPI operation
        spans and prefer the deepest failed operation carrying
        code.function.name.
        """

        spans = self.trace_spans(trace_id)
        if not spans:
            return None

        by_id = {span.span_id: span for span in spans}

        def depth(span: NativeTelemetrySpan) -> int:
            seen: set[str] = set()
            current = span
            result = 0
            while current.parent_span_id and current.parent_span_id in by_id:
                if current.parent_span_id in seen:
                    break
                seen.add(current.parent_span_id)
                result += 1
                current = by_id[current.parent_span_id]
            return result

        candidates: list[tuple[int, int, int, int, str]] = []
        for span in spans:
            raw_function = span.attributes.get("code.function.name")
            if not isinstance(raw_function, str) or not raw_function.strip():
                continue

            failed = int(
                span.status == "ERROR"
                or "error.type" in span.attributes
            )
            operation_priority = {
                "fastapi.endpoint": 3,
                "fastapi.dependencies": 2,
                "fastapi.background_task": 1,
                "fastapi.serialization": 0,
            }.get(span.name, 0)
            candidates.append(
                (
                    failed,
                    depth(span),
                    operation_priority,
                    span.end_ns,
                    raw_function.strip(),
                )
            )

        if not candidates:
            return None
        return max(candidates)[-1]

    def recent_server_spans(self, limit: int = 120) -> list[NativeTelemetrySpan]:
        return sorted(
            self.server_spans(),
            key=lambda span: span.end_ns,
        )[-max(1, limit):]

    def latest_control(self) -> NativeTelemetryControl | None:
        return self.controls[-1] if self.controls else None

    def logs_for_trace(self, trace_id: str) -> list[NativeTelemetryLog]:
        return [
            log
            for log in self.logs
            if log.trace_id == trace_id
        ]


__all__ = [
    "NativeTelemetryStore",
    "is_external_span",
    "span_category",
    "span_route_id",
    "span_target",
]
