# Phase 4 — 離線建庫管線（ingest/）實作計畫（savepoint 交易語意 + 雲端 LLM 零呼叫守門 + GPU gate）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `ingest/` 從空骨架升級為可離線建庫的 CLI：PDF + 書籍 YAML → Docling 逐頁 Markdown + 規範化 metadata、pdf2image PNG（200 DPI / 長邊 ≤2048）、ColPali `encode_pages` → `shared.binary` 二值化/池化 → 每頁 PNG 上 MinIO、patch_bin/pooled(halfvec) 帶 `kb_version` 寫 Postgres；**每批一交易、每頁 savepoint**（部分成功、`--resume` 續跑）；**MUST NOT 呼叫任何雲端 LLM API**（CI 守門）。

**Architecture:** 管線拆成「來源 → 編碼 → 儲存 → 寫入」四段，段與段以純資料類別（`types.py`）銜接。**來源**有兩個 producer 共用同一下游：`pdf_source`（真實，需 docling+poppler+GPU，走 host gate）與 `synthetic_source`（dev/CI，捏造 N 頁 PIL 影像 + 罐頭 Markdown，無 poppler/GPU）——兩者都產出 `PageParse + PIL.Image`，之後的 encode/binarize/upload/write **完全相同**。torch 隔離不變（D-L）：`colpali_encoder.py` 只透過 `shared.colpali_runtime.get_runtime(mock=...)` 取得 runtime，CI 用 mock（決定性、torch-free）。寫入端交易語意：**編碼/上傳全在 DB 交易外完成**，每批只用一個短交易（逐頁 `SAVEPOINT`→`INSERT pages`+`executemany page_patches`，成功 `RELEASE`、失敗 `ROLLBACK TO SAVEPOINT`+寫 `ingest_errors`，批末 `COMMIT`），批為提交邊界故 `--resume` 可從最後已提交頁續跑；連線走 PgBouncer `:6432`（交易短、不跨編碼，符合 CLAUDE.md）。

**Tech Stack:** docling 2.98 / docling-core 2.79（`DocumentConverter` + 逐頁 `export_to_markdown(page_no=)`）、pdf2image 1.17 + poppler-utils（**新系統套件**，host 與 ingest 容器需裝）、boto3（MinIO/S3，`put_object`）、asyncpg（連 `:6432`、`statement_cache_size=0`）、pyyaml、pillow、`anatomy_shared.binary`（binarize/pool_patches/to_pg_bits，既有單一來源）、`anatomy_shared.colpali_runtime`（mock/real runtime，既有）。CI mock 路徑 torch-free + poppler-free。

---

## 0. 設計定案與研究結論（本計畫內生效；Task 1 寫入治理文件）

### 0.1 ⚠️ 需使用者核准 / 知會的外部代價項（依 2026-06-08/10/11 授權範圍）

| 項目 | 內容 | 分類 | 處置 |
|---|---|---|---|
| 新系統套件 | `poppler-utils`（host GPU venv + ingest 執行環境）；pdf2image 的強制 runtime 後端（`pdftoppm`） | 「想安裝新套件」 | pdf2image 已於 Phase 0 列入 ingest 依賴並獲核准；poppler 是其**必備 runtime 伴隨物**、非新功能依賴。**本計畫視為既有核准 pdf2image 的延伸**，但因屬系統層安裝，Task 1 在 SETUP.md 明列、回報中知會使用者；不另設阻斷。 |
| 既有付費資源 | 無。離線管線**禁呼叫雲端 LLM**（OpenAI 等）——本計畫反而新增 CI 守門強制此紅線。 | — | 無新增成本。 |
| 既有硬體 | GPU（RTX 5060 Ti，Phase 3 已 de-risk cu128/sm_120）。真實頁面編碼走 host GPU venv（模型已快取 `~/.cache/huggingface`）。 | — | 沿用 Phase 3 既有，不新增。 |
| 既有 SaaS/連線 | MinIO（compose 內，Phase 0 既有）、Postgres（Phase 2 既有）。無新對外連線。 | — | 無。 |
| Python 依賴 | ingest 既有 manifest 已含 `docling/pdf2image/asyncpg/boto3/pyyaml/pillow` + `anatomy-shared[colpali]`。**本計畫不新增任何 Python 依賴**（dev 測試用既有 pytest/asgi 生態；`moto` 不採——改用最小 fake S3 client，見 Task 7）。 | — | 無新 Python 依賴。 |

> **結論**：本計畫唯一外部代價 = 安裝系統套件 `poppler-utils`（pdf2image 既核依賴的 runtime）。其餘全為既有資源沿用。實作可逕行；Task 1 治理附註與回報中知會 poppler 安裝即可。

### 0.2 交易語意決策（DL-023，Task 1 寫入 `decisions.md`，狀態 APPROVED（委派））

roadmap Phase 4 明訂「每頁 savepoint：成功 release、失敗 rollback 至 savepoint 並寫 `ingest_errors`，整書非單一 all-or-nothing」。spec §2.5 SHOULD「整本書放在單一 transaction」與 `--resume`（§2.7 MUST「從失敗頁繼續」）在「單一書交易」下相互矛盾（單交易崩潰即全 rollback，resume 無從續起）。**定案**：

- **提交邊界 = 批（`--batch-size`）**，非整書。每批一個交易。
- **編碼與 S3 上傳全在 DB 交易外**完成（GPU/網路慢操作不持 DB 連線）；交易只含該批的 `INSERT`，短而快 → PgBouncer transaction pooling 下只短暫佔用一個 server 連線。
- 交易內**逐頁 `SAVEPOINT`**：成功 `RELEASE SAVEPOINT`；失敗 `ROLLBACK TO SAVEPOINT`（不中止交易）後在**同一交易內** `INSERT ingest_errors`（stage='write'），批末 `COMMIT` → 錯誤紀錄與成功頁一起持久化。
- **批層級致命錯誤**（連線斷、ensure_partition 失敗）：該批整批 rollback，未提交；`--resume` 下次重跑該批。
- **`--resume`**：開跑前查 `SELECT page_num FROM pages WHERE book_id=$1 AND kb_version=$2` 得已完成集合，跳過之；另查 `ingest_errors WHERE resolved=false` 僅供報告（重跑該頁成功後該頁進 pages，報告層面視為已解，不強制改 `resolved` 旗標——避免額外寫）。
- **連線埠**：ingest 連 `DATABASE_URL`（PgBouncer `:6432`、`statement_cache_size=0`），**不**用 `PG_DIRECT_URL`（後者僅 Alembic）。理由：交易短、不跨編碼；`ensure_kb_partition` 的 `CREATE TABLE … PARTITION OF` DDL 在 transaction pooling 下可執行（Phase 2 已驗）。

### 0.3 測試分層（沿用 Phase 2/3 慣例）

| 層 | 跑在哪 | runtime | 來源 | DB | 涵蓋 |
|---|---|---|---|---|---|
| 單元（CI unit job） | CI / 本機 | mock（torch-free） | `synthetic_source` | 無（純函式） | classify / docling 抽取 / render resize / encoder 二值化 / storage(fake S3) / cli 參數 / **雲端 LLM 零呼叫 socket guard** |
| db 整合（CI db-integration job，`-m db`） | CI / compose | mock | `synthetic_source` | 真 Postgres（`:6432`） | writer savepoint 語意 / ingest_errors / resume / 5% 抽樣校驗 / 端到端 mock 管線（含 fake S3） |
| GPU gate（手動，非 CI） | host GPU venv | **real** ColPali | 真 1 頁 PDF（docling+poppler） | 真 Postgres + 真 MinIO | 真模型 `encode_pages`、二值化與 query 端一致、PNG 真上 MinIO、metadata 規範化 |

### 0.4 來源段 producer 介面（two-source seam）

```
produce_pages(...) -> Iterator[SourcePage]      # SourcePage = (parse: PageParse, image: PIL.Image)
  ├─ pdf_source(pdf_path, book_meta)            # 真實：DocumentConverter + pdf2image（host gate）
  └─ synthetic_source(n_pages, book_meta)       # dev/CI：捏造 PIL 影像 + 罐頭 markdown（無 poppler）
下游統一：encode_page_image → upload_page_png → write batch
```

### 0.5 研究確認（venv 實證，2026-06-13）

- docling-core 2.79：`DoclingDocument.export_to_markdown(page_no=N)` 可逐頁輸出；`iterate_items()` 給 `(item, level)`、`item.prov[].page_no` 可定位頁；`add_page/add_text/add_heading` 可程式化構造文件（單元測試用，免真 PDF）。
- pdf2image：`convert_from_path(pdf, dpi=200)` 回 `list[PIL.Image]`（1-indexed 對應頁）；**需 poppler**（host 未裝，CI 不跑此路徑）。
- 真實 `RealColPaliRuntime.encode_pages(images, batch_size)`（`shared/colpali_real.py`）與 `MockColPaliRuntime.encode_pages` 同介面 → ingest 兩模式共用。
- pooled halfvec 綁定：asyncpg 送 `'[f1,f2,…]'` 字串 + `$N::halfvec`（Phase 2 `test_schema_db.py` 既有寫法）；patch_bin 綁定 `to_pg_bits(bytes)` + `$N::text::bit(128)`（binary.py docstring）。
- `ensure_kb_partition(conn, v)`（`backend.db.kb_version`）已存在——但 ingest 不依賴 anatomy-backend。**本計畫在 ingest 內自帶等價 `ensure_kb_partition`**（同一份 SQL，避免跨包依賴；CI 單一來源守門不涵蓋此 DDL helper）。

---

## 檔案結構

```
ingest/
├── pyproject.toml                       # 既有（不改依賴）
├── src/anatomy_ingest/
│   ├── __init__.py                      # 既有（空）
│   ├── types.py                         # Create: PageParse / SourcePage / EncodedPage / WriteOutcome dataclasses
│   ├── config.py                        # Create: 讀 env（DB url / S3 creds），無新依賴（os.environ）
│   ├── classify.py                      # Create: chapter 抽取 / anatomy_system 對照 / page_type 啟發 / figures 抽取（純）
│   ├── docling_parser.py                # Create: convert_pdf(thin) + extract_pages(doc, meta)（用 classify）
│   ├── page_render.py                   # Create: render_pdf_pages(thin) + resize_long_edge（純）
│   ├── source.py                        # Create: pdf_source / synthetic_source / produce_pages
│   ├── colpali_encoder.py               # Create: encode_page_image(runtime, image) -> EncodedPage
│   ├── storage.py                       # Create: page_key / upload_page_png（boto3）
│   ├── writer.py                        # Create: ensure_kb_partition / write_batch（savepoint）/ completed_page_nums / sample_verify
│   └── cli.py                           # Create: argparse + 編排 + resume + 5% 抽樣 + no-cloud guard
├── scripts/
│   └── ingest_gate.py                   # Create: 手動 GPU 端到端 gate（真 PDF + real runtime + 真 MinIO/PG）
└── tests/
    ├── conftest.py                      # Create: db 守門（沿用 backend 慣例）+ fixtures
    ├── test_classify.py                 # Create
    ├── test_docling_parser.py           # Create
    ├── test_page_render.py              # Create
    ├── test_source.py                   # Create
    ├── test_colpali_encoder.py          # Create
    ├── test_storage.py                  # Create
    ├── test_writer_db.py                # Create（-m db）
    ├── test_cli.py                      # Create
    └── test_no_cloud_llm.py             # Create（socket guard）
```

其他改動：
- `Makefile`：修 `ingest-sample`（改 host `--synthetic` mock smoke，不再誤跑 backend 容器）+ 新增 `ingest-gate`（GPU）。
- `.github/workflows/ci.yml`：unit job 加「ingest 無雲端 LLM import」grep 守門 + 把 `ingest/tests` 納入 pytest；db-integration job 把 `ingest/tests -m db` 納入。
- `SETUP.md`：新增 ingest 章節（poppler 安裝、`make ingest-sample`、`make ingest-gate`）。
- `backend/Dockerfile`：註解澄清 ingest **不**在 backend 容器執行（移除誤導）。

---

## Task 1: 治理文件（DL-023 + spec 附註 + SETUP 骨架）

**Files:**
- Modify: `docs/decisions.md`（新增 DL-023）
- Modify: `docs/ARCHITECTURE.md:330`（§2.6 附註交易語意）
- Modify: `SETUP.md`（新增 §F ingest 章節骨架）

- [ ] **Step 1: 在 `docs/decisions.md` 末尾新增 DL-023**

```markdown
## DL-023: 離線建庫交易語意——批為提交邊界 + 每頁 savepoint；連 PgBouncer :6432

- **狀態**：APPROVED（委派）　**提案者**：main Claude　**日期**：2026-06-13　**影響檔案**：ARCHITECTURE.md §2.5、§2.6、§2.7

**背景**：spec §2.5 SHOULD「整本書放在單一 transaction」與 §2.7 MUST `--resume`「從失敗頁繼續」在單一書交易下矛盾（單交易崩潰即整書 rollback，resume 無從續起）。roadmap Phase 4 已定「每頁 savepoint、整書非 all-or-nothing」。

**決策**：
1. 提交邊界 = 批（`--batch-size`），非整書。每批一交易。
2. 編碼（GPU）與 S3 上傳在 DB 交易**外**完成；交易只含該批 INSERT，短而快。
3. 交易內逐頁 `SAVEPOINT`：成功 `RELEASE`、失敗 `ROLLBACK TO SAVEPOINT` + 同交易內寫 `ingest_errors`（stage='write'），批末 `COMMIT`。
4. `--resume`：開跑前查 `pages` 已完成 page_num 集合並跳過；批崩潰未提交者下次重跑。
5. 連線走 `DATABASE_URL`（PgBouncer :6432、`statement_cache_size=0`），非 `PG_DIRECT_URL`（交易短、不跨編碼；DDL 分區建立在 transaction pooling 下可執行）。

**取代**：§2.5「整本書單一 transaction」SHOULD 在本專案降為「批單一 transaction」；不影響「patch 批次插入」「失敗 rollback」其餘 SHOULD/MUST。
```

