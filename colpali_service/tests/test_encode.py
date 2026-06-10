import base64

import pytest
from colpali_service.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthz_ready():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["ready"] is True


@pytest.mark.asyncio
async def test_encode_query_deterministic_contract():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/encode_query", json={"q": "肱二頭肌的起止點"})
        r2 = await c.post("/encode_query", json={"q": "肱二頭肌的起止點"})
    j1, j2 = r1.json(), r2.json()
    assert j1 == j2                                   # 決定性
    assert len(base64.b64decode(j1["pooled_f32"])) == 512  # float32[128]，DL-019 不二值化
    assert len(j1["tokens_bin"]) >= 1
    assert all(len(base64.b64decode(t)) == 16 for t in j1["tokens_bin"])  # patch bit(128)
    # DL-020：中文 query 偵測 + mock identity 翻譯
    assert j1["lang"] == "zh" and j1["translated_q"] == "肱二頭肌的起止點"
    assert j1["mt_model"] == "mock-identity"


@pytest.mark.asyncio
async def test_encode_query_english_is_identity_lang_en():
    """純英文 query：lang=en、translated_q 為原文（DL-020 identity 路徑）。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/encode_query", json={"q": "origin of biceps brachii"})
    j = r.json()
    assert j["lang"] == "en" and j["translated_q"] == "origin of biceps brachii"


def test_get_encoder_real_not_implemented_yet(monkeypatch):
    """ENCODER_MOCK=false 但真實 encoder 未實作（Phase 3 前，如 make up-gpu）→
    應拋清楚的 NotImplementedError，而非難解的 ModuleNotFoundError。"""
    import colpali_service.encoder as enc

    monkeypatch.setenv("ENCODER_MOCK", "false")
    with pytest.raises(NotImplementedError, match="Phase 3"):
        enc.get_encoder()


@pytest.mark.asyncio
async def test_encode_query_distinct_queries_and_fp32_pooled_contract():
    """守護重構（Codex MEDIUM-4）：不同 query 產不同 token；token 數＝有效 token 數
    （排除特殊前綴）；pooled_f32＝有效 token 的 fp32 平均（不得有 f16 量化）。"""
    import numpy as np
    from anatomy_shared.binary import pool_patches
    from anatomy_shared.colpali_runtime import MockColPaliRuntime

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/encode_query", json={"q": "肱二頭肌"})
        r2 = await c.post("/encode_query", json={"q": "橈神經"})
    j1, j2 = r1.json(), r2.json()
    assert j1["tokens_bin"] != j2["tokens_bin"]
    rt = MockColPaliRuntime()
    assert len(j1["tokens_bin"]) == rt.n_query_tokens - rt.n_special_prefix
    enc = rt.encode_query("肱二頭肌")
    expected = pool_patches(enc.embeddings, valid_mask=enc.valid_mask).astype("<f4")
    got = np.frombuffer(base64.b64decode(j1["pooled_f32"]), dtype="<f4")
    assert np.array_equal(got, expected)
    assert j1["model"] == "mock-colpali"
