"""The live verification must detect corrupted graphs and changed old records."""

from copy import deepcopy

import pytest

from test_node_graph import graph_data
from tms_if_input.model import parse_geojson
from tms_if_input.node_graph import build_node_documents
from verification.verify_node_graph_mongodb import (
    source_expectations, verify_graph, verify_preserved,
)


@pytest.mark.parametrize('corruption', [
    'position', 'connection', 'id-list', 'duplicate', 'missing', 'extra-field',
])
def test_live_graph_verification_rejects_incorrect_records(corruption):
    source = graph_data()
    expected = build_node_documents(parse_geojson(source))
    points, neighbors, _, _ = source_expectations(source)
    stored = deepcopy(expected)
    if corruption == 'position':
        stored[1]['position']['x'] += 1
    elif corruption == 'connection':
        stored[1]['connect_node'] = []
    elif corruption == 'id-list':
        stored[0]['node_ids'].pop()
    elif corruption == 'duplicate':
        stored.append(deepcopy(stored[1]))
    elif corruption == 'missing':
        stored.pop()
    else:
        stored[1]['unexpected'] = True
    with pytest.raises(AssertionError):
        verify_graph(stored, expected, points, neighbors)


def test_live_graph_verification_allows_bson_ids_and_an_incomplete_preflight():
    source = graph_data()
    expected = build_node_documents(parse_geojson(source))
    points, neighbors, _, _ = source_expectations(source)
    stored = [{'_id': index, **doc} for index, doc in enumerate(deepcopy(expected))]
    verify_graph(stored, expected, points, neighbors)
    verify_graph(stored[:2], expected, points, neighbors, allow_missing=True)


@pytest.mark.parametrize('corruption', ['runtime', 'deleted', 'new-task'])
def test_live_preservation_check_rejects_changes_to_previous_data(corruption):
    before = {
        'parameter': [{'_id': 'manual', 'runtime': {'completed': True}}],
        'task': [{'_id': 'task-1', 'task_id': 1}],
    }
    after = deepcopy(before)
    if corruption == 'runtime':
        after['parameter'][0]['runtime']['completed'] = False
    elif corruption == 'deleted':
        after['parameter'].clear()
    else:
        after['task'].append({'_id': 'task-2', 'task_id': 2})
    with pytest.raises(AssertionError):
        verify_preserved(before, after)


def test_live_preservation_check_accepts_parameter_additions():
    before = {
        'parameter': [{'_id': 'manual', 'runtime': {'completed': True}}],
        'task': [{'_id': 'task-1', 'task_id': 1}],
    }
    after = deepcopy(before)
    after['parameter'].append({'_id': 'new', 'node_id': 'new-node'})
    verify_preserved(before, after)
