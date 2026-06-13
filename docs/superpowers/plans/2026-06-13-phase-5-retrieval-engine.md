# Phase 5 — 兩階段檢索引擎（引擎中立查詢表示, baseline）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有基礎（`shared.binary`、`backend.db`、Phase 3 encoder 契約）上實作 self-built pgvector 兩階段檢索引擎（Stage A HNSW 粗排 → Stage B MaxSim 精排 → BM25 → RRF → orchestrator），藏於引擎中立介面後（D-K：`QueryRepr` + `engine.py`），並以 recall harness gate 與 Stage B 並發/p95 benchmark gate 驗收。

**Architecture:** 線上檢索的純函式層（`stage_a`/`stage_b`/`bm25`/`rrf`，對應 §4.7 介面契約）被 `SelfBuiltEngine` 組合、由 `orchestrator.retrieve` 編排。orchestrator 在**單一 PgBouncer 連線、單一 transaction**（經 `hnsw_search_txn`）上序列跑 Stage A→B→BM25→metadata fetch（DL-002），用完即還連線（不跨 LLM 串流）。查詢表示經 `QueryRepr`（D-K）抽象：暴露 binary tokens（self-built 用）+ 可選 float multivector（VectorChord Phase 12 用）+ capability flags。Stage B 提供 **SQL 聚合** 與 **應用層 numpy XOR+popcount** 兩條等價路徑（§4.4），由並發 benchmark gate 決定 production 預設。

**Tech Stack:** Python 3.11+、asyncpg（連 :6432、`statement_cache_size=0`）、pgvector ≥0.8（`halfvec` cosine `<=>`、`bit` Hamming `<~>`）、numpy（numpy MaxSim 路徑 + benchmark）、pytest + pytest-asyncio（`@pytest.mark.db`）。

---

## 背景與既有基礎（實作者必讀，勿重造）

**既有、可直接 import（單一來源，CI 守門禁止在 backend 重新定義）：**
- `anatomy_shared.binary`：
  - `binarize(vec: np.ndarray) -> bytes`（sign-based、MSB-first via `np.packbits`、回 16 bytes）
  - `to_pg_bits(data: bytes) -> str`（16-byte → 128 字元 `'0'/'1'` 字串，MSB-first；**唯一**位序轉換點）
  - `pool_patches(patch_embs, valid_mask=None) -> np.ndarray`（→ float32[128]）
  - `hamming_distance(a: bytes, b: bytes) -> int`
  - `VECTOR_DIM = 128`
- `anatomy_eval.reference.maxsim_hamming(query_tokens_bin, page_patches_bin) -> float`（§4.4 MaxSim oracle，O(T×P) 參考實作，強制 16-byte；Stage B 測試的 ground truth）
- `anatomy_backend.db.tx_helpers.hnsw_search_txn(pool, ef_search=100)`（async context manager；acquire conn + 開 txn + `SET LOCAL hnsw.ef_search` + `SET LOCAL hnsw.iterative_scan = strict_order`；`ef_search` 驗 1..1000）
- `anatomy_backend.db.pool.create_pool(settings)`（測試/腳本用，非單例）、`get_pool()`（單例，prod）
- `anatomy_backend.db.kb_version.get_active_kb_version(settings)`
- `anatomy_backend.config.Settings`

**DB schema（與檢索相關欄位，migrations 001–007 已建）：**
- `books(book_id uuid pk, title text, edition text)`
- `pages(page_id uuid pk, book_id uuid, page_num int, page_image_uri text, docling_md text, metadata jsonb, pooled halfvec(128), text_tsv tsvector GENERATED, kb_version int, embed_model text)`；`UNIQUE(book_id, page_num, kb_version)`、`UNIQUE(kb_version, page_id)`；HNSW 索引 `pages_pooled_hnsw`（`halfvec_cosine_ops`, m16/efc64）
- `page_patches(kb_version int, page_id uuid, patch_idx int, patch_bin bit(128))` PK `(kb_version, page_id, patch_idx)`；`PARTITION BY LIST (kb_version)`（無 default 分區）；複合 FK `(kb_version, page_id) → pages`

**encoder `/encode_query` HTTP 回傳（JSON，base64 序列化）：**
```json
{ "tokens_bin": ["<b64 of 16-byte>", ...N],
  "pooled_f32": "<b64 of 512-byte float32[128] LE>",
  "translated_q": "string|null", "lang": "zh|en|...",
  "model": "...", "mt_model": "..." }
```

**既有 halfvec 綁定範式（勿用 pgvector type registration；transaction pooling 下用字面字串 + cast）：**
- 寫入/查詢一律 `$N::halfvec`，值為 `'[v1,v2,...]'` 字串（128 個 float，repr 保留 fp32 精度，DB 端量化為 fp16）。見 `ingest/src/anatomy_ingest/writer.py` 與 `backend/tests/test_tx_helpers_db.py`。

**既有 DL-013 探針（Phase 2）：** `backend/scripts/bench_stage_b.py`（`make bench-stageb`）—單連線 microbenchmark、p50≈157ms（2000 頁、K=100、18 tokens、WSL2）。Phase 5 在此之上加**並發 + numpy 路徑比較 + gate**。

**測試慣例：** db 測試標 `@pytest.mark.db`；fixture `pool(migrated_db)`（見 `test_tx_helpers_db.py`）；無 `__init__.py`、檔名全域唯一；`uv run --no-sync pytest`；CI db-integration job 設 `REQUIRE_DB_TESTS=1`、unit job 不需 DB。

**⚠️ 關鍵 API 陷阱（numpy 路徑必踩）：** asyncpg 回 `bit(128)` 為 `asyncpg.BitString`。取 16-byte **MUST 用 `bs.bytes`**（回 16 bytes）；**MUST NOT 用 `bytes(bs)`**（回 128 bytes，每 bit 一個 byte，會直接算錯 Hamming）。已實機確認。

**自主決策聲明（feedback-autonomy / roadmap Phase 5 産出清單）：** §4.7 原 `retrieve(conn, query, encoder_result, ...)` 在本 phase 演進為 `retrieve(pool, query, query_repr, ...)`（採 roadmap 明列的 D-K `QueryRepr` + `engine.py` 引擎中立層）。§4.7 的純函式介面（`stage_a_coarse`/`stage_b_maxsim`/`bm25_search`/`rrf_fuse`）原樣保留；orchestrator 連線生命週期由 pool 管理（用完即還，不跨 LLM 串流）。此為 roadmap 預先核可之介面細化，非變更 DECIDED，於 PR 說明即可。

---

## Codex 對抗式審查 round 1 處置（2026-06-13，verdict needs-attention，4 項全採納）

1. **[high] benchmark 非生產負載**（原 Task 12）→ **Task 12 重設計**：Stage B 量測在 `hnsw_search_txn` + savepoint 內跑（pin 連線同生產）、latency 含 pool acquire/queue 等待、pool 用生產級 sizing（不超額）、SQL/numpy **各自獨立 warmup**、候選/tokens 每查詢隨機。
2. **[high] §1.8 Stage B timeout→Stage A top3 降級缺失 + 單交易 abort 連鎖**→ **engine 改 `retrieve()` 回 `EngineResult`**（Task 2 加型別、Task 9 改寫）：Stage A 排序保留為降級來源；Stage B 包 **savepoint（`conn.transaction()`）+ `SET LOCAL statement_timeout`**，逾時/錯誤 → roll back savepoint（外層 txn 存活）→ 回傳 `degraded=True` + Stage A top-N；BM25/metadata 照常。
3. **[high] Stage A 排名未驗**→ **Task 5 加精確 cosine oracle 測試**：seed > Top-K（300 頁）+ 植入已知最近鄰，對 numpy brute-force cosine 比對 top 命中；另加一條**不強制 planner 設定**的 recall 斷言。
4. **[medium] 位序測試繞過入庫路徑**→ **Task 6/7 加端到端等價測試**：用**非對稱邊界位元向量**（只設 bit0 / 只設 bit127）走**真實 `to_pg_bits`→text→`::bit(128)` 入庫路徑**，再以 SQL / numpy(`BitString.bytes`) / oracle 三方精確比對。

---

## File Structure

**新增（`backend/src/anatomy_backend/retrieval/`）：**
| 檔案 | 責任 |
|---|---|
| `__init__.py` | re-export 公開 API |
| `types.py` | `RetrievalResult` dataclass（§4.7）+ `EngineResult`（ranked + coarse_ids + degraded，支援 §1.8 降級） |
| `query_repr.py` | `QueryRepr`（D-K：binary tokens + 可選 float multivector + capability flags + `from_encode_query_response`） |
| `stage_a.py` | `stage_a_coarse`（HNSW halfvec 粗排，§4.3） |
| `stage_b.py` | `stage_b_maxsim`（SQL）+ `stage_b_maxsim_numpy`（app-layer），§4.4 |
| `bm25.py` | `bm25_search`（tsvector + ts_rank_cd，§4.5） |
| `rrf.py` | `rrf_fuse`（純函式，§4.5） |
| `engine.py` | `RetrievalEngine` Protocol（引擎中立介面，D-K；`retrieve()` 回 `EngineResult`） |
| `engine_selfbuilt.py` | `SelfBuiltEngine`（Stage A → savepoint+statement_timeout Stage B → §1.8 降級；`stage_b_mode` 可選 sql/numpy） |
| `orchestrator.py` | `retrieve`（編排：txn 管理、序列執行、RRF、單一 SQL metadata fetch、IN 不保序重排） |

**修改：**
- `shared/src/anatomy_shared/binary.py`：新增 `pooled_to_halfvec_literal(vec) -> str`（halfvec 字面值格式，query 端與 ingest 端共用）
- `ingest/src/anatomy_ingest/writer.py`：改 import shared 的 `pooled_to_halfvec_literal`（刪私有 `_pooled_to_halfvec_literal`）
- `backend/pyproject.toml`：明列 `numpy` 直接依賴
- `Makefile`：新增 `bench-stageb-gate`（並發 + numpy + gate）
- `.github/workflows/ci.yml`：retrieval unit 測試入 unit job、db 測試入 db-integration job（多在 `pytest` 自動涵蓋；僅確認路徑）

**新增腳本/測試：**
- `backend/scripts/bench_stage_b_concurrency.py`（手動並發/p95 gate；非 CI）
- `backend/tests/test_query_repr_unit.py`、`test_rrf_unit.py`（unit）
- `backend/tests/test_stage_a_db.py`、`test_stage_b_db.py`、`test_bm25_db.py`、`test_engine_selfbuilt_db.py`、`test_orchestrator_db.py`、`test_recall_gate_db.py`（db）

---

## Task 1: `shared` 抽出 halfvec 字面值（DRY，query/ingest 共用）

**Files:**
- Modify: `shared/src/anatomy_shared/binary.py`
- Modify: `ingest/src/anatomy_ingest/writer.py`
- Test: `shared/tests/test_binary.py`（追加）

