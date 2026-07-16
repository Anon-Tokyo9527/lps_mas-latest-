import numpy as np
import math
import json
import time
from pathlib import Path

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.robot.manipulators.examples.universal_robots import UR10
from isaacsim.robot.manipulators.examples.universal_robots.controllers.pick_place_controller import PickPlaceController
from isaacsim.core.api.world import World

from .llm import LM


class Shuttle:
    def __init__(self, name, position=None, orientation=None):
        """
        初始化穿梭车实例。该类组合了一个移动底盘 (Ridgeback) 和一个机械臂 (UR10)，实现移动抓取功能。
        
        关键特性：
        1. **组合式架构**：底盘和机械臂是两个独立的 Prim，通过代码逻辑 (`sync_pos`) 保持位置同步，而非物理固定关节。
        2. **虚拟关节导航**：底盘移动依赖于 USD 模型中预设的虚拟棱柱关节 (Prismatic Joints) 和旋转关节，而非轮子摩擦力物理驱动。
        3. **集成控制**：封装了 PickPlaceController 用于机械臂的抓取和放置任务。

        Args:
            name (str): 机器人的唯一名称前缀。
            position (list[float], optional): 机器人的初始世界坐标 [x, y, z]。默认为 None。
            orientation (list[float], optional): 机器人的旋转角度（欧拉角 [x, y, z]，单位：度）。默认为 None。
        """
        if position is None:
            position = [0.0, 0.0, 0.0]
        self.base_position = np.array(position, dtype=float)
        self.position = self.base_position.copy()
        self._last_base_world_position = self.base_position.copy()
        self._fallback_last_move_at = None
        self._joint_retry_after = {}
        self.arm_mount_height = 0.29
        self.default_arm_positions = np.array([np.pi, -np.pi / 2, -np.pi / 2, -np.pi / 2, np.pi / 2, 0.0])
        self._max_arm_display_step = 0.16
        self.name = name
        self._step = 0
        self._x_joint_idx = None
        self._y_joint_idx = None
        self._rz_joint_idx = None

        extension_root = Path(__file__).resolve().parents[2]
        usd_path = extension_root / "data" / "Assets" / "ridgeback.usd"

        ridgeback_name = name + '_ridgeback'
        ridgeback_path = f"/World/{ridgeback_name}"

        if orientation is not None:
            orientation = euler_angles_to_quat(np.array(orientation), degrees=True)

        add_reference_to_stage(usd_path.as_posix(), ridgeback_path)
        self.ridgeback = SingleArticulation(
            prim_path = ridgeback_path, 
            name = ridgeback_name,
            position = self.base_position.tolist(),
            orientation = orientation
        )

        # 计算机械臂安装高度：在底盘位置基础上 Z 轴升高 0.29米
        arm_position = self.base_position.copy()
        arm_position[2] += self.arm_mount_height
        usd_path = extension_root / "data" / "Assets" / "ur10.usd"

        ur10_name = name + '_ur10'
        ur10_path = f"/World/{ur10_name}"

        add_reference_to_stage(usd_path.as_posix(), ur10_path)
        self.ur10 = UR10(
            prim_path = ur10_path,
            name = ur10_name,
            position = arm_position.tolist(),
            orientation = orientation,
            attach_gripper = True,
            gripper_usd = None
        )

        world = World.instance()
        world.scene.add(self.ridgeback)
        world.scene.add(self.ur10)

        # 设置机械臂初始姿态
        self.ur10.set_joints_default_state(
            positions=self.default_arm_positions.copy()
        )

        # 初始化抓取控制器 (PickPlaceController)
        self.controller = PickPlaceController(
            name="pick_place_controller", 
            gripper=self.ur10.gripper, 
            robot_articulation=self.ur10,
            #end_effector_initial_height=0.5 # 设定末端执行器的安全移动高度
        )

        self.sys_prompt = """
# Role
You are the **Shuttle Agent Logic Core**.
Your task is to parse A2A commands (`RETRIEVE_BIN` or `STORE_BIN`) and generate a precise **Execution Plan** (list of function calls) based on the current `SIMULATION STATE`.

# Environment Awareness
You are provided with a `SIMULATION STATE` list containing objects with:
- `name`: Unique ID.
- `position`: [x, y, z] (World coordinates).
- `size`: [length, width, height] (Bounding box).

# Available Functions (API)
You must ONLY use the following functions in your plan:

1. `move_to(pos=[x, y, z])`
   - Moves the shuttle chassis to the target world coordinates.

2. `pick(obj="String")`
   - Picks up the target object. The robot must be at the approach position before calling this.

3. `place_relative(obj="String", pos=[x, y, z], relative_obj="String" or None)`
   - **CASE A: Placing on a Pallet/Handover (Mobile/Dynamic)**
     - Set `relative_obj` = "Target_Pallet_Name".
     - Set `pos` = `[x, y, z]` (The **offset** relative to the pallet center).
   - **CASE B: Placing on a Rack/Shelf Port (Fixed/Static)**
     - Set `relative_obj` = `null` (or None).
     - Set `pos` = `[x, y, z]` (The **ABSOLUTE WORLD COORDINATES** of the target placement spot).

# Navigation & Safety Rules (CRITICAL)

## 1. Standoff Distance (Approach Point)
- NEVER move directly to a Shelf, Rack, or Port position. You will crash.
- Calculate an **Approach Position** offset by **0.8 meters** from the target.

## 2. Manhattan Pathing (Axis-Aligned Movement)
- **Do NOT move diagonally** unless absolutely necessary.
- Plan paths using **X-axis** and **Y-axis** segments to mimic warehouse lanes.
- **Example**: To go from `[0, 0]` to `[10, 5]`:
  - Step 1: `move_to([10, 0, 0])` (Move along X)
  - Step 2: `move_to([10, 5, 0])` (Move along Y)
- Check `SIMULATION STATE` to decide whether to move X first or Y first to avoid obstacles.
## 3. Environment Awareness & Collision Avoidance (HIGHEST PRIORITY)
- **Analyze SIMULATION STATE**: Before generating ANY `move_to` command, you MUST iterate through obstacles in the `SIMULATION STATE`.
- **Bounding Box Logic**: Treat every object as a solid box defined by its `position` ± (`size`/2).
- **Path Validation**: 
  - For every straight-line segment you plan, check if it intersects with any object's Bounding Box.
  - **Safety Margin**: Maintain at least **0.3m clearance** from any object's edge.
- **Conflict Resolution**:
  - If "X-first" path hits an obstacle, switch to "Y-first" path.
  - If both are blocked, insert an intermediate waypoint in open space to bypass the obstacle.
  - **DO NOT** output a path that passes through an object listed in `SIMULATION STATE`.

# Task Workflows

## A. RETRIEVE_BIN (From Shelf -> To Handover/Pallet)
1. Locate `target_bin` in Sim State.
2. `move_to` Approach Point of `target_bin` (Use Manhattan path).
3. `pick` the bin.
4. Locate `destination` (Handover/Pallet) in Sim State.
5. `move_to` Approach Point of `destination` (Use Manhattan path).
6. `place_relative` onto the pallet (`relative_obj`="Pallet_Name", `pos`=[Relative X, Y, Z]).

## B. STORE_BIN (From Handover/Pallet -> To Rack/Shelf)
1. Locate `source_port` (Handover) in Sim State.
2. `move_to` Approach Point of `source_port`.
3. `pick` the bin.
4. Locate `target_rack` in Sim State to find the drop-off coordinate.
5. `move_to` Approach Point of `target_rack`.
6. `place_relative` into the rack (`relative_obj`=None, `pos`=[Absolute World X, Y, Z]).

# Output Format
Return a A2A message. The `body` must contain:
- `msg_type`: "EXECUTION_PLAN"
- `payload`: A dictionary with `task_id` and a list `steps`.
  - `steps`: List of `{"func": "NAME", "args": {...}}`.
"""

        
    def initialize(self):
        """
        初始化物理关节索引。
        必须在仿真开始运行后调用。
        """
        self.ridgeback.initialize()
        dof_names = self.ridgeback.dof_names
        self._x_joint_idx = dof_names.index("dummy_base_prismatic_x_joint")
        self._y_joint_idx = dof_names.index("dummy_base_prismatic_y_joint")
        self._rz_joint_idx = dof_names.index("dummy_base_revolute_z_joint")
        self.sync_pos()
        self.set_arm_display_pose()


    def reset(self):
        """
        重置控制器的内部状态。
        """
        self.controller.reset()
        self._step = 0
        self.sync_pos()
        self.set_arm_display_pose()


    def move_to(self, pos):
        """
        控制底盘移动到指定位置。

        该函数实现了底盘的移动逻辑，并手动同步机械臂的位置，同时旋转机械臂基座以保持相对朝向。

        Args:
            pos (list[float]): 目标绝对坐标 [x, y, z]。

        Returns:
            bool: 如果到达目标位置范围 (0.2m 内) 返回 True，否则返回 False。
        """
        world_target = np.array(pos, dtype=float)
        pos = world_target.copy()
        if self._x_joint_idx is None or self._y_joint_idx is None or self._rz_joint_idx is None:
            try:
                self.initialize()
            except Exception:
                return self._move_to_world_pose_fallback(world_target)
        # 计算相对于初始位置的位移目标 (因为 dummy joints 通常是相对于初始点的偏移)
        pos[:2] = pos[:2] - self.base_position[:2]
        
        joint_positions = self._safe_get_joint_positions(self.ridgeback, "ridgeback")
        
