"""
支持多格数货架的仓库场景生成器

特点：
- 支持1格、2格、3格、4格货架
- 每格有效区域4m，左右边界各1m
- 货架总长度 = 左边界(1m) + 格数×4m + 右边界(1m)
  - 1格：6m (1+4+1)
  - 2格：10m (1+8+1)
  - 3格：14m (1+12+1)
  - 4格：18m (1+16+1)
- 智能货架摆放算法，根据可用空间自动选择合适格数的货架
- 多种布局策略：网格布局、块状布局、混合布局等
"""

import json
import os
import math
import random
from typing import List, Tuple, Optional, Dict
from enum import Enum

# 占用类型定义
EMPTY = 0
SHELF = 1
CONVEYOR = 2
AGENT = 3
ROBOT_ARM = 4
PACKING_TABLE = 5

# 安全间距（米）
SAFETY_MARGIN = 0.2

# 货架配置
SHELF_SEGMENT_LENGTH = 4.0  # 每格的有效区域长度（米）
SHELF_BOUNDARY_LEFT = 1.0   # 左边界长度（米）
SHELF_BOUNDARY_RIGHT = 1.0   # 右边界长度（米）
SHELF_WIDTH = 1.0
SHELF_HEIGHT = 6.0
AVAILABLE_SEGMENTS = [1, 2, 3, 4]  # 可用的格数

# 货架总长度计算说明：
# 货架总长度 = 左边界 + 格数×每格长度 + 右边界
# - 1格：1 + 4 + 1 = 6m
# - 2格：1 + 8 + 1 = 10m
# - 3格：1 + 12 + 1 = 14m
# - 4格：1 + 16 + 1 = 18m
# 注意：边界只在货架的两端，不是每格都有边界

# 箱子型号定义
BOX_TYPES = {
    'large': {'length': 0.4, 'width': 0.4, 'height': 0.6, 'name': 'L'},
    'medium': {'length': 0.3, 'width': 0.3, 'height': 0.5, 'name': 'M'},
    'small': {'length': 0.25, 'width': 0.25, 'height': 0.4, 'name': 'S'},
}


class PlacementStrategy(Enum):
    """货架摆放策略"""
    GRID = "grid"              # 网格布局：统一格数
    ADAPTIVE = "adaptive"       # 自适应：根据空间选择合适格数
    MIXED = "mixed"            # 混合：随机使用不同格数
    BLOCK = "block"            # 块状布局：大货架优先
    COMPACT = "compact"        # 紧凑布局：小货架优先
    GREEDY_COLUMN = "greedy_column"  # 贪心列布局：原始朝向，一列一列放
    GREEDY_ROW = "greedy_row"        # 贪心行布局：旋转90度，一行一行放


class ShelfConfig:
    """货架配置"""
    def __init__(self, segments: int):
        """
        Args:
            segments: 格数（1-4）
        """
        if segments not in AVAILABLE_SEGMENTS:
            raise ValueError(f"segments must be in {AVAILABLE_SEGMENTS}, got {segments}")
        self.segments = segments
        # 货架总长度 = 左边界 + 格数×每格长度 + 右边界
        self.length = SHELF_BOUNDARY_LEFT + segments * SHELF_SEGMENT_LENGTH + SHELF_BOUNDARY_RIGHT
        # 有效区域长度（不包括边界）
        self.effective_length = segments * SHELF_SEGMENT_LENGTH
        self.width = SHELF_WIDTH
        self.height = SHELF_HEIGHT
    
    def __repr__(self):
        return f"ShelfConfig({self.segments}格, 总长={self.length}m, 有效={self.effective_length}m)"


