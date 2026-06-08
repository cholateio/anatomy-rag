# anatomy-rag 多模態 RAG 系統 — 主實作路線圖（v2，已納入 Codex 跨模型審查）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`（建議）或 `superpowers:executing-plans` 逐任務實作。每個階段在執行前產生它自己的 bite-sized 詳細子計畫（見 `docs/superpowers/plans/`）；本檔是**階段層級路線圖**，鎖定範圍、相依、驗收與工程決策。
>
> **權威 spec**：`docs/ARCHITECTURE.md`（§0–9 + 附錄 A–D）+ `docs/decisions.md`（DL-001~018）。spec 與本計畫衝突時以 spec 為準，並走 `decisions.md` 流程修訂。
>
> **v2 修訂來源**：2026-06-07 Codex（gpt-5.x）跨模型對抗式審查 + Claude 逐項評估。修正涵蓋：可跑性 CRITICAL（Docker workspace、make migrate、PgBouncer 啟動、CI 拓樸）、spec 自身矛盾（page_patches 分區、Top-K）、治理順序、被低估的整合去風險（Blackwell GPU、Vercel 協定、VectorChord bit/float）、評估 gate 提前。

**Goal:** 把 `docs/ARCHITECTURE.md` 的設計藍圖，分階段落地成一套「全新機器照 `SETUP.md` 就能跑起來」、規格功能皆有對應測試、帶強制引文與教育用途浮水印的地端多模態（圖+文）解剖學 RAG 問答系統。

**Architecture:** 兩條獨立路徑 —（1）**離線批次建庫**：Docling 解析 PDF → Markdown + 頁面 PNG，ColPali 編碼成多向量 → mean-pool + 二值化（`shared/binary.py` 單一來源）→ PostgreSQL；（2）**線上推理**：query 經獨立 GPU encoder 微服務編碼 → 兩階段檢索（Stage A HNSW 粗排 / Stage B MaxSim 精排）+ BM25 RRF 融合 → 條件式附圖 → OpenAI LLM（強制引文）→ SSE 串流回 Next.js。檢索引擎藏在 `backend/retrieval/` §4.7 介面後（**引擎中立查詢表示**，見 D-K）：**v1 baseline = pgvector 自建兩階段（bit128）**，VectorChord 為介面後的 PoC。

**Tech Stack:** Python 3.11+（uv）、FastAPI + sse-starlette + asyncpg、PostgreSQL 16 + pgvector ≥0.8（`bit(128)` + HNSW + pg_trgm）經 PgBouncer（transaction pooling）、Redis 7 + redisvl、ColPali `vidore/colpali-v1.3-hf`（transformers `ColPaliForRetrieval`，cu128 PyTorch）、Docling、OpenAI 標準付費 API（`gpt-5.5`/`gpt-5.4`，原生 `openai` SDK）、Next.js 14+ + Vercel AI SDK（`useChat`，UI Message Stream 協定）、RAGAS 0.4.x、LangFuse + Sentry、Docker Compose。

---

## A. 鎖定的工程決策（含已核准的 DL）

> 觸及 `DECIDED`/`MUST` 者，**Phase 0 Task 0.1**（治理優先）會先寫入 `decisions.md` 並回寫 `ARCHITECTURE.md` 後才允許後續實作。

