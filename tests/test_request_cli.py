import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from spyder_fastapi.request_cli import execute_request, main


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if not raw:
            body = None
        elif content_type.startswith("application/json"):
            body = json.loads(raw.decode("utf-8"))
        elif content_type.startswith("application/x-www-form-urlencoded"):
            body = parse_qs(raw.decode("utf-8"))
        elif content_type.startswith("multipart/form-data"):
            body = {"raw": raw.decode("latin-1")}
        else:
            body = raw.decode("utf-8", errors="replace")

        if parsed.path == "/validation":
            payload = {
                "detail": [
                    {
                        "type": "missing",
                        "loc": ["body", "name"],
                        "msg": "Field required",
                        "input": body,
                    }
                ]
            }
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        payload = {
            "path": parsed.path,
            "query": parse_qs(parsed.query),
            "authorization": self.headers.get("Authorization"),
            "cookie": self.headers.get("Cookie"),
            "body": body,
            "content_type": content_type,
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_request_runner_builds_url_query_headers_cookies_and_json_body():
    server, thread = _serve()
    try:
        host, port = server.server_address
        result = execute_request(
            {
                "method": "POST",
                "base_url": f"http://{host}:{port}",
                "path": "/items/{item_id}",
                "path_params": {"item_id": "a/b"},
                "query": {"limit": 3},
                "headers": {"Authorization": "Bearer token"},
                "cookies": {"session": "abc"},
                "body": {"name": "Book"},
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.error is None
    assert result.status_code == 200
    assert result.json_body["path"] == "/items/a%2Fb"
    assert result.json_body["query"] == {"limit": ["3"]}
    assert result.json_body["authorization"] == "Bearer token"
    assert result.json_body["cookie"] == "session=abc"
    assert result.json_body["body"] == {"name": "Book"}
    assert result.elapsed_ms is not None


def test_request_runner_preserves_fastapi_style_422_json():
    server, thread = _serve()
    try:
        host, port = server.server_address
        result = execute_request(
            {
                "method": "POST",
                "base_url": f"http://{host}:{port}",
                "path": "/validation",
                "body": {},
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.error is None
    assert result.status_code == 422
    assert result.json_body["detail"][0]["loc"] == ["body", "name"]


def test_request_runner_stdin_stdout_protocol(monkeypatch, capsys):
    server, thread = _serve()
    try:
        host, port = server.server_address
        command = {
            "method": "POST",
            "base_url": f"http://{host}:{port}",
            "path": "/echo",
            "body": {"value": 7},
        }
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps(command)),
        )

        exit_code = main()
        captured = capsys.readouterr()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status_code"] == 200
    assert payload["json_body"]["body"] == {"value": 7}

def test_request_runner_sends_urlencoded_form():
    server, thread = _serve()
    try:
        host, port = server.server_address
        result = execute_request(
            {
                "method": "POST",
                "base_url": f"http://{host}:{port}",
                "path": "/form",
                "form": {
                    "username": "alice",
                    "remember": "true",
                },
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.error is None
    assert result.status_code == 200
    assert result.json_body["content_type"].startswith(
        "application/x-www-form-urlencoded"
    )
    assert result.json_body["body"] == {
        "username": ["alice"],
        "remember": ["true"],
    }


def test_request_runner_sends_multipart_form_and_file(tmp_path):
    upload = tmp_path / "hello.txt"
    upload.write_text("hello multipart", encoding="utf-8")

    server, thread = _serve()
    try:
        host, port = server.server_address
        result = execute_request(
            {
                "method": "POST",
                "base_url": f"http://{host}:{port}",
                "path": "/upload",
                "multipart": {
                    "description": "sample document",
                },
                "files": {
                    "document": str(upload),
                },
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.error is None
    assert result.status_code == 200
    assert result.json_body["content_type"].startswith("multipart/form-data; boundary=")

    raw = result.json_body["body"]["raw"]
    assert 'name="description"' in raw
    assert "sample document" in raw
    assert 'name="document"; filename="hello.txt"' in raw
    assert "Content-Type: text/plain" in raw
    assert "hello multipart" in raw


def test_request_runner_rejects_missing_upload_file(tmp_path):
    import pytest

    missing = tmp_path / "missing.bin"

    with pytest.raises(ValueError, match="Upload file does not exist"):
        execute_request(
            {
                "method": "POST",
                "base_url": "http://127.0.0.1:1",
                "path": "/upload",
                "files": {"document": str(missing)},
            }
        )

def test_request_runner_sends_multiple_files_for_same_field(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first payload", encoding="utf-8")
    second.write_text("second payload", encoding="utf-8")

    server, thread = _serve()
    try:
        host, port = server.server_address
        result = execute_request(
            {
                "method": "POST",
                "base_url": f"http://{host}:{port}",
                "path": "/batch",
                "files": {
                    "documents": [str(first), str(second)],
                },
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.error is None
    raw = result.json_body["body"]["raw"]
    assert raw.count('name="documents"') == 2
    assert 'filename="first.txt"' in raw
    assert 'filename="second.txt"' in raw
    assert "first payload" in raw
    assert "second payload" in raw

def test_request_runner_repeats_form_values_for_lists():
    server, thread = _serve()
    try:
        host, port = server.server_address
        result = execute_request(
            {
                "method": "POST",
                "base_url": f"http://{host}:{port}",
                "path": "/form-list",
                "form": {
                    "values": ["one", "two", "three"],
                },
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.error is None
    assert result.json_body["body"] == {
        "values": ["one", "two", "three"],
    }


def test_request_runner_repeats_multipart_values_for_lists(tmp_path):
    upload = tmp_path / "doc.txt"
    upload.write_text("payload", encoding="utf-8")

    server, thread = _serve()
    try:
        host, port = server.server_address
        result = execute_request(
            {
                "method": "POST",
                "base_url": f"http://{host}:{port}",
                "path": "/multipart-list",
                "multipart": {
                    "tags": ["alpha", "beta"],
                },
                "files": {"document": str(upload)},
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result.error is None
    raw = result.json_body["body"]["raw"]
    assert raw.count('name="tags"') == 2
    assert "alpha" in raw
    assert "beta" in raw

