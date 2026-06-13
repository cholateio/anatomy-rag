# 決策日誌 (Decision Log)

本檔記錄所有偏離 `docs/` 中 `DECIDED` 項目的提案與裁決。

## 使用時機

當實作過程中發現某個 `DECIDED` 項目在實務上不可行、有更好替代方案、或需要小幅修正時：

1. **不要直接修改 `docs/`**
2. **不要在 PR 中夾帶設計變更**
3. **先在此檔新增提案**，狀態為 `PROPOSED`
4. 待 reviewer 將狀態改為 `APPROVED` 或 `REJECTED` 後，才能在 PR 中實作對應變更
5. APPROVED 後同時更新 `docs/` 相關章節

## 格式

每個提案使用以下結構：

```
## DL-NNN: 簡短標題

- **狀態**：PROPOSED / APPROVED / REJECTED / SUPERSEDED
- **提案者**：（名字或 agent id）
- **日期**：YYYY-MM-DD
- **影響檔案**：docs/0X-xxx.md §X.Y
- **裁決者**：（待填）
- **裁決日期**：（待填）

### 背景
（當前 DECIDED 項目是什麼，為何要改）

### 提案
（具體要改成什麼）

### 替代方案
（其他考慮過的選項，為何不選）

### 影響評估
- 工作量：
- 相依模組：
- 回退成本：

### 裁決說明
（reviewer 填寫）
```

---

## DL-001: （範例佔位）

- **狀態**：APPROVED
- **提案者**：架構評審
- **日期**：（初始設計階段）
- **影響檔案**：docs/01 §1.5、docs/05 §5.3
- **裁決者**：專案負責人

### 背景
原規劃使用 Azure OpenAI + Gemini 作為主備 vendor，理由是 FERPA / ZDR。

### 提案
改用 OpenAI 標準付費 API，主 `gpt-5.5` / 備 `gpt-5.4`。

### 替代方案
- Azure OpenAI：採購流程複雜、新模型上線晚
- Anthropic Claude / Google Gemini：v1 暫不引入 vendor 級 fallback

### 影響評估
- 工作量：小（簡化環境變數與部署）
- 相依模組：05、06、08
- 回退成本：低（換 API key 即可）

### 裁決說明
v1 採此方案。教科書與學生查詢無 PHI，標準付費 API 之資料保留條款已足夠。

---

> 以下 DL-002～DL-006 為「11 份 docs 整併為 `ARCHITECTURE.md`」時發現的矛盾，使用者於 2026-06-07 委派 main Claude 逐項裁決並回寫 spec（「選擇最合理的方式進行」）。裁決者保留否決權。

## DL-002: orchestrator 在單一連線上序列執行 Stage B 與 BM25

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07
- **影響檔案**：ARCHITECTURE.md §4.7、§4.5、§1.6　**裁決者**：專案負責人（委派）

### 背景
原 §4.7 orchestrator 對單一 `conn` 跑 `asyncio.gather(stage_b_task, bm25_task)`，但同檔註明「用單一 conn」。asyncpg **禁止**在同一 connection 併發操作（會 raise `InterfaceError: another operation is in progress`）——線上檢索必崩（P0）。

### 提案
Stage B 與 BM25 改在同一 `conn` 上**序列** await（Stage B → BM25）。

### 替代方案
從 PgBouncer 池借第二條連線（`async with pool.acquire() as conn2`）給 BM25 以恢復並行——但每請求多佔一條連線，加重 transaction pooling 壓力。

### 影響評估
- 延遲：序列約 +50ms（Stage B <200ms + BM25 <50ms）；在 ~10k/月、低併發下可接受。
- 回退成本：低（要並行隨時可借第二條連線）。

---

## DL-003: 量化精排升級走 INT8 rescore（優先）而非 float32

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07
- **影響檔案**：ARCHITECTURE.md §2.4、§4.4、§4.6、§8.2　**裁決者**：專案負責人（委派）

### 背景
§2.4「MUST NOT 同存 float32 與 bit」與 §4.4/§4.6「Stage B 精度不足時改 float32（需另存）」直接衝突——升級路徑被自家 MUST NOT 堵死。

### 提案
v1 預設只存 bit。當 RAGAS `context_precision < 0.85` 且確認 binary 量化為瓶頸時，**MAY** 另存一份更高精度的 rescore 表示供 Stage B 精排，**優先 INT8**（相對 float32 省 4×、相對 binary 品質明顯回升），float32 為次選；啟用須經 RAGAS 與儲存評估。

### 替代方案
(a) 永遠只存 bit（精度不足時無路可走）；(b) 直接 float32（儲存 4× 於 INT8，CP 值差）。

### 裁決說明
binary 粗排 + INT8/float32-query rescore 為 2025–26 業界標準做法（HF embedding-quantization、Qdrant binary quantization + rescore、Vespa int8、HPC-ColPali）。

---

## DL-004: 語意快取 embedding 統一用 text-embedding-3-small

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07
- **影響檔案**：ARCHITECTURE.md §6.4、附錄 A　**裁決者**：專案負責人（委派）

### 背景
§6.4 內文用 `text-embedding-3-small`，附錄 A 與技術棧用 `text-embedding-3-large`（疑 typo）。

### 提案
統一用 `text-embedding-3-small`。語意快取是「query 近似去重」（threshold 0.95），非檢索品質關鍵；small 已足夠，且較便宜（~5×）、維度 1536 < 3072（省 Redis 記憶體）。

---

