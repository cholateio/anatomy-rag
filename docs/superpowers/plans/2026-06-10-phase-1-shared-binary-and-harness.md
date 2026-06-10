# Phase 1 — 共用二值化/池化 + 評估 harness 種子 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **模型分工（使用者 2026-06-10 指示）**：implementer subagent 一律 `model: sonnet`；任務間審查（spec 合規＋程式品質）照 subagent-driven 流程；phase 結尾跨模型審查走 Codex。
>
> **權威 spec**：`docs/ARCHITECTURE.md` §2.3/§2.4/§4.2/§4.4/§7.2/§7.3 + `docs/decisions.md` DL-013/019/020。
> 衝突時以 spec 為準。**禁止**改動 `DECIDED` 項；觸及時停下回報。

**Goal:** 交付離線端與 query 端**唯一**的二值化/池化來源（`shared/binary.py` 補齊 `pool_patches`/`hamming_distance`）、torch-free 的 `MockColPaliRuntime`（colpali_service 改為共用它），以及 D-P 評估 gate 的種子：`tests/golden_qa.seed.jsonl` + `eval` 的 recall@K by class harness（可對合成資料跑通）。

**Architecture:** `shared/binary.py`（純 numpy，禁 torch）是兩端共用的向量運算層；`shared/colpali_runtime.py` 定義 encoder runtime 介面並提供決定性 mock（真實 torch runtime 留待 Phase 3，等 GPU + transformers 5.10.2 相容性驗證）；`colpali_service` 的 mock 改為 delegate 到 shared runtime + `pool_patches`，消除重複邏輯；`anatomy_eval` 瘦身為輕量基底（numpy/pyyaml），RAGAS/Streamlit 移到 optional extras（Phase 11 才裝），讓 recall harness 能進 CI unit job。

**Tech Stack:** Python 3.11+ / uv workspace、numpy、pytest（asyncio_mode=auto）、ruff。**不新增任何套件**（eval 只是把既有依賴移到 extras）。

---

## 現況與約束（implementer 必讀）

- `shared/src/anatomy_shared/binary.py` 已有：`VECTOR_DIM=128`、`binarize()`（sign-based、MSB-first）、`to_pg_bits()`。**不要動它們的行為**。
- `colpali_service/src/colpali_service/encoder.py` 已有 `MockEncoder`（含 DL-020 的 `translated_q`/`lang`/`mt_model` identity mock）與 `_seeded_vectors`；本計畫把向量產生邏輯上移到 shared，**wire 契約欄位不可變**（`tokens_bin`/`pooled_f32`/`translated_q`/`lang`/`model`/`mt_model`）。
- DL-019：pooled 向量**不二值化**，float16 存 DB（wire 上為 512B LE float32）；cosine 對縮放不敏感 → pool 後**不需** re-normalize。
- pytest 約定（repo 根 `pyproject.toml`）：測試目錄**無 `__init__.py`**、**測試檔名全域唯一**。
- uv 坑：驗證一律 `uv sync --package <m> --inexact`（累加、不剪成員）後 `uv run --no-sync …`。
- §7.2：黃金題庫**沒有** `should_refuse` 類別；`out_of_scope` 測「教材中查無此項」，不計 retrieval recall。
- 提交訊息風格沿用 repo：`feat(phase-1): …` 繁體中文。

## 檔案結構地圖

```
shared/src/anatomy_shared/binary.py            # 修改：+pool_patches、+hamming_distance
shared/src/anatomy_shared/colpali_runtime.py   # 新建：MockColPaliRuntime + get_runtime（真實版 Phase 3）
shared/tests/test_binary.py                    # 修改：+pool/hamming 測試
shared/tests/test_colpali_runtime.py           # 新建：mock 形狀/決定性 + torch-free 斷言
colpali_service/src/colpali_service/encoder.py # 修改：MockEncoder delegate 到 shared runtime
eval/pyproject.toml                            # 修改：基底瘦身（numpy/pyyaml/anatomy-shared），ragas/review 移 extras
eval/src/anatomy_eval/golden.py                # 新建：GoldenQA dataclass + load_golden（schema 驗證）
eval/src/anatomy_eval/reference.py             # 新建：maxsim_hamming 參考實作（Phase 5 測試 oracle）
eval/src/anatomy_eval/harness.py               # 新建：recall_at_k + evaluate_recall_by_class（D-P）
eval/tests/test_golden_schema.py               # 新建
eval/tests/test_reference_maxsim.py            # 新建
eval/tests/test_recall_harness.py              # 新建：含合成資料端到端 gate
tests/golden_qa.seed.jsonl                     # 新建：每類 ≥2 題種子（教師於 Phase 11 校正）
.github/workflows/ci.yml                       # 修改：+eval 同步與測試、+binarize 重複定義斷言
Makefile                                       # 修改：修 lint/fmt 剪除 workspace 成員的坑
uv.lock                                        # 隨 eval/pyproject 變更重新 lock（uv lock）
```

---

### Task 0: 開分支

- [ ] **Step 1: 建立 feature branch**

```bash
cd /home/cholate/anatomy-rag
git checkout -b feat/phase-1-shared-binary-harness
```

預期：`Switched to a new branch 'feat/phase-1-shared-binary-harness'`

---

### Task 1: eval 套件瘦身（基底輕量化，RAGAS 移 extras）

**Files:**
- Modify: `eval/pyproject.toml`
- Modify: `uv.lock`（由 `uv lock` 產生）

理由：D-P 的 recall harness 要進 CI unit job；現在 eval 基底背著 ragas/langchain/streamlit（重型、Phase 11 才用），會把 CI 拖垮。**不新增任何套件**，只把既有依賴移到 optional extras。

- [ ] **Step 1: 改寫 `eval/pyproject.toml` 為下列完整內容**

```toml
[project]
name = "anatomy-eval"
version = "0.0.0"
requires-python = ">=3.11"
# 基底保持輕量（recall harness 進 CI unit job 用）；RAGAS/抽檢工具為 Phase 11 extras。
dependencies = ["anatomy-shared", "numpy>=1.26", "pyyaml>=6"]

[project.optional-dependencies]
ragas = ["ragas>=0.4,<0.5", "langchain-openai>=0.1", "datasets>=2.19"]
review = ["streamlit>=1.35"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/anatomy_eval"]
```

- [ ] **Step 2: 重新 lock 並同步**

```bash
uv lock
uv sync --package anatomy-eval --inexact
uv run --no-sync python -c "import anatomy_eval, anatomy_shared; print('eval base OK')"
```

預期：lock 成功、印出 `eval base OK`（不會安裝 ragas/streamlit）。

- [ ] **Step 3: Commit**