- [ ] **Step 2: 在 `docs/ARCHITECTURE.md` §2.6 末尾（`--resume` 段附近）加交易語意附註**

於 §2.6「重新執行」段後插入：

```markdown
> **交易語意（DL-023）**：提交邊界為**批**（`--batch-size`）非整書。每批：先在交易外完成編碼與 PNG 上傳，再開一個短交易逐頁 `SAVEPOINT`（成功 `RELEASE`、失敗 `ROLLBACK TO SAVEPOINT` 並於同交易寫 `ingest_errors`），批末 `COMMIT`。`--resume` 依 `pages` 已存在的 `(book_id, page_num, kb_version)` 跳過已完成頁。連線走 PgBouncer `:6432`。
```

- [ ] **Step 3: 在 `SETUP.md` 新增 §F「離線建庫（ingest）」骨架**（內容後續 Task 補細節，先佔位）

```markdown
## F. 離線建庫管線（ingest）

> 離線批次：PDF + 書籍 YAML → Docling/PNG/ColPali → Postgres + MinIO。**禁呼叫雲端 LLM。**

### F.1 系統前置（poppler）

pdf2image 需 poppler 後端（`pdftoppm`）。host GPU venv 與任何真實建庫環境須安裝：

```bash
sudo apt-get update && sudo apt-get install -y poppler-utils
pdftoppm -v   # 驗證
```

### F.2 mock smoke（無 GPU / 無 poppler / 無真 PDF）

```bash
make ingest-sample   # synthetic 來源 + mock runtime + 真 DB/MinIO，驗端到端寫入
```

### F.3 真實建庫 / GPU gate

```bash
make ingest-gate     # 真 1 頁 PDF + real ColPali + 真 MinIO/PG，端到端驗收
# 正式建庫：
uv run --no-sync python -m anatomy_ingest.cli \
    --pdf /data/books/gray_42e.pdf --book-meta /data/books/gray_42e.yaml \
    --kb-version 1 --batch-size 8
```
```

- [ ] **Step 4: Commit**

```bash
git add docs/decisions.md docs/ARCHITECTURE.md SETUP.md
git commit -m "docs(phase-4): DL-023 ingest 交易語意 + §2.6 附註 + SETUP §F 骨架"
```

---

## Task 2: `types.py` — 段間資料類別

**Files:**
- Create: `ingest/src/anatomy_ingest/types.py`
- Test: `ingest/tests/test_types.py`（極簡，僅驗建構/不可變）

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_types.py
import numpy as np
import pytest
from anatomy_ingest.types import PageParse, EncodedPage


def test_pageparse_holds_fields():
    p = PageParse(page_num=3, markdown="## Heart", metadata={"page_type": "mixed"})
    assert p.page_num == 3 and p.markdown.startswith("##") and p.metadata["page_type"] == "mixed"


def test_encodedpage_patch_bins_and_pooled():
    e = EncodedPage(page_num=1, patch_bins=[b"\x00" * 16, b"\xff" * 16],
                    pooled_f32=np.zeros(128, dtype=np.float32), embed_model="mock-colpali")
    assert e.n_patches == 2 and e.pooled_f32.shape == (128,)


def test_encodedpage_rejects_wrong_bin_length():
    with pytest.raises(ValueError):
        EncodedPage(page_num=1, patch_bins=[b"\x00" * 8],
                    pooled_f32=np.zeros(128, dtype=np.float32), embed_model="m").validate()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_types.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.types'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/types.py
"""ingest 段間資料類別：來源 → 編碼 → 寫入。純資料、無 torch/DB 依賴。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

PATCH_BIN_BYTES = 16  # bit(128) = 16 bytes


@dataclass(frozen=True)
class PageParse:
    """來源段輸出（每頁）：Docling Markdown + 規範化 metadata。"""

    page_num: int
    markdown: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SourcePage:
    """來源 producer 的單頁產物：解析結果 + 待編碼/上傳的頁面影像。

    image 可為 None：解析出該頁但渲染缺圖時（pdf_source 不靜默丟棄，由 cli 記 render 失敗）。
    """

    parse: PageParse
    image: Any  # PIL.Image.Image | None（避免在 types 引 pillow 型別）


@dataclass(frozen=True)
class EncodedPage:
    """編碼段輸出（每頁）：patch 二值化串列 + pooled float32（halfvec 來源）。"""

    page_num: int
    patch_bins: list[bytes]
    pooled_f32: np.ndarray
    embed_model: str

    @property
    def n_patches(self) -> int:
        return len(self.patch_bins)

    def validate(self) -> "EncodedPage":
        if not self.patch_bins:
            raise ValueError(f"page {self.page_num}: 無任何 patch")
        for i, b in enumerate(self.patch_bins):
            if len(b) != PATCH_BIN_BYTES:
                raise ValueError(
                    f"page {self.page_num} patch {i}: 期望 {PATCH_BIN_BYTES} bytes，收到 {len(b)}"
                )
        arr = np.asarray(self.pooled_f32)
        if arr.shape != (128,):
            raise ValueError(f"page {self.page_num}: pooled 形狀須 (128,)，收到 {arr.shape}")
        return self


@dataclass(frozen=True)
class WriteOutcome:
    """單批寫入結果摘要（cli 報告用）。"""

    written: list[int] = field(default_factory=list)   # 成功寫入的 page_num
    failed: list[int] = field(default_factory=list)    # 寫入失敗（已記 ingest_errors）的 page_num
    skipped: list[int] = field(default_factory=list)   # resume 跳過的 page_num
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_types.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/types.py ingest/tests/test_types.py
git commit -m "feat(ingest): 段間資料類別 types.py（PageParse/EncodedPage/WriteOutcome）"
```

---

## Task 3: `classify.py` — chapter / anatomy_system / page_type / figures（純函式）

**Files:**
- Create: `ingest/src/anatomy_ingest/classify.py`
- Test: `ingest/tests/test_classify.py`

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_classify.py
import pytest
from anatomy_ingest.classify import (
    ANATOMY_SYSTEMS, PAGE_TYPES, map_anatomy_system, classify_page_type, extract_figures,
)


def test_map_anatomy_system_keyword_hits():
    assert map_anatomy_system("Upper Limb") == "musculoskeletal"
    assert map_anatomy_system("The Heart and Great Vessels") == "cardiovascular"
    assert map_anatomy_system("Cranial Nerves") == "nervous"


def test_map_anatomy_system_override_then_default_other():
    assert map_anatomy_system("Foobar", overrides={"foobar": "respiratory"}) == "respiratory"
    assert map_anatomy_system("Totally Unknown Chapter") == "other"


def test_map_anatomy_system_result_in_enum():
    assert map_anatomy_system("Upper Limb") in ANATOMY_SYSTEMS


def test_classify_page_type():
    assert classify_page_type(n_pictures=0, n_tables=0, text_len=1200) == "pure_text"
    assert classify_page_type(n_pictures=3, n_tables=0, text_len=80) == "figure_heavy"
    assert classify_page_type(n_pictures=0, n_tables=2, text_len=120) == "table"
    assert classify_page_type(n_pictures=2, n_tables=0, text_len=900) == "mixed"
    assert classify_page_type(n_pictures=0, n_tables=0, text_len=0) in PAGE_TYPES


def test_extract_figures():
    md = "See Fig. 7-23 and Figure 8.4. Also fig 9-1 lowercase. Table 2 not a figure."
    figs = extract_figures(md)
    assert "Fig. 7-23" in figs and "Figure 8.4" in figs and "Fig. 9-1" in figs
    assert len(figs) == len(set(figs))  # 去重
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_classify.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.classify'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/classify.py
"""頁面分類純函式：chapter→anatomy_system 對照、page_type 啟發、figures 抽取。

全為純函式（無 IO），供 docling_parser 組 metadata 用，便於單元測試。
枚舉值對齊 ARCHITECTURE.md §3.2 metadata 規範。
"""
from __future__ import annotations

import re

ANATOMY_SYSTEMS = (
    "musculoskeletal", "cardiovascular", "nervous", "respiratory", "digestive",
    "urogenital", "endocrine", "integumentary", "lymphatic", "special_senses", "other",
)
PAGE_TYPES = ("pure_text", "figure_heavy", "table", "mixed")

# 章節關鍵字 → anatomy_system（小寫子字串比對；長詞優先）。book-meta 可用 overrides 補充/覆寫。
_SYSTEM_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("upper limb", "musculoskeletal"), ("lower limb", "musculoskeletal"),
    ("muscle", "musculoskeletal"), ("bone", "musculoskeletal"), ("joint", "musculoskeletal"),
    ("skeleton", "musculoskeletal"), ("back", "musculoskeletal"),
    ("heart", "cardiovascular"), ("vessel", "cardiovascular"), ("artery", "cardiovascular"),
    ("vein", "cardiovascular"), ("cardiovascular", "cardiovascular"), ("vascular", "cardiovascular"),
    ("nerve", "nervous"), ("brain", "nervous"), ("spinal cord", "nervous"),
    ("nervous", "nervous"), ("cranial", "nervous"), ("neuro", "nervous"),
    ("lung", "respiratory"), ("respiratory", "respiratory"), ("trachea", "respiratory"),
    ("bronch", "respiratory"), ("pleura", "respiratory"),
    ("stomach", "digestive"), ("intestine", "digestive"), ("liver", "digestive"),
    ("digestive", "digestive"), ("gastro", "digestive"), ("abdomen", "digestive"),
    ("kidney", "urogenital"), ("bladder", "urogenital"), ("urogenital", "urogenital"),
    ("renal", "urogenital"), ("reproductive", "urogenital"), ("pelvis", "urogenital"),
    ("thyroid", "endocrine"), ("pituitary", "endocrine"), ("endocrine", "endocrine"),
    ("adrenal", "endocrine"),
    ("skin", "integumentary"), ("integument", "integumentary"),
    ("lymph", "lymphatic"), ("spleen", "lymphatic"), ("immune", "lymphatic"),
    ("eye", "special_senses"), ("ear", "special_senses"), ("special sense", "special_senses"),
    ("orbit", "special_senses"),
)

# 長關鍵字優先比對（"spinal cord" 早於 "cord"），降低短詞誤命中
_SYSTEM_KEYWORDS_SORTED = tuple(sorted(_SYSTEM_KEYWORDS, key=lambda kv: -len(kv[0])))

_FIGURE_RE = re.compile(r"\b(fig(?:ure)?\.?\s*\d+[-.]?\d*)", re.IGNORECASE)


def map_anatomy_system(chapter: str | None, overrides: dict[str, str] | None = None) -> str:
    """章節名稱 → anatomy_system 枚舉值；無命中回 'other'。overrides 鍵為小寫章節全名。"""
    if not chapter:
        return "other"
    key = chapter.strip().lower()
    if overrides and key in overrides and overrides[key] in ANATOMY_SYSTEMS:
        return overrides[key]
    for kw, system in _SYSTEM_KEYWORDS_SORTED:
        if kw in key:
            return system
    return "other"


def classify_page_type(n_pictures: int, n_tables: int, text_len: int) -> str:
    """啟發式頁型分類（§3.2 枚舉）。閾值刻意保守，Phase 11 真實教材再校。"""
    if n_tables >= 2 and text_len < 400:
        return "table"
    if n_pictures >= 2 and text_len < 300:
        return "figure_heavy"
    if n_pictures == 0 and n_tables == 0:
        return "pure_text"
    return "mixed"


def extract_figures(markdown: str) -> list[str]:
    """從 Markdown 抽圖說標籤（'Fig. 7-23'/'Figure 8.4'/'fig 9-1'）；正規化大小寫、去重保序。"""
    seen: dict[str, None] = {}
    for m in _FIGURE_RE.finditer(markdown):
        raw = re.sub(r"\s+", " ", m.group(1).strip())
        # 正規化前綴：fig→Fig.、figure→Figure
        body = re.sub(r"^fig(ure)?\.?\s*", "", raw, flags=re.IGNORECASE)
        prefix = "Figure" if re.match(r"^fig(ure)\b", raw, re.IGNORECASE) else "Fig."
        norm = f"{prefix} {body}"
        seen.setdefault(norm, None)
    return list(seen.keys())
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_classify.py -q`
Expected: PASS（6 passed）

> 若 `test_extract_figures` 因正規化前綴判斷失準，調整 `prefix` 判斷邏輯使 `Fig. 9-1`（來源 `fig 9-1`）與 `Figure 8.4` 都正確；測試為權威。

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/classify.py ingest/tests/test_classify.py
git commit -m "feat(ingest): classify.py（anatomy_system 對照 / page_type 啟發 / figures 抽取）"
```

---

## Task 4: `docling_parser.py` — convert_pdf（thin）+ extract_pages（純）

**Files:**
- Create: `ingest/src/anatomy_ingest/docling_parser.py`
- Test: `ingest/tests/test_docling_parser.py`（用程式化構造的 `DoclingDocument`，免真 PDF）

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_docling_parser.py
from docling_core.types.doc import (
    DoclingDocument, ProvenanceItem, BoundingBox, DocItemLabel, Size,
)
from anatomy_ingest.docling_parser import extract_pages
from anatomy_ingest.types import PageParse


def _two_page_doc():
    doc = DoclingDocument(name="t")
    doc.add_page(page_no=1, size=Size(width=612, height=792))
    doc.add_page(page_no=2, size=Size(width=612, height=792))
    p1 = ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=100, b=10), charspan=(0, 10))
    doc.add_heading(text="Upper Limb", prov=p1)
    doc.add_text(label=DocItemLabel.TEXT, text="The brachial plexus. See Fig. 7-23.",
                 prov=ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=20, r=100, b=30), charspan=(0, 10)))
    doc.add_text(label=DocItemLabel.TEXT, text="Continued text on page two.",
                 prov=ProvenanceItem(page_no=2, bbox=BoundingBox(l=0, t=0, r=100, b=10), charspan=(0, 10)))
    return doc


def test_extract_pages_count_and_markdown():
    pages = extract_pages(_two_page_doc(), book_meta={"book_title": "Gray", "edition": 42})
    assert len(pages) == 2
    assert all(isinstance(p, PageParse) for p in pages)
    assert pages[0].page_num == 1 and "Upper Limb" in pages[0].markdown
    assert pages[1].page_num == 2 and "page two" in pages[1].markdown


def test_extract_pages_metadata_normalized():
    pages = extract_pages(_two_page_doc(), book_meta={"book_title": "Gray", "edition": 42})
    m = pages[0].metadata
    assert m["book_title"] == "Gray" and m["edition"] == 42 and m["page_num"] == 1
    assert m["chapter"] == "Upper Limb"
    assert m["anatomy_system"] == "musculoskeletal"
    assert m["page_type"] in ("pure_text", "figure_heavy", "table", "mixed")
    assert m["figures"] == ["Fig. 7-23"]


def test_extract_pages_chapter_carries_forward():
    # 第二頁無新標題 → 沿用第一頁章節
    pages = extract_pages(_two_page_doc(), book_meta={"book_title": "Gray", "edition": 42})
    assert pages[1].metadata["chapter"] == "Upper Limb"
    assert pages[1].metadata["anatomy_system"] == "musculoskeletal"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_docling_parser.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.docling_parser'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/docling_parser.py
"""Docling 解析：convert_pdf（thin，真實 PDF→DoclingDocument）+ extract_pages（純抽取）。

