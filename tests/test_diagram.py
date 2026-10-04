from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel

from spyder_fastapi.core import (
    global_projection,
    impact_projection,
    inspect_app,
    overlay_runtime_lineage,
    route_projection,
)
from spyder_fastapi.models import RouteTestIndex, SourceRef, RouteTestReference
from spyder_fastapi.core.telemetry import NativeTelemetryStore


class ItemIn(BaseModel):
    name: str


class ItemOut(BaseModel):
    name: str


def shared_auth(token: str = Header()) -> str:
    return token


def build_app() -> FastAPI:
    app = FastAPI(title="Diagram API")

    @app.post("/items", response_model=ItemOut)
    def create_item(
        item: ItemIn,
        _token: str = Depends(shared_auth),
    ):
        return ItemOut(name=item.name)

    @app.get("/items")
    def list_items(_token: str = Depends(shared_auth)):
        return []

    return app


def test_route_projection_keeps_full_request_flow():
    api_map = inspect_app(build_app())

    projection = route_projection(api_map, "POST /items")

    kinds = {node.kind for node in projection.nodes}
    assert {"route", "parameter", "model", "dependency", "handler"} <= kinds
    assert projection.roots == ["route:POST /items"]
    assert projection.focus_id == "route:POST /items"

    relations = {edge.relation for edge in projection.edges}
    assert {"accepts", "validates_as", "depends_on", "handled_by", "returns"} <= relations


def test_global_projection_compacts_parameters_but_keeps_models():
    api_map = inspect_app(build_app())

    projection = global_projection(api_map)

    assert projection.mode == "global"
    assert all(node.kind != "parameter" for node in projection.nodes)
    assert {"route:GET /items", "route:POST /items"} <= set(projection.roots)

    item_model = next(
        node for node in projection.nodes
        if node.kind == "model" and node.label == "ItemIn"
    )
    assert any(
        edge.source == "route:POST /items"
        and edge.target == item_model.id
        and edge.relation == "accepts_model"
        for edge in projection.edges
    )


def test_impact_projection_shows_real_paths_from_every_impacted_route():
    api_map = inspect_app(build_app())
    dependency = next(
        item
        for item in api_map.dependencies
        if item.name.endswith(".shared_auth")
    )

    projection = impact_projection(api_map, dependency.id)

    assert projection.mode == "impact"
    assert projection.focus_id == dependency.id
    assert set(projection.roots) == {
        "route:GET /items",
        "route:POST /items",
    }

    node_ids = {node.id for node in projection.nodes}
    assert dependency.id in node_ids
    assert "route:GET /items" in node_ids
    assert "route:POST /items" in node_ids

    assert all(
        edge.relation == "depends_on"
        for edge in projection.edges
    )

def _test_index() -> RouteTestIndex:
    return RouteTestIndex(
        scanned_files=1,
        references=[
            RouteTestReference(
                id="test:/tmp/test_items.py:10:POST:POST /items",
                route_id="POST /items",
                test_name="test_create_item",
                method="POST",
                requested_path="/items",
                match_kind="exact",
                source=SourceRef(
                    file="/tmp/test_items.py",
                    line=10,
                    qualname="test_create_item",
                ),
            ),
            RouteTestReference(
                id="test:/tmp/test_items.py:20:GET:GET /items",
                route_id="GET /items",
                test_name="test_list_items",
                method="GET",
                requested_path="/items",
                match_kind="exact",
                source=SourceRef(
                    file="/tmp/test_items.py",
                    line=20,
                    qualname="test_list_items",
                ),
            ),
        ],
    )


def test_route_projection_can_overlay_tests():
    api_map = inspect_app(build_app())

    projection = route_projection(
        api_map,
        "POST /items",
        _test_index(),
        include_tests=True,
    )

    test_nodes = [node for node in projection.nodes if node.kind == "test"]
    assert [node.label for node in test_nodes] == ["test_create_item"]
    assert any(
        edge.source == "route:POST /items"
        and edge.target == test_nodes[0].id
        and edge.relation == "tested_by"
        for edge in projection.edges
    )


def test_global_and_impact_projections_can_overlay_route_tests():
    api_map = inspect_app(build_app())
    index = _test_index()

    global_map = global_projection(
        api_map,
        index,
        include_tests=True,
    )
    assert {node.label for node in global_map.nodes if node.kind == "test"} == {
        "test_create_item",
        "test_list_items",
    }

    dependency = next(
        item
        for item in api_map.dependencies
        if item.name.endswith(".shared_auth")
    )
    impact = impact_projection(
        api_map,
        dependency.id,
        index,
        include_tests=True,
    )
    assert {node.label for node in impact.nodes if node.kind == "test"} == {
        "test_create_item",
        "test_list_items",
    }
    assert sum(
        edge.relation == "tested_by"
        for edge in impact.edges
    ) == 2


def _ingest_span(
    store: NativeTelemetryStore,
    *,
    trace_id: str,
    span_id: str,
    name: str,
    kind: str,
    start_ns: int,
    end_ns: int,
    parent_span_id: str | None = None,
    attributes: dict | None = None,
    scope_name: str = "test",
):
    store.ingest(
        {
            "signal": "span",
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "name": name,
            "kind": kind,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "duration_ms": (end_ns - start_ns) / 1_000_000,
            "attributes": attributes or {},
            "scope_name": scope_name,
        }
    )


