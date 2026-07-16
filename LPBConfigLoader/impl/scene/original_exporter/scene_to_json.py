import json
import os
import math
import random

# 占用类型定义
EMPTY = 0          # 空地
SHELF = 1          # 货架
CONVEYOR = 2       # 传送带
AGENT = 3          # 搬运车
ROBOT_ARM = 4      # 机械臂
PACKING_TABLE = 5  # 打包桌

# 安全间距（米）- 物体之间的最小间隔
SAFETY_MARGIN = 0.2

# 箱子型号定义（长×宽×高，单位：米）
BOX_TYPES = {
    'large': {'length': 0.4, 'width': 0.4, 'height': 0.6, 'name': 'L'},
    'medium': {'length': 0.3, 'width': 0.3, 'height': 0.5, 'name': 'M'},
    'small': {'length': 0.25, 'width': 0.25, 'height': 0.4, 'name': 'S'},
    'long': {'length': 0.5, 'width': 0.3, 'height': 0.5, 'name': 'Long'},
    'wide': {'length': 0.35, 'width': 0.45, 'height': 0.5, 'name': 'Wide'},
}

class WarehouseGrid:
    """仓库网格管理系统"""
    
    def __init__(self, size=40):
        """初始化网格，每个网格1m x 1m，默认40x40米"""
        self.size = size
        self.grid = [[EMPTY for _ in range(size)] for _ in range(size)]  # 0表示空地
        self.objects = []  # 存储所有物体
        self.agents = []   # 存储所有智能体
        self.packing_tables = []  # 存储打包桌
        self.crates = []  # 存储托盘
        self.boxes = []  # 存储箱子
    
    def is_valid_position(self, x, y):
        """检查坐标是否在有效范围内"""
        return 0 <= x < self.size and 0 <= y < self.size
    
    def is_area_free(self, center_x, center_y, length, width, rotation=0, margin=SAFETY_MARGIN):
        """检查指定区域是否空闲（以中心点为基准）
        
        Args:
            center_x, center_y: 中心点坐标
            length: X方向长度（米）
            width: Y方向宽度（米）
            rotation: 旋转角度（0或90度）
            margin: 安全边距（米），会扩大检查区域
        """
        if rotation == 90:
            length, width = width, length
        
        # 计算占用的网格范围（以中心点为基准），加上安全边距
        half_length = (length + margin) / 2
        half_width = (width + margin) / 2
        
        # 使用 floor 和 ceil 确保完全覆盖区域
        start_x = math.floor(center_x - half_length)
        end_x = math.ceil(center_x + half_length)
        start_y = math.floor(center_y - half_width)
        end_y = math.ceil(center_y + half_width)
        
        # 检查边界
        if start_x < 0 or end_x > self.size or start_y < 0 or end_y > self.size:
            return False
        
        # 检查占用
        for x in range(start_x, end_x):
            for y in range(start_y, end_y):
                if self.is_valid_position(x, y) and self.grid[y][x] != EMPTY:
                    return False
        return True
    
    def mark_area(self, center_x, center_y, length, width, obj_type, rotation=0):
        """标记指定区域为占用（以中心点为基准）
        注意：标记时不加安全边距，只标记实际占用的网格
        """
        if rotation == 90:
            length, width = width, length
        
        half_length = length / 2
        half_width = width / 2
        
        # 使用 floor 和 ceil 确保完全覆盖实际占用区域
        start_x = math.floor(center_x - half_length)
        end_x = math.ceil(center_x + half_length)
        start_y = math.floor(center_y - half_width)
        end_y = math.ceil(center_y + half_width)
        
        for x in range(start_x, end_x):
            for y in range(start_y, end_y):
                if self.is_valid_position(x, y):
                    self.grid[y][x] = obj_type
    
    def place_shelf(self, name, center_x, center_y, rotation=0, height=6.0):
        """放置货架（1m宽 x 16m长 x 6m高）"""
        length, width = 16, 1
        
        if not self.is_area_free(center_x, center_y, length, width, rotation):
            return False
        
        self.mark_area(center_x, center_y, length, width, SHELF, rotation)
        
        shelf = {
            "type": "shelf",
            "name": name,
            "dimensions": {"length": length, "width": width, "height": height},
            "position": {"x": center_x, "y": center_y, "z": 0},
            "color": "red"
        }
        if rotation != 0:
            shelf["rotation"] = rotation
        
        self.objects.append(shelf)
        return True
    
    def place_conveyor_segment(self, name, center_x, center_y):
        """放置传送带小段（2m长 x 1m宽）
        
        Args:
            name: 传送带名称
            center_x, center_y: 中心点坐标（米）
        """
        length, width = 2, 1
        
        # 使用统一的区域检查（不加安全边距，传送带和其他物体可以紧密相邻）
        if not self.is_area_free(center_x, center_y, length, width, margin=0):
            return False
        
        # 标记占用区域
        self.mark_area(center_x, center_y, length, width, CONVEYOR)
        
        segment = {
            "type": "conveyor",
            "name": name,
            "dimensions": {"length": length, "width": width, "height": 0.1},
            "position": {"x": center_x, "y": center_y, "z": 0},
            "color": "lightgray"
        }
        self.objects.append(segment)
        return True
    
    def place_conveyor_line(self, start_x, end_x, y):
        """放置传送带线（从start_x到end_x，y坐标固定）
        
        Args:
            start_x: 起始x坐标（网格坐标）
            end_x: 结束x坐标（网格坐标）
            y: y坐标（网格坐标）
        
        每个传送带段是2m长，中心点在网格中心，步长为2
        """
        segment_index = 0
        x = start_x
        
        while x + 2 <= end_x:  # 确保有足够空间放置2m长的段
            # 传送带段中心点：(x+1, y+0.5) - x方向占用[x, x+2)，y方向占用[y, y+1)
            center_x = x + 1.0
            center_y = y + 0.5
            
            if self.place_conveyor_segment(f"Conveyor_{segment_index}", center_x, center_y):
                segment_index += 1
            x += 2  # 每个段占用2个网格
    
    def place_packing_table(self, name, center_x, center_y):
        """放置打包桌（2m x 2m，中心点在底部中心）"""
        length, width = 2, 2
        
        if not self.is_area_free(center_x, center_y, length, width):
            return False
        
        self.mark_area(center_x, center_y, length, width, PACKING_TABLE)
        
        table = {
            "type": "packing_table",
            "name": name,
            "dimensions": {"length": length, "width": width, "height": 1.0},
            "position": {"x": center_x, "y": center_y, "z": 0},
            "color": "gray"
        }
        self.packing_tables.append(table)
        return True
    
    def place_robot_arm(self, name, center_x, center_y, rotation=0):
        """在地面上放置机械臂
        
        Args:
            name: 机械臂名称
            center_x, center_y: 中心位置（米）
            rotation: 旋转角度（度）
        """
        robot_arm = {
            "type": "robot_arm",
            "name": name,
            "dimensions": {"diameter": 0.8, "height": 1},
            "position": {"x": center_x, "y": center_y, "z": 0},
            "rotation": rotation,
            "color": "green"
        }
        self.agents.append(robot_arm)
        return True
    
    def place_crate(self, name, center_x, center_y, random_size=True):
        """在地面上放置托盘
        
        Args:
            name: 托盘名称
            center_x, center_y: 中心位置（米）
            random_size: 如果为True，随机选择托盘尺寸（1.2m×1m 或 1.2m×1.2m）
        """
        # 随机选择托盘尺寸
        if random_size:
            crate_sizes = [
                {"length": 1.2, "width": 1.0, "height": 0.15},
                {"length": 1.2, "width": 1.2, "height": 0.15}
            ]
            crate_dims = random.choice(crate_sizes)
            print(f"  托盘 {name} 随机尺寸: {crate_dims['length']}m × {crate_dims['width']}m")
        else:
            crate_dims = {"length": 1.0, "width": 1.0, "height": 0.15}
        
        crate = {
            "type": "crate",
            "name": name,
            "dimensions": crate_dims,
            "position": {"x": center_x, "y": center_y, "z": 0},
            "color": "yellow"
        }
        self.crates.append(crate)
        return True
    
    def place_box_on_shelf(self, shelf_name, box_name, shelf_center_x, shelf_center_y, 
                          level=1, offset_x=0, offset_y=0, rotation=0,
                          box_length=0.3, box_width=0.3, box_height=0.5):
        """在货架指定层上放置箱子
        
        Args:
            shelf_name: 货架名称
            box_name: 箱子名称
            shelf_center_x, shelf_center_y: 货架中心位置
            level: 层数 (1=1.3m, 2=2.8m, 3=4.3m, 4=5.8m)
            offset_x, offset_y: 相对于货架中心的偏移（米）
            rotation: 货架旋转角度（0或90度）
            box_length, box_width, box_height: 箱子尺寸（米）
        """
        # 货架层高对应表
        # 新货架层高：第一层 1.3m；之后每层 +1.5m
        # 注意：整条大货架由 4 个“格子/段”连接而成，只有最左侧第 1 段的第 3 层是 4.3m，其余段第 3 层为 4.1m
        level_heights = {1: 1.3, 2: 2.8, 3: 4.1, 4: 5.962}
        
        if level not in level_heights:
            print(f"错误：层数 {level} 无效，应为 1-4")
            return False
        
        # 获取该层的高度
        z_height = level_heights[level]
        if level == 3:
            # 大货架沿长度方向（offset_x）分 4 段，每段 4m：[-8,-4],[-4,0],[0,4],[4,8]
            shelf_length = 16.0
            seg_len = shelf_length / 4.0  # 4m
            seg1_min = -shelf_length / 2.0           # -8
            seg1_max = -shelf_length / 2.0 + seg_len # -4
            # “从左往右第一个格子” = 最左侧第 1 段
            if seg1_min <= float(offset_x) < seg1_max:
                z_height = 4.3
        
        # 计算箱子位置（offset_x/offset_y 视为货架局部坐标：x=长度方向，y=宽度方向）
        if rotation == 90:
            # 绕Z轴旋转90度：(x,y) -> (-y, x)
            box_x = shelf_center_x - offset_y
            box_y = shelf_center_y + offset_x
        else:
            box_x = shelf_center_x + offset_x
            box_y = shelf_center_y + offset_y
        
        box = {
            "type": "box",
            "name": box_name,
            "dimensions": {"length": box_length, "width": box_width, "height": box_height},
            "position": {"x": box_x, "y": box_y, "z": z_height},
            "usd_file": "Box.usd",
            "on_shelf": True,
            "shelf_name": shelf_name,
            "level": level,
            # 让导出脚本把箱子也一起旋转（与货架对齐）
            "rotation": rotation
        }
        self.boxes.append(box)
        return True
    
    def place_mixed_boxes_on_shelf(self, shelf_name, shelf_center_x, shelf_center_y,
                                   level=1, rotation=0, box_types=None, max_stacks=2, spacing=0.05):
        """在货架指定层上放置混合型号的箱子（支持堆叠）
        
        Args:
            shelf_name: 货架名称
            shelf_center_x, shelf_center_y: 货架中心位置
            level: 货架层数 (1-4，指货架的第几层横板)
            rotation: 货架旋转角度
            box_types: 箱子型号列表，如果为None则随机选择
            max_stacks: 每个位置最多堆叠几层箱子
            spacing: 箱子间距
        
        Returns:
            int: 成功放置的箱子总数
        """
        # 货架有效尺寸（16m长货架）
        # 重要：左右两端是卸货点区域，禁止放箱子 → 长度方向两端留空
        shelf_length = 16.0
        shelf_width = 0.8    # 可用宽度
        end_exclusion = 1.5  # 左右端禁放区（米），可按实际再调
        structural_margin = 0.2  # 结构/护栏等边缘留空
        
        # 新货架层高：第一层 1.3m；之后每层 +1.5m
        # 默认第 3 层为 4.1m；但“从左往右第 1 段”的第 3 层为 4.3m（见下方 _base_z_for_x）
        level_heights = {1: 1.3, 2: 2.8, 3: 4.1, 4: 5.962}
        if level not in level_heights:
            return 0
        
        base_z = level_heights[level]

        def _base_z_for_x(local_x: float) -> float:
            """按货架长度方向 local_x（局部坐标）返回该层的基准高度。"""
            if level != 3:
                return base_z
            shelf_length = 16.0
            seg_len = shelf_length / 4.0  # 4m
            seg1_min = -shelf_length / 2.0           # -8
            seg1_max = -shelf_length / 2.0 + seg_len # -4
            return 4.3 if (seg1_min <= float(local_x) < seg1_max) else base_z
        
        def _local_to_world(dx, dy):
            # dx/dy 是货架局部坐标偏移：dx=长度方向，dy=宽度方向
            if rotation == 90:
                return (shelf_center_x - dy, shelf_center_y + dx)
            return (shelf_center_x + dx, shelf_center_y + dy)
        
        # 如果没有指定箱子类型，随机生成
        if box_types is None:
            # 根据货架宽度选择合适的箱子组合
                box_types = random.choice([
                    ['medium', 'medium', 'medium', 'small'],
                    ['medium', 'small', 'small', 'medium'],
                    ['small', 'small', 'small', 'small', 'small'],
                ])
        
        # 货架有效区域（相对于货架中心，局部坐标）
        # x=长度方向；y=宽度方向
        shelf_x_min = -shelf_length / 2 + end_exclusion + structural_margin + spacing
        shelf_x_max =  shelf_length / 2 - end_exclusion - structural_margin - spacing
        shelf_y_min = -shelf_width  / 2 + structural_margin + spacing
        shelf_y_max =  shelf_width  / 2 - structural_margin - spacing

        # 第4层特殊：只有“从左往右第3块板”可放（16m分4段，每段4m）
        # 这里按局部 x 方向划分：段3 = [0, 4]
        if level == 4:
            seg_len = shelf_length / 4.0  # 4m
            seg3_min = -shelf_length / 2.0 + 2.0 * seg_len  # 0
            seg3_max = -shelf_length / 2.0 + 3.0 * seg_len  # 4
            shelf_x_min = max(shelf_x_min, seg3_min + structural_margin + spacing)
            shelf_x_max = min(shelf_x_max, seg3_max - structural_margin - spacing)
        
        # 第一层：在货架横板上放置箱子
        first_layer_boxes = []  # 存储第一层箱子的信息
        occupied_areas = []
        
        def is_position_free_on_shelf(x, y, box_len, box_wid, check_areas):
            """检查货架上的位置是否可用"""
            box_x_min = x - box_len / 2
            box_x_max = x + box_len / 2
            box_y_min = y - box_wid / 2
            box_y_max = y + box_wid / 2
            
            # 检查是否在货架范围内
            if (box_x_min < shelf_x_min or box_x_max > shelf_x_max or
                box_y_min < shelf_y_min or box_y_max > shelf_y_max):
                return False
            
            # 检查是否与其他箱子重叠
            for ox_min, ox_max, oy_min, oy_max in check_areas:
                if not (box_x_max + spacing <= ox_min or 
                       box_x_min - spacing >= ox_max or
                       box_y_max + spacing <= oy_min or 
                       box_y_min - spacing >= oy_max):
                    return False
            
            return True
        
        total_placed = 0
        
        # 放置第一层箱子（底层）
        for idx, box_type in enumerate(box_types):
            if box_type not in BOX_TYPES:
                continue
            
            box_spec = BOX_TYPES[box_type]
            box_len = box_spec['length']
            box_wid = box_spec['width']
            box_hgt = box_spec['height']
            
            # 扫描货架寻找可放置位置
            placed = False
            step = 0.05
            
            y = shelf_y_min + box_wid / 2
            while y + box_wid / 2 <= shelf_y_max and not placed:
                x = shelf_x_min + box_len / 2
                while x + box_len / 2 <= shelf_x_max and not placed:
                    if is_position_free_on_shelf(x, y, box_len, box_wid, occupied_areas):
                        box_x, box_y = _local_to_world(x, y)
                        
                        box_name = f"Box_{shelf_name}_L{level}_S1_{box_spec['name']}_{idx+1}"
                        
                        z_height = _base_z_for_x(x)
                        box = {
                            "type": "box",
                            "name": box_name,
                            "dimensions": {"length": box_spec['length'], "width": box_spec['width'], "height": box_hgt},
                            "position": {"x": box_x, "y": box_y, "z": z_height},
                            "usd_file": "Box.usd",
                            "on_shelf": True,
                            "shelf_name": shelf_name,
                            "level": level,
                            "stack": 1,
                            "box_type": box_type,
                            "rotation": rotation
                        }
                        self.boxes.append(box)
                        total_placed += 1
                        
                        # 记录第一层箱子信息（用于堆叠）
                        first_layer_boxes.append({
                            'x': x, 'y': y,
                            'x_min': x - box_len / 2,
                            'x_max': x + box_len / 2,
                            'y_min': y - box_wid / 2,
                            'y_max': y + box_wid / 2,
                            'height': box_hgt,
                            'box_type': box_type
                        })
                        
                        occupied_areas.append((
                            x - box_len / 2,
                            x + box_len / 2,
                            y - box_wid / 2,
                            y + box_wid / 2
                        ))
                        
                        placed = True
                    
                    x += step
                y += step
        
        # 堆叠第二层及以上（在第一层箱子上方）
        if max_stacks > 1 and len(first_layer_boxes) > 0:
            # 随机选择一些位置堆叠小箱子
            stack_count = min(max_stacks - 1, len(first_layer_boxes))
            stack_positions = random.sample(first_layer_boxes, min(2, stack_count))  # 最多堆叠2个位置
            
            for stack_base in stack_positions:
                # 在这个位置上堆叠小箱子
                stack_box_type = 'small'  # 上层只放小箱子
                if stack_box_type in BOX_TYPES:
                    box_spec = BOX_TYPES[stack_box_type]
                    
                    # 检查是否有足够空间
                    stack_box_len = box_spec['length']
                    stack_box_wid = box_spec['width']
                    
                    base_len = (stack_base['x_max'] - stack_base['x_min'])
                    base_wid = (stack_base['y_max'] - stack_base['y_min'])
                    
                    if stack_box_len <= base_len and stack_box_wid <= base_wid:
                        # 计算堆叠箱子的位置
                        stack_z = _base_z_for_x(stack_base['x']) + stack_base['height']
                        
                        # 使用与底层箱子相同的坐标变换
                        stack_box_x, stack_box_y = _local_to_world(stack_base['x'], stack_base['y'])
                        
                        box_name = f"Box_{shelf_name}_L{level}_S2_{box_spec['name']}_stacked"
                        
                        box = {
                            "type": "box",
                            "name": box_name,
                            "dimensions": {"length": box_spec['length'], "width": box_spec['width'], "height": box_spec['height']},
                            "position": {"x": stack_box_x, "y": stack_box_y, "z": stack_z},
                            "usd_file": "Box.usd",
                            "on_shelf": True,
                            "shelf_name": shelf_name,
                            "level": level,
                            "stack": 2,
                            "box_type": stack_box_type,
                            "rotation": rotation
                        }
                        self.boxes.append(box)
                        total_placed += 1
        
        return total_placed
    
    def place_boxes_grid_on_shelf(self, shelf_name, shelf_center_x, shelf_center_y,
                                  level=1, rows=1, cols=3, rotation=0,
                                  box_length=0.3, box_width=0.3, box_height=0.5,
                                  spacing=0.1):
        """在货架指定层上网格化放置多个箱子（旧版本，保留兼容性）
        
        Args:
            shelf_name: 货架名称
            shelf_center_x, shelf_center_y: 货架中心位置
            level: 层数 (1-4)
            rows: 行数（深度方向）
            cols: 列数（长度方向）
            rotation: 货架旋转角度
            box_length, box_width, box_height: 箱子尺寸
            spacing: 箱子间距
        
        货架尺寸：16m长 × 1m宽
        建议：cols=10-15, rows=1-2
        """
        # 货架有效尺寸（扣除竖板等结构）
        shelf_length = 15.4  # 可用长度
        shelf_width = 0.8    # 可用宽度
        
        # 计算网格总尺寸
        total_length = cols * box_length + (cols - 1) * spacing
        total_width = rows * box_width + (rows - 1) * spacing
        
        # 检查是否超出货架
        if rotation == 90:
            if total_length > shelf_width or total_width > shelf_length:
                print(f"⚠️  警告：箱子网格超出货架范围（旋转90度）")
                print(f"   网格尺寸: {total_length:.2f}m × {total_width:.2f}m")
                print(f"   货架尺寸: {shelf_width:.2f}m × {shelf_length:.2f}m")
        else:
            if total_length > shelf_length or total_width > shelf_width:
                print(f"⚠️  警告：箱子网格超出货架范围")
                print(f"   网格尺寸: {total_length:.2f}m × {total_width:.2f}m")
                print(f"   货架尺寸: {shelf_length:.2f}m × {shelf_width:.2f}m")
        
        # 起始位置（网格左下角，相对于货架中心）
        start_x = -(total_length - box_length) / 2
        start_y = -(total_width - box_width) / 2
        
        placed_count = 0
        for row in range(rows):
            for col in range(cols):
                offset_x = start_x + col * (box_length + spacing)
                offset_y = start_y + row * (box_width + spacing)
                box_name = f"Box_{shelf_name}_L{level}_{row*cols + col + 1}"
                
                if self.place_box_on_shelf(shelf_name, box_name, 
                                          shelf_center_x, shelf_center_y,
                                          level, offset_x, offset_y, rotation,
                                          box_length, box_width, box_height):
                    placed_count += 1
        
        print(f"✓ 在 {shelf_name} 第{level}层放置了 {placed_count}/{rows*cols} 个箱子")
        return placed_count
    
    def place_box_on_crate(self, crate_name, box_name, offset_x=0, offset_y=0, layer=1, 
                           box_length=0.2, box_width=0.2, box_height=0.6):
        """在托盘上放置箱子
        
        Args:
            crate_name: 托盘名称
            box_name: 箱子名称
            offset_x: 相对于托盘中心的x偏移（米）
            offset_y: 相对于托盘中心的y偏移（米）
            layer: 层数（1为第一层，放在托盘底部）
            box_length: 箱子长度（X方向）
            box_width: 箱子宽度（Y方向）
            box_height: 箱子高度（Z方向）
        
        Returns:
            bool: 是否成功放置
            
        重要说明（新托盘模型）：
        - 托盘本体有实际高度（通常约 0.3m）
        - 箱子应放在“托盘顶面”上：箱子底部 = 托盘底部 + 托盘高度
        - Box.usd 原点在箱子底部
        """
        # 查找托盘
        crate = None
        for c in self.crates:
            if c['name'] == crate_name:
                crate = c
                break
        
        if not crate:
            print(f"错误：找不到托盘 {crate_name}")
            return False
        
        # 获取托盘信息
        crate_pos = crate['position']
        crate_dims = crate.get('dimensions', {})
        crate_height = float(crate_dims.get('height', 0.3))
        
        # 计算箱子位置
        box_x = crate_pos['x'] + offset_x
        box_y = crate_pos['y'] + offset_y
        # 第一层箱子放在托盘顶面
        box_z = crate_pos['z'] + crate_height + (layer - 1) * box_height
        
        box = {
            "type": "box",
            "name": box_name,
            "dimensions": {"length": box_length, "width": box_width, "height": box_height},
            "position": {"x": box_x, "y": box_y, "z": box_z},
            "usd_file": "Box.usd",
            "on_crate": True,
            "crate_name": crate_name,
            "layer": layer
        }
        self.boxes.append(box)
        return True
    
    def place_mixed_boxes_on_crate(self, crate_name, box_types=None, max_layers=3, spacing=0.05):
        """在托盘上放置不同型号的混合箱子（支持多层堆叠）
        
        Args:
            crate_name: 托盘名称
            box_types: 箱子型号列表，如 ['medium', 'small', 'large', 'medium']
                      如果为None，则随机选择
            max_layers: 最大堆叠层数
            spacing: 箱子间距（米）
        
        Returns:
            int: 成功放置的箱子总数量
        """
        # 查找托盘
        crate = None
        for c in self.crates:
            if c['name'] == crate_name:
                crate = c
                break
        
        if not crate:
            print(f"错误：找不到托盘 {crate_name}")
            return 0
        
        crate_dims = crate['dimensions']
        crate_pos = crate['position']
        crate_length = crate_dims['length']  # X方向
        crate_width = crate_dims['width']    # Y方向
        crate_height = float(crate_dims.get('height', 0.3))
        crate_top_z = float(crate_pos['z']) + crate_height
        
        # 如果没有指定箱子类型，随机生成混合箱子
        if box_types is None:
            # 根据托盘大小智能选择箱子组合
            if crate_length >= 1.2 and crate_width >= 1.2:
                # 大托盘：放2大2中或4中
                box_types = random.choice([
                    ['large', 'large', 'medium', 'medium'],
                    ['medium', 'medium', 'medium', 'medium'],
                    ['large', 'medium', 'small', 'small'],
                ])
            elif crate_length >= 1.2 or crate_width >= 1.0:
                # 中等托盘：放1大2中或3中
                box_types = random.choice([
                    ['large', 'medium', 'medium'],
                    ['medium', 'medium', 'medium'],
                    ['medium', 'medium', 'small', 'small'],
                ])
            else:
                # 小托盘：放中小箱子
                box_types = random.choice([
                    ['medium', 'small'],
                    ['small', 'small', 'small'],
                ])
        
        # 多层堆叠逻辑
        all_placed_boxes = []
        layers_data = []  # 每一层的箱子数据 [{box_info, x, y, x_min, x_max, y_min, y_max, height}, ...]
        
        # 托盘的有效区域（相对于托盘中心）
        crate_x_min = -crate_length / 2 + spacing
        crate_x_max = crate_length / 2 - spacing
        crate_y_min = -crate_width / 2 + spacing
        crate_y_max = crate_width / 2 - spacing
        
        total_boxes_placed = 0
        
        # 逐层放置箱子
        for current_layer in range(1, max_layers + 1):
            # 当前层的占用区域
            current_occupied = []
            layer_boxes = []
            
            # 第一层：在托盘上放置
            # 第二层及以上：在下层箱子上放置
            if current_layer == 1:
                # 第一层箱子型号（较大的箱子）
                if box_types is None:
                    if crate_length >= 1.2 and crate_width >= 1.2:
                        layer_box_types = random.choice([
                            ['large', 'large', 'medium', 'medium'],
                            ['medium', 'medium', 'medium', 'medium'],
                        ])
                    elif crate_length >= 1.2 or crate_width >= 1.0:
                        layer_box_types = random.choice([
                            ['large', 'medium', 'medium'],
                            ['medium', 'medium', 'medium'],
                        ])
                    else:
                        layer_box_types = ['medium', 'medium']
                else:
                    layer_box_types = box_types
                
                # 检查位置是否可用（相对于托盘或当前层）
                def is_position_free(x, y, box_len, box_wid, check_layer_boxes):
                    box_x_min = x - box_len / 2
                    box_x_max = x + box_len / 2
                    box_y_min = y - box_wid / 2
                    box_y_max = y + box_wid / 2
                    
                    # 第一层：检查是否在托盘范围内
                    if current_layer == 1:
                        if (box_x_min < crate_x_min or box_x_max > crate_x_max or
                            box_y_min < crate_y_min or box_y_max > crate_y_max):
                            return False
                    
                    # 检查是否与当前层的其他箱子重叠
                    for ox_min, ox_max, oy_min, oy_max in check_layer_boxes:
                        if not (box_x_max + spacing <= ox_min or 
                               box_x_min - spacing >= ox_max or
                               box_y_max + spacing <= oy_min or 
                               box_y_min - spacing >= oy_max):
                            return False
                    
                    return True
                
            else:
                # 上层箱子：放置在下层箱子上方，尺寸要小一些
                if len(layers_data) == 0:
                    break  # 没有下层，无法放置上层
                
                # 上层使用更小的箱子
                layer_box_types = random.choice([
                    ['medium', 'small'],
                    ['small', 'small', 'small'],
                    ['medium', 'medium'],
                ])
                
                # 检查位置是否有足够的支撑（下层箱子）
                def is_position_free(x, y, box_len, box_wid, check_layer_boxes):
                    box_x_min = x - box_len / 2
                    box_x_max = x + box_len / 2
                    box_y_min = y - box_wid / 2
                    box_y_max = y + box_wid / 2
                    
                    # 检查是否与当前层的其他箱子重叠
                    for ox_min, ox_max, oy_min, oy_max in check_layer_boxes:
                        if not (box_x_max + spacing <= ox_min or 
                               box_x_min - spacing >= ox_max or
                               box_y_max + spacing <= oy_min or 
                               box_y_min - spacing >= oy_max):
                            return False
                    
                    # 检查是否有下层箱子支撑（至少50%重叠）
                    support_area = 0
                    box_area = box_len * box_wid
                    
                    for prev_box in layers_data:
                        px_min, px_max = prev_box['x_min'], prev_box['x_max']
                        py_min, py_max = prev_box['y_min'], prev_box['y_max']
                        
                        # 计算重叠区域
                        overlap_x_min = max(box_x_min, px_min)
                        overlap_x_max = min(box_x_max, px_max)
                        overlap_y_min = max(box_y_min, py_min)
                        overlap_y_max = min(box_y_max, py_max)
                        
                        if overlap_x_min < overlap_x_max and overlap_y_min < overlap_y_max:
                            overlap_area = (overlap_x_max - overlap_x_min) * (overlap_y_max - overlap_y_min)
                            support_area += overlap_area
                    
                    # 需要至少50%的支撑
                    return support_area >= box_area * 0.5
            
            # 放置当前层的箱子
            for idx, box_type in enumerate(layer_box_types):
                if box_type not in BOX_TYPES:
                    continue
                
                box_spec = BOX_TYPES[box_type]
                box_len = box_spec['length']
                box_wid = box_spec['width']
                box_hgt = box_spec['height']
                
                # 尝试找到可放置的位置
                placed = False
                step = 0.05
                
                # 扫描范围
                if current_layer == 1:
                    scan_y_min = crate_y_min + box_wid / 2
                    scan_y_max = crate_y_max
                    scan_x_min = crate_x_min + box_len / 2
                    scan_x_max = crate_x_max
                else:
                    # 上层扫描范围基于下层箱子的范围
                    if len(layers_data) > 0:
                        all_x_min = min(b['x_min'] for b in layers_data)
                        all_x_max = max(b['x_max'] for b in layers_data)
                        all_y_min = min(b['y_min'] for b in layers_data)
                        all_y_max = max(b['y_max'] for b in layers_data)
                        scan_x_min = all_x_min + box_len / 2
                        scan_x_max = all_x_max - box_len / 2
                        scan_y_min = all_y_min + box_wid / 2
                        scan_y_max = all_y_max - box_wid / 2
                    else:
                        break
                
                y = scan_y_min
                while y + box_wid / 2 <= scan_y_max and not placed:
                    x = scan_x_min
                    while x + box_len / 2 <= scan_x_max and not placed:
                        if is_position_free(x, y, box_len, box_wid, current_occupied):
                            # 计算z高度
                            if current_layer == 1:
                                # 第一层：放在托盘顶面
                                box_z = crate_top_z
                            else:
                                # 放在下层箱子的最高点
                                max_height = max(b['height'] for b in layers_data if 
                                               b['x_min'] < x + box_len/2 and b['x_max'] > x - box_len/2 and
                                               b['y_min'] < y + box_wid/2 and b['y_max'] > y - box_wid/2)
                                box_z = crate_top_z + max_height
                            
                            box_name = f"Box_{crate_name}_L{current_layer}_{box_spec['name']}_{idx+1}"
                            box_x = crate_pos['x'] + x
                            box_y = crate_pos['y'] + y
                            
                            box = {
                                "type": "box",
                                "name": box_name,
                                "dimensions": {"length": box_len, "width": box_wid, "height": box_hgt},
                                "position": {"x": box_x, "y": box_y, "z": box_z},
                                "usd_file": "Box.usd",
                                "on_crate": True,
                                "crate_name": crate_name,
                                "layer": current_layer,
                                "box_type": box_type
                            }
                            self.boxes.append(box)
                            layer_boxes.append((box_name, box_type))
                            
                            # 记录占用区域和高度
                            box_data = {
                                'x': x, 'y': y,
                                'x_min': x - box_len/2,
                                'x_max': x + box_len/2,
                                'y_min': y - box_wid/2,
                                'y_max': y + box_wid/2,
                                # height 是相对于“托盘顶面”的累计高度
                                'height': (box_z - crate_top_z) + box_hgt,
                                'box_type': box_type
                            }
                            layers_data.append(box_data)
                            current_occupied.append((box_data['x_min'], box_data['x_max'], 
                                                   box_data['y_min'], box_data['y_max']))
                            
                            placed = True
                        
                        x += step
                    y += step
            
            # 统计当前层
            if layer_boxes:
                all_placed_boxes.extend(layer_boxes)
                total_boxes_placed += len(layer_boxes)
                
                box_summary = {}
                for _, box_type in layer_boxes:
                    box_summary[box_type] = box_summary.get(box_type, 0) + 1
                summary_str = ", ".join([f"{BOX_TYPES[bt]['name']}×{cnt}" for bt, cnt in box_summary.items()])
                print(f"  第{current_layer}层: {len(layer_boxes)}个箱子 ({summary_str})")
            else:
                # 当前层无法放置箱子，停止堆叠
                break
        
        # 打印总结
        if all_placed_boxes:
            total_summary = {}
            for _, box_type in all_placed_boxes:
                total_summary[box_type] = total_summary.get(box_type, 0) + 1
            summary_str = ", ".join([f"{BOX_TYPES[bt]['name']}×{cnt}" for bt, cnt in total_summary.items()])
            print(f"✓ 在 {crate_name} 上放置了 {total_boxes_placed} 个混合箱子（{len(set(b[1] for b in all_placed_boxes))}种型号）")
            print(f"  总计: {summary_str}")
        
        return total_boxes_placed
    
    def place_boxes_grid_on_crate(self, crate_name, rows=2, cols=2, layer=1, 
                                  box_length=0.4, box_width=0.4, box_height=0.6, 
                                  spacing=0.15, auto_fit=False):
        """在托盘上网格化放置多个箱子
        
        Args:
            crate_name: 托盘名称
            rows: 行数（Y方向）
            cols: 列数（X方向）
            layer: 层数
            box_length: 箱子长度（X方向，米）
            box_width: 箱子宽度（Y方向，米）
            box_height: 箱子高度（Z方向，米）
            spacing: 箱子间距（米）
            auto_fit: 自动调整箱子大小以适应托盘（保持间距）
        
        Returns:
            int: 成功放置的箱子数量
        """
        # 查找托盘
        crate = None
        for c in self.crates:
            if c['name'] == crate_name:
                crate = c
                break
        
        if not crate:
            print(f"错误：找不到托盘 {crate_name}")
            return 0
        
        crate_dims = crate['dimensions']
        crate_length = crate_dims['length']  # X方向
        crate_width = crate_dims['width']    # Y方向
        
        # 自动适应模式：计算最大箱子尺寸
        if auto_fit:
            # 可用空间 = 托盘尺寸 - 总间距
            available_length = crate_length - (cols - 1) * spacing - 2 * spacing  # 留出边缘空间
            available_width = crate_width - (rows - 1) * spacing - 2 * spacing
            
            # 每个箱子的最大尺寸
            max_box_length = available_length / cols
            max_box_width = available_width / rows
            
            # 使用较小的尺寸确保不超出
            box_length = min(box_length, max_box_length)
            box_width = min(box_width, max_box_width)
            
            print(f"自动适应: 箱子尺寸调整为 {box_length:.3f}m × {box_width:.3f}m")
        
        # 计算网格总尺寸
        total_length = cols * box_length + (cols - 1) * spacing
        total_width = rows * box_width + (rows - 1) * spacing
        
        # 检查是否超出托盘范围
        if total_length > crate_length or total_width > crate_width:
            print(f"⚠️  警告：箱子网格 ({rows}x{cols}) 超出托盘范围")
            print(f"   网格尺寸: {total_length:.3f}m × {total_width:.3f}m")
            print(f"   托盘尺寸: {crate_length:.3f}m × {crate_width:.3f}m")
            print(f"   建议：减小箱子尺寸或增加间距")
        
        # 起始位置（相对于托盘中心）- 网格左下角
        start_x = -(total_length - box_length) / 2
        start_y = -(total_width - box_width) / 2
        
        placed_count = 0
        for row in range(rows):
            for col in range(cols):
                # 计算每个箱子的中心位置偏移
                offset_x = start_x + col * (box_length + spacing)
                offset_y = start_y + row * (box_width + spacing)
                box_name = f"Box_{crate_name}_L{layer}_{row*cols + col + 1}"
                
                if self.place_box_on_crate(crate_name, box_name, offset_x, offset_y, layer,
                                          box_length, box_width, box_height):
                    placed_count += 1
        
        print(f"✓ 在 {crate_name} 上放置了 {placed_count}/{rows*cols} 个箱子")
        print(f"  箱子尺寸: {box_length:.3f}m × {box_width:.3f}m × {box_height:.3f}m")
        print(f"  间距: {spacing:.3f}m")
        
        return placed_count
    
    def place_agent(self, agent_type, name, center_x, center_y, diameter=0.8, model="create3", rotation=0):
        """放置搬运车（最后放置，检查占用）"""
        # 检查中心点及周围是否可用（搬运车占用约1个网格）
        x = int(center_x)
        y = int(center_y)
        
        if not self.is_valid_position(x, y) or self.grid[y][x] != EMPTY:
            return False
        
        # 标记占用（但不阻止其他物体，因为搬运车可以移动）
        # 这里只标记，不阻止放置
        
        agent = {
            "type": agent_type,
            "name": name,
            "shape": "cylinder",
            "dimensions": {"diameter": diameter, "height": 1},
            "position": {"x": center_x, "y": center_y, "z": 0},
            "model": model,
            "rotation": rotation,
            "color": "blue" if agent_type == "agv" else "green"
        }
        self.agents.append(agent)
        return True
    
    def create_shelf_group(self, rows, cols, spacing_x=5, spacing_y=2.5):
        """创建整齐的货架组（返回货架组的位置列表）"""
        shelf_group = []
        for row in range(rows):
            for col in range(cols):
                # 计算相对位置（以组为单位）
                x = col * spacing_x
                y = row * spacing_y
                shelf_group.append((x, y))
        return shelf_group
    
    def place_shelf_group(self, group_name, base_x, base_y, rows, cols, 
                         spacing_x=5, spacing_y=2.5, rotation=0):
        """将货架组整体放置到指定位置"""
        shelf_group = self.create_shelf_group(rows, cols, spacing_x, spacing_y)
        placed_count = 0
        
        for idx, (rel_x, rel_y) in enumerate(shelf_group):
            center_x = base_x + rel_x
            center_y = base_y + rel_y
            name = f"{group_name}_{idx+1}"
            if self.place_shelf(name, center_x, center_y, rotation):
                placed_count += 1
        
        return placed_count
    
    def print_grid(self):
        """打印网格占用情况（调试用）"""
        # 图例
        symbols = {
            EMPTY: '·',
            SHELF: 'S',
            CONVEYOR: 'C',
            AGENT: 'A',
            ROBOT_ARM: 'R',
            PACKING_TABLE: 'T'
        }
        
        print("\n网格占用情况（15x15，每格1m）:")
        print("  ", end="")
        for x in range(self.size):
            print(f"{x:2d}", end="")
        print()
        
        for y in range(self.size - 1, -1, -1):  # 从上到下显示
            print(f"{y:2d} ", end="")
            for x in range(self.size):
                symbol = symbols.get(self.grid[y][x], '?')
                print(f" {symbol}", end="")
            print()
        
        print("\n图例：· = 空地, S = 货架, C = 传送带, T = 打包桌, A = 搬运车")
    
    def to_scene_dict(self, scene_name):
        """转换为场景字典，箱子作为父对象的children"""
        import copy
        
        # 深拷贝避免修改原始数据
        shelves = [copy.deepcopy(obj) for obj in self.objects if obj.get("type") == "shelf"]
        other_objects = [copy.deepcopy(obj) for obj in self.objects if obj.get("type") != "shelf"]
        crates = [copy.deepcopy(obj) for obj in self.crates]
        
        # 创建名称到对象的映射
        shelf_map = {obj["name"]: obj for obj in shelves}
        crate_map = {obj["name"]: obj for obj in crates}
        
        # 初始化所有货架和托盘的children列表
        for shelf in shelves:
            shelf["children"] = []
        for crate in crates:
            crate["children"] = []
        
        # 将箱子分配到对应的父对象
        standalone_boxes = []
        for box in self.boxes:
            box_copy = copy.deepcopy(box)
            if box.get("on_shelf") and box.get("shelf_name") in shelf_map:
                # 箱子在货架上，添加为货架的child
                shelf_map[box["shelf_name"]]["children"].append(box_copy)
            elif box.get("on_crate") and box.get("crate_name") in crate_map:
                # 箱子在托盘上，添加为托盘的child
                crate_map[box["crate_name"]]["children"].append(box_copy)
            else:
                # 独立箱子
                standalone_boxes.append(box_copy)
        
        # 合并所有对象（箱子已嵌套在父对象中）
        all_objects = shelves + other_objects + self.packing_tables + crates + standalone_boxes
        
        return {
            "scene_name": scene_name,
            "warehouse": {
                "name": "Warehouse",
                "dimensions": {"length": self.size, "width": self.size, "height": 10},
                "position": {"x": 0, "y": 0, "z": 0},
                "grid_size": self.size,
                "grid_occupancy": self.grid  # 保存占用情况
            },
            "objects": all_objects,
            "agents": self.agents
        }


