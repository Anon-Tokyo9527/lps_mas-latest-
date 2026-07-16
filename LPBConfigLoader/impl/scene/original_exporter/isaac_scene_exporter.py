"""
Isaac Sim 场景构建脚本（全新重写版）

特点：
- 不修改 isaac_sim_script_editor.py（你可以继续保留旧脚本）
- 优先使用 func/usd + func 里的类（Shelf / Manipulator / Transporter）
- func/usd 里没有的资产，自动回退用你原来的 assets/
- 解决 Script Editor 里反复运行脚本导致的 "name not unique"：
  - 运行前彻底重置 World 实例
"""

import json
import math
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

import omni.usd
import carb
from pxr import Usd, UsdGeom, Gf, UsdLux

from omni.isaac.core.utils.prims import create_prim
from omni.isaac.core.utils.viewports import set_camera_view
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleGeometryPrim
from isaacsim.core.api.world import World

# ===== 你只需要改这里（可选）=====
# 推荐：不改代码，直接在系统环境变量里设置：
#   SCENE_BASE_DIR=C:/Users/ASUS/Desktop/scene
#
# 如果不设置，会自动尝试从当前工作目录向上寻找包含 `assets/` 与 `warehouse_scenes/` 的项目根目录。
def _detect_base_dir() -> str:
    def _is_project_root(p: Path) -> bool:
        return (p / "assets").exists() and (p / "warehouse_scenes").exists()

    # 1) 优先使用环境变量（最稳）
    env = os.getenv("SCENE_BASE_DIR") or os.getenv("ISAAC_SCENE_BASE_DIR")
    if env:
        try:
            p = Path(env).expanduser().resolve()
            if _is_project_root(p):
                return str(p)
        except Exception:
            pass

    # 2) 如果脚本真实在项目根目录运行（而不是被 Script Editor 复制到 Temp），可以直接用 __file__ 父目录
    try:
        p = Path(__file__).resolve().parent
        if _is_project_root(p):
            return str(p)
    except Exception:
        pass

    # 3) 常见位置快速探测（Windows：桌面项目）
    try:
        home = Path.home().resolve()
        common = [
            home / "Desktop" / "scene",
            home / "Documents" / "scene",
            home / "scene",
        ]
        for p in common:
            if _is_project_root(p):
                return str(p.resolve())
    except Exception:
        pass

    # 4) 回退：从 cwd 向上寻找项目根（在某些启动方式下可行）
    cwd = Path(os.getcwd()).resolve()
    for p in [cwd] + list(cwd.parents):
        if _is_project_root(p):
            return str(p)

    # 5) 最后兜底：返回 cwd
    return str(cwd)

BASE_DIR = _detect_base_dir().replace("\\", "/")
JSON_FILE = "warehouse_scenes/dual_channel_layout.json"
#JSON_FILE = "warehouse_scenes/center_island_layout.json"
#JSON_FILE = "warehouse_scenes/symmetric_layout.json"
#JSON_FILE = "warehouse_scenes/l_shape_layout.json"
#JSON_FILE = "warehouse_scenes/original_layout.json"
# ==========================

# 让 Script Editor 能 import 本项目
if BASE_DIR:
    # 必须插到最前面，避免被其他同名包(如某些环境里的 func)抢先解析
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

    # Script Editor 反复运行时，sys.modules 里可能缓存了别的 func 包；强制清理后再导入
    try:
        # 清理 func 以及其子模块，避免导入到错误的同名包
        for k in list(sys.modules.keys()):
            if k == "func" or k.startswith("func."):
                del sys.modules[k]
    except Exception:
        pass
    try:
        import importlib
        importlib.invalidate_caches()
    except Exception:
        pass

# 强制重新加载 func 模块（避免 Script Editor 缓存旧版本）
import importlib
import func.shelf
import func.manipulator
import func.new_transporter
import func.ridgeback_base
import func.static_asset
importlib.reload(func.shelf)
importlib.reload(func.manipulator)
importlib.reload(func.new_transporter)
importlib.reload(func.ridgeback_base)
importlib.reload(func.static_asset)