convert_pdf 包裝 DocumentConverter（重、需 docling 模型，不進 CI）；extract_pages 對
已建好的 DoclingDocument 逐頁抽 Markdown + 規範化 metadata（純邏輯，單元測試直測）。
MUST NOT 呼叫任何雲端 LLM（離線管線紅線）。
"""
from __future__ import annotations

from typing import Any

from docling_core.types.doc import DocItemLabel

from .classify import classify_page_type, extract_figures, map_anatomy_system
from .types import PageParse


def convert_pdf(pdf_path: str):
    """真實路徑：PDF → DoclingDocument。需 docling 模型（首次下載），不在 CI 執行。"""
    from docling.document_converter import DocumentConverter  # lazy：重依賴

    return DocumentConverter().convert(pdf_path).document


def _page_numbers(doc) -> list[int]:
    pages = getattr(doc, "pages", None)
    if pages:
        return sorted(int(n) for n in pages.keys())
    # 後備：從 items 的 prov 收集
    nums = {prov.page_no for item, _ in doc.iterate_items() for prov in getattr(item, "prov", [])}
    return sorted(nums)


def _page_stats(doc, page_no: int) -> tuple[str | None, int, int]:
    """回 (該頁第一個 section_header 文字, 圖片數, 表格數)。"""
    chapter = None
    n_pictures = n_tables = 0
    for item, _level in doc.iterate_items():
        provs = getattr(item, "prov", [])
        if not any(p.page_no == page_no for p in provs):
            continue
        label = getattr(item, "label", None)
        if label == DocItemLabel.SECTION_HEADER and chapter is None:
            chapter = getattr(item, "text", None)
        elif label == DocItemLabel.PICTURE:
            n_pictures += 1
        elif label == DocItemLabel.TABLE:
            n_tables += 1
    return chapter, n_pictures, n_tables


def extract_pages(doc, book_meta: dict[str, Any]) -> list[PageParse]:
    """DoclingDocument → 每頁 PageParse（Markdown + 規範化 metadata，§3.2）。

    chapter 沿用：某頁無新 section_header 時，沿用前一頁章節（教科書段落跨頁常態）。
    overrides：book_meta['system_map']（小寫章節全名 → anatomy_system）。
    """
    overrides = book_meta.get("system_map")
    out: list[PageParse] = []
    last_chapter: str | None = None
    for page_no in _page_numbers(doc):
        markdown = doc.export_to_markdown(page_no=page_no)
        chapter, n_pictures, n_tables = _page_stats(doc, page_no)
        if chapter:
            last_chapter = chapter
        else:
            chapter = last_chapter
        figures = extract_figures(markdown)
        metadata = {
            "book_title": book_meta.get("book_title"),
            "edition": book_meta.get("edition"),
            "page_num": page_no,
            "chapter": chapter,
            "anatomy_system": map_anatomy_system(chapter, overrides),
            "page_type": classify_page_type(n_pictures, n_tables, len(markdown)),
            "figures": figures,
        }
        out.append(PageParse(page_num=page_no, markdown=markdown, metadata=metadata))
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_docling_parser.py -q`
Expected: PASS（3 passed）

> 若 docling-core 的 `add_heading` 產生的 label 非 `SECTION_HEADER`（版本差異），依實測調整 `_page_stats` 的 label 比對（venv 已驗 2.79 為 `SECTION_HEADER`）。測試為權威。

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/docling_parser.py ingest/tests/test_docling_parser.py
git commit -m "feat(ingest): docling_parser（convert_pdf thin + extract_pages 規範化 metadata）"
```

---

## Task 5: `page_render.py` — render（thin）+ resize_long_edge（純）

**Files:**
- Create: `ingest/src/anatomy_ingest/page_render.py`
- Test: `ingest/tests/test_page_render.py`（純 PIL，免 poppler）

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_page_render.py
from PIL import Image
from anatomy_ingest.page_render import resize_long_edge, MAX_LONG_EDGE


def test_resize_downscales_long_edge():
    img = Image.new("RGB", (4000, 2000), "white")
    out = resize_long_edge(img)
    assert max(out.size) == MAX_LONG_EDGE
    assert out.size == (MAX_LONG_EDGE, MAX_LONG_EDGE // 2)  # 維持長寬比


def test_resize_noop_when_small():
    img = Image.new("RGB", (1000, 800), "white")
    out = resize_long_edge(img)
    assert out.size == (1000, 800)


def test_resize_portrait():
    img = Image.new("RGB", (1500, 3000), "white")
    out = resize_long_edge(img)
    assert max(out.size) == MAX_LONG_EDGE and out.size == (MAX_LONG_EDGE // 2, MAX_LONG_EDGE)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_page_render.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.page_render'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/page_render.py
"""頁面渲染：render_pdf_pages（thin，pdf2image，需 poppler）+ resize_long_edge（純）。

§2.1：PNG 200 DPI、長邊上限 2048 px。render 在 CI 不跑（無 poppler）；resize 純邏輯可測。
"""
from __future__ import annotations

from PIL import Image

RENDER_DPI = 200
MAX_LONG_EDGE = 2048


def resize_long_edge(img: Image.Image, max_long_edge: int = MAX_LONG_EDGE) -> Image.Image:
    """等比縮放使長邊 ≤ max_long_edge；已在範圍內則原樣回傳。"""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge:
        return img
    scale = max_long_edge / long_edge
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def render_pdf_pages(pdf_path: str, dpi: int = RENDER_DPI) -> dict[int, Image.Image]:
    """真實路徑：PDF → {page_num(1-indexed): PIL.Image}（已 resize 長邊）。需 poppler。"""
    from pdf2image import convert_from_path  # lazy：需 poppler runtime

    images = convert_from_path(pdf_path, dpi=dpi)
    return {i: resize_long_edge(img.convert("RGB")) for i, img in enumerate(images, start=1)}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_page_render.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/page_render.py ingest/tests/test_page_render.py
git commit -m "feat(ingest): page_render（resize_long_edge 純邏輯 + render_pdf_pages thin）"
```

---

## Task 6: `colpali_encoder.py` — 頁面影像 → EncodedPage（二值化/池化）

**Files:**
- Create: `ingest/src/anatomy_ingest/colpali_encoder.py`
- Test: `ingest/tests/test_colpali_encoder.py`（mock runtime，決定性）

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_colpali_encoder.py
import numpy as np
from anatomy_shared.binary import binarize, pool_patches
from anatomy_shared.colpali_runtime import get_runtime
from anatomy_ingest.colpali_encoder import encode_page_image
from anatomy_ingest.types import EncodedPage


def test_encode_page_uses_shared_binarize_and_pool():
    runtime = get_runtime(mock=True)
    key = "page-key-A"  # mock encode_page 接受 str 鍵
    enc = encode_page_image(runtime, key)
    assert isinstance(enc, EncodedPage)
    assert enc.embed_model == "mock-colpali"
    # mock page = 64 patch、全 valid
    assert enc.n_patches == 64
    # 與直接用 shared 函式一致（單一來源驗證）
    vecs = runtime.encode_page(key)
    expected_bins = [binarize(v) for v in vecs.embeddings]
    expected_pooled = pool_patches(vecs.embeddings, vecs.valid_mask)
    assert enc.patch_bins == expected_bins
    np.testing.assert_array_equal(enc.pooled_f32, expected_pooled)


def test_encode_page_excludes_invalid_mask_from_pool_and_bins():
    # 構造一個 valid_mask 有 False 的假 runtime
    class FakeVecs:
        embeddings = np.random.default_rng(0).standard_normal((10, 128)).astype("float32")
        valid_mask = np.array([True] * 8 + [False] * 2)

    class FakeRuntime:
        model_id = "fake"
        def encode_page(self, image):
            return FakeVecs()

    enc = encode_page_image(FakeRuntime(), "x")
    assert enc.n_patches == 8  # 2 個 invalid 被排除
    assert enc.embed_model == "fake"


def test_encode_page_deterministic():
    r = get_runtime(mock=True)
    assert encode_page_image(r, "same").patch_bins == encode_page_image(r, "same").patch_bins
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_colpali_encoder.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.colpali_encoder'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/colpali_encoder.py
"""頁面影像 → EncodedPage：runtime 編碼 + shared.binary 二值化/池化（§2.4 單一來源）。

