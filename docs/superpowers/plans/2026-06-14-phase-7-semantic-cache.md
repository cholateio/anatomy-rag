# Phase 7 — 語意快取 SemanticCache 實作計畫（v2，含 Codex 對抗式審查修訂）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以「exact-normalized-query」為 v1 預設實作 `SemanticCache`（填滿 Phase 8 留下的 `CacheProtocol` seam），只快取通過引文驗證的答案、以 `(kb_version, metadata_filter)` 完整隔離、Redis 故障 fail-open，並讓 `build_cache` 在設定下回傳它。

**Architecture:** `SemanticCache` 把「正規化後的 query + canonical metadata_filter」雜湊成決定性 key（namespace 含 `kb_version`），值是 JSON envelope `{v, answer, sources, kb_version, verified}` 存進既有的 `redis.asyncio` client，TTL 由 Redis `SET ... EX` 控制。線上 lookup **零 OpenAI、零 embedding 套件、低誤命中**（決定性字面比對，非語意向量）。語意向量比對（fastembed，torch-free）列為**後續 config 開關**（`cache_mode="semantic"`，本 phase 不實作、留清楚的 `NotImplementedError` seam）。決策記於新增的 **DL-025**。

**Tech Stack:** Python 3.11、`redis.asyncio`（已在 deps，**不新增任何套件**）、`hashlib.sha256`、`unicodedata` NFKC、pytest（單元用手刻 `_FakeRedis`；整合用 CI db-integration job 既有的 `redis:7-alpine`）。

---

## Codex 對抗式審查（2026-06-14）修訂摘要

審查 verdict＝needs-attention，6 findings 已逐一查核並納入本 v2：

1. **[critical] key 遺漏 metadata_filter** → 已確認 `chat.py:145` 把 `normalized.metadata_filter` 餵進 `retrieve_fn`，會改變答案；但舊 seam 的 `cache.get/set` 只用 query+kb_version。**修**：`CacheProtocol`/`NoOpCache`/`SemanticCache`/`chat.py` 全部納入 canonical `metadata_filter`；加跨 filter 不命中測試。
2. **[high] verified 只信任呼叫端 bool** → 信任邊界本就在 `chat.py` 的 `verify_citations`（DL-012）；在快取內重新驗證會把 cache 耦合到 retrieval，**v1 不做**（理由見 DL-025）。但**採納**結構防禦：`set`/`get` 校驗每個 source 形狀（`book_title`+`page`），拒絕/略過損壞引文。
3. **[high] SCAN 清版本非原子/未接線** → 版本隔離的**正確性來自 kb_version namespace + 只讀 active version**：切版後舊 namespace 不再被查、靠 TTL 自然消亡，殘留 key 不會造成錯答。`clear_kb_version` 為記憶體回收（housekeeping），切版接線屬 §6.6 ops/runbook（非 v1 程式範圍）。**採納**：補 SCAN 迭代中途失敗的 fail-open 測試 + 文件化此論證。不採納 generation-namespace 重設計（kb_version 已是 generation）。
4. **[high]「零誤命中」宣稱不成立** → casefold/NFKC 為有損（如「US」ultrasound vs 代名詞「us」）。**採納**：改稱「決定性、誤命中極低（僅正規化等價字串會併）」；且即便誤命中，回的仍是**已驗證、有引文**的答案（安全網守住），harm 有界。醫學術語 precision/碰撞語料列為 Phase 11 eval gate。
5. **[medium] 命中只淺層型別檢查** → 同 #2 結構防禦；加命中路徑契約測試（sources 形狀、chat 接線傳 metadata_filter）。
6. **[medium] fail-open 缺可觀測性 + `json.dumps` 在 try 外** → **採納**：`json.dumps` 移進 `set` 的 try（序列化失敗也 fail-open）；補序列化失敗 / mid-scan 失敗測試。快取 hit-rate/failure metrics 接 LangFuse/Sentry 屬 **Phase 9**（觀測性），本 phase 以結構化 warning log 為界並明示延後。

---

## 範圍護欄（已交 Codex 對抗式審查；下列為定案）

- **MUST 只快取已驗證答案**：`set(verified=False)` 一律拒寫；信任邊界＝`chat.py` 的 `verify_citations`（DL-012）。快取層另加 source 形狀防禦（非重做引文驗證）。
- **完整隔離**：key = `f"{prefix}:kb{kb_version}:{sha256(normalized_query + \\x00 + canonical_metadata_filter)}"`；命中時再校驗 envelope `kb_version`，不符→miss。
- **本地 lookup、DL-012**：快取層 **MUST NOT** `import openai`；CI grep 守門。
- **Redis fail-open（§1.8）**：`get` 例外→miss、`set`/`clear` 例外→no-op（含序列化失敗、mid-scan 失敗），**絕不**讓快取故障中斷 `/chat`。
- **TTL 7–30 天**（config 預設 14 天 = 1209600s）。
- **追問（DL-021）**：由 `chat.py` 控制不查/不寫；`SemanticCache` 追問無關，但 `set` 仍防禦性拒絕未驗證。
- **誤命中**：exact 模式為決定性、誤命中極低；**非**「零」。即便誤命中，回的仍是已驗證有引文的答案。