- [ ] **Step 1: 寫失敗測試**

在 `shared/tests/test_binary.py` 末尾追加：
```python
def test_pooled_to_halfvec_literal_format():
    from anatomy_shared.binary import pooled_to_halfvec_literal
    import numpy as np
    lit = pooled_to_halfvec_literal(np.array([0.5, -0.25] + [0.0] * 126, dtype=np.float32))
    assert lit.startswith("[") and lit.endswith("]")
    parts = lit[1:-1].split(",")
    assert len(parts) == 128
    assert float(parts[0]) == 0.5 and float(parts[1]) == -0.25


def test_pooled_to_halfvec_literal_rejects_wrong_dim():
    import numpy as np
    import pytest
    from anatomy_shared.binary import pooled_to_halfvec_literal
    with pytest.raises(ValueError):
        pooled_to_halfvec_literal(np.zeros(64, dtype=np.float32))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest shared/tests/test_binary.py -k halfvec_literal -v`
Expected: FAIL（`ImportError: cannot import name 'pooled_to_halfvec_literal'`）

- [ ] **Step 3: 實作**

在 `shared/src/anatomy_shared/binary.py` 加（沿用既有 `VECTOR_DIM`）：
```python
def pooled_to_halfvec_literal(vec: "np.ndarray") -> str:
    """float32[128] → PostgreSQL halfvec 文字字面值 '[v1,v2,…]'（離線寫入與 query 端共用）。

    用 repr 保留 float 精度（halfvec 入庫/比對時 PG 端再量化為 fp16）。位序無關，
    與 binarize 不同——非檢索精度紅線，但集中於此處避免兩端格式漂移。
    """
    arr = np.asarray(vec, dtype=np.float32).ravel()
    if arr.shape[0] != VECTOR_DIM:
        raise ValueError(f"pooled 必須為 {VECTOR_DIM} 維，收到 {arr.shape[0]}")
    return "[" + ",".join(repr(float(x)) for x in arr) + "]"
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest shared/tests/test_binary.py -k halfvec_literal -v`
Expected: PASS（2 passed）

- [ ] **Step 5: ingest writer 改用 shared，刪私有副本**

在 `ingest/src/anatomy_ingest/writer.py`：刪除私有 `def _pooled_to_halfvec_literal(...)`，於 import 區加 `from anatomy_shared.binary import pooled_to_halfvec_literal`，並把呼叫處 `_pooled_to_halfvec_literal(enc.pooled_f32)` 改為 `pooled_to_halfvec_literal(enc.pooled_f32)`。

- [ ] **Step 6: 跑 ingest writer 測試確認未回歸**

Run: `uv run --no-sync pytest ingest/tests/ -k writer -v`
Expected: PASS（沿用既有 writer 測試）

- [ ] **Step 7: Commit**

```bash
git add shared/src/anatomy_shared/binary.py shared/tests/test_binary.py ingest/src/anatomy_ingest/writer.py
git commit -m "refactor(shared): 抽 pooled_to_halfvec_literal 供 query/ingest 共用（Phase 5 前置）"
```

---

## Task 2: `retrieval/types.py` — RetrievalResult

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/__init__.py`
- Create: `backend/src/anatomy_backend/retrieval/types.py`
- Test: `backend/tests/test_retrieval_types_unit.py`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_retrieval_types_unit.py`：
```python
import uuid

from anatomy_backend.retrieval.types import RetrievalResult


def test_retrieval_result_fields():
    pid = uuid.uuid4()
    r = RetrievalResult(
        page_id=pid, score=0.5, book_title="Gray's", edition="42e",
        page_num=12, page_image_uri="s3://x.png", docling_md="# md",
        metadata={"figures": ["12.3"]},
    )
    assert r.page_id == pid
    assert r.score == 0.5
    assert r.metadata["figures"] == ["12.3"]
    assert r.edition == "42e"


def test_engine_result_degraded_flag():
    from anatomy_backend.retrieval.types import EngineResult
    pid = uuid.uuid4()
    er = EngineResult(ranked=[(pid, 3.0)], coarse_ids=[pid], degraded=False)
    assert er.ranked[0] == (pid, 3.0)
    assert er.coarse_ids == [pid]
    assert er.degraded is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_retrieval_types_unit.py -v`
Expected: FAIL（`ModuleNotFoundError: anatomy_backend.retrieval`）

- [ ] **Step 3: 實作**

`backend/src/anatomy_backend/retrieval/__init__.py`：
```python
"""Phase 5 兩階段檢索（self-built baseline，引擎中立）。"""
```

`backend/src/anatomy_backend/retrieval/types.py`：
```python
"""檢索回傳型別（§4.7 介面契約 + §1.8 降級語意）。"""
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RetrievalResult:
    page_id: UUID
    score: float            # RRF 融合分數
    book_title: str
    edition: str | None
    page_num: int
    page_image_uri: str     # S3 / MinIO 路徑（內部）
    docling_md: str
    metadata: dict          # JSONB，含 figures 等


@dataclass(frozen=True)
class EngineResult:
    """引擎向量檢索輸出（§1.8 降級用）。

    ranked：Stage B（或原生 MaxSim）排名 (page_id, score)；degraded 時為空。
    coarse_ids：Stage A 距離遞增排序的候選頁；degraded 時 orchestrator 取其 top-N 當降級結果。
    degraded：Stage B 逾時/失敗、改用 Stage A 排序時為 True（§1.8，供 Phase 9 trace 標記）。
    """
    ranked: list[tuple[UUID, float]]
    coarse_ids: list[UUID]
    degraded: bool
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_retrieval_types_unit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/__init__.py backend/src/anatomy_backend/retrieval/types.py backend/tests/test_retrieval_types_unit.py
git commit -m "feat(retrieval): RetrievalResult 型別（§4.7）"
```

---

## Task 3: `retrieval/query_repr.py` — 引擎中立查詢表示（D-K）

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/query_repr.py`
- Test: `backend/tests/test_query_repr_unit.py`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_query_repr_unit.py`：
```python
import base64
import struct

import pytest

from anatomy_backend.retrieval.query_repr import QueryRepr


def _payload(n_tokens=3, translated="biceps origin", lang="zh"):
    tokens = [bytes([i]) + b"\x00" * 15 for i in range(n_tokens)]  # 各 16 bytes
    pooled = struct.pack("<128f", *[0.1 * i for i in range(128)])
    return {
        "tokens_bin": [base64.b64encode(t).decode() for t in tokens],
        "pooled_f32": base64.b64encode(pooled).decode(),
        "translated_q": translated, "lang": lang,
        "model": "colpali-v1.3-hf", "mt_model": "opus-mt-zh-en",
    }


def test_from_encode_query_response_decodes_base64():
    qr = QueryRepr.from_encode_query_response(_payload())
    assert len(qr.tokens_bin) == 3
    assert all(len(t) == 16 for t in qr.tokens_bin)
    assert qr.tokens_bin[1][0] == 1
    assert len(qr.pooled_f32) == 128
    assert abs(qr.pooled_f32[10] - 1.0) < 1e-5  # 0.1 * 10
    assert qr.translated_q == "biceps origin"
    assert qr.lang == "zh"


def test_capability_flags():
    qr = QueryRepr.from_encode_query_response(_payload())
    assert qr.has_binary_tokens is True
    assert qr.has_float_multivector is False  # v1 encoder 不回 per-token float


def test_translated_q_null_passthrough():
    qr = QueryRepr.from_encode_query_response(_payload(translated=None))
    assert qr.translated_q is None


def test_rejects_wrong_token_length():
    p = _payload()
    p["tokens_bin"][0] = base64.b64encode(b"\x00" * 15).decode()  # 15 bytes
    with pytest.raises(ValueError, match="16"):
        QueryRepr.from_encode_query_response(p)


def test_rejects_wrong_pooled_length():
    p = _payload()
    p["pooled_f32"] = base64.b64encode(b"\x00" * 256).decode()  # 64 floats
    with pytest.raises(ValueError, match="512"):
        QueryRepr.from_encode_query_response(p)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_query_repr_unit.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作**

`backend/src/anatomy_backend/retrieval/query_repr.py`：
```python
"""引擎中立查詢表示（D-K）。

self-built 引擎用 `tokens_bin`（binary）+ `pooled_f32`（Stage A halfvec）；
VectorChord（Phase 12）用 `float_multivector`（v1 encoder 未回傳，故為 None）。
capability flags 讓引擎宣告所需表示、orchestrator 早期失敗而非跑到一半。
"""
import base64
import struct
from dataclasses import dataclass, field

_TOKEN_BYTES = 16        # bit(128) = 16 bytes
_POOLED_BYTES = 512      # float32[128] LE


@dataclass(frozen=True)
class QueryRepr:
    pooled_f32: tuple[float, ...]                 # 128 維，給 Stage A
    tokens_bin: tuple[bytes, ...]                 # N × 16-byte，給 self-built Stage B
    translated_q: str | None                      # 給 BM25（null 退原文）
    lang: str
    float_multivector: tuple[tuple[float, ...], ...] | None = field(default=None)  # Phase 12

    @property
    def has_binary_tokens(self) -> bool:
        return len(self.tokens_bin) > 0

    @property
    def has_float_multivector(self) -> bool:
        return self.float_multivector is not None

    @classmethod
    def from_encode_query_response(cls, payload: dict) -> "QueryRepr":
        tokens = tuple(base64.b64decode(t) for t in payload["tokens_bin"])
        for t in tokens:
            if len(t) != _TOKEN_BYTES:
                raise ValueError(f"每個 token 必須 {_TOKEN_BYTES} bytes，收到 {len(t)}")
        pooled_raw = base64.b64decode(payload["pooled_f32"])
        if len(pooled_raw) != _POOLED_BYTES:
            raise ValueError(f"pooled_f32 必須 {_POOLED_BYTES} bytes，收到 {len(pooled_raw)}")
        pooled = struct.unpack("<128f", pooled_raw)
        return cls(
            pooled_f32=pooled,
            tokens_bin=tokens,
            translated_q=payload.get("translated_q"),
            lang=payload.get("lang", ""),
        )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_query_repr_unit.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/query_repr.py backend/tests/test_query_repr_unit.py
git commit -m "feat(retrieval): QueryRepr 引擎中立查詢表示（D-K，base64 解碼邊界）"
```

---

## Task 4: `retrieval/rrf.py` — RRF 融合（純函式）

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/rrf.py`
- Test: `backend/tests/test_rrf_unit.py`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_rrf_unit.py`：
```python
import uuid

from anatomy_backend.retrieval.rrf import rrf_fuse


def test_rrf_single_list_preserves_order():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = rrf_fuse([[a, b, c]])
    assert [pid for pid, _ in fused] == [a, b, c]


