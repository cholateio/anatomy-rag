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