valid_mask=False（padding/特殊 token）的列**同時**排除於 patch 二值化與 pooled——
與 query 端一致，否則檢索精度崩壞（CLAUDE.md 共用二值化紅線）。
runtime 由呼叫端用 get_runtime(mock=...) 取得（torch 隔離維持在 shared/colpali_real）。
"""
from __future__ import annotations

import numpy as np

from anatomy_shared.binary import binarize, pool_patches

from .types import EncodedPage


def encode_page_image(runtime, image) -> EncodedPage:
    """單頁編碼。image：真實版 PIL.Image；mock 接受 str 鍵或 array-like。"""
    vecs = runtime.encode_page(image)
    mask = np.asarray(vecs.valid_mask, dtype=bool)
    valid = vecs.embeddings[mask]
    patch_bins = [binarize(v) for v in valid]
    pooled = pool_patches(vecs.embeddings, mask)  # pool_patches 自行套 mask
    page_num = getattr(image, "page_num", 0)  # 真實流程由 cli 覆寫 page_num（見 Task 9）
    return EncodedPage(
        page_num=page_num,
        patch_bins=patch_bins,
        pooled_f32=np.asarray(pooled, dtype=np.float32),
        embed_model=getattr(runtime, "model_id", "unknown"),
    ).validate()
```

> 注意：`encode_page_image` 不知道 page_num（影像不帶頁碼），回傳 `page_num=0` 佔位；cli 編排時以 `dataclasses.replace(enc, page_num=parse.page_num)` 補上（Task 9）。測試只驗 patch/pooled，不依賴 page_num。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_colpali_encoder.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/colpali_encoder.py ingest/tests/test_colpali_encoder.py
git commit -m "feat(ingest): colpali_encoder（共用 shared.binary 二值化/池化，valid_mask 一致排除）"
```

---

## Task 7: `storage.py` — PNG 上傳 MinIO/S3（boto3）

**Files:**
- Create: `ingest/src/anatomy_ingest/storage.py`
- Test: `ingest/tests/test_storage.py`（最小 fake S3 client，免 moto）

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_storage.py
import io
from PIL import Image
from anatomy_ingest.storage import page_key, upload_page_png


def test_page_key_scheme():
    k = page_key(kb_version=2, book_id="b1234", page_num=7)
    assert k == "kb_v2/b1234/page_0007.png"


class _FakeS3:
    def __init__(self):
        self.puts = []
    def put_object(self, **kw):
        self.puts.append(kw)
        return {"ETag": "x"}


def test_upload_page_png_puts_png_bytes_and_returns_uri():
    s3 = _FakeS3()
    img = Image.new("RGB", (10, 10), "white")
    uri = upload_page_png(s3, bucket="anatomy-rag-pages", key="kb_v1/b/page_0001.png", image=img)
    assert uri == "s3://anatomy-rag-pages/kb_v1/b/page_0001.png"
    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Bucket"] == "anatomy-rag-pages" and put["Key"] == "kb_v1/b/page_0001.png"
    assert put["ContentType"] == "image/png"
    # body 為合法 PNG
    body = put["Body"]
    data = body.getvalue() if hasattr(body, "getvalue") else body
    assert Image.open(io.BytesIO(data)).format == "PNG"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_storage.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.storage'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/storage.py
"""物件儲存：頁面 PNG 上傳 MinIO/S3（§2.1）。boto3 client 由 config 建立並注入。"""
from __future__ import annotations

import io


def page_key(kb_version: int, book_id: str, page_num: int) -> str:
    """物件鍵：kb_v{N}/{book_id}/page_{num:04d}.png（依版本+書分層，利於刪除/備份）。"""
    return f"kb_v{kb_version}/{book_id}/page_{page_num:04d}.png"


def upload_page_png(s3_client, bucket: str, key: str, image) -> str:
    """把 PIL.Image 編碼為 PNG 上傳，回傳 s3:// URI（寫入 pages.page_image_uri）。"""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    s3_client.put_object(Bucket=bucket, Key=key, Body=buf, ContentType="image/png")
    return f"s3://{bucket}/{key}"
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_storage.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/storage.py ingest/tests/test_storage.py
git commit -m "feat(ingest): storage（page_key 分層命名 + upload_page_png boto3）"
```

---

## Task 8: `config.py` — env 讀取（DB url / S3 client，無新依賴）

**Files:**
- Create: `ingest/src/anatomy_ingest/config.py`
- Test: `ingest/tests/test_config.py`

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_config.py
import pytest
from anatomy_ingest.config import IngestConfig


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pgbouncer:6432/anatomy_rag")
    monkeypatch.setenv("S3_BUCKET", "anatomy-rag-pages")
    monkeypatch.setenv("S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    cfg = IngestConfig.from_env()
    assert cfg.database_url.endswith("/anatomy_rag") and cfg.s3_bucket == "anatomy-rag-pages"


@pytest.mark.parametrize("url", [
    "postgresql://u:p@postgres:5432/anatomy_rag",   # 直連 Postgres
    "postgresql://u:p@host/anatomy_rag",            # 無 port
    "postgresql://u:p@host:6543/anatomy_rag",       # 其他 port（如 Supavisor）
])
def test_config_requires_port_6432(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    for k in ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.setenv(k, "x")
    with pytest.raises(ValueError, match="6432|PgBouncer"):
        IngestConfig.from_env()


def test_config_missing_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        IngestConfig.from_env()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_config.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.config'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/config.py
"""ingest 設定：讀同一組 .env（DB / S3）。不引 pydantic-settings（不新增依賴），純 os.environ。

DB 連 PgBouncer :6432（CLAUDE.md 紅線；migrations 才用 PG_DIRECT_URL，ingest 不碰）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class IngestConfig:
    database_url: str
    s3_bucket: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str

    @classmethod
    def from_env(cls) -> "IngestConfig":
        def req(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                raise ValueError(f"缺必要環境變數 {name}")
            return v

        database_url = req("DATABASE_URL")
        port = urlparse(database_url).port
        if port != 6432:
            # 與 backend config._must_use_pgbouncer 同準則：只接受 :6432（無 port / 其他 port 一律拒）
            raise ValueError(
                f"ingest MUST 連 PgBouncer :6432（目前 port={port}）；禁止直連 :5432 或其他 port"
                "（僅 Alembic migrations 用 PG_DIRECT_URL 直連 :5432）"
            )
        return cls(
            database_url=database_url,
            s3_bucket=req("S3_BUCKET"),
            s3_endpoint=req("S3_ENDPOINT"),
            s3_access_key=req("S3_ACCESS_KEY"),
            s3_secret_key=req("S3_SECRET_KEY"),
        )

    def make_s3_client(self):
        """建立 boto3 S3 client（指向 MinIO/S3 endpoint）。"""
        import boto3  # lazy

        return boto3.client(
            "s3",
            endpoint_url=self.s3_endpoint,
            aws_access_key_id=self.s3_access_key,
            aws_secret_access_key=self.s3_secret_key,
        )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_config.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/config.py ingest/tests/test_config.py
git commit -m "feat(ingest): config（env 讀取，強制 :6432，S3 client 工廠）"
```

---

## Task 9: `source.py` — pdf_source / synthetic_source / produce_pages

**Files:**
- Create: `ingest/src/anatomy_ingest/source.py`
- Test: `ingest/tests/test_source.py`

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_source.py
from PIL import Image
from anatomy_ingest.source import synthetic_source
from anatomy_ingest.types import SourcePage


def test_synthetic_source_yields_n_pages():
    meta = {"book_title": "Synthetic Atlas", "edition": 1}
    pages = list(synthetic_source(n_pages=3, book_meta=meta))
    assert len(pages) == 3
    assert all(isinstance(p, SourcePage) for p in pages)
    nums = [p.parse.page_num for p in pages]
    assert nums == [1, 2, 3]


def test_synthetic_source_metadata_and_image():
    meta = {"book_title": "Synthetic Atlas", "edition": 1}
    p = next(iter(synthetic_source(n_pages=1, book_meta=meta)))
    assert p.parse.metadata["book_title"] == "Synthetic Atlas"
    assert p.parse.metadata["page_num"] == 1
    assert p.parse.metadata["page_type"] in ("pure_text", "figure_heavy", "table", "mixed")
    assert isinstance(p.image, Image.Image)


def test_synthetic_source_deterministic_markdown():
    meta = {"book_title": "A", "edition": 1}
    a = [sp.parse.markdown for sp in synthetic_source(2, meta)]
    b = [sp.parse.markdown for sp in synthetic_source(2, meta)]
    assert a == b


def test_pdf_source_yields_none_image_for_missing_render(monkeypatch):
    """渲染缺頁時 pdf_source 仍產出該頁（image=None），不靜默丟棄（Codex high #1）。"""
    import anatomy_ingest.source as src
    from anatomy_ingest.types import PageParse
    from PIL import Image

    parses = [
        PageParse(page_num=1, markdown="p1", metadata={"page_num": 1}),
        PageParse(page_num=2, markdown="p2", metadata={"page_num": 2}),
        PageParse(page_num=3, markdown="p3", metadata={"page_num": 3}),
    ]
    monkeypatch.setattr(src, "convert_pdf", lambda path: object())
    monkeypatch.setattr(src, "extract_pages", lambda doc, meta: parses)
    # 只渲染出 1、3 頁（缺第 2 頁）
    monkeypatch.setattr(src, "render_pdf_pages",
                        lambda path: {1: Image.new("RGB", (4, 4)), 3: Image.new("RGB", (4, 4))})
    out = list(src.pdf_source("x.pdf", {"book_title": "A"}))
    assert [sp.parse.page_num for sp in out] == [1, 2, 3]  # 三頁都產出，無遺漏
    assert out[1].image is None and out[0].image is not None and out[2].image is not None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_source.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.source'`）

- [ ] **Step 3: 實作**

```python
# ingest/src/anatomy_ingest/source.py
"""來源 producer：真實 PDF 與合成兩種，產出統一的 SourcePage（下游 encode/upload/write 相同）。

- pdf_source：DocumentConverter + pdf2image（需 docling 模型 + poppler；host gate）。
- synthetic_source：dev/CI 用，捏造 N 頁 PIL 影像 + 罐頭 Markdown（無 poppler/GPU/雲端）。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from PIL import Image, ImageDraw

from .classify import classify_page_type, extract_figures, map_anatomy_system
from .docling_parser import convert_pdf, extract_pages
from .page_render import render_pdf_pages
from .types import PageParse, SourcePage

_SYNTH_CHAPTERS = ("Upper Limb", "The Heart", "Cranial Nerves")
_SYNTH_BODY = "Synthetic page {n}. The structure is described here. See Fig. {n}-1."


def pdf_source(pdf_path: str, book_meta: dict[str, Any]) -> Iterator[SourcePage]:
    """真實路徑：PDF → SourcePage（解析 + 渲染對齊頁碼）。

    解析/渲染頁碼**對齊**：每個被解析出的頁都產出一個 SourcePage——若渲染缺該頁影像，
    `image=None`（**不靜默丟棄**），由 cli 記 stage='render' 的 ingest_error 並計為失敗
    （§2.7：單頁失敗須留痕，不可變成「成功的遺漏」）。
    """
    doc = convert_pdf(pdf_path)
    parses = {p.page_num: p for p in extract_pages(doc, book_meta)}
    images = render_pdf_pages(pdf_path)
    for page_num in sorted(parses):
        yield SourcePage(parse=parses[page_num], image=images.get(page_num))  # 缺圖→None，不丟棄


def synthetic_source(n_pages: int, book_meta: dict[str, Any]) -> Iterator[SourcePage]:
    """合成路徑：決定性 N 頁。影像為帶頁碼文字的白底圖；Markdown 罐頭但走真實 classify。"""
    for n in range(1, n_pages + 1):
        chapter = _SYNTH_CHAPTERS[(n - 1) % len(_SYNTH_CHAPTERS)]
        markdown = f"## {chapter}\n\n" + _SYNTH_BODY.format(n=n)
        metadata = {
            "book_title": book_meta.get("book_title"),
            "edition": book_meta.get("edition"),
            "page_num": n,
            "chapter": chapter,
            "anatomy_system": map_anatomy_system(chapter, book_meta.get("system_map")),
            "page_type": classify_page_type(n_pictures=0, n_tables=0, text_len=len(markdown)),
            "figures": extract_figures(markdown),
        }
        img = Image.new("RGB", (800, 1000), "white")
        ImageDraw.Draw(img).text((20, 20), f"{chapter} p{n}", fill="black")
        yield SourcePage(parse=PageParse(page_num=n, markdown=markdown, metadata=metadata), image=img)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_source.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/source.py ingest/tests/test_source.py
git commit -m "feat(ingest): source（pdf_source 真實 + synthetic_source dev/CI；缺渲染頁不丟棄）"
```

---

## Task 10: `writer.py` — 批交易 + 每頁 savepoint + ingest_errors + resume（MUST review）

**Files:**
- Create: `ingest/src/anatomy_ingest/writer.py`
- Test: `ingest/tests/test_writer_db.py`（`-m db`，真 Postgres）
- Create: `ingest/tests/conftest.py`（db 守門 + fixtures）

> ⚠️ **本 Task 為 MUST 審查項**（資料寫入/交易語意，CLAUDE.md「Phase-level review」）。實作後 phase-level review（Codex 優先）。

- [ ] **Step 1: 寫 `ingest/tests/conftest.py`（db 守門，沿用 backend 慣例）**

```python
# ingest/tests/conftest.py
"""ingest 測試 fixtures：db 守門（沿用 backend conftest 慣例）+ 真連線。

db 標記測試需 DATABASE_URL（:6432）+ PG_DIRECT_URL（:5432，建 schema 用）。
destructive 守門：DB 名須以 _test 結尾或設 ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1。
"""
import os
from urllib.parse import urlparse

import pytest

_DB_ENV_READY = bool(os.environ.get("DATABASE_URL")) and bool(os.environ.get("PG_DIRECT_URL"))


def pytest_configure(config):
    if os.environ.get("REQUIRE_DB_TESTS") == "1" and not _DB_ENV_READY:
        raise pytest.UsageError("REQUIRE_DB_TESTS=1 但缺 DATABASE_URL / PG_DIRECT_URL")
    if _DB_ENV_READY:
        db_name = urlparse(os.environ["DATABASE_URL"]).path.lstrip("/")
        if not db_name.endswith("_test") and os.environ.get(
            "ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE"
        ) != "1":
            raise pytest.UsageError(
                "db 測試會寫入/清空目標資料庫；DB 名須以 _test 結尾或設 "
                "ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1"
            )


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(reason="需要 DATABASE_URL + PG_DIRECT_URL（CI db-integration 或本機 compose）")
    for item in items:
        if "db" in item.keywords and not _DB_ENV_READY:
            item.add_marker(skip_db)
```

- [ ] **Step 2: 寫失敗測試 `ingest/tests/test_writer_db.py`**

