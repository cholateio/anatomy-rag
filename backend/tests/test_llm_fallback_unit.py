import httpx
import pytest
from anatomy_backend.llm.fallback import ModelFallbackClient
from anatomy_backend.llm.mock import MockLLMClient
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import wait_none

_REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _timeout():
    return APITimeoutError(request=_REQ)


def _rate():
    return RateLimitError("rate", response=httpx.Response(429, request=_REQ), body=None)


def _server():
    return InternalServerError("srv", response=httpx.Response(500, request=_REQ), body=None)


def _conn():
    return APIConnectionError(request=_REQ)


def _mfc(primary, fallback, **kw):
    kw.setdefault("max_attempts", 1)   # 預設單次嘗試（多數呼叫級測試）
    kw.setdefault("wait", wait_none())  # 不真睡
    return ModelFallbackClient(primary, fallback, **kw)


async def _drain(client):
    return [t async for t in client.stream_complete("S", "U", images=[])]


async def test_three_provider_errors_within_one_call_switch_to_fallback():
    # Codex F1：單次呼叫內 3 次底層 5xx/429 即切備援（max_attempts 容納重試）
    primary = MockLLMClient(error=_server(), fail_first=99)
    fallback = MockLLMClient(tokens=["備援回答"])
    mfc = ModelFallbackClient(
        primary, fallback, switch_threshold=3, max_attempts=4, wait=wait_none()
    )
    out = await _drain(mfc)
    assert out == ["備援回答"]
    assert primary.invocations == 3      # 3 次錯誤
    assert fallback.invocations == 1     # 第 4 次嘗試切備援
    assert mfc.using_fallback is True
    assert mfc.consecutive_errors == 0   # 成功歸零


async def test_call_level_counting_across_calls_then_switch():
    primary = MockLLMClient(error=_timeout(), fail_first=99)
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback, switch_threshold=3)  # max_attempts=1：每呼叫一次嘗試
    for _ in range(3):
        with pytest.raises(APITimeoutError):
            await _drain(mfc)
    assert mfc.consecutive_errors == 3
    assert mfc.using_fallback is True
    assert primary.invocations == 3
    # 第 4 次呼叫：sticky → 用備援
    out = await _drain(mfc)
    assert out == ["備援"]
    assert fallback.invocations == 1


async def test_sticky_switch_persists_after_fallback_success():
    # Codex F2：切換後不因單一成功回退主模型
    primary = MockLLMClient(error=_timeout(), fail_first=99)
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback, switch_threshold=3)
    for _ in range(3):
        with pytest.raises(APITimeoutError):
            await _drain(mfc)
    assert mfc.using_fallback is True
    await _drain(mfc)  # 備援成功
    await _drain(mfc)  # 仍應走備援
    assert fallback.invocations == 2
    assert primary.invocations == 3  # 切換後主模型不再被呼叫
    assert mfc.using_fallback is True


async def test_success_before_threshold_resets_counter():
    primary = MockLLMClient(error=_server(), fail_first=2, tokens=["主回答"])
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback, switch_threshold=3)
    for _ in range(2):
        with pytest.raises(InternalServerError):
            await _drain(mfc)
    assert mfc.consecutive_errors == 2
    out = await _drain(mfc)  # 第三次主模型成功
    assert out == ["主回答"]
    assert mfc.consecutive_errors == 0
    assert mfc.using_fallback is False
    assert fallback.invocations == 0


@pytest.mark.parametrize("make_exc", [_timeout, _rate, _server])
async def test_each_trigger_type_increments_counter(make_exc):
    primary = MockLLMClient(error=make_exc(), fail_first=99)
    fallback = MockLLMClient(tokens=["x"])
    mfc = _mfc(primary, fallback)
    with pytest.raises(type(make_exc())):
        await _drain(mfc)
    assert mfc.consecutive_errors == 1


async def test_connection_error_retried_but_not_counted():
    # APIConnectionError 僅重試、不計入切換（主備同 vendor/endpoint）
    primary = MockLLMClient(error=_conn(), fail_first=99)
    fallback = MockLLMClient(tokens=["x"])
    mfc = _mfc(primary, fallback)
    with pytest.raises(APIConnectionError):
        await _drain(mfc)
    assert mfc.consecutive_errors == 0
    assert mfc.using_fallback is False


async def test_tenacity_retries_transient_then_succeeds_no_count():
    primary = MockLLMClient(error=_timeout(), fail_first=2, tokens=["成功"])
    fallback = MockLLMClient(tokens=["備援"])
    mfc = ModelFallbackClient(primary, fallback, max_attempts=3, wait=wait_none())
    out = await _drain(mfc)
    assert out == ["成功"]
    assert primary.invocations == 3       # 2 失敗 + 1 成功（同一呼叫內 tenacity 重試）
    assert mfc.consecutive_errors == 0
    assert fallback.invocations == 0


async def test_mid_stream_error_after_first_token_not_counted():
    # 建立成功（已吐 token）後中途斷：傳播、不計數
    class _MidFail:
        def __init__(self):
            self.invocations = 0

        async def stream_complete(self, system, user, images, *, image_detail="high",
                                  forbidden_identifiers=frozenset()):
            self.invocations += 1
            yield "第一段"
            raise _server()

    primary = _MidFail()
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback)
    with pytest.raises(InternalServerError):
        await _drain(mfc)
    assert mfc.consecutive_errors == 0  # 已建立成功 → 不計建立期失敗