## DL-005: metadata 增 optional `figures[]`；權威圖號靠 LLM 逐句引文

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07
- **影響檔案**：ARCHITECTURE.md §3.2、§5.7　**裁決者**：專案負責人（委派）

### 背景
metadata MUST 欄位不含 `figure`，但 §5.4 引文格式與 §5.7 `PageCitation.figure = metadata.get("figure")` 都需圖號。

### 提案
metadata 增 optional `figures: string[]`（Docling 抽出的該頁圖說標籤清單，預設 `[]`），作為前端引用面板 hint。**權威圖號以 LLM 對高解析頁圖的逐句引文為準**（system prompt 已要求 `[書名, 頁碼, 圖號]`）；不靠 page-level metadata 鎖定單一圖（一頁常含多張圖）。`PageCitation.figure` 維持 optional，取 `figures` 首項作 hint。

---

## DL-006: 觀測埠分配 Next.js 3000 / Grafana 3001 / LangFuse 3100

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07
- **影響檔案**：ARCHITECTURE.md §1.5、附錄 A　**裁決者**：專案負責人（委派）

### 背景
§1.5 服務拓撲將 LangFuse 與 Next.js 都標 `:3000`，同機/對外暴露會撞埠。

### 提案
容器內以 service name 定址本不撞，但對外/同機暴露時統一錯開：Next.js `:3000`、Grafana `:3001`、LangFuse `:3100`。

---

> DL-007、DL-008 為「英文語料 + 中英混合 query + 地端 docker/uv/npm + 要求未來不被迫 re-platform」前提下，使用者於 2026-06-07 委派 main Claude 決定（「你自行決定」）。裁決者保留否決權。

## DL-007: DB 留單一 PostgreSQL；MaxSim 用 VectorChord（首選）/ 兩階段（fallback），拒絕 LanceDB

- **狀態**：APPROVED（委派）　**提案者**：main Claude　**日期**：2026-06-07
- **影響檔案**：ARCHITECTURE.md §4.1、§8.1、§8.2、§4.7　**裁決者**：專案負責人（委派）

### 背景
評估是否改用 LanceDB（embedded）以簡化部署 / 取得原生 ColPali MaxSim。使用者要求「不要先 LanceDB、未來又被迫整碗改 PostgreSQL」。

### 提案
留在 **PostgreSQL 作為單一 system of record**（向量 + 關聯 books/pages/metadata/query_logs + BM25 tsvector + ACID + kb_version 交易切換）。MaxSim 引擎藏在 §4.7 介面後，可互換：
- **首選**：VectorChord 0.3 擴充——Postgres 內原生、高效 MaxSim（decomposed MaxSim，受 XTR-WARP 啟發），採用後可**省掉自建兩階段**；地端有 `tensorchord/vchord` docker image。**先做小 PoC 驗證再正式採用。**
- **fallback**：應用層自建兩階段（pgvector binary HNSW 粗排 → MaxSim 精排），已具備。

### 替代方案
LanceDB（embedded）——**拒絕**。理由：它只解決向量端、不含關聯，最終仍需第二個 SQL store（雙 store）；multivector 目前僅 cosine、BM25 需另接；OSS（<50 QPS 單機）→ Enterprise（K8s）本身也是一次未來遷移。

### 裁決說明
本系統的決定因素是**資料模型需求（關聯 + 全文 + 分析：限流/抽檢/RAGAS/回饋/SSO），非 QPS**。即使永遠維持 <50 QPS 單機，這些需求都在 → 單一 Postgres 勝過「LanceDB + SQL」雙 store。VectorChord 讓 Postgres 取得 LanceDB 的主要技術優勢（原生 MaxSim）而**不離開 Postgres**，直接消除「未來被迫 re-platform」風險。回退成本低（PoC 失敗就用兩階段，皆 Postgres、皆在 §4.7 介面後）。

---

## DL-008: 中英混合 query → encoder 須跨語言實測；不預設 ColPali v1.3 安全

- **狀態**：APPROVED（委派）　**提案者**：main Claude　**日期**：2026-06-07
- **影響檔案**：ARCHITECTURE.md §8.2、§7.7　**裁決者**：專案負責人（委派）

### 背景
語料（教科書/講義）為英文，但學生 query 為**中英混合** → 「中文 query → 英文頁面」屬**跨語言檢索**。English-centric 視覺檢索 encoder（ColPali v1.3，PaliGemma 底）對中文 query 可能 recall 大降。注意：**頁面端**仍是英文嵌入，不需多語言「文件」模型；需要的是 **query 端**的跨語言處理。

### 提案
encoder 維持 OPEN，但**選型 gate 在「含中文 query 的黃金題庫」RAGAS 實測**（§7.7）。路徑：
- **(a) ColPali v1.3 + 查詢翻譯**：偵測中文 query → MT 成英文再編碼。保留英文嵌入模型（符合「不需多語言嵌入」偏好），代價是多一個 MT 步驟與少量術語誤譯風險（解剖術語多為拉丁/英文，誤譯風險低）。**先測這條。**
- **(b) 跨語言 late-interaction encoder**：ColQwen2.5-multilingual / ColNomic，免 MT 但需換模型並重建索引。(a) 不足再評。

### 裁決說明
BM25 副線對中文 query 幫助有限（tsvector 為英文）→ 中文 query 主要靠向量路徑，encoder 的跨語言品質更關鍵，故必須實測而非假設。

---

> DL-009～DL-013 來源：2026-06-07 對 `ARCHITECTURE.md` 做的雙模型 spec 審查——**Codex（跨模型隔離）** + **Claude Opus 4.8（同模型，狀態隔離）**，兩者獨立收斂於以下重點。使用者裁示「全部套用」。

