"""
在 Isaac Sim 当前 Stage 中“挂载” completed_scenes/ 下的已完成 USD 场景，并自动做单位缩放。

核心点：
- 不用 open_stage()（那会替换整个 Stage），避免与你现有的 shuttle/智能体场景冲突
- 使用 Reference 把完成场景挂到 /World/CompletedScene 下
- 读取源 USD 的 metersPerUnit，与当前 Stage 的 metersPerUnit 比较，自动设置缩放

一行用法（Script Editor）：
    from completed_scene_loader import load_completed_scene; load_completed_scene(1)
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any

import omni.usd
from pxr import Usd, UsdGeom, Gf


def _detect_base_dir() -> str:
    def _is_project_root(p: Path) -> bool:
        return (p / "assets").exists() and (p / "warehouse_scenes").exists()

    env = os.getenv("SCENE_BASE_DIR") or os.getenv("ISAAC_SCENE_BASE_DIR")
    if env:
        try:
            p = Path(env).expanduser().resolve()
            if _is_project_root(p):
                return str(p)
        except Exception:
            pass

    try:
        p = Path(__file__).resolve().parent
        if _is_project_root(p):
            return str(p)
    except Exception:
        pass

    try:
        home = Path.home().resolve()
        common = [
            home / "Desktop" / "scene",
            home / "Documents" / "scene",
            home / "scene",
        ]
        for p in common:
            if _is_project_root(p):
                return str(p.resolve())
    except Exception:
        pass

    cwd = Path(os.getcwd()).resolve()
    for p in [cwd] + list(cwd.parents):
        if _is_project_root(p):
            return str(p)

    return str(cwd)


BASE_DIR = _detect_base_dir().replace("\\", "/")


def _get_stage_or_create():
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()
    if stage:
        return stage
    # 极少数情况下没有 stage，创建一个空的
    ctx.new_stage()
    return ctx.get_stage()


def _get_meters_per_unit(stage: Optional[Usd.Stage]) -> Optional[float]:
    try:
        if not stage:
            return None
        v = UsdGeom.GetStageMetersPerUnit(stage)
        return float(v) if v else None
    except Exception:
        return None


def _set_scale_on_prim(prim: Usd.Prim, uniform_scale: float) -> None:
    xform = UsdGeom.Xformable(prim)
    if not xform:
        return

    # 尽量复用已有的 scale op
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeScale:
            op.Set(Gf.Vec3f(float(uniform_scale), float(uniform_scale), float(uniform_scale)))
            return

    op = xform.AddScaleOp()
    op.Set(Gf.Vec3f(float(uniform_scale), float(uniform_scale), float(uniform_scale)))


def load_completed_scene(
    scene_id: int,
    prim_path: str = "/World/CompletedScene",
    clear_existing: bool = True,
) -> Dict[str, Any]:
    """
    把 completed_scenes/completed_00X.usd 挂到当前 Stage，并按单位自动缩放。

    Args:
        scene_id: 1/2/3... -> completed_001.usd / completed_002.usd / ...
        prim_path: 挂载到哪个 prim 路径（默认 /World/CompletedScene）
        clear_existing: 是否先移除同名 prim 再挂载

    Returns:
        dict: {usd_path, prim_path, source_meters_per_unit, target_meters_per_unit, scale}
    """
    if not isinstance(scene_id, int):
        raise TypeError(f"scene_id must be int, got {type(scene_id).__name__}")
    if scene_id <= 0:
        raise ValueError(f"scene_id must be >= 1, got {scene_id}")

    filename = f"completed_{scene_id:03d}.usd"
    usd_path = (Path(BASE_DIR) / "completed_scenes" / filename).resolve()
    if not usd_path.exists():
        raise FileNotFoundError(f"USD file not found: {usd_path}")

    # 读源 USD 的 metersPerUnit
    src_stage = Usd.Stage.Open(usd_path.as_posix())
    src_mpu = _get_meters_per_unit(src_stage)

    # 当前 Stage
    stage = _get_stage_or_create()
    if not stage:
        raise RuntimeError("No USD stage available in omni.usd context.")
    dst_mpu = _get_meters_per_unit(stage)

    # 计算缩放：把“源 USD 的单位”映射到“当前 stage 的单位”
    scale = 1.0
    if src_mpu and dst_mpu and dst_mpu != 0:
        scale = float(src_mpu) / float(dst_mpu)

    # 清理旧 prim
    if clear_existing:
        old = stage.GetPrimAtPath(prim_path)
        if old and old.IsValid():
            stage.RemovePrim(prim_path)

    # 定义容器 prim，并加 reference
    prim = stage.DefinePrim(prim_path, "Xform")
    prim.GetReferences().AddReference(usd_path.as_posix())

    # 统一缩放（只对容器做缩放，避免改动源资产）
    _set_scale_on_prim(prim, scale)

    return {
        "usd_path": usd_path.as_posix(),
        "prim_path": prim_path,
        "source_meters_per_unit": src_mpu,
        "target_meters_per_unit": dst_mpu,
        "scale": scale,
    }

