"""Minimal first Spyder panel; richer route and lineage views build on this."""

from __future__ import annotations

from qtpy.QtWidgets import QLabel, QVBoxLayout
from spyder.api.widgets.main_widget import PluginMainWidget


class FastAPIStudioWidget(PluginMainWidget):
    """Initial FastAPI Studio dock widget."""

    def __init__(self, name=None, plugin=None, parent=None):
        super().__init__(name, plugin, parent)
        self._summary = QLabel(
            "FastAPI Studio\n\n"
            "Architecture bridge ready. Next: app discovery, route tree and lineage view."
        )
        self._summary.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addWidget(self._summary)
        layout.addStretch(1)
        self.setLayout(layout)

    def get_title(self):
        return "FastAPI Studio"

    def get_focus_widget(self):
        return self._summary

    def setup(self):
        pass

    def update_actions(self):
        pass