## DL-009: 成本路由——條件式附圖 + 小模型下放（解除「一律 high、固定 3 張」MUST）

- **狀態**：APPROVED　**日期**：2026-06-07　**影響檔案**：ARCHITECTURE.md §5.5、§5.6、§1.9、§5.3、附錄 D

### 背景
兩模型一致認定：每次查詢固定送 3 張 `detail:high` 整頁圖（連純文字題也送）是**最大、且被 spec 自己 MUST 鎖死**的成本項；影像約占輸入 token 47%，對純文字題是純浪費。

### 提案
影像附帶改**條件式**，由 `page_type`（pure_text/figure_heavy/table/mixed）+ query intent 路由：
- 純文字題 → 只送 `docling_md`，**不送圖**。
- 圖譜題 → 只對 figure_heavy/mixed 頁送圖，**預設 top-1（最多 2）**，非固定 3。
- `detail:"high"` 保留給「需判讀標籤」的圖頁；v2 評估 figure bbox 裁切（保標籤清晰又大砍 token）。
- 影像存 web-optimized JPEG/WebP derivative，避免 inline base64 膨脹。
- 小模型路由（§5.3 OPEN）正式列為成本槓桿：純文字/簡單題可下放較小模型（需 intent classifier + RAGAS）。

把 §5.5「一律 high」「固定 3 張」、§1.9 將 `detail:low/auto` 列「陷阱」——由 MUST/硬禁降為 **default**，交給路由與 RAGAS 取捨。

### 理由
這是「隨 #使用者成長是否負擔得起」的決定性槓桿（1M/月規模可差 ~$5–9k/月），且對純文字題零品質影響。

---

## DL-010: 擴展性——VectorChord 為擴展正解；v1 不完整實作自建兩階段；page_patches 分區 + RAM sizing

- **狀態**：APPROVED　**日期**：2026-06-07　**影響檔案**：ARCHITECTURE.md §3.3、§3.6、§4.1、§4.7、§4.8、附錄 D（補強 DL-007）

### 背景
兩模型一致：(1) `page_patches` 一 patch 一 row，含 tuple+PK overhead ≈ **~100 bytes/patch（~6× 原始）→ ~100MB/書**（非 16MB）；真正 cliff 是 Stage B 隨機讀需常駐 RAM。(2) v1 同時完整實作並測試「VectorChord + 自建兩階段」兩套是 gold-plating。

### 提案
- VectorChord（DL-007 首選）定位為**擴展正解**：其 decomposed/postings 儲存避開 row-per-patch 膨脹。**先做 PoC，通過即只實作它**；自建兩階段**保留 §4.7 介面、延後完整實作**（v1 不需 production-ready + 全測，§4.8 兩階段測試標注為 fallback 引擎用）。
- 容量規劃寫死公式：**Postgres RAM ≥ 作用中版本 `page_patches` 大小**。
- `page_patches` 按 `kb_version`（或 book）**分區**，利於刪除/備份/重建，並避免 blue-green 期間 HNSW 過濾撈不滿候選。
- 備份：immutable patch 資料不做每日全量 `pg_dump`，改 snapshot + 可重現 ingest。

---

## DL-011: 裁減 v1 過度設計——Modal scale-to-zero + 觀測先 LangFuse+Sentry + 縮短 blue-green 雙版窗

- **狀態**：APPROVED　**日期**：2026-06-07　**影響檔案**：ARCHITECTURE.md §6.2、§6.5、§6.6、§5.1

### 背景
兩模型一致指出與規模無關的固定浪費/過重維運。

### 提案
- **Modal `keep_warm=1` → scale-to-zero**（§6.2 由 MUST 降級）：主路徑為校內 GPU，fallback 罕用；常駐 L4 ~$800/月只為省偶發 ~10s 冷啟不划算，靠 `/warmup`+readiness 緩解。
- **v1 觀測先 LangFuse（trace，對 RAG 最有價值）+ Sentry（錯誤）**；Prometheus/Grafana 有明確需求或量起來再加（自託管 LangFuse 已是一組 stack）。
- **blue-green 雙版本並存窗**從固定 4 週縮為「canary + 短回滾期（數天）」，或舊版降 cold（不佔常駐 RAM）。

### 保留（非過度設計）
PgBouncer、reranker（已預設關閉 + RAGAS gate）、kb_version 機制本身、citations、MaxSim 介面抽象。

---

## DL-012: 正確性修補——連線不跨串流、引文真實性驗證、快取改本地 embedding

- **狀態**：APPROVED　**日期**：2026-06-07　**影響檔案**：ARCHITECTURE.md §5.6、§3.4、§5.4、§5.7、§6.4、§1.6（補強 DL-004）

### 提案
1. **DB 連線不得跨 LLM 串流持有**：`retrieve()`/影像 fetch 完成即歸還連線，再進 5–15s 串流（否則 25 連線池在班級突發下 ~1.6 QPS 即耗盡）。orchestrator 用 `async with pool.acquire()` 僅包檢索段。
2. **引文真實性驗證（不只格式 regex）**：對 LLM 輸出的每個引文，cited book/page 須對照 retrieved top-3、figure 對照 `figures[]`；無法佐證者移除/重生/明確標示未驗證。（強制引文是安全網核心，不能讓捏造合法外觀的引文漏過）
3. **語意快取改本地輕量 embedding 當 cache key**（補強 DL-004：text-embedding-3-small 仍可作離線/評估用，但**線上 cache lookup 用本地模型**避免 OpenAI 往返）；修 §1.6 延遲預算（OpenAI 往返 100–300ms 與 30ms 命中矛盾）。cache 命中率/precision 上線後實測，先用 exact-normalized-query 快取較安全。

