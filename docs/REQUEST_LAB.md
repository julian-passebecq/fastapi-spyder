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
- request history;
- persisted secrets or auth profiles;
- automatic server startup;
- replay under a Spyder breakpoint.

Those belong to later slices rather than being hidden behind best-effort
behavior.
