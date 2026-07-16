"""
Isaac Sim Script Editor 版本 - 修复版
直接在Isaac Sim的Script Editor中运行此脚本
"""

import json
import os
import sys
from omni.isaac.core.utils.prims import create_prim
from pxr import UsdGeom, Gf, UsdLux, Usd
import omni.usd
import carb
from omni.isaac.core.utils.viewports import set_camera_view

# ==================== 配置区域 ====================
# 【重要】基准目录：项目根目录（包含 assets/ 与 warehouse_scenes/）
# 推荐：不改代码，直接在系统环境变量里设置：
#   SCENE_BASE_DIR=C:/Users/ASUS/Desktop/scene
# 如果不设置，会自动从当前工作目录向上尝试寻找项目根目录。
def _detect_base_dir() -> str:
    env = os.getenv("SCENE_BASE_DIR")
    if env:
        return env
    cwd = os.getcwd()
    # 在 Script Editor 里 cwd 通常就是 Isaac Sim 的运行目录；这里向上找一次项目根
    try:
        import pathlib
        p = pathlib.Path(cwd).resolve()
        for candidate in [p] + list(p.parents):
            if (candidate / "assets").exists() and (candidate / "warehouse_scenes").exists():
                return str(candidate)
    except Exception:
        pass
    return cwd

BASE_DIR = _detect_base_dir().replace("\\", "/")

# 让 Script Editor 执行的临时脚本也能 import 本项目（例如 `import func.shelf`）
if BASE_DIR and BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
try:
    # Script Editor 反复运行时，sys.modules 里可能缓存了别的同名 func 包；清理后再导入
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

# 修改这里的JSON文件路径（相对路径，相对于BASE_DIR）
#JSON_FILE = "warehouse_scenes/symmetric_layout.json"
#JSON_FILE = "warehouse_scenes/l_shape_layout.json"
#JSON_FILE = "warehouse_scenes/original_layout.json"
JSON_FILE = "warehouse_scenes/dual_channel_layout.json"
#JSON_FILE = "warehouse_scenes/center_island_layout.json"

# 货架USD文件路径（相对路径，相对于BASE_DIR）
SHELF_USD_PATH = "assets/shelf.usd"

# 搬运车USD文件路径（相对路径，相对于BASE_DIR）
FORKLIFT_USD_PATH = "assets/Forklift.usd"

# 机械臂USD文件路径（相对路径，相对于BASE_DIR）
ROBOT_ARM_USD_PATH = "assets/factory_franka.usd"

# 传送带USD文件路径（相对路径，相对于BASE_DIR）
CONVEYOR_USD_PATH = "assets/ConveyorBelt_A05.usd"

# 打包桌USD文件路径（相对路径，相对于BASE_DIR）
PACKING_TABLE_USD_PATH = "assets/table.usd"

# 托盘USD文件路径（相对路径，相对于BASE_DIR）
CRATE_USD_PATH = "assets/container.usd"

# 箱子USD文件路径（相对路径，相对于BASE_DIR）
BOX_USD_PATH = "assets/Box.usd"
# ==================================================