---

## DL-013: 檢索品質 gate——BM25 餵 MT 英文、Top-K 起手 100、recall by question-class

- **狀態**：APPROVED　**日期**：2026-06-07　**影響檔案**：ARCHITECTURE.md §4.5、§4.6、§7.3、§7.7（補強 DL-008）

### 提案
- **BM25 也餵 DL-008 翻譯後的英文 query**（保留原始混語 query 給生成），讓 hybrid 對中英混合主力流量真正生效；否則重評 BM25/RRF 價值。
- **Stage A Top-K 起手 50 → 100**：Stage B 成本與語料大小無關（只碰候選頁），提高 Top-K 是便宜的 recall 保險。
- **上線 gate 改為 recall@K by question-class（text_only/figure_id/cross_page/clinical/oos，含中文 query）**，而非僅 RAGAS faithfulness；binary+mean-pool 的「掉 <3pp」須對本語料實測（評估 float 參考 / binary+INT8 rescore / all-binary / BM25-only 四變體）。
- encoder p95 SLO 分主/備（校內 <100ms / Modal <300ms）。

---

> DL-014～DL-018 為 Phase 0 實作起手前，使用者於 2026-06-07 委派 main Claude 提出、並由專案負責人**核准**的五項定案；本日誌記錄之，並回寫 `ARCHITECTURE.md` 對應章節。皆為 in-Postgres／介面後／可回退之低風險調整或既有矛盾之修補。

## DL-014: 檢索排序——v1 先做 pgvector 自建兩階段 baseline；VectorChord 列 Phase 12 PoC

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07　**裁決者**：專案負責人　**影響檔案**：ARCHITECTURE.md §4.1、§4.8、§8.2、附錄 D（補強 DL-010）

### 背景
DL-010 將 VectorChord 定為「先做 PoC，通過即只實作它」，自建兩階段「延後完整實作與測試」。但 v1 起手需要一條**已驗證、可靠**的檢索 baseline；把唯一可用路徑押在尚未驗證的擴充上，風險集中於上線初期。

### 提案
- **v1 先實作並完整測試 pgvector 自建兩階段**（§4.3 Stage A / §4.4 Stage B / §4.5 BM25+RRF / §4.7 介面 / §4.8 測試）作為可靠 baseline。
- **VectorChord 維持 §4.7 介面、列為 PoC（Phase 12）**；以 **recall@K + p95 + 運維（部署/備份/升級）實測勝出**為唯一切換條件，未勝出則續用自建兩階段。
- 本提案**微調 DL-010**「先 PoC 通過即只做它」的排序（不是推翻 VectorChord 為長期擴展正解的定位）。

### 影響評估
- 工作量：小（自建兩階段本即 §4.x 既有設計，僅把實作/測試前置到 v1）。
- 回退成本：低（兩者皆 in-Postgres、皆藏於 §4.7 介面後；切換不需 re-platform）。

## DL-015: 線上路徑移除 LlamaIndex（不採 RAG 框架）

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07　**裁決者**：專案負責人　**影響檔案**：ARCHITECTURE.md §5.5、§8.1

### 背景
原 §8.1 DECIDED 表與 §5.5、技術棧把 LlamaIndex 列為「編排」框架，但本系統的檢索是自訂兩階段（Stage A/B + BM25 + RRF），LLM 呼叫又走原生 `openai` SDK；LlamaIndex 介於中間既不負責檢索核心、也不負責生成，徒增抽象層與相依。

### 提案
- 線上路徑**不採任何 RAG 框架**；移除 §5.5、§8.1、技術棧中**所有**規範性 LlamaIndex 引用。
- 檢索編排由 `backend/retrieval/orchestrator.py` 自理（已是 §4.7 的主入口）。
- 加 **repo 層級斷言（CI grep）**確保程式碼/依賴無 `llama_index` 殘留。

### 影響評估
- 工作量：小（移除尚未存在的相依；orchestrator 本即設計核心）。
- 回退成本：低。

## DL-016: v1 校內 SSO 暫緩，以可插拔 auth 抽象替代

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07　**裁決者**：專案負責人　**影響檔案**：ARCHITECTURE.md §5.8

### 背景
§5.8 要求所有 `/chat` 走校內 SSO（OAuth2/SAML），但 v1 起手階段校內 SSO 尚未接通，硬性 MUST 會卡住開發與評估流程。

### 提案
- `backend/api/auth.py` 提供 auth 抽象（`get_current_user`）：**dev** 注入固定 `user_id`；**production** 保留 OIDC 介面與設定文件化。
- §5.8 的 SSO **MUST** 標注「**接回校內 SSO 時生效**」。
- `user_id` / 限流 / `query_logs` **照常運作**（dev stub 提供 `user_id`），不因暫緩而失去觀測與限流能力。

### 影響評估
- 工作量：小。
- 回退成本：低（介面已預留，接回 SSO 僅補實作）。
- 合規：不影響「不送 user_id 給 OpenAI」等紅線。

## DL-017: `page_patches` 加 `kb_version` 欄位並納入 PK（使 DL-010 分區可實作）

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07　**裁決者**：專案負責人　**影響檔案**：ARCHITECTURE.md §3.2、§3.3、§4.4（補強 DL-010）

