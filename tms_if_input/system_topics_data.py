"""Input-derived messages for the independent system topic interface.

This module never writes to MongoDB and does not change the existing importer.
ROS message classes are deliberately absent so the data can be checked without
a ROS installation or generated interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .model import identifier, number, parse_geojson, parse_plan, record_name
from .parameters import _connection_for_dump


_FLOAT32_MAX = 3.4028234663852886e38


def _float32(value, context: str) -> float:
    result = float(number(value, context))
    if abs(result) > _FLOAT32_MAX:
        raise ValueError(f'{context}: value is outside the float32 range')
    return result


@dataclass(frozen=True)
class NodeData:
    node_id: str = ''
    connect_node: tuple[str, ...] = ()
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    size: float = 0.0

    def to_dict(self) -> dict:
        return {'node_id': self.node_id, 'connect_node': list(self.connect_node),
                'x': self.x, 'y': self.y, 'z': self.z, 'size': self.size}


@dataclass(frozen=True)
class NodeGroupData:
    message_id: str
    node_ids: tuple[NodeData, ...]

    def to_dict(self) -> dict:
        return {'message_id': self.message_id,
                'node_ids': [node.to_dict() for node in self.node_ids]}


@dataclass(frozen=True)
class LevelingTaskData:
    task_id: str
    machinery_id: str
    leveling_height: float
    block_size_x: float
    block_size_y: float
    block_vector_x: float
    block_vector_y: float
    block_vector_z: float
    block_center: tuple[NodeData, ...]
    soil_volume: tuple[float, ...]
    dump_node: NodeGroupData
    bulldozer_node: NodeGroupData
    # These input-derived names are for the state reader, not ROS message fields.
    model_name: str
    blackboard_record_name: str
    block_record_names: tuple[str, ...]

    def to_dict(self) -> dict:
        return {'task_id': self.task_id, 'machinery_id': self.machinery_id,
                'leveling_height': self.leveling_height,
                'block_size_x': self.block_size_x, 'block_size_y': self.block_size_y,
                'block_vector_x': self.block_vector_x,
                'block_vector_y': self.block_vector_y,
                'block_vector_z': self.block_vector_z,
                'block_center': [node.to_dict() for node in self.block_center],
                'soil_volume': list(self.soil_volume),
                'dump_node': self.dump_node.to_dict(),
                'bulldozer_node': self.bulldozer_node.to_dict()}


@dataclass(frozen=True)
class TopicScenario:
    graph: NodeGroupData
    leveling_tasks: tuple[LevelingTaskData, ...]

    def to_dict(self) -> dict:
        return {'graph': self.graph.to_dict(),
                'leveling_tasks': [task.to_dict() for task in self.leveling_tasks]}


def _point_radii(data, graph) -> dict[str, float]:
    # The existing input model intentionally drops metadata; read the radius
    # from the same input without changing that model or inventing a default.
    radii = {}
    for feature in data['features']:
        if feature['geometry'].get('type') != 'Point':
            continue
        props = feature['properties']
        point_id = identifier(props.get('id', feature.get('id')))
        metadata = props.get('metadata')
        if not isinstance(metadata, dict) or 'radius' not in metadata:
            raise ValueError(f'Point {point_id}: metadata.radius is required')
        radius = _float32(metadata['radius'], f'Point {point_id} metadata.radius')
        if radius < 0:
            raise ValueError(f'Point {point_id}: metadata.radius must not be negative')
        radii[point_id] = radius
    if set(radii) != set(graph.points):
        raise ValueError('GeoJSON Point IDs disagree with radius metadata')
    return radii


def load_topic_scenario(geojson_path, xml_path) -> TopicScenario:
    """Extract all graph points and every leveling task from the supplied files.

    IDs and machine aliases remain strings; array order and values come from the
    XML. The block direction is not normalized or converted to a quaternion.
    """
    # A single GeoJSON snapshot supplies both geometry and metadata, even if
    # the source file is replaced while this function is running.
    data = json.loads(Path(geojson_path).read_text(encoding='utf-8-sig'))
    plan = parse_plan(Path(xml_path).read_text(encoding='utf-8-sig'))
    graph = parse_geojson(data)
    for task in plan.tasks.values():
        for name, value in task.parameters.items():
            if name == 'target_node':
                graph.point(value)
            elif name in {'backhoe_node', 'dump_node', 'bulldozer_node',
                          'connection_node', 'main_node', 'sub_node'}:
                for node_id in value:
                    graph.point(node_id)
    radii = _point_radii(data, graph)
    adjacency = {point_id: [] for point_id in graph.points}
    for section in graph.sections:
        for source, destination in ((section.start, section.end),
                                    (section.end, section.start)):
            if destination not in adjacency[source]:
                adjacency[source].append(destination)

    def point_node(node_id, connections=()) -> NodeData:
        point = graph.point(node_id)
        return NodeData(node_id=point.id, connect_node=tuple(connections),
                        x=_float32(point.x, f'Point {point.id} x'),
                        y=_float32(point.y, f'Point {point.id} y'),
                        z=0.0, size=radii[point.id])

    graph_data = NodeGroupData(
        message_id='FirstSetGraph',
        node_ids=tuple(point_node(point_id, adjacency[point_id])
                       for point_id in graph.points))
    tasks = []
    for task in plan.tasks.values():
        if task.name != 'leveling':
            continue
        params = task.parameters
        context = f'Task {task.id}'
        center_nodes = tuple(NodeData(
            x=_float32(center['x'], f'{context} block_center[{index}].x'),
            y=_float32(center['y'], f'{context} block_center[{index}].y'),
            z=_float32(center['z'], f'{context} block_center[{index}].z'))
            for index, center in enumerate(params['block_center']))
        vector = params['block_vector']
        # Validate the direction before passing it to the existing projection
        # algorithm, which identifies the entry column for each dump position.
        direction = {axis: _float32(vector[axis], f'{context} block_vector.{axis}')
                     for axis in 'xyz'}
        connection_ids = tuple(identifier(node_id)
                               for node_id in params['connection_node'])
        connections = [graph.point(node_id).xy() for node_id in connection_ids]
        dump_nodes = []
        for node_id in params['dump_node']:
            point = graph.point(node_id)
            connection = _connection_for_dump(point.xy(), connections, direction, task.id)
            selected = next(index for index, candidate in enumerate(connections)
                            if candidate is connection)
            dump_nodes.append(point_node(node_id, (connection_ids[selected],)))
        bulldozer_ids = tuple(identifier(node_id)
                              for node_id in params['bulldozer_node'])
        bulldozer_nodes = []
        for index, node_id in enumerate(bulldozer_ids):
            neighbors = []
            if index > 0:
                neighbors.append(bulldozer_ids[index - 1])
            if index + 1 < len(bulldozer_ids):
                neighbors.append(bulldozer_ids[index + 1])
            bulldozer_nodes.append(point_node(node_id, neighbors))
        model_name = plan.model(task)
        tasks.append(LevelingTaskData(
            task_id=task.id, machinery_id=task.machine,
            leveling_height=_float32(params['leveling_height'], f'{context} leveling_height'),
            block_size_x=_float32(params['block_size']['x'], f'{context} block_size.x'),
            block_size_y=_float32(params['block_size']['y'], f'{context} block_size.y'),
            block_vector_x=direction['x'], block_vector_y=direction['y'],
            block_vector_z=direction['z'], block_center=center_nodes,
            soil_volume=tuple(_float32(volume, f'{context} soil_volume[{index}]')
                              for index, volume in enumerate(params['soil_volume'])),
            dump_node=NodeGroupData('LevelingTaskParamDumpNode', tuple(dump_nodes)),
            bulldozer_node=NodeGroupData('LevelingTaskParamBulldozerNode',
                                         tuple(bulldozer_nodes)),
            model_name=model_name, blackboard_record_name=f'SAMPLE_BLACKBOARD_{model_name}',
            block_record_names=tuple(record_name(plan, task, f'block_{index}')
                                     for index in range(1, len(center_nodes) + 1))))
    if not tasks:
        raise ValueError('XML has no leveling tasks for the system topic interface')
    return TopicScenario(graph=graph_data, leveling_tasks=tuple(tasks))


def build_block_state(task: LevelingTaskData, completed_block_names) -> list[bool]:
    """Map MongoDB LEVELED_BLOCK records to the input's ordered block centers.

    Names from other tasks are ignored. Qualified names produced by the existing
    importer keep multiple leveling areas separate, even on the same machine.
    """
    if not isinstance(completed_block_names, list) or not all(
            isinstance(name, str) for name in completed_block_names):
        raise ValueError('LEVELED_BLOCK must be an array of record name strings')
    completed = set(completed_block_names)
    return [record in completed for record in task.block_record_names]
