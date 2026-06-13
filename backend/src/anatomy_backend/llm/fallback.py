"""模型 fallback（§5.3 / §6.1）——per-attempt 計數 + sticky 切換 + tenacity backoff+jitter。

語意（修正 Codex 對抗式審查 F1/F2）：
- consecutive_errors 計**每次合格 provider 錯誤嘗試**（非每次呼叫），連續達 threshold 即切。
  「連續 3 次 5xx/429」如實觸發（含單呼叫內 tenacity 重試所產生的多次錯誤）。
- 切換為 **sticky**：using_fallback 一旦為真即跨呼叫保持，不因單一成功 token 立即回退主模型
  （避免班級突發下反覆重擊故障主模型）。主→備恢復（half-open 探測 + cooldown）**延後
  Phase 9** 觀測/健康層（DL-011/§6）；本層為刻意最小範圍（v1 計數器 + sticky），非靜默缺口。
- 併發：asyncio 單執行緒、整數遞增間無 await → 無 torn write，不需 Lock（YAGNI）。
- APIConnectionError 僅 tenacity 重試、**不計入**切換（主備同 vendor/endpoint，切模型無益）。
- 建立成功後串流中途斷：傳播、不重試（避免重複 token）、不計數。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from anatomy_backend.llm.client import DEFAULT_IMAGE_DETAIL, LLMClientProtocol

# tenacity 重試集（含連線錯誤）
RETRYABLE_EXC: tuple[type[Exception], ...] = (
    APITimeoutError,
    RateLimitError,
    InternalServerError,
    APIConnectionError,
)
# 計入模型切換的觸發集（spec：Timeout/RateLimit/Server）
FALLBACK_TRIGGER_EXC: tuple[type[Exception], ...] = (
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

DEFAULT_SWITCH_THRESHOLD = 3
DEFAULT_MAX_ATTEMPTS = 3

_EMPTY = object()


def _default_wait():
    return wait_random_exponential(min=1, max=30)  # exponential backoff + jitter


class ModelFallbackClient:
    def __init__(
        self,
        primary: LLMClientProtocol,
        fallback: LLMClientProtocol,
        *,
        switch_threshold: int = DEFAULT_SWITCH_THRESHOLD,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        wait=None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._switch_threshold = switch_threshold
        self._max_attempts = max_attempts
        self._wait = wait if wait is not None else _default_wait()
        self.consecutive_errors = 0
        self.using_fallback = False  # sticky，跨呼叫保持

    def _active(self) -> LLMClientProtocol:
        return self._fallback if self.using_fallback else self._primary

    async def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(RETRYABLE_EXC),
            reraise=True,
        ):
            agen = None
            _ok = False  # 記錄建立是否成功；with attempt 吞例外後後續代碼仍會執行
            with attempt:
                client = self._active()  # 每次嘗試重選模型（達 threshold 後改備援）
                agen = client.stream_complete(
                    system,
                    user,
                    images,
                    image_detail=image_detail,
                    forbidden_identifiers=forbidden_identifiers,
                )
                try:
                    first = await agen.__anext__()
                except StopAsyncIteration:
                    first = _EMPTY
                except FALLBACK_TRIGGER_EXC:
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= self._switch_threshold:
                        self.using_fallback = True
                    raise  # 交 tenacity 重試 + backoff（APIConnectionError 不在此，直接交 tenacity）
                # 建立成功（with attempt 正常結束）
                self.consecutive_errors = 0
                _ok = True
            if not _ok:
                continue  # 此次嘗試失敗（例外已被 tenacity 吞；下一 iteration 重試）
            if first is _EMPTY:
                return
            yield first
            async for tok in agen:  # 中途斷會傳播、不重試、不計數
                yield tok
            return
