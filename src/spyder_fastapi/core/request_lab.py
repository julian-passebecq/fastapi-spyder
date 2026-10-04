"""Generate Request Lab forms and interpret FastAPI validation failures."""

from __future__ import annotations

import ipaddress
from collections import deque
from typing import Any
from urllib.parse import urlsplit

from spyder_fastapi.models import (
    FastAPIMap,
    RequestField,
    RequestTemplate,
    SourceRef,
    ValidationIssue,
)


def _route(api_map: FastAPIMap, route_id: str):
    route = next((item for item in api_map.routes if item.id == route_id), None)
    if route is None:
        raise KeyError(f"Unknown FastAPI route: {route_id}")
    return route


def _operation(api_map: FastAPIMap, method: str, path: str) -> dict[str, Any]:
    paths = api_map.openapi.get("paths", {})
    path_item = paths.get(path, {})
    operation = path_item.get(method.lower(), {})
    return operation if isinstance(operation, dict) else {}


def _schema_example(
    api_map: FastAPIMap,
    schema: dict[str, Any],
    seen: set[str] | None = None,
) -> Any:
    if seen is None:
        seen = set()

    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return {}
        target = (
            api_map.openapi.get("components", {})
            .get("schemas", {})
            .get(name, {})
        )
        if isinstance(target, dict):
            return _schema_example(api_map, target, seen | {name})

    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key)
        if isinstance(options, list) and options:
            if key == "allOf":
                merged: dict[str, Any] = {}
                for option in options:
                    if isinstance(option, dict):
                        value = _schema_example(api_map, option, seen)
                        if isinstance(value, dict):
                            merged.update(value)
                if merged:
                    return merged
            else:
                non_null = [
                    option
                    for option in options
                    if isinstance(option, dict) and option.get("type") != "null"
                ]
                if non_null:
                    return _schema_example(api_map, non_null[0], seen)

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        result: dict[str, Any] = {}
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for name, field_schema in properties.items():
                if isinstance(field_schema, dict):
                    result[name] = _schema_example(api_map, field_schema, seen)
        return result

    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return [_schema_example(api_map, items, seen)]
        return []

    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return False
    if schema_type == "string":
        return "string"
    if schema_type == "null":
        return None

    return None


