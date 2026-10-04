"""FastAPI inspection core; intentionally independent from Spyder and Qt."""

from .discovery import discover_targets
from .inspector import inspect_app
from .lineage import impacted_routes

__all__ = ["discover_targets", "impacted_routes", "inspect_app"]
