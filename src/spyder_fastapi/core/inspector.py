"""Inspect a live FastAPI application into a normalized architecture map."""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable
from typing import Any, get_args

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from spyder_fastapi.models import (
    DependencySpec,
    FastAPIMap,
    LineageEdge,
    LineageGraph,
    LineageNode,
    ModelSpec,
    ParameterSpec,
    RouteSpec,
    SourceRef,
)


_PARAM_GROUPS = (
    ("path", "path_params"),
    ("query", "query_params"),
    ("header", "header_params"),
    ("cookie", "cookie_params"),
    ("body", "body_params"),
)


def _callable_name(value: Any) -> str:
    if value is None:
        return "<unknown>"
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    return qualname or repr(value)


def _type_name(value: Any) -> str:
    if value is None:
        return "None"
    name = getattr(value, "__name__", None)
    if name:
        return name
    return str(value).replace("typing.", "")


def _field_type(field: Any) -> Any:
    """Resolve a FastAPI/Pydantic field annotation across compatibility layers."""

    direct = getattr(field, "type_", None)
    if direct is not None:
        return direct

    annotation = getattr(field, "annotation", None)
    if annotation is not None:
        return annotation

    field_info = getattr(field, "field_info", None)
    return getattr(field_info, "annotation", None)


def _field_required(field: Any) -> bool:
    """Resolve whether a FastAPI/Pydantic compatibility field is required."""

    required = getattr(field, "required", None)
    if required is not None:
        return bool(required)

    field_info = getattr(field, "field_info", None)
    is_required = getattr(field_info, "is_required", None)
    if callable(is_required):
        return bool(is_required())

    return False


def _source_ref(call: Callable[..., Any] | Any) -> SourceRef:
    if call is None:
        return SourceRef()

    try:
        unwrapped = inspect.unwrap(call)
    except (TypeError, ValueError):
        unwrapped = call

    file_name: str | None = None
    line: int | None = None
    try:
        file_name = inspect.getsourcefile(unwrapped) or inspect.getfile(unwrapped)
    except (TypeError, OSError):
        pass

    try:
        _, line = inspect.getsourcelines(unwrapped)
    except (TypeError, OSError):
        pass

    return SourceRef(
        file=file_name,
        line=line,
        qualname=_callable_name(unwrapped),
    )


def _model_field_sources(model: type[BaseModel]) -> dict[str, SourceRef]:
    """Map Pydantic fields to their class-body source lines when inspectable."""

    try:
        file_name = inspect.getsourcefile(model) or inspect.getfile(model)
        source_lines, start_line = inspect.getsourcelines(model)
    except (TypeError, OSError):
        return {}

    try:
        tree = ast.parse(textwrap.dedent("".join(source_lines)))
    except SyntaxError:
        return {}

    class_node = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef)),
        None,
    )
    if class_node is None:
        return {}

    model_fields = set(model.model_fields)
    result: dict[str, SourceRef] = {}

    for node in class_node.body:
        names: list[str] = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        elif isinstance(node, ast.Assign):
            names = [
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            ]

        for name in names:
            if name not in model_fields:
                continue
            result[name] = SourceRef(
                file=file_name,
                line=start_line + int(getattr(node, "lineno", 1)) - 1,
                qualname=f"{_callable_name(model)}.{name}",
            )

    return result


def _register_model_type(
    value: Any,
    registry: dict[str, SourceRef],
    field_registry: dict[str, dict[str, SourceRef]],
    seen: set[int] | None = None,
) -> None:
    """Record Pydantic model classes reachable from a type annotation."""

    if value is None:
        return

    if seen is None:
        seen = set()

    marker = id(value)
    if marker in seen:
        return
    seen.add(marker)

    for argument in get_args(value):
        _register_model_type(argument, registry, field_registry, seen)

    if not inspect.isclass(value):
        return

    try:
        is_model = issubclass(value, BaseModel)
    except TypeError:
        is_model = False

    if not is_model:
        return

    registry.setdefault(value.__name__, _source_ref(value))
    field_registry.setdefault(value.__name__, _model_field_sources(value))
    for field in value.model_fields.values():
        _register_model_type(field.annotation, registry, field_registry, seen)


def _dependency_id(call: Any) -> str:
    return f"dependency:{_callable_name(call)}"


