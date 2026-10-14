"""Start only the additional topic interface using the existing Python package."""
import json
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration


def _start(context):
    names = ("geojson_path", "xml_path", "mongo_uri", "mongo_db", "mongo_timeout_ms",
             "publish_period", "state_poll_period")
    command = [sys.executable, "-m", "tms_if_input.system_topics", "--ros-args"]
    for name in names:
        value = LaunchConfiguration(name).perform(context)
        if name in ("geojson_path", "xml_path", "mongo_uri", "mongo_db"):
            # Quote strings for the ROS YAML parameter parser, including numeric IDs.
            value = json.dumps(value)
        command.extend(["-p", f"{name}:={value}"])
    return [ExecuteProcess(cmd=command, name="system_topics_bridge", output="screen")]


def generate_launch_description():
    defaults = {
        "geojson_path": "261008-kyoto.geojson", "xml_path": "261008-kyoto.xml",
        "mongo_uri": "mongodb://localhost:27017", "mongo_db": "rostmsdb",
        "mongo_timeout_ms": "1000", "publish_period": "0.5", "state_poll_period": "0.1",
    }
    return LaunchDescription([
        DeclareLaunchArgument(name, default_value=value) for name, value in defaults.items()
    ] + [OpaqueFunction(function=_start)])
