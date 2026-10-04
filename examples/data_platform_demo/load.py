"""Generate mixed local traffic for the Data Platform Demo API."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate representative FastAPI Studio telemetry."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
    )
    return parser


def _headers() -> dict[str, str]:
    return {
        "x-api-key": "demo-secret",
        "x-tenant-id": "contoso",
        "x-actor": "telemetry-load",
    }


def main() -> int:
    args = build_parser().parse_args()
    base_url = args.base_url.rstrip("/")
    headers = _headers()

    cases: list[tuple[str, Callable[[httpx.Client], httpx.Response]]] = [
        ("health", lambda client: client.get("/health")),
        (
            "ingestion accepted",
            lambda client: client.post(
                "/v1/ingestions",
                headers=headers,
                json={
                    "source": "crm",
                    "record_count": 250,
                    "schema_version": 3,
                    "payload": {"batch": "demo"},
                },
            ),
        ),
        (
            "ingestion validation 422",
            lambda client: client.post(
                "/v1/ingestions",
                headers=headers,
                json={
                    "source": "crm",
                    "record_count": 0,
                    "schema_version": 3,
                },
            ),
        ),
        (
            "job lookup",
            lambda client: client.get(
                "/v1/jobs/job-42",
                headers=headers,
            ),
        ),
        (
            "job 404",
            lambda client: client.get(
                "/v1/jobs/missing",
                headers=headers,
            ),
        ),
        (
            "job retry",
            lambda client: client.post(
                "/v1/jobs/job-42/retry",
                headers=headers,
            ),
        ),
        (
            "slow route",
            lambda client: client.get(
                "/v1/slow",
                params={"milliseconds": 120},
            ),
        ),
        (
            "intentional 500",
            lambda client: client.get("/v1/debug/fail"),
        ),
    ]

    print(f"Generating telemetry against {base_url}")
    with httpx.Client(
        base_url=base_url,
        timeout=5.0,
    ) as client:
        for label, execute in cases:
            try:
                response = execute(client)
            except httpx.HTTPError as exc:
                print(f"{label:28} transport error: {exc}")
                continue

            elapsed_ms = response.elapsed.total_seconds() * 1000
            print(
                f"{label:28} "
                f"{response.status_code:3d} "
                f"{elapsed_ms:8.1f} ms"
            )
            if response.status_code >= 400:
                try:
                    body = response.json()
                except ValueError:
                    body = response.text
                print(
                    " " * 30
                    + json.dumps(
                        body,
                        ensure_ascii=False,
                    )[:220]
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
