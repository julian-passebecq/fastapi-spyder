from fastapi import FastAPI

from spyder_fastapi.core import discover_route_tests, inspect_app, tests_for_route


def build_app() -> FastAPI:
    app = FastAPI(title="Test Links API")

    @app.get("/users/{user_id}")
    def get_user(user_id: int):
        return {"id": user_id}

    @app.post("/users")
    def create_user():
        return {"ok": True}

    return app


def test_discovers_literal_template_request_and_keyword_calls(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_users.py"
    test_file.write_text(
        """
class TestUsers:
    def test_get_user(self, client):
        response = client.get("/users/42")
        assert response.status_code == 200

    async def test_create_user(self, async_client):
        response = await async_client.post(url="/users")
        assert response.status_code == 200

def test_request_method(ac, user_id):
    response = ac.request("GET", f"/users/{user_id}")
    assert response.status_code == 200

def helper(client):
    return client.get("/users/99")

def test_not_a_http_client():
    payload = {}
    assert payload.get("/users/42") is None
""".strip()
        + "\n",
        encoding="utf-8",
    )

    api_map = inspect_app(build_app())
    index = discover_route_tests(tmp_path, api_map)

    assert index.scanned_files == 1
    assert len(index.references) == 3

    get_refs = tests_for_route(index, "GET /users/{user_id}")
    assert len(get_refs) == 2
    assert {ref.match_kind for ref in get_refs} == {"template"}
    assert {
        ref.test_name for ref in get_refs
    } == {
        "TestUsers.test_get_user",
        "test_request_method",
    }

    post_refs = tests_for_route(index, "POST /users")
    assert len(post_refs) == 1
    assert post_refs[0].match_kind == "exact"
    assert post_refs[0].test_name == "TestUsers.test_create_user"


def test_supports_full_urls_and_ignores_unknown_routes(tmp_path):
    test_file = tmp_path / "api_test.py"
    test_file.write_text(
        """
def test_full_url(client):
    client.get("http://testserver/users/123?include=profile")

def test_unknown(client):
    client.get("/missing")
""".strip()
        + "\n",
        encoding="utf-8",
    )

    api_map = inspect_app(build_app())
    index = discover_route_tests(tmp_path, api_map)

    assert len(index.references) == 1
    ref = index.references[0]
    assert ref.route_id == "GET /users/{user_id}"
    assert ref.requested_path == "http://testserver/users/123?include=profile"
    assert ref.match_kind == "template"


def test_test_discovery_never_imports_test_modules(tmp_path):
    test_file = tmp_path / "test_dangerous.py"
    test_file.write_text(
        """
raise RuntimeError("must never import test module")

def test_route(client):
    client.post("/users")
""".strip()
        + "\n",
        encoding="utf-8",
    )

    api_map = inspect_app(build_app())
    index = discover_route_tests(tmp_path, api_map)

    assert [ref.route_id for ref in index.references] == ["POST /users"]
