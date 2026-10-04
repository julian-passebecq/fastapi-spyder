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
- dependency and schema blast-radius views
- serializable `FastAPIMap` JSON bridge
- diagnostics view for import/inspection output
- generated Request Lab for path/query/header/cookie inputs and JSON bodies
- isolated HTTP execution with response status/body/timing
- 422 validation visualizer with source navigation
- exact Pydantic field source mapping where inspectable
- in-memory request history with redacted display and exact replay
- Spyder-native handler breakpoint handoff using the first executable line
- local uvicorn launch through Spyder's public debugfile/IPython Console API
- headless CLI
- CI across Python 3.11-3.13 plus an offscreen Qt Spyder widget smoke test
- tests and a small bookstore example

Inspect the example without Spyder:

```bash
pip install -e ".[dev]"
PYTHONPATH=examples/bookstore fastapi-spyder app:app
```

## Product roadmap

### V0.1 - Understand

Implemented in the bootstrap branch:

- app discovery
- API tree
- route inspector
- Pydantic/OpenAPI model browser
- dependency browser + blast radius
- contract/dependency lineage
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

- request waterfall
- per-dependency/handler latency
- OpenTelemetry-based local traces
- correlated errors/logs
- optional export to an external observability backend

## Non-goals

FastAPI Studio is not Airflow, Dagster, Grafana, an API gateway, a data processor or a Spyder fork. Those boundaries are intentional.
