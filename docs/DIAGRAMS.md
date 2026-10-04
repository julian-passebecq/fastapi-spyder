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
- future SQL/HTTP/storage timing edges = observed runtime evidence.

## Runtime evolution

Future telemetry should enrich these same nodes instead of creating a separate
runtime graph. A route flow can therefore evolve from:

```text
route -> dependency -> handler -> response
```

to:

```text
route                         147 ms
  -> auth dependency            8 ms
  -> db dependency              4 ms
  -> handler                  121 ms
       -> SQL                  34 ms
       -> external HTTP        71 ms
  -> response validation        4 ms
```

Observed SQL/HTTP/storage edges must be explicitly marked as runtime evidence;
they should never be inferred from static source text and presented as fact.
