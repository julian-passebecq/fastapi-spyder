"""Isolated HTTP request runner used by FastAPI Studio's Request Lab."""

from __future__ import annotations

import json
import sys
import time
from http.client import responses as HTTP_REASONS
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from spyder_fastapi.models import RequestExecution


def _build_url(command: dict[str, Any]) -> str:
    base_url = str(command.get("base_url") or "").rstrip("/") + "/"
    path = str(command.get("path") or "").lstrip("/")

    path_params = command.get("path_params") or {}
    if not isinstance(path_params, dict):
        raise ValueError("path_params must be an object")

    for name, value in path_params.items():
        path = path.replace(
            "{" + str(name) + "}",
            quote(str(value), safe=""),
        )

    if "{" in path or "}" in path:
        raise ValueError(f"Missing path parameter for {path!r}")

    url = urljoin(base_url, path)

    query = command.get("query") or {}
    if not isinstance(query, dict):
        raise ValueError("query must be an object")

    if query:
        parts = urlsplit(url)
        encoded = urlencode(
            [(str(key), str(value)) for key, value in query.items()],
            doseq=True,
        )
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, encoded, parts.fragment)
        )

    return url


def execute_request(command: dict[str, Any]) -> RequestExecution:
    """Execute one HTTP request using only the Python standard library."""

    method = str(command.get("method") or "GET").upper()
    url = _build_url(command)
    timeout = float(command.get("timeout") or 10.0)

    headers = command.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("headers must be an object")
    request_headers = {str(key): str(value) for key, value in headers.items()}

    cookies = command.get("cookies") or {}
    if not isinstance(cookies, dict):
        raise ValueError("cookies must be an object")
    if cookies:
        cookie_value = "; ".join(
            f"{key}={value}" for key, value in cookies.items()
        )
        existing = request_headers.get("Cookie")
        request_headers["Cookie"] = (
            f"{existing}; {cookie_value}" if existing else cookie_value
        )

    data: bytes | None = None
    if "body" in command and command.get("body") is not None:
        data = json.dumps(command["body"]).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Accept", "application/json")

    request = Request(
        url=url,
        data=data,
        headers=request_headers,
        method=method,
    )

    started = time.perf_counter()
    try:
        response = urlopen(request, timeout=timeout)
        status_code = int(response.status)
        reason = getattr(response, "reason", None) or HTTP_REASONS.get(status_code)
        raw = response.read()
        response_headers = dict(response.headers.items())
    except HTTPError as exc:
        status_code = int(exc.code)
        reason = exc.reason or HTTP_REASONS.get(status_code)
        raw = exc.read()
        response_headers = dict(exc.headers.items()) if exc.headers else {}
    except URLError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return RequestExecution(
            url=url,
            elapsed_ms=elapsed_ms,
            error=str(exc.reason),
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    charset = "utf-8"
    content_type = response_headers.get("Content-Type", "")
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()

    text = raw.decode(charset, errors="replace")
    json_body: Any = None
    if text:
        try:
            json_body = json.loads(text)
        except json.JSONDecodeError:
            pass

    return RequestExecution(
        url=url,
        status_code=status_code,
        reason=str(reason) if reason is not None else None,
        headers=response_headers,
        text=text,
        json_body=json_body,
        elapsed_ms=elapsed_ms,
    )


def main() -> int:
    try:
        payload = sys.stdin.read()
        command = json.loads(payload)
        if not isinstance(command, dict):
            raise ValueError("request command must be a JSON object")
        result = execute_request(command)
    except Exception as exc:
        result = RequestExecution(
            url="",
            error=f"{exc.__class__.__name__}: {exc}",
        )

    print(result.model_dump_json(indent=2))
    return 0 if result.error is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