```bash
git add eval/pyproject.toml uv.lock
git commit -m "feat(phase-1): eval 基底瘦身——ragas/streamlit 移 optional extras，harness 可進 CI unit job"
```

---

### Task 2: `pool_patches`（DL-019 池化，fp32 累加 → float16）

**Files:**
- Modify: `shared/src/anatomy_shared/binary.py`
- Test: `shared/tests/test_binary.py`

- [ ] **Step 1: 在 `shared/tests/test_binary.py` 追加失敗測試**

```python
# --- pool_patches（DL-019：fp32 平均、輸出 float32；halfvec 量化只發生在 DB 綁定層）---


def test_pool_patches_shape_dtype_and_mean():
    """(n,128) → (128,) float32；值為逐維 fp32 平均（不得提早 f16 量化）。"""
    from anatomy_shared.binary import pool_patches

    patches = np.stack([np.full(VECTOR_DIM, 1.0), np.full(VECTOR_DIM, 3.0)])
    pooled = pool_patches(patches)
    assert pooled.shape == (VECTOR_DIM,) and pooled.dtype == np.float32
    assert np.allclose(pooled, 2.0)


def test_pool_patches_accumulates_in_fp32():
    """float16 輸入也必須以 fp32 累加：±fp16max 與 2.0 的平均應為有限值 ≈ 0.667。"""
    from anatomy_shared.binary import pool_patches

    big = np.float16(65504.0)  # fp16 最大值；fp16 直接相加會溢位成 inf
    patches = np.stack([
        np.full(VECTOR_DIM, big, dtype=np.float16),
        np.full(VECTOR_DIM, -big, dtype=np.float16),
        np.full(VECTOR_DIM, 2.0, dtype=np.float16),
    ])
    pooled = pool_patches(patches)
    assert np.all(np.isfinite(pooled))
    assert np.allclose(pooled, 2.0 / 3.0, atol=1e-3)


def test_pool_patches_valid_mask_excludes_padding():
    """valid_mask=False 的列（padding/特殊 token）不得進入平均。"""
    from anatomy_shared.binary import pool_patches

    patches = np.stack([
        np.full(VECTOR_DIM, 1.0),
        np.full(VECTOR_DIM, 999.0),  # padding 列，應被排除
    ])
    pooled = pool_patches(patches, valid_mask=[True, False])
    assert np.allclose(pooled, 1.0)


def test_pool_patches_rejects_empty_and_bad_shape():
    from anatomy_shared.binary import pool_patches

    with pytest.raises(ValueError):
        pool_patches(np.ones((2, 64)))                      # 維度錯
    with pytest.raises(ValueError):
        pool_patches(np.ones((2, VECTOR_DIM)), valid_mask=[False, False])  # 全被遮罩
    with pytest.raises(ValueError):
        pool_patches(np.ones((2, VECTOR_DIM)), valid_mask=[True])          # mask 長度錯


def test_pool_patches_accepts_list_input():
    from anatomy_shared.binary import pool_patches

    pooled = pool_patches([[0.5] * VECTOR_DIM, [1.5] * VECTOR_DIM])
    assert np.allclose(pooled, 1.0)
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
uv run --no-sync pytest shared/tests/test_binary.py -q
```

預期：新測試 FAIL（`ImportError: cannot import name 'pool_patches'`），既有測試 PASS。

- [ ] **Step 3: 在 `shared/src/anatomy_shared/binary.py` 末尾實作**

```python
def pool_patches(patch_embs, valid_mask=None) -> np.ndarray:
    """多向量 → 單一 pooled 向量（DL-019）：fp32 平均，輸出 float32[128]，不二值化。

    valid_mask（選填，shape=(n,) bool）：False 的列（padding/特殊 token）不進平均。
    Stage A 用 cosine 距離（對縮放不敏感），故 pool 後不需 re-normalize；
    float16（halfvec）量化**只發生在 DB 綁定/寫入層**，不在此處——query 端
    提早量化會無謂損失精度（Codex 審查 HIGH-1）。
    離線建庫端與 query 端 MUST 共用本函式（§2.4 同一來源原則）。
    """
    arr = np.asarray(patch_embs, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != VECTOR_DIM:
        raise ValueError(f"pool_patches 期望 (n, {VECTOR_DIM}) 矩陣，收到 {arr.shape}")
    if valid_mask is not None:
        mask = np.asarray(valid_mask, dtype=bool)
        if mask.shape != (arr.shape[0],):
            raise ValueError(f"valid_mask 長度 {mask.shape} 與 patch 數 {arr.shape[0]} 不符")
        arr = arr[mask]
    if arr.shape[0] == 0:
        raise ValueError("沒有有效 patch 可池化（全部被 valid_mask 排除或輸入為空）")
    return arr.mean(axis=0)
```

- [ ] **Step 4: 跑測試確認全綠**

```bash
uv run --no-sync pytest shared/tests/test_binary.py -q
```

預期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add shared/src/anatomy_shared/binary.py shared/tests/test_binary.py
git commit -m "feat(phase-1): pool_patches——fp32 累加、valid_mask 去 padding、輸出 float32（halfvec 量化留在 DB 綁定層，DL-019）"
```

---

### Task 3: `hamming_distance`

**Files:**
- Modify: `shared/src/anatomy_shared/binary.py`
- Test: `shared/tests/test_binary.py`

- [ ] **Step 1: 追加失敗測試**

```python
# --- hamming_distance ---


def test_hamming_distance_identical_zero_and_complement_full():
    from anatomy_shared.binary import hamming_distance

    a = binarize(np.random.default_rng(7).standard_normal(VECTOR_DIM))
    assert hamming_distance(a, a) == 0
    flipped = bytes(b ^ 0xFF for b in a)
    assert hamming_distance(a, flipped) == 128


def test_hamming_distance_known_value():
    from anatomy_shared.binary import hamming_distance

    a = b"\x00" * 15 + b"\x0f"   # 末 4 bit 不同
    b = b"\x00" * 16
    assert hamming_distance(a, b) == 4


