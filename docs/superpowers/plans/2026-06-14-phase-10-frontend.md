# Phase 10 — 前端（useChat + 引用面板 + 免責/回饋）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **視覺實作（Part 3–6 的元件）MUST 由 `frontend-design` skill 主導**（使用者 2026-06-10 指示）：本計畫提供每個元件的「介面契約 + 完整測試（行為規格）+ RWD 驗收」，由 frontend-design 寫出通過測試的高品質 UI。Part 0/1/2/7 為決定性程式碼，照寫。
> 模型分工（memory）：implementer 派 `Agent(model="sonnet")`；MUST 審查項（§9 後端 schema/feedback、emitter、引文/合規）優先 Codex，次選 `Agent(model="opus")`。

**Goal:** 以 Vercel AI SDK v6 `useChat` 打造解剖學 RAG 串流問答前端（mobile-first 單欄、引文面板、未驗證 banner、免責同意、per-turn 👍/👎、教育浮水印、全繁中），並完成支援 per-turn 回饋與 persistent data parts 的最小後端 pre-step。

**Architecture:** 前端 `useChat`(`@ai-sdk/react`) + `DefaultChatTransport`(`ai`) 指後端 `/chat`（UI-message-stream 模式）。引文/驗證走 **persistent** `data-sources`/`data-verification` part → 進 `message.parts`，`MessageBubble` 依 `part.type` 抽取渲染。回饋以後端產生的 `turn_id`（= `start.messageId` = 前端 `message.id`）精準更新單列。

**Tech Stack:** Next.js 16（App Router, Turbopack）/ React 19 / TypeScript / `ai@6.0.197` / `@ai-sdk/react@3.0.199` / TailwindCSS v4 / shadcn/ui(new-york) / next-themes / Vitest + Testing Library。後端：FastAPI + asyncpg + Alembic + pytest。

**權威來源：** `docs/superpowers/specs/2026-06-14-phase-10-frontend-design.md`、`ARCHITECTURE.md` §5.6/5.7/5.8/5.9/6.7/1.8、`decisions.md` DL-012/016/018/021/022、roadmap §A（D-H/D-N/D-S）。

**全程約束（每個 PR 都要守）：**
- 鎖死 `ai@6.0.197` / `@ai-sdk/react@3.0.199`，**不用 canary**；只用 `DefaultChatTransport`（**禁** `TextStreamChatTransport`/`streamProtocol:'text'`）。
- npm 安裝一律 `--legacy-peer-deps`（React 19 peer）；**提交 `package-lock.json`**（D-S）。
- **不得**新增自訂 webpack config（Next16 Turbopack 預設，否則 build 失敗）。
- **不改** `tests/golden_qa.jsonl`、`eval_thresholds.yaml`。
- 全 UI 繁體中文。

---

## 0. Codex 對抗式審查修訂（v2 — binding；實作前必納）

> 2026-06-14 Codex 跨模型對抗審查（writer=Claude、reviewer=Codex）。**12 項全查證為有效**，下列為**綁定修訂**，覆寫各 Task 對應處。
> Codex 確認協定假設成立：`start.messageId`→`message.id`、persistent data part 綁定 assistant 訊息、SSE `error` chunk→useChat error→`ErrorState`、手刻 emitter 與 SDK 吻合（§4 fallback 多半用不到，仍保留為保險）。

### R1（Critical，新增 Task 1.0）：前端必須 proxy /chat·/feedback·/warmup 到後端
`transport.api:"/chat"` 是相對路徑 → 預設打 Next:3000 → 404（mock fetch 測試遮蔽此問題）。後端＝compose service `backend:8000`（host dev＝`localhost:8000`）。**新增 Task 1.0（Part 1 最先做）**：
- `frontend/next.config.mjs`：
```js
/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/chat", destination: `${backend}/chat` },
      { source: "/feedback", destination: `${backend}/feedback` },
      { source: "/warmup", destination: `${backend}/warmup` },
    ];
  },
};
export default nextConfig;
```
- `docker-compose.yml` frontend 服務 `environment` 加 `BACKEND_ORIGIN: "http://backend:8000"`。
- **SSE 串流驗證（MUST）**：Next rewrites 對 `text/event-stream` 須真串流（後端已送 `x-accel-buffering:no`）。Task 8.3 compose smoke 實測 `curl -N localhost:3000/chat`（mock）逐塊收到 SSE（非一次性 buffer）。若 rewrites 緩衝→改 Route Handler（`app/chat/route.ts`，`fetch(backend,{duplex:"half"})` streaming proxy）或前端直連 `NEXT_PUBLIC_BACKEND_URL`＋後端 CORS。

### R2（Critical，覆寫 Task 0.3）：feedback 與 query_logs 寫入 race → 兩端改 upsert-on-turn_id
query_logs 列在串流尾 `spawn(log_query)` 才寫；feedback 可能先到 → `UPDATE … WHERE turn_id` 影響 0 列 → 例外被吞 → 回 `{"ok":true}` 但資料遺失。**兩個寫入端都改 upsert（turn_id 衝突鍵；migration 008 UNIQUE 索引使其合法）**：
- `_log_query`（main.py）：
```python
await conn.execute(
    "INSERT INTO query_logs "
    "(user_id, query_text, conversation_id, cache_hit, status, model_used, turn_id) "
    "VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7::uuid) "
    "ON CONFLICT (turn_id) DO UPDATE SET "
    "query_text=EXCLUDED.query_text, conversation_id=EXCLUDED.conversation_id, "
    "cache_hit=EXCLUDED.cache_hit, status=EXCLUDED.status, model_used=EXCLUDED.model_used",
    user_id, query, conversation_id, cache_hit,
    status if status in ALLOWED_LOG_STATUSES else "ok", model_used, turn_id,
)
```
- `_write_feedback`（main.py）：feedback 先到也不丟，且只能改自己的列；回傳是否命中：
```python
async def _write_feedback(*, user_id, message_id, rating, text) -> bool:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO query_logs (turn_id, user_id, query_text, feedback, feedback_text) "
                "VALUES ($3::uuid, $4::uuid, '', $1, $2) "
                "ON CONFLICT (turn_id) DO UPDATE SET feedback=EXCLUDED.feedback, "
                "feedback_text=EXCLUDED.feedback_text "
                "WHERE query_logs.user_id = $4::uuid "
                "RETURNING turn_id",
                rating, text, message_id, user_id,
            )
        return row is not None   # None＝turn_id 屬他人/被 WHERE 擋
    except Exception:
        logger.warning("feedback upsert 失敗", exc_info=True)
        return False
```
- `feedback.py` 端點：`ok = await request.app.state.write_feedback(user_id=..., message_id=..., rating=..., text=...)`；`apply_feedback` 回傳該 bool；`if not ok: raise HTTPException(404, "找不到該回合或無權限")`。
- **新增測試**：feedback 先於 log_query → upsert 後 feedback 存在；log_query 後到 → ON CONFLICT 補 query_text 不蓋 feedback；他人 turn_id → affected 0 → 404。（query_text 維持 NOT NULL；feedback-first 用 `''` 佔位，log_query 後到覆寫。）

### R3（High，補強 Task 0.2/0.5）：列出並替換既有過時 assertion
先 `grep -rn '"type": "start"\|transient\|conversation_id' backend/tests` 盤點。`test_api_chat_unit.py` 所有 `start == {"type":"start"}` → 加 `"messageId": FIXED_TURN`；`test_api_chat_sse_unit.py` golden 期望（含 `transient:true`、無 messageId）同步改；兩檔 deps 注入 `gen_turn_id=lambda: FIXED_TURN`。

