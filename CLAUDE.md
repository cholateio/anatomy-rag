# anatomy-rag — 解剖學科多模態 RAG 系統

> CLAUDE.md (kit v3.1, profile-aware). Fill the [PLACEHOLDERS] below on first
> use - or just run `claude` and paste the bootstrap prompt from PROMPTING.md
> (section 0) and let it fill them for you.
> The "Multi-Agent Workflow Rules" half is generic and rarely needs editing.

## Project goal

給單一醫學系內部使用、以解剖學參考書為基礎的**多模態（圖+文）RAG 問答系統**：
學生用中英文提問 → 視覺檢索匹配的教科書頁面 → 雲端 LLM 基於檢索結果生成**帶強制引文**
的串流回答；教材範圍外的提問一律回「教材中查無此項」而非編造。

> **重要：目前 repo 尚無任何應用程式碼。** 整個系統以設計藍圖形式存在於 `docs/`
> （`docs/ARCHITECTURE.md` + `docs/decisions.md`）。`docs/ARCHITECTURE.md` 即本專案的**權威需求來源（authoritative spec）**，
> 等同 workflow 規則中的 `docs/specs/`：實作前先讀 `ARCHITECTURE.md`、先審 spec、不要重新 brainstorm 範圍。
> 下面的 Stack / File layout 描述的是 **`docs/` 規劃的目標架構**，實作時以 `docs/` 為準。

## Stack

（以下為 `docs/ARCHITECTURE.md` §8 決策總表定案選型；版本號為起手值，落地時取最新穩定版並於 PR 註明）

- Language: Python 3.11+（後端 / 建庫 / 評估）、TypeScript 5+（前端）
- Backend: FastAPI 0.110+（async + SSE）、uvicorn、sse-starlette、LlamaIndex（RAG 編排）、pydantic v2
- Frontend: Next.js 14+（App Router）、Vercel AI SDK（`useChat` + SSE）、TailwindCSS、shadcn/ui
- Datastore: PostgreSQL 16+ + pgvector ≥ 0.8（patch `bit(128)` Hamming；pooled `halfvec(128)` HNSW cosine，DL-019）+ pg_trgm；**經 PgBouncer 1.21+（transaction pooling）連線**
- Cache: Redis 7+（語意快取 via `redisvl` + token-bucket 限流）
- ML/模型: ColPali `vidore/colpali-v1.3-hf`（視覺多向量）、Docling（PDF→Markdown）、pdf2image+poppler；
  LLM 主 `gpt-5.5` / 備 `gpt-5.4`（**OpenAI 標準付費 API**）
- DB client / migration: asyncpg（連 6432）、Alembic
- Build/run: Docker + Docker Compose（v1 不用 Kubernetes）
- Test: pytest + pytest-asyncio；評估用 RAGAS（黃金題庫 `tests/golden_qa.jsonl`，CI gate）
- 觀測: LangFuse（自託管 trace）、Prometheus+Grafana、Sentry

## File layout

`docs/` 為現存目錄（系統藍圖）。實作時依 `docs/ARCHITECTURE.md` 附錄 B 的目標結構：

```
anatomy-rag/
├── docs/              # 系統藍圖（現存，唯一已有內容）；權威 spec
├── backend/
│   ├── api/           # FastAPI routes（/chat /healthz /warmup）
│   ├── retrieval/     # Stage A/B、BM25、RRF、orchestrator
│   ├── llm/           # vendor 抽象、model fallback
│   ├── encoder/       # ColPali client（含 fallback）
│   ├── cache/         # 語意快取
│   ├── observability/ # LangFuse / Sentry
│   ├── db/migrations/ # Alembic（001_…遞增編號）
│   └── tests/
├── frontend/          # Next.js（app/ components/ lib/）
├── ingest/            # 離線建庫 CLI（docling_parser / colpali_encoder / binarize / cli）
├── colpali_service/   # 獨立 GPU 微服務（FastAPI :8001 /encode_query）
├── eval/              # RAGAS + 抽檢工具
├── infra/             # docker-compose、pgbouncer/、prometheus/
└── tests/golden_qa.jsonl
```

關鍵架構：**離線批次建庫**（Docling 解析 + ColPali 編碼 + 二值化 → Postgres）與
**線上推理**（query encode → 兩階段檢索 Stage A HNSW 粗排 / Stage B MaxSim 精排 → LLM → SSE）
是兩條獨立路徑；ColPali encoder 為**獨立微服務**，後端透過 HTTP 呼叫。

## Coding standards

- **共用二值化**：離線端與 query 端 binarize 必須是**同一份函式**（抽到 `shared/binary.py` 兩端 import）；
  不一致會直接讓檢索精度崩壞。
