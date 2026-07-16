"""
func 包：集中放置场景对象的 Python 封装类。

注意：这些类依赖 Isaac Sim 的 Python 环境（isaacsim / omni / pxr）。
在普通系统 Python 里导入可能会失败，所以这里用 try/except 做“可选导出”。
"""

__all__ = [
    "Shelf",
    "Manipulator",
    "Transporter",
    "RidgebackBase",
    "Shuttle",
    "StaticAsset",
]

try:
    from .shelf import Shelf
    from .manipulator import Manipulator
    from .new_transporter import Transporter
    from .ridgeback_base import RidgebackBase
    from .shuttle import Shuttle
    from .static_asset import StaticAsset
except Exception:
    # 允许在非 Isaac Sim 环境中 import func 而不直接报错
    pass


