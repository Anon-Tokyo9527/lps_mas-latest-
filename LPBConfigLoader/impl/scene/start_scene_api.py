"""
在 Isaac Sim 中启动场景信息 API 服务

使用方法：
1. 在 Isaac Sim 的 Script Editor 中运行此脚本（推荐入口）
2. 确保场景已加载（先运行 new_isaac_scene_exporter.py 或 isaac_scene_exporter.py）

说明：
- `isaac_scene_api.py`：FastAPI 服务实现（提供接口逻辑）
- `start_scene_api.py`：Isaac Sim 内启动器（后台线程 + 独立事件循环，避免阻塞 Isaac 主循环）

"""

import sys
import os
import threading
import asyncio
from pathlib import Path

# ===== 自动检测项目根目录（兼容 Script Editor 粘贴运行导致 __file__ 指向临时目录的情况）=====
def _detect_base_dir() -> str:
    def _is_project_root(p: Path) -> bool:
        return (p / "assets").exists() and (p / "warehouse_scenes").exists()

    # 0) 强制指定项目根目录（你要求的绝对路径）
    # 注意：如果你将项目移动到别的目录，需要同步修改这里。
    forced = Path(r"D:\workstation\scene")
    try:
        if _is_project_root(forced):
            return str(forced.resolve())
    except Exception:
        pass

    # 1) 优先使用环境变量
    env = os.getenv("SCENE_BASE_DIR") or os.getenv("ISAAC_SCENE_BASE_DIR")
    if env:
        try:
            p = Path(env).expanduser().resolve()
            if _is_project_root(p):
                return str(p)
        except Exception:
            pass

    # 2) 如果是从文件运行（非临时目录），尝试用脚本所在目录
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

    # 4) 从 cwd 向上寻找项目根（Isaac Sim 环境里 cwd 可能不在项目目录，但仍做兜底）
    cwd = Path(os.getcwd()).resolve()
    for p in [cwd] + list(cwd.parents):
        if _is_project_root(p):
            return str(p)

    # 5) 最后兜底：返回当前工作目录
    return str(cwd)


# 添加项目根目录到 sys.path，确保能 import 本项目模块（如 isaac_scene_api）
BASE_DIR = _detect_base_dir().replace("\\", "/")
if BASE_DIR and BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

def _load_api_app(reload_api: bool = True):
    """
    加载（并可选重载）isaac_scene_api，返回 FastAPI app。

    说明：
    - Script Editor 环境里经常反复改代码并重跑启动脚本
    - 直接 `from isaac_scene_api import app` 会拿到旧引用，导致你以为改了接口但实际没生效
    """
    import importlib
    import isaac_scene_api
    if reload_api:
        try:
            isaac_scene_api = importlib.reload(isaac_scene_api)
        except Exception as e:
            print(f"[start_scene_api] reload isaac_scene_api failed, continue with existing module: {e}")
    return isaac_scene_api.app


_SERVER_THREAD = None
_SERVER_LOOP = None
_UVICORN_SERVER = None


