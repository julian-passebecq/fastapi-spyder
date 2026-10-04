"""Headless inspection command used by tests, CI and eventually the Spyder UI."""

from __future__ import annotations

import argparse
import importlib
import sys
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
        model = inspect_app(load_app(args.app))
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        print(f"fastapi-spyder: {exc}", file=sys.stderr)
        return 2

    print(model.model_dump_json(by_alias=True, indent=args.indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
