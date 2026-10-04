"""FastAPI inspection core; intentionally independent from Spyder and Qt."""

from .inspector import inspect_app
from .lineage import impacted_routes

__all__ = ["impacted_routes", "inspect_app"]