# 增加空值判断
        if joint_positions is None:
            return self._move_to_world_pose_fallback(world_target)
            return False # 告诉调用者这一步还没完成
        
        # === 检查是否到达目标 ===
        # 使用曼哈顿距离判断误差是否小于 0.05
        if np.abs(joint_positions[self._x_joint_idx] - pos[0]) + np.abs(joint_positions[self._y_joint_idx] - pos[1]) < 0.05:
            self.sync_pos()
            self.set_arm_display_pose(target=world_target, mode="neutral")
            return True

        delta_x = pos[0] - joint_positions[self._x_joint_idx]
        delta_y = pos[1] - joint_positions[self._y_joint_idx]
        
        # 计算底盘需要朝向的角度 (朝着目标方向转)
        target_angle = math.atan2(delta_y, delta_x)

        action = ArticulationAction(joint_positions=np.full(self.ridgeback.num_dof, np.nan))
        action.joint_positions[self._x_joint_idx] = pos[0]
        action.joint_positions[self._y_joint_idx] = pos[1]
        action.joint_positions[self._rz_joint_idx] = target_angle

        self.ridgeback.apply_action(action)

        # === 同步机械臂的位置和角度 ===
        self.sync_pos()
        self.set_arm_display_pose(target=world_target, mode="neutral", yaw=target_angle)

        return False
        

    def pick(self, obj):
        """
        执行抓取动作。

        需要在仿真循环中每帧调用，直到返回 True。

        Args:
            obj (str): 待抓取物体的名称 (Prim Name)。

        Returns:
            bool: 如果抓取动作完成（Event 5 代表 Attached）返回 True，否则返回 False。
        """
        self.sync_pos()

        world = World.instance()
        target_obj = world.scene.get_object(obj)
        target_pos, _ = target_obj.get_world_pose()
        size = target_obj.get_local_scale()
        
        current_joint_positions = self._safe_get_joint_positions(self.ur10, "ur10")
        if current_joint_positions is None:
            return False

        self.ur10.apply_action(self.controller.forward(
            picking_position = target_pos,
            placing_position = [0, 0, 0.6], # 抓取时的临时放置目标（通常不重要，只要不是 None）
            current_joint_positions = current_joint_positions,
            end_effector_offset=np.array([0, 0, size[2]]),
            # end_effector_orientation = target_orientation
        ))

        if self.controller.get_current_event() == 5:
            return True

        return False
    

    def place_relative(self, obj, pos, relative_obj=None):
        """
        执行放置动作，支持相对坐标放置。

        需要在仿真循环中每帧调用，直到返回 True。

        如果机械臂在抓取后发生过移动，则调用本函数前需要先调用 `update` 。

        Args:
            obj (str): 正在搬运的物体名称。
            pos (list[float]): 放置的目标位置坐标 [x, y, z]。
            relative_obj (str, optional): 参考物体名称。如果提供，pos 将被视为相对于该物体的偏移。

        Returns:
            bool: 如果放置动作全部完成返回 True，否则返回 False。
        """
        self.sync_pos()

        world = World.instance()
        target_obj = world.scene.get_object(obj)
        target_pos, _ = target_obj.get_world_pose()
        size = target_obj.get_local_scale()

        # === 计算最终放置位置 ===
        if relative_obj is not None:
            # 相对模式
            relative_obj = world.scene.get_object(relative_obj)
            relative_pos, _ = relative_obj.get_world_pose()
            goal_pos = np.array([
            relative_pos[0] + pos[0],
            relative_pos[1] + pos[1],
            relative_pos[2] + 0.27 + pos[2] # 0.27 是托盘表面的高度
        ])
        else:
            # 绝对模式
            goal_pos = np.array([
                pos[0],
                pos[1],
                pos[2]
            ])
        
        current_joint_positions = self._safe_get_joint_positions(self.ur10, "ur10")
        if current_joint_positions is None:
            return False
        
        self.ur10.apply_action(self.controller.forward(
            picking_position = target_pos,
            placing_position = goal_pos,
            current_joint_positions = current_joint_positions,
            end_effector_offset=np.array([0, 0, size[2]]),
            # end_effector_orientation = target_orientation
        ))

        return self.controller.is_done()


    def _normalize_angle(self, angle):
        return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


    def get_base_world_position(self, joint_positions=None):
        if joint_positions is None:
            joint_positions = self._safe_get_joint_positions(self.ridgeback, "ridgeback")
        base = self._last_base_world_position.copy() if joint_positions is None else self.base_position.copy()
        if joint_positions is not None and hasattr(self, "_x_joint_idx") and hasattr(self, "_y_joint_idx"):
            base[0] += float(joint_positions[self._x_joint_idx])
            base[1] += float(joint_positions[self._y_joint_idx])
            self._last_base_world_position = base.copy()
        return base


    def set_arm_display_pose(self, target=None, mode="neutral", yaw=None):
        self.sync_pos()
        desired = self._arm_display_joint_targets(target=target, mode=mode)

        if yaw is None and target is not None:
            base = self.get_base_world_position()
            target = np.array(target, dtype=float)
            yaw = math.atan2(float(target[1] - base[1]), float(target[0] - base[0]))
        if yaw is not None:
            desired[0] = self._normalize_angle(float(yaw) - math.pi)

        self._apply_arm_display_pose(desired)


    def _arm_display_joint_targets(self, target=None, mode="neutral"):
        desired = self.default_arm_positions.copy()
        reach_ratio = 0.5
        height_ratio = 0.5

        if target is not None:
            try:
                base = self.get_base_world_position()
                target = np.array(target, dtype=float)
                delta = target - base
                reach_ratio = float(np.clip((np.linalg.norm(delta[:2]) - 0.35) / 1.45, 0.0, 1.0))
                height_ratio = float(np.clip((delta[2] - self.arm_mount_height - 0.25) / 1.4, 0.0, 1.0))
            except Exception:
                pass

        mode = str(mode or "neutral")
        if mode == "pick":
            desired = np.array([
                np.pi,
                -1.30 + 0.18 * height_ratio,
                -1.58 - 0.16 * reach_ratio,
                -1.22 - 0.10 * height_ratio,
                np.pi / 2,
                0.0,
            ])
        elif mode == "place":
            desired = np.array([
                np.pi,
                -1.26 + 0.14 * height_ratio,
                -1.50 - 0.14 * reach_ratio,
                -1.18 - 0.08 * height_ratio,
                np.pi / 2,
                0.0,
            ])

        return desired


    def _apply_arm_display_pose(self, desired):
        joint_count = min(6, int(getattr(self.ur10, "num_dof", 6) or 6), len(desired))
        joint_indices = list(range(joint_count))
        desired = np.asarray(desired[:joint_count], dtype=float)

        try:
            current = self._safe_get_joint_positions(self.ur10, "ur10")
            if current is None:
                return
            if current is not None and len(current) >= joint_count:
                current = np.asarray(current[:joint_count], dtype=float)
                limited = current.copy()
                for idx, target in enumerate(desired):
                    delta = self._normalize_angle(float(target) - float(current[idx]))
                    limited[idx] = float(current[idx]) + float(np.clip(delta, -self._max_arm_display_step, self._max_arm_display_step))
                desired = limited
        except Exception:
            pass

        try:
            self.ur10.apply_action(
                ArticulationAction(joint_positions=desired[:joint_count], joint_indices=joint_indices)
            )
        except Exception:
            try:
                self.ur10.set_joint_positions(desired[:joint_count], joint_indices=joint_indices)
            except Exception:
                pass


    def sync_pos(self):
        """
        同步机械臂与底盘的位置。
        
        由于底盘和机械臂没有通过物理关节连接，该函数读取底盘虚拟关节的位移，
        计算出底盘当前的实际位置，并将机械臂的基座“瞬移”到该位置。
        """
        joint_positions = self._safe_get_joint_positions(self.ridgeback, "ridgeback")
        if joint_positions is None:
            arm_pos = self._last_base_world_position.copy()
            arm_pos[2] += self.arm_mount_height
            try:
                self.ur10.set_world_pose(position=arm_pos.tolist())
            except Exception:
                pass
            return arm_pos.tolist()
        base_pos = self.get_base_world_position(joint_positions)
        arm_pos = base_pos.copy()
        arm_pos[2] += self.arm_mount_height
        self.ur10.set_world_pose(position=arm_pos.tolist())
        return arm_pos.tolist()


    def _safe_get_joint_positions(self, articulation, key):
        now = time.monotonic()
        if now < float(self._joint_retry_after.get(key, 0.0)):
            return None
        try:
            joints = articulation.get_joint_positions()
        except Exception:
            joints = None
        if joints is None:
            self._joint_retry_after[key] = now + 0.35
            return None
        self._joint_retry_after[key] = 0.0
        return joints


    def _move_to_world_pose_fallback(self, world_target, tolerance=0.05, speed=2.0):
        target = np.array(world_target, dtype=float)
        target[2] = self._last_base_world_position[2]
        current = self._last_base_world_position.copy()
        delta = target - current
        dist = float(np.linalg.norm(delta[:2]))
        now = time.monotonic()
        last = self._fallback_last_move_at
        self._fallback_last_move_at = now
        if dist <= float(tolerance):
            self._set_base_world_pose_fallback(target)
            self.set_arm_display_pose(target=world_target, mode="neutral")
            return True
        dt = 0.05 if last is None else max(0.016, min(now - float(last), 0.12))
        step = min(dist, float(speed) * dt)
        ratio = step / max(dist, 1e-6)
        next_pos = current.copy()
        next_pos[:2] = current[:2] + delta[:2] * ratio
        self._set_base_world_pose_fallback(next_pos)
        yaw = math.atan2(float(delta[1]), float(delta[0])) if dist > 1e-6 else None
        self.set_arm_display_pose(target=world_target, mode="neutral", yaw=yaw)
        return False


    def _set_base_world_pose_fallback(self, base_pos):
        base_pos = np.array(base_pos, dtype=float)
        self.base_position = base_pos.copy()
        self.position = base_pos.copy()
        self._last_base_world_position = base_pos.copy()
        try:
            self.ridgeback.set_world_pose(position=base_pos.tolist())
        except Exception:
            pass
        arm_pos = base_pos.copy()
        arm_pos[2] += self.arm_mount_height
        try:
            self.ur10.set_world_pose(position=arm_pos.tolist())
        except Exception:
            pass


    def update(self, obj):
        """
        更新控制器状态
        
        在调用 `place_relative` 前调用。

        Args:
            obj (str): 正在搬运的物体名称。
        """
        world = World.instance()
        target_pos, _ = world.scene.get_object(obj).get_world_pose()
        self.controller.update(target_pos)  
        

    def get_world_pose(self):
        """
        获取机械臂基座的世界位姿。

        Returns:
            tuple: (position [x,y,z], orientation [w,x,y,z])
        """
        self.sync_pos()
        pos = self.get_base_world_position()
        try:
            _, orientation = self.ridgeback.get_world_pose()
        except Exception:
            orientation = None
        return pos, orientation
    
    def get_end_effector_pose(self):#####
        """
        Return the UR10 end-effector pose if the Isaac Sim wrapper exposes it.

        The shuttle placement animation uses this as a visual grasp fallback: while
        the real project does not yet configure a Surface Gripper constraint, the
        package is kept under the moving end-effector instead of being animated on
        its own.
        """
        self.sync_pos()
        for candidate in (
            getattr(self.ur10, "end_effector", None),
            getattr(self.ur10, "_end_effector", None),
            getattr(self.ur10, "gripper", None),
        ):
            if candidate is None:
                continue
            if hasattr(candidate, "get_world_pose"):
                return candidate.get_world_pose()
        return None, None


    def set_world_pose(self, position=None, orientation=None):
        """
        设置世界位姿。
        目前未实现。

        Args:
            position (list[float], optional): [x, y, z].
            orientation (list[float], optional): [w, x, y, z].
        """
        print("This function is not implemented")


    def init_llm(self, provider, model_name, api_key):
        """
        初始化 LLM 客户端。

        Args:
            provider (str): 模型供应商。
            model_name (str): 具体模型。
            api_key (str): API 密钥。
        """
        self.lm = LM(provider, model_name, api_key, self.sys_prompt)


    def reason(self, sim_state, command):
        """
        智能体推理核心函数：根据指令和环境，通过 LLM 生成执行计划。

        Args:
            sim_state (list):
            
            command (dict): 指令
        Returns:
            tuple: (full_response_text, steps_list)
                   - full_response_text: 模型返回的完整原始字符串 (用于日志/调试)。
                   - steps_list: 解析出的执行步骤列表 (直接传给 step 函数使用)。
        """
        # 生成 Prompt
        user_prompt = self.generate_prompt(sim_state, command)

        raw_response = self.lm.generate(user_prompt)

        # 去除干扰字符
        clean_json_str = raw_response.strip()
        if clean_json_str.startswith("```json"):
            clean_json_str = clean_json_str[7:]
        if clean_json_str.startswith("```"):
            clean_json_str = clean_json_str[3:]
        if clean_json_str.endswith("```"):
            clean_json_str = clean_json_str[:-3]
        
        clean_json_str = clean_json_str.strip()

        response_data = json.loads(clean_json_str)

        # 提取 Steps
        if "body" in response_data:
            payload = response_data["body"].get("payload", {})
        else:
            payload = response_data.get("payload", response_data)
        
        steps = payload.get("steps", [])
        
        return raw_response, steps


    def step(self, plan_steps):
        """
        执行计划的步进函数。
        
        该函数需要在仿真的每一帧（或物理步）中被调用。它负责维护当前执行到的步骤索引 (`self._step`)，
        并判断当前动作是否完成，如果完成则自动切换到下一个动作。

        Args:
            plan_steps (list[dict]): 由 LLM 生成的完整执行计划列表。
                                     格式: [{"func": "...", "args": {...}}, ...]

        Returns:
            bool: 如果整个计划的所有步骤都已执行完毕，返回 True；否则返回 False。
        """
        if self._step == len(plan_steps):
            self.reset()
            return True

        step = plan_steps[self._step]
        func_name = step["func"]
        kwargs = step["args"]
        if self.execute_plan(func_name, kwargs):
            if self._step + 1 < len(plan_steps) and plan_steps[self._step + 1]["func"] == "place_relative":
                self.update(plan_steps[self._step + 1]["args"]["obj"])
            self._step += 1

        return False


    def execute_plan(self, func_name, kwargs): 
        """
        将字符串形式的函数名映射到实际的类方法调用。

        Args:
            func_name (str): 要执行的函数名称.
            kwargs (dict): 传递给函数的命名参数字典.

        Returns:
            bool: 对应底层函数的返回值。
                  通常 True 表示动作完成 (Is Done)，False 表示动作正在进行中。
        """
        if func_name == "move_to":
            return self.move_to(**kwargs)
            
        elif func_name == "pick":
            return self.pick(**kwargs)
            
        elif func_name == "place_relative":
            return self.place_relative(**kwargs)
        
        else: print(f"Error: Unknown function name '{func_name}' in execution plan.")


    def generate_prompt(self, sim_state, a2a_message):
        """
        填充 Shuttle 的 LLM 提示词。

        Args:
            sim_state (list): 当前的仿真环境状态数据 (将被转为 JSON 字符串)
            a2a_message (dict): 收到的 A2A 指令字典 (将被转为 JSON 字符串)

        Returns:
            str: 格式化后的完整 Prompt 字符串
        """

        # 辅助类处理 Numpy 数据转 JSON
        class NumpyEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                if isinstance(obj, np.float32) or isinstance(obj, np.float64):
                    return float(obj)
                return super().default(obj)

        sim_context = json.dumps(sim_state, indent=2, cls=NumpyEncoder)

        shuttle_name = self.name
        
        raw_pos, _ = self.get_world_pose()
        current_shuttle_position = f"[{float(raw_pos[0]):.2f}, {float(raw_pos[1]):.2f}, {float(raw_pos[2]-0.29):.2f}]"

        incoming_a2a_message_json = json.dumps(a2a_message, indent=2, cls=NumpyEncoder)

        prompt_template = f"""
### SIMULATION STATE (Current World Data) ###
{sim_context}

### ROBOT STATUS ###
- Name: {shuttle_name}
- Position: {current_shuttle_position}

### INCOMING COMMAND (A2A) ###
{incoming_a2a_message_json}

### INSTRUCTION ###
Generate the "EXECUTION_PLAN".
"""

        return prompt_template.strip()