### 背景
DL-010 要求 `page_patches` 按 `kb_version`（或 book）分區，但 PostgreSQL **宣告式分區鍵必須包含於 PRIMARY KEY 內**。原 §3.2 schema 的 `page_patches` PK 為 `(page_id, patch_idx)`，不含 `kb_version`，導致 DL-010 的分區無法落地。

### 提案
- §3.2 `page_patches` 加 `kb_version INTEGER NOT NULL`，PK 改為 `(kb_version, page_id, patch_idx)`，並 `PARTITION BY LIST (kb_version)`。
- §4.4 Stage B SQL **帶 `kb_version`** 過濾（`WHERE ... AND pp.kb_version = :kb_version`）。
- §3.3 索引說明更新 PK 描述，並註明 `page_patches` 查詢 **MUST 帶 `kb_version`**。

### 影響評估
- 工作量：小（schema + 一處 SQL）。
- 回退成本：低（migration 可逆）；屬離線建庫前的 schema 定案，無生產資料遷移風險。

## DL-018: Vercel AI SDK UI Message Stream——後端手刻薄 emitter（「不自寫」核准例外）

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**日期**：2026-06-07　**裁決者**：專案負責人　**影響檔案**：ARCHITECTURE.md §5.6、§6.3

### 背景
§5.6 規定「用 sse-starlette + Vercel AI SDK，**不自寫**」。但 AI SDK v5/v6 改用 **UI Message Stream 協定**（typed parts over SSE），且**無官方 Python lib**；後端需要產生符合該協定的事件，無現成套件可用。

### 提案
- 後端**手刻一個薄 emitter**（集中於 `backend/api/ai_stream.py`）產生 UI Message Stream 事件，作為 §5.6「不自寫」的**核准例外**；前端仍用 `useChat`、**不自寫** SSE/狀態管理。
- 事件對應：原 `sources` event → 自訂 **`data-sources` part**（前端以 **`onData`** 接收，非舊 `onResponse`）。
- 傳輸細節：HTTP header `x-vercel-ai-ui-message-stream: v1`；串流以 `data: [DONE]` 收尾。
- 回寫 §5.6（規則與事件表）、§6.3（串流契約交叉引用）。

### 影響評估
- 工作量：小至中（薄 emitter + 前端 `onData` 接線）。
- 回退成本：低（emitter 集中單檔，協定升級時只改一處）。

---

> DL-019～DL-021 來源：2026-06-10 Phase 1 起手前的獨立 spec+環境審查（fresh-context Claude session；
> **同模型審查，僅狀態/時間隔離**）。專案負責人裁示「四項 high 級問題採最合適方法修正；涉金錢才回頭討論」，
> 據此核准。涉費用之選項均列 OPEN 未啟用。

## DL-019: Stage A 的 pooled 向量改存 halfvec(128)，不再二值化

- **狀態**：APPROVED　**提案者**：獨立審查 session（Claude）　**日期**：2026-06-10
- **影響檔案**：ARCHITECTURE.md §1.4、§1.6、§1.7、§1.9、§2.1、§2.4、§3.2、§3.3、§4.2、§4.3、§4.7、§5.1、§8.1、附錄 D　**裁決者**：專案負責人

### 背景
§2.4 原 MUST「v1 資料庫只存 bit 版本」與 §3.2 `pooled_bin BIT(128)` 把二值化同時套在 patch 與 pooled 兩種向量。該 MUST 的動機（32× 儲存/計算壓縮）只對 `page_patches`（每頁 ~1024 向量、~100MB/千頁）成立；`pages` 每頁只有 1 個 pooled 向量，halfvec(128) 僅 ~256 bytes/頁（~0.25MB/千頁），成本可忽略。代價端：mean-pool 已是有損摘要，再經 sign 二值化後，Stage A 的距離函數只剩 129 個離散 Hamming 值（大量 tie、排序解析度差），而 Stage A 決定整條檢索鏈的 recall 上限。業界 ColPali 兩階段實務（Vespa/Qdrant/HF binary-quantization 系列）普遍為 patch 量化、pooled/粗排向量留 float。

### 提案
- `pages.pooled_bin BIT(128)` → `pages.pooled HALFVEC(128)`；HNSW 改 `halfvec_cosine_ops`（cosine 對縮放不敏感，mean 後毋須重新 L2 normalize）。
- encoder `/encode_query` 契約：`pooled_bin` → `pooled_f32`（base64 之 512-byte little-endian float32[128]；DB 寫入時降為 halfvec）。
- `shared/binary.py` 職責不變（仍只管 patch 二值化）；§2.4 MUST 改寫為「**`page_patches`** v1 只存 bit」。
- 評估註記：binary-pooled 變體可由 `binarize(pooled)` 即時導出，DL-013 四變體實測不需另存欄位。

### 替代方案
(a) 維持 bit pooled——召回上限受限，且成為 DL-013 實測的混淆變數；(b) 同存 bit+halfvec 做 A/B——不必要，binary 版可從 float 即時導出。

### 影響評估
- 工作量：小（schema 一欄、encoder 契約一鍵、Stage A operator）；Phase 2 動工前改動，零資料遷移成本。
- 回退成本：低（migration 可逆；Stage A 藏於 §4.7 介面後）。
- 金錢/硬體：無（儲存增量 ~0.25MB/千頁）。

---

## DL-020: 查詢翻譯落地——encoder 微服務內建本地 MT（中/混語 query → 英文）

