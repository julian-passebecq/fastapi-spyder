"""Aggregation of observed Request Lab route runtime evidence."""

from __future__ import annotations

from spyder_fastapi.models import (
    RequestExecution,
    RouteRuntimeStats,
    RuntimeEvidence,
)


def record_route_execution(
    evidence: RuntimeEvidence,
    route_id: str,
    result: RequestExecution,
) -> RouteRuntimeStats:
    """Record one Request Lab result and return the updated route stats.

    Elapsed time is end-to-end Request Lab client time. It is intentionally
    not described as handler or server processing time.
    """

    stats = evidence.routes.get(route_id)
    if stats is None:
        stats = RouteRuntimeStats(route_id=route_id)
        evidence.routes[route_id] = stats

    stats.request_count += 1
    stats.last_status_code = result.status_code

    if result.error:
        stats.transport_error_count += 1

    elapsed = result.elapsed_ms
    if elapsed is not None:
        elapsed = float(elapsed)
        previous_timed = stats.timed_count
        stats.timed_count += 1
        stats.last_elapsed_ms = elapsed

        if stats.average_elapsed_ms is None or previous_timed == 0:
            stats.average_elapsed_ms = elapsed
        else:
            stats.average_elapsed_ms = (
                stats.average_elapsed_ms * previous_timed + elapsed
            ) / stats.timed_count

        if stats.min_elapsed_ms is None:
            stats.min_elapsed_ms = elapsed
        else:
            stats.min_elapsed_ms = min(stats.min_elapsed_ms, elapsed)

        if stats.max_elapsed_ms is None:
            stats.max_elapsed_ms = elapsed
        else:
            stats.max_elapsed_ms = max(stats.max_elapsed_ms, elapsed)

    return stats


def clear_runtime_evidence(evidence: RuntimeEvidence) -> None:
    """Clear all in-memory Request Lab observations."""

    evidence.routes.clear()