- **DB 存取**：一律連 PgBouncer `:6432`，**禁止**直連 `:5432`（migrations 例外，走 `PG_DIRECT_URL`）。
  transaction pooling 下用 asyncpg 並設 `statement_cache_size=0`；禁用 prepared statements / `LISTEN`/NOTIFY / temp tables。
- **所有 `pages` 查詢 MUST 帶 `WHERE kb_version = :active`**；Stage B SQL 也要帶 kb_version。
- **SSE**：`sources` event 必在第一個 `delta` 之前送出；用 sse-starlette + Vercel AI SDK，不自寫；
  LLM 呼叫一律 `stream=True`；收尾的 log/cache 寫入用 `asyncio.create_task`，不要 `await` 卡在主串流。
- **LLM 影像**：條件式附圖（純文字題不送圖；圖譜頁用 `detail:"high"`，見 `docs/ARCHITECTURE.md` §5.5 / DL-009）；LLM 呼叫前**strip 掉 user_id/學號等識別資訊**。
- vendor 呼叫用 `tenacity` 包 exponential backoff + jitter；連續 3 次 5xx/429 自動切備援模型。
- schema 變更一律走 Alembic 且**含可逆 `downgrade`**；禁止對生產手動 `ALTER TABLE`。
- 秘鑰走 `.env` + KMS/Doppler/Vault，**禁止 hardcode**、禁止在 log 印完整 API key；CI 跑 gitleaks。
- 對 `WHERE page_id IN (...)` **不可假設保序**，排名在 Python 端依 RRF 順序重排。
- 任何 LLM/prompt/encoder/HNSW/pooling/reranker 變更 **MUST 經 RAGAS 評估**通過才上線。

## Project-specific constraints

> 本段是本專案最重要的規則。`docs/` 文件慣例：**MUST / MUST NOT** 為硬性要求，
> **DECIDED** 為已定案不可在實作中擅自變更，**OPEN** 為待實測決定。

**不該碰 / 不可擅改的區域：**

- **`docs/` 內的 `DECIDED` 決策不可在實作中變更。** 有強烈技術理由時，先在 `docs/decisions.md`
  新增 `PROPOSED` 提案，等 reviewer 改為 `APPROVED` 後才實作；**不要直接改 `docs/`，也不要在 PR 夾帶設計變更**。
- **`docs/` 與黃金題庫 `tests/golden_qa.jsonl` 為人工+agent 共同維護**；變更需附 PR 說明。
- **`eval_thresholds.yaml`（RAGAS 門檻）變更需人工審核**——防止偷偷降低品質門檻。

**合規與隱私硬性紅線（違反視為實作錯誤）：**

- **MUST NOT** 處理、傳輸或儲存任何病患可識別資訊（PHI）。
- **MUST NOT** 使用 ChatGPT 免費版／個人版（預設拿輸入做訓練）；**MUST** 只用 OpenAI 標準付費 API。
- **MUST NOT** 把 user_id／學號等識別資訊放進送往 OpenAI 的 prompt。
- **MUST** 對所有 LLM 輸出強制帶引文（書名簡寫、頁碼、圖號），並在每則回應底部顯示
  「教育用途，內容基於教科書」浮水印。
- query log / 向量資料 **MUST** 存於校內基礎設施或具同等資料治理保證之雲端。
- **離線建庫管線 MUST NOT 呼叫任何雲端 LLM API。**

**已被設計評審「移除」的做法（不要重新加回）：**

- 不做關鍵字攔截 / redirect「請諮詢醫師」、不做 prompt 層拒答規則、不做第一人稱症狀攔截。
  使用者皆為醫學系學生；安全網是「引文強制 + 教育用途浮水印 + 使用者回饋」三者組合，不是拒答。
- 黃金題庫**沒有** `should_refuse` 類別；`out_of_scope` 測的是「教材中查無此項」而非拒答臨床問題。

---

# Multi-Agent Workflow Rules

> 通用協作規則。除非專案特殊，否則不需編輯。

## Active profile (KIT_PROFILE)

This kit runs in one of two profiles, selected per-machine by the
`KIT_PROFILE` environment variable (default `full`):

| Profile | Research | Plan + execute | Reviewer (the isolation guarantee) |
|---------|----------|----------------|------------------------------------|
| `full`  | Gemini scout | Superpowers | **Codex Plugin** - different model = real isolation |
| `solo`  | none (your own search) | Superpowers | **fresh-context Claude subagent** - state/time isolation ONLY, NOT model isolation |

Wherever these rules say **"run a review"**, resolve it by the active profile:

- **full** -> invoke the Codex Plugin: `/codex:review` (and
  `/codex:adversarial-review` for high-stakes work).
- **solo** -> spawn a fresh-context subagent to review the diff with clean
  state, AND say plainly to the user: *"solo profile: cross-model isolation is
  OFF - this is a same-model self-review (state/time isolation only)."*
  Never present a solo self-review as if it were cross-model review.

