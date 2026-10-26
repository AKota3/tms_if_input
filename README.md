# tms_if_input

GeoJSONと施工計画XMLから施工機械のタスク・パラメータを生成し、MongoDBの `rostmsdb.parameter` と `rostmsdb.task` に未登録データを追加します。
追加機能では経路グラフと敷き均しパラメータをROS 2トピックで送り、完了通知を受けるまでMongoDBの放土状態を送信します。

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

掘削位置はXMLの `backhoe_node` 順に `excavate_position_1` ～ `excavate_position_n` として登録します。`model_name` は対象バックホウのモデル名を含む配列、`x, y, z, qx, qy, qz, qw` は `follow_waypoints` 用の1要素配列です。XY座標は対応するGeoJSONノード、Zは0、姿勢は同じブロックのXML `rotation` を使用します。同じ機械に複数の掘削積込タスクがある場合は、既存の命名規則に従って末尾にタスクIDを付けます。

ダンプの敷きならしエリア入口 `dumps_entry_point_leveling_area` は、対象の敷きならしタスクを参照する運搬タスクの生成経路から求めます。敷きならし側の末端ノードとXMLの `connection_node` のいずれかがGeoJSONの `startid` / `endid` で直接接続されていることを確認し、その末端ノードのPoint座標をXYに使用します。接続線の向きや `connection_node` の並び順には依存しません。Zは0、姿勢は従来どおり `block_vector` の逆方向です。運搬タスクがない場合、該当点がない場合、または候補の末端ノードが複数ある場合はエラーになります。既存のMongoDBレコードは引き続き自動更新しないため、登録済み入口点の変更は別途必要です。

**追加トピック機能の起動**

```bash
ros2 launch tms_if_input system_topics.launch.py \
  geojson_path:=261008-kyoto.geojson \
  xml_path:=261008-kyoto.xml
```

受領応答に合わせて、`NodeGroup` → `LevelingTaskParam` → `LevelingStateReport` の順に送信します。`block_state` はXMLのブロック順に、MongoDBの `RELEACED_BLOCK` にあれば `true`、なければ `false` です。XMLのタスクID・機械aliasと一致する `TaskFinish` を受け取ると、そのタスクの状態送信を停止します。複数タスクの場合は未完了のタスクだけ送信を続けます。ノードは `Ctrl+C` で終了します。

**相手システムの受領応答・完了通知を模擬**（起動中に別ターミナルで順に実行）

```bash
source ~/SIP_PROJECTS/ros2_tms_ws/install/setup.bash
ros2 topic pub --once /system/MessageReport machinery_task_msgs/msg/TaskReport \
  "{message_id: 'FirstSetGraph', task_id: '', machine_id: 'machine_4'}"
ros2 topic pub --once /system/MessageReport machinery_task_msgs/msg/TaskReport \
  "{message_id: 'TaskGet', task_id: '2600159710', machine_id: 'machine_4'}"
```

1つ目でグラフの受領、2つ目でタスクパラメータの受領を通知します。状態報告を確認した後、以下で敷き均し完了を通知すると送信が止まります。

```bash
ros2 topic pub --once /system/MessageReport machinery_task_msgs/msg/TaskReport \
  "{message_id: 'TaskFinish', task_id: '2600159710', machine_id: 'machine_4'}"
```

上記IDは `261008-kyoto.xml` の例です。入力を替えた場合は `task_id` を敷き均しタスクのID、`machine_id` を機械aliasに合わせて変更してください。`message_id` は通知の種類を表す `FirstSetGraph` / `TaskGet` / `TaskFinish` です。




榑林へ。MongoDbのrostmsdbデータベースのparameterコレクション直下に以下のデータを追加して。`RELEACED_BLOCK` に記載のブロックがダンプによる放土済みを意味し、状態報告ではこのフィールドを参照します。


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
    "block_1"
  ],
  "RELEACED_BLOCK": ["block_1", "block_2"],
  "LATEST_RELEACED_BLOCK": "block_2",
  "LATEST_LEVELED_BLOCK": "block_1",
  "w_blade": 3.2,
  "waypoints_record_name": [
    "bull_leveling_waypoint_1",
    "bull_leveling_waypoint_2",
    "bull_leveling_waypoint_3"
  ],
  "blade_offset": 2.5,
  "back_offset": 3.5
}

この9ブロックの例では `block_state` は `[true, true, false, false, false, false, false, false, false]` になります。


mongodbを起動するにはsudo systemctl start mongodを実行。その上で、上述の通り実行すると検証可能。**MongoDBへの登録**については、榑林システムと関係ないので飛ばすこと。