from func.shelf import Shelf
from func.manipulator import Manipulator
from func.new_transporter import Transporter
from func.ridgeback_base import RidgebackBase
from func.static_asset import StaticAsset

# ===== 是否强制使用 func 里的类创建对象 =====
# - True：只要 func 里有对应类，就优先用（失败则回退到静态 USD 方式，保证场景能起来）
# - False：保留旧行为（大部分用 add_to_scene_safe 直接加 USD）
USE_FUNC_CLASSES_WHERE_POSSIBLE = True


# Script Editor 里方便查看：保存本次创建出来的对象（prim/类实例）
LAST_SCENE_OBJECTS = {}


def _abs(p: str) -> str:
    return os.path.abspath(p).replace("\\", "/")


def _asset(*rel_parts: str) -> str:
    """
    查找资产文件，优先级：
    1. func/<资源名>/<资源名>.usd（包含完整材质的收集版本）
    2. func/usd（旧版本）
    3. assets
    """
    filename = rel_parts[-1] if rel_parts else ""
    name_without_ext = filename.rsplit(".", 1)[0] if "." in filename else filename
    
    # 优先级1：func/<资源名>/<资源名>.usd（收集的完整资源，包含材质）
    collected = Path(BASE_DIR) / "func" / name_without_ext / filename
    if collected.exists():
        print(f"  📁 使用收集的资源（含材质）: {collected.as_posix()}")
        return collected.as_posix()
    
    # 优先级2：func/usd（旧路径）
    func_usd = Path(BASE_DIR) / "func" / "usd" / Path(*rel_parts)
    if func_usd.exists():
        return func_usd.as_posix()
    
    # 优先级3：assets
    assets = Path(BASE_DIR) / "assets" / Path(*rel_parts)
    return assets.as_posix()


def _get_usd_bbox_raw(usd_path: str):
    """
    读取 USD 资产的原始 bbox 尺寸 (x,y,z)，不做单位转换。
    
    返回的是 USD 文件内部的原始数值，用于计算 scale。
    当 scale 应用到场景时：最终大小 = raw_bbox × scale
    """
    try:
        stage = Usd.Stage.Open(_abs(usd_path))
        if not stage:
            return None
        
        root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        bbox = bbox_cache.ComputeWorldBound(root)
        r = bbox.ComputeAlignedRange()
        s = r.GetSize()
        
        raw_size = (float(s[0]), float(s[1]), float(s[2]))
        
        print(f"  📐 USD bbox: raw={raw_size[0]:.2f}×{raw_size[1]:.2f}×{raw_size[2]:.2f}")
        
        return raw_size
    except Exception as e:
        print(f"  ⚠️ 获取 USD bbox 失败: {e}")
        return None


def _scale_from_bbox(usd_path: str, desired_xyz):
    """
    独立缩放 X/Y/Z（可能导致变形）。
    
    scale = desired_size / raw_bbox
    当 scale 应用后：最终大小 = raw_bbox × scale = desired_size
    """
    orig = _get_usd_bbox_raw(usd_path)
    if not orig:
        return None
    ox, oy, oz = orig
    if ox == 0 or oy == 0 or oz == 0:
        return None
    dx, dy, dz = desired_xyz
    scale = [dx / ox, dy / oy, dz / oz]
    print(f"    → scale = [{scale[0]:.4f}, {scale[1]:.4f}, {scale[2]:.4f}]")
    return scale


@lru_cache(maxsize=256)
def _get_usd_bbox_range_raw(usd_path: str):
    """返回 USD 的 AABB min/max（在该 USD 文件的 world space；通常等价于资产局部坐标）。"""
    try:
        stage = Usd.Stage.Open(_abs(usd_path))
        if not stage:
            return None
        root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        bbox = bbox_cache.ComputeWorldBound(root)
        r = bbox.ComputeAlignedRange()
        mn = r.GetMin()
        mx = r.GetMax()
        return (float(mn[0]), float(mn[1]), float(mn[2])), (float(mx[0]), float(mx[1]), float(mx[2]))
    except Exception:
        return None


