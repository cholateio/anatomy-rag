"""後端→ColPali encoder 微服務 HTTP client（§5.1）。回 QueryRepr（引擎中立查詢表示）。

主 URL 失敗（連線/逾時/5xx）→ 試 fallback URL（Modal scale-to-zero，DL-011）。mock 供測試/啟動。
"""
from __future__ import annotations

from typing import Protocol

import httpx

from anatomy_backend.retrieval.query_repr import QueryRepr

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class EncoderClientProtocol(Protocol):
    async def encode_query(self, text: str) -> QueryRepr: ...


class EncoderClient:
    def __init__(
        self,
        primary_url: str,
        *,
        fallback_url: str = "",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._primary = primary_url
        self._fallback = fallback_url
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT)

    async def _post(self, url: str, text: str) -> QueryRepr:
        resp = await self._http.post(url, json={"query": text})
        resp.raise_for_status()
        return QueryRepr.from_encode_query_response(resp.json())

    async def encode_query(self, text: str) -> QueryRepr:
        try:
            return await self._post(self._primary, text)
        except httpx.HTTPError:
            if not self._fallback:
                raise
            return await self._post(self._fallback, text)


class MockEncoderClient:
    """決定性 QueryRepr（測試/啟動；不開連線）。"""

    async def encode_query(self, text: str) -> QueryRepr:
        tok = bytes(range(16))
        pooled = tuple(0.01 * (i % 7) for i in range(128))
        return QueryRepr(
            pooled_f32=pooled,
            tokens_bin=(tok, tok),
            translated_q=text,
            lang="zh",
        )


def build_encoder(settings) -> EncoderClientProtocol:
    if getattr(settings, "encoder_mock", True):
        return MockEncoderClient()
    return EncoderClient(
        settings.colpali_primary_url,
        fallback_url=settings.colpali_fallback_url,
    )