- **狀態**：APPROVED　**提案者**：獨立審查 session（Claude）　**日期**：2026-06-10
- **影響檔案**：ARCHITECTURE.md §1.4、§1.6、§1.7、§4.2、§4.5、§4.7、§5.1、§5.6、§8.1、§8.2、附錄 A（補強 DL-008/DL-013）　**裁決者**：專案負責人

### 背景
DL-008 已定路徑 (a)「ColPali + 查詢翻譯」、DL-013 要求 BM25 餵翻譯後英文 query，但翻譯模組的引擎、部署位置、延遲預算、失敗 fallback 全未規格化：§1.6 時序沒有 MT 步驟；roadmap 原把 `translate.py` 放在 Phase 6 backend/llm（會把 torch 拖進 backend，違反 D-L）。

### 提案
- **部署位置**：翻譯內建於 **encoder 微服務**（colpali_service）的 `/encode_query` 管線：偵測語言（query 含 CJK 字元即需翻譯）→ 本地 MT 翻成英文 → 以英文做 ColPali 編碼 → 回傳 `{tokens_bin, pooled_f32, translated_q, lang, model, mt_model}`。backend 維持 torch-free（D-L）；BM25 直接用 `translated_q`（DL-013），英文 query 為 identity。
- **引擎（DECIDED 起手）**：`Helsinki-NLP/opus-mt-zh-en`（本地、零 API 成本、CPU 數十 ms 級）。**MUST NOT** 用雲端 API 做查詢翻譯（成本、延遲、離線可用性；非隱私紅線——query 本就會送 OpenAI 生成）。**SHOULD**（Phase 3 實測）：解剖術語 glossary 長詞優先替換 + 保護 query 內既有 ASCII/拉丁術語 span 不送 MT。
- **品質 gate**：MT 路徑成敗以 DL-013「recall@K by question-class（含中文 query）」實測裁決；不達標升級序＝更強本地 MT（NLLB-200-distilled-600M）→ 跨語言 encoder（DL-008 (b)）→ 雲端翻譯（最後選項，涉費用須另走 DL）。
- **失敗 fallback**：MT 例外 → 以原文編碼、`translated_q=null`、BM25 用原文（中文會空轉，trace 標記 `mt_failed`），不阻斷查詢。
- **延遲預算**：§1.7 encoder p95 SLO 對含 MT 的中文 query 放寬 +50ms（主 <150ms / Modal <350ms）；§1.6 時序補 MT 步驟。**MUST**：Modal fallback 映像內建同一 MT 模型（主/備契約一致）。
- **快取**：語意快取 key 維持以「原始 normalized query」為準（翻譯為決定性下游步驟，不進 key）。
- mock 契約（Phase 0 起）：決定性 identity 翻譯 + CJK 偵測（`mt_model="mock-identity"`），供下游演練。

### 替代方案
(a) backend 內翻譯：拖 torch 進 backend、違反 D-L；(b) 獨立翻譯微服務：對 v1 過重；(c) OpenAI 翻譯：每查詢新增付費 API 呼叫（費用+延遲），列為最後選項。

### 影響評估
- 工作量：中（Phase 3 encoder 內加 detect+MT+glossary；Phase 5 BM25 改接 `translated_q`）。
- 回退成本：低（變更集中於 encoder response 欄位，向後相容）。
- 金錢/硬體：無新增（本地模型 ~300MB，跑於既有 encoder 容器；無 API 費用）。

> **實作附註（2026-06-12，Phase 3 落地；APPROVED（委派），使用者保留否決權）**：
> (a) MT 前處理加 **OpenCC `t2s` 繁→簡**（opus-mt-zh-en 訓練語料以簡體為主；OpenCC 為本地
> C++ binding，零 API 成本，符合 MUST NOT 雲端翻譯）；(b) SHOULD 的「ASCII/拉丁術語 span
> 保護」以 **CJK-run 分段翻譯**實現（僅 CJK 段送 MT，非 CJK 段原樣保留），規避 placeholder
> 被 sentencepiece 拆壞的已知問題；MT 輸出段數不符或輸出仍含 CJK 一律視為失敗（translated_q=null）；
> (c) glossary 起手 40 詞（`colpali_service/glossary_zh_en.tsv`，繁體 key、載入時轉簡、長詞優先）；
> (d) 新依賴 sentencepiece/OpenCC/sacremoses 經使用者核准（2026-06-12）；
> (e) transformers pin `>=5,<6`（4.52–4.53 為 colpali-v1.3-hf 已知破損區間）。

---

## DL-021: 多輪對話 v1 政策——無狀態後端 + 規則式追問串接；生成不帶歷史

- **狀態**：APPROVED　**提案者**：獨立審查 session（Claude）　**日期**：2026-06-10
- **影響檔案**：ARCHITECTURE.md §3.2（query_logs）、§5.6、§5.7、新增 §5.9、§6.4、§8.1　**裁決者**：專案負責人

### 背景
§5.7 有 `conversation_id`、前端 `useChat` 預設送整段歷史，但 spec 未定義：檢索 query 如何由多輪訊息構造、追問（「那它的神經支配呢？」）如何解指代、歷史佔多少 token、語意快取 key 與上下文的關係。不定義則 Phase 8 `/chat` 無法實作；且任意送全史會直接放大 input token（與 DL-009 省 token 方向牴觸）。