### R4（High，補強 Task 0.2）：每條路徑都斷言帶 turn_id
對 **cache-hit / encoder_error / retrieval_error / llm_error / cancelled / success** 各觸發其分支，斷言該分支的 `log_query` 收到 `turn_id==FIXED_TURN`。

### R5（High，覆寫 Task 0.4）：列出所有既有 feedback 測試的更新
先 `grep -n conversation_id backend/tests/test_api_feedback_unit.py` 盤點；既有 validation/truncation/writer-argument/HTTP-400 全部 `conversation_id` → `message_id`＋`FIXED_TURN`；保留截斷(≤2000)、空 text、400 覆蓋；加 affected=0→404。

### R6（Medium，覆寫 Task 2.5）：transport 不送 credentials（DL-016 免登入）
`makeChatTransport` **移除 `credentials:"include"`**（預設 same-origin；經 R1 rewrites 為同源）。

### R7（Medium，補強 Task 1.5/3.2/Header）：safe-area 具體 CSS
App shell `min-h-dvh`；Header `pt-[env(safe-area-inset-top)]`；Composer 容器 `pb-[env(safe-area-inset-bottom)]`（Tailwind v4 arbitrary value）。viewport 已 `viewportFit:"cover"`。

### R8（Medium，新增 Header 元件）
§3 加 `components/Header.tsx`（標題「解剖學 RAG」＋「教育用途」badge＋safe-area-top＋mobile 緊湊）；`ChatPanel`（3.4）最上層渲染 `<Header/>`。測試：`render(<Header/>)`→`getByText(/教育用途/)`。

### R9（Medium，全前端測試）：fixture 一律用 FIXED_TURN UUID
所有 messageId fixture ＝ `"00000000-0000-0000-0000-0000000000aa"`（=後端 FIXED_TURN）；**不得**用 `"abc"`/`"t-aa"`（後端會 400）。涉 Task 2.4、3.1、5.4、8.1。

### R10（Medium，覆寫 Task 0.1 測試）：用既有 DB fixture + 補 UNIQUE/downgrade 覆蓋
先 `grep -n 'def .*conn\|@pytest.fixture\|asyncpg' backend/tests/conftest.py backend/tests/test_migrations_db.py` 找既有 DB 連線 fixture，沿用（**不要發明 `direct_conn`**）。補：兩列 `turn_id NULL` 共存；重複非-NULL `turn_id` 被拒（`asyncpg.UniqueViolationError`）；`downgrade -1` 後欄位與 `uq_query_logs_turn_id` 皆消失。

### R11（Medium，補強 Task 7.1）：dump 腳本 payload key 改 sources
`dump-golden-stream.mjs` `data:{citations:…}` → `data:{sources:EXAMPLE_CITATIONS}`（對齊後端 `data-sources` 的 `{sources:[…]}`）。

### R12（Low，併入 R10）：Task 0.1 測試正名 `test_008_turn_id_schema`（非真 roundtrip）。

---

## PART 0 — 後端 pre-step（Python / pytest / TDD）★ MUST 先完成，且交 Codex 審

> 動機：per-turn 回饋 + persistent data parts（spec §9）。觸及 §5.6/§5.7/D-H DECIDED 區 → 必須記 DL-027（Task 7.3）。
> 影響：`ai_stream.py` **無需改**（`start_part(message_id)`、`data_part(transient=)` 已支援）；改 chat.py/main.py/feedback.py + migration + golden。
> 決定性測試：固定 `turn_id`＝`"00000000-0000-0000-0000-0000000000aa"`（下稱 `FIXED_TURN`）。

### Task 0.1: migration 008 — query_logs 加 turn_id

**Files:**
- Create: `backend/src/anatomy_backend/db/migrations/versions/008_query_logs_turn_id.py`
- Test: `backend/tests/test_migrations_db.py`（沿用既有 roundtrip 測試風格）

- [ ] **Step 1: 先確認目前 head 的 revision id**

Run: `cd backend && uv run --no-sync alembic heads`（或讀 `007_ingest_errors.py` 內 `revision = "..."`）
Expected: 顯示 `007_ingest_errors`（若不同，`down_revision` 改成實際值）。

- [ ] **Step 2: 寫 migration**

```python
"""008: query_logs 加 turn_id（per-turn 回饋粒度，DL-027）。

turn_id = app 端每回合產生的 UUID（= AI SDK start.messageId = 前端 message.id）。
/feedback 以 turn_id 精準更新單列（取代以 conversation_id 套用整串）。
nullable 向後相容（舊列 NULL）；UNIQUE 索引允許多個 NULL。
"""
from alembic import op

revision = "008_query_logs_turn_id"
down_revision = "007_ingest_errors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE query_logs ADD COLUMN turn_id UUID")
    op.execute("CREATE UNIQUE INDEX uq_query_logs_turn_id ON query_logs (turn_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_query_logs_turn_id")
    op.execute("ALTER TABLE query_logs DROP COLUMN turn_id")
```

- [ ] **Step 3: 寫測試（column 存在 + roundtrip）**

於 `test_migrations_db.py` 既有 db fixture（host 配方：`DATABASE_URL=…@localhost:6432/anatomy_rag`、`PG_DIRECT_URL=…@localhost:5432/…`、`ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1`）下加：

```python
async def test_008_turn_id_column_and_index(direct_conn):  # 沿用既有 conn fixture
    col = await direct_conn.fetchval(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='query_logs' AND column_name='turn_id'"
    )
    assert col == "uuid"
    idx = await direct_conn.fetchval(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='query_logs' AND indexname='uq_query_logs_turn_id'"
    )
    assert idx == "uq_query_logs_turn_id"
```

> 若 `test_migrations_db.py` 既有 fixture 名稱不同，沿用該檔現有命名；本測試僅查 information_schema，不依賴特定 helper。

- [ ] **Step 4: 跑 migration + 測試**

Run: `cd backend && uv run --no-sync alembic upgrade head && uv run --no-sync pytest tests/test_migrations_db.py -k turn_id -v`
Expected: upgrade 成功；測試 PASS。再 `alembic downgrade -1 && alembic upgrade head` 確認可逆。

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/db/migrations/versions/008_query_logs_turn_id.py backend/tests/test_migrations_db.py
git commit -m "feat(db): 008 query_logs.turn_id（per-turn 回饋；DL-027）"
```

### Task 0.2: chat.py — turn_id 注入、start.messageId、persistent parts、log_query 帶 turn_id

**Files:**
- Modify: `backend/src/anatomy_backend/api/chat.py`
- Test: `backend/tests/test_api_chat_unit.py`, `backend/tests/test_api_chat_sse_unit.py`

- [ ] **Step 1: 改 deps + 測試先紅**　於 `test_api_chat_unit.py` 加（用 FIXED_TURN 注入）：

```python
FIXED_TURN = "00000000-0000-0000-0000-0000000000aa"

def _deps_with_fixed_turn(**over):
    d = make_fake_deps(**over)            # 沿用既有 fake deps builder
    d.gen_turn_id = lambda: FIXED_TURN     # 新欄位
    return d

async def test_start_frame_carries_message_id(...):
    events = [e async for e in chat_event_stream(_deps_with_fixed_turn(), normalized, user)]
    start = json.loads(events[0].data)
    assert start == {"type": "start", "messageId": FIXED_TURN}

async def test_data_parts_are_persistent(...):
    events = [e async for e in chat_event_stream(_deps_with_fixed_turn(), normalized, user)]
    payloads = [json.loads(e.data) for e in events if e.data not in ("[DONE]",)]
    src = next(p for p in payloads if p["type"] == "data-sources")
    ver = next(p for p in payloads if p["type"] == "data-verification")
    assert "transient" not in src and "transient" not in ver   # persistent