class WarehouseGrid:
    """支持多格数货架的仓库网格管理系统"""
    
    def __init__(self, size=40):
        """初始化网格，每个网格1m x 1m"""
        self.size = size
        self.grid = [[EMPTY for _ in range(size)] for _ in range(size)]
        self.objects = []
        self.agents = []
        self.packing_tables = []
        self.crates = []
        self.boxes = []
    
    def is_valid_position(self, x, y):
        """检查坐标是否在有效范围内"""
        return 0 <= x < self.size and 0 <= y < self.size
    
    def is_area_free(self, center_x, center_y, length, width, rotation=0, margin=SAFETY_MARGIN):
        """检查指定区域是否空闲"""
        if rotation == 90:
            length, width = width, length
        
        half_length = (length + margin) / 2
        half_width = (width + margin) / 2
        
        start_x = math.floor(center_x - half_length)
        end_x = math.ceil(center_x + half_length)
        start_y = math.floor(center_y - half_width)
        end_y = math.ceil(center_y + half_width)
        
        if start_x < 0 or end_x > self.size or start_y < 0 or end_y > self.size:
            return False
        
        for x in range(start_x, end_x):
            for y in range(start_y, end_y):
                if self.is_valid_position(x, y) and self.grid[y][x] != EMPTY:
                    return False
        return True
    
    def mark_area(self, center_x, center_y, length, width, obj_type, rotation=0):
        """标记指定区域为占用"""
        if rotation == 90:
            length, width = width, length
        
        half_length = length / 2
        half_width = width / 2
        
        start_x = math.floor(center_x - half_length)
        end_x = math.ceil(center_x + half_length)
        start_y = math.floor(center_y - half_width)
        end_y = math.ceil(center_y + half_width)
        
        for x in range(start_x, end_x):
            for y in range(start_y, end_y):
                if self.is_valid_position(x, y):
                    self.grid[y][x] = obj_type
    
    def place_shelf(self, name: str, center_x: float, center_y: float, 
                   segments: int = 4, rotation: int = 0, height: float = SHELF_HEIGHT) -> bool:
        """
        放置指定格数的货架
        
        Args:
            name: 货架名称
            center_x, center_y: 货架中心位置
            segments: 格数（1-4），每格4m
            rotation: 旋转角度（0或90度）
            height: 货架高度
        
        Returns:
            bool: 是否成功放置
        """
        config = ShelfConfig(segments)
        length, width = config.length, config.width
        
        if not self.is_area_free(center_x, center_y, length, width, rotation):
            return False
        
        self.mark_area(center_x, center_y, length, width, SHELF, rotation)
        
        shelf = {
            "type": "shelf",
            "name": name,
            "dimensions": {"length": length, "width": width, "height": height},
            "position": {"x": center_x, "y": center_y, "z": 0},
            "segments": segments,  # 添加segments字段
            "color": "red"
        }
        if rotation != 0:
            shelf["rotation"] = rotation
        
        self.objects.append(shelf)
        return True
    
    def find_best_shelf_segments(self, center_x: float, center_y: float, 
                                 available_length: float, rotation: int = 0) -> Optional[int]:
        """
        根据可用空间找到最合适的货架格数
        
        Args:
            center_x, center_y: 货架中心位置
            available_length: 可用长度（米），需要考虑边界
            rotation: 旋转角度
        
        Returns:
            Optional[int]: 最合适的格数，如果无法放置则返回None
        """
        # 计算可以放置的最大格数
        # 可用长度需要减去左右边界
        effective_available = available_length - SHELF_BOUNDARY_LEFT - SHELF_BOUNDARY_RIGHT
        if effective_available <= 0:
            return None
        
        # 计算可以放置的最大格数（基于有效区域）
        max_segments = int(effective_available // SHELF_SEGMENT_LENGTH)
        max_segments = min(max_segments, max(AVAILABLE_SEGMENTS))
        
        # 从大到小尝试，优先使用大货架
        for segments in range(max_segments, 0, -1):
            config = ShelfConfig(segments)
            # 检查总长度（包括边界）是否适合
            if config.length <= available_length:
                if self.is_area_free(center_x, center_y, config.length, config.width, rotation):
                    return segments
        
        return None
    
    def place_shelf_adaptive(self, name: str, center_x: float, center_y: float,
                            max_length: float, rotation: int = 0) -> Optional[ShelfConfig]:
        """
        自适应放置货架：根据可用空间自动选择合适格数
        
        Args:
            name: 货架名称
            center_x, center_y: 货架中心位置
            max_length: 最大可用长度
            rotation: 旋转角度
        
        Returns:
            Optional[ShelfConfig]: 成功放置的货架配置，失败返回None
        """
        segments = self.find_best_shelf_segments(center_x, center_y, max_length, rotation)
        if segments is None:
            return None
        
        if self.place_shelf(name, center_x, center_y, segments, rotation):
            return ShelfConfig(segments)
        return None
    
    def place_shelf_grid(self, base_x: float, base_y: float, rows: int, cols: int,
                        segments: int = 4, spacing_x: float = 5.0, spacing_y: float = 3.0,
                        rotation: int = 0) -> int:
        """
        网格布局：放置统一格数的货架网格
        
        Args:
            base_x, base_y: 起始位置
            rows: 行数
            cols: 列数
            segments: 货架格数
            spacing_x: X方向间距
            spacing_y: Y方向间距
            rotation: 旋转角度
        
        Returns:
            int: 成功放置的货架数量
        """
        placed_count = 0
        for row in range(rows):
            for col in range(cols):
                x = base_x + col * spacing_x
                y = base_y + row * spacing_y
                name = f"Shelf_{row*cols + col + 1}"
                if self.place_shelf(name, x, y, segments, rotation):
                    placed_count += 1
        return placed_count
    
    def place_shelf_block(self, region_x: float, region_y: float, 
                         region_width: float, region_height: float,
                         strategy: PlacementStrategy = PlacementStrategy.ADAPTIVE,
                         min_spacing: float = 2.5) -> int:
        """
        块状布局：在指定区域内智能放置货架
        
        Args:
            region_x, region_y: 区域起始位置（左下角）
            region_width, region_height: 区域尺寸
            strategy: 摆放策略
            min_spacing: 最小间距（货架宽度 + 通道宽度）
        
        Returns:
            int: 成功放置的货架数量
        """
        placed_count = 0
        shelf_index = 1
        
        # 根据策略选择格数分布
        if strategy == PlacementStrategy.GRID:
            # 统一使用4格
            segment_choices = [4]
        elif strategy == PlacementStrategy.MIXED:
            # 随机选择格数
            segment_choices = AVAILABLE_SEGMENTS
        elif strategy == PlacementStrategy.BLOCK:
            # 大货架优先
            segment_choices = [4, 3, 2, 1]
        elif strategy == PlacementStrategy.COMPACT:
            # 小货架优先
            segment_choices = [1, 2, 3, 4]
        else:  # ADAPTIVE
            segment_choices = AVAILABLE_SEGMENTS
        
        # 优化货架行放置
        y = region_y + SHELF_WIDTH / 2
        row_num = 0
        
        while y + SHELF_WIDTH / 2 <= region_y + region_height:
            row_num += 1
            x = region_x
            row_shelves = 0
            
            # 计算最小货架长度（1格+边界）
            min_shelf_length = ShelfConfig(1).length
            
            # 在当前行中尽可能多地放置货架（混合使用不同格数）
            while x + min_shelf_length <= region_x + region_width:
                # 计算剩余空间
                remaining_length = (region_x + region_width) - x
                
                # 智能选择最优格数：根据剩余空间灵活选择
                segments = None
                
                if strategy == PlacementStrategy.ADAPTIVE:
                    # 策略：
                    # 1. 如果行首（x接近region_x），尽量用大货架
                    # 2. 如果剩余空间足够大，继续用大货架
                    # 3. 剩余空间较小时，用小货架填补
                    
                    is_row_start = (x - region_x) < 1.0  # 行首1m内
                    
                    if is_row_start:
                        # 行首：尝试找到能最大化利用整行空间的组合
                        # 例如：列宽16.5m，优先选择 2格(10m)+1格(6m) = 16m
                        row_width = region_width
                        
                        if row_width >= 16 and row_width < 18:
                            # 16-18m：2格+1格 或 3格单独
                            search_order = [2, 3, 1, 4]
                        elif row_width >= 18 and row_width < 22:
                            # 18-22m：4格单独 或 3格+1格
                            search_order = [4, 3, 2, 1]
                        elif row_width >= 22 and row_width < 28:
                            # 22-28m：4格+2格 或其他组合
                            search_order = [4, 3, 2, 1]
                        elif row_width > 28:
                            # >28m：大货架优先
                            search_order = [4, 3, 2, 1]
                        else:
                            # <16m：中小货架
                            search_order = [2, 1, 3, 4]
                    elif remaining_length > 12:
                        # 行中：根据剩余空间选择
                        search_order = [2, 1, 3, 4]
                    elif remaining_length > 8:
                        # 较小空间：优先小货架
                        search_order = [2, 1]
                    else:
                        # 很小空间：只用最小货架
                        search_order = [1]
                    
                    for seg in search_order:
                        config = ShelfConfig(seg)
                        # 检查是否能放下，并且不会浪费太多空间
                        if config.length <= remaining_length + 0.5:
                            if self.is_area_free(x + config.length/2, y, config.length, SHELF_WIDTH, rotation=0):
                                segments = seg
                                break
                    
                    # 最后尝试：如果还是找不到，用最小的
                    if segments is None and remaining_length >= ShelfConfig(1).length:
                        segments = 1
                else:
                    # 其他策略：从候选格数中选择
                    for seg in segment_choices:
                        config = ShelfConfig(seg)
                        if (config.length <= remaining_length and 
                            self.is_area_free(x + config.length/2, y, config.length, SHELF_WIDTH, rotation=0)):
                            segments = seg
                            break
                
                if segments is None:
                    break
                
                # 放置货架
                config = ShelfConfig(segments)
                center_x = x + config.length / 2
                name = f"Shelf_{shelf_index}"
                
                # 检查是否超出边界
                if x + config.length > region_x + region_width + 0.1:
                    break
                
                if self.place_shelf(name, center_x, y, segments, rotation=0):
                    placed_count += 1
                    shelf_index += 1
                    row_shelves += 1
                    x += config.length + 0.3  # 货架之间小间隙
                else:
                    # 如果放置失败，尝试下一个位置
                    x += 1.0
                    if x + min_shelf_length > region_x + region_width:
                        break
            
            # 移动到下一行
            y += min_spacing
        
        return placed_count
    
    def place_shelves_optimized(self, start_x: float, end_x: float, 
                                start_y: float, end_y: float,
                                aisle_width: float = 2.0,
                                strategy: PlacementStrategy = PlacementStrategy.ADAPTIVE) -> dict:
        """
        智能优化货架摆放：自动计算最优列数和布局，最大化空间利用率
        
        Args:
            start_x, end_x: X方向可用范围
            start_y, end_y: Y方向可用范围
            aisle_width: 通道宽度（供AGV通行）
            strategy: 摆放策略
        
        Returns:
            int: 成功放置的货架数量
        """
        total_width = end_x - start_x
        total_height = end_y - start_y
        
        print(f"\n🧠 智能优化布局：可用空间 {total_width:.1f}m × {total_height:.1f}m")
        
        # 计算最优列数
        # 假设每列货架平均长度约10m（2格货架），宽度1m
        avg_shelf_length = 10.0
        
        # 尝试不同的列数，找到最优方案
        # 注意：至少需要2列才能有通道给AGV使用
        best_layout = None
        max_shelves = 0
        
        for num_columns in range(2, 7):  # 尝试2-6列（至少2列）
            # 计算通道数量和可用宽度
            aisle_count = num_columns - 1
            total_aisle_width = aisle_count * aisle_width
            available_width = total_width - total_aisle_width
            
            # 每列分配的宽度
            column_width = available_width / num_columns
            
            # 检查是否可行
            # 理想情况：每列能放下至少2格货架（10m）
            # 最低要求：每列能放下1格货架（6m）
            min_shelf_length = ShelfConfig(1).length
            ideal_shelf_length = ShelfConfig(2).length  # 10m
            
            if column_width < min_shelf_length + 1.0:  # 最低要求
                continue
            
            # 估算这个布局能放多少货架
            # 每行需要 SHELF_WIDTH + 通道宽度
            row_spacing = SHELF_WIDTH + 1.5  # 货架之间的通道（1.5m足够AGV横向通过）
            rows_per_column = int(total_height / row_spacing)
            
            # 每列每行平均能放的货架数（基于可用长度）
            # 考虑货架之间的小间隙（0.5m）
            shelves_per_row = max(1, int(column_width / (avg_shelf_length * 0.8 + 0.5)))
            
            estimated_shelves = num_columns * rows_per_column * shelves_per_row
            
            # 计算空间利用率（货架占地面积 / 总可用面积）
            shelf_area = estimated_shelves * avg_shelf_length * SHELF_WIDTH
            total_area = total_width * total_height
            utilization = (shelf_area / total_area) * 100
            
            # 计算货架多样性得分（能放多大的货架）
            diversity_score = 0
            if column_width >= ShelfConfig(4).length:
                diversity_score = 4
            elif column_width >= ShelfConfig(3).length:
                diversity_score = 3
            elif column_width >= ShelfConfig(2).length:
                diversity_score = 2
            else:
                diversity_score = 1
            
            print(f"  方案 {num_columns}列: 列宽={column_width:.1f}m, 行数≈{rows_per_column}, 预估货架≈{estimated_shelves}, 利用率≈{utilization:.1f}%, 货架规格≤{diversity_score}格")
            
            # 综合评分：优先考虑货架多样性，然后才是数量
            # 策略：宁愿少放几个货架，也要能使用多种规格
            diversity_bonus = diversity_score * 15  # 每个等级15分奖励
            score = estimated_shelves + diversity_bonus
            
            print(f"       综合得分: {score:.1f} (货架{estimated_shelves} + 多样性奖励{diversity_bonus})")
            
            # 选择得分最高的方案
            if score > max_shelves:
                max_shelves = score
                best_layout = {
                    'num_columns': num_columns,
                    'column_width': column_width,
                    'aisle_count': aisle_count,
                    'utilization': utilization,
                    'diversity_score': diversity_score,
                    'estimated_shelves': estimated_shelves
                }
        
        if best_layout is None:
            print("  ❌ 无法找到可行的布局方案")
            return 0
        
        print(f"\n✨ 最优方案：{best_layout['num_columns']}列布局")
        print(f"   ├─ 每列宽度：{best_layout['column_width']:.1f}m")
        print(f"   ├─ 通道数量：{best_layout['aisle_count']} (每条{aisle_width:.1f}m宽)")
        print(f"   ├─ 预估货架：{best_layout['estimated_shelves']} 个")
        print(f"   ├─ 货架规格：最大可放{best_layout['diversity_score']}格货架")
        print(f"   └─ 空间利用率：{best_layout['utilization']:.1f}%\n")
        
        # 开始放置货架
        placed_count = 0
        num_columns = best_layout['num_columns']
        column_width = best_layout['column_width']
        
        # 记录放置前的货架数量，用于统计每列的贡献
        before_count = len([o for o in self.objects if o.get("type") == "shelf"])
        
        for col in range(num_columns):
            # 计算当前列的X范围
            col_start_x = start_x + col * (column_width + aisle_width)
            col_end_x = col_start_x + column_width
            
            print(f"📦 放置第 {col+1}/{num_columns} 列货架 (X: {col_start_x:.1f}-{col_end_x:.1f}m)...")
            
            col_before = len([o for o in self.objects if o.get("type") == "shelf"])
            
            # 在这一列中放置货架
            count = self.place_shelf_block(
                region_x=col_start_x,
                region_y=start_y,
                region_width=column_width,
                region_height=total_height,
                strategy=strategy,
                min_spacing=1.8  # 货架行间距
            )
            placed_count += count
            print(f"   ✓ 第{col+1}列放置了 {count} 个货架")
        
        # 统计各种格数的货架数量
        after_count = len([o for o in self.objects if o.get("type") == "shelf"])
        new_shelves = [o for o in self.objects if o.get("type") == "shelf"][before_count:]
        
        segment_stats = {1: 0, 2: 0, 3: 0, 4: 0}
        for shelf in new_shelves:
            seg = shelf.get("segments", 4)
            segment_stats[seg] = segment_stats.get(seg, 0) + 1
        
        return {
            'total': placed_count,
            'by_segments': segment_stats
        }
    
    def place_shelf_fixed_segments(self, region_x: float, region_y: float,
                                   region_width: float, region_height: float,
                                   segments: int, rotation: int = 0, gap: float = 0.3) -> int:
        """
        固定格数布局：在指定区域内，只放置指定格数的货架
        - 与贪心算法不同，这个方法只放置一种格数的货架
        - 适用于分区管理，每个区域只存放特定规格的货物
        
        Args:
            region_x, region_y: 区域起始位置（左下角）
            region_width, region_height: 区域尺寸
            segments: 指定的货架格数（1-4）
            rotation: 0=原始朝向（长边沿X），90=旋转90度（长边沿Y）
            gap: 货架之间的间隙
        
        Returns:
            int: 成功放置的货架数量
        """
        placed_count = 0
        shelf_index = len([o for o in self.objects if o.get("type") == "shelf"]) + 1
        
        config = ShelfConfig(segments)
        
        if rotation == 0:
            # 原始朝向：货架长边沿X轴，一列一列放（沿Y方向排列）
            y = region_y
            col_num = 0
            
            while y + SHELF_WIDTH <= region_y + region_height:
                col_num += 1
                x = region_x
                col_placed = 0
                
                # 在当前列中，从左到右放置相同格数的货架
                while x + config.length <= region_x + region_width:
                    center_x = x + config.length / 2
                    center_y = y + SHELF_WIDTH / 2
                    
                    # 检查是否能放置
                    if self.is_area_free(center_x, center_y, config.length, SHELF_WIDTH, rotation=0):
                        name = f"Shelf_{shelf_index}"
                        if self.place_shelf(name, center_x, center_y, segments, rotation=0):
                            placed_count += 1
                            shelf_index += 1
                            col_placed += 1
                            x += config.length + gap
                        else:
                            x += 1.0
                    else:
                        x += 1.0
                
                # 移动到下一列
                y += SHELF_WIDTH + 1.5  # 货架宽度 + 通道
        
        else:  # rotation == 90
            # 旋转90度：货架长边沿Y轴，一行一行放（沿X方向排列）
            x = region_x
            row_num = 0
            
            while x + SHELF_WIDTH <= region_x + region_width:
                row_num += 1
                y = region_y
                row_placed = 0
                
                # 在当前行中，从下到上放置相同格数的货架
                while y + config.length <= region_y + region_height:
                    center_x = x + SHELF_WIDTH / 2
                    center_y = y + config.length / 2
                    
                    # 检查是否能放置（rotation=90）
                    if self.is_area_free(center_x, center_y, config.length, SHELF_WIDTH, rotation=90):
                        name = f"Shelf_{shelf_index}"
                        if self.place_shelf(name, center_x, center_y, segments, rotation=90):
                            placed_count += 1
                            shelf_index += 1
                            row_placed += 1
                            y += config.length + gap
                        else:
                            y += 1.0
                    else:
                        y += 1.0
                
                # 移动到下一行
                x += SHELF_WIDTH + 1.5  # 货架宽度 + 通道
        
        return placed_count
    
    def place_shelf_column_greedy(self, region_x: float, region_y: float,
                                  region_width: float, region_height: float,
                                  rotation: int = 0, gap: float = 0.3) -> int:
        """
        贪心列布局：在指定区域内，一列一列地放置货架
        - 货架原始朝向（rotation=0）或旋转90度（rotation=90）
        - 每列优先放最长格的货架（4格→3格→2格→1格）
        - 放置后剔除已占用空间，继续在剩余空间中放置
        
        Args:
            region_x, region_y: 区域起始位置（左下角）
            region_width, region_height: 区域尺寸
            rotation: 0=原始朝向（长边沿X），90=旋转90度（长边沿Y）
            gap: 货架之间的间隙
        
        Returns:
            int: 成功放置的货架数量
        """
        placed_count = 0
        shelf_index = 1
        
        if rotation == 0:
            # 原始朝向：货架长边沿X轴，一列一列放（沿Y方向排列）
            print(f"  📐 贪心列布局（原始朝向）：货架长边沿X轴")
            
            y = region_y
            col_num = 0
            
            while y < region_y + region_height:
                col_num += 1
                print(f"  📦 第 {col_num} 列 (Y={y:.1f}m)")
                
                x = region_x
                col_placed = 0
                
                # 在当前列中，从左到右贪心放置货架
                while x < region_x + region_width:
                    remaining_length = (region_x + region_width) - x
                    
                    # 从最长格数开始尝试（4→3→2→1）
                    placed = False
                    for segments in [4, 3, 2, 1]:
                        config = ShelfConfig(segments)
                        
                        if config.length > remaining_length + 0.1:
                            continue  # 太长，尝试下一个
                        
                        # 计算货架中心位置
                        center_x = x + config.length / 2
                        center_y = y + SHELF_WIDTH / 2
                        
                        # 检查是否能放置
                        if self.is_area_free(center_x, center_y, config.length, SHELF_WIDTH, rotation=0):
                            name = f"Shelf_{shelf_index}"
                            if self.place_shelf(name, center_x, center_y, segments, rotation=0):
                                print(f"    ✓ 放置 {segments}格货架 {name} at X={center_x:.1f}, Y={center_y:.1f}")
                                placed_count += 1
                                shelf_index += 1
                                col_placed += 1
                                x += config.length + gap
                                placed = True
                                break
                    
                    if not placed:
                        # 无法放置任何货架，跳到下一个位置
                        x += 1.0
                        if x >= region_x + region_width:
                            break
                
                print(f"    → 第 {col_num} 列放置了 {col_placed} 个货架")
                
                # 移动到下一列
                y += SHELF_WIDTH + 1.5  # 货架宽度 + 通道
        
        else:  # rotation == 90
            # 旋转90度：货架长边沿Y轴，一行一行放（沿X方向排列）
            print(f"  📐 贪心行布局（旋转90度）：货架长边沿Y轴")
            
            x = region_x
            row_num = 0
            
            while x < region_x + region_width:
                row_num += 1
                print(f"  📦 第 {row_num} 行 (X={x:.1f}m)")
                
                y = region_y
                row_placed = 0
                
                # 在当前行中，从下到上贪心放置货架
                while y < region_y + region_height:
                    remaining_length = (region_y + region_height) - y
                    
                    # 从最长格数开始尝试（4→3→2→1）
                    placed = False
                    for segments in [4, 3, 2, 1]:
                        config = ShelfConfig(segments)
                        
                        if config.length > remaining_length + 0.1:
                            continue  # 太长，尝试下一个
                        
                        # 计算货架中心位置（注意旋转90度后，长度方向变成Y轴）
                        center_x = x + SHELF_WIDTH / 2
                        center_y = y + config.length / 2
                        
                        # 检查是否能放置（rotation=90）
                        if self.is_area_free(center_x, center_y, config.length, SHELF_WIDTH, rotation=90):
                            name = f"Shelf_{shelf_index}"
                            if self.place_shelf(name, center_x, center_y, segments, rotation=90):
                                print(f"    ✓ 放置 {segments}格货架 {name} at X={center_x:.1f}, Y={center_y:.1f}")
                                placed_count += 1
                                shelf_index += 1
                                row_placed += 1
                                y += config.length + gap
                                placed = True
                                break
                    
                    if not placed:
                        # 无法放置任何货架，跳到下一个位置
                        y += 1.0
                        if y >= region_y + region_height:
                            break
                
                print(f"    → 第 {row_num} 行放置了 {row_placed} 个货架")
                
                # 移动到下一行
                x += SHELF_WIDTH + 1.5  # 货架宽度 + 通道
        
        return placed_count
    
    def place_shelf_dual_channel(self, left_x: float, right_x: float, 
                                start_y: float, end_y: float,
                                strategy: PlacementStrategy = PlacementStrategy.ADAPTIVE,
                                cross_aisle_every_rows: int = 4,
                                cross_aisle_gap: float = 4.0) -> int:
        """
        双通道布局：在左右两个区域放置货架
        
        Args:
            left_x, right_x: 左右两个区域的X中心
            start_y, end_y: Y方向范围
            strategy: 摆放策略
            cross_aisle_every_rows: 每隔多少行插入横向通道
            cross_aisle_gap: 横向通道宽度
        
        Returns:
            int: 成功放置的货架数量
        """
        placed_count = 0
        shelf_index = 1
        
        min_spacing = 2.5  # 行间距
        y = start_y
        rows_since_cross = 0
        
        while y <= end_y:
            # 检查是否需要插入横向通道
            if rows_since_cross >= cross_aisle_every_rows:
                y += cross_aisle_gap
                rows_since_cross = 0
                continue
            
            # 在左右两个区域各放置一个货架
            for x in [left_x, right_x]:
                # 计算可用空间（假设每侧有足够空间）
                available_length = 8.0  # 默认可用长度
                
                if strategy == PlacementStrategy.ADAPTIVE:
                    segments = self.find_best_shelf_segments(x, y, available_length, rotation=0)
                elif strategy == PlacementStrategy.MIXED:
                    segments = random.choice(AVAILABLE_SEGMENTS)
                else:
                    segments = 4  # 默认4格
                
                if segments:
                    name = f"Shelf_{shelf_index}"
                    if self.place_shelf(name, x, y, segments, rotation=0):
                        placed_count += 1
                        shelf_index += 1
            
            y += min_spacing
            rows_since_cross += 1
        
        return placed_count
    
    def place_conveyor_line(self, start_x, end_x, y):
        """放置传送带线"""
        segment_index = 0
        x = start_x
        while x + 2 <= end_x:
            center_x = x + 1.0
            center_y = y + 0.5
            if self.place_conveyor_segment(f"Conveyor_{segment_index}", center_x, center_y):
                segment_index += 1
            x += 2
    
    def place_conveyor_segment(self, name, center_x, center_y):
        """放置传送带小段"""
        length, width = 2, 1
        if not self.is_area_free(center_x, center_y, length, width, margin=0):
            return False
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
    
    def place_robot_arm(self, name, center_x, center_y, rotation=0):
        """放置机械臂"""
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
        """放置托盘"""
        if random_size:
            crate_sizes = [
                {"length": 1.2, "width": 1.0, "height": 0.15},
                {"length": 1.2, "width": 1.2, "height": 0.15}
            ]
            crate_dims = random.choice(crate_sizes)
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
    
    def place_agent(self, agent_type, name, center_x, center_y, diameter=0.8, model="create3", rotation=0):
        """放置搬运车"""
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
    
    def place_box_on_shelf(self, shelf_name, box_name, shelf_center_x, shelf_center_y, 
                          level=1, offset_x=0, offset_y=0, rotation=0,
                          box_length=0.3, box_width=0.3, box_height=0.5):
        """在货架指定层上放置箱子
        
        Args:
            shelf_name: 货架名称
            box_name: 箱子名称
            shelf_center_x, shelf_center_y: 货架中心位置
            level: 层数 (1=1.3m, 2=2.8m, 3=4.1m或4.3m, 4=5.962m)
            offset_x, offset_y: 相对于货架中心的偏移（米）
            rotation: 货架旋转角度（0或90度）
            box_length, box_width, box_height: 箱子尺寸（米）
        
        注意：
        - 第3层：只有从左往右第1格是4.3m，其余格是4.1m
        - 第4层：只有从左往右第3格才有板子
        """
        # 获取货架信息
        shelf_length = None
        segments = 4
        for obj in self.objects:
            if obj.get('type') == 'shelf' and obj.get('name') == shelf_name:
                shelf_length = obj['dimensions']['length']
                segments = obj.get('segments', 4)
                break
        
        if shelf_length is None:
            shelf_length = 16.0
        
        # 货架层高对应表
        level_heights = {1: 1.3, 2: 2.8, 3: 4.1, 4: 5.962}
        
        if level not in level_heights:
            print(f"错误：层数 {level} 无效，应为 1-4")
            return False
        
        z_height = level_heights[level]
        
        # 第3层高度规则：
        # - 仅 4格货架：从左往右第1格为 4.3m，其余为 4.1m
        # - 其它货架（1/2/3格）：第3层统一为 4.1m
        if level == 3 and int(segments) == 4:
            seg_len = SHELF_SEGMENT_LENGTH  # 4m
            seg1_min = -shelf_length / 2.0 + SHELF_BOUNDARY_LEFT
            seg1_max = seg1_min + seg_len
            if seg1_min <= float(offset_x) < seg1_max:
                z_height = 4.3
        
        # 第4层特殊检查：只有第3格才有板子
        if level == 4:
            if segments < 4:
                print(f"警告：货架 {shelf_name} 只有{segments}格，没有第4层")
                return False
            seg_len = SHELF_SEGMENT_LENGTH
            seg3_start = -shelf_length / 2.0 + SHELF_BOUNDARY_LEFT + 2.0 * seg_len
            seg3_end = seg3_start + seg_len
            # 检查offset_x是否在第3格范围内
            if not (seg3_start <= float(offset_x) < seg3_end):
                print(f"警告：第4层只能在第3格放置箱子 (范围: {seg3_start:.1f}~{seg3_end:.1f}m)")
                return False
        
        # 计算箱子位置（offset_x/offset_y 视为货架局部坐标）
        if rotation == 90:
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
            "rotation": rotation
        }
        self.boxes.append(box)
        return True
    
    def _place_boxes_in_slot(self, shelf_name, cx, cy, level, rotation, 
                              slot_min, slot_max, z_height, box_types, spacing=0.05):
        """在单个格子内扫描放置箱子（参考原scene_to_json逻辑）
        
        Args:
            cx, cy: 货架中心的世界坐标
            slot_min, slot_max: 格子在局部X方向的范围（沿货架长度方向）
            z_height: 该位置的层高
        
        坐标系说明：
        - 局部坐标原点在货架中心
        - lx: 沿货架长度方向（格子排列方向）
        - ly: 沿货架宽度方向
        - rotation=0: 长度方向对应世界X，宽度方向对应世界Y
        - rotation=90: 长度方向对应世界Y，宽度方向对应世界-X
        """
        shelf_width = 0.8
        margin = 0.15  # 边缘留空
        
        # 格子内有效放置区域（局部坐标）
        x_min = slot_min + margin
        x_max = slot_max - margin
        y_min = -shelf_width / 2 + margin
        y_max = shelf_width / 2 - margin
        
        def to_world(lx, ly):
            """局部坐标转世界坐标"""
            if rotation == 90:
                # 旋转90度: 长度方向→世界Y，宽度方向→世界-X
                return (cx - ly, cy + lx)
            else:
                # 无旋转: 长度方向→世界X，宽度方向→世界Y
                return (cx + lx, cy + ly)
        
        occupied = []
        placed = 0
        
        def is_free(x, y, blen, bwid):
            if x - blen/2 < x_min or x + blen/2 > x_max:
                return False
            if y - bwid/2 < y_min or y + bwid/2 > y_max:
                return False
            for ox_min, ox_max, oy_min, oy_max in occupied:
                if not (x + blen/2 + spacing <= ox_min or x - blen/2 - spacing >= ox_max or
                        y + bwid/2 + spacing <= oy_min or y - bwid/2 - spacing >= oy_max):
                    return False
            return True
        
        # 扫描放置
        step = 0.05
        for box_type in box_types:
            if box_type not in BOX_TYPES:
                continue
            spec = BOX_TYPES[box_type]
            blen, bwid, bhgt = spec['length'], spec['width'], spec['height']
            
            found = False
            y = y_min + bwid / 2
            while y + bwid / 2 <= y_max and not found:
                x = x_min + blen / 2
                while x + blen / 2 <= x_max and not found:
                    if is_free(x, y, blen, bwid):
                        wx, wy = to_world(x, y)
                        self.boxes.append({
                            "type": "box",
                            "name": f"Box_{shelf_name}_L{level}_{placed+1}",
                            "dimensions": {"length": blen, "width": bwid, "height": bhgt},
                            "position": {"x": wx, "y": wy, "z": z_height},
                            "usd_file": "Box.usd",
                            "on_shelf": True,
                            "shelf_name": shelf_name,
                            "level": level,
                            "rotation": rotation
                        })
                        occupied.append((x - blen/2, x + blen/2, y - bwid/2, y + bwid/2))
                        placed += 1
                        found = True
                    x += step
                y += step
        return placed
    
    def _place_boxes_1_segment(self, shelf_name, cx, cy, level, rotation, spacing=0.05):
        """一格货架(6m)放置箱子 - 可用区域: [-2, 2]（两端各留1m边界）"""
        # 除4格货架外，第3层统一为 4.1m
        level_heights = {1: 1.3, 2: 2.8, 3: 4.1}
        if level not in level_heights:
            return 0
        
        # 随机选择箱子类型
        box_types = random.choice([
            ['medium', 'small'],
            ['small', 'small', 'small'],
            ['medium'],
        ])
        
        return self._place_boxes_in_slot(shelf_name, cx, cy, level, rotation,
                                         -2, 2, level_heights[level], box_types, spacing)
    
    def _place_boxes_2_segment(self, shelf_name, cx, cy, level, rotation, spacing=0.05):
        """二格货架(10m)放置箱子 - 格子1: [-4,0], 格子2: [0,4]"""
        level_heights = {1: 1.3, 2: 2.8, 3: 4.1}
        if level not in level_heights:
            return 0
        
        slots = [
            (-4, 0, level_heights[level]),  # 第1格
            (0, 4, level_heights[level]),  # 第2格
        ]
        
        total = 0
        for slot_min, slot_max, z in slots:
            box_types = random.choice([
                ['medium', 'small'],
                ['small', 'small'],
                ['medium'],
            ])
            total += self._place_boxes_in_slot(shelf_name, cx, cy, level, rotation,
                                               slot_min, slot_max, z, box_types, spacing)
        return total
    
    def _place_boxes_3_segment(self, shelf_name, cx, cy, level, rotation, spacing=0.05):
        """三格货架(14m)放置箱子
        
        格子定义（每格4m宽）：
        注意：3格货架的局部原点在“第1格右边界底部中心点”（而不是第2格中心）。
        因此这里的局部 x 采用如下定义：
        - 第1格: [-4, 0] (4m宽)
        - 第2格: [0, 4] (4m宽)
        - 第3格: [4, 8] (4m宽)
        """
        level_heights = {1: 1.3, 2: 2.8, 3: 4.1}
        if level not in level_heights:
            return 0
        
        slots = [
            (-4, 0, level_heights[level]),  # 第1格
            (0, 4, level_heights[level]),    # 第2格
            (4, 8, level_heights[level]),    # 第3格
        ]
        
        total = 0
        for slot_min, slot_max, z in slots:
            box_types = random.choice([
                ['medium', 'small'],
                ['small', 'small'],
                ['medium'],
            ])
            total += self._place_boxes_in_slot(shelf_name, cx, cy, level, rotation,
                                               slot_min, slot_max, z, box_types, spacing)
        return total
    
    def _place_boxes_4_segment(self, shelf_name, cx, cy, level, rotation, spacing=0.05):
        """四格货架(18m)放置箱子 - 格子1: [-8,-4], 格子2: [-4,0], 格子3: [0,4], 格子4: [4,8]"""
        level_heights = {1: 1.3, 2: 2.8, 3: 4.1, 4: 5.962}
        if level not in level_heights:
            return 0
        
        # 第4层只有第3格有板子
        if level == 4:
            slots = [(0, 4, level_heights[level])]
        else:
            slots = [
                (-8, -4, 4.3 if level == 3 else level_heights[level]),  # 第1格，第3层4.3m
                (-4, 0, level_heights[level]),   # 第2格
                (0, 4, level_heights[level]),    # 第3格
                (4, 8, level_heights[level]),    # 第4格
            ]
        
        total = 0
        for slot_min, slot_max, z in slots:
            box_types = random.choice([
                ['medium', 'small'],
                ['small', 'small'],
                ['medium'],
            ])
            total += self._place_boxes_in_slot(shelf_name, cx, cy, level, rotation,
                                               slot_min, slot_max, z, box_types, spacing)
        return total
    
    def place_mixed_boxes_on_shelf(self, shelf_name, shelf_center_x, shelf_center_y,
                                   level=1, rotation=0, box_types=None, max_stacks=2, spacing=0.05,
                                   shelf_length=None):
        """在货架上放置箱子（自动根据格数调用对应方法）"""
        # 获取货架格数 - 必须从货架对象中获取
        segments = None
        for obj in self.objects:
            if obj.get('type') == 'shelf' and obj.get('name') == shelf_name:
                segments = obj.get('segments')
                break
        
        # 如果没找到segments，根据shelf_length推断
        if segments is None:
            if shelf_length is not None:
                # 根据长度推断格数: 6m=1格, 10m=2格, 14m=3格, 18m=4格
                if shelf_length <= 7:
                    segments = 1
                elif shelf_length <= 11:
                    segments = 2
                elif shelf_length <= 15:
                    segments = 3
                else:
                    segments = 4
            else:
                segments = 4  # 默认
        
        # 根据格数调用对应方法
        if segments == 1:
            return self._place_boxes_1_segment(shelf_name, shelf_center_x, shelf_center_y, level, rotation, spacing)
        elif segments == 2:
            return self._place_boxes_2_segment(shelf_name, shelf_center_x, shelf_center_y, level, rotation, spacing)
        elif segments == 3:
            return self._place_boxes_3_segment(shelf_name, shelf_center_x, shelf_center_y, level, rotation, spacing)
        else:
            return self._place_boxes_4_segment(shelf_name, shelf_center_x, shelf_center_y, level, rotation, spacing)
    
    def place_box_on_crate(self, crate_name, box_name, offset_x=0, offset_y=0, layer=1,
                           box_length=0.2, box_width=0.2, box_height=0.6):
        """在托盘上放置箱子"""
        crate = None
        for c in self.crates:
            if c['name'] == crate_name:
                crate = c
                break
        
        if not crate:
            print(f"错误：找不到托盘 {crate_name}")
            return False
        
        crate_pos = crate['position']
        crate_dims = crate.get('dimensions', {})
        crate_height = float(crate_dims.get('height', 0.15))
        
        box_x = crate_pos['x'] + offset_x
        box_y = crate_pos['y'] + offset_y
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
        """在托盘上放置不同型号的混合箱子（支持多层堆叠）"""
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
        crate_length = crate_dims['length']
        crate_width = crate_dims['width']
        crate_height = float(crate_dims.get('height', 0.15))
        crate_top_z = float(crate_pos['z']) + crate_height
        
        # 如果没有指定箱子类型，根据托盘大小智能选择
        if box_types is None:
            if crate_length >= 1.2 and crate_width >= 1.2:
                box_types = random.choice([
                    ['large', 'large', 'medium', 'medium'],
                    ['medium', 'medium', 'medium', 'medium'],
                ])
            elif crate_length >= 1.2 or crate_width >= 1.0:
                box_types = random.choice([
                    ['large', 'medium', 'medium'],
                    ['medium', 'medium', 'medium'],
                ])
            else:
                box_types = ['medium', 'small']
        
        all_placed_boxes = []
        layers_data = []
        
        crate_x_min = -crate_length / 2 + spacing
        crate_x_max = crate_length / 2 - spacing
        crate_y_min = -crate_width / 2 + spacing
        crate_y_max = crate_width / 2 - spacing
        
        total_boxes_placed = 0
        
        for current_layer in range(1, max_layers + 1):
            current_occupied = []
            layer_boxes = []
            
            if current_layer == 1:
                layer_box_types = box_types
                
                def is_position_free(x, y, box_len, box_wid, check_layer_boxes):
                    box_x_min = x - box_len / 2
                    box_x_max = x + box_len / 2
                    box_y_min = y - box_wid / 2
                    box_y_max = y + box_wid / 2
                    
                    if (box_x_min < crate_x_min or box_x_max > crate_x_max or
                        box_y_min < crate_y_min or box_y_max > crate_y_max):
                        return False
                    
                    for ox_min, ox_max, oy_min, oy_max in check_layer_boxes:
                        if not (box_x_max + spacing <= ox_min or 
                               box_x_min - spacing >= ox_max or
                               box_y_max + spacing <= oy_min or 
                               box_y_min - spacing >= oy_max):
                            return False
                    
                    return True
            else:
                if len(layers_data) == 0:
                    break
                
                layer_box_types = random.choice([
                    ['medium', 'small'],
                    ['small', 'small', 'small'],
                ])
                
                def is_position_free(x, y, box_len, box_wid, check_layer_boxes):
                    box_x_min = x - box_len / 2
                    box_x_max = x + box_len / 2
                    box_y_min = y - box_wid / 2
                    box_y_max = y + box_wid / 2
                    
                    for ox_min, ox_max, oy_min, oy_max in check_layer_boxes:
                        if not (box_x_max + spacing <= ox_min or 
                               box_x_min - spacing >= ox_max or
                               box_y_max + spacing <= oy_min or 
                               box_y_min - spacing >= oy_max):
                            return False
                    
                    support_area = 0
                    box_area = box_len * box_wid
                    
                    for prev_box in layers_data:
                        px_min, px_max = prev_box['x_min'], prev_box['x_max']
                        py_min, py_max = prev_box['y_min'], prev_box['y_max']
                        
                        overlap_x_min = max(box_x_min, px_min)
                        overlap_x_max = min(box_x_max, px_max)
                        overlap_y_min = max(box_y_min, py_min)
                        overlap_y_max = min(box_y_max, py_max)
                        
                        if overlap_x_min < overlap_x_max and overlap_y_min < overlap_y_max:
                            overlap_area = (overlap_x_max - overlap_x_min) * (overlap_y_max - overlap_y_min)
                            support_area += overlap_area
                    
                    return support_area >= box_area * 0.5
            
            for idx, box_type in enumerate(layer_box_types):
                if box_type not in BOX_TYPES:
                    continue
                
                box_spec = BOX_TYPES[box_type]
                box_len = box_spec['length']
                box_wid = box_spec['width']
                box_hgt = box_spec['height']
                
                placed = False
                step = 0.05
                
                if current_layer == 1:
                    scan_y_min = crate_y_min + box_wid / 2
                    scan_y_max = crate_y_max
                    scan_x_min = crate_x_min + box_len / 2
                    scan_x_max = crate_x_max
                else:
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
                            if current_layer == 1:
                                box_z = crate_top_z
                            else:
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
                            
                            box_data = {
                                'x': x, 'y': y,
                                'x_min': x - box_len/2,
                                'x_max': x + box_len/2,
                                'y_min': y - box_wid/2,
                                'y_max': y + box_wid/2,
                                'height': (box_z - crate_top_z) + box_hgt,
                                'box_type': box_type
                            }
                            layers_data.append(box_data)
                            current_occupied.append((box_data['x_min'], box_data['x_max'], 
                                                   box_data['y_min'], box_data['y_max']))
                            
                            placed = True
                        x += step
                    y += step
            
            if layer_boxes:
                all_placed_boxes.extend(layer_boxes)
                total_boxes_placed += len(layer_boxes)
                
                box_summary = {}
                for _, box_type in layer_boxes:
                    box_summary[box_type] = box_summary.get(box_type, 0) + 1
                summary_str = ", ".join([f"{BOX_TYPES[bt]['name']}×{cnt}" for bt, cnt in box_summary.items()])
                print(f"  第{current_layer}层: {len(layer_boxes)}个箱子 ({summary_str})")
            else:
                break
        
        if all_placed_boxes:
            total_summary = {}
            for _, box_type in all_placed_boxes:
                total_summary[box_type] = total_summary.get(box_type, 0) + 1
            summary_str = ", ".join([f"{BOX_TYPES[bt]['name']}×{cnt}" for bt, cnt in total_summary.items()])
            print(f"✓ 在 {crate_name} 上放置了 {total_boxes_placed} 个混合箱子")
            print(f"  总计: {summary_str}")
        
        return total_boxes_placed
    
    def to_scene_dict(self, scene_name):
        """转换为场景字典"""
        import copy
        
        shelves = [copy.deepcopy(obj) for obj in self.objects if obj.get("type") == "shelf"]
        other_objects = [copy.deepcopy(obj) for obj in self.objects if obj.get("type") != "shelf"]
        crates = [copy.deepcopy(obj) for obj in self.crates]
        
        shelf_map = {obj["name"]: obj for obj in shelves}
        crate_map = {obj["name"]: obj for obj in crates}
        
        for shelf in shelves:
            shelf["children"] = []
        for crate in crates:
            crate["children"] = []
        
        standalone_boxes = []
        for box in self.boxes:
            box_copy = copy.deepcopy(box)
            if box.get("on_shelf") and box.get("shelf_name") in shelf_map:
                shelf_map[box["shelf_name"]]["children"].append(box_copy)
            elif box.get("on_crate") and box.get("crate_name") in crate_map:
                crate_map[box["crate_name"]]["children"].append(box_copy)
            else:
                standalone_boxes.append(box_copy)
        
        all_objects = shelves + other_objects + self.packing_tables + crates + standalone_boxes
        
        return {
            "scene_name": scene_name,
            "warehouse": {
                "name": "Warehouse",
                "dimensions": {"length": self.size, "width": self.size, "height": 10},
                "position": {"x": 0, "y": 0, "z": 0},
                "grid_size": self.size,
                "grid_occupancy": self.grid
            },
            "objects": all_objects,
            "agents": self.agents
        }


