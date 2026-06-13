# Phase 7 — 語意快取 SemanticCache 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以「exact-normalized-query」為 v1 預設實作 `SemanticCache`（填滿 Phase 8 留下的 `CacheProtocol` seam），只快取通過引文驗證的答案、以 `kb_version` namespace 隔離、Redis 故障 fail-open，並讓 `build_cache` 在設定下回傳它。

**Architecture:** `SemanticCache` 把「正規化後的 query」雜湊成決定性 key（含 `kb_version` namespace），值是 JSON envelope `{v, answer, sources, kb_version, verified}` 存進既有的 `redis.asyncio` client，TTL 由 Redis `SET ... EX` 控制。線上 lookup **零 OpenAI、零 embedding 套件、零誤命中**（決定性字面比對）。語意向量比對（fastembed，torch-free）列為**後續 config 開關**（`cache_mode="semantic"`，本 phase 不實作、留清楚的 `NotImplementedError` seam）。決策記於新增的 **DL-025**。

**Tech Stack:** Python 3.11、`redis.asyncio`（已在 deps，**不新增任何套件**）、`hashlib.sha256`、`unicodedata` NFKC、pytest（單元用手刻 `_FakeRedis`；整合用 CI db-integration job 既有的 `redis:7-alpine`）。

**範圍護欄（交 Codex 對抗式審查挑戰）：**
- **MUST 只快取已驗證答案**：`set(verified=False)` 一律拒寫（§6.4 / DL-012 / 安全網核心）。
- **kb_version 隔離**：key namespace 含 `kb_version`；命中時再校驗 envelope `kb_version`，不符→miss；提供 `clear_kb_version()` 供 §6.6 版本切換清空（不用 FLUSHDB，避免誤清限流桶）。
- **本地 lookup、DL-012**：快取層 **MUST NOT** `import openai`；CI grep 守門。
- **Redis fail-open（§1.8）**：`get` 例外→miss、`set`/`clear` 例外→no-op，**絕不**讓快取故障中斷 `/chat`。
- **追問（DL-021）**：由 `chat.py` 控制不查/不寫；`SemanticCache` 保持追問無關，但 `set` 仍防禦性拒絕未驗證。
- **TTL 7–30 天**（config 預設 14 天 = 1209600s）。

**不新增套件確認（使用者 2026-06-14 核准）：** runtime 套件 0（`redis`/`redisvl` 已在 backend deps）；測試套件 0（整合測試用 CI 既有 real redis + 手刻 fake，沿用 ratelimit 測試 pattern）；不引入 fastembed / sentence-transformers / fakeredis。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `backend/src/anatomy_backend/cache/semantic_cache.py` | **Create** | `SemanticCache`（`normalize_query` / `get` / `set` / `clear_kb_version`）；exact-normalized-query v1 |
| `backend/src/anatomy_backend/cache/base.py` | Modify | `build_cache(settings, redis_client=None)` 分支：enabled+redis+exact→SemanticCache；semantic→NotImplementedError；否則 NoOpCache |
| `backend/src/anatomy_backend/cache/__init__.py` | Modify | 匯出 `SemanticCache` |
| `backend/src/anatomy_backend/config.py` | Modify | 新增 `cache_enabled: bool = True`、`cache_mode: str = "exact"` |
| `backend/src/anatomy_backend/api/main.py` | Modify | line 76：`build_cache(settings, redis_client)` |
| `backend/tests/test_api_semantic_cache_unit.py` | **Create** | 純單元（手刻 `_FakeRedis`，無真 Redis）：normalize / set 拒未驗證 / roundtrip / cross-kb / 損壞值 / fail-open / clear / build_cache 分支 |
| `backend/tests/test_api_semantic_cache_integration.py` | **Create** | `@pytest.mark.integration` + skipif 無 `REDIS_URL`：真 Redis set/get、cross-kb、未驗證不寫、TTL、clear |
| `backend/tests/conftest.py` | Modify | 加 `REQUIRE_REDIS_TESTS` 假綠防呆（鏡像 `REQUIRE_DB_TESTS`） |
| `backend/tests/test_api_cache_seam_unit.py` | Modify | 既有 `build_cache(SimpleNamespace())`→NoOp 仍須綠；補「有 redis→SemanticCache」測試 |
| `.github/workflows/ci.yml` | Modify | unit job 加 cache 零-OpenAI grep；db-integration「DB 整合測試」step 加 `REQUIRE_REDIS_TESTS: "1"` |
| `docs/decisions.md` | Modify | 追加 **DL-025**（APPROVED） |

