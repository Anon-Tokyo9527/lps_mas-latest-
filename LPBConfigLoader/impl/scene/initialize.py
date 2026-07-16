"""
Isaac Sim 场景一键初始化脚本

功能：
- 构建场景（优先调用 new_isaac_scene_exporter.build_scene；若不存在则回退 isaac_scene_exporter.build_scene）
- 启动 API 服务（调用 start_scene_api.start_scene_api）

使用方法（在 Isaac Sim Script Editor 中）：
    exec(open(r"C:/Users/ASUS/Desktop/scene/initialize.py", encoding="utf-8").read())

或者只调用 initialize() 函数：
    from initialize import initialize
    initialize()
"""

import sys
import os
from pathlib import Path

# ===== 自动检测项目根目录 =====
def _detect_base_dir() -> str:
    def _is_project_root(p: Path) -> bool:
        return (p / "assets").exists() and (p / "warehouse_scenes").exists()

    # 1) 优先使用环境变量
    env = os.getenv("SCENE_BASE_DIR") or os.getenv("ISAAC_SCENE_BASE_DIR")
    if env:
        try:
            p = Path(env).expanduser().resolve()
            if _is_project_root(p):
                return str(p)
        except Exception:
            pass

    # 2) 如果脚本在项目根目录
    try:
        p = Path(__file__).resolve().parent
        if _is_project_root(p):
            return str(p)
    except Exception:
        pass

    # 3) 常见位置探测（Windows：桌面项目）
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

    # 4) 从 cwd 向上寻找项目根
    cwd = Path(os.getcwd()).resolve()
    for p in [cwd] + list(cwd.parents):
        if _is_project_root(p):
            return str(p)

    return str(cwd)


BASE_DIR = _detect_base_dir().replace("\\", "/")

