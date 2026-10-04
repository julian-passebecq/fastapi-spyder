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
    OPTIONAL = [
        Plugins.Debugger,
        Plugins.Editor,
        Plugins.MainInterpreter,
        Plugins.WorkingDirectory,
    ]
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
        widget = self.get_widget()
        widget.sig_open_source.connect(
            lambda filename, line: editor.load(filename, line)
        )
        widget.sig_set_breakpoint.connect(self._set_handler_breakpoint)

    def _set_handler_breakpoint(self, filename: str, line: int) -> None:
        """Set, but never toggle off, a Spyder breakpoint for a route handler."""

        editor = self.get_plugin(Plugins.Editor)
        widget = self.get_widget()

        editor.load(filename, line)
        codeeditor = editor.get_codeeditor_for_filename(filename)
        if codeeditor is None:
            widget.set_status_message(
                f"Could not open {filename} to set a breakpoint."
            )
            return

        manager = getattr(codeeditor, "breakpoints_manager", None)
        if manager is None:
            widget.set_status_message(
                "Spyder's Debugger is not available for this editor. "
                "The handler source was opened instead."
            )
            return

        existing = {
            int(lineno)
            for lineno, _condition in manager.get_breakpoints()
        }
        if line not in existing:
            manager.toogle_breakpoint(line)

        refreshed = {
            int(lineno)
            for lineno, _condition in manager.get_breakpoints()
        }
        if line in refreshed:
            widget.set_status_message(
                f"Breakpoint ready at {filename}:{line}. "
                "Run the FastAPI server under Spyder's debugger, then replay "
                "the request from Request Lab."
            )
        else:
            widget.set_status_message(
                f"Spyder could not place a breakpoint at {filename}:{line}."
            )

    @on_plugin_available(plugin=Plugins.MainInterpreter)
    def on_main_interpreter_available(self):
        main_interpreter = self.get_plugin(Plugins.MainInterpreter)
        widget = self.get_widget()
        widget.set_python_executable(
            main_interpreter.get_container().get_main_interpreter()
        )
        main_interpreter.sig_interpreter_changed.connect(
            widget.set_python_executable
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
        widget = self.get_widget()
        widget.sig_open_source.disconnect()
        widget.sig_set_breakpoint.disconnect(self._set_handler_breakpoint)

    @on_plugin_teardown(plugin=Plugins.MainInterpreter)
    def on_main_interpreter_teardown(self):
        main_interpreter = self.get_plugin(Plugins.MainInterpreter)
        main_interpreter.sig_interpreter_changed.disconnect(
            self.get_widget().set_python_executable
        )

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
