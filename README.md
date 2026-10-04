# FastAPI Spyder

**See your FastAPI as it actually runs.**

`fastapi-spyder` is an external Spyder plugin focused on one framework: FastAPI. It does not fork Spyder and it does not try to become a scheduler, data platform or observability suite. It adds FastAPI-specific architecture knowledge on top of Spyder's existing editor, debugger, variable explorer and profiler.

## Why this project exists

FastAPI makes APIs concise, but important runtime structure is spread across decorators, Pydantic models, `Depends()` trees, source files and generated OpenAPI. Swagger shows the HTTP contract; a generic Python debugger shows stack frames. Neither gives a developer a single map from **request contract to Python implementation**.

FastAPI Studio aims to make that map inspectable and clickable:

```text
HTTP request
    |
    +-- path/query/header/body parameters
    |
    +-- Pydantic validation
    |
    +-- Depends() / nested dependencies
    |
    +-- route handler  <----> Python source line
    |
    +-- response model
    |
    +-- HTTP response
```

The practical goal is faster comprehension and safer changes: answer "what calls this?", "what does this endpoint depend on?", "which routes use this model?", "what breaks if I change it?" and later "where did this request spend its time?" without mentally reconstructing the application.

## Contract and dependency lineage

Lineage is a first-class feature, not a decorative graph.

The first implementation records only relationships FastAPI exposes deterministically:

- route -> request parameters
- body parameter -> Pydantic/OpenAPI model
- route -> dependency -> nested dependency
- route -> Python handler and source location
- handler -> response model
- shared model/dependency -> every route that uses it

This enables useful **impact analysis**. Selecting `BookMetadata`, `get_db`, or `get_current_user` can show every affected endpoint before a change is made.

Example:

```text
POST /books/metadata
        |
        +-- body: book ------> BookMetadata
        |
        +-- Depends() -------> get_request_context
        |                         |
        |                         +--> header: ingestion-id
        |
        +-- handled_by ------> ingest_metadata()  [app.py:37]
                                  |
                                  +--> StoredBook
```

We deliberately do **not** pretend static source inspection can reliably infer arbitrary S3, database, Kafka or HTTP side effects. A later runtime telemetry layer can add observed downstream edges (SQL spans, HTTP calls, queues, storage) to the same graph. Deterministic lineage first; observed runtime lineage second.

## Architecture

```text
FastAPI application
       |
       v
core inspector          (no Qt / no Spyder dependency)
       |
       v
FastAPIMap              (Pydantic model)
       |
       +--> JSON bridge / snapshots / API diff
       |
       +--> Spyder FastAPI panels
       |
       +--> tests / CI / future telemetry
```

Keeping the core headless is intentional. Spyder is the human UI, not the data model.

The Spyder pane also inspects applications in a **separate Python subprocess**. Static discovery never imports the project, and actual FastAPI imports do not run inside Spyder's GUI process. That gives the project a safer path toward debugging real applications with startup side effects.

## Current bootstrap

The repository now contains the v0.1 foundation:

- safe AST-based FastAPI app discovery (no project import)
- subprocess-based application inspection using Spyder's selected Python interpreter
- API route tree with route details
- path/query/header/cookie/body parameter extraction
- Pydantic/OpenAPI schema browser with model source navigation
- raw OpenAPI view alongside the normalized FastAPI map
- recursive `Depends()` discovery, including dependency request parameters
- Python source file + line mapping and double-click editor navigation
- route-scoped lineage tree
- native Qt Diagram tab with route-flow, global-architecture and impact views
- static route-to-test discovery with a dedicated Tests explorer
- optional test-function overlay on architecture diagrams
- dependency and schema blast-radius views
- serializable `FastAPIMap` JSON bridge
- diagnostics view for import/inspection output
- generated Request Lab for path/query/header/cookie inputs and JSON bodies
- isolated HTTP execution with response status/body/timing
- 422 validation visualizer with source navigation
- exact Pydantic field source mapping where inspectable
- in-memory request history with redacted display and exact replay
- observed Request Lab route timing/status overlay in the Diagram tab
- native FastAPI OpenTelemetry dashboard for server traces and FastAPI logs
- per-route request/error/average/P50/P95 summaries from native server spans
- request trace tree with FastAPI dependency/endpoint/serialization spans
- graphical native trace waterfall with relative span timing
- semantic classification of observed DB / outbound HTTP / messaging / RPC spans
- external-span targets surfaced directly in trace trees and waterfalls
- validation and exception KPIs kept separate from 5xx error rate
- native latency timeline without Grafana, WebEngine or a separate frontend
- Spyder-native handler breakpoint handoff using the first executable line
- local uvicorn launch through Spyder's public debugfile/IPython Console API
- headless CLI
- CI across Python 3.11-3.13 plus an offscreen Qt Spyder widget smoke test
- tests, a small bookstore example and a realistic data-platform demo client