def generate_original_layout():
    """生成原始草图布局（40×40米仓库，16m长货架）"""
    grid = WarehouseGrid(40)
    
    print("\n📦 原始布局 - 开始放置物体...")
    
    # 1. 先放置传送带（底部，沿底边延伸）
    grid.place_conveyor_line(1, 38, 0)
    print("✓ 传送带已放置")
    
    # 2. 在传送带旁边放置机械臂和托盘（直接在地上）
    # 传送带边缘约在 y=1，距离随机化增加多样性
    arm_x = 5
    arm_to_conveyor = random.uniform(0.08, 0.20)  # 机械臂离传送带 0.08-0.20m
    crate_to_arm = random.uniform(0.87, 1.14)     # 托盘离机械臂 0.87-1.14m
    arm_y = 1.0 + arm_to_conveyor
    crate_y = arm_y + crate_to_arm
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print(f"✓ 机械臂和托盘已放置 (机械臂距传送带:{arm_to_conveyor:.2f}m, 托盘距机械臂:{crate_to_arm:.2f}m)")
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 放置货架（16m长 x 1m宽 x 6m高）
    # 货架中心 x=12（占用 x=4~20），共2列，列间距20m
    # 每列10行，行间距3m（1m货架宽 + 2m通道）
    placed_count = 0
    for col in range(2):
        # 16m 长货架：半长=8m；考虑边界与 safety margin，右侧列不能放到 x=32（会越界导致放置失败）
        base_x = 12 if col == 0 else 28
        for row in range(10):
            shelf_y = 8 + row * 3.0
            shelf_name = f"Shelf_{col*10 + row + 1}"
            if grid.place_shelf(shelf_name, base_x, shelf_y, rotation=0):
                placed_count += 1
    print(f"✓ 货架组已放置: {placed_count}/20 个")
    
    # 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'], obj.get('rotation', 0))
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 3), 
                                        max(1, len(all_shelf_names) * 2 // 3))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 3)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level, 
                                                     rotation=rotation, max_stacks=max_stacks, 
                                                     spacing=random.uniform(0.03, 0.08))
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 4. 放置搬运车
    grid.place_agent("agv", "AGV_1", 25, 15, model="create3")
    grid.place_agent("agv", "AGV_2", 25, 25, model="ridgeback")
    grid.place_agent("agv", "AGV_3", 35, 20, model="create3")
    print("✓ 搬运车已放置")
    
    # 打印网格占用情况
    grid.print_grid()
    
    return grid.to_scene_dict("Scene_Original")