```python
# ingest/tests/test_writer_db.py
"""writer 交易語意（DL-023）：批交易 + 每頁 savepoint + ingest_errors + resume。

前置：schema 已由 backend Alembic migrate（CI db-integration 已 upgrade head）。
每個測試自建一本 book、用唯一 kb_version 避免相互污染，結束清理。
"""
import os
import uuid

import asyncpg
import numpy as np
import pytest

from anatomy_ingest.types import EncodedPage
from anatomy_ingest.writer import (
    ensure_kb_partition, completed_page_nums, write_batch, sample_verify,
)

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

KB = 9001  # 測試專用 kb_version（避開正式 1）


def _enc(page_num, n_patches=4):
    rng = np.random.default_rng(page_num)
    bins = [np.packbits((rng.standard_normal(128) > 0).astype("uint8")).tobytes()
            for _ in range(n_patches)]
    return EncodedPage(page_num=page_num, patch_bins=bins,
                       pooled_f32=rng.standard_normal(128).astype("float32"),
                       embed_model="mock-colpali")


def _page_record(page_num):
    return {
        "page_num": page_num,
        "page_image_uri": f"s3://b/kb_v{KB}/page_{page_num:04d}.png",
        "docling_md": f"## Chapter\n\npage {page_num}",
        "metadata": {"page_num": page_num, "anatomy_system": "musculoskeletal",
                     "page_type": "mixed", "figures": []},
    }


async def _conn():
    return await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)


async def _new_book(conn):
    return await conn.fetchval(
        "INSERT INTO books (title, edition) VALUES ($1, $2) RETURNING book_id",
        "Writer Test", "1",
    )


@pytest.fixture
async def conn():
    c = await _conn()
    await ensure_kb_partition(c, KB)
    yield c
    # 清理：刪本測試 kb_version 的資料
    await c.execute("DELETE FROM page_patches WHERE kb_version = $1", KB)
    await c.execute("DELETE FROM pages WHERE kb_version = $1", KB)
    await c.execute("DELETE FROM ingest_errors WHERE kb_version = $1", KB)
    await c.close()


async def test_write_batch_happy_path(conn):
    book_id = await _new_book(conn)
    batch = [(_page_record(1), _enc(1)), (_page_record(2), _enc(2))]
    outcome = await write_batch(conn, book_id, KB, batch)
    assert sorted(outcome.written) == [1, 2] and outcome.failed == []
    n_pages = await conn.fetchval("SELECT count(*) FROM pages WHERE kb_version=$1", KB)
    n_patches = await conn.fetchval("SELECT count(*) FROM page_patches WHERE kb_version=$1", KB)
    assert n_pages == 2 and n_patches == 8  # 2 頁 × 4 patch


async def test_savepoint_isolates_failed_page(conn):
    book_id = await _new_book(conn)
    good = (_page_record(1), _enc(1))
    # page 2 故意違反 UNIQUE(book_id,page_num,kb_version)：先塞一筆同 page_num
    await write_batch(conn, book_id, KB, [(_page_record(2), _enc(2))])
    dup = (_page_record(2), _enc(2))  # 重複 → 寫入失敗
    good3 = (_page_record(3), _enc(3))
    outcome = await write_batch(conn, book_id, KB, [good, dup, good3])
    assert sorted(outcome.written) == [1, 3]
    assert outcome.failed == [2]
    # ingest_errors 有 page 2 的 write 失敗紀錄
    err = await conn.fetchrow(
        "SELECT stage, page_num FROM ingest_errors WHERE kb_version=$1 AND page_num=2", KB)
    assert err["stage"] == "write"
    # 交易仍提交：page 1、3 在庫
    rows = await conn.fetch("SELECT page_num FROM pages WHERE kb_version=$1 ORDER BY page_num", KB)
    assert [r["page_num"] for r in rows] == [2, 3, 1] or {r["page_num"] for r in rows} == {1, 2, 3}


async def test_completed_page_nums_for_resume(conn):
    book_id = await _new_book(conn)
    await write_batch(conn, book_id, KB, [(_page_record(1), _enc(1)), (_page_record(5), _enc(5))])
    done = await completed_page_nums(conn, book_id, KB)
    assert done == {1, 5}


async def test_sample_verify_counts_match(conn):
    book_id = await _new_book(conn)
    batch = [(_page_record(i), _enc(i, n_patches=4)) for i in (1, 2, 3, 4)]
    await write_batch(conn, book_id, KB, batch)
    # 全抽（fraction=1.0）：每頁 patch 數應為 4
    report = await sample_verify(conn, book_id, KB, fraction=1.0, rng_seed=0)
    assert report["sampled"] == 4 and report["mismatches"] == []


async def test_record_error_failure_does_not_lose_batch(conn, monkeypatch):
    """記錯本身爆炸時，獨立 savepoint 保護同批已成功頁不被整批 rollback（Codex high #4）。"""
    import anatomy_ingest.writer as w

    book_id = await _new_book(conn)
    await write_batch(conn, book_id, KB, [(_page_record(2), _enc(2))])  # 先塞 page 2 → 後續重複觸發失敗

    async def boom(*a, **k):
        raise RuntimeError("ingest_errors 寫入爆炸")

    monkeypatch.setattr(w, "_record_error", boom)
    batch = [(_page_record(1), _enc(1)), (_page_record(2), _enc(2)), (_page_record(3), _enc(3))]
    outcome = await write_batch(conn, book_id, KB, batch)
    assert sorted(outcome.written) == [1, 3] and outcome.failed == [2]
    rows = await conn.fetch(
        "SELECT page_num FROM pages WHERE kb_version=$1 AND book_id=$2", KB, book_id)
    assert {r["page_num"] for r in rows} == {1, 2, 3}  # 1、3 仍提交，未因記錯爆炸而整批丟失


async def test_record_error_clamps_invalid_page_num_to_null(conn):
    """page_num<1 違反 pages CHECK → 記錯時 clamp 為 NULL（book 層），ingest_errors 插入不自爆。"""
    book_id = await _new_book(conn)
    bad = dict(_page_record(1))
    bad["page_num"] = -5  # 違反 pages CHECK(page_num>=1)
    outcome = await write_batch(conn, book_id, KB, [(bad, _enc(1)), (_page_record(7), _enc(7))])
    assert outcome.failed == [-5] and outcome.written == [7]
    err = await conn.fetchrow(
        "SELECT page_num, stage FROM ingest_errors WHERE kb_version=$1 AND book_id=$2"
        " ORDER BY error_id DESC LIMIT 1", KB, book_id)
    assert err["page_num"] is None and err["stage"] == "write"


async def test_page_identity_mismatch_is_per_page_failure(conn):
    """rec 與 enc 的 page_num 不符 → 該頁不寫入、記 ingest_errors，不汙染檢索（Codex high #1）。"""
    book_id = await _new_book(conn)
    mismatched = (_page_record(1), _enc(2))  # rec=page1 但 enc=page2 → 配對錯誤
    outcome = await write_batch(conn, book_id, KB, [mismatched, (_page_record(3), _enc(3))])
    assert outcome.written == [3] and outcome.failed == [1]
    n1 = await conn.fetchval(
        "SELECT count(*) FROM pages WHERE kb_version=$1 AND book_id=$2 AND page_num=1", KB, book_id)
    assert n1 == 0  # 身分不符的頁未寫入
    err = await conn.fetchrow(
        "SELECT page_num, stage FROM ingest_errors WHERE kb_version=$1 AND book_id=$2 AND page_num=1",
        KB, book_id)
    assert err is not None and err["stage"] == "write"


async def test_malformed_record_does_not_lose_batch(conn):
    """缺 page_num 的畸形 record（KeyError 在 savepoint 內）不致整批 rollback（Codex high #2）。"""
    book_id = await _new_book(conn)
    malformed = ({"page_image_uri": "s3://x", "docling_md": "x", "metadata": {}}, _enc(1))  # 缺 page_num
    outcome = await write_batch(
        conn, book_id, KB, [(_page_record(1), _enc(1)), malformed, (_page_record(3), _enc(3))])
    assert sorted(p for p in outcome.written) == [1, 3]
    assert None in outcome.failed  # 畸形 record 記為 None（book 層）
    rows = await conn.fetch(
        "SELECT page_num FROM pages WHERE kb_version=$1 AND book_id=$2", KB, book_id)
    assert {r["page_num"] for r in rows} == {1, 3}  # 好頁仍提交


async def test_sample_verify_detects_missing_expected(conn):
    """expected_page_nums 提供時，pages 缺的預期頁列入 mismatches（Codex medium #3）。"""
    book_id = await _new_book(conn)
    await write_batch(conn, book_id, KB, [(_page_record(1), _enc(1)), (_page_record(2), _enc(2))])
    # 預期 1、2、3 都在，但只寫了 1、2 → page 3 應被標 missing
    report = await sample_verify(conn, book_id, KB, fraction=1.0, rng_seed=0,
                                 expected_page_nums={1, 2, 3})
    missing = [m for m in report["mismatches"] if m["reason"] == "expected page missing from pages"]
    assert missing == [{"page_num": 3, "reason": "expected page missing from pages"}]
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_writer_db.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.writer'`；或無 DB 時 skip——本機請先 `make up` + `make migrate` 並設 `DATABASE_URL`/`PG_DIRECT_URL`/`ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1`，或留待 CI db-integration）

- [ ] **Step 4: 實作 `ingest/src/anatomy_ingest/writer.py`**

```python
# ingest/src/anatomy_ingest/writer.py
"""DB 寫入（DL-023 交易語意）：批交易 + 每頁 savepoint + ingest_errors + resume 輔助。

連線由呼叫端建立（asyncpg，連 :6432、statement_cache_size=0）。
編碼/上傳已在交易外完成；本模組只做短交易內的 INSERT。
patch_bin 綁定 to_pg_bits + ::text::bit(128)；pooled 綁定 ::halfvec（§4.4 / DL-019）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np

from anatomy_shared.binary import to_pg_bits

from .types import EncodedPage, WriteOutcome

logger = logging.getLogger(__name__)


def _pooled_to_halfvec_literal(pooled: np.ndarray) -> str:
    """float32[128] → PostgreSQL vector/halfvec 文字字面值 '[v1,v2,…]'。

    halfvec 字面值僅在離線寫入用；Phase 5 query 端若需同格式可再抽到 shared。
    用 repr 保留 float32 精度（halfvec 入庫時 PG 端再量化為 fp16）。
    """
    arr = np.asarray(pooled, dtype=np.float32).ravel()
    if arr.shape[0] != 128:
        raise ValueError(f"pooled 須 128 維，收到 {arr.shape[0]}")
    return "[" + ",".join(f"{float(x):.7g}" for x in arr) + "]"


async def ensure_kb_partition(conn, kb_version: int) -> None:
    """建立（冪等）page_patches 的 kb_version 分區（DL-010/DL-017）。

    與 backend.db.kb_version.ensure_kb_partition 同一份 SQL；ingest 自帶以免跨包依賴。
    """
    if type(kb_version) is not int or kb_version < 1:
        raise ValueError(f"kb_version 必須為正整數，收到 {kb_version!r}")
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{kb_version} "
        f"PARTITION OF page_patches FOR VALUES IN ({kb_version})"
    )


async def completed_page_nums(conn, book_id, kb_version: int) -> set[int]:
    """已成功寫入的 page_num（--resume 用）。"""
    rows = await conn.fetch(
        "SELECT page_num FROM pages WHERE book_id = $1 AND kb_version = $2", book_id, kb_version
    )
    return {r["page_num"] for r in rows}


async def _insert_page(conn, book_id, kb_version: int, rec: dict[str, Any], enc: EncodedPage):
    page_id = await conn.fetchval(
        "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
        " pooled, kb_version, embed_model)"
        " VALUES ($1, $2, $3, $4, $5::jsonb, $6::halfvec, $7, $8) RETURNING page_id",
        book_id, rec["page_num"], rec["page_image_uri"], rec["docling_md"],
        json.dumps(rec["metadata"]), _pooled_to_halfvec_literal(enc.pooled_f32),
        kb_version, enc.embed_model,
    )
    await conn.executemany(
        "INSERT INTO page_patches (kb_version, page_id, patch_idx, patch_bin)"
        " VALUES ($1, $2, $3, $4::text::bit(128))",
        [(kb_version, page_id, i, to_pg_bits(b)) for i, b in enumerate(enc.patch_bins)],
    )


async def _record_error(conn, book_id, kb_version: int, page_num, exc: Exception,
                        stage: str = "write"):
    """寫 ingest_errors。page_num<1（違反 CHECK）改記為 NULL（book 層）以免插入自身失敗。

    stage 可為 parse/render/encode/upload/write（§3.2 CHECK）；供 cli 上游階段共用。
    """
    safe_page = page_num if (isinstance(page_num, int) and page_num >= 1) else None
    await conn.execute(
        "INSERT INTO ingest_errors (kb_version, book_id, page_num, stage, error_type, message, detail)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
        kb_version, book_id, safe_page, stage, type(exc).__name__, str(exc)[:2000], json.dumps({}),
    )


async def record_page_error(conn, book_id, kb_version: int, page_num, exc: Exception,
                            stage: str) -> None:
    """獨立短交易寫 stage-specific ingest_errors（cli 的 encode/upload 階段用；不在 write_batch 交易內）。"""
    tx = conn.transaction()
    await tx.start()
    try:
        await _record_error(conn, book_id, kb_version, page_num, exc, stage=stage)
        await tx.commit()
    except Exception as rec_exc:
        await tx.rollback()
        logger.error("寫 ingest_errors 失敗（stage=%s page=%s）：原始=%r 記錯=%r",
                     stage, page_num, exc, rec_exc)


async def write_batch(conn, book_id, kb_version: int,
                      batch: list[tuple[dict[str, Any], EncodedPage]]) -> WriteOutcome:
    """一批 → 一交易；每頁 SAVEPOINT。成功 RELEASE、失敗 ROLLBACK TO SAVEPOINT + 寫 ingest_errors。

    批層級致命錯誤（連線斷等）會讓整批交易 rollback 並向上拋（cli 記批層級錯誤、續下批）。
    """
    written: list[int] = []
    failed: list[int] = []
    tx = conn.transaction()
    await tx.start()
    try:
        for idx, (rec, enc) in enumerate(batch):
            sp = f"sp_{idx}"
            await conn.execute(f"SAVEPOINT {sp}")
            page_num = None  # rec 畸形時保持 None → 記為 book 層錯誤、不致整批 rollback（Codex high #2）
            try:
                page_num = rec["page_num"]  # 移進 savepoint 內：畸形 record 的 KeyError 也走逐頁隔離
                if enc.page_num != page_num:
                    # 頁面身分守門（Codex high #1）：rec 與 enc 配對錯誤會把 A 頁 metadata 綁到 B 頁向量，
                    # 通過所有約束與 sample_verify 卻汙染檢索——當作該頁寫入失敗，不寫入。
                    raise ValueError(
                        f"page 識別不符：rec.page_num={page_num} 但 enc.page_num={enc.page_num}（疑似配對錯誤）")
                enc.validate()
                await _insert_page(conn, book_id, kb_version, rec, enc)
                await conn.execute(f"RELEASE SAVEPOINT {sp}")
                written.append(page_num)
            except Exception as exc:  # 單頁失敗：回退此頁、記錯（自身再包 savepoint）、續下一頁
                await conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                # 記錯包進獨立 savepoint：連寫 ingest_errors 都失敗（如 page_num 違反同一 CHECK）
                # 時 ROLLBACK TO 該 savepoint，**不波及同批已成功的頁**（Codex high #4）。
                esp = f"sp_err_{idx}"
                await conn.execute(f"SAVEPOINT {esp}")
                try:
                    await _record_error(conn, book_id, kb_version, page_num, exc)
                    await conn.execute(f"RELEASE SAVEPOINT {esp}")
                except Exception as rec_exc:
                    await conn.execute(f"ROLLBACK TO SAVEPOINT {esp}")
                    logger.error("寫 ingest_errors 失敗（page=%s）：原始=%r 記錯=%r",
                                 page_num, exc, rec_exc)
                failed.append(page_num)
        await tx.commit()
    except Exception:
        await tx.rollback()
        raise
    return WriteOutcome(written=written, failed=failed)


async def sample_verify(conn, book_id, kb_version: int, fraction: float = 0.05,
                        rng_seed: int | None = None,
                        expected_page_nums: set[int] | None = None) -> dict[str, Any]:
    """§2.7 SHOULD：隨機抽 fraction 比例頁面，比對 pages 存在 + page_patches 計數 > 0。

    expected_page_nums（選填，Codex medium #3）：呼叫端「預期應在庫」的 page_num 集合
    （cli 傳 todo 中未在上游階段失敗的頁）。提供時，**凡 expected 但 pages 缺的頁一律列入
    mismatches**（不受抽樣比例影響）——否則抽樣母體只取既存列，偵測不到「該在卻不在」的遺漏。
    """
    rows = await conn.fetch(
        "SELECT p.page_id, p.page_num, count(pp.patch_idx) AS n"
        " FROM pages p LEFT JOIN page_patches pp"
        "   ON pp.kb_version = p.kb_version AND pp.page_id = p.page_id"
        " WHERE p.book_id = $1 AND p.kb_version = $2"
        " GROUP BY p.page_id, p.page_num ORDER BY p.page_num",
        book_id, kb_version,
    )
    mismatches = []
    if expected_page_nums is not None:
        present = {r["page_num"] for r in rows}
        for pn in sorted(set(expected_page_nums) - present):
            mismatches.append({"page_num": pn, "reason": "expected page missing from pages"})
    if not rows:
        return {"sampled": 0, "mismatches": mismatches}
    rng = np.random.default_rng(rng_seed)
    k = max(1, round(len(rows) * fraction))
    idxs = rng.choice(len(rows), size=min(k, len(rows)), replace=False)
    for i in idxs:
        r = rows[int(i)]
        if r["n"] == 0:
            mismatches.append({"page_num": r["page_num"], "reason": "no patches"})
    return {"sampled": len(idxs), "mismatches": mismatches}
```

