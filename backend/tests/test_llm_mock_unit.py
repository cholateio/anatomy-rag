import httpx
import pytest
from openai import APITimeoutError

from anatomy_backend.llm.mock import MockLLMClient


async def test_deterministic_token_stream():
    m = MockLLMClient(tokens=["肱二頭肌", "起於", "喙突"])
    out1 = [t async for t in m.stream_complete("S", "U", images=[])]
    out2 = [t async for t in m.stream_complete("S", "U", images=[])]
    assert out1 == ["肱二頭肌", "起於", "喙突"]
    assert out1 == out2


async def test_default_tokens_non_empty():
    m = MockLLMClient()
    out = [t async for t in m.stream_complete("S", "U", images=[])]
    assert out


async def test_records_calls_including_forbidden_identifiers():
    m = MockLLMClient(tokens=["x"])
    _ = [
        t
        async for t in m.stream_complete(
            "SYS", "USER", images=[b"img"], image_detail="low",
            forbidden_identifiers=frozenset({"uid"}),
        )
    ]
    assert m.invocations == 1
    assert m.calls[0].system == "SYS"
    assert m.calls[0].user == "USER"
    assert m.calls[0].image_detail == "low"
    assert m.calls[0].forbidden_identifiers == frozenset({"uid"})


async def test_failure_injection_raises_then_succeeds():
    exc = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    m = MockLLMClient(tokens=["ok"], error=exc, fail_first=2)
    for _ in range(2):
        with pytest.raises(APITimeoutError):
            _ = [t async for t in m.stream_complete("S", "U", images=[])]
    out = [t async for t in m.stream_complete("S", "U", images=[])]
    assert out == ["ok"]
    assert m.invocations == 3
