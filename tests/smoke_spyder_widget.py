"""Cross-platform headless qualification for the Spyder FastAPI Studio widget."""

import os
import platform
import sys
from importlib.metadata import entry_points, version
from importlib.util import module_from_spec, spec_from_file_location
from inspect import signature
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import Depends, FastAPI, File, Form, Header, UploadFile
from fastapi.testclient import TestClient
from qtpy.QtCore import QEventLoop, QTimer
from qtpy.QtWidgets import QApplication
from spyder.app.find_plugins import find_external_plugins
from spyder.plugins.ipythonconsole.plugin import IPythonConsole
import uvicorn

from spyder_fastapi.core import NativeTelemetryStore, inspect_app
from spyder_fastapi.models import RequestExecution
from spyder_fastapi.telemetry_capture import configure_native_telemetry
from spyder_fastapi.spyder.compat import (
    spyder_contract_issues,
    variable_explorer_available,
)
from spyder_fastapi.spyder.plugin import FastAPIStudioPlugin
from spyder_fastapi.spyder.widget import FastAPIStudioWidget

before = FastAPI(title="Smoke API")

def auth(token: str = Header()):
    return token

@before.get("/health")
def health(_token: str = Depends(auth)):
    return {"ok": True}

@before.post("/upload")
async def upload(
    description: str = Form(),
    document: UploadFile = File(),
):
    return {
        "description": description,
        "filename": document.filename,
    }

after = FastAPI(title="Smoke API")

@after.get("/health")
def health_after(_token: str = Depends(auth)):
    return {"ok": True}

@after.get("/ready")
def ready():
    return {"ready": True}

print(
    "QUALIFICATION",
    f"os={platform.platform()}",
    f"python={platform.python_version()}",
    f"spyder={version('spyder')}",
    f"fastapi={version('fastapi')}",
    f"fastapi-spyder={version('fastapi-spyder')}",
)

plugin_entries = {
    entry.name: entry.value
    for entry in entry_points(group="spyder.plugins")
}
assert plugin_entries["fastapi_studio"] == (
    "spyder_fastapi.spyder.plugin:FastAPIStudioPlugin"
)

external_plugins = find_external_plugins()
assert external_plugins["fastapi_studio"] is FastAPIStudioPlugin
assert FastAPIStudioPlugin.NAME == "fastapi_studio"

contract_issues = spyder_contract_issues()
assert contract_issues == [], contract_issues
assert variable_explorer_available()
compatible, compatibility_message = FastAPIStudioPlugin.check_compatibility(None)
assert compatible, compatibility_message
assert uvicorn.__version__
print("SPYDER_CONTRACT PASS")
print(f"UVICORN_DEBUG_RUNTIME PASS version={uvicorn.__version__}")

qt_app = QApplication.instance() or QApplication([])
project_temp = TemporaryDirectory(prefix="FastAPI Studio ")
project_root = Path(project_temp.name)
tests_dir = project_root / "tests"
tests_dir.mkdir()
(tests_dir / "test_smoke_api.py").write_text(
    "def test_health(client):\n"
    "    response = client.get('/health')\n"
    "    assert response.status_code == 200\n",
    encoding="utf-8",
)

widget = FastAPIStudioWidget("fastapi_studio", None)

screenshot_root = os.environ.get("FASTAPI_STUDIO_SCREENSHOT_DIR")
screenshot_dir = Path(screenshot_root).resolve() if screenshot_root else None
if screenshot_dir is not None:
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    widget.resize(1600, 1000)
    widget.show()
    qt_app.processEvents()

def capture_widget(name: str) -> None:
    if screenshot_dir is None:
        return
    qt_app.processEvents()
    destination = screenshot_dir / f"{name}.png"
    assert widget.grab().save(str(destination), "PNG")
    assert destination.stat().st_size > 0
    print(f"SCREENSHOT {destination}")

widget.set_working_directory(project_root)
widget.set_api_map(inspect_app(before))

