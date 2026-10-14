"""Check the additional topic interface against raw, replaceable source files."""

import copy
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

from tms_if_input.system_topics_data import build_block_state, load_topic_scenario


SAMPLES = Path(__file__).resolve().parents[1] / "json_samples"


def raw_parameter(value):
    """Independent XML-value reader; no production parsing or compilation."""
    if value.startswith(("[", "{")):
        quoted = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', value)
        return json.loads(quoted)
    try:
        return float(value)
    except ValueError:
        return value


def source_node(feature, neighbors=()):
    properties = feature["properties"]
    coordinates = feature["geometry"]["coordinates"]
    return {"node_id": str(properties["id"]), "connect_node": list(neighbors),
            "x": coordinates[0], "y": coordinates[1], "z": 0.0,
            "size": properties["metadata"]["radius"]}


def assert_source_messages(scenario, geojson, root):
    """Compute every expected field directly from input, including topology."""
    points = {str(feature["properties"]["id"]): feature
              for feature in geojson["features"]
              if feature["geometry"]["type"] == "Point"}
    neighbors = {key: [] for key in points}
    for feature in geojson["features"]:
        if feature["geometry"]["type"] != "LineString":
            continue
        source, destination = (str(feature["properties"][key])
                               for key in ("startid", "endid"))
        for source, destination in ((source, destination), (destination, source)):
            if destination not in neighbors[source]:
                neighbors[source].append(destination)
    assert scenario.graph.to_dict() == {
        "message_id": "FirstSetGraph",
        "node_ids": [source_node(feature, neighbors[key])
                     for key, feature in points.items()],
    }
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    aliases = {element.get("id"): element.get("type")
               for element in root.findall("./machines/machine")}
    leveling = [element for element in root.findall("./procedure/tasks/task")
                if element.get("name") == "leveling"]
    assert len(scenario.leveling_tasks) == len(leveling)
    for data, task in zip(scenario.leveling_tasks, leveling):
        params = {element.get("name"): raw_parameter(element.get("value"))
                  for element in task.findall("parameter")}
        direction = params["block_vector"]
        connections = [str(node) for node in params["connection_node"]]
        dumps = []
        for raw_id in params["dump_node"]:
            feature = points[str(raw_id)]
            x, y = feature["geometry"]["coordinates"][:2]
            # The distance transverse to the leveling vector selects the column.
            def transverse_distance(connection_id):
                cx, cy = points[connection_id]["geometry"]["coordinates"][:2]
                return abs((x - cx) * direction["y"] - (y - cy) * direction["x"])
            connection = min(connections, key=transverse_distance)
            dumps.append(source_node(feature, [connection]))
        bulldozer_ids = [str(node) for node in params["bulldozer_node"]]
        bulldozers = [source_node(points[node],
                                 bulldozer_ids[max(0, index - 1):index]
                                 + bulldozer_ids[index + 1:index + 2])
                      for index, node in enumerate(bulldozer_ids)]
        assert data.to_dict() == {
            "task_id": task.get("id"), "machinery_id": params["machine"],
            "leveling_height": params["leveling_height"],
            "block_size_x": params["block_size"]["x"],
            "block_size_y": params["block_size"]["y"],
            "block_vector_x": direction["x"],
            "block_vector_y": direction["y"],
            "block_vector_z": direction["z"],
            "block_center": [{"node_id": "", "connect_node": [], "size": 0.0,
                              **center} for center in params["block_center"]],
            "soil_volume": params["soil_volume"],
            "dump_node": {"message_id": "LevelingTaskParamDumpNode", "node_ids": dumps},
            "bulldozer_node": {"message_id": "LevelingTaskParamBulldozerNode",
                               "node_ids": bulldozers},
        }
        assert data.model_name == aliases[params["machine"]]
        assert data.blackboard_record_name == "SAMPLE_BLACKBOARD_" + data.model_name
        suffix = "_" + task.get("id") if len(leveling) > 1 else ""
        assert data.block_record_names == tuple(
            f"block_{index}{suffix}" for index in range(1, len(params["block_center"]) + 1))


