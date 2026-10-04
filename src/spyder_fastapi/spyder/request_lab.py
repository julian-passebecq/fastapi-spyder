"""Request Lab Qt widget for generated FastAPI requests and 422 diagnostics."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from qtpy.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, Signal
from qtpy.QtNetwork import QAbstractSocket, QTcpSocket
from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spyder_fastapi.core import (
    build_request_template,
    local_debug_server_address,
    validation_issues,
)
from spyder_fastapi.models import (
    FastAPIMap,
    RequestExecution,
    RequestTemplate,
    SourceRef,
)


_ROLE_SOURCE_FILE = Qt.UserRole + 1
_ROLE_SOURCE_LINE = Qt.UserRole + 2
_ROLE_REQUIRED = Qt.UserRole + 3
_ROLE_HISTORY_INDEX = Qt.UserRole + 4
_ROLE_IS_FILE = Qt.UserRole + 5
_ROLE_MULTIPLE = Qt.UserRole + 6


def _source_text(source: SourceRef | None) -> str:
    if source is None or not source.file:
        return "source unavailable"
    if source.line:
        return f"{source.file}:{source.line}"
    return source.file


def _value_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class RequestLabWidget(QWidget):
    """Build and execute HTTP requests from the inspected FastAPI contract."""

    sig_status = Signal(str)
    sig_open_source = Signal(str, int)
    sig_set_breakpoint = Signal(str, int)
    sig_start_debug_server = Signal(str, str, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._api_map: FastAPIMap | None = None
        self._template: RequestTemplate | None = None
        self._body_source: SourceRef | None = None
        self._handler_source: SourceRef | None = None
        self._python_executable = sys.executable
        self._workdir = os.getcwd()
        self._app_target: str | None = None
        self._debug_server_available = False
        self._process: QProcess | None = None
        self._stdout_chunks: list[str] = []
        self._stderr_chunks: list[str] = []
        self._pending_payload: bytes | None = None
        self._active_command: dict | None = None
        self._active_route_id: str | None = None
        self._history: list[dict] = []
        self._debug_probe_attempts = 0
        self._debug_replay_command: dict | None = None
        self._debug_replay_route_id: str | None = None

        self._debug_probe = QTcpSocket(self)
        self._debug_probe.connected.connect(self._debug_server_ready)
        self._debug_probe.errorOccurred.connect(self._debug_probe_error)

        self._debug_probe_timer = QTimer(self)
        self._debug_probe_timer.setInterval(250)
        self._debug_probe_timer.timeout.connect(self._probe_debug_server)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        route_row = QHBoxLayout()
        route_row.addWidget(QLabel("Route"))
        self._route = QComboBox()
        self._route.currentTextChanged.connect(self._route_changed)
        route_row.addWidget(self._route, 1)

        route_row.addWidget(QLabel("Base URL"))
        self._base_url = QLineEdit("http://127.0.0.1:8000")
        self._base_url.setPlaceholderText("http://127.0.0.1:8000")
        route_row.addWidget(self._base_url, 1)

        self._breakpoint = QPushButton("Set handler breakpoint")
        self._breakpoint.setEnabled(False)
        self._breakpoint.clicked.connect(self._set_handler_breakpoint)
        route_row.addWidget(self._breakpoint)

        self._debug_server = QPushButton("Debug selected route")
        self._debug_server.setEnabled(False)
        self._debug_server.setToolTip(
            "Set the selected handler breakpoint when source is available, "
            "then start this FastAPI target through Spyder's native debugfile workflow."
        )
        self._debug_server.clicked.connect(self._start_debug_server)
        route_row.addWidget(self._debug_server)

        self._cancel_debug_wait = QPushButton("Cancel debug replay")
        self._cancel_debug_wait.setEnabled(False)
        self._cancel_debug_wait.clicked.connect(self._cancel_debug_replay)
        route_row.addWidget(self._cancel_debug_wait)

        self._send = QPushButton("Send")
        self._send.setEnabled(False)
        self._send.clicked.connect(self.send_request)
        route_row.addWidget(self._send)
        root.addLayout(route_row)

        input_splitter = QSplitter(Qt.Vertical)

        parameters_page = QWidget()
        parameters_layout = QVBoxLayout(parameters_page)
        parameters_layout.setContentsMargins(0, 0, 0, 0)
        parameters_layout.addWidget(QLabel("Path / query / header / cookie inputs"))

        self._parameters = QTableWidget(0, 5)
        self._parameters.setHorizontalHeaderLabels(
            ["Location", "Name", "Type", "Required", "Value"]
        )
        self._parameters.verticalHeader().setVisible(False)
        self._parameters.cellDoubleClicked.connect(self._open_parameter_source)
        self._parameters.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._parameters.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self._parameters.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._parameters.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self._parameters.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.Stretch
        )
        parameters_layout.addWidget(self._parameters)
        input_splitter.addWidget(parameters_page)

        body_page = QWidget()
        body_layout = QVBoxLayout(body_page)
        body_layout.setContentsMargins(0, 0, 0, 0)

        body_header = QHBoxLayout()
        self._body_label = QLabel("JSON body")
        body_header.addWidget(self._body_label)
        body_header.addStretch(1)

        self._reset_body = QPushButton("Reset example")
        self._reset_body.clicked.connect(self._reset_body_example)
        body_header.addWidget(self._reset_body)

        self._open_body_source = QPushButton("Open model source")
        self._open_body_source.clicked.connect(self._open_current_body_source)
        body_header.addWidget(self._open_body_source)
        body_layout.addLayout(body_header)

        self._body = QPlainTextEdit()
        self._body.setPlaceholderText("No JSON request body for this route.")
        body_layout.addWidget(self._body)

        self._body_fields = QTableWidget(0, 5)
        self._body_fields.setHorizontalHeaderLabels(
            ["Kind", "Name", "Type", "Required", "Value / file path"]
        )
        self._body_fields.verticalHeader().setVisible(False)
        self._body_fields.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._body_fields.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self._body_fields.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._body_fields.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self._body_fields.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.Stretch
        )
        self._body_fields.hide()
        body_layout.addWidget(self._body_fields)

        self._browse_body_file = QPushButton("Browse selected file...")
        self._browse_body_file.setEnabled(False)
        self._browse_body_file.clicked.connect(self._browse_selected_body_file)
        self._browse_body_file.hide()
        body_layout.addWidget(self._browse_body_file)

        input_splitter.addWidget(body_page)

        root.addWidget(input_splitter, 2)

        self._result_tabs = QTabWidget()

        response_page = QWidget()
        response_layout = QVBoxLayout(response_page)
        response_layout.setContentsMargins(0, 0, 0, 0)
        self._response_summary = QLabel("No request sent.")
        self._response_summary.setWordWrap(True)
        response_layout.addWidget(self._response_summary)
        self._response = QPlainTextEdit()
        self._response.setReadOnly(True)
        response_layout.addWidget(self._response)
        self._result_tabs.addTab(response_page, "Response")

        validation_page = QWidget()
        validation_layout = QVBoxLayout(validation_page)
        validation_layout.setContentsMargins(0, 0, 0, 0)
        self._validation_summary = QLabel(
            "FastAPI 422 validation issues will be mapped here."
        )
        self._validation_summary.setWordWrap(True)
        validation_layout.addWidget(self._validation_summary)

        self._validation = QTreeWidget()
        self._validation.setHeaderLabels(
            ["Location", "Field", "Expected", "Message", "Type"]
        )
        self._validation.setRootIsDecorated(False)
        self._validation.itemDoubleClicked.connect(self._open_validation_source)
        validation_layout.addWidget(self._validation, 1)
        self._result_tabs.addTab(validation_page, "422 Validation")

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(0, 0, 0, 0)

        history_actions = QHBoxLayout()
        self._replay_history = QPushButton("Replay selected")
        self._replay_history.setEnabled(False)
        self._replay_history.clicked.connect(self._replay_selected_history)
        history_actions.addWidget(self._replay_history)

        self._clear_history_button = QPushButton("Clear history")
        self._clear_history_button.setEnabled(False)
        self._clear_history_button.clicked.connect(self._clear_history)
        history_actions.addWidget(self._clear_history_button)
        history_actions.addStretch(1)
        history_layout.addLayout(history_actions)

        history_splitter = QSplitter(Qt.Horizontal)
        self._history_tree = QTreeWidget()
        self._history_tree.setHeaderLabels(
            ["#", "Status", "Route", "Elapsed", "URL"]
        )
        self._history_tree.setRootIsDecorated(False)
        self._history_tree.currentItemChanged.connect(self._history_selected)
        self._history_tree.itemDoubleClicked.connect(self._open_history_source)
        history_splitter.addWidget(self._history_tree)

        self._history_details = QPlainTextEdit()
        self._history_details.setReadOnly(True)
        history_splitter.addWidget(self._history_details)
        history_splitter.setStretchFactor(0, 2)
        history_splitter.setStretchFactor(1, 3)
        history_layout.addWidget(history_splitter, 1)

        self._result_tabs.addTab(history_page, "History")

        root.addWidget(self._result_tabs, 2)

    def shutdown(self) -> None:
        """Stop any isolated request process before Spyder closes."""

        process = self._process
        if process is None:
            return

        try:
            process.finished.disconnect(self._request_finished)
        except (TypeError, RuntimeError):
            pass

        process.kill()
        process.waitForFinished(500)
        process.deleteLater()
        self._process = None
        self._pending_payload = None
        self._active_command = None
        self._active_route_id = None
        self._cancel_debug_replay()

    def set_api_map(self, api_map: FastAPIMap) -> None:
        self._api_map = api_map
        current = self._route.currentText()

        self._route.blockSignals(True)
        self._route.clear()
        self._route.addItems([route.id for route in api_map.routes])
        self._route.blockSignals(False)

        if current and current in {route.id for route in api_map.routes}:
            self._route.setCurrentText(current)
        elif self._route.count():
            self._route.setCurrentIndex(0)

        self._route_changed(self._route.currentText())

    def set_app_target(self, target: str | None) -> None:
        self._app_target = target.strip() if target else None
        self._update_debug_server_enabled()

    def set_debug_server_available(self, available: bool) -> None:
        self._debug_server_available = bool(available)
        self._update_debug_server_enabled()

    def _update_debug_server_enabled(self) -> None:
        self._debug_server.setEnabled(
            self._debug_server_available
            and bool(self._app_target)
            and self._api_map is not None
        )

    def set_python_executable(self, path: str) -> None:
        if path:
            self._python_executable = os.path.abspath(path)

    def set_working_directory(self, path: str) -> None:
        if path:
            self._workdir = os.path.abspath(path)

    def select_route(self, route_id: str) -> None:
        index = self._route.findText(route_id)
        if index >= 0:
            self._route.setCurrentIndex(index)

    def _route_changed(self, route_id: str) -> None:
        self._parameters.setRowCount(0)
        self._body.clear()
        self._body_fields.setRowCount(0)
        self._body_fields.hide()
        self._browse_body_file.hide()
        self._browse_body_file.setEnabled(False)
        self._validation.clear()
        self._response.clear()
        self._response_summary.setText("No request sent.")
        self._validation_summary.setText(
            "FastAPI 422 validation issues will be mapped here."
        )
        self._template = None
        self._body_source = None
        self._handler_source = None
        self._breakpoint.setEnabled(False)

        if self._api_map is None or not route_id:
            self._send.setEnabled(False)
            self._body.setEnabled(False)
            self._reset_body.setEnabled(False)
            self._open_body_source.setEnabled(False)
            return

        try:
            template = build_request_template(self._api_map, route_id)
        except (KeyError, ValueError) as exc:
            self.sig_status.emit(f"Could not build request form: {exc}")
            self._send.setEnabled(False)
            return

        self._template = template
        self._body_source = template.body_source
        route = next(
            (
                candidate
                for candidate in self._api_map.routes
                if candidate.id == route_id
            ),
            None,
        )
        self._handler_source = route.source if route is not None else None
        self._breakpoint.setEnabled(
            self._handler_source is not None
            and bool(self._handler_source.file)
            and bool(
                self._handler_source.execution_line
                or self._handler_source.line
            )
        )
        self._populate_parameter_table(template)

        has_body = (
            template.body_example is not None
            or template.body_required
            or bool(template.body_fields)
        )
        content_type = template.body_content_type
        json_body_supported = (
            content_type is None
            or content_type == "application/json"
            or bool(content_type and content_type.endswith("+json"))
        )
        form_body_supported = content_type in {
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        }

        self._body.setVisible(json_body_supported)
        self._body.setEnabled(has_body and json_body_supported)
        self._body_fields.setVisible(form_body_supported)
        self._browse_body_file.setVisible(content_type == "multipart/form-data")
        self._reset_body.setEnabled(has_body and json_body_supported)
        self._open_body_source.setEnabled(
            self._body_source is not None and bool(self._body_source.file)
        )

        if has_body and json_body_supported:
            media = content_type or "application/json"
            self._body_label.setText(
                f"JSON body [{media}]"
                + (f" - {template.body_model}" if template.body_model else "")
                + (" (required)" if template.body_required else "")
            )
            self._reset_body_example()
        elif has_body and form_body_supported:
            self._body_label.setText(
                f"Form body [{content_type}]"
                + (" (required)" if template.body_required else "")
            )
            self._populate_body_fields(template)
        elif has_body:
            self._body_label.setText(
                f"Body [{content_type}] - media type not supported yet"
            )
            self._body.setVisible(True)
            self._body.setEnabled(False)
            self._body.setPlainText(
                "Request Lab does not support this request body media type yet."
            )
        else:
            self._body_label.setText("Request body - none")
            self._body.setVisible(True)
            self._body.setEnabled(False)
            self._body.clear()

        if has_body and template.body_required and not (
            json_body_supported or form_body_supported
        ):
            self._send.setEnabled(False)
            self.sig_status.emit(
                "Request Lab does not support this request body media type yet: "
                f"{content_type}."
            )
        else:
            self._send.setEnabled(True)

    def _populate_parameter_table(self, template: RequestTemplate) -> None:
        self._parameters.setRowCount(len(template.parameters))

        for row, field in enumerate(template.parameters):
            location_item = QTableWidgetItem(field.location)
            name_item = QTableWidgetItem(field.name)
            type_item = QTableWidgetItem(field.type_name)
            required_item = QTableWidgetItem("yes" if field.required else "no")
            value_item = QTableWidgetItem(_value_text(field.example))

            if field.python_name and field.python_name != field.name:
                name_item.setToolTip(
                    f"Python parameter: {field.python_name}"
                )

            for item in (location_item, name_item, type_item, required_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            location_item.setData(_ROLE_REQUIRED, field.required)
            if field.source is not None and field.source.file:
                location_item.setData(_ROLE_SOURCE_FILE, field.source.file)
                location_item.setData(_ROLE_SOURCE_LINE, field.source.line or 1)

            if field.description:
                value_item.setToolTip(field.description)

            self._parameters.setItem(row, 0, location_item)
            self._parameters.setItem(row, 1, name_item)
            self._parameters.setItem(row, 2, type_item)
            self._parameters.setItem(row, 3, required_item)
            self._parameters.setItem(row, 4, value_item)

    def _populate_body_fields(self, template: RequestTemplate) -> None:
        self._body_fields.setRowCount(len(template.body_fields))
        has_file = False

        for row, field in enumerate(template.body_fields):
            kind_item = QTableWidgetItem("file" if field.is_file else "field")
            name_item = QTableWidgetItem(field.name)
            type_item = QTableWidgetItem(field.type_name)
            required_item = QTableWidgetItem("yes" if field.required else "no")
            value_item = QTableWidgetItem(_value_text(field.example))

            for item in (kind_item, name_item, type_item, required_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

            kind_item.setData(_ROLE_REQUIRED, field.required)
            kind_item.setData(_ROLE_IS_FILE, field.is_file)
            kind_item.setData(_ROLE_MULTIPLE, field.multiple)
            if field.source is not None and field.source.file:
                kind_item.setData(_ROLE_SOURCE_FILE, field.source.file)
                kind_item.setData(_ROLE_SOURCE_LINE, field.source.line or 1)

            if field.description:
                value_item.setToolTip(field.description)
            if field.is_file:
                has_file = True
                value_item.setToolTip(
                    "Enter a local file path or select this row and use Browse."
                    + (
                        " For multiple files, use one path per line."
                        if field.multiple
                        else ""
                    )
                )

            self._body_fields.setItem(row, 0, kind_item)
            self._body_fields.setItem(row, 1, name_item)
            self._body_fields.setItem(row, 2, type_item)
            self._body_fields.setItem(row, 3, required_item)
            self._body_fields.setItem(row, 4, value_item)

        self._browse_body_file.setEnabled(has_file)

    def _browse_selected_body_file(self) -> None:
        row = self._body_fields.currentRow()
        if row < 0:
            self.sig_status.emit("Select a multipart file row first.")
            return

        kind_item = self._body_fields.item(row, 0)
        if kind_item is None or not bool(kind_item.data(_ROLE_IS_FILE)):
            self.sig_status.emit("The selected multipart row is not a file field.")
            return

        multiple = bool(kind_item.data(_ROLE_MULTIPLE))
        if multiple:
            paths, _selected_filter = QFileDialog.getOpenFileNames(
                self,
                "Select multipart upload files",
                self._workdir,
            )
            if not paths:
                return
            value = "\n".join(paths)
        else:
            path, _selected_filter = QFileDialog.getOpenFileName(
                self,
                "Select multipart upload file",
                self._workdir,
            )
            if not path:
                return
            value = path

        value_item = self._body_fields.item(row, 4)
        if value_item is None:
            value_item = QTableWidgetItem()
            self._body_fields.setItem(row, 4, value_item)
        value_item.setText(value)

    def _reset_body_example(self) -> None:
        if self._template is None or self._template.body_example is None:
            self._body.clear()
            return
        self._body.setPlainText(
            json.dumps(
                self._template.body_example,
                indent=2,
                ensure_ascii=False,
            )
        )

    def _start_debug_server(self) -> None:
        if not self._debug_server_available:
            self.sig_status.emit(
                "Spyder IPython Console is not available for debug-server launch."
            )
            return
        if not self._app_target:
            self.sig_status.emit(
                "Inspect a concrete module:attribute FastAPI target first."
            )
            return

        try:
            host, port = local_debug_server_address(self._base_url.text())
            replay_command = self._collect_command()
        except ValueError as exc:
            self.sig_status.emit(str(exc))
            return

        if self._handler_source is not None and self._handler_source.file:
            line = (
                self._handler_source.execution_line
                or self._handler_source.line
            )
            if line is not None:
                self.sig_set_breakpoint.emit(
                    self._handler_source.file,
                    int(line),
                )

        self._cancel_debug_replay(silent=True)
        self._debug_replay_command = replay_command
        self._debug_replay_route_id = (
            self._template.route_id if self._template is not None else None
        )
        self._debug_probe_attempts = 0
        self._cancel_debug_wait.setEnabled(True)

        self.sig_start_debug_server.emit(
            self._app_target,
            self._workdir,
            host,
            port,
        )

        self.sig_status.emit(
            f"Debug launch sent. Waiting for http://{host}:{port} "
            "before replaying the selected request..."
        )
        self._debug_probe.setProperty("host", host)
        self._debug_probe.setProperty("port", int(port))
        self._debug_probe_timer.start()
        self._probe_debug_server()

    def _probe_debug_server(self) -> None:
        if self._debug_replay_command is None:
            self._debug_probe_timer.stop()
            return

        if self._debug_probe.state() != QAbstractSocket.UnconnectedState:
            self._debug_probe.abort()

        self._debug_probe_attempts += 1
        if self._debug_probe_attempts > 120:
            self._cancel_debug_replay(silent=True)
            self.sig_status.emit(
                "Timed out waiting 30 seconds for the FastAPI debug server. "
                "Continue the Spyder debugger if it is paused, then try again."
            )
            return

        host = str(self._debug_probe.property("host") or "")
        port = int(self._debug_probe.property("port") or 0)
        if not host or not port:
            self._cancel_debug_replay(silent=True)
            self.sig_status.emit("Debug readiness probe has no valid host/port.")
            return

        self._debug_probe.connectToHost(host, port)

    def _debug_probe_error(self, _error) -> None:
        if self._debug_replay_command is None:
            return
        self._debug_probe.abort()

    def _debug_server_ready(self) -> None:
        if self._debug_replay_command is None:
            self._debug_probe.abort()
            return

        command = json.loads(json.dumps(self._debug_replay_command))
        route_id = self._debug_replay_route_id
        self._debug_probe_timer.stop()
        self._debug_probe.abort()
        self._debug_replay_command = None
        self._debug_replay_route_id = None
        self._cancel_debug_wait.setEnabled(False)

        self.sig_status.emit(
            "FastAPI debug server is listening. Replaying the selected request."
        )
        self._start_request(command, route_id)

    def _cancel_debug_replay(self, _checked=False, *, silent: bool = False) -> None:
        was_waiting = self._debug_replay_command is not None
        self._debug_probe_timer.stop()
        self._debug_probe.abort()
        self._debug_replay_command = None
        self._debug_replay_route_id = None
        self._debug_probe_attempts = 0
        self._cancel_debug_wait.setEnabled(False)

        if was_waiting and not silent:
            self.sig_status.emit("Cancelled debug-server readiness wait and replay.")

    def _set_handler_breakpoint(self) -> None:
        source = self._handler_source
        if source is None or not source.file:
            return

        line = source.execution_line or source.line
        if line is None:
            return

        self.sig_set_breakpoint.emit(source.file, int(line))

    def _open_parameter_source(self, row: int, _column: int) -> None:
        item = self._parameters.item(row, 0)
        if item is None:
            return
        filename = item.data(_ROLE_SOURCE_FILE)
        line = item.data(_ROLE_SOURCE_LINE)
        if filename:
            self.sig_open_source.emit(str(filename), int(line or 1))

    def _open_current_body_source(self) -> None:
        if self._body_source is None or not self._body_source.file:
            return
        self.sig_open_source.emit(
            self._body_source.file,
            int(self._body_source.line or 1),
        )

    def _collect_command(self) -> dict:
        if self._template is None:
            raise ValueError("Select a FastAPI route first.")

        base_url = self._base_url.text().strip()
        if not base_url:
            raise ValueError("Base URL is required.")

        path_params: dict[str, str] = {}
        query: dict[str, str] = {}
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}

        buckets = {
            "path": path_params,
            "query": query,
            "header": headers,
            "cookie": cookies,
        }

        for row in range(self._parameters.rowCount()):
            location_item = self._parameters.item(row, 0)
            name_item = self._parameters.item(row, 1)
            value_item = self._parameters.item(row, 4)
            if location_item is None or name_item is None:
                continue

            location = location_item.text()
            name = name_item.text()
            value = value_item.text().strip() if value_item is not None else ""
            required = bool(location_item.data(_ROLE_REQUIRED))

            if not value:
                if required:
                    raise ValueError(
                        f"Required {location} parameter is empty: {name}"
                    )
                continue

            bucket = buckets.get(location)
            if bucket is not None:
                bucket[name] = value

        command = {
            "method": self._template.method,
            "base_url": base_url,
            "path": self._template.path,
            "path_params": path_params,
            "query": query,
            "headers": headers,
            "cookies": cookies,
            "timeout": 10.0,
        }

        content_type = self._template.body_content_type
        json_body_supported = (
            content_type is None
            or content_type == "application/json"
            or bool(content_type and content_type.endswith("+json"))
        )

        if self._body.isEnabled() and json_body_supported:
            body_text = self._body.toPlainText().strip()
            if not body_text:
                if self._template.body_required:
                    raise ValueError("JSON request body is required.")
            else:
                try:
                    command["body"] = json.loads(body_text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Request body is not valid JSON: {exc.msg} "
                        f"(line {exc.lineno}, column {exc.colno})"
                    ) from exc

        elif content_type in {
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        }:
            values: dict[str, str] = {}
            files: dict[str, str] = {}

            for row in range(self._body_fields.rowCount()):
                kind_item = self._body_fields.item(row, 0)
                name_item = self._body_fields.item(row, 1)
                value_item = self._body_fields.item(row, 4)
                if kind_item is None or name_item is None:
                    continue

                name = name_item.text()
                value = value_item.text().strip() if value_item is not None else ""
                required = bool(kind_item.data(_ROLE_REQUIRED))
                is_file = bool(kind_item.data(_ROLE_IS_FILE))
                multiple = bool(kind_item.data(_ROLE_MULTIPLE))

                if not value:
                    if required:
                        label = "file" if is_file else "form field"
                        raise ValueError(f"Required {label} is empty: {name}")
                    continue

                if is_file:
                    if multiple:
                        paths = [
                            item.strip()
                            for item in value.splitlines()
                            if item.strip()
                        ]
                        if required and not paths:
                            raise ValueError(
                                f"Required file list is empty: {name}"
                            )
                        files[name] = paths
                    else:
                        files[name] = value
                else:
                    values[name] = value

            if content_type == "application/x-www-form-urlencoded":
                command["form"] = values
            else:
                command["multipart"] = values
                command["files"] = files

        return command

    def send_request(self) -> None:
        if self._process is not None:
            self.sig_status.emit("A Request Lab request is already running.")
            return

        try:
            command = self._collect_command()
        except ValueError as exc:
            self.sig_status.emit(str(exc))
            return

        route_id = self._template.route_id if self._template is not None else None
        self._start_request(command, route_id)

    def _start_request(self, command: dict, route_id: str | None) -> None:
        if self._process is not None:
            self.sig_status.emit("A Request Lab request is already running.")
            return

        self._stdout_chunks = []
        self._stderr_chunks = []
        self._active_command = json.loads(json.dumps(command))
        self._active_route_id = route_id
        self._pending_payload = json.dumps(command).encode("utf-8")
        self._response.clear()
        self._validation.clear()
        self._response_summary.setText("Sending request...")
        self._validation_summary.setText("Waiting for response...")
        self._send.setEnabled(False)
        self._replay_history.setEnabled(False)

        process = QProcess(self)
        process.setWorkingDirectory(self._workdir)
        process.setProcessEnvironment(self._process_environment())
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.started.connect(self._write_payload)
        process.errorOccurred.connect(self._request_process_error)
        process.finished.connect(self._request_finished)
        self._process = process

        process.start(
            self._python_executable,
            ["-m", "spyder_fastapi.request_cli"],
        )

    def _process_environment(self) -> QProcessEnvironment:
        environment = QProcessEnvironment.systemEnvironment()
        package_root = str(Path(__file__).resolve().parents[2])
        current_pythonpath = environment.value("PYTHONPATH")
        pythonpath_parts = [package_root]
        if current_pythonpath:
            pythonpath_parts.append(current_pythonpath)
        environment.insert("PYTHONPATH", os.pathsep.join(pythonpath_parts))
        return environment

    def _write_payload(self) -> None:
        if self._process is None or self._pending_payload is None:
            return
        self._process.write(self._pending_payload)
        self._process.closeWriteChannel()

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
        self._stderr_chunks.append(
            bytes(self._process.readAllStandardError()).decode(
                "utf-8", errors="replace"
            )
        )

    def _request_process_error(self, error) -> None:
        process = self._process
        if process is None:
            return

        message = process.errorString() or str(error)
        failed_to_start = getattr(QProcess, "FailedToStart", None)
        if failed_to_start is None:
            process_error = getattr(QProcess, "ProcessError", None)
            failed_to_start = (
                getattr(process_error, "FailedToStart", None)
                if process_error is not None
                else None
            )

        if failed_to_start is not None and error == failed_to_start:
            self._response_summary.setText(
                f"Request runner failed to start: {message}"
            )
            self._validation_summary.setText("No HTTP response received.")
            self._send.setEnabled(self._template is not None)
            self._pending_payload = None
            self._active_command = None
            self._active_route_id = None
            process.deleteLater()
            self._process = None

        self.sig_status.emit(f"Request Lab process error: {message}")

    def _request_finished(self, _exit_code: int, _exit_status) -> None:
        process = self._process
        self._send.setEnabled(self._template is not None)

        try:
            stdout = "".join(self._stdout_chunks).strip()
            stderr = "".join(self._stderr_chunks).strip()

            try:
                result = RequestExecution.model_validate_json(stdout)
            except ValueError as exc:
                self._response_summary.setText(
                    "Request runner returned invalid JSON."
                )
                self._response.setPlainText(
                    f"{stderr}\n\n{stdout}\n\nParse error: {exc}".strip()
                )
                self.sig_status.emit("Request Lab runner returned invalid JSON.")
                return

            self._render_result(result, route_id=self._active_route_id)
            self._record_history(
                result,
                command=self._active_command,
                route_id=self._active_route_id,
            )
            if stderr:
                self.sig_status.emit(
                    "Request completed with diagnostics; see Response."
                )
        finally:
            self._pending_payload = None
            self._active_command = None
            self._active_route_id = None
            if process is not None:
                process.deleteLater()
            self._process = None
            self._update_replay_enabled()

    def _render_result(
        self,
        result: RequestExecution,
        *,
        route_id: str | None = None,
    ) -> None:
        if result.error:
            self._response_summary.setText(f"Request failed: {result.error}")
            self._response.setPlainText(
                f"URL: {result.url or '-'}\nError: {result.error}"
            )
            self._validation_summary.setText("No HTTP response received.")
            self.sig_status.emit(f"Request failed: {result.error}")
            return

        elapsed = (
            f"{result.elapsed_ms:.1f} ms"
            if result.elapsed_ms is not None
            else "-"
        )
        self._response_summary.setText(
            f"{result.status_code} {result.reason or ''} | "
            f"{elapsed} | {result.url}"
        )

        if result.json_body is not None:
            body_text = json.dumps(
                result.json_body,
                indent=2,
                ensure_ascii=False,
            )
        else:
            body_text = result.text

        header_text = "\n".join(
            f"{key}: {value}" for key, value in sorted(result.headers.items())
        )
        self._response.setPlainText(
            f"URL\n{result.url}\n\n"
            f"Status\n{result.status_code} {result.reason or ''}\n\n"
            f"Elapsed\n{elapsed}\n\n"
            f"Headers\n{header_text or '-'}\n\n"
            f"Body\n{body_text}"
        )

        validation_route_id = route_id
        if validation_route_id is None and self._template is not None:
            validation_route_id = self._template.route_id

        if (
            result.status_code == 422
            and self._api_map is not None
            and validation_route_id is not None
        ):
            try:
                issues = validation_issues(
                    self._api_map,
                    validation_route_id,
                    result.json_body,
                )
            except KeyError:
                issues = []
            self._render_validation_issues(issues)
            self._result_tabs.setCurrentIndex(1)
        else:
            self._validation.clear()
            self._validation_summary.setText(
                "No FastAPI 422 validation response for this request."
            )
            self._result_tabs.setCurrentIndex(0)

        self.sig_status.emit(
            f"Request completed: {result.status_code} {result.reason or ''}".strip()
        )

    def _record_history(
        self,
        result: RequestExecution,
        *,
        command: dict | None,
        route_id: str | None,
    ) -> None:
        if command is None:
            return

        index = len(self._history)
        entry = {
            "command": json.loads(json.dumps(command)),
            "route_id": route_id,
            "result": result.model_copy(deep=True),
        }
        self._history.append(entry)

        status = str(result.status_code) if result.status_code is not None else "ERR"
        elapsed = (
            f"{result.elapsed_ms:.1f} ms"
            if result.elapsed_ms is not None
            else "-"
        )
        item = QTreeWidgetItem(
            [
                str(index + 1),
                status,
                route_id or "-",
                elapsed,
                result.url or "-",
            ]
        )
        item.setData(0, _ROLE_HISTORY_INDEX, index)
        if self._api_map is not None and route_id is not None:
            route = next(
                (candidate for candidate in self._api_map.routes if candidate.id == route_id),
                None,
            )
            if route is not None and route.source.file:
                item.setData(0, _ROLE_SOURCE_FILE, route.source.file)
                item.setData(0, _ROLE_SOURCE_LINE, route.source.line or 1)
                item.setToolTip(
                    2,
                    f"Double-click to open {route.handler} in Spyder.",
                )

        self._history_tree.insertTopLevelItem(0, item)
        self._clear_history_button.setEnabled(True)
        self._history_tree.setCurrentItem(item)
        self._update_replay_enabled()

        for column in range(self._history_tree.columnCount()):
            self._history_tree.resizeColumnToContents(column)

    @staticmethod
    def _redacted_command(command: dict) -> dict:
        display = json.loads(json.dumps(command))

        headers = display.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                lowered = key.casefold()
                if any(
                    marker in lowered
                    for marker in ("authorization", "api-key", "apikey", "token", "secret")
                ):
                    headers[key] = "***"

        cookies = display.get("cookies")
        if isinstance(cookies, dict):
            for key in list(cookies):
                cookies[key] = "***"

        return display

    def _history_selected(self, item: QTreeWidgetItem | None, _previous) -> None:
        self._update_replay_enabled()
        if item is None:
            self._history_details.clear()
            return

        index = item.data(0, _ROLE_HISTORY_INDEX)
        if index is None or not (0 <= int(index) < len(self._history)):
            self._history_details.clear()
            return

        entry = self._history[int(index)]
        result: RequestExecution = entry["result"]
        command = self._redacted_command(entry["command"])

        self._history_details.setPlainText(
            "Request\n"
            + json.dumps(command, indent=2, ensure_ascii=False)
            + "\n\nResponse\n"
            + result.model_dump_json(indent=2)
        )

    def _open_history_source(
        self,
        item: QTreeWidgetItem | None,
        _column: int,
    ) -> None:
        if item is None:
            return
        filename = item.data(0, _ROLE_SOURCE_FILE)
        line = item.data(0, _ROLE_SOURCE_LINE)
        if filename:
            self.sig_open_source.emit(str(filename), int(line or 1))

    def _update_replay_enabled(self) -> None:
        self._replay_history.setEnabled(
            self._process is None
            and self._history_tree.currentItem() is not None
        )

    def _replay_selected_history(self) -> None:
        if self._process is not None:
            self.sig_status.emit("A Request Lab request is already running.")
            return

        item = self._history_tree.currentItem()
        if item is None:
            return

        index = item.data(0, _ROLE_HISTORY_INDEX)
        if index is None or not (0 <= int(index) < len(self._history)):
            return

        entry = self._history[int(index)]
        command = json.loads(json.dumps(entry["command"]))
        route_id = entry.get("route_id")
        self.sig_status.emit(
            f"Replaying {route_id or command.get('path', 'request')} exactly."
        )
        self._start_request(command, route_id)

    def _clear_history(self) -> None:
        self._history.clear()
        self._history_tree.clear()
        self._history_details.clear()
        self._clear_history_button.setEnabled(False)
        self._update_replay_enabled()

    def _render_validation_issues(self, issues) -> None:
        self._validation.clear()
        self._validation_summary.setText(
            f"{len(issues)} validation issue(s). "
            "Double-click a source-backed issue to open Python."
        )

        for issue in issues:
            item = QTreeWidgetItem(
                [
                    issue.location,
                    issue.field_path or "-",
                    issue.expected_type or "-",
                    issue.message,
                    issue.error_type,
                ]
            )
            if issue.source is not None and issue.source.file:
                item.setData(0, _ROLE_SOURCE_FILE, issue.source.file)
                item.setData(0, _ROLE_SOURCE_LINE, issue.source.line or 1)
                item.setToolTip(1, _source_text(issue.source))
            self._validation.addTopLevelItem(item)

        for column in range(self._validation.columnCount()):
            self._validation.resizeColumnToContents(column)

    def _open_validation_source(
        self,
        item: QTreeWidgetItem | None,
        _column: int,
    ) -> None:
        if item is None:
            return
        filename = item.data(0, _ROLE_SOURCE_FILE)
        line = item.data(0, _ROLE_SOURCE_LINE)
        if filename:
            self.sig_open_source.emit(str(filename), int(line or 1))