---

## Task 1：config 旗標 `cache_enabled` / `cache_mode`

**Files:**
- Modify: `backend/src/anatomy_backend/config.py:33-36`（在語意快取設定區塊內補兩欄）
- Test: `backend/tests/test_api_semantic_cache_unit.py`（本 task 先建檔，放 config 測試）

- [ ] **Step 1：寫失敗測試**

建立 `backend/tests/test_api_semantic_cache_unit.py`，先放 config 預設值測試：

```python
"""Phase 7 SemanticCache 單元測試（exact-normalized-query；手刻 _FakeRedis，無真 Redis）。"""
from __future__ import annotations

import json

from anatomy_backend.config import Settings


def _settings(**over):
    # 提供 config 必填的 DSN（通過 :6432/:5432 validator），其餘取預設
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
Expected: FAIL（`AttributeError: ... 'cache_enabled'` / `'cache_mode'`）

- [ ] **Step 3：加 config 欄位**

在 `config.py` 的「# 語意快取設定」區塊（`cache_ttl_seconds` 之後）加：

```python
    # 語意快取啟用旗標與比對模式（DL-025）
    # exact：正規化後字面比對（v1 預設，零誤命中、零 embedding 套件）
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

## Task 2：`normalize_query`（決定性正規化）

**Files:**
- Create: `backend/src/anatomy_backend/cache/semantic_cache.py`
- Test: `backend/tests/test_api_semantic_cache_unit.py`

- [ ] **Step 1：寫失敗測試**（追加到測試檔）

```python
from anatomy_backend.cache.semantic_cache import SemanticCache


def test_normalize_trims_lowercases_collapses_ws():
    n = SemanticCache.normalize_query
    assert n("  Biceps   Brachii  ") == n("biceps brachii")
    assert n("Hello\tWorld\n") == "hello world"


def test_normalize_nfkc_fullwidth_to_halfwidth():
    n = SemanticCache.normalize_query
    # 全形英數字 / 全形問號 → 半形（NFKC），與半形視為同一 query
    assert n("ＡＢＣ１２３") == "abc123"
    assert n("肱二頭肌？") == n("肱二頭肌?")


def test_normalize_is_deterministic_idempotent():
    n = SemanticCache.normalize_query
    once = n("　肱二頭肌 的起止點　")  # 含全形空白
    assert once == n(once)
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k normalize -q`
Expected: FAIL（`ModuleNotFoundError: ...semantic_cache`）

- [ ] **Step 3：建立 `semantic_cache.py` 與 `normalize_query`**