def independent_inputs(count=4):
    """A valid plan with opaque IDs, an unknown machine model and changed values."""
    coordinates = {f"entry:{index}": (30.0 * index, -20.0 * index)
                   for index in range(3)}
    for index in range(count):
        cx, cy = coordinates[f"entry:{index % 3}"]
        coordinates[f"release:{index}"] = (cx + 6 + index * 3, cy + 8 + index * 4)
        coordinates[f"blade:{index}"] = (101 + index * 7.25, 21 - index * 2.75)
    coordinates["isolated:outside-work"] = (999.25, -432.125)
    features = [{"type": "Feature", "properties": {
        "id": key, "metadata": {"radius": 0.5 + index / 4, "route_rank": 0}},
        "geometry": {"type": "Point", "coordinates": [x, y, 9999]}}
        for index, (key, (x, y)) in enumerate(coordinates.items())]
    edges = [("entry:0", "entry:1"), ("entry:0", "entry:2")]
    edges += [(f"entry:{index % 3}", f"release:{index}") for index in range(count)]
    edges += [(f"blade:{index}", f"blade:{index + 1}") for index in range(count - 1)]
    for index, (source, destination) in enumerate(edges):
        for reverse in range(2):
            start, end = (destination, source) if reverse else (source, destination)
            features.append({"type": "Feature", "properties": {
                "id": f"edge:{index}:{reverse}", "startid": start, "endid": end},
                "geometry": {"type": "LineString", "coordinates": [
                    list(coordinates[start]), list(coordinates[end])]}})
    root = ET.Element("ConstructionPlan")
    machines = ET.SubElement(root, "machines")
    ET.SubElement(machines, "machine", {"id": "grader:delta", "type": "custom-grader-v42"})
    procedure = ET.SubElement(root, "procedure")
    tasks = ET.SubElement(procedure, "tasks")

    def add_task(task_id, name, params=None):
        element = ET.SubElement(tasks, "task", {"id": task_id, "name": name})
        for key, value in (params or {}).items():
            ET.SubElement(element, "parameter", {"name": key, "value":
                value if isinstance(value, str) else json.dumps(value)})
        return element

    add_task("begin:9", "start")
    add_task("ready:7", "initialize", {"machine": "grader:delta", "target_node": "blade:0",
             "rotation": {"x": 0, "y": 0, "z": 0, "w": 1}})
    add_task("grading:1125899906842625", "leveling", {
        "machine": "grader:delta", "leveling_height": 16.75,
        "block_size": {"x": 2.25, "y": 5.5}, "block_vector": {"x": 3, "y": 4, "z": 2},
        "block_center": [{"x": 500 + index * 2.5, "y": -700 - index, "z": 7.25 + index}
                         for index in range(count)],
        "soil_volume": [0.125 + index * 0.5 for index in range(count)],
        "dump_node": [f"release:{index}" for index in range(count)],
        "bulldozer_node": [f"blade:{index}" for index in range(count)],
        "connection_node": [f"entry:{index}" for index in range(3)],
    })
    add_task("done:2", "end")
    flow = ET.SubElement(procedure, "flow")
    sequence = ["begin:9", "ready:7", "grading:1125899906842625", "done:2"]
    for previous, following in zip(sequence, sequence[1:]):
        ET.SubElement(flow, "edge", {"from": previous, "to": following})
    return {"type": "FeatureCollection", "features": features}, root


def write_inputs(tmp_path, geojson, root):
    geojson_path = tmp_path / "changed-data_日本語.geojson"
    xml_path = tmp_path / "independent-scenario.xml"
    geojson_path.write_text(json.dumps(geojson), encoding="utf-8")
    xml_path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    return geojson_path, xml_path


def parameter(root, name):
    return root.find(f"./procedure/tasks/task[@name='leveling']/parameter[@name='{name}']")


def test_sample_messages_match_every_raw_source_value():
    geojson_path = SAMPLES / "261008-kyoto.geojson"
    xml_path = SAMPLES / "261008-kyoto.xml"
    geojson = json.loads(geojson_path.read_text(encoding="utf-8-sig"))
    root = ET.fromstring(xml_path.read_text(encoding="utf-8-sig"))
    scenario = load_topic_scenario(geojson_path, xml_path)
    assert_source_messages(scenario, geojson, root)
    assert len(scenario.graph.node_ids) == 42
    assert len(scenario.leveling_tasks[0].block_center) == 9


