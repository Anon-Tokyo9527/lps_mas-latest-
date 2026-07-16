"""
获取场景对象示例

调用 API 获取场景所有物体，存到 SCENE_OBJECTS 变量中供其他模块使用。

使用前确保已运行 start_scene_api.py 启动 API 服务。
"""

import requests
import json
import os

API_URL = os.getenv("SCENE_API_URL") or os.getenv("ISAAC_SCENE_API_URL") or "http://localhost:60123/api/scene/objects"
OUTPUT_FILE = "scene_objects.json"

# 场景对象数据（供其他模块使用）
SCENE_OBJECTS = None


def fetch_scene_objects():
    """从 API 获取场景对象"""
    global SCENE_OBJECTS
    try:
        response = requests.get(API_URL, timeout=10)
        response.raise_for_status()
        SCENE_OBJECTS = response.json()
        print(f"获取成功，共 {len(SCENE_OBJECTS)} 个对象")
        return SCENE_OBJECTS
    except Exception as e:
        print(f"获取失败: {e}")
        return None


def save_to_json(file_path=OUTPUT_FILE):
    """将场景对象保存到 JSON 文件"""
    if SCENE_OBJECTS is None:
        print("没有数据，请先调用 fetch_scene_objects()")
        return
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(SCENE_OBJECTS, f, ensure_ascii=False, indent=2)
    print(f"已保存到 {file_path}")


# 运行时自动获取并保存
if __name__ == "__main__":
    fetch_scene_objects()
    save_to_json()