# ===== 布局生成函数 =====

def generate_adaptive_layout():
    """生成自适应布局：智能优化空间利用率，自动计算最优货架布局"""
    grid = WarehouseGrid(40)
    
    print("\n📦 智能自适应布局 - 开始放置物体...")
    
    # 1. 放置传送带（底部）
    grid.place_conveyor_line(1, 38, 0)
    print("✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘（靠近传送带）
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 智能优化货架布局 - 自动计算最优列数和分布
    # 可用空间：X方向 3-38m (留出边距)，Y方向 6-37m
    result = grid.place_shelves_optimized(
        start_x=3,
        end_x=38,
        start_y=6,
        end_y=37,
        aisle_width=2.0,  # AGV通道宽度
        strategy=PlacementStrategy.ADAPTIVE
    )
    
    print(f"\n✅ 货架布局完成：共放置 {result['total']} 个货架")
    print(f"   货架类型分布：")
    for seg in [4, 3, 2, 1]:
        count = result['by_segments'].get(seg, 0)
        if count > 0:
            length = SHELF_BOUNDARY_LEFT + seg * SHELF_SEGMENT_LENGTH + SHELF_BOUNDARY_RIGHT
            print(f"   ├─ {seg}格货架({length:.0f}m): {count} 个")
    print(f"   └─ 混合使用多种规格，空间利用率最大化")
    
    # 4. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'], 
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 3), 
                                        max(1, len(all_shelf_names) * 2 // 3))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation, shelf_length = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 3)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level, 
                                                     rotation=rotation, max_stacks=max_stacks,
                                                     spacing=random.uniform(0.03, 0.08),
                                                     shelf_length=shelf_length)
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 5. 在通道中放置搬运车（根据实际通道位置动态调整）
    grid.place_agent("agv", "AGV_1", 15, 15, model="create3")
    grid.place_agent("agv", "AGV_2", 25, 25, model="ridgeback")
    print("✓ 搬运车已放置")
    
    return grid.to_scene_dict("Scene_Adaptive_Optimized")