| # | 決策 | 來源 | 觸及 spec | 治理紀錄 |
|---|---|---|---|---|
| **D-A 檢索排序** | v1 先做 **pgvector 自建兩階段（bit128）** baseline；**VectorChord 為 §4.7 介面後的 PoC** | 使用者授權自行決定 | 微調 DL-010 排序 | **DL-014（APPROVED）** |
| **D-B 移除 LlamaIndex** | 線上路徑不用 RAG 框架；移除**所有**規範性引用（§5.5、§8.1、Stack）+ repo 斷言無 `llama_index` | 使用者核准 | §5.5、§8.1 DECIDED | **DL-015（APPROVED）** |
| **D-C SSO 暫緩** | 可插拔 auth（dev stub + OIDC 介面文件化） | 使用者裁示 | §5.8 SSO MUST | **DL-016（APPROVED）** |
| **D-D Dev/Mock 模式** | mock encoder(CPU、決定性 `/encode_query` 契約)、mock LLM、auth dev stub；無 GPU/無憑證可跑**基礎設施 smoke**（真 `/chat` 在 Phase 8） | 工程＋使用者目標 | 無 | 採用 |
| **D-E Blackwell GPU** | cu128 PyTorch + SDPA（不裝 flash-attn）；CMD 用 `uv run --no-sync` 防 lockfile 還原 torch；**Phase 0 GPU smoke gate**（實機 build + `torch.cuda.is_available()` + 模型載入） | 研究發現 + Codex Dim5-1 | 無 | SETUP.md 逐行 |
| **D-F Postgres 映像** | baseline `pgvector/pgvector:pg16`；VectorChord PoC 才換 `tensorchord/vectorchord:pg16-*` | D-A | 無 | tag 落地現查並 pin digest |
| **D-G PgBouncer/SET LOCAL** | **Stage A 把 `SET LOCAL hnsw.ef_search=100` + HNSW SELECT 包在同一 transaction**（transaction pooling 下同一 txn 即 server-pinned，**不需** `track_extra_parameters`）；PgBouncer 用 edoburu **env 驅動**自動產生 userlist（不提交明文密碼） | 研究 + Codex Dim1-3/4 修正 | 無 | 採用（比研究建議更簡） |
| **D-H Vercel 協定** | AI SDK v5/v6 UI Message Stream；後端手刻**薄 emitter**（`backend/api/ai_stream.py`）；**Phase 0 即用 Node `toUIMessageStreamResponse()` 產生並提交 golden wire bytes**；引文走自訂 `data-sources` part（`onData`）；pin `ai`/`@ai-sdk/react` 精確版 + 提交 lockfile | 研究 + Codex Dim4-3/Dim5-2 | §5.6「不自寫」 | **DL-018（APPROVED）** |
| **D-I 快取** | redisvl `distance_threshold≈0.05`（≈相似度0.95）；本地 `HFTextVectorizer("intfloat/multilingual-e5-small")`；初期 exact-normalized-query；**只快取通過引文驗證的答案**並存驗證 metadata，命中時校驗 sources 屬 active kb_version | 研究 + DL-012 + Codex Dim2-4 | 無 | 採用 |
| **D-J RAGAS** | pin **0.4.x**；四核心指標餵 Docling MD；MultiModal* 抽檢；eval LLM 獨立 key 包 `LangchainLLMWrapper`；**eval 報告保留 ≥90 天 + Cohen's kappa<0.7 失敗路徑** | 研究 + Codex Dim4-10 | 無 | 採用 |
| **D-K 引擎中立查詢表示** | §4.7 介面定義**引擎中立查詢能力介面**（binary tokens / float multivector 兩 adapter），化解 self-built(bit128) 與 VectorChord(float) 的**輸入合約不相容**（非僅回傳同型別） | Codex Dim5-3 | §4.7 | 採用 |
| **D-L shared 拆分** | `shared/binary.py` **純 numpy 無 torch**；`colpali_runtime`（torch/transformers）移到 `anatomy-shared[colpali]` optional extra，backend/CI 不被拖入重型依賴 | Codex Dim5-7 | 無 | 採用 |
| **D-M PHI/外部回報脫敏** | **不**加內容層 PHI 攔截（與 §6.7 移除攔截衝突）；改為 **Sentry `before_send` + LangFuse trace 脫敏**（移除 query 文字/prompt/檢索內容/識別資訊）+ 持續 strip user_id（§5.8/§1.9） | Codex Dim2-2/6（部分接受） | §0.3、§6.5 | 採用（spec 相符解讀） |
| **D-N 引文驗證時序** | server `sources` event 由 retrieved top-3 衍生（**grounded by construction**）；LLM **行內引文**串流後驗證，無法佐證→**§5.4 警告 banner + 標示未驗證**（非 mid-stream 移除，避免違反 §1.7 TTFT/§5.6 串流 MUST）；未驗證答案不入快取 | Codex Dim2-3（部分接受/反駁緩衝） | §5.4、§5.6 | 採用（釐清政策） |
| **D-O encoder fallback** | **client 端 primary→fallback 邏輯保留（§5.1 MUST 在程式層滿足）**；Modal 部署 optional、dev fallback 指向 mock；Modal data-residency 走 DL 確認後才在 production 啟用 | Codex Dim4-5 | §5.1 MUST | 採用（MUST 程式層滿足） |
| **D-P 評估 gate 提前** | **最小 golden + recall@K by class harness 在 Phase 1/3/5 驗收前即引入**（§7.7 要求 encoder/pooling/binarize/檢索變更過評估）；Phase 11 擴充至完整 RAGAS + ≥110 題 | Codex Dim3-1 | §7.7 | 採用 |
| **D-Q page_patches 分區** | `page_patches` **加 `kb_version` 欄位並納入 PK**，方能依版本分區（PostgreSQL 分區鍵須在 PK 內），化解 §3.2 schema 與 DL-010 分區矛盾 | Codex Dim3-2 | §3.2 DECIDED schema | **DL-017（APPROVED）** |
| **D-R Top-K=100** | DL-013 已定 Stage A Top-K=100；**一致化 §4.6/§4.7/§8.2**（§4.7 `top_k=50` 預設改 100） | Codex Dim4-9 | §4.6/4.7/8.2 | 採用（DL-013 既有） |
| **D-S 版本 pin** | 關鍵 infra/前端依賴 pin 精確版本/image digest，提交所有 lockfile（uv.lock、package-lock.json） | Codex Dim5-8 | 無 | 採用 |

### Phase 0 Task 0.1 要寫入 `decisions.md` 的條目（皆 APPROVED，裁決者＝專案負責人，日期 2026-06-07）