# 确保项目目录在 sys.path 中
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def initialize(
    json_file: str = None,
    start_api: bool = True,
    reload_modules: bool = True,
    api_port: int = 60123,
    exporter: str = None,
):
    """
    一键初始化 Isaac Sim 场景和 API 服务

    Args:
        json_file: 场景布局 JSON 文件路径（相对于 BASE_DIR 或绝对路径）
                   默认使用 exporter 模块内部的 JSON_FILE
        start_api: 是否启动 API 服务（默认 True）
        reload_modules: 是否强制重新加载模块（Script Editor 反复运行时推荐 True）
        api_port: API 服务端口（默认 60123）
        exporter: 选择场景构建脚本：
                  - "new": 强制使用 new_isaac_scene_exporter
                  - "old": 强制使用 isaac_scene_exporter
                  - None/"auto": 自动（优先 new）

    Returns:
        dict: 包含初始化状态的字典
            - scene_built: 场景是否构建成功
            - api_started: API 是否启动成功
            - objects_count: 场景对象数量
            - api_url: API 服务地址
    """
    import importlib

    result = {
        "scene_built": False,
        "api_started": False,
        "objects_count": 0,
        "api_url": None,
    }

    print("=" * 60)
    print("Isaac Sim 场景一键初始化")
    print("=" * 60)

    def _pick_exporter_module(prefer: str = None):
        prefer = (prefer or "").strip().lower()
        if prefer in ("new", "new_isaac_scene_exporter", "new_exporter"):
            import new_isaac_scene_exporter as m
            return m
        if prefer in ("old", "isaac_scene_exporter", "exporter"):
            import isaac_scene_exporter as m
            return m

        # auto: 优先 new，没有再回退 old
        try:
            import new_isaac_scene_exporter as m
            return m
        except Exception:
            import isaac_scene_exporter as m
            return m

    # ===== Step 1: 构建场景 =====
    print("\n[1/2] 构建场景...")
    try:
        # 清理旧模块缓存（Script Editor 反复运行时需要）
        if reload_modules:
            for k in list(sys.modules.keys()):
                if k == "new_isaac_scene_exporter" or k.startswith("new_isaac_scene_exporter."):
                    del sys.modules[k]
                if k == "isaac_scene_exporter" or k.startswith("isaac_scene_exporter."):
                    del sys.modules[k]
                if k == "func" or k.startswith("func."):
                    del sys.modules[k]
            importlib.invalidate_caches()

        scene_exporter = _pick_exporter_module(exporter)
        if reload_modules:
            scene_exporter = importlib.reload(scene_exporter)

        # 如果指定了自定义 JSON 文件，更新配置
        if json_file:
            # new_isaac_scene_exporter / isaac_scene_exporter 都有 JSON_FILE 变量
            # new_isaac_scene_exporter 也支持环境变量 NEW_SCENE_JSON，但这里直接覆盖模块变量即可
            setattr(scene_exporter, "JSON_FILE", json_file)
            try:
                # 兼容 new_isaac_scene_exporter 的环境变量读取逻辑（可选）
                os.environ["NEW_SCENE_JSON"] = str(json_file)
            except Exception:
                pass
            print(f"  使用布局文件: {json_file} (exporter={getattr(scene_exporter, '__name__', 'unknown')})")

        # 构建场景
        scene_exporter.build_scene()

        result["scene_built"] = True
        result["objects_count"] = len(getattr(scene_exporter, "LAST_SCENE_OBJECTS", {}) or {})
        print(f"  ✓ 场景构建成功，共 {result['objects_count']} 个对象")

    except Exception as e:
        print(f"  ✗ 场景构建失败: {e}")
        import traceback
        traceback.print_exc()

    # ===== Step 2: 启动 API 服务 =====
    if start_api:
        print("\n[2/2] 启动 API 服务...")
        try:
            # 不删除 start_scene_api 模块缓存，保留服务器状态变量
            # 只清理 isaac_scene_api（API 逻辑）
            if reload_modules:
                for k in list(sys.modules.keys()):
                    if k == "isaac_scene_api":
                        del sys.modules[k]
                importlib.invalidate_caches()

            import start_scene_api

            # 检查服务是否已在运行
            api_already_running = (
                hasattr(start_scene_api, '_SERVER_THREAD') 
                and start_scene_api._SERVER_THREAD 
                and start_scene_api._SERVER_THREAD.is_alive()
            )

            if api_already_running:
                print("  ℹ API 服务已在运行中，无需重复启动")
                result["api_started"] = True
                result["api_url"] = f"http://localhost:{api_port}"
            else:
                # 启动新服务
                start_scene_api.start_scene_api(reload_api=reload_modules)
                result["api_started"] = True
                result["api_url"] = f"http://localhost:{api_port}"
                print(f"  ✓ API 服务已启动: {result['api_url']}")

        except Exception as e:
            print(f"  ✗ API 服务启动失败: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("\n[2/2] 跳过 API 服务启动（start_api=False）")

    # ===== 完成 =====
    print("\n" + "=" * 60)
    if result["scene_built"] and (not start_api or result["api_started"]):
        print("✓ 初始化完成！")
    else:
        print("⚠ 初始化部分完成（请检查上方错误信息）")

    if result["api_started"]:
        print(f"\nAPI 服务地址: {result['api_url']}")
        print(f"API 文档: {result['api_url']}/docs")
        print("\n可用接口:")
        print("  GET /api/scene/objects - 获取所有场景对象")
        print("  GET /api/scene/objects/{name} - 获取指定对象")
        print("\n停止服务:")
        print("  from start_scene_api import stop_scene_api")
        print("  stop_scene_api()")

    print("=" * 60)

    return result


def shutdown():
    """
    关闭 API 服务

    使用方法：
        from initialize import shutdown
        shutdown()
    """
    try:
        import start_scene_api
        start_scene_api.stop_scene_api(wait=True)
        print("✓ API 服务已停止")
    except Exception as e:
        print(f"停止 API 服务时出错: {e}")


def restart(json_file: str = None):
    """
    重启场景和 API 服务

    Args:
        json_file: 可选，指定新的场景布局文件

    使用方法：
        from initialize import restart
        restart()
        # 或指定新布局
        restart("warehouse_scenes/l_shape_layout.json")
    """
    shutdown()
    return initialize(json_file=json_file, reload_modules=True)


# ===== 模块直接运行时自动初始化 =====
# 支持通过环境变量或全局变量控制行为
_action = str(globals().get("INIT_ACTION", os.getenv("INIT_ACTION", "init"))).strip().lower()

if _action in ("stop", "shutdown", "close"):
    shutdown()
elif _action in ("restart", "reload"):
    restart()
elif _action in ("init", "start", "run"):
    initialize()
elif _action == "none":
    # 只导入模块，不执行任何操作
    pass
else:
    # 默认：初始化
    initialize()