def generate_mixed_layout():
    """生成混合布局：随机使用不同格数的货架"""
    grid = WarehouseGrid(40)
    
    print("\n📦 混合布局 - 开始放置物体...")
    
    # 1. 放置传送带
    grid.place_conveyor_line(1, 38, 0)
    print("✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 混合策略放置货架
    print("\n📦 混合策略放置货架...")
    
    left_count = grid.place_shelf_block(
        region_x=5, region_y=7,
        region_width=13, region_height=30,
        strategy=PlacementStrategy.MIXED
    )
    print(f"  左区域：放置了 {left_count} 个货架（混合格数）")
    
    right_count = grid.place_shelf_block(
        region_x=22, region_y=7,
        region_width=13, region_height=30,
        strategy=PlacementStrategy.MIXED
    )
    print(f"  右区域：放置了 {right_count} 个货架（混合格数）")
    
    # 4. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'],
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 4),
                                        max(1, len(all_shelf_names) // 2))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation, shelf_length = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks,
                                                     spacing=random.uniform(0.03, 0.08),
                                                     shelf_length=shelf_length)
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 5. 放置搬运车
    grid.place_agent("agv", "AGV_1", 20, 15, model="create3")
    print("✓ 搬运车已放置")
    
    return grid.to_scene_dict("Scene_Mixed")