def test_hamming_distance_length_mismatch_raises():
    from anatomy_shared.binary import hamming_distance

    with pytest.raises(ValueError):
        hamming_distance(b"\x00" * 16, b"\x00" * 15)
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
uv run --no-sync pytest shared/tests/test_binary.py -q
```

預期：新測試 FAIL（ImportError），其餘 PASS。

- [ ] **Step 3: 實作（`binary.py` 末尾）**

```python
def hamming_distance(a: bytes, b: bytes) -> int:
    """兩個等長 bit 串（bytes）的 Hamming 距離；§4.4 `<~>` 的純 Python 對照。"""
    if len(a) != len(b):
        raise ValueError(f"長度不一致：{len(a)} vs {len(b)} bytes")
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).bit_count()
```

- [ ] **Step 4: 跑測試確認全綠**

```bash
uv run --no-sync pytest shared/tests/test_binary.py -q
```

- [ ] **Step 5: Commit**

```bash
git add shared/src/anatomy_shared/binary.py shared/tests/test_binary.py
git commit -m "feat(phase-1): hamming_distance（pgvector <~> 的純 Python 對照）"
```

---

### Task 4: `shared/colpali_runtime.py` — MockColPaliRuntime（torch-free）

**Files:**
- Create: `shared/src/anatomy_shared/colpali_runtime.py`
- Test: `shared/tests/test_colpali_runtime.py`

設計（Codex 審查 HIGH-3/MEDIUM-5 修訂）：runtime 負責「輸入 → float32 向量矩陣 + `valid_mask`」，
回傳穩定的 `EncodedVectors` 結構；binarize/pool 由呼叫端用 `shared.binary` 組合（單一來源）。
**介面（`encode_query`/`encode_page` → `EncodedVectors`）自本 Task 起即為穩定契約**；真實 torch
runtime（`ColPaliForRetrieval` + `ColPaliProcessor`、bf16、SDPA）**由 Phase 3 承接實作**（需 GPU +
transformers 5.10.2 相容性驗證），本模組對 `mock=False` 拋出指引清楚的 `NotImplementedError`。
mock 的 query 輸出固定把**前 2 個位置標為特殊/前綴 token（valid_mask=False）**，讓下游「池化排除
前綴」的路徑被實際演練。

- [ ] **Step 1: 建立失敗測試 `shared/tests/test_colpali_runtime.py`**

```python
"""MockColPaliRuntime 契約測試：形狀、valid_mask、決定性、torch-free（D-L）。"""
import subprocess
import sys

import numpy as np
import pytest
from anatomy_shared.colpali_runtime import EncodedVectors, MockColPaliRuntime, get_runtime


def test_fresh_import_is_torch_free():
    """以 fresh subprocess 驗證 import 不拉 torch（D-L）；
    避免同進程中其他測試/插件已載入 torch 造成誤判（Codex MEDIUM-6）。"""
    code = "import anatomy_shared.colpali_runtime, sys; assert 'torch' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_mock_encode_query_shape_mask_and_determinism():
    rt = MockColPaliRuntime()
    enc1 = rt.encode_query("肱二頭肌的起止點")
    enc2 = rt.encode_query("肱二頭肌的起止點")
    assert isinstance(enc1, EncodedVectors)
    assert enc1.embeddings.shape == (rt.n_query_tokens, 128)
    assert enc1.embeddings.dtype == np.float32
    assert enc1.valid_mask.shape == (rt.n_query_tokens,) and enc1.valid_mask.dtype == np.bool_
    # mock 固定把前 2 個位置標為特殊/前綴 token（False）——演練「池化排除前綴」
    assert not enc1.valid_mask[0] and not enc1.valid_mask[1]
    assert enc1.valid_mask.sum() == rt.n_query_tokens - rt.n_special_prefix
    assert np.array_equal(enc1.embeddings, enc2.embeddings)
    assert not np.array_equal(enc1.embeddings, rt.encode_query("另一個問題").embeddings)


def test_mock_encode_page_accepts_str_key_and_array_image():
    rt = MockColPaliRuntime()
    p1 = rt.encode_page("gray42:812")
    p2 = rt.encode_page("gray42:812")
    assert p1.embeddings.shape == (rt.n_page_patches, 128) and p1.embeddings.dtype == np.float32
    assert bool(p1.valid_mask.all())                  # 頁面 patch 全有效
    assert np.array_equal(p1.embeddings, p2.embeddings)
    arr_img = np.zeros((4, 4, 3), dtype=np.uint8)     # array-like 影像（真實版為 PIL）
    a1, a2 = rt.encode_page(arr_img), rt.encode_page(arr_img)
    assert np.array_equal(a1.embeddings, a2.embeddings)


def test_get_runtime_real_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="Phase 3"):
        get_runtime(mock=False)
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
uv run --no-sync pytest shared/tests/test_colpali_runtime.py -q
```

預期：FAIL（`ModuleNotFoundError: anatomy_shared.colpali_runtime`）。

- [ ] **Step 3: 建立 `shared/src/anatomy_shared/colpali_runtime.py`**

```python
"""ColPali runtime 介面與決定性 mock（Phase 1）。

runtime 負責「輸入 → float32 向量矩陣 + valid_mask」（EncodedVectors）；二值化/池化
由呼叫端用 `anatomy_shared.binary` 組合（§2.4 單一來源）。真實 torch runtime
（ColPaliForRetrieval + ColPaliProcessor、bf16、SDPA、cu128）由 Phase 3 承接實作——
須先在 GPU 上驗 transformers 5.10.2 與 vidore/colpali-v1.3-hf 的相容性；
介面契約（encode_query/encode_page → EncodedVectors）自本檔起即為穩定契約。
本模組（mock 路徑）MUST 維持 torch-free（D-L）。
"""
import hashlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EncodedVectors:
    """runtime 輸出：embeddings (n,128) float32 + valid_mask (n,) bool（False=padding/特殊 token）。"""

    embeddings: np.ndarray
    valid_mask: np.ndarray


def _seeded_vectors(key: str, n: int, dim: int = 128) -> np.ndarray:
    """以字串雜湊播種，產生決定性 float32 向量（mock 用；query/頁面共用）。"""
    seed = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, dim)).astype("float32")


class MockColPaliRuntime:
    """決定性 mock：query → 20 token（前 2 個為特殊/前綴 token）、page → 64 patch（全有效）。"""

    model_id = "mock-colpali"
    n_query_tokens = 20
    n_special_prefix = 2   # 模擬 <bos>/任務前綴——池化與 tokens_bin 都應排除
    n_page_patches = 64    # mock 縮小規模以利測試；真實 ColPali 約 1024 patch/頁

    def encode_query(self, q: str) -> EncodedVectors:
        emb = _seeded_vectors(f"q::{q}", self.n_query_tokens)
        mask = np.ones(self.n_query_tokens, dtype=bool)
        mask[: self.n_special_prefix] = False
        return EncodedVectors(embeddings=emb, valid_mask=mask)

    def encode_page(self, image) -> EncodedVectors:
        """image：真實版收 PIL 影像；mock 另接受 str 鍵或 array-like（以位元組雜湊播種）。"""
        if isinstance(image, str):
            key = f"p::{image}"
        else:
            key = "p::" + hashlib.sha256(np.asarray(image).tobytes()).hexdigest()
        emb = _seeded_vectors(key, self.n_page_patches)
        return EncodedVectors(embeddings=emb, valid_mask=np.ones(self.n_page_patches, dtype=bool))