def _rotate_z_deg(v, yaw_deg: float):
    yaw = math.radians(float(yaw_deg))
    c = math.cos(yaw)
    s = math.sin(yaw)
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    return [c * x - s * y, s * x + c * y, z]


def _apply_bottom_center_pivot(desired_position, usd_path: str, scale, yaw_deg: float):
    """把 JSON 的 position 解释为“资产底部中心”，并返回需要传给创建接口的修正后 position。"""
    r = _get_usd_bbox_range_raw(usd_path)
    if not r:
        return desired_position
    (minx, miny, minz), (maxx, maxy, maxz) = r
    pivot_local = [(minx + maxx) / 2.0, (miny + maxy) / 2.0, minz]
    sx, sy, sz = 1.0, 1.0, 1.0
    if scale and len(scale) == 3:
        sx, sy, sz = float(scale[0]), float(scale[1]), float(scale[2])
    pivot_scaled = [pivot_local[0] * sx, pivot_local[1] * sy, pivot_local[2] * sz]
    pivot_rot = _rotate_z_deg(pivot_scaled, yaw_deg)
    return [
        float(desired_position[0]) - pivot_rot[0],
        float(desired_position[1]) - pivot_rot[1],
        float(desired_position[2]) - pivot_rot[2],
    ]


def _uniform_scale_from_bbox(usd_path: str, target_length: float, fit_axis: str = "length"):
    """
    等比缩放：保持原始长宽高比例，只根据目标长度/宽度计算统一缩放因子。
    
    Args:
        usd_path: USD 文件路径
        target_length: 目标尺寸（米）
        fit_axis: "length" 表示按 X 轴适配，"width" 表示按 Y 轴适配
    
    Returns:
        (uniform_scale, actual_size) 或 (None, None)
        uniform_scale: [s, s, s] 统一缩放
        actual_size: (实际长, 实际宽, 实际高) 缩放后的尺寸（米）
    """
    orig = _get_usd_bbox_raw(usd_path)
    if not orig:
        return None, None
    
    ox, oy, oz = orig
    if ox == 0 or oy == 0 or oz == 0:
        return None, None
    
    # scale = target / raw_bbox
    # 当 scale 应用后：最终大小 = raw_bbox × scale = target
    if fit_axis == "length":
        scale_factor = target_length / ox
    else:  # width
        scale_factor = target_length / oy
    
    # 计算缩放后的实际尺寸
    actual_size = (target_length if fit_axis == "length" else ox * scale_factor,
                   target_length if fit_axis == "width" else oy * scale_factor,
                   oz * scale_factor)
    
    print(f"    → scale = {scale_factor:.4f}, 实际尺寸 = {actual_size[0]:.2f}×{actual_size[1]:.2f}×{actual_size[2]:.2f}m")
    
    return [scale_factor, scale_factor, scale_factor], actual_size


def _normalize_deg(d: float) -> float:
    """把角度规范到 [-180, 180]，便于调试打印。"""
    d = float(d)
    while d > 180:
        d -= 360
    while d < -180:
        d += 360
    return d


def _base_rot_deg_align_long_to_x(usd_path: str) -> float:
    """
    资产通常有一个“长边轴”。我们希望在 rotation=0 时，长边沿世界 X。
    - 若 bbox 的 Y > X：认为长边在本地 Y，需要绕 Z 旋转 -90°，使本地 Y → 世界 X
    - 否则：长边已在本地 X，不需要基准旋转
    """
    bbox = _get_usd_bbox_raw(usd_path)
    if not bbox:
        return 0.0
    ox, oy, _ = bbox
    return -90.0 if oy > ox else 0.0


