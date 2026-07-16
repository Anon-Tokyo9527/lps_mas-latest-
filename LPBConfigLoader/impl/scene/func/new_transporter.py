import numpy as np
import math
import os
from pathlib import Path

from isaacsim.core.utils.stage import get_current_stage, add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat, quat_to_euler_angles
from isaacsim.robot.wheeled_robots.robots import WheeledRobot
from isaacsim.robot.wheeled_robots.controllers.wheel_base_pose_controller import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers.differential_controller import DifferentialController
from isaacsim.core.api.world import World

from pxr import UsdPhysics, Sdf, Gf


class Transporter:
    def __init__(self, name, position=None, orientation=None, scale=None):
        """
        初始化运输车实例。该类封装了一个基于差速驱动的移动机器人 (如 iRobot Create 3)。

        它实现了基本的点到点导航功能，并包含一种基于"逻辑绑定"的简易搬运逻辑。

        原始大小约 [0.35, 0.35, 0.1]
        注意：WheeledRobot 不支持 scale 参数，传入的 scale 会被忽略
        
        Args:
            name (str): 机器人在场景中的唯一名称。
            position (list[float], optional): 初始位置 [x, y, z]。
            orientation (list[float], optional): 初始欧拉角（欧拉角 [x, y, z]，单位：度）。默认为 None。
            scale: 忽略（WheeledRobot 不支持缩放）
        """
        self.loaded_obj = None # 当前搬运的物体对象引用
        self.target_yaw = None # 搬运时物体的朝向
        self.joint_prim = None # 搬运时绑定货物的临时关节

        # 优先使用项目内自带的 USD（避免使用别人电脑上的绝对路径导致加载失败）
        usd_path = (Path(__file__).resolve().parent / "usd" / "create_3.usd").as_posix()
        if not os.path.exists(usd_path):
            usd_path = "assets/create_3.usd"
        if not os.path.exists(usd_path):
            raise FileNotFoundError(
                f"Create3 USD not found. Tried: {Path(__file__).resolve().parent / 'usd' / 'create_3.usd'} and assets/create_3.usd"
            )

        prim_path = f"/World/{name}"

        quat = None
        if orientation is not None:
            quat = euler_angles_to_quat(np.array(orientation), degrees=True)

        # 关键修复：先手动添加 USD 引用（避免 WheeledRobot 内部使用错误的路径）
        # 然后用 create_robot=False 让 WheeledRobot 包装已存在的 prim
        add_reference_to_stage(usd_path, prim_path)

        self.create3 = WheeledRobot(
                prim_path = prim_path,
                name = name,
                wheel_dof_names = ["left_wheel_joint", "right_wheel_joint"],
                create_robot = False,  # 不让 WheeledRobot 创建，用我们已添加的
                usd_path = usd_path,
                position = position,
                orientation = quat
            )
        
        self.controller = WheelBasePoseController(name="cool_controller",
                                              open_loop_wheel_controller=DifferentialController(name="simple_control",
                                                                                                wheel_radius=0.03575,
                                                                                                wheel_base=0.233),
                                              is_holonomic=False)

        world = World.instance()
        world.scene.add(self.create3)


    def reset(self):
        """
        重置运输车状态（释放货物）。
        """
        self.release_pallet()


    def move_to(self, pos):
        """
        控制机器人移动到指定目标点。
        需要在仿真循环中每一帧调用。

        Args:
            pos (list[float]): 目标位置 [x, y, z] (通常只关心 x, y)。

        Returns:
            bool: 如果到达目标范围内 (误差 < 0.04m) 返回 True，否则返回 False。
        """
        start_position, start_orientation = self.create3.get_world_pose()
            
        euler = quat_to_euler_angles(start_orientation)
        euler[:2] = 0
        euler[2] = math.atan2(pos[1] - start_position[1], float(pos[0] - start_position[0] + 1e-5))
        robot_ori = euler_angles_to_quat(euler)

        self.create3.set_world_pose(orientation=robot_ori)

        # 如果当前处于“载货”状态，强制更新货物旋转角，使得货物不跟随小车旋转
        if self.loaded_obj is not None:
            agv_yaw = quat_to_euler_angles(robot_ori)[2] 
            delta_yaw = self.target_yaw - agv_yaw
            rel_quat_np = euler_angles_to_quat(np.array([0.0, 0.0, delta_yaw])) 

            rel_gf_quat = Gf.Quatf(*[float(v) for v in rel_quat_np])
            self.joint_prim.GetLocalRot0Attr().Set(rel_gf_quat)
  

        action = self.controller.forward(start_position, robot_ori, np.array(pos[:2]), lateral_velocity=0.8, position_tol=0.01)
        # Create3 的左右轮关节通常对应关节索引 2 和 3 
        action.joint_indices = np.array([2,3])
        if action is not None:
            self.create3.apply_action(action)

        # === 判断是否到达 ===
        if np.mean(np.abs(start_position[:2] - pos[:2])) < 0.01:
            return True
        return False

    
    def load_pallet(self, obj):
        """
        装载货物（逻辑绑定）。
        
        创建一个临时关节用于绑定小车和货物
        同时记录货物的朝向，并在后续移动中保持该朝向。

        Args:
            obj (str): 货物在场景中的名称。
        """
        world = World.instance()
        object = world.scene.get_object(obj)
        self.loaded_obj = object

        obj_pos, obj_ori = object.get_world_pose()
        obj_pos += 0.015
        self.target_yaw = quat_to_euler_angles(obj_ori)[2]

        create_path = self.create3.prim_path
        usd_joint = UsdPhysics.FixedJoint.Define(get_current_stage(), create_path + "/FixedJoint")
        usd_joint.CreateBody0Rel().SetTargets([Sdf.Path(create_path + "/base_link")])
        usd_joint.CreateBody1Rel().SetTargets([Sdf.Path(object.prim_path)])
        object.set_world_pose(position=obj_pos)
        self.joint_prim = usd_joint

        return True


    def release_pallet(self):
        """
        释放货物（解除绑定）。
        """
        joint_path = self.create3.prim_path + "/FixedJoint"
        stage = get_current_stage() 
        joint_prim = stage.GetPrimAtPath(joint_path)
        if joint_prim.IsValid():
            stage.RemovePrim(joint_path)

        self.loaded_obj = None
        self.target_yaw = None
        self.joint_prim = None
        return True


    def get_world_pose(self):
        """
        获取机器人的世界位姿。

        Returns:
            tuple: (position [x,y,z], orientation [w,x,y,z])
        """
        return self.create3.get_world_pose()
    

    def set_world_pose(self, position=None, orientation=None):
        """
        设置机器人的世界位姿。

        Args:
            position (list[float], optional): [x, y, z].
            orientation (list[float], optional): [w, x, y, z].
        """
        self.create3.set_world_pose(position=position, orientation=orientation)
    