def generate_dual_channel_layout():
    """生成双通道式布局（40×40米仓库，16m长货架）"""
    grid = WarehouseGrid(40)
    
    # 传送带（y=0.5，占用 y=[0,1)，沿底部延伸）
    grid.place_conveyor_line(1, 38, 0)
    
    # 在传送带旁边放置机械臂和托盘（直接在地上）
    # 传送带边缘约在 y=1，距离随机化增加多样性
    arm_x = 5
    arm_to_conveyor = random.uniform(0.08, 0.20)
    crate_to_arm = random.uniform(0.87, 1.14)
    arm_y = 1.0 + arm_to_conveyor
    crate_y = arm_y + crate_to_arm
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # ===== 货架优化放置算法（rack blocks + 横向穿越通道）=====
    # 目标：
    # - 预留底部传送带/打包区
    # - 用“货架块”填充左右两侧，让右边不再空
    #
    # 货架尺寸（由 place_shelf 内部定义）：16m长 × 1m宽
    shelf_len = 16.0
    shelf_wid = 1.0
    margin = 2.0           # 离边界最小距离
    aisle_y = 1.5          # 行间通道宽（保持合理通行）
    step_y = shelf_wid + aisle_y  # 1 + 1.5 = 2.5m

    # 横向穿越通道：每隔 N 行货架，额外留出一条更宽的“横向通道”（整条 y 带状留空）
    cross_aisle_every_rows = 4
    cross_aisle_extra_gap = 4.0   # 额外空出来的宽度（米）

    y_start = 7.0          # 从这里开始放货架（更靠近下方，但仍避开打包区）
    y_end = grid.size - margin - shelf_wid / 2.0

    # 每块的 X 中心：尽量把两块铺满 40m
    # 计算可用范围：[margin+half_len, size-margin-half_len]
    x_min = margin + shelf_len / 2.0      # 2 + 8 = 10
    x_max = grid.size - margin - shelf_len / 2.0  # 40 - 2 - 8 = 30
    block_xs = [x_min, x_max]  # 左块=10，右块=30

    placed_shelves = 0
    shelf_index = 1
    y = y_start
    rows_since_cross = 0

    # 以“行”为单位铺货架，保证左右两块的横向穿越通道对齐
    while y <= y_end:
        # 每放置 cross_aisle_every_rows 行，就插入一条更宽的横向通道
        if rows_since_cross >= cross_aisle_every_rows:
            y += cross_aisle_extra_gap
            rows_since_cross = 0
            continue

        for x in block_xs:
            name = f"Shelf_{shelf_index}"
            if grid.place_shelf(name, x, y, rotation=0):
                placed_shelves += 1
                shelf_index += 1
        y += step_y
        rows_since_cross += 1

    print(f"✓ 货架已放置: {placed_shelves}（含横向穿越通道）")
    
    # 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'], obj.get('rotation', 0))
    
    # 随机选择部分货架放置箱子
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 4), 
                                        max(1, len(all_shelf_names) // 2))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation = shelf_positions[shelf_name]
        
        # 随机选择在哪几层放置箱子
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)  # 双通道布局货架较多，每个放1-2层
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks, 
                                                     spacing=random.uniform(0.03, 0.08))
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 搬运车（放在两块货架之间的主通道中）
    # 注意：x=30 是右侧货架中心，容易被占用导致 place_agent 失败，所以统一放到中间通道 x=20
    grid.place_agent("agv", "AGV_1", 20, 12, model="create3")
    grid.place_agent("agv", "AGV_2", 20, 28, model="ridgeback")
    
    return grid.to_scene_dict("Scene_DualChannel")


