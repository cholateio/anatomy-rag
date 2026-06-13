"""決定性 mock LLM 客戶端（供測試 + make up；零 OpenAI 呼叫、零 token 費用）。

同 LLMClientProtocol。支援失敗注入（fail_first 次建立期拋 error），給 fallback 計數測試。
forbidden_identifiers 僅記錄（mock 不送任何網路，無洩漏風險）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

from anatomy_backend.llm.client import DEFAULT_IMAGE_DETAIL

_DEFAULT_MOCK_TOKENS: tuple[str, ...] = (
    "肱二頭肌",
    "起於肩胛骨喙突",
    " [Gray42, p.812, Fig.7-23]。\n\n",
    "（教育用途，內容基於教科書）",
)


class MockLLMClient:
    def __init__(
        self,
        *,
        tokens: list[str] | None = None,
        error: Exception | None = None,
        fail_first: int = 0,
        name: str = "mock",
    ) -> None:
        self.tokens = list(tokens) if tokens is not None else list(_DEFAULT_MOCK_TOKENS)
        self.error = error
        self.fail_first = fail_first
        self.name = name
        self.invocations = 0
        self.calls: list[SimpleNamespace] = []

    async def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]:
        self.invocations += 1
        self.calls.append(
            SimpleNamespace(
                system=system,
                user=user,
                images=list(images),
                image_detail=image_detail,
                forbidden_identifiers=forbidden_identifiers,
            )
        )
        if self.error is not None and self.invocations <= self.fail_first:
            raise self.error  # 建立期失敗（首個 __anext__ 觸發）
        for tok in self.tokens:
            yield tok