If you cannot determine the active profile, ask the user before reviewing.

## Three-capability orchestration

- **Gemini** (research scout, full only): web research / external info. Never
  writes code, never reviews.
- **Superpowers** (architect + worker): brainstorm, writing-plans,
  executing-plans. The primary planning/implementation flow in both profiles.
- **Reviewer** (per active profile, see table above): cross-model review in
  full, fresh-context self-review in solo. Never writes code.

Main Claude orchestrates these based on task type.

## Spec-driven entry (if a blueprint exists)

If `docs/specs/` contains a spec/blueprint:

- Treat it as the **authoritative requirements source**.
- **Skip brainstorming** - scope was already converged externally.
- Still run writing-plans to derive a codebase-aware plan.
- **Still review the spec itself** (prefer adversarial) before implementing.
  The spec is an external artifact written by another author/model, so the
  isolation principle applies to it exactly as it applies to a plan.

## Task-size classification

A `classify-task.sh` hook may inject a `TASK_CLASSIFICATION` hint. Honor it.
Otherwise classify yourself:

| Signal | Classification |
|--------|----------------|
| "just do it" / "quick" / "small" | `small_task` |
| "full workflow" / "review the plan" | `explicit_full` |
| < 30 lines, single file, single concern | `small_task` |
| UI / CSS / copy / formatting | `small_task` |
| bug fix without business-logic change | `small_task` |
| new feature, single file, < 100 lines | `medium_task` |
| new feature, multiple files OR new deps | `large_task` |
| refactor, schema migration, auth/payment | `large_task` |

### What to run for each

- **small_task**: just do it; skip planning; brief summary. No review unless it
  touched business logic (see Final review trigger).
- **medium_task**: superpowers:writing-plans -> run a review on the plan ->
  user approves -> implement -> final review.
- **large_task**: research-before-planning if it involves new libs / security /
  perf-critical / novel architecture -> superpowers:brainstorming ->
  writing-plans -> review the plan (adversarial if high-stakes) -> user approves
  -> executing-plans with phase-level review -> final review.

### Phase-level review during executing-plans

- **MUST review**: auth / authz / session; payment / billing / money;
  data migration / schema; anything in "Project-specific constraints".
- **Recommend (default yes)**: user-visible business logic; algorithms / state
  machines / concurrency; input validation / security boundaries; phase >= 100 lines.
- **Skip**: UI / styling / docs; simple glue / CRUD / type defs; < 50 lines, no
  business logic.

### Final review trigger

Before declaring the task complete, ask: *did this session modify
business-logic-bearing files that haven't been reviewed yet?* If yes -> run a
review (per active profile) on the full change set before summarizing. The Stop
hook (`verify-final-review.sh`) enforces this when enabled.

## Cross-model isolation principle

PRIMARY question: **"is the reviewer a different model than the writer?"** -
not "which specialist fits this task?".

- **full**: writer (main Claude) != reviewer (Codex) -> real isolation.
- **solo**: writer and reviewer are both Claude -> model isolation is OFF; you
  only get state/time isolation from the fresh subagent. Say so to the user.

### Anti-pattern (never do this)

Same model writes + same model reviews, presented as isolation. In full, never
review codex-written code (e.g. from `/codex:rescue`) with codex again - that's
zero isolation. Defer to user judgment or have main Claude review it.

## When to STOP and ask the user

- Research findings suggest a meaningfully better approach than the plan assumed.
- A review flagged a `critical`/`high` issue you can't resolve from context.
- Adversarial review challenged a fundamental premise.
- Phase will modify > [default 100] lines, or delete/rewrite > 30 existing lines.
- Touches anything in "Project-specific constraints".
- (full) Codex/Gemini unavailable - ask whether to proceed or wait.

## Service unavailability handling

When a tool fails: report clearly (never silently skip), categorize (quota /
auth / network) with the fix, and ask the user: skip / wait+retry / (research
only) proceed without it.

**full profile:** do NOT auto-fall-back from `/codex:review` to Claude
self-review silently - that breaks the isolation guarantee; the user must
explicitly accept it (which is effectively a temporary switch to solo).
**solo profile:** self-review is the declared default, not a silent fallback -
but still state that isolation is reduced.

## Inventory (profile-gated)

- Skill `research-before-planning` - full only (uses gemini scout).
- Subagent `gemini-research-scout` - full only.
- Hook `classify-task.sh` (UserPromptSubmit) - both profiles.
- Hook `verify-final-review.sh` (Stop) - both; reads `KIT_PROFILE` to decide
  which review path to enforce.

## NOT available (intentionally)

`codex-coder`/`codex-reviewer` subagents, bash wrappers for codex, or a
`plan-with-review` skill - all replaced by the official Codex Plugin (full) and
superpowers writing-plans.
