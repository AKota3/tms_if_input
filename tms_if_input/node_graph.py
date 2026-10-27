"""Compile all GeoJSON nodes and their undirected adjacency for MongoDB."""

from __future__ import annotations

import hashlib
import json

from .model import GeoGraph


def build_node_documents(graph: GeoGraph) -> list[dict]:
    """Return one ID list and one position/connection document per Point.

    Only endpoint connectivity matters: reverse and parallel LineStrings add
    no duplicate neighbors. Isolated Points remain in the graph. Content-based
    names allow changed graphs to be appended without overwriting old imports.
    """
    neighbors = {node_id: set() for node_id in graph.points}
    for section in graph.sections:
        neighbors[section.start].add(section.end)
        neighbors[section.end].add(section.start)

    node_ids = sorted(graph.points)
    nodes = [
        {'node_id': node_id,
         'position': {**graph.point(node_id).xy(), 'z': 0.0},
         'connect_node': sorted(neighbors[node_id])}
        for node_id in node_ids
    ]
    # Canonical node/neighbor ordering makes feature order, edge IDs and edge
    # direction irrelevant to graph identity, just as they are to adjacency.
    contents = json.dumps(nodes, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    graph_id = hashlib.sha256(contents.encode('utf-8')).hexdigest()[:16]
    common = {'type': 'static', 'graph_id': graph_id}
    return [
        {**common, 'record_name': f'geojson_node_ids_{graph_id}', 'node_ids': node_ids},
        *[{**common, 'record_name': f"geojson_node_{graph_id}_{node['node_id']}",
           **node} for node in nodes],
    ]
