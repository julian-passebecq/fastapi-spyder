"""Realistic fake client for exercising FastAPI Studio end to end.

The app is deliberately small but covers the development signals that matter to
FastAPI Studio: Pydantic contracts, nested dependencies, 422 validation,
background tasks, server errors, slow handlers and child OpenTelemetry spans.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    status,
)
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from pydantic import BaseModel, Field, field_validator


app = FastAPI(
    title="Data Platform Demo API",
    version="0.1.0",
)

_tracer = trace.get_tracer("fastapi-spyder.data-platform-demo")


class TenantContext(BaseModel):
    tenant_id: str
    actor: str


class IngestionEvent(BaseModel):
    source: str = Field(min_length=2, examples=["crm"])
    record_count: int = Field(gt=0, le=100_000)
    schema_version: int = Field(ge=1, le=20)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("source cannot be blank")
        return normalized


class IngestionReceipt(BaseModel):
    ingestion_id: str
    tenant_id: str
    accepted_records: int
    state: str
    accepted_at: datetime


class JobStatus(BaseModel):
    job_id: str
    tenant_id: str
    state: str
    attempts: int


def require_api_key(x_api_key: str = Header()) -> str:
    if x_api_key != "demo-secret":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid demo API key",
        )
    return x_api_key


def get_tenant_context(
    x_tenant_id: str = Header(),
    x_actor: str = Header(default="spyder-demo"),
    _api_key: str = Depends(require_api_key),
) -> TenantContext:
    return TenantContext(
        tenant_id=x_tenant_id,
        actor=x_actor,
    )


async def fake_database_operation(
    operation: str,
    *,
    namespace: str,
    delay_ms: int,
) -> None:
    """Emit a DB-shaped child span without requiring a real database."""

    with _tracer.start_as_current_span(
        f"{operation} {namespace}",
        kind=SpanKind.CLIENT,
        attributes={
            "db.system.name": "postgresql",
            "db.namespace": "demo_platform",
            "db.operation.name": operation,
            "server.address": "postgres.demo.internal",
        },
    ):
        await asyncio.sleep(delay_ms / 1000)


async def fake_http_call(
    method: str,
    *,
    service: str,
    delay_ms: int,
) -> None:
    """Emit an HTTP-client-shaped span without making an external request."""

    with _tracer.start_as_current_span(
        f"{method} {service}",
        kind=SpanKind.CLIENT,
        attributes={
            "http.request.method": method,
            "server.address": service,
            "url.full": f"https://{service}/events",
        },
    ):
        await asyncio.sleep(delay_ms / 1000)


async def publish_ingestion_event(
    ingestion_id: str,
    tenant_id: str,
) -> None:
    """Background work that remains attached to the FastAPI request trace."""

    with _tracer.start_as_current_span(
        "publish ingestion.accepted",
        kind=SpanKind.PRODUCER,
        attributes={
            "messaging.system": "kafka",
            "messaging.destination.name": "ingestion.accepted",
            "messaging.operation.type": "publish",
            "demo.ingestion_id": ingestion_id,
            "demo.tenant_id": tenant_id,
        },
    ):
        await asyncio.sleep(0.025)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/v1/ingestions",
    response_model=IngestionReceipt,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["ingestion"],
)
async def create_ingestion(
    event: IngestionEvent,
    background_tasks: BackgroundTasks,
    context: TenantContext = Depends(get_tenant_context),
) -> IngestionReceipt:
    await fake_database_operation(
        "INSERT",
        namespace="ingestions",
        delay_ms=18,
    )
    await fake_http_call(
        "POST",
        service="schema-registry.demo.internal",
        delay_ms=12,
    )

    ingestion_id = f"ing_{uuid4().hex[:10]}"
    background_tasks.add_task(
        publish_ingestion_event,
        ingestion_id,
        context.tenant_id,
    )

    return IngestionReceipt(
        ingestion_id=ingestion_id,
        tenant_id=context.tenant_id,
        accepted_records=event.record_count,
        state="accepted",
        accepted_at=datetime.now(timezone.utc),
    )


@app.get(
    "/v1/jobs/{job_id}",
    response_model=JobStatus,
    tags=["jobs"],
)
async def get_job(
    job_id: str,
    context: TenantContext = Depends(get_tenant_context),
) -> JobStatus:
    await fake_database_operation(
        "SELECT",
        namespace="jobs",
        delay_ms=14,
    )
    if job_id == "missing":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo job not found",
        )
    return JobStatus(
        job_id=job_id,
        tenant_id=context.tenant_id,
        state="running",
        attempts=1,
    )


@app.post(
    "/v1/jobs/{job_id}/retry",
    response_model=JobStatus,
    tags=["jobs"],
)
async def retry_job(
    job_id: str,
    context: TenantContext = Depends(get_tenant_context),
) -> JobStatus:
    await fake_database_operation(
        "UPDATE",
        namespace="jobs",
        delay_ms=22,
    )
    return JobStatus(
        job_id=job_id,
        tenant_id=context.tenant_id,
        state="queued",
        attempts=2,
    )


@app.get("/v1/slow", tags=["debug"])
async def slow_route(
    milliseconds: int = Query(default=80, ge=0, le=500),
) -> dict[str, int]:
    await asyncio.sleep(milliseconds / 1000)
    return {"slept_ms": milliseconds}


@app.get("/v1/debug/fail", tags=["debug"])
def fail_route() -> None:
    raise RuntimeError("Intentional demo failure for FastAPI Studio telemetry")
