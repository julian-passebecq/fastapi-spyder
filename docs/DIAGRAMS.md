# FastAPI Studio diagrams

The Diagram tab is a renderer over the existing `FastAPIMap`; it is not a
second source of architecture truth.

## Design rules

1. **Headless graph first.** Route/global/impact projections live under
   `spyder_fastapi.core` and have no Qt dependency.
2. **Native Spyder UI.** Rendering uses Qt `QGraphicsScene/QGraphicsView`.
   No WebEngine, D3 or embedded browser is required.
3. **Deterministic edges only.** The first diagrams show relationships FastAPI
   exposes directly: routes, request models, dependencies, handlers and
   response models.
4. **Impact is a real lineage path.** Blast-radius diagrams keep the actual
   route-to-node path instead of drawing invented direct relationships.
5. **Source remains one double-click away.** Source-backed nodes emit the same
   file/line navigation signal as the tree views.

## Views

### Route flow

Full request-contract lineage for one route:

```text
POST /orders
  |-- body ------------> OrderCreate
  |-- depends_on ------> get_current_user
  |                       |
  |                       +--> get_db
  |
  +-- handled_by ------> create_order()
                           |
                           +-- returns --> OrderRead
```

Request parameter nodes stay visible because they are useful when debugging one
route.

### Global architecture

The global projection removes parameter nodes to reduce graph size while
preserving request-model links:

```text
routes
  -> request models
  -> shared dependencies
  -> handlers
  -> response models
```

This view is intentionally compact rather than a raw dump of every OpenAPI
field.

### Impact / blast radius

Selecting a model or dependency displays every real lineage path from routes
that reach it.

```text
GET /me -----------\
POST /orders -------+--> get_current_user --> get_db
DELETE /orders/{id}/
```

This is the visual counterpart to `impacted_routes()`.

## Test evidence overlay

Route-to-test links are not part of FastAPI runtime lineage. They come from a
separate static scan of project tests and are therefore rendered as an optional
evidence layer.

When enabled:

```text
POST /orders ---- tested_by ----> test_create_order
GET /orders  ---- tested_by ----> test_list_orders
```

A single test function is represented once even when it performs several calls
to the same route. If one test function exercises multiple routes, those routes
can share the same test node in the global diagram.

The distinction remains explicit:

- route/model/dependency edges = deterministic FastAPI structure;
- tested_by edges = static project evidence;
- observed_* downstream edges = OpenTelemetry runtime evidence.

## Observed Request Lab timing

The first runtime overlay is already implemented at route level. Each completed
Request Lab execution can update the corresponding route node with:

- last end-to-end elapsed time;
- running average;
- min / max;
- last HTTP status;
- transport-error count.

This metric is deliberately labeled **Request Lab client elapsed**. It includes
local client/process/network overhead and must not be confused with server-only
handler latency.

The evidence is in-memory and can be cleared explicitly from the Diagram tab.

## Observed downstream runtime lineage

Native OpenTelemetry spans can now enrich the same Diagram instead of creating a
second architecture model.

Example:

```text
POST /ingestions
  |
  +-- Depends(get_tenant_context)
  |
  +-- handled_by --> create_ingestion()
  |                    |
  |                    +-- observed database -->
  |                    |      postgresql:demo_platform
  |                    |
  |                    +-- observed http client -->
  |                           schema-registry.demo.internal
  |
  +-- observed messaging -->
         ingestion.accepted
```

Runtime nodes are visually distinct from deterministic nodes and carry:

- observation count;
- last duration;
- average duration;
- P95 duration;
- OpenTelemetry category and target.

Runtime edges are derived only from captured spans. The Studio never infers
SQL/HTTP/messaging calls from source code and presents them as fact.

### Runtime anchoring

The overlay uses native FastAPI span parentage conservatively.

- downstream child of `fastapi.endpoint` -> static handler when present;
- downstream child of `fastapi.dependencies` -> matching static dependency
  when the callable maps uniquely;
- background-task/serialization child -> route;
- unresolved downstream child -> handler or route fallback.

The fallback is deliberate: a weaker but truthful route association is better
than inventing a precise static relationship.

The **Observed I/O** toggle removes these runtime nodes/edges while leaving the
deterministic graph and static test evidence untouched.