def get_runtime(mock: bool = True):
    if mock:
        return MockColPaliRuntime()
    raise NotImplementedError(
        "真實 ColPali runtime（ColPaliForRetrieval + ColPaliProcessor，bf16/SDPA/cu128）"
        "於 Phase 3 實作——需 GPU 並先驗 transformers 5.10.2 與 vidore/colpali-v1.3-hf "
        "相容性。目前請用 mock=True；介面契約（EncodedVectors）已固定。"
    )
```

- [ ] **Step 4: 跑測試確認全綠**

```bash
uv run --no-sync pytest shared/tests/test_colpali_runtime.py -q
```

- [ ] **Step 5: Commit**

```bash
git add shared/src/anatomy_shared/colpali_runtime.py shared/tests/test_colpali_runtime.py
git commit -m "feat(phase-1): EncodedVectors 穩定介面 + MockColPaliRuntime（torch-free、含特殊 token mask），真實 runtime 由 Phase 3 承接"
```

---

### Task 5: colpali_service 改用 shared runtime（重構，契約不變）

**Files:**
- Modify: `colpali_service/src/colpali_service/encoder.py`
- Test: `colpali_service/tests/test_encode.py`（既有測試**不改**作守護；**追加**強化契約測試，Codex MEDIUM-4）

注意：`_seeded_vectors` 的播種 key 從 `q` 改為 `f"q::{q}"`，mock 輸出**數值**會變——既有契約測試只斷言決定性/長度/欄位，不綁定數值，預期仍綠。pooled 保持 **fp32 全程不量化**（halfvec 量化留在 DB 綁定層，DL-019/Codex HIGH-1）；`tokens_bin` 與池化都**排除特殊/前綴 token**（valid_mask）。

- [ ] **Step 1: 先跑既有測試（基準綠）**

```bash
uv run --no-sync pytest colpali_service/tests -q
```

預期：全 PASS。

- [ ] **Step 2: 在 `colpali_service/tests/test_encode.py` 追加強化契約測試（先寫、預期 FAIL）**

```python
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
```

執行確認 FAIL：

```bash
uv run --no-sync pytest colpali_service/tests -q
```

- [ ] **Step 3: 改寫 `colpali_service/src/colpali_service/encoder.py` 為**

```python
"""Encoder 抽象：Phase 0/1 決定性 mock（delegate 到 shared runtime）；Phase 3 接真實 ColPali + 本地 MT（DL-020）。"""
import os
import re

from anatomy_shared.binary import binarize, pool_patches
from anatomy_shared.colpali_runtime import MockColPaliRuntime

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


def _detect_lang(text: str) -> str:
    """含 CJK 字元即視為需翻譯的中文/混語 query（DL-020）。"""
    return "zh" if _CJK_RE.search(text) else "en"


class MockEncoder:
    """決定性 mock：滿足 /encode_query 契約，供下游（後端 client、檢索）演練。

    向量來源＝shared 的 MockColPaliRuntime；二值化/池化＝shared.binary（§2.4 單一來源）。
    """

    ready = True

    def __init__(self) -> None:
        self._runtime = MockColPaliRuntime()
        self.model = self._runtime.model_id

    def encode_query(self, q: str) -> dict:
        enc = self._runtime.encode_query(q)
        valid = enc.embeddings[enc.valid_mask]   # 排除 padding/特殊前綴 token（§2.4 / roadmap AC）
        # DL-019：pooled 不二值化、全程 fp32（halfvec 量化只發生在 DB 綁定層）
        pooled_f32 = pool_patches(enc.embeddings, valid_mask=enc.valid_mask).astype("<f4")
        return {
            "tokens_bin": [binarize(t) for t in valid],
            "pooled_f32": pooled_f32.tobytes(),
            # DL-020：mock 為決定性 identity 翻譯（真實本地 MT 於 Phase 3 接 opus-mt-zh-en）
            "translated_q": q,
            "lang": _detect_lang(q),
            "model": self.model,
            "mt_model": "mock-identity",
        }


def get_encoder():
    # Phase 3：ENCODER_MOCK=false 時回真實 ColPali encoder
    if os.environ.get("ENCODER_MOCK", "true").lower() == "true":
        return MockEncoder()
    # 真實 encoder 於 Phase 3 才實作；在此之前（如 make up-gpu 設 ENCODER_MOCK=false）
    # 應給出清楚指引，而非讓容器以難解的 ModuleNotFoundError 崩潰。
    try:
        from colpali_service.real_encoder import RealColPaliEncoder  # Phase 3 實作
    except ModuleNotFoundError as e:
        raise NotImplementedError(
            "真實 ColPali encoder 尚未實作（Phase 3）。目前僅支援 mock：請設 ENCODER_MOCK=true。"
            "（make up-gpu 的真實 GPU 推理路徑將於 Phase 3 接 vidore/colpali-v1.3-hf 後啟用；"
            "Phase 0 的 GPU 硬體驗證請用 make gpu-smoke）"
        ) from e
    return RealColPaliEncoder()
```

- [ ] **Step 4: 跑 colpali_service + shared 全部測試**

```bash
uv run --no-sync pytest colpali_service/tests shared/tests -q
```

預期：全 PASS（含 Step 2 新增的強化契約測試）。

- [ ] **Step 5: Commit**

```bash
git add colpali_service/src/colpali_service/encoder.py colpali_service/tests/test_encode.py
git commit -m "refactor(phase-1): colpali_service mock delegate 到 shared runtime；排除特殊 token、pooled 全程 fp32"
```

---

### Task 6: 黃金題庫種子 + `load_golden` schema 驗證

**Files:**
- Create: `tests/golden_qa.seed.jsonl`
- Create: `eval/src/anatomy_eval/golden.py`
- Test: `eval/tests/test_golden_schema.py`

注意：seed 的 `expected_pages` 用 `"<bookkey>:<page>"` 字串 ID（如 `gray42:812`），**頁碼為佔位假設值**，由解剖教師於 Phase 11 校正（§7.2 人工標註）；Phase 1 的 harness 測試不依賴其真實性（用合成資料）。§7.2 紅線：**沒有 `should_refuse` 類別**。

- [ ] **Step 1: 建立失敗測試 `eval/tests/test_golden_schema.py`**

```python
"""golden_qa.seed.jsonl 與 load_golden 的 schema 驗證（§7.2）。"""
import json
from pathlib import Path

import pytest
from anatomy_eval.golden import ALLOWED_CATEGORIES, GoldenQA, load_golden

