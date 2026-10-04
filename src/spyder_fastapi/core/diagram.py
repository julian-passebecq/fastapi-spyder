"""Headless graph projections for FastAPI Studio diagrams."""

from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha1

from spyder_fastapi.core.lineage import impacted_routes
from spyder_fastapi.core.telemetry import (
    NativeTelemetryStore,
    is_external_span,
    span_category,
    span_route_id,
    span_target,
)
from spyder_fastapi.models import (
    DiagramEdge,
    DiagramNode,
    DiagramProjection,
    FastAPIMap,
    LineageEdge,
    LineageNode,
    NativeTelemetrySpan,
    RouteTestIndex,
)


def _diagram_node(
    api_map: FastAPIMap,
    node: LineageNode,
) -> DiagramNode:
    impact_count = 0
    if node.kind in {"dependency", "model"}:
        impact_count = len(impacted_routes(api_map, node.id))
    elif node.kind == "route":
        impact_count = 1

    return DiagramNode(
        id=node.id,
        kind=node.kind,
        label=node.label,
        source=node.source,
        route_id=node.route_id,
        impact_count=impact_count,
    )


def _test_nodes_for_routes(
    test_index: RouteTestIndex | None,
    route_ids: set[str],
) -> tuple[list[DiagramNode], list[DiagramEdge]]:
    if test_index is None:
        return [], []

    nodes: dict[str, DiagramNode] = {}
    edges: dict[tuple[str, str, str], DiagramEdge] = {}

    for reference in test_index.references:
        if reference.route_id not in route_ids:
            continue

        file_name = reference.source.file or "<unknown>"
        test_id = f"test-function:{file_name}:{reference.test_name}"
        nodes.setdefault(
            test_id,
            DiagramNode(
                id=test_id,
                kind="test",
                label=reference.test_name,
                source=reference.source,
                route_id=reference.route_id,
                evidence="test",
            ),
        )

        edge = DiagramEdge(
            source=f"route:{reference.route_id}",
            target=test_id,
            relation="tested_by",
        )
        edges[(edge.source, edge.target, edge.relation)] = edge

    return list(nodes.values()), list(edges.values())


def _edge(edge: LineageEdge) -> DiagramEdge:
    return DiagramEdge(
        source=edge.source,
        target=edge.target,
        relation=edge.relation,
    )


def route_projection(
    api_map: FastAPIMap,
    route_id: str,
    test_index: RouteTestIndex | None = None,
    *,
    include_tests: bool = False,
) -> DiagramProjection:
    """Project one route and every lineage node reachable from it."""

    root_id = f"route:{route_id}"
    nodes_by_id = {node.id: node for node in api_map.lineage.nodes}
    if root_id not in nodes_by_id:
        raise KeyError(f"Unknown route for diagram: {route_id}")

    adjacency: dict[str, list[LineageEdge]] = defaultdict(list)
    for edge in api_map.lineage.edges:
        adjacency[edge.source].append(edge)

    reachable = {root_id}
    queue = deque([root_id])
    while queue:
        current = queue.popleft()
        for edge in adjacency.get(current, []):
            if edge.target not in reachable:
                reachable.add(edge.target)
                queue.append(edge.target)

    nodes = [
        _diagram_node(api_map, nodes_by_id[node_id])
        for node_id in reachable
        if node_id in nodes_by_id
    ]
    edges = [
        _edge(edge)
        for edge in api_map.lineage.edges
        if edge.source in reachable and edge.target in reachable
    ]

    if include_tests:
        test_nodes, test_edges = _test_nodes_for_routes(
            test_index,
            {route_id},
        )
        nodes.extend(test_nodes)
        edges.extend(test_edges)

    return DiagramProjection(
        mode="route",
        title=route_id,
        roots=[root_id],
        nodes=sorted(nodes, key=lambda node: (node.kind, node.label, node.id)),
        edges=sorted(
            edges,
            key=lambda edge: (edge.source, edge.target, edge.relation),
        ),
        focus_id=root_id,
    )


