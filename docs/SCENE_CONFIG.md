# LPB Config Loader 场景 JSON 配置说明

本文档说明当前 `LPB Config Loader` 支持哪些场景自由度，以及这些自由度如何写进 JSON 配置文件。

默认配置文件：

```text
D:\workstation\isaac-sim-standalone-5.1.0-windows-x86_64\extsUser\LPB_config_server\config\default_scene.json
```

在插件窗口的 `World Controls -> Config JSON` 中填写 JSON 路径，然后点击 `LOAD` 即可加载。

## 坐标约定

所有位置默认使用 Isaac Sim 世界坐标：

```json
[x, y, z]
```

也可以写成对象格式：

```json
{ "x": 10, "y": 5, "z": 0 }
```

尺寸默认使用：

```json
[length, width, height]
```

也可以写成：

```json
{ "length": 8, "width": 1, "height": 6 }
```

旋转使用欧拉角里的 Z 轴角度，单位为度。常用写法：

```json
{ "rotation": 90 }
```

## 顶层结构

一个高层配置文件可以包含这些字段：

```json
{
  "scene_name": "my_scene",
  "seed": 7,
  "warehouse": {},
  "shelves": [],
  "conveyors": [],
  "vehicles": {},
  "robot_arms": [],
  "palletizing_areas": [],
  "pallet_transport_targets": [],
  "objects": [],
  "agents": []
}
```

`objects` 和 `agents` 是兼容旧版低层场景文件的透传字段。如果 JSON 只包含旧版 `objects/agents`，也可以直接加载。

## 1. 仓库范围

自由度：

- 仓库地面尺寸。
- 仓库原点
- 网格尺寸。

写法：

```json
{
  "warehouse": {
    "name": "Warehouse",
    "dimensions": [40, 30, 8],
    "position": [0, 0, 0],
    "grid_size": 40
  }
}
```

说明：

- `dimensions[0]` 控制 X 方向长度。
- `dimensions[1]` 控制 Y 方向宽度。
- `dimensions[2]` 暂时主要作为场景高度元信息。
- 当前加载器会生成 `/World/Ground`，并给它加静态碰撞。

## 2. 货架区域和排列

自由度：

- 货架生成区域的位置和大小。
- 按行优先或列优先排列。
- 行数、列数、货架间距。
- 单个货架尺寸、段数、旋转角。
- 货架上的包裹生成规则。

写法：

```json
{
  "shelves": [
    {
      "name_prefix": "Shelf",
      "area": {
        "center": [24, 14, 0],
        "size": [18, 12, 0]
      },
      "arrangement": "rows",
      "rows": 3,
      "columns": 4,
      "spacing": [1.8, 2.2],
      "shelf": {
        "dimensions": [8, 1, 6],
        "segments": 2,
        "rotation": 90
      }
    }
  ]
}
```

字段说明：

- `name_prefix`: 货架名前缀，实际生成 `Shelf_1`, `Shelf_2` 等。
- `area.center`: 货架区域中心坐标。
- `area.size`: 货架区域大小。
- `arrangement`: `"rows"` 表示行优先，`"columns"` 表示列优先。
- `rows`: 行数；不写时会根据区域大小和间距自动估算。
- `columns`: 列数；不写时会根据区域大小和间距自动估算。
- `count`: 可选，限制最多生成多少个货架。
- `spacing`: 货架中心之间额外留出的 X/Y 间距。
- `shelf.dimensions`: 单个货架尺寸。
- `shelf.segments`: 货架资产段数，当前支持 `1`, `2`, `3`, `4`。
- `shelf.rotation`: 货架绕 Z 轴旋转角。

如果不写 `segments`，加载器会按货架长度粗略推断：

- `length <= 6`: 1 段。
- `length <= 10`: 2 段。
- `length <= 14`: 3 段。
- 更长默认 4 段。

## 3. 货架包裹：随机模式

自由度：

- 每个货架包裹数量范围。
- 可用层数。
- 可用槽位数。
- 包裹尺寸类别。
- 随机填充比例。

写法：