def _scale_for_length_width_height_aligned_to_x(usd_path: str, length: float, width: float, height: float, base_rot_deg: float):
    """
    计算缩放，使得“最终世界坐标系下”的 (length,width,height) 对齐到 (X,Y,Z)。
    注意：scale 在本地坐标系生效；若 base_rot_deg=-90，意味着本地Y将对齐世界X、本地X将对齐世界Y。
    """
    base_rot_deg = _normalize_deg(base_rot_deg)
    if abs(base_rot_deg + 90.0) < 1e-6:  # -90
        desired_local = (width, length, height)  # local X->world Y, local Y->world X
    else:
        desired_local = (length, width, height)  # local X->world X, local Y->world Y
    return _scale_from_bbox(usd_path, desired_local)


def _uniform_scale_to_diameter_xy(usd_path: str, diameter: float):
    """把资产在 XY 平面的最大尺寸等比缩放到 diameter（用于小车）。"""
    bbox = _get_usd_bbox_raw(usd_path)
    if not bbox:
        return None
    ox, oy, _ = bbox
    ref = max(float(ox), float(oy))
    if ref == 0:
        return None
    s = float(diameter) / ref
    return [s, s, s]


def _stage_remove_prim(prim_path: str):
    try:
        stage = omni.usd.get_context().get_stage()
        if stage and stage.GetPrimAtPath(prim_path):
            stage.RemovePrim(prim_path)
    except Exception:
        pass


def reset_world_completely():
    """
    彻底重置 World 实例和 /World prim。
    
    关键：World.instance() 返回的是单例，其 scene 内部缓存了所有添加过的对象名。
    仅删 prim 不够，必须清空 scene 或重建 World。
    """
    # 1. 删除 stage 上的 /World prim（彻底清除几何数据）
    _stage_remove_prim("/World")
    
    # 2. 清空 World 单例（强制下次创建新的）
    try:
        world = World.instance()
        if world is not None:
            # 尝试调用 clear() 清除 scene 内注册的所有对象
            if hasattr(world, "clear"):
                world.clear()
            # 有些版本用 reset_scene
            elif hasattr(world.scene, "clear"):
                world.scene.clear()
    except Exception:
        pass
    
    # 3. 尝试销毁单例（确保下次 World() 创建全新实例）
    try:
        World.clear_instance()
    except Exception:
        pass
    
    LAST_SCENE_OBJECTS.clear()
    print("✓ World 已重置")


def add_to_scene_safe(usd_path: str, name: str, position, scale=None, orientation=None, orientation_euler_deg=None):
    """
    统一的"加到场景"函数：
    - 假设 World 已通过 reset_world_completely() 重置
    - 返回 SingleGeometryPrim（可 get_world_pose / get_local_scale）
    """
    prim_path = f"/World/{name}"

    add_reference_to_stage(_abs(usd_path), prim_path)

    # 兼容两种参数名：
    # - orientation: 期望是欧拉角 [x,y,z]（度）
    # - orientation_euler_deg: 同上（旧参数名）
    if orientation is None and orientation_euler_deg is not None:
        orientation = orientation_euler_deg

    quat = None
    if orientation is not None:
        quat = euler_angles_to_quat(np.array(orientation), degrees=True)

    prim = SingleGeometryPrim(
        prim_path=prim_path,
        name=name,
        position=position,
        orientation=quat,
        scale=scale,
    )

    world = World.instance()
    world.scene.add(prim)

    LAST_SCENE_OBJECTS[name] = prim
    return prim


def create_lights():
    distant_light = create_prim("/World/DistantLight", "DistantLight")
    light = UsdLux.DistantLight(distant_light)
    light.CreateIntensityAttr(3000)
    light.CreateAngleAttr(0.53)

    xformable = UsdGeom.Xformable(distant_light)
    rotate_op = xformable.AddRotateXYZOp()
    rotate_op.Set(Gf.Vec3f(-45, 45, 0))

    dome_light = create_prim("/World/DomeLight", "DomeLight")
    dome = UsdLux.DomeLight(dome_light)
    dome.CreateIntensityAttr(1000)


