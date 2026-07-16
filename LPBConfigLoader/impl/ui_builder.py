"""
Omniverse UI Framework:
  https://docs.omniverse.nvidia.com/kit/docs/omni.ui/latest/Overview.html

Isaac Sim UI Utilities extension:
  https://docs.omniverse.nvidia.com/py/isaacsim/source/extensions/omni.isaac.ui/docs/index.html
"""

import os
import webbrowser

import omni.kit.ui
import omni.ui as ui
import omni.timeline
from isaacsim.core.prims import SingleXFormPrim
from isaacsim.gui.components import ui_utils 
from isaacsim.examples.extension.core_connectors import LoadButton, ResetButton
from isaacsim.gui.components.element_wrappers import StateButton
from isaacsim.core.utils.stage import get_current_stage
from isaacsim.core.api.world import World
from pxr import Sdf, UsdLux

from .scenario import TestEnv
from .scene.config_scene_loader import get_default_scene_config_path


FILE_PATH = os.path.abspath(__file__)

class UIBuilder:
    """Manage extension UI"""

    def __init__(self, window_title, menu_path=None):
        self._menu = None
        self._window = None

        self._menu_path = menu_path
        self._window_title = window_title

        self._timeline = omni.timeline.get_timeline_interface()
        self._scenario = TestEnv()
        self._config_path_model = ui.SimpleStringModel(get_default_scene_config_path())
        self._script_path_model = ui.SimpleStringModel(self._scenario.get_default_command_script_path())
        
        # create menu
        if self._menu_path:
            self._menu = omni.kit.ui.get_editor_menu().add_item(self._menu_path, self.on_toggle, toggle=True, value=False)

    def on_toggle(self, *args, **kwargs):
        """Toggle window visibility"""
        self.build_ui()
        if self._window is not None:
            self._window.visible = not self._window.visible

    def build_ui(self):
        """Build the Graphical User Interface (GUI) in the underlying windowing system"""
        if not self._window:
            self._window = ui.Window(title=self._window_title, visible=False)
            with self._window.frame:
                with ui.VStack(spacing=5, height=0):
                    header = ui_utils.setup_ui_headers(ext_id="Test", file_path=FILE_PATH , title="LLM Planing Benchmark")

                    with ui.CollapsableFrame("World Controls", height=0):
                        with ui.VStack(spacing=8):
                            with ui.HStack(spacing=8, height=0):
                                ui.Label("Config JSON", width=100)
                                ui.StringField(model=self._config_path_model)
                            self._load_btn = LoadButton(
                                label="Load Scene", 
                                text="LOAD", 
                                setup_scene_fn=self._pre_load, 
                                setup_post_load_fn=self._post_load
                                )
                            self._reset_btn = ResetButton(
                                label="Reset Scene", 
                                text="RESET", 
                                pre_reset_fn=self._reset
                                )
                            self._reset_btn.enabled = False
                    
                    with ui.CollapsableFrame("Task Controls", height=0):
                        with ui.VStack(spacing=8):
                            with ui.HStack(spacing=8, height=0):
                                ui.Label("Script JSON", width=100)
                                ui.StringField(model=self._script_path_model)
                            self._run_btn = StateButton(
                                "Run Scenario",
                                "RUN",
                                "STOP",                               
                                on_a_click_fn=self._start,
                                on_b_click_fn=self._stop,
                            )
                            self._run_btn.enabled = False
                    with ui.CollapsableFrame("Server Controls", height=0):
                        with ui.VStack(spacing=8):
                            self._server_btn = StateButton(
                                "API Server",
                                "START API",
                                "STOP API",                               
                                on_a_click_fn=self._on_start_server,
                                on_b_click_fn=self._on_stop_server,
                            )
                            self._server_btn.enabled = False
                            self._console_btn = ui.Button("OPEN CONSOLE", clicked_fn=self._open_console)
                            self._console_btn.enabled = False
                        

    def _add_light_to_stage(self):
        sphereLight = UsdLux.SphereLight.Define(get_current_stage(), Sdf.Path("/World/SphereLight"))
        sphereLight.CreateRadiusAttr(2)
        sphereLight.CreateIntensityAttr(100000)
        SingleXFormPrim(str(sphereLight.GetPath())).set_world_pose([6.5, 0, 12])
    
    def _pre_load(self):
        try:
            if self._scenario.is_api_server_running():
                self._scenario.stop_api_server()
        except Exception:
            pass

        world = World.instance()
        if world is None:
            world = World()
        else:
            world.clear()

        self._add_light_to_stage()
        config_path = self._config_path_model.get_value_as_string().strip()
        self._scenario.load_assets(config_path=config_path)
        

    def _post_load(self):
        self._scenario.setup()

        self._reset_btn.enabled = True
        self._run_btn.reset()
        self._run_btn.enabled = True
        self._server_btn.reset()
        self._server_btn.enabled = True # 确保 Load 之后可以点开服务器
        self._console_btn.enabled = True

    def _reset(self):
        self._timeline.pause()
        self._scenario.reset_scene()
        self._run_btn.reset()
        self._run_btn.enabled = False
        self._reset_btn.enabled = False
        self._console_btn.enabled = self._scenario.is_api_server_running()

    
    def _start(self):
        self._scenario.ensure_runtime_pump()
        script_path = self._script_path_model.get_value_as_string().strip()
        if script_path:
            try:
                result = self._scenario.start_command_script(script_path=script_path)
                print(f"[UI] Command script: {result}")
            except Exception as exc:
                print(f"[UI] Failed to start command script: {exc}")
                return
        self._timeline.play()


    def _stop(self):
        self._scenario.stop_command_script()
        self._timeline.pause()


    def _run(self, step):
        # Kept for compatibility if a future UI element registers it. Runtime
        # execution is now driven by TestEnv.ensure_runtime_pump().
        done = self._scenario.run()
        if done:
            self._reset()

    def _on_start_server(self):
        result = self._scenario.start_api_server()
        print(f"[UI] API Server: {result}")
        print(f"[UI] API Console: {self._scenario.get_api_console_url()}")

    def _on_stop_server(self):
        result = self._scenario.stop_api_server()
        print(f"[UI] API Server: {result}")

    def _open_console(self):
        if not self._scenario.is_api_server_running():
            self._on_start_server()
        url = self._scenario.get_api_console_url()
        print(f"[UI] Opening API Console: {url}")
        webbrowser.open(url)

    def cleanup(self):
        """Clean up window and menu"""
        try:
            self._scenario.stop_api_server()
        except Exception:
            pass
        try:
            self._scenario.stop_runtime_pump()
        except Exception:
            pass
        # destroy window
        if self._window is not None:
            self._window.destroy()
            self._window = None
        # destroy menu
        if self._menu is not None:
            try:
                omni.kit.ui.get_editor_menu().remove_item(self._menu)
            except:
                omni.kit.ui.get_editor_menu().remove_item(self._menu_path)
            self._menu = None
