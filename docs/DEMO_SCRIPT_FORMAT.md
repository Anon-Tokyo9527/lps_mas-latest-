# 演示脚本 JSON 格式说明

本文档专门说明：

`D:\Works\lpb_mas\LPB_config_server\config\default_demo_script.json`

这个文件是 `LPB Config Loader` 插件里 `RUN` 按钮默认读取的固定流程脚本。它的作用不是直接驱动底层物理，而是按顺序把一批 API 命令送入仿真任务队列，用来演示一整套已经编排好的流程。

---

## 1. 这个脚本在系统里是怎么工作的

当前实现里，演示脚本的执行链路是：

1. 插件窗口在 `Script JSON` 输入框里拿到一个脚本路径。
2. 点击 `RUN`，或调用 `POST /scripts/start`。
3. 系统读取该 JSON 文件。
4. 按 `steps` 从前到后逐步调度。
5. 每个 step 并不是立刻直接改场景，而是转换成一个 API 任务并入队。
6. 这些队列任务再由 runtime pump 在 Isaac Sim 仿真步里逐步推进。

所以要区分两层：

- 脚本层：负责“什么时候发什么命令”
- API 任务层：负责“这条命令在仿真里如何一步步执行”

---

## 2. 顶层 JSON 结构

当前推荐写法如下：

```json
{
  "name": "large_pick_conveyor_pallet_transport_demo",
  "description": "Two-shelf visual flow: shuttle pick queue -> long conveyor -> end slot -> palletizing arm -> long pallet transport.",
  "default_delay_seconds": 0.25,
  "steps": [
    {
      "label": "Step 1 - pick two packages from shelf 1 and place them on conveyor start",
      "command": "shuttle_pick_to_conveyor",
      "params": {
        "shelf_index": 1,
        "package_names": ["ShelfPkg_1", "ShelfPkg_2"],
        "conveyor_name": "DemoConveyor",
        "shuttle_name": "shuttle1",
        "drop_spacing": 0.45
      }
    }
  ]
}
```

顶层字段说明：

- `name`
  - 脚本名。
  - 主要用于标识和阅读，不参与调度逻辑。

- `description`
  - 脚本说明。
  - 主要用于人类阅读，不参与调度逻辑。

- `default_delay_seconds`
  - 整个脚本的默认步间隔，单位秒。
  - 如果某个 step 没写自己的 `delay` 或 `delay_seconds`，就用这里的值。

- `default_delay`
  - 是 `default_delay_seconds` 的别名。
  - 当前代码同时支持这两个名字。

- `steps`
  - 必填。
  - 必须是一个非空数组。
  - 数组顺序就是脚本调度顺序。

---

## 3. `steps` 是怎么编排的

`steps` 是一个按顺序执行的步骤列表。系统会从 `steps[0]` 开始，依次把每一步送入 API 任务队列。

最重要的几个调度规则如下。

### 3.1 顺序

step 永远按数组顺序处理，不会乱序。

### 3.2 延迟

每个 step 最终都会得到一个内部延迟值 `_delay`，来源如下：

1. 先看当前 step 有没有 `delay`
2. 再看当前 step 有没有 `delay_seconds`
3. 如果都没有，就回退到顶层 `default_delay` 或 `default_delay_seconds`

也就是说，单步延迟的优先级高于全局默认延迟。

### 3.3 `wait_for_idle`

如果某一步写了：

```json
{
  "wait_for_idle": true
}
```

那么系统在发送这一步之前，会等待当前 API 任务队列清空。

它适合用于这些场景：

- 等拣货机器人把上一批包裹全部放完，再开始下一批
- 等传送带流程基本结束，再让机械臂开始从候选池取包裹
- 等机械臂码放完成，再让搬运机器人来搬托盘

注意这里的语义是“等待任务队列空闲后再提交下一步”，不是“暂停物理世界”。

### 3.4 脚本完成状态

脚本调度状态大致有这些值：

- `idle`：还没启动
- `running`：正在按步提交命令
- `submitted`：所有 step 都已经提交完，但队列里可能还有任务没跑完
- `done`：所有 step 都提交完，而且 API 队列也空了
- `stopped`：手动停止
- `failed`：某一步提交时报错

---

