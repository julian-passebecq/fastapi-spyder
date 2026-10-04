"""Semantic comparison of two FastAPI architecture snapshots."""

from __future__ import annotations

from typing import Any

from spyder_fastapi.core.lineage import impacted_routes
from spyder_fastapi.models import (
    APIDiff,
    DependencySpec,
    FastAPIMap,
    ModelSpec,
    RouteSpec,
    SemanticChange,
)


def _parameter_map(parameters) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (parameter.location, parameter.alias or parameter.name): {
            "type_name": parameter.type_name,
            "required": parameter.required,
        }
        for parameter in parameters
    }


def _route_contract(route: RouteSpec) -> dict[str, Any]:
    return {
        "method": route.method,
        "path": route.path,
        "status_code": route.status_code,
        "tags": sorted(route.tags),
        "parameters": _parameter_map(route.parameters),
        "request_models": sorted(route.request_models),
        "response_model": route.response_model,
        "dependencies": sorted(route.dependencies),
        "handler": route.handler,
    }


def _dependency_contract(dependency: DependencySpec) -> dict[str, Any]:
    return {
        "parameters": _parameter_map(dependency.parameters),
        "children": sorted(dependency.children),
        "use_cache": dependency.use_cache,
        "scope": dependency.scope,
    }


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    keys = sorted(set(before) | set(after))
    return [key for key in keys if before.get(key) != after.get(key)]


def _route_breaking_reasons(before: RouteSpec, after: RouteSpec) -> list[str]:
    reasons: list[str] = []
    before_params = _parameter_map(before.parameters)
    after_params = _parameter_map(after.parameters)

    for key, after_value in after_params.items():
        location, name = key
        before_value = before_params.get(key)
        if before_value is None and after_value["required"]:
            reasons.append(f"required {location} parameter added: {name}")
            continue
        if before_value is None:
            continue
        if not before_value["required"] and after_value["required"]:
            reasons.append(f"parameter became required: {location} {name}")
        if before_value["type_name"] != after_value["type_name"]:
            reasons.append(
                "parameter type changed: "
                f"{location} {name} "
                f"({before_value['type_name']} -> {after_value['type_name']})"
            )

    if before.response_model != after.response_model:
        reasons.append(
            "response model changed: "
            f"{before.response_model or '-'} -> {after.response_model or '-'}"
        )

    if before.status_code != after.status_code:
        reasons.append(
            "success status code changed: "
            f"{before.status_code or 'default'} -> {after.status_code or 'default'}"
        )

    return reasons


def _schema_breaking_reasons(before: ModelSpec, after: ModelSpec) -> list[str]:
    reasons: list[str] = []
    before_schema = before.schema_
    after_schema = after.schema_

    before_required = set(before_schema.get("required", []))
    after_required = set(after_schema.get("required", []))
    for field in sorted(after_required - before_required):
        reasons.append(f"required schema field added: {field}")

    before_properties = before_schema.get("properties", {})
    after_properties = after_schema.get("properties", {})

    for field in sorted(set(before_properties) - set(after_properties)):
        reasons.append(f"schema field removed: {field}")

    for field in sorted(set(before_properties) & set(after_properties)):
        before_field = before_properties[field]
        after_field = after_properties[field]

        before_type = (
            before_field.get("type"),
            before_field.get("$ref"),
            tuple(before_field.get("anyOf", [])),
            tuple(before_field.get("oneOf", [])),
        )
        after_type = (
            after_field.get("type"),
            after_field.get("$ref"),
            tuple(after_field.get("anyOf", [])),
            tuple(after_field.get("oneOf", [])),
        )
        if before_type != after_type:
            reasons.append(f"schema field type changed: {field}")

    return reasons


def _dependency_breaking_reasons(
    before: DependencySpec,
    after: DependencySpec,
) -> list[str]:
    reasons: list[str] = []
    before_params = _parameter_map(before.parameters)
    after_params = _parameter_map(after.parameters)

    for key, after_value in after_params.items():
        location, name = key
        before_value = before_params.get(key)
        if before_value is None and after_value["required"]:
            reasons.append(
                f"dependency adds required {location} parameter: {name}"
            )
        elif before_value is not None:
            if not before_value["required"] and after_value["required"]:
                reasons.append(
                    f"dependency parameter became required: {location} {name}"
                )
            if before_value["type_name"] != after_value["type_name"]:
                reasons.append(
                    f"dependency parameter type changed: {location} {name}"
                )

    return reasons


def _affected_for_model(
    before: FastAPIMap,
    after: FastAPIMap,
    name: str,
) -> list[str]:
    node_id = f"model:{name}"
    return sorted(
        set(impacted_routes(before, node_id))
        | set(impacted_routes(after, node_id))
    )


