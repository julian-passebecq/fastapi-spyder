from fastapi import Depends, FastAPI, Header, Query
from pydantic import BaseModel

from spyder_fastapi.core import (
    build_request_template,
    inspect_app,
    validation_issues,
)


class Payload(BaseModel):
    name: str
    count: int


def auth(authorization: str = Header()) -> str:
    return authorization


def build_app() -> FastAPI:
    app = FastAPI(title="Request Lab API")

    @app.post("/items/{item_id}")
    def create_item(
        item_id: int,
        payload: Payload,
        limit: int = Query(default=10),
        token: str = Depends(auth),
    ):
        _ = (item_id, limit, token)
        return payload

    return app


def test_request_template_includes_contract_and_dependency_inputs():
    api_map = inspect_app(build_app())

    template = build_request_template(api_map, "POST /items/{item_id}")

    assert template.method == "POST"
    assert template.path == "/items/{item_id}"
    assert template.body_model == "Payload"
    assert template.body_required is True
    assert template.body_content_type == "application/json"
    assert template.body_example == {
        "name": "string",
        "count": 0,
    }

    fields = {
        (field.location, field.name): field
        for field in template.parameters
    }
    assert fields[("path", "item_id")].required is True
    assert fields[("query", "limit")].required is False
    assert fields[("query", "limit")].example == 10
    assert fields[("header", "authorization")].required is True
    assert fields[("header", "authorization")].source is not None


def test_422_validation_issues_map_back_to_model_and_dependency_sources():
    api_map = inspect_app(build_app())

    payload = {
        "detail": [
            {
                "type": "int_parsing",
                "loc": ["body", "count"],
                "msg": "Input should be a valid integer",
                "input": "abc",
            },
            {
                "type": "missing",
                "loc": ["header", "authorization"],
                "msg": "Field required",
                "input": None,
            },
        ]
    }

    issues = validation_issues(
        api_map,
        "POST /items/{item_id}",
        payload,
    )

    assert len(issues) == 2

    body_issue = issues[0]
    assert body_issue.location == "body"
    assert body_issue.field_path == "count"
    assert body_issue.expected_type == "integer"
    assert body_issue.source is not None
    assert body_issue.source.qualname.endswith(".Payload.count")

    header_issue = issues[1]
    assert header_issue.location == "header"
    assert header_issue.field_path == "authorization"
    assert header_issue.expected_type == "str"
    assert header_issue.source is not None
    assert header_issue.source.qualname.endswith(".auth")


def test_validation_parser_ignores_non_fastapi_error_shapes():
    api_map = inspect_app(build_app())

    assert validation_issues(api_map, "POST /items/{item_id}", {"error": "nope"}) == []
    assert validation_issues(api_map, "POST /items/{item_id}", ["not", "a", "dict"]) == []
