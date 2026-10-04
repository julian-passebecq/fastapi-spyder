"""Local capture bridge for FastAPI's native OpenTelemetry signals.

This module is imported only by the Spyder debug launcher. The core inspector
and its JSON bridge remain independent from the OpenTelemetry SDK.
"""

from __future__ import annotations

import inspect
import json
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 1
_DEFERRED_PROVIDERS = {
    ("opentelemetry.trace", "ProxyTracerProvider"),
    ("opentelemetry._logs._internal", "ProxyLoggerProvider"),
}


class _JsonlSink:
    def __init__(self) -> None:
        self._path: Path | None = None
        self._lock = threading.RLock()

    def set_path(self, path: str | Path) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("", encoding="utf-8")
        with self._lock:
            self._path = destination
        return destination

    def write(self, payload: dict[str, Any]) -> None:
        with self._lock:
            path = self._path
            if path is None:
                return
            line = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")


_SINK = _JsonlSink()
_TRACE_PROCESSOR = None
_LOG_PROCESSOR = None


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return value[:2048]

    if isinstance(value, bytes):
        return value[:256].hex()

    if isinstance(value, (tuple, list)):
        return [_safe_value(item) for item in value[:50]]

    if isinstance(value, dict):
        return {
            str(key)[:256]: _safe_value(item)
            for key, item in list(value.items())[:100]
        }

    return str(value)[:2048]


def _safe_attributes(attributes: Any) -> dict[str, Any]:
    if not attributes:
        return {}
    try:
        items = attributes.items()
    except AttributeError:
        return {}
    return {
        str(key): _safe_value(value)
        for key, value in items
    }


def _hex_id(value: int | None, width: int) -> str | None:
    if not value:
        return None
    return f"{int(value):0{width}x}"


def _is_deferred_provider(provider: Any) -> bool:
    cls = type(provider)
    return (cls.__module__, cls.__name__) in _DEFERRED_PROVIDERS


def _fastapi_version() -> str | None:
    try:
        return version("fastapi")
    except PackageNotFoundError:
        return None


def _install_trace_capture() -> tuple[bool, str]:
    global _TRACE_PROCESSOR

    if _TRACE_PROCESSOR is not None:
        return True, "reused"

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )

    class _JsonlSpanExporter(SpanExporter):
        def export(self, spans):
            for span in spans:
                context = span.context
                parent = span.parent
                start_ns = int(span.start_time or 0)
                end_ns = int(span.end_time or start_ns)
                scope = getattr(span, "instrumentation_scope", None)
                status = getattr(span, "status", None)
                status_code = getattr(status, "status_code", None)

                _SINK.write(
                    {
                        "signal": "span",
                        "schema_version": _SCHEMA_VERSION,
                        "trace_id": _hex_id(
                            getattr(context, "trace_id", None),
                            32,
                        )
                        or "0" * 32,
                        "span_id": _hex_id(
                            getattr(context, "span_id", None),
                            16,
                        )
                        or "0" * 16,
                        "parent_span_id": _hex_id(
                            getattr(parent, "span_id", None),
                            16,
                        ),
                        "name": str(span.name),
                        "kind": getattr(
                            getattr(span, "kind", None),
                            "name",
                            str(getattr(span, "kind", "")),
                        ),
                        "start_ns": start_ns,
                        "end_ns": end_ns,
                        "duration_ms": max(
                            0.0,
                            (end_ns - start_ns) / 1_000_000,
                        ),
                        "status": getattr(
                            status_code,
                            "name",
                            str(status_code) if status_code is not None else None,
                        ),
                        "attributes": _safe_attributes(span.attributes),
                        "scope_name": getattr(scope, "name", None),
                        "scope_version": getattr(scope, "version", None),
                    }
                )
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    exporter = _JsonlSpanExporter()
    processor = SimpleSpanProcessor(exporter)
    current = trace.get_tracer_provider()

    if _is_deferred_provider(current):
        provider = TracerProvider(shutdown_on_exit=False)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        mode = "global_provider"
    elif hasattr(current, "add_span_processor"):
        current.add_span_processor(processor)
        mode = "attached_processor"
    else:
        return False, (
            "The active OpenTelemetry tracer provider does not support "
            "adding a local span processor."
        )

    _TRACE_PROCESSOR = processor
    return True, mode


