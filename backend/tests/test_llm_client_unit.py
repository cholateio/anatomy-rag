from types import SimpleNamespace

import pytest
from anatomy_backend.llm import PIILeakError
from anatomy_backend.llm.client import (
    DEFAULT_MAX_COMPLETION_TOKENS,
    TOKEN_LIMIT_PARAM,
    LLMClient,
)


def _chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def _usage_chunk():
    return SimpleNamespace(choices=[])


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for c in self._chunks:
            yield c

    async def close(self):
        self.closed = True


class _FakeCompletions:
    def __init__(self, chunks, rec):
        self._chunks = chunks
        self._rec = rec

    async def create(self, **kwargs):
        self._rec["kwargs"] = kwargs
        self._rec["create_calls"] = self._rec.get("create_calls", 0) + 1
        s = _FakeStream(self._chunks)
        self._rec["stream"] = s
        return s


class _FakeClient:
    def __init__(self, chunks, rec):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks, rec))


async def test_stream_yields_tokens_and_skips_none_and_usage_chunks():
    chunks = [_chunk(None), _chunk("肱"), _chunk("二頭肌"), _usage_chunk(), _chunk(None)]
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient(chunks, rec))
    out = [t async for t in client.stream_complete("SYS", "U", images=[])]
    assert out == ["肱", "二頭肌"]


async def test_create_kwargs_match_spec():
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient([_chunk("x")], rec))
    _ = [t async for t in client.stream_complete("SYS", "U", images=[])]
    kw = rec["kwargs"]
    assert kw["model"] == "gpt-5.5"
    assert kw["stream"] is True
    assert kw["temperature"] == 0.2
    assert kw[TOKEN_LIMIT_PARAM] == DEFAULT_MAX_COMPLETION_TOKENS
    assert "response_format" not in kw  # 不可用 json_object
    assert "user" not in kw             # 不送 user_id


async def test_real_client_constructed_with_max_retries_zero():
    client = LLMClient("gpt-5.5", api_key="sk-test-dummy")
    assert client._client.max_retries == 0


async def test_images_passed_as_image_url_parts():
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient([_chunk("x")], rec))
    _ = [t async for t in client.stream_complete("S", "U", images=[b"png"], image_detail="high")]
    user_parts = rec["kwargs"]["messages"][1]["content"]
    assert user_parts[1]["image_url"]["detail"] == "high"


async def test_pii_guard_blocks_create_when_identifier_present():
    # Codex F3：識別碼嵌入 user 字串 → create() 不得被呼叫
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient([_chunk("x")], rec))
    uid = "stud-2026-00042"
    with pytest.raises(PIILeakError):
        _ = [
            t
            async for t in client.stream_complete(
                "S", f"問題 {uid}", images=[], forbidden_identifiers=frozenset({uid})
            )
        ]
    assert rec.get("create_calls", 0) == 0  # 送出前即攔下


async def test_stream_is_closed_after_consumption():
    # Fix 1c：正常消費完畢後底層串流應被 close()（防止 httpx 連線洩漏）
    rec = {}
    fake_client = _FakeClient([_chunk("x")], rec)
    client = LLMClient("gpt-5.5", client=fake_client)
    _ = [t async for t in client.stream_complete("S", "U", images=[])]
    assert rec["stream"].closed is True