**不新增套件確認（使用者 2026-06-14 核准）：** runtime 0、測試 0；不引入 fastembed / sentence-transformers / fakeredis。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `backend/src/anatomy_backend/cache/semantic_cache.py` | **Create** | `SemanticCache`（`normalize_query`/`_canonical_filter`/`_sources_ok`/`_key`/`get`/`set`/`clear_kb_version`） |
| `backend/src/anatomy_backend/cache/base.py` | Modify | `CacheProtocol`/`NoOpCache` 簽章加 `metadata_filter`；`build_cache(settings, redis_client=None)` 分支 |
| `backend/src/anatomy_backend/cache/__init__.py` | Modify | 匯出 `SemanticCache` |
| `backend/src/anatomy_backend/config.py` | Modify | 新增 `cache_enabled: bool = True`、`cache_mode: str = "exact"` |
| `backend/src/anatomy_backend/api/main.py` | Modify | line 76：`build_cache(settings, redis_client)` |
| `backend/src/anatomy_backend/api/chat.py` | Modify | `cache.get`/`cache.set` 傳入 `normalized.metadata_filter`（lines 93、250） |
| `backend/tests/test_api_semantic_cache_unit.py` | **Create** | 純單元（手刻 `_FakeRedis`）：normalize / 跨 filter / set 拒未驗證+損壞 / roundtrip / cross-kb / 損壞值 / fail-open（含序列化、mid-scan）/ clear / build_cache 分支 |
| `backend/tests/test_api_semantic_cache_integration.py` | **Create** | `@pytest.mark.integration` + skipif 無 `REDIS_URL`：真 Redis set/get、cross-kb、跨 filter、未驗證不寫、TTL、clear |
| `backend/tests/test_api_chat_sse_unit.py` | Modify | 加 `_RecordingCache` 測試：chat 將 `metadata_filter` 傳給 `cache.get`/`cache.set` |
| `backend/tests/conftest.py` | Modify | 加 `REQUIRE_REDIS_TESTS` 假綠防呆（鏡像 `REQUIRE_DB_TESTS`） |
| `backend/tests/test_api_cache_seam_unit.py` | Modify | 補「有 redis→SemanticCache」「無 redis→NoOp」分支測試 |
| `.github/workflows/ci.yml` | Modify | unit job 加 cache 零-OpenAI grep；db-integration step 加 `REQUIRE_REDIS_TESTS: "1"` |
| `docs/decisions.md` | Modify | 追加 **DL-025**（APPROVED） |

---

## Task 1：config 旗標 `cache_enabled` / `cache_mode`

**Files:**
- Modify: `backend/src/anatomy_backend/config.py`（語意快取設定區塊，`cache_ttl_seconds` 之後）
- Test: `backend/tests/test_api_semantic_cache_unit.py`（本 task 建檔）

- [ ] **Step 1：寫失敗測試**

建立 `backend/tests/test_api_semantic_cache_unit.py`：

```python
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
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py::test_cache_config_defaults -q`
Expected: FAIL（`AttributeError ... 'cache_enabled'`）

- [ ] **Step 3：加 config 欄位**

`config.py`「# 語意快取設定」區塊（`cache_ttl_seconds` 之後）加：

```python
    # 語意快取啟用旗標與比對模式（DL-025）
    # exact：正規化後字面比對（v1 預設，決定性、誤命中極低、零 embedding 套件）
    # semantic：本地向量比對（fastembed，torch-free）——後續 config 開關，本 phase 未實作
    cache_enabled: bool = True
    cache_mode: str = "exact"
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py::test_cache_config_defaults -q`
Expected: PASS

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/config.py backend/tests/test_api_semantic_cache_unit.py
git commit -m "feat(cache): Phase 7 config 旗標 cache_enabled/cache_mode（DL-025）"
```

---

## Task 2：`normalize_query` + `_canonical_filter` + `_key`（含 metadata_filter）

**Files:**
- Create: `backend/src/anatomy_backend/cache/semantic_cache.py`
- Test: `backend/tests/test_api_semantic_cache_unit.py`

- [ ] **Step 1：寫失敗測試**

```python
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
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k "normalize or canonical or key_varies or distinct" -q`
Expected: FAIL（`ModuleNotFoundError: ...semantic_cache`）

- [ ] **Step 3：建立 `semantic_cache.py`**

```python
"""語意快取（§6.4 / DL-004 / DL-012 / DL-025）。

v1 預設＝exact-normalized-query：把『正規化 query + canonical metadata_filter』雜湊成
決定性 key（namespace 含 kb_version），zero embedding 套件、決定性、誤命中極低、命中
<30ms（§1.6）。語意向量比對為後續 config 開關（fastembed，torch-free；cache_mode="semantic"）。

硬性規則：
- 只快取已驗證答案：set(verified=False) 一律拒寫（信任邊界＝chat.py verify_citations, DL-012）。
- 完整隔離：key 納入 kb_version + metadata_filter；命中時再校驗 envelope kb_version。
- 本地 lookup：本模組 MUST NOT import openai（DL-012；CI grep 守門）。
- Redis fail-open（§1.8）：get→miss、set/clear→no-op（含序列化/mid-scan 失敗），絕不中斷 /chat。
誤命中：決定性正規化會把『正規化等價字串』併為同 key（如 casefold 後 US/us）；非語意比對故無向量誤命中。
即便誤命中，回的仍是已驗證有引文的答案（安全網守住）。醫學術語 precision 語料列 Phase 11 eval gate。
DL-021 追問不查/不寫由 chat.py 控制，本類保持追問無關。
"""
from __future__ import annotations