## 4. 单个 step 的通用字段

无论使用哪种写法，一个 step 常见都会包含这些字段：

```json
{
  "label": "Step 1 - ...",
  "wait_for_idle": true,
  "delay_seconds": 2.0,
  "command": "...",
  "params": {}
}
```

字段说明：

- `label`
  - 可选。
  - 用于日志显示。
  - 不写时会退回 `name`，再不写则自动生成 `step_序号`。

- `name`
  - 可选。
  - 可作为 `label` 的替代显示名。

- `wait_for_idle`
  - 可选，布尔值。
  - 为 `true` 时，等当前 API 队列清空后再提交本步。

- `delay`
  - 可选，数字，单位秒。
  - 当前 step 的延迟覆盖项。

- `delay_seconds`
  - 可选，数字，单位秒。
  - 与 `delay` 等价，也是当前 step 的延迟覆盖项。

---

## 5. 当前支持的两种 step 写法

当前代码支持两种写法：

1. `command + params` 语义写法
2. `method + path + body` HTTP 风格写法

推荐优先使用第一种，因为更短、更稳，也更贴合当前 demo 的用途。

---

## 6. 写法一：`command + params`

这是最常用的写法：

```json
{
  "label": "Pick shelf packages",
  "command": "shuttle_pick_to_conveyor",
  "params": {
    "shelf_index": 1,
    "package_names": ["ShelfPkg_1", "ShelfPkg_2"],
    "conveyor_name": "DemoConveyor",
    "shuttle_name": "shuttle1"
  }
}
```

其中：

- `command` 表示要调用的动作类型
- `params` 表示该动作的参数

当前支持的 `command` 如下。

### 6.1 `conveyor_packages`

别名：

- `spawn_conveyor_packages`

作用：

- 按顺序在传送带起点或终点侧生成一批包裹
- 包裹会被加入对应的传送带运输流程

示例：

```json
{
  "label": "Spawn conveyor packages",
  "command": "conveyor_packages",
  "params": {
    "conveyor_name": "DemoConveyor",
    "count": 3,
    "package_prefix": "DemoBox",
    "dimensions": [0.3, 0.3, 0.5],
    "spacing": 0.45,
    "at": "start",
    "interval_seconds": 0.35
  }
}
```

参数说明：

- `conveyor_name`
  - 必填。
  - 目标传送带名。

- `count`
  - 可选，默认 `1`。
  - 要生成的包裹数量。

- `package_prefix`
  - 可选。
  - 包裹名前缀。
  - 实际对象名会变成类似 `DemoBox_1`、`DemoBox_2`。

- `dimensions`
  - 可选，默认 `[0.3, 0.3, 0.5]`。
  - 包裹尺寸。

- `spacing`
  - 可选，默认 `0.45`。
  - 同批包裹的放置间距。

- `at`
  - 可选，默认 `"end"`。
  - 可写 `"start"` 或 `"end"`。
  - 指定从传送带起点侧还是终点侧生成。

- `interval_seconds`
  - 可选，默认 `0.35`。
  - 同一批包裹逐个放上带时的间隔。

### 6.2 `arm_pick_place`

别名：

- `manipulator_pick_place`

作用：

- 让码放机械臂从候选池取一个包裹，并放到目标世界坐标

示例：

```json
{
  "label": "Palletize one candidate",
  "command": "arm_pick_place",
  "params": {
    "candidate_index": 1,
    "target": [21.15, 7.8, 0.85],
    "arm_name": "pallet_arm1"
  }
}
```

参数说明：

- `target`
  - 必填。
  - 目标放置坐标，格式 `[x, y, z]`。

- `candidate_index`
  - 与 `package_name` 二选一。
  - 候选池里的第几个候选包裹。
  - 当前是 1 基索引。

- `package_name`
  - 与 `candidate_index` 二选一。
  - 直接用包裹名指定目标。

- `arm_name`
  - 可选。
  - 不填时使用当前场景里的第一个机械臂。

注意：

- `candidate_index` 只会在当前候选池里挑选状态为 `candidate` 或 `on_conveyor` 的包裹。

### 6.3 `shuttle_pick_to_conveyor`

别名：

- `pick_to_conveyor`

作用：

