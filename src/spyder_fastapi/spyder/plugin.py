"""Spyder dockable plugin entry point."""

from __future__ import annotations

from qtpy.QtGui import QIcon
from spyder.api.plugin_registration.decorators import (
    on_plugin_available,
    on_plugin_teardown,
)
from spyder.api.plugins import Plugins, SpyderDockablePlugin

from spyder_fastapi.spyder.widget import FastAPIStudioWidget


class FastAPIStudioPlugin(SpyderDockablePlugin):
    """FastAPI Studio panel."""

    NAME = "fastapi_studio"
    REQUIRES = []
    OPTIONAL = [Plugins.Editor, Plugins.WorkingDirectory]
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

    @on_plugin_available(plugin=Plugins.Editor)
    def on_editor_available(self):
        editor = self.get_plugin(Plugins.Editor)
        self.get_widget().sig_open_source.connect(
            lambda filename, line: editor.load(filename, line)
        )

    @on_plugin_available(plugin=Plugins.WorkingDirectory)
    def on_working_directory_available(self):
        working_directory = self.get_plugin(Plugins.WorkingDirectory)
        widget = self.get_widget()
        widget.set_working_directory(working_directory.get_workdir())
        working_directory.sig_current_directory_changed.connect(
            widget.set_working_directory
        )

    @on_plugin_teardown(plugin=Plugins.Editor)
    def on_editor_teardown(self):
        self.get_widget().sig_open_source.disconnect()

    @on_plugin_teardown(plugin=Plugins.WorkingDirectory)
    def on_working_directory_teardown(self):
        working_directory = self.get_plugin(Plugins.WorkingDirectory)
        working_directory.sig_current_directory_changed.disconnect(
            self.get_widget().set_working_directory
        )

    def check_compatibility(self):
        return True, ""

    def on_close(self, cancelable=False):
        return True
