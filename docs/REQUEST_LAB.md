# Request Lab

FastAPI Studio's Request Lab turns the inspected FastAPI contract into an executable local request form.

## Flow

```text
FastAPIMap
   |
   +-- route path + method
   +-- route parameters
   +-- dependency parameters
   +-- OpenAPI request body
   +-- Pydantic model source
   |
   v
generated request form
   |
   +-- path values
   +-- query values
   +-- headers
   +-- cookies
   +-- JSON body example
   |
   v
isolated HTTP subprocess
   |
   v
response
   |
   +-- status / headers / body / timing
   |
   +-- HTTP 422
         |
         v
structured validation issues
         |
         +-- location
         +-- field path
         +-- expected type
         +-- Pydantic/FastAPI message
         +-- source file + exact field/dependency line
```

## Why the request runner is isolated

The Qt/Spyder process does not perform blocking HTTP itself.

Request Lab starts the Python interpreter selected in Spyder and runs
`spyder_fastapi.request_cli`. The runner uses only the Python standard
library for HTTP execution, receives one JSON command on stdin and returns one
JSON result on stdout.

This gives the GUI a simple process boundary:

- a slow endpoint does not block Spyder's UI thread;
- the request runner does not require an extra HTTP client dependency in the
  user's FastAPI environment;
- transport failures are returned as structured data;
- the process can later become the boundary for replay + debugger workflows.

## Generated inputs

Request Lab combines direct route parameters and parameters contributed by the
recursive `Depends()` tree. This matters for inputs such as authentication
headers that are often absent from the handler signature's visible business
parameters.

For JSON request bodies, FastAPI Studio uses the generated OpenAPI schema to
produce a starter JSON document. Defaults, examples and enum values are used
when available; otherwise a type-shaped placeholder is generated.

Non-JSON required request bodies are detected and are not silently sent with
the wrong media type.

## Request history and exact replay

Successful HTTP responses and transport failures are kept in an in-memory
history for the current Spyder session. Each entry keeps the exact request
command used by the isolated runner, so **Replay selected** sends the same
method, URL inputs, query values, headers, cookies and JSON body again.

History is intentionally not persisted yet. Authentication headers and cookie
values are redacted in the history detail display, while the in-memory command
retains them so exact replay remains exact.

This provides the request-evidence layer needed for debugger integration
without prematurely turning FastAPI Studio into a server runtime.

## Spyder breakpoint handoff

For the selected route, Request Lab resolves both the Python definition line and
the first executable statement in the handler. **Set handler breakpoint** asks
Spyder's existing Debugger/Editor breakpoint manager to place a breakpoint at
that executable line, while preserving a breakpoint that is already present.

The workflow is deliberately built on Spyder's own execution APIs:

```text
select route
   -> Set handler breakpoint
   -> Debug server
        |
        +-- Spyder IPython Console.run_script(..., method="debugfile")
        +-- small uvicorn launcher
        +-- reload=False / same debugged process
   -> Replay selected request
```

The **Debug server** action only accepts loopback `http://` origins. It does
not silently bind a development server to `0.0.0.0` or a remote hostname.
The launcher deliberately does not make `uvicorn` a dependency of the Spyder
plugin: `uvicorn` must exist in the selected project environment, where the
FastAPI server dependency belongs.

Spyder still owns the debug session, stepping, console and Variable Explorer.
FastAPI Studio does not implement a second debugger runtime. If Spyder is
configured to stop on the first line, continue once through the launcher; the
route breakpoint then remains a normal Spyder breakpoint.

## 422 visualizer

A FastAPI validation response such as:

```json
{
  "detail": [
    {
      "type": "int_parsing",
      "loc": ["body", "count"],
      "msg": "Input should be a valid integer",
      "input": "abc"
    }
  ]
}
```

is normalized into a source-aware issue:

```text
body | count | integer | Input should be a valid integer | int_parsing
```

When source can be resolved, double-clicking the issue opens the exact Pydantic
field line. Dependency-derived query/header/cookie errors open the dependency
callable that introduced that parameter.

## Boundaries

The first Request Lab slice intentionally supports JSON bodies first.

Not implemented yet:

- multipart/file uploads;
- form-urlencoded editors;
- persisted request history;
- persisted secrets or auth profiles;
- automatic readiness detection / health probing for the debug server;
- coordinated stop/restart of the debug server;
- one-click start + readiness + replay sequencing.

Those belong to later slices rather than being hidden behind best-effort
behavior.
