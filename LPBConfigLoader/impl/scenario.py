import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import omni.physx as _physx
import omni.timeline
import omni.usd
from isaacsim.core.api.world import World
from isaacsim.core.prims import SingleXFormPrim
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.utils.stage import add_reference_to_stage
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from .manipulator import Manipulator
from .shuttle import Shuttle
from .transporter import Transporter
from .scene.config_scene_loader import get_default_scene_config_path, load_config_scene

# 渲染、脚本、输入格式
class TestEnv:
    def __init__(self):
        self.current_command = None
        self.current_plan = None
        self.is_reasoning = False
        self.task_done = False
        self.loaded_config_path = None
        self.scene_data = {}
        self.scene_index = {"shelves": [], "conveyors": {}, "pallets": [], "transport_targets": []}
        self.candidate_pool = []
        self.api_tasks = {}
        self.api_queue = []
        self.active_api_task_id = None
        self._task_lock = threading.RLock()
        self._api_server = None
        self._api_server_thread = None
        self._api_server_host = None
        self._api_server_port = None
        self._timeline = omni.timeline.get_timeline_interface()
        self._physx_iface = None
        self._runtime_pump_subscription = None
        self._runtime_pump_in_tick = False
        self.logs = []
        self._log_seq = 0
        self._log_file_path = None
        self.command_script = None
        self.command_script_path = None
        self.command_script_steps = []
        self.command_script_running = False
        self.command_script_status = "idle"
        self.command_script_started_at = None
        self.command_script_next_at = None
        self.command_script_index = 0
        self.command_script_error = None
        self.command_script_submitted_tasks = []
        self._clear_runtime_objects()

    def add_log(self, level, message, data=None):
        with self._task_lock:
            self._log_seq += 1
            entry = {
                "seq": self._log_seq,
                "time": time.time(),
                "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "level": str(level).upper(),
                "message": str(message),
                "data": self._json_safe(data),
            }
            self.logs.append(entry)
            if len(self.logs) > 5000:
                del self.logs[: len(self.logs) - 5000]
            self._append_log_file_locked(entry)

        if data is None:
            print(f"[LPB {entry['level']}] {entry['message']}")
        else:
            print(f"[LPB {entry['level']}] {entry['message']}: {data}")
        return dict(entry)

    def get_logs(self, since=None, limit=200, level=None):
        try:
            since = int(since) if since is not None else None
        except Exception:
            since = None
        try:
            limit = max(1, min(int(limit), 1000))
        except Exception:
            limit = 200

        level = str(level).upper() if level else None
        with self._task_lock:
            entries = list(self.logs)

        if since is not None:
            entries = [entry for entry in entries if int(entry.get("seq", 0)) > since]
        if level:
            entries = [entry for entry in entries if entry.get("level") == level]
        return {"entries": entries[-limit:], "count": len(entries), "file_path": self._log_file_path}

    def clear_logs(self):
        with self._task_lock:
            self.logs = []
            self._log_seq = 0
            self._rotate_log_file_locked()
        return {"ok": True}

    def _append_log_file_locked(self, entry):
        try:
            if self._log_file_path is None:
                self._rotate_log_file_locked()
            with open(self._log_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _rotate_log_file_locked(self):
        try:
            log_dir = Path(__file__).resolve().parents[2] / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            self._log_file_path = str((log_dir / f"api_debug_{stamp}.jsonl").resolve())
        except Exception:
            self._log_file_path = None

    def _json_safe(self, value, depth=0):
        if depth > 6:
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, "tolist"):
            try:
                return value.tolist()
            except Exception:
                return str(value)
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return str(value)
        if isinstance(value, dict):
            return {str(k): self._json_safe(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(v, depth + 1) for v in value]
        return str(value)

    def get_runtime_status(self):
        return {
            "pump_running": self._runtime_pump_subscription is not None,
            "timeline_playing": self._is_timeline_playing(),
        }

    def _clear_runtime_objects(self):
        self.shuttles = []
        self.transporters = []
        self.manipulators = []
        self.shuttle = None
        self.transporter = None
        self.manipulator = None
        self._held_packages = {}
        self._transporter_loads = {}
        self._transporter_load_records = {}
        self._shuttle_pick_queues = {}
        self._conveyor_flows = {}
        self._package_scales = {}
        self._shuttle_roof_positions = {}
        self._runtime_last_update = None

    def start_api_server(self, host="0.0.0.0", port=60124):
        from .api_server import create_server

        with self._task_lock:
            if self._api_server_thread is not None and self._api_server_thread.is_alive():
                url = self._api_url()
                self.ensure_runtime_pump()
                self.add_log("INFO", "API server is already running", {"url": url})
                return {"running": True, "url": url}

            server = create_server(self, host=host, port=port)
            thread = threading.Thread(
                target=server.serve_forever,
                name="LPBConfigApiServer",
                daemon=True,
            )
            self._api_server = server
            self._api_server_thread = thread
            self._api_server_host = host
            self._api_server_port = int(port)
            thread.start()
            self.ensure_runtime_pump()

        url = self._api_url()
        self.add_log("INFO", "API server started", {"url": url, "console_url": self.get_api_console_url()})
        return {"running": True, "url": url}

    def stop_api_server(self):
        with self._task_lock:
            server = self._api_server
            thread = self._api_server_thread

        if server is None:
            self.add_log("INFO", "API server is not running")
            return {"running": False}

        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)

        with self._task_lock:
            self._api_server = None
            self._api_server_thread = None
            self._api_server_host = None
            self._api_server_port = None

        self.stop_runtime_pump()
        self.add_log("INFO", "API server stopped")
        return {"running": False}

    def is_api_server_running(self):
        return self._api_server_thread is not None and self._api_server_thread.is_alive()

    def _api_url(self):
        host = self._api_server_host or "localhost"
        if host in ("0.0.0.0", "::"):
            host = "localhost"
        port = self._api_server_port or 60124
        return f"http://{host}:{port}"

    def get_api_console_url(self):
        return self._api_url().rstrip("/") + "/console"

    def ensure_runtime_pump(self):
        with self._task_lock:
            if self._runtime_pump_subscription is not None:
                return {"running": True}
            if self._physx_iface is None:
                self._physx_iface = _physx.get_physx_interface()
            self._runtime_pump_subscription = self._physx_iface.subscribe_physics_step_events(self._runtime_pump_tick)
        self.add_log("INFO", "Runtime pump registered")
        return {"running": True}

    def stop_runtime_pump(self):
        with self._task_lock:
            self._runtime_pump_subscription = None
        self.add_log("INFO", "Runtime pump stopped")
        return {"running": False}

    def _runtime_pump_tick(self, step):
        with self._task_lock:
            if self._runtime_pump_in_tick:
                return
            self._runtime_pump_in_tick = True
        try:
            self.run()
        except Exception as exc:
            self.add_log("ERROR", "Runtime pump tick failed", {"error": str(exc)})
        finally:
            with self._task_lock:
                self._runtime_pump_in_tick = False

    def _play_timeline_for_api_task(self):
        try:
            self.ensure_runtime_pump()
            if self._timeline is not None:
                self._timeline.play()
        except Exception as exc:
            self.add_log("WARN", "Could not auto-play timeline for API task", {"error": str(exc)})

    def get_default_command_script_path(self):
        return str((Path(__file__).resolve().parents[2] / "config" / "default_demo_script.json").resolve())

    def get_command_script_status(self):
        with self._task_lock:
            return {
                "path": self.command_script_path,
                "running": self.command_script_running,
                "status": self.command_script_status,
                "current_step": self.command_script_index,
                "total_steps": len(self.command_script_steps),
                "next_at": self.command_script_next_at,
                "error": self.command_script_error,
                "submitted_task_ids": list(self.command_script_submitted_tasks),
            }

    def start_command_script(self, script_path=None, script=None):
        if script is None:
            path = Path(str(script_path or self.get_default_command_script_path()).strip().strip('"')).expanduser()
            if not path.is_absolute():
                path = (Path(__file__).resolve().parents[2] / path).resolve()
            with open(path, "r", encoding="utf-8") as f:
                script = json.load(f)
            resolved_path = str(path.resolve())
        else:
            resolved_path = str(script_path or "<inline>")

        steps = script.get("steps", [])
        if not isinstance(steps, list) or not steps:
            raise ValueError("Command script must contain a non-empty 'steps' list.")

        default_delay = float(script.get("default_delay", script.get("default_delay_seconds", 0.0)))
        normalised_steps = []
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                raise ValueError(f"Script step {index} must be an object.")
            item = dict(step)
            item["_delay"] = float(item.get("delay", item.get("delay_seconds", default_delay)))
            normalised_steps.append(item)

        now = time.monotonic()
        with self._task_lock:
            self.command_script = dict(script)
            self.command_script_path = resolved_path
            self.command_script_steps = normalised_steps
            self.command_script_running = True
            self.command_script_status = "running"
            self.command_script_started_at = now
            self.command_script_next_at = now + normalised_steps[0]["_delay"]
            self.command_script_index = 0
            self.command_script_error = None
            self.command_script_submitted_tasks = []

        print(f"[LPB Script] Started command script: {resolved_path}")
        self.add_log("INFO", "Command script started", {"path": resolved_path, "steps": len(normalised_steps)})
        self._play_timeline_for_api_task()
        return self.get_command_script_status()

    def stop_command_script(self):
        with self._task_lock:
            self.command_script_running = False
            if self.command_script_status == "running":
                self.command_script_status = "stopped"
            status = self.get_command_script_status()
        self.add_log("INFO", "Command script stopped")
        return status

    def reset_scene(self, clear_tasks=True, clear_script=True):
        if clear_script:
            with self._task_lock:
                self.command_script_running = False
                self.command_script_status = "idle"
                self.command_script_error = None
                self.command_script_index = 0
                self.command_script_next_at = None
                self.command_script_submitted_tasks = []

        with self._task_lock:
            self.candidate_pool = []
            if clear_tasks:
                self.api_tasks = {}
                self.api_queue = []
                self.active_api_task_id = None
            else:
                self.api_queue = []
            self.loaded_config_path = None
            self.scene_data = {}
            self.scene_index = {"shelves": [], "conveyors": {}, "pallets": [], "transport_targets": []}

        self._clear_runtime_objects()

        world = World.instance()
        if world is None:
            world = World()

        try:
            world.clear()
        except Exception as exc:
            print(f"[LPB] world.clear() failed during reset_scene: {exc}")
            stage = omni.usd.get_context().get_stage()
            if stage:
                world_prim = stage.GetPrimAtPath("/World")
                if world_prim and world_prim.IsValid():
                    for child in list(world_prim.GetChildren()):
                        if child.GetName() != "PhysicsScene":
                            stage.RemovePrim(child.GetPath())

        self.add_log("INFO", "Scene cleared")
        return {"ok": True, "message": "Scene cleared."}

    def enqueue_reset_scene(self):
        return self._enqueue_task(
            kind="reset_scene",
            steps=[{"op": "reset_scene"}],
            metadata={"clear_scene": True},
        )

    # ========== A2A 相关代码已注释 ==========
    # def start_a2a_server(self):
    #     import uvicorn
    #     from a2a.server.apps import A2AStarletteApplication
    #     from a2a.server.request_handlers import DefaultRequestHandler
    #     from a2a.server.tasks import InMemoryTaskStore
    #     from a2a.types import AgentCapabilities, AgentCard, AgentSkill
    #
    #     skill = AgentSkill(
    #         id="hello_world",
    #         name="Returns hello world",
    #         description="just returns hello world",
    #         tags=["hello world"],
    #         examples=["hi"],
    #     )
    #
    #     public_agent_card = AgentCard(
    #         name="Isaac Sim Agent",
    #         description="A2A Agent running inside Isaac Sim",
    #         url="http://localhost:9999/",
    #         version="1.0.0",
    #         default_input_modes=["text"],
    #         default_output_modes=["text"],
    #         capabilities=AgentCapabilities(streaming=True),
    #         skills=[skill],
    #     )
    #
    #     request_handler = DefaultRequestHandler(
    #         agent_executor=self.a2a_executor,
    #         task_store=InMemoryTaskStore(),
    #     )
    #     server_app = A2AStarletteApplication(
    #         agent_card=public_agent_card,
    #         http_handler=request_handler,
    #     )
    #
    #     print("[Sim Server] Starting Uvicorn on port 9999...")
    #     self._server_running = True
    #     uvicorn.run(server_app.build(), host="0.0.0.0", port=9999, log_level="info")

    def load_assets(self, config_path=None):
        self._clear_runtime_objects()
        self.candidate_pool = []
        self.api_tasks = {}
        self.api_queue = []
        self.active_api_task_id = None
        self.stop_command_script()

        result = load_config_scene(config_path or get_default_scene_config_path())
        self.loaded_config_path = result["config_path"]
        self.scene_data = result.get("scene_data", {})
        self.scene_index = self._build_scene_index(self.scene_data)
        self._create_dynamic_agents(result.get("dynamic_agents", {}))

        self._world = World.instance()
        if self._world is None:
            self._world = World()

        print(f"[LPB] Loaded scene config: {self.loaded_config_path}")
        print(f"[LPB] Static object count: {result.get('object_count', 0)}")
        return result

    def _build_scene_index(self, scene_data):
        index = {"shelves": [], "conveyors": {}, "pallets": [], "slots": {}, "transport_targets": []}
        for obj in scene_data.get("objects", []):
            obj_type = obj.get("type")
            name = obj.get("name", "")
            if obj_type == "shelf":
                index["shelves"].append(obj)
            elif obj_type == "conveyor":
                base = self._base_conveyor_name(name)
                index["conveyors"].setdefault(base, []).append(obj)
                if name != base:
                    index["conveyors"].setdefault(name, []).append(obj)
            elif obj_type == "crate":
                index["pallets"].append(obj)
            elif obj_type == "slot":
                index["slots"][name] = obj
            elif obj_type == "pallet_transport_target":
                index["transport_targets"].append(obj)

        for segments in index["conveyors"].values():
            segments.sort(key=lambda item: self._numeric_suffix(item.get("name", "")))
        return index

    def _create_dynamic_agents(self, dynamic_agents):
        for spec in dynamic_agents.get("shuttles", []):
            shuttle = Shuttle(
                name=spec["name"],
                position=list(spec.get("position", [0.0, 0.0, 0.0])),
                orientation=spec.get("orientation"),
            )
            self.shuttles.append(shuttle)

        for spec in dynamic_agents.get("transporters", []):
            transporter = Transporter(
                name=spec["name"],
                position=list(spec.get("position", [0.0, 0.0, 0.0])),
                orientation=spec.get("orientation"),
            )
            self.transporters.append(transporter)

        for spec in dynamic_agents.get("manipulators", []):
            manipulator = Manipulator(
                name=spec["name"],
                position=list(spec.get("position", [0.0, 0.0, 0.0])),
                orientation=spec.get("orientation"),
            )
            self.manipulators.append(manipulator)

        self.shuttle = self.shuttles[0] if self.shuttles else None
        self.transporter = self.transporters[0] if self.transporters else None
        self.manipulator = self.manipulators[0] if self.manipulators else None

    def setup(self):
        for shuttle in self.shuttles:
            try:
                shuttle.initialize()
            except Exception as exc:
                print(f"[LPB] Shuttle initialize skipped/failed: {exc}")

        for transporter in self.transporters:
            try:
                transporter.initialize()
            except Exception as exc:
                print(f"[LPB] Transporter initialize skipped/failed: {exc}")

        # ========== A2A 相关代码已注释 ==========
        # from .a2a_local.agent_executor import HelloWorldAgentExecutor
        #
        # self.a2a_executor = HelloWorldAgentExecutor(self)

        api_key = os.getenv("LPB_GEMINI_API_KEY", "")
        if self.shuttle is not None and api_key:
            self.shuttle.init_llm(
                provider=os.getenv("LPB_LLM_PROVIDER", "gemini"),
                model_name=os.getenv("LPB_LLM_MODEL", "gemini-3-flash-preview"),
                api_key=api_key,
            )

    def reset(self):
        for obj in [*self.manipulators, *self.shuttles, *self.transporters]:
            try:
                obj.reset()
            except Exception as exc:
                print(f"[LPB] Reset skipped for {obj}: {exc}")

    def run(self):
        self._update_visual_runtime()
        self._run_command_script_tick()

        if self._run_api_task_tick():
            return False

        if not self.current_plan or self.shuttle is None:
            return False

        print(self.current_plan)
        is_done = self.shuttle.step(self.current_plan)
        if is_done:
            print("[Scenario] Local Task Finished!")
            self.current_plan = None
            return True
        return False

    def get_api_state(self):
        with self._task_lock:
            return {
                "loaded_config_path": self.loaded_config_path,
                "shelves": len(self.scene_index.get("shelves", [])),
                "conveyors": sorted(self.scene_index.get("conveyors", {}).keys()),
                "pallets": [p.get("name") for p in self.scene_index.get("pallets", [])],
                "transport_targets": [t.get("name") for t in self.scene_index.get("transport_targets", [])],
                "candidate_pool": self.get_candidate_pool(),
                "active_task_id": self.active_api_task_id,
                "queued_task_ids": list(self.api_queue),
                "task_summary": self._task_queue_summary_locked(),
                "agent_positions": self._agent_positions_snapshot(),
                "held_packages": self._held_packages_snapshot(),
                "conveyor_flows": self._conveyor_flows_snapshot(),
                "api_server": {
                    "running": self.is_api_server_running(),
                    "url": self._api_url() if self.is_api_server_running() else None,
                    "console_url": self.get_api_console_url() if self.is_api_server_running() else None,
                },
                "runtime": self.get_runtime_status(),
                "command_script": self.get_command_script_status(),
            }

    def get_debug_snapshot(self, include_world=True, include_tasks=True):
        with self._task_lock:
            snapshot = {
                "time": time.time(),
                "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "loaded_config_path": self.loaded_config_path,
                "runtime": self.get_runtime_status(),
                "api_server": {
                    "running": self.is_api_server_running(),
                    "url": self._api_url() if self.is_api_server_running() else None,
                },
                "tasks": self._task_queue_summary_locked(),
                "command_script": self.get_command_script_status(),
                "scene": self._scene_index_snapshot(),
                "agents": self._agent_positions_snapshot(),
                "candidate_pool": [dict(item) for item in self.candidate_pool],
                "held_packages": self._held_packages_snapshot(),
                "shuttle_roof_queues": {name: list(values) for name, values in self._shuttle_pick_queues.items()},
                "transporter_loads": self._transporter_loads_snapshot(),
                "conveyor_flows": self._conveyor_flows_snapshot(),
                "log": {
                    "next_seq": self._log_seq + 1,
                    "memory_count": len(self.logs),
                    "file_path": self._log_file_path,
                },
            }
            if include_tasks:
                snapshot["task_details"] = {
                    task_id: self._public_task_snapshot(task)
                    for task_id, task in self.api_tasks.items()
                }
            if include_world:
                snapshot["world_objects"] = self._world_objects_snapshot()
            return self._json_safe(snapshot)

    def _task_queue_summary_locked(self):
        active = self.api_tasks.get(self.active_api_task_id) if self.active_api_task_id else None
        return {
            "active_task_id": self.active_api_task_id,
            "active": self._public_task_snapshot(active) if active else None,
            "queued_task_ids": list(self.api_queue),
            "queued_count": len(self.api_queue),
            "status_counts": self._task_status_counts_locked(),
        }

    def _task_status_counts_locked(self):
        counts = {}
        for task in self.api_tasks.values():
            status = task.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _public_task_snapshot(self, task):
        if not task:
            return None
        current_step = int(task.get("current_step", 0))
        steps = task.get("steps", [])
        current = steps[current_step] if current_step < len(steps) else None
        return {
            "task_id": task.get("task_id"),
            "kind": task.get("kind"),
            "status": task.get("status"),
            "current_step": current_step,
            "total_steps": len(steps),
            "current_op": current.get("op") if isinstance(current, dict) else None,
            "metadata": self._json_safe(task.get("metadata", {})),
            "error": task.get("error"),
        }

    def _scene_index_snapshot(self):
        shelves = []
        for idx, shelf in enumerate(self.scene_index.get("shelves", []), 1):
            packages = [pkg for pkg in shelf.get("children", []) if pkg.get("type") == "box"]
            shelves.append(
                {
                    "index": idx,
                    "name": shelf.get("name"),
                    "position": self._pos_from_obj(shelf),
                    "dimensions": shelf.get("dimensions"),
                    "package_count": len(packages),
                    "package_names": [pkg.get("name") for pkg in packages],
                }
            )

        conveyors = {}
        for name, segments in self.scene_index.get("conveyors", {}).items():
            conveyors[name] = [
                {
                    "name": segment.get("name"),
                    "position": self._pos_from_obj(segment),
                    "dimensions": segment.get("dimensions"),
                    "rotation": segment.get("rotation"),
                }
                for segment in segments
            ]

        pallets = [
            {
                "index": idx,
                "name": pallet.get("name"),
                "position": self._pos_from_obj(pallet),
                "dimensions": pallet.get("dimensions"),
                "child_count": len(pallet.get("children", []) or []),
            }
            for idx, pallet in enumerate(self.scene_index.get("pallets", []), 1)
        ]

        slots = {
            name: {
                "name": slot.get("name"),
                "position": self._pos_from_obj(slot),
                "dimensions": slot.get("dimensions"),
            }
            for name, slot in self.scene_index.get("slots", {}).items()
        }

        transport_targets = [
            {
                "index": idx,
                "name": target.get("name"),
                "position": self._pos_from_obj(target),
                "dimensions": target.get("dimensions"),
                "rotation": target.get("rotation", 0.0),
            }
            for idx, target in enumerate(self.scene_index.get("transport_targets", []), 1)
        ]

        return {
            "shelves": shelves,
            "conveyors": conveyors,
            "pallets": pallets,
            "slots": slots,
            "transport_targets": transport_targets,
        }

    def _agent_positions_snapshot(self):
        return {
            "shuttles": [self._agent_snapshot(agent) for agent in self.shuttles],
            "transporters": [self._agent_snapshot(agent) for agent in self.transporters],
            "manipulators": [self._agent_snapshot(agent) for agent in self.manipulators],
        }

    def _agent_snapshot(self, agent):
        name = self._agent_name(agent)
        snapshot = {"name": name, "position": self._agent_position(agent)}
        try:
            _, orientation = agent.get_world_pose()
            snapshot["orientation"] = self._json_safe(orientation)
        except Exception:
            snapshot["orientation"] = None
        return snapshot

    def _held_packages_snapshot(self):
        result = {}
        for package_name, held in self._held_packages.items():
            result[package_name] = {
                "agent_name": held.get("agent_name"),
                "hold_height": held.get("hold_height"),
                "dimensions": held.get("dimensions"),
                "fixed_position": held.get("fixed_position"),
                "position": self._package_world_position(package_name),
            }
        return result

    def _transporter_loads_snapshot(self):
        return {
            "loads": dict(self._transporter_loads),
            "records": {
                name: {
                    "pallet_name": record.get("pallet_name"),
                    "packages": list(record.get("packages") or []),
                    "package_offsets": dict(record.get("package_offsets") or {}),
                }
                for name, record in self._transporter_load_records.items()
            },
        }

    def _conveyor_flows_snapshot(self):
        result = {}
        for package_name, flow in self._conveyor_flows.items():
            result[package_name] = {
                "conveyor_name": flow.get("conveyor_name"),
                "status": flow.get("status"),
                "start": flow.get("start"),
                "current": self._package_world_position(package_name) or flow.get("current"),
                "end": flow.get("end"),
                "slot_index": flow.get("slot_index"),
                "speed": flow.get("speed"),
            }
        return result

    def _world_objects_snapshot(self, max_objects=300):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return {"available": False, "objects": []}

        objects = []
        try:
            root = stage.GetPrimAtPath("/World")
            if not root or not root.IsValid():
                return {"available": True, "objects": []}
            for prim in Usd.PrimRange(root):
                if len(objects) >= int(max_objects):
                    break
                path = str(prim.GetPath())
                if path == "/World":
                    continue
                name = prim.GetName()
                if not self._is_debug_relevant_prim(name, path):
                    continue
                objects.append(
                    {
                        "name": name,
                        "path": path,
                        "type": prim.GetTypeName(),
                        "position": self._prim_world_position(prim),
                        "valid": prim.IsValid(),
                    }
                )
        except Exception as exc:
            return {"available": False, "error": str(exc), "objects": objects}
        return {"available": True, "count": len(objects), "objects": objects}

    def _is_debug_relevant_prim(self, name, path):
        prefixes = ("Shelf", "Box", "ShelfPkg", "ConvPkg", "ConsoleBox", "InboundBox", "Pallet", "DemoConveyor")
        if name.startswith(prefixes):
            return True
        lowered = path.lower()
        return any(token in lowered for token in ("shuttle", "transporter", "pallet_arm", "conveyor", "slot"))

    def _prim_world_position(self, prim):
        try:
            xform = UsdGeom.Xformable(prim)
            matrix = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            translation = matrix.ExtractTranslation()
            return [float(translation[0]), float(translation[1]), float(translation[2])]
        except Exception:
            return None

    def _is_timeline_playing(self):
        try:
            return bool(self._timeline.is_playing())
        except Exception:
            return None

    def get_api_task(self, task_id):
        with self._task_lock:
            task = self.api_tasks.get(task_id)
            return dict(task) if task else None

    def get_candidate_pool(self):
        with self._task_lock:
            return [dict(item) for item in self.candidate_pool]

    def get_shelf_packages(self, shelf_index):
        with self._task_lock:
            shelf = self._get_shelf(shelf_index)
            return {
                "shelf_index": int(shelf_index),
                "shelf_name": shelf.get("name"),
                "packages": [dict(pkg) for pkg in shelf.get("children", []) if pkg.get("type") == "box"],
            }

    def enqueue_arm_pick_place(
        self,
        package_name=None,
        candidate_index=None,
        target=None,
        arm_name=None,
        pallet_name=None,
        pallet_index=None,
        pallet_offset=None,
    ):
        if package_name is None and candidate_index is None:
            raise ValueError("package_name or candidate_index is required.")
        if target is None and pallet_name is None and pallet_index is None:
            raise ValueError("target or pallet_name/pallet_index with pallet_offset is required.")

        package = package_name
        requested_target = target
        manipulator = self._get_manipulator(arm_name)
        if package is not None and target is None:
            target = self._resolve_pallet_relative_target(
                package_name=package,
                pallet_name=pallet_name,
                pallet_index=pallet_index,
                pallet_offset=pallet_offset,
            )
        resolved_target = self._resolve_manipulator_place_target(package, target) if package is not None else None
        self.add_log(
            "DEBUG",
            "Arm pick-place enqueue resolved",
            {
                "request": {
                    "package_name": package_name,
                    "candidate_index": candidate_index,
                    "target": target,
                    "arm_name": arm_name,
                    "pallet_name": pallet_name,
                    "pallet_index": pallet_index,
                    "pallet_offset": pallet_offset,
                },
                "resolved": {
                    "package_name": package,
                    "candidate_index": candidate_index,
                    "arm_name": manipulator.name if hasattr(manipulator, "name") else arm_name,
                    "target": resolved_target if resolved_target is not None else "deferred_until_candidate_ready",
                    "candidate_pool": self.get_candidate_pool(),
                },
            },
        )
        task = self._enqueue_task(
            kind="arm_pick_place",
            steps=[
                {
                    "op": "manipulator_pick_place",
                    "agent_name": manipulator.name if hasattr(manipulator, "name") else arm_name,
                    "package_name": package,
                    "candidate_index": candidate_index,
                    "target": resolved_target,
                    "requested_target": requested_target,
                    "pallet_name": pallet_name,
                    "pallet_index": pallet_index,
                    "pallet_offset": pallet_offset,
                }
            ],
            metadata={
                "package_name": package,
                "candidate_index": candidate_index,
                "target": resolved_target if resolved_target is not None else requested_target,
                "pallet_name": pallet_name,
                "pallet_index": pallet_index,
                "pallet_offset": pallet_offset,
            },
        )
        if package is not None:
            self._mark_candidate(package, "reserved")
        return task

    def enqueue_conveyor_packages(
        self,
        conveyor_name,
        count=1,
        package_prefix=None,
        dimensions=None,
        spacing=0.45,
        at="end",
        interval_seconds=0.35,
    ):
        if not conveyor_name:
            raise ValueError("conveyor_name is required.")
        count = int(count)
        if count <= 0:
            raise ValueError("count must be positive.")
        package_prefix = package_prefix or f"ConvPkg_{conveyor_name}"
        dimensions = self._vec3(dimensions or [0.3, 0.3, 0.5])
        self.add_log(
            "DEBUG",
            "Conveyor package enqueue resolved",
            {
                "conveyor_name": conveyor_name,
                "count": count,
                "package_prefix": package_prefix,
                "dimensions": dimensions,
                "spacing": float(spacing),
                "at": at,
                "interval_seconds": float(interval_seconds),
                "start": self._conveyor_endpoint(conveyor_name, at="start"),
                "end": self._conveyor_endpoint(conveyor_name, at="end"),
            },
        )

        steps = []
        for index in range(count):
            steps.append(
                {
                    "op": "spawn_conveyor_package",
                    "conveyor_name": conveyor_name,
                    "package_base_name": f"{package_prefix}_{index + 1}",
                    "dimensions": dimensions,
                    "spacing": float(spacing),
                    "at": at,
                    "sequence_index": index,
                    "delay_seconds": 0.0 if index == 0 else float(interval_seconds),
                }
            )

        task = self._enqueue_task(
            kind="conveyor_packages",
            steps=steps,
            metadata={"conveyor_name": conveyor_name, "count": count},
        )
        return task

    def enqueue_shuttle_pick_to_conveyor(
        self,
        shelf_index,
        package_names=None,
        package_positions=None,
        conveyor_name=None,
        shuttle_name=None,
        drop_spacing=0.45,
    ):
        if shelf_index is None:
            raise ValueError("shelf_index is required.")
        if not conveyor_name:
            raise ValueError("conveyor_name is required.")

        shelf = self._get_shelf(shelf_index)
        packages = self._resolve_shelf_packages(shelf, package_names, package_positions)
        shuttle = self._get_shuttle(shuttle_name)
        conveyor_pos = self._conveyor_endpoint(conveyor_name, at="start")
        steps = []
        self.add_log(
            "DEBUG",
            "Shuttle pick enqueue resolved",
            {
                "request": {
                    "shelf_index": shelf_index,
                    "package_names": package_names,
                    "package_positions": package_positions,
                    "conveyor_name": conveyor_name,
                    "shuttle_name": shuttle_name,
                },
                "resolved": {
                    "shelf_name": shelf.get("name"),
                    "packages": [pkg.get("name") for pkg in packages],
                    "shuttle_name": shuttle.name,
                    "conveyor_start": conveyor_pos,
                },
            },
        )

        for idx, package in enumerate(packages):
            package_name = package["name"]
            package_pos = self._pos_from_obj(package)
            approach_pos = self._shelf_package_approach_position(shelf, package_pos)
            steps.extend(
                [
                    {"op": "shuttle_move_to", "agent_name": shuttle.name, "target": approach_pos},
                    {
                        "op": "shuttle_pick",
                        "agent_name": shuttle.name,
                        "package_name": package_name,
                        "package_position": package_pos,
                    },
                ]
            )

        steps.append({"op": "shuttle_move_to", "agent_name": shuttle.name, "target": [conveyor_pos[0], conveyor_pos[1], 0.0]})
        conveyor_surface_z = self._conveyor_surface_z(conveyor_name, at="start")
        for idx, package in enumerate(packages):
            package_name = package["name"]
            drop_pos = [
                conveyor_pos[0] - idx * float(drop_spacing),
                conveyor_pos[1],
                self._conveyor_package_rest_z(
                    conveyor_name,
                    at="start",
                    package_name=package_name,
                    dimensions=package.get("dimensions"),
                ),
            ]
            steps.append(
                {
                    "op": "shuttle_place_abs",
                    "agent_name": shuttle.name,
                    "package_name": package_name,
                    "target": drop_pos,
                    "conveyor_name": conveyor_name,
                    "delay_seconds": idx * 0.5,
                }
            )

        task = self._enqueue_task(
            kind="shuttle_pick_to_conveyor",
            steps=steps,
            metadata={
                "shelf_index": int(shelf_index),
                "shelf_name": shelf.get("name"),
                "package_names": [pkg["name"] for pkg in packages],
                "conveyor_name": conveyor_name,
            },
        )
        return task

    def enqueue_transporter_move_pallet(
        self,
        pallet_name=None,
        pallet_index=None,
        target=None,
        transporter_name=None,
        target_name=None,
        target_index=None,
    ):
        target_obj = None
        if target is None and (target_name or target_index):
            target_obj = self._get_transport_target(target_name=target_name, target_index=target_index)
            target = self._pos_from_obj(target_obj)
        if target is None:
            raise ValueError("target is required, e.g. [x, y] / [x, y, z], or use target_name / target_index.")
        pallet = self._get_pallet(pallet_name=pallet_name, pallet_index=pallet_index)
        transporter = self._get_transporter(transporter_name)
        pallet_pos = self._pos_from_obj(pallet)
        approach_pos = self._transporter_pallet_approach_position(transporter, pallet, threshold=0.05)
        target_pos = self._vec3(target, z_default=0.0)
        release_standoff_pos = self._transporter_release_standoff_position(transporter, pallet, target_pos, target_obj=target_obj)
        self.add_log(
            "DEBUG",
            "Transporter move enqueue resolved",
            {
                "request": {
                    "pallet_name": pallet_name,
                    "pallet_index": pallet_index,
                    "target": target,
                    "target_name": target_name,
                    "target_index": target_index,
                    "transporter_name": transporter_name,
                },
                "resolved": {
                    "pallet_name": pallet.get("name"),
                    "pallet_position": pallet_pos,
                    "pallet_approach_position": approach_pos,
                    "target": target_pos,
                    "release_standoff_position": release_standoff_pos,
                    "target_object": target_obj.get("name") if target_obj else None,
                    "transporter_name": transporter.name,
                },
            },
        )

        task = self._enqueue_task(
            kind="transporter_move_pallet",
            steps=[
                {
                    "op": "transporter_approach_pallet",
                    "agent_name": transporter.name,
                    "target": approach_pos,
                    "pallet_name": pallet["name"],
                    "near_threshold": 0.05,
                },
                {"op": "transporter_move_to", "agent_name": transporter.name, "target": target_pos},
                {
                    "op": "transporter_release_pallet",
                    "agent_name": transporter.name,
                    "pallet_name": pallet["name"],
                    "target": target_pos,
                    "release_standoff": release_standoff_pos,
                },
            ],
            metadata={"pallet_name": pallet["name"], "target": target_pos, "release_standoff": release_standoff_pos},
        )
        return task

    def enqueue_named_command(self, command, params=None):
        params = dict(params or {})
        command = str(command or "").strip().lower()

        if command in ("conveyor_packages", "spawn_conveyor_packages"):
            return self.enqueue_conveyor_packages(**params)

        if command in ("arm_pick_place", "manipulator_pick_place"):
            return self.enqueue_arm_pick_place(**params)

        if command in ("shuttle_pick_to_conveyor", "pick_to_conveyor"):
            return self.enqueue_shuttle_pick_to_conveyor(**params)

        if command in ("transporter_move_pallet", "move_pallet"):
            return self.enqueue_transporter_move_pallet(**params)

        if command in ("reset", "reset_scene"):
            return self.enqueue_reset_scene()

        raise ValueError(f"Unknown command script command: {command}")

    def _run_command_script_tick(self):
        with self._task_lock:
            if not self.command_script_running:
                if self.command_script_status == "submitted" and self._is_api_idle_locked():
                    self.command_script_status = "done"
                return False
            if self.command_script_index >= len(self.command_script_steps):
                self.command_script_running = False
                self.command_script_status = "done"
                return False

            step = self.command_script_steps[self.command_script_index]
            now = time.monotonic()
            next_at = self.command_script_next_at or now
            if now < next_at:
                return False
            if step.get("wait_for_idle", False) and not self._is_api_idle_locked():
                return False

        try:
            result = self._dispatch_command_script_step(step)
        except Exception as exc:
            with self._task_lock:
                self.command_script_running = False
                self.command_script_status = "failed"
                self.command_script_error = str(exc)
            print(f"[LPB Script] Step failed: {exc}")
            return True

        with self._task_lock:
            task_id = result.get("task_id") if isinstance(result, dict) else None
            if task_id:
                self.command_script_submitted_tasks.append(task_id)
            self.command_script_index += 1
            if self.command_script_index >= len(self.command_script_steps):
                self.command_script_running = False
                self.command_script_status = "submitted"
                self.command_script_next_at = None
            else:
                next_step = self.command_script_steps[self.command_script_index]
                self.command_script_next_at = time.monotonic() + float(next_step.get("_delay", 0.0))
        return True

    def _dispatch_command_script_step(self, step):
        label = step.get("label") or step.get("name") or f"step_{self.command_script_index + 1}"
        print(f"[LPB Script] Dispatching {label}")

        if step.get("command"):
            return self.enqueue_named_command(step.get("command"), step.get("params") or step.get("payload") or {})

        method = str(step.get("method", "POST")).upper()
        path = str(step.get("path") or step.get("endpoint") or "").strip()
        payload = step.get("body") or step.get("payload") or step.get("params") or {}

        if method != "POST":
            raise ValueError("Command scripts currently support POST endpoint steps only.")

        parts = [part for part in path.strip("/").split("/") if part]
        if parts == ["arm", "pick_place"]:
            return self.enqueue_arm_pick_place(
                package_name=payload.get("package_name"),
                candidate_index=payload.get("candidate_index"),
                target=payload.get("target"),
                arm_name=payload.get("arm_name"),
                pallet_name=payload.get("pallet_name"),
                pallet_index=payload.get("pallet_index"),
                pallet_offset=payload.get("pallet_offset") or payload.get("relative_target") or payload.get("relative_position"),
            )

        if len(parts) == 3 and parts[0] == "conveyors" and parts[2] == "packages":
            return self.enqueue_conveyor_packages(
                conveyor_name=parts[1],
                count=payload.get("count", payload.get("n", 1)),
                package_prefix=payload.get("package_prefix"),
                dimensions=payload.get("dimensions"),
                spacing=payload.get("spacing", 0.45),
                at=payload.get("at", "end"),
                interval_seconds=payload.get("interval_seconds", 0.35),
            )

        if parts == ["shuttle", "pick_to_conveyor"]:
            return self.enqueue_shuttle_pick_to_conveyor(
                shelf_index=payload.get("shelf_index"),
                package_names=payload.get("package_names"),
                package_positions=payload.get("package_positions"),
                conveyor_name=payload.get("conveyor_name"),
                shuttle_name=payload.get("shuttle_name"),
                drop_spacing=payload.get("drop_spacing", 0.45),
            )

        if parts == ["transporter", "move_pallet"]:
            return self.enqueue_transporter_move_pallet(
                pallet_name=payload.get("pallet_name"),
                pallet_index=payload.get("pallet_index"),
                target=payload.get("target"),
                target_name=payload.get("target_name"),
                target_index=payload.get("target_index"),
                transporter_name=payload.get("transporter_name"),
            )

        if parts == ["reset"]:
            return self.enqueue_reset_scene()

        raise ValueError(f"Unknown script endpoint: {method} {path}")

    def _enqueue_task(self, kind, steps, metadata=None):
        if not steps:
            raise ValueError("Task has no steps.")
        task_id = uuid.uuid4().hex[:12]
        task = {
            "task_id": task_id,
            "kind": kind,
            "status": "queued",
            "current_step": 0,
            "total_steps": len(steps),
            "steps": steps,
            "metadata": metadata or {},
            "error": None,
        }
        with self._task_lock:
            self.api_tasks[task_id] = task
            self.api_queue.append(task_id)
        self.add_log(
            "INFO",
            "Task queued",
            {
                "task_id": task_id,
                "kind": kind,
                "steps": len(steps),
                "metadata": metadata or {},
                "queue": self._task_queue_summary_locked(),
            },
        )
        self._play_timeline_for_api_task()
        return dict(task)

    def _run_api_task_tick(self):
        with self._task_lock:
            if self.active_api_task_id is None and self.api_queue:
                self.active_api_task_id = self.api_queue.pop(0)
                self.api_tasks[self.active_api_task_id]["status"] = "running"
                self.add_log(
                    "INFO",
                    "Task started",
                    {
                        "task_id": self.active_api_task_id,
                        "kind": self.api_tasks[self.active_api_task_id].get("kind"),
                        "queue": self._task_queue_summary_locked(),
                    },
                )

            task_id = self.active_api_task_id
            if task_id is None:
                return False
            task = self.api_tasks[task_id]
            step_index = task["current_step"]
            if step_index >= len(task["steps"]):
                task["status"] = "done"
                self.active_api_task_id = None
                self._finalize_task(task)
                return True
            step = task["steps"][step_index]

        try:
            if not step.get("_logged"):
                self.add_log(
                    "DEBUG",
                    "Task step executing",
                    {
                        "task_id": task_id,
                        "step_index": step_index,
                        "op": step.get("op"),
                        "args": self._public_step_args(step),
                        "snapshot": self._compact_debug_snapshot(),
                    },
                )
                step["_logged"] = True
                step["_last_wait_log"] = time.monotonic()
            done = self._execute_api_step(step)
        except Exception as exc:
            with self._task_lock:
                task["status"] = "failed"
                task["error"] = str(exc)
                self.active_api_task_id = None
            self.add_log("ERROR", "Task failed", {"task_id": task_id, "error": str(exc), "step": step})
            return True

        if done:
            with self._task_lock:
                task["current_step"] += 1
                completed_step_index = task["current_step"] - 1
                completed = {
                    "task_id": task_id,
                    "step_index": completed_step_index,
                    "op": step.get("op"),
                    "snapshot": self._compact_debug_snapshot(),
                }
                if task["current_step"] >= len(task["steps"]):
                    task["status"] = "done"
                    self.active_api_task_id = None
                    self._finalize_task(task)
                    self.add_log("DEBUG", "Task step done", completed)
                    self.add_log(
                        "INFO",
                        "Task done",
                        {
                            "task_id": task_id,
                            "kind": task.get("kind"),
                            "queue": self._task_queue_summary_locked(),
                            "snapshot": self._compact_debug_snapshot(),
                        },
                    )
                else:
                    self.add_log("DEBUG", "Task step done", completed)
        else:
            now = time.monotonic()
            last_wait_log = float(step.get("_last_wait_log", 0.0))
            if now - last_wait_log >= 5.0:
                step["_last_wait_log"] = now
                self.add_log(
                    "DEBUG",
                    "Task step still running",
                    {
                        "task_id": task_id,
                        "step_index": step_index,
                        "op": step.get("op"),
                        "snapshot": self._compact_debug_snapshot(),
                    },
                )
        return True

    def _is_api_idle_locked(self):
        return self.active_api_task_id is None and not self.api_queue

    def _public_step_args(self, step):
        return {k: v for k, v in step.items() if k != "op" and not str(k).startswith("_")}

    def _compact_debug_snapshot(self):
        return {
            "active_task_id": self.active_api_task_id,
            "queued_task_ids": list(self.api_queue),
            "agents": self._agent_positions_snapshot(),
            "candidate_pool": [
                {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "position": item.get("position"),
                    "source": item.get("source"),
                }
                for item in self.candidate_pool
            ],
            "held_packages": self._held_packages_snapshot(),
            "shuttle_roof_queues": {name: list(values) for name, values in self._shuttle_pick_queues.items()},
            "transporter_loads": self._transporter_loads_snapshot(),
            "conveyor_flows": self._conveyor_flows_snapshot(),
        }

    def _execute_api_step(self, step):
        op = step["op"]
        if op == "reset_scene":
            self.reset_scene(clear_tasks=False, clear_script=True)
            return True

        if op == "spawn_conveyor_packages":
            self._spawn_conveyor_packages(**self._public_step_args(step))
            return True

        if op == "spawn_conveyor_package":
            if not self._step_delay_elapsed(step):
                return False
            self._spawn_conveyor_package(**self._public_step_args(step))
            return True

        if op == "manipulator_pick_place":
            return self._virtual_manipulator_pick_place(step)

        if op == "shuttle_move_to":
            agent = self._get_shuttle(step.get("agent_name"))
            done = agent.move_to(step["target"])
            if done:
                if hasattr(agent, "set_arm_display_pose"):
                    agent.set_arm_display_pose(target=step["target"], mode="neutral")
            self._sync_virtual_holds(agent)
            return done or self._step_timed_out(step, timeout_seconds=8.0)

        if op == "shuttle_pick":
            agent = self._get_shuttle(step.get("agent_name"))
            return self._visual_shuttle_pick(agent, step)

        if op == "shuttle_place_abs":
            if not self._step_delay_elapsed(step):
                return False
            agent = self._get_shuttle(step.get("agent_name"))
            return self._visual_shuttle_place_on_conveyor(agent, step)

        if op == "transporter_move_to":
            agent = self._get_transporter(step.get("agent_name"))
            done = agent.move_to(step["target"])
            self._sync_transporter_load_to_target(agent, self._agent_position(agent))
            if done:
                self._sync_transporter_load_to_target(agent, step["target"])
                return True
            if self._step_timed_out(step, timeout_seconds=10.0):
                try:
                    agent.set_world_pose(position=step["target"])
                except Exception as exc:
                    self.add_log("WARN", "Transporter force pose failed", {"agent": step.get("agent_name"), "error": str(exc)})
                self._sync_transporter_load_to_target(agent, step["target"])
                return True
            return False

        if op == "transporter_approach_pallet":
            agent = self._get_transporter(step.get("agent_name"))
            pallet = self._get_pallet(pallet_name=step["pallet_name"])
            threshold = float(step.get("near_threshold", 0.05))
            done = agent.move_to(step["target"], tolerance=min(threshold, 0.05))
            near_info = self._transporter_pallet_near_info(agent, pallet, threshold=threshold)
            if done or near_info["near"]:
                self._visual_attach_pallet_to_transporter(agent, step["pallet_name"], near_info=near_info)
                return True
            if self._step_timed_out(step, timeout_seconds=10.0):
                try:
                    agent.set_world_pose(position=step["target"])
                except Exception as exc:
                    self.add_log("WARN", "Transporter approach force pose failed", {"agent": step.get("agent_name"), "error": str(exc)})
                near_info = self._transporter_pallet_near_info(agent, pallet, threshold=threshold)
                self._visual_attach_pallet_to_transporter(agent, step["pallet_name"], near_info=near_info)
                return True
            return False

        if op == "transporter_load_pallet":
            agent = self._get_transporter(step.get("agent_name"))
            try:
                agent.load_pallet(step["pallet_name"])
            except Exception as exc:
                self.add_log(
                    "WARN",
                    "Transporter physical load skipped, using virtual load",
                    {"agent": self._agent_name(agent), "pallet_name": step["pallet_name"], "error": str(exc)},
                )
            self._attach_pallet_to_transporter(agent, step["pallet_name"])
            return True

        if op == "transporter_release_pallet":
            agent = self._get_transporter(step.get("agent_name"))
            try:
                agent.release_pallet()
            except Exception as exc:
                self.add_log("WARN", "Transporter physical release skipped", {"agent": self._agent_name(agent), "error": str(exc)})
            if step.get("target"):
                self._release_transporter_pallet(
                    agent,
                    step["target"],
                    release_standoff=step.get("release_standoff"),
                )
            return True

        raise ValueError(f"Unknown API task op: {op}")

    def _finalize_task(self, task):
        if task["kind"] == "shuttle_pick_to_conveyor":
            self.add_log(
                "DEBUG",
                "Shuttle task finalized; conveyor flow will publish candidates at the end slot",
                {
                    "task_id": task.get("task_id"),
                    "package_names": task.get("metadata", {}).get("package_names", []),
                    "conveyor_flows": self._conveyor_flows_snapshot(),
                },
            )

    def _step_delay_elapsed(self, step):
        delay = float(step.get("delay_seconds", 0.0) or 0.0)
        if delay <= 0.0:
            return True
        ready_at = step.get("_ready_at")
        if ready_at is None:
            step["_ready_at"] = time.monotonic() + delay
            return False
        return time.monotonic() >= float(ready_at)

    def _step_timed_out(self, step, timeout_seconds):
        started_at = step.get("_started_at")
        if started_at is None:
            step["_started_at"] = time.monotonic()
            return False
        if time.monotonic() - float(started_at) < float(timeout_seconds):
            return False
        if not step.get("_timeout_logged"):
            step["_timeout_logged"] = True
            self.add_log(
                "WARN",
                "Task step forced complete after timeout",
                {"op": step.get("op"), "timeout_seconds": float(timeout_seconds)},
            )
        return True

    def _spawn_conveyor_package(
        self,
        conveyor_name,
        package_base_name,
        dimensions,
        spacing,
        at,
        sequence_index=0,
        delay_seconds=None,
    ):
        start_pos = self._conveyor_endpoint(conveyor_name, at="start")
        endpoint = self._conveyor_endpoint(conveyor_name, at="end")
        segment = self._conveyor_segment(conveyor_name, at="start")
        yaw = math.radians(float(segment.get("rotation", 0.0)))
        dx = -math.cos(yaw) * float(spacing)
        dy = -math.sin(yaw) * float(spacing)
        idx = int(sequence_index)
        name = self._unique_scene_name(package_base_name)
        pos = [start_pos[0] + dx * idx, start_pos[1] + dy * idx, start_pos[2] + 0.45]
        pos[2] = self._conveyor_package_rest_z(conveyor_name, at="start", package_name=name, dimensions=dimensions)
        self._spawn_box(name=name, position=pos, dimensions=dimensions)
        self._conveyor_flows[name] = {
            "conveyor_name": conveyor_name,
            "start": pos,
            "end": self._conveyor_package_end_position(
                conveyor_name,
                pos,
                package_name=name,
                dimensions=dimensions,
            ),
            "dimensions": dimensions,
            "speed": 1.2,
            "status": "moving",
            "normal_scale": self._package_normal_scale(name),
        }
        self._set_package_physics(name, rigid=True, kinematic=True)
        self._apply_conveyor_velocity(name, conveyor_name)
        self.add_log(
            "INFO",
            "Conveyor package spawned at start",
            {"conveyor_name": conveyor_name, "package_name": name, "start": pos, "end": endpoint},
        )
        return {"name": name, "position": pos}

    def _move_conveyor_package_to_pool(self, package_name):
        flow = self._conveyor_flows.get(package_name)
        if not flow:
            return
        conveyor_name = flow.get("conveyor_name")
        slot_pos = self._conveyor_slot_position(conveyor_name, package_name)
        slot_obj = self.scene_index.get("slots", {}).get(f"{conveyor_name}_EndSlot")
        self.add_log(
            "DEBUG",
            "Conveyor slot placement resolved",
            {
                "package_name": package_name,
                "conveyor_name": conveyor_name,
                "slot_name": slot_obj.get("name") if slot_obj else None,
                "slot_surface_z": self._slot_surface_z(slot_obj) if slot_obj else None,
                "package_half_height": self._package_world_half_height(
                    package_name=package_name,
                    dimensions=(flow or {}).get("dimensions"),
                    clearance=float((slot_obj or {}).get("package_clearance_z", 0.0)),
                ),
                "slot_pos": slot_pos,
            },
        )
        self._set_package_world_position(package_name, slot_pos)
        self._set_package_scale(package_name, flow.get("normal_scale") or [1.0, 1.0, 1.0])
        self._set_package_physics(package_name, rigid=True, kinematic=True)
        flow["status"] = "in_slot"
        flow["slot_index"] = self._next_conveyor_slot_index(conveyor_name, exclude=package_name)
        self._upsert_candidate(
            package_name=package_name,
            position=slot_pos,
            dimensions=flow.get("dimensions"),
            source=f"conveyor_slot:{conveyor_name}",
            status="candidate",
        )
        self.add_log(
            "INFO",
            "Conveyor package moved to candidate pool",
            {
                "conveyor_name": conveyor_name,
                "package_name": package_name,
                "position": slot_pos,
                "candidate_pool": self.get_candidate_pool(),
                "flow": self._conveyor_flows_snapshot().get(package_name),
            },
        )

    def _update_visual_runtime(self):
        now = time.monotonic()
        last = self._runtime_last_update
        self._runtime_last_update = now
        dt = 0.0 if last is None else max(0.0, min(now - float(last), 0.2))
        self._update_conveyor_flows(dt)
        self._update_shuttle_roof_queues()
        self._update_transporter_loads()

    def _update_transporter_loads(self):
        for agent in self.transporters:
            agent_name = self._agent_name(agent)
            if agent_name in self._transporter_load_records:
                self._sync_transporter_load_to_target(agent, self._agent_position(agent))

    def _update_conveyor_flows(self, dt):
        if dt <= 0.0:
            return
        for package_name, flow in list(self._conveyor_flows.items()):
            if flow.get("status") != "moving":
                continue

            current = self._package_world_position(package_name) or flow.get("current") or flow.get("start")
            end = self._vec3(flow.get("end"))
            direction = [end[i] - current[i] for i in range(3)]
            dist = math.sqrt(sum(v * v for v in direction))
            if dist <= 0.05:
                self._move_conveyor_package_to_pool(package_name)
                continue

            speed = float(flow.get("speed", 1.2))
            step = min(dist, speed * dt)
            next_pos = [current[i] + direction[i] / max(dist, 1e-6) * step for i in range(3)]
            flow["current"] = next_pos
            self._set_package_world_position(package_name, next_pos)
            self._apply_conveyor_velocity(package_name, flow.get("conveyor_name"))

    def _shelf_package_approach_position(self, shelf, package_pos):
        shelf_pos = self._pos_from_obj(shelf)
        dims = shelf.get("dimensions", {}) or {}
        width = float(dims.get("width", 1.0))
        yaw = math.radians(float(shelf.get("rotation", 0.0)))
        normal = [-math.sin(yaw), math.cos(yaw)]
        side = -1.0 if package_pos[1] <= shelf_pos[1] else 1.0
        clearance = max(1.0, width * 0.5 + 0.75)
        return [
            float(package_pos[0]) + normal[0] * side * clearance,
            float(package_pos[1]) + normal[1] * side * clearance,
            0.0,
        ]

    def _visual_shuttle_pick(self, agent, step):
        package_name = step["package_name"]
        package_pos = self._vec3(step.get("package_position") or self._package_world_position(package_name))
        if not step.get("_visual_started_at"):
            step["_visual_started_at"] = time.monotonic()
            step["_pick_target"] = package_pos
            step["_pick_display_target"] = self._shuttle_gripper_display_target(
                agent,
                package_pos,
                package_name=package_name,
                min_clearance=step.get("pick_gripper_min_clearance", 0.36),
            )
            try:
                if hasattr(agent, "reset"):
                    agent.reset()
                if hasattr(agent, "set_arm_display_pose"):
                    agent.set_arm_display_pose(target=step["_pick_display_target"], mode="pick")
            except Exception as exc:
                self.add_log(
                    "WARN",
                    "Shuttle pick display pose failed, using roof queue",
                    {"agent": self._agent_name(agent), "package_name": package_name, "error": str(exc)},
                )
            return False

        try:
            if hasattr(agent, "set_arm_display_pose"):
                display_target = step.get("_pick_display_target") or self._shuttle_gripper_display_target(
                    agent,
                    step.get("_pick_target") or package_pos,
                    package_name=package_name,
                    min_clearance=step.get("pick_gripper_min_clearance", 0.36),
                )
                agent.set_arm_display_pose(target=display_target, mode="pick")
        except Exception:
            pass

        elapsed = time.monotonic() - float(step.get("_visual_started_at", time.monotonic()))
        if elapsed < float(step.get("visual_pick_seconds", 1.0)):
            return False

        self._add_package_to_shuttle_queue(agent, package_name)
        self._remove_package_from_shelves(package_name)
        self._mark_candidate(package_name, "picked_by_shuttle")
        self.add_log(
            "INFO",
            "Shuttle package shown on roof queue",
            {
                "agent": self._agent_name(agent),
                "package_name": package_name,
                "visual_elapsed": elapsed,
                "roof_queue": list(self._shuttle_pick_queues.get(self._agent_name(agent), [])),
                "package_position": self._package_world_position(package_name),
            },
        )
        try:
            if hasattr(agent, "set_arm_display_pose"):
                agent.set_arm_display_pose(mode="neutral")
        except Exception:
            pass
        return True

    def _shuttle_gripper_display_target(self, agent, target, package_name=None, min_clearance=0.32):
        target = self._vec3(target)
        try:
            base_pos = self._vec3(agent.get_base_world_position())
        except Exception:
            base_pos = self._agent_position(agent)

        package_dims = self._package_dimensions_for(package_name) if package_name else None
        package_height = self._dimensions_xyz(package_dims, default=(0.3, 0.3, 0.5))[2]
        mount_height = float(getattr(agent, "arm_mount_height", 0.29))
        min_z = float(base_pos[2]) + mount_height + max(float(min_clearance), float(package_height) * 0.5 + 0.16)
        if target[2] < min_z:
            target[2] = min_z
        return target

    def _drive_shuttle_gripper_place(self, agent, package_name, target, step):
        display_target = self._shuttle_gripper_display_target(
            agent,
            target,
            package_name=package_name,
            min_clearance=step.get("place_gripper_min_clearance", 0.34),
        )
        try:
            if hasattr(agent, "set_arm_display_pose"):
                agent.set_arm_display_pose(target=display_target, mode="place")
        except Exception as exc:
            if not step.get("_place_display_warned"):
                step["_place_display_warned"] = True
                self.add_log(
                    "WARN",
                    "Shuttle gripper display pose skipped",
                    {
                        "agent": self._agent_name(agent),
                        "package_name": package_name,
                        "target": self._vec3(target),
                        "display_target": display_target,
                        "error": str(exc),
                    },
                )
        return False

    def _set_shuttle_place_phase(self, step, phase, now=None):
        phase = str(phase)
        if step.get("_place_phase") == phase:
            return
        step["_place_phase"] = phase
        step["_place_phase_started_at"] = float(now if now is not None else time.monotonic())

    def _sync_package_to_shuttle_gripper(self, agent, package_name, fallback_pos, step):
        fallback_pos = self._vec3(fallback_pos)
        try:
            ee_pos, _ = agent.get_end_effector_pose()
        except Exception as exc:
            ee_pos = None
            if not step.get("_end_effector_pose_warned"):
                step["_end_effector_pose_warned"] = True
                self.add_log(
                    "DEBUG",
                    "Shuttle end-effector pose unavailable, using fallback package path",
                    {"agent": self._agent_name(agent), "package_name": package_name, "error": str(exc)},
                )

        if ee_pos is None:
            smooth_pos = self._smooth_follow_target(step, fallback_pos, key="_smooth_fallback")
            self._set_package_world_position(package_name, smooth_pos)
            return smooth_pos

        dims = self._package_dimensions_for(package_name)
        package_height = float(self._dimensions_xyz(dims, default=(0.3, 0.3, 0.5))[2])
        ee_pos = self._vec3(ee_pos)
        sane_distance = float(step.get("gripper_pose_sane_distance", 1.2))
        min_ee_z = float(step.get("gripper_min_world_z", max(0.25, package_height + 0.08)))
        distance_to_fallback = math.sqrt(sum((ee_pos[i] - fallback_pos[i]) ** 2 for i in range(3)))
        if (
            not all(math.isfinite(float(value)) for value in ee_pos)
            or distance_to_fallback > sane_distance
            or float(ee_pos[2]) < min_ee_z
        ):
            if not step.get("_end_effector_pose_rejected"):
                step["_end_effector_pose_rejected"] = True
                self.add_log(
                    "WARN",
                    "Shuttle end-effector pose rejected, using smooth fallback package path",
                    {
                        "agent": self._agent_name(agent),
                        "package_name": package_name,
                        "ee_pos": ee_pos,
                        "fallback_pos": fallback_pos,
                        "distance": distance_to_fallback,
                        "min_ee_z": min_ee_z,
                    },
                )
            smooth_pos = self._smooth_follow_target(step, fallback_pos, key="_smooth_fallback")
            self._set_package_world_position(package_name, smooth_pos)
            return smooth_pos

        z_offset = float(step.get("gripper_package_z_offset", package_height))
        package_pos = [ee_pos[0], ee_pos[1], ee_pos[2] - z_offset]
        self._set_package_world_position(package_name, package_pos)
        # Reset smooth target to current ee position so next rejection starts from here
        step["_smooth_fallback"] = list(package_pos)
        return package_pos
 
    def _visual_shuttle_place_on_conveyor(self, agent, step):
        package_name = step["package_name"]
        conveyor_name = step.get("conveyor_name")
        target = self._vec3(step.get("target"))
        now = time.monotonic()

        if not step.get("_visual_started_at"):
            hold_pos = self._agent_hold_position(agent, 0.95)
            above_target = [target[0], target[1], target[2] + float(step.get("place_hover_height", 0.35))]
            step["_visual_started_at"] = now
            step["_place_hold_pos"] = hold_pos
            step["_place_above_target"] = above_target
            step["_place_released"] = False
            self._set_shuttle_place_phase(step, "hold", now=now)
            try:
                agent.reset()
            except Exception:
                pass
            self._sync_package_to_shuttle_gripper(agent, package_name, hold_pos, step)
            return False

        hold_seconds = float(step.get("place_hold_seconds", 0.35))
        approach_seconds = float(step.get("place_approach_seconds", 1.2))
        lower_seconds = float(step.get("place_lower_seconds", 0.65))
        release_seconds = float(step.get("place_release_seconds", 0.25))
        phase = step.get("_place_phase", "hold")
        phase_started_at = float(step.get("_place_phase_started_at", step.get("_visual_started_at", now)))
        phase_elapsed = max(0.0, now - phase_started_at)
        hold_pos = self._vec3(step.get("_place_hold_pos"))
        above_target = self._vec3(step.get("_place_above_target"))

        if phase == "hold":
            self._sync_package_to_shuttle_gripper(agent, package_name, hold_pos, step)
            if phase_elapsed >= hold_seconds:
                self._set_shuttle_place_phase(step, "approach", now=now)
            return False

        if phase == "approach":
            controller_done = self._drive_shuttle_gripper_place(agent, package_name, above_target, step)
            self._sync_package_to_shuttle_gripper(agent, package_name, above_target, step)
            if controller_done or phase_elapsed >= approach_seconds:
                self._set_shuttle_place_phase(step, "lower", now=now)
            return False

        if phase == "lower":
            controller_done = self._drive_shuttle_gripper_place(agent, package_name, target, step)
            self._sync_package_to_shuttle_gripper(agent, package_name, target, step)
            if controller_done or phase_elapsed >= lower_seconds:
                self._set_shuttle_place_phase(step, "release", now=now)
            return False

        if not step.get("_place_released"):
            step["_place_released"] = True
            step["_place_released_at"] = now
            self._remove_package_from_shuttle_queue(agent, package_name)
            self._set_package_scale(package_name, self._package_normal_scale(package_name))
            self._set_package_world_position(package_name, target)
            return False

        if now - float(step.get("_place_released_at", now)) < release_seconds:
            self._set_package_world_position(package_name, target)
            return False

        self._conveyor_flows[package_name] = {
            "conveyor_name": conveyor_name,
            "start": target,
            "current": target,
            "end": self._conveyor_package_end_position(conveyor_name, target, package_name=package_name),
            "dimensions": self._package_dimensions_for(package_name),
            "speed": float(step.get("conveyor_speed", 1.2)),
            "status": "moving",
            "normal_scale": self._package_normal_scale(package_name),
        }
        self._set_package_physics(package_name, rigid=True, kinematic=True)
        self._apply_conveyor_velocity(package_name, conveyor_name)
        self.add_log(
            "INFO",
            "Shuttle placed package on conveyor start; waiting for end-slot arrival before candidate pool",
            {
                "agent": self._agent_name(agent),
                "package_name": package_name,
                "conveyor_name": conveyor_name,
                "target": target,
                "flow": self._conveyor_flows_snapshot().get(package_name),
                "candidate_pool": self.get_candidate_pool(),
            },
        )
        try:
            agent.reset()
        except Exception:
            pass
        return True

    def _add_package_to_shuttle_queue(self, agent, package_name):
        agent_name = self._agent_name(agent)
        queue = self._shuttle_pick_queues.setdefault(agent_name, [])
        if package_name not in queue:
            queue.append(package_name)
        self._remember_package_scale(package_name)
        self._set_package_scale(package_name, self._package_roof_scale(package_name))
        self._update_shuttle_roof_queues()

    def _remove_package_from_shuttle_queue(self, agent, package_name):
        agent_name = self._agent_name(agent)
        queue = self._shuttle_pick_queues.setdefault(agent_name, [])
        if package_name in queue:
            queue.remove(package_name)
        self._update_shuttle_roof_queues()

    def _update_shuttle_roof_queues(self):
        for agent_name, package_names in list(self._shuttle_pick_queues.items()):
            agent = None
            try:
                agent = self._get_shuttle(agent_name)
            except Exception:
                continue
            base = self._agent_position(agent)
            for idx, package_name in enumerate(list(package_names)):
                row = idx // 3
                col = idx % 3
                offset_x = (col - 1) * 0.22
                offset_y = 0.18 + row * 0.22
                raw_z = max(base[2] + 0.55, 0.55)
                target = [base[0] + offset_x, base[1] + offset_y, raw_z]
                prev = self._shuttle_roof_positions.get(package_name)
                if prev is not None:
                    alpha = 0.3
                    roof_pos = [
                        prev[0] + (target[0] - prev[0]) * alpha,
                        prev[1] + (target[1] - prev[1]) * alpha,
                        prev[2] + (target[2] - prev[2]) * alpha,
                    ]
                else:
                    roof_pos = target
                self._shuttle_roof_positions[package_name] = list(roof_pos)
                self._set_package_world_position(package_name, roof_pos)

    def _conveyor_package_end_position(self, conveyor_name, start_pos, package_name=None, dimensions=None):
        end = self._conveyor_endpoint(conveyor_name, at="end")
        rest_z = self._conveyor_package_rest_z(
            conveyor_name,
            at="end",
            package_name=package_name,
            dimensions=dimensions,
        )
        return [end[0], end[1], max(rest_z, start_pos[2])]

    def _conveyor_surface_z(self, conveyor_name, at="end"):
        segment = self._conveyor_segment(conveyor_name, at=at)
        pos = self._pos_from_obj(segment)
        dims = segment.get("dimensions", {}) or {}
        return pos[2] + float(dims.get("height", 0.35)) * 0.5

    def _package_support_height(self, package_name=None, dimensions=None, clearance=0.01):
        dims = dimensions if dimensions is not None else self._package_dimensions_for(package_name)
        if isinstance(dims, dict):
            height = float(dims.get("height", dims.get("z", 0.5)))
        else:
            raw = list(dims or [])
            height = float(raw[2]) if len(raw) > 2 else 0.5
        return height * 0.5 + float(clearance)

    def _object_size_xyz(self, obj, default=(1.0, 1.0, 1.0)):
        dims = (obj or {}).get("dimensions", {}) or {}
        return self._dimensions_xyz(dims, default=default)

    def _dimensions_xyz(self, dims, default=(1.0, 1.0, 1.0)):
        if isinstance(dims, dict):
            return (
                float(dims.get("length", dims.get("x", default[0]))),
                float(dims.get("width", dims.get("y", default[1]))),
                float(dims.get("height", dims.get("z", default[2]))),
            )
        raw = list(dims or [])
        return (
            float(raw[0]) if len(raw) > 0 else float(default[0]),
            float(raw[1]) if len(raw) > 1 else float(default[1]),
            float(raw[2]) if len(raw) > 2 else float(default[2]),
        )

    def _point_in_object_footprint(self, obj, point, padding=0.0):
        center = self._pos_from_obj(obj)
        px, py = float(point[0]) - float(center[0]), float(point[1]) - float(center[1])
        yaw = math.radians(float((obj or {}).get("rotation", 0.0)))
        local_x = px * math.cos(yaw) + py * math.sin(yaw)
        local_y = -px * math.sin(yaw) + py * math.cos(yaw)
        length, width, _ = self._object_size_xyz(obj)
        return abs(local_x) <= length * 0.5 + float(padding) and abs(local_y) <= width * 0.5 + float(padding)

    def _pallet_surface_z(self, pallet):
        center = self._pos_from_obj(pallet)
        _, _, height = self._object_size_xyz(pallet, default=(1.2, 1.0, 0.25))
        default_surface_bias = -float(height) * 0.16
        surface_bias = float((pallet or {}).get("surface_offset_z", default_surface_bias))
        return float(center[2]) + float(height) * 0.5 + surface_bias

    def _support_top_z_at_target(self, target, exclude_package=None):
        target = self._vec3(target)
        support_top = None
        seen = set()
        for item in self.candidate_pool:
            name = item.get("name")
            if not name or name == exclude_package or name in seen:
                continue
            if item.get("status") not in ("placed", "on_pallet"):
                continue
            if not self._point_in_object_footprint(item, target, padding=0.03):
                continue
            _, _, height = self._object_size_xyz(item, default=(0.3, 0.3, 0.5))
            top = float(self._pos_from_obj(item)[2]) + float(height) * 0.5
            support_top = top if support_top is None else max(support_top, top)
            seen.add(name)
        return support_top

    def _resolve_pallet_relative_target(self, package_name, pallet_name=None, pallet_index=None, pallet_offset=None):
        if pallet_name is None and pallet_index is None:
            raise ValueError("target or pallet_name/pallet_index with pallet_offset is required.")
        pallet = self._get_pallet(pallet_name=pallet_name, pallet_index=pallet_index)
        offset = self._vec3(pallet_offset or [0.0, 0.0, 0.0])
        center = self._pos_from_obj(pallet)
        length, width, _ = self._object_size_xyz(pallet, default=(1.2, 1.0, 0.25))
        package_dims = self._package_dimensions_for(package_name)
        pkg_length, pkg_width, _ = self._dimensions_xyz(package_dims, default=(0.3, 0.3, 0.5))

        max_x = max(0.0, float(length) * 0.5 - float(pkg_length) * 0.5 - 0.02)
        max_y = max(0.0, float(width) * 0.5 - float(pkg_width) * 0.5 - 0.02)
        clamped_offset = [
            float(np.clip(offset[0], -max_x, max_x)),
            float(np.clip(offset[1], -max_y, max_y)),
            max(0.0, float(offset[2])),
        ]
        yaw = math.radians(float((pallet or {}).get("rotation", 0.0)))
        world_xy = [
            center[0] + clamped_offset[0] * math.cos(yaw) - clamped_offset[1] * math.sin(yaw),
            center[1] + clamped_offset[0] * math.sin(yaw) + clamped_offset[1] * math.cos(yaw),
        ]
        target = [
            world_xy[0],
            world_xy[1],
            self._pallet_surface_z(pallet) + clamped_offset[2],
        ]
        self.add_log(
            "DEBUG",
            "Pallet-relative arm target resolved",
            {
                "package_name": package_name,
                "pallet_name": pallet.get("name"),
                "requested_offset": offset,
                "clamped_offset": clamped_offset,
                "target": target,
            },
        )
        return target

    def _resolve_manipulator_place_target(self, package_name, target):
        target_pos = self._vec3(target)
        package_dims = self._package_dimensions_for(package_name)
        package_support = self._package_support_height(package_name=package_name, dimensions=package_dims, clearance=0.003)
        for pallet in self.scene_index.get("pallets", []):
            if not self._point_in_object_footprint(pallet, target_pos, padding=0.05):
                continue
            support_top = self._pallet_surface_z(pallet)
            stacked_top = self._support_top_z_at_target(target_pos, exclude_package=package_name)
            if stacked_top is not None:
                support_top = max(support_top, stacked_top)
            resolved = [target_pos[0], target_pos[1], support_top + package_support]
            self.add_log(
                "DEBUG",
                "Manipulator place target snapped to pallet support",
                {
                    "package_name": package_name,
                    "requested_target": self._vec3(target),
                    "resolved_target": resolved,
                    "pallet_name": pallet.get("name"),
                    "support_top_z": support_top,
                },
            )
            return resolved
        return target_pos

    def _conveyor_package_rest_z(self, conveyor_name, at="start", package_name=None, dimensions=None):
        segment = self._conveyor_segment(conveyor_name, at=at)
        segment_dims = segment.get("dimensions", {}) or {}
        default_surface_bias = -float(segment_dims.get("height", 0.35)) * 0.28
        surface_bias = float(segment.get("surface_offset_z", default_surface_bias))
        clearance = float(segment.get("package_clearance_z", 0.01))
        return self._conveyor_surface_z(conveyor_name, at=at) + surface_bias + self._package_support_height(
            package_name=package_name,
            dimensions=dimensions,
            clearance=clearance,
        )

    def _slot_surface_z(self, slot_obj):
        slot_name = (slot_obj or {}).get("name")
        bounds = self._prim_world_z_bounds(slot_name) if slot_name else None
        if bounds is not None:
            surface_bias = float((slot_obj or {}).get("surface_offset_z", 0.0))
            return float(bounds[1]) + surface_bias
        center = self._pos_from_obj(slot_obj)
        _, _, height = self._object_size_xyz(slot_obj, default=(1.6, 1.0, 0.18))
        default_surface_bias = -float(height) * 0.45
        surface_bias = float((slot_obj or {}).get("surface_offset_z", default_surface_bias))
        return float(center[2]) + float(height) * 0.5 + surface_bias

    def _prim_world_z_bounds(self, name):
        if not name:
            return None
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return None
            prim = stage.GetPrimAtPath(f"/World/{name}")
            if not prim or not prim.IsValid():
                return None
            bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
            bbox = bbox_cache.ComputeWorldBound(prim)
            aligned = bbox.ComputeAlignedRange()
            return float(aligned.GetMin()[2]), float(aligned.GetMax()[2])
        except Exception:
            return None

    def _package_world_half_height(self, package_name=None, dimensions=None, clearance=0.01):
        bounds = self._prim_world_z_bounds(package_name) if package_name else None
        if bounds is not None:
            return max(0.0, (float(bounds[1]) - float(bounds[0])) * 0.5) + float(clearance)
        return self._package_support_height(package_name=package_name, dimensions=dimensions, clearance=clearance)

    def _conveyor_slot_position(self, conveyor_name, package_name=None):
        flow = self._conveyor_flows.get(package_name or "", {})
        slot_index = flow.get("slot_index")
        if slot_index is None:
            slot_index = self._next_conveyor_slot_index(conveyor_name, exclude=package_name)
        slot_obj = self.scene_index.get("slots", {}).get(f"{conveyor_name}_EndSlot")
        if slot_obj:
            center = self._pos_from_obj(slot_obj)
            dims = slot_obj.get("dimensions", {}) or {}
            slot_clearance = float(slot_obj.get("package_clearance_z", 0.002))
            row = int(slot_index) // 3
            col = int(slot_index) % 3
            spacing_x = min(0.45, max(float(dims.get("length", 1.6)) / 4.0, 0.32))
            spacing_y = min(0.45, max(float(dims.get("width", 1.0)) / 3.0, 0.32))
            return [
                center[0] + (col - 1) * spacing_x,
                center[1] + (row - 0.5) * spacing_y,
                self._slot_surface_z(slot_obj)
                + self._package_world_half_height(
                    package_name=package_name,
                    dimensions=flow.get("dimensions"),
                    clearance=slot_clearance,
                ),
            ]

        end = self._conveyor_endpoint(conveyor_name, at="end")
        segment = self._conveyor_segment(conveyor_name, at="end")
        yaw = math.radians(float(segment.get("rotation", 0.0)))
        side = [math.sin(yaw), -math.cos(yaw)]
        row = int(slot_index) // 3
        col = int(slot_index) % 3
        return [
            end[0] + side[0] * 1.0 + (col - 1) * 0.38,
            end[1] + side[1] * 1.0 + row * 0.38,
            end[2] + 0.62,
        ]

    def _next_conveyor_slot_index(self, conveyor_name, exclude=None):
        used = []
        for name, item in self._conveyor_flows.items():
            if name == exclude:
                continue
            if item.get("conveyor_name") == conveyor_name and item.get("status") == "in_slot":
                used.append(int(item.get("slot_index", len(used))))
        return max(used, default=-1) + 1

    def _set_package_physics(self, package_name, rigid=True, kinematic=True):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(f"/World/{package_name}")
        if not prim or not prim.IsValid():
            return
        requested_kinematic = bool(kinematic)
        if rigid and not requested_kinematic:
            kinematic = True
            self.add_log(
                "DEBUG",
                "Package dynamic rigid body disabled; keeping kinematic for mesh-safe demo motion",
                {"package_name": package_name},
            )
        try:
            if rigid and not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI.Apply(prim)
            if rigid:
                rigid_api = UsdPhysics.RigidBodyAPI.Apply(prim)
                try:
                    rigid_api.CreateKinematicEnabledAttr().Set(bool(kinematic))
                except Exception:
                    pass
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI.Apply(prim)
        except Exception as exc:
            self.add_log("DEBUG", "Package physics setup skipped", {"package_name": package_name, "error": str(exc)})

    def _set_package_collision(self, package_name, enabled=True):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(f"/World/{package_name}")
        if not prim or not prim.IsValid():
            return
        try:
            updated_paths = []
            targets = []
            for candidate in Usd.PrimRange(prim):
                if candidate == prim or candidate.HasAPI(UsdPhysics.CollisionAPI) or candidate.IsA(UsdGeom.Mesh):
                    targets.append(candidate)
            for target_prim in targets:
                collision_api = UsdPhysics.CollisionAPI.Apply(target_prim)
                try:
                    collision_api.CreateCollisionEnabledAttr().Set(bool(enabled))
                except Exception:
                    attr = target_prim.GetAttribute("physics:collisionEnabled")
                    if not attr:
                        attr = target_prim.CreateAttribute("physics:collisionEnabled", Sdf.ValueTypeNames.Bool)
                    attr.Set(bool(enabled))
                updated_paths.append(str(target_prim.GetPath()))
            self.add_log(
                "DEBUG",
                "Package collision updated",
                {"package_name": package_name, "enabled": bool(enabled), "paths": updated_paths},
            )
        except Exception as exc:
            self.add_log(
                "DEBUG",
                "Package collision update skipped",
                {"package_name": package_name, "enabled": bool(enabled), "error": str(exc)},
            )

    def _apply_conveyor_velocity(self, package_name, conveyor_name):
        if not conveyor_name:
            return None
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return None
        prim = stage.GetPrimAtPath(f"/World/{package_name}")
        if not prim or not prim.IsValid():
            return None
        try:
            segment = self._conveyor_segment(conveyor_name, at="end")
            yaw = math.radians(float(segment.get("rotation", 0.0)))
            flow = self._conveyor_flows.get(package_name, {})
            speed = float(flow.get("speed", 1.2))
            rigid_api = UsdPhysics.RigidBodyAPI.Apply(prim)
            rigid_api.CreateVelocityAttr().Set(Gf.Vec3f(math.cos(yaw) * speed, math.sin(yaw) * speed, 0.0))
        except Exception as exc:
            self.add_log("DEBUG", "Conveyor velocity update skipped", {"package_name": package_name, "error": str(exc)})
        return None

    def _package_world_position(self, package_name):
        try:
            world = World.instance()
            if world is not None:
                obj = world.scene.get_object(package_name)
                if obj is not None:
                    pos, _ = obj.get_world_pose()
                    return self._vec3(pos)
        except Exception:
            pass
        record = self._find_package_record(package_name)
        return self._pos_from_obj(record) if record else None

    def _package_dimensions_for(self, package_name):
        record = self._find_package_record(package_name)
        return record.get("dimensions") if record and record.get("dimensions") else [0.3, 0.3, 0.5]

    def _package_normal_scale(self, package_name):
        if package_name in self._package_scales:
            return self._package_scales[package_name]
        record = self._find_package_record(package_name)
        if record and record.get("_normal_scale"):
            self._package_scales[package_name] = record["_normal_scale"]
            return record["_normal_scale"]
        scale = self._get_package_scale(package_name) or [1.0, 1.0, 1.0]
        self._package_scales[package_name] = scale
        if record is not None:
            record["_normal_scale"] = scale
        return scale

    def _package_roof_scale(self, package_name):
        normal = self._package_normal_scale(package_name)
        return [float(v) * 0.45 for v in normal]

    def _set_package_scale(self, package_name, scale):
        if scale is None:
            return
        scale = [float(scale[0]), float(scale[1]), float(scale[2])]
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(f"/World/{package_name}")
        if not prim or not prim.IsValid():
            return
        try:
            xformable = UsdGeom.Xformable(prim)
            scale_op = None
            for op in xformable.GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                    scale_op = op
                    break
            if scale_op is None:
                scale_op = xformable.AddScaleOp(UsdGeom.XformOp.PrecisionDouble)
            scale_op.Set(Gf.Vec3d(*scale))
        except Exception as exc:
            self.add_log("DEBUG", "Package scale update skipped", {"package_name": package_name, "error": str(exc)})

    def _get_package_scale(self, package_name):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return None
        prim = stage.GetPrimAtPath(f"/World/{package_name}")
        if not prim or not prim.IsValid():
            return None
        try:
            for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                    value = op.Get()
                    return [float(value[0]), float(value[1]), float(value[2])]
        except Exception:
            return None
        return None

    def _remember_package_scale(self, package_name):
        if package_name not in self._package_scales:
            self._package_scales[package_name] = self._get_package_scale(package_name) or [1.0, 1.0, 1.0]

    def _spawn_conveyor_packages(self, conveyor_name, count, package_prefix, dimensions, spacing, at):
        created = []
        for idx in range(int(count)):
            created.append(
                self._spawn_conveyor_package(
                    conveyor_name=conveyor_name,
                    package_base_name=f"{package_prefix}_{idx + 1}",
                    dimensions=dimensions,
                    spacing=spacing,
                    at=at,
                    sequence_index=idx,
                )
            )
        self.add_log(
            "INFO",
            "Conveyor packages spawned",
            {"conveyor_name": conveyor_name, "count": int(count), "packages": created},
        )

    def _spawn_box(self, name, position, dimensions):
        extension_root = Path(__file__).resolve().parents[2]
        usd_path = extension_root / "data" / "Assets" / "box.usd"
        return self.add_to_scene(
            usd_path=usd_path.as_posix(),
            name=name,
            position=position,
            scale=dimensions,
        )

    def _virtual_pick(self, agent, package_name, hold_height=0.9):
        agent_name = self._agent_name(agent)
        package_record = self._find_package_record(package_name)
        dimensions = package_record.get("dimensions") if package_record else None
        self._held_packages[package_name] = {
            "agent": agent,
            "agent_name": agent_name,
            "hold_height": float(hold_height),
            "dimensions": dimensions,
        }
        self._sync_virtual_package(package_name)
        self.add_log(
            "INFO",
            "Package virtually picked",
            {
                "agent": agent_name,
                "package_name": package_name,
                "position": self._package_world_position(package_name),
                "held_packages": self._held_packages_snapshot(),
            },
        )
        return True

    def _virtual_place_on_conveyor(self, agent, package_name, target):
        target = self._vec3(target)
        package_record = self._find_package_record(package_name)
        held = self._held_packages.get(package_name, {})
        dimensions = package_record.get("dimensions") if package_record else held.get("dimensions")
        self._set_package_world_position(package_name, target)
        self._remove_package_from_shelves(package_name)
        self._release_virtual_package(package_name)
        self.add_log(
            "INFO",
            "Shelf package placed on conveyor; candidate pool waits for end-slot arrival",
            {
                "package_name": package_name,
                "position": target,
                "candidate_pool": self.get_candidate_pool(),
            },
        )
        try:
            agent.reset()
        except Exception:
            pass
        return True

    def _virtual_manipulator_pick_place(self, step):
        agent = self._get_manipulator(step.get("agent_name"))
        now = time.monotonic()
        if not self._resolve_deferred_manipulator_candidate(step, now=now):
            return False

        package_name = step["package_name"]
        target = self._vec3(step["target"])
        if not step.get("_virtual_started_at"):
            step["_virtual_started_at"] = now
            package_record = self._find_package_record(package_name) or {"position": target}
            package_pos = self._package_world_position(package_name) or self._pos_from_obj(package_record)
            package_dims = package_record.get("dimensions") or self._package_dimensions_for(package_name)
            package_half_height = self._package_support_height(
                package_name=package_name,
                dimensions=package_dims,
                clearance=0.0,
            )
            pick_hover = [
                package_pos[0],
                package_pos[1],
                max(package_pos[2] + 0.55, package_pos[2] + package_half_height + 0.28),
            ]
            place_hover = [
                target[0],
                target[1],
                max(target[2] + 0.55, pick_hover[2]),
            ]
            pre_release = [target[0], target[1], target[2] + 0.08]
            step["_package_start_pos"] = package_pos
            step["_package_dims"] = package_dims
            step["_pick_hover_pos"] = pick_hover
            step["_place_hover_pos"] = place_hover
            step["_pre_release_pos"] = pre_release
            step["_manipulator_phase"] = "approach_pick"
            step["_phase_started_at"] = now
            step["_phase_durations"] = {
                "approach_pick": float(step.get("approach_pick_seconds", 1.0)),
                "lower_pick": float(step.get("lower_pick_seconds", 0.8)),
                "grip": float(step.get("grip_seconds", 0.35)),
                "lift": float(step.get("lift_seconds", 0.8)),
                "transfer": float(step.get("transfer_seconds", 1.25)),
                "lower_place": float(step.get("lower_place_seconds", 0.9)),
                "release": float(step.get("release_seconds", 0.25)),
                "retreat": float(step.get("retreat_seconds", 0.55)),
            }
            try:
                agent.reset()
            except Exception:
                pass
            self._set_package_physics(package_name, rigid=True, kinematic=True)
            self._set_package_velocity(package_name)
            if package_name in self._conveyor_flows:
                self._conveyor_flows[package_name]["status"] = "reserved_by_arm"
            self.add_log(
                "INFO",
                "Manipulator pick-place started",
                {
                    "agent": self._agent_name(agent),
                    "package_name": package_name,
                    "package_start": package_pos,
                    "pick_hover": pick_hover,
                    "target": target,
                    "place_hover": place_hover,
                },
            )

        phase = step.get("_manipulator_phase", "approach_pick")
        phase_elapsed = now - float(step.get("_phase_started_at", now))
        durations = step.get("_phase_durations", {})

        package_start = self._vec3(step.get("_package_start_pos"))
        pick_hover = self._vec3(step.get("_pick_hover_pos"))
        place_hover = self._vec3(step.get("_place_hover_pos"))
        pre_release = self._vec3(step.get("_pre_release_pos"))
        self._drive_manipulator_semantic_pose(
            agent,
            phase=phase,
            package_start=package_start,
            pick_hover=pick_hover,
            place_hover=place_hover,
            pre_release=pre_release,
            target=target,
            phase_elapsed=phase_elapsed,
            durations=durations,
        )

        if phase == "approach_pick":
            if phase_elapsed >= float(durations.get("approach_pick", 1.0)) or self._manipulator_end_effector_near(
                agent,
                pick_hover,
                tolerance=0.28,
            ):
                self._set_manipulator_phase(step, "lower_pick", now, agent, package_name)
            return False

        if phase == "lower_pick":
            t = self._phase_ratio(phase_elapsed, durations.get("lower_pick", 0.8))
            st = self._smoothstep(t)
            package_half = self._package_world_half_height(
                package_name=package_name, clearance=0.04
            )
            lift_z = package_start[2] + (package_half + 0.06) * st
            step["_mp_lift_pos"] = [package_start[0], package_start[1], lift_z]
            self._set_package_world_position(package_name, step["_mp_lift_pos"])
            if phase_elapsed >= float(durations.get("lower_pick", 0.8)) or self._manipulator_end_effector_near(
                agent,
                package_start,
                tolerance=0.32,
            ):
                self._attach_package_to_manipulator(agent, package_name, package_start)
                if package_name in self._conveyor_flows:
                    self._conveyor_flows[package_name]["status"] = "picked_by_arm"
                self._mark_candidate(package_name, "picked_by_arm")
                self._set_manipulator_phase(step, "grip", now, agent, package_name)
            return False

        if phase == "grip":
            self._sync_manipulator_attached_package(agent, package_name, package_start)
            if phase_elapsed >= float(durations.get("grip", 0.35)):
                self._set_manipulator_phase(step, "lift", now, agent, package_name)
            return False

        if phase == "lift":
            t = self._phase_ratio(phase_elapsed, durations.get("lift", 0.8))
            fallback = self._lerp_vec3(package_start, pick_hover, self._smoothstep(t))
            self._sync_manipulator_attached_package(agent, package_name, fallback)
            if t >= 1.0:
                self._set_manipulator_phase(step, "transfer", now, agent, package_name)
            return False

        if phase == "transfer":
            t = self._phase_ratio(phase_elapsed, durations.get("transfer", 1.25))
            fallback = self._lerp_vec3(pick_hover, place_hover, self._smoothstep(t))
            self._sync_manipulator_attached_package(agent, package_name, fallback)
            if t >= 1.0:
                self._set_manipulator_phase(step, "lower_place", now, agent, package_name)
            return False

        if phase == "lower_place":
            t = self._phase_ratio(phase_elapsed, durations.get("lower_place", 0.9))
            fallback = self._lerp_vec3(place_hover, pre_release, self._smoothstep(t))
            self._sync_manipulator_attached_package(agent, package_name, fallback)
            if t >= 1.0 or self._manipulator_end_effector_near(agent, pre_release, tolerance=0.32):
                self._set_manipulator_phase(step, "release", now, agent, package_name)
            return False

        if phase == "release":
            self._sync_manipulator_attached_package(agent, package_name, pre_release)
            if phase_elapsed < float(durations.get("release", 0.25)):
                return False
            self._set_package_world_position(package_name, target)
            self._release_virtual_package(package_name)
            self._set_package_physics(package_name, rigid=True, kinematic=True)
            self._set_package_collision(package_name, enabled=True)
            if package_name in self._conveyor_flows:
                self._conveyor_flows[package_name]["status"] = "placed_by_arm"
            status = "placed"
            for pallet in self.scene_index.get("pallets", []):
                if self._point_in_object_footprint(pallet, target, padding=0.05):
                    status = "on_pallet"
                    break
            self._mark_candidate(package_name, status)
            self.add_log(
                "INFO",
                "Package released by manipulator",
                {
                    "agent": self._agent_name(agent),
                    "package_name": package_name,
                    "target": target,
                    "status": status,
                    "controller_done": bool(step.get("_controller_done")),
                    "candidate_pool": self.get_candidate_pool(),
                },
            )
            self._set_manipulator_phase(step, "retreat", now, agent, package_name)
            return False

        if phase == "retreat":
            if phase_elapsed < float(durations.get("retreat", 0.55)):
                return False
            try:
                agent.reset()
            except Exception:
                pass
            self.add_log(
                "INFO",
                "Manipulator pick-place completed",
                {
                    "agent": self._agent_name(agent),
                    "package_name": package_name,
                    "target": target,
                    "final_position": self._package_world_position(package_name),
                    "controller_done": bool(step.get("_controller_done")),
                },
            )
            return True

        self.add_log(
            "WARN",
            "Unknown manipulator phase, forcing release",
            {"phase": phase, "package_name": package_name, "target": target},
        )
        self._set_package_world_position(package_name, target)
        self._release_virtual_package(package_name)
        self._set_package_physics(package_name, rigid=True, kinematic=True)
        self._set_package_collision(package_name, enabled=True)
        self._mark_candidate(package_name, "placed")
        return True

    def _resolve_deferred_manipulator_candidate(self, step, now=None):
        if step.get("package_name") and step.get("target") is not None:
            return True

        now = float(now if now is not None else time.monotonic())
        package_name = step.get("package_name")
        candidate_index = step.get("candidate_index")
        if not package_name:
            candidate = self._candidate_by_index(candidate_index, required=False)
            if candidate is None:
                last_log = float(step.get("_candidate_wait_logged_at", 0.0))
                if now - last_log >= 2.0:
                    step["_candidate_wait_logged_at"] = now
                    self.add_log(
                        "INFO",
                        "Manipulator waiting for conveyor end-slot candidate",
                        {
                            "agent": step.get("agent_name"),
                            "candidate_index": candidate_index,
                            "candidate_pool": self.get_candidate_pool(),
                            "conveyor_flows": self._conveyor_flows_snapshot(),
                        },
                    )
                return False

            package_name = candidate.get("name")
            step["package_name"] = package_name
            self._mark_candidate(package_name, "reserved")
            self.add_log(
                "INFO",
                "Manipulator claimed candidate from end-slot pool",
                {
                    "agent": step.get("agent_name"),
                    "candidate_index": candidate_index,
                    "package_name": package_name,
                    "candidate": dict(candidate),
                },
            )

        if step.get("target") is None:
            requested_target = step.get("requested_target")
            target = (
                self._vec3(requested_target)
                if requested_target is not None
                else self._resolve_pallet_relative_target(
                    package_name=package_name,
                    pallet_name=step.get("pallet_name"),
                    pallet_index=step.get("pallet_index"),
                    pallet_offset=step.get("pallet_offset"),
                )
            )
            step["target"] = self._resolve_manipulator_place_target(package_name, target)
            self.add_log(
                "DEBUG",
                "Deferred manipulator target resolved",
                {
                    "agent": step.get("agent_name"),
                    "package_name": package_name,
                    "target": step.get("target"),
                    "pallet_name": step.get("pallet_name"),
                    "pallet_index": step.get("pallet_index"),
                    "pallet_offset": step.get("pallet_offset"),
                },
            )

        return True

    def _drive_manipulator_controller(self, agent, package_name, target, step):
        if step.get("_controller_done") or step.get("_controller_failed"):
            return bool(step.get("_controller_done"))
        try:
            if hasattr(agent, "pick_and_place_positions"):
                done = agent.pick_and_place_positions(
                    picking_position=step.get("_package_start_pos") or self._package_world_position(package_name),
                    placing_position=target,
                    package_dimensions=step.get("_package_dims"),
                )
            else:
                done = agent.pick_and_place_relative(
                    obj=package_name,
                    pos=target,
                    relative_obj=None,
                )
            control_status = getattr(agent, "last_control_status", {}) or {}
            if control_status and not control_status.get("accepted", True):
                step["_controller_failed"] = True
                self.add_log(
                    "WARN",
                    "Manipulator controller action rejected, continuing semantic motion",
                    {
                        "agent": self._agent_name(agent),
                        "package_name": package_name,
                        "control_status": control_status,
                    },
                )
                return False
            step["_controller_done"] = bool(done)
            return bool(done)
        except Exception as exc:
            step["_controller_failed"] = True
            self.add_log(
                "WARN",
                "Manipulator controller skipped, using semantic package motion",
                {"agent": self._agent_name(agent), "package_name": package_name, "error": str(exc)},
            )
            return False

    def _drive_manipulator_semantic_pose(
        self,
        agent,
        phase,
        package_start,
        pick_hover,
        place_hover,
        pre_release,
        target,
        phase_elapsed=0.0,
        durations=None,
    ):
        if not hasattr(agent, "drive_semantic_pose"):
            return False
        durations = durations or {}
        phase = str(phase or "neutral")
        if phase == "approach_pick":
            pose_target = pick_hover
            mode = "hover"
        elif phase == "lower_pick":
            pose_target = package_start
            mode = "pick"
        elif phase == "grip":
            pose_target = package_start
            mode = "grip"
        elif phase == "lift":
            t = self._phase_ratio(phase_elapsed, durations.get("lift", 0.8))
            pose_target = self._lerp_vec3(package_start, pick_hover, self._smoothstep(t))
            mode = "lift"
        elif phase == "transfer":
            t = self._phase_ratio(phase_elapsed, durations.get("transfer", 1.25))
            pose_target = self._lerp_vec3(pick_hover, place_hover, self._smoothstep(t))
            mode = "transfer"
        elif phase == "lower_place":
            t = self._phase_ratio(phase_elapsed, durations.get("lower_place", 0.9))
            pose_target = self._lerp_vec3(place_hover, pre_release, self._smoothstep(t))
            mode = "place"
        elif phase == "release":
            pose_target = target
            mode = "release"
        elif phase == "retreat":
            pose_target = place_hover
            mode = "hover"
        else:
            pose_target = None
            mode = "neutral"
        pose_target = self._safe_manipulator_pose_target(agent, pose_target, phase=phase)
        try:
            return agent.drive_semantic_pose(target_position=pose_target, mode=mode)
        except Exception as exc:
            self.add_log(
                "DEBUG",
                "Manipulator semantic pose skipped",
                {"agent": self._agent_name(agent), "phase": phase, "error": str(exc)},
            )
            return False

    def _safe_manipulator_pose_target(self, agent, pose_target, phase=None):
        if pose_target is None:
            return None
        target = self._vec3(pose_target)
        try:
            base_pos = self._agent_position(agent)
        except Exception:
            base_pos = [0.0, 0.0, 0.0]
        min_z = float(base_pos[2]) + 0.35
        max_reach = 1.85
        dx = float(target[0]) - float(base_pos[0])
        dy = float(target[1]) - float(base_pos[1])
        reach = math.sqrt(dx * dx + dy * dy)
        adjusted = False
        if target[2] < min_z:
            target[2] = min_z
            adjusted = True
        if reach > max_reach:
            ratio = max_reach / max(reach, 1e-6)
            target[0] = float(base_pos[0]) + dx * ratio
            target[1] = float(base_pos[1]) + dy * ratio
            adjusted = True
        if adjusted:
            self.add_log(
                "DEBUG",
                "Manipulator pose target clamped to safe display range",
                {
                    "agent": self._agent_name(agent),
                    "phase": phase,
                    "requested_target": self._vec3(pose_target),
                    "safe_target": target,
                    "base_position": base_pos,
                },
            )
        return target

    def _set_manipulator_phase(self, step, phase, now, agent, package_name):
        previous = step.get("_manipulator_phase")
        step["_manipulator_phase"] = phase
        step["_phase_started_at"] = now
        self.add_log(
            "DEBUG",
            "Manipulator phase changed",
            {
                "agent": self._agent_name(agent),
                "package_name": package_name,
                "from": previous,
                "to": phase,
                "end_effector": self._manipulator_end_effector_position(agent),
                "package_position": self._package_world_position(package_name),
            },
        )

    def _attach_package_to_manipulator(self, agent, package_name, fallback_position):
        package_record = self._find_package_record(package_name)
        dimensions = package_record.get("dimensions") if package_record else self._package_dimensions_for(package_name)
        hold_pos = self._manipulator_package_follow_position(agent, package_name, fallback_position)
        self._held_packages[package_name] = {
            "agent": agent,
            "agent_name": self._agent_name(agent),
            "hold_height": 0.75,
            "dimensions": dimensions,
            "fixed_position": hold_pos,
        }
        self._set_package_physics(package_name, rigid=True, kinematic=True)
        self._set_package_collision(package_name, enabled=False)
        self._set_package_velocity(package_name)
        self._sync_virtual_package(package_name)
        self.add_log(
            "INFO",
            "Package attached to manipulator gripper",
            {
                "agent": self._agent_name(agent),
                "package_name": package_name,
                "position": self._package_world_position(package_name),
                "end_effector": self._manipulator_end_effector_position(agent),
            },
        )

    def _sync_manipulator_attached_package(self, agent, package_name, fallback_position):
        held = self._held_packages.get(package_name)
        if not held:
            return
        hold_pos = self._manipulator_package_follow_position(agent, package_name, fallback_position)
        held["fixed_position"] = hold_pos
        self._sync_virtual_package(package_name)

    def _manipulator_package_follow_position(self, agent, package_name, fallback_position):
        fallback = self._vec3(fallback_position)
        end_effector = self._manipulator_end_effector_position(agent)
        if end_effector is None:
            return fallback
        base_pos = self._agent_position(agent)
        if end_effector[2] < float(base_pos[2]) + 0.25:
            self.add_log(
                "DEBUG",
                "Manipulator end-effector below safe height; using scripted package fallback",
                {
                    "agent": self._agent_name(agent),
                    "package_name": package_name,
                    "end_effector": end_effector,
                    "fallback": fallback,
                },
            )
            return fallback
        package_half_height = self._package_world_half_height(package_name=package_name, clearance=0.0)
        candidate = [
            end_effector[0],
            end_effector[1],
            max(0.03, end_effector[2] - package_half_height - 0.04),
        ]
        if self._distance(candidate, fallback) <= 1.15:
            return candidate
        return fallback

    def _manipulator_end_effector_position(self, agent):
        if not hasattr(agent, "get_end_effector_pose"):
            return None
        try:
            pos, _ = agent.get_end_effector_pose()
            if pos is None:
                return None
            pos = self._vec3(pos)
            if all(math.isfinite(v) for v in pos):
                return pos
        except Exception:
            pass
        return None

    def _manipulator_end_effector_near(self, agent, target, tolerance=0.25):
        end_effector = self._manipulator_end_effector_position(agent)
        if end_effector is None:
            return False
        return self._distance(end_effector, self._vec3(target)) <= float(tolerance)

    def _phase_ratio(self, elapsed, duration):
        duration = max(float(duration), 0.001)
        return max(0.0, min(float(elapsed) / duration, 1.0))

    def _smoothstep(self, t):
        t = max(0.0, min(float(t), 1.0))
        return t * t * (3.0 - 2.0 * t)

    def _lerp_vec3(self, start, end, t):
        start = self._vec3(start)
        end = self._vec3(end)
        t = max(0.0, min(float(t), 1.0))
        return [start[i] + (end[i] - start[i]) * t for i in range(3)]

    def _smooth_follow_target(self, step, target, key="_smooth_fallback", alpha=0.25):
        target = self._vec3(target)
        prev = step.get(key)
        if prev is None:
            step[key] = list(target)
            return target
        smooth = [
            prev[0] + (target[0] - prev[0]) * alpha,
            prev[1] + (target[1] - prev[1]) * alpha,
            prev[2] + (target[2] - prev[2]) * alpha,
        ]
        step[key] = smooth
        return smooth

    def _set_package_velocity(self, package_name, linear=None, angular=None):
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(f"/World/{package_name}")
        if not prim or not prim.IsValid():
            return
        try:
            rigid_api = UsdPhysics.RigidBodyAPI.Apply(prim)
            linear = self._vec3(linear or [0.0, 0.0, 0.0])
            angular = self._vec3(angular or [0.0, 0.0, 0.0])
            rigid_api.CreateVelocityAttr().Set(Gf.Vec3f(*linear))
            rigid_api.CreateAngularVelocityAttr().Set(Gf.Vec3f(*angular))
        except Exception as exc:
            self.add_log("DEBUG", "Package velocity reset skipped", {"package_name": package_name, "error": str(exc)})

    def _sync_virtual_holds(self, agent):
        for package_name, held in list(self._held_packages.items()):
            if held.get("agent") is agent:
                self._sync_virtual_package(package_name)

    def _sync_virtual_package(self, package_name):
        held = self._held_packages.get(package_name)
        if not held:
            return
        agent = held.get("agent")
        hold_pos = held.get("fixed_position") or self._agent_hold_position(agent, held.get("hold_height", 0.9))
        self._set_package_world_position(package_name, hold_pos)

    def _release_virtual_package(self, package_name):
        self._held_packages.pop(package_name, None)

    def _agent_hold_position(self, agent, hold_height):
        pos = self._agent_position(agent)
        return [pos[0], pos[1], max(float(pos[2]) + float(hold_height), float(hold_height))]

    def _agent_position(self, agent):
        try:
            pos, _ = agent.get_world_pose()
            return self._vec3(pos)
        except Exception:
            return self._vec3(getattr(agent, "position", [0.0, 0.0, 0.0]))

    def _agent_name(self, agent):
        return getattr(agent, "name", None) or getattr(getattr(agent, "ur10", None), "name", None) or str(agent)

    def _sync_transporter_load_to_target(self, agent, target):
        agent_name = self._agent_name(agent)
        record = self._transporter_load_records.get(agent_name)
        if not record:
            return
        base = self._agent_position(agent)
        pallet_name = record["pallet_name"]
        try:
            pallet_obj = self._get_pallet(pallet_name=pallet_name)
            _, _, pallet_height = self._object_size_xyz(pallet_obj, default=(1.2, 1.0, 0.25))
        except Exception:
            pallet_height = 0.25
        platform_surface_z = 0.19
        pallet_bottom_z = base[2] + platform_surface_z + 0.02
        carrier_pos = [base[0], base[1], pallet_bottom_z + pallet_height * 0.5]
        self._move_pallet_stack(
            record["pallet_name"],
            carrier_pos,
            package_names=record.get("packages"),
            package_offsets=record.get("package_offsets"),
            log=False,
        )

    def _visual_attach_pallet_to_transporter(self, agent, pallet_name, near_info=None):
        agent_name = self._agent_name(agent)
        if self._transporter_loads.get(agent_name) == pallet_name:
            self._sync_transporter_load_to_target(agent, self._agent_position(agent))
            return
        self._attach_pallet_to_transporter(agent, pallet_name)
        self.add_log(
            "INFO",
            "Pallet visually attached after transporter near check",
            {
                "agent": agent_name,
                "pallet_name": pallet_name,
                "near_info": near_info or {},
                "agent_position": self._agent_position(agent),
                "transporter_loads": self._transporter_loads_snapshot(),
            },
        )

    def _attach_pallet_to_transporter(self, agent, pallet_name):
        agent_name = self._agent_name(agent)
        try:
            pallet = self._get_pallet(pallet_name=pallet_name)
            pallet_pos = self._pos_from_obj(pallet)
        except Exception:
            pallet = None
            pallet_pos = self._agent_position(agent)
        packages = self._packages_on_or_near_pallet(pallet, pallet_pos)
        package_offsets = self._capture_pallet_package_offsets(pallet_pos, packages)
        self._transporter_loads[agent_name] = pallet_name
        self._transporter_load_records[agent_name] = {
            "pallet_name": pallet_name,
            "packages": packages,
            "package_offsets": package_offsets,
        }
        for package_name in packages:
            self._set_package_physics(package_name, rigid=True, kinematic=True)
        self._sync_transporter_load_to_target(agent, self._agent_position(agent))
        self.add_log(
            "INFO",
            "Pallet attached above transporter",
            {
                "agent": agent_name,
                "pallet_name": pallet_name,
                "packages": packages,
                "package_offsets": package_offsets,
                "transporter_loads": self._transporter_loads_snapshot(),
                "agent_position": self._agent_position(agent),
            },
        )

    def _capture_pallet_package_offsets(self, pallet_pos, package_names):
        base = self._vec3(pallet_pos)
        offsets = {}
        for package_name in package_names or []:
            package_pos = self._package_world_position(package_name)
            if package_pos is None:
                record = self._find_package_record(package_name)
                package_pos = self._pos_from_obj(record) if record else None
            if package_pos is None:
                continue
            offsets[package_name] = [float(package_pos[i]) - float(base[i]) for i in range(3)]
        return offsets

    def _packages_on_or_near_pallet(self, pallet, pallet_pos):
        names = []
        for child in (pallet or {}).get("children", []) or []:
            if child.get("type") == "box" and child.get("name"):
                names.append(child.get("name"))
        names.extend(self._packages_near_position(pallet_pos, radius=1.6, max_height=2.5))
        deduped = []
        for name in names:
            if name and name not in deduped:
                deduped.append(name)
        return deduped

    def _transporter_pallet_approach_position(self, agent, pallet, threshold=0.05):
        center = self._pos_from_obj(pallet)
        agent_pos = self._agent_position(agent)
        length, width, _ = self._object_size_xyz(pallet, default=(1.2, 1.0, 0.25))
        yaw = math.radians(float((pallet or {}).get("rotation", 0.0)))
        dx = float(agent_pos[0]) - float(center[0])
        dy = float(agent_pos[1]) - float(center[1])
        local_x = dx * math.cos(yaw) + dy * math.sin(yaw)
        local_y = -dx * math.sin(yaw) + dy * math.cos(yaw)
        if abs(local_x) + abs(local_y) < 1e-6:
            local_x, local_y = -1.0, 0.0

        half_l = max(float(length) * 0.5, 0.001)
        half_w = max(float(width) * 0.5, 0.001)
        scale = max(abs(local_x) / half_l, abs(local_y) / half_w, 1e-6)
        boundary_x = local_x / scale
        boundary_y = local_y / scale
        norm = math.sqrt(local_x * local_x + local_y * local_y)
        dir_x = local_x / norm
        dir_y = local_y / norm
        radius = self._transporter_footprint_radius(agent)
        clearance = radius + float(threshold)
        approach_local_x = boundary_x + dir_x * clearance
        approach_local_y = boundary_y + dir_y * clearance
        world_x = center[0] + approach_local_x * math.cos(yaw) - approach_local_y * math.sin(yaw)
        world_y = center[1] + approach_local_x * math.sin(yaw) + approach_local_y * math.cos(yaw)
        return [world_x, world_y, 0.0]

    def _transporter_pallet_near_info(self, agent, pallet, threshold=0.05):
        agent_pos = self._agent_position(agent)
        footprint_distance = self._distance_to_object_footprint_xy(pallet, agent_pos)
        radius = self._transporter_footprint_radius(agent)
        clearance = max(0.0, footprint_distance - radius)
        return {
            "near": clearance <= float(threshold),
            "clearance": clearance,
            "threshold": float(threshold),
            "agent_radius": radius,
            "agent_position": agent_pos,
            "pallet_name": pallet.get("name"),
            "pallet_position": self._pos_from_obj(pallet),
        }

    def _transporter_release_standoff_position(self, agent, pallet, target, target_obj=None, clearance=0.25):
        target = self._vec3(target, z_default=0.0)
        _, pallet_width, _ = self._object_size_xyz(pallet, default=(1.2, 1.0, 0.25))
        radius = self._transporter_footprint_radius(agent)
        yaw_source = target_obj if target_obj is not None else pallet
        yaw = math.radians(float((yaw_source or {}).get("rotation", 0.0)))
        side_distance = float(pallet_width) * 0.5 + radius + float(clearance)
        local_x = 0.0
        local_y = -side_distance
        world_x = target[0] + local_x * math.cos(yaw) - local_y * math.sin(yaw)
        world_y = target[1] + local_x * math.sin(yaw) + local_y * math.cos(yaw)
        return [world_x, world_y, 0.0]

    def _distance_to_object_footprint_xy(self, obj, point):
        center = self._pos_from_obj(obj)
        px, py = float(point[0]) - float(center[0]), float(point[1]) - float(center[1])
        yaw = math.radians(float((obj or {}).get("rotation", 0.0)))
        local_x = px * math.cos(yaw) + py * math.sin(yaw)
        local_y = -px * math.sin(yaw) + py * math.cos(yaw)
        length, width, _ = self._object_size_xyz(obj)
        dx = max(abs(local_x) - float(length) * 0.5, 0.0)
        dy = max(abs(local_y) - float(width) * 0.5, 0.0)
        return math.sqrt(dx * dx + dy * dy)

    def _transporter_footprint_radius(self, agent):
        return float(getattr(agent, "footprint_radius", 0.65))

    def _release_transporter_pallet(self, agent, target, release_standoff=None):
        agent_name = self._agent_name(agent)
        record = self._transporter_load_records.pop(agent_name, None)
        pallet_name = (record or {}).get("pallet_name") or self._transporter_loads.get(agent_name)
        packages = (record or {}).get("packages")
        package_offsets = (record or {}).get("package_offsets")
        if pallet_name:
            self._move_pallet_stack(pallet_name, target, package_names=packages, package_offsets=package_offsets)
        self._transporter_loads.pop(agent_name, None)
        standoff_pos = self._move_transporter_to_release_standoff(agent, release_standoff) if release_standoff else None
        self.add_log(
            "INFO",
            "Pallet released from transporter",
            {
                "agent": agent_name,
                "pallet_name": pallet_name,
                "target": self._vec3(target),
                "release_standoff": standoff_pos,
                "packages": packages or [],
                "package_offsets": package_offsets or {},
                "transporter_loads": self._transporter_loads_snapshot(),
            },
        )

    def _move_transporter_to_release_standoff(self, agent, release_standoff):
        standoff_pos = self._vec3(release_standoff, z_default=0.0)
        try:
            agent.set_world_pose(position=standoff_pos)
            self.add_log(
                "INFO",
                "Transporter visually moved outside released pallet",
                {"agent": self._agent_name(agent), "release_standoff": standoff_pos},
            )
        except Exception as exc:
            self.add_log(
                "WARN",
                "Transporter release standoff move failed",
                {"agent": self._agent_name(agent), "release_standoff": standoff_pos, "error": str(exc)},
            )
        return standoff_pos

    def _move_pallet_stack(self, pallet_name, target, package_names=None, package_offsets=None, log=True):
        target = self._vec3(target)
        try:
            pallet = self._get_pallet(pallet_name=pallet_name)
            old_pos = self._pos_from_obj(pallet)
        except Exception:
            pallet = None
            old_pos = target

        stack_packages = list(package_names) if package_names is not None else self._packages_near_position(old_pos)
        self._set_scene_object_world_position(pallet_name, target, log=log)
        if pallet is not None:
            pallet["position"] = self._position_dict(target)

        offsets = dict(package_offsets or {})
        if not offsets and stack_packages:
            offsets = self._capture_pallet_package_offsets(old_pos, stack_packages)
        delta = [target[i] - old_pos[i] for i in range(3)]
        for package_name in stack_packages:
            if package_name in offsets:
                offset = self._vec3(offsets[package_name])
                self._set_package_world_position(package_name, [target[i] + offset[i] for i in range(3)])
            else:
                package_record = self._find_package_record(package_name)
                if package_record is None:
                    continue
                current = self._package_world_position(package_name) or self._pos_from_obj(package_record)
                self._set_package_world_position(package_name, [current[i] + delta[i] for i in range(3)])

        if log:
            self.add_log(
                "INFO",
                "Pallet stack moved",
                {
                    "pallet_name": pallet_name,
                    "target": target,
                    "packages": stack_packages,
                    "package_offsets": offsets,
                    "candidate_pool": self.get_candidate_pool(),
                },
            )

    def _packages_near_position(self, center, radius=1.4, max_height=2.0):
        center = self._vec3(center)
        names = []
        for item in self.candidate_pool:
            if item.get("status") not in ("placed", "on_pallet"):
                continue
            pos = self._pos_from_obj(item)
            if abs(pos[0] - center[0]) <= radius and abs(pos[1] - center[1]) <= radius:
                if -0.1 <= pos[2] - center[2] <= max_height:
                    names.append(item.get("name"))
        return [name for name in names if name]

    def _set_scene_object_world_position(self, name, position, log=True):
        pos = self._vec3(position)
        try:
            world = World.instance()
            if world is not None:
                obj = world.scene.get_object(name)
                if obj is not None:
                    obj.set_world_pose(position=np.array(pos))
        except Exception:
            pass
        if log:
            self.add_log("DEBUG", "Scene object position set", {"name": name, "position": pos})

    def _set_package_world_position(self, package_name, position):
        pos = self._vec3(position)
        try:
            world = World.instance()
            if world is not None:
                obj = world.scene.get_object(package_name)
                if obj is not None:
                    obj.set_world_pose(position=np.array(pos))
        except Exception:
            pass
        self._update_package_position_records(package_name, pos)

    def _update_package_position_records(self, package_name, position):
        pos = self._vec3(position)
        pos_dict = self._position_dict(pos)
        for item in self.candidate_pool:
            if item.get("name") == package_name:
                item["position"] = pos
        for shelf in self.scene_index.get("shelves", []):
            for package in shelf.get("children", []):
                if package.get("name") == package_name:
                    package["position"] = pos_dict
        for pallet in self.scene_index.get("pallets", []):
            for package in pallet.get("children", []):
                if package.get("name") == package_name:
                    package["position"] = pos_dict

    def _position_dict(self, position):
        pos = self._vec3(position)
        return {"x": pos[0], "y": pos[1], "z": pos[2]}

    def _get_shelf(self, shelf_index):
        return self._one_based(self.scene_index.get("shelves", []), shelf_index, "shelf")

    def _get_pallet(self, pallet_name=None, pallet_index=None):
        pallets = self.scene_index.get("pallets", [])
        if pallet_name:
            for pallet in pallets:
                if pallet.get("name") == pallet_name:
                    return pallet
            raise ValueError(f"Pallet not found: {pallet_name}")
        return self._one_based(pallets, pallet_index or 1, "pallet")

    def _get_transport_target(self, target_name=None, target_index=None):
        targets = self.scene_index.get("transport_targets", [])
        if target_name:
            for target in targets:
                if target.get("name") == target_name:
                    return target
            raise ValueError(f"Pallet transport target not found: {target_name}")
        return self._one_based(targets, target_index or 1, "pallet transport target")

    def _get_manipulator(self, name=None):
        if name:
            for item in self.manipulators:
                if getattr(item, "name", None) == name or getattr(getattr(item, "ur10", None), "name", None) == name:
                    return item
            raise ValueError(f"Manipulator not found: {name}")
        if not self.manipulator:
            raise ValueError("No manipulator configured.")
        return self.manipulator

    def _get_shuttle(self, name=None):
        if name:
            for item in self.shuttles:
                if getattr(item, "name", None) == name:
                    return item
            raise ValueError(f"Shuttle not found: {name}")
        if not self.shuttle:
            raise ValueError("No shuttle configured.")
        return self.shuttle

    def _get_transporter(self, name=None):
        if name:
            for item in self.transporters:
                if getattr(item, "name", None) == name:
                    return item
            raise ValueError(f"Transporter not found: {name}")
        if not self.transporter:
            raise ValueError("No transporter configured.")
        return self.transporter

    def _resolve_shelf_packages(self, shelf, package_names=None, package_positions=None):
        packages = [pkg for pkg in shelf.get("children", []) if pkg.get("type") == "box"]
        if package_names:
            names = set(package_names)
            found = [pkg for pkg in packages if pkg.get("name") in names]
            missing = names - {pkg.get("name") for pkg in found}
            if missing:
                raise ValueError(f"Packages not found on shelf {shelf.get('name')}: {sorted(missing)}")
            return found

        if package_positions:
            resolved = []
            for target in package_positions:
                target_pos = self._vec3(target)
                resolved.append(min(packages, key=lambda pkg: self._distance(self._pos_from_obj(pkg), target_pos)))
            return resolved

        raise ValueError("package_names or package_positions is required.")

    def _remove_package_from_shelves(self, package_name):
        for shelf in self.scene_index.get("shelves", []):
            children = shelf.get("children", [])
            shelf["children"] = [pkg for pkg in children if pkg.get("name") != package_name]

    def _find_package_record(self, package_name):
        for shelf in self.scene_index.get("shelves", []):
            for package in shelf.get("children", []):
                if package.get("name") == package_name:
                    return package
        for pallet in self.scene_index.get("pallets", []):
            for package in pallet.get("children", []):
                if package.get("name") == package_name:
                    return package
        for item in self.candidate_pool:
            if item.get("name") == package_name:
                return item
        return None

    def _candidate_by_index(self, candidate_index, required=True):
        if candidate_index is None:
            raise ValueError("package_name or candidate_index is required.")
        available = [
            item
            for item in self.candidate_pool
            if item.get("status") == "candidate"
        ]
        if not required:
            idx = int(candidate_index)
            if idx < 1 or idx > len(available):
                return None
        return self._one_based(available, candidate_index, "candidate")

    def _mark_candidate(self, package_name, status):
        for item in self.candidate_pool:
            if item.get("name") == package_name:
                old_status = item.get("status")
                item["status"] = status
                self.add_log(
                    "DEBUG",
                    "Candidate status changed",
                    {"package_name": package_name, "old_status": old_status, "new_status": status},
                )
                return

    def _upsert_candidate(self, package_name, position=None, dimensions=None, source=None, status="candidate"):
        for item in self.candidate_pool:
            if item.get("name") == package_name:
                before = dict(item)
                if position is not None:
                    item["position"] = self._vec3(position)
                if dimensions is not None:
                    item["dimensions"] = dimensions
                if source is not None:
                    item["source"] = source
                if status is not None:
                    item["status"] = status
                self.add_log(
                    "DEBUG",
                    "Candidate updated",
                    {"before": before, "after": dict(item)},
                )
                return item

        item = {
            "name": package_name,
            "position": self._vec3(position or [0.0, 0.0, 0.0]),
            "dimensions": dimensions or [0.3, 0.3, 0.5],
            "source": source,
            "status": status or "candidate",
        }
        self.candidate_pool.append(item)
        self.add_log("DEBUG", "Candidate added", {"candidate": dict(item)})
        return item

    def _conveyor_segment(self, conveyor_name, at="end"):
        segments = self.scene_index.get("conveyors", {}).get(conveyor_name)
        if not segments:
            raise ValueError(f"Conveyor not found: {conveyor_name}")
        return segments[-1] if at == "end" else segments[0]

    def _conveyor_endpoint(self, conveyor_name, at="end"):
        return self._pos_from_obj(self._conveyor_segment(conveyor_name, at=at))

    def _one_based(self, items, index, label):
        if not items:
            raise ValueError(f"No {label}s are available.")
        idx = int(index)
        if idx < 1 or idx > len(items):
            raise ValueError(f"{label}_index must be 1..{len(items)}, got {idx}.")
        return items[idx - 1]

    def _pos_from_obj(self, obj):
        return self._vec3(obj.get("position", [0.0, 0.0, 0.0]))

    def _vec3(self, value, z_default=0.0):
        if value is None:
            raise ValueError("Expected [x, y, z].")
        if isinstance(value, dict):
            return [float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", z_default))]
        vals = list(value)
        if len(vals) == 2:
            vals.append(z_default)
        return [float(vals[0]), float(vals[1]), float(vals[2])]

    def _distance(self, a, b):
        return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))

    def _base_conveyor_name(self, name):
        return re.sub(r"_\d+$", "", name or "")

    def _numeric_suffix(self, name):
        match = re.search(r"_(\d+)$", name or "")
        return int(match.group(1)) if match else 0

    def _unique_scene_name(self, base):
        existing = {item.get("name") for item in self.candidate_pool}
        existing.update(obj.get("name") for obj in self.scene_data.get("objects", []))
        if base not in existing:
            return base
        index = 2
        while f"{base}_{index}" in existing:
            index += 1
        return f"{base}_{index}"

    def add_to_scene(self, usd_path, name, position, scale=None, orientation=None):
        prim_path = f"/World/{name}"
        add_reference_to_stage(usd_path, prim_path)

        if orientation is not None:
            orientation = euler_angles_to_quat(np.array(orientation), degrees=True)

        prim = SingleXFormPrim(
            prim_path=prim_path,
            name=name,
            position=position,
            orientation=orientation,
            scale=scale,
        )

        world = World.instance()
        if world is None:
            world = World()
        world.scene.add(prim)
        return prim
