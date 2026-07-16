import numpy as np
import json
import time
from pathlib import Path

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.robot.manipulators.examples.universal_robots import UR10
from isaacsim.robot.manipulators.examples.universal_robots.controllers.pick_place_controller import PickPlaceController
from isaacsim.core.api.world import World

from .llm import LM


class Manipulator:
    def __init__(self, name, position=None, orientation=None):
        """
        初始化机械臂实例。该类封装了 UR10 机械臂的加载、初始化及高层控制逻辑。
        
        它主要用于执行“抓取-放置”任务，支持绝对坐标放置和基于参考物体的相对坐标放置。

        Args:
            name (str): 机械臂在场景中的名称。
            position (list[float], optional): 机械臂的世界坐标 [x, y, z]。默认为 None。
            orientation (list[float], optional): 机械臂的旋转角度（欧拉角 [x, y, z]，单位：度）。默认为 None。
        """
        self.name = name
        self._step = 0
        self._max_joint_step = 0.025
        self._reject_joint_step = 6.4
        self.last_control_status = {"accepted": True, "reason": "init"}
        self._joint_retry_after = 0.0
        self.default_joint_positions = np.array([np.pi, -np.pi / 2, -np.pi / 2, -np.pi / 2, np.pi / 2, 0.0])

        extension_root = Path(__file__).resolve().parents[2]
        usd_path = extension_root / "data" / "Assets" / "ur10.usd"

        prim_path = f"/World/{name}"

        add_reference_to_stage(usd_path.as_posix(), prim_path)

        if orientation is not None:
            orientation = euler_angles_to_quat(np.array(orientation), degrees=True)

        self.ur10 = UR10(
            prim_path = prim_path,
            name = name,
            position = position,
            orientation = orientation,
            attach_gripper = True,
            gripper_usd = None
        )

        world = World.instance()
        world.scene.add(self.ur10)

        self.ur10.set_joints_default_state(positions=self.default_joint_positions.copy())

        # 初始化抓取控制器 (PickPlaceController)
        self.controller = PickPlaceController(
            name="pick_place_controller", 
            gripper=self.ur10.gripper, 
            robot_articulation=self.ur10,
            #end_effector_initial_height=0.65, # 设定末端执行器的安全移动高度
            events_dt=[0.006, 0.003, 0.04, 0.08, 0.004, 0.002, 0.004, 0.08, 0.006, 0.04],
        )

        self.sys_prompt = """
# Role
You are the **Robotic Arm Palletizing Logic Core**.
Your task is to parse `PALLETIZE` commands and generate a precise **Execution Plan** to stack items into a target container.

# Environment Awareness
You are provided with a `SIMULATION STATE` list containing objects with:
- `name`: Unique ID.
- `position`: [x, y, z] (World coordinates).
- `size`: [length, width, height] (Bounding box).

# Available Functions (API)
You must ONLY use the following functions in your plan:

1. `pick_and_place_relative(obj="String", pos=[x, y, z], relative_obj="String" or None)`
   - **CASE A: Placing on a Pallet/Handover (Mobile/Dynamic)**
     - Set `relative_obj` = "Target_Pallet_Name".
     - Set `pos` = `[x, y, z]` (The **offset** relative to the pallet center).
   - **CASE B: Placing on a Rack/Shelf Port (Fixed/Static)**
     - Set `relative_obj` = `null` (or None).
     - Set `pos` = `[x, y, z]` (The **ABSOLUTE WORLD COORDINATES** of the target placement spot).

# Stacking Logic (3D Bin Packing)

## 1. Coordinate Anchor: BOTTOM CENTER
- The `pos` argument represents the **BOTTOM CENTER** of the item, NOT the geometric center.
- **Z-Axis Implication**:
  - To place an item on the floor of the container: **`z = 0`**.
  - To place an item on top of another item with height $H$: **`z = H`** (Cumulative Height).
  - **DO NOT** add half-height ($H/2$) to the Z coordinate.
  
## 2. Strategy: "COMPACT" (Maximize Space)
- **Objective**: Pack items as efficiently as possible to minimize wasted space inside the container.
- **Physics Safety Gap (CRITICAL)**: 
  - In physics simulations, objects placed perfectly touching each other causes collision jitter (instability).
  - **Rule**: You MUST leave a small **Safety Gap (approx 0.02m to 0.03m)** between items.
  - Do NOT let items overlap or touch sides perfectly.
- **Layout**:
  - Use your spatial reasoning to fit mixed-sized items.
  - Prioritize filling the XY plane (Floor) first before stacking vertically (Z).
  - Ensure items stay within the container's bounding box (check `SIMULATION STATE` for container size).

# Task Workflow
1. **Plan Stacking**: 
   - Calculate `[x, y, z]` for each item based on **Bottom Center** logic.
   - Apply the **Safety Gap** between items.
2. **Generate Sequence**: Create a list of `pick_and_place_relative` calls.

# Output Format
Return a A2A message. The `body` must contain:
- `msg_type`: "EXECUTION_PLAN"
- `payload`: A dictionary with `task_id` and a list `steps`.
  - `steps`: List of `{"func": "NAME", "args": {...}}`.
"""


    def reset(self):
        """
        重置控制器的内部状态。
        当开始新的抓取任务序列时调用。
        """
        self.controller.reset()
        self._step = 0
        self.last_control_status = {"accepted": True, "reason": "reset"}


    def pick_and_place_relative(self, obj, pos, relative_obj=None):
        """
        执行抓取并放置任务（支持相对坐标）。

        需要在仿真循环中每帧调用，直到返回 True。

        Args:
            obj (str): 待抓取物体的名称。
            pos (list[float]): 放置的目标位置坐标 [x, y, z]。
            relative_obj (str, optional): 参考物体名称。
                                          - 如果为 None，pos 被视为绝对世界坐标。
                                          - 如果不为 None，pos 被视为相对于该物体基准点的偏移。

        Returns:
            bool: 任务是否全部完成 (Is Done)。
        """
        world = World.instance()
        target_obj = world.scene.get_object(obj)
        target_position, _ = target_obj.get_world_pose()
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
        
        current_joint_positions = self._safe_get_joint_positions()
        if current_joint_positions is None:
            return False
        
        offset_z = self._gripper_clearance_offset(size)
        action = self.controller.forward(
            picking_position = target_position,
            placing_position = goal_pos,
            current_joint_positions = current_joint_positions,
            end_effector_offset=np.array([0.0, 0.0, offset_z]),
            # end_effector_orientation = target_orientation
        )
        self._apply_safe_action(action)

        return self.controller.is_done()


    def pick_and_place_positions(self, picking_position, placing_position, package_dimensions=None):
        """
        Drive the pick-place controller with fixed world-space pick/place targets.

        This is used by the semantic demo path where the package is visually attached
        to the gripper after pickup. Keeping the original pick target fixed prevents
        the controller from chasing the package after it starts following the gripper.
        """
        offset_z = self._gripper_clearance_offset(package_dimensions)

        current_joint_positions = self._safe_get_joint_positions()
        if current_joint_positions is None:
            return False
        action = self.controller.forward(
            picking_position=np.array(picking_position),
            placing_position=np.array(placing_position),
            current_joint_positions=current_joint_positions,
            end_effector_offset=np.array([0.0, 0.0, offset_z]),
        )
        self._apply_safe_action(action)
        return self.controller.is_done()


    def _gripper_clearance_offset(self, dimensions):
        if isinstance(dimensions, dict):
            height = float(dimensions.get("height", dimensions.get("z", 0.4)))
        else:
            raw = list(dimensions or [])
            height = float(raw[2]) if len(raw) > 2 else 0.4
        return min(max(height * 0.5 + 0.08, 0.16), 0.42)


    def _safe_get_joint_positions(self):
        now = time.monotonic()
        if now < float(self._joint_retry_after):
            return None
        try:
            joints = self.ur10.get_joint_positions()
        except Exception:
            joints = None
        if joints is None:
            self._joint_retry_after = now + 0.35
            self.last_control_status = {"accepted": False, "reason": "physics_view_not_ready"}
            return None
        self._joint_retry_after = 0.0
        return joints


    def _apply_safe_action(self, action):
        safe_action = self._limit_joint_action(action)
        if safe_action is None:
            return False
        try:
            self.ur10.apply_action(safe_action)
            return True
        except Exception as exc:
            self.last_control_status = {"accepted": False, "reason": "apply_action_failed", "error": str(exc)}
            return False


    def _limit_joint_action(self, action):
        if action is None:
            self.last_control_status = {"accepted": False, "reason": "empty_action"}
            return None
        joint_positions = getattr(action, "joint_positions", None)
        if joint_positions is None:
            self.last_control_status = {"accepted": True, "reason": "no_joint_position"}
            return action

        raw_positions = list(joint_positions)
        if all(pos is None for pos in raw_positions):
            self.last_control_status = {"accepted": True, "reason": "no_position_targets"}
            return action

        current_raw = self._safe_get_joint_positions()
        if current_raw is None:
            self.last_control_status = {"accepted": False, "reason": "physics_view_not_ready"}
            return None
        current = np.asarray(current_raw, dtype=float)
        if len(raw_positions) != len(current):
            self.last_control_status = {
                "accepted": True,
                "reason": "non_arm_action",
                "target_len": len(raw_positions),
                "joint_len": len(current),
            }
            return action

        limited_positions = []
        max_delta = 0.0
        for idx, target in enumerate(raw_positions):
            if target is None:
                limited_positions.append(None)
                continue
            target = float(target)
            if not np.isfinite(target):
                self.last_control_status = {"accepted": False, "reason": "non_finite_target", "joint_index": idx}
                return None
            delta = self._shortest_angle_delta(target, float(current[idx]))
            max_delta = max(max_delta, abs(delta))
            if abs(delta) > self._reject_joint_step:
                self.last_control_status = {
                    "accepted": False,
                    "reason": "joint_jump_rejected",
                    "joint_index": idx,
                    "delta": float(delta),
                    "target": float(target),
                    "current": float(current[idx]),
                }
                return None
            delta = float(np.clip(delta, -self._max_joint_step, self._max_joint_step))
            limited_positions.append(float(current[idx] + delta))

        self.last_control_status = {
            "accepted": True,
            "reason": "limited",
            "max_delta": float(max_delta),
            "max_joint_step": float(self._max_joint_step),
        }
        return ArticulationAction(
            joint_positions=limited_positions,
            joint_velocities=None,
            joint_efforts=None,
            joint_indices=getattr(action, "joint_indices", None),
        )


    def _shortest_angle_delta(self, target, current):
        return (float(target) - float(current) + np.pi) % (2.0 * np.pi) - np.pi


    def drive_semantic_pose(self, target_position=None, mode="neutral"):
        """
        Move the UR10 through stable, pre-shaped poses for visual pick/place demos.

        This avoids relying on IK for every package and pallet point. The scenario
        still controls semantic attach/release; this method only makes the arm move
        in a believable direction without violent joint jumps.
        """
        target_joints = self._semantic_joint_targets(target_position=target_position, mode=mode)
        return self._apply_safe_action(ArticulationAction(joint_positions=target_joints))


    def _semantic_joint_targets(self, target_position=None, mode="neutral"):
        current_raw = self._safe_get_joint_positions()
        if current_raw is None:
            return self.default_joint_positions.copy().tolist()
        current = np.asarray(current_raw, dtype=float)
        if len(current) < 6:
            return current.tolist()

        base_yaw = current[0]
        reach = 0.8
        height = 0.5
        if target_position is not None:
            try:
                target = np.asarray(target_position, dtype=float)
                base_pos, _ = self.get_world_pose()
                base_pos = np.asarray(base_pos, dtype=float)
                delta = target - base_pos
                base_yaw = float(np.arctan2(delta[1], delta[0]) + np.pi)
                reach = float(np.clip(np.linalg.norm(delta[:2]), 0.35, 1.8))
                height = float(np.clip(delta[2], 0.05, 1.7))
            except Exception:
                pass

        reach_ratio = float(np.clip((reach - 0.35) / 1.45, 0.0, 1.0))
        height_ratio = float(np.clip((height - 0.05) / 1.65, 0.0, 1.0))
        mode = str(mode or "neutral")

        if mode in ("pick", "place", "lower"):
            shoulder = -1.30 + 0.12 * height_ratio
            elbow = -1.58 - 0.16 * reach_ratio
            wrist_1 = -1.20 - 0.08 * height_ratio
            wrist_2 = 1.57
            wrist_3 = 0.0
        elif mode in ("grip", "release"):
            shoulder = -1.24 + 0.10 * height_ratio
            elbow = -1.62 - 0.14 * reach_ratio
            wrist_1 = -1.16 - 0.08 * height_ratio
            wrist_2 = 1.57
            wrist_3 = 0.12 if mode == "grip" else -0.12
        elif mode in ("lift", "carry", "transfer"):
            shoulder = -1.42 + 0.08 * height_ratio
            elbow = -1.38 - 0.08 * reach_ratio
            wrist_1 = -1.38
            wrist_2 = 1.57
            wrist_3 = 0.05
        elif mode in ("hover", "approach"):
            shoulder = -1.46 + 0.08 * height_ratio
            elbow = -1.35 - 0.10 * reach_ratio
            wrist_1 = -1.36
            wrist_2 = 1.57
            wrist_3 = 0.0
        else:
            shoulder = -1.45
            elbow = -1.45
            wrist_1 = -1.45
            wrist_2 = 1.57
            wrist_3 = 0.0

        joints = current.tolist()
        joints[:6] = [base_yaw, shoulder, elbow, wrist_1, wrist_2, wrist_3]
        return joints
    

    def get_world_pose(self):
        """
        获取机械臂基座的世界位姿。

        Returns:
            tuple: (position [x,y,z], orientation [w,x,y,z])
        """
        return self.ur10.get_world_pose()


    def get_end_effector_pose(self):
        """
        Return the gripper/end-effector world pose when the UR10 wrapper exposes it.

        Isaac Sim versions differ slightly in where the UR10 end effector is stored,
        so this method tries the common public/private handles and falls back to the
        gripper object if it can report a world pose.
        """
        candidates = [
            getattr(self.ur10, "end_effector", None),
            getattr(self.ur10, "_end_effector", None),
            getattr(self.ur10, "gripper", None),
            getattr(getattr(self.ur10, "gripper", None), "end_effector", None),
        ]
        for candidate in candidates:
            if candidate is None or not hasattr(candidate, "get_world_pose"):
                continue
            try:
                return candidate.get_world_pose()
            except Exception:
                continue
        return None, None
    
    
    def set_world_pose(self, position=None, orientation=None):
        """
        设置机械臂基座的世界位姿。

        对于固定机械臂不建议调用该函数。

        Args:
            position (list[float], optional): [x, y, z].
            orientation (list[float], optional): [w, x, y, z].
        """
        self.ur10.set_world_pose(position=position, orientation=orientation)


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
            sim_state (list): 环境状态
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
            self.controller.reset()
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
        if func_name == "pick_and_place_relative":
            return self.pick_and_place_relative(**kwargs)
        
        else: print(f"Error: Unknown function name '{func_name}' in execution plan.")


    # ========== A2A 相关代码已注释 ==========
    # def generate_prompt(self, sim_state, a2a_message):
    #     """
    #     填充 Manipulator 的 LLM 提示词。
    #
    #     Args:
    #         sim_state (list): 当前的仿真环境状态数据 (将被转为 JSON 字符串)
    #         a2a_message (dict): 收到的 A2A 指令字典 (将被转为 JSON 字符串)
    #
    #     Returns:
    #         str: 格式化后的完整 Prompt 字符串
    #     """
    #
    #     # 辅助类处理 Numpy 数据转 JSON
    #     class NumpyEncoder(json.JSONEncoder):
    #         def default(self, obj):
    #             if isinstance(obj, np.ndarray):
    #                 return obj.tolist()
    #             if isinstance(obj, np.float32) or isinstance(obj, np.float64):
    #                 return float(obj)
    #             return super().default(obj)
    #
    #     sim_context = json.dumps(sim_state, indent=2, cls=NumpyEncoder)
    #
    #     incoming_a2a_message_json = json.dumps(a2a_message, indent=2, cls=NumpyEncoder)
    #
    #     prompt_template = f"""
    # ### SIMULATION STATE (Current World Data) ###
    # {sim_context}
    #
    # ### INCOMING COMMAND (A2A) ###
    # {incoming_a2a_message_json}
    #
    # ### INSTRUCTION ###
    # Generate the "EXECUTION_PLAN".
    # """
    #
    #     return prompt_template.strip()
    
