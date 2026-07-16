import numpy as np
import math

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.core.api.world import World
from pathlib import Path
import os


class RidgebackBase:
    """
    仅底盘版 Ridgeback（不带机械臂）。
    - 使用 func/usd/ridgeback.usd
    - 提供 initialize + move_to（基于 dummy joints）用于简单导航
    """

    def __init__(self, name, position=None, orientation=None, scale=None):
        """
        Args:
            name (str): 机器人唯一名称
            position (list[float], optional): 初始位置 [x, y, z]
            orientation (list[float], optional): 初始欧拉角 [x, y, z]（度）
            scale (list[float], optional): 缩放比例 [sx, sy, sz]
        """
        self.position = np.array(position if position is not None else [0.0, 0.0, 0.0], dtype=float)
        self._scale_factor = scale[0] if scale else 1.0

        usd_path = (Path(__file__).resolve().parent / "usd" / "ridgeback.usd").as_posix()
        if not os.path.exists(usd_path):
            usd_path = "assets/ridgeback.usd"

        prim_path = f"/World/{name}"

        quat = None
        if orientation is not None:
            quat = euler_angles_to_quat(np.array(orientation), degrees=True)

        add_reference_to_stage(usd_path, prim_path)
        self.ridgeback = SingleArticulation(
            prim_path=prim_path,
            name=name,
            position=self.position.tolist(),
            orientation=quat,
            scale=scale,  # 传递缩放参数
        )

        world = World.instance()
        if world is None:
            world = World()
        world.scene.add(self.ridgeback)

        self._x_joint_idx = None
        self._y_joint_idx = None
        self._rz_joint_idx = None

    def initialize(self):
        """必须在仿真开始运行后调用一次，才能 move_to。"""
        self.ridgeback.initialize()
        dof_names = self.ridgeback.dof_names
        self._x_joint_idx = dof_names.index("dummy_base_prismatic_x_joint")
        self._y_joint_idx = dof_names.index("dummy_base_prismatic_y_joint")
        self._rz_joint_idx = dof_names.index("dummy_base_revolute_z_joint")

    def move_to(self, pos):
        """
        简单移动到底盘目标点（绝对坐标）。
        返回 True 表示到达（0.2m 内）。
        """
        if self._x_joint_idx is None:
            raise RuntimeError("RidgebackBase.initialize() must be called before move_to().")

        pos = np.array(pos, dtype=float)
        # dummy joints 多数是相对初始点的位移
        target = pos.copy()
        target[:2] = target[:2] - self.position[:2]

        joint_positions = self.ridgeback.get_joint_positions()
        if np.abs(joint_positions[self._x_joint_idx] - target[0]) + np.abs(joint_positions[self._y_joint_idx] - target[1]) < 0.2:
            return True

        delta_x = target[0] - joint_positions[self._x_joint_idx]
        delta_y = target[1] - joint_positions[self._y_joint_idx]
        target_angle = math.atan2(delta_y, delta_x)

        action = ArticulationAction(joint_positions=np.full(self.ridgeback.num_dof, np.nan))
        action.joint_positions[self._x_joint_idx] = target[0]
        action.joint_positions[self._y_joint_idx] = target[1]
        action.joint_positions[self._rz_joint_idx] = target_angle
        self.ridgeback.apply_action(action)
        return False

    def get_world_pose(self):
        return self.ridgeback.get_world_pose()

    def set_world_pose(self, position=None, orientation=None):
        self.ridgeback.set_world_pose(position=position, orientation=orientation)


