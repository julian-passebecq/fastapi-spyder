"""Persistence helpers for versioned FastAPI Studio snapshots."""

from __future__ import annotations

from pathlib import Path

from spyder_fastapi.models import FastAPIMap, SnapshotEnvelope


CURRENT_SNAPSHOT_FORMAT = 1


def dump_snapshot(
    api_map: FastAPIMap,
    *,
    target: str | None = None,
    indent: int = 2,
) -> str:
    """Serialize an API map into the versioned snapshot envelope."""

    envelope = SnapshotEnvelope(
        format_version=CURRENT_SNAPSHOT_FORMAT,
        target=target,
        api=api_map,
    )
    return envelope.model_dump_json(by_alias=True, indent=indent)


def load_snapshot_text(text: str) -> SnapshotEnvelope:
    """Parse and validate a snapshot document."""

    envelope = SnapshotEnvelope.model_validate_json(text)
    if envelope.format_version != CURRENT_SNAPSHOT_FORMAT:
        raise ValueError(
            "Unsupported FastAPI Studio snapshot format "
            f"{envelope.format_version}; expected {CURRENT_SNAPSHOT_FORMAT}."
        )
    return envelope


def save_snapshot(
    path: str | Path,
    api_map: FastAPIMap,
    *,
    target: str | None = None,
) -> Path:
    """Write a snapshot atomically enough for normal local IDE use."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        dump_snapshot(api_map, target=target) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def load_snapshot(path: str | Path) -> SnapshotEnvelope:
    """Load a versioned FastAPI Studio snapshot from disk."""

    source = Path(path)
    return load_snapshot_text(source.read_text(encoding="utf-8"))
