"""Isolated HTTP request runner used by FastAPI Studio's Request Lab."""

from __future__ import annotations

import json
import mimetypes
import secrets
import sys
import time
from pathlib import Path
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


def _form_body(
    values: dict[str, Any],
) -> tuple[bytes, str]:
    encoded = urlencode(
        [(str(key), str(value)) for key, value in values.items()],
        doseq=True,
    ).encode("utf-8")
    return encoded, "application/x-www-form-urlencoded"


def _multipart_body(
    values: dict[str, Any],
    files: dict[str, Any],
) -> tuple[bytes, str]:
    boundary = "----fastapi-spyder-" + secrets.token_hex(12)
    chunks: list[bytes] = []

    def add_line(value: str = "") -> None:
        chunks.append(value.encode("utf-8") + b"\r\n")

    for name, value in values.items():
        add_line(f"--{boundary}")
        add_line(
            f'Content-Disposition: form-data; name="{str(name)}"'
        )
        add_line()
        add_line(str(value))

    for name, raw_paths in files.items():
        if isinstance(raw_paths, (list, tuple)):
            upload_paths = list(raw_paths)
        else:
            upload_paths = [raw_paths]

        for raw_path in upload_paths:
            path = Path(str(raw_path)).expanduser()
            if not path.is_file():
                raise ValueError(f"Upload file does not exist: {path}")

            content_type = (
                mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            )
            safe_name = path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
            safe_field = str(name).replace('"', "_").replace("\r", "_").replace("\n", "_")

            add_line(f"--{boundary}")
            add_line(
                f'Content-Disposition: form-data; name="{safe_field}"; '
                f'filename="{safe_name}"'
            )
            add_line(f"Content-Type: {content_type}")
            add_line()
            chunks.append(path.read_bytes())
            chunks.append(b"\r\n")

    add_line(f"--{boundary}--")
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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

    body_modes = [
        command.get("body") is not None,
        bool(command.get("form")),
        bool(command.get("multipart")) or bool(command.get("files")),
    ]
    if sum(bool(mode) for mode in body_modes) > 1:
        raise ValueError(
            "request command must use only one body mode: body, form, or multipart/files"
        )

    if "body" in command and command.get("body") is not None:
        data = json.dumps(command["body"]).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Accept", "application/json")
    elif command.get("form"):
        form = command.get("form")
        if not isinstance(form, dict):
            raise ValueError("form must be an object")
        data, content_type = _form_body(form)
        request_headers.setdefault("Content-Type", content_type)
        request_headers.setdefault("Accept", "application/json")
    elif command.get("multipart") or command.get("files"):
        multipart = command.get("multipart") or {}
        files = command.get("files") or {}
        if not isinstance(multipart, dict):
            raise ValueError("multipart must be an object")
        if not isinstance(files, dict):
            raise ValueError("files must be an object")
        data, content_type = _multipart_body(multipart, files)
        request_headers.setdefault("Content-Type", content_type)
        request_headers.setdefault("Accept", "application/json")

    request = Request(
        url=url,
        data=data,
        headers=request_headers,
        method=method,
    )

    started = time.perf_counter()
    final_url = url
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = int(response.status)
            reason = getattr(response, "reason", None) or HTTP_REASONS.get(status_code)
            raw = response.read()
            response_headers = dict(response.headers.items())
            final_url = response.geturl()
    except HTTPError as exc:
        try:
            status_code = int(exc.code)
            reason = exc.reason or HTTP_REASONS.get(status_code)
            raw = exc.read()
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            final_url = exc.geturl()
        finally:
            exc.close()
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
        url=final_url,
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