def generate_l_shape_layout():
    """生成L型布局（40×40米仓库，16m长货架）"""
    grid = WarehouseGrid(40)
    
    # 传送带
    grid.place_conveyor_line(1, 38, 0)
    
    # 机械臂和托盘（直接在地上）
    # 传送带边缘约在 y=1，距离随机化增加多样性
    arm_x = 5
    arm_to_conveyor = random.uniform(0.08, 0.20)
    crate_to_arm = random.uniform(0.87, 1.14)
    arm_y = 1.0 + arm_to_conveyor
    crate_y = arm_y + crate_to_arm
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # L型布局：横向货架（不旋转）+ 纵向货架（旋转90度）
    # 横向货架：5行，中心x=12，从y=8开始，间距3m
    for row in range(5):
        shelf_y = 8 + row * 3.0
        grid.place_shelf(f"HShelf_{row+1}", 12, shelf_y, rotation=0)
    
    # 纵向货架：旋转90度后，16m沿Y轴，1m沿X轴
    # 放在右侧，从x=28开始，y=16（货架中心）
    for col in range(3):
        shelf_x = 28 + col * 3.0  # 间距3m
        grid.place_shelf(f"VShelf_{col+1}", shelf_x, 20, rotation=90)
    
    # 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'], obj.get('rotation', 0))
    
    # 随机选择部分货架放置箱子
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 3), 
                                        max(1, len(all_shelf_names) * 2 // 3))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation = shelf_positions[shelf_name]
        
        # 随机选择在哪几层放置箱子
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 3)  # L型布局，每个货架放1-3层
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks, 
                                                     spacing=random.uniform(0.03, 0.08))
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 搬运车
    grid.place_agent("agv", "AGV_1", 25, 15, model="create3")
    grid.place_agent("agv", "AGV_2", 35, 25, model="ridgeback")
    
    return grid.to_scene_dict("Scene_LShape")