def test_rrf_rewards_consensus():
    a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # a 在兩表都高名次 → 應勝過任一單表的 rank0
    fused = rrf_fuse([[a, b, c], [a, d]])
    assert fused[0][0] == a
    # 分數 = 1/(60+0) + 1/(60+0) for a
    assert abs(fused[0][1] - (1.0 / 60 + 1.0 / 60)) < 1e-9


def test_rrf_formula_and_k():
    a = uuid.uuid4()
    fused = rrf_fuse([[uuid.uuid4(), a]], k=10)  # a 在 rank 1 → 分數 1/(10+1)，排第二
    assert abs(fused[1][1] - 1.0 / (10 + 1)) < 1e-9


def test_rrf_empty_lists():
    assert rrf_fuse([[], []]) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_rrf_unit.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作**

`backend/src/anatomy_backend/retrieval/rrf.py`（直接採 §4.5 spec 實作）：
```python
"""Reciprocal Rank Fusion（§4.5）。"""
from uuid import UUID


def rrf_fuse(rank_lists: list[list[UUID]], k: int = 60) -> list[tuple[UUID, float]]:
    """rank_lists：每個 list 按相關性遞減；回傳融合後 (page_id, score) 按 score 遞減。"""
    scores: dict[UUID, float] = {}
    for ranks in rank_lists:
        for rank, pid in enumerate(ranks):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_rrf_unit.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/rrf.py backend/tests/test_rrf_unit.py
git commit -m "feat(retrieval): rrf_fuse（§4.5）"
```

---

## Task 5: `retrieval/stage_a.py` — Stage A HNSW 粗排

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/stage_a.py`
- Test: `backend/tests/test_stage_a_db.py`

**前置知識：** `stage_a_coarse` 假設 `conn` **已在 `hnsw_search_txn` 開的 txn 內**（ef_search + iterative_scan 已 SET LOCAL）；本函式只跑 SELECT。pooled 用 `pooled_to_halfvec_literal` 轉字面值 + `$1::halfvec`。

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_stage_a_db.py`：
```python
import json
import uuid

import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.db.tx_helpers import hnsw_search_txn
from anatomy_backend.retrieval.stage_a import stage_a_coarse

pytestmark = pytest.mark.db
import os


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


def _vec(rng) -> str:
    return "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"


async def _seed_pages(conn, n, kb, *, metadata=None, book_title="stage-a"):
    book = await conn.fetchval(
        "INSERT INTO books (title) VALUES ($1) RETURNING book_id", book_title)
    rng = np.random.default_rng(42)
    rows = [
        (book, i + 1, "s3://x.png", f"page {i}", json.dumps(metadata or {}),
         _vec(rng), kb, "colpali-v1.3-hf")
        for i in range(n)
    ]
    await conn.executemany(
        "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
        " pooled, kb_version, embed_model) VALUES ($1,$2,$3,$4,$5::jsonb,$6::halfvec,$7,$8)",
        rows)
    return book


async def test_stage_a_returns_top_k(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_pages(conn, 120, kb=1)
        q = [float(x) for x in np.random.default_rng(7).standard_normal(128)]
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL enable_sort = off")
            res = await stage_a_coarse(conn, q, None, kb_version=1, top_k=100)
        assert len(res) == 100
        assert all(isinstance(p, uuid.UUID) for p in res)
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_a_filters_kb_version(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_pages(conn, 100, kb=1, book_title="v1")
            await _seed_pages(conn, 100, kb=2, book_title="v2")
        q = [float(x) for x in np.random.default_rng(7).standard_normal(128)]
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            res = await stage_a_coarse(conn, q, None, kb_version=1, top_k=100)
            # 全部回傳的 page 都屬 kb_version=1
            kbs = await conn.fetch(
                "SELECT DISTINCT kb_version FROM pages WHERE page_id = ANY($1::uuid[])", res)
        assert {r["kb_version"] for r in kbs} == {1}
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_a_metadata_filter(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_pages(conn, 40, kb=1, metadata={"anatomy_system": "nervous"},
                              book_title="nerve")
            await _seed_pages(conn, 40, kb=1, metadata={"anatomy_system": "muscular"},
                              book_title="muscle")
        q = [float(x) for x in np.random.default_rng(7).standard_normal(128)]
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            res = await stage_a_coarse(
                conn, q, {"anatomy_system": "nervous"}, kb_version=1, top_k=100)
            systems = await conn.fetch(
                "SELECT metadata->>'anatomy_system' AS s FROM pages"
                " WHERE page_id = ANY($1::uuid[])", res)
        assert {r["s"] for r in systems} == {"nervous"}
        assert len(res) == 40
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_a_returns_true_nearest_against_oracle(pool):
    """植入式最近鄰（Codex review #3）：300 頁 > Top-K、用真實 planner（不強制設定），
    抓 halfvec 序列化錯 / 距離方向反 / 排名錯——前述測試僅斷言『回 100 筆』不足以驗正確性。"""
    rng = np.random.default_rng(101)
    q = rng.standard_normal(128).astype(np.float32)
    qn = q / np.linalg.norm(q)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            book = await conn.fetchval(
                "INSERT INTO books (title) VALUES ('oracle') RETURNING book_id")
            rows = []
            for i in range(5):  # 植入頁：方向≈query（query+微噪）→ cosine 必最近
                v = qn + 0.01 * rng.standard_normal(128).astype(np.float32)
                rows.append((i + 1, "[" + ",".join(f"{x:.6f}" for x in v) + "]"))
            for i in range(295):  # 遠頁：隨機方向（高維下與 query 近正交）
                v = rng.standard_normal(128).astype(np.float32)
                rows.append((i + 6, "[" + ",".join(f"{x:.6f}" for x in v) + "]"))
            ids = []
            for num, vec in rows:
                pid = await conn.fetchval(
                    "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md,"
                    " metadata, pooled, kb_version, embed_model)"
                    " VALUES ($1,$2,'s3://x','md','{}'::jsonb,$3::halfvec,1,'m')"
                    " RETURNING page_id", book, num, vec)
                ids.append(pid)
            planted = set(ids[:5])
        # 不強制 planner 設定 → 真實索引路徑（Codex review #3：不被 forced settings 遮蔽）
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            res = await stage_a_coarse(
                conn, [float(x) for x in qn], None, kb_version=1, top_k=20)
        assert planted.issubset(set(res[:20]))   # 5 植入頁全進 top-20
        assert res[0] in planted                 # top-1 為植入頁（距離方向正確）
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_stage_a_db.py -v`
Expected: FAIL（`ModuleNotFoundError: ...retrieval.stage_a`）

- [ ] **Step 3: 實作**

`backend/src/anatomy_backend/retrieval/stage_a.py`：
```python
"""Stage A — HNSW 粗排（§4.3、DL-013 Top-K=100、DL-019 halfvec cosine）。

呼叫端 MUST 先經 `hnsw_search_txn`（SET LOCAL ef_search + iterative_scan=strict_order
在同一 txn）。本函式只跑 SELECT。
"""
import json
from collections.abc import Sequence
from uuid import UUID

import asyncpg

from anatomy_shared.binary import pooled_to_halfvec_literal

_STAGE_A_SQL = """
SELECT page_id
FROM pages
WHERE kb_version = $2
  AND ($3::jsonb IS NULL OR metadata @> $3::jsonb)
ORDER BY pooled <=> $1::halfvec
LIMIT $4
"""


async def stage_a_coarse(
    conn: asyncpg.Connection,
    query_pooled: Sequence[float],
    metadata_filter: dict | None,
    kb_version: int,
    top_k: int = 100,
) -> list[UUID]:
    pooled_literal = pooled_to_halfvec_literal(query_pooled)
    meta = json.dumps(metadata_filter) if metadata_filter else None
    rows = await conn.fetch(_STAGE_A_SQL, pooled_literal, kb_version, meta, top_k)
    return [r["page_id"] for r in rows]
```

> 註：`pooled_to_halfvec_literal` 內 `np.asarray(query_pooled)` 接受 list/tuple/ndarray，故 `query_pooled` 傳 `QueryRepr.pooled_f32`（tuple）亦可。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_stage_a_db.py -v`
Expected: PASS（4 passed；若本機無 DB 則 skip，需 compose 起 DB + migrate）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/stage_a.py backend/tests/test_stage_a_db.py
git commit -m "feat(retrieval): Stage A HNSW 粗排（§4.3, halfvec, Top-K=100, kb_version+metadata 過濾）"
```

---