native_path = project_root / "native-telemetry.jsonl"
capture = configure_native_telemetry(native_path)
assert capture["event"] == "capture_configured"
assert capture["tracing"] is True
assert capture["logs"] is True

native_client = TestClient(before)
assert native_client.get(
    "/health",
    headers={"token": "native-smoke"},
).status_code == 200
assert native_client.get("/health").status_code == 422

widget._native_telemetry_path = native_path
widget._native_telemetry_offset = 0
widget._native_telemetry_partial = b""
widget._poll_native_telemetry()

assert widget._native_telemetry.total_requests() == 2
assert any(
    span.name == "fastapi.dependencies"
    for span in widget._native_telemetry.spans
)
assert any(
    span.name == "fastapi.endpoint"
    for span in widget._native_telemetry.spans
)
assert any(
    span.name == "fastapi.serialization"
    for span in widget._native_telemetry.spans
)
assert any(
    log.event_name == "fastapi.validation.failed"
    for log in widget._native_telemetry.logs
)
assert widget._telemetry._routes.topLevelItemCount() == 1
assert "LIVE" in widget._telemetry._status.text()

assert widget._telemetry._validation._value.text() == "1"
assert widget._telemetry._exceptions._value.text() == "0"
assert widget._telemetry._waterfall._rows
native_summary = widget._diagram._native_route_summaries["GET /health"]
assert native_summary.request_count == 2
assert native_summary.validation_failure_count == 1

demo_path = Path("examples/data_platform_demo/app.py").resolve()

# Exercise the real asynchronous inspector, including a live-directory change
# before its result arrives and the synchronous source-opening breakpoint signal.
inspection_widget = FastAPIStudioWidget("fastapi_studio", None)
inspection_widget.set_working_directory(demo_path.parent)
inspection_widget._target.setCurrentText("app:app")
inspection_widget.inspect_current_app()
inspection_process = inspection_widget._process
assert inspection_process is not None
inspection_loop = QEventLoop()
inspection_process.finished.connect(inspection_loop.quit)
inspection_timeout = QTimer()
inspection_timeout.setSingleShot(True)
inspection_timeout.timeout.connect(inspection_loop.quit)
inspection_timeout.start(30000)
inspection_widget.set_working_directory(demo_path.parent.parent)
inspection_loop.exec_()
inspection_timeout.stop()
assert inspection_widget._process is None, "Demo inspection timed out"
assert inspection_widget._api_map is not None, (
    inspection_widget._diagnostics.toPlainText()
)
assert inspection_widget._loaded_target == "app:app"
assert Path(inspection_widget._loaded_workdir).samefile(demo_path.parent)
inspection_widget._request_lab.select_route("GET /v1/jobs/{job_id}")
for row in range(inspection_widget._request_lab._parameters.rowCount()):
    name = inspection_widget._request_lab._parameters.item(row, 1).text()
    value = {
        "job_id": "job-qualification",
        "x-api-key": "demo-secret",
        "x-tenant-id": "contoso",
    }.get(name, "qualification")
    inspection_widget._request_lab._parameters.item(row, 4).setText(value)
inspection_widget._request_lab.sig_set_breakpoint.connect(
    lambda _filename, _line: inspection_widget.set_working_directory(
        project_root
    )
)
inspection_launches = []
inspection_widget._request_lab.sig_start_debug_server.connect(
    lambda target, workdir, host, port: inspection_launches.append(
        (target, workdir, host, port)
    )
)
inspection_widget.set_debug_server_available(True)
inspection_widget._request_lab._debug_server.click()
assert inspection_launches, inspection_widget._status.text()
assert inspection_launches[0][0] == "app:app"
assert Path(inspection_launches[0][1]).samefile(demo_path.parent)
assert Path(inspection_widget._workdir).samefile(project_root)
inspection_widget.shutdown()
inspection_widget.close()
print("PINNED_INSPECTION_DEBUG_WORKDIR PASS")