def _dependant_parameters(dependant: Any) -> list[ParameterSpec]:
    params: list[ParameterSpec] = []
    for location, attribute in _PARAM_GROUPS:
        for field in getattr(dependant, attribute, ()):
            params.append(
                ParameterSpec(
                    name=field.name,
                    location=location,
                    type_name=_type_name(_field_type(field)),
                    required=_field_required(field),
                )
            )
    return params


def _walk_dependency(
    dependant: Any,
    registry: dict[str, DependencySpec],
    model_sources: dict[str, SourceRef],
    model_field_sources: dict[str, dict[str, SourceRef]],
) -> str:
    call = getattr(dependant, "call", None)
    dep_id = _dependency_id(call)

    children: list[str] = []
    for field in getattr(dependant, "body_params", ()):
        _register_model_type(
            _field_type(field),
            model_sources,
            model_field_sources,
        )

    for child in getattr(dependant, "dependencies", ()):
        children.append(
            _walk_dependency(
                child,
                registry,
                model_sources,
                model_field_sources,
            )
        )

    registry[dep_id] = DependencySpec(
        id=dep_id,
        name=_callable_name(call),
        source=_source_ref(call),
        use_cache=bool(getattr(dependant, "use_cache", True)),
        scope=getattr(dependant, "scope", None),
        parameters=_dependant_parameters(dependant),
        children=children,
    )
    return dep_id


def _route_parameters(route: APIRoute) -> list[ParameterSpec]:
    return _dependant_parameters(route.dependant)


def _request_models(route: APIRoute) -> list[str]:
    names: list[str] = []
    for field in getattr(route.dependant, "body_params", ()):
        type_name = _type_name(_field_type(field))
        if type_name not in names:
            names.append(type_name)
    return names


def _lineage_for_route(
    route_spec: RouteSpec,
    dependency_registry: dict[str, DependencySpec],
) -> LineageGraph:
    graph = LineageGraph()
    route_node_id = f"route:{route_spec.id}"
    handler_node_id = f"handler:{route_spec.handler}"

    graph.nodes.extend(
        [
            LineageNode(
                id=route_node_id,
                kind="route",
                label=route_spec.id,
                route_id=route_spec.id,
                source=route_spec.source,
            ),
            LineageNode(
                id=handler_node_id,
                kind="handler",
                label=route_spec.handler,
                source=route_spec.source,
            ),
        ]
    )
    graph.edges.append(
        LineageEdge(source=route_node_id, target=handler_node_id, relation="handled_by")
    )

    for parameter in route_spec.parameters:
        parameter_id = (
            f"parameter:{route_spec.id}:{parameter.location}:{parameter.name}"
        )
        graph.nodes.append(
            LineageNode(
                id=parameter_id,
                kind="parameter",
                label=f"{parameter.location}:{parameter.name}",
                route_id=route_spec.id,
            )
        )
        graph.edges.append(
            LineageEdge(source=route_node_id, target=parameter_id, relation="accepts")
        )

        if parameter.location == "body" and parameter.type_name in route_spec.request_models:
            model_id = f"model:{parameter.type_name}"
            graph.nodes.append(
                LineageNode(
                    id=model_id,
                    kind="model",
                    label=parameter.type_name,
                )
            )
            graph.edges.append(
                LineageEdge(source=parameter_id, target=model_id, relation="validates_as")
            )

    visited_dependencies: set[str] = set()

    def add_dependency(dep_id: str, parent_id: str) -> None:
        dep = dependency_registry[dep_id]
        if dep_id not in visited_dependencies:
            graph.nodes.append(
                LineageNode(
                    id=dep_id,
                    kind="dependency",
                    label=dep.name,
                    source=dep.source,
                )
            )
            visited_dependencies.add(dep_id)

        graph.edges.append(
            LineageEdge(source=parent_id, target=dep_id, relation="depends_on")
        )

        for parameter in dep.parameters:
            parameter_id = (
                f"parameter:{dep_id}:{parameter.location}:{parameter.name}"
            )
            if not any(node.id == parameter_id for node in graph.nodes):
                graph.nodes.append(
                    LineageNode(
                        id=parameter_id,
                        kind="parameter",
                        label=f"{parameter.location}:{parameter.name}",
                    )
                )
            graph.edges.append(
                LineageEdge(source=dep_id, target=parameter_id, relation="accepts")
            )

            if parameter.location == "body":
                model_id = f"model:{parameter.type_name}"
                if not any(node.id == model_id for node in graph.nodes):
                    graph.nodes.append(
                        LineageNode(
                            id=model_id,
                            kind="model",
                            label=parameter.type_name,
                        )
                    )
                graph.edges.append(
                    LineageEdge(
                        source=parameter_id,
                        target=model_id,
                        relation="validates_as",
                    )
                )

        for child_id in dep.children:
            add_dependency(child_id, dep_id)

    for dependency_id in route_spec.dependencies:
        add_dependency(dependency_id, route_node_id)

    if route_spec.response_model:
        response_id = f"model:{route_spec.response_model}"
        if not any(node.id == response_id for node in graph.nodes):
            graph.nodes.append(
                LineageNode(
                    id=response_id,
                    kind="model",
                    label=route_spec.response_model,
                )
            )
        graph.edges.append(
            LineageEdge(source=handler_node_id, target=response_id, relation="returns")
        )

    return graph


