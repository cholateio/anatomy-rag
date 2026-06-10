# anatomy-rag — 系統架構 Spec

> 解剖學科多模態（圖+文）RAG 問答系統的**單一權威設計來源**。
> 供開發者（人類或 AI agent）在實作前理解設計、約束與決策。本檔不含任務切分；
> 任務切分由實作 agent 規劃，並可於實作中提出修訂建議（走 `decisions.md`）。

---

## 目錄

- [0. 前言](#0-前言)（文件慣例、全域核心約束、變更流程）
- [1. 系統總覽](#1-系統總覽)
- [2. 離線建庫管線](#2-離線建庫管線)
- [3. 資料庫](#3-資料庫)
- [4. 兩階段檢索（核心）](#4-兩階段檢索核心)
- [5. 線上推理](#5-線上推理)
- [6. 工程必要項](#6-工程必要項)
- [7. 評估與品質保證](#7-評估與品質保證)
- [8. 決策總表（DECIDED / OPEN）](#8-決策總表decided--open)
- [9. 不在範圍（future directions）](#9-不在範圍future-directions)
- [附錄 A：環境變數](#附錄-a環境變數清單)
- [附錄 B：repo 結構](#附錄-brepo-結構)
- [附錄 C：合併時發現的矛盾（已裁決）](#附錄-c合併時發現的矛盾已裁決)
- [附錄 D：容量與成本模型](#附錄-d容量與成本模型)

---

# 0. 前言

## 0.1 系統定位

為單一醫學系內部使用、以解剖學參考書或教學講義為基礎的多模態圖文問答系統。學生用中英文提問
→ 視覺檢索匹配的教科書頁面（圖+文）→ 雲端 LLM 基於檢索結果生成**帶強制引文**的串流回答；
教材範圍外的提問一律回「教材中查無此項」而非編造。

## 0.2 文件慣例

| 標記 | 意義 |
|---|---|
| **MUST** | 硬性要求，違反視為實作錯誤 |
| **MUST NOT** | 硬性禁止 |
| **SHOULD** | 強烈建議；違反需於 PR 說明理由 |
| **MAY** | 可選方案 |
| **DECIDED** | 已定案的設計決策，**不應在實作中變更**；如有強烈異議，於 `decisions.md` 提案後再修改 |
| **OPEN** | 留待實作時依實測決定的參數或選項 |
| ⚠️ **待裁決** | 兩處設計互相矛盾（本版合併時發現的 5 項已全數裁決，見 [附錄 C](#附錄-c合併時發現的矛盾已裁決)） |

## 0.3 全域核心約束（任何實作都必須遵守）

**合規與隱私紅線（違反視為實作錯誤）：**

- **MUST** 只用 OpenAI 標準付費 API（platform.openai.com）；**MUST NOT** 使用 ChatGPT 免費版／個人版（預設將輸入用於訓練）。
- **MUST NOT** 處理、傳輸或儲存任何病患可識別資訊（PHI）；HIPAA 不適用（系統內無 PHI）。
- **MUST NOT** 把 user_id／學號等識別資訊放進送往 OpenAI 的 prompt。
- **MUST** 對所有 LLM 輸出強制帶引文（書名簡寫、頁碼、圖號）。
- **MUST** 在每則回應底部顯示「教育用途，內容基於教科書」浮水印。
- **MUST** 將 query log 與向量資料儲存在校內基礎設施(校內server db)。

**工程紅線：**

- **MUST** 一律連 PgBouncer `:6432`，**禁止**直連 Postgres `:5432`（migrations 例外，走 `PG_DIRECT_URL`）。
- **MUST NOT** 將 API key／DB 密碼 hardcode；秘鑰走 `.env`。
- **MUST NOT** 自行變更 `DECIDED` 項目；需先在 `decisions.md` 提案並待人工審核。

## 0.4 變更與決策流程

- 本檔（`ARCHITECTURE.md`）與黃金題庫 `tests/golden_qa.jsonl` 由人工 + agent 共同維護；變更需附 PR 說明。
- 偏離 `DECIDED` 的提案與裁決一律記錄於 `decisions.md`（PROPOSED → APPROVED/REJECTED → 通過後才實作並回寫本檔）。**不要直接改本檔的 DECIDED 項，也不要在 PR 夾帶設計變更。**
- `eval_thresholds.yaml`（RAGAS 門檻）變更需人工審核——防止偷偷降低品質門檻。

---

# 1. 系統總覽

## 1.1 核心能力

- 接受自然語言提問（中英文）
- 檢索匹配的教科書頁面（圖 + 文）
- 由 LLM 基於檢索結果生成回答，每項事實附帶來源引文（書名、頁碼、圖號）
- 串流回應（SSE）
- 對教材範圍外的提問，回應「教材中查無此項」而非編造

## 1.2 範圍

**IN SCOPE**
- 雲端 LLM API 為生成核心（OpenAI 標準付費 API，主 `gpt-5.5` / 備 `gpt-5.4`）
- 單一 PostgreSQL + pgvector 集中式向量庫
- FastAPI 後端（Python）+ Next.js 前端（TypeScript）
- 單一機構（醫學系）內部使用
- 批次離線建庫（管理者上傳 PDF，非學生即時上傳）

**OUT OF SCOPE** — 見 [§9](#9-不在範圍future-directions)：地端 VLM、跨機構 federated 知識庫、學生即時上傳、3D 渲染、語音介面。

## 1.3 合規前提

| 項目 | 規則 |
|---|---|
| PHI（病患可識別資訊） | **MUST NOT** 處理或儲存 |
| HIPAA | 不適用（無 PHI） |
| 個資保護（學生 query log） | 受《個人資料保護法》規範，**MUST** 採付費 API |
| 教科書授權 | 假設由圖書館 / 採購單獨確認；本系統不負責授權合規 |
| ChatGPT 免費版 / 個人版 | **MUST NOT** 使用（預設將輸入用於訓練） |
| 標準付費 OpenAI API | 允許（自 2023 起付費 API 預設不用於訓練；30 天保留供濫用監控） |

## 1.4 端到端架構

**離線建庫管線（書籍變更時批次重跑）**

```
PDF 教科書
  ├─→ Docling 解析 ──→ Markdown + 結構化 metadata
  └─→ 頁面渲染為 PNG ──→ ColPali encoder ──→ 多向量矩陣 (~1024 patches/頁)
                                              │
                                              ├─→ mean-pooling ──→ 單一頁面向量 (halfvec128，DL-019 不二值化)
                                              └─→ patch 二值化 (float32 → bit128)
                                                  │
                                                  ▼
                              PostgreSQL: pages + page_patches
```

**線上推理管線（每次使用者提問）**

```
使用者查詢 (text)
  │
  ▼
ColPali query encoder (校內 GPU 主 / Serverless GPU 備)
  ├─ 中文/混語 query 先經本地 MT 翻成英文（DL-020；並回傳 translated_q 供 BM25）
  ├─→ N 個 query token 向量 (典型 N≈20)
  └─→ mean-pool 得單一查詢向量 (float32)
       │
       ▼
[Stage A] HNSW 在 pages.pooled (halfvec, cosine) 上撈 Top-100 候選頁 + JSONB metadata 預過濾
       │
       ▼
[Stage B] 候選頁的 patches 做 MaxSim → Top-10 → 與 BM25 RRF 融合 → Top-3
       │
       ├─→ (optional) bge-reranker-v2-m3 二次精排
       ▼
取出 page_image_uri (PNG) + docling_md (Markdown)
       │
       ▼
gpt-5.5 (OpenAI 標準付費 API) + 護欄 prompt + 強制引文
       │
       ▼
SSE 串流回前端 (Next.js + Vercel AI SDK)
```

> 關鍵架構：**離線批次建庫**與**線上推理**是兩條獨立路徑；ColPali encoder 為**獨立 GPU 微服務**，後端透過 HTTP 呼叫。

## 1.5 服務拓撲

```
┌─────────────────────────────────────────────────────────────┐
│  瀏覽器  Next.js (Vercel 或 校內 Node :3000)                  │
│  └─ Vercel AI SDK useChat                                     │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS, SSE
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Backend API  (FastAPI :8000, ≥2 uvicorn worker)             │
│  /chat  /healthz  /warmup                                    │
│  依序：SSO 驗證 → rate limit → 語意快取 → 檢索 → LLM         │
└──┬──────────┬────────────┬──────────────────────┬────────────┘
   │          │            │ HTTPS                │ HTTPS
   │          │            ▼                      ▼
   │          │     ┌─────────────────┐    ┌─────────────────┐
   │          │     │ ColPali Encoder │    │ OpenAI API      │
   │          │     │ FastAPI :8001   │    │ gpt-5.5 / 5.4   │
   │          │     │ GPU 校內主      │    │ embedding (副)  │
   │          │     │ Modal 備援      │    │                 │
   │          │     └─────────────────┘    └─────────────────┘
   │          ▼
   │   ┌─────────────────┐
   │   │ Redis :6379     │  - rate limit  - semantic cache
   │   └─────────────────┘
   ▼
┌─────────────────────────────┐      ┌─────────────────────────┐
│ PgBouncer :6432 (txn pool)  │      │ S3 / MinIO              │
│      ↓                       │      │ - 原始 PDF  - 渲染 PNG  │
│ PostgreSQL :5432            │      └─────────────────────────┘
│ + pgvector + pg_trgm        │
└─────────────────────────────┘

旁路（觀測/維運）：LangFuse :3100（校內自託管）、Sentry（雲端）、Prometheus :9090 + Grafana :3001
```

> **DL-006 已裁決**：埠分配 Next.js `:3000`、Grafana `:3001`、LangFuse `:3100`（容器內以 service name 定址本不撞，但對外/同機暴露時統一錯開避免歧義）。

## 1.6 端到端請求時序（單一查詢，p50 預估）

以 `"肱二頭肌的起止點是什麼？"` 為例。p95 目標見 [§1.7](#17-效能預算p95-目標)。

```
T+0     [前端]  POST /chat {query, conversation_id?, metadata_filter?}
T+5ms   [API]   SSO 中介層解 JWT → user_id
T+10ms  [API]   rate limit (Redis token bucket)；超量 → 429
T+30ms  [API]   語意快取查詢（本地 embedding，DL-012）；similarity > 0.95 且同 kb_version → 直接回 cached，結束
T+200ms [Encoder] POST /encode_query → {tokens_bin[~20], pooled_f32, translated_q}
                  (校內 5060 Ti ~50ms / Modal fallback ~200ms；中文 query 含本地 MT +30–80ms，DL-020)
T+230ms [DB]    Stage A: HNSW on pooled (halfvec) + metadata 過濾 → 100 候選 page_ids（DL-013）
T+280ms [DB]    序列（單一連線）：Stage B MaxSim → top10；BM25 tsvector → top50
T+330ms [App]   RRF fusion → final top3
T+400ms [DB+S3] 並行：一次 SQL 撈 3 頁完整 metadata；fetch S3 PNG + sign URL
T+450ms [SSE]   推送 "sources" event (PageCitation[]) → 前端立即顯示引用面板
T+500ms [OpenAI] chat.completions.create(stream=True)，含 inline base64 圖
T+1.8s  [SSE]   第一個 "delta"（LLM TTFT）
T+10s   [SSE]   "done"；並行 asyncio.create_task：寫 query_logs / 寫快取 / LangFuse flush
```

## 1.7 效能預算（p95 目標）

| 階段 | p95 目標 | 監控指標 |
|---|---|---|
| TTFB sources event | < 600ms | `chat.ttfb_sources` |
| TTFT（LLM 首 token） | < 2.5s | `chat.ttft_llm` |
| TT-complete（完整回應） | < 15s | `chat.tt_complete` |
| Encoder（校內主 / Modal 備） | < 100ms / < 300ms（中文 query 含 MT：< 150ms / < 350ms，DL-020） | `encoder.latency` |
| Stage A | < 30ms | `retrieval.stage_a.latency` |
| Stage B（100 候選，DL-013；數值待 Phase 5 壓測校準） | < 200ms | `retrieval.stage_b.latency` |
| BM25 | < 50ms | `retrieval.bm25.latency` |
| S3 fetch（3 圖並行） | < 100ms | `s3.fetch_top3.latency` |
| 快取命中整體 | < 250ms | `chat.cache_hit_latency` |

不達標時的處理見 [§7.5](#75-線上指標與告警)。

## 1.8 錯誤處理矩陣

| 階段 | 失敗類型 | 處理 | 對使用者 |
|---|---|---|---|
| SSO | JWT 無效/過期 | 回 401 | 提示重新登入 |
| Rate limit | 超量 | 回 429 + `Retry-After` | 友善訊息 |
| Cache lookup | Redis 連線失敗 | 視為 miss，記 metric，繼續 | 透明 |
| Encoder primary | timeout > 2s / 5xx | 切 Modal fallback | 透明（多 ~150ms） |
| Encoder 全失敗 | 兩路皆 fail | 推 `error` event `encoder_unavailable` | 「服務忙碌」+ 後端告警 |
| Stage A | 0 results | 改用 BM25-only 路徑 | 透明（trace 標記） |
| Stage B | timeout > 1s | 退回 Stage A 排序的 top3 | 透明（trace 標記） |
| S3 fetch | 任一圖失敗 | 該頁以 placeholder 取代，仍送其他 | 引用面板少一張 |
| LLM primary | timeout/429/5xx | 切 `gpt-5.4` | 透明 |
| LLM 全失敗 | 兩 model 都 fail | 推 `error` event `llm_unavailable` | 「服務異常，請稍候」 |
| Stream 中斷 | client disconnect | `Request.is_disconnected()` → 取消 LLM 生成 | n/a |
| query_logs / cache 寫入 | DB/Redis 寫入失敗 | 記 Sentry/metric，不影響回應 | 透明 |

## 1.9 常見實作陷阱

| 陷阱 | 後果 | 防範 |
|---|---|---|
| 直連 Postgres :5432，不走 PgBouncer | 中等併發即耗盡連線 | 強制 `DATABASE_URL` 指向 :6432 |
| Transaction pooling 下用 prepared statements | 偶發錯誤難 debug | asyncpg + `statement_cache_size=0` |
| 離線/線上 binarize 函式不同步 | 檢索精度大降 | 抽到 `shared/binary.py`，兩端 import（見 [§2.4](#24-二值化壓縮)） |
| SQL `WHERE page_id IN (...)` 預期保序 | 排名亂掉 | Python 端依 RRF 順序重排 |
| ColPali query 編碼忘了 mean-pool | Stage A 沒有 pooled 向量可比 | encoder 服務直接回 tokens_bin + pooled_f32 |
| 圖譜頁用 detail="low"/"auto" | 解剖標籤判讀失準 | 需判讀的圖頁用 `detail:"high"`（非一律；附圖路由見 §5.5/DL-009） |
| LLM 呼叫漏設 `stream=True` | 整段等 10 秒才回 | code review 必檢 |
| `await log_query()` 卡在主串流結束處 | SSE 提早 close 觸發 client retry | 用 `asyncio.create_task` |
| Stage B SQL 沒帶 `kb_version` 過濾 | 版本切換後撈到舊版 | orchestrator 一定帶 kb_version 進去 |
| 把 user_id/學號塞進 LLM prompt | 個資外流到 OpenAI | LLM 呼叫前 strip 敏感欄位 |

---

# 2. 離線建庫管線

> **觸發方式**：管理者命令列工具或排程任務（非 API）。**離線管線 MUST NOT 呼叫任何雲端 LLM API。**

## 2.1 產物總覽

對每本教科書、每一頁產出三類產物：

1. **PNG 截圖**（200 DPI、長邊上限 2048 px）→ 物件儲存（S3/GCS/MinIO）
2. **Markdown 文字**（Docling 結構化抽取）→ `pages.docling_md`
3. **多向量矩陣**（ColPali，每頁 ~1024 個 128-dim 向量）→ 二值化後存 `page_patches.patch_bin`

每頁額外計算：**mean-pooled 向量**（128-dim，以 float16 存 `pages.pooled`，halfvec、不二值化，DL-019）（Stage A 用）；**metadata JSONB** 存 `pages.metadata`。

## 2.2 文字結構化解析（Docling）

- **DECIDED**：採 Docling 為主要文字解析工具（保留 Markdown 階層與表格；批次離線執行，無 API 依賴；表格單元格識別優於 OCR-based 工具）。

每頁解析後須抽取：

| 欄位 | 來源 | 用途 |
|---|---|---|
| `docling_md` | Docling export | LLM 生成階段的文字 payload |
| `book_id` | 上層批次參數 | FK 至 books 表 |
| `page_num` | Docling page metadata | 引文 |
| `chapter` | 從 markdown 標題規則化抽取 | metadata 過濾 |
| `anatomy_system` | 從章節名稱對照表分類 | metadata 過濾 |
| `page_type` | 啟發式分類（pure_text/figure_heavy/table/mixed） | 評估與路由 |

**實作**：`DocumentConverter().convert(pdf).document` 逐頁 `export_to_markdown()`；同頁以 `pdf2image.convert_from_path(..., dpi=200)` 渲染 PNG；組 metadata（`book_title/edition/page_num/chapter/anatomy_system/page_type`）後 `save_to_storage(img, md, metadata)`。

**規則**：
- **MUST** 同時保留 PNG 與 Markdown（兩者送 LLM 時都需要）
- **MUST** 將 metadata 規範化為固定 schema 寫入 `pages.metadata` JSONB（schema 見 [§3.2](#32-schema)）
- **SHOULD** 對表格密集章節做單元格 schema 校驗
- **MUST NOT** 在離線管線中呼叫雲端 LLM API

## 2.3 視覺多向量索引（ColPali）

- **DECIDED 起手版本**：`vidore/colpali-v1.3-hf`
- **OPEN**：未來可評估升級至 ColQwen2.5（向量空間不通用，升級需重建索引）

**契約**：`encode_page(pil_image) -> Tensor[num_patches, 128]`，典型 `num_patches ≈ 1024`（`bfloat16`、`device_map="cuda"`、`@torch.no_grad()`）。

**規則**：
- **MUST** 在 `pages.embed_model` 記錄編碼模型 ID（升級時識別舊資料）
- **MUST** 編碼前將圖像 resize 到模型預期尺寸（由 processor 處理）
- **SHOULD** 採批次處理；每批大小依 GPU VRAM 調整

## 2.4 二值化壓縮

**目的**：每個 128-dim float32 向量（512 bytes）→ `bit(128)`（16 bytes）。32× 儲存壓縮、32× 計算降載（Hamming 比浮點點積快很多），對 ColPali 檢索精度影響有限（典型 recall@5 下降 < 3pp）。

**契約**：`binarize(vec_f32: Tensor[128]) -> bytes(16)`，sign-based（`>0` 取 1，否則 0；bit i 對應 `out[i//8]` 的第 `7-(i%8)` 位）。patch 向量：逐一 `binarize`；pooled 向量＝`patch_embs.mean(dim=0)`，**不二值化**，以 float16 存 `pages.pooled`（halfvec，DL-019；cosine 對縮放不敏感，毋須重新 normalize）。SQL 綁定時 bytes→bit 字串的**唯一轉換點**為 `shared/binary.py` 的 `to_pg_bits()`（PostgreSQL 無 bytea→bit cast，見 [§4.4](#44-stage-b--精排maxsim)）。

**規則**：
- **MUST** patch 向量二值化後寫入 `page_patches`；pooled 向量以 halfvec(128) 寫入 `pages.pooled`（DL-019，不二值化）
- **MUST** 查詢時使用**相同**的二值化函式（離線端與 query 端**共用同一份** `shared/binary.py`，兩端 import；不一致會直接讓檢索精度崩壞）
- **MUST**（v1 預設）`page_patches` 只存 bit 版本，不存 float32。**MAY**（升級路徑，DL-003）當 RAGAS `context_precision < 0.85` 且確認 binary 量化為瓶頸時，另存一份**更高精度的 rescore 表示**供 Stage B 精排——**優先 INT8**（相對 float32 省 4×、相對 binary 品質明顯回升），float32 為次選；啟用須經 RAGAS 與儲存評估並走 `decisions.md`。

## 2.5 寫入資料庫

對應 schema 見 [§3.2](#32-schema)。`ingest_page(conn, page_data)`：先 `INSERT INTO pages(...) RETURNING page_id`，再以 `executemany` 批次插入 `page_patches`。

**規則**：
- **MUST** 寫入時帶 `kb_version`（見 [§6.6](#66-知識庫版本管理)）
- **SHOULD** 整本書放在單一 transaction，失敗則 rollback
- **MUST** patches 採批次插入（單筆 INSERT 數量過多不可行）

## 2.6 執行模式

- 輸入：本地 PDF 路徑 + 書籍 metadata YAML；輸出：寫入 PostgreSQL + 上傳 PNG 到物件儲存
- 重新執行：先 `DELETE FROM pages WHERE book_id = ? AND kb_version = ?` 再重跑

```bash
python -m ingest.cli \
    --pdf /data/books/gray_anatomy_42e.pdf \
    --book-meta /data/books/gray_anatomy_42e.yaml \
    --kb-version 3 --batch-size 8
```

## 2.7 失敗處理

- **MUST** 對單頁失敗保留 partial state 並記錄到 `ingest_errors` 表（schema 由實作 agent 設計）
- **MUST** 提供 `--resume` 旗標從失敗頁繼續
- **SHOULD** 寫入後抽樣校驗（隨機抽 5% 頁面，比對 `pages` 與 `page_patches` 計數）

---

# 3. 資料庫

## 3.1 版本與擴充套件

- **MUST**：PostgreSQL ≥ 16
- **MUST**：pgvector ≥ 0.8（支援 binary vectors 與 HNSW on bit）
- **MUST**：pg_trgm（給 BM25 / 文字相似度副線）

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

## 3.2 Schema

```sql
CREATE TABLE books (
    book_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title      TEXT NOT NULL,
    edition    TEXT,
    isbn       TEXT,
    license    TEXT,                          -- 授權型態，留作稽核
    added_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE pages (                          -- 頁面層：Stage A 與 LLM payload 來源
    page_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id         UUID NOT NULL REFERENCES books(book_id),
    page_num        INTEGER NOT NULL,
    page_image_uri  TEXT NOT NULL,             -- S3/MinIO 路徑（PNG）
    docling_md      TEXT NOT NULL,             -- 結構化文字
    metadata        JSONB NOT NULL DEFAULT '{}',
    pooled          HALFVEC(128) NOT NULL,     -- mean-pooled patch 向量（float16；DL-019 不二值化）
    text_tsv        TSVECTOR
                     GENERATED ALWAYS AS (to_tsvector('simple', docling_md)) STORED,
    kb_version      INTEGER NOT NULL,          -- 見 §6.6
    embed_model     TEXT NOT NULL,             -- 例：'colpali-v1.3-hf'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (book_id, page_num, kb_version)
);

CREATE TABLE page_patches (                    -- 區塊層：Stage B 用
    kb_version  INTEGER NOT NULL,              -- DL-017：分區鍵須在 PK 內
    page_id     UUID NOT NULL,
    patch_idx   INTEGER NOT NULL,
    patch_bin   BIT(128) NOT NULL,
    PRIMARY KEY (kb_version, page_id, patch_idx),
    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
) PARTITION BY LIST (kb_version);              -- DL-010 分區（每 kb_version 一分區）

CREATE TABLE query_logs (                      -- 觀測 / 評估用
    log_id        BIGSERIAL PRIMARY KEY,
    user_id       UUID NOT NULL,
    conversation_id UUID,                      -- DL-021：多輪分組（nullable，僅 logging/UX）
    query_text    TEXT NOT NULL,
    retrieved     JSONB,                       -- top-3 page_ids + scores
    answer        TEXT,
    feedback      SMALLINT,                    -- -1 / 0 / 1
    latency_ms    INTEGER,
    kb_version    INTEGER,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
```

**`metadata` JSONB 規範化欄位**（MUST 至少包含）：

```json
{
  "book_title": "Gray's Anatomy", "edition": 42, "page_num": 812,
  "chapter": "Upper Limb", "anatomy_system": "musculoskeletal", "page_type": "figure_heavy",
  "figures": ["Fig. 7-23", "Fig. 7-24"]
}
```

- `anatomy_system` 列舉：`musculoskeletal, cardiovascular, nervous, respiratory, digestive, urogenital, endocrine, integumentary, lymphatic, special_senses, other`
- `page_type` 列舉：`pure_text, figure_heavy, table, mixed`
- `figures`（optional, string[]）：Docling 抽出的該頁圖說標籤清單（如 `["Fig. 7-23"]`），預設 `[]`，作為前端引用面板 hint。**權威圖號以 LLM 對高解析頁圖的逐句引文為準**（見 [§5.4](#54-system-prompt)），非靠 page-level metadata 鎖定（一頁常含多張圖）。（DL-005）

## 3.3 索引

```sql
-- Stage A 粗排：HNSW on cosine（pooled 為 halfvec，DL-019）
CREATE INDEX pages_pooled_hnsw ON pages USING hnsw (pooled halfvec_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX pages_meta_gin   ON pages USING gin (metadata);   -- metadata 過濾
CREATE INDEX pages_tsv_gin    ON pages USING gin (text_tsv);   -- BM25 / tsvector 副線
CREATE INDEX pages_kb_version ON pages (kb_version);           -- 版本切換查詢
CREATE INDEX query_logs_created ON query_logs (created_at DESC);
CREATE INDEX query_logs_user    ON query_logs (user_id, created_at DESC);
```

**規則**：
- **MUST**：所有對 `pages` 的查詢都帶 `WHERE kb_version = :active`
- **SHOULD**：HNSW 查詢時設 `SET LOCAL hnsw.ef_search = 100`（依 recall 調校）
- `page_patches` 不需額外索引（PRIMARY KEY 已含 `(kb_version, page_id, patch_idx)`，按 page_id 過濾足夠快）
- **MUST（DL-017）**：`page_patches` 的查詢都帶 `WHERE kb_version = :active`（PK 含 `kb_version`、且已按 `kb_version` 分區，見 [§4.4](#44-stage-b--精排maxsim)）
- **SHOULD（DL-010）**：`page_patches` 按 `kb_version`（或 book）**分區**，利於刪除/備份/重建並避免 blue-green 期間 HNSW 過濾撈不滿候選
- **MUST（DL-010）**：容量規劃 **Postgres RAM ≥ 作用中版本 `page_patches` 大小**（row+index overhead ≈ ~100 bytes/patch ≈ ~100MB/千頁書；超出 RAM → Stage B 隨機讀退化）。詳見[附錄 D](#附錄-d容量與成本模型)

## 3.4 PgBouncer 連線池

**MUST** 在 Phase 1 即部署，FastAPI async + 多 worker 沒有連線池會耗盡 Postgres 連線。

```ini
[databases]
anatomy_rag = host=postgres port=5432 dbname=anatomy_rag

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
reserve_pool_size = 5
server_idle_timeout = 600
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
```

**規則**：
- **MUST**：FastAPI 連到 PgBouncer 6432，**不可**直連 Postgres 5432
- **MUST**：`pool_mode = transaction`（不是 session）
- **MUST NOT**：在應用層使用 `LISTEN/NOTIFY`、prepared statements、temp tables（與 transaction pooling 不相容）；asyncpg 需設 `statement_cache_size=0`
- **MUST**（DL-012）：DB 連線**不得跨 LLM 串流持有**；`retrieve()` ＋圖片 fetch 完成即歸還（否則 25 連線在班級突發下約 1.6 QPS 即耗盡）
- **SHOULD**：用 asyncpg 或 psycopg[pool]

## 3.5 Migrations

- **MUST**：使用 Alembic 或同等工具管理 schema 變更，每次變更都要有可逆 migration（含 `downgrade`）
- **MUST NOT**：手動 ALTER TABLE 生產環境
- 編號規範：`backend/db/migrations/001_initial_extensions.sql`、`002_books_table.sql`…（遞增）

## 3.6 備份與還原

- **SHOULD（DL-010）**：immutable 的 patch 資料不做每日全量 `pg_dump`；改 snapshot + 可重現 ingest 產物（向量資料量大，需對應儲存空間）
- **SHOULD**：物件儲存（PNG）獨立備份
- **SHOULD**：每次知識庫版本切換前做手動 snapshot

---

# 4. 兩階段檢索（核心）

> 本系統最關鍵的技術設計。

## 4.1 為何兩階段

ColPali 一頁約 1024 個 patch 向量；千頁教科書 ≈ 100 萬 patch，多本擴展到數百萬。直接對所有 patch 做 query token × patch 全表 MaxSim（cross join），即使有 binary quantization 也無法滿足 < 1 秒互動延遲。pgvector 核心至 2026 仍**無原生 multi-vector MaxSim**；本系統有兩條 **in-Postgres** 路徑（DL-007），皆藏在 [§4.7](#47-模組介面契約) 介面後可互換：

1. **應用層自建兩階段（v1 baseline）**：v1 先實作並完整測試此路徑當可靠 baseline（DL-014）。
   - **Stage A 粗排**：mean-pooled 單一向量 + HNSW，快速撈 Top-K 候選頁（K=100 起手，DL-013）
   - **Stage B 精排**：只在 K 個候選頁的 patches 上做完整 MaxSim，取 Top-N
2. **VectorChord 擴充（Phase 12 PoC）**：提供原生、高效 MaxSim（decomposed MaxSim，受 XTR-WARP 啟發），採用後可省掉自建兩階段；列為 §4.7 介面後的 PoC（Phase 12），以 recall@K + p95 + 運維實測勝出才切換（DL-014）。

## 4.2 Query 端編碼契約

encoder service 回傳 `{"tokens_bin": [bytes_16, ...N], "pooled_f32": float32[128], "translated_q": str|null, "lang": str}`：`pooled_f32` 給 Stage A（DB 端以 halfvec 比對，DL-019）、`tokens_bin` 給 Stage B、`translated_q` 給 BM25（DL-013/DL-020）。完整部署與翻譯規則見 [§5.1](#51-colpali-query-encoder-部署)。

- **MUST**：query 端的 binarize 函式與離線端必須相同（見 [§2.4](#24-二值化壓縮)）

## 4.3 Stage A — 粗排

```sql
-- :query_pooled halfvec(128)（encoder 回傳之 pooled_f32；以 pgvector 的 HalfVector／'[...]'字串綁定，DL-019）
-- :metadata_filter jsonb(nullable)  :kb_version int  :top_k int(起手100, DL-013)
SELECT page_id, pooled <=> :query_pooled AS distance        -- <=> = cosine distance
FROM pages
WHERE kb_version = :kb_version
  AND (:metadata_filter IS NULL OR metadata @> :metadata_filter)
ORDER BY pooled <=> :query_pooled
LIMIT :top_k;
```

**規則**：
- **MUST**：每次查詢前 `SET LOCAL hnsw.ef_search = 100`（OPEN：依實測調校）
- **SHOULD**：對 query 做 metadata 推斷（例「上肢神經」→ `{"anatomy_system": "nervous"}`）縮小候選空間
- **MUST NOT**：返回未帶 `kb_version` 的查詢

## 4.4 Stage B — 精排（MaxSim）

`score(page) = Σ_{query_tokens t} max_{patches p of page} similarity(t, p)`

```sql
-- :candidate_page_ids uuid[](來自 Stage A)  :kb_version int  :top_n int(起手10)
-- :query_tokens_bits text[]：每元素為 128 字元 '0'/'1' 字串（MSB-first），由 shared/binary.py 的
--   to_pg_bits() 自 16-byte token 轉出。PostgreSQL **沒有 bytea→bit 的 cast**（直接 ::bit 會報錯）；
--   bytes→bit 字串的轉換 MUST 集中於 to_pg_bits()（與 binarize 同檔，鎖定 MSB-first 位序約定）。
--   MAY：asyncpg 亦可用 asyncpg.BitString 綁定 bit(128)[] 免文字轉換，位序仍以 to_pg_bits 為準。
WITH query_tokens AS (
    SELECT token_idx, q_bits::bit(128) AS q_bin
    FROM unnest(:query_tokens_bits::text[]) WITH ORDINALITY AS qt(q_bits, token_idx)
),
token_max_per_page AS (
    SELECT pp.page_id, qt.token_idx,
           MAX(128 - (pp.patch_bin <~> qt.q_bin))::float AS sim
    FROM page_patches pp
    JOIN query_tokens qt ON true
    WHERE pp.page_id = ANY(:candidate_page_ids)
      AND pp.kb_version = :kb_version          -- DL-017：page_patches 已按 kb_version 分區，PK 含 kb_version
    GROUP BY pp.page_id, qt.token_idx
)
SELECT page_id, SUM(sim) AS maxsim_score
FROM token_max_per_page
GROUP BY page_id
ORDER BY maxsim_score DESC
LIMIT :top_n;
```

- `<~>` 為 pgvector Hamming distance operator（值越小越相似）；`128 - distance` 轉相似度。若版本未提供此 operator，可改用 `bit_count(a # b)`（XOR 後 popcount）。

**規則**：
- **MUST**：Stage B 只接受 Stage A 回傳的 `candidate_page_ids`，不可全表掃描
- **MUST（DL-017）**：Stage B 查詢 MUST 帶 `kb_version = :active`（`page_patches` 已按 `kb_version` 分區、PK 含 `kb_version`）；由 orchestrator 將作用中 `kb_version` 傳入
- **SHOULD**：監控 p95 latency，目標 < 200ms（K=100、N≈20 query tokens；DL-013 起手值，數值於 Phase 5 壓測校準。若 SQL 聚合不達標，**MAY** 在 §4.7 介面後改以應用層 numpy XOR+popcount 計算 MaxSim——撈出候選頁 patch_bin（K=100 約 1.6MB）後向量化計算，仍屬「應用層自建兩階段」範圍）
- **MAY**：若 binary 精度不足（RAGAS context_precision < 0.85），Stage B 改用**更高精度 rescore**（優先 INT8，float32 次選；需另存對應精度的 patch 向量）。詳見 [§2.4](#24-二值化壓縮) 升級路徑（DL-003）。

## 4.5 文字檢索副線（BM25 / tsvector）+ RRF

對純文字提問（如「肱二頭肌的起止點」），ColPali 視覺檢索不一定優於文字檢索，故並行一條 BM25 副線並以 RRF 融合。

```sql
SELECT page_id, ts_rank_cd(text_tsv, plainto_tsquery('simple', :q)) AS rank
FROM pages
WHERE text_tsv @@ plainto_tsquery('simple', :q) AND kb_version = :kb_version
ORDER BY rank DESC LIMIT 50;
```

```python
def rrf_fuse(rank_lists: list[list], k: int = 60) -> list[tuple]:
    """rank_lists: 每個 list 按相關性遞減；回傳融合後 (page_id, score) 按 score 遞減。"""
    scores: dict = {}
    for ranks in rank_lists:
        for rank, pid in enumerate(ranks):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])
```

**規則**：
- **MUST（DL-013）**：中英混合 query 時，BM25 餵 **encoder 回傳的 `translated_q`**（DL-020 本地 MT；tsvector 為英文，原始中文 query 會空轉）；`translated_q` 為 null（MT 失敗）時退回原 query 並 trace 標記；原始混語 query 保留給生成
- **預設序列執行**（DL-002）：Stage B → BM25 依序 await（單一 PgBouncer 連線不可併發）；**MAY** 借第二條連線恢復並行（見 [§4.7](#47-模組介面契約)）
- **MAY**：依 query intent 動態調整融合權重（例純文字題給 BM25 較高權重）

## 4.6 可調參數總表（OPEN）

| 參數 | 起手值 | 調校方法 |
|---|---|---|
| Stage A Top-K | **100（起手，DL-013）** | Stage B 成本與語料大小無關，提高 Top-K 是便宜的 recall 保險；用黃金題庫量 recall@K 調整 |
| HNSW `m` | 16 | 起手不動 |
| HNSW `ef_construction` | 64 | 起手不動 |
| HNSW `ef_search` | 100 | 依召回率調 |
| Pooling 策略 | mean | 召回不理想時試 max / attention-weighted |
| Stage B 精度 | binary | 不足時升 INT8 rescore（優先）或 float32（次選），見 §2.4（DL-003） |
| RRF `k` | 60 | 標準值 |
| Reranker | 不啟用 | RAGAS faithfulness < 0.85 時引入 bge-reranker-v2-m3 |

## 4.7 模組介面契約

實作 agent **MUST** 建立以下函式介面，便於替換內部實作。

```python
# backend/retrieval/types.py
@dataclass
class RetrievalResult:
    page_id: UUID
    score: float            # RRF 融合分數
    book_title: str
    edition: str | None
    page_num: int
    page_image_uri: str     # S3 / MinIO 路徑（內部）
    docling_md: str
    metadata: dict          # JSONB metadata，含 figure 等

# backend/retrieval/
async def stage_a_coarse(conn, query_pooled: "Sequence[float]", metadata_filter: dict | None,
                         kb_version: int, top_k: int = 100) -> list[UUID]: ...  # DL-013: 起手 100；halfvec（DL-019）
async def stage_b_maxsim(conn, candidate_page_ids: list[UUID], query_tokens_bin: list[bytes],
                         kb_version: int, top_n: int = 10) -> list[tuple[UUID, float]]: ...  # DL-017: 帶 kb_version
async def bm25_search(conn, query: str, kb_version: int, top_k: int = 50) -> list[UUID]: ...
def rrf_fuse(rank_lists: list[list[UUID]], k: int = 60) -> list[tuple[UUID, float]]: ...

# backend/retrieval/orchestrator.py — 主入口
async def retrieve(conn, query: str, encoder_result: dict, metadata_filter: dict | None,
                   kb_version: int, top_n: int = 3) -> list[RetrievalResult]:
    candidate_ids = await stage_a_coarse(
        conn, encoder_result["pooled_f32"], metadata_filter, kb_version)   # DL-019
    # DL-002：序列執行，避免在單一 PgBouncer 連線上併發（asyncpg 禁止同連線併發）
    stage_b_res = await stage_b_maxsim(conn, candidate_ids, encoder_result["tokens_bin"], kb_version)  # DL-017
    bm25_q = encoder_result.get("translated_q") or query                   # DL-013/DL-020
    bm25_res = await bm25_search(conn, bm25_q, kb_version)
    fused = rrf_fuse([[pid for pid, _ in stage_b_res], bm25_res])
    final_ids = [pid for pid, _ in fused[:top_n]]
    final_scores = dict(fused[:top_n])
    rows = await conn.fetch("""
        SELECT p.page_id, p.book_id, p.page_num, p.page_image_uri,
               p.docling_md, p.metadata, b.title AS book_title, b.edition
        FROM pages p JOIN books b USING (book_id)
        WHERE p.page_id = ANY($1::uuid[])""", final_ids)
    rows_by_id = {r["page_id"]: r for r in rows}      # SQL 不保證 IN 順序
    return [RetrievalResult(page_id=pid, score=final_scores[pid],
                            book_title=rows_by_id[pid]["book_title"],
                            edition=rows_by_id[pid]["edition"],
                            page_num=rows_by_id[pid]["page_num"],
                            page_image_uri=rows_by_id[pid]["page_image_uri"],
                            docling_md=rows_by_id[pid]["docling_md"],
                            metadata=rows_by_id[pid]["metadata"]) for pid in final_ids]
```

> **DL-002（原 P0）已裁決**：Stage B 與 BM25 在單一 `conn` 上**序列執行**（asyncpg 禁止同連線併發）。在 ~10k/月、Stage B(<200ms)+BM25(<50ms) 的延遲預算下，序列化省下的 ~50ms 不值得為每個請求多借一條 PgBouncer 連線。**MAY**：若日後延遲預算吃緊，再用 `async with pool.acquire() as conn2` 給 BM25 另一條連線以恢復並行。

**實作建議**：
- Stage B 取 top 10 而非 top 3（給 RRF 融合更多空間）
- 最終 metadata fetch 必須以單一 SQL 撈出（不要 N+1）
- 保持 RRF 排序：SQL `WHERE page_id IN (...)` 不保證順序，必須在 Python 重排

## 4.8 測試契約

> **DL-010 / DL-014**：v1 以自建兩階段為 baseline 並完整實作下列 Stage A/B 測試；VectorChord 為 §4.7 介面後的 **Phase 12 PoC**，於其以 recall@K + p95 + 運維實測勝出而切換時另補對應測試（DL-014 已將排序定為「先做 baseline、VectorChord 後 PoC」，取代 DL-010 原「先 PoC 通過即只做它」）。

實作 agent **MUST** 提供：
- `test_stage_a.py`：100 頁假資料，驗證 HNSW 撈出預期 Top-K
- `test_stage_b.py`：5 頁候選，驗證 MaxSim 排序與手算一致
- `test_rrf.py`：已知排名輸入，驗證融合結果
- `test_orchestrator.py`：端到端整合測試，使用 5 題迷你黃金題庫

---

# 5. 線上推理

## 5.1 ColPali Query Encoder 部署

每次查詢都需 GPU 推理，**MUST NOT** 用 CPU fallback。部署為獨立 FastAPI 微服務。

| 選項 | 描述 | 用途 |
|---|---|---|
| A | 校內 GPU 服務（RTX 5060 Ti 16GB 或同級） | 主路徑（DECIDED） |
| B | Modal serverless GPU（L4 級，`keep_warm=1`） | Fallback / 備援 |
| C | RunPod / Lambda Labs 常駐 GPU | 不採用（閒置成本高） |

```
POST /encode_query
Request:  {"q": "肱二頭肌的起止點"}
Response: {"tokens_bin": ["base64...", ...],             // N 個 16-byte bit(128) token（base64）
           "pooled_f32": "base64(512B LE float32[128])", // Stage A 用（DB 端 halfvec，DL-019）
           "translated_q": "origin and insertion of biceps brachii",  // DL-020；英文 query 為原文、MT 失敗為 null
           "lang": "zh", "model": "colpali-v1.3-hf", "mt_model": "opus-mt-zh-en"}
```

主後端 client（含 fallback）：`ColPaliClient.encode_query(text, timeout=2.0)`——先打 primary（timeout 2s），`httpx.TimeoutException/HTTPError` 時改打 fallback（timeout 10s）。

**規則**：
- **MUST**：primary 失敗自動 fallback；不可讓使用者看到 503
- **MUST**：encoder 服務做 readiness check（模型載入完成才回 healthy）
- **MUST**：query 與離線 ingest 必須使用相同的 binarize 函式
- **SHOULD**：encoder 服務於 FastAPI startup hook 做一次 dummy encoding 預熱

**跨語言查詢翻譯（DL-020，補強 DL-008/DL-013）**：

- 翻譯內建於 encoder 服務的 `/encode_query` 管線：偵測語言（query 含 CJK 字元即需翻譯）→ **本地 MT**
  翻成英文 → 以英文做 ColPali 編碼；回傳 `translated_q`（供 BM25）與 `lang`。英文 query 為 identity。
- **DECIDED 起手引擎**：`Helsinki-NLP/opus-mt-zh-en`（本地、零 API 成本、CPU 數十 ms 級）。
  **MUST NOT** 用雲端 API 做查詢翻譯（成本／延遲／離線可用性）。
- **SHOULD**（Phase 3 實測）：解剖術語 glossary 長詞優先替換；query 內既有 ASCII/拉丁術語 span 保護不送 MT。
- **失敗 fallback**：MT 例外 → 以原文編碼、`translated_q=null`、trace 標記 `mt_failed`，不阻斷查詢。
- **MUST**：Modal fallback 映像內建同一 MT 模型（主／備契約一致）。MT 路徑品質以 DL-013
  recall@K by question-class（含中文 query）gate；不達標升級序＝更強本地 MT（NLLB-600M）→
  跨語言 encoder（DL-008 (b)）→ 雲端翻譯（最後選項，涉費用須另走 `decisions.md`）。
- mock 模式：決定性 identity 翻譯 + CJK 偵測（`mt_model="mock-identity"`）。

## 5.2 中間層 Reranker（選配，預設不啟用）

對 Top-K 做文字 cross-encoder reranking：模型 `BAAI/bge-reranker-v2-m3`、輸入 `(query_text, page_md)` pairs、額外延遲 50–150ms。

**啟用條件（OPEN）**：RAGAS `faithfulness` < 0.85，或人工抽檢發現 Top-1 命中率不足。實作 agent **MAY** 實作但預設關閉，由設定檔控制。

## 5.3 LLM 生成

| 角色 | 模型 | 部署 |
|---|---|---|
| 主 | `gpt-5.5` | 標準付費 OpenAI API |
| 備援 | `gpt-5.4` | 同一 OpenAI 帳號 |

- `gpt-5.5` 為 2026 中 OpenAI 最新旗艦（視覺與推理品質最強）；`gpt-5.4` 作為偶發 timeout/5xx 的快速 fallback。
- v1 範圍只做**模型級** fallback；未來若需 **vendor 級** fallback（OpenAI 全面中斷）**MAY** 加入第二 vendor（Anthropic/Gemini）。
- **成本優化（OPEN，DL-009 成本槓桿之一）**：對純文字、簡單概念題路由到較小模型（需 query intent classifier + RAGAS 驗證）。

抽象層 `LLMClient.stream_complete(system, user, images: list[bytes]) -> AsyncIterator[str]`；`ModelFallbackClient` 維護 `consecutive_errors`，連續 3 次 `(TimeoutError, RateLimitError, ServerError)` 後切 fallback model。

**規則**：
- **MUST**：用 `tenacity` 或 `backoff` 包 retry（exponential backoff + jitter）
- **MUST**：連續 3 次 5xx 或 429 自動切換到備援模型
- **MUST NOT**：在請求中傳遞 user_id 或學號給 OpenAI API
- **SHOULD**：監控 RPM/TPM，接近 80% 時告警

## 5.4 System Prompt

```
你是一位協助醫學系學生學習解剖學的助教。使用者皆為醫學相關科系學生，具備基本醫學素養。

【行為準則】
1. 僅能基於下方提供的「教科書摘錄」與「教科書頁面圖像」回答。
2. 若提供的資料不足以回答，明確說「教材中查無此項」，不得編造。
3. 每一項事實後面都必須附帶引文，格式為 [書名簡寫, 頁碼, 圖號（若有）]，
   例如：肱二頭肌起於肩胛骨喙突 [Gray42, p.812, Fig.7-23]。
4. 回答風格：簡潔、條列、優先使用教科書原文用語。可包含教科書中的臨床
   correlation（如手術解剖、神經損傷風險、病理機轉），但不主動延伸至診斷
   或治療建議；如使用者明確要求，可在引文範圍內回答。

【教科書摘錄】{docling_markdown_top3}
【教科書頁面圖像】{page_images_top3}
【使用者問題】{user_query}
```

**規則**：
- **MUST**：System prompt 用版本化常數管理（不可雜在程式碼）
- **MUST**：對 LLM 輸出做引文**格式 + 真實性驗證**（DL-012）——格式 regex；且 cited book/page 對照 retrieved top-3、figure 對照 `figures[]`，無法佐證者移除/重生/標示未驗證。未含引文於前端顯示警告 banner
- **SHOULD**：System prompt 變更時將舊版保留至少 30 天供 A/B 比對
- **MUST NOT**：在 prompt 或應用層加入「拒答臨床問題」的硬性規則（使用者皆為醫學相關科系學生）；理由見 [§6.7](#67-醫學教育免責-ux)、[§9](#9-不在範圍future-directions)

## 5.5 OpenAI 多模態呼叫

LLM 呼叫走原生 `openai` SDK（型別清楚、debug 直接）；檢索編排由 `backend/retrieval/orchestrator.py` 自理（**DL-015 已移除 LlamaIndex**，線上路徑不採 RAG 框架）。核心：

```python
image_contents = [{"type": "image_url",
    "image_url": {"url": f"data:image/png;base64,{b64(img)}", "detail": "high"}}
    for img in image_bytes_list]                       # 解剖圖譜需高解析
messages = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [{"type": "text", "text": f"...{text_context}...{user_query}"},
                                         *image_contents]}]
stream = await client.chat.completions.create(
    model=os.environ["OPENAI_MODEL_PRIMARY"], messages=messages,
    stream=True, max_tokens=1500, temperature=0.2)     # 醫學事實型偏低 temperature
```

**規則 / 建議**：
- **影像為條件式附帶（default，DL-009）**：依 `page_type` + query intent 路由——純文字題只送 `docling_md`、**不送圖**；圖譜題只對 figure_heavy/mixed 頁送圖、**預設 top-1（最多 2）**而非固定 3。此為最大成本槓桿（影像約占輸入 token 47%）。
- **`detail: "high"`** 用於需判讀標籤的圖頁（標籤、引導線、小字體用 low/auto 易失準）；非圖譜頁不送圖，故不再「一律 high」。v2 評估 figure bbox 裁切（保標籤清晰又大砍 token）。
- 影像存 web-optimized JPEG/WebP derivative、避免 inline base64 膨脹（+33%）。
- `temperature=0.2` 適合醫學事實型；太僵硬可調 0.3–0.4。`max_tokens=1500` 對單題足夠
- **不要**用 `response_format={"type":"json_object"}`（與 streaming 不易相容）
- **觀測**：呼叫前後埋 LangFuse span（model、prompt/completion token、TTFT）

## 5.6 SSE 串流回應

LLM 完整生成需 5–15 秒，**MUST** 串流。`/chat` 回 `EventSourceResponse(event_stream())`，流程：

1. 限流兜底 → 2. 語意快取查詢（**本地 embedding**，命中直接 yield `sources`+`delta`+`done` 並 `create_task` log，return；**追問跳過快取**，DL-021）
3. 編碼 query（encoder 內含 DL-020 翻譯；追問先依 [§5.9](#59-多輪對話dl-021) 串接 retrieval_q）→ 4. 兩階段檢索 + RRF（`retrieve(...) -> list[RetrievalResult]`）；**檢索＋圖片 fetch 完成即歸還 DB 連線，不得跨 LLM 串流持有（DL-012）**
5. 依 page_type/intent **條件式** fetch 圖片並轉 PageCitation（純文字題可不送圖；圖譜題 top-1~2，DL-009）→ 6. **先送 `sources` event**（前端立即顯示引用）
7. 串流 LLM（`async for delta in generate_answer(...)`；例外 yield `error` event 並 raise 給 Sentry）
8. yield `done` → 9. 非同步副作用：`asyncio.create_task(log_query(...))`、`create_task(semantic_cache.set(...))`；並做**引文真實性驗證**（cited page/figure 對照 retrieved/figures[]，DL-012）

| Event | Data | 用途 |
|---|---|---|
| `sources` | JSON array of `PageCitation` | 前端顯示引用頁面（在 LLM 開始輸出前送出） |
| `delta` | token 文字 | 串流 LLM 輸出 |
| `done` | empty | 結束標記 |
| `error` | JSON `{message, code}` | 錯誤事件，前端顯示友善訊息 |

**規則（不可違反）**：
- **MUST**：`sources` 必須在第一個 `delta` 之前送出
- **MUST**：前端用 Vercel AI SDK `useChat`、**不自寫** SSE/狀態；後端產生事件用集中的薄 emitter（`backend/api/ai_stream.py`，見下方 DL-018），不自行散落手刻
- **MUST**：後端 timeout ≥ 60 秒
- **MUST**：寫 query_logs 與寫 cache 用 `asyncio.create_task`，不阻塞 SSE 結束
- **MUST NOT**：在 SSE 連線中做大量同步 IO（會卡死整個串流）
- **SHOULD**：client disconnect 時用 `Request.is_disconnected()` 提前取消 LLM 生成（省 token）

> **DL-018**：AI SDK v5/v6 用 **UI Message Stream 協定**（typed parts over SSE），無官方 Python lib → 後端**手刻薄 emitter**（`backend/api/ai_stream.py`）為『不自寫』的**核准例外**（前端仍用 `useChat`、不自寫 SSE/狀態）。事件對應：`sources`→自訂 **`data-sources` part**（前端 **`onData`**，非舊 `onResponse`）；HTTP header `x-vercel-ai-ui-message-stream: v1`；以 `data: [DONE]` 收尾。

## 5.7 API Schema

```python
class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    metadata_filter: dict | None = None     # 例：{"anatomy_system": "musculoskeletal"}
    conversation_id: UUID | None = None
    # DL-021：實際 wire 請求為 AI SDK useChat 的 messages 形狀；後端正規化為本 schema
    # （query=當前訊息；追問時另攜前一問，見 §5.9），只讀最後兩則 user 訊息。

class PageCitation(BaseModel):              # 前端可見，由 RetrievalResult 衍生
    book_title: str
    edition: str | None = None
    page: int
    figure: str | None = None              # hint：取 metadata.figures 首項（見 §3.2, DL-005）；權威圖號來自 LLM 逐句引文
    image_url: str                         # 已 sign 過、前端可直接 <img>
    snippet: str                           # 取 docling_md 前 200 字
    score: float
```

`build_citations_and_images(results) -> (list[PageCitation], list[bytes])`：並行 `sign_url`（前端用）與 `fetch_bytes`（LLM 吃的原圖）；`figure`＝`metadata.figures` 首項（hint，可為 None）、`snippet=r.docling_md[:200]`。`RetrievalResult` 定義見 [§4.7](#47-模組介面契約)。

## 5.8 認證

> **DL-016**：v1 校內 SSO **暫緩**，以**可插拔 auth**（`backend/api/auth.py` 的 `get_current_user`：dev 注入固定 `user_id` stub、production 留 OIDC 介面與設定文件化）替代；下列 SSO **MUST** 於**接回校內 SSO 時生效**。`user_id` / 限流 / `query_logs` 照常運作（dev stub 提供 `user_id`）。

- **MUST**：所有 `/chat` 請求需通過校內 SSO（OAuth2 / SAML 整合）
- **MUST**：query_logs 記錄 user_id（用於限流與抽檢）
- **MUST NOT**：將 user_id 或學號傳給 LLM API

## 5.9 多輪對話（DL-021）

v1 原則：**後端無會話狀態、零額外 LLM token**。`conversation_id` 僅用於 query_logs 分組與前端 UX。

- **請求契約**：`/chat` 接受 AI SDK `useChat` 的 messages 形狀，但後端 **MUST 只讀取最後兩則 user
  訊息**（當前問題＋前一問）；其餘歷史 **MUST NOT** 進入任何 LLM payload（防止把全史塞進 prompt）。
- **追問判定（純規則、零 LLM 成本；規則 OPEN，Phase 8 以追問型測例調校）**：當前訊息含指代詞
  （它／其／這／那／該／this／it／that…）或長度 < 8 字 → 視為追問。
- **檢索**：追問時 `retrieval_q = 前一則 user 訊息 + "\n" + 當前訊息`（不含 assistant 回答）；否則＝
  當前訊息。翻譯（DL-020）、encode、BM25 皆以 `retrieval_q` 進行（encoder 為本地服務，串接零成本）。
- **生成**：追問時【使用者問題】帶前一問（「前一問：…／當前追問：…」）；**MUST NOT** 帶歷史回答與
  先前檢索內容（token 增量僅前一問文字 ~30–80 tokens、只在追問時發生）。
- **快取**：追問 **MUST NOT** 查或寫語意快取（答案依賴上下文，快取必錯）；非追問照 [§6.4](#64-redis-語意快取)。
- **OPEN（涉 token 費用，啟用須走 `decisions.md`）**：(a) 生成附最近 1–2 輪完整 Q/A（估 +500–1500
  input tokens/追問）；(b) LLM query-rewrite/condense（+1 次小模型呼叫/追問）。皆 MUST 經 RAGAS＋成本評估。
- 黃金題庫 **SHOULD** 於 Phase 11 增少量追問型案例驗證串接規則。

---

# 6. 工程必要項

> 八項「不論規模多小都必須具備」的工程要件；任一缺失都會在實際使用 1–2 週內暴露問題。**不可**因「Phase 1 規模小」而省略。

## 6.1 API Rate Limit 與 Model Fallback

OpenAI 標準付費 API 有 RPM/TPM 限額，課堂同步使用會撞限額。

- **MUST**：申請/提升 tier 用「峰值併發 × 平均 token / 分鐘」估算，不要用日均
- **MUST**：實作模型 fallback 抽象層（見 [§5.3](#53-llm-生成)）；連續 3 次 5xx/429 自動切 `gpt-5.5 → gpt-5.4`
- **MUST**：用 `tenacity` 包 retry（exponential backoff + jitter）
- **SHOULD**：監控 RPM/TPM，接近 80% 告警並考慮升 tier

## 6.2 Cold Start 處理

- **SHOULD（DL-011）**：Modal fallback 預設 **scale-to-zero**（不 `keep_warm`）——主路徑為校內 GPU、fallback 罕用，常駐 L4 ~$800/月不划算；冷啟靠下方 `/warmup`+readiness 緩解。僅實測證明故障切換需求值得時才開 keep_warm
- **MUST**：FastAPI startup hook 對整條 pipeline 做一次 dummy query 預熱
- **SHOULD**：前端首次連線送 `/warmup`（背景執行，不阻塞使用者）
- **SHOULD**：encoder 服務 readiness probe 須等模型載入完成才回 200

## 6.3 串流回應（SSE）

設計與規則見 [§5.6](#56-sse-串流回應)。不可違反的關鍵點：`sources` 必在第一個 `delta` 前送出；前端用 Vercel AI SDK `useChat`、不自寫；SSE 連線中不做大量同步 IO。**DL-018**：協定為 AI SDK v5/v6 的 UI Message Stream（typed parts over SSE），後端以集中薄 emitter（`backend/api/ai_stream.py`）產生事件（`sources`→`data-sources` part、前端 `onData`；header `x-vercel-ai-ui-message-stream: v1`；`data: [DONE]` 收尾），詳見 [§5.6](#56-sse-串流回應)。

## 6.4 Redis 語意快取

醫學教育場景相同/相似問題重複率高（考前同問），語意快取可省 30–50% LLM 呼叫。

```
新查詢 q → 本地輕量 embedding 編碼成 v_q → Redis 最近鄰 (cosine)
  → sim > 0.95 且同 kb_version：回上次答案 + 標記 cache_hit
  → 否則正常執行，完成後寫入 (v_q, query, answer, sources, kb_version)
```

骨架：`SemanticCache(redis, embed_fn, threshold=0.95, ttl=86400*14)`，`get(query, kb_version)` / `set(query, answer, sources, kb_version)`。

**規則**：
- **MUST**：cache key namespace 含 `kb_version`，版本切換時不混用；knowledge base 版本變更時清空所有舊版快取
- **MUST**：cache TTL 7–30 天
- **MUST**：cache hit 時仍寫入 query_logs（標記 cache_hit=true）
- **SHOULD**：使用 `redisvl`（Redis 官方 vector library）
- **MUST**（DL-004 + DL-012）：**線上 cache lookup 用本地輕量 embedding**（避免 OpenAI 往返破壞 §1.6 的 ~30ms 命中預算）；`text-embedding-3-small` 可作離線/評估用。命中率/precision 上線後實測，初期用 exact-normalized-query 快取較安全

## 6.5 觀測性

> **v1 範圍（DL-011）**：先上 **LangFuse（trace）+ Sentry（錯誤）**；Prometheus/Grafana 待有明確需求或量起來再加（自託管 LangFuse 本身已是一組 stack，三套齊上對 v1 過重）。

| 層級 | 工具 | 內容 |
|---|---|---|
| Trace | LangFuse（自託管） | 全鏈路：encoding → Stage A/B → reranker → LLM → token 用量 |
| Metrics | Prometheus + Grafana | RPS、p50/p95/p99 latency、各 vendor 錯誤率、cache hit rate、token 用量 top-10 user |
| Errors | Sentry | FastAPI 例外、未處理錯誤 |

**規則**：
- **MUST**：每筆 trace 含 `user_id`（不傳給 LLM，但要能追蹤）
- **MUST**：Sentry 接 FastAPI 例外；Grafana alerts 接 Slack（連續錯誤、quota > 80%）
- **使用者回饋**：**MUST** 前端每則回答提供 👍/👎 + 文字回饋，寫入 `query_logs.feedback`；**SHOULD** 每週由助教檢視所有 👎 案例

## 6.6 知識庫版本管理

教科書改版、補圖、ColPali 模型升級時都需重建索引。機制：`pages.kb_version` + `pages.embed_model` 標記每筆資料；應用層透過 `settings.ACTIVE_KB_VERSION` 控制當前服務版本；Blue-Green 切換（新版寫入後切 `ACTIVE_KB_VERSION`，舊版保留 N 週）。

**規則**：
- **MUST**：所有 `pages` 查詢帶 `WHERE kb_version = :active`
- **MUST**：版本切換流程（離線重建 → staging 評估 → 切流量 → 觀察 → 砍舊版）寫入 runbook
- **MUST**：版本切換前在 staging 跑完整 RAGAS，新版指標達標才切
- **MUST**：版本切換後立即清空語意快取
- **SHOULD（DL-011）**：雙版本並存窗縮為「canary + 短回滾期（數天）」，或舊版降 cold（不佔常駐 RAM）；非固定 4 週常駐（#書成長時雙版本＝patch 儲存與 RAM ×2）

切換流程：`ingest.cli --kb-version 4` → `eval.ragas --kb-version 4` → 改 `ACTIVE_KB_VERSION=4` → `redis FLUSHDB` → 觀察 1 週 → `DELETE FROM pages WHERE kb_version = 3`。

## 6.7 醫學教育免責 UX

使用者皆為醫學相關科系學生，採「**輕量提醒，不阻擋功能**」策略。

- **MUST**：首次進入聊天室強制顯示免責同意視窗（教育用途、系統可能出錯應自行驗證、查詢日誌會被儲存供品質改善），同意後寫入持久化
- **MUST**：每次回答底部固定顯示引文清單與「教育用途，內容基於教科書」浮水印
- **MUST**：使用者首次倒讚醫學內容時，提示回報機制
- **MAY（預設關閉）**：對第一人稱症狀類提問做 query log 標記（`clinical_flavored=true`）便於抽檢優先檢視，**不阻擋回應**

**核心安全網**＝引文強制（[§5.4](#54-system-prompt)）+ 教育用途浮水印 + 使用者回饋，三者組合。

**已於設計評審移除（不要重新加回）**：關鍵字攔截 + redirect「請諮詢醫師」、prompt 層拒答規則、第一人稱症狀攔截——對醫學系學生過度限制，且「症狀/診斷/治療」是教科書天天出現的詞彙。

## 6.8 限流（Per-User / Global）

| 範圍 | 預設限額 | 目的 |
|---|---|---|
| Per-user / 分鐘 | 10–20 次 | 防誤點與爬蟲 |
| Per-user / 日 | 200–500 次 | 防個人異常爆量 |
| Global RPS | 依 vendor quota 推算 | 保護下游 API |

- **MUST**：用 Redis token bucket 實作（不能用 in-memory，多 worker 會錯）
- **MUST**：超量回 429 + 友善訊息（含 retry_after）
- **MUST**：教師/admin 角色不受限（透過 SSO claim 判斷）
- **MUST**：限流規則寫入設定檔，可不重啟服務調整
- **SHOULD**：用 `slowapi` 或 `fastapi-limiter`

## 6.9 安全與祕鑰

- **MUST NOT**：API key、DB 密碼 hardcode；**MUST NOT**：log 印出完整 API key
- **MUST**：用 `.env` + 雲端 KMS / Doppler / Vault；CI/CD 使用 secret scanning（gitleaks）
- **SHOULD**：query_text 在 query_logs 保留 ≥ 90 天供評估；超過 6 個月應評估去識別化或歸檔（依校方資料治理政策）

---

# 7. 評估與品質保證

## 7.1 三層架構

| 層級 | 觸發 | 工具 | 用途 |
|---|---|---|---|
| 自動化指標 | CI / 每 PR | RAGAS | 防止 regression |
| 人工抽檢 | 每週 | 解剖學助教 | 真實品質 |
| 使用者回饋 | 即時 | 前端 👍/👎 | 線上監控 |

## 7.2 黃金題庫

JSONL，每行一題。一般題含 `id, category, query, expected_pages[], expected_concepts[], metadata_filter`；`out_of_scope` 題含 `id, category, query, expected_response_type:"教材中查無此項"`。

| 類別 | 描述 | 起手最少題數 |
|---|---|---|
| `text_only` | 純文字題（起止點、神經支配、血液供應） | 30 |
| `figure_id` | 圖譜題（指認結構、辨識切面方位） | 30 |
| `cross_page` | 跨頁綜合題（系統性比較、發育對應） | 20 |
| `clinical_correlation` | 教科書臨床相關題（手術解剖、神經損傷風險、病理機轉） | 20 |
| `out_of_scope` | 非解剖學提問（測「教材中查無此項」回應） | 10 |

- **MUST**：總計 ≥ 110 題，由解剖學科教師/助教標註
- **注意**：本系統移除「拒答臨床問題」的限制，故黃金題庫**沒有** `should_refuse` 類別。`out_of_scope` 測的是「題目不在教材範圍時系統正確說明『教材中查無此項』」，**不是**拒答臨床問題。

## 7.3 RAGAS 自動化評估

| 指標 | 目標 | 不達標處理 |
|---|---|---|
| `context_precision` | ≥ 0.85 | 檢查 Stage A/B 排序 |
| `context_recall` | ≥ 0.80 | 提高 Top-K 或加 BM25 副線權重 |
| `faithfulness` | ≥ 0.90 | 強化 system prompt、引入 reranker |
| `answer_relevancy` | ≥ 0.85 | 檢查 prompt 與 LLM 設定 |
| `out_of_scope_correctness` | ≥ 0.90 | 對 OOS 類題應回「教材中查無此項」而非編造 |

CI：`eval.ragas --golden tests/golden_qa.jsonl --report eval_report.json` → `eval.gate --report ... --thresholds eval_thresholds.yaml`。

**規則**：
- **MUST**：每個 PR 跑 RAGAS，未達標阻擋 merge
- **MUST**：`eval_thresholds.yaml` 變更須有人工審核（防止偷偷降低門檻）
- **MUST**：評估結果儲存至少 90 天
- **SHOULD**：RAGAS 用獨立的「評估 LLM」（獨立 API key），不與線上服務共用
- **MUST（DL-013）**：encoder/量化/pooling 變更的上線 gate＝**recall@K by question-class（text_only/figure_id/cross_page/clinical/oos，含中文 query）**，非僅 faithfulness；binary+mean-pool 的「掉 <3pp」須對本語料實測（float 參考 / binary+INT8 rescore / all-binary / BM25-only 四變體）

## 7.4 人工抽檢

- **每週**：助教抽 30–50 筆生產 query log，標註 `correct/partial/wrong/comment`
- 錯誤案例 **MUST** 自動加入回歸測試集（隔週 RAGAS 須通過）
- 👎 案例 **MUST** 在 24 小時內人工檢視一次；`clinical_flavored=true` 的 log 可優先抽檢
- 抽檢工具 **SHOULD** 提供簡易內部工具（Streamlit / Gradio）

## 7.5 線上指標與告警

Grafana dashboard：每日查詢量、平均/p95 latency、模型錯誤率（5.5 vs 5.4 fallback 觸發率）、cache hit rate、👍/👎 比率、token 用量趨勢、引文格式驗證通過率。

**告警**：
- **MUST**：p95 latency > 8s 連續 10 分鐘 → Slack
- **MUST**：模型錯誤率 > 5% 連續 5 分鐘 → Slack + email
- **MUST**：RPM/TPM 用量達 80% → Slack
- **SHOULD**：引文格式驗證失敗率 > 10% 連續 30 分鐘 → Slack（可能是 prompt 退化）

## 7.6 評估資料管理

- **MUST**：黃金題庫存在 git，與程式碼同 PR review
- **MUST NOT**：黃金題庫 leak 到 LLM 的 fine-tuning 資料
- **MUST**：標註者多於一人時計算 inter-annotator agreement（Cohen's kappa），< 0.7 表示題目不清需重寫
- **SHOULD**：黃金題庫每季由教師檢視一次，補充新題、淘汰過時題

## 7.7 模型 / Prompt 變更流程

以下變更 **MUST 經過 RAGAS 評估**通過才上線：LLM 模型版本升級、System prompt 變更、ColPali/encoder 模型變更、HNSW / Stage A·B 參數變更、Pooling 策略變更、啟用/停用 reranker。

流程：staging 部署 → 完整 RAGAS → 與生產版比對 → 任一指標下降 > 2pp 需人工 review → canary 10%→50%→100% → 上線後第 1/7/14/30 天人工抽檢。

---

# 8. 決策總表（DECIDED / OPEN）

## 8.1 DECIDED（不應在實作中變更；異議走 `decisions.md`）

| 決策項 | 選擇 | 排除的替代方案 | 理由 |
|---|---|---|---|
| 主 LLM | `gpt-5.5` via 標準付費 OpenAI API | ChatGPT 免費/個人版 | 多模態+推理品質；付費 API 不用於訓練 |
| 備援 LLM | `gpt-5.4` via 同一帳號 | - | 模型版本 fallback |
| 評估 LLM | `gpt-5.5`（獨立 API key） | - | RAGAS 後端，與生產分離 |
| 視覺檢索 | ColPali (`vidore/colpali-v1.3-hf`) | OCR + 文字 embedding | 保留圖譜空間資訊 |
| 文字解析 | Docling | LlamaParse, Unstructured | 表格與結構化能力 |
| 向量/關聯式 DB | PostgreSQL 16+（單一 store：向量+關聯+BM25+ACID） | LanceDB, Qdrant, Vespa, Milvus | 系統本質為關聯+全文+分析，單一 store 勝過雙 store；MaxSim 引擎見 §8.2（DL-007） |
| 連線池 | PgBouncer（transaction mode） | - | 必要，避免連線耗盡 |
| 後端 | FastAPI（Python 3.11+） | - | async + SSE |
| 前端 | Next.js 14+ + Vercel AI SDK | - | useChat 原生支援 SSE |
| 快取 | Redis 7+ | - | 語意快取 + rate limit |
| 觀測 | LangFuse（自託管） | LangSmith（雲端） | 校內資料治理需求 |
| 評估 | RAGAS | - | 標準 RAG 評估框架 |
| Encoder 主路徑 | 校內 GPU（RTX 5060 Ti 16GB） | RunPod/Lambda 常駐 | 閒置成本 |
| Stage A pooled 表示 | `halfvec(128)` + HNSW cosine | bit(128) pooled | 每頁僅 1 向量、儲存可忽略；二值化犧牲召回上限（DL-019） |
| 查詢翻譯 | encoder 服務內本地 MT（opus-mt-zh-en 起手） | 雲端 API 翻譯 | 零 API 成本、延遲可控；gate＝DL-013 recall（DL-020） |
| 多輪對話 v1 | 無狀態＋規則式追問串接；生成不帶歷史 | LLM rewrite／送全史 | 零額外 token；費用選項列 OPEN（DL-021） |

> ~~編排＝LlamaIndex~~ 已於 **DL-015 移除**；線上路徑不採 RAG 框架，檢索編排由 `backend/retrieval/orchestrator.py` 負責（見 [§4.7](#47-模組介面契約)、[§5.5](#55-openai-多模態呼叫)）。

## 8.2 OPEN（留待實測決定）

| 項目 | 起手值 | 決策時點 |
|---|---|---|
| Encoder 模型 | `vidore/colpali-v1.3-hf`（起手） | **中英混合 query**：須對中文 query 跨語言實測；(a) ColPali+查詢翻譯（已落地 DL-020：encoder 內本地 MT）先測，(b) 跨語言 encoder 備選（DL-008） |
| HNSW 參數 | `m=16, ef_construction=64, ef_search=100` | 依實測 recall@3 調校 |
| Stage A Top-K | 100（DL-013） | recall vs latency 權衡 |
| Pooling 策略 | mean | 召回不理想再試 max / attention |
| Reranker | 暫不啟用 | RAGAS faithfulness < 0.85 再評估 |
| Stage B 精度 | binary Hamming | 不足時升 INT8 rescore（優先）或 float32（DL-003） |
| MaxSim 引擎 | pgvector 兩階段（v1 baseline）/ VectorChord 原生（Phase 12 PoC，勝出才切換） | 皆 in-Postgres、藏於 §4.7 介面（DL-007、DL-014） |
| RRF `k` | 60 | 標準值 |
| Encoder 部署 | 校內 GPU 主、Modal 備 | 視校內 GPU 可用性 |
| 小模型路由（成本優化） | 未啟用 | 配合 query intent classifier |

---

# 9. 不在範圍（future directions）

下列功能 **MUST NOT** 在當前實作加入；如有強烈需求，先於 `decisions.md` 提案，人工審核通過才變動範圍。

- **9.1 地端 VLM 遷移**：當前用雲端 LLM API。注意 MedGemma 27B 的視覺編碼器（SigLIP）偏向放射/病理/眼科/皮膚臨床影像，對解剖教科書插畫的判讀**不一定優於通用 VLM**（Qwen2.5-VL、InternVL3）。如遷移需用真實 query log + 黃金題庫做 head-to-head 盲測，勝出者再評估硬體採購。
- **9.2 學生即時上傳 PDF**：當前採批次離線建庫。如實作需另設計隔離 namespace、檔案大小/張數限制、內容過濾、個人向量空間清理。
- **9.3 跨機構 Federated 知識庫**：當前為單一機構部署。
- **9.4 3D 解剖渲染整合**：不整合 Visible Body / Complete Anatomy。
- **9.5 語音介面**：不支援。
- **9.6 個人化學習進度追蹤**：不做。
- **9.7 跨書籍引用對比**：不主動做（LLM 可能自然提及，但非設計目標）。
- **9.8 即時臨床決策支援界線**：本系統**不是** FDA/TFDA 等級 CDSS，不可作臨床診斷/治療/用藥依據；但**不拒答臨床類問題**（界線靠引文強制 + 浮水印 + 使用者素養 + query log 抽檢，見 [§6.7](#67-醫學教育免責-ux)）。
- **9.9 可開放討論的擴展（須提案）**：多語言支援、引文格式擴充（BibTeX）、LMS 整合（Moodle/Canvas）、教師端後台。
- **9.10 成本預估**：容量與成本模型見[附錄 D](#附錄-d容量與成本模型)（DL-009~011）。數字為**估算**，上線前須用 token counting 量**實際影像 token** 校準（gpt-5.5 影像計法未確認）；建議試用期累積 4–6 週真實用量後定預算。實作 agent **MUST NOT** 在程式碼/設定中嵌入成本估算邏輯（維運層職責）。

---

# 附錄 A：環境變數清單

```bash
# Database
DATABASE_URL=postgresql://user:pass@pgbouncer:6432/anatomy_rag
PG_DIRECT_URL=postgresql://user:pass@postgres:5432/anatomy_rag  # migrations only

# Redis
REDIS_URL=redis://redis:6379/0

# LLM (OpenAI 標準付費 API)
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL_PRIMARY=gpt-5.5
OPENAI_MODEL_FALLBACK=gpt-5.4
OPENAI_BASE_URL=https://api.openai.com/v1   # 預設值，通常不需改

# Embedding (semantic cache)
OPENAI_EMBED_MODEL=text-embedding-3-small   # DL-004：快取去重用 small 即足夠

# Eval LLM（獨立 key，與生產分離）
EVAL_OPENAI_API_KEY=sk-proj-...
EVAL_OPENAI_MODEL=gpt-5.5

# ColPali Encoder
COLPALI_PRIMARY_URL=http://gpu-server:8001/encode_query
COLPALI_FALLBACK_URL=https://colpali-modal.run.modal.com/encode_query

# Query 翻譯（DL-020；encoder 服務內本地 MT，MUST NOT 用雲端 API）
MT_MODEL=Helsinki-NLP/opus-mt-zh-en
TRANSLATE_ENABLED=true

# Object storage
S3_BUCKET=anatomy-rag-pages
S3_ENDPOINT=https://s3.amazonaws.com  # 或 MinIO endpoint

# Knowledge base
ACTIVE_KB_VERSION=3

# Auth
SSO_CLIENT_ID=...
SSO_CLIENT_SECRET=...
SSO_DISCOVERY_URL=https://sso.school.edu.tw/.well-known/openid-configuration

# Observability
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=http://langfuse:3100
SENTRY_DSN=...

# Rate limit
RATE_LIMIT_PER_USER_MIN=15
RATE_LIMIT_PER_USER_DAY=300
RATE_LIMIT_GLOBAL_RPS=20

# Optional flags
CLINICAL_FLAVORED_LOGGING=false   # §6.7 預設關閉
```

# 附錄 B：repo 結構

```
anatomy-rag/
├── docs/                  # ARCHITECTURE.md（本檔）、decisions.md
├── backend/
│   ├── api/               # FastAPI routes（/chat /healthz /warmup）、schemas、citation_builder
│   ├── retrieval/         # Stage A/B、BM25、RRF、orchestrator、types
│   ├── llm/               # vendor 抽象、model fallback、openai_client
│   ├── encoder/           # ColPali client（含 fallback）
│   ├── cache/             # 語意快取
│   ├── observability/     # LangFuse / Sentry
│   ├── db/migrations/     # Alembic（001_… 遞增）
│   └── tests/
├── frontend/              # Next.js（app/ components/ lib/）
├── ingest/                # 離線建庫 CLI（docling_parser / colpali_encoder / binarize / cli）
├── colpali_service/       # 獨立 GPU 微服務（FastAPI :8001 /encode_query）
├── eval/                  # RAGAS + 抽檢工具
├── infra/                 # docker-compose、pgbouncer/、prometheus/
├── shared/                # binary.py（離線與 query 端共用二值化）
└── tests/golden_qa.jsonl
```

> **共用二值化**：`shared/binary.py` 為離線端（`ingest/`）與 query 端（`colpali_service/`）**唯一**的 binarize 來源，兩端 import；見 [§2.4](#24-二值化壓縮)。

---

# 附錄 C：合併時發現的矛盾（已裁決）

> 合併原始 11 份文件時發現以下矛盾/缺口。已於 2026-06-07 由專案負責人（委派 main Claude）逐項裁決，記錄於 `decisions.md` DL-002～DL-006 並回寫對應章節。裁決者保留否決權。

| # | 矛盾 | 裁決（摘要） | 記錄 / 章節 |
|---|---|---|---|
| C-1（原 P0）| orchestrator 單一 `conn` 併發 `asyncio.gather`，asyncpg 禁止 | Stage B → BM25 **序列執行**；日後需要再借第二條連線並行 | DL-002 / §4.7、§4.5 |
| C-2 | float32 同存 MUST NOT vs 精排升級需另存 | v1 只存 bit；升級用 **INT8 rescore（優先）/ float32（次選）**，經 RAGAS 與評估 | DL-003 / §2.4、§4.4 |
| C-3 | 語意快取 embedding small vs large | 統一 **text-embedding-3-small** | DL-004 / §6.4、附錄 A |
| C-4 | metadata 無 figure，引文需圖號 | metadata 加 optional **`figures[]`**（hint）；權威圖號靠 LLM 逐句引文 | DL-005 / §3.2、§5.7 |
| C-5 | LangFuse 與 Next.js 撞 :3000 | Next.js 3000 / Grafana 3001 / **LangFuse 3100** | DL-006 / §1.5、附錄 A |

---

# 附錄 D：容量與成本模型

> 來源：2026-06-07 雙模型 spec 審查（Codex 跨模型 + Claude Opus 4.8）收斂結論。**所有數字為估算（order-of-magnitude）**，影像 token 計法與 gpt-5.5 實際定價未完全確認；**上線前須用 token counting 校準**。對應 DL-009（成本路由）、DL-010（儲存/RAM）、DL-011（固定浪費）。

## D.1 結論（先講重點）

- **隨「#參考書」成長 → 便宜且安全**：儲存/HNSW/GPU 皆小量線性。唯一須主動規劃：**Postgres RAM ≥ 作用中 `page_patches`**（D.3）。
- **隨「#使用者」成長 → 成本幾乎全在 LLM token**；其中**影像與輸出 token 是大頭**。
- **三大成本槓桿**（依影響）：① 條件式/裁切影像（DL-009，最大）② 語意快取命中率 ③ 小模型路由。

## D.2 每次查詢（cache miss）LLM 成本

| 項目 | 估算 token | 備註 |
|---|---|---|
| system prompt | ~300 | 固定 |
| 3× docling_md | ~3,000 | 視頁面文字量 |
| 3× 整頁圖 @ detail:high | ~3,000–6,000 | **影像計法未確認，誤差來源最大**；約占輸入 40–50% |
| query | ~50 | |
| 輸出 | ~500–1,000 | |
| **每 miss 成本** | **~$0.02–0.07** | 依影像 token 法與定價；影像是可砍的大頭 |

> **DL-009 影響**：若 ~50% 為純文字題改不送圖、其餘送 1–2 張，blended 輸入可降 ~30–40%。

## D.3 月成本（LLM，估算區間）

| 月查詢量 | 無快取/無路由（gross） | 套 ~40% 快取 + DL-009 路由 |
|---:|---:|---:|
| 10k | ~$270–700 | **~$150–300** |
| 100k | ~$2.7k–7k | **~$1.5k–4k** |
| 1M | ~$27k–70k | **~$16k–40k**（此規模 LLM 帳單＝唯一重點，路由槓桿差 ~$5–9k/月） |

固定基建（與規模幾乎無關）：校內 GPU（已購）+ Postgres 主機 + Redis + LangFuse + Sentry ≈ 量級 $300–1,500/月等值（視託管方式）。

## D.4 儲存與 RAM（隨 #書 成長）

| 項目 | 每千頁書 | 備註 |
|---|---|---|
| `page_patches`（含 row+index overhead） | **~100 MB** | 非 16MB；~6× 原始 bit 量（DL-010） |
| HNSW（每頁 1 pooled halfvec 向量，DL-019） | 數 MB（百萬頁仍輕） | 非瓶頸 |
| 頁面 PNG/WebP derivative | ~1–5 GB | 物件儲存，便宜 |
| INT8 rescore（若啟用，DL-003） | +~125 MB | float32 為 +~500 MB |

- 50 書 ≈ patches 5 GB；200 書 ≈ 20–30 GB；blue-green 期間暫 ×2。
- **RAM 規則（MUST，DL-010）**：Postgres RAM ≥ 作用中版本 `page_patches` 大小，否則 Stage B 隨機讀（每查詢 ~50 頁 ×1024 patch）退化。VectorChord 的 decomposed 儲存為長期解。

## D.5 GPU / 並發

- 單張 RTX 5060 Ti 16GB、~50ms/query → 序列上限 **~20 q/s**；對 ~10k/月穩態綽綽有餘。
- **風險是突發**（整班同時問）：需 batching + 佇列深度 admission control + 飽和觸發 fallback；再擴充先加第二張 encoder replica 或升級 GPU，**不必動 DB 架構**。
- Stage B（自建兩階段 v1 baseline）在單機約 20 RPS、Top-K=100 時為 CPU 瓶頸點 → 故 VectorChord（Phase 12 PoC）+ 並發壓測列為**擴展評估 gate**（DL-007/DL-010/DL-014）。
