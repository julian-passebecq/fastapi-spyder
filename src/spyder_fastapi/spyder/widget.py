"""FastAPI Studio main dock widget."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from uuid import uuid4
from collections import defaultdict
from pathlib import Path

from qtpy.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from spyder.api.widgets.main_widget import PluginMainWidget

from spyder_fastapi.core import (
    diff_maps,
    discover_route_tests,
    discover_targets,
    impacted_routes,
    clear_runtime_evidence,
    NativeTelemetryStore,
    load_snapshot,
    record_route_execution,
    save_snapshot,
    route_tests,
)
from spyder_fastapi.models import (
    APIDiff,
    FastAPIMap,
    RequestExecution,
    RouteTestIndex,
    RuntimeEvidence,
    SourceRef,
)
from spyder_fastapi.spyder.diagram import FastAPIDiagramWidget
from spyder_fastapi.spyder.request_lab import RequestLabWidget
from spyder_fastapi.spyder.telemetry import FastAPITelemetryWidget


_ROLE_ID = 32
_ROLE_SOURCE_FILE = 33
_ROLE_SOURCE_LINE = 34


def _short_name(value: str) -> str:
    return value.rsplit(".", 1)[-1]


def _source_text(source: SourceRef | None) -> str:
    if source is None or not source.file:
        return "source unavailable"
    if source.line:
        return f"{source.file}:{source.line}"
    return source.file


class FastAPIStudioWidget(PluginMainWidget):
    """FastAPI architecture and lineage explorer."""

    sig_open_source = Signal(str, int)
    sig_set_breakpoint = Signal(str, int)
    sig_start_debug_server = Signal(str, str, str, int)
    sig_stop_debug_server = Signal()

    def __init__(self, name=None, plugin=None, parent=None):
        super().__init__(name, plugin, parent)

        self._api_map: FastAPIMap | None = None
        self._baseline: FastAPIMap | None = None
        self._baseline_target: str | None = None
        self._loaded_target: str | None = None
        self._current_diff: APIDiff | None = None
        self._test_index = RouteTestIndex()
        self._runtime_evidence = RuntimeEvidence()
        self._runtime_target: str | None = None
        self._native_telemetry = NativeTelemetryStore()
        self._native_telemetry_path: Path | None = None
        self._native_telemetry_offset = 0
        self._native_telemetry_partial = b""
        self._workdir = os.getcwd()
        self._python_executable = sys.executable
        self._process: QProcess | None = None
        self._stdout_chunks: list[str] = []
        self._stderr_chunks: list[str] = []

        self._native_telemetry_timer = QTimer(self)
        self._native_telemetry_timer.setInterval(250)
        self._native_telemetry_timer.timeout.connect(
            self._poll_native_telemetry
        )

        self._build_ui()
        self.set_working_directory(self._workdir)

    # --- PluginMainWidget API
    # ------------------------------------------------------------------
    def get_title(self):
        return "FastAPI Studio"

    def get_focus_widget(self):
        return self._target

    def setup(self):
        pass

    def update_actions(self):
        pass

    # --- UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self._target = QComboBox()
        self._target.setEditable(True)
        self._target.setMinimumContentsLength(24)
        self._target.setToolTip(
            "FastAPI application target in module:attribute form, e.g. app.main:app"
        )

        self._discover_button = QPushButton("Discover")
        self._discover_button.clicked.connect(self.discover_apps)

        self._inspect_button = QPushButton("Inspect")
        self._inspect_button.clicked.connect(self.inspect_current_app)

        self._baseline_button = QPushButton("Set baseline")
        self._baseline_button.setEnabled(False)
        self._baseline_button.setToolTip(
            "Keep the currently inspected API as the comparison baseline."
        )
        self._baseline_button.clicked.connect(self.set_baseline)

        self._clear_baseline_button = QPushButton("Clear baseline")
        self._clear_baseline_button.setEnabled(False)
        self._clear_baseline_button.clicked.connect(self.clear_baseline)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("App"))
        controls.addWidget(self._target, 1)
        controls.addWidget(self._discover_button)
        controls.addWidget(self._inspect_button)
        controls.addWidget(self._baseline_button)
        controls.addWidget(self._clear_baseline_button)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(
            "Route, model or dependency..."
        )
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter, 1)

        self._workdir_label = QLabel()
        self._workdir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._interpreter_label = QLabel()
        self._interpreter_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.set_python_executable(self._python_executable)

        self._status = QLabel("No FastAPI application inspected yet.")
        self._status.setWordWrap(True)

        self._tabs = QTabWidget()
        self._overview = QTextBrowser()
        self._tabs.addTab(self._overview, "Overview")

        changes_page = QWidget()
        changes_layout = QVBoxLayout(changes_page)
        self._baseline_label = QLabel(
            "No baseline. Inspect an app, then choose Set baseline."
        )
        self._baseline_label.setWordWrap(True)
        changes_layout.addWidget(self._baseline_label)

        snapshot_actions = QHBoxLayout()
        self._save_snapshot_button = QPushButton("Save snapshot...")
        self._save_snapshot_button.setEnabled(False)
        self._save_snapshot_button.clicked.connect(self.save_snapshot_dialog)
        snapshot_actions.addWidget(self._save_snapshot_button)

        self._load_baseline_button = QPushButton("Load baseline...")
        self._load_baseline_button.clicked.connect(self.load_baseline_dialog)
        snapshot_actions.addWidget(self._load_baseline_button)
        snapshot_actions.addStretch(1)
        changes_layout.addLayout(snapshot_actions)

        self._changes_tree = QTreeWidget()
        self._changes_tree.setHeaderLabels(
            ["Delta", "Entity", "Name", "Affected", "Compatibility risk"]
        )
        self._changes_tree.setRootIsDecorated(False)
        self._changes_tree.currentItemChanged.connect(self._change_selected)
        self._changes_tree.itemDoubleClicked.connect(self._open_tree_item_source)

        self._change_details = QPlainTextEdit()
        self._change_details.setReadOnly(True)

        changes_splitter = QSplitter(Qt.Horizontal)
        changes_splitter.addWidget(self._changes_tree)
        changes_splitter.addWidget(self._change_details)
        changes_splitter.setStretchFactor(0, 2)
        changes_splitter.setStretchFactor(1, 3)
        changes_layout.addWidget(changes_splitter, 1)
        self._tabs.addTab(changes_page, "Changes")

        self._routes_tree = QTreeWidget()
        self._routes_tree.setHeaderLabels(["Method", "Path / handler", "Tests"])
        self._routes_tree.setRootIsDecorated(True)
        self._routes_tree.currentItemChanged.connect(self._route_selected)
        self._routes_tree.itemDoubleClicked.connect(self._open_tree_item_source)
        self._route_details = QPlainTextEdit()
        self._route_details.setReadOnly(True)
        self._route_open = QPushButton("Open handler source")
        self._route_open.clicked.connect(
            lambda: self._open_tree_item_source(self._routes_tree.currentItem(), 0)
        )
        self._tabs.addTab(
            self._split_page(self._routes_tree, self._route_details, self._route_open),
            "Routes",
        )

        self._models_tree = QTreeWidget()
        self._models_tree.setHeaderLabels(["Model", "Routes"])
        self._models_tree.setRootIsDecorated(False)
        self._models_tree.currentItemChanged.connect(self._model_selected)
        self._models_tree.itemDoubleClicked.connect(self._open_tree_item_source)
        self._model_details = QPlainTextEdit()
        self._model_details.setReadOnly(True)
        self._model_open = QPushButton("Open model source")
        self._model_open.clicked.connect(
            lambda: self._open_tree_item_source(self._models_tree.currentItem(), 0)
        )
        self._tabs.addTab(
            self._split_page(
                self._models_tree,
                self._model_details,
                self._model_open,
            ),
            "Models",
        )

        self._dependencies_tree = QTreeWidget()
        self._dependencies_tree.setHeaderLabels(["Dependency", "Routes"])
        self._dependencies_tree.setRootIsDecorated(False)
        self._dependencies_tree.currentItemChanged.connect(self._dependency_selected)
        self._dependencies_tree.itemDoubleClicked.connect(
            self._open_tree_item_source
        )
        self._dependency_details = QPlainTextEdit()
        self._dependency_details.setReadOnly(True)
        self._dependency_open = QPushButton("Open dependency source")
        self._dependency_open.clicked.connect(
            lambda: self._open_tree_item_source(
                self._dependencies_tree.currentItem(), 0
            )
        )
        self._tabs.addTab(
            self._split_page(
                self._dependencies_tree,
                self._dependency_details,
                self._dependency_open,
            ),
            "Dependencies",
        )

        self._diagram = FastAPIDiagramWidget()
        self._diagram.sig_open_source.connect(self.sig_open_source.emit)
        self._diagram.sig_clear_runtime.connect(self._clear_runtime_evidence)
        self._tabs.addTab(self._diagram, "Diagram")

        self._telemetry = FastAPITelemetryWidget()
        self._diagram.sig_open_telemetry.connect(
            self._open_telemetry_route
        )
        self._telemetry.sig_clear.connect(self.clear_native_telemetry)
        self._telemetry.sig_route_selected.connect(
            self._telemetry_route_selected
        )
        self._telemetry.sig_function_selected.connect(
            self._telemetry_function_selected
        )
        self._tabs.addTab(self._telemetry, "Telemetry")

        tests_page = QWidget()
        tests_layout = QVBoxLayout(tests_page)

        tests_controls = QHBoxLayout()
        self._tests_summary = QLabel("No route-to-test scan yet.")
        self._tests_summary.setWordWrap(True)
        tests_controls.addWidget(self._tests_summary, 1)
        self._refresh_tests_button = QPushButton("Refresh tests")
        self._refresh_tests_button.clicked.connect(self.refresh_test_links)
        tests_controls.addWidget(self._refresh_tests_button)
        tests_layout.addLayout(tests_controls)

        self._tests_tree = QTreeWidget()
        self._tests_tree.setHeaderLabels(["Route", "Test", "Match", "Source"])
        self._tests_tree.setRootIsDecorated(False)
        self._tests_tree.currentItemChanged.connect(self._test_selected)
        self._tests_tree.itemDoubleClicked.connect(self._open_tree_item_source)
        tests_layout.addWidget(self._tests_tree, 1)
        self._tabs.addTab(tests_page, "Tests")

        lineage_page = QWidget()
        lineage_layout = QVBoxLayout(lineage_page)
        lineage_controls = QHBoxLayout()
        lineage_controls.addWidget(QLabel("Route"))
        self._lineage_route = QComboBox()
        self._lineage_route.currentTextChanged.connect(
            self._populate_lineage_for_route
        )
        lineage_controls.addWidget(self._lineage_route, 1)
        lineage_layout.addLayout(lineage_controls)

        self._lineage_tree = QTreeWidget()
        self._lineage_tree.setHeaderLabels(["Relationship", "Node", "Kind"])
        self._lineage_tree.itemDoubleClicked.connect(self._open_tree_item_source)
        lineage_layout.addWidget(self._lineage_tree, 1)
        self._tabs.addTab(lineage_page, "Lineage")

        self._request_lab = RequestLabWidget()
        self._request_lab.sig_status.connect(self._status.setText)
        self._request_lab.sig_open_source.connect(self.sig_open_source.emit)
        self._request_lab.sig_set_breakpoint.connect(
            self.sig_set_breakpoint.emit
        )
        self._request_lab.sig_start_debug_server.connect(
            self.sig_start_debug_server.emit
        )
        self._request_lab.sig_stop_debug_server.connect(
            self.sig_stop_debug_server.emit
        )
        self._request_lab.sig_request_completed.connect(
            self._request_completed
        )
        self._request_lab.set_python_executable(self._python_executable)
        self._request_lab.set_working_directory(self._workdir)
        self._tabs.addTab(self._request_lab, "Request Lab")

        self._openapi_view = QPlainTextEdit()
        self._openapi_view.setReadOnly(True)
        self._tabs.addTab(self._openapi_view, "OpenAPI")

        self._json_view = QPlainTextEdit()
        self._json_view.setReadOnly(True)
        self._tabs.addTab(self._json_view, "Map JSON")

        self._diagnostics = QPlainTextEdit()
        self._diagnostics.setReadOnly(True)
        self._diagnostics.setPlaceholderText(
            "Application import output and inspection errors appear here."
        )
        self._tabs.addTab(self._diagnostics, "Diagnostics")

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addLayout(filter_row)
        layout.addWidget(self._workdir_label)
        layout.addWidget(self._interpreter_label)
        layout.addWidget(self._status)
        layout.addWidget(self._tabs, 1)
        self.setLayout(layout)

    @staticmethod
    def _split_page(
        tree: QTreeWidget,
        details: QPlainTextEdit,
        action: QPushButton | None = None,
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(tree)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        if action is not None:
            layout.addWidget(action)
        return page

    def set_status_message(self, message: str) -> None:
        self._status.setText(message)

    def set_debug_server_available(self, available: bool) -> None:
        self._request_lab.set_debug_server_available(available)

    def set_debug_server_running(self, running: bool) -> None:
        self._request_lab.set_debug_server_running(running)

    def shutdown(self) -> None:
        """Terminate child processes owned by FastAPI Studio."""

        process = self._process
        if process is not None:
            try:
                process.finished.disconnect(self._inspection_finished)
            except (TypeError, RuntimeError):
                pass
            process.kill()
            process.waitForFinished(500)
            process.deleteLater()
            self._process = None

        self._request_lab.shutdown()
        self._native_telemetry_timer.stop()
        self._poll_native_telemetry()
        self._cleanup_native_telemetry_file()

    # --- Project/app discovery
    # ------------------------------------------------------------------
    def set_working_directory(self, path: str) -> None:
        self._workdir = os.path.abspath(path)
        self._workdir_label.setText(f"Working directory: {self._workdir}")
        self._workdir_label.setToolTip(self._workdir)
        if hasattr(self, "_request_lab"):
            self._request_lab.set_working_directory(self._workdir)

    def set_python_executable(self, path: str) -> None:
        """Use Spyder's selected interpreter for project inspection."""

        if path:
            self._python_executable = os.path.abspath(path)
        self._interpreter_label.setText(
            f"Python interpreter: {self._python_executable}"
        )
        self._interpreter_label.setToolTip(self._python_executable)
        if hasattr(self, "_request_lab"):
            self._request_lab.set_python_executable(self._python_executable)

    def discover_apps(self) -> None:
        self._status.setText("Scanning Python files for FastAPI applications...")
        candidates = discover_targets(self._workdir)

        current = self._target.currentText().strip()
        self._target.blockSignals(True)
        self._target.clear()
        for candidate in candidates:
            self._target.addItem(candidate.target)
        self._target.blockSignals(False)

        if current and current not in {candidate.target for candidate in candidates}:
            self._target.setEditText(current)
        elif candidates:
            self._target.setCurrentText(candidates[0].target)

        if not candidates:
            self._status.setText(
                "No simple FastAPI() assignment found. Enter module:attribute manually."
            )
        else:
            self._status.setText(
                f"Discovered {len(candidates)} FastAPI application target(s)."
            )

    # --- Subprocess inspection
    # ------------------------------------------------------------------
    def inspect_current_app(self) -> None:
        target = self._target.currentText().strip()
        if not target:
            self._status.setText("Enter or discover a FastAPI app target first.")
            return

        if self._process is not None:
            self._status.setText("An inspection is already running.")
            return

        self._stdout_chunks = []
        self._stderr_chunks = []
        self._diagnostics.clear()
        self._inspect_button.setEnabled(False)
        self._status.setText(f"Inspecting {target}...")

        process = QProcess(self)
        process.setWorkingDirectory(self._workdir)

        # The FastAPI project usually lives in Spyder's selected interpreter,
        # while this plugin can be installed in Spyder's own environment.
        # Expose the plugin package to the child without requiring a second
        # fastapi-spyder installation in the project environment.
        environment = QProcessEnvironment.systemEnvironment()
        package_root = str(Path(__file__).resolve().parents[2])
        current_pythonpath = environment.value("PYTHONPATH")
        pythonpath_parts = [package_root]
        if current_pythonpath:
            pythonpath_parts.append(current_pythonpath)
        environment.insert("PYTHONPATH", os.pathsep.join(pythonpath_parts))
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._inspection_finished)
        self._process = process

        process.start(
            self._python_executable,
            [
                "-m",
                "spyder_fastapi.cli",
                target,
                "--indent",
                "0",
            ],
        )

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        self._stdout_chunks.append(
            bytes(self._process.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            )
        )

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        self._stderr_chunks.append(text)
        self._diagnostics.appendPlainText(text.rstrip())

    def _inspection_finished(self, exit_code: int, _exit_status) -> None:
        process = self._process
        self._inspect_button.setEnabled(True)
        stdout = "".join(self._stdout_chunks).strip()
        stderr = "".join(self._stderr_chunks).strip()

        try:
            if exit_code != 0:
                self._status.setText(
                    f"Inspection failed with exit code {exit_code}. See Diagnostics."
                )
                if stderr:
                    self._status.setToolTip(stderr)
                return

            try:
                payload = json.loads(stdout)
                api_map = FastAPIMap.model_validate(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                self._status.setText(
                    "Inspector returned invalid JSON. See Diagnostics for details."
                )
                self._diagnostics.appendPlainText(
                    f"\nJSON parse error: {exc}\n{stdout}"
                )
                return

            self._loaded_target = self._target.currentText().strip() or None
            self.set_api_map(api_map)
            if stderr:
                self._status.setToolTip(stderr)
        finally:
            if process is not None:
                process.deleteLater()
            self._process = None

    # --- Rendering
    # ------------------------------------------------------------------
    def set_api_map(self, api_map: FastAPIMap) -> None:
        runtime_target = (
            self._loaded_target
            or self._target.currentText().strip()
            or api_map.title
        )
        if (
            self._runtime_target is not None
            and self._runtime_target != runtime_target
        ):
            self._runtime_evidence = RuntimeEvidence()
        self._runtime_target = runtime_target

        self._api_map = api_map
        self._baseline_button.setEnabled(True)
        self._save_snapshot_button.setEnabled(True)
        self._scan_test_links()
        self._populate_overview()
        self._populate_changes()
        self._populate_routes()
        self._populate_models()
        self._populate_dependencies()
        self._populate_lineage_routes()
        self._diagram.set_api_map(api_map)
        self._diagram.set_test_index(self._test_index)
        self._diagram.set_runtime_evidence(self._runtime_evidence)
        self._diagram.set_native_telemetry(self._native_telemetry)
        self._populate_test_links()
        self._request_lab.set_api_map(api_map)
        self._request_lab.set_app_target(
            self._loaded_target
            or self._target.currentText().strip()
            or None
        )
        self._openapi_view.setPlainText(
            json.dumps(api_map.openapi, indent=2, sort_keys=True)
        )
        self._json_view.setPlainText(
            api_map.model_dump_json(
                by_alias=True,
                indent=2,
                exclude={"openapi"},
            )
        )

        self._apply_filter(self._filter.text())
        self._status.setText(
            f"Loaded {api_map.title}: "
            f"{len(api_map.routes)} routes, "
            f"{len(api_map.models)} models, "
            f"{len(api_map.dependencies)} dependencies, "
            f"{len(self._test_index.references)} linked test call(s)."
        )

    def _snapshot_key(self) -> str:
        if self._loaded_target:
            return self._loaded_target
        target = self._target.currentText().strip()
        if target:
            return target
        if self._api_map is not None:
            return self._api_map.title
        return "<unknown>"

    def set_baseline(self) -> None:
        """Capture the currently loaded API map for semantic comparison."""

        if self._api_map is None:
            self._status.setText("Inspect a FastAPI application before setting a baseline.")
            return

        self._baseline = self._api_map.model_copy(deep=True)
        self._baseline_target = self._snapshot_key()
        self._clear_baseline_button.setEnabled(True)
        self._populate_changes()
        self._status.setText(
            f"Baseline captured for {self._baseline_target}. "
            "Edit the API and Inspect again to see semantic changes."
        )

    def clear_baseline(self) -> None:
        self._baseline = None
        self._baseline_target = None
        self._current_diff = None
        self._clear_baseline_button.setEnabled(False)
        self._populate_changes()

    def save_snapshot_dialog(self) -> None:
        if self._api_map is None:
            self._status.setText("Inspect a FastAPI application before saving a snapshot.")
            return

        suggested = os.path.join(self._workdir, "fastapi-studio.snapshot.json")
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save FastAPI Studio snapshot",
            suggested,
            "FastAPI Studio snapshot (*.json);;JSON files (*.json)",
        )
        if not path:
            return

        try:
            destination = save_snapshot(
                path,
                self._api_map,
                target=self._snapshot_key(),
            )
        except (OSError, ValueError) as exc:
            self._status.setText(f"Could not save snapshot: {exc}")
            self._diagnostics.appendPlainText(f"Snapshot save error: {exc}")
            return

        self._status.setText(f"Saved FastAPI snapshot: {destination}")

    def load_baseline_dialog(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load FastAPI Studio baseline",
            self._workdir,
            "FastAPI Studio snapshot (*.json);;JSON files (*.json)",
        )
        if not path:
            return

        try:
            envelope = load_snapshot(path)
        except (OSError, ValueError) as exc:
            self._status.setText(f"Could not load baseline snapshot: {exc}")
            self._diagnostics.appendPlainText(f"Snapshot load error: {exc}")
            return

        self._baseline = envelope.api
        self._baseline_target = envelope.target or envelope.api.title
        self._current_diff = None
        self._clear_baseline_button.setEnabled(True)
        self._populate_changes()
        self._status.setText(
            f"Loaded baseline snapshot for {self._baseline_target}."
        )

    def _populate_changes(self) -> None:
        self._changes_tree.clear()
        self._change_details.clear()
        self._current_diff = None

        if self._baseline is None:
            self._baseline_label.setText(
                "No baseline. Inspect an app, then choose Set baseline."
            )
            return

        if self._api_map is None:
            self._baseline_label.setText(
                f"Baseline: {self._baseline_target}. No current API loaded."
            )
            return

        current_target = self._snapshot_key()
        if self._baseline_target != current_target:
            self._baseline_label.setText(
                f"Baseline belongs to {self._baseline_target}; "
                f"current API is {current_target}. Set a new baseline to compare."
            )
            return

        diff = diff_maps(self._baseline, self._api_map)
        self._current_diff = diff
        self._baseline_label.setText(
            f"Baseline: {self._baseline_target} | "
            f"{len(diff.changes)} semantic change(s) | "
            f"{len(diff.affected_routes)} affected route(s) | "
            f"{diff.breaking_candidates} compatibility-risk candidate(s)"
        )

        if not diff.changes:
            self._change_details.setPlainText(
                "No semantic FastAPI contract or dependency changes detected."
            )
            return

        symbols = {"added": "+", "removed": "-", "changed": "~"}
        for index, change in enumerate(diff.changes):
            item = QTreeWidgetItem(
                [
                    symbols[change.kind],
                    change.entity,
                    change.name,
                    str(len(change.affected_routes)),
                    "candidate" if change.breaking_reasons else "",
                ]
            )
            item.setData(0, _ROLE_ID, index)
            self._set_item_source(item, change.source)
            if change.affected_routes:
                item.setToolTip(3, "\n".join(change.affected_routes))
            if change.breaking_reasons:
                item.setToolTip(4, "\n".join(change.breaking_reasons))
            self._changes_tree.addTopLevelItem(item)

        for column in range(self._changes_tree.columnCount()):
            self._changes_tree.resizeColumnToContents(column)

        self._changes_tree.setCurrentItem(self._changes_tree.topLevelItem(0))

    def _change_selected(self, item: QTreeWidgetItem | None, _previous) -> None:
        if item is None or self._current_diff is None:
            self._change_details.clear()
            return

        index = item.data(0, _ROLE_ID)
        if index is None or not (0 <= int(index) < len(self._current_diff.changes)):
            self._change_details.clear()
            return

        change = self._current_diff.changes[int(index)]
        fields = "\n".join(f"  {field}" for field in change.fields) or "  -"
        routes = (
            "\n".join(f"  {route}" for route in change.affected_routes)
            or "  -"
        )
        risks = (
            "\n".join(f"  {reason}" for reason in change.breaking_reasons)
            or "  -"
        )

        self._change_details.setPlainText(
            f"{change.kind.upper()} {change.entity}: {change.name}\n\n"
            f"Changed semantic fields\n{fields}\n\n"
            f"Affected routes\n{routes}\n\n"
            f"Compatibility-risk candidates\n{risks}\n\n"
            "Risk candidates are conservative signals, not a guarantee that "
            "a client is broken."
        )

    def _populate_overview(self) -> None:
        if self._api_map is None:
            return

        shared_dependencies = 0
        for dependency in self._api_map.dependencies:
            if len(impacted_routes(self._api_map, dependency.id)) > 1:
                shared_dependencies += 1

        shared_models = 0
        for model in self._api_map.models:
            if len(impacted_routes(self._api_map, f"model:{model.name}")) > 1:
                shared_models += 1

        linked_test_routes = {
            reference.route_id
            for reference in self._test_index.references
        }

        self._overview.setHtml(
            "<h3>{}</h3>"
            "<p><b>Version:</b> {}<br>"
            "<b>OpenAPI:</b> {}<br>"
            "<b>Routes:</b> {}<br>"
            "<b>Models:</b> {}<br>"
            "<b>Dependencies:</b> {}<br>"
            "<b>Routes with static test links:</b> {} / {}</p>"
            "<p><b>Shared dependency blast-radius nodes:</b> {}<br>"
            "<b>Shared schema blast-radius nodes:</b> {}</p>"
            "<p>The lineage view is derived from FastAPI's actual route, "
            "Pydantic and Depends() structures. Test links are static project "
            "evidence and are not execution-coverage claims. Double-click "
            "source-backed nodes to open their Python implementation.</p>".format(
                self._api_map.title,
                self._api_map.version or "-",
                self._api_map.openapi_version,
                len(self._api_map.routes),
                len(self._api_map.models),
                len(self._api_map.dependencies),
                len(linked_test_routes),
                len(self._api_map.routes),
                shared_dependencies,
                shared_models,
            )
        )

    def _populate_routes(self) -> None:
        self._routes_tree.clear()
        if self._api_map is None:
            return

        by_path = defaultdict(list)
        for route in self._api_map.routes:
            by_path[route.path].append(route)

        first_route_item = None
        for path in sorted(by_path):
            path_item = QTreeWidgetItem(["", path])
            path_item.setToolTip(1, "HTTP path")
            self._routes_tree.addTopLevelItem(path_item)

            for route in sorted(by_path[path], key=lambda candidate: candidate.method):
                linked_tests = route_tests(self._test_index, route.id)
                unique_tests = {
                    (test.source.file, test.test_name)
                    for test in linked_tests
                }
                item = QTreeWidgetItem(
                    [
                        route.method,
                        _short_name(route.handler),
                        str(len(unique_tests)),
                    ]
                )
                item.setData(0, _ROLE_ID, route.id)
                self._set_item_source(item, route.source)
                item.setToolTip(1, route.handler)
                if linked_tests:
                    item.setToolTip(
                        2,
                        "\n".join(test.test_name for test in linked_tests),
                    )
                path_item.addChild(item)
                if first_route_item is None:
                    first_route_item = item

            path_item.setExpanded(True)

        self._routes_tree.resizeColumnToContents(0)
        self._routes_tree.resizeColumnToContents(1)
        self._routes_tree.resizeColumnToContents(2)
        if first_route_item is not None:
            self._routes_tree.setCurrentItem(first_route_item)

    def _route_selected(self, item: QTreeWidgetItem | None, _previous) -> None:
        if item is None or self._api_map is None:
            self._route_details.clear()
            return

        route_id = item.data(0, _ROLE_ID)
        route = next(
            (candidate for candidate in self._api_map.routes if candidate.id == route_id),
            None,
        )
        if route is None:
            self._route_details.clear()
            return

        dependencies_by_id = {
            dependency.id: dependency for dependency in self._api_map.dependencies
        }
        dependency_names = [
            dependencies_by_id[dep_id].name
            for dep_id in route.dependencies
            if dep_id in dependencies_by_id
        ]

        params = "\n".join(
            f"  {parameter.location:6} "
            f"{parameter.alias or parameter.name}"
            + (
                f" [python: {parameter.name}]"
                if parameter.alias and parameter.alias != parameter.name
                else ""
            )
            + f": {parameter.type_name}"
            + (" (required)" if parameter.required else "")
            for parameter in route.parameters
        ) or "  -"

        deps = "\n".join(f"  {_short_name(name)}" for name in dependency_names) or "  -"
        linked_tests = route_tests(self._test_index, route.id)
        unique_route_tests: dict[tuple[str | None, str], str] = {}
        for test in linked_tests:
            key = (test.source.file, test.test_name)
            unique_route_tests.setdefault(
                key,
                f"  {test.test_name} [{test.match_kind}]",
            )
        tests_text = "\n".join(unique_route_tests.values()) or "  -"

        self._route_details.setPlainText(
            f"{route.id}\n\n"
            f"Handler\n  {route.handler}\n"
            f"  {_source_text(route.source)}\n\n"
            f"Status\n  {route.status_code or 'default'}\n\n"
            f"Tags\n  {', '.join(route.tags) or '-'}\n\n"
            f"Parameters\n{params}\n\n"
            f"Request models\n  {', '.join(route.request_models) or '-'}\n\n"
            f"Dependencies\n{deps}\n\n"
            f"Tests\n{tests_text}\n\n"
            f"Response model\n  {route.response_model or '-'}"
        )

        self._lineage_route.setCurrentText(route.id)
        self._diagram.select_route(route.id)
        self._request_lab.select_route(route.id)

    def _populate_models(self) -> None:
        self._models_tree.clear()
        if self._api_map is None:
            return

        for model in self._api_map.models:
            routes = impacted_routes(self._api_map, f"model:{model.name}")
            item = QTreeWidgetItem([model.name, str(len(routes))])
            item.setData(0, _ROLE_ID, model.name)
            self._set_item_source(item, model.source)
            item.setToolTip(0, _source_text(model.source))
            item.setToolTip(1, "\n".join(routes))
            self._models_tree.addTopLevelItem(item)

        self._models_tree.resizeColumnToContents(1)
        if self._models_tree.topLevelItemCount():
            self._models_tree.setCurrentItem(self._models_tree.topLevelItem(0))

    def _model_selected(self, item: QTreeWidgetItem | None, _previous) -> None:
        if item is None or self._api_map is None:
            self._model_details.clear()
            return

        model_name = item.data(0, _ROLE_ID)
        model = next(
            (candidate for candidate in self._api_map.models if candidate.name == model_name),
            None,
        )
        if model is None:
            return

        routes = impacted_routes(self._api_map, f"model:{model.name}")
        route_text = "\n".join(f"  {route}" for route in routes) or "  -"
        schema = json.dumps(model.schema_, indent=2, sort_keys=True)
        self._model_details.setPlainText(
            f"{model.name}\n"
            f"{_source_text(model.source)}\n\n"
            f"Blast radius ({len(routes)} route(s))\n{route_text}\n\n"
            f"OpenAPI schema\n{schema}"
        )
        self._diagram.focus_node(f"model:{model.name}")

    def _populate_dependencies(self) -> None:
        self._dependencies_tree.clear()
        if self._api_map is None:
            return

        for dependency in self._api_map.dependencies:
            routes = impacted_routes(self._api_map, dependency.id)
            item = QTreeWidgetItem([_short_name(dependency.name), str(len(routes))])
            item.setData(0, _ROLE_ID, dependency.id)
            self._set_item_source(item, dependency.source)
            item.setToolTip(0, dependency.name)
            item.setToolTip(1, "\n".join(routes))
            self._dependencies_tree.addTopLevelItem(item)

        self._dependencies_tree.resizeColumnToContents(1)
        if self._dependencies_tree.topLevelItemCount():
            self._dependencies_tree.setCurrentItem(
                self._dependencies_tree.topLevelItem(0)
            )

    def _dependency_selected(self, item: QTreeWidgetItem | None, _previous) -> None:
        if item is None or self._api_map is None:
            self._dependency_details.clear()
            return

        dependency_id = item.data(0, _ROLE_ID)
        dependency = next(
            (
                candidate
                for candidate in self._api_map.dependencies
                if candidate.id == dependency_id
            ),
            None,
        )
        if dependency is None:
            return

        dependencies_by_id = {
            candidate.id: candidate for candidate in self._api_map.dependencies
        }
        children = [
            dependencies_by_id[child].name
            for child in dependency.children
            if child in dependencies_by_id
        ]
        params = "\n".join(
            f"  {parameter.location:6} "
            f"{parameter.alias or parameter.name}"
            + (
                f" [python: {parameter.name}]"
                if parameter.alias and parameter.alias != parameter.name
                else ""
            )
            + f": {parameter.type_name}"
            for parameter in dependency.parameters
        ) or "  -"
        routes = impacted_routes(self._api_map, dependency.id)
        route_text = "\n".join(f"  {route}" for route in routes) or "  -"

        self._dependency_details.setPlainText(
            f"{dependency.name}\n"
            f"{_source_text(dependency.source)}\n\n"
            f"Cache per request\n  {dependency.use_cache}\n\n"
            f"Scope\n  {dependency.scope or '-'}\n\n"
            f"Request parameters\n{params}\n\n"
            f"Nested dependencies\n"
            + ("\n".join(f"  {_short_name(child)}" for child in children) or "  -")
            + f"\n\nBlast radius ({len(routes)} route(s))\n{route_text}"
        )
        self._diagram.focus_node(dependency.id)

    def _populate_lineage_routes(self) -> None:
        self._lineage_route.blockSignals(True)
        self._lineage_route.clear()
        if self._api_map is not None:
            self._lineage_route.addItems([route.id for route in self._api_map.routes])
        self._lineage_route.blockSignals(False)

        if self._lineage_route.count():
            self._lineage_route.setCurrentIndex(0)
            self._populate_lineage_for_route(self._lineage_route.currentText())
        else:
            self._lineage_tree.clear()

    def _populate_lineage_for_route(self, route_id: str) -> None:
        self._lineage_tree.clear()
        if self._api_map is None or not route_id:
            return

        nodes = {node.id: node for node in self._api_map.lineage.nodes}
        adjacency = defaultdict(list)
        for edge in self._api_map.lineage.edges:
            adjacency[edge.source].append(edge)

        root_id = f"route:{route_id}"
        root = nodes.get(root_id)
        if root is None:
            return

        root_item = QTreeWidgetItem(["route", root.label, root.kind])
        self._set_item_source(root_item, root.source)
        self._lineage_tree.addTopLevelItem(root_item)

        def add_children(parent_item, node_id: str, path: set[str]) -> None:
            edges = sorted(
                adjacency.get(node_id, []),
                key=lambda edge: (
                    edge.relation,
                    nodes.get(edge.target).label if nodes.get(edge.target) else edge.target,
                ),
            )
            for edge in edges:
                node = nodes.get(edge.target)
                if node is None:
                    continue
                child = QTreeWidgetItem([edge.relation, node.label, node.kind])
                child.setData(0, _ROLE_ID, node.id)
                self._set_item_source(child, node.source)
                parent_item.addChild(child)
                if node.id not in path:
                    add_children(child, node.id, path | {node.id})

        add_children(root_item, root_id, {root_id})
        root_item.setExpanded(True)
        self._lineage_tree.expandToDepth(2)
        self._lineage_tree.resizeColumnToContents(0)
        self._lineage_tree.resizeColumnToContents(2)

    # --- Filtering
    # ------------------------------------------------------------------
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()

        for index in range(self._routes_tree.topLevelItemCount()):
            path_item = self._routes_tree.topLevelItem(index)
            path_matches = not needle or needle in path_item.text(1).casefold()
            visible_children = False

            for child_index in range(path_item.childCount()):
                child = path_item.child(child_index)
                route_id = str(child.data(0, _ROLE_ID) or "")
                haystack = " ".join(
                    [child.text(0), child.text(1), route_id]
                ).casefold()
                visible = path_matches or not needle or needle in haystack
                child.setHidden(not visible)
                visible_children = visible_children or visible

            path_item.setHidden(bool(needle) and not (path_matches or visible_children))

        for tree in (
            self._models_tree,
            self._dependencies_tree,
            self._tests_tree,
        ):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                haystack = " ".join(
                    item.text(column)
                    for column in range(item.columnCount())
                ).casefold()
                item.setHidden(bool(needle) and needle not in haystack)

    # --- FastAPI native OpenTelemetry capture
    # ------------------------------------------------------------------
    def prepare_native_telemetry_capture(self) -> str:
        """Create a fresh local JSONL sink for the next debug server."""

        self._native_telemetry_timer.stop()
        self._cleanup_native_telemetry_file()

        directory = Path(tempfile.gettempdir()) / "fastapi-spyder"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"telemetry-{uuid4().hex}.jsonl"
        path.write_bytes(b"")

        self._native_telemetry = NativeTelemetryStore()
        self._native_telemetry_path = path
        self._native_telemetry_offset = 0
        self._native_telemetry_partial = b""
        self._telemetry.set_store(self._native_telemetry)
        self._diagram.set_native_telemetry(self._native_telemetry)
        self._native_telemetry_timer.start()
        return str(path)

    def clear_native_telemetry(self) -> None:
        """Clear the dashboard while keeping the live capture attached."""

        self._native_telemetry.clear()
        path = self._native_telemetry_path
        if path is not None and path.exists():
            try:
                self._native_telemetry_offset = path.stat().st_size
            except OSError:
                self._native_telemetry_offset = 0
        else:
            self._native_telemetry_offset = 0
        self._native_telemetry_partial = b""
        self._telemetry.set_store(self._native_telemetry)
        self._diagram.set_native_telemetry(self._native_telemetry)

    def _cleanup_native_telemetry_file(self) -> None:
        path = self._native_telemetry_path
        self._native_telemetry_path = None
        self._native_telemetry_offset = 0
        self._native_telemetry_partial = b""
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _poll_native_telemetry(self) -> None:
        path = self._native_telemetry_path
        if path is None or not path.exists():
            return

        try:
            with path.open("rb") as handle:
                handle.seek(self._native_telemetry_offset)
                chunk = handle.read()
                self._native_telemetry_offset = handle.tell()
        except OSError:
            return

        if not chunk:
            return

        payload = self._native_telemetry_partial + chunk
        lines = payload.split(b"\n")
        self._native_telemetry_partial = lines.pop()

        changed = False
        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._native_telemetry.invalid_lines += 1
                changed = True
                continue
            if isinstance(event, dict):
                changed = self._native_telemetry.ingest(event) or changed
            else:
                self._native_telemetry.invalid_lines += 1
                changed = True

        if changed:
            self._telemetry.set_store(self._native_telemetry)
            self._diagram.set_native_telemetry(self._native_telemetry)

    def _telemetry_route_selected(self, route_id: str) -> None:
        self._diagram.select_route(route_id)
        diagram_index = self._tabs.indexOf(self._diagram)
        if diagram_index >= 0:
            self._tabs.setCurrentIndex(diagram_index)

    def _open_telemetry_route(
        self,
        route_id: str,
        trace_id: str = "",
    ) -> None:
        """Drill from observed Diagram evidence into its captured trace."""

        found = (
            self._telemetry.select_trace(trace_id)
            if trace_id
            else False
        )
        if not found:
            found = self._telemetry.select_route(route_id)

        telemetry_index = self._tabs.indexOf(self._telemetry)
        if telemetry_index >= 0:
            self._tabs.setCurrentIndex(telemetry_index)

        self._status.setText(
            (
                f"Opened runtime evidence trace for {route_id}."
                if found and trace_id
                else (
                    f"Opened latest native telemetry for {route_id}."
                    if found
                    else f"No native telemetry trace captured yet for {route_id}."
                )
            )
        )

    def _telemetry_function_selected(self, function_name: str) -> None:
        """Open source for an observed FastAPI operation when resolvable."""

        api_map = self._api_map
        if api_map is None:
            return

        candidates: list[SourceRef] = []
        for route in api_map.routes:
            if route.handler == function_name:
                candidates.append(route.source)
        for dependency in api_map.dependencies:
            if dependency.name == function_name:
                candidates.append(dependency.source)

        if not candidates:
            suffix_matches: list[SourceRef] = []
            short_name = function_name.rsplit(".", 1)[-1]
            for route in api_map.routes:
                if route.handler.rsplit(".", 1)[-1] == short_name:
                    suffix_matches.append(route.source)
            for dependency in api_map.dependencies:
                if dependency.name.rsplit(".", 1)[-1] == short_name:
                    suffix_matches.append(dependency.source)
            if len(suffix_matches) == 1:
                candidates = suffix_matches

        source = next(
            (
                candidate
                for candidate in candidates
                if candidate.file
            ),
            None,
        )
        if source is None:
            self._status.setText(
                f"No unique source mapping for telemetry function {function_name}."
            )
            return

        self.sig_open_source.emit(
            source.file,
            int(source.execution_line or source.line or 1),
        )

    # --- Runtime evidence
    # ------------------------------------------------------------------
    def _request_completed(
        self,
        route_id: str,
        result: RequestExecution,
    ) -> None:
        record_route_execution(
            self._runtime_evidence,
            route_id,
            result,
        )
        self._diagram.set_runtime_evidence(self._runtime_evidence)

    def _clear_runtime_evidence(self) -> None:
        clear_runtime_evidence(self._runtime_evidence)
        self._diagram.set_runtime_evidence(self._runtime_evidence)
        self._status.setText(
            "Cleared observed Request Lab runtime evidence."
        )

    # --- Route-to-test links
    # ------------------------------------------------------------------
    def _scan_test_links(self) -> None:
        if self._api_map is None:
            self._test_index = RouteTestIndex()
            return

        self._test_index = discover_route_tests(
            self._workdir,
            self._api_map,
        )

    def refresh_test_links(self) -> None:
        if self._api_map is None:
            self._status.setText("Inspect a FastAPI application before scanning tests.")
            return

        self._status.setText("Scanning project tests for FastAPI route calls...")
        self._scan_test_links()
        self._populate_test_links()
        self._populate_routes()
        self._diagram.set_test_index(self._test_index)
        self._status.setText(
            f"Linked {len(self._test_index.references)} test call(s) "
            f"from {self._test_index.scanned_files} test file(s)."
        )

    def _populate_test_links(self) -> None:
        self._tests_tree.clear()

        linked_routes = {
            reference.route_id
            for reference in self._test_index.references
        }
        self._tests_summary.setText(
            f"{len(self._test_index.references)} linked HTTP test call(s) / "
            f"{len(linked_routes)} route(s) / "
            f"{self._test_index.scanned_files} scanned test file(s). "
            "Links are static evidence from literal or f-string client calls."
        )

        for reference in self._test_index.references:
            source = reference.source
            source_text = _source_text(source)
            item = QTreeWidgetItem(
                [
                    reference.route_id,
                    reference.test_name,
                    reference.match_kind,
                    source_text,
                ]
            )
            item.setData(0, _ROLE_ID, reference.route_id)
            self._set_item_source(item, source)
            item.setToolTip(0, reference.requested_path)
            self._tests_tree.addTopLevelItem(item)

        for column in range(self._tests_tree.columnCount()):
            self._tests_tree.resizeColumnToContents(column)

    def _test_selected(self, item: QTreeWidgetItem | None, _previous) -> None:
        if item is None:
            return
        route_id = item.data(0, _ROLE_ID)
        if not route_id:
            return
        self._diagram.select_route(str(route_id))
        self._diagram.set_show_tests(True)

    # --- Source navigation
    # ------------------------------------------------------------------
    @staticmethod
    def _set_item_source(item: QTreeWidgetItem, source: SourceRef | None) -> None:
        if source is None or not source.file:
            return
        item.setData(0, _ROLE_SOURCE_FILE, source.file)
        item.setData(0, _ROLE_SOURCE_LINE, source.line or 1)

    def _open_tree_item_source(
        self,
        item: QTreeWidgetItem | None,
        _column: int = 0,
    ) -> None:
        if item is None:
            return
        filename = item.data(0, _ROLE_SOURCE_FILE)
        line = item.data(0, _ROLE_SOURCE_LINE)
        if filename:
            self.sig_open_source.emit(str(filename), int(line or 1))
