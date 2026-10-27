"""Verify and optionally append a GeoJSON node graph in the real MongoDB.

Run from the package root with python3 -m verification.verify_node_graph_mongodb.
The --write option performs two insert-only imports and checks both readbacks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from bson import json_util
from pymongo import MongoClient

from tms_if_input.compiler import Compilation, validate_documents
from tms_if_input.database import write_database
from tms_if_input.model import identifier, parse_geojson
from tms_if_input.node_graph import build_node_documents


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def fingerprint(document):
    canonical = json_util.dumps(document, json_options=json_util.CANONICAL_JSON_OPTIONS,
                                sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def source_expectations(source):
    """Derive XY positions and unique endpoint pairs directly from raw GeoJSON."""
    points = {}
    edges = set()
    directed_count = 0
    for feature in source['features']:
        props, geometry = feature['properties'], feature['geometry']
        if geometry['type'] == 'Point':
            node_id = identifier(props.get('id', feature.get('id')))
            points[node_id] = {'x': geometry['coordinates'][0],
                               'y': geometry['coordinates'][1], 'z': 0.0}
        elif geometry['type'] == 'LineString':
            edges.add(frozenset((identifier(props['startid']), identifier(props['endid']))))
            directed_count += 1
    neighbors = {node_id: set() for node_id in points}
    for edge in edges:
        left, right = sorted(edge)
        neighbors[left].add(right)
        neighbors[right].add(left)
    return points, neighbors, len(edges), directed_count


def verify_graph(documents, expected, points, neighbors, *, allow_missing=False):
    expected_by_name = {doc['record_name']: doc for doc in expected}
    names = [doc['record_name'] for doc in documents]
    require(len(names) == len(set(names)), 'Duplicate graph records found')
    require(set(names) <= set(expected_by_name), 'Unexpected records share the graph ID')
    if not allow_missing:
        require(set(names) == set(expected_by_name), 'Stored graph has missing records')
    for document in documents:
        contents = {key: value for key, value in document.items() if key != '_id'}
        require(contents == expected_by_name[document['record_name']],
                f"Stored contents differ: {document['record_name']}")
        if 'node_ids' in document:
            require(document['node_ids'] == sorted(points), 'ID list differs from raw GeoJSON')
        else:
            node_id = document['node_id']
            require(document['position'] == points[node_id], f'Position mismatch: {node_id}')
            require(document['connect_node'] == sorted(neighbors[node_id]),
                    f'Connectivity mismatch: {node_id}')


def snapshot(database, path):
    documents = {name: list(database[name].find({})) for name in ('parameter', 'task')}
    path.write_text(json_util.dumps(documents, indent=2), encoding='utf-8')
    return documents


def verify_preserved(before, after):
    for name, documents in before.items():
        current = {fingerprint(doc['_id']): fingerprint(doc) for doc in after[name]}
        for document in documents:
            require(current.get(fingerprint(document['_id'])) == fingerprint(document),
                    f"Existing {name} document was changed or removed: {document['_id']}")
    require(len(before['task']) == len(after['task']), 'Task collection count changed')


def verify_summary(database, result):
    name = result['summary_record_name']
    matches = list(database.parameter.find({'record_name': name}))
    require(len(matches) == 1, 'Expected exactly one import summary')
    document = {key: value for key, value in matches[0].items() if key != '_id'}
    require(document == {
        'type': 'static', 'model_name': 'all', 'record_name': name,
        'machinery_names': [], 'joint_angle_param_names': [],
        'machinery_position_param_names': [], 'block_position_param_names': [],
        'geo_fence_param_names': [],
    }, 'Node-only import changed the existing summary classification')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geojson', type=Path, default=ROOT / 'json_samples/261004-kyoto.geojson')
    parser.add_argument('--mongo-uri', default='mongodb://localhost:27017')
    parser.add_argument('--mongo-db', default='rostmsdb')
    parser.add_argument('--timeout-ms', type=int, default=30000)
    parser.add_argument('--write', action='store_true', help='Insert the graph and verify a repeated import')
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)

    source_bytes = args.geojson.read_bytes()
    source = json.loads(source_bytes.decode('utf-8-sig'))
    graph = parse_geojson(source)
    documents = build_node_documents(graph)
    compilation = Compilation(documents, [])
    validate_documents(compilation)
    points, neighbors, edges, directed_count = source_expectations(source)
    verify_graph(documents, documents, points, neighbors)
    graph_id = documents[0]['graph_id']
    args.evidence_dir.mkdir(parents=True, exist_ok=True)

    with MongoClient(args.mongo_uri, serverSelectionTimeoutMS=args.timeout_ms,
                     connectTimeoutMS=args.timeout_ms, socketTimeoutMS=args.timeout_ms) as client:
        client.admin.command('ping')
        database = client[args.mongo_db]
        before = snapshot(database, args.evidence_dir / 'before.json')
        existing = list(database.parameter.find({'graph_id': graph_id}))
        verify_graph(existing, documents, points, neighbors, allow_missing=True)
        report = {
            'verified_at_utc': datetime.now(timezone.utc).isoformat(),
            'database': args.mongo_db, 'input': str(args.geojson.resolve()),
            'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
            'graph_id': graph_id, 'id_list_record_name': documents[0]['record_name'],
            'node_count': len(points), 'graph_document_count': len(documents),
            'source_line_count': directed_count, 'unique_connection_count': edges,
            'existing_graph_documents': len(existing),
            'before_counts': {name: len(rows) for name, rows in before.items()},
            'before_sha256': {name: fingerprint(rows) for name, rows in before.items()},
            'evidence_directory': str(args.evidence_dir.resolve()),
            'write_requested': args.write,
        }
        if args.write:
            first = write_database(compilation, mongo_uri=args.mongo_uri, mongo_db=args.mongo_db,
                                   timeout_ms=args.timeout_ms)
            require(first['inserted_parameters'] == len(documents) - len(existing),
                    'Unexpected number of graph insertions')
            require(first['skipped_parameters'] == len(existing), 'Unexpected number of skips')
            require(first['inserted_tasks'] == 0, 'Unexpected task insertion')
            verify_summary(database, first)
            after_first = snapshot(database, args.evidence_dir / 'after_first.json')
            verify_preserved(before, after_first)
            verify_graph(list(database.parameter.find({'graph_id': graph_id})),
                         documents, points, neighbors)
            require(len(after_first['parameter']) == len(before['parameter'])
                    + first['inserted_parameters'] + 1, 'Unexpected parameter additions')

            repeated = write_database(compilation, mongo_uri=args.mongo_uri, mongo_db=args.mongo_db,
                                      timeout_ms=args.timeout_ms)
            require(repeated['inserted_parameters'] == 0, 'Repeated import duplicated graph records')
            require(repeated['skipped_parameters'] == len(documents), 'Repeated import missed records')
            require(repeated['inserted_tasks'] == 0, 'Repeated import added tasks')
            verify_summary(database, repeated)
            after_repeat = snapshot(database, args.evidence_dir / 'after_repeat.json')
            verify_preserved(after_first, after_repeat)
            verify_graph(list(database.parameter.find({'graph_id': graph_id})),
                         documents, points, neighbors)
            require(len(after_repeat['parameter']) == len(after_first['parameter']) + 1,
                    'Repeated import added records other than its summary')
            require(args.geojson.read_bytes() == source_bytes, 'GeoJSON source changed during verification')
            report.update({
                'first_import': first, 'repeated_import': repeated,
                'after_first_counts': {name: len(rows) for name, rows in after_first.items()},
                'after_repeat_counts': {name: len(rows) for name, rows in after_repeat.items()},
                'all_existing_documents_unchanged': True,
                'all_source_nodes_and_connections_verified': True,
                'repeated_import_added_no_graph_duplicates': True,
                'task_collection_unchanged': True, 'source_file_unchanged': True,
                'status': 'passed',
            })
        else:
            report['status'] = 'read_only_preflight_passed'
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