import hashlib
import json
import logging
import unicodedata

from anatomy_backend.cache.base import CachedAnswer

logger = logging.getLogger(__name__)


class SemanticCache:
    """exact-normalized-query 快取，實作 CacheProtocol（get/set）。"""

    _SCHEMA = 1
    _DEFAULT_PREFIX = "semcache"

    def __init__(self, redis, *, ttl_seconds: int, key_prefix: str | None = None) -> None:
        self._redis = redis
        self._ttl = int(ttl_seconds)
        self._prefix = key_prefix or self._DEFAULT_PREFIX

    @staticmethod
    def normalize_query(query: str) -> str:
        """決定性正規化：NFKC（全→半形/相容字）→ strip → casefold → 摺疊空白。"""
        s = unicodedata.normalize("NFKC", query)
        s = s.strip().casefold()
        s = " ".join(s.split())
        return s

    @staticmethod
    def _canonical_filter(metadata_filter: dict | None) -> str:
        """canonical 形式：空→""；否則 sort_keys 的精簡 JSON（順序不敏感、決定性）。"""
        if not metadata_filter:
            return ""
        return json.dumps(
            metadata_filter, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @staticmethod
    def _sources_ok(sources) -> bool:
        """sources 須為 list；每個 present 元素須為 dict 且具 book_title(非空 str)+page(int)。
        允許空 list（教材查無此項類已驗證答案無 citation）。"""
        if not isinstance(sources, list):
            return False
        for s in sources:
            if not isinstance(s, dict):
                return False
            bt = s.get("book_title")
            pg = s.get("page")
            if not isinstance(bt, str) or not bt:
                return False
            if not isinstance(pg, int) or isinstance(pg, bool):
                return False
        return True

    def _key(self, query: str, kb_version: int, metadata_filter: dict | None = None) -> str:
        norm = self.normalize_query(query)
        canon = self._canonical_filter(metadata_filter)
        digest = hashlib.sha256(f"{norm}\x00{canon}".encode("utf-8")).hexdigest()
        return f"{self._prefix}:kb{kb_version}:{digest}"
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k "normalize or canonical or key_varies or distinct" -q`
Expected: PASS（6 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/cache/semantic_cache.py backend/tests/test_api_semantic_cache_unit.py
git commit -m "feat(cache): SemanticCache normalize/_canonical_filter/_key（key 納入 kb_version+metadata_filter）"
```

---

## Task 3：`set` / `get`（verified 守門 + source 形狀防禦 + 完整隔離 + fail-open）

**Files:**
- Modify: `backend/src/anatomy_backend/cache/semantic_cache.py`
- Test: `backend/tests/test_api_semantic_cache_unit.py`（加 `_FakeRedis` + 行為測試）

- [ ] **Step 1：寫失敗測試**（先定義手刻 `_FakeRedis`）

```python
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
```

> 註：`_sources_ok` 對 set 內 `{1,2}`（set 型別）仍通過形狀檢查（`bad` 欄不檢查），但 `json.dumps` 會在 try 內拋 `TypeError`→fail-open，故 `set_calls==0`。

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k "set or get or false_hit" -q`
Expected: FAIL（`AttributeError: 'SemanticCache' object has no attribute 'get'`）

- [ ] **Step 3：實作 `get` / `set`**（加到 `_key` 之後）

```python
    async def get(
        self, query: str, kb_version: int, metadata_filter: dict | None = None
    ) -> CachedAnswer | None:
        try:
            raw = await self._redis.get(self._key(query, kb_version, metadata_filter))
        except Exception:  # noqa: BLE001  fail-open（§1.8）
            logger.warning("SemanticCache.get Redis 失敗→miss", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)   # json.loads 接受 bytes/str
        except (ValueError, TypeError):
            logger.warning("SemanticCache 損壞值→miss", exc_info=True)
            return None
        if not isinstance(payload, dict):
            return None
        # 防禦性校驗：schema / kb_version / verified / source 形狀，任一不符→miss
        if payload.get("v") != self._SCHEMA:
            return None
        if payload.get("kb_version") != kb_version:
            return None
        if not payload.get("verified"):
            return None
        answer = payload.get("answer")
        sources = payload.get("sources")
        if not isinstance(answer, str) or not self._sources_ok(sources):
            return None
        return CachedAnswer(answer=answer, sources=sources)

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
        metadata_filter: dict | None = None,
    ) -> None:
        # MUST：只快取已驗證答案（信任邊界＝chat.py verify_citations, DL-012）。
        if not verified:
            return
        # 結構防禦（Codex#2/#5）：拒絕非字串答案 / 損壞 source 形狀。
        if not isinstance(answer, str) or not self._sources_ok(sources):
            logger.warning("SemanticCache.set 拒絕：answer/sources 形狀不合法（防損壞引文入快取）")
            return
        try:
            payload = {
                "v": self._SCHEMA,
                "answer": answer,
                "sources": sources,
                "kb_version": kb_version,
                "verified": True,
            }
            value = json.dumps(payload, ensure_ascii=False).encode("utf-8")  # 序列化在 try 內
            await self._redis.set(self._key(query, kb_version, metadata_filter), value, ex=self._ttl)
        except Exception:  # noqa: BLE001  fail-open（含序列化失敗）
            logger.warning("SemanticCache.set Redis/序列化 失敗→no-op", exc_info=True)
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -q`
Expected: PASS（全部）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/cache/semantic_cache.py backend/tests/test_api_semantic_cache_unit.py
git commit -m "feat(cache): SemanticCache.get/set——verified 守門+source 形狀防禦+kb/filter 隔離+fail-open"
```