- **DL-014（APPROVED）檢索排序**：v1 先實作並完整測試 pgvector 自建兩階段（§4.3/4.4/4.5/4.7/4.8）當可靠 baseline；VectorChord 維持 §4.7 介面、列為 PoC（Phase 12），以 recall@K + p95 + 運維實測勝出才切換。微調 DL-010「先 PoC 通過即只做它」的排序；兩者皆 in-Postgres、皆在介面後、回退成本低。
- **DL-015（APPROVED）移除 LlamaIndex**：線上路徑不採 RAG 框架；移除 §5.5、§8.1、技術棧的**所有**規範性 LlamaIndex 引用；檢索編排由 `backend/retrieval/orchestrator.py` 負責；加 repo 層級斷言（CI grep）確保無 `llama_index` 殘留依賴。
- **DL-016（APPROVED）SSO 暫緩**：`backend/api/auth.py` 抽象（`get_current_user`）：dev 注入固定 `user_id`；production 留 OIDC 介面與設定文件化。§5.8 SSO MUST 標注「接回校內 SSO 時生效」；`user_id`/限流/query_logs 照常（dev stub 供 user_id）。
- **DL-017（APPROVED）page_patches 加 kb_version**：§3.2 `page_patches` 加 `kb_version INTEGER NOT NULL`，PK 改 `(kb_version, page_id, patch_idx)`（或等效含 kb_version 的 PK），FK 對應 `pages(... kb_version)`；使 DL-010「按 kb_version 分區」可實作。回寫 §3.2 schema、§4.4 Stage B SQL（帶 kb_version）、§3.3 索引說明。
- **DL-018（APPROVED）Vercel UI Message Stream emitter**：AI SDK v5/v6 用 UI Message Stream 協定、無官方 Python lib → 後端手刻薄 emitter（集中 `backend/api/ai_stream.py`）為 §5.6「不自寫」的核准例外（前端仍用 `useChat`、不自寫 SSE/狀態）。回寫 §5.6、§6.3 的事件對應（`sources`→`data-sources` part；`onResponse`→`onData`；header `x-vercel-ai-ui-message-stream: v1`；`[DONE]` 收尾）。

---

## B. 完整檔案結構地圖

```
anatomy-rag/
├── docs/{ARCHITECTURE.md, decisions.md, superpowers/plans/}
├── SETUP.md  README.md  Makefile  .gitignore  .dockerignore
├── .env.example
├── pyproject.toml  uv.lock                 # uv workspace（含 [dependency-groups].dev）
├── docker-compose.yml                      # 核心（mock encoder，無 GPU；含 minio-init / pgbouncer healthcheck）
├── docker-compose.gpu.yml                  # override：真實 GPU encoder（cu128）
├── docker-compose.observability.yml        # override：LangFuse（獨立 DB，不違反 :5432 紅線）
├── .github/workflows/ci.yml                # unit job（無 DB）+ db-integration job（postgres+pgbouncer+migrate）
├── .pre-commit-config.yaml
│
├── shared/                                 # 跨端共用
│   └── src/anatomy_shared/{binary.py(純numpy), colpali_runtime.py(extra: colpali)}
│
├── backend/
│   ├── Dockerfile  alembic.ini
│   └── src/anatomy_backend/
│       ├── config.py                       # pydantic-settings（DSN 解析驗證 :6432）
│       ├── db/{pool.py(statement_cache_size=0), kb_version.py, migrations/}
│       ├── retrieval/{types.py, query_repr.py(引擎中立查詢表示,D-K), stage_a.py, stage_b.py,
│       │              bm25.py, rrf.py, engine.py(MaxSimEngine), engine_selfbuilt.py, orchestrator.py}
│       ├── encoder/client.py               # primary→fallback（§5.1 MUST，D-O）
│       ├── llm/{client.py, fallback.py, prompts.py, image_routing.py, translate.py(BM25/跨語言,D), mock.py}
│       ├── cache/semantic_cache.py         # 只快取已驗證答案（D-I）
│       ├── api/{main.py, chat.py, ai_stream.py(emitter,D-H), schemas.py, citations.py(驗證,D-N),
│       │        feedback.py(回饋 endpoint), auth.py(dev stub+OIDC), ratelimit.py}
│       └── observability/{tracing.py, errors.py(Sentry before_send 脫敏,D-M), alerts.py(§7.5 告警)}
│   └── tests/
│
├── colpali_service/{Dockerfile(GPU,cu128), Dockerfile.cpu, modal_app.py(optional),
│                    src/colpali_service/{main.py, encoder.py}, tests/}
├── ingest/src/anatomy_ingest/{docling_parser.py, page_render.py, colpali_encoder.py,
│                              writer.py(savepoint/checkpoint,D), storage.py, cli.py}
├── eval/{eval_thresholds.yaml,
│        src/anatomy_eval/{harness.py(最小recall harness,D-P), ragas_runner.py, gate.py,
│                          recall_by_class.py, retention.py(≥90天), kappa.py, review_tool.py,
│                          regression.py(錯誤案例升級)}}
├── frontend/{package.json(pin ai), package-lock.json, app/, components/, lib/}
├── infra/{postgres/init.sql(僅CREATE EXTENSION), pgbouncer/(env驅動), minio/(bucket init),
│          golden/ai_stream_golden.jsonl(D-H wire bytes)}
└── tests/golden_qa.jsonl
```

---

## C. 相依關係與階段總覽