def _resolve_schema(
    api_map: FastAPIMap,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a local OpenAPI schema reference when possible."""

    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        target = (
            api_map.openapi.get("components", {})
            .get("schemas", {})
            .get(name, {})
        )
        if isinstance(target, dict):
            return target
    return schema


def _body_fields(
    api_map: FastAPIMap,
    schema: dict[str, Any],
    *,
    source: SourceRef | None,
) -> list[RequestField]:
    """Generate editable form/multipart fields from an OpenAPI body schema."""

    resolved = _resolve_schema(api_map, schema)
    properties = resolved.get("properties", {})
    if not isinstance(properties, dict):
        return []

    required_names = set(resolved.get("required", []))
    fields: list[RequestField] = []
    for name, raw_field_schema in properties.items():
        if not isinstance(raw_field_schema, dict):
            continue

        field_schema = _resolve_schema(api_map, raw_field_schema)
        field_type = str(
            field_schema.get("type")
            or raw_field_schema.get("type")
            or "string"
        )
        field_format = field_schema.get("format") or raw_field_schema.get("format")
        multiple = False
        is_file = field_type == "string" and field_format == "binary"

        if field_type == "array":
            items = field_schema.get("items") or raw_field_schema.get("items")
            if isinstance(items, dict):
                item_schema = _resolve_schema(api_map, items)
                is_file = (
                    item_schema.get("type") == "string"
                    and item_schema.get("format") == "binary"
                )
                multiple = is_file

        example = None if is_file else _schema_example(api_map, raw_field_schema)
        media_type = (
            field_schema.get("contentMediaType")
            or raw_field_schema.get("contentMediaType")
        )

        fields.append(
            RequestField(
                name=str(name),
                python_name=str(name),
                location="body",
                type_name=("file[]" if multiple else "file") if is_file else field_type,
                required=name in required_names,
                example=example,
                description=raw_field_schema.get("description"),
                source=source,
                is_file=is_file,
                multiple=multiple,
                media_type=str(media_type) if media_type else None,
            )
        )

    return fields


def _parameter_metadata(
    operation: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    parameters = operation.get("parameters", [])
    if not isinstance(parameters, list):
        return result

    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        location = parameter.get("in")
        if isinstance(name, str) and isinstance(location, str):
            result[(location, name)] = parameter
    return result


def _dependency_parameters(api_map: FastAPIMap, root_ids: list[str]):
    dependencies = {item.id: item for item in api_map.dependencies}
    queue = deque(root_ids)
    visited: set[str] = set()

    while queue:
        dependency_id = queue.popleft()
        if dependency_id in visited:
            continue
        visited.add(dependency_id)

        dependency = dependencies.get(dependency_id)
        if dependency is None:
            continue

        for parameter in dependency.parameters:
            yield parameter, dependency.source
        queue.extend(dependency.children)


def _example_from_parameter(parameter: dict[str, Any]) -> Any:
    if "example" in parameter:
        return parameter["example"]
    schema = parameter.get("schema")
    if isinstance(schema, dict):
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        enum = schema.get("enum")
        if isinstance(enum, list) and enum:
            return enum[0]
    return None


def local_debug_server_address(base_url: str) -> tuple[str, int]:
    """Resolve a loopback HTTP origin into a safe local uvicorn bind address."""

    value = base_url.strip()
    if not value:
        raise ValueError("Base URL is required.")

    parsed = urlsplit(value)
    if parsed.scheme != "http":
        raise ValueError(
            "Spyder debug server currently supports local http:// URLs only."
        )
    if not parsed.hostname:
        raise ValueError("Base URL must include a host.")
    if parsed.username or parsed.password:
        raise ValueError("Base URL must not contain credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL must not contain a query or fragment.")
    if parsed.path not in ("", "/"):
        raise ValueError(
            "Spyder debug server requires an origin URL without an API path."
        )

    host = parsed.hostname
    is_loopback = host.casefold() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False

    if not is_loopback:
        raise ValueError(
            "Spyder debug server only binds to loopback hosts "
            "(localhost, 127.0.0.1 or ::1)."
        )

    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise ValueError("Base URL contains an invalid port.") from exc

    return host, int(port)


def build_request_template(api_map: FastAPIMap, route_id: str) -> RequestTemplate:
    """Generate an editable request form from FastAPI's own OpenAPI contract."""

    route = _route(api_map, route_id)
    operation = _operation(api_map, route.method, route.path)
    metadata = _parameter_metadata(operation)

    fields: list[RequestField] = []
    seen: set[tuple[str, str]] = set()

    for parameter in route.parameters:
        if parameter.location == "body":
            continue
        public_name = parameter.alias or parameter.name
        key = (parameter.location, public_name)
        seen.add(key)
        details = metadata.get(key, {})
        fields.append(
            RequestField(
                name=public_name,
                python_name=parameter.name,
                location=parameter.location,
                type_name=parameter.type_name,
                required=parameter.required,
                example=_example_from_parameter(details),
                description=details.get("description"),
                source=route.source,
            )
        )

    for parameter, source in _dependency_parameters(api_map, route.dependencies):
        if parameter.location == "body":
            continue
        public_name = parameter.alias or parameter.name
        key = (parameter.location, public_name)
        if key in seen:
            continue
        seen.add(key)
        details = metadata.get(key, {})
        fields.append(
            RequestField(
                name=public_name,
                python_name=parameter.name,
                location=parameter.location,
                type_name=parameter.type_name,
                required=parameter.required,
                example=_example_from_parameter(details),
                description=details.get("description"),
                source=source,
            )
        )

    body_example: Any = None
    body_required = False
    body_content_type: str | None = None
    body_model = route.request_models[0] if route.request_models else None
    body_source: SourceRef | None = None
    body_fields: list[RequestField] = []
    if body_model:
        model = next((item for item in api_map.models if item.name == body_model), None)
        if model is not None:
            body_source = model.source

    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        body_required = bool(request_body.get("required", False))
        request_content = request_body.get("content", {})
        if isinstance(request_content, dict) and request_content:
            if "application/json" in request_content:
                body_content_type = "application/json"
            else:
                body_content_type = next(
                    (
                        key
                        for key in request_content
                        if key.endswith("+json")
                    ),
                    next(iter(request_content)),
                )

            preferred = request_content.get(body_content_type)
            if isinstance(preferred, dict):
                schema = preferred.get("schema")
                if isinstance(schema, dict):
                    body_example = _schema_example(api_map, schema)
                    if body_content_type in {
                        "application/x-www-form-urlencoded",
                        "multipart/form-data",
                    }:
                        body_fields = _body_fields(
                            api_map,
                            schema,
                            source=body_source or route.source,
                        )

    if body_example is None and body_model:
        model = next((item for item in api_map.models if item.name == body_model), None)
        if model is not None:
            body_example = _schema_example(api_map, model.schema_)

    return RequestTemplate(
        route_id=route.id,
        method=route.method,
        path=route.path,
        parameters=fields,
        body_example=body_example,
        body_required=body_required,
        body_content_type=body_content_type,
        body_model=body_model,
        body_source=body_source,
        body_fields=body_fields,
    )


def _parameter_source_and_type(
    api_map: FastAPIMap,
    route_id: str,
    location: str,
    name: str,
) -> tuple[SourceRef | None, str | None]:
    route = _route(api_map, route_id)

    if location == "body":
        template = build_request_template(api_map, route_id)
        for field in template.body_fields:
            if name in {field.name, field.python_name or field.name}:
                return field.source or route.source, field.type_name

        model_name = route.request_models[0] if route.request_models else None
        model = next(
            (item for item in api_map.models if item.name == model_name),
            None,
        )
        expected_type = None
        if model is not None:
            properties = model.schema_.get("properties", {})
            if isinstance(properties, dict):
                field_schema = properties.get(name)
                if isinstance(field_schema, dict):
                    expected_type = field_schema.get("type") or field_schema.get("$ref")
        if model is not None:
            return (
                model.field_sources.get(name) or model.source,
                expected_type,
            )
        return route.source, expected_type

    for parameter in route.parameters:
        if (
            parameter.location == location
            and name in {parameter.name, parameter.alias or parameter.name}
        ):
            return route.source, parameter.type_name

    for parameter, source in _dependency_parameters(api_map, route.dependencies):
        if (
            parameter.location == location
            and name in {parameter.name, parameter.alias or parameter.name}
        ):
            return source, parameter.type_name

    return route.source, None


def validation_issues(
    api_map: FastAPIMap,
    route_id: str,
    payload: Any,
) -> list[ValidationIssue]:
    """Normalize FastAPI's HTTP 422 detail payload and map it back to source."""

    if not isinstance(payload, dict):
        return []
    detail = payload.get("detail")
    if not isinstance(detail, list):
        return []

    issues: list[ValidationIssue] = []
    for entry in detail:
        if not isinstance(entry, dict):
            continue

        loc = entry.get("loc")
        if not isinstance(loc, (list, tuple)) or not loc:
            location = "unknown"
            field_parts: list[Any] = []
        else:
            location = str(loc[0])
            field_parts = list(loc[1:])

        field_path = ".".join(str(part) for part in field_parts)
        root_name = str(field_parts[0]) if field_parts else ""
        source, expected_type = _parameter_source_and_type(
            api_map,
            route_id,
            location,
            root_name,
        )

        issues.append(
            ValidationIssue(
                location=location,
                field_path=field_path,
                message=str(entry.get("msg", "Validation error")),
                error_type=str(entry.get("type", "validation_error")),
                expected_type=expected_type,
                input_value=entry.get("input"),
                source=source,
            )
        )

    return issues