---

## Task 4：`clear_kb_version`（§6.6 版本切換清空；非原子但正確）

**Files:**
- Modify: `backend/src/anatomy_backend/cache/semantic_cache.py`
- Test: `backend/tests/test_api_semantic_cache_unit.py`

> **設計論證（回應 Codex#3）：** 版本隔離的**正確性**來自「key namespace 含 kb_version」+「線上只以 active kb_version 查」：一旦 `ACTIVE_KB_VERSION` 切換，舊版 namespace 不再被任何 `get` 觸及，殘留 key 不會造成錯答，並靠 TTL 自然消亡。`clear_kb_version` 是**記憶體回收**而非正確性 gate，故 SCAN 與並發 set 的非原子性不影響答案正確性。版本切換流程接線屬 §6.6 ops/runbook（v1 無 version-switch endpoint），不在本 phase 程式範圍。

- [ ] **Step 1：寫失敗測試**

```python
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
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k clear -q`
Expected: FAIL（`AttributeError ... 'clear_kb_version'`）

- [ ] **Step 3：實作 `clear_kb_version`**

```python
    async def clear_kb_version(self, kb_version: int) -> None:
        """清空指定 kb_version 的快取（§6.6 版本切換之記憶體回收）。

        以 namespace pattern SCAN + UNLINK，只清該版本——不用 FLUSHDB（避免誤清同
        Redis 的限流桶）。非原子；但版本隔離正確性來自 namespace+active-only 讀取，
        殘留 key 不致錯答（見本 task 設計論證）。fail-open。
        """
        pattern = f"{self._prefix}:kb{kb_version}:*"
        try:
            batch: list = []
            async for key in self._redis.scan_iter(match=pattern, count=500):
                batch.append(key)
                if len(batch) >= 500:
                    await self._redis.unlink(*batch)
                    batch = []
            if batch:
                await self._redis.unlink(*batch)
        except Exception:  # noqa: BLE001  fail-open
            logger.warning("SemanticCache.clear_kb_version Redis 失敗（部分清除）", exc_info=True)
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k clear -q`
Expected: PASS（3 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/cache/semantic_cache.py backend/tests/test_api_semantic_cache_unit.py
git commit -m "feat(cache): SemanticCache.clear_kb_version——namespace SCAN+UNLINK（非 FLUSHDB）+ 設計論證"
```

---

## Task 5：`CacheProtocol`/`NoOpCache` 簽章 + `build_cache` 分支 + 匯出

**Files:**
- Modify: `backend/src/anatomy_backend/cache/base.py`
- Modify: `backend/src/anatomy_backend/cache/__init__.py`
- Test: `backend/tests/test_api_semantic_cache_unit.py` + `backend/tests/test_api_cache_seam_unit.py`

- [ ] **Step 1：寫失敗測試**（追加到 unit 檔）

```python
from anatomy_backend.cache import NoOpCache, SemanticCache, build_cache


def test_build_cache_semantic_when_enabled_with_redis():
    assert isinstance(build_cache(_settings(cache_mode="exact"), _FakeRedis()), SemanticCache)


def test_build_cache_noop_when_disabled():
    assert isinstance(build_cache(_settings(cache_enabled=False), _FakeRedis()), NoOpCache)


def test_build_cache_noop_without_redis():
    assert isinstance(build_cache(_settings(cache_enabled=True), None), NoOpCache)


def test_build_cache_semantic_mode_not_implemented():
    with pytest.raises(NotImplementedError):
        build_cache(_settings(cache_mode="semantic"), _FakeRedis())


async def test_noop_accepts_metadata_filter():
    c = NoOpCache()
    assert await c.get("q", 1, {"a": 1}) is None
    await c.set("q", "a", [], 1, verified=True, metadata_filter={"a": 1})  # 不拋
```

並在 `test_api_cache_seam_unit.py` 補：

```python
def test_build_cache_noop_without_redis_arg():
    assert isinstance(build_cache(SimpleNamespace()), NoOpCache)  # 舊 no-arg 形狀仍回 NoOp
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k build_cache backend/tests/test_api_cache_seam_unit.py -q`
Expected: FAIL（`ImportError: SemanticCache` / `build_cache() takes 1 positional argument`）

- [ ] **Step 3：改 `base.py`**（`CacheProtocol`、`NoOpCache`、`build_cache` 三處納入 `metadata_filter`）

`CacheProtocol`：

```python
class CacheProtocol(Protocol):
    async def get(
        self, query: str, kb_version: int, metadata_filter: dict | None = None
    ) -> CachedAnswer | None: ...

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
        metadata_filter: dict | None = None,
    ) -> None: ...