async def test_log_query_receives_turn_id(...):
    captured = []
    deps = _deps_with_fixed_turn(log_query=lambda **kw: captured.append(kw) or _noop())
    [_ async for _ in chat_event_stream(deps, normalized, user)]
    assert all(c["turn_id"] == FIXED_TURN for c in captured)
```

- [ ] **Step 2: 跑→紅**　Run: `cd backend && uv run --no-sync pytest tests/test_api_chat_unit.py -k "message_id or persistent or turn_id" -v` Expected: FAIL（gen_turn_id 不存在 / start 無 messageId / parts 仍 transient）。

- [ ] **Step 3: 改 chat.py**
  1. `ChatDeps` 加欄位：`gen_turn_id: Callable[[], str] = field(default_factory=lambda: (lambda: str(__import__("uuid").uuid4())))`（或檔頂 `import uuid` 後 `lambda: str(uuid.uuid4())`）。
  2. `chat_event_stream` 進 `with deps.tracer.trace(...)` 後、第一個 yield 前：`turn_id = deps.gen_turn_id()`。
  3. 兩處 `ais.start_part()` → `ais.start_part(turn_id)`（cache-hit 路徑 ~L100、主路徑 ~L128）。
  4. 兩處 `ais.data_part("sources", …)` → 加 `transient=False`（~L101、~L177）。
  5. 一處 `ais.data_part("verification", …)` → 加 `transient=False`（~L230）。
  6. 全部 5 處 `deps.log_query(…)` 呼叫 → 加 `turn_id=turn_id`（cache-hit/encoder_error/retrieval_error/llm_error/success）。

- [ ] **Step 4: 跑→綠 + 全回歸**　Run: `uv run --no-sync pytest tests/test_api_chat_unit.py tests/test_api_chat_sse_unit.py -v`
Expected: 新測試 PASS；`test_api_chat_sse_unit.py` 因 golden 未更新會**暫紅**（Task 0.5 修）——本步先確認新單元測試綠。

- [ ] **Step 5: Commit**（待 0.5 golden 一起綠再 commit，見 0.5）

### Task 0.3: main.py — _log_query 寫 turn_id、_build_chat_deps 注入 gen_turn_id、_write_feedback 改 by turn_id

**Files:** Modify `backend/src/anatomy_backend/api/main.py`

- [ ] **Step 1: 改 _log_query**（加 `turn_id` 參數與欄位）

```python
async def _log_query(*, user_id, query, conversation_id=None, cache_hit=False,
                     status="ok", model_used=None, turn_id=None, **_kw):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO query_logs "
                "(user_id, query_text, conversation_id, cache_hit, status, model_used, turn_id) "
                "VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7::uuid)",
                user_id, query, conversation_id, cache_hit,
                status if status in ALLOWED_LOG_STATUSES else "ok",
                model_used, turn_id,
            )
    except Exception:
        logger.warning("query_logs INSERT 失敗", exc_info=True)
```

- [ ] **Step 2: 改 _write_feedback（by turn_id）**

```python
async def _write_feedback(*, user_id, message_id, rating, text):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE query_logs SET feedback=$1, feedback_text=$2 "
                "WHERE turn_id=$3::uuid AND user_id=$4::uuid",
                rating, text, message_id, user_id,
            )
    except Exception:
        logger.warning("feedback UPDATE 失敗", exc_info=True)
```

- [ ] **Step 3: _build_chat_deps 注入 gen_turn_id**　於 `ChatDeps(...)` 建構加：`gen_turn_id=lambda: str(uuid.uuid4()),`（檔頂已 `import` 過 contextvars 等；補 `import uuid`）。

- [ ] **Step 4: 跑 e2e 回歸**　Run: `uv run --no-sync pytest tests/test_api_e2e* tests/test_api_feedback* -v` Expected: feedback 相關測試會在 Task 0.4 更新後綠；本步確認 import 無誤、其餘 e2e 不回歸。

- [ ] **Step 5: Commit**（與 0.4/0.5 合併 commit）

### Task 0.4: feedback.py — body 改 message_id（= turn_id）

**Files:** Modify `backend/src/anatomy_backend/api/feedback.py`；Test `backend/tests/test_api_feedback_unit.py`

- [ ] **Step 1: 改測試先紅**

```python
def test_parse_requires_message_id():
    fb = parse_feedback_body({"message_id": "00000000-0000-0000-0000-0000000000aa", "rating": -1, "text": "錯了"})
    assert fb.message_id == "00000000-0000-0000-0000-0000000000aa" and fb.rating == -1

def test_parse_rejects_bad_message_id():
    import pytest
    with pytest.raises(ValueError):
        parse_feedback_body({"message_id": "not-uuid", "rating": 1})
```

- [ ] **Step 2: 跑→紅**　Run: `uv run --no-sync pytest tests/test_api_feedback_unit.py -v` Expected: FAIL（仍是 conversation_id）。

- [ ] **Step 3: 改 feedback.py**
  - `FeedbackInput`: `conversation_id` → `message_id`。
  - `parse_feedback_body`: 取 `body.get("message_id")`，必填 + 驗 UUID（沿用既有 try/except 樣式）。
  - `apply_feedback`: `await writer(user_id=user_id, message_id=fb.message_id, rating=fb.rating, text=text)`。
  - 文字截斷 `_TEXT_MAX` 不變。

- [ ] **Step 4: 跑→綠**　Run: `uv run --no-sync pytest tests/test_api_feedback_unit.py -v` Expected: PASS。

- [ ] **Step 5: Commit**（與 0.5）

### Task 0.5: golden 更新（start.messageId + 去 transient）

**Files:** Modify `infra/golden/ai_stream_golden.jsonl`；對應 `backend/tests/test_api_chat_sse_unit.py`

- [ ] **Step 1: 讓 SSE 測試用 FIXED_TURN 驅動**　確認 `test_api_chat_sse_unit.py` 的 deps builder 注入 `gen_turn_id = lambda: FIXED_TURN`（與 golden 對齊）。

- [ ] **Step 2: 改 golden 第一行與 data 行**
  - 第 1 行：`{"type":"start"}` → `{"type":"start","messageId":"00000000-0000-0000-0000-0000000000aa"}`
  - `data-sources` 行：去掉 `,"transient":true`
  - `data-verification` 行：去掉 `,"transient":true`
  - 其餘行不動。

- [ ] **Step 3: 跑→綠**　Run: `cd backend && uv run --no-sync pytest tests/test_api_chat_sse_unit.py tests/test_api_ai_stream_unit.py -v`
Expected: PASS（`test_data_part_is_transient_by_default` 仍綠＝emitter 預設 transient 不變，只有 chat.py 呼叫端傳 `transient=False`）。

- [ ] **Step 4: 全後端回歸 + lint**　Run: `cd backend && uv run --no-sync pytest -q && uv run --no-sync ruff check .`（**勿** `ruff format`）Expected: 全綠、ruff 乾淨。

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/api/chat.py backend/src/anatomy_backend/api/main.py \
        backend/src/anatomy_backend/api/feedback.py infra/golden/ai_stream_golden.jsonl backend/tests/
git commit -m "feat(api): per-turn turn_id + persistent data parts + feedback by message_id（DL-027）"
```

- [ ] **Step 6: Codex 審 Part 0**（MUST：schema/feedback/emitter/合規）　`/codex:review`（高風險可 `/codex:adversarial-review`）對 Part 0 diff；逐項處置後再進 Part 1+。

---

## PART 1 — 前端 scaffolding（Tailwind v4 + shadcn + Vitest + next-themes + app shell）

