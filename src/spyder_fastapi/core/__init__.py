"""FastAPI inspection core; intentionally independent from Spyder and Qt."""

from .diagram import (
    global_projection,
    impact_projection,
    overlay_runtime_lineage,
    route_projection,
)
from .diff import diff_maps
from .discovery import discover_targets
from .inspector import inspect_app
from .lineage import impacted_routes
from .request_lab import (
    build_request_template,
    local_debug_server_address,
    validation_issues,
)
from .runtime import clear_runtime_evidence, record_route_execution
from .snapshot import (
    dump_snapshot,
    load_snapshot,
    load_snapshot_text,
    save_snapshot,
)
from .telemetry import NativeTelemetryStore
from .test_links import discover_route_tests, route_tests

__all__ = [
    "build_request_template",
    "clear_runtime_evidence",
    "diff_maps",
    "discover_route_tests",
    "discover_targets",
    "dump_snapshot",
    "global_projection",
    "impact_projection",
    "impacted_routes",
    "inspect_app",
    "load_snapshot",
    "load_snapshot_text",
    "NativeTelemetryStore",
    "overlay_runtime_lineage",
    "local_debug_server_address",
    "record_route_execution",
    "route_projection",
    "save_snapshot",
    "route_tests",
    "validation_issues",
]
