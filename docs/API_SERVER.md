# LPB 仿真 API 服务器说明

本文档说明 `LPB Config Loader` 插件当前暴露的最小 HTTP API。接口用于在 Isaac Sim 仿真运行过程中动态下发简单动作指令。

## 启动流程

1. 在 Isaac Sim 中启用 `LPB Config Loader` 扩展。
2. 在窗口的 `Config JSON` 输入配置文件路径，点击 `LOAD` 加载场景。
3. 点击 `START API` 启动接口服务器。
4. 通过网页控制台或 HTTP API 下发命令。API 服务会注册仿真步回调并自动播放 timeline，以便队列任务逐帧推进。

默认服务地址：

```text
http://localhost:60124
```

服务器实现只使用 Python 标准库，不依赖 `fastapi` 或 `uvicorn`。`GET /docs` 返回简单接口索引，不是 OpenAPI 页面。

## 动态交互机制

可以在仿真运行过程中动态调用 HTTP 接口。为了避免在 HTTP 线程直接修改 USD/物理对象，当前实现采用任务队列：

- HTTP 请求线程只做参数校验和任务入队。
- 真实仿真动作在 `TestEnv.run()` 中逐帧执行，由 API 服务启动时注册的 runtime pump 驱动。
- 查询类接口会立即返回当前内存索引状态。
- 动作类接口返回 `task_id`，客户端通过 `GET /tasks/{task_id}` 查询进度。

任务状态：

```text
queued -> running -> done
queued -> running -> failed
```

## 基础查询

### 健康检查

```http
GET /health
```

返回示例：

```json
{
  "ok": true,
  "loaded_config_path": "D:/.../config/default_scene.json",
  "candidate_count": 3
}
```

### 当前状态

```http
GET /state
```

返回当前配置路径、货架数量、传送带名称、托盘名称、候选池、当前任务、排队任务、API server 状态和 runtime pump 状态。

### Runtime 状态

```http
GET /runtime
```

返回 runtime pump 是否已注册、timeline 是否正在播放。

也可以手动启动 runtime pump：

```http
POST /runtime/start
```

### 服务器日志

```http
GET /logs
```

可选查询参数：

- `limit`：返回最近多少条，默认 200，最大 1000。
- `since`：只返回 `seq` 大于该值的日志。
- `level`：按日志等级过滤，例如 `INFO`、`DEBUG`、`ERROR`。

清空日志：

```http
POST /logs/clear
```

### 查询任务

```http
GET /tasks/{task_id}
```

返回示例：

```json
{
  "task_id": "1a2b3c4d5e6f",
  "kind": "conveyor_packages",
  "status": "done",
  "current_step": 1,
  "total_steps": 1,
  "metadata": {
    "conveyor_name": "InboundConveyor",
    "count": 3
  },
  "error": null
}
```

## 1. 机械臂从候选池抓包裹并释放到坐标

```http
POST /arm/pick_place
```

请求体：

```json
{
  "candidate_index": 1,
  "target": [12, 8, 0.8],
  "arm_name": "pallet_arm1"
}
```

也可以直接指定包裹名：

```json
{
  "package_name": "InboundBox_1",
  "target": [12, 8, 0.8],
  "arm_name": "pallet_arm1"
}
```

字段说明：

- `candidate_index`：候选池中的第几个包裹，1 基索引。
- `package_name`：场景对象名。与 `candidate_index` 二选一。
- `target`：世界坐标 `[x, y, z]`，当前按放置点坐标传给机械臂控制器。
- `arm_name`：可选，不填则使用配置中的第一个机械臂。

## 2. 传送带按顺序放入 N 个包裹并进入候选池

```http
POST /conveyors/{conveyor_name}/packages
```

请求体：

```json
{
  "count": 3,
  "package_prefix": "InboundBox",
  "dimensions": [0.3, 0.3, 0.5],
  "spacing": 0.45,
  "at": "end"
}
```

字段说明：

- `conveyor_name`：传送带配置名，例如 `InboundConveyor`。也可以使用具体段名，例如 `InboundConveyor_1`。
- `count` / `n`：新增包裹数量。
- `package_prefix`：包裹名前缀，可选。
- `dimensions`：包裹缩放尺寸 `[length, width, height]`。
- `spacing`：连续包裹间距。
- `at`：`"start"` 或 `"end"`，表示从传送带起点或终点侧生成。

新增包裹会追加到候选池，可用：

```http
GET /candidate_pool
```

## 3. 查询第 x 个货架有哪些包裹

```http
GET /shelves/{shelf_index}/packages
```

示例：

```http
GET /shelves/1/packages
```

`shelf_index` 是 1 基索引，顺序来自 JSON 展开后的货架列表。

返回示例：

