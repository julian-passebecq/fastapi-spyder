"""Headless inspection command used by tests, CI and the Spyder UI."""

from __future__ import annotations

import argparse
import importlib
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from fastapi import FastAPI

from spyder_fastapi.core import inspect_app


def load_app(target: str) -> FastAPI:
    """Load ``module:attribute`` and ensure it is a FastAPI application."""

    if ":" not in target:
        raise ValueError("App target must use module:attribute syntax, e.g. app.main:app")
    module_name, attribute = target.split(":", 1)
    module = importlib.import_module(module_name)
    app: Any = getattr(module, attribute)
    if not isinstance(app, FastAPI):
        raise TypeError(f"{target} is not a FastAPI instance")
    return app


def inspect_target(target: str):
    """Inspect a target while keeping stdout clean for machine-readable JSON.

    User modules sometimes print during import or OpenAPI construction. The
    Spyder UI consumes stdout as JSON, so application output is captured and
    forwarded to stderr instead.
    """

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
        model = inspect_app(load_app(target))

    noise = captured_stdout.getvalue() + captured_stderr.getvalue()
    return model, noise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fastapi-spyder",
        description="Inspect a FastAPI app into the FastAPI Studio JSON bridge.",
    )
    parser.add_argument("app", help="FastAPI target in module:attribute form")
    parser.add_argument(
        "--indent", type=int, default=2, help="JSON indentation (default: 2)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        model, noise = inspect_target(args.app)
    except Exception as exc:  # User application imports may raise arbitrary errors.
        print(
            f"fastapi-spyder: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    if noise:
        print("[application output captured during inspection]", file=sys.stderr)
        print(noise.rstrip(), file=sys.stderr)

    print(model.model_dump_json(by_alias=True, indent=args.indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