def _affected_for_dependency(
    before: FastAPIMap,
    after: FastAPIMap,
    dependency_id: str,
) -> list[str]:
    return sorted(
        set(impacted_routes(before, dependency_id))
        | set(impacted_routes(after, dependency_id))
    )


def diff_maps(before: FastAPIMap, after: FastAPIMap) -> APIDiff:
    """Return a deterministic semantic diff between two FastAPI maps.

    Source locations and raw OpenAPI ordering are ignored. The comparison is
    intentionally based on route contracts, Pydantic schemas and dependency
    structure so refactors that only move code do not create noise.
    """

    changes: list[SemanticChange] = []

    before_routes = {route.id: route for route in before.routes}
    after_routes = {route.id: route for route in after.routes}

    for route_id in sorted(set(before_routes) | set(after_routes)):
        old = before_routes.get(route_id)
        new = after_routes.get(route_id)

        if old is None and new is not None:
            changes.append(
                SemanticChange(
                    entity="route",
                    name=route_id,
                    kind="added",
                    affected_routes=[route_id],
                    source=new.source,
                )
            )
            continue

        if new is None and old is not None:
            changes.append(
                SemanticChange(
                    entity="route",
                    name=route_id,
                    kind="removed",
                    affected_routes=[route_id],
                    breaking_reasons=["route removed"],
                    source=old.source,
                )
            )
            continue

        assert old is not None and new is not None
        before_contract = _route_contract(old)
        after_contract = _route_contract(new)
        fields = _changed_fields(before_contract, after_contract)
        if fields:
            changes.append(
                SemanticChange(
                    entity="route",
                    name=route_id,
                    kind="changed",
                    fields=fields,
                    affected_routes=[route_id],
                    breaking_reasons=_route_breaking_reasons(old, new),
                    source=new.source,
                )
            )

    before_models = {model.name: model for model in before.models}
    after_models = {model.name: model for model in after.models}

    for name in sorted(set(before_models) | set(after_models)):
        old = before_models.get(name)
        new = after_models.get(name)
        affected = _affected_for_model(before, after, name)

        if old is None and new is not None:
            changes.append(
                SemanticChange(
                    entity="model",
                    name=name,
                    kind="added",
                    affected_routes=affected,
                    source=new.source,
                )
            )
            continue

        if new is None and old is not None:
            changes.append(
                SemanticChange(
                    entity="model",
                    name=name,
                    kind="removed",
                    affected_routes=affected,
                    breaking_reasons=["schema removed"],
                    source=old.source,
                )
            )
            continue

        assert old is not None and new is not None
        if old.schema_ != new.schema_:
            changes.append(
                SemanticChange(
                    entity="model",
                    name=name,
                    kind="changed",
                    fields=["schema"],
                    affected_routes=affected,
                    breaking_reasons=_schema_breaking_reasons(old, new),
                    source=new.source,
                )
            )

    before_dependencies = {
        dependency.id: dependency for dependency in before.dependencies
    }
    after_dependencies = {
        dependency.id: dependency for dependency in after.dependencies
    }

    for dependency_id in sorted(
        set(before_dependencies) | set(after_dependencies)
    ):
        old = before_dependencies.get(dependency_id)
        new = after_dependencies.get(dependency_id)
        affected = _affected_for_dependency(
            before,
            after,
            dependency_id,
        )

        if old is None and new is not None:
            changes.append(
                SemanticChange(
                    entity="dependency",
                    name=dependency_id.removeprefix("dependency:"),
                    kind="added",
                    affected_routes=affected,
                    source=new.source,
                )
            )
            continue

        if new is None and old is not None:
            changes.append(
                SemanticChange(
                    entity="dependency",
                    name=dependency_id.removeprefix("dependency:"),
                    kind="removed",
                    affected_routes=affected,
                    source=old.source,
                )
            )
            continue

        assert old is not None and new is not None
        before_contract = _dependency_contract(old)
        after_contract = _dependency_contract(new)
        fields = _changed_fields(before_contract, after_contract)
        if fields:
            changes.append(
                SemanticChange(
                    entity="dependency",
                    name=dependency_id.removeprefix("dependency:"),
                    kind="changed",
                    fields=fields,
                    affected_routes=affected,
                    breaking_reasons=_dependency_breaking_reasons(old, new),
                    source=new.source,
                )
            )

    affected_routes = sorted(
        {
            route
            for change in changes
            for route in change.affected_routes
        }
    )
    breaking_candidates = sum(
        1 for change in changes if change.breaking_reasons
    )

    return APIDiff(
        changes=changes,
        affected_routes=affected_routes,
        breaking_candidates=breaking_candidates,
    )