```json
{
  "shelves": [
    {
      "name_prefix": "Shelf",
      "area": { "center": [24, 14, 0], "size": [18, 12, 0] },
      "rows": 3,
      "columns": 4,
      "shelf": { "dimensions": [8, 1, 6], "segments": 2, "rotation": 90 },
      "packages": {
        "mode": "random",
        "min_count": 1,
        "max_count": 5,
        "levels": [1, 2, 3],
        "slots": 8,
        "sizes": ["small", "medium", "large"]
      }
    }
  ]
}
```

也可以用填充比例：

```json
{
  "packages": {
    "mode": "random",
    "fill_rate": 0.25,
    "levels": [1, 2, 3],
    "slots": 8,
    "sizes": ["medium"]
  }
}
```

内置包裹尺寸：

```json
{
  "small":  { "length": 0.25, "width": 0.25, "height": 0.4 },
  "medium": { "length": 0.3,  "width": 0.3,  "height": 0.5 },
  "large":  { "length": 0.45, "width": 0.35, "height": 0.6 }
}
```

可调槽位参数：

```json
{
  "packages": {
    "slot_margin": 0.8,
    "slot_spacing": 0.55,
    "base_z": 1.2,
    "level_gap": 1.3
  }
}
```

说明：

- `slot_margin`: 沿货架长度方向的起始边距。
- `slot_spacing`: 相邻槽位间距。
- `base_z`: 第 1 层包裹高度。
- `level_gap`: 层间高度差。

## 4. 货架包裹：枚举模式

自由度：

- 指定某个货架、某一行、某一列放哪些包裹。
- 指定槽位、层数、尺寸或自定义尺寸。
- 按每排批量生成包裹。

### 4.1 精确枚举 items

```json
{
  "packages": {
    "mode": "enumerated",
    "items": [
      {
        "shelf_index": 0,
        "slot": 0,
        "level": 1,
        "size": "medium"
      },
      {
        "shelf_name": "Shelf_3",
        "slot": 3,
        "level": 2,
        "dimensions": [0.4, 0.3, 0.5]
      }
    ]
  }
}
```

匹配字段：

- `shelf_name`: 匹配指定货架名，例如 `"Shelf_3"`。
- `shelf_index`: 匹配第几个货架，从 `0` 开始。
- `row_index`: 匹配货架排列中的第几行，从 `0` 开始。
- `column_index`: 匹配货架排列中的第几列，从 `0` 开始。

这些字段也可以写数组：

```json
{ "row_index": [0, 1], "slot": 2, "level": 1, "size": "small" }
```

也可以写 `"all"` 或 `"*"` 表示全部匹配。

### 4.2 每排规则 rows

```json
{
  "packages": {
    "mode": "enumerated",
    "rows": [
      {
        "row": 0,
        "count": 4,
        "start_slot": 0,
        "level": 1,
        "size": "medium"
      },
      {
        "row": 1,
        "items": [
          { "slot": 1, "level": 2, "size": "small" },
          { "slot": 3, "level": 3, "size": "large" }
        ]
      }
    ]
  }
}
```

说明：

- `row`: 货架排列中的行号，从 `0` 开始。
- `count`: 在该行匹配到的每个货架上生成几个包裹。
- `start_slot`: 起始槽位。
- `items`: 手动列出该行的包裹。

## 5. 传送带

自由度：

- 可以有多条传送带。
- 每条传送带用起点和终点定义。
- 自动按线段切成多个传送带资产。
- 可指定单段长度、宽度、高度。

写法：

```json
{
  "conveyors": [
    {
      "name": "InboundConveyor",
      "start": [2, 4, 0],
      "end": [18, 4, 0],
      "segment_length": 2.0,
      "width": 0.8,
      "height": 0.35
    },
    {
      "name": "SortConveyor",
      "start": [18, 4, 0],
      "end": [18, 12, 0],
      "segment_length": 2.0
    }
  ]
}
```

说明：

