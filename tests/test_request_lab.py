from fastapi import Body, Depends, FastAPI, File, Form, Header, Query, UploadFile
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


def test_request_template_detects_non_json_body_media_type():
    app = FastAPI(title="Media API")

    @app.post("/text")
    def text_body(payload: str = Body(media_type="text/plain")):
        return payload

    api_map = inspect_app(app)
    template = build_request_template(api_map, "POST /text")

    assert template.body_required is True
    assert template.body_content_type == "text/plain"


def test_request_template_uses_openapi_path_format_for_path_converters():
    app = FastAPI(title="Path API")

    @app.get("/files/{file_path:path}")
    def read_file(file_path: str):
        return {"file_path": file_path}

    api_map = inspect_app(app)

    assert [route.id for route in api_map.routes] == ["GET /files/{file_path}"]
    template = build_request_template(api_map, "GET /files/{file_path}")
    assert template.path == "/files/{file_path}"
    assert [(field.location, field.name) for field in template.parameters] == [
        ("path", "file_path")
    ]


def test_request_template_uses_wire_aliases_for_headers_and_queries():
    app = FastAPI(title="Alias API")

    @app.get("/client")
    def client_info(
        user_agent: str = Header(),
        page_size: int = Query(default=25, alias="page-size"),
    ):
        return {"user_agent": user_agent, "page_size": page_size}

    api_map = inspect_app(app)
    template = build_request_template(api_map, "GET /client")

    fields = {
        (field.location, field.name): field
        for field in template.parameters
    }

    assert ("header", "user-agent") in fields
    assert fields[("header", "user-agent")].python_name == "user_agent"
    assert ("query", "page-size") in fields
    assert fields[("query", "page-size")].python_name == "page_size"
    assert fields[("query", "page-size")].example == 25

    issues = validation_issues(
        api_map,
        "GET /client",
        {
            "detail": [
                {
                    "type": "missing",
                    "loc": ["header", "user-agent"],
                    "msg": "Field required",
                    "input": None,
                }
            ]
        },
    )
    assert len(issues) == 1
    assert issues[0].expected_type == "str"
    assert issues[0].source is not None


def test_pydantic_field_alias_maps_back_to_exact_field_source():
    from pydantic import Field

    app = FastAPI(title="Body Alias API")

    class AliasedPayload(BaseModel):
        count: int = Field(alias="itemCount")

    @app.post("/aliased")
    def aliased(payload: AliasedPayload):
        return payload

    api_map = inspect_app(app)
    issues = validation_issues(
        api_map,
        "POST /aliased",
        {
            "detail": [
                {
                    "type": "int_parsing",
                    "loc": ["body", "itemCount"],
                    "msg": "Input should be a valid integer",
                    "input": "bad",
                }
            ]
        },
    )

    assert len(issues) == 1
    assert issues[0].source is not None
    assert issues[0].source.qualname.endswith(".AliasedPayload.count")

def test_request_template_builds_urlencoded_form_fields():
    app = FastAPI(title="Form API")

    @app.post("/login")
    def login(
        username: str = Form(),
        remember: bool = Form(default=False),
    ):
        return {"username": username, "remember": remember}

    api_map = inspect_app(app)
    template = build_request_template(api_map, "POST /login")

    assert template.body_content_type == "application/x-www-form-urlencoded"
    fields = {field.name: field for field in template.body_fields}
    assert fields["username"].required is True
    assert fields["username"].is_file is False
    assert fields["remember"].required is False
    assert fields["remember"].is_file is False


def test_request_template_builds_multipart_fields_and_file_inputs():
    app = FastAPI(title="Upload API")

    @app.post("/upload")
    async def upload(
        description: str = Form(),
        document: UploadFile = File(),
    ):
        return {"description": description, "filename": document.filename}

    api_map = inspect_app(app)
    template = build_request_template(api_map, "POST /upload")

    assert template.body_content_type == "multipart/form-data"
    fields = {field.name: field for field in template.body_fields}
    assert fields["description"].required is True
    assert fields["description"].is_file is False
    assert fields["document"].required is True
    assert fields["document"].is_file is True
    assert fields["document"].type_name == "file"

def test_request_template_detects_multiple_upload_files():
    app = FastAPI(title="Batch Upload API")

    @app.post("/batch-upload")
    async def batch_upload(
        documents: list[UploadFile] = File(),
    ):
        return {"count": len(documents)}

    api_map = inspect_app(app)
    template = build_request_template(api_map, "POST /batch-upload")

    assert template.body_content_type == "multipart/form-data"
    fields = {field.name: field for field in template.body_fields}
    assert fields["documents"].is_file is True
    assert fields["documents"].multiple is True
    assert fields["documents"].type_name == "file[]"

def test_form_and_file_422_issues_map_to_request_fields():
    app = FastAPI(title="Upload Validation API")

    @app.post("/upload-validation")
    async def upload_validation(
        description: str = Form(),
        document: UploadFile = File(),
    ):
        return {
            "description": description,
            "filename": document.filename,
        }

    api_map = inspect_app(app)
    issues = validation_issues(
        api_map,
        "POST /upload-validation",
        {
            "detail": [
                {
                    "type": "missing",
                    "loc": ["body", "description"],
                    "msg": "Field required",
                    "input": None,
                },
                {
                    "type": "missing",
                    "loc": ["body", "document"],
                    "msg": "Field required",
                    "input": None,
                },
            ]
        },
    )

    assert len(issues) == 2
    assert issues[0].expected_type == "string"
    assert issues[0].source is not None
    assert issues[0].source.qualname.endswith(".upload_validation")
    assert issues[1].expected_type == "file"
    assert issues[1].source is not None
    assert issues[1].source.qualname.endswith(".upload_validation")

