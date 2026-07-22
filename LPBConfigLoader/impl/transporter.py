import json
import math
import time
from pathlib import Path

import numpy as np
from isaacsim.core.api.world import World
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.utils.stage import add_reference_to_stage, get_current_stage
from isaacsim.core.utils.types import ArticulationAction
from pxr import Sdf, UsdPhysics

from .llm import LM


class Transporter:
    """Large Ridgeback-style pallet transporter.

    The public methods intentionally match the older Create3 transporter:
    move_to, load_pallet, release_pallet, get_world_pose and set_world_pose.
    """

    def __init__(self, name, position=None, orientation=None):
        self.loaded_obj = None
        self.target_yaw = None
        self.joint_prim = None
        self.name = name
        self._step = 0
        self.lm = None
        self.footprint_radius = 0.65

        self.position = np.array(position if position is not None else [0.0, 0.0, 0.0], dtype=float)
        self._last_base_world_position = self.position.copy()
        self._fallback_last_move_at = None
        self._joint_retry_after = {}
        quat = euler_angles_to_quat(np.array(orientation), degrees=True) if orientation is not None else None

        extension_root = Path(__file__).resolve().parents[2]
        usd_path = extension_root / "data" / "Assets" / "ridgeback.usd"
        self.prim_path = f"/World/{name}"

        add_reference_to_stage(usd_path.as_posix(), self.prim_path)
        self.ridgeback = SingleArticulation(
            prim_path=self.prim_path,
            name=name,
            position=self.position.tolist(),
            orientation=quat,
            scale=[1.0, 1.0, 1.0],
        )

        world = World.instance()
        if world is None:
            world = World()
        world.scene.add(self.ridgeback)

        self._x_joint_idx = None
        self._y_joint_idx = None
        self._rz_joint_idx = None
        self.sys_prompt = """
# Role
You are the Transporter Agent Logic Core (AGV/AMR).
Return execution plans using only move_to(pos=[x,y,z]), load_pallet(obj="name"), and release_pallet().
Prefer axis-aligned warehouse paths and keep clearance around static objects.
"""

    def initialize(self):
        self.ridgeback.initialize()
        dof_names = self.ridgeback.dof_names
        self._x_joint_idx = dof_names.index("dummy_base_prismatic_x_joint")
        self._y_joint_idx = dof_names.index("dummy_base_prismatic_y_joint")
        self._rz_joint_idx = dof_names.index("dummy_base_revolute_z_joint")

    def reset(self):
        self.release_pallet()
        self._step = 0

    def move_to(self, pos, tolerance=0.18):
        if self._x_joint_idx is None:
            try:
                self.initialize()
            except Exception:
                return self._move_to_world_pose_fallback(pos, tolerance=tolerance)

        pos = np.array(pos, dtype=float)
        target = pos.copy()
        target[:2] = target[:2] - self.position[:2]

        joint_positions = self._safe_get_joint_positions()
        if joint_positions is None:
            return self._move_to_world_pose_fallback(pos, tolerance=tolerance)

        dx = target[0] - joint_positions[self._x_joint_idx]
        dy = target[1] - joint_positions[self._y_joint_idx]
        if abs(dx) + abs(dy) < float(tolerance):
            return True

        action = ArticulationAction(joint_positions=np.full(self.ridgeback.num_dof, np.nan))
        action.joint_positions[self._x_joint_idx] = target[0]
        action.joint_positions[self._y_joint_idx] = target[1]
        action.joint_positions[self._rz_joint_idx] = math.atan2(dy, dx)
        self.ridgeback.apply_action(action)
        return False

    def load_pallet(self, obj):
        world = World.instance()
        if world is None:
            return True

        try:
            loaded = world.scene.get_object(obj)
        except Exception:
            loaded = None
        self.loaded_obj = loaded

        if loaded is None:
            return True

        try:
            stage = get_current_stage()
            joint_path = self.prim_path + "/FixedJoint"
            joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
            joint.CreateBody0Rel().SetTargets([Sdf.Path(self.prim_path)])
            joint.CreateBody1Rel().SetTargets([Sdf.Path(loaded.prim_path)])
            self.joint_prim = joint
        except Exception:
            self.joint_prim = None
        return True

    def release_pallet(self):
        try:
            stage = get_current_stage()
            joint_path = self.prim_path + "/FixedJoint"
            joint_prim = stage.GetPrimAtPath(joint_path)
            if joint_prim.IsValid():
                stage.RemovePrim(joint_path)
        except Exception:
            pass

        self.loaded_obj = None
        self.target_yaw = None
        self.joint_prim = None
        return True

    def get_world_pose(self):
        try:
            base_position, orientation = self.ridgeback.get_world_pose()
        except Exception:
            base_position, orientation = self.position.copy(), None

        if self._x_joint_idx is None or self._y_joint_idx is None:
            return np.array(base_position, dtype=float), orientation

        try:
            joint_positions = self._safe_get_joint_positions()
        except Exception:
            joint_positions = None
        if joint_positions is None:
            return self._last_base_world_position.copy(), orientation

        position = self.position.copy()
        position[0] += float(joint_positions[self._x_joint_idx])
        position[1] += float(joint_positions[self._y_joint_idx])
        self._last_base_world_position = position.copy()
        return position, orientation

    def set_world_pose(self, position=None, orientation=None):
        self.ridgeback.set_world_pose(position=position, orientation=orientation)
        if position is not None:
            self.position = np.array(position, dtype=float)
            self._last_base_world_position = self.position.copy()
            self._reset_dummy_base_joints()

    def _reset_dummy_base_joints(self):
        if self._x_joint_idx is None:
            return
        try:
            joint_positions = self._safe_get_joint_positions()
            if joint_positions is None:
                return
            joint_positions = np.array(joint_positions, dtype=float)
            joint_positions[self._x_joint_idx] = 0.0
            joint_positions[self._y_joint_idx] = 0.0
            if self._rz_joint_idx is not None:
                joint_positions[self._rz_joint_idx] = 0.0
            self.ridgeback.set_joint_positions(joint_positions)
        except Exception:
            pass

    def _safe_get_joint_positions(self):
        now = time.monotonic()
        if now < float(self._joint_retry_after.get("ridgeback", 0.0)):
            return None
        try:
            joints = self.ridgeback.get_joint_positions()
        except Exception:
            joints = None
        if joints is None:
            self._joint_retry_after["ridgeback"] = now + 0.35
            return None
        self._joint_retry_after["ridgeback"] = 0.0
        return joints

    def _move_to_world_pose_fallback(self, pos, tolerance=0.18, speed=1.1):
        target = np.array(pos, dtype=float)
        target[2] = self._last_base_world_position[2]
        current = self._last_base_world_position.copy()
        delta = target - current
        dist = float(np.linalg.norm(delta[:2]))
        now = time.monotonic()
        last = self._fallback_last_move_at
        self._fallback_last_move_at = now
        if dist <= float(tolerance):
            self._set_base_world_pose_fallback(target)
            return True
        dt = 0.05 if last is None else max(0.016, min(now - float(last), 0.12))
        step = min(dist, float(speed) * dt)
        ratio = step / max(dist, 1e-6)
        next_pos = current.copy()
        next_pos[:2] = current[:2] + delta[:2] * ratio
        self._set_base_world_pose_fallback(next_pos)
        return False

    def _set_base_world_pose_fallback(self, base_pos):
        base_pos = np.array(base_pos, dtype=float)
        self._last_base_world_position = base_pos.copy()
        try:
            self.ridgeback.set_world_pose(position=base_pos.tolist())
            if self._x_joint_idx is not None and self._y_joint_idx is not None:
                self.ridgeback.set_joint_positions(
                    positions=np.array([0.0, 0.0, 0.0]),
                    joint_indices=[self._x_joint_idx, self._y_joint_idx, self._rz_joint_idx],
                )
        except Exception:
            pass

    def init_llm(self, provider, model_name, api_key):
        self.lm = LM(provider, model_name, api_key, self.sys_prompt)

    def reason(self, sim_state, command):
        if self.lm is None:
            raise RuntimeError("Transporter LLM is not initialized.")
        raw_response = self.lm.generate(self.generate_prompt(sim_state, command))
        clean = raw_response.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        if clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        data = json.loads(clean.strip())
        payload = data.get("body", {}).get("payload", data.get("payload", data))
        return raw_response, payload.get("steps", [])

    def step(self, plan_steps):
        if self._step == len(plan_steps):
            self.reset()
            return True

        step = plan_steps[self._step]
        if self.execute_plan(step["func"], step.get("args", {})):
            self._step += 1
        return False

    def execute_plan(self, func_name, kwargs):
        if func_name == "move_to":
            return self.move_to(**kwargs)
        if func_name == "load_pallet":
            return self.load_pallet(**kwargs)
        if func_name == "release_pallet":
            return self.release_pallet()
        print(f"Error: Unknown function name '{func_name}' in execution plan.")
        return False

    # ========== A2A 相关代码已注释 ==========
    # def generate_prompt(self, sim_state, a2a_message):
    #     return json.dumps(
    #         {
    #             "simulation_state": sim_state,
    #             "robot": {"name": self.name, "position": self.get_world_pose()[0].tolist()},
    #             "command": a2a_message,
    #             "available_functions": ["move_to", "load_pallet", "release_pallet"],
    #         },
    #         ensure_ascii=False,
    #         indent=2,
    #         cls=_NumpyEncoder,
    #     )


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.float32, np.float64)):
            return float(obj)
        return super().default(obj)
