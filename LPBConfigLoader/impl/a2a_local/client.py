
import asyncio
import json
import logging
import re
import warnings
from pathlib import Path
from uuid import uuid4

import httpx

from a2a.client import A2ACardResolver, A2AClient
from a2a.types import AgentCard, MessageSendParams, SendMessageRequest, SendStreamingMessageRequest
from a2a.utils.constants import EXTENDED_AGENT_CARD_PATH

# ===== 全局：关闭所有与工程无关的日志 =====
import logging
import warnings

# 1. 关闭 a2a / httpx 的 INFO 日志
logging.getLogger("a2a").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)

# 2. 关闭 DeprecationWarning（A2AClient deprecated）
warnings.filterwarnings("ignore", category=DeprecationWarning)

# # 创建一个完全不走代理的传输层
# # proxies 设为 None 表示即使系统开了 VPN，也不走 VPN
# async_client = httpx.AsyncClient(
#     proxies={"http://": None, "https://": None},
#     timeout=120.0  #保留这个长超时，给 Gemini 推理留时间
# )

# # 使用这个定制的 client
# client = A2AClient(base_url="http://localhost:9999", httpx_client=async_client)


def load_testcase_json_from_md(md_file: Path) -> str:
    """
    Extract ALL ```json ... ``` blocks from markdown:
    - Validate each via json.loads
    - Prefer a dict with `tasks` as list and len(tasks) >= 5 (your 5-step testcase)
    - Fallback to any dict with `tasks` as list
    - Final fallback: first valid JSON block
    Return a canonical JSON string (json.dumps with ensure_ascii=False).
    """
    text = md_file.read_text(encoding="utf-8")

    blocks = re.findall(r"```json\s*(.*?)\s*```", text, flags=re.S | re.I)
    if not blocks:
        raise ValueError(f"No JSON code block found in: {md_file}")

    candidates = []
    for b in blocks:
        b = b.strip()
        try:
            obj = json.loads(b)
            candidates.append(obj)#把能解析成功的加入 candidates
        except Exception:
            continue

    if not candidates:
        raise ValueError(f"Found json fences but none is valid JSON in: {md_file}")

    for obj in candidates:
        if isinstance(obj, dict) and isinstance(obj.get("tasks"), list) and len(obj["tasks"]) >= 5:
            return json.dumps(obj, ensure_ascii=False)

    for obj in candidates:
        if isinstance(obj, dict) and isinstance(obj.get("tasks"), list):
            return json.dumps(obj, ensure_ascii=False)

    return json.dumps(candidates[0], ensure_ascii=False)


def pretty_print_business_result(response_obj: dict) -> None:
    """
    Print only the server business payload returned inside result.parts[0].text.
    If parsing fails, fall back to printing the whole response in pretty JSON.
    """
    try:
        text = response_obj["result"]["parts"][0]["text"]
        payload = json.loads(text)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        print(json.dumps(response_obj, ensure_ascii=False, indent=2))


async def main() -> None:
    # ---- suppress noise (optional, but recommended) ----
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("a2a").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    # ---------------------------------------------------

    base_url = "http://localhost:9999"

    #  cross-platform: md file is next to this client by default
    base_dir = Path(__file__).resolve().parent
    md_file = base_dir / "TEST_CASE_TASK_5_STEPS.md"

    text_to_send = load_testcase_json_from_md(md_file)
    print("DEBUG md_file =", md_file.resolve())
    print("DEBUG send length =", len(text_to_send))
    print("DEBUG send head =", text_to_send[:120])
# 1. 定义完全不走代理且长超时的客户端参数
    async with httpx.AsyncClient(proxy=None,             # 显式设为 None 禁用系统代理
    trust_env=False,        # 关键：禁止从环境变量（如 VPN 设置）读取代理配置
    timeout=120.0) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)

        # Public agent card#  获取 agent card（此时已绕过 VPN，直接访问本地 9999）
        public_card = await resolver.get_agent_card()
        final_agent_card: AgentCard = public_card

        # Extended card (optional)
        if getattr(public_card, "supports_authenticated_extended_card", False):
            try:
                auth_headers = {"Authorization": "Bearer dummy-token-for-extended-card"}
                extended_card = await resolver.get_agent_card(
                    relative_card_path=EXTENDED_AGENT_CARD_PATH,
                    http_kwargs={"headers": auth_headers},
                )
                final_agent_card = extended_card
            except Exception:
                pass

        client = A2AClient(httpx_client=httpx_client, agent_card=final_agent_card)

        send_message_payload = {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": text_to_send}],
                "messageId": uuid4().hex,
            }
        }

        request = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(**send_message_payload))
        response = await client.send_message(request)

        resp_obj = response.model_dump(mode="json", exclude_none=True)
        pretty_print_business_result(resp_obj)

        # Streaming (optional)
        # streaming_request = SendStreamingMessageRequest(id=str(uuid4()), params=MessageSendParams(**send_message_payload))
        # async for chunk in client.send_message_streaming(streaming_request):
        #     chunk_obj = chunk.model_dump(mode="json", exclude_none=True)
        #     pretty_print_business_result(chunk_obj)


if __name__ == "__main__":
    asyncio.run(main())
