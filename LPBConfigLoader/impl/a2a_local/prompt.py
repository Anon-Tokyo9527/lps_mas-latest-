# helloworld/agent/prompt.py
from abc import ABC, abstractmethod
from typing import Dict, Any

class BasePrompt(ABC):
    def __init__(self):
        self.static_info: Dict[str, Any] = {}
        self.dynamic_info: Dict[str, Any] = {}

    def update_static(self, **kwargs):
        self.static_info.update(kwargs)

    def update_dynamic(self, **kwargs):
        self.dynamic_info.update(kwargs)

    @abstractmethod
    def build_prompt(self) -> str:
        pass


class StructuredPrompt(BasePrompt):
    def build_prompt(self) -> str:
        sections = []
        if "role" in self.static_info:
            sections.append(f"[角色信息]\n{self.static_info['role']}")
        if "task" in self.static_info:
            sections.append(f"[任务信息]\n{self.static_info['task']}")

        for key, label in [
            ("observation", "观察状态"),
            ("self_state", "自身状态"),
            ("plan", "规划信息"),
            ("exec_state", "任务执行状态"),
        ]:
            if key in self.dynamic_info:
                sections.append(f"[{label}]\n{self.dynamic_info[key]}")

        return "\n\n".join(sections)