def test_runtime_lineage_attaches_observed_database_to_static_handler():
    api_map = inspect_app(build_app())
    projection = route_projection(api_map, "POST /items")
    store = NativeTelemetryStore()
    trace_id = "d" * 32

    _ingest_span(
        store,
        trace_id=trace_id,
        span_id="1" * 16,
        name="POST /items",
        kind="SERVER",
        start_ns=0,
        end_ns=20_000_000,
        attributes={
            "http.route": "/items",
            "http.request.method": "POST",
            "http.response.status_code": 200,
        },
        scope_name="fastapi",
    )
    _ingest_span(
        store,
        trace_id=trace_id,
        span_id="2" * 16,
        parent_span_id="1" * 16,
        name="fastapi.endpoint",
        kind="INTERNAL",
        start_ns=2_000_000,
        end_ns=18_000_000,
        attributes={"code.function.name": "create_item"},
        scope_name="fastapi",
    )
    _ingest_span(
        store,
        trace_id=trace_id,
        span_id="3" * 16,
        parent_span_id="2" * 16,
        name="SELECT items",
        kind="CLIENT",
        start_ns=4_000_000,
        end_ns=10_000_000,
        attributes={
            "db.system.name": "postgresql",
            "db.namespace": "catalog",
            "db.operation.name": "SELECT",
        },
        scope_name="demo.db",
    )

    enriched = overlay_runtime_lineage(api_map, projection, store)

    runtime_nodes = [
        node for node in enriched.nodes if node.evidence == "runtime"
    ]
    assert len(runtime_nodes) == 1
    runtime = runtime_nodes[0]
    assert runtime.kind == "database"
    assert runtime.target == "postgresql:catalog"
    assert runtime.label == "postgresql:catalog · SELECT"
    assert runtime.observed_count == 1
    assert runtime.last_ms == 6.0
    assert runtime.trace_id == trace_id

    handler_id = next(
        edge.target
        for edge in api_map.lineage.edges
        if edge.source == "route:POST /items"
        and edge.relation == "handled_by"
    )
    assert any(
        edge.source == handler_id
        and edge.target == runtime.id
        and edge.relation == "observed_database"
        for edge in enriched.edges
    )


def test_runtime_lineage_can_anchor_observed_io_to_dependency():
    api_map = inspect_app(build_app())
    dependency = next(
        item
        for item in api_map.dependencies
        if item.name.endswith(".shared_auth")
    )
    projection = route_projection(api_map, "GET /items")
    store = NativeTelemetryStore()
    trace_id = "e" * 32

    _ingest_span(
        store,
        trace_id=trace_id,
        span_id="1" * 16,
        name="GET /items",
        kind="SERVER",
        start_ns=0,
        end_ns=12_000_000,
        attributes={
            "http.route": "/items",
            "http.request.method": "GET",
            "http.response.status_code": 200,
        },
        scope_name="fastapi",
    )
    _ingest_span(
        store,
        trace_id=trace_id,
        span_id="2" * 16,
        parent_span_id="1" * 16,
        name="fastapi.dependencies",
        kind="INTERNAL",
        start_ns=1_000_000,
        end_ns=8_000_000,
        attributes={"code.function.name": dependency.name},
        scope_name="fastapi",
    )
    _ingest_span(
        store,
        trace_id=trace_id,
        span_id="3" * 16,
        parent_span_id="2" * 16,
        name="GET auth.internal",
        kind="CLIENT",
        start_ns=2_000_000,
        end_ns=5_000_000,
        attributes={
            "http.request.method": "GET",
            "server.address": "auth.internal",
        },
        scope_name="demo.http",
    )

    enriched = overlay_runtime_lineage(api_map, projection, store)
    runtime = next(node for node in enriched.nodes if node.evidence == "runtime")

    assert runtime.kind == "http-client"
    assert runtime.target == "auth.internal"
    assert any(
        edge.source == dependency.id
        and edge.target == runtime.id
        and edge.relation == "observed_http_client"
        for edge in enriched.edges
    )


def test_runtime_lineage_aggregates_repeated_observations_without_mutating_static_map():
    api_map = inspect_app(build_app())
    projection = route_projection(api_map, "POST /items")
    original_node_ids = {node.id for node in projection.nodes}
    store = NativeTelemetryStore()

    for index, duration_ms in enumerate((4.0, 8.0), start=1):
        trace_id = f"{index:032x}"
        root_id = f"{index:016x}"
        endpoint_id = f"{index + 10:016x}"
        db_id = f"{index + 20:016x}"
        _ingest_span(
            store,
            trace_id=trace_id,
            span_id=root_id,
            name="POST /items",
            kind="SERVER",
            start_ns=0,
            end_ns=20_000_000,
            attributes={
                "http.route": "/items",
                "http.request.method": "POST",
                "http.response.status_code": 200,
            },
            scope_name="fastapi",
        )
        _ingest_span(
            store,
            trace_id=trace_id,
            span_id=endpoint_id,
            parent_span_id=root_id,
            name="fastapi.endpoint",
            kind="INTERNAL",
            start_ns=1_000_000,
            end_ns=18_000_000,
            scope_name="fastapi",
        )
        _ingest_span(
            store,
            trace_id=trace_id,
            span_id=db_id,
            parent_span_id=endpoint_id,
            name="SELECT items",
            kind="CLIENT",
            start_ns=3_000_000,
            end_ns=3_000_000 + int(duration_ms * 1_000_000),
            attributes={
                "db.system.name": "postgresql",
                "db.namespace": "catalog",
                "db.operation.name": "SELECT",
            },
            scope_name="demo.db",
        )

    enriched = overlay_runtime_lineage(api_map, projection, store)
    runtime = next(node for node in enriched.nodes if node.evidence == "runtime")

    assert runtime.observed_count == 2
    assert runtime.average_ms == 6.0
    assert runtime.p95_ms == 7.8
    assert runtime.last_ms == 8.0
    assert {node.id for node in projection.nodes} == original_node_ids
    assert all(node.evidence != "runtime" for node in projection.nodes)