```

`NoOpCache`：

```python
class NoOpCache:
    async def get(
        self, query: str, kb_version: int, metadata_filter: dict | None = None
    ) -> CachedAnswer | None:
        return None

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
        metadata_filter: dict | None = None,
    ) -> None:
        return None
```

`build_cache`：

```python
def build_cache(settings, redis_client=None) -> CacheProtocol:
    """依設定回傳快取實作（DL-025）。

    cache_enabled=False 或無 redis_client → NoOpCache（退路）。
    cache_mode="exact"（v1 預設）→ SemanticCache（exact-normalized-query）。
    cache_mode="semantic" → 後續 config 開關，尚未實作（需 fastembed，torch-free）。
    """
    if not getattr(settings, "cache_enabled", True) or redis_client is None:
        return NoOpCache()
    mode = getattr(settings, "cache_mode", "exact")
    if mode == "exact":
        from anatomy_backend.cache.semantic_cache import SemanticCache

        return SemanticCache(redis_client, ttl_seconds=settings.cache_ttl_seconds)
    if mode == "semantic":
        raise NotImplementedError(
            "cache_mode='semantic' 向量比對尚未啟用（需 fastembed，torch-free；見 DL-025 / DL-012）"
        )
    raise ValueError(f"未知 cache_mode：{mode!r}")
```

- [ ] **Step 4：改 `__init__.py`**

```python
from anatomy_backend.cache.base import CachedAnswer, CacheProtocol, NoOpCache, build_cache
from anatomy_backend.cache.semantic_cache import SemanticCache

__all__ = ["CachedAnswer", "CacheProtocol", "NoOpCache", "SemanticCache", "build_cache"]
```

- [ ] **Step 5：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py backend/tests/test_api_cache_seam_unit.py -q`
Expected: PASS（含既有 seam）

- [ ] **Step 6：commit**

```bash
git add backend/src/anatomy_backend/cache/base.py backend/src/anatomy_backend/cache/__init__.py backend/tests/test_api_semantic_cache_unit.py backend/tests/test_api_cache_seam_unit.py
git commit -m "feat(cache): CacheProtocol/NoOpCache 納入 metadata_filter + build_cache 依設定回傳 SemanticCache"
```

---

## Task 6：接線 `main.py` + `chat.py`（傳 redis_client / metadata_filter）

**Files:**
- Modify: `backend/src/anatomy_backend/api/main.py:76`
- Modify: `backend/src/anatomy_backend/api/chat.py`（lines 93、250）
- Test: `backend/tests/test_api_chat_sse_unit.py`（加 `_RecordingCache` 測試）

- [ ] **Step 1：寫失敗測試**（反 Codex#1/#5：chat 必須把 metadata_filter 傳給快取）

在 `test_api_chat_sse_unit.py` 加（mirror 既有 `_build_deps` harness、用 `chat_event_stream` 直接驅動）：

```python
async def test_chat_threads_metadata_filter_into_cache():
    """chat 將 metadata_filter 傳給 cache.get（lookup）與 cache.set（write）。"""
    import anatomy_backend.api.chat as chatmod
    from anatomy_backend.api.chat import ChatDeps, chat_event_stream
    from anatomy_backend.api.schemas import normalize_chat
    from anatomy_backend.cache import CachedAnswer

    class _RecordingCache:
        def __init__(self):
            self.get_args = []
            self.set_args = []

        async def get(self, query, kb_version, metadata_filter=None):
            self.get_args.append((query, kb_version, metadata_filter))
            return None   # 強制 miss → 走完整流程到 set

        async def set(self, query, answer, sources, kb_version, *, verified, metadata_filter=None):
            self.set_args.append((query, kb_version, verified, metadata_filter))

    cache = _RecordingCache()
    mf = {"anatomy_system": "musculoskeletal"}
    # 用既有測試 helper 組 deps（見本檔上方 _build_deps / fakes），cache 換成 _RecordingCache。
    # normalize_chat 以含 metadata_filter 的 body 正規化（非追問）。
    normalized = normalize_chat({"messages": [{"role": "user", "content": "肱二頭肌的起止點"}],
                                 "metadata_filter": mf})
    deps = _make_chat_deps(cache=cache)   # ← 實作：複用本檔既有 fake encoder/llm/retrieve/sign/fetch/spawn
    user = _make_user()                    # ← 既有 helper 或直接 User(...)
    spawned = []
    deps.spawn = lambda coro: spawned.append(coro)
    async for _ in chat_event_stream(deps, normalized, user):
        pass
    # 收尾 spawn 的 cache.set coroutine 需被執行
    for coro in spawned:
        try:
            await coro
        except Exception:
            pass
    assert cache.get_args and cache.get_args[0][2] == mf      # lookup 帶 filter
    assert cache.set_args and cache.set_args[0][3] == mf      # write 帶 filter
```

