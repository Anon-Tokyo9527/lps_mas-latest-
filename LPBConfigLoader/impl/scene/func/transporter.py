"""
兼容入口：`Transporter` 已迁移到 `func.new_transporter`。

如果你的旧脚本仍在使用 `from func.transporter import Transporter`，
这里会保持向后兼容，并实际返回新实现。
"""

from .new_transporter import Transporter

__all__ = ["Transporter"]
    