```
Phase 0 (環境+骨架; 治理優先 Task0.1)
   ├─> Phase 1 (shared/binary.py 純numpy) ──> [評估 harness 種子 D-P]
   │       ├─> Phase 3 (encoder 微服務 + 決定性 mock /encode_query)
   │       └─> Phase 4 (離線建庫) ── 需 Phase 2 + Phase 1
   ├─> Phase 2 (DB 層: page_patches 含 kb_version, 分區; migrations)
   │       └─> Phase 5 (兩階段檢索 + 引擎中立查詢表示) ── 需 Phase 2 + 假資料
   │               需 Phase 6 的 translate.py（BM25 餵英文 query, DL-013）
   ├─> Phase 6 (LLM 層 + translate.py)   [可獨立, 用 mock]
   ├─> Phase 7 (語意快取, 只快取已驗證)   [需 Redis + Phase 8 驗證結果]
   └─> Phase 9 (觀測性 + Sentry 脫敏 + 告警)  [橫切]
Phase 8 (API + SSE) ── 整合 3/5/6/7 + auth + ratelimit + 引文驗證 + feedback endpoint
Phase 10 (前端) ── 需 Phase 8 SSE 契約 + Phase 0 golden wire bytes
Phase 11 (評估擴充) ── 完整 RAGAS + ≥110 題 + 保留/kappa/回歸升級
Phase 12 (VectorChord PoC; 引擎可選但 PoC/並發 benchmark 為上線 gate, 附錄 D.5)
Phase 13 (整合測試 + 文件, final)

評估/recall gate（D-P）：Phase 1/3/5 驗收前必過「最小 golden + recall@K by class」harness。
Stage B 並發/p95 benchmark gate（附錄 D.5）：v1 上線前必過。
```

| 階段 | 名稱 | 對應 spec | 相依 | 規模 |
|---|---|---|---|---|
| 0 | 環境與專案骨架（治理優先） | 附錄 A/B、§1.5、§3.4、§6.9 | — | 大（最高優先） |
| 1 | 共用二值化 `shared/`（+ 評估 harness 種子） | §2.3、§2.4、§7.7 | 0 | 中 |
| 2 | 資料庫層 + migrations（page_patches 含 kb_version） | §3、DL-017 | 0 | 中 |
| 3 | ColPali encoder 微服務（+ 決定性 mock） | §5.1、§4.2 | 1 | 中 |
| 4 | 離線建庫管線（savepoint 語意） | §2 | 1,2 | 大 |
| 5 | 兩階段檢索（引擎中立查詢表示, baseline） | §4 | 2,(6 translate) | 大（核心） |
| 6 | LLM 層 + 翻譯模組 | §5.3/5.4/5.5、§6.1、DL-008/013 | 0 | 中 |
| 7 | 語意快取（只快取已驗證） | §6.4 | 0,8 | 小 |
| 8 | API 與 SSE（emitter + 引文驗證 + feedback） | §1.6/1.8、§5.6/5.7/5.8、§6.3/6.8 | 3,5,6,7 | 大（核心） |
| 9 | 觀測性（trace + Sentry 脫敏 + 告警） | §6.5、§7.5 | 0 | 中 |
| 10 | 前端 | §5.6、§6.7 | 8 | 中 |
| 11 | 評估擴充（RAGAS + 保留/kappa/回歸） | §7 | 1,4,8 | 中 |
| 12 | VectorChord PoC（引擎可選；PoC/並發 gate 必過） | §4.1/4.7、DL-007/010、附錄 D.5 | 5 | 中 |
| 13 | 整合測試 + 文件（final） | §6.6、§7.7、整體 | 全部 | 中 |

---

## D. 各階段詳述

> 格式：**目標 / 產出 / 驗收標準 / 風險與研究註記**。每階段執行前產生其 bite-sized 詳細子計畫。Phase 0 已備獨立詳細計畫。

### Phase 0 — 環境與專案骨架（治理優先）★最高優先（見獨立詳細計畫 v2）

- **目標**：建立 monorepo 骨架與可重現環境，讓全新機器照 `SETUP.md` 起核心 stack（mock encoder/LLM、無 GPU/無憑證）通過**基礎設施 smoke test**（健康探針 + 決定性 mock `/encode_query`），並落定環境變數/版本 pin/Docker/CI/治理更新。**真正的端到端 `/chat` 在 Phase 8**（Phase 0 不誇稱 e2e）。
- **產出**（相對 v1 的新增/修正）：
  - **Task 0.1（治理優先）**：`decisions.md` 寫入 DL-014~018（皆 APPROVED）；回寫 `ARCHITECTURE.md`（§3.2 page_patches 加 kb_version、§5.5/§8.1 移除 LlamaIndex、§5.6/§6.3 Vercel emitter、§5.8 SSO 暫緩、§4.6/4.7/8.2 Top-K=100）。**先做完才允許後續任務。**
  - `SETUP.md`（dev + production 兩路徑、逐行、成功輸出、排錯；含 GPU/cu128 段與 **GPU smoke gate**）。
  - `.env.example`（全部變數 + dev/mock 旗標 + LangFuse secrets 佔位 + 用途註解）。
  - uv workspace（根含 `[dependency-groups].dev`）；`shared` 拆 binary(純numpy)/colpali(extra)（D-L）；各 `pyproject` pin 版本（D-S）。
  - Docker：各 Python Dockerfile **複製全部 workspace 成員**（D 修 Codex Dim1-1）；`docker-compose.yml`（pgbouncer **env 驅動** + healthcheck、encoder/frontend healthcheck、**minio-init** bucket、backend depends_on pgbouncer `service_healthy`）；`gpu.yml`（cu128，CMD `uv run --no-sync`）；`observability.yml`（LangFuse **獨立 DB**、secrets 由 .env、不違反 :5432 紅線）。
  - `infra/pgbouncer/`（env 驅動，不提交明文密碼）、`infra/postgres/init.sql`（僅 CREATE EXTENSION）、`infra/golden/ai_stream_golden.jsonl`（**Node 產生的 UI message stream wire bytes**，D-H）。
  - `backend/config.py`（**DSN 解析驗證**須 :6432，D 修 Codex Dim1-11）、`/healthz` `/warmup`、決定性 mock `/encode_query`。
  - `Makefile`（`migrate` 在 backend container 內跑、`gpu-smoke`、`golden-bytes`）、CI（**unit job 無 DB + db-integration job 含 postgres+pgbouncer+migrate**，D 修 Codex Dim1-10）、pre-commit、`README.md`。
