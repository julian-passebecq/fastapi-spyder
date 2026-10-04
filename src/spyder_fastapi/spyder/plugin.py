"""Spyder dockable plugin entry point."""

from __future__ import annotations

from qtpy.QtGui import QIcon
from spyder.api.plugins import Plugins, SpyderDockablePlugin

from spyder_fastapi.spyder.widget import FastAPIStudioWidget


class FastAPIStudioPlugin(SpyderDockablePlugin):
    """FastAPI Studio panel."""

    NAME = "fastapi_studio"
    REQUIRES = []
    OPTIONAL = [Plugins.Editor]
    WIDGET_CLASS = FastAPIStudioWidget
    CONF_SECTION = NAME
    TABIFY = [Plugins.Editor]

    def get_name(self):
        return "FastAPI Studio"

    def get_description(self):
        return "Inspect FastAPI routes, contracts, dependencies and lineage."

    def get_icon(self):
        return QIcon()

    def on_initialize(self):
        pass

    def check_compatibility(self):
        return True, ""

    def on_close(self, cancellable=True):
        return True