def generate_center_island_layout():
    """生成中心岛式布局（40×40米仓库，16m长货架）"""
    grid = WarehouseGrid(40)
    
    # 传送带
    grid.place_conveyor_line(1, 38, 0)
    
    # 机械臂和托盘（直接在地上）
    # 传送带边缘约在 y=1，距离随机化增加多样性
    arm_x = 5
    arm_to_conveyor = random.uniform(0.08, 0.20)
    crate_to_arm = random.uniform(0.87, 1.14)
    arm_y = 1.0 + arm_to_conveyor
    crate_y = arm_y + crate_to_arm
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 外围货架布局
    # 上部横向货架：2个，中心x=12和x=28，y=8
    grid.place_shelf("TopShelf_1", 12, 8, rotation=0)
    grid.place_shelf("TopShelf_2", 28, 8, rotation=0)
    
    # 左侧纵向货架（旋转90度，16m沿Y轴）
    grid.place_shelf("LeftShelf_1", 3, 20, rotation=90)
    
    # 右侧纵向货架（旋转90度）
    grid.place_shelf("RightShelf_1", 37, 20, rotation=90)
    
    # 中心岛货架（横向，2x3排列）
    for row in range(3):
        for col in range(2):
            shelf_x = 12 + col * 16  # x=12 和 x=28
            shelf_y = 15 + row * 6   # y=15, 21, 27
            grid.place_shelf(f"CenterShelf_{row*2+col+1}", shelf_x, shelf_y, rotation=0)
    
    # 底部横向货架
    grid.place_shelf("BottomShelf_1", 12, 35, rotation=0)
    grid.place_shelf("BottomShelf_2", 28, 35, rotation=0)
    
    # 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'], obj.get('rotation', 0))
    
    # 随机选择部分货架放置箱子（中心岛式货架较多）
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 3), 
                                        max(1, len(all_shelf_names) * 3 // 4))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation = shelf_positions[shelf_name]
        
        # 随机选择在哪几层放置箱子
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)  # 中心岛式货架多，每个放1-2层
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks, 
                                                     spacing=random.uniform(0.03, 0.08))
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 搬运车（在通道中）
    grid.place_agent("agv", "AGV_1", 20, 20, model="create3")
    grid.place_agent("agv", "AGV_2", 20, 30, model="ridgeback")
    
    return grid.to_scene_dict("Scene_CenterIsland")