def global_projection(
    api_map: FastAPIMap,
    test_index: RouteTestIndex | None = None,
    *,
    include_tests: bool = False,
) -> DiagramProjection:
    """Compact the whole API into routes, handlers, dependencies and models.

    Request parameter nodes are intentionally contracted. Body/model edges are
    preserved by connecting the parameter parent directly to the model so the
    global graph remains readable on non-trivial APIs.
    """

    nodes_by_id = {node.id: node for node in api_map.lineage.nodes}
    keep_kinds = {"route", "handler", "dependency", "model"}
    kept_ids = {
        node.id for node in api_map.lineage.nodes if node.kind in keep_kinds
    }

    incoming: dict[str, list[LineageEdge]] = defaultdict(list)
    outgoing: dict[str, list[LineageEdge]] = defaultdict(list)
    for edge in api_map.lineage.edges:
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)

    edges: dict[tuple[str, str, str], DiagramEdge] = {}

    for edge in api_map.lineage.edges:
        if edge.source in kept_ids and edge.target in kept_ids:
            projected = _edge(edge)
            edges[(projected.source, projected.target, projected.relation)] = projected

    for parameter in (
        node for node in api_map.lineage.nodes if node.kind == "parameter"
    ):
        parents = [
            edge.source
            for edge in incoming.get(parameter.id, [])
            if edge.source in kept_ids
        ]
        model_targets = [
            edge.target
            for edge in outgoing.get(parameter.id, [])
            if edge.target in kept_ids
            and nodes_by_id.get(edge.target) is not None
            and nodes_by_id[edge.target].kind == "model"
        ]

        for parent in parents:
            for model_id in model_targets:
                relation = "accepts_model"
                projected = DiagramEdge(
                    source=parent,
                    target=model_id,
                    relation=relation,
                )
                edges[(parent, model_id, relation)] = projected

    nodes = [
        _diagram_node(api_map, node)
        for node in api_map.lineage.nodes
        if node.id in kept_ids
    ]
    roots = sorted(
        node.id
        for node in api_map.lineage.nodes
        if node.kind == "route"
    )

    if include_tests:
        test_nodes, test_edges = _test_nodes_for_routes(
            test_index,
            {
                node.route_id
                for node in api_map.lineage.nodes
                if node.kind == "route" and node.route_id is not None
            },
        )
        nodes.extend(test_nodes)
        for edge in test_edges:
            edges[(edge.source, edge.target, edge.relation)] = edge

    return DiagramProjection(
        mode="global",
        title=api_map.title,
        roots=roots,
        nodes=sorted(nodes, key=lambda node: (node.kind, node.label, node.id)),
        edges=sorted(
            edges.values(),
            key=lambda edge: (edge.source, edge.target, edge.relation),
        ),
    )


def impact_projection(
    api_map: FastAPIMap,
    node_id: str,
    test_index: RouteTestIndex | None = None,
    *,
    include_tests: bool = False,
) -> DiagramProjection:
    """Project only real lineage paths from impacted routes to one node."""

    nodes_by_id = {node.id: node for node in api_map.lineage.nodes}
    focus = nodes_by_id.get(node_id)
    if focus is None:
        raise KeyError(f"Unknown lineage node for impact diagram: {node_id}")

    adjacency: dict[str, list[LineageEdge]] = defaultdict(list)
    for edge in api_map.lineage.edges:
        adjacency[edge.source].append(edge)

    selected_nodes = {node_id}
    selected_edges: dict[tuple[str, str, str], DiagramEdge] = {}
    roots: list[str] = []

    route_nodes = [
        node
        for node in api_map.lineage.nodes
        if node.kind == "route" and node.route_id is not None
    ]

    for route_node in route_nodes:
        queue = deque([route_node.id])
        previous: dict[str, tuple[str, LineageEdge] | None] = {
            route_node.id: None
        }

        while queue and node_id not in previous:
            current = queue.popleft()
            for edge in adjacency.get(current, []):
                if edge.target in previous:
                    continue
                previous[edge.target] = (current, edge)
                queue.append(edge.target)

        if node_id not in previous:
            continue

        roots.append(route_node.id)
        cursor = node_id
        selected_nodes.add(route_node.id)

        while cursor != route_node.id:
            parent_info = previous.get(cursor)
            if parent_info is None:
                break
            parent, edge = parent_info
            selected_nodes.add(parent)
            selected_nodes.add(cursor)
            projected = _edge(edge)
            selected_edges[
                (projected.source, projected.target, projected.relation)
            ] = projected
            cursor = parent

    nodes = [
        _diagram_node(api_map, nodes_by_id[current])
        for current in selected_nodes
        if current in nodes_by_id
    ]

    if include_tests:
        impacted_route_ids = {
            nodes_by_id[root].route_id
            for root in roots
            if root in nodes_by_id and nodes_by_id[root].route_id is not None
        }
        test_nodes, test_edges = _test_nodes_for_routes(
            test_index,
            impacted_route_ids,
        )
        nodes.extend(test_nodes)
        for edge in test_edges:
            selected_edges[(edge.source, edge.target, edge.relation)] = edge

    return DiagramProjection(
        mode="impact",
        title=f"Impact: {focus.label}",
        roots=sorted(set(roots)),
        nodes=sorted(nodes, key=lambda node: (node.kind, node.label, node.id)),
        edges=sorted(
            selected_edges.values(),
            key=lambda edge: (edge.source, edge.target, edge.relation),
        ),
        focus_id=node_id,
    )


def _runtime_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]

    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _runtime_handler_anchor(
    api_map: FastAPIMap,
    route_id: str,
    projected_ids: set[str],
) -> str | None:
    route_node = f"route:{route_id}"
    for edge in api_map.lineage.edges:
        if (
            edge.source == route_node
            and edge.relation == "handled_by"
            and edge.target in projected_ids
        ):
            return edge.target
    return None


