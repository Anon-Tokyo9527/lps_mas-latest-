import numpy as np

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.robot.manipulators.examples.universal_robots import UR10
from isaacsim.robot.manipulators.examples.universal_robots.controllers.pick_place_controller import PickPlaceController
from isaacsim.core.api.world import World
from pathlib import Path
import os


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
        usd_path = (Path(__file__).resolve().parent / "usd" / "ur10.usd").as_posix()
        if not os.path.exists(usd_path):
            usd_path = "assets/factory_franka.usd"

        prim_path = f"/World/{name}"

        add_reference_to_stage(usd_path, prim_path)

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
        if world is None:
            world = World()
        world.scene.add(self.ur10)

        # === 配置机械臂参数 ===
        # 设置吸盘检测点的偏移量 (根据吸盘模型长度调整)
        # 注意：不同版本 Isaac Sim 的 gripper API 可能不同
        try:
            if hasattr(self.ur10.gripper, 'set_translate'):
                self.ur10.gripper.set_translate(value=0.162)
            if hasattr(self.ur10.gripper, 'set_direction'):
                self.ur10.gripper.set_direction(value="x")
        except Exception:
            pass  # gripper 配置失败时静默忽略
        
        # 设置机械臂初始姿态
        self.ur10.set_joints_default_state(
            positions=np.array([np.pi, -np.pi / 2, -np.pi / 2, -np.pi / 2, np.pi / 2, 0])
        )

        # 初始化抓取控制器 (PickPlaceController)
        # 不同 Isaac Sim 版本参数不一致：有的版本不支持 end_effector_initial_height
        # 注意：某些版本的 TypeError 可能不被捕获（内部抛出），因此这里做更稳的“签名检测”。
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
            # 最保底：不带该参数
            self.controller = PickPlaceController(
                name="pick_place_controller", 
                gripper=self.ur10.gripper, 
                robot_articulation=self.ur10,
            )


    def reset(self):
        """
        重置控制器的内部状态。
        当开始新的抓取任务序列时调用。
        """
        self.controller.reset()


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
        
        current_joint_positions = self.ur10.get_joint_positions()
        
        self.ur10.apply_action(self.controller.forward(
            picking_position = target_position,
            placing_position = goal_pos,
            current_joint_positions = current_joint_positions,
            end_effector_offset=np.array([0, 0, size[2]]),
            # end_effector_orientation = target_orientation
        ))

        return self.controller.is_done()
    

    def get_world_pose(self):
        """
        获取机械臂基座的世界位姿。

        Returns:
            tuple: (position [x,y,z], orientation [w,x,y,z])
        """
        return self.ur10.get_world_pose()
    
    
    def set_world_pose(self, position=None, orientation=None):
        """
        设置机械臂基座的世界位姿。

        对于固定机械臂不建议调用该函数。

        Args:
            position (list[float], optional): [x, y, z].
            orientation (list[float], optional): [w, x, y, z].
        """
        self.ur10.set_world_pose(position=position, orientation=orientation)
    