> 實作備註給工人：本檔已有 `_build_deps`（line ~157）與各 fake（encoder/llm/`_retrieve`/sign/fetch）。請抽出一個 `_make_chat_deps(cache=...)` 或直接在測試內 inline 組 `ChatDeps`（mirror line 157-170），把 `cache` 換成 `_RecordingCache()`、`spawn` 換成收集器。`_make_user()` 可用既有方式或 `from anatomy_backend.api.auth import User` 直接建。retrieve fake 須回非空 results 以使 `verification.all_grounded=True`、status=ok，才會觸發 `cache.set`（若既有 fake 已滿足即可）。

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_chat_sse_unit.py::test_chat_threads_metadata_filter_into_cache -q`
Expected: FAIL（cache.get 收到的 metadata_filter 為預設 None，因 chat.py 尚未傳）

- [ ] **Step 3：改 `chat.py` 兩處呼叫**

line 93：
```python
        cached = await deps.cache.get(normalized.query, kb, normalized.metadata_filter)
```

line 250：
```python
            deps.cache.set(
                normalized.query, answer, sources_payload, kb,
                verified=True, metadata_filter=normalized.metadata_filter,
            )
```

- [ ] **Step 4：改 `main.py:76`**

```python
    cache = build_cache(settings, redis_client)
```

- [ ] **Step 5：跑測試確認通過 + 既有 chat/lifespan 不破**

Run: `uv run --no-sync pytest backend/tests/ -k "chat or lifespan or main or cache" -q`
Expected: PASS（含新 metadata_filter 測試；e2e ASGITransport 不啟 lifespan、注入 fakes 不受影響）

- [ ] **Step 6：commit**

```bash
git add backend/src/anatomy_backend/api/main.py backend/src/anatomy_backend/api/chat.py backend/tests/test_api_chat_sse_unit.py
git commit -m "feat(cache): chat/main 接線——cache 帶 metadata_filter（修 Codex#1 critical）+ 啟用 SemanticCache"
```

---

## Task 7：真 Redis 整合測試 + conftest 假綠防呆

**Files:**
- Create: `backend/tests/test_api_semantic_cache_integration.py`
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1：conftest 加 REDIS 防呆**（鏡像 `REQUIRE_DB_TESTS`）

`_DB_ENV_READY` 之後加：

```python
_REDIS_ENV_READY = bool(os.environ.get("REDIS_URL"))
```

`pytest_configure` 內（`REQUIRE_DB_TESTS` 檢查之後）加：

```python
    if os.environ.get("REQUIRE_REDIS_TESTS") == "1" and not _REDIS_ENV_READY:
        raise pytest.UsageError("REQUIRE_REDIS_TESTS=1 但缺 REDIS_URL")
```

- [ ] **Step 2：寫整合測試**

```python
"""Phase 7 SemanticCache 真 Redis 整合測試。

需 REDIS_URL（CI db-integration job 已設 redis://localhost:6379/0；本機用 compose redis）。
無 REDIS_URL → 自動 skip。每個 cache 用唯一 key_prefix 隔離，測試後清掉自身 namespace。
"""
from __future__ import annotations

import os
import uuid

import pytest

from anatomy_backend.cache.semantic_cache import SemanticCache

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("REDIS_URL"),
        reason="需真 Redis：設 REDIS_URL（CI db-integration 已設；本機用 compose redis）",
    ),
]

SRC = [{"book_title": "Gray's", "edition": "42e", "page": 12, "figure": None,
        "image_url": "http://x/y.png", "snippet": "...", "score": 0.9}]


@pytest.fixture
async def redis_client():
    import redis.asyncio as aioredis

    client = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def cache(redis_client):
    prefix = f"semcache_test_{uuid.uuid4().hex}"
    c = SemanticCache(redis_client, ttl_seconds=60, key_prefix=prefix)
    try:
        yield c
    finally:
        async for k in redis_client.scan_iter(match=f"{prefix}:*", count=500):
            await redis_client.unlink(k)


async def test_real_redis_set_get_hit(cache):
    await cache.set("肱二頭肌起止點", "起於...[Gray's, p.12]", SRC, 1, verified=True)
    hit = await cache.get(" 肱二頭肌起止點 ", 1)
    assert hit is not None and hit.answer == "起於...[Gray's, p.12]" and hit.sources == SRC


async def test_real_redis_cross_kb_miss(cache):
    await cache.set("q", "a", SRC, 1, verified=True)
    assert await cache.get("q", 2) is None


async def test_real_redis_cross_metadata_filter_miss(cache):
    await cache.set("q", "musc", SRC, 1, verified=True, metadata_filter={"anatomy_system": "musculoskeletal"})
    assert await cache.get("q", 1, {"anatomy_system": "nervous"}) is None
    assert await cache.get("q", 1, None) is None
    hit = await cache.get("q", 1, {"anatomy_system": "musculoskeletal"})
    assert hit is not None and hit.answer == "musc"


async def test_real_redis_unverified_not_written(cache, redis_client):
    await cache.set("q", "a", SRC, 1, verified=False)
    assert await cache.get("q", 1) is None
    assert await redis_client.get(cache._key("q", 1)) is None


async def test_real_redis_ttl_applied(cache, redis_client):
    await cache.set("q", "a", SRC, 1, verified=True)
    ttl = await redis_client.ttl(cache._key("q", 1))
    assert 0 < ttl <= 60