def _merge_lineage(graphs: list[LineageGraph]) -> LineageGraph:
    nodes: dict[str, LineageNode] = {}
    edges: dict[tuple[str, str, str], LineageEdge] = {}
    for graph in graphs:
        for node in graph.nodes:
            nodes.setdefault(node.id, node)
        for edge in graph.edges:
            edges.setdefault((edge.source, edge.target, edge.relation), edge)
    return LineageGraph(nodes=list(nodes.values()), edges=list(edges.values()))


def inspect_app(app: FastAPI) -> FastAPIMap:
    """Return a deterministic, JSON-serializable map of a FastAPI application.

    The first lineage implementation is intentionally conservative: it only
    represents relationships FastAPI itself exposes (request parameters,
    Pydantic/OpenAPI models, dependency injection, handlers and response
    models). It does not guess database, queue or storage calls from source
    text. Runtime telemetry can enrich those downstream edges later.
    """

    dependency_registry: dict[str, DependencySpec] = {}
    model_sources: dict[str, SourceRef] = {}
    model_field_sources: dict[str, dict[str, SourceRef]] = {}
    routes: list[RouteSpec] = []
    lineage_parts: list[LineageGraph] = []

    api_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    api_routes.sort(key=lambda route: (route.path, sorted(route.methods or {""})))

    for route in api_routes:
        root_dependencies = [
            _walk_dependency(
                dependant,
                dependency_registry,
                model_sources,
                model_field_sources,
            )
            for dependant in route.dependant.dependencies
        ]

        for field in getattr(route.dependant, "body_params", ()):
            _register_model_type(
                _field_type(field),
                model_sources,
                model_field_sources,
            )
        _register_model_type(
            route.response_model,
            model_sources,
            model_field_sources,
        )

        methods = sorted(route.methods or {"GET"})
        for method in methods:
            route_id = f"{method} {route.path}"
            spec = RouteSpec(
                id=route_id,
                method=method,
                path=route.path,
                name=route.name,
                handler=_callable_name(route.endpoint),
                source=_source_ref(route.endpoint),
                status_code=route.status_code,
                tags=list(route.tags or []),
                parameters=_route_parameters(route),
                request_models=_request_models(route),
                response_model=(
                    _type_name(route.response_model)
                    if route.response_model is not None
                    else None
                ),
                dependencies=root_dependencies,
            )
            routes.append(spec)
            lineage_parts.append(_lineage_for_route(spec, dependency_registry))

    openapi = app.openapi()
    schema_definitions = (
        openapi.get("components", {}).get("schemas", {}) if isinstance(openapi, dict) else {}
    )
    models = [
        ModelSpec(
            name=name,
            source=model_sources.get(name),
            field_sources=model_field_sources.get(name, {}),
            schema=schema,
        )
        for name, schema in sorted(schema_definitions.items())
    ]

    lineage = _merge_lineage(lineage_parts)
    for node in lineage.nodes:
        if node.kind == "model" and node.label in model_sources:
            node.source = model_sources[node.label]

    return FastAPIMap(
        title=app.title,
        version=getattr(app, "version", None),
        openapi_version=getattr(app, "openapi_version", "3.1.0"),
        openapi=openapi,
        routes=routes,
        dependencies=sorted(dependency_registry.values(), key=lambda dep: dep.id),
        models=models,
        lineage=lineage,
    )