def _runtime_dependency_anchor(
    api_map: FastAPIMap,
    function_name: str | None,
    projected_ids: set[str],
) -> str | None:
    if not function_name:
        return None

    exact = [
        dependency.id
        for dependency in api_map.dependencies
        if dependency.name == function_name
        and dependency.id in projected_ids
    ]
    if len(exact) == 1:
        return exact[0]

    short_name = function_name.rsplit(".", 1)[-1]
    suffix = [
        dependency.id
        for dependency in api_map.dependencies
        if dependency.name.rsplit(".", 1)[-1] == short_name
        and dependency.id in projected_ids
    ]
    return suffix[0] if len(suffix) == 1 else None


def _runtime_anchor(
    api_map: FastAPIMap,
    route_id: str,
    projected_ids: set[str],
    span,
    spans_by_id: dict[str, NativeTelemetrySpan],
) -> str:
    """Anchor one observed downstream span to the closest static FastAPI node."""

    route_node = f"route:{route_id}"
    handler = _runtime_handler_anchor(
        api_map,
        route_id,
        projected_ids,
    )

    parent_id = span.parent_span_id
    visited: set[str] = set()
    while parent_id and parent_id not in visited:
        visited.add(parent_id)
        parent = spans_by_id.get(parent_id)
        if parent is None:
            break

        if parent.name == "fastapi.dependencies":
            function_name = parent.attributes.get("code.function.name")
            dependency = _runtime_dependency_anchor(
                api_map,
                str(function_name) if function_name else None,
                projected_ids,
            )
            if dependency is not None:
                return dependency
            return route_node

        if parent.name == "fastapi.endpoint":
            return handler or route_node

        # Background tasks and serialization are runtime phases rather than
        # deterministic static nodes in FastAPIMap. Keep them attached to the
        # route instead of pretending they are handler/dependency lineage.
        if parent.name in {
            "fastapi.background_task",
            "fastapi.serialization",
        }:
            return route_node

        parent_id = parent.parent_span_id

    return handler or route_node


def overlay_runtime_lineage(
    api_map: FastAPIMap,
    projection: DiagramProjection,
    telemetry: NativeTelemetryStore,
) -> DiagramProjection:
    """Overlay observed OpenTelemetry downstream calls on a static projection.

    The returned graph keeps static/test/runtime evidence separate. Runtime
    nodes are only created from spans actually captured in traces whose route
    already exists in the current projection.
    """

    projected_route_ids = {
        node.route_id
        for node in projection.nodes
        if node.kind == "route" and node.route_id is not None
    }
    if not projected_route_ids or not telemetry.spans:
        return projection.model_copy(deep=True)

    result = projection.model_copy(deep=True)
    projected_ids = {node.id for node in result.nodes}
    runtime_groups: dict[
        tuple[str, str, str, str, str],
        list[NativeTelemetrySpan],
    ] = defaultdict(list)

    for trace_id in telemetry.trace_ids():
        root = telemetry.trace_root(trace_id)
        route_id = span_route_id(root) if root is not None else None
        if route_id not in projected_route_ids:
            continue

        trace_spans = telemetry.trace_spans(trace_id)
        spans_by_id = {span.span_id: span for span in trace_spans}

        for span in trace_spans:
            if not is_external_span(span):
                continue

            category = span_category(span)
            target = span_target(span) or span.name
            anchor = _runtime_anchor(
                api_map,
                route_id,
                projected_ids,
                span,
                spans_by_id,
            )
            key = (
                route_id,
                anchor,
                category,
                target,
                span.name,
            )
            runtime_groups[key].append(span)

    if not runtime_groups:
        return result

    existing_edges = {
        (edge.source, edge.target, edge.relation)
        for edge in result.edges
    }

    for (
        route_id,
        anchor,
        category,
        target,
        span_name,
    ), spans in sorted(runtime_groups.items()):
        ordered = sorted(spans, key=lambda item: item.end_ns)
        durations = [span.duration_ms for span in ordered]
        digest = sha1(
            "|".join(
                [
                    route_id,
                    anchor,
                    category,
                    target,
                    span_name,
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]
        node_id = f"runtime:{category}:{digest}"

        label = target
        if category == "database":
            operation = ordered[-1].attributes.get("db.operation.name")
            if operation:
                label = f"{target} · {operation}"

        result.nodes.append(
            DiagramNode(
                id=node_id,
                kind=category,
                label=label,
                route_id=route_id,
                evidence="runtime",
                observed_count=len(ordered),
                average_ms=sum(durations) / len(durations),
                p95_ms=_runtime_percentile(durations, 0.95),
                last_ms=ordered[-1].duration_ms,
                target=target,
                trace_id=ordered[-1].trace_id,
            )
        )

        relation = f"observed_{category.replace('-', '_')}"
        edge_key = (anchor, node_id, relation)
        if edge_key not in existing_edges:
            result.edges.append(
                DiagramEdge(
                    source=anchor,
                    target=node_id,
                    relation=relation,
                )
            )
            existing_edges.add(edge_key)

    result.nodes.sort(
        key=lambda node: (
            node.evidence,
            node.kind,
            node.label,
            node.id,
        )
    )
    result.edges.sort(
        key=lambda edge: (edge.source, edge.target, edge.relation)
    )
    return result