- 让拣货车从指定货架取一批包裹
- 先把这批包裹排入车顶队列
- 再统一前往传送带起始端按顺序放下

示例：

```json
{
  "label": "Pick two shelf packages to conveyor",
  "command": "shuttle_pick_to_conveyor",
  "params": {
    "shelf_index": 1,
    "package_names": ["ShelfPkg_1", "ShelfPkg_2"],
    "conveyor_name": "DemoConveyor",
    "shuttle_name": "shuttle1",
    "drop_spacing": 0.45
  }
}
```

参数说明：

- `shelf_index`
  - 必填。
  - 货架索引，1 基。

- `conveyor_name`
  - 必填。
  - 目标传送带名。

- `package_names`
  - 与 `package_positions` 二选一。
  - 要从货架上取走的包裹名列表。

- `package_positions`
  - 与 `package_names` 二选一。
  - 包裹位置列表，系统会在该货架上找最近包裹。

- `shuttle_name`
  - 可选。
  - 不填时使用当前场景里的第一个拣货车。

- `drop_spacing`
  - 可选，默认 `0.45`。
  - 多个包裹在传送带起点的投放间距。

当前 step 组织逻辑是：

1. 逐个接近货架包裹
2. 逐个语义拾取
3. 整体移动到传送带起点
4. 按顺序把所有包裹逐个放上传送带

所以这个命令天然适合“连续取货，再连续上带”的流程。

### 6.4 `transporter_move_pallet`

别名：

- `move_pallet`

作用：

- 让搬运车去托盘附近
- 装载托盘
- 搬运到目标坐标
- 再释放托盘

示例：

```json
{
  "label": "Move pallet to outbound",
  "command": "transporter_move_pallet",
  "params": {
    "pallet_index": 1,
    "target": [25.0, 12.0, 0.0],
    "transporter_name": "transporter1"
  }
}
```

参数说明：

- `target`
  - 必填。
  - 目标世界坐标。
  - 可写 `[x, y]` 或 `[x, y, z]`。

- `pallet_index`
  - 与 `pallet_name` 二选一。
  - 托盘索引，1 基。

- `pallet_name`
  - 与 `pallet_index` 二选一。
  - 托盘名。

- `transporter_name`
  - 可选。
  - 不填时使用当前场景里的第一个搬运车。

### 6.5 `reset_scene`

别名：

- `reset`

作用：

- 把当前场景清空为干净状态

示例：

```json
{
  "label": "Reset scene",
  "command": "reset_scene",
  "params": {}
}
```

这个命令本身不需要参数。

---

## 7. 写法二：HTTP 风格 step

除了 `command + params`，还支持一种更接近 HTTP 的写法：

```json
{
  "label": "Spawn packages via endpoint style",
  "method": "POST",
  "path": "/conveyors/DemoConveyor/packages",
  "body": {
    "count": 2,
    "package_prefix": "DemoBox",
    "at": "start"
  }
}
```

当前字段说明：

- `method`
  - 可选，默认 `"POST"`。
  - 当前实现只支持 `POST`。

- `path`
  - 或 `endpoint`
  - 指向内部支持的 API 路径。

- `body`
  - 或 `payload`
  - 或 `params`
  - 作为这一步的请求体。

当前支持的路径映射如下：

- `POST /arm/pick_place`
- `POST /conveyors/{conveyor_name}/packages`
- `POST /shuttle/pick_to_conveyor`
- `POST /transporter/move_pallet`
- `POST /reset`

这类写法最终也会被转换成和 `command + params` 相同的内部入队逻辑。

---

## 8. `default_demo_script.json` 当前实际在演示什么

当前默认脚本一共 6 步，完整路径如下：

`D:\Works\lpb_mas\LPB_config_server\config\default_demo_script.json`

它演示的是一条最小但完整的物流链：

1. 拣货车从 `shelf_1` 拿两件货，上带
2. 拣货车从 `shelf_2` 再拿一件货，上带
3. 机械臂从候选区取第 1 件包裹，放到托盘点位 1
4. 机械臂从候选区再取第 1 件包裹，放到托盘点位 2
5. 机械臂从候选区再取第 1 件包裹，放到托盘点位 3
6. 搬运车把托盘搬到出库点

对应的设计意图是：

