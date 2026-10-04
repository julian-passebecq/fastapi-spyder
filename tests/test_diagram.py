from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel

from spyder_fastapi.core import (
    global_projection,
    impact_projection,
    inspect_app,
    route_projection,
)


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
