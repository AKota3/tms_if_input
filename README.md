# tms_if_input

GeoJSONと施工計画XMLから施工機械のタスク・パラメータを生成し、MongoDBの `rostmsdb.parameter` と `rostmsdb.task` に未登録データを追加します。
追加機能では経路グラフと敷き均しパラメータをROS 2トピックで送り、MongoDBの敷き均し完了状態を継続送信します。

**ビルド**（入力ファイルやコードを変更した際も再実行）

```bash
cd ~/SIP_PROJECTS/ros2_tms_ws
source /opt/ros/humble/setup.bash
colcon build \
  --base-paths src/tms_if_input src/tms_if_input/additional_interfaces/machinery_task_msgs \
  --packages-select machinery_task_msgs tms_if_input
source install/setup.bash
```

`geojson_path` と `xml_path` には、`json_samples` 直下のファイル名を指定します。MongoDB（既定: `localhost:27017`）を起動してから実行してください。

**MongoDBへの登録**

```bash
ros2 launch tms_if_input tms_if_input.launch.py \
  geojson_path:=261008-kyoto.geojson \
  xml_path:=261008-kyoto.xml
```

登録後は自動終了します。既存データは削除・更新しません。ファイル指定を省略した場合の既定値は `261004-kyoto.geojson` / `261004-kyoto.xml` です。

**追加トピック機能の起動**

```bash
ros2 launch tms_if_input system_topics.launch.py \
  geojson_path:=261008-kyoto.geojson \
  xml_path:=261008-kyoto.xml
```

受領応答に合わせて、`NodeGroup` → `LevelingTaskParam` → `LevelingStateReport` の順に送信します。MongoDBから状態を読み取り、`Ctrl+C` で終了します。

**相手システムの受領応答を模擬**（起動中に別ターミナルで順に実行）

```bash
source ~/SIP_PROJECTS/ros2_tms_ws/install/setup.bash
ros2 topic pub --once /system/MessageReport machinery_task_msgs/msg/TaskReport \
  "{message_id: 'FirstSetGraph', task_id: '', machine_id: 'machine_4'}"
ros2 topic pub --once /system/MessageReport machinery_task_msgs/msg/TaskReport \
  "{message_id: 'TaskGet', task_id: '2600159710', machine_id: 'machine_4'}"
```

1つ目でグラフの受領、2つ目でタスクパラメータの受領を通知します。上記IDは `261008-kyoto.xml` の例です。入力を替えた場合は敷き均しタスクのIDと機械aliasに合わせて変更してください。




榑林へ。MongoDbのrostmsbdデータベースのparameterコレクション直下に以下のデータを追加して。"LEVELED_BLOCKに記載のブロックが敷き均し完了を意味します。


/**
* Paste one or more documents here
*/
{
  "model_name": "d37pxi",
  "type": "dynamic",
  "record_name": "SAMPLE_BLACKBOARD_d37pxi",
  "timestamps": {
    "$date": "2026-10-12T16:28:11.571Z"
  },
  "LEVELED_BLOCK": [
    "block_1",
    "block_2",
    "block_3",
    "block_4",
    "block_5",
    "block_6",
    "block_7",
    "block_8",
    "block_9"
  ],
  "RELEACED_BLOCK": [],
  "LATEST_RELEACED_BLOCK": "block_5",
  "LATEST_LEVELED_BLOCK": "block_9",
  "w_blade": 3.2,
  "waypoints_record_name": [
    "bull_leveling_waypoint_1",
    "bull_leveling_waypoint_2",
    "bull_leveling_waypoint_3"
  ],
  "blade_offset": 2.5,
  "back_offset": 3.5
}


mongodbを起動するにはsudo systemctl start mongodを実行。その上で、上述の通り実行すると検証可能。**MongoDBへの登録**については、榑林システムと関係ないので飛ばすこと。
