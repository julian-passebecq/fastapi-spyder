"""Small uvicorn launcher intentionally executed by Spyder's native debugger."""

from __future__ import annotations

import argparse
import importlib
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fastapi-spyder-debug-server",
        description="Run one FastAPI target under Spyder's debugfile workflow.",
    )
    parser.add_argument("target", help="FastAPI target in module:attribute form")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def _load_uvicorn() -> Any:
    try:
        return importlib.import_module("uvicorn")
    except ImportError as exc:
        raise RuntimeError(
            "uvicorn is required in the selected Spyder Python environment "
            "to run the FastAPI debug server. Install it with your project's "
            "normal dependency workflow."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    uvicorn = _load_uvicorn()
    uvicorn.run(
        args.target,
        host=args.host,
        port=args.port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