def _install_log_capture() -> tuple[bool, str]:
    global _LOG_PROCESSOR

    if _LOG_PROCESSOR is not None:
        return True, "reused"

    from opentelemetry import _logs
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import (
        LogRecordExportResult,
        LogRecordExporter,
        SimpleLogRecordProcessor,
    )

    class _JsonlLogExporter(LogRecordExporter):
        def export(self, batch):
            for readable in batch:
                record = readable.log_record
                scope = getattr(readable, "instrumentation_scope", None)
                exception = getattr(record, "exception", None)
                severity_number = getattr(record, "severity_number", None)

                _SINK.write(
                    {
                        "signal": "log",
                        "schema_version": _SCHEMA_VERSION,
                        "timestamp_ns": getattr(record, "timestamp", None),
                        "observed_timestamp_ns": getattr(
                            record,
                            "observed_timestamp",
                            None,
                        ),
                        "trace_id": _hex_id(
                            getattr(record, "trace_id", None),
                            32,
                        ),
                        "span_id": _hex_id(
                            getattr(record, "span_id", None),
                            16,
                        ),
                        "severity": (
                            getattr(severity_number, "name", None)
                            or getattr(record, "severity_text", None)
                        ),
                        "event_name": getattr(record, "event_name", None),
                        "body": (
                            str(getattr(record, "body", ""))[:2048]
                            if getattr(record, "body", None) is not None
                            else None
                        ),
                        "attributes": _safe_attributes(
                            getattr(record, "attributes", None)
                        ),
                        # Deliberately do not persist traceback or exception
                        # message in the local JSONL bridge.
                        "exception_type": (
                            f"{type(exception).__module__}."
                            f"{type(exception).__qualname__}"
                            if exception is not None
                            else None
                        ),
                        "scope_name": getattr(scope, "name", None),
                    }
                )
            return LogRecordExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    exporter = _JsonlLogExporter()
    processor = SimpleLogRecordProcessor(exporter)
    current = _logs.get_logger_provider()

    if _is_deferred_provider(current):
        provider = LoggerProvider(shutdown_on_exit=False)
        provider.add_log_record_processor(processor)
        _logs.set_logger_provider(provider)
        mode = "global_provider"
    elif hasattr(current, "add_log_record_processor"):
        current.add_log_record_processor(processor)
        mode = "attached_processor"
    else:
        return False, (
            "The active OpenTelemetry logger provider does not support "
            "adding a local log processor."
        )

    _LOG_PROCESSOR = processor
    return True, mode


def configure_native_telemetry(
    destination: str | Path,
) -> dict[str, Any]:
    """Capture FastAPI native traces/logs into an ephemeral local JSONL file.

    Providers already owned by the application are preserved when possible:
    the Studio adds a processor instead of replacing the provider.
    """

    path = _SINK.set_path(destination)
    fastapi_version = _fastapi_version()

    try:
        from fastapi import FastAPI
    except ImportError:
        payload = {
            "signal": "control",
            "schema_version": _SCHEMA_VERSION,
            "event": "capture_unavailable",
            "message": "FastAPI is not installed in the selected environment.",
            "fastapi_version": fastapi_version,
            "tracing": False,
            "logs": False,
        }
        _SINK.write(payload)
        return payload

    if "telemetry" not in inspect.signature(FastAPI).parameters:
        payload = {
            "signal": "control",
            "schema_version": _SCHEMA_VERSION,
            "event": "capture_unavailable",
            "message": (
                "This FastAPI version does not expose native telemetry yet. "
                "Architecture, Request Lab and client timing remain available."
            ),
            "fastapi_version": fastapi_version,
            "tracing": False,
            "logs": False,
        }
        _SINK.write(payload)
        return payload

    trace_mode = "disabled"
    log_mode = "disabled"

    try:
        tracing, trace_mode = _install_trace_capture()
    except ImportError as exc:
        tracing = False
        trace_mode = f"SDK unavailable: {exc}"
    except Exception as exc:
        tracing = False
        trace_mode = f"{type(exc).__name__}: {exc}"

    try:
        logs, log_mode = _install_log_capture()
    except ImportError as exc:
        logs = False
        log_mode = f"SDK unavailable: {exc}"
    except Exception as exc:
        logs = False
        log_mode = f"{type(exc).__name__}: {exc}"

    event = "capture_configured" if tracing or logs else "capture_unavailable"
    message = (
        f"FastAPI native telemetry capture "
        f"{'ready' if event == 'capture_configured' else 'unavailable'} "
        f"(traces: {trace_mode}; logs: {log_mode})."
    )
    payload = {
        "signal": "control",
        "schema_version": _SCHEMA_VERSION,
        "event": event,
        "message": message,
        "fastapi_version": fastapi_version,
        "tracing": tracing,
        "logs": logs,
    }
    _SINK.write(payload)
    return {**payload, "path": str(path)}


__all__ = ["configure_native_telemetry"]
