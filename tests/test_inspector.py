from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel

from spyder_fastapi.core import inspect_app


class ItemIn(BaseModel):
    name: str


class ItemOut(BaseModel):
    name: str
    stored: bool


def auth(authorization: str = Header()) -> str:
    return authorization


def db(user: str = Depends(auth)) -> str:
    return f"db:{user}"


def build_app() -> FastAPI:
    app = FastAPI(title="Test API", version="1.2.3")

    @app.post("/items/{item_id}", response_model=ItemOut, status_code=201)
    def create_item(item_id: int, item: ItemIn, connection: str = Depends(db)):
        """Create an item while giving execution-line detection a docstring."""
        _ = (item_id, connection)
        return ItemOut(name=item.name, stored=True)

    return app


def test_inspection_extracts_contract_source_and_lineage():
    result = inspect_app(build_app())

    assert result.title == "Test API"
    assert result.version == "1.2.3"
    assert len(result.routes) == 1

    route = result.routes[0]
    assert route.id == "POST /items/{item_id}"
    assert route.request_models == ["ItemIn"]
    assert route.response_model == "ItemOut"
    assert route.source.line is not None
    assert route.source.execution_line is not None
    assert route.source.execution_line > route.source.line
    with open(route.source.file, encoding="utf-8") as source_file:
        source_lines = source_file.read().splitlines()
    assert source_lines[route.source.execution_line - 1].strip().startswith("_ =")

    locations = {(param.location, param.name) for param in route.parameters}
    assert ("path", "item_id") in locations
    assert ("body", "item") in locations

    dependency_names = {dependency.name for dependency in result.dependencies}
    assert any(name.endswith(".db") for name in dependency_names)
    assert any(name.endswith(".auth") for name in dependency_names)

    auth_dependency = next(
        dependency
        for dependency in result.dependencies
        if dependency.name.endswith(".auth")
    )
    assert [
        (parameter.location, parameter.name, parameter.required)
        for parameter in auth_dependency.parameters
    ] == [("header", "authorization", True)]
    assert auth_dependency.source.execution_line is not None

    edge_relations = {edge.relation for edge in result.lineage.edges}
    assert {"accepts", "validates_as", "depends_on", "handled_by", "returns"} <= edge_relations

    header_node = next(
        node
        for node in result.lineage.nodes
        if node.kind == "parameter" and node.label == "header:authorization"
    )
    assert any(
        edge.source == auth_dependency.id
        and edge.target == header_node.id
        and edge.relation == "accepts"
        for edge in result.lineage.edges
    )

    model_names = {model.name for model in result.models}
    assert {"ItemIn", "ItemOut"} <= model_names
    item_in = next(model for model in result.models if model.name == "ItemIn")
    assert item_in.source is not None
    assert item_in.source.line is not None
    assert item_in.field_sources["name"].line is not None
    assert item_in.field_sources["name"].qualname.endswith(".ItemIn.name")
    assert "/items/{item_id}" in result.openapi["paths"]


def test_json_bridge_uses_schema_key():
    result = inspect_app(build_app())
    payload = result.model_dump(by_alias=True)
    first_model = payload["models"][0]
    assert "schema" in first_model
    assert "schema_" not in first_model


def test_lineage_can_compute_dependency_blast_radius():
    from spyder_fastapi.core import impacted_routes

    app = FastAPI(title="Impact API")

    def shared_dependency(token: str = Header()):
        return token

    @app.get("/one")
    def one(_token: str = Depends(shared_dependency)):
        return {"ok": True}

    @app.get("/two")
    def two(_token: str = Depends(shared_dependency)):
        return {"ok": True}

    result = inspect_app(app)
    dependency_id = next(
        dependency.id
        for dependency in result.dependencies
        if dependency.name.endswith(".shared_dependency")
    )

    assert impacted_routes(result, dependency_id) == ["GET /one", "GET /two"]