async def test_real_redis_clear_kb_version(cache):
    await cache.set("q1", "a1", SRC, 1, verified=True)
    await cache.set("q2", "a2", SRC, 2, verified=True)
    await cache.clear_kb_version(1)
    assert await cache.get("q1", 1) is None
    assert await cache.get("q2", 2) is not None
```

- [ ] **Step 3：本機跑整合測試（需 compose redis）**

Run（先 `make up`）:
```bash
REDIS_URL=redis://localhost:6379/0 uv run --no-sync pytest backend/tests/test_api_semantic_cache_integration.py -q -m integration
```
Expected: PASS（6 passed）

- [ ] **Step 4：確認無 REDIS_URL 會 skip**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_integration.py -q`
Expected: `6 skipped`

- [ ] **Step 5：commit**

```bash
git add backend/tests/test_api_semantic_cache_integration.py backend/tests/conftest.py
git commit -m "test(cache): 真 Redis 整合測試（set/get/cross-kb/cross-filter/未驗證/TTL/clear）+ REQUIRE_REDIS_TESTS 防呆"
```

---

## Task 8：CI 守門（零-OpenAI grep + REQUIRE_REDIS_TESTS）

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1：unit job 加 cache 零-OpenAI grep**（line 26 ingest 紅線 grep 之後）

```yaml
      - name: 確認語意快取層無雲端 LLM SDK（DL-012：本地比對、零 OpenAI）
        run: "! grep -rInE --include='*.py' '(import|from)\\s+openai\\b' backend/src/anatomy_backend/cache || (echo '語意快取禁 import openai（線上 lookup 本地比對，DL-012）' && exit 1)"
```

- [ ] **Step 2：db-integration「DB 整合測試」step env 加 `REQUIRE_REDIS_TESTS`**（line 78-84 env 區塊）

```yaml
          REQUIRE_REDIS_TESTS: "1"               # conftest guard：REDIS_URL 漏傳時 fail（防快取整合測試假綠）
```

（`REDIS_URL` 已在該 step；`-m "db or integration"` 已選到 `integration` 標記測試，run 行不變。）

- [ ] **Step 3：本機驗 grep 守門**

Run:
```bash
! grep -rInE --include='*.py' '(import|from)\s+openai\b' backend/src/anatomy_backend/cache && echo "GUARD OK：cache 層無 openai"
```
Expected: 印 `GUARD OK：cache 層無 openai`

- [ ] **Step 4：commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: 快取層零-OpenAI grep 守門 + db-integration REQUIRE_REDIS_TESTS（防假綠）"
```

---

## Task 9：DL-025 寫入 decisions.md

**Files:**
- Modify: `docs/decisions.md`（DL-024 之後追加）

- [ ] **Step 1：追加 DL-025**

```markdown

## DL-025: 語意快取 v1＝exact-normalized-query（含 metadata_filter）；語意向量列後續開關（fastembed）

- **狀態**：APPROVED　**提案者**：main Claude（Phase 7）　**日期**：2026-06-14　**裁決者**：專案負責人（2026-06-14 確認，含 Codex 對抗式審查修訂）
- **影響檔案**：ARCHITECTURE.md §6.4、§1.6、§6.6；`backend/.../cache/semantic_cache.py`、`base.py`、`config.py`、`api/main.py`、`api/chat.py`

### 背景
§6.4 / DL-004 / DL-012 已定：線上 lookup 用本地輕量 embedding、不打 OpenAI，「初期用 exact-normalized-query 較安全」。Phase 7 落地需定案 v1 模式、套件選型，並回應 Codex 對抗式審查發現的正確性缺口。

### 提案（與 DL-004/DL-012 一致，屬落地記錄、非變更 DECIDED）
1. **v1 預設 `cache_mode="exact"`**：把『正規化 query（NFKC→trim→casefold→摺疊空白）+ canonical metadata_filter』以 SHA-256 雜湊成 key；決定性、命中遠在 §1.6 ~30ms 內、**零 embedding 套件**。
2. **完整隔離鍵（修 Codex#1 critical）**：cache key **MUST** 納入 `kb_version`（namespace）**與 `metadata_filter`**（canonical sort_keys JSON）——因 `metadata_filter` 會改變檢索結果與答案；命中時再校驗 envelope `kb_version`。
3. **誤命中誠實化（修 Codex#4）**：exact 模式為決定性、誤命中極低（僅正規化等價字串會併，如 casefold 後 US/us）；**非「零誤命中」**。即便誤命中，回的仍是已驗證、有引文的答案（安全網守住）。醫學術語 precision/碰撞語料列為 **Phase 11 eval gate**。
4. **只快取已驗證 + 結構防禦（修 Codex#2/#5）**：`set(verified=False)` 一律拒寫；信任邊界＝`chat.py` 的 `verify_citations`（DL-012）。**不**在快取內重做引文驗證（會把 cache 耦合到 retrieval）；改加 source 形狀防禦（每個 source 須 dict 且具 `book_title`+`page`），損壞→`set` 拒寫 / `get` 視為 miss。
5. **版本切換清空（澄清 Codex#3）**：版本隔離正確性來自 namespace + active-only 讀取——切版後舊 namespace 不再被查、靠 TTL 消亡，殘留 key 不致錯答。`clear_kb_version()` 以 namespace SCAN+UNLINK 做記憶體回收（取代 §6.6 的 `FLUSHDB`，避免誤清限流桶）；非原子但不影響正確性。切版接線屬 §6.6 ops/runbook（v1 無 endpoint）。
6. **Redis fail-open（含序列化/mid-scan 失敗）**：`get`→miss、`set`/`clear`→no-op，絕不中斷 `/chat`（§1.8）。快取 hit-rate/failure **metrics 接 LangFuse/Sentry 屬 Phase 9**；本 phase 以結構化 warning log 為界。
7. **語意向量比對列後續 config 開關**（`cache_mode="semantic"`）：啟用時用 **fastembed（ONNX, torch-free, `intfloat/multilingual-e5-small` 384d）**，**不**用 redisvl 預設 `HFTextVectorizer`（會把 torch 拉進 backend、破壞 torch-free 不變量）；`cosine_distance < cache_distance_threshold(0.05)` ≈ sim > 0.95。啟用前須補 RAGAS/誤命中評估。
8. **不新增套件**：`redis`/`redisvl` 已在 deps；本 phase runtime/測試皆零新套件。

