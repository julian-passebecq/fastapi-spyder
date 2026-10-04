# FastAPI lineage model

FastAPI Studio uses **lineage** to answer architecture and change-impact questions that are difficult to see from Swagger/OpenAPI alone.

## What is lineage here?

The initial graph is intentionally deterministic. It is derived from FastAPI's own route and dependency model, not from guesses over source text.

```text
HTTP route
  |-- accepts ------> path/query/header/body parameter
  |                     |
  |                     +-- validates_as --> Pydantic/OpenAPI model
  |
  |-- depends_on ---> dependency
  |                     |
  |                     +-- depends_on ----> nested dependency
  |
  +-- handled_by ---> Python callable
                         |
                         +-- returns -------> response model
```

## Why it is useful

### Dependency blast radius

Select `get_current_user` or `get_db` and show every route whose dependency tree reaches it. A shared dependency change stops being a repository-wide search problem.

### Schema blast radius

Select `BookMetadata` and show every endpoint that accepts or returns it. This becomes the foundation for semantic API diff: a schema change can be connected to the exact routes affected.

### Source navigation

Route handlers and dependencies carry resolved Python file/line locations, allowing the GUI to turn graph nodes into direct editor navigation targets.

### Debug context

The same node IDs can later attach request replay, exceptions and timing spans to the architecture graph. The graph remains stable while runtime evidence is layered onto it.

## What we do not infer yet

Static code scanning cannot reliably prove that an arbitrary helper writes to PostgreSQL, calls an HTTP service, publishes to Kafka or uploads to object storage. V0.1 therefore does not draw those edges.

A later telemetry layer can add **observed runtime lineage** such as:

```text
POST /books
  -> get_db
  -> create_book()
  -> SQL INSERT books
  -> HTTP call metadata-service
  -> StoredBook
```

That separation is deliberate:

- **contract lineage**: deterministic from FastAPI/Pydantic
- **dependency lineage**: deterministic from `Depends()`
- **runtime lineage**: observed from telemetry/tracing

The UI can combine all three without pretending they have the same confidence level.

## Change-impact lineage

A lineage graph becomes much more useful when it is compared across snapshots.

FastAPI Studio can keep or load a baseline `FastAPIMap`, inspect the current app again and produce a semantic diff. Changes to schemas and dependencies are then expanded through lineage to the routes that depend on them.

Example:

```text
baseline                         current

BookMetadata                     BookMetadata
  isbn: str                       isbn: str
  title: str                      title: str
                                  source: str  [new required field]
       \                              /
        \                            /
         +---- POST /books/metadata
         +---- PUT  /books/{id}
```

The useful output is not merely "JSON changed":

```text
MODEL CHANGED: BookMetadata
compatibility candidate:
  required schema field added: source

affected routes:
  POST /books/metadata
  PUT /books/{id}
```

This is the distinction between a generic OpenAPI diff and FastAPI Studio's intended developer workflow: the diff is connected back to the Python implementation and to the dependency/schema blast radius.

Compatibility findings are deliberately labeled **candidates**. FastAPI Studio can detect structural signals such as removed routes, newly required fields, parameter type changes and changed response models, but it cannot prove how every external client behaves.