def create_ground(scene_data):
    dims = scene_data.get("warehouse", {}).get("dimensions", {})
    length = float(dims.get("length", 15))
    width = float(dims.get("width", 15))

    position = Gf.Vec3d(length / 2, width / 2, -0.05)
    size = Gf.Vec3d(length, width, 0.1)
    color = Gf.Vec3f(0.3, 0.4, 0.5)

    cube_prim = create_prim("/World/Ground", "Cube")
    cube = UsdGeom.Cube(cube_prim)
    cube.GetSizeAttr().Set(1.0)
    
    xformable = UsdGeom.Xformable(cube_prim)
    xformable.ClearXformOpOrder()
    # 使用 double 精度以避免 precision mismatch 警告
    xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(position)
    xformable.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(size)
    cube.GetDisplayColorAttr().Set([color])


def setup_camera(scene_data):
    dims = scene_data.get("warehouse", {}).get("dimensions", {})
    length = float(dims.get("length", 40))
    width = float(dims.get("width", 40))
    center_x = length / 2
    center_y = width / 2
    # 相机高度和距离根据场景大小动态调整
    cam_distance = max(length, width) * 0.8
    cam_height = max(length, width) * 0.5
    eye = Gf.Vec3d(center_x + cam_distance, center_y - cam_distance * 0.5, cam_height)
    target = Gf.Vec3d(center_x, center_y, 0)
    set_camera_view(eye=eye, target=target, camera_prim_path="/OmniverseKit_Persp")


