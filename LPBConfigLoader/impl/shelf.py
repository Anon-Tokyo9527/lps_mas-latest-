import numpy as np
from pathlib import Path

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import quat_to_rot_matrix, euler_angles_to_quat
from isaacsim.core.prims import SingleRigidPrim
from isaacsim.core.api.world import World

class Shelf:
    def __init__(self, name, position, unload_offset, orientation=None, scale=None):
        """
        初始化货架实例。该类用于在 Isaac Sim 仿真场景中管理一个带有固定卸货点的货架。
        
        它负责加载货架资产、计算世界坐标系下的卸货点位置，并管理货物的生成、装货、卸货等逻辑。

        大小约为 [1.1, 17.5, 6]
        Args:
            name (str): 货架在场景中的唯一名称，也将用于生成 Prim 路径。
            position (list[float]): 货架在世界坐标系中的位置 [x, y, z]。
            unload_offset (list[float]): 卸货点相对于货架中心的偏移量 [x, y, z]。
            orientation (list[float], optional): 货架的旋转角度（欧拉角 [x, y, z]，单位：度）。默认为 None。
            scale (list[float], optional): 货架的缩放比例 [x, y, z]。默认为 None。
        """
        prim_path = f"/World/{name}"
        extension_root = Path(__file__).resolve().parents[2]
        usd_path = extension_root / "data" / "Assets" / "shelf" / "shelf.usd"


        add_reference_to_stage(usd_path.as_posix(), prim_path)
        
        if orientation is not None:
            orientation = euler_angles_to_quat(np.array(orientation), degrees=True)

        self.shelf = SingleRigidPrim(
            prim_path=prim_path,
            name=name,
            position=position,
            orientation=orientation,
            scale=scale
        )

        world = World.instance()
        world.scene.add(self.shelf)

        self.position, self.orientation = self.shelf.get_world_pose()

        # === 计算卸货点的世界坐标 ===
        self.unload_offset = np.array(unload_offset)
        # 将四元数转为 3x3 旋转矩阵
        rot_matrix = quat_to_rot_matrix(self.orientation)
        # 坐标变换公式：P_world = P_shelf + (R_shelf @ P_offset)
        # 将相对偏移量旋转到世界坐标系方向
        rotated_offset = rot_matrix @ self.unload_offset
        # 加上货架的世界坐标，得到卸货点的绝对世界坐标
        self.unload_world_pos = self.position + rotated_offset
        
        print(f"[{name}] Unload point set at world pos: {self.unload_world_pos}")

        self.cargos = {}
        self.backup = {}


    def spawn_cargo(self, cargo_name, relative_pos, scale, mass=None):
        """
        在货架的指定相对位置生成一个货物（箱子）。

        Args:
            cargo_name (str): 货物名称（需唯一）。
            relative_pos (list[float]): 货物相对于货架的位置 [x, y, z]。
            scale (list[float]): 货物的缩放比例 [x, y, z]。
            mass (float, optional): 货物的质量。默认为 None。
        """
        if cargo_name in self.cargos:
            print(f"Warning: Cargo {cargo_name} already exists.")
            return

        usd_path = "D:/Development/simulation_assets/Box.usd"
        cargo_prim_path = f"/World/{cargo_name}"
        
        # === 计算货物的世界坐标 ===
        # 逻辑与计算卸货点相同：将相对坐标转换为世界坐标
        rel_pos = np.array(relative_pos)
        rot_matrix = quat_to_rot_matrix(self.orientation)
        rotated_rel_pos = rot_matrix @ rel_pos
        world_pos = self.position + rotated_rel_pos
        
        add_reference_to_stage(usd_path, cargo_prim_path)
        
        cargo = SingleRigidPrim(
            prim_path=cargo_prim_path,
            name=cargo_name,
            position=world_pos,
            orientation=self.orientation,
            scale=scale,
            mass=mass
        )
        
        world = World.instance()
        world.scene.add(cargo)
        
        self.cargos[cargo_name] = cargo
        self.backup[cargo_name] = cargo
        print(f"Spawned cargo: {cargo_name} at {world_pos}")


    def create_cargo(self, cargo_data):
        """
        根据数据字典解析参数并生成货物。通常用于从配置读取数据。

        Args:
            cargo_data (dict): 包含货物信息的字典。
        """
        cargo_original_size = (1.0, 1.0, 1.0)

        name = cargo_data.get('name')
        dims = cargo_data.get('dimensions')
        pos = cargo_data['position']
        
        position = [float(pos['x']), float(pos['y']), float(pos['z'])]
        
        desired_length = float(dims.get('length', 0.2))
        desired_width = float(dims.get('width', 0.2))
        desired_height = float(dims.get('height', 0.6))
        
        scale = [
            desired_length / cargo_original_size[0],
            desired_width / cargo_original_size[1],
            desired_height / cargo_original_size[2]
        ]

        self.spawn_cargo(
            cargo_name=name,
            relative_pos=position,
            scale=scale,
            mass=None
        )

    def unload_cargo(self, cargo_name):
        """
        将指定货物从货架瞬移到卸货点。

        Args:
            cargo_name (str): 要卸载的货物名称。

        Returns:
            bool: 成功返回 True，失败返回 False。
        """
        if cargo_name not in self.cargos:
            print(f"Error: Cargo {cargo_name} not found on shelf.")
            return False

        cargo_obj = self.cargos[cargo_name]
        size = cargo_obj.get_local_scale()
        safe_drop_pos = self.unload_world_pos + np.array([0, 0, 0])

        cargo_obj.set_world_pose(position=safe_drop_pos, orientation=self.orientation)

        del self.cargos[cargo_name]
        
        print(f"Unloaded {cargo_name} to {safe_drop_pos}")
        return True


    def load_cargo(self, cargo_name, target_relative_pos, threshold=0.4):
        """
        将货物从卸货点装回货架。会检查货物是否在卸货点附近。

        Args:
            cargo_name (str): 货物名称。
            target_relative_pos (list[float]): 货架上的目标相对位置 [x, y, z]。
            threshold (float, optional): 允许装货的最大距离阈值（米）。默认为 0.4。

        Returns:
            bool: 成功返回 True，失败返回 False。
        """
        world = World.instance()
        cargo_obj = world.scene.get_object(cargo_name)
        
        if cargo_obj is None:
            print(f"Error: Cargo '{cargo_name}' not found in the scene.")
            return False
        
        if cargo_name in self.cargos:
            print(f"Error: Cargo {cargo_name} is already loaded on the shelf.")
            return False

        # 距离校验：确保货物确实在卸货点附近
        current_pos, _ = cargo_obj.get_world_pose()
        distance = np.linalg.norm(current_pos - self.unload_world_pos)
        
        if distance > threshold:
            print(f"Refused to load: '{cargo_name}' is too far from unload point. "
                  f"Distance: {distance:.2f}m > Threshold: {threshold}m")
            return False

        # 计算货架上的目标世界坐标
        rel_pos = np.array(target_relative_pos)
        rot_matrix = quat_to_rot_matrix(self.orientation)
        rotated_rel_pos = rot_matrix @ rel_pos
        world_target_pos = self.position + rotated_rel_pos

        cargo_obj.set_world_pose(position=world_target_pos, orientation=self.orientation)
        
        self.cargos[cargo_name] = cargo_obj
        
        print(f"Loaded '{cargo_name}' from unloading point to shelf at {target_relative_pos}")
        return True


    def reset(self):
        """
        重置货架状态。
        注意：当前实现仅重置了字典引用，并未物理重置物体位置。
        需在外部配合 Simulation Context 的 reset 。
        """
        self.cargos = self.backup.copy()
    
    
    def get_unload_position(self):
        """
        获取卸货点的世界绝对坐标。

        Returns:
            np.array: [x, y, z] 坐标。
        """
        return self.unload_world_pos
    

    def get_world_pose(self):
        """
        获取货架自身的世界位姿。

        Returns:
            tuple: (position [x,y,z], orientation [w,x,y,z])
        """
        return self.shelf.get_world_pose()
    

    def set_world_pose(self, position=None, orientation=None):
        """
        设置货架的世界位姿。

        Args:
            position (list[float], optional): [x, y, z].
            orientation (list[float], optional): [w, x, y, z].
        """
        self.shelf.set_world_pose(position=position, orientation=orientation)