## Task 6: `retrieval/stage_b.py`（SQL 路徑）— MaxSim 精排

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/stage_b.py`
- Test: `backend/tests/test_stage_b_db.py`

**核心正確性：** Stage B SQL 排序 MUST 與 `anatomy_eval.reference.maxsim_hamming` 手算一致；MUST 只掃候選頁（不全表）；MUST 帶 `kb_version`（DL-017 分區）。query tokens 經 `to_pg_bits`（唯一位序轉換點）轉 128-char text → `::bit(128)`。

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_stage_b_db.py`：
```python
import os
import uuid

import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.stage_b import stage_b_maxsim
from anatomy_eval.reference import maxsim_hamming

pytestmark = pytest.mark.db
KB = 5


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


async def _seed_patches(conn, pages_patches: dict, kb):
    """pages_patches: {page_id: [bytes16, ...]}。建分區 + pages + page_patches。"""
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb})")
    book = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('stage-b') RETURNING book_id")
    pooled = "[" + ",".join("0.01" for _ in range(128)) + "]"
    for i, (pid, patches) in enumerate(pages_patches.items()):
        await conn.execute(
            "INSERT INTO pages (page_id, book_id, page_num, page_image_uri, docling_md,"
            " metadata, pooled, kb_version, embed_model)"
            " VALUES ($1,$2,$3,'s3://x','md','{}'::jsonb,$4::halfvec,$5,'m')",
            pid, book, i + 1, pooled, kb)
        recs = [(kb, pid, j, asyncpg_bits(p)) for j, p in enumerate(patches)]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
    return book


def asyncpg_bits(b: bytes):
    import asyncpg
    return asyncpg.BitString.frombytes(b, bitlength=128)


async def test_stage_b_matches_oracle(pool):
    rng = np.random.default_rng(3)
    pages = {uuid.uuid4(): [rng.bytes(16) for _ in range(8)] for _ in range(5)}
    query = [rng.bytes(16) for _ in range(6)]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_patches(conn, pages, KB)
            cand = list(pages.keys())
            res = await stage_b_maxsim(conn, cand, query, kb_version=KB, top_n=10)
        # 與 oracle 手算逐頁比對
        oracle = {pid: maxsim_hamming(query, patches) for pid, patches in pages.items()}
        oracle_ranked = sorted(oracle, key=lambda p: -oracle[p])
        assert [pid for pid, _ in res] == oracle_ranked
        for pid, score in res:
            assert abs(score - oracle[pid]) < 1e-6
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_b_only_scans_candidates(pool):
    rng = np.random.default_rng(9)
    pages = {uuid.uuid4(): [rng.bytes(16) for _ in range(8)] for _ in range(6)}
    query = [rng.bytes(16) for _ in range(6)]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_patches(conn, pages, KB)
            all_ids = list(pages.keys())
            cand = all_ids[:5]                      # 第 6 頁不在候選
            res = await stage_b_maxsim(conn, cand, query, kb_version=KB, top_n=10)
        returned = {pid for pid, _ in res}
        assert all_ids[5] not in returned
        assert returned.issubset(set(cand))
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


# 邊界位元向量（MSB-first）：抓 to_pg_bits 入庫 ↔ SQL bit ops ↔ oracle 的位序一致性
B0 = b"\x80" + b"\x00" * 15        # 只設 bit 0（byte0 MSB）
B127 = b"\x00" * 15 + b"\x01"      # 只設 bit 127（byte15 LSB）
B56 = b"\x00" * 7 + b"\x80" + b"\x00" * 8   # 只設 bit 56


async def _seed_via_to_pg_bits(conn, pages: dict, kb):
    """走『生產入庫路徑』灌 patch（to_pg_bits → text → ::bit(128)，同 ingest/writer.py），
    而非 BitString.frombytes——證明真實儲存表示與讀取/oracle 三方一致（Codex review #4）。"""
    from anatomy_shared.binary import to_pg_bits
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb})")
    book = await conn.fetchval("INSERT INTO books (title) VALUES ('edge') RETURNING book_id")
    pv = "[" + ",".join("0.01" for _ in range(128)) + "]"
    for i, (pid, patches) in enumerate(pages.items()):
        await conn.execute(
            "INSERT INTO pages (page_id, book_id, page_num, page_image_uri, docling_md,"
            " metadata, pooled, kb_version, embed_model)"
            " VALUES ($1,$2,$3,'s3://x','md','{}'::jsonb,$4::halfvec,$5,'m')",
            pid, book, i + 1, pv, kb)
        for j, pb in enumerate(patches):
            await conn.execute(
                "INSERT INTO page_patches (kb_version, page_id, patch_idx, patch_bin)"
                " VALUES ($1,$2,$3,$4::text::bit(128))", kb, pid, j, to_pg_bits(pb))
    return book


async def test_stage_b_edge_bits_via_real_storage(pool):
    pages = {uuid.uuid4(): [B0, B127], uuid.uuid4(): [B127, B56], uuid.uuid4(): [B56, B0]}
    query = [B0, B127]   # 非對稱：與不同 patch 的 hamming 差異隨位序而變
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_via_to_pg_bits(conn, pages, KB)
            cand = list(pages.keys())
            res = await stage_b_maxsim(conn, cand, query, kb_version=KB, top_n=10)
        oracle = {pid: maxsim_hamming(query, patches) for pid, patches in pages.items()}
        for pid, score in res:
            assert abs(score - oracle[pid]) < 1e-6, f"位序不一致 @ {pid}"
        assert [p for p, _ in res] == sorted(oracle, key=lambda p: -oracle[p])
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_stage_b_db.py -v`
Expected: FAIL（`ModuleNotFoundError: ...retrieval.stage_b`）

- [ ] **Step 3: 實作（SQL 路徑）**

`backend/src/anatomy_backend/retrieval/stage_b.py`：
```python
"""Stage B — MaxSim 精排（§4.4）。

score(page) = Σ_t max_p (128 - hamming(token_t, patch_p))

兩條等價路徑（§4.4）：
- stage_b_maxsim：SQL 聚合（pgvector `<~>` Hamming），spec 主路徑。
- stage_b_maxsim_numpy：撈候選頁 patch_bin 後應用層 numpy XOR+popcount（並發退路）。
預設由 Stage B 並發/p95 benchmark gate 決定（見 SelfBuiltEngine.stage_b_mode）。

query tokens 經 shared.binary.to_pg_bits（唯一位序轉換點）轉 128-char '0'/'1' → ::bit(128)。
MUST 只掃候選頁、MUST 帶 kb_version（DL-017 分區）。
"""
from collections.abc import Sequence
from uuid import UUID

import asyncpg

from anatomy_shared.binary import to_pg_bits

_STAGE_B_SQL = """
WITH query_tokens AS (
    SELECT token_idx, q_bits::bit(128) AS q_bin
    FROM unnest($1::text[]) WITH ORDINALITY AS qt(q_bits, token_idx)
),
token_max_per_page AS (
    SELECT pp.page_id, qt.token_idx,
           MAX(128 - (pp.patch_bin <~> qt.q_bin))::float AS sim
    FROM page_patches pp
    JOIN query_tokens qt ON true
    WHERE pp.page_id = ANY($2::uuid[])
      AND pp.kb_version = $3
    GROUP BY pp.page_id, qt.token_idx
)
SELECT page_id, SUM(sim) AS maxsim_score
FROM token_max_per_page
GROUP BY page_id
ORDER BY maxsim_score DESC, page_id ASC   -- page_id 次序鍵：分數並列時輸出決定性
LIMIT $4
"""


async def stage_b_maxsim(
    conn: asyncpg.Connection,
    candidate_page_ids: list[UUID],
    query_tokens_bin: Sequence[bytes],
    kb_version: int,
    top_n: int = 10,
) -> list[tuple[UUID, float]]:
    if not candidate_page_ids:
        return []
    q_bits = [to_pg_bits(t) for t in query_tokens_bin]
    rows = await conn.fetch(_STAGE_B_SQL, q_bits, candidate_page_ids, kb_version, top_n)
    return [(r["page_id"], float(r["maxsim_score"])) for r in rows]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_stage_b_db.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/stage_b.py backend/tests/test_stage_b_db.py
git commit -m "feat(retrieval): Stage B MaxSim SQL 路徑（§4.4，與 oracle 一致、只掃候選、帶 kb_version、邊界位元端到端等價）"
```

---

## Task 7: `retrieval/stage_b.py`（numpy 路徑）— 應用層 MaxSim

**Files:**
- Modify: `backend/src/anatomy_backend/retrieval/stage_b.py`
- Modify: `backend/pyproject.toml`（加 `numpy` 直接依賴）
- Test: `backend/tests/test_stage_b_db.py`（追加 numpy 對拍）

**⚠️ 陷阱：** asyncpg 回 `bit(128)` 為 `asyncpg.BitString`；取 16-byte **用 `bs.bytes`**，**不可用 `bytes(bs)`**（回 128 bytes）。

- [ ] **Step 1: 在 backend/pyproject.toml 加 numpy 依賴**

`backend/pyproject.toml` 的 `[project].dependencies` 加一行 `"numpy>=1.26"`（numpy MaxSim 路徑與 benchmark 用；雖經 anatomy-shared 傳遞可得，明列以免日後 shared 改動斷鏈）。

- [ ] **Step 2: 寫失敗測試（追加到 test_stage_b_db.py）**

```python
async def test_stage_b_numpy_matches_sql_and_oracle(pool):
    from anatomy_backend.retrieval.stage_b import stage_b_maxsim_numpy
    rng = np.random.default_rng(11)
    pages = {uuid.uuid4(): [rng.bytes(16) for _ in range(8)] for _ in range(5)}
    query = [rng.bytes(16) for _ in range(6)]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_patches(conn, pages, KB)
            cand = list(pages.keys())
            sql_res = await stage_b_maxsim(conn, cand, query, kb_version=KB, top_n=10)
            np_res = await stage_b_maxsim_numpy(conn, cand, query, kb_version=KB, top_n=10)
        oracle = {pid: maxsim_hamming(query, patches) for pid, patches in pages.items()}
        assert [p for p, _ in np_res] == [p for p, _ in sql_res]
        for pid, score in np_res:
            assert abs(score - oracle[pid]) < 1e-6
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_stage_b_numpy_edge_bits_via_real_storage(pool):
    """numpy 路徑（BitString.bytes 讀取）對 to_pg_bits 真實入庫的邊界位元 + oracle 三方等價
    （Codex review #4）。"""
    from anatomy_backend.retrieval.stage_b import stage_b_maxsim_numpy
    pages = {uuid.uuid4(): [B0, B127], uuid.uuid4(): [B127, B56], uuid.uuid4(): [B56, B0]}
    query = [B0, B127]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await _seed_via_to_pg_bits(conn, pages, KB)
            cand = list(pages.keys())
            np_res = await stage_b_maxsim_numpy(conn, cand, query, kb_version=KB, top_n=10)
        oracle = {pid: maxsim_hamming(query, patches) for pid, patches in pages.items()}
        for pid, score in np_res:
            assert abs(score - oracle[pid]) < 1e-6, f"BitString.bytes↔to_pg_bits 位序不一致 @ {pid}"
        assert [p for p, _ in np_res] == sorted(oracle, key=lambda p: -oracle[p])
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_stage_b_db.py -k numpy -v`
Expected: FAIL（`ImportError: cannot import name 'stage_b_maxsim_numpy'`）

- [ ] **Step 4: 實作 numpy 路徑（追加到 stage_b.py）**

```python
import numpy as np

# 256-entry uint8 popcount 查表（一次建好）
_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint16)

_FETCH_PATCHES_SQL = """
SELECT page_id, patch_bin
FROM page_patches
WHERE page_id = ANY($1::uuid[]) AND kb_version = $2
"""


async def stage_b_maxsim_numpy(
    conn: "asyncpg.Connection",
    candidate_page_ids: list[UUID],
    query_tokens_bin: Sequence[bytes],
    kb_version: int,
    top_n: int = 10,
) -> list[tuple[UUID, float]]:
    """撈候選頁 patch_bin → 應用層 numpy XOR+popcount MaxSim（§4.4 並發退路）。

    K=100 約 100×1024×16B ≈ 1.6MB 傳輸；CPU 從 PG 後端（共享、易爭用）移到 app worker
    （隨 worker 數擴展）。位序與 SQL 路徑一致（皆 16-byte big-endian bit(128)）。
    """
    if not candidate_page_ids:
        return []
    # query tokens → (T, 16) uint8
    q = np.frombuffer(b"".join(query_tokens_bin), dtype=np.uint8).reshape(
        len(query_tokens_bin), 16)
    rows = await conn.fetch(_FETCH_PATCHES_SQL, candidate_page_ids, kb_version)
    # 依 page 聚 patch（bs.bytes = 16 bytes；勿用 bytes(bs)）
    by_page: dict[UUID, list[bytes]] = {}
    for r in rows:
        by_page.setdefault(r["page_id"], []).append(r["patch_bin"].bytes)
    scores: list[tuple[UUID, float]] = []
    for pid, patch_bytes in by_page.items():
        P = np.frombuffer(b"".join(patch_bytes), dtype=np.uint8).reshape(
            len(patch_bytes), 16)                       # (P, 16)
        xor = q[:, None, :] ^ P[None, :, :]             # (T, P, 16)
        dist = _POPCOUNT[xor].sum(axis=2)               # (T, P) Hamming
        sim = 128 - dist                                # (T, P) 相似度
        score = float(sim.max(axis=1).sum())            # Σ_t max_p
        scores.append((pid, score))
    scores.sort(key=lambda x: (-x[1], x[0]))            # 分數降冪 + page_id 升冪（並列決定性，對齊 SQL）
    return scores[:top_n]
```