SEED = Path(__file__).resolve().parents[2] / "tests" / "golden_qa.seed.jsonl"


def test_seed_file_loads_and_covers_all_classes():
    items = load_golden(SEED)
    assert len(items) >= 10
    by_cat: dict[str, int] = {}
    for it in items:
        by_cat[it.category] = by_cat.get(it.category, 0) + 1
    assert set(by_cat) == ALLOWED_CATEGORIES        # 五類齊
    assert all(n >= 2 for n in by_cat.values())     # 每類 ≥2 題
    assert any("一" <= ch <= "鿿" for it in items for ch in it.query)  # 含中文 query（DL-013）


def test_non_oos_items_have_expected_pages_and_oos_dont():
    for it in load_golden(SEED):
        if it.category == "out_of_scope":
            assert it.expected_pages == () and it.expected_response_type == "教材中查無此項"
        else:
            assert len(it.expected_pages) >= 1 and it.expected_response_type is None


def test_load_golden_rejects_should_refuse(tmp_path):
    """§7.2：黃金題庫沒有 should_refuse 類別——出現即報錯，防止被偷加回。"""
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({
        "id": "x1", "category": "should_refuse", "query": "q",
    }) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="should_refuse"):
        load_golden(bad)


def test_load_golden_rejects_unknown_category_and_duplicate_id(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "x1", "category": "nope", "query": "q"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="category"):
        load_golden(bad)
    dup = tmp_path / "dup.jsonl"
    row = {"id": "same", "category": "text_only", "query": "q", "expected_pages": ["a:1"]}
    dup.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重複"):
        load_golden(dup)


