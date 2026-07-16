import carb
import omni.ext
import omni.kit.app
import os
import sys
from .ui_builder import UIBuilder


class Extension(omni.ext.IExt):
    """The Extension class"""

    def on_startup(self, ext_id):
        """Method called when the extension is loaded/enabled"""
        carb.log_info(f"on_startup {ext_id}")
        ext_path = omni.kit.app.get_app().get_extension_manager().get_extension_path(ext_id)
        # 2. 计算 a2a 源码所在的目录 (即 a2a 文件夹的父目录)
        # 路径：LPB/impl/a2a-samples-main/samples/python/
        a2a_repo_path = os.path.join(ext_path, "impl", "a2a-samples-main", "samples", "python")
        
        # 3. 将该路径加入 sys.path
        if a2a_repo_path not in sys.path:
            sys.path.append(a2a_repo_path)
            carb.log_info(f"Added A2A path to sys.path: {a2a_repo_path}")

        # UI handler
        self.ui_builder = UIBuilder(window_title="LPB Config Loader", menu_path="Window/LPB Config Loader")

    def on_shutdown(self):
        """Method called when the extension is disabled"""
        carb.log_info(f"on_shutdown")

        # clean up UI
        self.ui_builder.cleanup()