def generate_symmetric_layout():
    """生成对称式紧凑布局（40×40米仓库，16m长货架）"""
    grid = WarehouseGrid(40)
    
    # 传送带
    grid.place_conveyor_line(1, 38, 0)
    
    # 机械臂和托盘（直接在地上）
    # 传送带边缘约在 y=1，距离随机化增加多样性
    arm_x = 5
    arm_to_conveyor = random.uniform(0.08, 0.20)
    crate_to_arm = random.uniform(0.87, 1.14)
    arm_y = 1.0 + arm_to_conveyor
    crate_y = arm_y + crate_to_arm
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 对称式布局：左右两区各5排货架
    # 左区：货架中心x=10，5排，间距3m
    for row in range(5):
        shelf_y = 8 + row * 3.0
        grid.place_shelf(f"LeftZone_{row+1}", 10, shelf_y, rotation=0)
    
    # 右区：货架中心x=30，5排，间距3m（与左区对称）
    for row in range(5):
        shelf_y = 8 + row * 3.0
        grid.place_shelf(f"RightZone_{row+1}", 30, shelf_y, rotation=0)
    
    # 中间通道两侧的纵向货架（旋转90度）
    grid.place_shelf("CenterL_1", 18, 15, rotation=90)
    grid.place_shelf("CenterR_1", 22, 15, rotation=90)
    
    # 底部横向货架
    grid.place_shelf("BottomShelf_1", 10, 32, rotation=0)
    grid.place_shelf("BottomShelf_2", 30, 32, rotation=0)
    
    # 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'], obj.get('rotation', 0))
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 2), len(all_shelf_names))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 3)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks, 
                                                     spacing=random.uniform(0.03, 0.08))
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 搬运车（在中间通道）
    grid.place_agent("agv", "AGV_1", 20, 15, model="create3")
    grid.place_agent("agv", "AGV_2", 20, 25, model="ridgeback")
    
    return grid.to_scene_dict("Scene_Symmetric")


