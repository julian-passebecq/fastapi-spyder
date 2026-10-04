# FastAPI Studio manual qualification

This checklist validates the parts of FastAPI Studio that headless CI cannot
prove: real Spyder integration, editor navigation, debugger handoff and visual
usability.

Use the realistic fake client under `examples/data_platform_demo`. Do not
replace it with a toy app during qualification.


## Fast path after a green Windows CI run

When the current commit has a green **spyder-windows** job, do not manually
repeat checks that CI already proves. The Windows job already validates package
installation, Spyder plugin discovery, dependency health, demo tests, the
cross-platform Qt widget smoke, native telemetry capture, observed DB/HTTP/
messaging lineage, Diagram -> Telemetry trace drilldown and offscreen visual
captures.

The remaining **human-only** qualification is intentionally small:

1. launch Spyder from the qualification environment and verify the FastAPI
   Studio dock is visible, dockable, closable and reopenable;
2. use the real **Discover** -> **Inspect** buttons on
   `examples/data_platform_demo`;
3. double-click one route, `get_tenant_context`, one model and one linked test
   and verify the Spyder editor lands on the expected source;
4. exercise Diagram pan/zoom/Fit at a normal laptop resolution and confirm the
   graph remains usable;
5. start the debug server from Request Lab, hit a real handler breakpoint and
   verify the debugger/Variable Explorer context;
6. generate demo traffic and visually confirm Telemetry/waterfall readability,
   then return through Diagram -> observed database node -> exact matching
   Telemetry trace;
7. exercise 422, 404 and 500 once and verify the plugin remains responsive.

Record these seven checks as **PASS / FAIL / BLOCKED / UNCERTAIN**. Any FAIL or
UNCERTAIN result should be captured before adding more product features.

The full checklist below remains the canonical diagnostic path when one of
those seven checks fails or when the Windows CI job is not green.

## Supported qualification target

- OS: Windows 11 first, then Linux
- Python: 3.12
- Spyder: current installed `spyder>=6`
- FastAPI: version installed by the project environment
- project: `examples/data_platform_demo`

## Install

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev,spyder]"
spyder
```

The plugin must appear without copying files into Spyder itself.

## Qualification states

Record each item as one of:

- **PASS**: visibly or functionally verified;
- **FAIL**: acceptance criterion is not met;
- **BLOCKED**: environment prevents the check;
- **UNCERTAIN**: behavior is observable but the result is ambiguous.

Do not promote an UNCERTAIN item to PASS from code inspection alone.

## 1. Plugin discovery

1. Start Spyder from the environment above.
2. Confirm the FastAPI Studio dock is available.
3. Open `examples/data_platform_demo` as the working directory.

Acceptance:

- Spyder starts normally;
- FastAPI Studio is visible and dockable;
- no plugin-load traceback appears;
- closing/reopening the dock does not lose Spyder stability.

## 2. Discover and inspect

1. Click **Discover**.
2. Select `app:app`.
3. Click **Inspect**.

Expected project shape:

```text
GET  /health
POST /v1/ingestions
GET  /v1/jobs/{job_id}
POST /v1/jobs/{job_id}/retry
GET  /v1/slow
GET  /v1/debug/fail
```

Acceptance:

- six routes are visible;
- Pydantic models are populated;
- nested dependencies are visible;
- static route-to-test links are populated;
- no manual JSON editing is required.

## 3. Source navigation

Double-click:

- a route handler;
- `get_tenant_context`;
- one Pydantic model;
- one linked test.

Acceptance:

- Spyder opens the expected Python file;
- the editor lands at or immediately adjacent to the relevant definition;
- paths containing spaces still open correctly if the repository is copied to
  such a path.

## 4. Diagram

Open **Diagram** and inspect:

- Route flow;
- Global architecture;
- Impact / blast radius.

Acceptance:

- labels do not overlap enough to make the graph unusable;
- pan/zoom/fit remain responsive;
- route/model/dependency/test evidence is visually distinguishable;
- changing views does not lose the inspected app.

## 5. Request Lab

For authenticated routes use:

```text
x-api-key: demo-secret
x-tenant-id: contoso
```

Exercise:

- `GET /health`;
- successful `POST /v1/ingestions`;
- invalid ingestion with `record_count = 0`;
- `GET /v1/jobs/missing`;
- `GET /v1/slow?milliseconds=120`.

Acceptance:

- response status/body/timing are readable;
- 422 validation issues are rendered structurally;
- history/replay works;
- secrets are redacted from history display;
- the client timing is labeled as client elapsed, not server latency.

## 6. Debug server

From Request Lab start the local debug server.

Acceptance:

- Spyder debugger launches the FastAPI Studio debug launcher;
- the server reaches listening state;
- a breakpoint in a route handler can be hit;
- stopping the debug server returns the plugin to a usable state;
- telemetry failure, if forced, does not prevent FastAPI from starting.

## 7. Native FastAPI Telemetry

With the debug server running, generate traffic:

```powershell
python examples\data_platform_demo\load.py
```

Acceptance:

- Telemetry becomes **LIVE**;
- request/error/validation/exception KPIs update;
- Average/P50/P95 are populated;
- the trace tree contains FastAPI dependency/endpoint/serialization spans;
- the graphical waterfall is readable at normal Spyder dock sizes;
- 422 validation is not counted as a 5xx server error.

## 8. Observed downstream I/O

For a successful ingestion, expected observed children include:

```text
database
  postgresql:demo_platform

http-client
  schema-registry.demo.internal

messaging
  ingestion.accepted
```

Acceptance:

- downstream spans appear in Telemetry;
- the Diagram **Observed I/O** toggle overlays them;
- runtime nodes are visually distinct from deterministic architecture;
- runtime edges are visually distinct from static lineage;
- disabling **Observed I/O** restores the static graph unchanged.

## 9. Evidence drilldown

Double-click the observed database node in Diagram.

Acceptance:

- FastAPI Studio switches to Telemetry;
- the latest matching route trace is selected;
- the waterfall corresponds to `POST /v1/ingestions`;
- the user can return to Diagram without losing state.

## 10. Failure cases

Exercise:

- invalid request -> 422;
- missing job -> 404;
- `GET /v1/debug/fail` -> 500.

Acceptance:

- 422 appears as validation evidence;
- 404 does not inflate server-error metrics;
- 500 appears as server error / exception evidence;
- the plugin remains responsive after all three.

## Exit criteria for first Windows UX qualification

The first manual Windows qualification is complete only when:

- sections 1-10 are PASS, or any non-PASS item is explicitly recorded;
- no crash or Spyder restart is required during the flow;
- Diagram and Telemetry remain usable at a normal laptop resolution;
- at least one real breakpoint is hit in a FastAPI handler;
- source navigation is verified from both static and telemetry-backed views.

Headless CI complements this checklist but does not replace it.
