"""FastAPI inspection core; intentionally independent from Spyder and Qt."""

from .diff import diff_maps
from .discovery import discover_targets
from .inspector import inspect_app
from .lineage import impacted_routes
from .request_lab import build_request_template, validation_issues
from .snapshot import (
    dump_snapshot,
    load_snapshot,
    load_snapshot_text,
    save_snapshot,
)

__all__ = [
    "build_request_template",
    "diff_maps",
    "discover_targets",
    "dump_snapshot",
    "impacted_routes",
    "inspect_app",
    "load_snapshot",
    "load_snapshot_text",
    "save_snapshot",
    "validation_issues",
]
