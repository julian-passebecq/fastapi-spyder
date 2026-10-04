from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel

from spyder_fastapi.core import (
    global_projection,
    impact_projection,
    inspect_app,
    route_projection,
)
from spyder_fastapi.models import RouteTestIndex, SourceRef, RouteTestReference


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

