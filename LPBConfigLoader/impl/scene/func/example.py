from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleGeometryPrim
from isaacsim.core.api.world import World

from .shelf import Shelf
from .shuttle import Shuttle
from .manipulator import Manipulator
from .new_transporter import Transporter

import numpy as np


def add_to_scene(usd_path, name, position, scale=None, orientation=None):
    """
    在指定位置生成指定物体。

    Args:
        usd_path (str): 资产文件路径
        name (str): 名称（需唯一）。
        position (list[float]): 生成位置 [x, y, z]。
        scale (list[float], optional): 缩放比例 [x, y, z]。默认为 None。
        orientation (list[float], optional): 旋转角度（欧拉角 [x, y, z]，单位：度）。默认为 None。
    Returns:
        prim: 返回新创建的 Prim 对象
    """
    
    prim_path = f"/World/{name}"
    
    add_reference_to_stage(usd_path, prim_path)

    if orientation is not None:
        orientation = euler_angles_to_quat(np.array(orientation), degrees=True)
    
    prim = SingleGeometryPrim(
        prim_path=prim_path,
        name=name,
        position=position,
        orientation=orientation,
        scale=scale
    )
    
    world = World.instance()
    world.scene.add(prim)
    
    return prim



# 使用示例
def test():
    # 在场景中添加物体示例
    # 使用项目内的 pallet.usd（优先 func/pallet/pallet.usd，再 func/usd/pallet.usd）
    from pathlib import Path
    pallet_usd = (Path(__file__).resolve().parent / "pallet" / "pallet.usd").as_posix()
    if not os.path.exists(pallet_usd):
        pallet_usd = (Path(__file__).resolve().parent / "usd" / "pallet.usd").as_posix()
    
    pallet = add_to_scene(
            usd_path=pallet_usd,
            name="pallet0",
            position=[3, 0, 0]
            # scale 和 orientation 不指定则为默认值，下面的示例也是这样
        )
    
    # 需要你输出的场景信息中的实时数据（位置，旋转，大小）可以通过下面两句获得
    position, orientation = pallet.get_world_pose()
    scale = pallet.get_local_scale()


    # Manipulator 类使用示例
    ur10 = Manipulator(
        name = "ur10",
        position = [0, 0, 0]
    )
    

    # Shelf 类使用示例
    shelf = Shelf(
        name="shelf",
        position=[-10.0, -5.0, 0.0],
        unload_offset=[0, -8.5, 0], 
        orientation=[0, 0, 90]
    )
    
    # 使用Shelf类生成箱子示例
    box_data = {
        'name': 'box_1',
        'dimensions': {'length': 0.3, 'width': 0.3, 'height': 0.5},
        'position': {'x': 0.0, 'y': -6.0, 'z': 0}
    }
    
    shelf.create_cargo(box_data)


    # Shuttle 类使用示例
    shuttle = Shuttle(
        name = "shuttle",
        position = [0, 3, 0]
    )


    # Transporter 类使用示例
    create3 = Transporter(
        name="create3",
        position = [0, -3, 0]
    )