```python
"""語意快取（§6.4 / DL-004 / DL-012 / DL-025）。

v1 預設＝exact-normalized-query：把正規化後的 query 雜湊成決定性 key，
zero embedding 套件、zero 誤命中、命中 <30ms（§1.6）。語意向量比對為
後續 config 開關（fastembed，torch-free；cache_mode="semantic"）。

硬性規則：
- 只快取已驗證答案：set(verified=False) 一律拒寫（§6.4 / DL-012 安全網核心）。
- kb_version 隔離：key namespace 含 kb_version + 命中時校驗 envelope kb_version。
- 本地 lookup：本模組 MUST NOT import openai（DL-012；CI grep 守門）。
- Redis fail-open（§1.8）：get 例外→miss、set/clear 例外→no-op，絕不中斷 /chat。
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

    def _key(self, query: str, kb_version: int) -> str:
        digest = hashlib.sha256(self.normalize_query(query).encode("utf-8")).hexdigest()
        return f"{self._prefix}:kb{kb_version}:{digest}"
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k normalize -q`
Expected: PASS（3 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/cache/semantic_cache.py backend/tests/test_api_semantic_cache_unit.py
git commit -m "feat(cache): SemanticCache.normalize_query 決定性正規化（NFKC/trim/casefold/ws）"
```

---

## Task 3：`set` / `get`（verified 守門 + kb namespace + envelope + fail-open）

**Files:**
- Modify: `backend/src/anatomy_backend/cache/semantic_cache.py`（加 `get`/`set`）
- Test: `backend/tests/test_api_semantic_cache_unit.py`（加 `_FakeRedis` + 行為測試）

- [ ] **Step 1：寫失敗測試**（追加；先定義手刻 `_FakeRedis`）

```python
import fnmatch

import pytest


