"""
Isaac Sim 场景构建脚本（针对 new_scene_to_json 生成的布局）

功能特点：
  ✓ 支持多格数货架（1-4格），自动选择对应的 USD 资产
  ✓ 根据 segments 字段智能加载 func/shelf/{1,2,3,4}_segments.usd
  ✓ 自动计算缩放比例，确保货架尺寸符合 JSON 规格
  ✓ 支持货架、传送带、托盘、箱子、AGV、机械臂等多种对象
  ✓ 彻底的场景重置机制，避免重复运行时的名称冲突

使用方法：
  1. 默认加载: warehouse_scenes/adaptive_layout.json
  2. 自定义布局: 设置环境变量 NEW_SCENE_JSON=warehouse_scenes/mixed_layout.json
  3. 项目路径: 设置环境变量 SCENE_BASE_DIR=C:/Users/ASUS/Desktop/scene

货架 USD 资产选择规则：
  - 1格 (6m):  func/shelf/shelf_1_segments.usd
  - 2格 (10m): func/shelf/shelf_2_segments.usd
  - 3格 (14m): func/shelf/shelf_3_segments.usd
  - 4格 (18m): func/shelf/shelf.usd（默认）

支持的布局文件：
  - adaptive_layout.json:         自适应格数布局（智能优化空间利用率）
  - mixed_layout.json:            混合格数布局（随机混合1-4格货架）
  - dual_channel_adaptive.json:   双通道自适应布局（左右两列，中间通道）
  - grid_mixed.json:              网格混合布局（不同区域不同格数）
  - greedy_column_layout.json:    贪心列布局（原始朝向，一列一列放）
  - greedy_row_layout.json:       贪心行布局（旋转90度，一行一行放）
  - four_zone_layout.json:        四区域布局（按比例分配4个模块，每个模块不同格数）
  - four_zone_random_layout.json: 随机四区域布局（随机货架类型、区域大小、放置方向）
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
from pxr import Usd, UsdGeom, Gf, UsdLux, UsdPhysics, Vt

from omni.isaac.core.utils.prims import create_prim
from omni.isaac.core.utils.viewports import set_camera_view
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleGeometryPrim
from isaacsim.core.api.world import World

# ===== Base directory 探测（可选）=====
def _detect_base_dir() -> str:
    def _is_project_root(p: Path) -> bool:
        return (p / "assets").exists() and (p / "warehouse_scenes").exists()

    env = os.getenv("SCENE_BASE_DIR") or os.getenv("ISAAC_SCENE_BASE_DIR")
    if env:
        try:
            p = Path(env).expanduser().resolve()
            if _is_project_root(p):
                return str(p)
        except Exception:
            pass

    try:
        p = Path(__file__).resolve().parent
        if _is_project_root(p):
            return str(p)
    except Exception:
        pass

    try:
        home = Path.home().resolve()
        common = [
            home / "Desktop" / "scene",
            home / "Documents" / "scene",
            home / "scene",
        ]
        for p in common:
            if _is_project_root(p):
                return str(p)
    except Exception:
        pass

    cwd = Path(os.getcwd()).resolve()
    for p in [cwd] + list(cwd.parents):
        if _is_project_root(p):
            return str(p)

    return str(cwd)

BASE_DIR = _detect_base_dir().replace("\\", "/")

# ===== 可用的场景布局文件 =====
# 取消注释下面任意一行来切换场景，或者通过环境变量 NEW_SCENE_JSON 指定

# 1. 智能自适应布局 自动计算最优列数和货架规格，混合使用多种货架
#DEFAULT_JSON_FILE = "warehouse_scenes/adaptive_layout.json"

# 2. 混合格数布局 - 随机混合使用1-4格货架
#DEFAULT_JSON_FILE = "warehouse_scenes/mixed_layout.json"

# 3. 双通道自适应布局 - 左右两列货架，中间通道
#DEFAULT_JSON_FILE = "warehouse_scenes/dual_channel_adaptive.json"

# 4. 网格混合布局 - 不同区域使用不同格数的货架
#DEFAULT_JSON_FILE = "warehouse_scenes/grid_mixed.json"

# 5. 贪心列布局（方案1）- 货架原始朝向，一列一列放，优先放最长格货架
#DEFAULT_JSON_FILE = "warehouse_scenes/greedy_column_layout.json"

# 6. 贪心行布局（方案2）- 货架旋转90度，一行一行放，优先放最长格货架
DEFAULT_JSON_FILE = "warehouse_scenes/greedy_row_layout.json"

# 7. 四区域布局（方案3）- 区域按比例分配，每个模块放不同格数的货架
#DEFAULT_JSON_FILE = "warehouse_scenes/four_zone_layout.json"

# 8. 随机四区域布局（方案3增强版）- 随机分配货架类型、区域大小和放置方向
#DEFAULT_JSON_FILE = "warehouse_scenes/four_zone_random_layout.json"

# 环境变量方式（优先级最高）：
# Windows: set NEW_SCENE_JSON=warehouse_scenes/mixed_layout.json
# Linux/Mac: export NEW_SCENE_JSON=warehouse_scenes/mixed_layout.json
JSON_FILE = os.getenv("NEW_SCENE_JSON") or DEFAULT_JSON_FILE

if BASE_DIR:
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

    try:
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

from func.manipulator import Manipulator
from func.new_transporter import Transporter
from func.ridgeback_base import RidgebackBase
from func.static_asset import StaticAsset

USE_FUNC_CLASSES_WHERE_POSSIBLE = True
LAST_SCENE_OBJECTS = {}
NAME_COUNTERS = {}  # 用于追踪重名对象的计数器


def get_unique_name(base_name: str) -> str:
    """
    确保对象名称唯一，如果已存在同名对象，自动添加后缀 _1, _2, ...
    
    Args:
        base_name: 基础名称（可能重复）
    
    Returns:
        唯一的名称
    """
    # 检查是否已经存在
    if base_name not in LAST_SCENE_OBJECTS:
        return base_name
    
    # 如果存在，找一个唯一的名称
    if base_name not in NAME_COUNTERS:
        NAME_COUNTERS[base_name] = 1
    
    counter = NAME_COUNTERS[base_name]
    while True:
        new_name = f"{base_name}_{counter}"
        if new_name not in LAST_SCENE_OBJECTS:
            NAME_COUNTERS[base_name] = counter + 1
            print(f"  ⚠️  检测到重名：{base_name} → {new_name}")
            return new_name
        counter += 1


def create_static_asset_safe(name: str, usd_path: str, position, scale=None, orientation_euler_deg=None):
    """
    安全地创建 StaticAsset，确保在创建前彻底清理同名对象。
    
    这是对 StaticAsset 的包装，解决了重复运行脚本时的名称冲突问题。
    """
    # 多次确保名称空闲（因为 StaticAsset.__init__ 会自动调用 scene.add）
    _ensure_name_free(name)
    
    # 再次检查并等待（确保清理完成）
    try:
        world = World.instance()
        if world and hasattr(world.scene, "_objects"):
            if name in world.scene._objects:
                # 如果还在，再次强制删除
                print(f"  ⚠️ 对象 {name} 仍在 scene 中，再次强制删除")
                del world.scene._objects[name]
                # 小延迟确保清理完成
                import time
                time.sleep(0.01)
    except Exception:
        pass
    
    # 创建对象
    try:
        asset = StaticAsset(
            name=name,
            usd_path=usd_path,
            position=position,
            scale=scale,
            orientation_euler_deg=orientation_euler_deg,
        )
        return asset
    except Exception as e:
        print(f"  ❌ 创建 StaticAsset {name} 失败: {e}")
        # 如果 StaticAsset 失败，回退到 add_to_scene_safe
        print(f"  🔄 回退到 add_to_scene_safe 方式创建 {name}")
        orientation = orientation_euler_deg if orientation_euler_deg is not None else [0, 0, 0]
        return add_to_scene_safe(usd_path, name, position, scale=scale, orientation=orientation)

SHELF_SEGMENT_ASSETS = {
    1: "shelf_1_segments.usd",
    2: "shelf_2_segments.usd",
    3: "shelf_3_segments.usd",
    4: "shelf.usd",
}


def _abs(p: str) -> str:
    return os.path.abspath(p).replace("\\", "/")


def _asset(*rel_parts: str) -> str:
    filename = rel_parts[-1] if rel_parts else ""
    name_without_ext = filename.rsplit(".", 1)[0] if "." in filename else filename

    collected = Path(BASE_DIR) / "func" / name_without_ext / filename
    if collected.exists():
        print(f"  📁 使用收集的资源（含材质）: {collected.as_posix()}")
        return collected.as_posix()

    func_usd = Path(BASE_DIR) / "func" / "usd" / Path(*rel_parts)
    if func_usd.exists():
        return func_usd.as_posix()

    assets = Path(BASE_DIR) / "assets" / Path(*rel_parts)
    return assets.as_posix()


def _shelf_usd_for_segments(segments: int) -> str:
    """
    根据货架格数选择对应的 USD 资产。
    
    优先级：
    1. func/shelf/shelf_{segments}_segments.usd (1-3格)
    2. func/shelf/shelf.usd (4格或其他)
    3. assets/shelf.usd (最终回退)
    """
    filename = SHELF_SEGMENT_ASSETS.get(segments, SHELF_SEGMENT_ASSETS[4])
    candidate = Path(BASE_DIR) / "func" / "shelf" / filename
    if candidate.exists():
        return candidate.as_posix()

    candidate = Path(BASE_DIR) / "func" / "shelf" / "shelf.usd"
    if candidate.exists():
        return candidate.as_posix()

    fallback = _asset("shelf.usd")
    return fallback


def _get_usd_bbox_raw(usd_path: str):
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
    """把 JSON 的 position 解释为“资产底部中心”，并返回需要传给 create/add 的修正后 position。"""
    r = _get_usd_bbox_range_raw(usd_path)
    if not r:
        return desired_position
    (minx, miny, minz), (maxx, maxy, maxz) = r
    # 资产底部中心（在资产坐标中）
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


def _scale_from_bbox(usd_path: str, desired_xyz):
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


def _normalize_deg(d: float) -> float:
    d = float(d)
    while d > 180:
        d -= 360
    while d < -180:
        d += 360
    return d


def _base_rot_deg_align_long_to_x(usd_path: str) -> float:
    bbox = _get_usd_bbox_raw(usd_path)
    if not bbox:
        return 0.0
    ox, oy, _ = bbox
    return -90.0 if oy > ox else 0.0


def _scale_for_length_width_height_aligned_to_x(usd_path: str, length: float, width: float, height: float, base_rot_deg: float):
    base_rot_deg = _normalize_deg(base_rot_deg)
    if abs(base_rot_deg + 90.0) < 1e-6:  # -90
        desired_local = (width, length, height)
    else:
        desired_local = (length, width, height)
    return _scale_from_bbox(usd_path, desired_local)


def _uniform_scale_to_diameter_xy(usd_path: str, diameter: float):
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


def _disable_rigid_bodies_under(prim_path: str):
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return

    root = stage.GetPrimAtPath(prim_path)
    if not root or not root.IsValid():
        return

    for prim in Usd.PrimRange(root):
        try:
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                rigid_body = UsdPhysics.RigidBodyAPI(prim)
                rigid_body.CreateRigidBodyEnabledAttr(False).Set(False)
                rigid_body.CreateKinematicEnabledAttr(True).Set(True)
        except Exception:
            pass


def _ensure_name_free(name: str):
    """彻底删除 stage 和 World.scene 中已存在的同名对象，避免重复添加。"""
    if not name:
        return
    
    prim_path = f"/World/{name}"
    
    # 步骤1：从 World.scene 中删除对象引用
    try:
        world = World.instance()
        if world is not None:
            # 方法1：直接从内部字典删除
            if hasattr(world.scene, "_objects"):
                if name in world.scene._objects:
                    print(f"  🗑️  清理已存在对象: {name}")
                    del world.scene._objects[name]
            
            # 方法2：如果有公开的删除方法，也调用一下
            try:
                existing = world.scene.get_object(name)
                if existing is not None and hasattr(world.scene, "remove_object"):
                    world.scene.remove_object(name)
            except Exception:
                pass
    except Exception as e:
        print(f"  ⚠️ 清理对象 {name} 时出错: {e}")
    
    # 步骤2：从 USD Stage 中删除 Prim
    _stage_remove_prim(prim_path)


def reset_world_completely():
    """清空场景内容，但保留 Isaac LoadButton 正在使用的 World 单例。"""
    print("[LPB exporter] Clearing scene contents and preserving active World...")

    try:
        world = World.instance()
        if world is None:
            world = World()

        # Isaac 的 LoadButton 在 setup_scene_fn() 返回后还会继续调用同一个
        # world.reset_async()。这里不能 World.clear_instance()，否则那个 world
        # 会被删除 _scene 属性，触发 "'World' object has no attribute '_scene'"。
        world.clear()
    except Exception as e:
        print(f"[LPB exporter] world.clear() failed, falling back to stage cleanup: {e}")
        stage = omni.usd.get_context().get_stage()
        if stage:
            world_prim = stage.GetPrimAtPath("/World")
            if world_prim and world_prim.IsValid():
                for child in list(world_prim.GetChildren()):
                    if child.GetName() != "PhysicsScene":
                        stage.RemovePrim(child.GetPath())

    LAST_SCENE_OBJECTS.clear()
    NAME_COUNTERS.clear()
    print("[LPB exporter] Scene cache cleared.\n")


def add_to_scene_safe(usd_path: str, name: str, position, scale=None, orientation=None, orientation_euler_deg=None):
    prim_path = f"/World/{name}"
    add_reference_to_stage(_abs(usd_path), prim_path)
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
    xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(position)
    xformable.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(size)
    UsdPhysics.CollisionAPI.Apply(cube_prim)
    cube.GetDisplayColorAttr().Set([color])


def _rotated_xy_offset(x: float, y: float, yaw_deg: float):
    yaw = math.radians(float(yaw_deg))
    c = math.cos(yaw)
    s = math.sin(yaw)
    return c * x - s * y, s * x + c * y


def _dashed_segments_between(start, end, dash_length: float, gap_length: float):
    sx, sy, sz = float(start[0]), float(start[1]), float(start[2])
    ex, ey, ez = float(end[0]), float(end[1]), float(end[2])
    dx, dy, dz = ex - sx, ey - sy, ez - sz
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length <= 1e-6:
        return []

    dash = max(float(dash_length), 0.02)
    gap = max(float(gap_length), 0.0)
    step = max(dash + gap, 0.02)
    segments = []
    cursor = 0.0
    while cursor < length:
        next_cursor = min(cursor + dash, length)
        a = cursor / length
        b = next_cursor / length
        segments.append(
            (
                [sx + dx * a, sy + dy * a, sz + dz * a],
                [sx + dx * b, sy + dy * b, sz + dz * b],
            )
        )
        cursor += step
    return segments


def _create_dashed_box(name: str, position, dimensions, rotation: float = 0.0, color=None, dash_length=0.35, gap_length=0.18, line_width=0.035):
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return None

    length = float(dimensions.get("length", 2.4))
    width = float(dimensions.get("width", 1.8))
    height = max(float(dimensions.get("height", 0.05)), 0.0)
    x, y, z = float(position[0]), float(position[1]), float(position[2])
    color = color or [1.0, 0.05, 0.02]

    bottom_z = z + 0.035
    top_z = bottom_z + height
    local_corners = [
        (-length / 2.0, -width / 2.0),
        (length / 2.0, -width / 2.0),
        (length / 2.0, width / 2.0),
        (-length / 2.0, width / 2.0),
    ]
    bottom = []
    top = []
    for lx, ly in local_corners:
        ox, oy = _rotated_xy_offset(lx, ly, rotation)
        bottom.append([x + ox, y + oy, bottom_z])
        top.append([x + ox, y + oy, top_z])

    edges = []
    for idx in range(4):
        nxt = (idx + 1) % 4
        edges.append((bottom[idx], bottom[nxt]))
        if height > 0.02:
            edges.append((top[idx], top[nxt]))
            edges.append((bottom[idx], top[idx]))

    dashed = []
    for a, b in edges:
        dashed.extend(_dashed_segments_between(a, b, dash_length, gap_length))
    if not dashed:
        return None

    curve_path = f"/World/{name}"
    curves = UsdGeom.BasisCurves.Define(stage, curve_path)
    curves.CreateTypeAttr("linear")
    curves.CreateCurveVertexCountsAttr(Vt.IntArray([2] * len(dashed)))
    points = []
    for a, b in dashed:
        points.append(Gf.Vec3f(float(a[0]), float(a[1]), float(a[2])))
        points.append(Gf.Vec3f(float(b[0]), float(b[1]), float(b[2])))
    curves.CreatePointsAttr(Vt.Vec3fArray(points))
    curves.CreateWidthsAttr(Vt.FloatArray([float(line_width)] * max(len(points), 1)))
    curves.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(float(color[0]), float(color[1]), float(color[2]))]))
    return stage.GetPrimAtPath(curve_path)


def setup_camera(scene_data):
    dims = scene_data.get("warehouse", {}).get("dimensions", {})
    length = float(dims.get("length", 40))
    width = float(dims.get("width", 40))
    center_x = length / 2
    center_y = width / 2
    cam_distance = max(length, width) * 0.8
    cam_height = max(length, width) * 0.5
    eye = Gf.Vec3d(center_x + cam_distance, center_y - cam_distance * 0.5, cam_height)
    target = Gf.Vec3d(center_x, center_y, 0)
    set_camera_view(eye=eye, target=target, camera_prim_path="/OmniverseKit_Persp")


def build_scene(scene_data=None, source_path=None):
    print("\n" + "="*60)
    print("🏗️  开始构建场景")
    print("="*60)
    
    reset_world_completely()
    world = World.instance()
    if world is None:
        world = World()

    json_path = None
    if scene_data is None:
        json_path = _abs(os.path.join(BASE_DIR, JSON_FILE) if not os.path.isabs(JSON_FILE) else JSON_FILE)
        if not os.path.exists(json_path):
            raise FileNotFoundError(json_path)
        with open(json_path, "r", encoding="utf-8") as f:
            scene_data = json.load(f)

    print(f"📄 加载场景配置: {source_path or json_path or JSON_FILE}")
    print(f"📂 项目目录: {BASE_DIR}")
    print("="*60 + "\n")

    stage = omni.usd.get_context().get_stage()
    world_prim = stage.GetPrimAtPath("/World") if stage else None
    if not world_prim or not world_prim.IsValid():
        create_prim("/World", "Xform")
    create_lights()
    create_ground(scene_data)
    
    print("✓ 灯光和地面已创建\n")

    objects = scene_data.get("objects", [])
    print(f"📦 开始放置 {len(objects)} 个对象...\n")
    
    created_count = 0
    failed_count = 0
    
    for idx, obj in enumerate(objects, 1):
        t = obj.get("type")
        original_name = obj.get("name")
        
        # 确保名称唯一
        name = get_unique_name(original_name)
        
        print(f"[{idx}/{len(objects)}] 处理对象: {name} (类型: {t})")
        
        _ensure_name_free(name)
        pos = obj.get("position", {})
        rotation = float(obj.get("rotation", 0))
        position = [float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0))]

        if t == "shelf":
            dims = obj["dimensions"]
            segments = int(obj.get("segments", 4))
            target_length = float(dims["length"])
            target_width = float(dims["width"])
            target_height = float(dims["height"])
            shelf_usd = _shelf_usd_for_segments(segments)
            
            print(f"📦 创建货架 {name}: {segments}格, {target_length}m×{target_width}m×{target_height}m, 位置=({position[0]:.1f}, {position[1]:.1f})")
            
            base_rot = -90.0
            final_rot = _normalize_deg(base_rot + rotation)
            scale = _scale_from_bbox(shelf_usd, (target_width, target_length, target_height))
            if scale is None:
                print(f"  ⚠️ 缩放失败，使用默认 1:1:1")
                scale = [1.0, 1.0, 1.0]

            unload_offset = [0.0, -(target_length / 2.0 - 0.5), 0.0]
            # 关键修正：JSON position 约定为“底部中心”，但 USD 资产原点可能不在底部中心
            # 因此需要根据 bbox 的 bottom-center 做一次反向偏移（包含缩放与旋转）
            shelf_position = _apply_bottom_center_pivot(position, shelf_usd, scale, final_rot)

            try:
                if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                    asset = create_static_asset_safe(
                        name=name,
                        usd_path=shelf_usd,
                        position=shelf_position,
                        scale=scale,
                        orientation_euler_deg=[0, 0, final_rot],
                    )
                    LAST_SCENE_OBJECTS[name] = asset
                else:
                    prim = add_to_scene_safe(
                        shelf_usd,
                        name=name,
                        position=shelf_position,
                        scale=scale,
                        orientation=[0, 0, final_rot],
                    )
                    LAST_SCENE_OBJECTS[name] = prim
                created_count += 1
                _disable_rigid_bodies_under(f"/World/{name}")
                print(f"  ✅ 货架 {name} 创建成功")
            except Exception as e:
                failed_count += 1
                print(f"  ❌ 货架 {name} 创建失败: {e}")
                import traceback
                traceback.print_exc()

            for child in obj.get("children", []):
                if child.get("type") == "box":
                    original_child_name = child.get("name")
                    child_name = get_unique_name(original_child_name)
                    _ensure_name_free(child_name)
                    child_dims = child.get("dimensions", {})
                    child_pos = child["position"]
                    child_rot = float(child.get("rotation", 0))
                    child_desired = (
                        float(child_dims.get("length", 0.3)),
                        float(child_dims.get("width", 0.3)),
                        float(child_dims.get("height", 0.5)),
                    )
                    box_usd = _asset("Box.usd")
                    child_position = [
                        float(child_pos.get("x", 0)),
                        float(child_pos.get("y", 0)),
                        float(child_pos.get("z", 0)),
                    ]
                    child_scale = _scale_from_bbox(box_usd, child_desired)
                    if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                        child_obj = create_static_asset_safe(
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
            dims = obj["dimensions"]
            length = float(dims["length"])
            width = float(dims["width"])
            height = float(dims["height"])
            conveyor_usd = _asset("ConveyorBelt_A05.usd")
            base_rot = _base_rot_deg_align_long_to_x(conveyor_usd)
            scale = _scale_for_length_width_height_aligned_to_x(conveyor_usd, length, width, height, base_rot)
            conveyor_rot = float(obj.get("rotation", 0))
            orientation = [0, 0, _normalize_deg(base_rot + conveyor_rot)]
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                asset = create_static_asset_safe(
                    name=name,
                    usd_path=conveyor_usd,
                    position=position,
                    scale=scale,
                    orientation_euler_deg=orientation,
                )
                LAST_SCENE_OBJECTS[name] = asset
            else:
                prim = add_to_scene_safe(conveyor_usd, name, position, scale=scale, orientation=orientation)
                LAST_SCENE_OBJECTS[name] = prim

        elif t == "crate":
            dims = obj.get("dimensions", {})
            desired = (
                float(dims.get("length", 1.2)),
                float(dims.get("width", 1.0)),
                float(dims.get("height", 0.3)),
            )
            pallet_usd = _asset("pallet.usd")
            crate_position = position
            scale = _scale_from_bbox(pallet_usd, desired)
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                crate_obj = create_static_asset_safe(
                    name=name,
                    usd_path=pallet_usd,
                    position=crate_position,
                    scale=scale,
                )
                LAST_SCENE_OBJECTS[name] = crate_obj
            else:
                prim = add_to_scene_safe(pallet_usd, name, crate_position, scale=scale)
                LAST_SCENE_OBJECTS[name] = prim

            for child in obj.get("children", []):
                if child.get("type") == "box":
                    original_child_name = child.get("name")
                    child_name = get_unique_name(original_child_name)
                    _ensure_name_free(child_name)
                    child_dims = child.get("dimensions", {})
                    child_pos = child["position"]
                    child_rot = float(child.get("rotation", 0))
                    child_desired = (
                        float(child_dims.get("length", 0.3)),
                        float(child_dims.get("width", 0.3)),
                        float(child_dims.get("height", 0.5)),
                    )
                    box_usd = _asset("Box.usd")
                    child_position = [
                        float(child_pos.get("x", 0)),
                        float(child_pos.get("y", 0)),
                        float(child_pos.get("z", 0)),
                    ]
                    child_scale = _scale_from_bbox(box_usd, child_desired)
                    if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                        child_obj = create_static_asset_safe(
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

        elif t == "slot":
            dims = obj.get("dimensions", {})
            size = Gf.Vec3d(
                float(dims.get("length", 1.6)),
                float(dims.get("width", 1.0)),
                float(dims.get("height", 0.18)),
            )
            prim = create_prim(f"/World/{name}", "Cube")
            cube = UsdGeom.Cube(prim)
            cube.CreateSizeAttr(1.0)
            xformable = UsdGeom.Xformable(prim)
            xformable.ClearXformOpOrder()
            xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*position))
            xformable.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(0.0, 0.0, rotation))
            xformable.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(size)
            cube.GetDisplayColorAttr().Set([Gf.Vec3f(0.12, 0.36, 0.72)])
            try:
                UsdPhysics.CollisionAPI.Apply(prim)
            except Exception:
                pass
            LAST_SCENE_OBJECTS[name] = prim

        elif t == "pallet_transport_target":
            dims = obj.get("dimensions", {})
            raw_color = obj.get("color", [1.0, 0.05, 0.02])
            if isinstance(raw_color, dict):
                color = [
                    float(raw_color.get("x", raw_color.get("r", 1.0))),
                    float(raw_color.get("y", raw_color.get("g", 0.05))),
                    float(raw_color.get("z", raw_color.get("b", 0.02))),
                ]
            else:
                color_values = list(raw_color) if isinstance(raw_color, (list, tuple)) else [1.0, 0.05, 0.02]
                color = [
                    float((color_values + [1.0, 0.05, 0.02])[0]),
                    float((color_values + [1.0, 0.05, 0.02])[1]),
                    float((color_values + [1.0, 0.05, 0.02])[2]),
                ]
            prim = _create_dashed_box(
                name=name,
                position=position,
                dimensions=dims,
                rotation=rotation,
                color=color,
                dash_length=float(obj.get("dash_length", 0.35)),
                gap_length=float(obj.get("gap_length", 0.18)),
                line_width=float(obj.get("line_width", 0.035)),
            )
            if prim is not None:
                LAST_SCENE_OBJECTS[name] = prim

        elif t == "box":
            dims = obj.get("dimensions", {})
            desired = (
                float(dims.get("length", 0.3)),
                float(dims.get("width", 0.3)),
                float(dims.get("height", 0.5)),
            )
            box_usd = _asset("Box.usd")
            scale = _scale_from_bbox(box_usd, desired)
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                box_obj = create_static_asset_safe(
                    name=name,
                    usd_path=box_usd,
                    position=position,
                    scale=scale,
                    orientation_euler_deg=[0, 0, rotation],
                )
                LAST_SCENE_OBJECTS[name] = box_obj
            else:
                prim = add_to_scene_safe(box_usd, name, position, scale=scale, orientation=[0, 0, rotation])
                LAST_SCENE_OBJECTS[name] = prim

    agents = scene_data.get("agents", [])
    if agents:
        print(f"\n🤖 开始放置 {len(agents)} 个代理...\n")
    
    for agent in agents:
        t = agent.get("type")
        original_name = agent.get("name")
        
        # 确保名称唯一
        name = get_unique_name(original_name)
        
        _ensure_name_free(name)
        pos = agent.get("position", {})
        position = [float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0))]

        if t == "agv":
            print(f"🚗 创建AGV {name}: 位置=({position[0]:.1f}, {position[1]:.1f})")
            model = (agent.get("model") or "create3").lower()
            rot = float(agent.get("rotation", 0))
            dims = agent.get("dimensions", {}) or {}
            diameter = float(dims.get("diameter", 0.8))
            usd = _asset("ridgeback.usd") if model in ("ridgeback", "rb") else _asset("create_3.usd")
            base_rot = _base_rot_deg_align_long_to_x(usd)
            scale = _uniform_scale_to_diameter_xy(usd, diameter)
            final_rot = _normalize_deg(base_rot + rot)
            if scale is None:
                scale = [1.0, 1.0, 1.0]

            agv_obj = None
            if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                try:
                    if model in ("ridgeback", "rb"):
                        agv_obj = RidgebackBase(name=name, position=position, orientation=[0, 0, final_rot], scale=scale)
                    else:
                        agv_obj = Transporter(name=name, position=position, orientation=[0, 0, final_rot], scale=scale)
                except Exception as e:
                    print(f"  ⚠️ 使用 func AGV 类创建失败，回退到静态 USD: {e}")
                    agv_obj = None

            if agv_obj is None:
                if USE_FUNC_CLASSES_WHERE_POSSIBLE:
                    agv_obj = create_static_asset_safe(
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

        elif t == "robot_arm":
            print(f"🦾 创建机械臂 {name}: 位置=({position[0]:.1f}, {position[1]:.1f})")
            rot = float(agent.get("rotation", 0))
            try:
                arm = Manipulator(name=name, position=position, orientation=[0, 0, rot])
                LAST_SCENE_OBJECTS[name] = arm
            except Exception as e:
                print(f"  ⚠️ 创建失败: {e}")

    setup_camera(scene_data)
    
    # 统计信息
    shelf_count = len([o for o in objects if o.get("type") == "shelf"])
    conveyor_count = len([o for o in objects if o.get("type") == "conveyor"])
    crate_count = len([o for o in objects if o.get("type") == "crate"])
    box_count = len([o for o in objects if o.get("type") == "box"])
    transport_target_count = len([o for o in objects if o.get("type") == "pallet_transport_target"])
    agent_count = len(scene_data.get("agents", []))
    
    print("\n" + "="*60)
    print("✓ 场景构建完成")
    print("="*60)
    print(f"📦 货架: {shelf_count} 个 (定义在JSON中)")
    print(f"   ├─ 成功创建: {created_count} 个")
    print(f"   └─ 失败: {failed_count} 个")
    print(f"🔄 传送带: {conveyor_count} 个")
    print(f"📦 托盘: {crate_count} 个")
    print(f"📦 箱子: {box_count} 个")
    print(f"🤖 代理: {agent_count} 个")
    print(f"📊 场景中实际对象数: {len(LAST_SCENE_OBJECTS)}")
    print("="*60)
    
    carb.log_info(
        f"Build done. Total objects={len(LAST_SCENE_OBJECTS)}, Created={created_count}, "
        f"Failed={failed_count}, PalletTransportTargets={transport_target_count}"
    )


def main():
    build_scene()


if __name__ == "__main__":
    main()