spec = spec_from_file_location("data_platform_demo_app", demo_path)
assert spec is not None and spec.loader is not None
demo_module = module_from_spec(spec)
sys.modules[spec.name] = demo_module
spec.loader.exec_module(demo_module)

demo_map = inspect_app(demo_module.app)
widget.set_working_directory(demo_path.parent)
widget._loaded_workdir = str(demo_path.parent)
widget.set_api_map(demo_map)
assert Path(widget._request_lab._workdir).resolve() == demo_path.parent

# Opening source/debugger surfaces may change Spyder's live working directory.
# The runtime target must keep using the exact directory that imported app:app.
widget.set_working_directory(demo_path.parent.parent)
assert Path(widget._request_lab._workdir).resolve() == demo_path.parent
widget.set_working_directory(demo_path.parent)

assert len(demo_map.routes) == 6
assert len(demo_map.dependencies) >= 2
assert len(widget._test_index.references) >= 5

demo_native_path = project_root / "demo-native-telemetry.jsonl"
demo_capture = configure_native_telemetry(demo_native_path)
assert demo_capture["event"] == "capture_configured"

demo_client = TestClient(
    demo_module.app,
    raise_server_exceptions=False,
)
demo_headers = {
    "x-api-key": "demo-secret",
    "x-tenant-id": "contoso",
}
assert demo_client.post(
    "/v1/ingestions",
    headers=demo_headers,
    json={
        "source": "crm",
        "record_count": 25,
        "schema_version": 1,
    },
).status_code == 202
assert demo_client.post(
    "/v1/ingestions",
    headers=demo_headers,
    json={
        "source": "crm",
        "record_count": 0,
        "schema_version": 1,
    },
).status_code == 422
assert demo_client.get("/v1/debug/fail").status_code == 500

widget._native_telemetry = NativeTelemetryStore()
widget._native_telemetry_path = demo_native_path
widget._native_telemetry_offset = 0
widget._native_telemetry_partial = b""
widget._poll_native_telemetry()

categories = widget._native_telemetry.external_span_categories()
assert categories == {
    "database": 1,
    "http-client": 1,
    "messaging": 1,
}
assert widget._telemetry._external._value.text() == "3"
assert widget._telemetry._validation._value.text() == "1"
assert widget._telemetry._exceptions._value.text() == "1"

exception_item = next(
    (
        widget._telemetry._logs.topLevelItem(index)
        for index in range(widget._telemetry._logs.topLevelItemCount())
        if widget._telemetry._logs.topLevelItem(index).text(1)
        == "http.server.request.exception"
    ),
    None,
)
assert exception_item is not None
assert widget._telemetry._logs.columnCount() == 6
assert exception_item.text(4).endswith("fail_route")
assert str(exception_item.data(0, 42)).endswith("fail_route")
widget._tabs.setCurrentWidget(widget._telemetry)
widget._telemetry._tabs.setCurrentIndex(2)
capture_widget("telemetry-exception-log")
opened_exception_sources = []
widget.sig_open_source.connect(
    lambda filename, line: opened_exception_sources.append((filename, line))
)
widget._telemetry._log_activated(exception_item, 0)
assert widget._telemetry._tabs.currentIndex() == 1
assert widget._telemetry._traces.currentItem() is not None
assert opened_exception_sources
assert Path(opened_exception_sources[-1][0]).resolve() == demo_path
assert opened_exception_sources[-1][1] > 0

assert any(
    span.name == "fastapi.background_task"
    for span in widget._native_telemetry.spans
)
assert any(
    span.name == "publish ingestion.accepted"
    for span in widget._native_telemetry.spans
)
assert widget._telemetry._traces.columnCount() == 6
assert widget._telemetry._waterfall._rows

