import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from spyder_fastapi.request_cli import execute_request


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw.decode("utf-8")) if raw else None

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