class _FakeRedis:
    """最小 async Redis 替身（沿用 ratelimit 測試手刻 fake 的慣例）。

    fail=True 時 get/set/scan/unlink 全拋，用於 fail-open 測試。
    decode_responses=False 語意：value 存 bytes、回 bytes。
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, bytes] = {}
        self.ex: dict[str, int] = {}
        self.set_calls = 0
        self._fail = fail

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
        for k in list(self.store.keys()):
            if match is None or fnmatch.fnmatchcase(k, match):
                yield k

    async def unlink(self, *keys):
        if self._fail:
            raise RuntimeError("redis down")
        for k in keys:
            self.store.pop(k, None)
            self.ex.pop(k, None)


def _cache(redis, ttl=1209600):
    return SemanticCache(redis, ttl_seconds=ttl)


async def test_set_rejects_unverified_answer():
    r = _FakeRedis()
    await _cache(r).set("肱二頭肌起止點", "造假答案", [{"book_title": "X", "page": 1}], 1, verified=False)
    assert r.set_calls == 0            # 一律不寫
    assert r.store == {}


async def test_set_then_get_roundtrip_hit():
    r = _FakeRedis()
    c = _cache(r)
    src = [{"book_title": "Gray's", "edition": "42e", "page": 12, "figure": "Fig.1",
            "image_url": "http://x/y.png", "snippet": "...", "score": 0.9}]
    await c.set("肱二頭肌起止點", "起於...止於...[Gray's, p.12]", src, 1, verified=True)
    assert r.set_calls == 1
    assert r.ex[next(iter(r.store))] == 1209600          # TTL 已設
    hit = await c.get("  肱二頭肌起止點 ", 1)              # 正規化後同 key → 命中
    assert hit is not None
    assert hit.answer == "起於...止於...[Gray's, p.12]"
    assert hit.sources == src


async def test_get_miss_when_absent():
    assert await _cache(_FakeRedis()).get("沒寫過", 1) is None


async def test_get_cross_kb_version_miss():
    r = _FakeRedis()
    c = _cache(r)
    await c.set("q", "a", [{"book_title": "X", "page": 1}], 1, verified=True)
    assert await c.get("q", 2) is None                   # 不同 kb_version → 不同 key → miss


async def test_get_kb_envelope_mismatch_miss():
    # 防禦：即使 key 撞到，envelope kb_version 不符也視為 miss
    r = _FakeRedis()
    c = _cache(r)
    key = c._key("q", 1)
    r.store[key] = json.dumps(
        {"v": 1, "answer": "a", "sources": [], "kb_version": 999, "verified": True}
    ).encode("utf-8")
    assert await c.get("q", 1) is None


async def test_get_corrupt_value_is_miss_not_raise():
    r = _FakeRedis()
    c = _cache(r)
    r.store[c._key("q", 1)] = b"\\xff not-json"
    assert await c.get("q", 1) is None                   # 不拋、視為 miss


async def test_get_unknown_schema_miss():
    r = _FakeRedis()
    c = _cache(r)
    r.store[c._key("q", 1)] = json.dumps(
        {"v": 999, "answer": "a", "sources": [], "kb_version": 1, "verified": True}
    ).encode("utf-8")
    assert await c.get("q", 1) is None


async def test_get_fail_open_returns_none():
    assert await _cache(_FakeRedis(fail=True)).get("q", 1) is None  # Redis 故障→miss


async def test_set_fail_open_no_raise():
    await _cache(_FakeRedis(fail=True)).set("q", "a", [], 1, verified=True)  # 不拋


async def test_similar_but_different_query_no_false_hit():
    # exact 模式：相似但不同題 → 不同正規化 → 不同 key → 不誤命中
    r = _FakeRedis()
    c = _cache(r)
    await c.set("肱二頭肌的起止點", "二頭肌答案", [{"book_title": "X", "page": 1}], 1, verified=True)
    assert await c.get("肱三頭肌的起止點", 1) is None
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k "set or get or false_hit" -q`
Expected: FAIL（`AttributeError: 'SemanticCache' object has no attribute 'get'`）

- [ ] **Step 3：實作 `get` / `set`**（加到 `semantic_cache.py` `_key` 之後）

```python
    async def get(self, query: str, kb_version: int) -> CachedAnswer | None:
        try:
            raw = await self._redis.get(self._key(query, kb_version))
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
        # 防禦性校驗：schema / kb_version / verified 任一不符→miss
        if payload.get("v") != self._SCHEMA:
            return None
        if payload.get("kb_version") != kb_version:
            return None
        if not payload.get("verified"):
            return None
        answer = payload.get("answer")
        sources = payload.get("sources")
        if not isinstance(answer, str) or not isinstance(sources, list):
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
    ) -> None:
        # MUST：拒絕未驗證答案（§6.4 / DL-012 安全網核心；別假設一定非追問）
        if not verified:
            return
        payload = {
            "v": self._SCHEMA,
            "answer": answer,
            "sources": sources,
            "kb_version": kb_version,
            "verified": True,
        }
        value = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            await self._redis.set(self._key(query, kb_version), value, ex=self._ttl)
        except Exception:  # noqa: BLE001  fail-open（§1.8）
            logger.warning("SemanticCache.set Redis 失敗→no-op", exc_info=True)
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -q`
Expected: PASS（全部）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/cache/semantic_cache.py backend/tests/test_api_semantic_cache_unit.py
git commit -m "feat(cache): SemanticCache.get/set——verified 守門+kb namespace+envelope+fail-open"
```

---

## Task 4：`clear_kb_version`（§6.6 版本切換清空，不用 FLUSHDB）

**Files:**
- Modify: `backend/src/anatomy_backend/cache/semantic_cache.py`（加 `clear_kb_version`）
- Test: `backend/tests/test_api_semantic_cache_unit.py`

- [ ] **Step 1：寫失敗測試**

```python
async def test_clear_kb_version_only_clears_target():
    r = _FakeRedis()
    c = _cache(r)
    await c.set("q1", "a1", [{"book_title": "X", "page": 1}], 1, verified=True)
    await c.set("q2", "a2", [{"book_title": "X", "page": 2}], 1, verified=True)
    await c.set("q3", "a3", [{"book_title": "X", "page": 3}], 2, verified=True)
    await c.clear_kb_version(1)
    assert await c.get("q1", 1) is None       # kb1 已清
    assert await c.get("q2", 1) is None
    assert await c.get("q3", 2) is not None    # kb2 不受影響


async def test_clear_kb_version_fail_open_no_raise():
    await _cache(_FakeRedis(fail=True)).clear_kb_version(1)  # Redis 故障不拋
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k clear -q`
Expected: FAIL（`AttributeError: ... 'clear_kb_version'`）

- [ ] **Step 3：實作 `clear_kb_version`**

```python
    async def clear_kb_version(self, kb_version: int) -> None:
        """清空指定 kb_version 的所有快取（§6.6 版本切換）。

        以 namespace pattern SCAN + UNLINK，只清該版本——不用 FLUSHDB，
        避免誤清同 Redis 的限流桶等其他 key。fail-open。
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
            logger.warning("SemanticCache.clear_kb_version Redis 失敗", exc_info=True)
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k clear -q`
Expected: PASS（2 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/cache/semantic_cache.py backend/tests/test_api_semantic_cache_unit.py
git commit -m "feat(cache): SemanticCache.clear_kb_version——namespace SCAN+UNLINK（非 FLUSHDB）"
```