widget._diagram.select_route("POST /v1/ingestions")
runtime_nodes = [
    node
    for node in widget._diagram._projection.nodes
    if node.evidence == "runtime"
]
assert {node.kind for node in runtime_nodes} == {
    "database",
    "http-client",
    "messaging",
}
assert any(
    edge.relation == "observed_database"
    for edge in widget._diagram._projection.edges
)
assert any(
    edge.relation == "observed_http_client"
    for edge in widget._diagram._projection.edges
)
assert any(
    edge.relation == "observed_messaging"
    for edge in widget._diagram._projection.edges
)
assert "observed downstream node" in widget._diagram._summary.text()

database_node = next(
    node
    for node in runtime_nodes
    if node.kind == "database"
)
widget._diagram._node_activated(database_node.id)
assert widget._tabs.currentWidget() is widget._telemetry
assert widget._telemetry._tabs.currentIndex() == 1
assert widget._telemetry._traces.currentItem() is not None
assert database_node.trace_id
assert (
    widget._telemetry._traces.currentItem().data(0, 41)
    == database_node.trace_id
)
assert widget._telemetry._traces.currentItem().text(4) == "202"
assert (
    "Opened runtime evidence trace for POST /v1/ingestions."
    in widget._status.text()
)
capture_widget("telemetry")

widget._tabs.setCurrentWidget(widget._diagram)
qt_app.processEvents()
capture_widget("diagram-runtime-lineage")

widget._diagram._show_downstream.setChecked(False)
assert all(
    node.evidence != "runtime"
    for node in widget._diagram._projection.nodes
)
widget._diagram._show_downstream.setChecked(True)
assert any(
    node.evidence == "runtime"
    for node in widget._diagram._projection.nodes
)

widget.set_working_directory(project_root)
widget._loaded_workdir = str(project_root)
widget.set_api_map(inspect_app(before))
widget._diagram.select_route("GET /health")

assert FastAPIStudioPlugin.NAME == "fastapi_studio"
assert "method" in signature(IPythonConsole.run_script).parameters
assert widget._routes_tree.topLevelItemCount() == 2
assert widget._dependencies_tree.topLevelItemCount() == 1
assert widget._lineage_tree.topLevelItemCount() == 1
assert widget._request_lab._route.currentText() == "GET /health"
assert widget._diagram._projection is not None
assert widget._diagram._projection.mode == "route"
assert widget._diagram._node_items

assert len(widget._test_index.references) == 1
assert widget._tests_tree.topLevelItemCount() == 1
assert widget._test_index.references[0].route_id == "GET /health"

widget._diagram.set_show_tests(True)
assert any(
    node.kind == "test"
    for node in widget._diagram._projection.nodes
)

global_index = widget._diagram._mode.findData("global")
widget._diagram._mode.setCurrentIndex(global_index)
assert widget._diagram._projection.mode == "global"
assert all(
    node.kind != "parameter"
    for node in widget._diagram._projection.nodes
)

dependency_id = widget._api_map.dependencies[0].id
widget._diagram.focus_node(dependency_id)
assert widget._diagram._projection.mode == "impact"
assert widget._diagram._projection.focus_id == dependency_id
assert "route:GET /health" in widget._diagram._projection.roots

widget._diagram.select_route("GET /health")
assert widget._diagram._projection.mode == "route"
assert widget._request_lab._route.currentText() == "GET /health"
assert widget._request_lab._parameters.rowCount() == 1

widget._request_lab.select_route("POST /upload")
assert widget._request_lab._body_fields.rowCount() == 2
with TemporaryDirectory() as temp_dir:
    upload_path = Path(temp_dir) / "smoke.txt"
    upload_path.write_text("smoke upload", encoding="utf-8")

    for row in range(widget._request_lab._body_fields.rowCount()):
        kind = widget._request_lab._body_fields.item(row, 0).text()
        name = widget._request_lab._body_fields.item(row, 1).text()
        value = widget._request_lab._body_fields.item(row, 4)
        if name == "description":
            value.setText("smoke")
        elif kind == "file" and name == "document":
            value.setText(str(upload_path))

    multipart_command = widget._request_lab._collect_command()
    assert multipart_command["multipart"]["description"] == "smoke"
    assert multipart_command["files"]["document"] == str(upload_path)

