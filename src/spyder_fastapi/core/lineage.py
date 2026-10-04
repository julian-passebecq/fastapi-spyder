"""Queries over the normalized FastAPI lineage graph."""

from __future__ import annotations

from collections import defaultdict, deque

from spyder_fastapi.models import FastAPIMap


def impacted_routes(api_map: FastAPIMap, node_id: str) -> list[str]:
    """Return route IDs whose lineage reaches ``node_id``.

    This is the primitive behind a future "blast radius" view. It works for
    shared models, dependencies and handlers without parsing source code.
    """

    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in api_map.lineage.edges:
        adjacency[edge.source].append(edge.target)

    route_nodes = {
        node.id: node.route_id
        for node in api_map.lineage.nodes
        if node.kind == "route" and node.route_id is not None
    }

    impacted: list[str] = []
    for route_node, route_id in route_nodes.items():
        queue = deque([route_node])
        visited = {route_node}
        found = False
        while queue and not found:
            current = queue.popleft()
            if current == node_id:
                found = True
                break
            for target in adjacency.get(current, []):
                if target not in visited:
                    visited.add(target)
                    queue.append(target)
        if found:
            impacted.append(route_id)

    return sorted(impacted)