- [ ] **Step 5: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_stage_b_db.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/stage_b.py backend/tests/test_stage_b_db.py backend/pyproject.toml
git commit -m "feat(retrieval): Stage B numpy XOR+popcount 退路（§4.4，並發 gate 候選；BitString.bytes 陷阱；邊界位元等價）"
```

---

## Task 8: `retrieval/bm25.py` — BM25 文字副線

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/bm25.py`
- Test: `backend/tests/test_bm25_db.py`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_bm25_db.py`：
```python
import os

import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.bm25 import bm25_search

pytestmark = pytest.mark.db


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


async def _seed(conn, kb, pages: list[tuple[int, str]]):
    book = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('bm25') RETURNING book_id")
    pooled = "[" + ",".join("0.01" for _ in range(128)) + "]"
    ids = {}
    for num, md in pages:
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1,$2,'s3://x',$3,'{}'::jsonb,$4::halfvec,$5,'m') RETURNING page_id",
            book, num, md, pooled, kb)
        ids[num] = pid
    return ids


async def test_bm25_ranks_matching_page(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            ids = await _seed(conn, 1, [
                (1, "The biceps brachii origin and insertion on the radius"),
                (2, "The femur is the thigh bone of the lower limb"),
            ])
            res = await bm25_search(conn, "biceps brachii origin", kb_version=1, top_k=50)
        assert res[0] == ids[1]
        assert ids[2] not in res  # 無 term 命中 → 不在 @@ 結果
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_bm25_respects_kb_version(pool):
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed(conn, 1, [(1, "biceps brachii origin")])
            ids2 = await _seed(conn, 2, [(1, "biceps brachii origin")])
            res = await bm25_search(conn, "biceps", kb_version=2, top_k=50)
        assert res == [ids2[1]]
    finally:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_bm25_db.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作**

`backend/src/anatomy_backend/retrieval/bm25.py`：
```python
"""BM25 文字副線（§4.5；tsvector simple config + ts_rank_cd）。"""
from uuid import UUID

import asyncpg

_BM25_SQL = """
SELECT page_id
FROM pages
WHERE text_tsv @@ plainto_tsquery('simple', $1) AND kb_version = $2
ORDER BY ts_rank_cd(text_tsv, plainto_tsquery('simple', $1)) DESC
LIMIT $3
"""


async def bm25_search(
    conn: asyncpg.Connection, query: str, kb_version: int, top_k: int = 50
) -> list[UUID]:
    rows = await conn.fetch(_BM25_SQL, query, kb_version, top_k)
    return [r["page_id"] for r in rows]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_bm25_db.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/bm25.py backend/tests/test_bm25_db.py
git commit -m "feat(retrieval): BM25 tsvector 副線（§4.5, kb_version 過濾）"
```

---

## Task 9: `engine.py` + `engine_selfbuilt.py` — 引擎中立介面（D-K）

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/engine.py`
- Create: `backend/src/anatomy_backend/retrieval/engine_selfbuilt.py`
- Test: `backend/tests/test_engine_selfbuilt_db.py`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_engine_selfbuilt_db.py`：
```python
import os
import uuid

import asyncpg
import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.db.tx_helpers import hnsw_search_txn
from anatomy_backend.retrieval.engine_selfbuilt import SelfBuiltEngine
from anatomy_backend.retrieval.query_repr import QueryRepr

pytestmark = pytest.mark.db
KB = 6


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


async def test_selfbuilt_requires_binary_tokens(pool):
    eng = SelfBuiltEngine()
    qr = QueryRepr(pooled_f32=tuple([0.0] * 128), tokens_bin=(), translated_q=None, lang="en")
    async with hnsw_search_txn(pool, ef_search=100) as conn:
        with pytest.raises(ValueError, match="binary"):
            await eng.retrieve(conn, qr, None, kb_version=KB, top_k=100, top_n=10)


async def _seed_three(conn, page_ids, patches, pooled):
    await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
    await conn.execute(
        f"CREATE TABLE page_patches_v{KB} PARTITION OF page_patches FOR VALUES IN ({KB})")
    book = await conn.fetchval("INSERT INTO books (title) VALUES ('eng') RETURNING book_id")
    pv = "[" + ",".join(f"{x:.4f}" for x in pooled) + "]"
    for i, pid in enumerate(page_ids):
        await conn.execute(
            "INSERT INTO pages (page_id, book_id, page_num, page_image_uri, docling_md,"
            " metadata, pooled, kb_version, embed_model)"
            " VALUES ($1,$2,$3,'s3://x','md','{}'::jsonb,$4::halfvec,$5,'m')",
            pid, book, i + 1, pv, KB)
        recs = [(KB, pid, j, asyncpg.BitString.frombytes(p, bitlength=128))
                for j, p in enumerate(patches[pid])]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])


async def test_selfbuilt_sql_and_numpy_modes_agree(pool):
    rng = np.random.default_rng(21)
    page_ids = [uuid.uuid4() for _ in range(3)]
    patches = {pid: [rng.bytes(16) for _ in range(8)] for pid in page_ids}
    query_tokens = patches[page_ids[0]][:6]   # query=第1頁子集 → 第1頁 MaxSim 最高
    pooled = [float(x) for x in rng.standard_normal(128)]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_three(conn, page_ids, patches, pooled)
        qr = QueryRepr(pooled_f32=tuple(pooled), tokens_bin=tuple(query_tokens),
                       translated_q=None, lang="en")
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            sql = await SelfBuiltEngine("sql").retrieve(
                conn, qr, None, kb_version=KB, top_k=100, top_n=10)
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            npy = await SelfBuiltEngine("numpy").retrieve(
                conn, qr, None, kb_version=KB, top_k=100, top_n=10)
        assert sql.degraded is False and npy.degraded is False
        assert [p for p, _ in sql.ranked] == [p for p, _ in npy.ranked]
        assert sql.ranked[0][0] == page_ids[0]
        assert page_ids[0] in sql.coarse_ids   # Stage A 候選保留
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_selfbuilt_degraded_falls_back_to_stage_a(pool, monkeypatch):
    """Codex review #2：Stage B 逾時/錯誤 → savepoint 回滾、外層 txn 存活、
    回 degraded + Stage A 候選（§1.8）。以 monkeypatch 確定性模擬逾時。"""
    import anatomy_backend.retrieval.engine_selfbuilt as esb
    rng = np.random.default_rng(22)
    page_ids = [uuid.uuid4() for _ in range(3)]
    patches = {pid: [rng.bytes(16) for _ in range(8)] for pid in page_ids}
    pooled = [float(x) for x in rng.standard_normal(128)]

    async def _boom(conn, cand, tokens, kb, top_n):
        raise asyncpg.exceptions.QueryCanceledError("simulated statement timeout")

    monkeypatch.setitem(esb._STAGE_B, "sql", _boom)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await _seed_three(conn, page_ids, patches, pooled)
        qr = QueryRepr(pooled_f32=tuple(pooled),
                       tokens_bin=tuple(patches[page_ids[0]][:6]),
                       translated_q=None, lang="en")
        async with hnsw_search_txn(pool, ef_search=100) as conn:
            er = await SelfBuiltEngine("sql").retrieve(
                conn, qr, None, kb_version=KB, top_k=100, top_n=10)
            alive = await conn.fetchval("SELECT 1")   # 外層 txn 未被連鎖 abort
        assert er.degraded is True
        assert er.ranked == []
        assert set(er.coarse_ids) == set(page_ids)    # Stage A 候選保留供降級
        assert alive == 1
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_engine_selfbuilt_db.py -v`
Expected: FAIL（`ModuleNotFoundError: ...engine_selfbuilt`）

- [ ] **Step 3: 實作 engine.py**

`backend/src/anatomy_backend/retrieval/engine.py`：
```python
"""引擎中立檢索介面（D-K）。

self-built（v1 baseline）用 binary tokens；VectorChord（Phase 12 PoC）用 float
multivector。兩者皆藏於本 Protocol 後，orchestrator 不感知內部實作。
retrieve() 回 EngineResult（含 §1.8 降級語意）。
"""
from typing import Protocol

import asyncpg

from .query_repr import QueryRepr
from .types import EngineResult


class RetrievalEngine(Protocol):
    async def retrieve(
        self,
        conn: asyncpg.Connection,
        query: QueryRepr,
        metadata_filter: dict | None,
        kb_version: int,
        top_k: int = 100,
        top_n: int = 10,
        stage_b_timeout_ms: int = 1000,
    ) -> EngineResult:
        """Stage A 粗排 → Stage B 精排（含逾時降級），回 EngineResult。"""
        ...
```

- [ ] **Step 4: 實作 engine_selfbuilt.py**

`backend/src/anatomy_backend/retrieval/engine_selfbuilt.py`：
```python
"""self-built pgvector 兩階段引擎（v1 baseline，DL-014）。"""
from dataclasses import dataclass

import asyncpg

from .query_repr import QueryRepr
from .stage_a import stage_a_coarse
from .stage_b import stage_b_maxsim, stage_b_maxsim_numpy
from .types import EngineResult

_STAGE_B = {"sql": stage_b_maxsim, "numpy": stage_b_maxsim_numpy}


@dataclass
class SelfBuiltEngine:
    """stage_b_mode: 'sql'（spec 主路徑）| 'numpy'（並發退路）；預設由 benchmark gate 決定。"""
    stage_b_mode: str = "sql"

    def __post_init__(self) -> None:
        if self.stage_b_mode not in _STAGE_B:
            raise ValueError(f"stage_b_mode 必須為 {set(_STAGE_B)}，收到 {self.stage_b_mode!r}")

    async def retrieve(
        self,
        conn: asyncpg.Connection,
        query: QueryRepr,
        metadata_filter: dict | None,
        kb_version: int,
        top_k: int = 100,
        top_n: int = 10,
        stage_b_timeout_ms: int = 1000,
    ) -> EngineResult:
        if not query.has_binary_tokens:
            raise ValueError("SelfBuiltEngine 需要 binary tokens（QueryRepr.tokens_bin 為空）")
        coarse = await stage_a_coarse(
            conn, query.pooled_f32, metadata_filter, kb_version, top_k)
        if not coarse:
            return EngineResult(ranked=[], coarse_ids=[], degraded=False)
        try:
            # savepoint 隔離：Stage B 逾時/錯誤只回滾本子交易，外層 txn 存活
            # → BM25 / metadata fetch 仍可在同連線跑（§1.8 降級，非整請求失敗）。
            # statement_timeout 綁住 SQL 路徑全程與 numpy 路徑的 DB fetch；numpy 的
            # 純 Python popcount（K=100 約 1.6MB、sub-10ms）不在 PG 逾時內，benchmark 證實其快。
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('statement_timeout', $1, true)",
                    str(stage_b_timeout_ms))
                ranked = await _STAGE_B[self.stage_b_mode](
                    conn, coarse, list(query.tokens_bin), kb_version, top_n)
            return EngineResult(ranked=ranked, coarse_ids=coarse, degraded=False)
        except asyncpg.PostgresError:
            # §1.8：Stage B timeout > 1s → 退回 Stage A 排序（orchestrator 取 coarse top-N）
            return EngineResult(ranked=[], coarse_ids=coarse, degraded=True)
```

- [ ] **Step 5: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_engine_selfbuilt_db.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/engine.py backend/src/anatomy_backend/retrieval/engine_selfbuilt.py backend/tests/test_engine_selfbuilt_db.py
git commit -m "feat(retrieval): 引擎中立介面 + SelfBuiltEngine（D-K, retrieve()→EngineResult, savepoint+timeout §1.8 降級）"
```

---

## Task 10: `orchestrator.py` — 主入口編排

**Files:**
- Create: `backend/src/anatomy_backend/retrieval/orchestrator.py`
- Modify: `backend/src/anatomy_backend/retrieval/__init__.py`（re-export）
- Test: `backend/tests/test_orchestrator_db.py`

**核心規則：** 單一連線 + 單一 txn（`hnsw_search_txn`）序列跑 Stage A→B→BM25→metadata fetch（DL-002）；BM25 餵 `translated_q or query`（DL-013/020）；最終 metadata 用**單一 SQL** 撈、帶 `kb_version`；`WHERE page_id IN(...)` 不保序 → Python 端依 RRF 重排。

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_orchestrator_db.py`：
```python
import os
import uuid

import asyncpg
import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.orchestrator import retrieve
from anatomy_backend.retrieval.query_repr import QueryRepr
from anatomy_backend.retrieval.types import RetrievalResult

pytestmark = pytest.mark.db
KB = 7


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


async def _seed(conn, kb, pages):
    """pages: list[(page_num, docling_md, patches:list[bytes16])]，第 0 頁為 query 目標頁。"""
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb})")
    book = await conn.fetchval(
        "INSERT INTO books (title, edition) VALUES ('Atlas','42e') RETURNING book_id")
    rng = np.random.default_rng(5)
    ids = []
    for num, md, patches in pages:
        pv = "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1,$2,'s3://p.png',$3,'{\"figures\":[\"1.1\"]}'::jsonb,$4::halfvec,$5,'m')"
            " RETURNING page_id", book, num, md, pv, kb)
        ids.append(pid)
        recs = [(kb, pid, j, asyncpg.BitString.frombytes(p, bitlength=128))
                for j, p in enumerate(patches)]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
    return book, ids


async def test_retrieve_returns_ranked_results(pool):
    rng = np.random.default_rng(13)
    target_patches = [rng.bytes(16) for _ in range(8)]
    pages = [
        (1, "biceps brachii origin insertion", target_patches),
        (2, "femur thigh bone", [rng.bytes(16) for _ in range(8)]),
        (3, "scapula shoulder", [rng.bytes(16) for _ in range(8)]),
    ]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            _, ids = await _seed(conn, KB, pages)
        # query：tokens = target page patches（MaxSim 最高）；translated_q 命中第 1 頁文字
        qr = QueryRepr(
            pooled_f32=tuple(float(x) for x in rng.standard_normal(128)),
            tokens_bin=tuple(target_patches[:6]),
            translated_q="biceps brachii origin", lang="zh")
        res = await retrieve(pool, "二頭肌起止點", qr, None, kb_version=KB, top_n=3)
        assert isinstance(res, list) and all(isinstance(r, RetrievalResult) for r in res)
        assert res[0].page_id == ids[0]           # 視覺 + 文字雙命中 → RRF 最高
        assert res[0].book_title == "Atlas" and res[0].edition == "42e"
        assert res[0].metadata["figures"] == ["1.1"]
        # 順序 = RRF 遞減（score 單調不增）
        assert [r.score for r in res] == sorted((r.score for r in res), reverse=True)
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_retrieve_bm25_uses_translated_q(pool):
    rng = np.random.default_rng(17)
    pages = [
        (1, "clavicle collarbone", [rng.bytes(16) for _ in range(8)]),
        (2, "patella kneecap", [rng.bytes(16) for _ in range(8)]),
    ]
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            _, ids = await _seed(conn, KB, pages)
        # 原文中文不會命中英文 tsvector；translated_q='clavicle' 命中第 1 頁
        qr = QueryRepr(pooled_f32=tuple([0.0] * 128),
                       tokens_bin=tuple(rng.bytes(16) for _ in range(6)),
                       translated_q="clavicle", lang="zh")
        res = await retrieve(pool, "鎖骨", qr, None, kb_version=KB, top_n=3)
        # 第 1 頁因 BM25（translated_q）入榜
        assert ids[0] in [r.page_id for r in res]
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")


async def test_retrieve_degrades_on_stage_b_failure(pool, monkeypatch):
    """Codex review #2：Stage B 失敗時 orchestrator 仍回結果（Stage A 降級 + BM25），
    非整請求失敗——savepoint 隔離使外層 txn 的 BM25/metadata 照常。"""
    import anatomy_backend.retrieval.engine_selfbuilt as esb
    rng = np.random.default_rng(19)
    target_patches = [rng.bytes(16) for _ in range(8)]
    pages = [
        (1, "biceps brachii origin", target_patches),
        (2, "femur thigh bone", [rng.bytes(16) for _ in range(8)]),
    ]

    async def _boom(conn, cand, tokens, kb, top_n):
        raise asyncpg.exceptions.QueryCanceledError("simulated statement timeout")

    monkeypatch.setitem(esb._STAGE_B, "sql", _boom)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            _, ids = await _seed(conn, KB, pages)
        qr = QueryRepr(
            pooled_f32=tuple(float(x) for x in rng.standard_normal(128)),
            tokens_bin=tuple(target_patches[:6]),
            translated_q="biceps brachii origin", lang="zh")
        res = await retrieve(pool, "二頭肌", qr, None, kb_version=KB, top_n=3)
        assert len(res) >= 1                          # 降級仍回非空（Stage A + BM25 RRF）
        assert ids[0] in [r.page_id for r in res]
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_orchestrator_db.py -v`
Expected: FAIL（`ModuleNotFoundError: ...orchestrator`）

- [ ] **Step 3: 實作**

`backend/src/anatomy_backend/retrieval/orchestrator.py`：
```python
"""檢索主入口（§4.7）。

單一 PgBouncer 連線 + 單一 transaction（hnsw_search_txn）序列跑
Stage A → Stage B → BM25 → 單一 SQL metadata fetch（DL-002）。連線用完即還，
不跨 LLM 串流（Phase 8 落實串流，此處設計先對）。
"""
import json
from uuid import UUID

import asyncpg

from ..db.tx_helpers import hnsw_search_txn
from .bm25 import bm25_search
from .engine import RetrievalEngine
from .engine_selfbuilt import SelfBuiltEngine
from .query_repr import QueryRepr
from .rrf import rrf_fuse
from .types import RetrievalResult

_METADATA_SQL = """
SELECT p.page_id, p.page_num, p.page_image_uri, p.docling_md, p.metadata,
       b.title AS book_title, b.edition
FROM pages p JOIN books b USING (book_id)
WHERE p.page_id = ANY($1::uuid[]) AND p.kb_version = $2
"""


async def retrieve(
    pool: asyncpg.Pool,
    query: str,
    query_repr: QueryRepr,
    metadata_filter: dict | None,
    kb_version: int,
    top_n: int = 3,
    engine: RetrievalEngine | None = None,
    ef_search: int = 100,
) -> list[RetrievalResult]:
    engine = engine or SelfBuiltEngine()
    async with hnsw_search_txn(pool, ef_search=ef_search) as conn:
        # DL-002：單一 conn 序列（asyncpg 禁同連線併發）
        er = await engine.retrieve(
            conn, query_repr, metadata_filter, kb_version, top_k=100, top_n=10)
        # §1.8 降級：Stage B 逾時/失敗（er.degraded）→ 用 Stage A 排序 top-N 餵 RRF；
        # er.degraded 供 Phase 9 trace 標記（此處不另外 log）
        vector_ids = [pid for pid, _ in er.ranked] if er.ranked else er.coarse_ids[:10]
        bm25_q = query_repr.translated_q or query                  # DL-013/DL-020
        bm25_res = await bm25_search(conn, bm25_q, kb_version, top_k=50)
        fused = rrf_fuse([vector_ids, bm25_res])
        final = fused[:top_n]
        final_ids = [pid for pid, _ in final]
        final_scores = dict(final)
        if not final_ids:
            return []
        rows = await conn.fetch(_METADATA_SQL, final_ids, kb_version)
    by_id = {r["page_id"]: r for r in rows}                        # IN 不保序
    out: list[RetrievalResult] = []
    for pid in final_ids:                                          # 依 RRF 順序重排
        r = by_id.get(pid)
        if r is None:
            continue
        meta = r["metadata"]
        out.append(RetrievalResult(
            page_id=pid, score=final_scores[pid],
            book_title=r["book_title"], edition=r["edition"],
            page_num=r["page_num"], page_image_uri=r["page_image_uri"],
            docling_md=r["docling_md"],
            metadata=meta if isinstance(meta, dict) else json.loads(meta)))
    return out
```

更新 `backend/src/anatomy_backend/retrieval/__init__.py`：
```python
"""Phase 5 兩階段檢索（self-built baseline，引擎中立）。"""
from .orchestrator import retrieve
from .query_repr import QueryRepr
from .types import RetrievalResult

__all__ = ["retrieve", "QueryRepr", "RetrievalResult"]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_orchestrator_db.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/orchestrator.py backend/src/anatomy_backend/retrieval/__init__.py backend/tests/test_orchestrator_db.py
git commit -m "feat(retrieval): orchestrator 主入口（DL-002 序列, translated_q BM25, IN 不保序重排, 帶 kb_version, §1.8 降級）"
```

---

## Task 11: recall harness gate（D-P 接線 smoke）

**Files:**
- Test: `backend/tests/test_recall_gate_db.py`

**目的：** 把 orchestrator 接進 `anatomy_eval.harness.evaluate_recall_by_class`，用**植入式**合成語料（query tokens=目標頁 patches → MaxSim 必最高）證明檢索鏈端到端把目標頁撈回（recall@K=1.0）。含一題中文 query（靠 `translated_q` 經 BM25 命中），驗證 §4.5 中英混合主力流量路徑。**這是 plumbing smoke，非 DL-013 真實品質 gate（真實 ColPali+教材 recall 留 Phase 11）。**

- [ ] **Step 1: 寫失敗測試**

`backend/tests/test_recall_gate_db.py`：
```python
import os
import uuid

import asyncpg
import numpy as np
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.retrieval.orchestrator import retrieve
from anatomy_backend.retrieval.query_repr import QueryRepr
from anatomy_eval.golden import GoldenQA
from anatomy_eval.harness import evaluate_recall_by_class

pytestmark = pytest.mark.db
KB = 8


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        pg_direct_url=os.environ["PG_DIRECT_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ))
    yield p
    await p.close()


async def test_recall_gate_plumbing(pool):
    """植入式語料：每題目標頁的 patches = 該題 query tokens → MaxSim 必中。"""
    rng = np.random.default_rng(31)
    # 20 頁 distractor + 3 頁目標（text_only / figure_id / cross_page 各一）
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute(
                f"CREATE TABLE page_patches_v{KB} PARTITION OF page_patches "
                f"FOR VALUES IN ({KB})")
            book = await conn.fetchval(
                "INSERT INTO books (title) VALUES ('recall') RETURNING book_id")

            async def add_page(num, md, patches):
                pv = "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"
                pid = await conn.fetchval(
                    "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md,"
                    " metadata, pooled, kb_version, embed_model)"
                    " VALUES ($1,$2,'s3://x',$3,'{}'::jsonb,$4::halfvec,$5,'m')"
                    " RETURNING page_id", book, num, md, pv, KB)
                recs = [(KB, pid, j, asyncpg.BitString.frombytes(p, bitlength=128))
                        for j, p in enumerate(patches)]
                await conn.copy_records_to_table(
                    "page_patches", records=recs,
                    columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
                return pid

            for n in range(20):
                await add_page(n + 1, f"distractor page {n}",
                               [rng.bytes(16) for _ in range(8)])
            targets = {}
            specs = [("q_text", "biceps brachii origin", "text_only"),
                     ("q_fig", "deltoid muscle figure", "figure_id"),
                     ("q_cross", "brachial plexus pathway", "cross_page")]
            qreprs = {}
            for i, (qid, text, _cat) in enumerate(specs):
                patches = [rng.bytes(16) for _ in range(8)]
                pid = await add_page(100 + i, text, patches)
                targets[qid] = pid
                qreprs[qid] = QueryRepr(
                    pooled_f32=tuple(float(x) for x in rng.standard_normal(128)),
                    tokens_bin=tuple(patches[:6]),
                    translated_q=text, lang="zh")

        golden = [
            GoldenQA(id=qid, category=cat, query=text,
                     expected_pages=(str(targets[qid]),))
            for (qid, text, cat) in specs
        ]

        async def _retrieve_ids(qa: GoldenQA):
            res = await retrieve(pool, qa.query, qreprs[qa.id], None,
                                 kb_version=KB, top_n=3)
            return [str(r.page_id) for r in res]

        # harness 為同步介面；逐題以 asyncio.run 過 orchestrator
        import asyncio

        def retrieve_fn(qa):
            return asyncio.get_event_loop().run_until_complete(_retrieve_ids(qa))

        # 因已在 event loop 內，改用直接 await 收集後餵 harness（見下）
        id_map = {qa.id: await _retrieve_ids(qa) for qa in golden}
        report = evaluate_recall_by_class(golden, lambda qa: id_map[qa.id], k=3)
        assert report["overall"] == 1.0
        assert report["by_class"]["text_only"] == 1.0
        assert report["by_class"]["figure_id"] == 1.0
        assert report["by_class"]["cross_page"] == 1.0
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{KB}")
            await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
```

> 註：harness 是同步 `Callable`；測試在 async 內先用 `await` 把每題結果收進 `id_map`，再把 `lambda qa: id_map[qa.id]` 餵給 harness，避免 event-loop 重入。實作者若改 harness 介面為 async 須回頭改 eval（不在本 phase 範圍，維持同步餵法）。刪除上面示意用的 `retrieve_fn`（保留 `id_map` 路徑）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_recall_gate_db.py -v`
Expected: FAIL（先因 orchestrator 行為或 import；確認失敗訊息為斷言或模組層級而非語法）

- [ ] **Step 3: 修整測試（移除 event-loop 重入示意碼）**

把 Step 1 測試中 `retrieve_fn` 與 `asyncio` 段刪除，僅保留 `id_map` + `evaluate_recall_by_class(golden, lambda qa: id_map[qa.id], k=3)`。確保測試本體乾淨。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_recall_gate_db.py -v`
Expected: PASS（1 passed；recall@3 == 1.0 三類全中）

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_recall_gate_db.py
git commit -m "test(retrieval): recall harness gate 接線 smoke（植入語料 recall@3=1.0, 含中文 query→translated_q）"
```

---

## Task 12: Stage B 並發/p95 benchmark gate（附錄 D.5，硬性驗收）

**Files:**
- Create: `backend/scripts/bench_stage_b_concurrency.py`
- Modify: `Makefile`（加 `bench-stageb-gate`）

**目的（硬性驗收）：** 在 2000 頁×1024 patches 真實量級下，**並發**量測 Stage B SQL 與 numpy 兩路徑的 p50/p95，對 200ms 預算（§1.7，數值待校準）裁決，並**建議 production 預設 `stage_b_mode`**。DL-013 探針已顯示單連線 p50≈157ms、餘裕僅 ~20%；並發下 SQL 聚合（~2M pair/查詢、PG 後端 CPU 共享）很可能爆預算，numpy 路徑（CPU 移到 app worker、隨 worker 擴展）為 §4.4 退路。**本腳本為手動 gate（非 CI），但 Phase 5 完成前 MUST 跑過並把結果寫入 PR / decisions。**

- [ ] **Step 1: 實作腳本（沿用 bench_stage_b.py 的 seed/cleanup/fail-fast 範式，擴並發 + numpy）**

`backend/scripts/bench_stage_b_concurrency.py`：
```python
"""Stage B 並發/p95 benchmark gate（手動；附錄 D.5 硬性驗收）。

用法（需 migrations 已跑、compose 起 postgres+pgbouncer）：
  DATABASE_URL=postgresql://anatomy:***@localhost:6432/anatomy_rag \\
  uv run --no-sync python backend/scripts/bench_stage_b_concurrency.py \\
      [--pages 2000] [--candidates 100] [--concurrency 32] [--pool-size 10] \\
      [--iters 200] [--budget-ms 200]

對 SQL 與 numpy 兩路徑各跑並發負載，回報 p50/p95/max，對預算裁決並建議預設 mode。
生產保真（Codex review #1）：Stage B 在 hnsw_search_txn + savepoint 內跑（pin 連線同生產），
latency 含 pool.acquire/queue 等待，pool 為生產級固定大小（< concurrency → acquire 排隊，
反映 numpy 占用連線做 Python compute 的爭用代價）；SQL/numpy 各自獨立 warmup。
以 kb_version=998 建合成資料（跑完清除）；所有連線經 PgBouncer :6432。
單連線探針見 bench_stage_b.py（DL-013）。
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid

import asyncpg
import numpy as np

from anatomy_backend.db.tx_helpers import hnsw_search_txn
from anatomy_backend.retrieval.stage_b import stage_b_maxsim, stage_b_maxsim_numpy

PATCHES_PER_PAGE = 1024
QUERY_TOKENS = 20
BENCH_KB = 998


def _rand_bits(rng) -> asyncpg.BitString:
    return asyncpg.BitString.frombytes(rng.bytes(16), bitlength=128)


async def seed(conn, n_pages):
    rng = np.random.default_rng(0)
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{BENCH_KB} "
        f"PARTITION OF page_patches FOR VALUES IN ({BENCH_KB})")
    book_id = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('bench-conc') RETURNING book_id")
    pooled = "[" + ",".join("0.01" for _ in range(128)) + "]"
    page_ids = []
    for i in range(n_pages):
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1,$2,'bench','bench','{}'::jsonb,$3::halfvec,$4,'bench')"
            " RETURNING page_id", book_id, i + 1, pooled, BENCH_KB)
        page_ids.append(pid)
        recs = [(BENCH_KB, pid, j, _rand_bits(rng)) for j in range(PATCHES_PER_PAGE)]
        await conn.copy_records_to_table(
            "page_patches", records=recs,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"])
        if (i + 1) % 200 == 0:
            print(f"  seeded {i + 1}/{n_pages}")
    return book_id, page_ids


async def cleanup(conn, book_id):
    await conn.execute(f"DROP TABLE IF EXISTS page_patches_v{BENCH_KB}")
    await conn.execute(
        "DELETE FROM pages WHERE kb_version = $1 AND book_id = $2", BENCH_KB, book_id)
    await conn.execute("DELETE FROM books WHERE book_id = $1", book_id)


async def _run_path(pool, fn, page_ids, n_cand, iters, concurrency, rng):
    """模型生產 Stage B：在 hnsw_search_txn + savepoint 內跑（pin 連線、同生產 txn 生命週期），
    latency 從 pool.acquire 之前起算（含 queue 等待）。offered concurrency = burst；pool < concurrency
    時於 acquire 排隊——numpy 占用連線做 Python compute 的代價會反映在 p95（Codex review #1）。"""
    sem = asyncio.Semaphore(concurrency)
    latencies = []

    async def one():
        cand = [page_ids[i] for i in rng.choice(len(page_ids), n_cand, replace=False)]
        tokens = [rng.bytes(16) for _ in range(QUERY_TOKENS)]
        async with sem:
            t0 = time.perf_counter()                      # 含 acquire/queue 等待
            async with hnsw_search_txn(pool) as conn:     # 同生產：pin 連線 + SET LOCAL
                async with conn.transaction():            # 同生產 Stage B savepoint
                    await fn(conn, cand, tokens, BENCH_KB, 10)
            latencies.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*[one() for _ in range(iters)])
    latencies.sort()
    return {
        "p50_ms": round(statistics.median(latencies), 1),
        "p95_ms": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 1),
        "max_ms": round(latencies[-1], 1),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2000)
    ap.add_argument("--candidates", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=32)   # offered burst（同時在途請求）
    ap.add_argument("--pool-size", type=int, default=10)     # 生產池大小（應對齊部署值）
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--budget-ms", type=int, default=200)
    args = ap.parse_args()

    # 生產級固定池；pool < concurrency → 多餘請求於 acquire 排隊（模型 PgBouncer 占用爭用）
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"], statement_cache_size=0,
        min_size=args.pool_size, max_size=args.pool_size)
    seed_conn = await pool.acquire()
    leftover = await seed_conn.fetchval(
        "SELECT count(*) FROM pages WHERE kb_version = $1", BENCH_KB)
    if leftover:
        await pool.release(seed_conn)
        await pool.close()
        print(f"錯誤：殘留 bench 資料（kb_version={BENCH_KB}：{leftover} 列），請先清理")
        sys.exit(1)

    book_id = None
    try:
        print(f"seeding {args.pages} pages × {PATCHES_PER_PAGE}（首次約數分鐘）…")
        book_id, page_ids = await seed(seed_conn, args.pages)
        await pool.release(seed_conn)
        seed_conn = None
        n_cand = min(args.candidates, len(page_ids))
        rng = np.random.default_rng(1)

        # 各路徑「獨立 warmup → 量測」（Codex review #1：避免 SQL 先暖了 numpy 的資料而失真）
        await _run_path(pool, stage_b_maxsim, page_ids, n_cand, args.concurrency,
                        args.concurrency, rng)
        sql = await _run_path(pool, stage_b_maxsim, page_ids, n_cand, args.iters,
                              args.concurrency, rng)
        await _run_path(pool, stage_b_maxsim_numpy, page_ids, n_cand, args.concurrency,
                        args.concurrency, rng)
        npy = await _run_path(pool, stage_b_maxsim_numpy, page_ids, n_cand, args.iters,
                              args.concurrency, rng)

        recommend = "sql" if sql["p95_ms"] <= args.budget_ms else (
            "numpy" if npy["p95_ms"] <= args.budget_ms else "neither")
        report = {
            "pages": args.pages, "candidates": n_cand, "concurrency": args.concurrency,
            "pool_size": args.pool_size, "iters": args.iters, "budget_ms": args.budget_ms,
            "sql": sql, "numpy": npy, "recommended_stage_b_mode": recommend,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if recommend == "neither":
            print("GATE FAIL：兩路徑 p95 皆超預算——需 INT8 rescore / VectorChord（Phase 12）"
                  " 或調 K / efSearch；見 §4.4 / §4.6")
            sys.exit(2)
        print(f"GATE PASS：建議 production stage_b_mode='{recommend}'")
    finally:
        print("cleaning up…")
        c = await pool.acquire()
        if book_id is not None:
            await cleanup(c, book_id)
        await pool.release(c)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Makefile 加 target**

在 `Makefile` 的 `.PHONY` 補 `bench-stageb-gate`，並加：
```makefile
# Stage B 並發/p95 benchmark gate（手動，硬性驗收；非 CI）。需 compose 起 DB + 已 migrate。
bench-stageb-gate:
	uv run --no-sync python backend/scripts/bench_stage_b_concurrency.py
```
並在 `help` 區加一行說明。

- [ ] **Step 3: 語法/import 健檢（不需 DB）**

Run: `uv run --no-sync python -c "import ast; ast.parse(open('backend/scripts/bench_stage_b_concurrency.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 4: 跑 gate（需 DB；驗收時執行，把結果貼回 PR）**

Run（compose 起 DB + migrate 後）:
```bash
DATABASE_URL=postgresql://anatomy:***@localhost:6432/anatomy_rag \
  make bench-stageb-gate
```
Expected: 印出 JSON 報表 + `GATE PASS：建議 production stage_b_mode='...'`（exit 0）。若 `neither` → 升級路徑見 §4.4。

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/bench_stage_b_concurrency.py Makefile
git commit -m "feat(bench): Stage B 並發/p95 gate（附錄 D.5；SQL vs numpy，建議 production mode）"
```

---

## Task 13: 收尾 — 預設 mode 定案、CI、全測綠

> **決議（2026-06-13，benchmark gate 跑完後）：** Stage B 並發/p95 gate 實測 SQL p95 c1=164ms / c4=448ms / c32=1229ms；numpy 每層級皆更慢（c1=198ms / c32=10919ms）→ **numpy 退路被推翻**。專案負責人選 **Option A**：v1 預設 `stage_b_mode='sql'`（不改）、numpy 保留為已測非預設替代、並發/p95 gate 轉為附錄 D.5 的 **Phase 12（VectorChord）擴展觸發器**。已寫 **DL-024**（decisions.md）+ §1.7 校準註記 + engine_selfbuilt docstring。Step 1/2 依此執行。

**Files:**
- Modify: `backend/src/anatomy_backend/retrieval/engine_selfbuilt.py`（若 benchmark 判 numpy，改預設）
- Modify: `docs/decisions.md`（記 DL-024：Stage B 預設路徑由 benchmark 定）
- Modify: `.github/workflows/ci.yml`（確認 retrieval 測試入對的 job）

- [ ] **Step 1: 依 benchmark 結果定 `SelfBuiltEngine.stage_b_mode` 預設**

跑完 Task 12 gate 後：若建議 `numpy`，把 `engine_selfbuilt.py` 的 `stage_b_mode: str = "sql"` 改為 `"numpy"`，並在 docstring 註明 benchmark 數據（p95）。若建議 `sql`，維持 `"sql"`，docstring 註明餘裕。

- [ ] **Step 2: 寫 DL-024 入 decisions.md**

在 `docs/decisions.md` 末尾加：
```markdown
## DL-024: Stage B 預設精排路徑由 Phase 5 並發/p95 benchmark 裁決

- **狀態**：APPROVED（委派）　**提案者**：main Claude　**日期**：2026-06-13
- **影響檔案**：ARCHITECTURE.md §4.4、backend/retrieval/stage_b.py、engine_selfbuilt.py

### 背景
§4.4 允許 Stage B 在 SQL 聚合不達 200ms 預算時改用應用層 numpy XOR+popcount。
DL-013 探針顯示單連線 p50≈157ms（餘裕 ~20%），並發下 SQL 聚合（PG 後端 CPU 共享）
風險高。

### 提案
兩路徑皆實作並對拍 oracle（等價），production 預設 `stage_b_mode` 由
`bench_stage_b_concurrency.py`（附錄 D.5 gate）的 p95 對 200ms 預算裁決：
<填入 benchmark 實測 sql/numpy p95 + 採用 mode>。兩者皆藏於 §4.7 介面後，
日後可隨資料量重評切換；VectorChord（Phase 12）仍為長期擴展正解。

### 回退成本
低（mode 為 SelfBuiltEngine 參數，改字串即切換；皆 in-Postgres / 應用層）。
```
（填入實測數據；數值非降品質門檻，不需 `eval_thresholds.yaml` 審核流程。）

- [ ] **Step 3: 確認 CI 涵蓋**

檢視 `.github/workflows/ci.yml`：unit job 的 `pytest`（torch-free）應自動跑到 `test_query_repr_unit.py`、`test_rrf_unit.py`、`test_retrieval_types_unit.py`（無 DB、無 torch）；db-integration job（`REQUIRE_DB_TESTS=1`）跑 `@pytest.mark.db` 的 retrieval 測試。確認 db-integration job 已 `uv sync` 含 backend + eval（Stage B 測試 import `anatomy_eval.reference`）。若 eval 未在該 job 安裝，補 `--package anatomy-eval`。單一來源 grep（line 24）已涵蓋 `to_pg_bits`（Stage B 只 import 不重定義）—確認不誤觸。

- [ ] **Step 4: 全測綠（unit 路徑，無 DB）**

Run: `uv run --no-sync pytest backend/tests/test_query_repr_unit.py backend/tests/test_rrf_unit.py backend/tests/test_retrieval_types_unit.py shared/tests/test_binary.py -v`
Expected: 全 PASS

- [ ] **Step 5: 全測綠（db 路徑，需 compose DB）**

Run: `REQUIRE_DB_TESTS=1 ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1 uv run --no-sync pytest backend/tests/ -v`
Expected: 全 PASS（含全部 `*_db.py` retrieval 測試）

- [ ] **Step 6: lint**

Run: `make lint`
Expected: ruff 乾淨

- [ ] **Step 7: Commit**

```bash
git add backend/src/anatomy_backend/retrieval/engine_selfbuilt.py docs/decisions.md .github/workflows/ci.yml
git commit -m "chore(retrieval): Stage B 預設 mode 定案(DL-024) + CI 涵蓋確認 + 全測綠"
```

---

## Self-Review（對 §4 spec 逐項核對）

| Spec 要求 | 對應 Task |
|---|---|
| §4.2 Query 編碼契約（tokens_bin/pooled_f32/translated_q）| Task 3（QueryRepr.from_encode_query_response）|
| §4.3 Stage A halfvec cosine + Top-K=100 + kb_version + metadata 過濾 | Task 5 |
| §4.3 SET LOCAL ef_search + iterative_scan 同 txn | Task 5 前置 + Task 10（hnsw_search_txn）|
| §4.4 Stage B MaxSim、只掃候選、帶 kb_version、與 oracle 一致 | Task 6 |
| §4.4 應用層 numpy MaxSim 退路 | Task 7 |
| §4.4 bytea→bit 不存在 → to_pg_bits text[] | Task 6（`to_pg_bits` 唯一來源）|
| §4.5 BM25 tsvector + ts_rank_cd + kb_version | Task 8 |
| §4.5 BM25 餵 translated_q（null 退原文）| Task 10 |
| §4.5 RRF 融合 | Task 4 + Task 10 |
| §4.7 RetrievalResult 介面 | Task 2 |
| §4.7 DL-002 單一 conn 序列 | Task 10 |
| §4.7 單一 SQL metadata fetch（不 N+1）| Task 10 |
| §4.7 IN 不保序 → Python 重排 | Task 10 |
| §1.8 Stage B timeout → Stage A top-3 降級（不連鎖 abort）| Task 9（savepoint+timeout+EngineResult）+ Task 10 |
| D-K 引擎中立 QueryRepr + engine | Task 3 + Task 9 |
| §4.8 test_stage_a / test_stage_b / test_rrf / test_orchestrator | Task 5/6/4/10 |
| recall harness gate（D-P，含中文 query）| Task 11 |
| 附錄 D.5 Stage B 並發/p95 benchmark gate（硬性）| Task 12 |
| 共用 binarize/to_pg_bits 單一來源 | Task 1/6（import shared）|
| 連線用完即還、不跨 LLM 串流 | Task 10（txn context 退出即還）|

**Placeholder 掃描：** 無 TBD/TODO；每 code step 含完整實作。
**型別一致性：** `stage_a_coarse`/`stage_b_maxsim`/`stage_b_maxsim_numpy`/`bm25_search`/`rrf_fuse`/`RetrievalEngine.retrieve`→`EngineResult`/`orchestrator.retrieve`/`QueryRepr`/`RetrievalResult` 跨 task 簽章一致；`stage_b_mode` 在 Task 7/9/12/13 一致為 `"sql"|"numpy"`；`_STAGE_B` dict 為 monkeypatch 注入點（Task 9/10 降級測試）。
**Codex 對抗式審查 round 1（4 項）覆蓋：** #1 benchmark 生產保真→Task 12 重設計；#2 §1.8 降級+savepoint→Task 2/9/10；#3 Stage A 精確 oracle→Task 5；#4 邊界位元端到端等價→Task 6/7。

**已知尾巴（非本 phase 範圍）：** 真實 ColPali+教材 recall by-class gate（Phase 11）；連線不跨 LLM 串流的串流實作（Phase 8）；VectorChord float adapter（Phase 12，QueryRepr.float_multivector 已留位）。