---

## Task 5：`build_cache` 分支 + `__init__` 匯出 + seam 測試

**Files:**
- Modify: `backend/src/anatomy_backend/cache/base.py:46-47`（`build_cache`）
- Modify: `backend/src/anatomy_backend/cache/__init__.py`
- Test: `backend/tests/test_api_semantic_cache_unit.py` + `backend/tests/test_api_cache_seam_unit.py`

- [ ] **Step 1：寫失敗測試**（build_cache 分支；追加到 unit 檔）

```python
from anatomy_backend.cache import NoOpCache, SemanticCache, build_cache


def test_build_cache_semantic_when_enabled_with_redis():
    c = build_cache(_settings(cache_enabled=True, cache_mode="exact"), _FakeRedis())
    assert isinstance(c, SemanticCache)


def test_build_cache_noop_when_disabled():
    c = build_cache(_settings(cache_enabled=False), _FakeRedis())
    assert isinstance(c, NoOpCache)


def test_build_cache_noop_without_redis():
    c = build_cache(_settings(cache_enabled=True), None)
    assert isinstance(c, NoOpCache)


def test_build_cache_semantic_mode_not_implemented():
    with pytest.raises(NotImplementedError):
        build_cache(_settings(cache_mode="semantic"), _FakeRedis())
```

並在 `backend/tests/test_api_cache_seam_unit.py` 補一條（確認既有 no-arg 行為不破）：

```python
def test_build_cache_noop_without_redis_arg():
    # 無 redis_client（舊呼叫形狀）→ 仍回退 NoOpCache
    assert isinstance(build_cache(SimpleNamespace()), NoOpCache)
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py -k build_cache backend/tests/test_api_cache_seam_unit.py -q`
Expected: FAIL（`ImportError: cannot import name 'SemanticCache'` / `build_cache() takes 1 positional argument`）

- [ ] **Step 3：改 `build_cache`**（`base.py`，整段替換 line 46-47）

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

- [ ] **Step 4：改 `__init__.py` 匯出 `SemanticCache`**

```python
from anatomy_backend.cache.base import CachedAnswer, CacheProtocol, NoOpCache, build_cache
from anatomy_backend.cache.semantic_cache import SemanticCache

__all__ = ["CachedAnswer", "CacheProtocol", "NoOpCache", "SemanticCache", "build_cache"]
```

> 註：`base.build_cache` 內部 lazy import `SemanticCache`，`__init__` 頂層 import 不會造成循環（`semantic_cache` 只 import `base.CachedAnswer`）。

- [ ] **Step 5：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_unit.py backend/tests/test_api_cache_seam_unit.py -q`
Expected: PASS（全部，含既有 seam 3 條）

- [ ] **Step 6：commit**

```bash
git add backend/src/anatomy_backend/cache/base.py backend/src/anatomy_backend/cache/__init__.py backend/tests/test_api_semantic_cache_unit.py backend/tests/test_api_cache_seam_unit.py
git commit -m "feat(cache): build_cache 依設定回傳 SemanticCache（NoOpCache 退路；semantic 模式 seam）"
```

---

## Task 6：接線 `main.py`（傳 redis_client 給 build_cache）

**Files:**
- Modify: `backend/src/anatomy_backend/api/main.py:76`

- [ ] **Step 1：改一行**

```python
    cache = build_cache(settings, redis_client)