- **驗收標準**：
  - 全新機器 `make up` → `curl :8000/healthz`=`{"status":"ok"}`、`curl :8001/healthz`=ready、`POST :8000/encode_query`（mock，決定性）回固定向量、`:3000` 顯示骨架頁、`make migrate` 在 container 內成功（Phase 0 為 framework no-op，驗收明寫「僅驗 migration framework 可執行」）。
  - `docker compose config`（核心/gpu/obs）三者通過；**unit CI 綠**（不連 DB）；`pre-commit` 綠；`gitleaks` 無洩漏（含 LangFuse secrets 不入 git）。
  - **GPU smoke gate**（實機，非 CI）：GPU image build 成功、容器內 `torch.cuda.is_available()` True、ColPali 載入無 uninitialized weights。production 驗收前必過。
  - **golden wire bytes** 已提交（Phase 8/10 emitter 對照基準）。
  - Task 0.1 治理更新完成且 `grep LlamaIndex` 無殘留規範性引用。
- **風險與研究註記**：Blackwell cu128（D-E）；PgBouncer 用同一 txn 內 SET LOCAL 免 track_extra_parameters（D-G）；映像 pin digest（D-S）。

### Phase 1 — 共用二值化 `shared/` + 評估 harness 種子

- **目標**：實作離線端與 query 端**唯一**二值化/池化來源（§2.4），`binary.py` **純 numpy 無 torch**（D-L）；ColPali runtime（含 mock）在 `[colpali]` extra；建立**最小 recall harness 種子**（D-P）。
- **產出**：`binary.py`（`binarize(vec)->bytes(16)` sign-based、`pool_patches(...)`：**fp32 mean → 去 padding/特殊 token → 重新 L2 normalize → binarize**、`hamming_distance`；輸入接受 numpy/list，不依賴 torch）；`colpali_runtime.py`（`ColPaliForRetrieval`+`ColPaliProcessor`、bf16、SDPA、`MockColPaliRuntime` 決定性）在 extra；`eval/harness.py` + `tests/golden_qa.seed.jsonl`（每類 2–3 題）。
- **驗收標準**：binarize round-trip 一致 + 位序對照 §2.4；pool 排除 padding/前綴；mock runtime 形狀/決定性；**`binary.py` import 不拉 torch**（測試斷言）；harness 能對假資料算 recall@K by class。
- **風險與研究註記**：mean 必 fp32；ColPali token L2-normalized → pool 後重 normalize；兩端 import 同函式（CI grep 禁他處重複定義 binarize）。

### Phase 2 — 資料庫層 + migrations（page_patches 含 kb_version）

- **目標**：schema（**DL-017：`page_patches` 含 `kb_version`、PK 含之、按 kb_version 分區**）、索引、Alembic 可逆遷移、asyncpg pool（連 :6432、`statement_cache_size=0`）、kb_version 輔助、**Stage A SET LOCAL 包同一 transaction 的 helper**（D-G）。
- **產出**：migrations `001_extensions`…`004_page_patches`（含 kb_version + 分區）…`006_indexes`（HNSW `bit_hamming_ops`、GIN、kb_version）…`007_ingest_errors`；`pool.py`、`kb_version.py`、`tx_helpers.py`（`async with conn.transaction(): SET LOCAL ...; SELECT ...`）。
- **驗收標準**：`upgrade head`/`downgrade base` 可逆無殘留；對真實 Postgres 插假資料，`bit(128) <~>` 可運算、HNSW/GIN 存在、分區生效（不同 kb_version 落不同分區）；pool 連 6432 + `statement_cache_size=0`（屬性斷言）；所有 `pages`/`page_patches` 查詢帶 `WHERE kb_version`；**CI db-integration job 跑 migration + 這些測試**。
- **風險與研究註記**：分區鍵須在 PK（DL-017）；§4.4 Stage B SQL 也帶 kb_version。

### Phase 3 — ColPali encoder 微服務 + 決定性 mock

- **目標**：FastAPI :8001 `/encode_query`（`tokens_bin[]`+`pooled_bin`）、`/healthz`(readiness)、`/warmup`；真實 ColPali(cu128)+**決定性 mock**（D-D，下游可演練契約）；用 `shared[colpali]`。
- **產出**：`main.py`、`encoder.py`、Dockerfile(GPU,cu128,`--no-sync`)、Dockerfile.cpu、`modal_app.py`(optional)。
- **驗收標準**：mock `/encode_query` 決定性、無 GPU 可跑（CI）、契約（token 數、pooled 16 bytes、base64）；真實模式（GPU smoke）載入無 uninitialized 警告、binarize 與離線端一致；readiness 行為正確；**過 recall harness gate**（D-P）。
- **風險與研究註記**：transformers 版本 pin；Blackwell cu128/SDPA；Modal data-residency 走 DL 才在 production 啟用。

