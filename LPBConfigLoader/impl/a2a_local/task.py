# task.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class Step:
    """A single sub-step within a Task."""
    step_id: str
    step_name: str
    required_agents: List[str]
    dependencies: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_name": self.step_name,
            "required_agents": list(self.required_agents),
            "dependencies": list(self.dependencies),
            "payload": dict(self.payload),
        }


@dataclass
class Task:
    """
    Task extracted from an incoming A2A message.

    Requirements supported:
    1) Extracted from incoming message text.
    2) Describes: number of sub-steps + required agents per sub-step.
    3) Supports parsing multiple tasks from a single message (loop processing).
    """
    task_id: str
    goal: str
    steps: List[Step] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def num_steps(self) -> int:
        return len(self.steps)

    def agents_for_step(self, step_id: Union[str, int]) -> List[str]:
        sid = str(step_id)
        for s in self.steps:
            if s.step_id == sid:
                return s.required_agents
        return []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "num_steps": self.num_steps,
            "steps": [s.to_dict() for s in self.steps],
        }

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Any]:
        try:
            return json.loads(text)
        except Exception:
            return None

    @classmethod
    #def from_text(cls, text: str) -> "Task | List[Task]":
    def from_text(cls, text: str) -> "Task":  #第一次json.loads
        """
        Accepts:
        - single task json: {task_id, goal, steps:[...]}
        - multi task json:  {tasks:[{...},{...}]}
        - docx atomic-cmd list json: {task_id, goal, tasks:[{header,body}, ...]}
          (kept as one Task with empty steps; planner will generate steps)
        - plain text -> single Task with one default worker step
        """
        parsed = cls._try_parse_json(text)#尝试把text当字符串解析成python对象（dict/list/str/number 等）

        # if isinstance(parsed, dict) and "tasks" in parsed and isinstance(parsed["tasks"], list):
        #     items = parsed["tasks"]
        #
        #     # If items look like explicit tasks (goal/steps), return a list[Task]
        #     looks_like_task = lambda x: isinstance(x, dict) and ("goal" in x or "steps" in x or "task_id" in x)
        #     if all(looks_like_task(x) for x in items):
        #         tasks: List[Task] = []
        #         for it in items:
        #             tasks.append(cls._from_task_dict(it, raw=it))
        #         return tasks
        #
        #     # Otherwise treat as atomic-cmd list: represent as a single Task; planner will fill steps
        #     return cls(
        #         task_id=str(parsed.get("task_id", "TASK_ATOMIC_LIST")),
        #         goal=str(parsed.get("goal", "atomic_cmd_list")),
        #         steps=[],
        #         raw=parsed,
        #     )

        if isinstance(parsed, dict) and ("goal" in parsed or "steps" in parsed or "task_id" in parsed):
            return cls._from_task_dict(parsed, raw=parsed)###这里的raw是我的json保持原样

        # plain text fallback
        # return cls(
        #     task_id="TASK_TEXT",
        #     goal=text.strip()[:200] if isinstance(text, str) else str(text),
        #     steps=[Step(step_id="step_1", step_name="default_work", required_agents=["worker"])],
        #     raw={"text": text},
        # )


####解析显示step，我自己的json是隐式的step时我可以使用下面的函数设计
    @classmethod  ##d，已经 parse 过的 dict（来自 json.loads）
    def _from_task_dict(cls, d: Dict[str, Any], raw: Optional[Dict[str, Any]] = None) -> "Task":
        task_id = str(d.get("task_id") or d.get("id") or d.get("taskId") or "TASK_001")
        goal = str(d.get("goal") or d.get("task") or d.get("description") or "unspecified")
        steps_raw = d.get("steps") or []
        steps: List[Step] = []

        if isinstance(steps_raw, list):
            for i, s in enumerate(steps_raw):
                if not isinstance(s, dict):   # 非 dict 的 step 直接跳过
                    continue
                step_id = str(s.get("step_id") or s.get("id") or s.get("stepId") or f"step_{i+1}")
                step_name = str(s.get("step_name") or s.get("name") or s.get("stepName") or step_id)
                required_agents = list(s.get("required_agents") or s.get("agents") or [])
                # if not required_agents:
                #     required_agents = ["worker"]
                deps = list(s.get("dependencies") or [])
                payload = dict(s.get("payload") or {})
                steps.append(Step(step_id=step_id, step_name=step_name, required_agents=required_agents, dependencies=deps, payload=payload))

        return cls(task_id=task_id, goal=goal, steps=steps, raw=raw or d)#raw不参与执行，出错时用于回溯

    @classmethod
    def many_from_normalized(cls, normalized: Dict[str, Any]) -> List["Task"]:
        """normalized = {task_type, inputs, task} (from executor preprocess)"""
        task_text = normalized.get("task", "")  ####此处的task对应这md文件中的整体json字符串形式呈现
        if isinstance(task_text, str) and task_text.strip():
            parsed = cls.from_text(task_text)##真正处理json信息的功能
            if isinstance(parsed, list):
                return parsed
            return [parsed]

        # fallback
        return [cls(task_id="TASK_FALLBACK", goal="fallback", steps=[Step("step_1","default_work",["worker"])], raw={"inputs": normalized.get("inputs")})]