```

（注意：`redis_client` 變數於 line 71 已建立；`build_cache` 在 line 76、`redis_client` 之後，順序正確。）

- [ ] **Step 2：跑既有 lifespan/e2e 測試確認不破**

Run: `uv run --no-sync pytest backend/tests/ -k "lifespan or chat_sse or main" -q`
Expected: PASS（e2e 用 ASGITransport 不啟 lifespan、注入 fakes，不受影響；lifespan 測試走 mock 設定，`cache_enabled` 預設 True 但 mock 模式下 redis client 來自 from_url——若 lifespan 測試實際連 redis 失敗，cache 仍會被建立但不影響啟動，因 SemanticCache 建構不連線）

- [ ] **Step 3：commit**

```bash
git add backend/src/anatomy_backend/api/main.py
git commit -m "feat(cache): main lifespan 傳 redis_client 給 build_cache（啟用 SemanticCache）"
```

---

## Task 7：真 Redis 整合測試 + conftest 假綠防呆

**Files:**
- Create: `backend/tests/test_api_semantic_cache_integration.py`
- Modify: `backend/tests/conftest.py`（加 `REQUIRE_REDIS_TESTS` 防呆）

- [ ] **Step 1：conftest 加 REDIS 防呆**（鏡像 `REQUIRE_DB_TESTS`）

在 `conftest.py`：`_DB_ENV_READY` 之後加

```python
_REDIS_ENV_READY = bool(os.environ.get("REDIS_URL"))
```

在 `pytest_configure` 內（`REQUIRE_DB_TESTS` 檢查之後）加

```python
    if os.environ.get("REQUIRE_REDIS_TESTS") == "1" and not _REDIS_ENV_READY:
        raise pytest.UsageError("REQUIRE_REDIS_TESTS=1 但缺 REDIS_URL")