```json
{
  "shelf_index": 1,
  "shelf_name": "Shelf_1",
  "packages": [
    {
      "type": "box",
      "name": "Box_Shelf_1_L1_M_1",
      "dimensions": { "length": 0.3, "width": 0.3, "height": 0.5 },
      "position": { "x": 20.5, "y": 8.0, "z": 1.2 },
      "rotation": 90,
      "on_shelf": true,
      "shelf_name": "Shelf_1"
    }
  ]
}
```

## 4. 拣货机器人从货架拣选包裹并送到传送带端

```http
POST /shuttle/pick_to_conveyor
```

按包裹名拣选：

```json
{
  "shelf_index": 1,
  "package_names": ["Box_Shelf_1_L1_M_1", "Box_Shelf_1_L2_S_2"],
  "conveyor_name": "InboundConveyor",
  "shuttle_name": "shuttle1",
  "drop_spacing": 0.45
}
```

按坐标附近的包裹拣选：

```json
{
  "shelf_index": 1,
  "package_positions": [[20.5, 8.0, 1.2], [21.1, 8.0, 2.5]],
  "conveyor_name": "InboundConveyor"
}
```

字段说明：

- `shelf_index`：货架 1 基索引。
- `package_names`：要拣选的包裹名列表。
- `package_positions`：目标坐标列表，系统会在该货架上找最近包裹。与 `package_names` 二选一。
- `conveyor_name`：目标传送带名。
- `shuttle_name`：可选，不填则使用第一个拣货机器人。
- `drop_spacing`：多个包裹放到传送带端时的间隔。

完成后，被拣选的包裹会从对应货架索引中移除，并进入候选池，状态为 `on_conveyor`。

## 5. 搬运机器人托起托盘并运送到世界坐标

```http
POST /transporter/move_pallet
```

请求体：

```json
{
  "pallet_index": 1,
  "target": [15, 5, 0],
  "transporter_name": "transporter1"
}
```

也可以按托盘名指定：

```json
{
  "pallet_name": "Pallet_1",
  "target_name": "PalletDropZone_1",
  "transporter_name": "transporter1"
}
```

字段说明：

- `pallet_index`：托盘 1 基索引。
- `pallet_name`：托盘对象名。与 `pallet_index` 二选一。
- `target`：世界坐标 `[x, y]` 或 `[x, y, z]`。
- `target_name`：可选，引用场景 JSON 中 `pallet_transport_targets[].name`。
- `target_index`：可选，引用场景 JSON 中第 N 个托盘搬运目标区，1 基索引。
- `transporter_name`：可选，不填则使用第一个搬运机器人。

## PowerShell 调用示例

新增 3 个传送带包裹：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:60124/conveyors/InboundConveyor/packages" `
  -ContentType "application/json" `
  -Body '{"count":3,"package_prefix":"InboundBox","dimensions":[0.3,0.3,0.5]}'
```

查询候选池：

```powershell
Invoke-RestMethod "http://localhost:60124/candidate_pool"
```

查询任务：

```powershell
Invoke-RestMethod "http://localhost:60124/tasks/<task_id>"
```

## 固定顺序测试 Client

插件目录下提供了一个简单测试客户端：

```text
client/api_sequence_client.py
```

默认顺序：

1. `GET /health`
2. `GET /state`
3. `POST /conveyors/InboundConveyor/packages`
4. `GET /candidate_pool`
5. `POST /arm/pick_place`
6. `GET /shelves/1/packages`
7. `POST /shuttle/pick_to_conveyor`
8. `POST /transporter/move_pallet`
9. `GET /state`

只测试接口能否接收并入队：

```powershell
python D:\workstation\isaac-sim-standalone-5.1.0-windows-x86_64\extsUser\LPB_config_server\client\api_sequence_client.py
```

如果 Isaac Sim 已经加载场景、启动 API，并且点击了 `RUN`，可以加 `--wait` 等待每个任务完成：

```powershell
python D:\workstation\isaac-sim-standalone-5.1.0-windows-x86_64\extsUser\LPB_config_server\client\api_sequence_client.py --wait --task-timeout 180
```

常用参数：

```powershell
python D:\workstation\isaac-sim-standalone-5.1.0-windows-x86_64\extsUser\LPB_config_server\client\api_sequence_client.py `
  --base-url http://localhost:60124 `
  --conveyor-name InboundConveyor `
  --arm-name pallet_arm1 `
  --shuttle-name shuttle1 `
  --transporter-name transporter1 `
  --shelf-index 1 `
  --pallet-index 1 `
  --arm-target 12,8,0.8 `
  --transporter-target 15,5,0
```

## 当前限制

- 这是第一版低层接口，任务没有全局路径规划和避障，只按已有机器人类的基础动作执行。
- 动作任务需要时间轴处于 `RUN` 状态；否则任务会保持 `queued` 或停在当前步骤。
- 查询接口主要读取 JSON 展开后的内存索引；如果场景中物体被外部脚本直接移动，索引不会自动反扫 USD。
- 传送带生成包裹目前按传送带首段/末段中心附近排布，后续可再升级成沿完整线段连续投放。