def generate_dual_channel_adaptive():
    """生成双通道自适应布局"""
    grid = WarehouseGrid(40)
    
    print("\n📦 双通道自适应布局 - 开始放置物体...")
    
    # 1. 放置传送带
    grid.place_conveyor_line(1, 38, 0)
    print("✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 双通道自适应布局
    print("\n📦 双通道自适应放置货架...")
    
    count = grid.place_shelf_dual_channel(
        left_x=10, right_x=30,
        start_y=7, end_y=35,
        strategy=PlacementStrategy.ADAPTIVE,
        cross_aisle_every_rows=4,
        cross_aisle_gap=4.0
    )
    print(f"  共放置了 {count} 个货架")
    
    # 4. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'],
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 4),
                                        max(1, len(all_shelf_names) // 2))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation, shelf_length = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks,
                                                     spacing=random.uniform(0.03, 0.08),
                                                     shelf_length=shelf_length)
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 5. 放置搬运车
    grid.place_agent("agv", "AGV_1", 20, 15, model="create3")
    grid.place_agent("agv", "AGV_2", 20, 28, model="ridgeback")
    print("✓ 搬运车已放置")
    
    return grid.to_scene_dict("Scene_DualChannelAdaptive")


def generate_grid_mixed():
    """生成网格混合布局：不同区域使用不同格数"""
    grid = WarehouseGrid(40)
    
    print("\n📦 网格混合布局 - 开始放置物体...")
    
    # 1. 放置传送带
    grid.place_conveyor_line(1, 38, 0)
    print("✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 不同区域使用不同格数
    print("\n📦 网格混合放置货架...")
    
    # 左上区域：1格货架
    count1 = grid.place_shelf_grid(5, 7, rows=5, cols=3, segments=1, spacing_x=2.5, spacing_y=3.0)
    print(f"  左上区域（1格）：{count1} 个货架")
    
    # 右上区域：2格货架
    count2 = grid.place_shelf_grid(22, 7, rows=5, cols=2, segments=2, spacing_x=5.0, spacing_y=3.0)
    print(f"  右上区域（2格）：{count2} 个货架")
    
    # 左下区域：3格货架
    count3 = grid.place_shelf_grid(5, 22, rows=3, cols=2, segments=3, spacing_x=7.0, spacing_y=3.0)
    print(f"  左下区域（3格）：{count3} 个货架")
    
    # 右下区域：4格货架
    count4 = grid.place_shelf_grid(22, 22, rows=3, cols=1, segments=4, spacing_x=8.0, spacing_y=3.0)
    print(f"  右下区域（4格）：{count4} 个货架")
    
    # 4. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'],
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 3),
                                        max(1, len(all_shelf_names) * 2 // 3))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation, shelf_length = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks,
                                                     spacing=random.uniform(0.03, 0.08),
                                                     shelf_length=shelf_length)
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 5. 放置搬运车
    grid.place_agent("agv", "AGV_1", 20, 15, model="create3")
    print("✓ 搬运车已放置")
    
    return grid.to_scene_dict("Scene_GridMixed")


