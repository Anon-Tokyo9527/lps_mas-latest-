import json
import numpy as np

from google import genai
from google.genai import types


class LM:
    """
    大语言模型 (LLM) 统一调用接口包装类。

    该类旨在为上层智能体提供统一的 'generate' 接口，屏蔽不同模型供应商 (Gemini, OpenAI, Claude等) 的 API 差异和参数配置。
    """

    def __init__(self, provider="gemini", model_name="gemini-3-flash-preview", api_key=None, sys_prompt=None):
        """
        初始化 LLM 客户端。

        Args:
            provider (str): 模型供应商。
            model_name (str): 具体模型。
            api_key (str): API 密钥。
            sys_prompt (str): 系统提示词。
        """
        self.provider = provider
        self.model_name = model_name
        self.sys_prompt = sys_prompt

        if self.provider == "gemini":
            self.client = genai.Client(api_key=api_key)

    def generate(self, messages):
        """
        统一生成接口。

        Args:
            messages (str): 提示词。

        Returns:
            str: 模型返回的内容。
        """
        try:
            if self.provider == "gemini":
                return self._call_gemini(messages)
        except Exception as e:
            print(f"[LM Error] Failed to generate response: {e}")
            return ""

    def _call_gemini(self, messages):
        """
        Gemini 模型的具体调用逻辑。
        """
        response = self.client.models.generate_content(
            model=self.model_name,
            config=types.GenerateContentConfig(
                system_instruction=self.sys_prompt),
            contents=messages
        )

        return response.text
