# agent.py
from __future__ import annotations
from typing import Tuple
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
# from task import Task, Step
import httpx
import asyncio

class AgentPool:

    """A registry of available agents."""
    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: "BaseAgent") -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> "BaseAgent":
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' not found. Available: {list(self._agents.keys())}")
        return self._agents[name]

    def list(self) -> List[str]:
        return list(self._agents.keys())##返回当前有哪些agent


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name
        self.state: Dict[str, Any] = {}


class ShuttleAgent(BaseAgent):
    def __init__(self, name: str = "shuttle", test_env=None):
        super().__init__(name)
        # 持有 TestEnv 实例的引用
        self.test_env = test_env
        self._current_plan = None
    async def pickup(self, command: dict):
        """
        本地调用模式：直接驱动仿真侧的 shuttle
        """
        print(f"[Internal A2A] Processing command for: {self.name}")

        # 1. 获取当前仿真状态快照 (需根据场景实际需求获取数据)
        # 这里假设你有一套获取 sim_state 的机制，或者传一个简化的占位符
        sim_state = ["在环境中获取"] 

        # 2. 同步调用本地 shuttle 的 reason 函数 (LLM 生成计划)
        # 注意：此处会卡顿界面几秒，除非将 reason 包装在线程中执行
        print("[Internal A2A] Starting LLM Reasoning...")
        full_resp, steps = self.test_env.shuttle.reason(sim_state, command)
        self.test_env.current_plan = steps  # 把生成的计划存入 TestEnv 实例
        print(f"[Internal A2A] Plan generated with {len(steps)} steps.")

        # 3. 将生成的计划交给物理实例开始执行
        # 注意：shuttle.step 是逐帧调用的，我们需要在外部循环里不断驱动它
        # 这里的返回值通常用于告知 A2A 框架任务已开始
        return {
            "status": "executing",
            "plan_steps": steps,
            "agent": self.name
        }



class RoboticArmAgent(BaseAgent):
    async def step(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"agent": self.name, "type": "arm", "result": f"Arm handled: {input_data.get('step_name')}"}


# class AGVAgent(BaseAgent):
#     async def step(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
#         return {"agent": self.name, "type": "agv", "result": f"AGV handled: {input_data.get('step_name')}"}


class WorkerAgent(BaseAgent):
    async def step(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"agent": self.name, "type": "worker", "result": f"Worker completed: {input_data.get('step_name')}"}


import json
from typing import Any, Dict, List

AGENT_ROLE_TO_AGENT = {
    "SHUTTLE": "shuttle",
    "ROBOTIC_ARM": "arm",
    "AGV": "agv",
    "WORK": "worker",
}

ACTION_TO_AGENT = {
    "RETRIEVE_BIN": "shuttle",
    "STORE_BIN": "shuttle",
    "PALLETIZE": "arm",
    "LIFT_TRANSPORT": "agv",
}

