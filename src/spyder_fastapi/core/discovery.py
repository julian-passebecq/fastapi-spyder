"""Static discovery of FastAPI application objects without importing user code."""

from __future__ import annotations

import ast
from pathlib import Path

from spyder_fastapi.models import AppCandidate, SourceRef


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


def _module_name(root: Path, file_path: Path) -> str:
    relative = file_path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _fastapi_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    constructor_names: set[str] = set()
    module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fastapi":
            for alias in node.names:
                if alias.name == "FastAPI":
                    constructor_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "fastapi":
                    module_aliases.add(alias.asname or alias.name)

    return constructor_names, module_aliases


def _is_fastapi_call(
    call: ast.Call,
    constructor_names: set[str],
    module_aliases: set[str],
) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in constructor_names
    if isinstance(func, ast.Attribute) and func.attr == "FastAPI":
        return isinstance(func.value, ast.Name) and func.value.id in module_aliases
    return False


def _assignment_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def discover_targets(
    root: str | Path,
    *,
    max_files: int = 2000,
) -> list[AppCandidate]:
    """Find simple `name = FastAPI(...)` targets below `root`.

    Discovery is intentionally static. It never imports project modules, so
    clicking "Discover" in Spyder cannot trigger application startup side effects.
    """

    root_path = Path(root).resolve()
    if not root_path.exists():
        return []

    files: list[Path]
    if root_path.is_file():
        files = [root_path] if root_path.suffix == ".py" else []
        scan_root = root_path.parent
    else:
        scan_root = root_path
        files = []
        for path in root_path.rglob("*.py"):
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            files.append(path)
            if len(files) >= max_files:
                break

    candidates: list[AppCandidate] = []
    for file_path in sorted(files):
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        constructor_names, module_aliases = _fastapi_aliases(tree)
        if not constructor_names and not module_aliases:
            continue

        module = _module_name(scan_root, file_path)
        if not module:
            continue

        for node in tree.body:
            value = None
            if isinstance(node, ast.Assign):
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                value = node.value

            if not isinstance(value, ast.Call):
                continue
            if not _is_fastapi_call(value, constructor_names, module_aliases):
                continue

            for attribute in _assignment_names(node):
                candidates.append(
                    AppCandidate(
                        target=f"{module}:{attribute}",
                        module=module,
                        attribute=attribute,
                        source=SourceRef(
                            file=str(file_path),
                            line=getattr(node, "lineno", None),
                            qualname=f"{module}.{attribute}",
                        ),
                    )
                )

    candidates.sort(key=lambda candidate: candidate.target)
    return candidates