```

- [ ] **Step 2：寫整合測試**（真 Redis；無 `REDIS_URL` 自動 skip）

```python
"""Phase 7 SemanticCache 真 Redis 整合測試。

需 REDIS_URL（CI db-integration job 已設 redis://localhost:6379/0；本機用 compose redis）。
無 REDIS_URL → 自動 skip（unit job 不受影響）。每個 cache 用唯一 key_prefix 隔離，
測試後 clear，避免污染共用 Redis。
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
        # 清掉本測試所有 kb namespace（涵蓋 kb1/kb2）
        async for k in redis_client.scan_iter(match=f"{prefix}:*", count=500):
            await redis_client.unlink(k)


SRC = [{"book_title": "Gray's", "edition": "42e", "page": 12, "figure": None,
        "image_url": "http://x/y.png", "snippet": "...", "score": 0.9}]


async def test_real_redis_set_get_hit(cache):
    await cache.set("肱二頭肌起止點", "起於...[Gray's, p.12]", SRC, 1, verified=True)
    hit = await cache.get(" 肱二頭肌起止點 ", 1)   # 正規化後同 key
    assert hit is not None
    assert hit.answer == "起於...[Gray's, p.12]"
    assert hit.sources == SRC


async def test_real_redis_cross_kb_miss(cache):
    await cache.set("q", "a", SRC, 1, verified=True)
    assert await cache.get("q", 2) is None


async def test_real_redis_unverified_not_written(cache, redis_client):
    await cache.set("q", "a", SRC, 1, verified=False)
    assert await cache.get("q", 1) is None
    # 真的沒寫進 Redis
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

Run（本機，先 `make up` 讓 redis healthy）:
```bash
REDIS_URL=redis://localhost:6379/0 uv run --no-sync pytest backend/tests/test_api_semantic_cache_integration.py -q -m integration
```
Expected: PASS（5 passed）。若無 redis：`pytest backend/tests/test_api_semantic_cache_integration.py -q` → 5 skipped。

- [ ] **Step 4：確認 unit job 路徑會 skip（無 REDIS_URL）**

Run: `uv run --no-sync pytest backend/tests/test_api_semantic_cache_integration.py -q`
Expected: `5 skipped`（skipif 生效）

- [ ] **Step 5：commit**

```bash
git add backend/tests/test_api_semantic_cache_integration.py backend/tests/conftest.py
git commit -m "test(cache): 真 Redis 整合測試（set/get/cross-kb/未驗證不寫/TTL/clear）+ REQUIRE_REDIS_TESTS 防呆"
```

---

## Task 8：CI 守門（零-OpenAI grep + REQUIRE_REDIS_TESTS）

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1：unit job 加 cache 零-OpenAI grep**（在現有 ingest 紅線 grep step 之後，line 26 後）

```yaml
      - name: 確認語意快取層無雲端 LLM SDK（DL-012：本地比對、零 OpenAI）
        run: "! grep -rInE --include='*.py' '(import|from)\\s+openai\\b' backend/src/anatomy_backend/cache || (echo '語意快取禁 import openai（線上 lookup 本地比對，DL-012）' && exit 1)"
```

- [ ] **Step 2：db-integration「DB 整合測試」step 加 `REQUIRE_REDIS_TESTS`**（line 78-84 env 區塊內，`REQUIRE_DB_TESTS` 旁）

```yaml
          REQUIRE_REDIS_TESTS: "1"               # conftest guard：REDIS_URL 漏傳時 fail（防快取整合測試假綠）
```

（`REDIS_URL` 已在該 step env，`-m "db or integration"` 已會選到 `integration` 標記的快取測試，無需改 run 行。）

- [ ] **Step 3：本機驗 grep 守門（語意自檢，模擬 CI）**

Run:
```bash
! grep -rInE --include='*.py' '(import|from)\s+openai\b' backend/src/anatomy_backend/cache && echo "GUARD OK：cache 層無 openai"
```
Expected: 印出 `GUARD OK：cache 層無 openai`

- [ ] **Step 4：commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: 快取層零-OpenAI grep 守門 + db-integration REQUIRE_REDIS_TESTS（防假綠）"
```

---

## Task 9：DL-025 寫入 decisions.md

**Files:**
- Modify: `docs/decisions.md`（在 DL-024 之後追加）

- [ ] **Step 1：追加 DL-025**

```markdown

## DL-025: 語意快取 v1＝exact-normalized-query；語意向量列後續開關（fastembed，torch-free）

- **狀態**：APPROVED　**提案者**：main Claude（Phase 7）　**日期**：2026-06-14　**裁決者**：專案負責人（2026-06-14 確認）
- **影響檔案**：ARCHITECTURE.md §6.4、§1.6、§6.6；`backend/.../cache/semantic_cache.py`、`config.py`、`api/main.py`

### 背景
§6.4 / DL-004 / DL-012 已定：線上 cache lookup 用**本地輕量 embedding**、不打 OpenAI，且「初期用 exact-normalized-query 較安全」。Phase 7 落地需定案 v1 究竟先上哪一種，及本地 embedding 套件選型（sentence-transformers vs fastembed）。

### 提案（與 DL-004/DL-012 一致，屬落地記錄、非變更 DECIDED）
1. **v1 預設 `cache_mode="exact"`**：把正規化（NFKC→trim→casefold→摺疊空白）後的 query 以 SHA-256 雜湊成 key；決定性、零誤命中、命中遠在 §1.6 ~30ms 預算內、**零 embedding 套件**。
2. **語意向量比對列為後續 config 開關**（`cache_mode="semantic"`）：上線實測命中率/precision 後再開；啟用時用 **fastembed（ONNX, torch-free, `intfloat/multilingual-e5-small` 384d）**，**不**用 redisvl 預設 `HFTextVectorizer`（會把 torch 拉進 backend、破壞 torch-free 不變量）。`cosine_distance < cache_distance_threshold(0.05)` ≈ sim > 0.95。
3. **只快取已驗證答案**：`set(verified=False)` 一律拒寫（安全網核心）。
4. **kb_version 隔離 + 版本切換清空**：key namespace 含 `kb_version` + 命中時校驗 envelope `kb_version`；`clear_kb_version()` 以 namespace SCAN+UNLINK 清單一版本（取代 §6.6 的 `FLUSHDB`，避免誤清同 Redis 的限流桶）。
5. **Redis fail-open**：`get`→miss、`set`/`clear`→no-op，絕不中斷 `/chat`（§1.8）。
6. **不新增套件**：`redis`/`redisvl` 已在 deps；本 phase runtime/測試皆零新套件（整合測試用既有 CI real redis + 手刻 fake）。

### 後果
- 命中率上限受限於「字面正規化後相同」；待語意開關啟用才涵蓋換句話。先求**零誤命中**的安全側。
- 啟用 semantic 模式時須走本決策第 2 點（fastembed），並補 RAGAS/誤命中評估後才上線。
```

- [ ] **Step 2：commit**

```bash
git add docs/decisions.md
git commit -m "docs(decisions): DL-025 語意快取 v1=exact-normalized-query、語意向量後續開關（fastembed）"
```

---

## Task 10：全套件回歸 + lint + 收尾

**Files:** 無（驗證）

- [ ] **Step 1：ruff check（勿 ruff format）**

Run: `uv run --no-sync ruff check backend/src/anatomy_backend/cache backend/tests/test_api_semantic_cache_unit.py backend/tests/test_api_semantic_cache_integration.py backend/src/anatomy_backend/config.py backend/src/anatomy_backend/api/main.py`
Expected: `All checks passed!`（若 import 排序問題，用 `ruff check --fix`，**勿** `ruff format .`）

- [ ] **Step 2：backend 全 unit 回歸（無 redis，整合測試應 skip）**

Run: `uv run --no-sync pytest backend/tests -q`
Expected: 全綠；`test_api_semantic_cache_integration.py` 顯示 skipped（無 REDIS_URL）

- [ ] **Step 3：本機真 Redis 整合（compose redis up 後）**

Run:
```bash
REDIS_URL=redis://localhost:6379/0 uv run --no-sync pytest backend/tests -q -m "integration" -k semantic_cache
```
Expected: 5 passed

- [ ] **Step 4：最終確認（無遺留 commit）**

Run: `git status && git log --oneline -10`
Expected: working tree clean；可見 Task 1–9 的 commit

---

## Self-Review（spec 對照）

| spec / 驗收 | 對應 task |
|---|---|
| `SemanticCache(redis, ..., threshold, ttl)` 實作 CacheProtocol | Task 2/3（exact 模式 threshold 不適用，留 semantic seam） |
| build_cache 依設定回傳 | Task 5 |
| MUST 只快取已驗證（set verified=False 拒寫） | Task 3（unit）+ Task 7（real redis 斷言未寫） |
| kb_version namespace + 命中校驗 + 版本切換清空 | Task 3（cross-kb/envelope）+ Task 4 + Task 7 |
| 本地 embedding、零 OpenAI（DL-012；CI 守門） | 設計（無 openai import）+ Task 8 grep |
| TTL 7–30 天（預設 14） | Task 1（config）+ Task 3（ex=ttl）+ Task 7（real ttl 斷言） |
| Redis 故障 fail-open | Task 3/4（unit）|
| 追問不查/不寫（chat.py 控制，set 仍拒未驗證） | 既有 chat.py + Task 3 verified 守門 |
| 相似不同題不誤命中（單元） | Task 3 `test_similar_but_different_query_no_false_hit` |
| 真 Redis set/get、cross-kb、版本切換清空、零 OpenAI | Task 7 + Task 8 |
| decisions.md DL-025 | Task 9 |

**Placeholder scan：** 無 TODO/TBD；每個 code step 均含完整程式碼。
**Type 一致性：** `CachedAnswer(answer:str, sources:list[dict])`、`SemanticCache(redis, *, ttl_seconds, key_prefix=None)`、`get(query,kb_version)`、`set(query,answer,sources,kb_version,*,verified)`、`clear_kb_version(kb_version)`、`build_cache(settings, redis_client=None)` 跨 task 一致。