- [ ] **Step 5: 跑測試確認通過**

Run（需 compose DB；本機）:
```bash
make up && make migrate
DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag \
PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag \
ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1 \
uv run --no-sync pytest ingest/tests/test_writer_db.py -q
```
Expected: PASS（9 passed）

> `test_savepoint_isolates_failed_page` 的最終斷言放寬為「集合 == {1,2,3}」即可（IN 不保序，排序斷言用集合）。若 asyncpg savepoint 名稱衝突，確認每頁用唯一 `sp_{idx}`。

- [ ] **Step 6: 跑全 ingest 單元測試（非 db）確認未破壞**

Run: `uv run --no-sync pytest ingest/tests -q -m "not db"`
Expected: PASS（前述各 Task 測試全綠）

- [ ] **Step 7: Commit**

```bash
git add ingest/src/anatomy_ingest/writer.py ingest/tests/test_writer_db.py ingest/tests/conftest.py
git commit -m "feat(ingest): writer 批交易+每頁 savepoint+ingest_errors+resume（DL-023）"
```

- [ ] **Step 8: Phase-level review（MUST，交易/資料寫入）** — Codex `/codex:review`（writer.py + test_writer_db.py diff）。採納修正後再續 Task 11。

---

## Task 11: `cli.py` — 編排 + resume + 5% 抽樣 + no-cloud guard

**Files:**
- Create: `ingest/src/anatomy_ingest/cli.py`
- Test: `ingest/tests/test_cli.py`

- [ ] **Step 1: 寫失敗測試**

```python
# ingest/tests/test_cli.py
import subprocess
import sys

import pytest

from anatomy_ingest.cli import build_parser, chunk_pages, plan_pages
from anatomy_ingest.source import synthetic_source


def test_parser_required_args():
    p = build_parser()
    ns = p.parse_args(["--pdf", "x.pdf", "--book-meta", "x.yaml", "--kb-version", "3"])
    assert ns.kb_version == 3 and ns.batch_size == 8 and ns.resume is False and ns.book_id is None


def test_parser_flags():
    p = build_parser()
    ns = p.parse_args(["--synthetic", "5", "--book-meta", "x.yaml", "--kb-version", "1",
                       "--batch-size", "2", "--resume", "--book-id", "b-123"])
    assert ns.synthetic == 5 and ns.batch_size == 2 and ns.resume is True and ns.book_id == "b-123"


@pytest.mark.parametrize("args", [
    ["--synthetic", "0", "--book-meta", "x.yaml", "--kb-version", "1"],    # synthetic 0
    ["--synthetic", "-3", "--book-meta", "x.yaml", "--kb-version", "1"],   # 負
    ["--synthetic", "2", "--book-meta", "x.yaml", "--kb-version", "0"],    # kb 0
    ["--synthetic", "2", "--book-meta", "x.yaml", "--kb-version", "1", "--batch-size", "0"],  # batch 0
])
def test_parser_rejects_nonpositive_ints(args):
    with pytest.raises(SystemExit) as e:
        build_parser().parse_args(args)
    assert e.value.code == 2  # argparse 參數錯誤退碼 2


def test_chunk_pages():
    items = list(range(1, 8))
    assert chunk_pages(items, 3) == [[1, 2, 3], [4, 5, 6], [7]]


def test_plan_pages_resume_skips_completed():
    pages = list(synthetic_source(4, {"book_title": "A", "edition": 1}))
    todo, skipped = plan_pages(pages, completed={2, 3})
    assert [sp.parse.page_num for sp in todo] == [1, 4]
    assert skipped == [2, 3]


def test_help_exits_0_without_optional_deps(monkeypatch):
    """`--help` 不得因 import asyncpg/yaml/docling 等重依賴而失敗（Codex high #7）：
    隱藏這些模組仍要能印 usage。子行程驗證 import 鏈確實 dependency-light。"""
    code = (
        "import sys, builtins\n"
        "real_import = builtins.__import__\n"
        "blocked = {'asyncpg','yaml','docling','pdf2image','boto3','torch','transformers'}\n"
        "def guard(name, *a, **k):\n"
        "    if name.split('.')[0] in blocked: raise ImportError('blocked '+name)\n"
        "    return real_import(name, *a, **k)\n"
        "builtins.__import__ = guard\n"
        "from anatomy_ingest.cli import main\n"
        "sys.argv=['x','--help']\n"
        "try:\n"
        "    main()\n"
        "except SystemExit as e:\n"
        "    sys.exit(e.code or 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"--help 應退 0；stderr={r.stderr}"
    assert "usage" in r.stdout.lower()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run --no-sync pytest ingest/tests/test_cli.py -q`
Expected: FAIL（`No module named 'anatomy_ingest.cli'`）

- [ ] **Step 3: 實作 `ingest/src/anatomy_ingest/cli.py`**

> **重要（Codex high #7）**：`cli.py` **頂層只 import 輕量項**（argparse/asyncio/dataclasses/sys/typing）。
> 所有重依賴（asyncpg/yaml/get_runtime/source/storage/writer/config）一律 **lazy import 進 `_run`**——
> 讓 `--help` 在缺 poppler/torch/asyncpg 的部分佈建環境仍可印 usage。`build_parser`/`chunk_pages`/
> `plan_pages` 為純函式，不得依賴重模組。