def save_scene_to_json(scene, filename, grid=None):
    """保存场景到JSON文件
    
    Args:
        scene: 场景字典
        filename: 输出文件名
        grid: WarehouseGrid对象（可选），用于打印网格占用情况
    """
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(scene, f, ensure_ascii=False, indent=2)
    print(f"✓ 已生成: {filename}")
    
    # 打印网格占用情况
    if grid:
        grid.print_grid()


def main():
    """主函数：生成所有场景"""
    # 创建输出目录
    output_dir = "warehouse_scenes"
    os.makedirs(output_dir, exist_ok=True)
    
    print("="*60)
    print("仓库场景生成器 - 优化版（避免重叠）")
    print("="*60)
    
    # 生成各种布局（返回 grid 和 scene）
    layouts = [
        ("original_layout.json", generate_original_layout),
        ("dual_channel_layout.json", generate_dual_channel_layout),
        ("l_shape_layout.json", generate_l_shape_layout),
        ("center_island_layout.json", generate_center_island_layout),
        ("symmetric_layout.json", generate_symmetric_layout)
    ]
    
    for filename, layout_func in layouts:
        print(f"\n{'='*60}")
        print(f"生成: {filename}")
        print('='*60)
        
        # 创建网格
        grid = WarehouseGrid(15)
        
        # 生成布局并获取场景
        # 需要修改布局函数返回 (grid, scene) 而不是只返回 scene
        # 为了简化，我们直接调用函数
        scene = layout_func()
        
        # 保存场景
        filepath = os.path.join(output_dir, filename)
        # 注意：这里无法传递 grid，因为布局函数只返回 scene
        save_scene_to_json(scene, filepath)
    
    print(f"\n{'='*60}")
    print(f"所有场景已生成到 {output_dir} 目录")
    print('='*60)


if __name__ == "__main__":
    main()