- 加载器会根据 `start -> end` 自动计算每段中心点和旋转角。
- 生成对象名为 `InboundConveyor_1`, `InboundConveyor_2` 等。
- 如果 `start` 和 `end` 不在同一条水平/垂直线上，也会按直线摆放。

## 6. 车辆

自由度：

- 指定 shuttle 初始位置和朝向。
- 指定 transporter 初始位置和朝向。

写法：

```json
{
  "vehicles": {
    "shuttles": [
      {
        "name": "shuttle1",
        "position": [4, 10, 0],
        "rotation": 0
      }
    ],
    "transporters": [
      {
        "name": "transporter1",
        "position": [4, 2, 0],
        "rotation": 0
      }
    ]
  }
}
```

别名：

- `vehicles.sorting_carts` 等同于 `vehicles.shuttles`。
- `vehicles.carriers` 等同于 `vehicles.transporters`。

## 7. 独立机械臂

自由度：

- 指定机械臂名字。
- 指定机械臂世界坐标。
- 指定机械臂朝向。

写法：

```json
{
  "robot_arms": [
    {
      "name": "robot_arm1",
      "position": [10, 8, 0],
      "rotation": 90
    }
  ]
}
```

如果机械臂属于码放区，更推荐写到 `palletizing_areas` 中。

## 8. 码放区

自由度：

- 指定码放区中心和大小。
- 机械臂在码放区内用相对坐标摆放。
- 托盘在码放区内用相对坐标摆放。
- 托盘上包裹可以随机或枚举生成。

写法：

```json
{
  "palletizing_areas": [
    {
      "name": "PalletizingArea_1",
      "area": {
        "center": [10, 8, 0],
        "size": [6, 4, 0]
      },
      "robot_arm": {
        "name": "pallet_arm1",
        "relative_position": [-1.0, 0.0, 0.0],
        "rotation": 90
      },
      "pallets": [
        {
          "name": "Pallet_1",
          "relative_position": [1.2, 0.0, 0.0],
          "dimensions": [1.2, 1.0, 0.25],
          "packages": {
            "mode": "random",
            "count": 4,
            "rows": 2,
            "columns": 2,
            "sizes": ["medium"]
          }
        }
      ]
    }
  ]
}
```

说明：

- `area.center` 是码放区中心坐标。
- `robot_arm.relative_position` 是相对码放区中心的偏移。
- `pallets[].relative_position` 是相对码放区中心的偏移。
- 托盘上的包裹默认按托盘中心做相对布局。

## 9. 托盘包裹：随机模式

```json
{
  "packages": {
    "mode": "random",
    "count": 4,
    "rows": 2,
    "columns": 2,
    "sizes": ["medium"]
  }
}
```

说明：

- `count`: 生成包裹数量。
- `rows`: 托盘上 Y 方向划分几行。
- `columns`: 托盘上 X 方向划分几列。
- `sizes`: 从哪些尺寸类型里随机选。

## 10. 托盘包裹：枚举模式

```json
{
  "packages": {
    "mode": "enumerated",
    "items": [
      {
        "name": "Box_Pallet_1_A",
        "relative_position": [-0.25, -0.2, 0.4],
        "size": "medium"
      },
      {
        "name": "Box_Pallet_1_B",
        "row": 0,
        "column": 1,
        "dimensions": [0.4, 0.3, 0.5]
      }
    ]
  }
}
```

如果写了 `relative_position`，则直接使用相对托盘中心的位置。否则使用 `row/column` 自动换算。

## 11. 托盘搬运目标区

自由度：

- 指定搬运机器人最终放下托盘的目标区域。
- 目标区包含世界坐标、区域大小、朝向。
- 场景渲染时会生成一个红色虚线框，仅作为可视化标记，不参与碰撞。
- API 中 `/transporter/move_pallet` 可以继续传 `target: [x, y, z]`，也可以传 `target_name` 或 `target_index` 引用这里定义的目标区。

写法：

```json
{
  "pallet_transport_targets": [
    {
      "name": "PalletDropZone_1",
      "position": [24.0, 12.0, 0.0],
      "size": [2.6, 1.9, 0.08],
      "rotation": 0,
      "color": [1.0, 0.05, 0.02],
      "dash_length": 0.35,
      "gap_length": 0.18,
      "line_width": 0.04
    }
  ]
}
```