@pytest.mark.parametrize("count", [1, 4, 12])
def test_replaced_files_use_new_ids_model_alias_values_and_variable_counts(tmp_path, count):
    geojson, root = independent_inputs(count)
    paths = write_inputs(tmp_path, geojson, root)
    before = [path.read_bytes() for path in paths]
    scenario = load_topic_scenario(*paths)
    assert_source_messages(scenario, geojson, root)
    assert [path.read_bytes() for path in paths] == before
    task = scenario.leveling_tasks[0]
    # This API preserves the supplied vector, including z and non-unit length.
    assert (task.block_vector_x, task.block_vector_y, task.block_vector_z) == (3, 4, 2)
    assert task.task_id == "grading:1125899906842625"
    assert task.machinery_id == "grader:delta"
    assert len(task.bulldozer_node.node_ids) == count
    assert all(node.z == 0 for node in scenario.graph.node_ids)
    assert next(node for node in scenario.graph.node_ids
                if node.node_id == "isolated:outside-work").connect_node == ()
    if count >= 4:
        entry = next(node for node in scenario.graph.node_ids if node.node_id == "entry:0")
        assert len(entry.connect_node) >= 4
        assert len(set(entry.connect_node)) == len(entry.connect_node)


def test_namespace_and_reversed_input_feature_order_are_supported(tmp_path):
    geojson, root = independent_inputs()
    root.set("xmlns", "urn:another-plan:2026")
    geojson["features"].reverse()
    scenario = load_topic_scenario(*write_inputs(tmp_path, geojson, root))
    assert_source_messages(scenario, geojson, root)


def test_multiple_areas_use_qualified_block_names_and_keep_states_separate(tmp_path):
    geojson, root = independent_inputs()
    tasks = root.find("./procedure/tasks")
    second = copy.deepcopy(tasks.find("task[@name='leveling']"))
    second.set("id", "grading:second-area")
    tasks.insert(len(tasks) - 1, second)
    flow = root.find("./procedure/flow")
    edge = flow.find("edge[@to='done:2']")
    edge.set("to", "grading:second-area")
    ET.SubElement(flow, "edge", {"from": "grading:second-area", "to": "done:2"})
    scenario = load_topic_scenario(*write_inputs(tmp_path, geojson, root))
    assert_source_messages(scenario, geojson, root)
    first, second_data = scenario.leveling_tasks
    completed = [first.block_record_names[0], second_data.block_record_names[2], "block_2"]
    assert build_block_state(first, completed) == [True, False, False, False]
    assert build_block_state(second_data, completed) == [False, False, True, False]


def test_state_array_follows_centers_and_changes_with_each_read(tmp_path):
    task = load_topic_scenario(*write_inputs(tmp_path, *independent_inputs(12))).leveling_tasks[0]
    assert build_block_state(task, []) == [False] * 12
    completed = ["block_12", "block_2", "block_2", "block_99", "another_area"]
    assert build_block_state(task, completed) == [False, True] + [False] * 9 + [True]
    assert build_block_state(task, ["block_1"]) == [True] + [False] * 11
    assert completed == ["block_12", "block_2", "block_2", "block_99", "another_area"]


@pytest.mark.parametrize("invalid", [None, "block_1", {"block_1": True}, [1], [True], [None]])
def test_invalid_mongo_state_is_rejected(tmp_path, invalid):
    task = load_topic_scenario(*write_inputs(tmp_path, *independent_inputs())).leveling_tasks[0]
    with pytest.raises(ValueError, match="LEVELED_BLOCK"):
        build_block_state(task, invalid)


@pytest.mark.parametrize("radius", [None, -1, True, float("nan"), float("inf"), 4e38])
def test_missing_invalid_or_unrepresentable_radius_is_rejected(tmp_path, radius):
    geojson, root = independent_inputs()
    metadata = geojson["features"][0]["properties"]["metadata"]
    if radius is None:
        metadata.pop("radius")
    else:
        metadata["radius"] = radius
    with pytest.raises(ValueError, match="radius|finite|float32|number"):
        load_topic_scenario(*write_inputs(tmp_path, geojson, root))