class GlobalOrchestratorAgent(BaseAgent):
    """Global orchestrator that manages Task and dispatches step execution via AgentPool."""

    def __init__(self, name: str, agent_pool: AgentPool):
        super().__init__(name=name)
        self.pool = agent_pool
        self.env = None
        # action_type -> (agent_name, api_method)
        self.ACTION_API: Dict[str, Tuple[str, str]] = {
            "RETRIEVE_BIN": ("shuttle", "pickup"),
            "STORE_BIN": ("shuttle", "store"),
            "PALLETIZE": ("arm", "palletize"),
            "LIFT_TRANSPORT": ("agv", "lift_transport"),
        }

        # agent_role -> agent_name（atomic cmd 用）
        self.ROLE_TO_AGENT: Dict[str, str] = {
            "SHUTTLE": "shuttle",
            "ROBOTIC_ARM": "arm",
            "AGV": "agv",
            "WORK": "worker",
            "WORKER": "worker",
        }
    def _parse_steps(self, task_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        把 task_dict 解析成一个 step 列表。
        atomic cmd list：
           {"task_id":..., "tasks":[{"header":{...},"body":{"agent_role":"SHUTTLE","action_type":"RETRIEVE_BIN","payload":{...}}}, ...]}
        """
        # if not isinstance(task_dict, dict):
        #     return [self._default_worker_step()]

        # atomic cmd list：把每个 cmd 转成 step
        atomic_list = task_dict.get("tasks")
        if isinstance(atomic_list, list) and atomic_list:
            steps_out: List[Dict[str, Any]] = []  ##初始化step列表
            prev_id = None  # 存前一个依赖
            for i, cmd in enumerate(atomic_list, start=1):  # 遍历每条 atomic cmd
                if not isinstance(cmd, dict):
                    continue
                header = cmd.get("header")
                body = cmd.get("body")
                agent_role = body.get("agent_role")
                action_type = body.get("action_type")
                payload = body.get("payload")

                step_id = (payload.get("task_id") if isinstance(payload, dict) else None)
                ###用于日志，debug
                step_name = f"{agent_role}::{action_type}" if agent_role and action_type else f"step_{i}"

                # required_agents：优先按 action_type 映射，其次按 agent_role 映射
                if action_type in self.ACTION_API:
                    req_agent = self.ACTION_API[action_type][0]
                else:
                    req_agent = self.ROLE_TO_AGENT.get(str(agent_role), "worker")

                deps = []
                if prev_id is not None:
                    deps = [prev_id]  # 串行依赖
                prev_id = step_id

                steps_out.append({
                    "step_id": step_id,
                    "step_name": step_name,
                    "required_agents": [req_agent],
                    "dependencies": deps,
                    # 把 atomic 信息保留进 payload，方便 agent API 使用
                    "command": {
                        "header": header,
                        "body": body,
                    }
                })

            return steps_out
        return []

    async def _call_agent_api(self, agent_name: str, command: Dict[str, Any]) -> Dict[str, Any]:
        """
        直接：pool["shuttle"].pickup(...)
        - 如果 payload 里有 body.action_type，则按 action_type 决定调用哪个 API
        - 否则：对 worker 调 work，其他 agent 找不到就报错
        """
        a2a_body = (command or {}).get("body") or {}
        action_type = a2a_body.get("action_type")

        # 决定要调哪个 api
        api_name = None
        if action_type in self.ACTION_API:
            mapped_agent, mapped_api = self.ACTION_API[action_type]
            # 如果 required_agents 传进来的 agent_name 和映射不一致，也优先用 required_agents 的 agent_name
            api_name = mapped_api

        if api_name is None:# 如果什么api都没调用返回error
            return {"agent": agent_name, "error": f"No API mapping for step"}

        agent = self.pool.get(agent_name)
        fn = getattr(agent, api_name, None)
        if fn is None:
            return {"agent": agent_name, "error": f"Agent has no api '{api_name}'"}
        out = await fn(command)
        return {"agent": agent_name, "api": api_name, "result": out}



    # def execute_task(self, task):
    #     for step in task:
    #         if step.receiver_id == 'shuttle':
    #             self.pool[0].pickup(step.target)


    async def step(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        #1、先生成steps_out列表
        steps_out = self._parse_steps(normalized)
        if not steps_out:
            return {"agent": self.name, "error": "No steps parsed"}
        #2、将处理后的steps_out列表中的第一个step取出,后面的step如法炮制
        required = steps_out[0].get("required_agents")
        print(required)#拿到的agent_name不是一个字符串，下面一行要取该字符串
        if isinstance(required, list):
            agent_name = required[0] if required else None
        result = await self._call_agent_api(agent_name,steps_out[0]["command"])
        return result
    # def _resolve_agent_name(self, logical: str) -> str:
    #     mapping = {"shuttle": "shuttle", "arm": "arm", "agv": "agv", "worker": "worker"}
    #     return mapping.get(logical, logical)
