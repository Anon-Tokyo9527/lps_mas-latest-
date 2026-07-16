import numpy as np
import os
from pathlib import Path

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleGeometryPrim
from isaacsim.core.api.world import World


class StaticAsset:
    """
    通用静态资产封装（把“直接 add_reference + SingleGeometryPrim”包装成 func 里的类）。

    用途：
    - 传送带 / 箱子 / 托盘 / 桌子 等“没有专用控制逻辑”的纯资产
    - 让 exporter 里的创建逻辑统一走 func 类（便于管理、后续扩展）
    """

    def __init__(
        self,
        name: str,
        usd_path: str,
        position=None,
        scale=None,
        orientation_euler_deg=None,
        prim_path: str | None = None,
    ):
        if prim_path is None:
            prim_path = f"/World/{name}"

        # 尽量把相对路径变成绝对路径（在 Script Editor 临时目录执行时更稳）
        resolved_usd = usd_path
        try:
            # 若是相对路径，按当前工程工作目录解析（由上层脚本保障 sys.path/base_dir）
            if not os.path.isabs(resolved_usd):
                resolved_usd = os.path.abspath(resolved_usd)
        except Exception:
            pass

        add_reference_to_stage(resolved_usd, prim_path)

        quat = None
        if orientation_euler_deg is not None:
            quat = euler_angles_to_quat(np.array(orientation_euler_deg), degrees=True)

        self.prim = SingleGeometryPrim(
            prim_path=prim_path,
            name=name,
            position=position,
            orientation=quat,
            scale=scale,
        )

        world = World.instance()
        if world is None:
            world = World()
        world.scene.add(self.prim)

    def get_world_pose(self):
        return self.prim.get_world_pose()

    def set_world_pose(self, position=None, orientation=None):
        return self.prim.set_world_pose(position=position, orientation=orientation)

    def get_local_scale(self):
        return self.prim.get_local_scale()