@pytest.mark.parametrize("name", ["soil_volume", "dump_node", "bulldozer_node"])
def test_mismatched_block_arrays_are_rejected(tmp_path, name):
    geojson, root = independent_inputs()
    element = parameter(root, name)
    element.set("value", json.dumps(json.loads(element.get("value"))[:-1]))
    with pytest.raises(ValueError, match="length"):
        load_topic_scenario(*write_inputs(tmp_path, geojson, root))


def test_unknown_line_endpoint_is_rejected(tmp_path):
    geojson, root = independent_inputs()
    edge = next(feature for feature in geojson["features"]
                if feature["geometry"]["type"] == "LineString")
    edge["properties"]["endid"] = "not-in-this-file"
    with pytest.raises(ValueError, match="Point ID.*not-in-this-file"):
        load_topic_scenario(*write_inputs(tmp_path, geojson, root))


def test_unknown_leveling_node_is_rejected(tmp_path):
    geojson, root = independent_inputs()
    element = parameter(root, "dump_node")
    values = json.loads(element.get("value"))
    values[0] = "removed-dump"
    element.set("value", json.dumps(values))
    with pytest.raises(ValueError, match="Point ID.*removed-dump"):
        load_topic_scenario(*write_inputs(tmp_path, geojson, root))


@pytest.mark.parametrize("field", ["leveling_height", "soil_volume", "block_center", "block_vector"])
def test_values_outside_ros_float32_range_are_rejected(tmp_path, field):
    geojson, root = independent_inputs()
    element = parameter(root, field)
    value = json.loads(element.get("value"))
    if field == "soil_volume":
        value[0] = 4e38
    elif field == "block_center":
        value[0]["z"] = 4e38
    elif field == "block_vector":
        value["x"] = 4e38
    else:
        value = 4e38
    element.set("value", json.dumps(value))
    with pytest.raises(ValueError, match="float32"):
        load_topic_scenario(*write_inputs(tmp_path, geojson, root))


def test_ambiguous_dump_entry_column_is_rejected(tmp_path):
    geojson, root = independent_inputs()
    first = next(feature for feature in geojson["features"]
                 if feature["properties"]["id"] == "entry:0")
    second = next(feature for feature in geojson["features"]
                  if feature["properties"]["id"] == "entry:1")
    second["geometry"]["coordinates"] = first["geometry"]["coordinates"][:]
    for feature in geojson["features"]:
        if feature["geometry"]["type"] != "LineString":
            continue
        for endpoint, offset in (("startid", 0), ("endid", -1)):
            if feature["properties"][endpoint] == "entry:1":
                feature["geometry"]["coordinates"][offset] = second["geometry"]["coordinates"][:2]
    with pytest.raises(ValueError, match="ambiguous connection"):
        load_topic_scenario(*write_inputs(tmp_path, geojson, root))


def test_xml_without_leveling_is_rejected(tmp_path):
    geojson, root = independent_inputs()
    root.find("./machines/machine").set("type", "d37pxi")
    tasks = root.find("./procedure/tasks")
    tasks.remove(tasks.find("task[@name='leveling']"))
    flow = root.find("./procedure/flow")
    flow.remove(flow.find("edge[@from='grading:1125899906842625']"))
    flow.find("edge[@from='ready:7']").set("to", "done:2")
    with pytest.raises(ValueError, match="no leveling tasks"):
        load_topic_scenario(*write_inputs(tmp_path, geojson, root))


