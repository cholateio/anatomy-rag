"""Phase 7 SemanticCache 單元測試（exact-normalized-query；手刻 _FakeRedis，無真 Redis）。"""
from __future__ import annotations

import fnmatch
import json

import pytest

from anatomy_backend.config import Settings


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@localhost:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@localhost:5432/anatomy_rag",
        redis_url="redis://localhost:6379/0",
    )
    base.update(over)
    return Settings(**base)


def test_cache_config_defaults():
    s = _settings()
    assert s.cache_enabled is True
    assert s.cache_mode == "exact"
    assert s.cache_ttl_seconds == 1209600  # 14 天（7–30 天區間）


from anatomy_backend.cache.semantic_cache import SemanticCache


def test_normalize_trims_lowercases_collapses_ws():
    n = SemanticCache.normalize_query
    assert n("  Biceps   Brachii  ") == n("biceps brachii")
    assert n("Hello\tWorld\n") == "hello world"


def test_normalize_nfkc_fullwidth_to_halfwidth():
    n = SemanticCache.normalize_query
    assert n("ＡＢＣ１２３") == "abc123"
    assert n("肱二頭肌？") == n("肱二頭肌?")


def test_normalize_is_deterministic_idempotent():
    n = SemanticCache.normalize_query
    once = n("　肱二頭肌 的起止點　")
    assert once == n(once)


def test_distinct_medical_terms_stay_distinct():
    # 反 Codex#4：相似但不同的醫學術語不可被正規化併在一起
    n = SemanticCache.normalize_query
    assert n("肱二頭肌的起止點") != n("肱三頭肌的起止點")
    assert n("femoral nerve") != n("femoral artery")


def test_canonical_filter_order_insensitive_and_empty():
    cf = SemanticCache._canonical_filter
    assert cf(None) == "" == cf({})
    # dict 順序不影響 canonical 形式（sort_keys）
    assert cf({"a": 1, "b": 2}) == cf({"b": 2, "a": 1})
    assert cf({"anatomy_system": "musculoskeletal"}) != cf({"anatomy_system": "nervous"})


def test_key_varies_by_kb_and_filter():
    c = SemanticCache(object(), ttl_seconds=60)
    base = c._key("q", 1, None)
    assert c._key("q", 2, None) != base                       # kb 不同
    assert c._key("q", 1, {"book": "gray"}) != base           # filter 不同
    assert c._key("q", 1, {"book": "gray"}) == c._key("q", 1, {"book": "gray"})  # 決定性


class _FakeRedis:
    """最小 async Redis 替身（沿用 ratelimit 測試手刻 fake 的慣例）。

    fail=True：get/set/scan/unlink 全拋（fail-open 測試）。
    scan_fail_after=N：scan_iter 吐 N 個 key 後拋（mid-iteration 失敗）。
    decode_responses=False 語意：value 存 bytes、回 bytes。
    """

    def __init__(self, *, fail: bool = False, scan_fail_after: int | None = None) -> None:
        self.store: dict[str, bytes] = {}
        self.ex: dict[str, int] = {}
        self.set_calls = 0
        self._fail = fail
        self._scan_fail_after = scan_fail_after

    async def get(self, key):
        if self._fail:
            raise RuntimeError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self._fail:
            raise RuntimeError("redis down")
        self.set_calls += 1
        self.store[key] = value
        if ex is not None:
            self.ex[key] = ex

    async def scan_iter(self, match=None, count=None):
        if self._fail:
            raise RuntimeError("redis down")
        n = 0
        for k in list(self.store.keys()):
            if match is None or fnmatch.fnmatchcase(k, match):
                if self._scan_fail_after is not None and n >= self._scan_fail_after:
                    raise RuntimeError("scan interrupted")
                n += 1
                yield k

    async def unlink(self, *keys):
        if self._fail:
            raise RuntimeError("redis down")
        for k in keys:
            self.store.pop(k, None)
            self.ex.pop(k, None)


def _cache(redis, ttl=1209600):
    return SemanticCache(redis, ttl_seconds=ttl)


# 形似 PageCitation.model_dump() 的合法 source
SRC = [{"book_title": "Gray's", "edition": "42e", "page": 12, "figure": "Fig.1",
        "image_url": "http://x/y.png", "snippet": "...", "score": 0.9}]


async def test_set_rejects_unverified_answer():
    r = _FakeRedis()
    await _cache(r).set("肱二頭肌起止點", "造假", SRC, 1, verified=False)
    assert r.set_calls == 0 and r.store == {}


async def test_set_rejects_malformed_sources():
    # 反 Codex#2/#5：verified=True 但 source 缺 book_title/page → 拒寫
    r = _FakeRedis()
    await _cache(r).set("q", "a", [{"page": 1}], 1, verified=True)            # 缺 book_title
    await _cache(r).set("q", "a", [{"book_title": "X"}], 1, verified=True)    # 缺 page
    await _cache(r).set("q", "a", "not-a-list", 1, verified=True)            # 非 list
    assert r.set_calls == 0


