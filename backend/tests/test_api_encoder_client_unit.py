"""Unit tests for encoder/client.py — HTTP→QueryRepr, mock, fallback.

All tests inject a fake httpx object — no real network connections are opened.
"""
import base64
import struct

from anatomy_backend.encoder.client import EncoderClient, MockEncoderClient
from anatomy_backend.retrieval.query_repr import QueryRepr


def _payload():
    tok = base64.b64encode(b"\x00" * 16).decode()
    pooled = base64.b64encode(struct.pack("<128f", *([0.1] * 128))).decode()
    return {"tokens_bin": [tok, tok], "pooled_f32": pooled, "translated_q": "biceps origin",
            "lang": "en", "mt_model": "mock"}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _FakeHTTP:
    def __init__(self, resp, record):
        self._resp = resp
        self._rec = record

    async def post(self, url, json):
        self._rec["url"] = url
        self._rec["json"] = json
        return self._resp


async def test_encode_query_builds_queryrepr():
    rec = {}
    client = EncoderClient(
        "http://encoder:8001/encode_query",
        http=_FakeHTTP(_FakeResp(_payload()), rec),
    )
    qr = await client.encode_query("biceps 起點")
    assert isinstance(qr, QueryRepr)
    assert qr.translated_q == "biceps origin" and qr.lang == "en"
    assert len(qr.tokens_bin) == 2
    assert rec["json"] == {"query": "biceps 起點"}


async def test_mock_encoder_is_deterministic_queryrepr():
    m = MockEncoderClient()
    a = await m.encode_query("q")
    b = await m.encode_query("q")
    assert isinstance(a, QueryRepr)
    assert a.tokens_bin == b.tokens_bin and a.pooled_f32 == b.pooled_f32


async def test_fallback_url_used_when_primary_fails():
    import httpx

    class _FlakyHTTP:
        def __init__(self):
            self.calls = []

        async def post(self, url, json):
            self.calls.append(url)
            if "primary" in url:
                raise httpx.ConnectError("down")
            return _FakeResp(_payload())

    http = _FlakyHTTP()
    client = EncoderClient("http://primary/encode_query", http=http,
                           fallback_url="http://fallback/encode_query")
    qr = await client.encode_query("q")
    assert isinstance(qr, QueryRepr)
    assert http.calls == ["http://primary/encode_query", "http://fallback/encode_query"]