def multiple_task_scenario(tmp_path, separate_machines):
    geojson, root = independent_inputs(3)
    tasks = root.find("./procedure/tasks")
    second = copy.deepcopy(tasks.find("task[@name='leveling']"))
    second.set("id", "grading:second-area")
    if separate_machines:
        ET.SubElement(root.find("machines"), "machine", {
            "id": "grader:epsilon", "type": "another-custom-model"})
        initialize = copy.deepcopy(tasks.find("task[@name='initialize']"))
        initialize.set("id", "ready:epsilon")
        initialize.find("parameter[@name='machine']").set("value", "grader:epsilon")
        second.find("parameter[@name='machine']").set("value", "grader:epsilon")
        tasks.insert(len(tasks) - 1, initialize)
    tasks.insert(len(tasks) - 1, second)
    flow = root.find("./procedure/flow")
    old_end = flow.find("edge[@to='done:2']")
    old_end.set("to", "ready:epsilon" if separate_machines else "grading:second-area")
    if separate_machines:
        ET.SubElement(flow, "edge", {"from": "ready:epsilon", "to": "grading:second-area"})
    ET.SubElement(flow, "edge", {"from": "grading:second-area", "to": "done:2"})
    return load_topic_scenario(*write_inputs(tmp_path, geojson, root))


def test_protocol_rejects_foreign_or_early_reports_and_runs_three_stages(tmp_path):
    from tms_if_input.system_topics_protocol import TopicProtocol

    scenario = load_topic_scenario(*write_inputs(tmp_path, *independent_inputs()))
    task = scenario.leveling_tasks[0]
    protocol = TopicProtocol(scenario)
    assert protocol.phase == "graph"
    assert protocol.pending_tasks == ()
    assert protocol.reporting_tasks == ()
    for report in [("TaskGet", task.task_id, task.machinery_id),
                   ("FirstSetGraph", task.task_id, task.machinery_id),
                   ("FirstSetGraph", "", "foreign-machine"),
                   ("SomeOtherMessage", "", task.machinery_id)]:
        assert protocol.accept_report(*report) is False
        assert protocol.phase == "graph"
    assert protocol.accept_report("FirstSetGraph", "", task.machinery_id) is True
    assert protocol.phase == "parameters"
    assert protocol.pending_tasks == (task,)
    assert protocol.reporting_tasks == ()
    for report in [("FirstSetGraph", "", task.machinery_id),
                   ("TaskGet", "wrong-task", task.machinery_id),
                   ("TaskGet", task.task_id, "wrong-machine")]:
        assert protocol.accept_report(*report) is False
        assert protocol.phase == "parameters"
    assert protocol.accept_report("TaskGet", task.task_id, task.machinery_id) is True
    assert protocol.phase == "state"
    assert protocol.pending_tasks == ()
    assert protocol.reporting_tasks == (task,)
    for _ in range(3):
        assert protocol.accept_report("TaskGet", task.task_id, task.machinery_id) is False
        assert protocol.accept_report("FirstSetGraph", "", task.machinery_id) is False
        assert protocol.phase == "state"
        assert protocol.reporting_tasks == (task,)


@pytest.mark.parametrize("separate_machines", [False, True])
def test_protocol_waits_for_every_input_task_and_distinct_machine(tmp_path, separate_machines):
    from tms_if_input.system_topics_protocol import TopicProtocol

    scenario = multiple_task_scenario(tmp_path, separate_machines)
    first, second = scenario.leveling_tasks
    protocol = TopicProtocol(scenario)
    assert protocol.accept_report("FirstSetGraph", "", first.machinery_id) is True
    assert protocol.accept_report("FirstSetGraph", "", first.machinery_id) is False
    if separate_machines:
        assert protocol.phase == "graph"
        assert protocol.pending_tasks == ()
        assert protocol.accept_report("FirstSetGraph", "", second.machinery_id) is True
    assert protocol.phase == "parameters"
    assert protocol.pending_tasks == (first, second)
    assert protocol.accept_report("TaskGet", second.task_id, second.machinery_id) is True
    assert protocol.phase == "parameters"
    assert protocol.pending_tasks == (first,)
    assert protocol.reporting_tasks == ()
    assert protocol.accept_report("TaskGet", second.task_id, second.machinery_id) is False
    assert protocol.accept_report("TaskGet", first.task_id, "not-from-this-xml") is False
    assert protocol.accept_report("TaskGet", first.task_id, first.machinery_id) is True
    assert protocol.phase == "state"
    assert protocol.pending_tasks == ()
    assert protocol.reporting_tasks == (first, second)


