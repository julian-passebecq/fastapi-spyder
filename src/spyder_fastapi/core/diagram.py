"""Headless graph projections for FastAPI Studio diagrams."""

from __future__ import annotations

from collections import defaultdict, deque

from spyder_fastapi.core.lineage import impacted_routes
from spyder_fastapi.models import (
    DiagramEdge,
    DiagramNode,
    DiagramProjection,
    FastAPIMap,
    LineageEdge,
    LineageNode,
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


def _edge(edge: LineageEdge) -> DiagramEdge:
    return DiagramEdge(
        source=edge.source,
        target=edge.target,
        relation=edge.relation,
    )


def route_projection(api_map: FastAPIMap, route_id: str) -> DiagramProjection:
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


def global_projection(api_map: FastAPIMap) -> DiagramProjection:
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
