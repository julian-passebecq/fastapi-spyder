"""FastAPI inspection core; intentionally independent from Spyder and Qt."""

from .diagram import global_projection, impact_projection, route_projection
from .diff import diff_maps
from .discovery import discover_targets
from .inspector import inspect_app
from .lineage import impacted_routes
from .request_lab import (
    build_request_template,
    local_debug_server_address,
    validation_issues,
)
from .snapshot import (
    dump_snapshot,
    load_snapshot,
    load_snapshot_text,
    save_snapshot,
)
from .test_links import discover_route_tests, route_tests

__all__ = [
    "build_request_template",
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
    "local_debug_server_address",
    "route_projection",
    "save_snapshot",
    "route_tests",
    "validation_issues",
]