### Phase 4 — 離線建庫管線（savepoint 語意）

- **目標**：CLI：PDF+YAML → Docling MD+metadata、pdf2image PNG → ColPali 編碼 → `shared` 二值化 → 寫 pages/page_patches(帶 kb_version) + MinIO PNG；**明訂 transaction 語意**（D 修 Codex Dim3-4）。**MUST NOT 呼叫雲端 LLM。**
- **產出**：`docling_parser.py`、`page_render.py`、`colpali_encoder.py`、`writer.py`（**每頁 savepoint：成功 release、失敗 rollback 至 savepoint 並寫 `ingest_errors` 後續跑，整書非單一 all-or-nothing**）、`storage.py`、`cli.py`（`--kb-version`/`--resume`/`--batch-size`）。
- **驗收標準**：小樣本 PDF 成功寫入、pages 數=頁數、patches/pooled_bin 存在、PNG 上 MinIO（**依賴 minio-init bucket**）；metadata 規範化（§3.2 + figures[]）；`--resume` 續跑、單頁失敗寫 ingest_errors 不中斷、抽樣校驗 5%；測試斷言**無任何雲端 LLM 呼叫**（網路 mock）。
- **風險與研究註記**：Docling v2 API；大 PDF 自切頁 + checkpoint；逐頁 MD 漏字抽檢；pdf2image 需 poppler。

### Phase 5 — 兩階段檢索（引擎中立查詢表示, baseline）

- **目標**：§4.7 介面 + **引擎中立查詢表示**（D-K）+ self-built 引擎：Stage A、Stage B(MaxSim)、BM25、RRF、orchestrator（DL-002 序列、IN 不保序重排、單一 SQL metadata、帶 kb_version、**Top-K=100** D-R）。
- **產出**：`query_repr.py`（`QueryRepr`：暴露 binary tokens + 可選 float multivector + capability flags；self-built 用 binary、VectorChord 用 float adapter）、`types.py`、`stage_a.py`、`stage_b.py`、`bm25.py`、`rrf.py`、`engine.py`(介面)、`engine_selfbuilt.py`、`orchestrator.py`。
- **驗收標準（§4.8）**：`test_stage_a`（100 頁假資料、Top-K=100、metadata/kb_version）；`test_stage_b`（5 頁、MaxSim 與手算一致、只掃候選）；`test_rrf`；`test_orchestrator`（5 題迷你 golden、回 `list[RetrievalResult]` 順序=RRF）；Stage B/BM25 單 conn 序列；Stage A SET LOCAL 在同 txn；**BM25 餵 `translate.py` 的英文 query（DL-013）**；**過 recall harness gate**（D-P）；**Stage B 並發/p95 benchmark gate**（附錄 D.5）。
- **風險與研究註記**：MaxSim SQL `<~>` 或 `bit_count(a # b)`；DB 連線不跨 LLM 串流（Phase 8 落實）；VectorChord float 路徑由 D-K adapter 承接（Phase 12）。

### Phase 6 — LLM 層 + 翻譯模組

- **目標**：原生 `openai` SDK 串流、模型 fallback、版本化 prompt、條件式附圖（DL-009）、strip user_id、**翻譯模組**（DL-008/013：語言偵測 + 中→英 MT，BM25/檢索用；含決定性 mock）、mock LLM。
- **產出**：`client.py`、`fallback.py`（tenacity、連 3 次 5xx/429 切 gpt-5.4）、`prompts.py`、`image_routing.py`、`translate.py`（`detect_lang` + `to_english`；mock 為查表/identity）、`mock.py`。
- **驗收標準**：mock 串流 token；fallback 計數切換；image_routing 表驅動（pure_text→0 圖、figure_heavy→1~2 圖 detail:high）；payload **不含 user_id**（斷言）；`translate.py` 對中文 query 回英文（mock 決定性）、英文 query identity；**過 recall harness gate**（翻譯影響檢索 D-P）。
- **風險與研究註記**：gpt-5.5/5.4 影像 token 計法未定（§9.10 不嵌成本邏輯）；解剖術語誤譯風險低但 MT 失敗要 fallback 原文。

### Phase 7 — 語意快取（只快取已驗證）

- **目標**：`SemanticCache`（redisvl + 本地 embedding、kb_version 獨立 index、`distance_threshold≈0.05`、TTL）、初期 exact-normalized-query；**只快取通過引文驗證的答案 + 存驗證 metadata**（D-I/Codex Dim2-4）。
- **產出**：`semantic_cache.py`：`get/set`、`HFTextVectorizer(multilingual-e5-small)`、命中校驗 sources 屬 active kb_version 否則 miss、**拒絕快取未驗證答案**。
- **驗收標準**：真 Redis：set/get 命中、跨 kb_version 不命中、相似不同題不誤命中；Redis 失敗視為 miss 不拋；未驗證答案不入快取（斷言）；版本切換清空。
- **風險與研究註記**：distance 非相似度（D-I）；本地 embedding 不呼叫 OpenAI（DL-012）。

