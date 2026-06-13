"""原生 openai SDK 生成客戶端（§5.3/§5.5）+ fail-closed PII 邊界（§0 合規紅線）。

常數集中於此，方便 Phase 8 smoke 一行校正：
- TOKEN_LIMIT_PARAM：gpt-5.x 用 max_completion_tokens（已離線驗 SDK 接受）。
- DEFAULT_TEMPERATURE：醫學事實型偏低；若 reasoning 模型僅允許 1，Phase 8 smoke 校正。
"""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_COMPLETION_TOKENS = 1500
TOKEN_LIMIT_PARAM = "max_completion_tokens"
DEFAULT_IMAGE_DETAIL = "high"


@runtime_checkable
class LLMClientProtocol(Protocol):
    """生成客戶端介面（LLMClient / MockLLMClient / ModelFallbackClient 共用）。"""

    def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]: ...


class PIILeakError(ValueError):
    """送往 OpenAI 的 payload 偵測到禁止識別資訊（user_id/學號）；fail-closed 拒送。"""


def build_chat_messages(
    system: str,
    user: str,
    images: list[bytes],
    *,
    image_detail: str = DEFAULT_IMAGE_DETAIL,
) -> list[dict]:
    """組 chat.completions messages（§5.5）。

    - system role：靜態行為準則（版本化常數）。
    - user role：text part（教科書摘錄 + 使用者問題）＋ 0..N image_url parts。
    """
    content: list[dict] = [{"type": "text", "text": user}]
    for img in images:
        b64 = base64.b64encode(img).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": image_detail},
            }
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def assert_no_identifiers(messages: list[dict], forbidden: frozenset[str]) -> None:
    """fail-closed PII 邊界（§0 / §5.8；Codex F3）。

    偵測 forbidden 中任一非空字串出現在序列化後的 messages → raise PIILeakError（拒送）。
    Phase 8 orchestrator MUST 傳入該請求的 user_id/學號。為避免二次外洩，例外訊息**不**印出
    洩漏內容本身，只報數量。
    """
    if not forbidden:
        return
    blob = json.dumps(messages, ensure_ascii=False)
    leaked = [s for s in forbidden if s and s in blob]
    if leaked:
        raise PIILeakError(
            f"OpenAI payload 含禁止識別資訊（{len(leaked)} 項），fail-closed 拒送"
        )


from openai import AsyncOpenAI  # noqa: E402  （置檔尾與常數/純函式分區）


class LLMClient:
    """單一模型的原生 openai async 串流客戶端（§5.5）。

    max_retries=0：關閉 SDK 內建重試，讓 tenacity + ModelFallbackClient 計數看到每次失敗。
    可注入 client 供測試（零 API 呼叫）。送 OpenAI 前先 assert_no_identifiers fail-closed。
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str = "",
        base_url: str | None = None,
        client: AsyncOpenAI | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_completion_tokens = max_completion_tokens
        self._client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=0
        )

    async def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]:
        messages = build_chat_messages(system, user, images, image_detail=image_detail)
        assert_no_identifiers(messages, forbidden_identifiers)  # fail-closed，送出前
        kwargs = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "temperature": self._temperature,
            TOKEN_LIMIT_PARAM: self._max_completion_tokens,
        }
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:  # usage-only chunk
                continue
            delta = chunk.choices[0].delta
            if delta.content:  # 首尾 chunk content 為 None
                yield delta.content
