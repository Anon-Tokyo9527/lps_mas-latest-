"""
Isaac Sim 场景对象信息 REST API 服务

提供 HTTP REST 接口，实时获取 Isaac Sim 场景中每个对象的信息
包括位置、方向、大小、子对象等

入口说明：
- 在 Isaac Sim Script Editor 里启动：运行 `start_scene_api.py`（推荐，不阻塞 Isaac 主线程）
- 在普通 Python 环境启动（需要有 omni.usd/pxr 环境）：可直接运行本文件（`__main__` 会启动 uvicorn）
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Optional
import omni.usd
from pxr import Usd, UsdGeom, Gf

API_VERSION = "1.3"

app = FastAPI(title="Isaac Sim Scene API", description="实时获取 Isaac Sim 场景对象信息")

GROUP_CONTAINERS = {
    # Script Editor 版本常用的分类目录
    "agents",
    "shelves",
    "boxes",
    "crates",
    "packingtables",
    "conveyors",
}

def _iter_candidate_prims(world_prim: Usd.Prim):
    """
    生成“可能的主对象”候选 prim：
    - /World 的直接子对象
    - 以及 /World/<Group> 下的第一层子对象（Script Editor 风格）
    """
    for c in world_prim.GetChildren():
        yield c
        if c.GetName().lower() in GROUP_CONTAINERS:
            for cc in c.GetChildren():
                yield cc


def _debug_eval_prim(prim: Usd.Prim) -> Dict[str, Any]:
    """
    评估一个 prim 会被 prim_to_dict 过滤在哪一步（用于调试）。
    不抛异常，尽量返回更多信息。
    """
    name = prim.GetName()
    prim_path = str(prim.GetPath())
    result: Dict[str, Any] = {
        "name": name,
        "path": prim_path,
    }

    # /World 范围
    if not prim_path.startswith("/World/") or prim_path == "/World":
        result["skip_reason"] = "not_under_world"
        return result

    # internal 过滤
    try:
        internal = is_internal_component(prim_path, name)
        result["is_internal_component"] = internal
        if internal:
            result["skip_reason"] = "internal_component"
            return result
    except Exception as e:
        result["is_internal_component_error"] = str(e)

    # visibility
    try:
        imageable = UsdGeom.Imageable(prim)
        if imageable:
            vis = imageable.ComputeVisibility()
            result["visibility"] = str(vis)
            if vis == UsdGeom.Tokens.invisible:
                result["skip_reason"] = "invisible"
                return result
    except Exception as e:
        result["visibility_error"] = str(e)

    # type inference / main check
    try:
        obj_type = infer_object_type(prim)
        result["inferred_type"] = obj_type
        result["is_main_type"] = is_main_object_type(obj_type)
        if (not result["is_main_type"]) and obj_type != "Package":
            result["skip_reason"] = "not_main_type"
            return result
    except Exception as e:
        result["inferred_type_error"] = str(e)

    # bbox
    try:
        size = get_prim_bbox(prim)
        result["bbox_size"] = size
        if size is not None and any(s < 0 or s > 1000 for s in size):
            result["skip_reason"] = "bbox_abnormal"
            return result
    except Exception as e:
        result["bbox_error"] = str(e)

    result["skip_reason"] = None
    return result


def matrix3d_to_quaternion(rot_matrix: Gf.Matrix3d) -> list:
    """
    从 3x3 旋转矩阵计算四元数
    
    Args:
        rot_matrix: 3x3 旋转矩阵
    
    Returns:
        [w, x, y, z] 四元数
    """
    # 从旋转矩阵提取元素
    m = rot_matrix
    trace = m[0][0] + m[1][1] + m[2][2]
    
    if trace > 0:
        s = (trace + 1.0) ** 0.5 * 2.0  # s = 4 * qw
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = ((1.0 + m[0][0] - m[1][1] - m[2][2]) ** 0.5) * 2.0  # s = 4 * qx
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = ((1.0 + m[1][1] - m[0][0] - m[2][2]) ** 0.5) * 2.0  # s = 4 * qy
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = ((1.0 + m[2][2] - m[0][0] - m[1][1]) ** 0.5) * 2.0  # s = 4 * qz
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    
    return [float(w), float(x), float(y), float(z)]


def get_prim_world_transform(prim: Usd.Prim) -> tuple:
    """
    获取 prim 的世界坐标变换（位置和四元数）
    
    Returns:
        (position: [x, y, z], quaternion: [w, x, y, z])
    """
    try:
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return [0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]
        
        # 获取世界变换矩阵
        time = Usd.TimeCode.Default()
        world_transform = xformable.ComputeLocalToWorldTransform(time)
        
        # 提取位置
        translation = world_transform.ExtractTranslation()
        position = [float(translation[0]), float(translation[1]), float(translation[2])]

        # 提取方向（四元数）
        # 重要：不要用 Gf.Rotation(Matrix3d) 这种写法（Python 绑定不支持），
        # 且部分 Isaac/USD 版本的 ExtractRotationMatrix 绑定实现可能会间接触发该错误。
        # 优先使用 ExtractRotationQuat（直接返回 Gf.Quatd）。
        try:
            quat = world_transform.ExtractRotationQuat()  # Gf.Quatd
            imag = quat.GetImaginary()  # Gf.Vec3d
            quaternion = [float(quat.GetReal()), float(imag[0]), float(imag[1]), float(imag[2])]
        except Exception:
            # 兼容性 fallback：提取 3x3 旋转矩阵后手动转四元数
            rotation_matrix = world_transform.ExtractRotationMatrix()
            quaternion = matrix3d_to_quaternion(rotation_matrix)
        
        return position, quaternion
    except Exception as e:
        # 如果提取失败，返回默认值（无旋转）
        print(f"获取变换失败 {prim.GetPath()}: {e}")
        return [0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]


def get_prim_bbox(prim: Usd.Prim) -> Optional[List[float]]:
    """
    获取 prim 的世界坐标系下的边界框尺寸
    
    Returns:
        [length, width, height] 或 None
    """
    try:
        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        bbox = bbox_cache.ComputeWorldBound(prim)
        if not bbox:
            return None
        
        bbox_range = bbox.ComputeAlignedRange()
        size = bbox_range.GetSize()
        
        # 返回 [x, y, z] 尺寸（对应 length, width, height）
        return [float(size[0]), float(size[1]), float(size[2])]
    except Exception as e:
        print(f"获取 bbox 失败 {prim.GetPath()}: {e}")
        return None


def infer_object_type(prim: Usd.Prim) -> str:
    """
    根据 prim 名称和类型推断对象类型
    注意：优先检查 Package（箱子），避免误判
    """
    name = prim.GetName().lower()
    path = str(prim.GetPath()).lower()
    
    # 优先检查 Package（箱子）- 必须最先检查，避免误判
    # 箱子名称通常以 Box_ 开头，或包含 Box_ 前缀
    if name.startswith("box_") or (name.startswith("box") and "_" in name):
        return "Package"
    
    # 检查是否是“顶层对象”
    # - 标准结构：/World/<name>
    # - Script Editor 结构：/World/<Group>/<name> （如 /World/Agents/RobotArm_1）
    path_parts = path.split("/")
    is_top_level = (
        len(path_parts) == 3  # /World/<name>
        or (len(path_parts) == 4 and path_parts[2] in GROUP_CONTAINERS)  # /World/<group>/<name>
    )

    # 对于非“顶层对象”，更严格的匹配规则：不推断为主要类型
    if not is_top_level:
        return "Object"
    
    # 顶层对象的类型推断（Package 已经在上面处理了）
    if "shelf" in name and not name.startswith("box"):
        return "Shelf"
    elif "pallet" in name or "crate" in name:
        return "Pallet"
    elif "robot" in name or "arm" in name:
        return "Robot"
    elif "agv" in name or "transporter" in name:
        return "AGV"
    elif "conveyor" in name:
        return "Conveyor"
    elif "table" in name:
        return "Table"
    else:
        return "Object"


def is_main_object_type(obj_type: str) -> bool:
    """
    判断是否是主要对象类型（需要显示的对象）
    """
    main_types = ["Shelf", "Robot", "AGV", "Package", "Pallet", "Conveyor", "Table"]
    return obj_type in main_types


def is_internal_component(prim_path: str, name: str) -> bool:
    """
    判断是否是内部组件（材质、着色器、结构部件等），应该被过滤
    """
    path_lower = prim_path.lower()
    name_lower = name.lower()
    
    # 过滤材质、着色器相关（优先按路径判断）
    if any(keyword in path_lower for keyword in [
        "/looks", "/look", "/shader", "/material", "/materials",
    ]):
        return True

    # 过滤 USD 资产内部命名约定（仅当“前缀”匹配）
    # 注意：不能用 `in` 子串匹配，否则 RobotArm_1 会因为包含 "m_"（ar[m]_) 被误判。
    if name_lower.startswith(("sm_", "m_", "t_")):
        return True

    # 过滤常见内部结构部件命名
    if any(keyword in path_lower or keyword in name_lower for keyword in [
        "body", "bolts", "cube_0", "cube_1", "cube_2", "cube_3", "cube_4"
    ]):
        return True
    
    # 过滤路径层级过深的对象（通常是资产内部组件）
    # 但允许 Script Editor 风格的分组目录：/World/<Group>/<name>
    path_parts = prim_path.split("/")
    # /World/<group>/<name> 这一层需要保留（机械臂/搬运车/货架/箱子/托盘等会落在这里）
    if len(path_parts) == 4 and path_parts[2].lower() in GROUP_CONTAINERS:
        return False

    if len(path_parts) > 3:  # /World/对象名/子对象 或 /World/<group>/<name>/子对象...
        # 检查是否是 Package（箱子名称通常包含 Box_）
        if "box_" not in name_lower:
            return True
    
    return False


def prim_to_dict(prim: Usd.Prim, parent_path: str = "", parent_is_main: bool = False) -> Optional[Dict[str, Any]]:
    """
    将 prim 转换为字典格式，只保留主要对象（货架、机械臂、箱子、搬运车等）
    
    Args:
        prim: USD prim 对象
        parent_path: 父对象路径
        parent_is_main: 父对象是否是主要对象
    
    Returns:
        对象信息字典或 None（如果跳过该对象）
    """
    # 跳过某些系统 prim
    name = prim.GetName()
    if name in ["DistantLight", "DomeLight", "Ground", "CollectionAreas"]:
        return None
    
    # 获取路径
    prim_path = str(prim.GetPath())
    
    # 只处理 /World 下的对象
    if not prim_path.startswith("/World/"):
        return None
    
    # 跳过 /World 本身
    if prim_path == "/World":
        return None
    
    # 过滤内部组件
    if is_internal_component(prim_path, name):
        return None
    
    # 跳过不可见的对象
    imageable = UsdGeom.Imageable(prim)
    if imageable:
        visibility = imageable.ComputeVisibility()
        if visibility == UsdGeom.Tokens.invisible:
            return None
    
    # 推断类型
    obj_type = infer_object_type(prim)
    
    # 判断是否是主要对象
    is_main = is_main_object_type(obj_type)
    
    # 过滤逻辑：
    # 1. 如果父对象是主要对象，只保留 Package 类型的子对象（过滤机械臂、货架等的内部组件）
    if parent_is_main and obj_type != "Package":
        return None
    
    # 2. 如果不是主要对象，且不是 Package，则跳过
    if not is_main and obj_type != "Package":
        return None
    
    # 获取位置和方向
    position, quaternion = get_prim_world_transform(prim)
    
    # 获取大小
    size = get_prim_bbox(prim)
    if size is None:
        size = [0.0, 0.0, 0.0]
    
    # 过滤异常大小（负数或极大值，通常是无效的 bbox）
    if any(s < 0 or s > 1000 for s in size):
        return None
    
    # 构建对象信息
    obj_dict = {
        "name": name,
        "type": obj_type,
        "path": prim_path,
        "position": position,
        "orientation_quat": quaternion,
        "size": size,
        "children": []
    }
    
    # 不在这里处理子对象，统一在 get_scene_objects 中处理
    # 这样可以避免重复，并且可以通过名称/位置匹配来关联 Package
    
    return obj_dict


def calc_relative_position(child_world_pos: List[float], parent_world_pos: List[float]) -> List[float]:
    """
    计算子对象相对于父对象的位置
    
    注意：父对象（货架）的中心点在底部中心，所以相对位置直接用世界坐标相减即可
    - relative_x = child_x - parent_x
    - relative_y = child_y - parent_y  
    - relative_z = child_z - parent_z（表示子对象高于父对象底部的距离）
    
    Args:
        child_world_pos: 子对象的世界坐标 [x, y, z]
        parent_world_pos: 父对象的世界坐标 [x, y, z]
    
    Returns:
        相对位置 [x, y, z]
    """
    return [
        child_world_pos[0] - parent_world_pos[0],
        child_world_pos[1] - parent_world_pos[1],
        child_world_pos[2] - parent_world_pos[2]
    ]


def get_scene_objects() -> List[Dict[str, Any]]:
    """
    获取场景中所有对象的信息
    
    Returns:
        对象列表
    """
    try:
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return []
        
        # 获取 /World prim
        world_prim = stage.GetPrimAtPath("/World")
        if not world_prim or not world_prim.IsValid():
            return []
        
        # 第一步：收集主要对象（支持两种结构）
        # 1) /World/<name>
        # 2) /World/<Group>/<name> （Script Editor 版本：Agents/Shelves/Boxes/Crates/...）
        main_objects = {}  # {name: obj_dict}
        all_packages = []  # 所有 Package 对象
        
        def iter_candidate_prims():
            # /World 的直接子对象（exporter 版本常用）
            for c in world_prim.GetChildren():
                yield c
                # Script Editor 版本：/World/<Group>/<name>
                if c.GetName().lower() in GROUP_CONTAINERS:
                    for cc in c.GetChildren():
                        yield cc

        for prim in iter_candidate_prims():
            obj_dict = prim_to_dict(prim, parent_path=str(prim.GetPath().GetParentPath()), parent_is_main=False)
            if not obj_dict:
                continue

            obj_type = obj_dict.get("type", "")
            obj_name = obj_dict.get("name", "")

            if is_main_object_type(obj_type):
                if obj_type == "Package":
                    all_packages.append(obj_dict)
                else:
                    main_objects[obj_name] = obj_dict
        
        # 第二步：在主要对象下搜索 Package（深度搜索）
        def find_packages_in_main_object(main_prim, main_name):
            """在主要对象下搜索 Package"""
            packages = []
            for child in main_prim.GetChildren():
                # 递归搜索所有子对象
                def search_recursive(prim):
                    found = []
                    for sub_child in prim.GetChildren():
                        # 跳过内部组件
                        sub_path = str(sub_child.GetPath())
                        sub_name = sub_child.GetName()
                        if is_internal_component(sub_path, sub_name):
                            continue
                        
                        # 检查是否是 Package
                        sub_type = infer_object_type(sub_child)
                        if sub_type == "Package":
                            pkg_dict = prim_to_dict(sub_child, parent_path=str(prim.GetPath()), parent_is_main=True)
                            if pkg_dict:
                                found.append(pkg_dict)
                        else:
                            # 继续递归搜索
                            found.extend(search_recursive(sub_child))
                    return found
                
                packages.extend(search_recursive(child))
            return packages
        
        # 为每个主要对象搜索 Package
        # 注意：主对象可能位于两种结构：
        # - /World/<name>
        # - /World/<Group>/<name>
        # 因此不能用 world_prim.GetChild(main_name) 取 prim（会找不到分组结构），应优先用记录的 path。
        for main_name, main_obj in main_objects.items():
            main_path = str(main_obj.get("path") or "")
            main_prim = stage.GetPrimAtPath(main_path) if main_path else None
            if main_prim and main_prim.IsValid():
                packages = find_packages_in_main_object(main_prim, main_name)
                # 为每个 Package 添加相对于父对象的位置
                parent_pos = main_obj.get("position", [0, 0, 0])
                for pkg in packages:
                    pkg_pos = pkg.get("position", [0, 0, 0])
                    pkg["relative_position"] = calc_relative_position(pkg_pos, parent_pos)
                main_obj["children"].extend(packages)
        
        # 第三步：将独立的 Package（顶层箱子）关联到对应的父对象（货架/托盘）
        # 优先通过位置匹配：箱子在货架/托盘的范围内
        # 名称匹配仅作为备用验证手段
        for package in all_packages:
            package_name = package.get("name", "").lower()
            package_pos = package.get("position", [0, 0, 0])
            package_size = package.get("size", [0, 0, 0])
            
            matched = False
            best_match = None
            best_distance = float('inf')
            
            # 收集所有可能的父对象（货架/托盘）
            candidate_parents = [
                (name, obj) for name, obj in main_objects.items() 
                if obj.get("type") in ["Shelf", "Pallet"]
            ]
            
            # 优先通过位置匹配：找到距离最近且在范围内的货架/托盘
            for main_name, main_obj in candidate_parents:
                main_pos = main_obj.get("position", [0, 0, 0])
                main_size = main_obj.get("size", [0, 0, 0])
                
                # 计算 X, Y 平面上的距离（不考虑 Z）
                dx = abs(package_pos[0] - main_pos[0])
                dy = abs(package_pos[1] - main_pos[1])
                horizontal_distance = (dx ** 2 + dy ** 2) ** 0.5
                
                # 计算 X, Y 方向上的范围（考虑容差）
                # 容差包括箱子自身尺寸的一半，以及一些额外的容差
                tolerance = 0.3  # 基础容差（米）
                x_tolerance = main_size[0] / 2 + package_size[0] / 2 + tolerance
                y_tolerance = main_size[1] / 2 + package_size[1] / 2 + tolerance
                
                # 检查箱子是否在货架/托盘的 X, Y 范围内
                in_range_xy = dx <= x_tolerance and dy <= y_tolerance
                
                if in_range_xy:
                    # 检查 Z 坐标：箱子应该在货架/托盘上方，但不能太高
                    # 允许箱子在货架/托盘底部到顶部+一定高度的范围内
                    dz = package_pos[2] - main_pos[2]
                    max_height = main_size[2] + 2.0  # 货架高度 + 额外容差（允许箱子在货架上方）
                    
                    if dz >= -0.1 and dz <= max_height:  # 允许稍微低于底部（容差），但不能太高
                        # 这是一个候选匹配，记录距离（用于选择最佳匹配）
                        if horizontal_distance < best_distance:
                            best_distance = horizontal_distance
                            best_match = (main_name, main_obj, main_pos)
            
            # 如果找到位置匹配，使用它
            if best_match:
                main_name, main_obj, main_pos = best_match
                package["relative_position"] = calc_relative_position(package_pos, main_pos)
                main_obj["children"].append(package)
                matched = True
            else:
                # 位置匹配失败时，尝试通过名称匹配作为备用（仅用于调试/验证）
                # 注意：这不应该作为主要判断方法，因为对象会移动
                for main_name, main_obj in candidate_parents:
                    main_name_lower = main_name.lower()
                    pattern1 = f"box_{main_name_lower}_"
                    pattern2 = f"box_{main_name_lower}l"
                    
                    if package_name.startswith(pattern1) or package_name.startswith(pattern2):
                        # 即使名称匹配，也检查位置是否合理
                        main_pos = main_obj.get("position", [0, 0, 0])
                        main_size = main_obj.get("size", [0, 0, 0])
                        dx = abs(package_pos[0] - main_pos[0])
                        dy = abs(package_pos[1] - main_pos[1])
                        
                        # 如果位置距离太远（超过5米），可能是名称匹配错误，不关联
                        if (dx ** 2 + dy ** 2) ** 0.5 > 5.0:
                            continue
                        
                        package["relative_position"] = calc_relative_position(package_pos, main_pos)
                        main_obj["children"].append(package)
                        matched = True
                        break
        
        # 返回主要对象列表
        # 注意：顶层 Package 已经关联到父对象，不应该作为独立对象返回
        # 只返回非 Package 的主要对象
        result = []
        for obj in main_objects.values():
            if obj.get("type") != "Package":
                result.append(obj)
        
        return result
    except Exception as e:
        print(f"获取场景对象失败: {e}")
        import traceback
        traceback.print_exc()
        return []


@app.get("/")
async def root():
    """根路径"""
    return {"message": "Isaac Sim Scene API", "version": API_VERSION}


@app.get("/api/debug/version")
async def debug_version():
    """用于确认服务是否加载了最新代码/配置"""
    return {
        "version": API_VERSION,
        "group_containers": sorted(list(GROUP_CONTAINERS)),
    }


@app.get("/api/debug/candidates")
async def debug_candidates(q: str = "", limit: int = 200):
    """
    调试接口：返回 API 扫描到的候选 prim，以及它们为什么会被过滤。

    用法示例：
    - /api/debug/candidates?q=RobotArm
    - /api/debug/candidates?q=/World/Agents
    """
    try:
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return {"version": API_VERSION, "items": [], "error": "no_stage"}

        world_prim = stage.GetPrimAtPath("/World")
        if not world_prim or not world_prim.IsValid():
            return {"version": API_VERSION, "items": [], "error": "no_world"}

        q_lower = (q or "").lower().strip()
        items: List[Dict[str, Any]] = []
        for prim in _iter_candidate_prims(world_prim):
            info = _debug_eval_prim(prim)
            if q_lower:
                if q_lower not in info.get("name", "").lower() and q_lower not in info.get("path", "").lower():
                    continue
            items.append(info)
            if len(items) >= max(1, int(limit)):
                break

        return {"version": API_VERSION, "count": len(items), "items": items}
    except Exception as e:
        return {"version": API_VERSION, "items": [], "error": str(e)}


@app.get("/api/scene/objects")
async def get_objects():
    """
    获取场景中所有对象的信息
    
    Returns:
        JSON 格式的对象列表，包含位置、方向、大小、子对象等
    """
    try:
        objects = get_scene_objects()
        return JSONResponse(content=objects)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取场景对象失败: {str(e)}")


@app.get("/api/scene/objects/{object_name}")
async def get_object_by_name(object_name: str):
    """
    根据名称获取特定对象的信息
    
    Args:
        object_name: 对象名称
    
    Returns:
        对象信息字典
    """
    try:
        objects = get_scene_objects()
        
        def find_object(obj_list: List[Dict], name: str) -> Optional[Dict]:
            for obj in obj_list:
                if obj["name"] == name:
                    return obj
                if obj["children"]:
                    found = find_object(obj["children"], name)
                    if found:
                        return found
            return None
        
        obj = find_object(objects, object_name)
        if obj:
            return JSONResponse(content=obj)
        else:
            raise HTTPException(status_code=404, detail=f"未找到对象: {object_name}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取对象信息失败: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=60123)