class WarehouseSceneBuilder:
    """仓库场景构建器 - Script Editor版本"""
    
    def __init__(self, json_file, base_dir):
        """
        初始化场景构建器
        
        Args:
            json_file: JSON文件路径（相对或绝对路径）
            base_dir: 基准目录（必须指定）
        """
        self.base_dir = base_dir
        
        # 处理JSON文件路径（如果是相对路径，转换为绝对路径）
        if not os.path.isabs(json_file):
            self.json_file = os.path.join(base_dir, json_file)
        else:
            self.json_file = json_file
        
        self.scene_data = None
        self.stage = None
        self.box_original_size = None  # 存储Box.usd的原始尺寸
        self.crate_original_size = None  # 存储Crate.usd的原始尺寸
    
    def get_usd_bounding_box(self, usd_file_path):
        """获取USD文件的边界框尺寸"""
        try:
            # 处理相对路径
            if not os.path.isabs(usd_file_path):
                usd_file_path = os.path.join(self.base_dir, usd_file_path)
            usd_file_path = os.path.abspath(usd_file_path)
            
            # 打开USD文件
            temp_stage = Usd.Stage.Open(usd_file_path)
            if not temp_stage:
                return None
            
            # 获取默认prim或根prim
            root_prim = temp_stage.GetDefaultPrim()
            if not root_prim:
                root_prim = temp_stage.GetPseudoRoot()
            
            # 计算边界框
            bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render'])
            bbox = bbox_cache.ComputeWorldBound(root_prim)
            
            if bbox:
                bbox_range = bbox.ComputeAlignedRange()
                size = bbox_range.GetSize()
                return (size[0], size[1], size[2])
            
            return None
        except Exception as e:
            carb.log_warn(f"无法获取USD文件尺寸: {e}")
            return None
        
    def load_json(self):
        """加载JSON场景文件"""
        if not os.path.exists(self.json_file):
            carb.log_error(f"文件不存在: {self.json_file}")
            return False
            
        with open(self.json_file, 'r', encoding='utf-8') as f:
            self.scene_data = json.load(f)
        
        scene_name = self.scene_data.get('scene_name', 'Unknown')
        carb.log_info(f"已加载场景: {scene_name}")
        print(f"✓ 已加载场景: {scene_name}")
        return True
    
    def create_cube_prim(self, path, position, size, color, rotation_z=0):
        """创建长方体"""
        # 如果prim已存在，先删除
        if self.stage.GetPrimAtPath(path):
            self.stage.RemovePrim(path)
        
        # 创建Cube几何体
        cube_prim = create_prim(
            prim_path=path,
            prim_type="Cube",
        )
        
        cube = UsdGeom.Cube(cube_prim)
        
        # 设置大小（Cube默认大小是2，需要缩放）
        cube.GetSizeAttr().Set(1.0)
        
        # 设置Transform
        xformable = UsdGeom.Xformable(cube_prim)
        
        # 检查是否已经有transform操作
        existing_ops = xformable.GetOrderedXformOps()
        
        if not existing_ops:
            # 没有现有操作，添加新的
            translate_op = xformable.AddTranslateOp()
            translate_op.Set(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
            
            # 如果有旋转，先添加旋转操作
            if rotation_z != 0:
                rotate_op = xformable.AddRotateZOp()
                rotate_op.Set(float(rotation_z))
            
            scale_op = xformable.AddScaleOp()
            scale_op.Set(Gf.Vec3d(float(size[0]), float(size[1]), float(size[2])))
        else:
            # 使用现有操作
            for op in existing_ops:
                op_name = op.GetOpName()
                if 'translate' in op_name:
                    op.Set(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
                elif 'scale' in op_name:
                    op.Set(Gf.Vec3d(float(size[0]), float(size[1]), float(size[2])))
                elif 'rotateZ' in op_name or 'rotate' in op_name.lower():
                    op.Set(float(rotation_z))
        
        # 设置颜色
        cube.GetDisplayColorAttr().Set([Gf.Vec3f(float(color[0]), float(color[1]), float(color[2]))])
        
        return cube_prim
    
    def load_usd_reference(self, path, usd_file_path, position, rotation_z=0, scale=None):
        """加载USD文件作为引用并设置transform
        
        注意：usd_file_path应该已经是绝对路径（由调用者处理）
        """
        # 如果prim已存在，先删除
        if self.stage.GetPrimAtPath(path):
            self.stage.RemovePrim(path)
        
        # 创建Xform prim
        xform_prim = self.stage.DefinePrim(path, "Xform")
        xformable = UsdGeom.Xformable(xform_prim)
        
        # 添加USD文件引用
        references = xform_prim.GetReferences()
        # 确保是绝对路径并转换路径分隔符
        abs_usd_path = os.path.abspath(usd_file_path).replace("\\", "/")
        references.AddReference(abs_usd_path)
        
        # 设置Transform
        xformable.ClearXformOpOrder()
        
        # 平移
        translate_op = xformable.AddTranslateOp()
        translate_op.Set(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
        
        # 旋转
        if rotation_z != 0:
            rotate_op = xformable.AddRotateZOp()
            rotate_op.Set(float(rotation_z))
        
        # 缩放（如果需要）
        if scale is not None:
            scale_op = xformable.AddScaleOp()
            scale_op.Set(Gf.Vec3d(float(scale[0]), float(scale[1]), float(scale[2])))
        
        return xform_prim
    
    def create_cylinder_prim(self, path, position, radius, height, color):
        """创建圆柱体"""
        # 如果prim已存在，先删除
        if self.stage.GetPrimAtPath(path):
            self.stage.RemovePrim(path)
        
        # 创建Cylinder几何体
        cylinder_prim = create_prim(
            prim_path=path,
            prim_type="Cylinder",
        )
        
        cylinder = UsdGeom.Cylinder(cylinder_prim)
        
        # 设置半径和高度
        cylinder.GetRadiusAttr().Set(float(radius))
        cylinder.GetHeightAttr().Set(float(height))
        
        # 设置Transform
        xformable = UsdGeom.Xformable(cylinder_prim)
        
        # 检查是否已经有transform操作
        existing_ops = xformable.GetOrderedXformOps()
        
        if not existing_ops:
            # 没有现有操作，添加新的
            translate_op = xformable.AddTranslateOp()
            translate_op.Set(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
        else:
            # 使用现有操作
            for op in existing_ops:
                if 'translate' in op.GetOpName():
                    op.Set(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
        
        # 设置颜色
        cylinder.GetDisplayColorAttr().Set([Gf.Vec3f(float(color[0]), float(color[1]), float(color[2]))])
        
        return cylinder_prim
        
    def create_lights(self):
        """创建灯光"""
        # 创建主光源（太阳光）
        distant_light = create_prim("/World/DistantLight", "DistantLight")
        light = UsdLux.DistantLight(distant_light)
        light.CreateIntensityAttr(3000)
        light.CreateAngleAttr(0.53)
        
        # 设置光源方向
        xformable = UsdGeom.Xformable(distant_light)
        rotate_op = xformable.AddRotateXYZOp()
        rotate_op.Set(Gf.Vec3f(-45, 45, 0))
        
        # 创建环境光（天空球）
        dome_light = create_prim("/World/DomeLight", "DomeLight")
        dome = UsdLux.DomeLight(dome_light)
        dome.CreateIntensityAttr(1000)
        
        print(f"✓ 创建灯光系统")
    
    def setup_camera(self):
        """设置相机到合适的观察位置"""
        try:
            # 仓库中心位置
            warehouse = self.scene_data.get('warehouse', {})
            dims = warehouse.get('dimensions', {})
            center_x = float(dims.get('length', 15)) / 2
            center_y = float(dims.get('width', 15)) / 2
            
            # 相机位置：从斜上方俯视仓库
            eye = Gf.Vec3d(center_x + 20, center_y - 15, 15)  # 相机位置
            target = Gf.Vec3d(center_x, center_y, 0)  # 看向仓库中心
            
            set_camera_view(eye=eye, target=target, camera_prim_path="/OmniverseKit_Persp")
            print(f"✓ 设置相机视角")
        except Exception as e:
            print(f"⚠ 相机设置失败（可手动调整）: {e}")
    
    def create_warehouse_ground(self):
        """创建仓库地面"""
        warehouse = self.scene_data.get('warehouse', {})
        dims = warehouse.get('dimensions', {})
        length = float(dims.get('length', 15))
        width = float(dims.get('width', 15))
        
        position = [length/2, width/2, -0.05]
        size = [length, width, 0.1]
        color = [0.3, 0.4, 0.5]
        
        self.create_cube_prim("/World/Ground", position, size, color)
        print(f"✓ 创建地面: {length}m x {width}m")
        
    def create_shelf(self, shelf_data, index):
        """创建货架（使用USD文件）"""
        name = shelf_data.get('name', f'shelf_{index}')
        dims = shelf_data['dimensions']
        pos = shelf_data['position']
        rotation = shelf_data.get('rotation', 0)
        
        # 位置：JSON中的position是中心点，直接使用
        # Z坐标设置为0，使货架底部在地面上（假设USD文件的原点在底部）
        position = [
            float(pos['x']),
            float(pos['y']),
            0.0
        ]
        
        # 处理USD文件路径
        if not os.path.isabs(SHELF_USD_PATH):
            shelf_usd_path = os.path.join(self.base_dir, SHELF_USD_PATH)
        else:
            shelf_usd_path = SHELF_USD_PATH
        
        # 检查USD文件是否存在
        if not os.path.exists(shelf_usd_path):
            carb.log_warn(f"货架USD文件不存在: {SHELF_USD_PATH}，使用立方体替代")
            # 如果USD文件不存在，回退到立方体
            length = float(dims['length'])
            width = float(dims['width'])
            if rotation == 90:
                size = [width, length, float(dims['height'])]
            else:
                size = [length, width, float(dims['height'])]
            color = [0.8, 0.1, 0.1]  # 红色
            self.create_cube_prim(f"/World/Shelves/{name}", position, size, color, rotation_z=rotation)
            return
        
        # 使用USD文件
        # 注意：如果USD文件的尺寸与JSON中的尺寸不同，可能需要缩放
        # 这里假设USD文件已经是正确尺寸，如果需要缩放，可以取消下面的注释并设置scale
        scale = None  # 如果需要缩放，设置为 [scale_x, scale_y, scale_z]
        
        self.load_usd_reference(
            f"/World/Shelves/{name}",
            shelf_usd_path,  # 使用处理后的绝对路径
            position,
            rotation_z=rotation,
            scale=scale
        )
        
    def create_collection_area(self, area_data, index):
        """创建集中区"""
        name = area_data.get('name', f'collection_area_{index}')
        dims = area_data['dimensions']
        pos = area_data['position']
        
        position = [
            float(pos['x']) + float(dims['length'])/2,
            float(pos['y']) + float(dims['width'])/2,
            0.01
        ]
        
        size = [
            float(dims['length']),
            float(dims['width']),
            0.02
        ]
        
        color = [0.9, 0.9, 0.1]  # 黄色
        
        self.create_cube_prim(f"/World/CollectionAreas/{name}", position, size, color)
        
    def create_conveyor(self, conveyor_data, index):
        """创建传送带（使用USD文件）"""
        name = conveyor_data.get('name', f'conveyor_{index}')
        dims = conveyor_data['dimensions']
        pos = conveyor_data['position']
        
        # JSON中的position是中心点，但USD文件的原点在底部最左边中心
        # 需要调整x坐标：中心点 - length/2 = 最左边
        # y坐标不变：都是中心
        # z坐标：原点在底部，所以z=0
        length = float(dims['length'])
        width = float(dims['width'])
        
        position = [
            float(pos['x']) - length / 2,  # 从中心点调整到最左边
            float(pos['y']),                # y方向都是中心，不变
            0.0                              # 原点在底部
        ]
        
        # 处理USD文件路径
        if not os.path.isabs(CONVEYOR_USD_PATH):
            conveyor_usd_path = os.path.join(self.base_dir, CONVEYOR_USD_PATH)
        else:
            conveyor_usd_path = CONVEYOR_USD_PATH
        
        # 检查USD文件是否存在
        if not os.path.exists(conveyor_usd_path):
            carb.log_warn(f"传送带USD文件不存在: {CONVEYOR_USD_PATH}，使用立方体替代")
            # 如果USD文件不存在，回退到立方体（立方体的原点在中心）
            size = [
                length,
                width,
                float(dims['height'])
            ]
            color = [0.7, 0.7, 0.7]  # 灰色
            # 立方体需要中心点位置
            position_cube = [
                float(pos['x']),
                float(pos['y']),
                float(dims['height'])/2
            ]
            self.create_cube_prim(f"/World/Conveyors/{name}", position_cube, size, color)
            return
        
        # 使用USD文件
        # 注意：如果USD文件的尺寸与JSON中的尺寸不同，可能需要缩放
        scale = None  # 如果需要缩放，设置为 [scale_x, scale_y, scale_z]
        
        self.load_usd_reference(
            f"/World/Conveyors/{name}",
            conveyor_usd_path,  # 使用处理后的绝对路径
            position,
            rotation_z=0,
            scale=scale
        )
        
    def create_agent(self, agent_data, index):
        """创建智能体（搬运车和机械臂都使用USD文件）"""
        name = agent_data.get('name', f'agent_{index}')
        agent_type = agent_data.get('type', 'agv')
        dims = agent_data['dimensions']
        pos = agent_data['position']
        on_table = agent_data.get('on_table', False)
        
        # 位置：JSON中的position是中心点
        # 如果机械臂在桌子上，使用JSON中的z坐标（桌面高度）
        # 如果搬运车在地面上，z坐标设置为0
        if agent_type == 'agv':
            # 搬运车在地面上，z=0
            position = [
                float(pos['x']),
                float(pos['y']),
                0.0
            ]
        else:
            # 机械臂：如果在桌子上，使用JSON中的z坐标；否则在地面上
            if on_table:
                position = [
                    float(pos['x']),
                    float(pos['y']),
                    float(pos['z'])  # 使用JSON中的z坐标（桌面高度）
                ]
            else:
                position = [
                    float(pos['x']),
                    float(pos['y']),
                    0.0
                ]
        
        # 搬运车使用USD文件
        if agent_type == 'agv':
            # 处理USD文件路径
            if not os.path.isabs(FORKLIFT_USD_PATH):
                forklift_usd_path = os.path.join(self.base_dir, FORKLIFT_USD_PATH)
            else:
                forklift_usd_path = FORKLIFT_USD_PATH
            
            # 检查USD文件是否存在
            if not os.path.exists(forklift_usd_path):
                carb.log_warn(f"搬运车USD文件不存在: {forklift_usd_path}，使用圆柱体替代")
                # 如果USD文件不存在，回退到圆柱体
                radius = float(dims['diameter']) / 2
                height = float(dims['height'])
                color = [0.1, 0.3, 0.8]  # 蓝色
                position[2] = float(dims['height'])/2  # 圆柱体需要调整z坐标
                self.create_cylinder_prim(f"/World/Agents/{name}", position, radius, height, color)
                return
            
            # 使用USD文件
            scale = None  # 如果需要缩放，设置为 [scale_x, scale_y, scale_z]
            self.load_usd_reference(
                f"/World/Agents/{name}",
                forklift_usd_path,  # 使用处理后的绝对路径
                position,
                rotation_z=0,
                scale=scale
            )
        else:
            # 处理USD文件路径
            if not os.path.isabs(ROBOT_ARM_USD_PATH):
                robot_arm_usd_path = os.path.join(self.base_dir, ROBOT_ARM_USD_PATH)
            else:
                robot_arm_usd_path = ROBOT_ARM_USD_PATH
            
            # 机械臂使用USD文件
            if not os.path.exists(robot_arm_usd_path):
                carb.log_warn(f"机械臂USD文件不存在: {robot_arm_usd_path}，使用圆柱体替代")
                # 如果USD文件不存在，回退到圆柱体
                radius = float(dims['diameter']) / 2
                height = float(dims['height'])
                color = [0.1, 0.7, 0.2]  # 绿色
                # 如果在桌子上，保持z坐标；否则调整
                if not on_table:
                    position[2] = float(dims['height'])/2  # 圆柱体需要调整z坐标
                self.create_cylinder_prim(f"/World/Agents/{name}", position, radius, height, color)
                return
            
            # 使用USD文件
            scale = None  # 如果需要缩放，设置为 [scale_x, scale_y, scale_z]
            self.load_usd_reference(
                f"/World/Agents/{name}",
                robot_arm_usd_path,  # 使用处理后的绝对路径
                position,
                rotation_z=0,
                scale=scale
            )
    
    def create_packing_table(self, table_data, index):
        """创建打包桌（使用USD文件）"""
        name = table_data.get('name', f'packing_table_{index}')
        dims = table_data['dimensions']
        pos = table_data['position']
        
        # 位置：JSON中的position是中心点（底部中心），直接使用
        # Z坐标设置为0，使底部在地面上（假设USD文件的原点在底部中心）
        position = [
            float(pos['x']),
            float(pos['y']),
            0.0
        ]
        
        # 处理USD文件路径
        if not os.path.isabs(PACKING_TABLE_USD_PATH):
            packing_table_usd_path = os.path.join(self.base_dir, PACKING_TABLE_USD_PATH)
        else:
            packing_table_usd_path = PACKING_TABLE_USD_PATH
        
        # 检查USD文件是否存在
        if not os.path.exists(packing_table_usd_path):
            carb.log_warn(f"打包桌USD文件不存在: {packing_table_usd_path}，使用立方体替代")
            # 如果USD文件不存在，回退到立方体
            size = [
                float(dims['length']),
                float(dims['width']),
                float(dims['height'])
            ]
            color = [0.5, 0.5, 0.5]  # 灰色
            position[2] = float(dims['height'])/2  # 立方体需要调整z坐标
            self.create_cube_prim(f"/World/PackingTables/{name}", position, size, color)
            return
        
        # 使用USD文件
        scale = None  # 如果需要缩放，设置为 [scale_x, scale_y, scale_z]
        
        self.load_usd_reference(
            f"/World/PackingTables/{name}",
            packing_table_usd_path,  # 使用处理后的绝对路径
            position,
            rotation_z=0,
            scale=scale
        )
    
    def create_crate(self, crate_data, index):
        """创建托盘（使用USD文件）
        注意：根据JSON中的dimensions缩放container.usd到指定尺寸
        """
        name = crate_data.get('name', f'crate_{index}')
        dims = crate_data.get('dimensions', {})
        pos = crate_data['position']
        
        # 位置：JSON中的position已经是实际位置（在桌面上），直接使用
        position = [
            float(pos['x']),
            float(pos['y']),
            float(pos['z'])  # 使用JSON中的z坐标（在桌面上）
        ]
        
        # 从JSON读取期望的托盘尺寸
        desired_length = float(dims.get('length', 1.0))
        desired_width = float(dims.get('width', 1.0))
        desired_height = float(dims.get('height', 0.3))
        
        # 处理USD文件路径
        if not os.path.isabs(CRATE_USD_PATH):
            usd_file_path = os.path.join(self.base_dir, CRATE_USD_PATH)
        else:
            usd_file_path = CRATE_USD_PATH
        
        if not os.path.exists(usd_file_path):
            carb.log_warn(f"托盘USD文件不存在: {usd_file_path}，使用立方体替代")
            # 如果USD文件不存在，回退到立方体
            size = [desired_length, desired_width, desired_height]
            color = [0.9, 0.9, 0.1]  # 黄色
            self.create_cube_prim(f"/World/Crates/{name}", position, size, color)
            return
        
        # 获取container.usd的原始尺寸（只获取一次）
        if self.crate_original_size is None:
            self.crate_original_size = self.get_usd_bounding_box(usd_file_path)
            if self.crate_original_size:
                print(f"\n📦 检测到 container.usd 原始尺寸: {self.crate_original_size[0]:.3f} × "
                      f"{self.crate_original_size[1]:.3f} × {self.crate_original_size[2]:.3f} 米")
            else:
                # 如果无法检测，使用从 extent 测量的实际值
                # extent: [(-0.3058352, -0.2057982, -1.1168363e-9), (0.3058352, 0.2057982, 0.09034392)]
                # 真实尺寸: 0.6116704m × 0.4115964m × 0.09034392m
                self.crate_original_size = (0.6116704, 0.4115964, 0.09034392)
                print(f"\n📦 使用 container.usd 实际尺寸: 0.612m × 0.412m × 0.090m")
                print(f"   (从 extent 测量)")
        
        CRATE_ORIGINAL_LENGTH = self.crate_original_size[0]
        CRATE_ORIGINAL_WIDTH = self.crate_original_size[1]
        CRATE_ORIGINAL_HEIGHT = self.crate_original_size[2]
        
        scale = [
            desired_length / CRATE_ORIGINAL_LENGTH,
            desired_width / CRATE_ORIGINAL_WIDTH,
            desired_height / CRATE_ORIGINAL_HEIGHT
        ]
        
        print(f"✓ 托盘 {name}: 期望尺寸=({desired_length:.3f}×{desired_width:.3f}×{desired_height:.3f}), "
              f"原始尺寸=({CRATE_ORIGINAL_LENGTH:.3f}×{CRATE_ORIGINAL_WIDTH:.3f}×{CRATE_ORIGINAL_HEIGHT:.3f}), "
              f"缩放=({scale[0]:.2f}, {scale[1]:.2f}, {scale[2]:.2f})")
        
        self.load_usd_reference(
            f"/World/Crates/{name}",
            usd_file_path,
            position,
            rotation_z=0,
            scale=scale
        )
    
    def create_box(self, box_data, index):
        """创建箱子（使用USD文件）
        注意：Box.usd 的原点在箱子底部中心
        箱子放在托盘里面，z坐标等于托盘z坐标（都是底部）
        
        重要：根据JSON中的dimensions缩放Box.usd到指定尺寸
        """
        name = box_data.get('name', f'box_{index}')
        dims = box_data.get('dimensions', {})
        pos = box_data['position']
        
        # 位置：JSON中的position是箱子底部位置，直接使用
        position = [
            float(pos['x']),
            float(pos['y']),
            float(pos['z'])
        ]
        
        # 从JSON读取期望的箱子尺寸
        desired_length = float(dims.get('length', 0.2))
        desired_width = float(dims.get('width', 0.2))
        desired_height = float(dims.get('height', 0.6))
        
        # 处理USD文件路径
        if not os.path.isabs(BOX_USD_PATH):
            usd_file_path = os.path.join(self.base_dir, BOX_USD_PATH)
        else:
            usd_file_path = BOX_USD_PATH
        
        if not os.path.exists(usd_file_path):
            carb.log_warn(f"箱子USD文件不存在: {usd_file_path}，使用立方体替代")
            # 如果USD文件不存在，回退到立方体
            size = [desired_length, desired_width, desired_height]
            color = [0.8, 0.6, 0.4]  # 棕色
            # 立方体原点在中心，需要调整z坐标
            position[2] += desired_height / 2
            self.create_cube_prim(f"/World/Boxes/{name}", position, size, color)
            return
        
        # 获取Box.usd的原始尺寸（只获取一次）
        if self.box_original_size is None:
            self.box_original_size = self.get_usd_bounding_box(usd_file_path)
            if self.box_original_size:
                print(f"\n📦 检测到 Box.usd 原始尺寸: {self.box_original_size[0]:.3f} × "
                      f"{self.box_original_size[1]:.3f} × {self.box_original_size[2]:.3f} 米")
            else:
                # 如果无法检测，使用从 extent 测量的实际值
                # extent: [(-19, -12.5, -0.0), (19, 12.5, 14.875)] 单位：厘米
                # 真实尺寸: 38cm × 25cm × 14.875cm = 0.38m × 0.25m × 0.14875m
                self.box_original_size = (0.38, 0.25, 0.14875)
                print(f"\n📦 使用 Box.usd 实际尺寸: 0.38m × 0.25m × 0.14875m")
                print(f"   (从 extent 测量: 38cm × 25cm × 14.875cm)")
        
        BOX_ORIGINAL_LENGTH = self.box_original_size[0]
        BOX_ORIGINAL_WIDTH = self.box_original_size[1]
        BOX_ORIGINAL_HEIGHT = self.box_original_size[2]
        
        scale = [
            desired_length / BOX_ORIGINAL_LENGTH,
            desired_width / BOX_ORIGINAL_WIDTH,
            desired_height / BOX_ORIGINAL_HEIGHT
        ]
        
        print(f"✓ 箱子 {name}: 期望尺寸=({desired_length:.3f}×{desired_width:.3f}×{desired_height:.3f}), "
              f"原始尺寸=({BOX_ORIGINAL_LENGTH:.3f}×{BOX_ORIGINAL_WIDTH:.3f}×{BOX_ORIGINAL_HEIGHT:.3f}), "
              f"缩放=({scale[0]:.2f}, {scale[1]:.2f}, {scale[2]:.2f})")
        
        self.load_usd_reference(
            f"/World/Boxes/{name}",
            usd_file_path,
            position,
            rotation_z=0,
            scale=scale
        )
        
    def build_scene(self):
        """构建完整场景"""
        print("\n" + "="*50)
        print("开始构建仓库场景...")
        print("="*50)
        
        # 获取当前stage
        self.stage = omni.usd.get_context().get_stage()
        
        # 清理旧的World节点（如果存在）
        if self.stage.GetPrimAtPath("/World"):
            self.stage.RemovePrim("/World")
        
        # 创建World根节点
        create_prim("/World", "Xform")
        
        # 创建灯光
        self.create_lights()
        
        # 创建仓库地面
        self.create_warehouse_ground()
        
        # 创建objects
        objects = self.scene_data.get('objects', [])
        shelf_count = 0
        conveyor_count = 0
        area_count = 0
        table_count = 0
        crate_count = 0
        box_count = 0
        
        for idx, obj in enumerate(objects):
            obj_type = obj.get('type')
            
            if obj_type == 'shelf':
                self.create_shelf(obj, idx)
                shelf_count += 1
            elif obj_type == 'collection_area':
                self.create_collection_area(obj, idx)
                area_count += 1
            elif obj_type == 'conveyor':
                self.create_conveyor(obj, idx)
                conveyor_count += 1
            elif obj_type == 'packing_table':
                self.create_packing_table(obj, idx)
                table_count += 1
            elif obj_type == 'crate':
                self.create_crate(obj, idx)
                crate_count += 1
            elif obj_type == 'box':
                self.create_box(obj, idx)
                box_count += 1
                
        print(f"✓ 创建货架: {shelf_count} 个")
        print(f"✓ 创建集中区: {area_count} 个")
        print(f"✓ 创建传送带: {conveyor_count} 个")
        print(f"✓ 创建打包桌: {table_count} 个")
        print(f"✓ 创建托盘: {crate_count} 个")
        print(f"✓ 创建箱子: {box_count} 个")
        
        # 创建agents
        agents = self.scene_data.get('agents', [])
        agv_count = 0
        arm_count = 0
        
        for idx, agent in enumerate(agents):
            self.create_agent(agent, idx)
            if agent.get('type') == 'agv':
                agv_count += 1
            else:
                arm_count += 1
                
        print(f"✓ 创建搬运车: {agv_count} 个")
        print(f"✓ 创建机械臂: {arm_count} 个")
        
        # 设置相机视角
        self.setup_camera()
        
        print("="*50)
        print("场景构建完成！✓")
        print("="*50 + "\n")
        
    def run(self):
        """运行场景构建"""
        if self.load_json():
            self.build_scene()
            return True
        return False


# ==================== 主执行代码 ====================
def main():
    """主函数 - 在Script Editor中执行"""
    
    print("\n" + "="*60)
    print("    Isaac Sim 仓库场景生成器 v2.1")
    print("="*60)
    
    # 检查BASE_DIR配置
    if not os.path.exists(BASE_DIR):
        print(f"\n❌ 错误: 基准目录不存在")
        print(f"   BASE_DIR: {BASE_DIR}")
        print(f"\n请在脚本顶部修改 BASE_DIR 为你的实际项目路径！")
        print(f"   例如: BASE_DIR = 'C:/Users/ASUS/Desktop/scene'")
        print("="*60 + "\n")
        return
    
    # 处理JSON文件路径（如果是相对路径，转换为绝对路径）
    if not os.path.isabs(JSON_FILE):
        json_file_path = os.path.join(BASE_DIR, JSON_FILE)
    else:
        json_file_path = JSON_FILE
    
    # 检查JSON文件路径
    if not os.path.exists(json_file_path):
        print(f"\n❌ 错误: 找不到JSON文件")
        print(f"   文件路径: {json_file_path}")
        print(f"   相对路径: {JSON_FILE}")
        print(f"   BASE_DIR: {BASE_DIR}")
        print(f"\n请检查:")
        print(f"   1. BASE_DIR 是否正确")
        print(f"   2. JSON_FILE 相对路径是否正确")
        print("="*60 + "\n")
        return
    
    print(f"\n配置信息:")
    print(f"  - 基准目录: {BASE_DIR}")
    print(f"  - 加载场景: {os.path.basename(json_file_path)}")
    
    # 构建场景（传入基准目录）
    builder = WarehouseSceneBuilder(JSON_FILE, base_dir=BASE_DIR)
    success = builder.run()
    
    if success:
        print("\n💡 查看场景的方法：")
        print("   • 鼠标中键拖动 - 旋转视角")
        print("   • Shift+鼠标中键 - 平移视角")
        print("   • 滚轮 - 缩放")
        print("   • 选中对象后按 F - 聚焦到该对象")
        print("   • Stage面板 - 查看所有对象层次结构")
        print("\n🎨 场景元素颜色：")
        print("   • 红色 = 货架")
        print("   • 黄色 = 集中区")
        print("   • 灰色 = 传送带")
        print("   • 蓝色 = 搬运车")
        print("   • 绿色 = 机械臂")
    

# 执行主函数
if __name__ == "__main__":
    main()