async def test_set_allows_empty_sources():
    # 教材查無此項類已驗證答案可無 citation（空 list 合法）
    r = _FakeRedis()
    await _cache(r).set("查無此項", "教材中查無此項。", [], 1, verified=True)
    assert r.set_calls == 1


async def test_set_then_get_roundtrip_hit_preserves_pagecitation_shape():
    r = _FakeRedis()
    c = _cache(r)
    await c.set("肱二頭肌起止點", "起於...止於...[Gray's, p.12]", SRC, 1, verified=True)
    assert r.set_calls == 1
    assert r.ex[next(iter(r.store))] == 1209600
    hit = await c.get("  肱二頭肌起止點 ", 1)            # 正規化後同 key
    assert hit is not None
    assert hit.answer == "起於...止於...[Gray's, p.12]"
    assert hit.sources == SRC                            # 形狀完整保留


async def test_get_miss_when_absent():
    assert await _cache(_FakeRedis()).get("沒寫過", 1) is None


async def test_get_cross_kb_version_miss():
    r = _FakeRedis()
    c = _cache(r)
    await c.set("q", "a", SRC, 1, verified=True)
    assert await c.get("q", 2) is None


async def test_get_cross_metadata_filter_miss():
    # 反 Codex#1 critical：同 query 不同 filter 不可互相命中
    r = _FakeRedis()
    c = _cache(r)
    await c.set("肱二頭肌", "musculo 答案", SRC, 1, verified=True,
                metadata_filter={"anatomy_system": "musculoskeletal"})
    assert await c.get("肱二頭肌", 1, {"anatomy_system": "nervous"}) is None
    assert await c.get("肱二頭肌", 1, None) is None
    # 同 filter → 命中
    hit = await c.get("肱二頭肌", 1, {"anatomy_system": "musculoskeletal"})
    assert hit is not None and hit.answer == "musculo 答案"


async def test_get_kb_envelope_mismatch_miss():
    r = _FakeRedis()
    c = _cache(r)
    r.store[c._key("q", 1)] = json.dumps(
        {"v": 1, "answer": "a", "sources": [], "kb_version": 999, "verified": True}
    ).encode("utf-8")
    assert await c.get("q", 1) is None


async def test_get_corrupt_value_is_miss_not_raise():
    r = _FakeRedis()
    c = _cache(r)
    r.store[c._key("q", 1)] = b"\\xff not-json"
    assert await c.get("q", 1) is None


async def test_get_unknown_schema_miss():
    r = _FakeRedis()
    c = _cache(r)
    r.store[c._key("q", 1)] = json.dumps(
        {"v": 999, "answer": "a", "sources": SRC, "kb_version": 1, "verified": True}
    ).encode("utf-8")
    assert await c.get("q", 1) is None


async def test_get_malformed_cached_sources_miss():
    # 反 Codex#5：命中資料 source 損壞 → 視為 miss，不外送
    r = _FakeRedis()
    c = _cache(r)
    r.store[c._key("q", 1)] = json.dumps(
        {"v": 1, "answer": "a", "sources": [{"page": 1}], "kb_version": 1, "verified": True}
    ).encode("utf-8")
    assert await c.get("q", 1) is None


async def test_get_fail_open_returns_none():
    assert await _cache(_FakeRedis(fail=True)).get("q", 1) is None


async def test_set_fail_open_no_raise():
    await _cache(_FakeRedis(fail=True)).set("q", "a", SRC, 1, verified=True)


async def test_set_serialization_failure_fail_open():
    # 反 Codex#6：不可序列化 sources → fail-open（json.dumps 在 try 內），不拋
    r = _FakeRedis()
    await _cache(r).set("q", "a", [{"book_title": "X", "page": 1, "bad": {1, 2}}], 1, verified=True)
    assert r.set_calls == 0   # 序列化失敗→未寫


async def test_similar_but_different_query_no_false_hit():
    r = _FakeRedis()
    c = _cache(r)
    await c.set("肱二頭肌的起止點", "二頭肌答案", SRC, 1, verified=True)
    assert await c.get("肱三頭肌的起止點", 1) is None


async def test_clear_kb_version_only_clears_target():
    r = _FakeRedis()
    c = _cache(r)
    await c.set("q1", "a1", SRC, 1, verified=True)
    await c.set("q2", "a2", SRC, 1, verified=True)
    await c.set("q3", "a3", SRC, 2, verified=True)
    await c.clear_kb_version(1)
    assert await c.get("q1", 1) is None
    assert await c.get("q2", 1) is None
    assert await c.get("q3", 2) is not None   # kb2 不受影響


async def test_clear_kb_version_fail_open_no_raise():
    await _cache(_FakeRedis(fail=True)).clear_kb_version(1)


async def test_clear_kb_version_mid_scan_failure_no_raise():
    # 反 Codex#6：scan 迭代中途失敗 → fail-open（部分清除可接受，殘留靠 namespace+TTL）
    r = _FakeRedis(scan_fail_after=1)
    c = _cache(r)
    await c.set("q1", "a1", SRC, 1, verified=True)
    await c.set("q2", "a2", SRC, 1, verified=True)
    await c.clear_kb_version(1)   # 不拋