> 多為安裝/設定，驗收＝build/render/test smoke。所有 `npm i` 帶 `--legacy-peer-deps`，完成後提交 `package-lock.json`。

### Task 1.1: Tailwind v4 + PostCSS

**Files:** Create `frontend/postcss.config.mjs`, `frontend/app/globals.css`；Modify `frontend/app/layout.tsx`

- [ ] **Step 1: 安裝**　`cd frontend && npm i tailwindcss@4 @tailwindcss/postcss postcss tw-animate-css --legacy-peer-deps`
- [ ] **Step 2: postcss.config.mjs**

```js
const config = { plugins: { "@tailwindcss/postcss": {} } };
export default config;
```

- [ ] **Step 3: 暫時 globals.css（最小，shadcn init 會補 tokens）**

```css
@import "tailwindcss";
@import "tw-animate-css";
```

- [ ] **Step 4: layout.tsx import globals**　頂部加 `import "./globals.css";`
- [ ] **Step 5: smoke**　`npm run build`（Turbopack）Expected: build 成功。提交 lockfile。
- [ ] **Step 6: Commit**　`git add frontend/ && git commit -m "chore(fe): tailwind v4 + postcss"`

### Task 1.2: shadcn init + base components

**Files:** Create `frontend/components.json`, `frontend/lib/utils.ts`, `frontend/components/ui/*`；shadcn 補 `globals.css` tokens

- [ ] **Step 1: init**　`cd frontend && npx shadcn@latest init --legacy-peer-deps`（選 new-york、base color neutral、CSS variables yes、`@/` alias 已存在）
- [ ] **Step 2: add 元件**　`npx shadcn@latest add button textarea dialog alert card skeleton --legacy-peer-deps`
- [ ] **Step 3: 驗證**　確認 `components/ui/{button,textarea,dialog,alert,card,skeleton}.tsx`、`lib/utils.ts`(`cn()`)、`globals.css` 已含 `:root`/`.dark` OKLCH tokens 與 `@custom-variant dark`。`npm run build` 綠。
- [ ] **Step 4: Commit**　`git add frontend/ && git commit -m "chore(fe): shadcn(new-york) init + base ui"`

### Task 1.3: Vitest + Testing Library

**Files:** Create `frontend/vitest.config.ts`, `frontend/vitest.setup.ts`；Modify `frontend/package.json`, `frontend/tsconfig.json`

