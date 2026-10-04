"""Static route-to-test discovery without importing test modules."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

from spyder_fastapi.models import (
    FastAPIMap,
    RouteTestIndex,
    SourceRef,
    TestReference,
)


_HTTP_METHODS = {
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
}
_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}
_DYNAMIC = "{*}"


def _candidate_test_files(root: Path, max_files: int) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".py" else []

    candidates: set[Path] = set()
    patterns = ("test_*.py", "*_test.py")
    for pattern in patterns:
        for path in root.rglob(pattern):
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            candidates.add(path)
            if len(candidates) >= max_files:
                return sorted(candidates)

    for tests_dir in root.rglob("tests"):
        if not tests_dir.is_dir():
            continue
        if any(part in _IGNORED_DIRS for part in tests_dir.parts):
            continue
        for path in tests_dir.rglob("*.py"):
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            candidates.add(path)
            if len(candidates) >= max_files:
                return sorted(candidates)

    return sorted(candidates)


def _string_value(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(_DYNAMIC)
            else:
                return None
        return "".join(parts)

    return None


def _keyword_value(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _request_call(call: ast.Call) -> tuple[str, str] | None:
    function = call.func
    if not isinstance(function, ast.Attribute):
        return None

    method_name = function.attr.casefold()

    if method_name in _HTTP_METHODS:
        path_node = (
            call.args[0]
            if call.args
            else _keyword_value(call, "url")
            or _keyword_value(call, "path")
        )
        path = _string_value(path_node)
        if path and path.startswith(("/", "http://", "https://")):
            return method_name.upper(), path
        return None

    if method_name != "request":
        return None

    method_node = (
        call.args[0]
        if call.args
        else _keyword_value(call, "method")
    )
    path_node = (
        call.args[1]
        if len(call.args) > 1
        else _keyword_value(call, "url")
        or _keyword_value(call, "path")
    )
    method = _string_value(method_node)
    path = _string_value(path_node)
    if not method or not path:
        return None

    normalized_method = method.upper()
    if normalized_method.casefold() not in _HTTP_METHODS:
        return None
    if not path.startswith(("/", "http://", "https://")):
        return None
    return normalized_method, path


def _request_path(value: str) -> str:
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        return parsed.path or "/"
    return value.split("?", 1)[0].split("#", 1)[0] or "/"


def _route_pattern(path: str) -> re.Pattern[str]:
    chunks: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", path):
        chunks.append(re.escape(path[cursor:match.start()]))
        chunks.append(r"[^/]+")
        cursor = match.end()
    chunks.append(re.escape(path[cursor:]))
    return re.compile("^" + "".join(chunks) + "$")


def _match_route(
    api_map: FastAPIMap,
    method: str,
    requested_path: str,
):
    path = _request_path(requested_path)
    method = method.upper()

    exact = [
        route
        for route in api_map.routes
        if route.method == method and route.path == path
    ]
    if exact:
        return exact[0], "exact"

    candidates = [
        route
        for route in api_map.routes
        if route.method == method and _route_pattern(route.path).match(path)
    ]
    if len(candidates) == 1:
        return candidates[0], "template"

    return None


class _TestCallVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path, api_map: FastAPIMap):
        self.file_path = file_path
        self.api_map = api_map
        self.references: list[TestReference] = []
        self._classes: list[str] = []
        self._functions: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef):
        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    def visit_Call(self, node: ast.Call):
        if not self._functions:
            self.generic_visit(node)
            return

        function_name = self._functions[-1]
        if not function_name.startswith("test_"):
            self.generic_visit(node)
            return

        request = _request_call(node)
        if request is not None:
            method, requested_path = request
            match = _match_route(self.api_map, method, requested_path)
            if match is not None:
                route, match_kind = match
                qualname = ".".join(
                    [*self._classes, *self._functions]
                )
                line = getattr(node, "lineno", None)
                ref_id = (
                    f"test:{self.file_path}:{line or 0}:"
                    f"{method}:{route.id}"
                )
                self.references.append(
                    TestReference(
                        id=ref_id,
                        route_id=route.id,
                        test_name=qualname,
                        method=method,
                        requested_path=requested_path,
                        match_kind=match_kind,
                        source=SourceRef(
                            file=str(self.file_path),
                            line=line,
                            execution_line=line,
                            qualname=qualname,
                        ),
                    )
                )

        self.generic_visit(node)


def discover_route_tests(
    root: str | Path,
    api_map: FastAPIMap,
    *,
    max_files: int = 2000,
) -> RouteTestIndex:
    """Link literal/f-string HTTP calls in tests to known FastAPI routes.

    This is deliberately static and conservative: test modules are never
    imported, and only calls inside functions named test_* are linked.
    """

    root_path = Path(root).resolve()
    if not root_path.exists():
        return RouteTestIndex()

    files = _candidate_test_files(root_path, max_files)
    references: list[TestReference] = []

    for file_path in files:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        visitor = _TestCallVisitor(file_path, api_map)
        visitor.visit(tree)
        references.extend(visitor.references)

    references.sort(
        key=lambda reference: (
            reference.route_id,
            reference.source.file or "",
            reference.source.line or 0,
            reference.test_name,
        )
    )
    return RouteTestIndex(
        references=references,
        scanned_files=len(files),
    )


def tests_for_route(
    index: RouteTestIndex,
    route_id: str,
) -> list[TestReference]:
    """Return all discovered test references for one FastAPI route."""

    return [
        reference
        for reference in index.references
        if reference.route_id == route_id
    ]