- 前两步展示“货架拣货 -> 连续上带”
- 中间三步展示“传送带末端候选池 -> 机械臂码放托盘”
- 最后一步展示“整托搬运”

这里第 3 步特意写了：

```json
"delay_seconds": 8.5
```

这是为了给传送带运输留出明显时间，让第一件包裹有机会到达候选区，再让机械臂开始拿。

而第 2 到第 6 步普遍使用了：

```json
"wait_for_idle": true
```

它的作用是让大阶段之间串行化，避免前一个机器人任务还没结束，下一个系统就提前抢着开始。

---

## 9. 一个推荐的脚本编排思路

如果后面要自己写新的 demo 脚本，建议按下面这个节奏组织：

1. 拣货阶段
   - 用 `shuttle_pick_to_conveyor`
   - 一次尽量处理一批包裹

2. 传送阶段
   - 需要等待包裹真的跑到末端时，给 `arm_pick_place` 前面加 `delay_seconds`

3. 码放阶段
   - 多个码放 step 之间通常加 `wait_for_idle: true`

4. 搬运阶段
   - 托盘装满后，再发 `transporter_move_pallet`

一条常见经验是：

- 同一类动作内部用命令自身的多步逻辑来完成
- 不要把本来应该是一条完整语义命令的事情，拆成很多过碎的 step

比如：

- 一个货架的一批货，优先用一条 `shuttle_pick_to_conveyor`
- 不要拆成“取 1 件 -> 放 1 件 -> 取 1 件 -> 放 1 件”

---

## 10. 当前实现里需要特别注意的点

### 10.1 各种索引都是 1 基

这些字段当前都是从 `1` 开始，不是从 `0` 开始：

- `shelf_index`
- `candidate_index`
- `pallet_index`

### 10.2 名字和索引通常是二选一

这些命令一般都支持“按名字”或“按索引”：

- 机械臂：`package_name` 或 `candidate_index`
- 搬运车：`pallet_name` 或 `pallet_index`

### 10.3 省略 agent 名时会取第一个

当前实现里这些字段不填时，会默认取场景中的第一个对应对象：

- `arm_name`
- `shuttle_name`
- `transporter_name`

### 10.4 `steps` 负责提交任务，不负责等待任务天然完成

脚本只是按规则发命令。

如果你需要“上一阶段真的做完，再进入下一阶段”，要靠：

- `wait_for_idle`
- 合理的 `delay_seconds`

来控制节奏。

### 10.5 endpoint 风格目前只支持 `POST`

如果写成 HTTP 风格 step，目前不能写 `GET`、`PUT`、`DELETE`。

---

## 11. 一个最小可用模板

```json
{
  "name": "simple_demo",
  "description": "Minimal LPB demo script.",
  "default_delay_seconds": 0.25,
  "steps": [
    {
      "label": "Pick shelf packages to conveyor",
      "command": "shuttle_pick_to_conveyor",
      "params": {
        "shelf_index": 1,
        "package_names": ["ShelfPkg_1", "ShelfPkg_2"],
        "conveyor_name": "DemoConveyor",
        "shuttle_name": "shuttle1",
        "drop_spacing": 0.45
      }
    },
    {
      "label": "Wait then palletize first package",
      "wait_for_idle": true,
      "delay_seconds": 8.0,
      "command": "arm_pick_place",
      "params": {
        "candidate_index": 1,
        "target": [21.15, 7.8, 0.85],
        "arm_name": "pallet_arm1"
      }
    },
    {
      "label": "Move pallet to outbound",
      "wait_for_idle": true,
      "command": "transporter_move_pallet",
      "params": {
        "pallet_index": 1,
        "target": [25.0, 12.0, 0.0],
        "transporter_name": "transporter1"
      }
    }
  ]
}
```

---

## 12. 配套查看文档

如果需要继续往下看，建议配合下面几份文档一起使用：

- API 说明：
  - `D:\Works\lpb_mas\LPB_config_server\docs\API_SERVER.md`

- 控制台说明：
  - `D:\Works\lpb_mas\LPB_config_server\docs\COMMAND_CONSOLE.md`

- 当前默认脚本文件：
  - `D:\Works\lpb_mas\LPB_config_server\config\default_demo_script.json`