### 提案（v1，零額外 LLM token 為原則）
- **後端無會話狀態**：`conversation_id` 僅用於 query_logs 分組與前端 UX；`query_logs` 加 `conversation_id UUID NULL` 欄位。
- **追問判定（純規則、零 LLM 成本）**：當前訊息含中英指代詞（它/其/這/那/該/this/it/that…）或長度 < 8 字 → 視為追問（規則 OPEN，Phase 8 以追問型測例調校）。
- **檢索**：追問時 `retrieval_q = 前一則 user 訊息 + "\n" + 當前訊息`（不含 assistant 回答）；否則＝當前訊息。翻譯（DL-020）、encode、BM25 皆以 `retrieval_q` 進行——encoder 為本地服務，串接零成本。
- **生成**：追問時【使用者問題】帶前一問（「前一問：…／當前追問：…」），**不帶歷史回答、不帶先前檢索內容**。token 增量僅前一問文字（~30–80 tokens、只在追問時發生，<1% 輸入量，de minimis 已向專案負責人揭露）。
- **快取**：追問**不查也不寫**語意快取（答案依賴上下文，快取必錯）；非追問照 §6.4。
- **請求契約**：`/chat` 接受 AI SDK `useChat` 的 messages 形狀，但後端**只讀取最後兩則 user 訊息**；其餘歷史 **MUST NOT** 進任何 LLM payload。
- **OPEN（涉 token 費用，啟用須專案負責人核可後走 decisions.md）**：(a) 生成附最近 1–2 輪完整 Q/A（估 +500–1500 input tokens/追問）；(b) LLM query-rewrite/condense（+1 次小模型呼叫/追問）。兩者 MUST 經 RAGAS + 成本評估。
- 黃金題庫 **SHOULD** 於 Phase 11 增少量追問型案例驗證串接規則。

### 替代方案
(a) v1 純單輪（連串接都不做）：免費但追問體驗直接壞掉，而規則式串接成本為零；(b) LLM rewrite：檢索品質最佳但每追問多一次付費呼叫，列 OPEN。

### 影響評估
- 工作量：小（Phase 8 規則函式 + 契約節選；Phase 2 query_logs 一欄）。
- 回退成本：低（規則集中單處；升級到 rewrite 不改介面）。
- 金錢/硬體：基線零新增；唯追問時 +~30–80 input tokens（de minimis）。OPEN 項涉實質費用，未啟用。

---

## DL-022: query_logs 擴充 inference/client 紀錄 + log 分層政策（DB vs Redis）

- **狀態**：APPROVED（裁決者=專案負責人，2026-06-11）
- **背景**：使用者要求 AI inference 階段記錄 rate-limit 脈絡（ip/country/user_agent）與模型用量
  （model_used/tool_used/tokens/cost_usd），同時要求避免高頻 log 灌 DB 妨礙查詢；並全面授權
  實作端自行決定表/欄位設計（取代先前「新資料庫表先問」約定，原因=維護性顧慮延後處理）。
- **決議**：
  1. `query_logs` 擴充欄位（每回合一列）：`status`、`cache_hit`、`model_used`、`tool_used JSONB`、
     `tokens_in`、`tokens_out`、`cost_usd NUMERIC(12,6)`、`ip INET`、`country TEXT`（alpha-2）、
     `user_agent TEXT`（應用層截斷 ≤512）、`clinical_flavored BOOLEAN DEFAULT FALSE`（§6.7 MAY）。
     同時修 spec 內部矛盾：§6.5 MUST「👍/👎＋文字回饋」但 §3.2 `feedback` 為 SMALLINT
     存不了文字 → 加 `feedback_text TEXT`（2026-06-11 Codex 計畫審查 HIGH 發現）。
  2. 高頻事件（429 拒絕、token-bucket 狀態、abuse 計數）MUST NOT 逐筆入 DB；
     一律 Redis TTL 計數器（跨 worker 一致，禁 per-process in-memory），告警走觀測層（Phase 9）。
  3. 隱私：ip/country/user_agent/user_id MUST NOT 進 LLM payload；D-M 脫敏（Sentry/LangFuse）
     涵蓋上述欄位；country 推導 MUST 本地（GeoIP db），MUST NOT 呼叫外部 geo API。
  4. schema 細節（表/欄位/索引）授權實作端調整並於 PR 說明；DECIDED schema 持續回寫 §3.2。
- **影響**：§3.2（query_logs、ingest_errors 文件化）、§3.3（索引）、§6.8（限流紀錄分層）。

---

## DL-023: 離線建庫交易語意——批為提交邊界 + 每頁 savepoint；連 PgBouncer :6432

- **狀態**：APPROVED（委派）　**提案者**：main Claude　**日期**：2026-06-13　**影響檔案**：ARCHITECTURE.md §2.5、§2.6、§2.7
- **背景**：spec §2.5 SHOULD「整本書放在單一 transaction」與 §2.7 MUST `--resume`「從失敗頁繼續」在單一書交易下矛盾（單交易崩潰即整書 rollback，resume 無從續起）。roadmap Phase 4 已定「每頁 savepoint、整書非 all-or-nothing」。2026-06-13 Codex 對抗式審查另指出：書本識別若靠 title 猜測會續跑到錯的書/版本、上游階段（render/encode/upload）失敗未留痕、記錯本身失敗會 rollback 整批。
- **決議**：
  1. 提交邊界 = 批（`--batch-size`），非整書。每批一交易。
  2. 編碼（GPU）與 S3 上傳在 DB 交易**外**完成；交易只含該批 INSERT，短而快。
  3. 交易內逐頁 `SAVEPOINT`：成功 `RELEASE`、失敗 `ROLLBACK TO SAVEPOINT` + 同交易內寫 `ingest_errors`（stage='write'，且記錯再包一層獨立 savepoint，連記錯失敗都不波及同批成功頁），批末 `COMMIT`。
  4. 上游階段失敗（render 缺頁影像 / encode / upload / 整檔 parse）逐頁（或 book 層）以獨立短交易寫 stage-specific `ingest_errors` 並續跑（§2.7）；**不靜默丟棄**頁。
  5. 書本識別走顯式 `--book-id`（UUID）：不帶＝首次建庫新增一本；帶且無 `--resume`＝§2.6 重建（先 DELETE 該 book+kb_version 既有頁）；帶且 `--resume`＝跳過已完成頁。`--resume` **MUST** 搭 `--book-id`（不靠 title 猜書）。
  6. 連線走 `DATABASE_URL`（PgBouncer :6432、`statement_cache_size=0`），非 `PG_DIRECT_URL`（交易短、不跨編碼；分區建立 DDL 在 transaction pooling 下可執行）。
