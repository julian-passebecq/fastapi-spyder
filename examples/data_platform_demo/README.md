# Data Platform Demo Client

This is a deliberately small but realistic FastAPI project used to validate
FastAPI Studio as a development tool.

It is not a benchmark and it does not require a real database, Kafka broker or
schema registry. Those calls are represented by OpenTelemetry child spans so
the Studio can exercise its runtime views without external infrastructure.

## What it exercises

The app contains:

- Pydantic request/response contracts;
- nested FastAPI dependencies;
- header authentication and tenant context;
- successful requests;
- HTTP 404;
- FastAPI/Pydantic 422 validation;
- an intentional unhandled 500;
- a slow async route;
- a FastAPI `BackgroundTasks` operation;
- database-shaped OpenTelemetry CLIENT spans;
- HTTP-client-shaped OpenTelemetry CLIENT spans;
- Kafka-shaped OpenTelemetry PRODUCER spans;
- project tests that FastAPI Studio can link back to routes.

## Use it in Spyder

Open this directory as the working directory:

```text
examples/data_platform_demo
```

In **FastAPI Studio**:

1. Click **Discover**.
2. Select `app:app`.
3. Click **Inspect**.
4. Explore Routes, Models, Dependencies, Tests, Lineage and Diagram.
5. Open Request Lab.
6. For authenticated routes use:
   - `x-api-key: demo-secret`
   - `x-tenant-id: contoso`
7. Start the local debug server from Request Lab.

The native Telemetry tab should become LIVE when the selected FastAPI
environment exposes the current native OpenTelemetry capability.

## Generate representative traffic

With the debug server listening on port 8000:

```bash
python load.py
```

The load script intentionally produces a mixture of:

```text
200 health
202 ingestion accepted
422 ingestion validation failure
200 job lookup
404 job missing
200 job retry
200 slow request
500 intentional failure
```

This gives the Telemetry tab useful data immediately.

## Expected trace shape

The successful ingestion route is intended to look approximately like:

```text
POST /v1/ingestions
|
+-- fastapi.dependencies
|
+-- fastapi.endpoint
|   |
|   +-- INSERT ingestions
|   |     category: database
|   |     target: postgresql:demo_platform
|   |
|   +-- POST schema-registry.demo.internal
|         category: http-client
|         target: schema-registry.demo.internal
|
+-- fastapi.serialization
|
+-- fastapi.background_task
    |
    +-- publish ingestion.accepted
          category: messaging
          target: ingestion.accepted
```

The exact durations are runtime observations, not fixed expectations.

## Run the example tests

From the repository root:

```bash
pytest examples/data_platform_demo/tests
```

These tests are also useful static project evidence for FastAPI Studio's
route-to-test explorer.