```python
# ingest/src/anatomy_ingest/cli.py
"""離線建庫 CLI（§2.6）。MUST NOT 呼叫任何雲端 LLM API（離線紅線；test_no_cloud_llm 守門）。

流程（每批）：來源頁 → encode（GPU/mock，交易外，逐頁 guard）→ 上傳 PNG（交易外，逐頁 guard）
→ write_batch（短交易，'write' 階段 savepoint）。各上游階段（render/encode/upload）失敗逐頁寫
stage-specific ingest_errors 並續跑（§2.7）。書本識別走顯式 --book-id（§2.6 重建/續跑），不靠 title 猜測。
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
from typing import Any


def _positive_int(value: str) -> int:
    """argparse type：正整數（>=1）；0/負/非整數 → argparse 退碼 2（Codex medium #8）。"""
    try:
        iv = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"須為整數，收到 {value!r}")
    if iv < 1:
        raise argparse.ArgumentTypeError(f"須為正整數（>=1），收到 {iv}")
    return iv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anatomy_ingest.cli", description="離線建庫管線")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", help="教科書 PDF 路徑（真實路徑，需 poppler + docling）")
    src.add_argument("--synthetic", type=_positive_int, metavar="N",
                     help="合成 N 頁（dev/CI，無 poppler/GPU）")
    p.add_argument("--book-meta", required=True, help="書籍 metadata YAML")
    p.add_argument("--kb-version", type=_positive_int, required=True)
    p.add_argument("--batch-size", type=_positive_int, default=8)
    p.add_argument("--book-id", default=None,
                   help="既有 book_id（UUID）：重建（無 --resume：先刪該 book+kb_version 既有頁）"
                        "或續跑（--resume：跳過已完成頁）。首次建庫不帶此旗標→新增一本書。")
    p.add_argument("--resume", action="store_true",
                   help="跳過 pages 已存在的頁（須搭 --book-id；不靠 title 猜書）")
    p.add_argument("--mock-encoder", action="store_true", help="用決定性 mock runtime（CI/無 GPU）")
    return p


def chunk_pages(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def plan_pages(pages, completed: set[int]):
    todo = [sp for sp in pages if sp.parse.page_num not in completed]
    skipped = sorted(sp.parse.page_num for sp in pages if sp.parse.page_num in completed)
    return todo, skipped


def _load_book_meta(path: str) -> dict[str, Any]:
    import yaml  # lazy

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def _resolve_book(conn, ns, book_meta: dict[str, Any]):
    """書本識別（§2.6，Codex high #3）：

    - 帶 --book-id：書須存在。無 --resume＝重建 → 先 DELETE pages（cascade page_patches）；
      --resume → 不刪、回傳既有 book_id 供跳過已完成頁。
    - 不帶 --book-id：--resume 為非法（不靠 title 猜書）；否則新增一本書（首次建庫）。
    """
    if ns.book_id:
        import uuid

        try:
            book_uuid = uuid.UUID(str(ns.book_id))
        except ValueError:
            raise SystemExit(f"--book-id 非合法 UUID：{ns.book_id!r}")
        exists = await conn.fetchval("SELECT 1 FROM books WHERE book_id = $1", book_uuid)
        if not exists:
            raise SystemExit(f"--book-id {ns.book_id} 不存在；首次建庫請不帶 --book-id")
        if not ns.resume:
            # §2.6 重新執行：先刪該書該版本既有頁（page_patches 經 ON DELETE CASCADE 連帶刪）
            await conn.execute(
                "DELETE FROM pages WHERE book_id = $1 AND kb_version = $2", book_uuid, ns.kb_version)
            print(f"[rebuild] 已刪除 book={ns.book_id} kb_version={ns.kb_version} 既有頁，重建")
        return book_uuid
    if ns.resume:
        raise SystemExit("--resume 須搭配 --book-id（不靠 title 猜書，避免續跑到錯的書/版本）")
    book_uuid = await conn.fetchval(
        "INSERT INTO books (title, edition, isbn) VALUES ($1, $2, $3) RETURNING book_id",
        book_meta.get("book_title") or "Untitled",
        str(book_meta.get("edition") or ""), book_meta.get("isbn"),
    )
    print(f"[new] 新增書本 book_id={book_uuid}")
    return book_uuid


async def _encode_and_upload_batch(runtime, s3, cfg, book_id, kb_version, conn, batch_pages):
    """逐頁 encode + upload，各階段失敗寫 stage-specific ingest_errors 並續跑（Codex high #1/#2）。

    回 (records, failed_page_nums)：records 為通過 encode+upload、待 write_batch 的 (rec, enc)。
    """
    from .colpali_encoder import encode_page_image
    from .storage import page_key, upload_page_png
    from .writer import record_page_error

    records, failed = [], []
    for sp in batch_pages:
        pn = sp.parse.page_num
        if sp.image is None:  # 渲染缺頁（pdf_source 未丟棄）→ 記 render 失敗，跳過
            await record_page_error(conn, book_id, kb_version, pn, RuntimeError("render 缺頁影像"), "render")
            failed.append(pn)
            continue
        try:
            enc = encode_page_image(runtime, sp.image)
            enc = dataclasses.replace(enc, page_num=pn)
        except Exception as exc:
            await record_page_error(conn, book_id, kb_version, pn, exc, "encode")
            failed.append(pn)
            continue
        try:
            key = page_key(kb_version, str(book_id), pn)
            uri = upload_page_png(s3, cfg.s3_bucket, key, sp.image)
        except Exception as exc:
            await record_page_error(conn, book_id, kb_version, pn, exc, "upload")
            failed.append(pn)
            continue
        records.append(({
            "page_num": pn,
            "page_image_uri": uri,
            "docling_md": sp.parse.markdown,
            "metadata": sp.parse.metadata,
        }, enc))
    return records, failed


async def _run(ns: argparse.Namespace) -> int:
    import asyncpg

    from anatomy_shared.colpali_runtime import get_runtime

    from .config import IngestConfig
    from .source import pdf_source, synthetic_source
    from .writer import (
        completed_page_nums, ensure_kb_partition, record_page_error, sample_verify, write_batch,
    )

    cfg = IngestConfig.from_env()
    book_meta = _load_book_meta(ns.book_meta)
    runtime = get_runtime(mock=ns.mock_encoder or bool(ns.synthetic))
    s3 = cfg.make_s3_client()
    conn = await asyncpg.connect(cfg.database_url, statement_cache_size=0)
    try:
        await ensure_kb_partition(conn, ns.kb_version)
        book_id = await _resolve_book(conn, ns, book_meta)

        # 來源段（parse/render 為整檔操作）：整檔失敗記 book 層 parse 錯誤、非零退出（§2.7）
        try:
            if ns.synthetic:
                pages = list(synthetic_source(ns.synthetic, book_meta))
            else:
                pages = list(pdf_source(ns.pdf, book_meta))
        except Exception as exc:
            await record_page_error(conn, book_id, ns.kb_version, None, exc, "parse")
            print(f"[fatal] 來源解析/渲染失敗：{exc!r}（已記 ingest_errors stage=parse）")
            return 1

        completed = await completed_page_nums(conn, book_id, ns.kb_version) if ns.resume else set()
        todo, skipped = plan_pages(pages, completed)
        if skipped:
            print(f"[resume] 跳過已完成 {len(skipped)} 頁：{skipped}")

        total_written, total_failed = [], []
        for batch_pages in chunk_pages(todo, ns.batch_size):
            records, up_failed = await _encode_and_upload_batch(
                runtime, s3, cfg, book_id, ns.kb_version, conn, batch_pages)
            total_failed += up_failed
            outcome = await write_batch(conn, book_id, ns.kb_version, records)
            total_written += outcome.written
            total_failed += outcome.failed
            print(f"[batch] 寫入 {outcome.written}，失敗 寫={outcome.failed} 上游={up_failed}")

        # expected = 本次嘗試（todo）中未在上游階段失敗的頁；sample_verify 據此偵測「該在卻不在」
        expected = {sp.parse.page_num for sp in todo} - set(total_failed)
        report = await sample_verify(conn, book_id, ns.kb_version, fraction=0.05,
                                     expected_page_nums=expected)
        print(f"[done] 共寫入 {len(total_written)} 頁、失敗 {len(total_failed)} 頁；抽樣校驗 {report}")
        return 1 if (total_failed or report["mismatches"]) else 0
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    return asyncio.run(_run(ns))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_cli.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add ingest/src/anatomy_ingest/cli.py ingest/tests/test_cli.py
git commit -m "feat(ingest): cli 編排（顯式 book-id/§2.6 重建/逐頁 stage 失敗留痕/輕量 --help/正整數驗證）"
```

---

## Task 12: 雲端 LLM 零呼叫守門（socket guard + import grep）

**Files:**
- Test: `ingest/tests/test_no_cloud_llm.py`
- Modify: `.github/workflows/ci.yml`（unit job 加 grep 守門）

- [ ] **Step 1: 寫測試 `ingest/tests/test_no_cloud_llm.py`**

```python
# ingest/tests/test_no_cloud_llm.py
"""離線管線 MUST NOT 呼叫雲端 LLM（CLAUDE.md 紅線）。

(1) socket guard：跑完整 mock 管線（synthetic 來源 + mock runtime + fake S3 + 假 DB 寫入路徑），
    任何對非本機位址的 TCP connect 立即拋錯 → 證明無對外連線。
(2) import 守門：ingest 套件原始碼不得 import openai/anthropic（與 CI grep 雙保險）。
"""
import socket
from pathlib import Path

import pytest

from anatomy_ingest.colpali_encoder import encode_page_image
from anatomy_ingest.source import synthetic_source
from anatomy_ingest.storage import page_key, upload_page_png
from anatomy_shared.colpali_runtime import get_runtime

INGEST_SRC = Path(__file__).resolve().parents[1] / "src" / "anatomy_ingest"


class _NoNetwork:
    """攔截所有 socket.connect；放行 loopback（本機 DB/MinIO），阻擋其餘。"""

    def __init__(self):
        self._orig = socket.socket.connect

    def __enter__(self):
        orig = self._orig

        def guarded(sock, address):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host in ("127.0.0.1", "::1", "localhost"):
                return orig(sock, address)
            raise AssertionError(f"離線管線嘗試對外連線：{address}（疑似雲端 LLM/外部 API）")

        socket.socket.connect = guarded
        return self

    def __exit__(self, *a):
        socket.socket.connect = self._orig


class _FakeS3:
    def put_object(self, **kw):
        return {"ETag": "x"}


def test_mock_pipeline_makes_no_outbound_connection():
    runtime = get_runtime(mock=True)
    s3 = _FakeS3()
    with _NoNetwork():
        for sp in synthetic_source(3, {"book_title": "A", "edition": 1}):
            enc = encode_page_image(runtime, sp.image)
            assert enc.n_patches > 0
            key = page_key(1, "book", sp.parse.page_num)
            upload_page_png(s3, "bucket", key, sp.image)  # fake，不連網


def test_ingest_source_does_not_import_cloud_llm():
    offenders = []
    for py in INGEST_SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for needle in ("import openai", "from openai", "import anthropic", "from anthropic"):
            if needle in text:
                offenders.append(f"{py.name}: {needle}")
    assert offenders == [], f"ingest 不得 import 雲端 LLM SDK：{offenders}"
```

- [ ] **Step 2: 跑測試確認通過**

Run: `uv run --no-sync pytest ingest/tests/test_no_cloud_llm.py -q`
Expected: PASS（2 passed）

- [ ] **Step 3: CI unit job 加 grep 守門**

於 `.github/workflows/ci.yml` 既有「確認無 LlamaIndex 殘留」step 後新增：

```yaml
      - name: 確認 ingest 離線管線無雲端 LLM SDK（CLAUDE.md 紅線）
        run: "! grep -rInE --include='*.py' '(import|from)\\s+(openai|anthropic)\\b' ingest || (echo '離線管線禁 import 雲端 LLM SDK' && exit 1)"
```

並把 ingest 測試納入 unit job 的 pytest（找到既有 `pytest backend/tests shared/tests colpali_service/tests eval/tests` 行，補 `ingest/tests`）：

```yaml
      - run: uv run --no-sync pytest backend/tests shared/tests colpali_service/tests eval/tests ingest/tests -q -m "not db"
```

> 注意：unit job 需能 import `anatomy_ingest`/`docling`/`pdf2image`/`boto3`。確認 unit job 有 `uv sync --package anatomy-ingest --inexact`（若無則於既有 sync steps 後補一行）。docling 重，若 unit job 不宜裝，改為將 ingest 純單元測試（不需 docling 的）獨立；但 `test_docling_parser` 需 docling-core（輕，非 docling 全套）。**實作時先確認 unit job 裝得起 anatomy-ingest；若 docling 太重，將 ingest 測試移到 db-integration job 連同 `-m db` 一起跑，unit job 僅保留 grep 守門。** 以實測決定，於 commit message 註明選擇。

- [ ] **Step 4: 本機驗 CI 指令片段**

Run: `! grep -rInE --include='*.py' '(import|from)\s+(openai|anthropic)\b' ingest && echo CLEAN`
Expected: 印出 `CLEAN`（無殘留）

- [ ] **Step 5: Commit**

```bash
git add ingest/tests/test_no_cloud_llm.py .github/workflows/ci.yml
git commit -m "feat(ingest): 雲端 LLM 零呼叫守門（socket guard + CI grep）+ ingest 測試納 CI"
```

---

## Task 13: Makefile / SETUP / backend Dockerfile 收尾 + GPU gate 腳本

**Files:**
- Modify: `Makefile`（修 `ingest-sample` + 新增 `ingest-gate`）
- Create: `ingest/scripts/ingest_gate.py`
- Modify: `SETUP.md`（補 §F.2/F.3 實際指令）
- Modify: `backend/Dockerfile`（註解澄清）

- [ ] **Step 1: 修 `Makefile` 的 `ingest-sample`、新增 `ingest-gate`**

把既有：
```make
ingest-sample:
	docker compose run --rm backend sh -c "uv run python -m anatomy_ingest.cli --pdf /data/sample.pdf --book-meta /data/sample.yaml --kb-version 1 --batch-size 4"
```
改為：
```make
# mock smoke：synthetic 來源 + mock runtime，寫入真 DB/MinIO（需 make up + make migrate）。
# 連 localhost:6432/9000（compose 對外埠）；不需 GPU/poppler。
ingest-sample:
	DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag \
	S3_ENDPOINT=http://localhost:9000 S3_BUCKET=anatomy-rag-pages \
	S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin \
	uv run --no-sync python -m anatomy_ingest.cli \
	  --synthetic 6 --book-meta ingest/scripts/sample_book.yaml --kb-version 1 --batch-size 2

# GPU gate：真 1 頁 PDF + real ColPali + 真 MinIO/PG（手動，非 CI；需 poppler + GPU venv）。
ingest-gate:
	uv run --no-sync python ingest/scripts/ingest_gate.py
```

並建立 `ingest/scripts/sample_book.yaml`：
```yaml
book_title: Synthetic Atlas
edition: 1
```

- [ ] **Step 2: 建立 `ingest/scripts/ingest_gate.py`（手動 GPU 端到端）**