- [ ] **Step 1: 安裝**　`npm i -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom @vitejs/plugin-react --legacy-peer-deps`
- [ ] **Step 2: vitest.config.ts**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./", import.meta.url)) } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./vitest.setup.ts"] },
});
```

- [ ] **Step 3: vitest.setup.ts**

```ts
import "@testing-library/jest-dom/vitest";
// jsdom 未實作的瀏覽器 API（shadcn Dialog/RWD 會用到）
if (!window.matchMedia) {
  window.matchMedia = (q: string) =>
    ({ matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as unknown as MediaQueryList;
}
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
```

- [ ] **Step 4: package.json scripts**　加 `"test": "vitest run"`, `"test:watch": "vitest"`。
- [ ] **Step 5: smoke 測試**　Create `frontend/lib/__tests__/smoke.test.ts`：`import { describe, it, expect } from "vitest"; describe("smoke", () => it("runs", () => expect(1 + 1).toBe(2)));` → Run `npm test` Expected: PASS。
- [ ] **Step 6: Commit**　`git add frontend/ && git commit -m "chore(fe): vitest + testing-library"`

### Task 1.4: next-themes

**Files:** Create `frontend/components/theme-provider.tsx`；Modify `frontend/app/layout.tsx`

- [ ] **Step 1: 安裝**　`npm i next-themes --legacy-peer-deps`
- [ ] **Step 2: theme-provider.tsx**

```tsx
"use client";
import { ThemeProvider as NextThemes } from "next-themes";
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return <NextThemes attribute="class" defaultTheme="light" enableSystem>{children}</NextThemes>;
}
```

- [ ] **Step 3: layout 包 ThemeProvider**（見 Task 1.5 一併改）
- [ ] **Step 4: Commit**（與 1.5）

### Task 1.5: layout viewport + app shell + page 掛載點

**Files:** Modify `frontend/app/layout.tsx`, `frontend/app/page.tsx`

- [ ] **Step 1: layout.tsx**（補 `viewport`、ThemeProvider、dvh body）

```tsx
import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";

export const metadata: Metadata = {
  title: "解剖學 RAG 問答系統",
  description: "以解剖學教科書為基礎的多模態 RAG 問答系統（醫學系內部使用）",
};
export const viewport: Viewport = {
  width: "device-width", initialScale: 1, viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-Hant" suppressHydrationWarning>
      <body className="min-h-dvh bg-background text-foreground antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
```

- [ ] **Step 2: page.tsx 暫掛**　以 `<main className="mx-auto flex h-dvh w-full max-w-3xl flex-col">{/* ChatPanel Task 3.4 */}</main>` 取代 Phase 0 placeholder（ChatPanel 於 Part 3 補）。
- [ ] **Step 3: smoke**　`npm run build && npm test` 綠。
- [ ] **Step 4: Commit**　`git add frontend/ && git commit -m "feat(fe): app shell + viewport(dvh, safe-area) + theme"`

---

## PART 2 — lib（決定性邏輯，TDD 全碼）

### Task 2.1: lib/types.ts（型別契約）

**Files:** Create `frontend/lib/types.ts`

- [ ] **Step 1: 寫型別**

```ts
import type { UIMessage } from "ai";

export type Citation = {
  book_title: string;
  edition?: string | null;
  page: number;
  figure?: string | null;
  image_url: string;
  snippet: string;
  score: number;
};
export type SourcesData = { sources: Citation[] };
export type VerificationData = { verified: boolean; has_citations: boolean; unverified: string[] };

/** 後端 data-sources/data-verification 為 persistent → 進 message.parts。 */
export type AnatomyUIMessage = UIMessage<never, { sources: SourcesData; verification: VerificationData }>;
```

- [ ] **Step 2: 型別 smoke**　Run: `cd frontend && npx tsc --noEmit` Expected: 無錯。
- [ ] **Step 3: Commit**

### Task 2.2: lib/conversation.ts（每分頁穩定 conversation_id）

**Files:** Create `frontend/lib/conversation.ts`, `frontend/lib/__tests__/conversation.test.ts`

- [ ] **Step 1: 失敗測試**

```ts
import { describe, it, expect, beforeEach } from "vitest";
import { getOrCreateConversationId } from "@/lib/conversation";

beforeEach(() => sessionStorage.clear());
describe("conversation id", () => {
  it("stable within session", () => {
    const a = getOrCreateConversationId();
    expect(getOrCreateConversationId()).toBe(a);
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
  });
});
```

- [ ] **Step 2: 跑→紅** Run: `npm test -- conversation` Expected: FAIL（模組不存在）。
- [ ] **Step 3: 實作**

```ts
const KEY = "anatomy-rag:conversation_id";
export function getOrCreateConversationId(): string {
  let id = sessionStorage.getItem(KEY);
  if (!id) { id = crypto.randomUUID(); sessionStorage.setItem(KEY, id); }
  return id;
}
```

- [ ] **Step 4: 跑→綠** Run: `npm test -- conversation` Expected: PASS
- [ ] **Step 5: Commit**

### Task 2.3: lib/disclaimer.ts（同意 + 首次倒讚旗標）

**Files:** Create `frontend/lib/disclaimer.ts`, `frontend/lib/__tests__/disclaimer.test.ts`

- [ ] **Step 1: 失敗測試**

```ts
import { describe, it, expect, beforeEach } from "vitest";
import { isDisclaimerAccepted, acceptDisclaimer, shouldPromptFirstDownvote, markFirstDownvotePrompted } from "@/lib/disclaimer";

beforeEach(() => localStorage.clear());
describe("disclaimer", () => {
  it("defaults to not accepted, persists on accept", () => {
    expect(isDisclaimerAccepted()).toBe(false);
    acceptDisclaimer();
    expect(isDisclaimerAccepted()).toBe(true);
  });
  it("first downvote prompt fires once", () => {
    expect(shouldPromptFirstDownvote()).toBe(true);
    markFirstDownvotePrompted();
    expect(shouldPromptFirstDownvote()).toBe(false);
  });
});
```

- [ ] **Step 2: 跑→紅**
- [ ] **Step 3: 實作**

```ts
const DISCLAIMER = "anatomy-rag:disclaimer:v1";
const FIRST_DOWNVOTE = "anatomy-rag:first-downvote";
export const isDisclaimerAccepted = () => localStorage.getItem(DISCLAIMER) === "1";
export const acceptDisclaimer = () => localStorage.setItem(DISCLAIMER, "1");
export const shouldPromptFirstDownvote = () => localStorage.getItem(FIRST_DOWNVOTE) !== "1";
export const markFirstDownvotePrompted = () => localStorage.setItem(FIRST_DOWNVOTE, "1");
```

- [ ] **Step 4: 跑→綠**　- [ ] **Step 5: Commit**

### Task 2.4: lib/api.ts（postFeedback by message_id）

**Files:** Create `frontend/lib/api.ts`, `frontend/lib/__tests__/api.test.ts`

- [ ] **Step 1: 失敗測試**

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { postFeedback } from "@/lib/api";

beforeEach(() => vi.restoreAllMocks());
describe("postFeedback", () => {
  it("POSTs /feedback with message_id/rating/text", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await postFeedback({ messageId: "abc", rating: -1, text: "錯" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/feedback");
    expect(JSON.parse(init.body)).toEqual({ message_id: "abc", rating: -1, text: "錯" });
    expect(init.method).toBe("POST");
  });
});
```

- [ ] **Step 2: 跑→紅**
- [ ] **Step 3: 實作**

```ts
export async function postFeedback(input: { messageId: string; rating: 1 | -1; text?: string }): Promise<void> {
  const res = await fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: input.messageId, rating: input.rating, ...(input.text ? { text: input.text } : {}) }),
  });
  if (!res.ok) throw new Error(`feedback failed: ${res.status}`);
}
```

- [ ] **Step 4: 跑→綠**　- [ ] **Step 5: Commit**

### Task 2.5: lib/transport.ts（DefaultChatTransport 工廠）

**Files:** Create `frontend/lib/transport.ts`, `frontend/lib/__tests__/transport.test.ts`

- [ ] **Step 1: 失敗測試**（驗證指向 `/chat`、保證非 text-stream＝用 DefaultChatTransport）

```ts
import { describe, it, expect } from "vitest";
import { DefaultChatTransport } from "ai";
import { makeChatTransport } from "@/lib/transport";

describe("transport", () => {
  it("is a DefaultChatTransport to /chat (UI-message-stream mode)", () => {
    const t = makeChatTransport("conv-1");
    expect(t).toBeInstanceOf(DefaultChatTransport);
  });
});
```

- [ ] **Step 2: 跑→紅**
- [ ] **Step 3: 實作**

```ts
import { DefaultChatTransport } from "ai";

export function makeChatTransport(conversationId: string) {
  return new DefaultChatTransport({
    api: "/chat",
    credentials: "include",
    body: { conversation_id: conversationId },
  });
}
```

- [ ] **Step 4: 跑→綠**　- [ ] **Step 5: Commit**

---

## PART 3 — 核心 chat 元件（frontend-design 主導；測試＝契約）

> 每個元件：先寫**完整測試**（行為規格）→ 跑紅 → 由 **frontend-design skill** 寫出通過測試的 UI（new-york、繁中、RWD §5.1）→ 跑綠 → commit。frontend-design 可在不破壞測試前提下自由設計視覺。

### Task 3.1: MessageBubble（依 part.type 抽取渲染 — 全 phase 最關鍵）

**Files:** Create `frontend/components/MessageBubble.tsx`, `frontend/components/__tests__/MessageBubble.test.tsx`

- [ ] **Step 1: 完整測試（契約）**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "@/components/MessageBubble";
import type { AnatomyUIMessage } from "@/lib/types";

const sources = { sources: [{ book_title: "Gray", edition: "42", page: 812, figure: "Fig.7-23", image_url: "/p/1.webp", snippet: "肱二頭肌…", score: 0.9 }] };

function assistant(parts: AnatomyUIMessage["parts"]): AnatomyUIMessage {
  return { id: "t-aa", role: "assistant", parts } as AnatomyUIMessage;
}

describe("MessageBubble (assistant)", () => {
  it("renders answer text, citation panel, and verification banner; citations BELOW text but present", () => {
    const msg = assistant([
      { type: "data-sources", data: sources } as never,
      { type: "text", text: "起於喙突 [Gray, p.812, Fig.7-23]。" } as never,
      { type: "data-verification", data: { verified: false, has_citations: true, unverified: ["[X, p.1]"] } } as never,
    ]);
    render(<MessageBubble message={msg} status="ready" />);
    expect(screen.getByText(/起於喙突/)).toBeInTheDocument();
    expect(screen.getByText(/引用/)).toBeInTheDocument();      // CitationPanel
    expect(screen.getByText(/未驗證/)).toBeInTheDocument();    // UnverifiedBanner
    expect(screen.getByText(/教育用途，內容基於教科書/)).toBeInTheDocument(); // Watermark
  });

  it("no banner when has_citations is false (out-of-scope answer)", () => {
    const msg = assistant([
      { type: "text", text: "教材中查無此項。" } as never,
      { type: "data-verification", data: { verified: false, has_citations: false, unverified: [] } } as never,
    ]);
    render(<MessageBubble message={msg} status="ready" />);
    expect(screen.queryByText(/未驗證/)).not.toBeInTheDocument();
  });

  it("user message renders its text only", () => {
    const msg = { id: "u1", role: "user", parts: [{ type: "text", text: "肱二頭肌起點?" }] } as AnatomyUIMessage;
    render(<MessageBubble message={msg} status="ready" />);
    expect(screen.getByText("肱二頭肌起點?")).toBeInTheDocument();
    expect(screen.queryByText(/教育用途/)).not.toBeInTheDocument(); // 浮水印只在 assistant
  });
});
```

- [ ] **Step 2: 跑→紅** Run: `npm test -- MessageBubble`
- [ ] **Step 3: frontend-design 實作**　契約：
  - props：`{ message: AnatomyUIMessage; status: "submitted"|"streaming"|"ready"|"error" }`。
  - assistant：抽 `text`(合併所有 text part)、`data-sources`、`data-verification`；**垂直序＝答案文字 → `<CitationPanel data={sources}/>` → `<UnverifiedBanner data={verification}/>`(僅 `verified===false && has_citations`) → 底列 `<FeedbackButtons messageId={message.id}/>` + `<Watermark/>`**。
  - user：泡泡只渲染 text，無浮水印/引文/回饋。
  - streaming：文字後顯示打字游標（`status==='streaming'`）。
  - 依賴元件（CitationPanel/UnverifiedBanner/Watermark/FeedbackButtons）此時可先放最小 stub 讓測試文字命中，Part 4/5 補完整視覺（或先做 Part 4/5 再回此整合——subagent 順序自行安排）。
- [ ] **Step 4: 跑→綠**　- [ ] **Step 5: Commit**

### Task 3.2: Composer（鍵盤友善輸入）

**Files:** Create `frontend/components/Composer.tsx`, `frontend/components/__tests__/Composer.test.tsx`

- [ ] **Step 1: 完整測試**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Composer } from "@/components/Composer";

describe("Composer", () => {
  it("calls onSend with text and clears", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} status="ready" />);
    const box = screen.getByRole("textbox");
    await userEvent.type(box, "肱二頭肌起點?");
    await userEvent.click(screen.getByRole("button", { name: /送出/ }));
    expect(onSend).toHaveBeenCalledWith("肱二頭肌起點?");
  });
  it("disables send while streaming", () => {
    render(<Composer onSend={() => {}} status="streaming" />);
    expect(screen.getByRole("button", { name: /送出/ })).toBeDisabled();
  });
});
```

- [ ] **Step 2: 跑→紅**
- [ ] **Step 3: frontend-design 實作**　契約：props `{ onSend(text:string):void; status }`；shadcn Textarea + Button；**RWD §5.1**：釘底、`text-base`(≥16px)、auto-grow、`enterKeyHint="send"`、Enter 送出/Shift+Enter 換行、`status!=='ready'` 禁用、min 44px 觸控；送出後清空、trim 空字串不送。
- [ ] **Step 4: 跑→綠**　- [ ] **Step 5: Commit**

### Task 3.3: MessageList + EmptyState

**Files:** Create `frontend/components/MessageList.tsx`, `frontend/components/EmptyState.tsx`, `frontend/components/__tests__/MessageList.test.tsx`

- [ ] **Step 1: 完整測試**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageList } from "@/components/MessageList";

describe("MessageList", () => {
  it("shows EmptyState with example questions when no messages", async () => {
    const onPick = vi.fn();
    render(<MessageList messages={[]} status="ready" onPickExample={onPick} />);
    const ex = screen.getAllByRole("button")[0];
    await userEvent.click(ex);
    expect(onPick).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑→紅**
- [ ] **Step 3: frontend-design 實作**　`MessageList`：`{ messages: AnatomyUIMessage[]; status; onPickExample(q:string) }`；無訊息→`EmptyState`（3 個繁中示例問題、`onPick`）；否則 map → `MessageBubble`；容器 `flex-1 overflow-y-auto`。
- [ ] **Step 4: 跑→綠**　- [ ] **Step 5: Commit**

### Task 3.4: ChatPanel（useChat 整合）

**Files:** Create `frontend/components/ChatPanel.tsx`；整合測試見 Part 8

- [ ] **Step 1: 實作（client component）**　契約：
  - `"use client"`；`useChat<AnatomyUIMessage>({ transport: makeChatTransport(getOrCreateConversationId()) })`。
  - **不需 `onData`**（引文走 persistent parts 進 message.parts）。
  - 渲染：`DisclaimerModal`(gate) → `MessageList(messages,status,onPickExample=setInput→sendMessage)` → `Composer(onSend=(t)=>sendMessage({text:t}), status)`；`status==='error'`→`ErrorState(error,onRetry=regenerate)`。
  - 版面：`flex h-dvh flex-col`，list `flex-1`，composer 釘底。
- [ ] **Step 2: 掛到 page.tsx**　`app/page.tsx` 用 `<ChatPanel/>`（client island）。
- [ ] **Step 3: build smoke**　`npm run build` 綠。
- [ ] **Step 4: Commit**（整合測試在 Part 8 補綠後再一起）

---

## PART 4 — 引用面板（frontend-design 主導）

### Task 4.1: CitationCard + CitationImage（圖佔位 + 點放大 lightbox）

**Files:** Create `frontend/components/CitationCard.tsx`, `frontend/components/CitationImage.tsx`, tests `frontend/components/__tests__/Citation.test.tsx`

- [ ] **Step 1: 完整測試**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CitationCard } from "@/components/CitationCard";
import { CitationImage } from "@/components/CitationImage";

const c = { book_title: "Gray", edition: "42", page: 812, figure: "Fig.7-23", image_url: "http://localhost:9000/bad.png", snippet: "肱二頭肌起於喙突", score: 0.9 };

describe("Citation", () => {
  it("card shows book/page/figure/snippet", () => {
    render(<CitationCard c={c} />);
    expect(screen.getByText(/Gray/)).toBeInTheDocument();
    expect(screen.getByText(/812/)).toBeInTheDocument();
  });
  it("image falls back to placeholder on error", () => {
    render(<CitationImage src={c.image_url} alt="Gray p.812" />);
    const img = screen.getByRole("img") as HTMLImageElement;
    fireEvent.error(img);
    expect(img.src).toMatch(/placeholder/); // 換成本地佔位資源路徑
  });
});
```

- [ ] **Step 2: 跑→紅**
- [ ] **Step 3: frontend-design 實作**　
  - `CitationImage`：`{src,alt}`；`loading="lazy"`；`onError`→換 `/placeholder-page.svg`（放 `frontend/public/`，解剖風佔位）；點擊→shadcn Dialog 全螢幕放大（手機可讀標籤）。
  - `CitationCard`：`{c: Citation}`；shadcn Card；顯示 `book_title`(+edition)、`p.{page}`、`figure`、`snippet`(手機截斷+展開)、`CitationImage`；圖高度上限、≥44px 觸控。
- [ ] **Step 4: 跑→綠**　- [ ] **Step 5: Commit**

### Task 4.2: CitationPanel

**Files:** Create `frontend/components/CitationPanel.tsx`, test `__tests__/CitationPanel.test.tsx`

- [ ] **Step 1: 完整測試**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CitationPanel } from "@/components/CitationPanel";

describe("CitationPanel", () => {
  it("renders 📚 引用 (n) and one card per source", () => {
    const data = { sources: [
      { book_title:"Gray", page:812, image_url:"/a", snippet:"x", score:0.9 },
      { book_title:"Netter", page:401, image_url:"/b", snippet:"y", score:0.8 },
    ]};
    render(<CitationPanel data={data as never} />);
    expect(screen.getByText(/引用.*2/)).toBeInTheDocument();
    expect(screen.getByText(/Gray/)).toBeInTheDocument();
    expect(screen.getByText(/Netter/)).toBeInTheDocument();
  });
  it("renders nothing for empty sources", () => {
    const { container } = render(<CitationPanel data={{ sources: [] } as never} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: 跑→紅**　- [ ] **Step 3: 實作**　`{ data: SourcesData }`；標頭「📚 引用 (n)」；map `CitationCard`；空陣列→`null`。- [ ] **Step 4: 綠**　- [ ] **Step 5: Commit**

---

## PART 5 — banner / 浮水印 / 免責 / 回饋 / 錯誤（frontend-design 主導）

### Task 5.1: UnverifiedBanner（D-N）

**Files:** Create `frontend/components/UnverifiedBanner.tsx`, test

- [ ] **Step 1: 測試**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { UnverifiedBanner } from "@/components/UnverifiedBanner";

describe("UnverifiedBanner", () => {
  it("shows warning + unverified snippets when not verified & has citations", () => {
    render(<UnverifiedBanner data={{ verified:false, has_citations:true, unverified:["[X, p.1]"] }} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/未驗證/);
    expect(screen.getByText(/\[X, p\.1\]/)).toBeInTheDocument();
  });
  it("renders nothing when verified", () => {
    const { container } = render(<UnverifiedBanner data={{ verified:true, has_citations:true, unverified:[] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: 紅** - [ ] **Step 3: 實作**　shadcn Alert(variant warning)；`verified || !has_citations`→`null`；列出 `unverified` 片段 + 提醒「請以教科書核對」。- [ ] **Step 4: 綠** - [ ] **Step 5: Commit**

### Task 5.2: Watermark（§6.7）

**Files:** Create `frontend/components/Watermark.tsx`, test

- [ ] **Step 1: 測試**　`render(<Watermark/>); expect(screen.getByText("教育用途，內容基於教科書")).toBeInTheDocument();`
- [ ] **Step 2: 紅** - [ ] **Step 3: 實作**　固定小字句。- [ ] **Step 4: 綠** - [ ] **Step 5: Commit**

### Task 5.3: DisclaimerModal（§6.7，手機 bottom-sheet）

**Files:** Create `frontend/components/DisclaimerModal.tsx`, test

- [ ] **Step 1: 測試**

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DisclaimerModal } from "@/components/DisclaimerModal";

beforeEach(() => localStorage.clear());
describe("DisclaimerModal", () => {
  it("blocks until accepted, then persists & hides", async () => {
    const { rerender } = render(<DisclaimerModal />);
    expect(screen.getByText(/教育用途/)).toBeInTheDocument();
    expect(screen.getByText(/系統可能出錯/)).toBeInTheDocument();
    expect(screen.getByText(/查詢日誌/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /同意/ }));
    rerender(<DisclaimerModal />);
    expect(screen.queryByText(/系統可能出錯/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 紅** - [ ] **Step 3: 實作**　用 `lib/disclaimer`；未同意→開啟（手機 bottom-sheet/near-fullscreen，桌面 Dialog）；三點內容；「我了解並同意」→`acceptDisclaimer()`+關閉。`open` 由 `isDisclaimerAccepted()` 推導（client useEffect 初始化避免 hydration 不一致）。- [ ] **Step 4: 綠** - [ ] **Step 5: Commit**

### Task 5.4: FeedbackButtons（per-turn + 首次倒讚提示）

**Files:** Create `frontend/components/FeedbackButtons.tsx`, test

- [ ] **Step 1: 測試**

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "@/lib/api";
import { FeedbackButtons } from "@/components/FeedbackButtons";

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
describe("FeedbackButtons", () => {
  it("downvote opens text box + first-downvote report hint; submit posts message_id", async () => {
    const spy = vi.spyOn(api, "postFeedback").mockResolvedValue();
    render(<FeedbackButtons messageId="t-aa" />);
    await userEvent.click(screen.getByRole("button", { name: /👎|倒讚|不準確/ }));
    expect(screen.getByText(/回報/)).toBeInTheDocument();          // 首次倒讚提示
    await userEvent.type(screen.getByRole("textbox"), "頁碼錯");
    await userEvent.click(screen.getByRole("button", { name: /送出回饋|提交/ }));
    expect(spy).toHaveBeenCalledWith({ messageId: "t-aa", rating: -1, text: "頁碼錯" });
  });
  it("upvote posts immediately", async () => {
    const spy = vi.spyOn(api, "postFeedback").mockResolvedValue();
    render(<FeedbackButtons messageId="t-aa" />);
    await userEvent.click(screen.getByRole("button", { name: /👍|讚|有幫助/ }));
    expect(spy).toHaveBeenCalledWith({ messageId: "t-aa", rating: 1 });
  });
});
```

- [ ] **Step 2: 紅** - [ ] **Step 3: 實作**　`{ messageId: string }`；👍→`postFeedback({messageId,rating:1})`；👎→展開選填文字框，若 `shouldPromptFirstDownvote()` 顯示回報機制說明並 `markFirstDownvotePrompted()`；送出→`postFeedback({messageId,rating:-1,text})`；送出後顯示「已收到回饋」、防重複；≥44px 觸控、👍/👎 間距。- [ ] **Step 4: 綠** - [ ] **Step 5: Commit**

### Task 5.5: ErrorState

**Files:** Create `frontend/components/ErrorState.tsx`, test

- [ ] **Step 1: 測試**　`render(<ErrorState onRetry={fn}/>)`→有「重試」按鈕、點擊呼叫 `onRetry`；顯示友善繁中訊息。
- [ ] **Step 2: 紅** - [ ] **Step 3: 實作**　`{ error?: Error; onRetry():void }`；友善訊息（不洩漏內部）。- [ ] **Step 4: 綠** - [ ] **Step 5: Commit**

---

## PART 6 — RWD/mobile 收尾（§5.1）

### Task 6.1: 自動捲到底 + 回到最新 FAB

**Files:** Create `frontend/lib/useStickToBottom.ts`, test `frontend/lib/__tests__/useStickToBottom.test.ts`；接入 `MessageList`

- [ ] **Step 1: 測試（hook 邏輯）**　以假的 scroll 容器測：在底部時 `shouldAutoScroll===true`；上捲後 `false` 且 `showJumpToLatest===true`；呼叫 `jumpToLatest()` 後回 true。
- [ ] **Step 2: 紅** - [ ] **Step 3: 實作 hook**（純邏輯：依 scrollTop/scrollHeight/clientHeight 判斷貼底；expose `containerRef`, `showJumpToLatest`, `jumpToLatest`）。
- [ ] **Step 4: 接入 MessageList**　串流新內容自動捲底（貼底時）；非貼底顯示「回到最新」FAB。
- [ ] **Step 5: 綠 + Commit**

### Task 6.2: RWD 手動驗收 checklist

**Files:** （驗收，無新檔；必要的 dvh/safe-area utility 已在 globals/layout）

- [ ] **Step 1: 本機跑** `npm run dev`，用瀏覽器 devtools 模擬 360×640 / 390×844 / 768 / 1024：
  - dvh 滿版、無雙捲軸；composer 釘底、軟鍵盤（行動裝置或模擬）不遮輸入；
  - safe-area：iPhone 模擬下 composer/header 不被瀏海/home indicator 裁切；
  - 觸控目標 ≥44px（👍/👎、送出、引文卡、免責按鈕）；
  - 引文圖點擊放大全螢幕、標籤可讀；snippet 手機可展開；
  - 串流自動捲底、上捲出現「回到最新」、字級 ≥16px 不觸發 iOS 放大。
- [ ] **Step 2: 記錄結果**　把通過項記入 PR 說明（對齊 spec §5.1 驗收目標窗）。

---

## PART 7 — dump-golden 解耦 + 協定交叉檢查 + DL-027

### Task 7.1: 重新指向 dump-golden 輸出檔（不再蓋後端 golden）

**Files:** Modify `frontend/scripts/dump-golden-stream.mjs`

- [ ] **Step 1: 改 outputPath**　`../../infra/golden/ai_stream_golden.jsonl` → `../../infra/golden/ai_stream_wire_sample.json`；更新檔頭註解：「協定參考樣本（真 SDK wire bytes）；**後端 golden 為 `ai_stream_golden.jsonl`、由 `test_api_chat_sse_unit.py` 手工維護，勿由本腳本覆寫**」。
- [ ] **Step 2: 跑腳本 + 確認不動後端 golden**　Run: `node frontend/scripts/dump-golden-stream.mjs && git status --short`
Expected: 只新增/改 `infra/golden/ai_stream_wire_sample.json`；`ai_stream_golden.jsonl` **未變**。
- [ ] **Step 3: Commit**　`git add frontend/scripts/dump-golden-stream.mjs infra/golden/ai_stream_wire_sample.json && git commit -m "chore: decouple dump-golden wire sample from backend golden"`

### Task 7.2: 協定交叉檢查測試（wire sample vs 後端 part 形狀）

**Files:** Create `frontend/lib/__tests__/wire-protocol.test.ts`

- [ ] **Step 1: 測試**　讀 `infra/golden/ai_stream_wire_sample.json` 的 `wire`，切 `data: ` 行解析，斷言：`start` 有 `messageId`、有 `text-delta`、收尾 `[DONE]`；並讀後端 `infra/golden/ai_stream_golden.jsonl` 斷言其 `data-sources`/`data-verification` **無** `transient`、`start` 有 `messageId`（與 §6 後端契約一致）。
- [ ] **Step 2: 跑→（紅/綠）**　調整斷言到實際格式；Run `npm test -- wire-protocol`。
- [ ] **Step 3: Commit**

### Task 7.3: 寫 DL-027 到 decisions.md

**Files:** Modify `docs/decisions.md`

- [ ] **Step 1: 追加條目**（沿用既有 DL 格式；狀態 APPROVED（委派），裁決者＝專案負責人，日期 2026-06-14）

標題：`## DL-027: Phase 10 前端協定落地——per-turn turn_id（start.messageId）+ data parts 改 persistent + dump-golden 解耦`
內容要點：背景（§5.6 start 無 messageId、§6.7 per-turn 回饋、D-H persistent）；提案（migration 008 turn_id；`start.messageId=turn_id`；`data-sources`/`data-verification` 去 transient 入 message.parts；`/feedback` 改 by `message_id`；dump 腳本輸出解耦至 `ai_stream_wire_sample.json`；dev 頁圖 placeholder-on-error；v1 免登入沿用 DL-016）；影響檔案（§5.6/§5.7、chat.py/main.py/feedback.py、005→008、ai_stream_golden.jsonl）；影響評估（皆可逆、不動檢索/LLM 核心、RAGAS 不受影響）。

- [ ] **Step 2: Commit**　`git add docs/decisions.md && git commit -m "docs(decisions): DL-027 前端協定落地（per-turn/persistent/解耦）"`

---

## PART 8 — 整合 + build smoke + 收尾

### Task 8.1: ChatPanel 整合測試（mock UI-message-stream transport）

**Files:** Create `frontend/components/__tests__/ChatPanel.integration.test.tsx`, helper `frontend/lib/__tests__/_sseFixture.ts`

- [ ] **Step 1: SSE fixture helper**

```ts
// 組一段 UI-message-stream SSE bytes（與後端 emitter 同格式）
export function uiMessageStreamResponse(): Response {
  const frames = [
    { type: "start", messageId: "t-aa" },
    { type: "data-sources", data: { sources: [{ book_title:"Gray", edition:"42", page:812, figure:"Fig.7-23", image_url:"/p/1.webp", snippet:"肱二頭肌起於喙突", score:0.9 }] } },
    { type: "text-start", id: "t0" },
    { type: "text-delta", id: "t0", delta: "起於喙突 [Gray, p.812, Fig.7-23]。" },
    { type: "text-end", id: "t0" },
    { type: "data-verification", data: { verified:true, has_citations:true, unverified:[] } },
    { type: "finish" },
  ];
  const body = frames.map(f => `data: ${JSON.stringify(f)}\n\n`).join("") + "data: [DONE]\n\n";
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream", "x-vercel-ai-ui-message-stream": "v1" } });
}
```

- [ ] **Step 2: 整合測試**

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPanel } from "@/components/ChatPanel";
import { uiMessageStreamResponse } from "@/lib/__tests__/_sseFixture";

beforeEach(() => { localStorage.setItem("anatomy-rag:disclaimer:v1","1"); vi.restoreAllMocks(); });

describe("ChatPanel e2e (mock stream)", () => {
  it("shows citations and streamed answer with watermark", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(uiMessageStreamResponse()));
    render(<ChatPanel />);
    await userEvent.type(screen.getByRole("textbox"), "肱二頭肌起點?");
    await userEvent.click(screen.getByRole("button", { name: /送出/ }));
    await waitFor(() => expect(screen.getByText(/起於喙突/)).toBeInTheDocument());
    expect(screen.getByText(/引用/)).toBeInTheDocument();
    expect(screen.getByText(/教育用途，內容基於教科書/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 跑**　Run: `npm test -- ChatPanel.integration`　Expected: PASS。
  - **pin-verify A**：若 `message.id` 不等於 `start.messageId`（FeedbackButtons 拿不到 turn_id）→ 啟用 fallback：後端 `data-verification` payload 併 `turn_id`，`MessageBubble` 從該 part 取 id 傳給 `FeedbackButtons`（spec §4 已載）。同步調整 Part 0 golden 與此 fixture。
  - **pin-verify B**：error chunk → useChat error 狀態（另寫一條 fixture 含 `{type:"error",errorText:...}` 測 `ErrorState`）。
- [ ] **Step 4: Commit**

### Task 8.2: 全量 build + lint + test

- [ ] **Step 1:** `cd frontend && npm run build`（**pin-verify TS6.0↔Next16**：若 type-check 報錯且與 UI 無關，記錄並查 Next16 release notes；必要時 `npm i -D typescript@<Next16 支援版>` 並回報）。
- [ ] **Step 2:** `npm test`（全綠）+ `npm run lint`。
- [ ] **Step 3:** `cd .. && make up` 起 compose（mock 模式），瀏覽器打開前端→真打 mock `/chat`，肉眼確認：先引用面板→串流文字→浮水印、👍/👎 寫入（看後端 log）、免責視窗。
- [ ] **Step 4: Commit**（若有修正）

### Task 8.3: 最終審查（MUST 跨模型）

- [ ] **Step 1:** Opus spec/quality 雙審（`Agent(model="opus")`）：對照 spec §0–§11 逐項覆蓋；查 emitter 契約、PII（前端不送 user_id）、引文/浮水印/banner 合規、RWD 驗收。
- [ ] **Step 2:** Codex 終審 `/codex:review`（Part 0 後端 + 前端全集）；逐輪修到 clean。
- [ ] **Step 3:** `superpowers:finishing-a-development-branch` 收尾（merge/PR 由使用者裁決）。

---

## Self-Review（against spec）

- **Spec coverage**：(a) per-turn→0.2–0.5、8.1；(a′) persistent→0.2/0.5；(b) 套件→1.1–1.4；(c) 佔位圖→4.1；(d) 免登入→無 auth UI（ChatPanel 不送憑證）；(e) 鎖版/DefaultChatTransport/解耦→全程約束 + 2.5 + 7.1；(f) DL-027→7.3。§3 元件→Part 3–5 全覆蓋；§4 render-by-type→3.1；§5 狀態→3.2/3.4/5.5；§5.1 RWD→Part 6 + 各元件契約；§6 golden/解耦→0.5/7.1/7.2；§7 setup→Part 1；§8 測試→各 task + 8.1；§9 後端 pre-step→Part 0；§10 pin-verify→8.1/8.2；§11 DoD→8.2/8.3。
- **Placeholder scan**：視覺元件以「完整測試＋契約」交付，非 TODO；frontend-design 在契約內實作。pin-verify 為實作期查證點、已給 fallback。
- **Type consistency**：`AnatomyUIMessage`/`Citation`/`SourcesData`/`VerificationData`（2.1）；`postFeedback({messageId,rating,text})`（2.4）→ FeedbackButtons（5.4）→ 後端 `message_id`（0.4）一致；`turn_id`（0.1–0.5）＝`start.messageId`＝`message.id` 一致。
- **執行順序建議**：Part 0 →（Codex 審）→ Part 1 → Part 2 →（Part 4/5 葉元件可先於 3.1 整合）→ Part 3 → Part 6 → Part 7 → Part 8。