字段说明：

- `name`：目标区对象名。后续命令可用 `target_name` 引用，例如 `PalletDropZone_1`。
- `position`：目标区底部中心的世界坐标 `[x, y, z]`。通常 `z` 写 `0`。
- `size`：目标区尺寸 `[length, width, height]`。建议比托盘尺寸略大一圈。
- `rotation`：绕 Z 轴旋转角度，单位为度。
- `color`：虚线框颜色，默认红色。
- `dash_length`：单段虚线长度。
- `gap_length`：虚线间隔长度。
- `line_width`：线宽，单位为场景米。

等价别名：

- 顶层 `transport_targets` 等同于 `pallet_transport_targets`。
- `size` 等同于 `dimensions`。
- `position` 也可以写为 `center`。

## 12. 旧版 objects/agents 兼容

如果你已经有类似 `warehouse_scenes/*.json` 的低层格式，可以继续直接加载：

```json
{
  "scene_name": "legacy_layout",
  "warehouse": {
    "dimensions": { "length": 40, "width": 40, "height": 10 }
  },
  "objects": [
    {
      "type": "shelf",
      "name": "Shelf_1",
      "dimensions": { "length": 8, "width": 1, "height": 6 },
      "position": { "x": 10, "y": 10, "z": 0 },
      "rotation": 90,
      "children": []
    }
  ],
  "agents": [
    {
      "type": "robot_arm",
      "name": "RobotArm_1",
      "position": { "x": 8, "y": 8, "z": 0 },
      "rotation": 0
    }
  ]
}
```

如果 JSON 同时写了高层字段和 `objects/agents`，加载器会先展开高层字段，再把 `objects/agents` 一并交给原场景构建逻辑。

## 13. 完整示例

```json
{
  "scene_name": "example_scene",
  "seed": 7,
  "warehouse": {
    "dimensions": [40, 30, 8],
    "position": [0, 0, 0],
    "grid_size": 40
  },
  "shelves": [
    {
      "name_prefix": "Shelf",
      "area": { "center": [24, 14, 0], "size": [18, 12, 0] },
      "arrangement": "rows",
      "rows": 3,
      "columns": 4,
      "spacing": [1.8, 2.2],
      "shelf": { "dimensions": [8, 1, 6], "segments": 2, "rotation": 90 },
      "packages": {
        "mode": "random",
        "min_count": 1,
        "max_count": 5,
        "levels": [1, 2, 3],
        "slots": 8,
        "sizes": ["small", "medium", "large"]
      }
    }
  ],
  "conveyors": [
    {
      "name": "InboundConveyor",
      "start": [2, 4, 0],
      "end": [18, 4, 0],
      "segment_length": 2.0,
      "width": 0.8,
      "height": 0.35
    }
  ],
  "vehicles": {
    "shuttles": [
      { "name": "shuttle1", "position": [4, 10, 0], "rotation": 0 }
    ],
    "transporters": [
      { "name": "transporter1", "position": [4, 2, 0], "rotation": 0 }
    ]
  },
  "palletizing_areas": [
    {
      "area": { "center": [10, 8, 0], "size": [6, 4, 0] },
      "robot_arm": {
        "name": "pallet_arm1",
        "relative_position": [-1.0, 0.0, 0.0],
        "rotation": 90
      },
      "pallets": [
        {
          "name": "Pallet_1",
          "relative_position": [1.2, 0.0, 0.0],
          "dimensions": [1.2, 1.0, 0.25],
          "packages": {
            "mode": "random",
            "count": 4,
            "rows": 2,
            "columns": 2,
            "sizes": ["medium"]
          }
        }
      ]
    }
  ],
  "pallet_transport_targets": [
    {
      "name": "PalletDropZone_1",
      "position": [24, 12, 0],
      "size": [2.6, 1.9, 0.08],
      "rotation": 0
    }
  ]
}
```