def generate_greedy_column_layout():
    """
    生成贪心列布局：货架原始朝向（长边沿X轴），一列一列放置
    策略：每列优先放最长格的货架，空间逐步剔除
    """
    grid = WarehouseGrid(40)
    
    print("\n📦 贪心列布局（方案1：原始朝向）- 开始放置物体...")
    print("   策略：货架长边沿X轴，一列一列放，优先放最长格货架")
    
    # 1. 放置传送带
    grid.place_conveyor_line(1, 38, 0)
    print("✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 贪心列布局放置货架
    print("\n📦 贪心列布局放置货架...")
    print("="*60)
    
    count = grid.place_shelf_column_greedy(
        region_x=3,
        region_y=6,
        region_width=35,
        region_height=31,
        rotation=0,  # 原始朝向
        gap=0.3
    )
    
    print("="*60)
    print(f"✅ 共放置了 {count} 个货架")
    
    # 统计各种格数的货架数量
    shelves = [o for o in grid.objects if o.get("type") == "shelf"]
    segment_stats = {1: 0, 2: 0, 3: 0, 4: 0}
    for shelf in shelves:
        seg = shelf.get("segments", 4)
        segment_stats[seg] = segment_stats.get(seg, 0) + 1
    
    print(f"\n📊 货架类型分布：")
    for seg in [4, 3, 2, 1]:
        count_seg = segment_stats.get(seg, 0)
        if count_seg > 0:
            length = SHELF_BOUNDARY_LEFT + seg * SHELF_SEGMENT_LENGTH + SHELF_BOUNDARY_RIGHT
            print(f"   ├─ {seg}格货架({length:.0f}m): {count_seg} 个")
    
    # 4. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'],
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 4),
                                        max(1, len(all_shelf_names) // 2))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation, shelf_length = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks,
                                                     spacing=random.uniform(0.03, 0.08),
                                                     shelf_length=shelf_length)
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 5. 放置搬运车
    grid.place_agent("agv", "AGV_1", 20, 20, model="create3")
    grid.place_agent("agv", "AGV_2", 15, 28, model="ridgeback")
    print("✓ 搬运车已放置")
    
    return grid.to_scene_dict("Scene_GreedyColumn")


def generate_greedy_row_layout():
    """
    生成贪心行布局：货架旋转90度（长边沿Y轴），一行一行放置
    策略：每行优先放最长格的货架，空间逐步剔除
    """
    grid = WarehouseGrid(40)
    
    print("\n📦 贪心行布局（方案2：旋转90度）- 开始放置物体...")
    print("   策略：货架长边沿Y轴，一行一行放，优先放最长格货架")
    
    # 1. 放置传送带
    grid.place_conveyor_line(1, 38, 0)
    print("✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 贪心行布局放置货架
    print("\n📦 贪心行布局放置货架...")
    print("="*60)
    
    count = grid.place_shelf_column_greedy(
        region_x=3,
        region_y=6,
        region_width=35,
        region_height=31,
        rotation=90,  # 旋转90度
        gap=0.3
    )
    
    print("="*60)
    print(f"✅ 共放置了 {count} 个货架")
    
    # 统计各种格数的货架数量
    shelves = [o for o in grid.objects if o.get("type") == "shelf"]
    segment_stats = {1: 0, 2: 0, 3: 0, 4: 0}
    for shelf in shelves:
        seg = shelf.get("segments", 4)
        segment_stats[seg] = segment_stats.get(seg, 0) + 1
    
    print(f"\n📊 货架类型分布：")
    for seg in [4, 3, 2, 1]:
        count_seg = segment_stats.get(seg, 0)
        if count_seg > 0:
            length = SHELF_BOUNDARY_LEFT + seg * SHELF_SEGMENT_LENGTH + SHELF_BOUNDARY_RIGHT
            print(f"   ├─ {seg}格货架({length:.0f}m): {count_seg} 个")
    
    # 4. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'],
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 4),
                                        max(1, len(all_shelf_names) // 2))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation, shelf_length = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks,
                                                     spacing=random.uniform(0.03, 0.08),
                                                     shelf_length=shelf_length)
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 5. 放置搬运车
    grid.place_agent("agv", "AGV_1", 12, 20, model="create3")
    grid.place_agent("agv", "AGV_2", 25, 20, model="ridgeback")
    print("✓ 搬运车已放置")
    
    return grid.to_scene_dict("Scene_GreedyRow")


def generate_four_zone_layout():
    """
    生成四区域布局（方案3）：将货架区域按比例分成4个模块，每个模块放不同格数的货架
    
    布局示意（按货架大小合理分配空间）：
    ┌──────────────────┬──────────────┐
    │   模块1(4格)     │  模块2(3格)  │
    │   左上区(较大)   │  右上区(较大)│
    ├──────────────────┼──────────────┤
    │   模块3(2格)     │  模块4(1格)  │
    │   左下区         │  右下区(较小)│
    └──────────────────┴──────────────┘
    
    策略：
    - 按货架大小合理分配区域：4格>3格>2格>1格
    - 模块1（左上）：55%宽度×58%高度，放4格货架（18m，最大规格）
    - 模块2（右上）：45%宽度×58%高度，放3格货架（14m）
    - 模块3（左下）：55%宽度×42%高度，放2格货架（10m）
    - 模块4（右下）：45%宽度×42%高度，放1格货架（6m，最小规格）
    - 大货架分配更多空间，小货架分配较少空间
    """
    grid = WarehouseGrid(40)
    
    print("\n📦 四区域布局（方案3）- 开始放置物体...")
    print("   策略：按货架大小合理分配区域，大货架区域更大")
    print("   ┌──────────────────┬──────────────┐")
    print("   │   模块1(4格)     │  模块2(3格)  │")
    print("   │  左上(55%×58%)   │ 右上(45%×58%)│")
    print("   ├──────────────────┼──────────────┤")
    print("   │   模块3(2格)     │  模块4(1格)  │")
    print("   │  左下(55%×42%)   │ 右下(45%×42%)│")
    print("   └──────────────────┴──────────────┘")
    
    # 1. 放置传送带
    grid.place_conveyor_line(1, 38, 0)
    print("\n✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 计算四个区域的边界（按比例分配）
    # 可用区域：X: 3-38m (35m), Y: 6-37m (31m)
    region_start_x = 3
    region_start_y = 6
    region_width = 35
    region_height = 31
    
    # 留出中央通道（2m宽）
    aisle_width = 2.0
    half_aisle = aisle_width / 2
    
    # 按比例分割（左侧55%，右侧45%）
    # 左侧可以放下18m的4格货架，右侧可以放下14m的3格货架
    left_width_ratio = 0.55  # 左侧占55%
    right_width_ratio = 0.45  # 右侧占45%
    
    # 上下分割（上侧58%，下侧42%）
    # 上侧给大货架（4格、3格）更多空间
    top_height_ratio = 0.58  # 上侧占58%
    bottom_height_ratio = 0.42  # 下侧占42%
    
    # 计算分割点
    split_x = region_start_x + region_width * left_width_ratio
    split_y = region_start_y + region_height * bottom_height_ratio
    
    print(f"\n📦 四区域布局放置货架...")
    print(f"   总区域: X={region_start_x}-{region_start_x + region_width}m, Y={region_start_y}-{region_start_y + region_height}m")
    print(f"   左右分割点: X={split_x:.1f}m (左60% | 右40%)")
    print(f"   上下分割点: Y={split_y:.1f}m (下45% | 上55%)")
    print(f"   中央十字通道宽度: {aisle_width}m")
    print("="*60)
    
    total_count = 0
    zone_stats = {}
    
    # 计算四个区域的具体坐标
    # 模块1：左上区（大区域）- X: 0-60%, Y: 45%-100%
    zone1_x = region_start_x
    zone1_y = split_y + half_aisle
    zone1_width = (split_x - region_start_x - half_aisle)
    zone1_height = (region_start_y + region_height - split_y - half_aisle)
    
    # 模块2：右上区 - X: 60%-100%, Y: 45%-100%
    zone2_x = split_x + half_aisle
    zone2_y = split_y + half_aisle
    zone2_width = (region_start_x + region_width - split_x - half_aisle)
    zone2_height = (region_start_y + region_height - split_y - half_aisle)
    
    # 模块3：左下区（大区域）- X: 0-60%, Y: 0-45%
    zone3_x = region_start_x
    zone3_y = region_start_y
    zone3_width = (split_x - region_start_x - half_aisle)
    zone3_height = (split_y - region_start_y - half_aisle)
    
    # 模块4：右下区（小区域）- X: 60%-100%, Y: 0-45%
    zone4_x = split_x + half_aisle
    zone4_y = region_start_y
    zone4_width = (region_start_x + region_width - split_x - half_aisle)
    zone4_height = (split_y - region_start_y - half_aisle)
    
    # 模块1：左上区 - 4格货架（混合朝向，充分利用空间）
    print(f"\n📦 模块1（左上区）：放4格货架（横向+纵向混合）")
    print(f"   区域: X={zone1_x:.1f}-{zone1_x+zone1_width:.1f}m ({zone1_width:.1f}m), Y={zone1_y:.1f}-{zone1_y+zone1_height:.1f}m ({zone1_height:.1f}m)")
    
    # 先放置横向的4格货架
    zone1_h_count = grid.place_shelf_fixed_segments(
        region_x=zone1_x,
        region_y=zone1_y,
        region_width=zone1_width,
        region_height=zone1_height,
        segments=4,
        rotation=0,
        gap=0.3
    )
    print(f"   ✓ 横向放置了 {zone1_h_count} 个4格货架")
    
    # 再在剩余空间放置纵向的4格货架（旋转90度）
    zone1_v_count = grid.place_shelf_fixed_segments(
        region_x=zone1_x,
        region_y=zone1_y,
        region_width=zone1_width,
        region_height=zone1_height,
        segments=4,
        rotation=90,
        gap=0.3
    )
    print(f"   ✓ 纵向放置了 {zone1_v_count} 个4格货架")
    
    zone1_count = zone1_h_count + zone1_v_count
    print(f"   ✓ 模块1共放置了 {zone1_count} 个4格货架（横向{zone1_h_count}+纵向{zone1_v_count}）")
    total_count += zone1_count
    zone_stats[4] = zone1_count
    
    # 模块2：右上区 - 3格货架（混合朝向，充分利用空间）
    print(f"\n📦 模块2（右上区）：放3格货架（横向+纵向混合）")
    print(f"   区域: X={zone2_x:.1f}-{zone2_x+zone2_width:.1f}m ({zone2_width:.1f}m), Y={zone2_y:.1f}-{zone2_y+zone2_height:.1f}m ({zone2_height:.1f}m)")
    
    # 先放置横向的3格货架
    zone2_h_count = grid.place_shelf_fixed_segments(
        region_x=zone2_x,
        region_y=zone2_y,
        region_width=zone2_width,
        region_height=zone2_height,
        segments=3,
        rotation=0,
        gap=0.3
    )
    print(f"   ✓ 横向放置了 {zone2_h_count} 个3格货架")
    
    # 再在剩余空间放置纵向的3格货架（旋转90度）
    zone2_v_count = grid.place_shelf_fixed_segments(
        region_x=zone2_x,
        region_y=zone2_y,
        region_width=zone2_width,
        region_height=zone2_height,
        segments=3,
        rotation=90,
        gap=0.3
    )
    print(f"   ✓ 纵向放置了 {zone2_v_count} 个3格货架")
    
    zone2_count = zone2_h_count + zone2_v_count
    print(f"   ✓ 模块2共放置了 {zone2_count} 个3格货架（横向{zone2_h_count}+纵向{zone2_v_count}）")
    total_count += zone2_count
    zone_stats[3] = zone2_count
    
    # 模块3：左下区 - 2格货架（混合朝向，充分利用空间）
    print(f"\n📦 模块3（左下区）：放2格货架（横向+纵向混合）")
    print(f"   区域: X={zone3_x:.1f}-{zone3_x+zone3_width:.1f}m ({zone3_width:.1f}m), Y={zone3_y:.1f}-{zone3_y+zone3_height:.1f}m ({zone3_height:.1f}m)")
    
    # 先放置横向的2格货架
    zone3_h_count = grid.place_shelf_fixed_segments(
        region_x=zone3_x,
        region_y=zone3_y,
        region_width=zone3_width,
        region_height=zone3_height,
        segments=2,
        rotation=0,
        gap=0.3
    )
    print(f"   ✓ 横向放置了 {zone3_h_count} 个2格货架")
    
    # 再在剩余空间放置纵向的2格货架（旋转90度）
    zone3_v_count = grid.place_shelf_fixed_segments(
        region_x=zone3_x,
        region_y=zone3_y,
        region_width=zone3_width,
        region_height=zone3_height,
        segments=2,
        rotation=90,
        gap=0.3
    )
    print(f"   ✓ 纵向放置了 {zone3_v_count} 个2格货架")
    
    zone3_count = zone3_h_count + zone3_v_count
    print(f"   ✓ 模块3共放置了 {zone3_count} 个2格货架（横向{zone3_h_count}+纵向{zone3_v_count}）")
    total_count += zone3_count
    zone_stats[2] = zone3_count
    
    # 模块4：右下区 - 1格货架（混合朝向，充分利用空间）
    print(f"\n📦 模块4（右下区）：放1格货架（横向+纵向混合）")
    print(f"   区域: X={zone4_x:.1f}-{zone4_x+zone4_width:.1f}m ({zone4_width:.1f}m), Y={zone4_y:.1f}-{zone4_y+zone4_height:.1f}m ({zone4_height:.1f}m)")
    
    # 先放置横向的1格货架
    zone4_h_count = grid.place_shelf_fixed_segments(
        region_x=zone4_x,
        region_y=zone4_y,
        region_width=zone4_width,
        region_height=zone4_height,
        segments=1,
        rotation=0,
        gap=0.3
    )
    print(f"   ✓ 横向放置了 {zone4_h_count} 个1格货架")
    
    # 再在剩余空间放置纵向的1格货架（旋转90度）
    zone4_v_count = grid.place_shelf_fixed_segments(
        region_x=zone4_x,
        region_y=zone4_y,
        region_width=zone4_width,
        region_height=zone4_height,
        segments=1,
        rotation=90,
        gap=0.3
    )
    print(f"   ✓ 纵向放置了 {zone4_v_count} 个1格货架")
    
    zone4_count = zone4_h_count + zone4_v_count
    print(f"   ✓ 模块4共放置了 {zone4_count} 个1格货架（横向{zone4_h_count}+纵向{zone4_v_count}）")
    total_count += zone4_count
    zone_stats[1] = zone4_count
    
    print("="*60)
    print(f"✅ 四区域布局完成，共放置了 {total_count} 个货架")
    
    print(f"\n📊 各模块货架分布：")
    print(f"   ├─ 模块1（左上，55%×58%）：{zone_stats.get(4, 0)} 个 4格货架(18m)")
    print(f"   ├─ 模块2（右上，45%×58%）：{zone_stats.get(3, 0)} 个 3格货架(14m)")
    print(f"   ├─ 模块3（左下，55%×42%）：{zone_stats.get(2, 0)} 个 2格货架(10m)")
    print(f"   └─ 模块4（右下，45%×42%）：{zone_stats.get(1, 0)} 个 1格货架(6m)")
    
    # 4. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'],
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 3),
                                        max(1, len(all_shelf_names) * 2 // 3))
    selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
    
    print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
    
    for shelf_name in selected_shelves:
        x, y, rotation, shelf_length = shelf_positions[shelf_name]
        available_levels = [1, 2, 3, 4]
        num_levels = random.randint(1, 2)
        selected_levels = random.sample(available_levels, num_levels)
        
        for level in sorted(selected_levels):
            max_stacks = random.randint(1, 2)
            placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                     rotation=rotation, max_stacks=max_stacks,
                                                     spacing=random.uniform(0.03, 0.08),
                                                     shelf_length=shelf_length)
            if placed > 0:
                print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    
    # 5. 在中央十字通道放置搬运车
    grid.place_agent("agv", "AGV_1", split_x, split_y - 3, model="create3")
    grid.place_agent("agv", "AGV_2", split_x, split_y + 3, model="ridgeback")
    print("✓ 搬运车已放置在中央通道")
    
    return grid.to_scene_dict("Scene_FourZone")


