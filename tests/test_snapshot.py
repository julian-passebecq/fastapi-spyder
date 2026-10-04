from pathlib import Path

import pytest
from fastapi import FastAPI

from spyder_fastapi.core import (
    dump_snapshot,
    inspect_app,
    load_snapshot,
    load_snapshot_text,
    save_snapshot,
)


def build_app():
    app = FastAPI(title="Snapshot API", version="2.0")

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def test_snapshot_round_trip(tmp_path: Path):
    api_map = inspect_app(build_app())
    destination = tmp_path / "baseline.fastapi.json"

    save_snapshot(destination, api_map, target="service.main:app")
    loaded = load_snapshot(destination)

    assert loaded.format_version == 1
    assert loaded.target == "service.main:app"
    assert loaded.api.title == "Snapshot API"
    assert [route.id for route in loaded.api.routes] == ["GET /health"]
    assert loaded.api.model_dump(by_alias=True) == api_map.model_dump(by_alias=True)


def test_snapshot_text_rejects_unknown_format():
    api_map = inspect_app(build_app())
    text = dump_snapshot(api_map).replace(
        '"format_version": 1',
        '"format_version": 999',
        1,
    )

    with pytest.raises(ValueError, match="Unsupported FastAPI Studio snapshot format"):
        load_snapshot_text(text)
