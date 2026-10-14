追加トピックインタフェース
========================

GeoJSON・施工計画XMLからグラフと敷き均しパラメータを送信する独立ノードです。
MongoDBの完了状態を読み取り、敷き均し状態を継続送信します。

ワークスペース直下で追加メッセージとノードをビルドします。
メッセージパッケージを既存パッケージ内に追加したため、探索先を明示します。

.. code-block:: bash

   colcon build \
     --base-paths src/tms_if_input src/tms_if_input/additional_interfaces/machinery_task_msgs \
     --packages-select machinery_task_msgs tms_if_input
   source install/setup.bash

.. code-block:: bash

   ros2 launch tms_if_input system_topics.launch.py \
     geojson_path:=261008-kyoto.geojson \
     xml_path:=261008-kyoto.xml

ファイル名は ``json_samples`` 直下から読み込みます。絶対パスも指定できます。
この追加ノードは ``Ctrl+C`` で終了します。

通信は添付定義と同じ ``machinery_task_msgs`` のメッセージ型を使います。

* ``/system/NodeGroup``: 全Pointと接続を送信。``message_id`` は ``FirstSetGraph``。
* ``/system/LevelingTaskParam``: XMLの敷き均しタスクごとのパラメータを送信。
* ``/system/LevelingStateReport``: XMLのブロック順に完了・未完了の配列を送信。
* ``/system/MessageReport``: ``TaskReport`` 型で相手の受領応答を受信。

``FirstSetGraph``、空の ``task_id``、XMLの機械aliasと一致する ``machine_id``
を受け取るとグラフ送信が止まり、タスクパラメータ送信に進みます。
次に ``TaskGet`` とXMLのタスクID・機械aliasが一致する応答を受け取ると、
パラメータ送信が止まり、状態報告の継続送信に進みます。
複数の機械・タスクがある場合は対応する全受領応答を待ちます。

状態は ``rostmsdb.parameter`` の ``SAMPLE_BLACKBOARD_<XMLの機械type>`` にある
``type=dynamic`` の ``LEVELED_BLOCK`` を毎回読み取り、
``block_1`` をXMLの先頭ブロックに対応させます。
複数の敷き均しタスクでは既存のブロック命名規則に合わせてタスクIDの接尾辞を使います。
状態取得ができた場合は未完了ブロックを ``false`` として送信し、値の変化は次回の取得で反映します。
取得エラー時はログに原因を出して再試行します。

再送間隔は ``publish_period:=0.5``、状態の取得・送信間隔は
``state_poll_period:=0.1`` が既定値です。MongoDB接続先は
``mongo_uri:=mongodb://localhost:27017``、``mongo_db:=rostmsdb`` で変更できます。

仕様書末尾の第3段階は、確認された指示に従って ``LevelingStateReport`` を送信します。
全グラフの隣接ノードは実際のエッジから抽出し、仕様書の自己参照を含む例の誤記は取り込みません。