```python
# ingest/scripts/ingest_gate.py
"""手動 GPU gate：真 3 頁 PDF → real ColPali → 真 MinIO/PG 端到端建庫驗收（非 CI）。

前置：poppler 已裝、GPU venv 有 torch+colpali、make up + make migrate 已跑、.env 指 localhost。
產生 3 頁可區辨 PDF（PIL），走完整 pdf_source（docling+poppler+real runtime）→ 寫 kb_version=9000。
驗收（Codex medium #6）：
- pages.page_num 集合 == {1,2,3}（解析/渲染/DB 頁碼精確對應，抓 docling/pdf2image 頁碼漂移）
- 每頁 page_patches>0、embed_model=vidore/colpali-v1.3-hf
- **逐頁 GET MinIO 物件**確認存在且為 PNG（非只數 DB 列）
完成後清理該 kb_version + book。
"""
import asyncio
import io
import os
import tempfile

import asyncpg
from PIL import Image, ImageDraw

from anatomy_ingest.cli import _run, build_parser  # 重用編排
from anatomy_ingest.config import IngestConfig

KB = 9000
N_PAGES = 3


def _make_pdf(path: str):
    pages = []
    chapters = ["Upper Limb", "The Heart", "Cranial Nerves"]
    for i, chap in enumerate(chapters, start=1):
        img = Image.new("RGB", (1240, 1754), "white")  # ~150dpi A4
        d = ImageDraw.Draw(img)
        d.text((80, 80), f"Chapter: {chap}", fill="black")
        d.text((80, 140), f"This is distinguishable page {i}. See Fig. {i}-1.", fill="black")
        pages.append(img)
    pages[0].save(path, "PDF", resolution=200.0, save_all=True, append_images=pages[1:])


async def main():
    os.environ.setdefault("DATABASE_URL", "postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag")
    os.environ.setdefault("S3_ENDPOINT", "http://localhost:9000")
    os.environ.setdefault("S3_BUCKET", "anatomy-rag-pages")
    os.environ.setdefault("S3_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("S3_SECRET_KEY", "minioadmin")
    cfg = IngestConfig.from_env()

    with tempfile.TemporaryDirectory() as td:
        pdf = os.path.join(td, "gate.pdf")
        meta = os.path.join(td, "gate.yaml")
        _make_pdf(pdf)
        with open(meta, "w") as f:
            f.write("book_title: Gate Atlas\nedition: 1\n")
        ns = build_parser().parse_args(
            ["--pdf", pdf, "--book-meta", meta, "--kb-version", str(KB), "--batch-size", "2"]
        )
        rc = await _run(ns)

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    s3 = cfg.make_s3_client()
    try:
        rows = await conn.fetch(
            "SELECT page_num, page_image_uri, embed_model,"
            " (SELECT count(*) FROM page_patches pp WHERE pp.kb_version=p.kb_version"
            "  AND pp.page_id=p.page_id) AS n_patches"
            " FROM pages p WHERE kb_version=$1 ORDER BY page_num", KB)
        page_nums = [r["page_num"] for r in rows]
        print(f"[gate] rc={rc} page_nums={page_nums}")
        assert page_nums == list(range(1, N_PAGES + 1)), f"頁碼對應不符：{page_nums}"
        for r in rows:
            assert r["n_patches"] > 0, f"page {r['page_num']} 無 patch"
            assert r["embed_model"] == "vidore/colpali-v1.3-hf", "embed_model 應為真實模型"
            # 逐頁 GET MinIO 物件並驗 PNG
            key = r["page_image_uri"].split(f"{cfg.s3_bucket}/", 1)[1]
            obj = s3.get_object(Bucket=cfg.s3_bucket, Key=key)
            data = obj["Body"].read()
            assert Image.open(io.BytesIO(data)).format == "PNG", f"page {r['page_num']} MinIO 物件非 PNG"
            print(f"[gate] page {r['page_num']} patches={r['n_patches']} png={len(data)}B OK")
        print("[gate] PASS — 清理測試資料")
        book_ids = await conn.fetch("SELECT DISTINCT book_id FROM pages WHERE kb_version=$1", KB)
        await conn.execute("DELETE FROM pages WHERE kb_version=$1", KB)  # page_patches cascade
        for b in book_ids:
            await conn.execute("DELETE FROM books WHERE book_id=$1", b["book_id"])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: `SETUP.md` §F.2/F.3 補完整指令**（Task 1 佔位 → 換成 `make ingest-sample` / `make ingest-gate` 實際說明 + poppler 安裝再次強調 gate 前置）

- [ ] **Step 4: `backend/Dockerfile` 註解澄清**

把第 13 行 `COPY ingest ./ingest` 上方加註：
```dockerfile
# 注意：ingest 原始碼隨 workspace 一起 COPY（uv sync 需所有成員 manifest），
# 但 backend 映像**不安裝** anatomy-ingest（torch/docling/poppler 重依賴）。
# 離線建庫 MUST 在 GPU host venv 執行（make ingest-gate）或專用 ingest 容器，非此 backend 容器。
```

- [ ] **Step 5: 本機驗 mock smoke（需 compose）**

Run:
```bash
make up && make migrate && make ingest-sample
```
Expected: 印出多個 `[batch] 寫入 [...]`、`[done] 共寫入 6 頁、失敗 0 頁；抽樣校驗 {'sampled': 1, 'mismatches': []}`，exit 0。

- [ ] **Step 6: Commit**

```bash
git add Makefile ingest/scripts/ SETUP.md backend/Dockerfile
git commit -m "chore(ingest): 修 ingest-sample（synthetic mock smoke）+ ingest-gate GPU 腳本 + SETUP/Dockerfile 澄清"
```

---

## Task 14: 全量回歸 + 收尾驗證

- [ ] **Step 1: 全 ingest 單元測試（非 db）**

Run: `uv run --no-sync pytest ingest/tests -q -m "not db"`
Expected: PASS（types/classify/docling/render/encoder/storage/config/source/cli/no_cloud 全綠）

- [ ] **Step 2: db 整合測試（需 compose）**

Run:
```bash
DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag \
PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag \
ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1 \
uv run --no-sync pytest ingest/tests -q -m db
```
Expected: PASS（writer 9 測試）

- [ ] **Step 3: lint + 既有套件未破壞**

Run:
```bash
uv run --no-sync ruff check ingest
uv run --no-sync pytest shared/tests backend/tests -q -m "not db"
```
Expected: ruff 乾淨；shared/backend 既有測試全綠（確認未污染單一來源守門）。

- [ ] **Step 4: 單一來源 grep 守門本機驗**

Run: `! grep -rInE --include='*.py' 'def (binarize|to_pg_bits|pool_patches|hamming_distance)\(' backend ingest eval colpali_service && echo OK`
Expected: 印 `OK`（ingest 未自行定義向量運算）

- [ ] **Step 5: GPU gate（host，手動）**

Run: `make up && make migrate && make ingest-gate`（需 poppler + GPU venv）
Expected: `[gate] PASS`（pages=1、patches>0、embed_model=vidore/colpali-v1.3-hf）

> GPU gate 非 CI；若 host 暫不便跑，回報中標註「GPU gate 待使用者於 host 執行」並附指令。

---

## 自我檢查（spec 覆蓋）

- §2.2 Docling 逐頁 MD + metadata 規範化（chapter/anatomy_system/page_type/figures）→ Task 3/4 ✓
- §2.1/§2.2 PNG 200 DPI / 長邊 ≤2048 → Task 5 ✓
- §2.3 ColPali `encode_pages` + embed_model 記錄 → Task 6/11 ✓
- §2.4 共用二值化（binarize/pool_patches 來自 shared、valid_mask 一致排除）→ Task 6 ✓（CI 單一來源守門 Task 14）
- §2.5 寫入帶 kb_version、patches 批次插入 → Task 10 ✓
- §2.6 CLI（--pdf/--book-meta/--kb-version/--batch-size/--resume/--book-id）→ Task 11 ✓
- §2.6 重新執行先 DELETE FROM pages WHERE book_id+kb_version → Task 11 `_resolve_book`（--book-id 無 --resume=重建）✓
- §2.7 單頁失敗寫 ingest_errors 不中斷（render/encode/upload/write 各階段）+ --resume + 5% 抽樣校驗 → Task 10/11 ✓
- DL-023 批交易 + 每頁 savepoint + :6432 → Task 1/10 ✓
- 紅線「離線管線 MUST NOT 呼叫雲端 LLM」→ Task 12 守門 ✓
- PNG 上 MinIO（依賴 minio-init bucket）→ Task 7/11 ✓
- 「無雲端 LLM 呼叫（網路 mock）」測試 → Task 12 socket guard ✓

## Codex 對抗式審查處置（2026-06-13 第一輪，writer=Claude / reviewer=Codex，真正跨模型隔離）

審查 verdict=needs-attention，8 項全採納並已併入上方 Task（無一挑戰架構前提；皆健全性/正確性缺口）：

| # | 嚴重度 | 問題 | 處置（Task） |
|---|---|---|---|
| 1 | high | `pdf_source` 渲染缺頁靜默丟棄 → 成功的遺漏 | image=None 不丟棄 + cli 記 stage='render'（Task 9/11）+ 對齊測試 |
| 2 | high | parse/render/encode/upload 失敗繞過逐頁恢復 | `_encode_and_upload_batch` 逐頁 guard + `record_page_error` stage-specific + 整檔 parse 失敗記 book 層（Task 10/11） |
| 3 | high | book 識別：重跑重複、resume 靠 title 猜錯書 | 顯式 `--book-id`（UUID 驗證）；無旗標=新書、帶旗標無 --resume=§2.6 DELETE 重建、--resume 須搭 --book-id（Task 11） |
| 4 | high | 記錯本身失敗會 rollback 整批成功頁 | `_record_error` clamp page_num<1→NULL + write_batch 記錯包獨立 savepoint（Task 10）+ 2 個新 db 測試 |
| 5 | medium | config 只擋 5432、無 port/他 port 漏網 | 改 `port != 6432` 一律拒（對齊 backend validator）+ 參數化測試（Task 8） |
| 6 | medium | 單頁 GPU gate 抓不到頁碼漂移、未真讀 MinIO | 3 頁可區辨 fixture + 頁碼精確對應斷言 + 逐頁 GET MinIO 驗 PNG（Task 13） |
| 7 | high | cli `--help` 急切 import 重依賴 → 部分佈建環境失敗 | 頂層只輕量 import、重依賴 lazy 進 `_run` + 子行程 `--help` 退 0 測試（Task 11） |
| 8 | medium | 數值參數未驗證（batch=0 崩、負數靜默空跑） | `_positive_int` argparse type + 參數化「退碼 2」測試（Task 11） |

**第二輪（2026-06-13，writer 實作後的跨模型 phase review，commit 6bbbf26）**：Codex 確認核心交易設計健全
（asyncpg `conn.transaction()` + 手動 SAVEPOINT 不 desync；`::text::bit(128)`/`::halfvec` 綁定、executemany、
f-string 名稱在 transaction pooling + `statement_cache_size=0` 下皆相容；無注入）。另抓 3 項計畫未涵蓋缺口，全採納併入 Task 10/11：

| # | 嚴重度 | 問題 | 處置 |
|---|---|---|---|
| 9 | high | `write_batch` 不驗 `rec.page_num == enc.page_num` → 配對錯誤把 A 頁 metadata 綁 B 頁向量，通過所有約束與 sample_verify 卻汙染檢索 | 插入前身分守門，不符＝該頁寫入失敗 + `test_page_identity_mismatch_is_per_page_failure`（Task 10） |
| 10 | high | `rec["page_num"]` 在 savepoint 之前讀取，畸形 record 的 KeyError 逃到外層整批 rollback | page_num 讀取/驗證移進 savepoint 內、page_num 預設 None + `test_malformed_record_does_not_lose_batch`（Task 10） |
| 11 | medium | `sample_verify` 母體只取既存列 → 偵測不到「該在卻不在」的遺漏頁 | 加 `expected_page_nums` 參數、cli 傳 todo−failed + `test_sample_verify_detects_missing_expected`（Task 10/11） |

**第三輪（2026-06-13，全 Phase 4 整合終審，commit 0fd686a）**：Codex 確認 writer 三項已修、核心健全；另抓 5 項整合層缺口。4 項已修、1 項判定為單操作員離線工具的已知限制延後：

| # | 嚴重度 | 問題 | 處置 |
|---|---|---|---|
| 12 | high | rebuild 在來源解析前就 DELETE 舊版 → 壞 PDF 會先毀舊版 | DELETE 移到來源解析成功後（`_resolve_book` 改回 `needs_rebuild`，`_run` 解析成功才刪）（cli.py FIX A） |
| 13 | high | GPU gate 忽略 rc、依 kb_version 而非本 run book_id 清理 → 可假性通過並刪無關資料 | gate 跑前 fail-fast（kb_version=9000 非空即拒）、`assert rc==0` 後才驗證（ingest_gate.py FIX B） |
| 14 | high | 並發 run 競爭同一 deterministic key → DB 列可能指向他 run 的影像 | **已知限制，延後**：ingest=單操作員離線 admin 工具（§2）；advisory lock 在 transaction pooling 不可靠，需 lock 表/staging key，歸 Phase 13 blue-green/ops。文件化、回報使用者 |
| 15 | medium | no-cloud 守門沒跑真正的 `_run`（手動拼裝步驟），雲端呼叫經 httpx/間接套件/localhost proxy 可漏 | 新增 db 測試在 socket guard 下跑完整 `_run`（synthetic + 真 localhost DB/MinIO）（test_no_cloud_llm.py FIX D） |
| 16 | medium | Docling 漏解析但 pdf2image 有渲染的頁被靜默丟棄 | `pdf_source` 改迭代 parsed∪rendered 聯集，漏解析頁標 `parse_failed` → cli 記 stage='parse'（source.py/cli.py FIX C） |

> **#14 並發寫入競爭（延後決策）**：v1 假設離線建庫由單一操作員串行執行（§2「管理者 CLI / 排程」）。
> 同一 `(book_id, kb_version)` 的並發 ingest 為運維反模式。真正的解（per-run advisory lock 或 staging
> key + 原子啟用）與 kb_version blue-green 切換同屬一個設計面，歸 **Phase 13 runbook**；於 SETUP/runbook
> 明示「勿並發跑同書同版本」。本項不在 Phase 4 修補。

## 風險與待後續處理（非阻斷）

1. **批交易在 PgBouncer transaction pooling 下的長度**：編碼/上傳已移到交易外，交易僅含 INSERT；大 batch_size × ~1024 patch/頁 的 executemany 仍可能偏長——實作時 batch_size 預設 8，真實教材壓測再調（Phase 13）。
2. **halfvec 字面值精度**：`%.7g` float32 → halfvec(fp16)；Phase 5 query 端若需同格式應抽到 shared 統一（目前離線端唯一使用點，docstring 已註）。
3. **page_type / anatomy_system 啟發式覆蓋率**：關鍵字表不全，Phase 11 真實教材再校（可在 metadata 標 low-confidence，留待 Phase 11）。
4. **unit job 是否裝得起 docling**（重依賴）→ Task 12 Step 3 註記的 fallback（ingest 測試改掛 db-integration job；以實測決定並於 commit 註明）。
5. **第二輪 Codex 複審**：本次修訂涉及 cli/writer 大改，實作完成後的 phase-level review（Task 10 Step 8）與 final review 仍走 Codex，確認修訂未引入新問題。
