import json
import math
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PACKAGE_SIZES = {
    "small": {"length": 0.25, "width": 0.25, "height": 0.4},
    "medium": {"length": 0.3, "width": 0.3, "height": 0.5},
    "large": {"length": 0.45, "width": 0.35, "height": 0.6},
}


def get_default_scene_config_path() -> str:
    return str((Path(__file__).resolve().parents[3] / "config" / "default_scene.json").resolve())


def load_config_scene(config_path: Optional[str] = None) -> Dict[str, Any]:
    resolved_path = _resolve_config_path(config_path)
    with open(resolved_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    scene_data, dynamic_agents = expand_scene_config(config)

    from . import new_isaac_scene_exporter

    new_isaac_scene_exporter.build_scene(
        scene_data=scene_data,
        source_path=str(resolved_path),
    )

    return {
        "config_path": str(resolved_path),
        "scene_data": scene_data,
        "dynamic_agents": dynamic_agents,
        "object_count": len(scene_data.get("objects", [])),
    }


def expand_scene_config(config: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    if not _uses_high_level_schema(config):
        return dict(config), {"shuttles": [], "transporters": [], "manipulators": []}

    scene_data: Dict[str, Any] = {
        "scene_name": config.get("scene_name", "configured_warehouse"),
        "warehouse": _normalise_warehouse(config.get("warehouse", {})),
        "objects": list(config.get("objects", [])),
        "agents": list(config.get("agents", [])),
    }
    dynamic_agents = {"shuttles": [], "transporters": [], "manipulators": []}

    objects = scene_data["objects"]
    seed = config.get("seed")

    for idx, layout in enumerate(_as_list(config.get("shelf_layouts") or config.get("shelves"))):
        objects.extend(_expand_shelf_layout(layout, idx, seed))

    for idx, conveyor in enumerate(_as_list(config.get("conveyors"))):
        objects.extend(_expand_conveyor_line(conveyor, idx))

    for idx, area in enumerate(_as_list(config.get("palletizing_areas"))):
        area_objects, area_agents = _expand_palletizing_area(area, idx, seed)
        objects.extend(area_objects)
        dynamic_agents["manipulators"].extend(area_agents)

    for idx, target in enumerate(_as_list(config.get("pallet_transport_targets") or config.get("transport_targets"))):
        objects.append(_expand_pallet_transport_target(target, idx))

    dynamic_agents["manipulators"].extend(_expand_robot_arms(config.get("robot_arms")))
    _expand_vehicles(config.get("vehicles", {}), dynamic_agents)

    return scene_data, dynamic_agents


def _resolve_config_path(config_path: Optional[str]) -> Path:
    if config_path and str(config_path).strip():
        path = Path(str(config_path).strip().strip('"')).expanduser()
        if not path.is_absolute():
            path = (Path(__file__).resolve().parents[3] / path).resolve()
    else:
        path = Path(get_default_scene_config_path())

    if not path.exists():
        raise FileNotFoundError(f"Scene config not found: {path}")
    return path


def _uses_high_level_schema(config: Dict[str, Any]) -> bool:
    high_level_keys = {
        "shelf_layouts",
        "shelves",
        "conveyors",
        "vehicles",
        "robot_arms",
        "palletizing_areas",
        "pallet_transport_targets",
        "transport_targets",
    }
    return any(key in config for key in high_level_keys)


def _normalise_warehouse(warehouse: Dict[str, Any]) -> Dict[str, Any]:
    dims = _dimensions(warehouse.get("dimensions") or warehouse.get("size"), (40.0, 30.0, 8.0))
    return {
        "name": warehouse.get("name", "Warehouse"),
        "dimensions": dims,
        "position": _position_dict(_vec3(warehouse.get("position"), (0.0, 0.0, 0.0))),
        "grid_size": warehouse.get("grid_size", max(dims["length"], dims["width"])),
    }


def _expand_shelf_layout(layout: Dict[str, Any], layout_index: int, seed: Optional[int]) -> List[Dict[str, Any]]:
    area = _area(layout.get("area") or layout)
    shelf_cfg = layout.get("shelf", {})
    dims = _dimensions(shelf_cfg.get("dimensions") or layout.get("dimensions"), (8.0, 1.0, 6.0))
    length = dims["length"]
    width = dims["width"]
    height = dims["height"]

    rotation = float(shelf_cfg.get("rotation", layout.get("rotation", _rotation_for_direction(layout.get("direction", "x")))))
    footprint_x, footprint_y = _rotated_footprint(length, width, rotation)
    spacing = _vec2(layout.get("spacing") or layout.get("gap"), (1.5, 1.5))
    pitch_x = footprint_x + spacing[0]
    pitch_y = footprint_y + spacing[1]

    columns = _positive_int(layout.get("columns"))
    rows = _positive_int(layout.get("rows"))
    if columns is None:
        columns = max(1, int(math.floor((area["size"][0] + spacing[0]) / max(pitch_x, 0.001))))
    if rows is None:
        rows = max(1, int(math.floor((area["size"][1] + spacing[1]) / max(pitch_y, 0.001))))

    max_count = rows * columns
    requested_count = _positive_int(layout.get("count")) or max_count
    count = min(requested_count, max_count)

    grid_width = (columns - 1) * pitch_x
    grid_height = (rows - 1) * pitch_y
    start_x = area["center"][0] - grid_width / 2.0
    start_y = area["center"][1] - grid_height / 2.0
    z = area["center"][2]

    name_prefix = layout.get("name_prefix") or layout.get("prefix") or f"Shelf_{layout_index + 1}"
    arrangement = str(layout.get("arrangement", "rows")).lower()
    segments = _positive_int(shelf_cfg.get("segments") or layout.get("segments")) or _segments_for_length(length)
    package_cfg = layout.get("packages", {})

    shelves: List[Dict[str, Any]] = []
    rng = random.Random((seed if seed is not None else 0) + layout_index * 1009)

    for index in range(count):
        if arrangement in ("columns", "column", "column_major"):
            row_index = index % rows
            col_index = index // rows
        else:
            row_index = index // columns
            col_index = index % columns

        shelf_name = f"{name_prefix}_{index + 1}"
        position = [start_x + col_index * pitch_x, start_y + row_index * pitch_y, z]
        shelf = {
            "type": "shelf",
            "name": shelf_name,
            "dimensions": dict(dims),
            "position": _position_dict(position),
            "rotation": rotation,
            "segments": segments,
            "children": [],
        }
        shelf["children"] = _packages_for_shelf(
            package_cfg,
            shelf_name=shelf_name,
            shelf_index=index,
            row_index=row_index,
            column_index=col_index,
            shelf_position=position,
            shelf_dimensions=dims,
            shelf_rotation=rotation,
            rng=rng,
        )
        shelves.append(shelf)

    return shelves


def _packages_for_shelf(
    package_cfg: Dict[str, Any],
    shelf_name: str,
    shelf_index: int,
    row_index: int,
    column_index: int,
    shelf_position: List[float],
    shelf_dimensions: Dict[str, float],
    shelf_rotation: float,
    rng: random.Random,
) -> List[Dict[str, Any]]:
    if not package_cfg:
        return []

    inferred_mode = "enumerated" if ("items" in package_cfg or "rows" in package_cfg) else "random"
    mode = str(package_cfg.get("mode", inferred_mode)).lower()
    specs: List[Dict[str, Any]] = []

    if mode in ("enum", "enumerated", "manual"):
        for item in _as_list(package_cfg.get("items")):
            if _matches_target(item, shelf_name, shelf_index, row_index, column_index):
                specs.append(dict(item))

        for rule in _as_list(package_cfg.get("rows")):
            if not _matches_target(rule, shelf_name, shelf_index, row_index, column_index, row_key="row"):
                continue
            rule_items = _as_list(rule.get("items"))
            if rule_items:
                for item in rule_items:
                    merged = dict(rule)
                    merged.update(item)
                    specs.append(merged)
            else:
                count = _positive_int(rule.get("count")) or 0
                start_slot = int(rule.get("start_slot", 0))
                for offset in range(count):
                    generated = dict(rule)
                    generated["slot"] = start_slot + offset
                    specs.append(generated)
    else:
        levels = list(package_cfg.get("levels", [1, 2, 3]))
        sizes = list(package_cfg.get("sizes", ["medium"]))
        slot_count = _positive_int(package_cfg.get("slots")) or _default_slot_count(shelf_dimensions["length"], package_cfg)
        if "fill_rate" in package_cfg and "count" not in package_cfg and "max_count" not in package_cfg:
            count = int(max(0, slot_count * len(levels) * float(package_cfg.get("fill_rate", 0))))
        else:
            min_count = int(package_cfg.get("min_count", package_cfg.get("count", 0)))
            max_count = int(package_cfg.get("max_count", package_cfg.get("count", min_count)))
            count = rng.randint(min_count, max(max_count, min_count)) if max_count > 0 else 0
        used = set()
        for _ in range(count):
            for _attempt in range(50):
                slot = rng.randrange(max(slot_count, 1))
                level = int(rng.choice(levels))
                key = (slot, level)
                if key not in used:
                    used.add(key)
                    break
            specs.append({"slot": slot, "level": level, "size": rng.choice(sizes)})

    packages: List[Dict[str, Any]] = []
    for package_index, spec in enumerate(specs, 1):
        dimensions = _package_dimensions(spec)
        local = _package_local_position(spec, shelf_dimensions, package_cfg)
        world = _local_to_world(shelf_position, local, shelf_rotation)
        size_name = str(spec.get("size", "custom"))
        name = spec.get("name") or f"Box_{shelf_name}_L{int(spec.get('level', 1))}_{size_name[:1].upper()}_{package_index}"
        packages.append(
            {
                "type": "box",
                "name": name,
                "dimensions": dimensions,
                "position": _position_dict(world),
                "rotation": shelf_rotation,
                "on_shelf": True,
                "shelf_name": shelf_name,
            }
        )
    return packages


def _expand_conveyor_line(conveyor: Dict[str, Any], conveyor_index: int) -> List[Dict[str, Any]]:
    start = _vec3(conveyor.get("start"), (0.0, 0.0, 0.0))
    end = _vec3(conveyor.get("end"), (0.0, 0.0, 0.0))
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    total_length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if total_length <= 0:
        return []

    dims = _dimensions(conveyor.get("dimensions"), (float(conveyor.get("segment_length", 2.0)), 0.8, 0.35))
    segment_length = float(conveyor.get("segment_length", dims["length"]))
    segment_count = max(1, int(math.ceil(total_length / max(segment_length, 0.001))))
    yaw = math.degrees(math.atan2(dy, dx))
    name_prefix = conveyor.get("name") or f"Conveyor_{conveyor_index + 1}"
    width = float(conveyor.get("width", dims["width"]))
    height = float(conveyor.get("height", dims["height"]))

    objects = []
    for idx in range(segment_count):
        t0 = idx / segment_count
        t1 = (idx + 1) / segment_count
        center_t = (t0 + t1) / 2.0
        actual_length = total_length / segment_count
        center = [
            start[0] + dx * center_t,
            start[1] + dy * center_t,
            start[2] + dz * center_t,
        ]
        objects.append(
            {
                "type": "conveyor",
                "name": f"{name_prefix}_{idx + 1}",
                "dimensions": {"length": actual_length, "width": width, "height": height},
                "position": _position_dict(center),
                "rotation": yaw,
                "surface_offset_z": float(conveyor.get("surface_offset_z", conveyor.get("belt_surface_offset_z", 0.0))),
                "package_clearance_z": float(conveyor.get("package_clearance_z", 0.01)),
            }
        )
    slot_cfg = conveyor.get("end_slot") or conveyor.get("slot")
    if slot_cfg is not False:
        slot_cfg = slot_cfg or {}
        slot_dims = _dimensions(slot_cfg.get("dimensions"), (1.6, 1.0, 0.18))
        offset = _vec3(slot_cfg.get("offset"), (0.0, -1.05, 0.12))
        yaw_rad = math.radians(yaw)
        rotated_offset = [
            offset[0] * math.cos(yaw_rad) - offset[1] * math.sin(yaw_rad),
            offset[0] * math.sin(yaw_rad) + offset[1] * math.cos(yaw_rad),
            offset[2],
        ]
        slot_center = [end[0] + rotated_offset[0], end[1] + rotated_offset[1], end[2] + rotated_offset[2]]
        objects.append(
            {
                "type": "slot",
                "name": slot_cfg.get("name") or f"{name_prefix}_EndSlot",
                "dimensions": slot_dims,
                "position": _position_dict(slot_center),
                "rotation": float(slot_cfg.get("rotation", yaw)),
                "surface_offset_z": float(slot_cfg.get("surface_offset_z", slot_cfg.get("top_surface_offset_z", 0.0))),
                "package_clearance_z": float(slot_cfg.get("package_clearance_z", 0.002)),
            }
        )
    return objects


def _expand_palletizing_area(
    area_config: Dict[str, Any],
    area_index: int,
    seed: Optional[int],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    area = _area(area_config.get("area") or area_config)
    center = area["center"]
    objects: List[Dict[str, Any]] = []
    manipulators: List[Dict[str, Any]] = []

    arm_cfg = area_config.get("robot_arm")
    if arm_cfg:
        arm_position = _add(center, _vec3(arm_cfg.get("relative_position") or arm_cfg.get("offset"), (0.0, 0.0, 0.0)))
        manipulators.append(
            {
                "name": arm_cfg.get("name", f"PalletArm_{area_index + 1}"),
                "position": arm_position,
                "orientation": _orientation(arm_cfg),
            }
        )

    rng = random.Random((seed if seed is not None else 0) + area_index * 2017)
    for pallet_index, pallet in enumerate(_as_list(area_config.get("pallets") or area_config.get("crates")), 1):
        rel = _vec3(pallet.get("relative_position") or pallet.get("offset"), (0.0, 0.0, 0.0))
        position = _add(center, rel)
        name = pallet.get("name") or f"Pallet_{area_index + 1}_{pallet_index}"
        dims = _dimensions(pallet.get("dimensions"), (1.2, 1.0, 0.25))
        rotation = float(pallet.get("rotation", 0.0))
        objects.append(
            {
                "type": "crate",
                "name": name,
                "dimensions": dims,
                "position": _position_dict(position),
                "rotation": rotation,
                "surface_offset_z": float(pallet.get("surface_offset_z", pallet.get("top_surface_offset_z", 0.0))),
                "package_clearance_z": float(pallet.get("package_clearance_z", 0.005)),
                "children": _packages_for_pallet(pallet.get("packages", {}), name, position, rotation, dims, rng),
            }
        )

    return objects, manipulators


def _expand_pallet_transport_target(target: Dict[str, Any], target_index: int) -> Dict[str, Any]:
    if "area" in target:
        area = _area(target.get("area") or {})
        position = area["center"]
        dims = _dimensions(area["size"], (2.4, 1.8, 0.05))
    else:
        position = _vec3(target.get("position") or target.get("center"), (0.0, 0.0, 0.0))
        dims = _dimensions(target.get("size") or target.get("dimensions"), (2.4, 1.8, 0.05))

    return {
        "type": "pallet_transport_target",
        "name": target.get("name", f"PalletDropZone_{target_index + 1}"),
        "position": _position_dict(position),
        "dimensions": dims,
        "rotation": float(target.get("rotation", 0.0)),
        "color": _vec3(target.get("color"), (1.0, 0.05, 0.02)),
        "dash_length": float(target.get("dash_length", 0.35)),
        "gap_length": float(target.get("gap_length", 0.18)),
        "line_width": float(target.get("line_width", 0.035)),
    }


def _packages_for_pallet(
    package_cfg: Dict[str, Any],
    pallet_name: str,
    pallet_position: List[float],
    pallet_rotation: float,
    pallet_dimensions: Dict[str, float],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    if not package_cfg:
        return []

    specs: List[Dict[str, Any]] = []
    inferred_mode = "enumerated" if "items" in package_cfg else "random"
    mode = str(package_cfg.get("mode", inferred_mode)).lower()
    if mode in ("enum", "enumerated", "manual"):
        specs = [dict(item) for item in _as_list(package_cfg.get("items"))]
    else:
        count = int(package_cfg.get("count", 0))
        rows = int(package_cfg.get("rows", 2))
        columns = int(package_cfg.get("columns", 2))
        sizes = list(package_cfg.get("sizes", ["medium"]))
        for idx in range(count):
            row = idx // max(columns, 1)
            col = idx % max(columns, 1)
            if row >= rows:
                break
            specs.append({"row": row, "column": col, "size": rng.choice(sizes)})

    objects = []
    for idx, spec in enumerate(specs, 1):
        dimensions = _package_dimensions(spec)
        local = _pallet_local_position(spec, pallet_dimensions, package_cfg)
        world = _local_to_world(pallet_position, local, pallet_rotation)
        size_name = str(spec.get("size", "custom"))
        objects.append(
            {
                "type": "box",
                "name": spec.get("name") or f"Box_{pallet_name}_{size_name[:1].upper()}_{idx}",
                "dimensions": dimensions,
                "position": _position_dict(world),
                "rotation": pallet_rotation,
                "crate_name": pallet_name,
            }
        )
    return objects


def _expand_robot_arms(robot_arms: Any) -> List[Dict[str, Any]]:
    agents = []
    for idx, arm in enumerate(_as_list(robot_arms), 1):
        agents.append(
            {
                "name": arm.get("name", f"RobotArm_{idx}"),
                "position": _vec3(arm.get("position"), (0.0, 0.0, 0.0)),
                "orientation": _orientation(arm),
            }
        )
    return agents


def _expand_vehicles(vehicles: Dict[str, Any], dynamic_agents: Dict[str, List[Dict[str, Any]]]) -> None:
    if not vehicles:
        return

    for idx, item in enumerate(_as_list(vehicles.get("shuttles") or vehicles.get("sorting_carts")), 1):
        dynamic_agents["shuttles"].append(_dynamic_agent(item, f"shuttle{idx}"))

    for idx, item in enumerate(_as_list(vehicles.get("transporters") or vehicles.get("carriers")), 1):
        dynamic_agents["transporters"].append(_dynamic_agent(item, f"transporter{idx}"))


def _dynamic_agent(item: Dict[str, Any], default_name: str) -> Dict[str, Any]:
    return {
        "name": item.get("name", default_name),
        "position": _vec3(item.get("position"), (0.0, 0.0, 0.0)),
        "orientation": _orientation(item),
    }


def _matches_target(
    spec: Dict[str, Any],
    shelf_name: str,
    shelf_index: int,
    row_index: int,
    column_index: int,
    row_key: str = "row_index",
) -> bool:
    if "shelf_name" in spec and not _target_matches(spec["shelf_name"], shelf_name):
        return False
    if "shelf_index" in spec and not _target_matches(spec["shelf_index"], shelf_index):
        return False
    if row_key in spec and not _target_matches(spec[row_key], row_index):
        return False
    if "row_index" in spec and not _target_matches(spec["row_index"], row_index):
        return False
    if "column_index" in spec and not _target_matches(spec["column_index"], column_index):
        return False
    return True


def _target_matches(value: Any, current: Any) -> bool:
    if isinstance(value, (list, tuple, set)):
        return "*" in value or "all" in value or current in value
    return value in (current, "*", "all")


def _package_local_position(spec: Dict[str, Any], shelf_dimensions: Dict[str, float], package_cfg: Dict[str, Any]) -> List[float]:
    if "relative_position" in spec:
        return _vec3(spec["relative_position"], (0.0, 0.0, 0.0))

    slot = int(spec.get("slot", 0))
    level = int(spec.get("level", 1))
    margin = float(package_cfg.get("slot_margin", 0.8))
    slot_spacing = float(package_cfg.get("slot_spacing", 0.55))
    base_z = float(package_cfg.get("base_z", 1.2))
    level_gap = float(package_cfg.get("level_gap", 1.3))
    x = -shelf_dimensions["length"] / 2.0 + margin + slot * slot_spacing
    y = float(spec.get("offset_y", 0.0))
    z = base_z + max(level - 1, 0) * level_gap
    return [x, y, z]


def _pallet_local_position(spec: Dict[str, Any], pallet_dimensions: Dict[str, float], package_cfg: Dict[str, Any]) -> List[float]:
    if "relative_position" in spec:
        return _vec3(spec["relative_position"], (0.0, 0.0, pallet_dimensions["height"]))

    rows = int(package_cfg.get("rows", 2))
    columns = int(package_cfg.get("columns", 2))
    row = int(spec.get("row", 0))
    column = int(spec.get("column", 0))
    x_step = pallet_dimensions["length"] / max(columns, 1)
    y_step = pallet_dimensions["width"] / max(rows, 1)
    x = -pallet_dimensions["length"] / 2.0 + x_step / 2.0 + column * x_step
    y = -pallet_dimensions["width"] / 2.0 + y_step / 2.0 + row * y_step
    z = float(spec.get("z", pallet_dimensions["height"] + 0.15))
    return [x, y, z]


def _package_dimensions(spec: Dict[str, Any]) -> Dict[str, float]:
    if "dimensions" in spec:
        return _dimensions(spec["dimensions"], (0.3, 0.3, 0.5))
    return dict(PACKAGE_SIZES.get(str(spec.get("size", "medium")).lower(), PACKAGE_SIZES["medium"]))


def _default_slot_count(length: float, package_cfg: Dict[str, Any]) -> int:
    margin = float(package_cfg.get("slot_margin", 0.8))
    spacing = float(package_cfg.get("slot_spacing", 0.55))
    return max(1, int(math.floor(max(length - margin * 2.0, 0.1) / max(spacing, 0.001))) + 1)


def _local_to_world(origin: List[float], local: List[float], yaw_deg: float) -> List[float]:
    yaw = math.radians(yaw_deg)
    c = math.cos(yaw)
    s = math.sin(yaw)
    return [
        origin[0] + local[0] * c - local[1] * s,
        origin[1] + local[0] * s + local[1] * c,
        origin[2] + local[2],
    ]


def _rotated_footprint(length: float, width: float, yaw_deg: float) -> Tuple[float, float]:
    yaw = math.radians(yaw_deg)
    c = abs(math.cos(yaw))
    s = abs(math.sin(yaw))
    return length * c + width * s, length * s + width * c


def _segments_for_length(length: float) -> int:
    if length <= 6.0:
        return 1
    if length <= 10.0:
        return 2
    if length <= 14.0:
        return 3
    return 4


def _rotation_for_direction(direction: str) -> float:
    return 90.0 if str(direction).lower() in ("y", "vertical", "north_south") else 0.0


def _area(value: Dict[str, Any]) -> Dict[str, List[float]]:
    return {
        "center": _vec3(value.get("center") or value.get("position"), (0.0, 0.0, 0.0)),
        "size": _vec3(value.get("size") or value.get("dimensions"), (10.0, 10.0, 0.0)),
    }


def _dimensions(value: Any, default: Tuple[float, float, float]) -> Dict[str, float]:
    vec = _vec3(value, default)
    return {"length": vec[0], "width": vec[1], "height": vec[2]}


def _orientation(item: Dict[str, Any]) -> List[float]:
    if "orientation" in item:
        return _vec3(item["orientation"], (0.0, 0.0, 0.0))
    return [0.0, 0.0, float(item.get("rotation", 0.0))]


def _vec3(value: Any, default: Iterable[float]) -> List[float]:
    default_list = list(default)
    if value is None:
        return [float(default_list[0]), float(default_list[1]), float(default_list[2])]
    if isinstance(value, dict):
        return [
            float(value.get("x", value.get("length", default_list[0]))),
            float(value.get("y", value.get("width", default_list[1]))),
            float(value.get("z", value.get("height", default_list[2]))),
        ]
    if isinstance(value, (list, tuple)):
        vals = list(value) + default_list
        return [float(vals[0]), float(vals[1]), float(vals[2])]
    raise TypeError(f"Expected vector as dict/list, got {type(value).__name__}")


def _vec2(value: Any, default: Tuple[float, float]) -> Tuple[float, float]:
    if value is None:
        return float(default[0]), float(default[1])
    if isinstance(value, dict):
        return float(value.get("x", default[0])), float(value.get("y", default[1]))
    if isinstance(value, (list, tuple)):
        vals = list(value) + list(default)
        return float(vals[0]), float(vals[1])
    scalar = float(value)
    return scalar, scalar


def _position_dict(position: Iterable[float]) -> Dict[str, float]:
    vals = list(position)
    return {"x": float(vals[0]), "y": float(vals[1]), "z": float(vals[2])}


def _add(a: List[float], b: List[float]) -> List[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _positive_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    number = int(value)
    return number if number > 0 else None