### 後果
- 命中率上限受限於「字面正規化後相同」；換句話/繁簡同義待語意開關才涵蓋。先求安全側（低誤命中、命中即已驗證有引文）。
- 啟用 semantic 模式須走本決策第 7 點並過評估 gate 才上線。
```

- [ ] **Step 2：commit**

```bash
git add docs/decisions.md
git commit -m "docs(decisions): DL-025 語意快取 v1=exact-normalized-query（含 metadata_filter）、語意向量後續開關"
```

---

## Task 10：全套件回歸 + lint + 收尾

- [ ] **Step 1：ruff check（勿 ruff format）**

Run: `uv run --no-sync ruff check backend/src/anatomy_backend/cache backend/src/anatomy_backend/config.py backend/src/anatomy_backend/api/main.py backend/src/anatomy_backend/api/chat.py backend/tests/test_api_semantic_cache_unit.py backend/tests/test_api_semantic_cache_integration.py backend/tests/test_api_chat_sse_unit.py backend/tests/conftest.py backend/tests/test_api_cache_seam_unit.py`
Expected: `All checks passed!`（import 排序問題用 `ruff check --fix`，**勿** `ruff format .`）

- [ ] **Step 2：backend 全 unit 回歸（無 redis，整合測試 skip）**

Run: `uv run --no-sync pytest backend/tests -q`
Expected: 全綠；`test_api_semantic_cache_integration.py` 6 skipped

- [ ] **Step 3：本機真 Redis 整合（compose redis up 後）**

Run:
```bash
REDIS_URL=redis://localhost:6379/0 uv run --no-sync pytest backend/tests -q -m "integration" -k semantic_cache
```
Expected: 6 passed

- [ ] **Step 4：最終確認**

Run: `git status && git log --oneline -12`
Expected: working tree clean；Task 1–9 commit 可見

---

## Self-Review（spec + Codex 對照）

| spec / 驗收 / Codex finding | 對應 task |
|---|---|
| `SemanticCache` 實作 CacheProtocol、build_cache 依設定回傳 | Task 2/3/5 |
| MUST 只快取已驗證（set verified=False 拒寫） | Task 3 + Task 7 |
| **[Codex#1 critical] key 納入 metadata_filter** | Task 2（_key）+ Task 5（Protocol）+ Task 6（chat 接線）+ Task 3/7（cross-filter 測試） |
| [Codex#2/#5] verified 信任邊界 + source 形狀防禦 | Task 3（_sources_ok）+ DL-025 第 4 點 |
| [Codex#3] 版本隔離正確性 + clear 論證 | Task 4（論證+mid-scan 測試）+ DL-025 第 5 點 |
| [Codex#4] 撤回「零誤命中」+ Phase 11 gate | Task 2（distinct 測試）+ 文件/ DL-025 第 3 點 |
| [Codex#6] json.dumps 進 try + fail-open 測試 | Task 3（序列化）+ Task 4（mid-scan） |
| kb_version namespace + 命中校驗 + 版本切換清空 | Task 2/3/4 + Task 7 |
| 本地 embedding、零 OpenAI（DL-012；CI 守門） | 設計（無 openai import）+ Task 8 |
| TTL 7–30 天（預設 14） | Task 1 + Task 3 + Task 7 |
| Redis 故障 fail-open | Task 3/4/7 |
| 相似不同題不誤命中（單元） | Task 3 |
| 真 Redis 驗收（set/get/cross-kb/cross-filter/clear/零 OpenAI） | Task 7 + Task 8 |
| decisions.md DL-025 | Task 9 |

**Placeholder scan：** 無 TODO/TBD；唯一「工人備註」在 Task 6 Step 1（明確指示複用既有 chat 測試 harness，非佔位）。
**Type 一致性：** `CachedAnswer(answer:str, sources:list[dict])`、`SemanticCache(redis, *, ttl_seconds, key_prefix=None)`、`get(query,kb_version,metadata_filter=None)`、`set(query,answer,sources,kb_version,*,verified,metadata_filter=None)`、`_canonical_filter`、`_sources_ok`、`clear_kb_version(kb_version)`、`build_cache(settings, redis_client=None)` 跨 task 一致。
