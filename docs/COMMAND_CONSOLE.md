# LPB 控制台网页与演示脚本

## 控制台网页

启动流程：

1. 在 Isaac Sim 中启用 `LPB Config Loader`。
2. 填写 `Config JSON`，点击 `LOAD`。
3. 点击 `START API`。
4. 打开：

```text
http://localhost:60124/console
```

插件窗口里的 `OPEN CONSOLE` 会在服务器未启动时先启动 API，然后打开控制台网页。

网页控制台当前提供：

- 查询 `/state`
- 清空场景：`POST /reset`
- 传送带放入包裹
- 机械臂从候选池抓包裹并释放到坐标
- 查询指定货架包裹
- 拣货机器人从货架拣包裹到传送带端
- 搬运机器人搬运托盘
- 启动/停止演示脚本
- 查看服务器内部日志
- 原始 HTTP 请求输入框

注意：动作请求会进入仿真任务队列。API 服务启动后会注册 runtime pump，并在任务入队时自动播放 timeline，队列任务会在 Isaac 的仿真步回调中推进。

## Reset 行为

插件窗口的 `RESET` 现在会真正清空当前场景：

- 删除 `/World` 下的场景对象
- 清空候选池
- 清空 API 任务队列
- 清空当前加载配置索引
- 停止正在播放的演示脚本

为了兼容 Isaac Sim，`/World` 和 `PhysicsScene` 根节点会保留。

网页控制台里的 `清空场景` 会通过 `POST /reset` 入队，随后由 runtime pump 在主仿真线程执行。

## 调试日志

网页控制台右侧有 `服务器日志` 面板，可以刷新、自动刷新或清空日志。

HTTP 接口：

```http
GET /logs?limit=200
POST /logs/clear
GET /runtime
```

日志会记录：

- API server 启动/停止
- runtime pump 注册/停止
- 任务入队、开始、完成、失败
- 当前执行的任务 step
- 传送带生成包裹结果
- reset 和脚本启动/停止

## 演示脚本路径

默认脚本：

```text
D:\workstation\isaac-sim-standalone-5.1.0-windows-x86_64\extsUser\LPB_config_server\config\default_demo_script.json
```

插件窗口 `RUN` 按钮上方的 `Script JSON` 默认指向该文件。点击 `RUN` 后会读取这个 JSON，并按固定节奏把动作命令加入仿真队列。

## 脚本 JSON 格式

推荐使用 `command + params`：

```json
{
  "name": "my_demo",
  "default_delay_seconds": 0.5,
  "steps": [
    {
      "label": "Put packages on conveyor",
      "command": "conveyor_packages",
      "params": {
        "conveyor_name": "InboundConveyor",
        "count": 2,
        "package_prefix": "DemoBox",
        "dimensions": [0.3, 0.3, 0.5],
        "spacing": 0.45,
        "at": "end"
      }
    },
    {
      "label": "Arm pick candidate",
      "wait_for_idle": true,
      "command": "arm_pick_place",
      "params": {
        "candidate_index": 1,
        "target": [11.2, 8.0, 0.8],
        "arm_name": "pallet_arm1"
      }
    }
  ]
}
```

字段说明：

- `default_delay_seconds`：每一步默认延迟秒数。
- `steps`：动作步骤列表。
- `label`：日志显示名，可选。
- `delay` / `delay_seconds`：覆盖单步延迟。
- `wait_for_idle`：为 `true` 时，等当前 API 任务队列清空后再发送该步。
- `command`：动作类型。
- `params`：动作参数。

支持的 `command`：

- `conveyor_packages`
- `arm_pick_place`
- `shuttle_pick_to_conveyor`
- `transporter_move_pallet`
- `reset_scene`

也可以写成 HTTP 风格：

```json
{
  "steps": [
    {
      "method": "POST",
      "path": "/conveyors/InboundConveyor/packages",
      "body": {
        "count": 2,
        "package_prefix": "DemoBox"
      }
    }
  ]
}
```