def test_mongo_reader_uses_xml_model_and_rereads_completed_blocks(tmp_path):
    from tms_if_input.system_topics import read_completed_block_names

    task = load_topic_scenario(*write_inputs(tmp_path, *independent_inputs(3))).leveling_tasks[0]

    class ReadOnlyCollection:
        def __init__(self):
            self.document = {"LEVELED_BLOCK": ["block_2"], "RELEACED_BLOCK": ["block_1"]}
            self.calls = []

        def find_one(self, query, projection, *, sort):
            self.calls.append((query, projection, sort))
            return copy.deepcopy(self.document)

    collection = ReadOnlyCollection()
    first = build_block_state(task, read_completed_block_names(collection, task))
    assert first == [False, True, False]
    collection.document["LEVELED_BLOCK"] = ["block_1", "block_3"]
    second = build_block_state(task, read_completed_block_names(collection, task))
    assert second == [True, False, True]
    assert first == [False, True, False]
    assert collection.calls == [({"record_name": "SAMPLE_BLACKBOARD_custom-grader-v42",
                                  "model_name": "custom-grader-v42", "type": "dynamic"},
                                 {"LEVELED_BLOCK": 1}, [("timestamps", -1), ("_id", -1)])] * 2
    assert collection.document["LEVELED_BLOCK"] == ["block_1", "block_3"]


@pytest.mark.parametrize("document", [None, {}, {"RELEACED_BLOCK": ["block_1"]}])
def test_missing_mongo_state_reports_all_blocks_incomplete(tmp_path, document):
    from tms_if_input.system_topics import read_completed_block_names

    task = load_topic_scenario(*write_inputs(tmp_path, *independent_inputs(3))).leveling_tasks[0]

    class ReadOnlyCollection:
        def find_one(self, query, projection, *, sort):
            return document

    assert build_block_state(task, read_completed_block_names(ReadOnlyCollection(), task)) == [False] * 3


def test_source_replacement_cannot_mix_geometry_and_radius(tmp_path, monkeypatch):
    geojson, root = independent_inputs(3)
    geojson_path, xml_path = write_inputs(tmp_path, geojson, root)
    replacement = copy.deepcopy(geojson)
    for feature in replacement["features"]:
        if feature["geometry"]["type"] == "Point":
            feature["geometry"]["coordinates"][0] += 1000
            feature["properties"]["metadata"]["radius"] += 10
        elif feature["geometry"]["type"] == "LineString":
            for point in feature["geometry"]["coordinates"]:
                point[0] += 1000
    original_read = Path.read_text
    reads = []

    def changing_read(path, *args, **kwargs):
        if path == geojson_path:
            reads.append(path)
            return json.dumps(geojson if len(reads) == 1 else replacement)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", changing_read)
    scenario = load_topic_scenario(geojson_path, xml_path)
    assert reads == [geojson_path]
    assert_source_messages(scenario, geojson, root)


def test_newer_static_and_other_machine_records_do_not_replace_dynamic_state(tmp_path):
    from tms_if_input.system_topics import read_completed_block_names

    task = load_topic_scenario(*write_inputs(tmp_path, *independent_inputs(3))).leveling_tasks[0]
    common = {"record_name": task.blackboard_record_name, "model_name": task.model_name}
    records = [
        {**common, "type": "dynamic", "timestamps": 1, "LEVELED_BLOCK": ["block_1"]},
        {**common, "type": "dynamic", "timestamps": 2, "LEVELED_BLOCK": ["block_2"]},
        {**common, "type": "static", "timestamps": 3, "LEVELED_BLOCK": ["block_3"]},
        {**common, "type": "dynamic", "model_name": "another-machine",
         "timestamps": 4, "LEVELED_BLOCK": ["block_3"]},
    ]

    class ReadOnlyCollection:
        def find_one(self, query, projection, *, sort):
            matching = [record for record in records
                        if all(record.get(key) == value for key, value in query.items())]
            newest = max(matching, key=lambda record: record["timestamps"])
            return {key: value for key, value in newest.items() if key in projection}

    states = build_block_state(task, read_completed_block_names(ReadOnlyCollection(), task))
    assert states == [False, True, False]