def _run_server_in_thread(app):
    """在独立线程中运行 FastAPI 服务器，创建新的事件循环"""
    import uvicorn
    global _SERVER_LOOP, _UVICORN_SERVER

    # 在独立线程中创建新的事件循环
    # 这样不会与 Isaac Sim 的主事件循环冲突
    loop = asyncio.new_event_loop()
    _SERVER_LOOP = loop
    asyncio.set_event_loop(loop)

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=60123,
        log_level="info"
    )
    server = uvicorn.Server(config)
    # 重要：在 Isaac Sim 这种宿主应用里跑 uvicorn 时，尽量不要安装信号处理器
    # 否则可能影响宿主进程的信号/退出行为（不同版本表现不一致）。
    try:
        server.install_signal_handlers = lambda: None  # type: ignore[attr-defined]
    except Exception:
        pass
    _UVICORN_SERVER = server

    try:
        loop.run_until_complete(server.serve())
    except BaseException as e:
        # 捕获 BaseException，避免 SystemExit/KeyboardInterrupt 等意外影响宿主进程
        print(f"服务器运行错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            loop.close()
        finally:
            _UVICORN_SERVER = None
            _SERVER_LOOP = None


def start_scene_api(reload_api: bool = True):
    """启动 API 服务（后台线程）"""
    global _SERVER_THREAD

    if _SERVER_THREAD and _SERVER_THREAD.is_alive():
        print("Isaac Sim 场景信息 API 服务已在运行中（无需重复启动）。")
        print("如需停止：在 Script Editor 里运行 `stop_scene_api()`。")
        return

    app = _load_api_app(reload_api=reload_api)
    _SERVER_THREAD = threading.Thread(target=_run_server_in_thread, args=(app,), daemon=True)
    _SERVER_THREAD.start()


def restart_scene_api(timeout: float = 2.0):
    """重启 API 服务（优先优雅停止，再启动并 reload 代码）"""
    stop_scene_api(wait=True, timeout=timeout)
    start_scene_api(reload_api=True)


def stop_scene_api(wait: bool = False, timeout: float = 2.0):
    """
    停止 API 服务（优雅退出）

    用法（在 Isaac Sim Script Editor 里运行）：
      stop_scene_api()

    Args:
        wait: 是否等待后台线程退出（可能会短暂阻塞）
        timeout: wait=True 时最多等待秒数
    """
    global _UVICORN_SERVER, _SERVER_LOOP, _SERVER_THREAD

    if not _UVICORN_SERVER or not _SERVER_LOOP:
        print("API 服务当前未运行。")
        return

    _UVICORN_SERVER.should_exit = True
    # 某些情况下需要更强制的退出标记
    try:
        _UVICORN_SERVER.force_exit = True  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        # 唤醒 event loop，让 uvicorn 尽快检测 should_exit
        _SERVER_LOOP.call_soon_threadsafe(lambda: None)
    except Exception:
        pass

    if wait and _SERVER_THREAD:
        _SERVER_THREAD.join(timeout=timeout)
        if _SERVER_THREAD.is_alive():
            print("停止请求已发送，但服务尚未完全退出（可稍等或重启 Isaac Sim）。")
        else:
            print("API 服务已停止。")


# 判断是否应该自动启动
# - 当被 initialize.py 导入时，不自动启动（由 initialize.py 控制）
# - 当直接运行或通过 exec() 执行时，自动启动
def _should_auto_start() -> bool:
    """判断是否应该自动启动服务"""
    # 如果是被 initialize 模块导入，不自动启动
    caller_modules = [m for m in sys.modules.keys() if "initialize" in m]
    if caller_modules:
        return False
    
    # 检查是否设置了跳过自动启动的标志
    if globals().get("SCENE_API_NO_AUTO_START"):
        return False
    if os.getenv("SCENE_API_NO_AUTO_START"):
        return False
    
    return True


# 默认行为：启动服务
# 但支持在 Script Editor 中用 "只停止不启动" 的方式调用：
#   SCENE_API_ACTION = "stop"
#   exec(open(r".../start_scene_api.py").read())
_action = str(globals().get("SCENE_API_ACTION", "start")).strip().lower()

if _action in ("stop", "shutdown", "close"):
    # 只停止，不做任何启动动作（避免竞态/重复启动）
    stop_scene_api(wait=True)
elif _action in ("restart", "reload"):
    restart_scene_api()
elif _action == "none" or not _should_auto_start():
    # 跳过自动启动（被其他模块导入时）
    pass
else:
    # 启动服务器（在后台线程中运行，避免阻塞）
    start_scene_api()

    print("=" * 60)
    print("Isaac Sim 场景信息 API 服务已启动")
    print("=" * 60)
    print("服务地址: http://localhost:60123")
    print("API 文档: http://localhost:60123/docs")
    print("")
    print("可用接口:")
    print("  GET /api/scene/objects - 获取所有场景对象")
    print("  GET /api/scene/objects/{name} - 获取指定对象")
    print("")
    print("使用示例:")
    print("  Python: requests.get('http://localhost:60123/api/scene/objects')")
    print("  curl: curl http://localhost:60123/api/scene/objects")
    print("")
    print("停止服务:")
    print("  在 Script Editor 中运行：stop_scene_api()")
    print("  推荐（只停止不启动）：")
    print('    SCENE_API_ACTION = "stop"')
    print('    exec(open(r"C:/Users/ASUS/Desktop/scene/start_scene_api.py", encoding="utf-8").read())')
    print("=" * 60)