- **取代**：§2.5「整本書單一 transaction」SHOULD 在本專案降為「批單一 transaction」；不影響「patch 批次插入」「失敗 rollback」其餘 SHOULD/MUST。

---

## DL-024: Stage B 精排 v1 預設＝SQL；numpy 退路經 benchmark 推翻；並發 p95 gate 轉 Phase 12 擴展觸發器

- **狀態**：APPROVED　**提案者**：main Claude（委派）　**裁決者**：專案負責人（2026-06-13 選定 Option A）　**日期**：2026-06-13　**影響檔案**：ARCHITECTURE.md §1.7、§4.4、附錄 D.5；backend/retrieval/stage_b.py、engine_selfbuilt.py、scripts/bench_stage_b_concurrency.py

### 背景
§4.4 允許 Stage B 在 SQL 聚合不達 p95 預算時改用應用層 numpy XOR+popcount；§1.7 的 Stage B「<200ms」標註「數值待 Phase 5 壓測校準」；附錄 D.5 早已把「Stage B 並發壓測」列為 VectorChord（Phase 12）的擴展評估 gate。DL-013 單連線探針 p50≈157ms（餘裕 ~20%）。Phase 5 Codex 對抗式審查 #1 要求 benchmark 須以生產交易/連線占用建模（在 hnsw_search_txn + savepoint 內跑、latency 含 acquire/queue、生產級固定池、SQL/numpy 各自獨立 warmup）。

### Phase 5 並發/p95 benchmark 實測（2000 頁×1024 patches、K=100、20 tokens、pool=10、RTX 5060 Ti/WSL2）
| 並發 | SQL p50/p95 | numpy p50/p95 |
|---|---|---|
| 1 | 159 / 164ms | 184 / 198ms |
| 4 | 436 / 448ms | 862 / 906ms |
| 32 | 900 / 1229ms | 6544 / 10919ms |

### 決議（Option A）
1. **v1 production `stage_b_mode = sql`**（已為預設）。
2. **numpy 退路假設被推翻**：每個並發層級 numpy 皆比 SQL 慢（持 DB 連線做 Python popcount + 大量 `BitString.bytes` 轉換 + GIL 爭用，並發下惡化至 ~10×）。numpy 路徑**保留為已測非預設替代**（§4.7 介面後仍在、與 oracle 等價），但**不**作為並發退路。
3. **§1.7 Stage B「<200ms」校準結果**：單機 K=100 自建兩階段僅在 ~1 並發達標（159ms）；並發 ≥4 即超預算——此為附錄 D.5 早已預期之「Stage B ~20 RPS CPU 瓶頸點」。
4. **並發/p95 gate 轉為附錄 D.5 的 Phase 12 擴展觸發器**：突發並發（整班同時問）的 p95 達標歸 **VectorChord（Phase 12 PoC，原生 MaxSim）＋ encoder replica / GPU 升級**，不在 v1 範圍。v1 ~10k/月穩態落在達標區（encoder ~20 q/s 上限亦把 Stage B 到達率經 Little's law 限在 ~3 並發以下）。
5. Top-K 維持 100（DL-013 recall 保險）；未來若需並發餘裕，調 K 為便宜旋鈕，但須以真實 ColPali＋教材 recall 重驗（Phase 11）後才動。

### 回退成本
低（mode 為 `SelfBuiltEngine` 參數，改字串即切換；皆 in-Postgres／應用層、藏於 §4.7 介面後）。VectorChord 切換條件仍依 DL-014（recall＋p95＋運維三勝才換）。

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
6. **Redis fail-open（含序列化/mid-scan 失敗）**：`get`→miss、`set`/`clear`→no-op，絕不中斷 `/chat`（§1.8）。快取 hit-rate/failure **metrics 接 LangFuse/Sentry 屬 Phase 9**；本 phase 以結構化 warning log 為界並明示延後。
7. **語意向量比對列後續 config 開關**（`cache_mode="semantic"`）：啟用時用 **fastembed（ONNX, torch-free, `intfloat/multilingual-e5-small` 384d）**，**不**用 redisvl 預設 `HFTextVectorizer`（會把 torch 拉進 backend、破壞 torch-free 不變量）；`cosine_distance < cache_distance_threshold(0.05)` ≈ sim > 0.95。啟用前須補 RAGAS/誤命中評估。
8. **不新增套件**：`redis`/`redisvl` 已在 deps；本 phase runtime/測試皆零新套件。

### 後果
- 命中率上限受限於「字面正規化後相同」；換句話/繁簡同義待語意開關才涵蓋。先求安全側（低誤命中、命中即已驗證有引文）。
- 啟用 semantic 模式須走本決策第 7 點並過評估 gate 才上線。