def generate_four_zone_random_layout():
    """
    生成随机四区域布局（方案3增强版）：随机分配货架类型、区域大小和放置方向
    
    随机化要素：
    1. 模块位置随机：4种货架随机分配到4个模块
    2. 区域大小随机：在满足最小尺寸要求的前提下随机分配
    3. 放置方向随机：每个模块随机选择横向、纵向或混合放置
    
    约束条件：
    - 每个区域至少能放下1列该种货架
    - 保留中央通道供AGV通行
    """
    grid = WarehouseGrid(40)
    
    print("\n📦 随机四区域布局（方案3增强版）- 开始放置物体...")
    print("   策略：随机分配货架类型、区域大小和放置方向")
    
    # 1. 放置传送带
    grid.place_conveyor_line(1, 38, 0)
    print("\n✓ 传送带已放置")
    
    # 2. 放置机械臂和托盘
    arm_x = 5
    arm_y = 1.5
    crate_y = 3.0
    grid.place_robot_arm("RobotArm_1", arm_x, arm_y)
    grid.place_crate("Pallet_1", arm_x, crate_y, random_size=True)
    print("✓ 机械臂和托盘已放置")
    
    # 在托盘上放置混合箱子
    print("\n📦 在托盘上放置混合箱子（多层堆叠）...")
    grid.place_mixed_boxes_on_crate("Pallet_1", max_layers=2, spacing=0.05)
    
    # 3. 随机分配货架类型到4个模块
    shelf_types = [1, 2, 3, 4]
    random.shuffle(shelf_types)
    
    zone_assignments = {
        'left_top': shelf_types[0],
        'right_top': shelf_types[1],
        'left_bottom': shelf_types[2],
        'right_bottom': shelf_types[3]
    }
    
    print(f"\n🎲 随机货架分配：")
    print(f"   左上区：{zone_assignments['left_top']}格货架")
    print(f"   右上区：{zone_assignments['right_top']}格货架")
    print(f"   左下区：{zone_assignments['left_bottom']}格货架")
    print(f"   右下区：{zone_assignments['right_bottom']}格货架")
    
    # 4. 计算每种货架的最小尺寸要求
    region_start_x = 3
    region_start_y = 6
    region_width = 35
    region_height = 31
    aisle_width = 2.0
    half_aisle = aisle_width / 2
    
    # 计算左右两侧需要的最小宽度
    left_top_seg = zone_assignments['left_top']
    left_bottom_seg = zone_assignments['left_bottom']
    right_top_seg = zone_assignments['right_top']
    right_bottom_seg = zone_assignments['right_bottom']
    
    # 左侧最小宽度 = max(左上货架长度, 左下货架长度) + 货架宽度 + 通道 + 边距
    # 需要考虑：货架长度 + 货架之间的间隙 + 至少能放1列的空间
    left_min_width = max(ShelfConfig(left_top_seg).length, ShelfConfig(left_bottom_seg).length) + SHELF_WIDTH + 2.5
    right_min_width = max(ShelfConfig(right_top_seg).length, ShelfConfig(right_bottom_seg).length) + SHELF_WIDTH + 2.5
    
    # 上下最小高度（考虑横向和纵向放置的可能性）
    top_min_height = max(ShelfConfig(left_top_seg).length, ShelfConfig(right_top_seg).length) + SHELF_WIDTH + 2.5
    bottom_min_height = max(ShelfConfig(left_bottom_seg).length, ShelfConfig(right_bottom_seg).length) + SHELF_WIDTH + 2.5
    
    # 检查总尺寸是否可行
    if left_min_width + right_min_width + aisle_width > region_width:
        print(f"   ⚠️ 警告：左右区域最小宽度之和({left_min_width + right_min_width + aisle_width:.1f}m) > 总宽度({region_width}m)")
        print(f"   🔄 调整分配方案...")
        # 按比例缩减
        total_min = left_min_width + right_min_width + aisle_width
        left_min_width = left_min_width * (region_width - aisle_width) / (left_min_width + right_min_width)
        right_min_width = right_min_width * (region_width - aisle_width) / (left_min_width + right_min_width)
    
    if top_min_height + bottom_min_height + aisle_width > region_height:
        print(f"   ⚠️ 警告：上下区域最小高度之和({top_min_height + bottom_min_height + aisle_width:.1f}m) > 总高度({region_height}m)")
        print(f"   🔄 调整分配方案...")
        # 按比例缩减
        total_min = top_min_height + bottom_min_height + aisle_width
        top_min_height = top_min_height * (region_height - aisle_width) / (top_min_height + bottom_min_height)
        bottom_min_height = bottom_min_height * (region_height - aisle_width) / (top_min_height + bottom_min_height)
    
    # 随机生成分割比例（在合理范围内）
    # 左右比例：确保两侧都有足够空间
    left_ratio_min = left_min_width / region_width
    left_ratio_max = (region_width - right_min_width - aisle_width) / region_width
    
    # 确保范围有效
    if left_ratio_min >= left_ratio_max:
        left_ratio = left_min_width / region_width
    else:
        left_ratio = random.uniform(left_ratio_min, min(0.65, left_ratio_max))
    
    # 上下比例
    bottom_ratio_min = bottom_min_height / region_height
    bottom_ratio_max = (region_height - top_min_height - aisle_width) / region_height
    
    # 确保范围有效
    if bottom_ratio_min >= bottom_ratio_max:
        bottom_ratio = bottom_min_height / region_height
    else:
        bottom_ratio = random.uniform(bottom_ratio_min, min(0.55, bottom_ratio_max))
    
    split_x = region_start_x + region_width * left_ratio
    split_y = region_start_y + region_height * bottom_ratio
    
    print(f"\n🎲 随机区域分配：")
    print(f"   左右分割: {left_ratio*100:.1f}% | {(1-left_ratio)*100:.1f}%")
    print(f"   上下分割: {(1-bottom_ratio)*100:.1f}% | {bottom_ratio*100:.1f}%")
    
    # 5. 计算四个区域的具体坐标
    zones = {
        'left_top': {
            'x': region_start_x,
            'y': split_y + half_aisle,
            'width': split_x - region_start_x - half_aisle,
            'height': region_start_y + region_height - split_y - half_aisle,
            'segments': left_top_seg
        },
        'right_top': {
            'x': split_x + half_aisle,
            'y': split_y + half_aisle,
            'width': region_start_x + region_width - split_x - half_aisle,
            'height': region_start_y + region_height - split_y - half_aisle,
            'segments': right_top_seg
        },
        'left_bottom': {
            'x': region_start_x,
            'y': region_start_y,
            'width': split_x - region_start_x - half_aisle,
            'height': split_y - region_start_y - half_aisle,
            'segments': left_bottom_seg
        },
        'right_bottom': {
            'x': split_x + half_aisle,
            'y': region_start_y,
            'width': region_start_x + region_width - split_x - half_aisle,
            'height': split_y - region_start_y - half_aisle,
            'segments': right_bottom_seg
        }
    }
    
    # 6. 智能混合放置：先放主方向，再用旋转方向填充剩余空间
    print(f"\n📦 开始放置货架（智能填充策略）...")
    print("="*60)
    
    total_count = 0
    zone_stats = {1: 0, 2: 0, 3: 0, 4: 0}
    zone_names = {
        'left_top': '左上区',
        'right_top': '右上区',
        'left_bottom': '左下区',
        'right_bottom': '右下区'
    }
    
    # 先验证大货架（4格和3格）能否成功放置
    critical_segments = [4, 3]
    critical_zones_failed = []
    
    for zone_key, zone_info in zones.items():
        if zone_info['segments'] in critical_segments:
            segments = zone_info['segments']
            # 检查区域尺寸是否足够（至少一个维度要能放下货架）
            shelf_length = ShelfConfig(segments).length
            # 横向或纵向至少有一个方向能放下一排货架
            can_place_horizontal = zone_info['width'] >= shelf_length + 0.5
            can_place_vertical = zone_info['height'] >= shelf_length + 0.5
            
            if not (can_place_horizontal or can_place_vertical):
                critical_zones_failed.append((zone_key, segments, zone_info['width'], zone_info['height']))
                print(f"   ⚠️ {zone_names[zone_key]}（{segments}格，{shelf_length:.0f}m）区域过小: " +
                      f"{zone_info['width']:.1f}m×{zone_info['height']:.1f}m")
                print(f"      横向: {zone_info['width']:.1f}m < {shelf_length:.0f}m ❌")
                print(f"      纵向: {zone_info['height']:.1f}m < {shelf_length:.0f}m ❌")
    
    if critical_zones_failed:
        print(f"\n❌ 关键货架（4格/3格）无法放置，布局不可行！")
        print(f"   建议：重新运行脚本生成新的随机布局")
        # 返回空场景（或者可以在这里实现重试逻辑）
        return grid.to_scene_dict("Scene_FourZoneRandom_Failed")
    
    # 按格数从大到小排序放置（优先放大货架）
    zones_sorted = sorted(zones.items(), key=lambda x: x[1]['segments'], reverse=True)
    
    for zone_key, zone_info in zones_sorted:
        zone_name = zone_names[zone_key]
        segments = zone_info['segments']
        shelf_length = ShelfConfig(segments).length
        
        # 智能选择主放置方向：根据区域形状
        # 如果宽度 > 高度，优先横向；如果高度 > 宽度，优先纵向
        width_ratio = zone_info['width'] / shelf_length if shelf_length > 0 else 0
        height_ratio = zone_info['height'] / shelf_length if shelf_length > 0 else 0
        
        # 随机决定是否按最优方向，还是反向（增加随机性）
        prefer_optimal = random.random() < 0.7  # 70%概率选择最优方向
        
        if prefer_optimal:
            # 选择更适合的方向作为主方向
            if width_ratio > height_ratio:
                primary_rotation = 0  # 横向
                secondary_rotation = 90  # 纵向填充
                primary_name = "横向"
                secondary_name = "纵向"
            else:
                primary_rotation = 90  # 纵向
                secondary_rotation = 0  # 横向填充
                primary_name = "纵向"
                secondary_name = "横向"
        else:
            # 随机选择方向
            if random.random() < 0.5:
                primary_rotation = 0
                secondary_rotation = 90
                primary_name = "横向"
                secondary_name = "纵向"
            else:
                primary_rotation = 90
                secondary_rotation = 0
                primary_name = "纵向"
                secondary_name = "横向"
        
        print(f"\n📦 {zone_name}（{segments}格货架，{shelf_length:.0f}m）")
        print(f"   区域: {zone_info['width']:.1f}m × {zone_info['height']:.1f}m")
        print(f"   策略: 先{primary_name}放置，再用{secondary_name}填充剩余空间")
        
        # 第一步：按主方向放置
        primary_count = grid.place_shelf_fixed_segments(
            region_x=zone_info['x'],
            region_y=zone_info['y'],
            region_width=zone_info['width'],
            region_height=zone_info['height'],
            segments=segments,
            rotation=primary_rotation,
            gap=0.3
        )
        print(f"   ✓ {primary_name}放置: {primary_count} 个")
        
        # 第二步：在剩余空间中用旋转方向填充
        secondary_count = grid.place_shelf_fixed_segments(
            region_x=zone_info['x'],
            region_y=zone_info['y'],
            region_width=zone_info['width'],
            region_height=zone_info['height'],
            segments=segments,
            rotation=secondary_rotation,
            gap=0.3
        )
        
        if secondary_count > 0:
            print(f"   ✓ {secondary_name}填充: {secondary_count} 个")
            print(f"   ✨ 共放置: {primary_count + secondary_count} 个（主{primary_count}+填充{secondary_count}）")
        else:
            print(f"   ℹ️  剩余空间不足，未能放置{secondary_name}货架")
            print(f"   ✓ 共放置: {primary_count} 个")
        
        zone_count = primary_count + secondary_count
        total_count += zone_count
        zone_stats[segments] = zone_stats.get(segments, 0) + zone_count
    
    print("="*60)
    print(f"✅ 随机四区域布局完成，共放置了 {total_count} 个货架")
    
    print(f"\n📊 各格数货架分布：")
    for seg in [4, 3, 2, 1]:
        count = zone_stats.get(seg, 0)
        if count > 0:
            length = SHELF_BOUNDARY_LEFT + seg * SHELF_SEGMENT_LENGTH + SHELF_BOUNDARY_RIGHT
            print(f"   ├─ {seg}格货架({length:.0f}m): {count} 个")
    
    # 最终验证：确保关键货架（4格和3格）都有放置
    critical_missing = []
    for seg in [4, 3]:
        if zone_stats.get(seg, 0) == 0:
            critical_missing.append(seg)
    
    if critical_missing:
        print(f"\n❌ 关键货架放置失败：{critical_missing}格货架数量为0！")
        print(f"   布局验证未通过，标记为失败")
        return grid.to_scene_dict("Scene_FourZoneRandom_Failed")
    
    # 6. 在货架上随机放置箱子
    print("\n📦 在货架上随机放置箱子...")
    shelf_positions = {}
    for obj in grid.objects:
        if obj['type'] == 'shelf':
            shelf_positions[obj['name']] = (obj['position']['x'], obj['position']['y'],
                                           obj.get('rotation', 0), obj['dimensions']['length'])
    
    all_shelf_names = list(shelf_positions.keys())
    if len(all_shelf_names) > 0:
        num_shelves_to_use = random.randint(max(1, len(all_shelf_names) // 3),
                                            max(1, len(all_shelf_names) * 2 // 3))
        selected_shelves = random.sample(all_shelf_names, min(num_shelves_to_use, len(all_shelf_names)))
        
        print(f"  随机选择了 {len(selected_shelves)}/{len(all_shelf_names)} 个货架")
        
        for shelf_name in selected_shelves:
            x, y, rotation, shelf_length = shelf_positions[shelf_name]
            available_levels = [1, 2, 3, 4]
            num_levels = random.randint(1, 2)
            selected_levels = random.sample(available_levels, num_levels)
            
            for level in sorted(selected_levels):
                max_stacks = random.randint(1, 2)
                placed = grid.place_mixed_boxes_on_shelf(shelf_name, x, y, level=level,
                                                         rotation=rotation, max_stacks=max_stacks,
                                                         spacing=random.uniform(0.03, 0.08),
                                                         shelf_length=shelf_length)
                if placed > 0:
                    print(f"  货架 {shelf_name} 第{level}层: {placed}个箱子")
    else:
        print("  ⚠️  没有货架可以放置箱子")
    
    # 7. 在中央十字通道放置搬运车
    grid.place_agent("agv", "AGV_1", split_x, split_y - 3, model="create3")
    grid.place_agent("agv", "AGV_2", split_x, split_y + 3, model="ridgeback")
    print("✓ 搬运车已放置在中央通道")
    
    return grid.to_scene_dict("Scene_FourZoneRandom")


def main():
    """主函数：生成所有场景"""
    output_dir = "warehouse_scenes"
    os.makedirs(output_dir, exist_ok=True)
    
    print("="*60)
    print("多格数货架仓库场景生成器")
    print("="*60)
    
    layouts = [
        ("adaptive_layout.json", generate_adaptive_layout),
        ("mixed_layout.json", generate_mixed_layout),
        ("dual_channel_adaptive.json", generate_dual_channel_adaptive),
        ("grid_mixed.json", generate_grid_mixed),
        ("greedy_column_layout.json", generate_greedy_column_layout),
        ("greedy_row_layout.json", generate_greedy_row_layout),
        ("four_zone_layout.json", generate_four_zone_layout),
        ("four_zone_random_layout.json", generate_four_zone_random_layout),
    ]
    
    for filename, layout_func in layouts:
        print(f"\n{'='*60}")
        print(f"生成: {filename}")
        print('='*60)
        
        # 对于随机布局，添加重试机制
        if "random" in filename:
            max_retries = 10
            for attempt in range(max_retries):
                scene = layout_func()
                
                # 检查是否是失败的场景
                if "Failed" not in scene.get("scene_name", ""):
                    # 成功生成
                    break
                else:
                    if attempt < max_retries - 1:
                        print(f"\n🔄 第 {attempt + 1} 次尝试失败，重新生成...")
                    else:
                        print(f"\n❌ 尝试 {max_retries} 次后仍然失败，使用最后一次结果")
        else:
            scene = layout_func()
        
        filepath = os.path.join(output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(scene, f, ensure_ascii=False, indent=2)
        print(f"✓ 已生成: {filepath}")
    
    print(f"\n{'='*60}")
    print(f"所有场景已生成到 {output_dir} 目录")
    print('='*60)


if __name__ == "__main__":
    main()