### Phase 8 — API 與 SSE（emitter + 引文驗證 + feedback）★核心整合

- **目標**：`/chat` 串接 快取→encoder→檢索→條件式附圖→LLM→**UI Message Stream SSE**（D-H emitter，`data-sources` 在第一個 text-delta 前）；可插拔 auth（dev stub）、Redis 限流；**DB 連線不跨 LLM 串流**（DL-012）；**引文驗證政策**（D-N）；**feedback endpoint**（Codex Dim3-6）；`/healthz`/`/warmup` 全鏈路預熱。
- **產出**：`main.py`、`chat.py`、`ai_stream.py`（emitter + **對 `infra/golden/ai_stream_golden.jsonl` 的對照測試**）、`schemas.py`、`citations.py`（server sources grounded + 行內引文驗證 + 警告 banner 標示）、`feedback.py`（寫 `query_logs.feedback`、auth、驗證）、`auth.py`、`ratelimit.py`。
- **驗收標準**：端到端（mock encoder/LLM + 假資料）SSE 事件序：`start`→`data-sources`→`text-delta`*→`text-end`→`finish`→`[DONE]`，**sources 在第一個 delta 前**；header `x-vercel-ai-ui-message-stream: v1`；**golden bytes 對照通過**；DB 連線於 retrieve+圖 fetch 後歸還、不跨串流（連線計數斷言）；限流 429+Retry-After（多 worker 一致）、admin 不受限；**未驗證行內引文 → 警告 banner + 標示、不入快取**；payload 無 user_id；錯誤矩陣（encoder/LLM 全失敗推 error event）；收尾 `asyncio.create_task`；feedback endpoint 寫入 + 測試。
- **風險與研究註記**：最高整合風險＝協定 wire-format（D-H golden 去風險）；sse-starlette 15s ping 需實測不干擾 parser；client disconnect 取消生成。

### Phase 9 — 觀測性（trace + Sentry 脫敏 + 告警）

- **目標**：LangFuse 全鏈路 trace、Sentry 錯誤（**`before_send` 脫敏**：移除 query 文字/prompt/檢索內容/識別資訊，D-M）、**§7.5 強制告警**有 owner（以 Sentry/LangFuse 為基礎，Prometheus 延後，Codex Dim4-7）。
- **產出**：`tracing.py`、`errors.py`（Sentry init + before_send 脫敏 processor）、`alerts.py`（p95>8s、模型錯誤率>5%、RPM/TPM>80% → 通知）；LangFuse **獨立 DB**（D-M/Codex Dim2-1）。
- **驗收標準**：trace 全鏈路可見（手動 + span 包裝不改回傳）；**Sentry before_send 移除敏感欄位（測試注入 query 文字斷言被遮蔽）**；告警條件單元測試；LangFuse 缺席 fail-open；trace 含 user_id 但 user_id 不送 LLM。
- **風險與研究註記**：DL-011 觀測先 LangFuse+Sentry；LangFuse v3 部署較重（獨立 DB/依官方 self-host compose）。

### Phase 10 — 前端

- **目標**：Next.js App Router + `useChat`+`DefaultChatTransport` 指後端 `/chat`；渲染 `data-sources` 引用面板、串流文字、免責同意視窗、👍/👎 回饋（打 Phase 8 feedback endpoint）、教育用途浮水印、**未驗證引文警告 banner**（D-N）。
- **產出**：`app/`、`components/{ChatPanel,CitationPanel,DisclaimerModal,FeedbackButtons,Watermark,UnverifiedBanner}`、`lib/`；pin `ai`/`@ai-sdk/react` + 提交 `package-lock.json`（D-S）。
- **驗收標準**：對後端/mock SSE：先顯示引用面板→串流文字→完成；免責視窗持久化；底部浮水印；👍/👎+文字寫入；倒讚提示回報；未驗證引文顯示 banner；全繁體中文。
- **風險與研究註記**：D-H（`data-sources` persistent part、`onData`）；鎖 ai 精確版、勿用 canary；text-stream 模式不可用。建議搭配 `frontend-design` skill。

### Phase 11 — 評估擴充（RAGAS + 保留/kappa/回歸）

- **目標**：完整 RAGAS（eval LLM 獨立 key）、≥110 題黃金題庫、`eval_thresholds.yaml` + gate（人工審核）、recall@K by class（DL-013）、**eval 報告保留 ≥90 天**、**Cohen's kappa<0.7 失敗路徑**、**錯誤案例自動升級回歸題庫**（Codex Dim4-8/10）、人工抽檢工具。
- **產出**：`ragas_runner.py`、`gate.py`、`recall_by_class.py`、`retention.py`、`kappa.py`、`regression.py`、`review_tool.py`(Streamlit)、`eval/eval_thresholds.yaml`、`tests/golden_qa.jsonl`（擴充 seed→≥110，待教師標註）。
- **驗收標準**：jsonl schema 驗證（**無 should_refuse**，§7.2）；四指標 + oos_correctness，contexts 餵 Docling MD（D-J）；gate 未達標 exit 1、門檻檔變更需人工審核（CODEOWNERS）；recall by class（含中文 query）；保留 ≥90 天（持久化）；kappa<0.7 報告/重寫路徑；👎/錯誤案例升級為回歸題；CI RAGAS gate（固定 model、temp 0）。
- **風險與研究註記**：RAGAS 0.4.x V2、`LangchainLLMWrapper`；多模態指標覆蓋窄；題庫不得 leak 到 fine-tuning。