widget._request_lab.select_route("GET /health")

debug_launches = []
widget._request_lab.sig_start_debug_server.connect(
    lambda target, workdir, host, port: debug_launches.append(
        (target, workdir, host, port)
    )
)
widget._request_lab.set_app_target("service.main:app")
widget._request_lab.set_debug_server_available(True)
assert widget._request_lab._debug_server.isEnabled()
widget._request_lab._parameters.item(0, 4).setText("smoke-token")
widget._request_lab._debug_server.click()
assert debug_launches
assert widget._request_lab._cancel_debug_wait.isEnabled()
assert debug_launches[0][0] == "service.main:app"
# Windows TEMP can use an 8.3 alias (e.g. RUNNER~1); compare directory identity.
assert Path(debug_launches[0][1]).samefile(project_root), debug_launches[0]
assert debug_launches[0][2:] == ("127.0.0.1", 8000)
debug_stops = []
widget._request_lab.sig_stop_debug_server.connect(
    lambda: debug_stops.append(True)
)
widget.set_debug_server_running(True)
assert widget._request_lab._stop_debug_server.isEnabled()
widget._request_lab._stop_debug_server.click()
assert debug_stops
widget.set_debug_server_running(False)

widget._request_lab._render_result(
    RequestExecution(
        url="http://127.0.0.1:8000/health",
        status_code=422,
        reason="Unprocessable Entity",
        json_body={
            "detail": [
                {
                    "type": "missing",
                    "loc": ["header", "token"],
                    "msg": "Field required",
                    "input": None,
                }
            ]
        },
    )
)
assert widget._request_lab._validation.topLevelItemCount() == 1

widget._request_lab._record_history(
    RequestExecution(
        url="http://127.0.0.1:8000/health",
        status_code=200,
        reason="OK",
        json_body={"ok": True},
        elapsed_ms=2.5,
    ),
    command={
        "method": "GET",
        "base_url": "http://127.0.0.1:8000",
        "path": "/health",
        "headers": {"Authorization": "Bearer secret"},
    },
    route_id="GET /health",
)
assert widget._request_lab._history_tree.topLevelItemCount() == 1
assert "***" in widget._request_lab._history_details.toPlainText()

widget._request_lab.sig_request_completed.emit(
    "GET /health",
    RequestExecution(
        url="http://127.0.0.1:8000/health",
        status_code=200,
        reason="OK",
        elapsed_ms=12.5,
    ),
)
runtime_stats = widget._runtime_evidence.routes["GET /health"]
assert runtime_stats.request_count == 1
assert runtime_stats.last_elapsed_ms == 12.5
assert widget._diagram._clear_runtime.isEnabled()

widget._diagram._clear_runtime.click()
assert widget._runtime_evidence.routes == {}

redacted = widget._request_lab._redacted_command(
    {
        "form": {"username": "alice", "password": "secret"},
        "files": {
            "document": "/tmp/private/report.pdf",
            "documents": [
                "/tmp/private/a.txt",
                "/tmp/private/b.txt",
            ],
        },
    }
)
assert redacted["form"]["username"] == "alice"
assert redacted["form"]["password"] == "***"
assert redacted["files"]["document"] == "report.pdf"
assert redacted["files"]["documents"] == ["a.txt", "b.txt"]

widget.set_baseline()
widget.set_api_map(inspect_app(after))
assert widget._changes_tree.topLevelItemCount() >= 1
assert widget._current_diff is not None
assert "GET /ready" in widget._current_diff.affected_routes

widget.shutdown()
widget.close()
project_temp.cleanup()
qt_app.processEvents()
print("FastAPI Studio widget smoke test passed")