Inspect the bookstore example without Spyder:

```bash
pip install -e ".[dev]"
PYTHONPATH=examples/bookstore fastapi-spyder app:app
```

## End-to-end demo client

A more realistic fake client lives in
`examples/data_platform_demo`. It is intentionally self-contained: no real
PostgreSQL, Kafka or schema-registry service is required.

It exercises the complete Studio path:

```text
FastAPI contract
  -> Pydantic models
  -> nested Depends()
  -> static route-to-test links
  -> Request Lab
  -> Spyder debug server
  -> native FastAPI traces/logs
  -> observed DB / HTTP / messaging child spans
  -> Telemetry timeline + waterfall
  -> Diagram server/client overlays
```

Use `examples/data_platform_demo` as the Spyder working directory, discover
`app:app`, start the debug server, then run:

```bash
python load.py
```

The generated local traffic includes successful requests, a 422 validation
failure, a 404, a slow route, an intentional 500 and a background task. See
`examples/data_platform_demo/README.md` for the expected trace shape.

## Route-to-test links

FastAPI Studio can statically scan project test files and connect HTTP calls
back to inspected routes without importing the test suite.

The first implementation recognizes common TestClient/httpx-style calls inside
functions named `test_*`:

```python
def test_get_user(client):
    response = client.get("/users/42")

async def test_create_user(async_client):
    response = await async_client.post(url="/users")
```

Literal paths, full test-server URLs and simple f-string paths are matched
against FastAPI route templates. The Tests pane shows route, test function,
match confidence and source location. Double-click opens the test function.

These links are intentionally classified as **static project evidence**, not
FastAPI contract lineage. They can be overlaid on Route, Global and Impact
diagrams with `route -> tested_by -> test function` edges.

No test module is imported or executed during discovery.

## Native FastAPI telemetry dashboard

Current FastAPI versions provide native OpenTelemetry request traces and logs.
FastAPI Studio attaches a local OpenTelemetry processor before the debug server
imports the application, then renders that native telemetry directly in Spyder.

The local path is intentionally small:

```text
FastAPI native telemetry
        |
        +-- HTTP server span
        +-- fastapi.dependencies
        +-- fastapi.endpoint
        +-- fastapi.serialization
        +-- fastapi.background_task
        +-- validation / exception logs
        |
        v
ephemeral JSONL bridge
        |
        v
Spyder Telemetry tab
```

The dashboard derives request count, error rate and latency percentiles from
finished native HTTP server spans. It does not claim those aggregates are
Prometheus/Grafana metrics.

Other spans from the same OpenTelemetry trace are also kept. FastAPI Studio
classifies common semantic-convention attributes as `database`,
`http-client`, `messaging`, `rpc` or generic `external`. This is observed
runtime evidence only: the Studio does not infer database or network calls from
Python source.

The JSONL file is temporary, local to the debug session and removed by the
Studio lifecycle. Request bodies and FastAPI's local `TelemetryData` are not
persisted by this bridge. Exception tracebacks/messages are also deliberately
not written to the JSONL sink.

If the selected project uses a FastAPI version without native telemetry, the
Telemetry tab reports that capability gap while the architecture, Request Lab
and client-side timing features continue to work.

## Interactive architecture diagrams