def test_load_golden_rejects_missing_required_and_unknown_fields(tmp_path):
    """Codex MEDIUM-7：缺必要欄位要報清楚錯誤（非 raw KeyError）；未知欄位視為拼字錯誤拒絕。"""
    missing = tmp_path / "missing.jsonl"
    missing.write_text(json.dumps({"category": "text_only", "query": "q",
                                   "expected_pages": ["a:1"]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="必要欄位"):
        load_golden(missing)
    typo = tmp_path / "typo.jsonl"
    typo.write_text(json.dumps({"id": "x", "category": "text_only", "query": "q",
                                "expected_page": ["a:1"]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="未知欄位"):
        load_golden(typo)


def test_goldenqa_is_frozen():
    it = GoldenQA(id="a", category="text_only", query="q", expected_pages=("a:1",))
    with pytest.raises(AttributeError):
        it.query = "changed"  # type: ignore[misc]
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
uv run --no-sync pytest eval/tests/test_golden_schema.py -q
```

預期：FAIL（`ModuleNotFoundError: anatomy_eval.golden`）。

- [ ] **Step 3: 建立 `eval/src/anatomy_eval/golden.py`**

```python
"""黃金題庫載入與 schema 驗證（§7.2）。

紅線：黃金題庫**沒有** `should_refuse` 類別（出現即 ValueError）；
`out_of_scope` 測「教材中查無此項」，不帶 expected_pages、不計 retrieval recall。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_CATEGORIES = {
    "text_only", "figure_id", "cross_page", "clinical_correlation", "out_of_scope",
}

_KNOWN_FIELDS = {"id", "category", "query", "expected_pages", "expected_concepts",
                 "metadata_filter", "expected_response_type"}


@dataclass(frozen=True)
class GoldenQA:
    id: str
    category: str
    query: str
    expected_pages: tuple[str, ...] = ()
    expected_concepts: tuple[str, ...] = ()
    metadata_filter: dict | None = field(default=None)
    expected_response_type: str | None = None


def load_golden(path: str | Path) -> list[GoldenQA]:
    items: list[GoldenQA] = []
    seen_ids: set[str] = set()
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        cat = raw.get("category")
        if cat == "should_refuse":
            raise ValueError(f"line {lineno}: 黃金題庫不得有 should_refuse 類別（§7.2）")
        for req in ("id", "category", "query"):
            if not isinstance(raw.get(req), str) or not raw.get(req):
                raise ValueError(f"line {lineno}: 缺少或非字串的必要欄位 {req!r}")
        unknown = set(raw) - _KNOWN_FIELDS
        if unknown:
            raise ValueError(f"line {lineno}: 未知欄位 {sorted(unknown)}（疑似拼字錯誤）")
        if cat not in ALLOWED_CATEGORIES:
            raise ValueError(f"line {lineno}: 未知 category {cat!r}")
        if raw["id"] in seen_ids:
            raise ValueError(f"line {lineno}: 重複 id {raw['id']!r}")
        seen_ids.add(raw["id"])
        item = GoldenQA(
            id=raw["id"],
            category=cat,
            query=raw["query"],
            expected_pages=tuple(raw.get("expected_pages", [])),
            expected_concepts=tuple(raw.get("expected_concepts", [])),
            metadata_filter=raw.get("metadata_filter"),
            expected_response_type=raw.get("expected_response_type"),
        )
        if cat == "out_of_scope":
            if item.expected_pages:
                raise ValueError(f"line {lineno}: out_of_scope 不得帶 expected_pages")
            if item.expected_response_type != "教材中查無此項":
                raise ValueError(f"line {lineno}: out_of_scope 須 expected_response_type=教材中查無此項")
        elif not item.expected_pages:
            raise ValueError(f"line {lineno}: {cat} 題必須有 expected_pages")
        items.append(item)
    return items
```

- [ ] **Step 4: 建立 `tests/golden_qa.seed.jsonl`（完整內容如下；頁碼為待教師校正的佔位）**

```jsonl
{"id": "seed-text-001", "category": "text_only", "query": "肱二頭肌的起止點是什麼？", "expected_pages": ["gray42:812"], "expected_concepts": ["biceps brachii", "coracoid process", "radial tuberosity"], "metadata_filter": {"anatomy_system": "musculoskeletal"}}
{"id": "seed-text-002", "category": "text_only", "query": "正中神經支配前臂哪些肌肉？", "expected_pages": ["gray42:850"], "expected_concepts": ["median nerve", "flexor digitorum superficialis"], "metadata_filter": {"anatomy_system": "nervous"}}
{"id": "seed-text-003", "category": "text_only", "query": "What is the blood supply of the femoral head?", "expected_pages": ["gray42:1350"], "expected_concepts": ["medial circumflex femoral artery"], "metadata_filter": {"anatomy_system": "cardiovascular"}}
{"id": "seed-fig-001", "category": "figure_id", "query": "請指出臂神經叢五個分區的相對位置", "expected_pages": ["gray42:820", "netter8:416"], "expected_concepts": ["brachial plexus", "roots trunks divisions cords branches"], "metadata_filter": null}
{"id": "seed-fig-002", "category": "figure_id", "query": "In a cross-section at T4, which structures are visible in the superior mediastinum?", "expected_pages": ["gray42:990"], "expected_concepts": ["superior mediastinum", "aortic arch"], "metadata_filter": null}
{"id": "seed-cross-001", "category": "cross_page", "query": "比較上肢與下肢主要關節的屈伸肌群與其神經支配", "expected_pages": ["gray42:812", "gray42:1360"], "expected_concepts": ["flexor compartment", "innervation"], "metadata_filter": {"anatomy_system": "musculoskeletal"}}
{"id": "seed-cross-002", "category": "cross_page", "query": "從發育角度說明前腸、中腸、後腸對應的成體器官與血供", "expected_pages": ["gray42:1100", "gray42:1120"], "expected_concepts": ["foregut", "midgut", "hindgut", "celiac trunk"], "metadata_filter": {"anatomy_system": "digestive"}}
{"id": "seed-clin-001", "category": "clinical_correlation", "query": "肱骨中段骨折最可能傷到哪條神經？會有什麼表現？", "expected_pages": ["gray42:830"], "expected_concepts": ["radial nerve", "wrist drop"], "metadata_filter": null}
{"id": "seed-clin-002", "category": "clinical_correlation", "query": "Which structure is at risk during a McBurney point incision?", "expected_pages": ["gray42:1080"], "expected_concepts": ["iliohypogastric nerve", "appendix"], "metadata_filter": null}
{"id": "seed-oos-001", "category": "out_of_scope", "query": "今天台北的天氣如何？", "expected_response_type": "教材中查無此項"}
{"id": "seed-oos-002", "category": "out_of_scope", "query": "請解釋克氏循環的酵素調控", "expected_response_type": "教材中查無此項"}
```

- [ ] **Step 5: 跑測試確認全綠**

```bash
uv run --no-sync pytest eval/tests/test_golden_schema.py -q
```

- [ ] **Step 6: Commit**

```bash
git add tests/golden_qa.seed.jsonl eval/src/anatomy_eval/golden.py eval/tests/test_golden_schema.py
git commit -m "feat(phase-1): 黃金題庫種子（5類×≥2題，含中文）+ load_golden schema 驗證（§7.2，禁 should_refuse）"
```

---

### Task 7: `maxsim_hamming` 參考實作（Phase 5 測試 oracle）

**Files:**
- Create: `eval/src/anatomy_eval/reference.py`
- Test: `eval/tests/test_reference_maxsim.py`

- [ ] **Step 1: 建立失敗測試 `eval/tests/test_reference_maxsim.py`**

```python
"""maxsim_hamming 參考實作測試——§4.4 score(page)=Σ_t max_p (128 - hamming)。"""
import numpy as np
from anatomy_eval.reference import maxsim_hamming
from anatomy_shared.binary import VECTOR_DIM, binarize


def test_maxsim_identical_tokens_score_full():
    """query token 與某 patch 完全相同 → 該 token 貢獻滿分 128。"""
    rng = np.random.default_rng(1)
    patch_vecs = [rng.standard_normal(VECTOR_DIM) for _ in range(4)]
    patches = [binarize(v) for v in patch_vecs]
    tokens = [patches[0], patches[2]]  # 直接取兩個 patch 當 token
    assert maxsim_hamming(tokens, patches) == 128.0 * 2


def test_maxsim_hand_computed_small_case():
    """2 token × 2 patch 手算對照。"""
    t1 = b"\x00" * 16                    # 與 p1 距離 0、與 p2 距離 4
    t2 = b"\xff" + b"\x00" * 15          # 與 p1 距離 8、與 p2 距離 12
    p1 = b"\x00" * 16
    p2 = b"\x00" * 15 + b"\x0f"
    # token1 max sim = 128-0；token2 max sim = 128-8
    assert maxsim_hamming([t1, t2], [p1, p2]) == (128.0 - 0) + (128.0 - 8)


def test_maxsim_orders_relevant_page_first():
    """query 取自 page A 的 patch → A 的分數必須高於無關的 page B。"""
    rng = np.random.default_rng(2)
    page_a = [binarize(rng.standard_normal(VECTOR_DIM)) for _ in range(8)]
    page_b = [binarize(rng.standard_normal(VECTOR_DIM)) for _ in range(8)]
    tokens = page_a[:3]
    assert maxsim_hamming(tokens, page_a) > maxsim_hamming(tokens, page_b)


def test_maxsim_rejects_non_16_byte_inputs():
    """128-distance 的假設只對 bit(128)=16 bytes 成立——其他長度必須報錯（Codex LOW-8）。"""
    import pytest

    with pytest.raises(ValueError):
        maxsim_hamming([b"\x00" * 8], [b"\x00" * 8])
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
uv run --no-sync pytest eval/tests/test_reference_maxsim.py -q
```

預期：FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 建立 `eval/src/anatomy_eval/reference.py`**

```python
"""檢索評分的純 Python 參考實作——Phase 5 SQL/應用層實作的測試 oracle。"""
from collections.abc import Sequence

from anatomy_shared.binary import hamming_distance


def maxsim_hamming(query_tokens_bin: Sequence[bytes], page_patches_bin: Sequence[bytes]) -> float:
    """§4.4 MaxSim：score(page) = Σ_t max_p (128 - hamming(t, p))。

    小規模 O(T×P) 直算，僅供測試/評估對照；線上路徑由 Stage B（SQL 或 numpy）負責。
    128-distance 的相似度轉換只對 bit(128) 成立，故強制 16-byte 輸入。
    """
    if not query_tokens_bin or not page_patches_bin:
        raise ValueError("query tokens 與 page patches 皆不可為空")
    for v in (*query_tokens_bin, *page_patches_bin):
        if len(v) != 16:
            raise ValueError("maxsim_hamming 僅支援 bit(128)＝16-byte 輸入")
    return float(sum(
        max(128 - hamming_distance(t, p) for p in page_patches_bin)
        for t in query_tokens_bin
    ))
```

- [ ] **Step 4: 跑測試確認全綠**

```bash
uv run --no-sync pytest eval/tests/test_reference_maxsim.py -q
```

- [ ] **Step 5: Commit**

```bash
git add eval/src/anatomy_eval/reference.py eval/tests/test_reference_maxsim.py
git commit -m "feat(phase-1): maxsim_hamming 參考實作（Phase 5 Stage B 的測試 oracle）"
```

---

### Task 8: recall harness（recall@K by class，D-P gate）

**Files:**
- Create: `eval/src/anatomy_eval/harness.py`
- Test: `eval/tests/test_recall_harness.py`

- [ ] **Step 1: 建立失敗測試 `eval/tests/test_recall_harness.py`（先寫單元部分）**

```python
"""recall@K harness 測試：單元 + 合成資料端到端（D-P gate 種子）。"""
import numpy as np
import pytest
from anatomy_eval.golden import GoldenQA
from anatomy_eval.harness import evaluate_recall_by_class, recall_at_k
from anatomy_eval.reference import maxsim_hamming
from anatomy_shared.binary import binarize
from anatomy_shared.colpali_runtime import MockColPaliRuntime


def test_recall_at_k_basic():
    assert recall_at_k(["a", "b", "c"], ["a"], k=1) == 1.0
    assert recall_at_k(["a", "b", "c"], ["c"], k=2) == 0.0
    assert recall_at_k(["a", "b", "c"], ["a", "z"], k=3) == 0.5


def test_recall_at_k_rejects_empty_expected():
    with pytest.raises(ValueError):
        recall_at_k(["a"], [], k=3)


def _mk(qid, cat, query, pages):
    return GoldenQA(id=qid, category=cat, query=query, expected_pages=tuple(pages))


def test_evaluate_skips_oos_and_groups_by_class():
    golden = [
        _mk("t1", "text_only", "q1", ["p1"]),
        _mk("f1", "figure_id", "q2", ["p9"]),
        GoldenQA(id="o1", category="out_of_scope", query="oos",
                 expected_response_type="教材中查無此項"),
    ]
    # 固定回傳 p1, p2, ...：t1 命中、f1 未命中
    report = evaluate_recall_by_class(golden, lambda qa: ["p1", "p2", "p3"], k=3)
    assert report["k"] == 3
    assert report["n_evaluated"] == 2 and report["n_skipped_oos"] == 1
    assert report["by_class"]["text_only"] == 1.0
    assert report["by_class"]["figure_id"] == 0.0
    assert report["overall"] == 0.5
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
uv run --no-sync pytest eval/tests/test_recall_harness.py -q
```

預期：FAIL（ModuleNotFoundError）。

- [ ] **Step 3: 建立 `eval/src/anatomy_eval/harness.py`**

```python
"""最小 recall@K by question-class harness（D-P / DL-013 上線 gate 的種子）。

retrieve_fn 為引擎中立介面：給 GoldenQA、回傳排序後 page_id 字串列表。
Phase 3/5 將以真實 encoder/檢索接上同一介面；out_of_scope 題不計 retrieval recall
（其正確性屬生成層 gate，§7.2/§7.3）。
"""
from collections.abc import Callable, Sequence

from anatomy_eval.golden import GoldenQA


def recall_at_k(retrieved: Sequence[str], expected: Sequence[str], k: int) -> float:
    """expected 中出現在 retrieved 前 k 名的比例。"""
    if not expected:
        raise ValueError("expected 不可為空（out_of_scope 題不計 recall）")
    top = set(list(retrieved)[:k])
    return sum(1 for p in expected if p in top) / len(expected)


def evaluate_recall_by_class(
    golden: Sequence[GoldenQA],
    retrieve_fn: Callable[[GoldenQA], Sequence[str]],
    k: int = 10,
) -> dict:
    """對黃金題庫逐題呼叫 retrieve_fn，回報 recall@K（依 category 分組 + overall）。"""
    per_class: dict[str, list[float]] = {}
    skipped = 0
    for qa in golden:
        if qa.category == "out_of_scope":
            skipped += 1
            continue
        score = recall_at_k(retrieve_fn(qa), qa.expected_pages, k)
        per_class.setdefault(qa.category, []).append(score)
    all_scores = [s for scores in per_class.values() for s in scores]
    return {
        "k": k,
        "n_evaluated": len(all_scores),
        "n_skipped_oos": skipped,
        "by_class": {c: sum(v) / len(v) for c, v in per_class.items()},
        "overall": (sum(all_scores) / len(all_scores)) if all_scores else 0.0,
    }
```

- [ ] **Step 4: 跑測試確認單元部分全綠**

```bash
uv run --no-sync pytest eval/tests/test_recall_harness.py -q
```

- [ ] **Step 5: 在 `eval/tests/test_recall_harness.py` 追加合成端到端「煙霧」測試**

> **定位（Codex HIGH-2 修訂）**：本測試驗證的是「harness 管線可運轉」（binarize → binary MaxSim →
> recall@K by class 的機械正確性），**不構成 DL-013 / §7.3 gate 的通過宣稱**——query 直接取自目標頁
> patch，繞過了真實 encoder/翻譯/Stage A。DL-013 的四變體實測（float 參考 / binary+INT8 / all-binary /
> BM25-only，對已校準語料）由 Phase 3/5 對真實管線執行。

```python
def test_synthetic_smoke_binary_maxsim_recall_pipeline():
    """合成語料煙霧測試：mock runtime 產頁面 patch → binarize → binary MaxSim 檢索 →
    recall@3 by class 應為 1.0（query 取自目標頁的 patch 子集 + 雜訊，自我檢索必中）。
    僅驗證 harness 管線可運轉；**非** DL-013 gate（見計畫 Task 8 定位說明）。
    """
    rt = MockColPaliRuntime()
    rng = np.random.default_rng(42)
    page_ids = [f"fake:{i}" for i in range(20)]
    corpus = {pid: [binarize(v) for v in rt.encode_page(pid).embeddings] for pid in page_ids}

    def query_tokens_for(pid: str) -> list[bytes]:
        vecs = rt.encode_page(pid).embeddings[:8]       # 取該頁 8 個 patch
        noisy = vecs + rng.normal(0, 0.05, vecs.shape)  # 小雜訊（不翻轉多數符號）
        return [binarize(v.astype("float32")) for v in noisy]

    def retrieve(qa: GoldenQA) -> list[str]:
        tokens = query_tokens_for(qa.expected_pages[0])
        scored = sorted(page_ids, key=lambda pid: -maxsim_hamming(tokens, corpus[pid]))
        return scored

    golden = [
        _mk("s1", "text_only", "q", ["fake:0"]),
        _mk("s2", "text_only", "q", ["fake:1"]),
        _mk("s3", "figure_id", "q", ["fake:2"]),
        _mk("s4", "cross_page", "q", ["fake:3"]),
        _mk("s5", "clinical_correlation", "q", ["fake:4"]),
    ]
    report = evaluate_recall_by_class(golden, retrieve, k=3)
    assert report["overall"] == 1.0
    assert set(report["by_class"]) == {"text_only", "figure_id", "cross_page", "clinical_correlation"}
    assert all(v == 1.0 for v in report["by_class"].values())
```

- [ ] **Step 6: 跑測試確認全綠**

```bash
uv run --no-sync pytest eval/tests -q
```

- [ ] **Step 7: Commit**

```bash
git add eval/src/anatomy_eval/harness.py eval/tests/test_recall_harness.py
git commit -m "feat(phase-1): recall@K by class harness + 合成煙霧測試（D-P 種子；DL-013 實測 gate 留 Phase 3/5）"
```

---

### Task 9: CI / Makefile 衛生

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `Makefile`

- [ ] **Step 1: ci.yml unit job——在 `- run: uv sync --package colpali-service --inexact` 之後加一行 eval 同步**

```yaml
      - run: uv sync --package anatomy-eval --inexact
```

- [ ] **Step 2: ci.yml unit job——把 pytest 那行（原 `- run: uv run --no-sync pytest backend/tests shared/tests colpali_service/tests -q`）改為**

```yaml
      - run: uv run --no-sync pytest backend/tests shared/tests colpali_service/tests eval/tests -q
```

- [ ] **Step 3: ci.yml unit job——在「確認無 LlamaIndex 殘留」step 之後追加重複定義斷言**

```yaml
      - name: 確認 binarize/pool/hamming 僅定義於 shared（§2.4 單一來源）
        run: "! grep -rInE --include='*.py' 'def (binarize|to_pg_bits|pool_patches|hamming_distance)\\(' backend ingest eval colpali_service || (echo '向量運算函式只能定義在 shared/binary.py' && exit 1)"
```

- [ ] **Step 3b: ci.yml unit job——把既有「確認 binary.py 不依賴 torch（D-L）」step 改為涵蓋兩個模組（各自 fresh process，Codex MEDIUM-6）**

```yaml
      - name: 確認 shared 模組 torch-free（D-L）
        run: |
          uv run --no-sync python -c "import anatomy_shared.binary, sys; assert 'torch' not in sys.modules; print('binary torch-free OK')"
          uv run --no-sync python -c "import anatomy_shared.colpali_runtime, sys; assert 'torch' not in sys.modules; print('colpali_runtime torch-free OK')"
```

- [ ] **Step 4: Makefile——把 `lint:` 與 `fmt:` 目標改為（修「uv run --group dev 會剪除 workspace 成員」的坑）**

```make
lint:
	uv sync --group dev --inexact
	uv run --no-sync ruff check .

fmt:
	uv sync --group dev --inexact
	uv run --no-sync ruff format .
```

- [ ] **Step 5: 本機模擬 CI unit job 全流程驗證**

```bash
uv sync --group dev
uv sync --package anatomy-backend --inexact
uv sync --package colpali-service --inexact
uv sync --package anatomy-eval --inexact
uv run --no-sync ruff check .
uv run --no-sync python -c "import anatomy_shared.binary, sys; assert 'torch' not in sys.modules; print('binary torch-free OK')"
uv run --no-sync python -c "import anatomy_shared.colpali_runtime, sys; assert 'torch' not in sys.modules; print('colpali_runtime torch-free OK')"
! grep -rInE --include='*.py' 'def (binarize|to_pg_bits|pool_patches|hamming_distance)\(' backend ingest eval colpali_service && echo "單一來源 OK"
uv run --no-sync pytest backend/tests shared/tests colpali_service/tests eval/tests -q
```

預期：`torch-free OK`、`單一來源 OK`、pytest 全綠。

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml Makefile
git commit -m "ci(phase-1): unit job 納入 eval tests + 向量函式單一來源斷言；修 make lint/fmt 剪成員坑"
```

---

### Task 10: 收尾驗證

- [ ] **Step 1: 全套測試 + lint**

```bash
make lint
uv run --no-sync pytest backend/tests shared/tests colpali_service/tests eval/tests -q
```

預期：ruff 乾淨、全部 PASS（預估 ~40+ 測試）。

- [ ] **Step 2: Docker smoke（encoder 重構後容器仍健康、契約不變）**

```bash
make up
sleep 20 && docker compose ps --format "table {{.Service}}\t{{.Status}}"
A=$(curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' -d '{"q":"肱二頭肌"}')
B=$(curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' -d '{"q":"肱二頭肌"}')
[ "$A" = "$B" ] && echo "$A" | python3 -c "import sys,json,base64; j=json.load(sys.stdin); assert len(base64.b64decode(j['pooled_f32']))==512; assert j['lang']=='zh'; print('容器契約 OK')"
```

預期：7 服務 healthy、`容器契約 OK`。

- [ ] **Step 3: 確認工作樹乾淨並回報**

```bash
git status --porcelain   # 應為空
git log --oneline main..HEAD
```

回報內容：commit 清單、測試數、D-P harness 報告範例輸出（跑 `uv run --no-sync python -c "..."` 不必，引用 Task 8 測試輸出即可）。

---

## 驗收標準（Phase 1 DoD，對照 roadmap）

1. `binarize` round-trip 一致 + 位序對照 §2.4（既有測試 + `to_pg_bits` 測試守護）。
2. `pool_patches` fp32 累加、排除 padding（valid_mask）、**輸出 float32**（halfvec 量化只在 DB 綁定層）、不 re-normalize（DL-019）。
3. `EncodedVectors` 穩定介面 + `MockColPaliRuntime` 形狀/mask/決定性正確；`binary.py` 與 `colpali_runtime.py` fresh import **不拉 torch**（subprocess 測試 + CI 斷言）。
4. colpali_service mock delegate 到 shared（契約欄位不變、既有 + 強化契約測試綠、容器 smoke 過；tokens/pooled 皆排除特殊 token、pooled 全程 fp32）。
5. harness 能對合成資料算 recall@K by class（D-P 種子可運轉；**明確標注非 DL-013 gate 通過**，四變體實測在 Phase 3/5）。
6. `tests/golden_qa.seed.jsonl` 五類各 ≥2 題、含中文 query、schema 驗證通過、**無 should_refuse**。
7. CI unit job 含 eval tests + 單一來源斷言；`make lint/fmt` 不再剪除 workspace 成員。

## 明確不做（YAGNI / 留待後續 Phase）

- 真實 ColPali torch runtime（Phase 3 **明確承接**：以 Phase 1 固定的 `EncodedVectors` 介面實作 `get_runtime(mock=False)`；GPU + transformers 5.10.2 相容性 + 本地 MT。roadmap 已同步註記）。
- `ingest_errors`、DB 寫入、任何 SQL（Phase 2）。
- RAGAS / Streamlit 抽檢工具（Phase 11；本 Phase 只把依賴移 extras，**不新增套件**）。
- seed 題庫頁碼的真實性校正（Phase 11 教師標註）。
