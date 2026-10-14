"""Publish the additional system topics without running the MongoDB importer."""
from __future__ import annotations

from pathlib import Path
import signal
import sys
from threading import Lock, RLock

from .system_topics_data import build_block_state, load_topic_scenario
from .system_topics_protocol import TopicProtocol


def _node_message(data, types):
    message = types.Node()
    message.node_id = data.node_id
    message.connect_node = list(data.connect_node)
    for field in ("x", "y", "z", "size"):
        setattr(message, field, float(getattr(data, field)))
    return message


def _group_message(data, types):
    message = types.NodeGroup()
    message.message_id = data.message_id
    message.node_ids = [_node_message(node, types) for node in data.node_ids]
    return message


def _task_message(data, types):
    message = types.LevelingTaskParam()
    message.task_id = data.task_id
    message.machinery_id = data.machinery_id
    for field in ("leveling_height", "block_size_x", "block_size_y",
                  "block_vector_x", "block_vector_y", "block_vector_z"):
        setattr(message, field, float(getattr(data, field)))
    message.block_center = [_node_message(node, types) for node in data.block_center]
    message.soil_volume = [float(volume) for volume in data.soil_volume]
    message.dump_node = _group_message(data.dump_node, types)
    message.bulldozer_node = _group_message(data.bulldozer_node, types)
    return message


def read_completed_block_names(collection, task):
    """Read the latest machine blackboard without inserting or updating data."""
    document = collection.find_one(
        {"record_name": task.blackboard_record_name, "model_name": task.model_name,
         "type": "dynamic"},
        {"LEVELED_BLOCK": 1}, sort=[("timestamps", -1), ("_id", -1)])
    return [] if document is None else document.get("LEVELED_BLOCK", [])


def main(args=None) -> int:
    import rclpy
    from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
    from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
    from rclpy.node import Node as RosNode
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from ament_index_python.packages import get_package_share_directory
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    from machinery_task_msgs import msg as types

    class SystemTopics(RosNode):
        def __init__(self):
            super().__init__("system_topics_bridge")
            self._client = None
            try:
                defaults = {
                    "geojson_path": "261008-kyoto.geojson", "xml_path": "261008-kyoto.xml",
                    "mongo_uri": "mongodb://localhost:27017", "mongo_db": "rostmsdb",
                    "mongo_timeout_ms": 1000, "publish_period": 0.5, "state_poll_period": 0.1,
                }
                for name, default in defaults.items():
                    self.declare_parameter(name, default)
                parameters = {name: self.get_parameter(name).value for name in defaults}
                for name in ("mongo_timeout_ms", "publish_period", "state_poll_period"):
                    if parameters[name] <= 0:
                        raise ValueError(f"{name} must be positive")
                samples = Path(get_package_share_directory("tms_if_input"))/"json_samples"

                def input_path(name):
                    value = Path(parameters[name])
                    return value if value.is_absolute() else samples/value

                self._scenario = load_topic_scenario(input_path("geojson_path"), input_path("xml_path"))
                self._protocol = TopicProtocol(self._scenario)
                self._lock = RLock()
                self._state_lock = Lock()
                self._last_error = None
                qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                                 durability=DurabilityPolicy.VOLATILE)
                self._graph_publisher = self.create_publisher(types.NodeGroup, "/system/NodeGroup", qos)
                self._task_publisher = self.create_publisher(types.LevelingTaskParam, "/system/LevelingTaskParam", qos)
                self._state_publisher = self.create_publisher(types.LevelingStateReport, "/system/LevelingStateReport", qos)
                self._graph_message = _group_message(self._scenario.graph, types)
                self._task_messages = {(task.task_id, task.machinery_id): _task_message(task, types)
                                       for task in self._scenario.leveling_tasks}
                self._client = MongoClient(
                    parameters["mongo_uri"], serverSelectionTimeoutMS=parameters["mongo_timeout_ms"],
                    connectTimeoutMS=parameters["mongo_timeout_ms"], socketTimeoutMS=parameters["mongo_timeout_ms"])
                self._collection = self._client[parameters["mongo_db"]]["parameter"]
                # Separate callback groups keep acknowledgements responsive during DB reads.
                self._transfer_group = MutuallyExclusiveCallbackGroup()
                self._state_group = MutuallyExclusiveCallbackGroup()
                self._ack_group = MutuallyExclusiveCallbackGroup()
                self._subscription = self.create_subscription(
                    types.TaskReport, "/system/MessageReport", self._on_report, qos,
                    callback_group=self._ack_group)
                self._transfer_timer = self.create_timer(
                    parameters["publish_period"], self._publish_transfer,
                    callback_group=self._transfer_group)
                self._state_timer = self.create_timer(
                    parameters["state_poll_period"], self._publish_states,
                    callback_group=self._state_group)
                self.get_logger().info(
                    f"Loaded {len(self._scenario.graph.node_ids)} graph nodes and "
                    f"{len(self._scenario.leveling_tasks)} leveling tasks; waiting for FirstSetGraph acknowledgements")
                self._publish_transfer()
            except Exception:
                if self._client is not None:
                    self._client.close()
                self.destroy_node()
                raise

        def _publish_transfer(self):
            with self._lock:
                if self._protocol.phase == "graph":
                    self._graph_publisher.publish(self._graph_message)
                elif self._protocol.phase == "parameters":
                    for task in self._protocol.pending_tasks:
                        self._task_publisher.publish(self._task_messages[(task.task_id, task.machinery_id)])

        def _on_report(self, report):
            with self._lock:
                previous = self._protocol.phase
                accepted = self._protocol.accept_report(report.message_id, report.task_id, report.machine_id)
                phase = self._protocol.phase
            if accepted and phase != previous:
                self.get_logger().info(f"Acknowledgements received; phase: {phase}")
                self._publish_transfer()
                if phase == "state":
                    self._publish_states()

        def _publish_states(self):
            with self._lock:
                tasks = self._protocol.reporting_tasks
            if not tasks or not self._state_lock.acquire(blocking=False):
                return
            try:
                completed_by_machine = {}
                for task in tasks:
                    if not rclpy.ok():
                        return
                    key = (task.blackboard_record_name, task.model_name)
                    try:
                        if key not in completed_by_machine:
                            completed_by_machine[key] = read_completed_block_names(self._collection, task)
                        states = build_block_state(task, completed_by_machine[key])
                    except (PyMongoError, ValueError) as exc:
                        error = str(exc)
                        if error != self._last_error:
                            self.get_logger().error(f"Cannot read leveling state; retrying: {error}")
                            self._last_error = error
                        continue
                    self._last_error = None
                    message = types.LevelingStateReport()
                    message.task_id = task.task_id
                    message.machinery_id = task.machinery_id
                    message.block_state = states
                    self._state_publisher.publish(message)
            finally:
                self._state_lock.release()

        def close_database(self):
            if self._client is not None:
                self._client.close()

    node = executor = None
    try:
        rclpy.init(args=args)
        node = SystemTopics()
        executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        if node is not None:
            node.get_logger().error(str(exc))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    finally:
        # launch forwards SIGINT after the terminal has already sent it to the
        # process group. Let cleanup finish when that second signal arrives.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if executor is not None:
                executor.shutdown()
            if node is not None:
                node.close_database()
                node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        finally:
            # The entry point is exiting. Restoring SIGINT here can interrupt
            # Python's remaining teardown after the node has already closed.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