def build_scene():
    # 彻底重置 World 和 /World prim
    reset_world_completely()
    
    # 确保有新的 World 实例
    world = World.instance()
    if world is None:
        world = World()

    json_path = _abs(os.path.join(BASE_DIR, JSON_FILE) if not os.path.isabs(JSON_FILE) else JSON_FILE)
    if not os.path.exists(json_path):
        raise FileNotFoundError(json_path)

    with open(json_path, "r", encoding="utf-8") as f:
        scene_data = json.load(f)

    # 创建 /World
    create_prim("/World", "Xform")
    create_lights()
    create_ground(scene_data)

    objects = scene_data.get("objects", [])
    shelves_by_name = {}

    # ===== objects =====
    for obj in objects:
        t = obj.get("type")
        name = obj.get("name")

        if t == "shelf":
            dims = obj["dimensions"]
            pos = obj["position"]
            rot = float(obj.get("rotation", 0))
            position = [float(pos["x"]), float(pos["y"]), 0.0]

            shelf_usd = _asset("shelf.usd")
            
            # 货架 USD bbox: raw=1.38×17.62×6.00 (X×Y×Z)
            # USD 资产本身：X=宽度(1.38), Y=长度(17.62), Z=高度(6.00)
            # 
            # JSON 定义: length=16, width=1, height=6 (新的16m长货架)
            # 未旋转(rot=0): 货架长度沿 X 轴
            # 旋转90度(rot=90): 货架长度沿 Y 轴
            
            target_length = float(dims["length"])  # 16m (从JSON读取)
            target_width = float(dims["width"])    # 1m  
            target_height = float(dims["height"])  # 6m
            
            # 关键：shelf.usd 的长边在本地 Y。我们希望 rotation=0 时“长度沿世界 X”。
            base_rot = -90.0  # 固定基准旋转：local Y -> world X
            final_rot = _normalize_deg(base_rot + rot)

            # 缩放仍按“本地轴”映射：local X=宽，local Y=长，local Z=高
            scale = _scale_from_bbox(shelf_usd, (target_width, target_length, target_height))
            
            if scale:
                # 验证缩放后的尺寸
                bbox = _get_usd_bbox_raw(shelf_usd)
                if bbox:
                    actual = [bbox[0]*scale[0], bbox[1]*scale[1], bbox[2]*scale[2]]
                    print(f"📦 货架 {name}: 目标={target_width}(宽)×{target_length}(长)×{target_height}(高)m, "
                          f"rot={rot}°, base={base_rot}°, final={final_rot}°, 缩放后实际={actual[0]:.2f}×{actual[1]:.2f}×{actual[2]:.2f}m")
            
            # 卸货点偏移量（沿资产本地 Y 的负方向，即“长度方向的一端”）
            # 让卸货点接近货架一端：-(L/2 - 0.5)
            unload_offset = [0.0, -(target_length / 2.0 - 0.5), 0.0]

            # 关键修正：JSON position 约定为“底部中心”，但 USD 资产原点可能不在底部中心
            # 因此需要根据 bbox 的 bottom-center 做一次反向偏移（包含缩放与旋转）
            position = _apply_bottom_center_pivot(position, shelf_usd, scale, final_rot)

            shelf = Shelf(
                name=name,
                position=position,
                unload_offset=unload_offset,
                orientation=[0, 0, final_rot],
                scale=scale,
            )
            shelves_by_name[name] = shelf
            LAST_SCENE_OBJECTS[name] = shelf
            
            # 处理货架上的箱子（children）
            for child in obj.get("children", []):
                if child.get("type") == "box":
                    child_name = child.get("name")
                    child_dims = child.get("dimensions", {})
                    child_pos = child["position"]
                    child_rot = float(child.get("rotation", 0))
                    child_desired = (
                        float(child_dims.get("length", 0.3)),
                        float(child_dims.get("width", 0.3)),
                        float(child_dims.get("height", 0.5)),
                    )
                    box_usd = _asset("Box.usd")
                    child_position = [float(child_pos["x"]), float(child_pos["y"]), float(child_pos["z"])]
                    child_scale = _scale_from_bbox(box_usd, child_desired)
                    if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                        child_obj = StaticAsset(
                            name=child_name,
                            usd_path=box_usd,
                            position=child_position,
                            scale=child_scale,
                            orientation_euler_deg=[0, 0, child_rot],
                        )
                        LAST_SCENE_OBJECTS[child_name] = child_obj
                    else:
                        child_prim = add_to_scene_safe(box_usd, child_name, child_position, scale=child_scale, orientation=[0, 0, child_rot])
                        LAST_SCENE_OBJECTS[child_name] = child_prim

        elif t == "conveyor":
            # func/usd 没有 conveyor，回退你的原资产
            dims = obj["dimensions"]
            pos = obj["position"]
            length = float(dims["length"])
            width = float(dims["width"])
            height = float(dims["height"])
            conveyor_usd = _asset("ConveyorBelt_A05.usd")

            # 关键：统一约定 JSON 的 position 是“中心点”
            position = [float(pos["x"]), float(pos["y"]), 0.0]

            # 自动把传送带“长边”对齐世界 X（避免看起来竖着/步进方向错）
            base_rot = _base_rot_deg_align_long_to_x(conveyor_usd)
            scale = _scale_for_length_width_height_aligned_to_x(conveyor_usd, length, width, height, base_rot)
            orientation = [0, 0, base_rot]

            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                obj = StaticAsset(
                    name=name,
                    usd_path=conveyor_usd,
                    position=position,
                    scale=scale,
                    orientation_euler_deg=orientation,
                )
                LAST_SCENE_OBJECTS[name] = obj
            else:
                prim = add_to_scene_safe(conveyor_usd, name, position, scale=scale, orientation=orientation)
                LAST_SCENE_OBJECTS[name] = prim

        elif t == "packing_table":
            # func/usd 没有 table，回退你的原资产
            dims = obj["dimensions"]
            pos = obj["position"]
            length = float(dims["length"])
            width = float(dims["width"])
            height = float(dims["height"])
            table_usd = _asset("table.usd")

            position = [float(pos["x"]), float(pos["y"]), 0.0]
            scale = _scale_from_bbox(table_usd, (length, width, height))
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                obj = StaticAsset(
                    name=name,
                    usd_path=table_usd,
                    position=position,
                    scale=scale,
                )
                LAST_SCENE_OBJECTS[name] = obj
            else:
                prim = add_to_scene_safe(table_usd, name, position, scale=scale)
                LAST_SCENE_OBJECTS[name] = prim

        elif t == "crate":
            dims = obj.get("dimensions", {})
            pos = obj["position"]
            desired = (
                float(dims.get("length", 1.2)),
                float(dims.get("width", 1.0)),
                float(dims.get("height", 0.3)),
            )
            # func/usd 有 pallet.usd（优先用它），否则回退 container.usd
            pallet_usd = _asset("pallet.usd")
            position = [float(pos["x"]), float(pos["y"]), float(pos["z"])]
            scale = _scale_from_bbox(pallet_usd, desired)
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                crate_obj = StaticAsset(
                    name=name,
                    usd_path=pallet_usd,
                    position=position,
                    scale=scale,
                )
                LAST_SCENE_OBJECTS[name] = crate_obj
            else:
                prim = add_to_scene_safe(pallet_usd, name, position, scale=scale)
                LAST_SCENE_OBJECTS[name] = prim
            
            # 处理托盘上的箱子（children）—— 注意：obj 仍是原始 JSON dict
            for child in obj.get("children", []):
                if child.get("type") == "box":
                    child_name = child.get("name")
                    child_dims = child.get("dimensions", {})
                    child_pos = child["position"]
                    child_rot = float(child.get("rotation", 0))
                    child_desired = (
                        float(child_dims.get("length", 0.3)),
                        float(child_dims.get("width", 0.3)),
                        float(child_dims.get("height", 0.5)),
                    )
                    box_usd = _asset("Box.usd")
                    child_position = [float(child_pos["x"]), float(child_pos["y"]), float(child_pos["z"])]
                    child_scale = _scale_from_bbox(box_usd, child_desired)
                    if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                        child_obj = StaticAsset(
                            name=child_name,
                            usd_path=box_usd,
                            position=child_position,
                            scale=child_scale,
                            orientation_euler_deg=[0, 0, child_rot],
                        )
                        LAST_SCENE_OBJECTS[child_name] = child_obj
                    else:
                        child_prim = add_to_scene_safe(box_usd, child_name, child_position, scale=child_scale, orientation=[0, 0, child_rot])
                        LAST_SCENE_OBJECTS[child_name] = child_prim

        elif t == "box":
            dims = obj.get("dimensions", {})
            pos = obj["position"]
            rot = float(obj.get("rotation", 0))
            desired = (
                float(dims.get("length", 0.3)),
                float(dims.get("width", 0.3)),
                float(dims.get("height", 0.5)),
            )
            box_usd = _asset("Box.usd")
            position = [float(pos["x"]), float(pos["y"]), float(pos["z"])]
            scale = _scale_from_bbox(box_usd, desired)
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                obj = StaticAsset(
                    name=name,
                    usd_path=box_usd,
                    position=position,
                    scale=scale,
                    orientation_euler_deg=[0, 0, rot],
                )
                LAST_SCENE_OBJECTS[name] = obj
            else:
                prim = add_to_scene_safe(box_usd, name, position, scale=scale, orientation=[0, 0, rot])
                LAST_SCENE_OBJECTS[name] = prim

        elif t == "collection_area":
            # 仍用简单 Cube（这类 func 没提供资产/类）
            dims = obj["dimensions"]
            pos = obj["position"]
            position = [
                float(pos["x"]) + float(dims["length"]) / 2,
                float(pos["y"]) + float(dims["width"]) / 2,
                0.01,
            ]
            size = [float(dims["length"]), float(dims["width"]), 0.02]
            color = [0.9, 0.9, 0.1]

            prim_path = f"/World/CollectionAreas/{name}"
            _stage_remove_prim(prim_path)
            cube_prim = create_prim(prim_path, "Cube")
            cube = UsdGeom.Cube(cube_prim)
            cube.GetSizeAttr().Set(1.0)
            xformable = UsdGeom.Xformable(cube_prim)
            xformable.ClearXformOpOrder()
            xformable.AddTranslateOp().Set(Gf.Vec3d(*position))
            xformable.AddScaleOp().Set(Gf.Vec3d(*size))
            cube.GetDisplayColorAttr().Set([Gf.Vec3f(*color)])

    # ===== agents =====
    for agent in scene_data.get("agents", []):
        t = agent.get("type")
        name = agent.get("name")
        pos = agent.get("position", {})
        on_table = bool(agent.get("on_table", False))

        if t == "agv":
            # AGV：优先用 func 里的类（Transporter / RidgebackBase），失败再回退为静态 USD
            position = [float(pos["x"]), float(pos["y"]), float(pos.get("z", 0.0)) + 0.0]
            model = (agent.get("model") or "create3").lower()
            rot = float(agent.get("rotation", 0.0))
            dims = agent.get("dimensions", {}) or {}
            diameter = float(dims.get("diameter", 0.8))

            if model in ("ridgeback", "rb"):
                usd = _asset("ridgeback.usd")
            else:
                usd = _asset("create_3.usd")

            base_rot = _base_rot_deg_align_long_to_x(usd)
            scale = _uniform_scale_to_diameter_xy(usd, diameter)
            if scale is None:
                scale = [1.0, 1.0, 1.0]
            final_rot = _normalize_deg(base_rot + rot)

            print(f"🚗 AGV spawn: name={name}, model={model}, usd={usd}")
            print(f"    pos={position}, diameter={diameter}, base_rot={base_rot}, rot={rot}, final_rot={final_rot}, scale={scale}")

            agv_obj = None
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                try:
                    if model in ("ridgeback", "rb"):
                        agv_obj = RidgebackBase(name=name, position=position, orientation=[0, 0, final_rot], scale=scale)
                    else:
                        # create3：差速底盘 Transporter（可 move_to）
                        agv_obj = Transporter(name=name, position=position, orientation=[0, 0, final_rot], scale=scale)
                except Exception as e:
                    print(f"  ⚠️ 使用 func AGV 类创建失败，回退到静态 USD: {e}")
                    agv_obj = None

            if agv_obj is None:
                # 回退：静态 USD（有缩放，但无控制逻辑）
                if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                    agv_obj = StaticAsset(
                        name=name,
                        usd_path=usd,
                        position=position,
                        scale=scale,
                        orientation_euler_deg=[0, 0, final_rot],
                    )
                else:
                    agv_obj = add_to_scene_safe(
                        usd_path=usd,
                        name=name,
                        position=position,
                        scale=scale,
                        orientation=[0, 0, final_rot],
                    )
            LAST_SCENE_OBJECTS[name] = agv_obj

            # 强校验：stage 上是否真的有这个 prim，并显式设置可见性
            try:
                stage = omni.usd.get_context().get_stage()
                prim_path = f"/World/{name}"
                prim_obj = stage.GetPrimAtPath(prim_path) if stage else None
                exists = prim_obj and prim_obj.IsValid()
                
                if exists:
                    # 显式设置为可见
                    imageable = UsdGeom.Imageable(prim_obj)
                    if imageable:
                        imageable.MakeVisible()
                        vis = imageable.ComputeVisibility()
                        print(f"    stage prim exists=True, visibility={vis}, path={prim_path}")
                    else:
                        print(f"    stage prim exists=True (not imageable), path={prim_path}")
                else:
                    print(f"    stage prim exists=False, path={prim_path}")
            except Exception as e:
                print(f"    stage prim check/visibility failed: {e}")
        elif t == "robot_arm":
            # 机械臂放在地面
            position = [float(pos["x"]), float(pos["y"]), float(pos.get("z", 0.0))]
            rot = float(agent.get("rotation", 0.0))
            
            print(f"🦾 机械臂: name={name}, pos={position}, rot={rot}")
            try:
                arm = Manipulator(name=name, position=position, orientation=[0, 0, rot])
                LAST_SCENE_OBJECTS[name] = arm
            except Exception as e:
                print(f"  ⚠️ 创建机械臂失败: {e}")

    setup_camera(scene_data)
    carb.log_info(f"Build done. objects={len(LAST_SCENE_OBJECTS)}")


def main():
    build_scene()


if __name__ == "__main__":
    main()
