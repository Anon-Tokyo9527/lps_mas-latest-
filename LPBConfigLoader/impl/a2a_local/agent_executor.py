# agent_executor.py
from __future__ import annotations

import json
from typing import Any, Dict

# ========== A2A 相关代码已注释 ==========
# from a2a.server.agent_execution import AgentExecutor, RequestContext
# from a2a.server.events import EventQueue
# from a2a.utils import new_agent_text_message

from .agent import AgentPool,GlobalOrchestratorAgent,ShuttleAgent,RoboticArmAgent,WorkerAgent


class HelloWorldAgentExecutor(AgentExecutor):
    """Executor that preprocesses message->text, builds Task(s), and loops through tasks."""
    print("LOADED EXECUTOR FROM:", __file__, flush=True)
    def __init__(self,test_env_instance):
        super().__init__()
        self.pool = AgentPool()
        self.pool.register(ShuttleAgent("shuttle", test_env=test_env_instance))# # 将 TestEnv 实例传给 ShuttleAgent
        self.pool.register(RoboticArmAgent("arm"))
        # self.pool.register(AGVAgent("agv"))
        self.pool.register(WorkerAgent("worker"))
        self.global_agent = GlobalOrchestratorAgent("global_orchestrator", agent_pool=self.pool)

    # 这是agent池的形式
    # pool._agents == {
    #     "shuttle": < ShuttleAgent >,agent名字，真实的agent
    # "arm": < ArmAgent >,
    # "agv": < AgvAgent >,
    # "worker": < WorkerAgent >,
    # }

    def _preprocess_message(self, context: RequestContext) -> Dict[str, Any]:
        """
            从 A2A context.message 中读取 client 发来的 JSON 文本
            并返回解析后的 dict
            """
        import json

        # 1. 拿到 client 发送的原始 JSON 字符串
        message = context.message
        if not message or not message.parts:
            raise ValueError("Empty A2A message")

        text = message.parts[0].root.text
        if not isinstance(text, str):
            raise ValueError("Message part is not text")

        # 2. JSON → dict
        data = json.loads(text)

        return data

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 1) 只从 context 取框架解析好的 message -> normalized
        normalized = self._preprocess_message(context)

        # 2) 交给全局智能体执行（全局智能体内部：解析 task JSON -> steps -> 逐步调 agent API）step1 = task.step1

        result = await self.global_agent.step(normalized)

        # 把结果发回客户端
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(result, ensure_ascii=False))
        )
        print("start server")


    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
