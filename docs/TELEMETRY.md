# Native FastAPI telemetry in Spyder

FastAPI Studio intentionally does not embed Grafana or build a second
observability backend.

Recent FastAPI releases emit native OpenTelemetry signals. During a Spyder
debug launch, Studio attaches a local OpenTelemetry processor before Uvicorn
imports the application and renders those signals directly in the IDE.

## Truth layers

Telemetry is kept separate from the static architecture model.

```text
FastAPIMap
  deterministic contract / dependency structure

RouteTestIndex
  static project evidence

RuntimeEvidence
  Request Lab client-side observations

NativeTelemetryStore
  observed FastAPI/OpenTelemetry server spans and logs
```

A renderer can combine those layers, but none of them is silently promoted into
another.

## Native signals used

FastAPI's native request tracing currently emits a server span plus operation
spans such as:

```text
GET /items/{item_id}
  +-- fastapi.dependencies
  +-- fastapi.endpoint
  +-- fastapi.serialization
  +-- fastapi.background_task
```

Operation spans can include `code.function.name`, allowing Studio to connect
observed runtime work back to the functions already represented in the static
FastAPI map.

FastAPI also emits native OpenTelemetry logs for request validation failures
and unhandled exceptions.

## Local capture bridge

```text
Spyder debugfile
   |
   v
spyder_fastapi.debug_server
   |
   +-- configure OpenTelemetry span/log processors
   |
   v
user FastAPI app
   |
   +-- native FastAPI telemetry
   |
   v
temporary JSONL
   |
   v
NativeTelemetryStore
   |
   v
Telemetry dashboard
```

The JSONL bridge is an IDE transport, not an observability standard. OpenTelemetry
remains the runtime telemetry contract.

## Privacy / safety

The local bridge intentionally does not persist:

- request bodies;
- dependency argument values;
- FastAPI `TelemetryData.values`;
- exception tracebacks;
- exception messages.

It records normalized span attributes, route/status/timing metadata and compact
FastAPI log metadata. The temporary file is removed when the Studio capture is
replaced or the plugin shuts down.

## Dashboard

The first native dashboard contains:

- request count;
- 5xx/native-error count and error rate;
- validation-failure count;
- unhandled-exception count;
- average latency;
- P50 / P95 latency;
- per-route request/error/validation/latency table;
- recent request latency timeline;
- trace tree plus graphical relative-time waterfall;
- FastAPI validation and exception log list;
- operation-span navigation back to Python source when `code.function.name`
  maps unambiguously to the inspected FastAPI app.

The route KPIs are calculated from finished FastAPI HTTP server spans. They are
not presented as Prometheus or Grafana metrics.

## Error semantics

The dashboard deliberately keeps different FastAPI failure signals separate.

- **Server errors** are 5xx/error server spans and drive the error-rate KPI.
- **Validation failures** come from FastAPI's native
  `fastapi.validation.failed` warning logs and do not inflate the server error
  rate.
- **Unhandled exceptions** come from FastAPI's native exception log events.

This avoids labeling normal contract rejection (for example HTTP 422) as an
internal server failure.

## Trace waterfall

The trace view has two synchronized representations:

```text
tree                         relative-time waterfall

GET /orders                  |=======================|
  fastapi.dependencies         |====|
  fastapi.endpoint                  |==============|
  fastapi.serialization                            |=|
```

The bars use the span timestamps emitted by OpenTelemetry. They are not
reconstructed from log order. Background-task and third-party child spans can
therefore extend beyond the FastAPI HTTP response span when the runtime trace
actually does so.

## Latency semantics

Two timing sources coexist and must stay explicitly named:

### Request Lab client elapsed

End-to-end local request time observed by the isolated Request Lab runner. It
includes client/process/network overhead.

### Native FastAPI server span

Duration of FastAPI's native OpenTelemetry HTTP server span. This is the
preferred source for server-side route performance.

Child operation spans provide the next level of detail for dependency
resolution, endpoint execution, serialization and background work.

## Provider compatibility

Studio preserves an existing OpenTelemetry provider when it exposes the normal
SDK processor attachment API. Otherwise it reports that local capture cannot be
attached instead of replacing a vendor/provider silently.

Applications that explicitly supply their own provider through
`FastAPI(telemetry={...})` can therefore remain authoritative.

## Later extensions

The same viewer can accept additional OpenTelemetry spans from instrumented
libraries, for example SQL clients or outbound HTTP clients. Those will be
shown as observed runtime evidence rather than inferred architecture.

Native FastAPI metrics such as `http.server.request.duration` and
`http.server.active_requests` can be added later when their local collection
lifecycle is isolated cleanly from Spyder's long-lived IPython kernel.