### Phase 12 — VectorChord PoC（引擎可選；PoC/並發 gate 必過）

- **目標**：§4.7 介面 + D-K float adapter 後實作 `VectorChordEngine`，小資料集 benchmark vs self-built（recall@K + p95 + 運維 + 並發）。**引擎切換 optional，但 PoC/並發 benchmark 為上線 gate（附錄 D.5、DL-010），不得靜默略過**（Codex Dim4-6）。
- **產出**：`engine_vectorchord.py`、benchmark 腳本、結果報告；Postgres 換 `tensorchord/vectorchord:pg16-*`(override)。
- **驗收標準**：VectorChord `vector(128)[]`+`@#` MaxSim 經 D-K float adapter 回同介面結果；benchmark 報告含 recall@K by class + p95 + 並發；切換決策寫 `decisions.md`。
- **風險與研究註記**：VectorChord float + 自有量化（與 bit128 分歧，靠 D-K adapter）；社群較小、rebuild 成本實測。

### Phase 13 — 整合測試 + 文件（final）

- **目標**：端到端整合測試、kb_version blue-green runbook、`SETUP.md` 全新機器驗證、文件、跨模型最終審查。
- **產出**：端到端整合測試（真實 compose、mock 或受控真實）、`docs/runbook-kb-version.md`、`docs/operations.md`、更新 README/SETUP、`decisions.md`/spec 一致性檢查。
- **驗收標準**：全新機器照 SETUP.md 跑到 `/chat` 端到端（含引用面板+浮水印）；整合測試涵蓋 ingest→檢索→/chat→SSE→前端 + kb_version 切換演練；spec 覆蓋檢查表逐項對應；RAGAS gate 綠；最終 Codex 審查 critical/high 已解。
- **風險與研究註記**：影像 token 校準（§9.10）；blue-green patch/RAM ×2（附錄 D）。

---

## E. 環境交付總覽（SETUP.md / .env.example / Docker）

- **必裝軟體與版本**：Docker Engine ≥24 + Compose v2、uv ≥0.11、Node ≥20、（GPU 路徑）NVIDIA Driver 支援 CUDA 12.8 + nvidia-container-toolkit、poppler。版本 pin（D-S）。
- **`.env.example`**：附錄 A 全部 + dev/mock 旗標（`ENCODER_MOCK`/`LLM_MOCK`/`AUTH_MODE`/`DEV_USER_ID`/`CACHE_LOCAL_EMBED_MODEL`/`CACHE_DISTANCE_THRESHOLD`）+ LangFuse secrets 佔位，逐一附用途/範例。
- **Docker 啟動**：`make up`/`make up-gpu`/`make up-obs`；`make migrate`（container 內）/`make gpu-smoke`/`make golden-bytes`/`make ingest-sample`/`make test`/`make eval`/`make down`。
- **每步成功輸出 + 排錯**：SETUP.md 每步附預期輸出與排錯（cu128 `torch.cuda.is_available()`、PgBouncer env 驅動、MinIO bucket、port 衝突、pgvector 擴充、poppler、Docker workspace build）。

---

## F. 完成定義（DoD）

1. 照 `SETUP.md` 在**全新機器**跑起來（mock 端到端 smoke 通；真實 GPU/OpenAI 路徑文件齊全且 GPU smoke gate 通過）。
2. spec 功能模組皆實作且有**對應測試**（覆蓋檢查表逐項對應）。
3. 評估：Phase 1/3/5/6 過最小 recall harness gate；Phase 11 完整 RAGAS gate 綠 + 保留/kappa/回歸；Stage B 並發/p95 gate 通過；`eval_thresholds.yaml` 在版控且變更需人工審核。
4. 合規紅線：強制引文 + 引文真實性驗證（D-N）、教育浮水印、離線管線無雲端 LLM、LLM payload 無 user_id、Sentry/trace 脫敏（D-M）、密鑰不入 git（gitleaks）、向量/log 存校內、應用層連 :6432、LangFuse 獨立 DB。
5. 使用者不需再追問「怎麼啟動／怎麼設定」。
6. 觸及 DECIDED/MUST 的偏離皆記 `decisions.md`（DL-014~018）並回寫 spec。
7. 跨模型最終審查通過。

---

## G. 執行方式與下一步

- **計畫狀態**：本 v2 已納入 Codex 跨模型審查的全部 accepted 修正 + 你核准的 DL-014~018。建議下一步：(1) 你過目本 v2 + Phase 0 v2 詳細計畫；(2) 核准後從 **Phase 0 Task 0.1（治理優先）** 開始執行。
- **執行模式（superpowers）**：1) **Subagent-Driven（建議）** 每任務派新 subagent + 任務間審查；2) **Inline Execution** 本 session `executing-plans` 批次 + 檢查點。
- **Phase-level 審查（full profile）**：對 MUST 審查項（auth、schema migration DL-017、檢索核心、引文/合規、Vercel emitter）做跨模型（Codex）phase 審查。各後續階段執行前生成 bite-sized 子計畫。