FastAPI Studio renders the same deterministic `FastAPIMap` through a native
Qt graphics view. No browser engine or separate JavaScript graph runtime is
required.

Three projections are available:

```text
Route flow
  one HTTP route
      -> parameters / models
      -> Depends() tree
      -> handler
      -> response model

Global architecture
  all routes
      -> handlers
      -> shared dependencies
      -> request/response models
  request parameter nodes are compacted to keep the map readable

Impact / blast radius
  every real lineage path
  from affected routes
      -> selected dependency or model
```

Nodes support pan/zoom, details, impact counts and double-click navigation back
to Python source. The projection layer is headless and tested independently
from Qt, so later runtime timing/telemetry can enrich the same diagram instead
of creating a second graph model.

## Product roadmap

### V0.1 - Understand

Implemented in the bootstrap branch:

- app discovery
- API tree
- route inspector
- Pydantic/OpenAPI model browser
- dependency browser + blast radius
- contract/dependency lineage
- interactive architecture diagrams with pan/zoom and source navigation
- route-to-test explorer and optional diagram test overlay
- source navigation
- normalized JSON view

Remaining before calling V0.1 complete:

- polished empty/error/loading states
- a first real in-Spyder manual smoke test on Windows/Linux

### V0.2 - Change safely

Implemented:

- in-memory and persistent versioned API baseline snapshots
- save current snapshot / load baseline from the Spyder Changes pane
- semantic route/model/dependency diff
- affected-route blast radius for schema and dependency changes
- conservative compatibility-risk candidates
- Spyder Changes explorer with source navigation

Also implemented:

- generated request builder from FastAPI/OpenAPI contracts
- dependency-derived auth/query/header inputs
- JSON body examples from Pydantic/OpenAPI
- isolated non-blocking request runner
- 422 validation visualizer linked to model/dependency source

Implemented:

- application/x-www-form-urlencoded form editor and sender
- multipart/form-data editor and sender
- single and multiple UploadFile fields with local file selection

Next:

- richer non-JSON body types beyond forms/uploads

### V0.3 - Debug requests

Implemented foundation:

- in-memory request history
- exact request replay
- handler breakpoint handoff to Spyder's existing debugger
- loopback debug-server launch through Spyder's native `debugfile` workflow
- non-blocking loopback readiness probe
- automatic replay as soon as the debug server starts listening
- explicit cancellation of a pending debug replay
- targeted Stop debug server action that interrupts only the Spyder shell used for that launch

Next:

- exception -> source navigation
- Variable Explorer integration

### V0.4 - Observe locally

Implemented:

- route-level runtime evidence from real Request Lab executions
- last / average / min / max end-to-end client elapsed time
- last HTTP status and transport-error counts
- optional Request Lab runtime overlay on the architecture diagram
- FastAPI's native OpenTelemetry tracing captured during Spyder debug launches
- FastAPI native operation spans for dependency resolution, endpoint execution,
  response serialization and background tasks
- native FastAPI validation/error logs linked to traces when available
- Telemetry dashboard with request/error/validation/exception KPIs,
  average/P50/P95, per-route summaries and recent latency timeline
- trace tree plus native graphical waterfall for relative server/operation timing
- double-click route/trace -> corresponding architecture diagram
- double-click source-backed operation spans -> Python source when resolvable
- ephemeral local JSONL bridge with bounded in-memory ingestion
- explicit clear lifecycle for both client and native telemetry

The two latency sources stay distinct:

- **Request Lab client elapsed** = end-to-end local client observation;
- **FastAPI native server span** = server-side OpenTelemetry request span.

No Grafana, Prometheus, browser frontend or custom FastAPI middleware is needed
for the local dashboard.

Next:

- project/vendor child spans (SQL/HTTP/etc.) when those libraries already emit
  OpenTelemetry spans
- optional native FastAPI metrics signal for active requests
- exception -> source correlation
- optional OTLP export remains compatible with external observability backends

## Non-goals

FastAPI Studio is not Airflow, Dagster, Grafana, an API gateway, a data processor or a Spyder fork. Those boundaries are intentional.
