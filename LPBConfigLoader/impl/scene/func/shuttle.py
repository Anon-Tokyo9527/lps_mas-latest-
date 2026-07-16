import numpy as np
import math

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.robot.manipulators.examples.universal_robots import UR10
from isaacsim.robot.manipulators.examples.universal_robots.controllers.pick_place_controller import PickPlaceController
from isaacsim.core.api.world import World
from pathlib import Path
import os


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
        self.position = position

        usd_path = (Path(__file__).resolve().parent / "usd" / "ridgeback.usd").as_posix()
        if not os.path.exists(usd_path):
            usd_path = "assets/ridgeback.usd"

        ridgeback_name = name + '_ridgeback'
        ridgeback_path = f"/World/{ridgeback_name}"

        if orientation is not None:
            orientation = euler_angles_to_quat(np.array(orientation), degrees=True)

        add_reference_to_stage(usd_path, ridgeback_path)
        self.ridgeback = SingleArticulation(
            prim_path = ridgeback_path, 
            name = ridgeback_name,
            position = position,
            orientation = orientation
        )

        # 计算机械臂安装高度：在底盘位置基础上 Z 轴升高 0.29米
        position[2] += 0.29
        usd_path = (Path(__file__).resolve().parent / "usd" / "ur10.usd").as_posix()
        if not os.path.exists(usd_path):
            usd_path = "assets/factory_franka.usd"

        ur10_name = name + '_ur10'
        ur10_path = f"/World/{ur10_name}"

        add_reference_to_stage(usd_path, ur10_path)
        self.ur10 = UR10(
            prim_path = ur10_path,
            name = ur10_name,
            position = position,
            orientation = orientation,
            attach_gripper = True,
            gripper_usd = None
        )

        world = World.instance()
        if world is None:
            world = World()
        world.scene.add(self.ridgeback)
        world.scene.add(self.ur10)

        # === 配置机械臂参数 ===
        # 不同版本 Isaac Sim 的 gripper API 可能不同（避免 AttributeError）
        try:
            if hasattr(self.ur10.gripper, "set_translate"):
                self.ur10.gripper.set_translate(value=0.162)
            if hasattr(self.ur10.gripper, "set_direction"):
                self.ur10.gripper.set_direction(value="x")
        except Exception:
            pass
        # 设置机械臂初始姿态
        self.ur10.set_joints_default_state(
            positions=np.array([np.pi, -np.pi / 2, -np.pi / 2, -np.pi / 2, np.pi / 2, 0])
        )

        # 初始化抓取控制器 (PickPlaceController) —— 兼容不同版本参数
        try:
            import inspect
            sig = inspect.signature(PickPlaceController.__init__)
            if "end_effector_initial_height" in sig.parameters:
                self.controller = PickPlaceController(
                    name="pick_place_controller",
                    gripper=self.ur10.gripper,
                    robot_articulation=self.ur10,
                    end_effector_initial_height=0.5,
                )
            else:
                self.controller = PickPlaceController(
                    name="pick_place_controller",
                    gripper=self.ur10.gripper,
                    robot_articulation=self.ur10,
                )
        except Exception:
            self.controller = PickPlaceController(
                name="pick_place_controller", 
                gripper=self.ur10.gripper, 
                robot_articulation=self.ur10,
            )

        
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


    def reset(self):
        """
        重置控制器的内部状态。
        """
        self.controller.reset()


    def move_to(self, pos):
        """
        控制底盘移动到指定位置。

        该函数实现了底盘的移动逻辑，并手动同步机械臂的位置，同时旋转机械臂基座以保持相对朝向。

        Args:
            pos (list[float]): 目标绝对坐标 [x, y, z]。

        Returns:
            bool: 如果到达目标位置范围 (0.2m 内) 返回 True，否则返回 False。
        """
        pos = np.array(pos)
        # 计算相对于初始位置的位移目标 (因为 dummy joints 通常是相对于初始点的偏移)
        pos[:2] = pos[:2] - self.position[:2]
        
        joint_positions = self.ridgeback.get_joint_positions()

        # === 检查是否到达目标 ===
        # 使用曼哈顿距离判断误差是否小于 0.2
        if np.abs(joint_positions[self._x_joint_idx] - pos[0]) + np.abs(joint_positions[self._y_joint_idx] - pos[1]) < 0.2:
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
        action = ArticulationAction(joint_positions=[target_angle - np.pi], joint_indices=[0])
        self.ur10.apply_action(action)

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
        
        current_joint_positions = self.ur10.get_joint_positions()

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
        
        current_joint_positions = self.ur10.get_joint_positions()
        
        self.ur10.apply_action(self.controller.forward(
            picking_position = target_pos,
            placing_position = goal_pos,
            current_joint_positions = current_joint_positions,
            end_effector_offset=np.array([0, 0, size[2]]),
            # end_effector_orientation = target_orientation
        ))

        return self.controller.is_done()


    def sync_pos(self):
        """
        同步机械臂与底盘的位置。
        
        由于底盘和机械臂没有通过物理关节连接，该函数读取底盘虚拟关节的位移，
        计算出底盘当前的实际位置，并将机械臂的基座“瞬移”到该位置。
        """
        joint_positions = self.ridgeback.get_joint_positions()
        ridgeback_pos = [
            joint_positions[self._x_joint_idx] + self.position[0], 
            joint_positions[self._y_joint_idx] + self.position[1], 
            0.29
        ]
        self.ur10.set_world_pose(position=ridgeback_pos)      


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
        return self.ur10.get_world_pose()
    

    def set_world_pose(self, position=None, orientation=None):
        """
        设置世界位姿。
        目前未实现。

        Args:
            position (list[float], optional): [x, y, z].
            orientation (list[float], optional): [w, x, y, z].
        """
        print("This function is not implemented")
