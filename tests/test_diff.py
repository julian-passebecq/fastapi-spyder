from fastapi import FastAPI
from pydantic import BaseModel

from spyder_fastapi.core import diff_maps, inspect_app


def build_before() -> FastAPI:
    app = FastAPI(title="Diff API")

    class Payload(BaseModel):
        name: str

    @app.post("/items", response_model=Payload)
    def create_item(payload: Payload, limit: int | None = None):
        _ = limit
        return payload

    @app.get("/legacy")
    def legacy():
        return {"legacy": True}

    return app


def build_after() -> FastAPI:
    app = FastAPI(title="Diff API")

    class Payload(BaseModel):
        name: str
        count: int

    @app.post("/items", response_model=Payload)
    def create_item(payload: Payload, limit: int):
        _ = limit
        return payload

    @app.get("/new")
    def new_route():
        return {"new": True}

    return app


def test_semantic_diff_tracks_routes_models_and_blast_radius():
    before = inspect_app(build_before())
    after = inspect_app(build_after())

    diff = diff_maps(before, after)

    changes = {(change.entity, change.name, change.kind): change for change in diff.changes}

    assert ("route", "GET /legacy", "removed") in changes
    assert ("route", "GET /new", "added") in changes
    assert ("route", "POST /items", "changed") in changes
    assert ("model", "Payload", "changed") in changes

    route_change = changes[("route", "POST /items", "changed")]
    assert "parameters" in route_change.fields
    assert any(
        "parameter became required: query limit" in reason
        for reason in route_change.breaking_reasons
    )

    model_change = changes[("model", "Payload", "changed")]
    assert model_change.affected_routes == ["POST /items"]
    assert "required schema field added: count" in model_change.breaking_reasons

    removed_route = changes[("route", "GET /legacy", "removed")]
    assert removed_route.breaking_reasons == ["route removed"]

    assert diff.affected_routes == [
        "GET /legacy",
        "GET /new",
        "POST /items",
    ]
    assert diff.breaking_candidates >= 3


def test_source_line_changes_do_not_create_semantic_diff():
    before = inspect_app(build_before())
    after = before.model_copy(deep=True)

    after.routes[0].source.line = (after.routes[0].source.line or 1) + 100
    for model in after.models:
        if model.source is not None:
            model.source.line = (model.source.line or 1) + 100

    diff = diff_maps(before, after)

    assert diff.changes == []
    assert diff.affected_routes == []
    assert diff.breaking_candidates == 0
