# Phase 8 — API 與 SSE 核心整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`). 全程 mock（mock encoder / mock LLM / 假資料 / fakeredis 或注入 fake），CI 零真實 OpenAI 呼叫、零 token 費用；真實 key smoke 留手動。

**Goal:** 把 Phase 3（encoder）/5（檢索）/6（LLM）整合成可串流的 `/chat` 端點：UI Message Stream SSE（DL-018，`data-sources` 在第一個 text-delta 前）、條件式附圖、引文真實性驗證（DL-012/D-N）、多輪 DL-021、可插拔 auth（dev stub）、Redis token-bucket 限流、feedback 端點、`/healthz` `/warmup` 全鏈路預熱；**DB 連線不跨 LLM 串流（DL-012）**、**送 OpenAI 的 payload 無 user_id**。

**Architecture:** 四部分、皆獨立可測：
- **A 輸入與契約**：`schemas.py`（useChat 訊息正規化→只讀最後兩則 user、追問判定 DL-021、`ChatRequest`/`PageCitation` §5.7）、`encoder/client.py`（HTTP→`QueryRepr`，mock+fallback）、`cache/base.py`（`CacheProtocol`+`NoOpCache` seam；真 SemanticCache=Phase 7）。
- **B SSE 與引文**：`api/ai_stream.py`（UI Message Stream emitter + golden bytes 對照）、`api/citations.py`（`build_citations_and_images` + `verify_citations` D-N/DL-012）。
- **C 橫切**：`api/auth.py`（`get_current_user` dev stub + OIDC 介面）、`api/ratelimit.py`（Redis atomic Lua token-bucket、429+Retry-After、admin 豁免、DL-022 不逐筆寫 DB）。
- **D 編排與整合**：`api/chat.py`（§5.6 九步流程）、`api/feedback.py`、`api/main.py`（router + lifespan 全鏈路預熱）、端到端 SSE golden 測試。

**Tech Stack:** FastAPI async、sse-starlette（`EventSourceResponse`）、redis-py async（`redis.asyncio`，Lua `register_script`）、httpx（encoder client）、asyncpg（既有 pool）、pytest-asyncio。**無新套件**（皆已在 `backend/pyproject.toml`）。

---

## 研究結論（context7 + WebSearch；gemini scout 不可用，已依使用者授權 fallback）

UI Message Stream wire（v5/v6 一致，讀自 AI SDK 原始碼 schema，HIGH 信心）：
- 每幀 `data: <compact-json>\n\n`（compact＝`json.dumps(x, separators=(",",":"))`，須與 `JSON.stringify` 對齊）。
- 部件：`{"type":"start"}`（messageId 選填）/`{"type":"text-start","id":"t0"}`/`{"type":"text-delta","id":"t0","delta":"…"}`（**欄位是 `delta` 非 textDelta**；`id` 必填且三段共用）/`{"type":"text-end","id":"t0"}`/`{"type":"finish"}`；終止 `data: [DONE]\n\n`。
- 自訂資料部件：`{"type":"data-<name>","data":<json>,"transient":true}`（`transient:true`＝不寫進 message.parts，僅經 `onData`）。引文用 `data-sources`、驗證用 `data-verification`。
- 必注入標頭 **`x-vercel-ai-ui-message-stream: v1`**（其餘 `text/event-stream`/`no-cache`/`keep-alive`/`x-accel-buffering:no` sse-starlette 已設）。
- sse-starlette：yield `ServerSentEvent(data=<json_str>, sep="\n")`（**不要設 event name**）；預設 ping 是**註解行**（`: ping …`），AI SDK 解析器忽略（安全）；`ping=None` 不會關（回退 15s），要靜音用大間隔。**disconnect 不會自動取消 LLM**——在 generator 內輪詢 `await request.is_disconnected()` → break 以省 token。`sep="\n"` 才得 `\n\n`（預設可能 `\r\n`，golden 用實際 wire bytes 比對故不受影響，但統一 sep="\n"）。
- Redis 限流：atomic **Lua token-bucket**（避免 INCR 無 EXPIRE 的永久鎖死 race），`register_script` async 呼叫；429 + 整數 `Retry-After`。

> **golden 測試策略**：以**端到端實際 wire bytes** 為準——用 httpx `ASGITransport` 對真 app 跑 mock `/chat`，解析 SSE response 的每個 `data:` 行 JSON，與 `infra/golden/ai_stream_golden.jsonl`（每行一個 part 物件）逐一比對（順序＋內容）＋斷言 `data-sources` 在第一個 `text-delta` 前＋標頭＋`[DONE]`。

## 對抗式審查修正（Codex 2026-06-13，5 high + 3 medium；**覆蓋下方對應 Task 程式碼，實作以此為準**）

> 下列修正在實作時**取代**對應 Task 的原始碼/測試。每項都附正確碼。

**[F1/H] 串流收尾副作用 MUST `create_task`，不得 await（§5.6/CLAUDE.md）。** `ChatDeps` 加 `spawn: Callable[[Awaitable], None]`。production：`_spawn` 用 `asyncio.create_task` + 集合保留參考避免 GC + done callback 記錯。測試：`spawn=lambda coro: collected.append(coro)`，`_collect` 後 `await asyncio.gather(*collected)` 再斷言副作用內容（兼顧非阻塞與決定性）。chat 的 cache-hit 分支與正常收尾的 `log_query`/`cache.set` 一律經 `deps.spawn(...)`，**不得 `await` 在串流產生器內**。
```python
# main.py 提供 production spawn（保留參考 + 記錯）
_BG: set = set()
def _spawn(coro):
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(lambda x: (_BG.discard(x), x.exception() and logger.error("bg task 失敗", exc_info=x.exception())))
```

**[F2/H] 只快取已驗證答案（DL-012）。** orchestrator 守門 MUST 含 `verification.all_grounded`：
```python
if (not normalized.is_followup) and status == "ok" and verification.all_grounded:
    deps.spawn(deps.cache.set(normalized.query, answer, sources_payload, kb, verified=True))
```
新增測試：未驗證（含偽造引文）或無引文答案 → `cache.set` **絕不被呼叫**（spy cache）。

**[F3/H] 引文驗證須綁定 cited book + page（不可只比頁碼）。** `verify_citations` 改以 (book, page) 對組驗證；book 正規化涵蓋 `book_title` 與 `book_title+edition`（去空白、小寫），figure 對照「該 book/page」的 figures[]。
```python
def _norm_book(s: str) -> str:
    return "".join(s.split()).lower()

def verify_citations(answer: str, results: list[RetrievalResult]) -> VerificationResult:
    # 建 (norm_book, page) -> figures 集合；book 同時收 title 與 title+edition 兩種別名
    pages_by_book: dict[tuple[str, int], set[str]] = {}
    for r in results:
        figs = {f.lower() for f in (r.metadata.get("figures") or [])}
        for alias in {_norm_book(r.book_title), _norm_book(r.book_title + (r.edition or ""))}:
            pages_by_book.setdefault((alias, r.page_num), set()).update(figs)
    matches = list(_CITATION_RE.finditer(answer))
    if not matches:
        return VerificationResult(False, False, [])
    unverified = []
    for m in matches:
        book = _norm_book(m.group(1)); page = int(m.group(2))
        fig = (m.group(3) or "").strip().lower()
        figs = pages_by_book.get((book, page))
        ok = figs is not None and (not fig or fig in figs)
        if not ok:
            unverified.append(m.group(0))
    return VerificationResult(True, not unverified, unverified)
```
新增測試：跨書同頁碼——`[FakeBook, p.812]` 在 retrieved 只有 `Gray p.812` 時 **unverified**；`[Gray42, p.812]`（abbrev=title+edition）**verified**。

**[F4/H] 限流三桶單一 atomic Lua、all-or-nothing。** 一支 Lua 收三個 key 與各自 (cap, rate)，先全部 refill 並檢查，**全部足夠才一起扣**，否則一律不扣、回 `allowed=0` 與**最大** retry。`RateLimiter.check` 改為單次 `script(keys=[k_min,k_day,k_global], args=[caps..., rates..., now, cost])`。
```lua
-- KEYS=3 桶；ARGV: cap1,cap2,cap3, rate1,rate2,rate3, now_ms, cost
local now=tonumber(ARGV[7]); local cost=tonumber(ARGV[8])
local tok={}; local retry=0
for i=1,3 do
  local cap=tonumber(ARGV[i]); local rate=tonumber(ARGV[3+i])
  local b=redis.call('HMGET', KEYS[i], 'tokens','ts')
  local t=tonumber(b[1]); local ts=tonumber(b[2])
  if t==nil then t=cap; ts=now end
  t=math.min(cap, t + (now-ts)/1000*rate)
  tok[i]={t,cap,rate}
  if t < cost then retry=math.max(retry, math.ceil((cost-t)/rate*1000)) end
end
if retry>0 then return {0, retry} end
for i=1,3 do
  redis.call('HSET', KEYS[i], 'tokens', tok[i][1]-cost, 'ts', now)
  redis.call('PEXPIRE', KEYS[i], math.ceil(tok[i][2]/tok[i][3]*1000)+1000)
end
return {1, 0}
```
測試對應改為單次腳本呼叫（fake script 回 `[allowed, retry_ms]`）：全允許→扣並 allowed；任一不足→不扣、allowed=0、retry=max；admin 跳過；Redis 故障 fail-open。

**[F5/H] 影像抓取 async + 並行、串流前完成（§5.7/§5.6 禁同步重 IO）。** `fetch_bytes` 改 `async (uri)->bytes`；`build_citations_and_images` 改 `async`，routed 影像以 `asyncio.gather` 並行抓、加逾時；`sign_url` 可留同步（本地 presign，無網路）。`ChatDeps.fetch_bytes` 型別為 async。chat 內 `citations, images = await build_citations_and_images(...)`，且在 `yield start` **之前**完成（sources/first token 不被 S3 拖累整個 event loop）。
```python
async def build_citations_and_images(results, routing, *, sign_url, fetch_bytes):
    citations = [PageCitation(...) for r in results]   # 同前（sign_url 同步可）
    imgs = await asyncio.gather(*(fetch_bytes(results[i].page_image_uri) for i in routing.indices))
    return citations, list(imgs)
```
測試：`fetch_bytes` 用 async fake；斷言並行抓且只抓 routed indices。

**[F6/M] 請求驗證（§5.7）→ 400。** `normalize_chat` 後驗證：`1 <= len(query) <= 2000`、`conversation_id` 若有須合法 UUID、`metadata_filter` 為 dict|None；違反 raise `ValueError`，`/chat` route 捕捉 `ValueError` 回 **400**（非 500）。新增測試：超長 query、壞 UUID、非 dict metadata_filter → 400。

**[F7/M] golden 端到端比對「實際 wire bytes」。** D4 測試除語意外，MUST 斷言原始 frame 為 `data: <compact-json>\n\n`（無多餘空白/欄位）、`text-delta`/`text-start`/`text-end`/`finish`/`start` frame 與預期 compact 位元組逐字相等、終止為字面 `data: [DONE]\n\n`、分隔為 `\n\n`（非 `\r\n`）。動態 `data-sources` 行驗 compact 形狀 + 關鍵欄位（page/transient），不鎖 snippet 全文。

**[F8/M] LLM 串流失敗契約（§5.6 step 7）。** LLM 例外時：emit `error` part（`{"type":"error","errorText":...}`）→ **不** emit verification/正常 `finish` → emit `[DONE]` 收尾 → `status="error"` →`deps.spawn(log_query(... status='error'))` → `logger.exception`（Sentry 接線 Phase 9）。即「失敗串流」與「成功 finish」語意分離；client 不得把失敗當完成。測試：注入會拋的 LLM → 事件含 `error`、**不含** `finish`、以 `[DONE]` 收尾、log status=error。

> `ChatDeps` 更新欄位：`fetch_bytes: Callable[[str], Awaitable[bytes]]`（async）、新增 `spawn: Callable[[Awaitable], None]`。其餘同下方契約。

## 統一契約（跨檔一致，務必對齊）

```python
# schemas.py
@dataclass(frozen=True)
class NormalizedChat:
    query: str                    # 當前 user 訊息
    prev_query: str | None        # 前一則 user 訊息（無則 None）
    metadata_filter: dict | None
    conversation_id: str | None
    is_followup: bool             # DL-021 規則判定（且 prev_query 存在）

class PageCitation(BaseModel):     # §5.7（前端可見）
    book_title: str; edition: str | None = None; page: int
    figure: str | None = None; image_url: str; snippet: str; score: float

# encoder/client.py
class EncoderClientProtocol(Protocol):
    async def encode_query(self, text: str) -> QueryRepr: ...

# cache/base.py
@dataclass(frozen=True)
class CachedAnswer:
    answer: str; sources: list[dict]   # sources＝PageCitation.model_dump() 串列
class CacheProtocol(Protocol):
    async def get(self, query: str, kb_version: int) -> CachedAnswer | None: ...
    async def set(self, query: str, answer: str, sources: list[dict], kb_version: int, *, verified: bool) -> None: ...

# auth.py
@dataclass(frozen=True)
class User:
    user_id: str; is_admin: bool

# ratelimit.py
@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool; retry_after: int   # 秒（整數）
```

LLM 取得：`build_llm(settings)`（Phase 6）。影像路由：`route_images(results, intent)`（Phase 6）。prompt：`get_system_prompt()`、`build_user_text(text_context, user_query, prev_query)`（Phase 6）。檢索：`retrieve(pool, query, query_repr, metadata_filter, kb_version, top_n)`（Phase 5）。

## 檔案結構

| 檔案 | 職責 |
|---|---|
| `backend/src/anatomy_backend/api/schemas.py` | useChat 正規化（最後兩則 user）、追問判定（DL-021）、`PageCitation` |
| `backend/src/anatomy_backend/encoder/__init__.py` `encoder/client.py` | HTTP `/encode_query`→`QueryRepr`、`MockEncoderClient`、`build_encoder(settings)` |
| `backend/src/anatomy_backend/cache/__init__.py` `cache/base.py` | `CacheProtocol`/`CachedAnswer`/`NoOpCache`/`build_cache(settings)` |
| `backend/src/anatomy_backend/api/ai_stream.py` | UI Message Stream emitter（part builders + `ServerSentEvent` + 標頭常數）|
| `backend/src/anatomy_backend/api/citations.py` | `build_citations_and_images`、`verify_citations`（D-N） |
| `backend/src/anatomy_backend/api/auth.py` | `get_current_user` dev stub + OIDC 介面（DL-016） |
| `backend/src/anatomy_backend/api/ratelimit.py` | Redis atomic Lua token-bucket、429+Retry-After |
| `backend/src/anatomy_backend/api/chat.py` | `/chat` 九步編排（§5.6） |
| `backend/src/anatomy_backend/api/feedback.py` | `/feedback`→`query_logs` |
| `backend/src/anatomy_backend/api/main.py` | router 掛載 + lifespan 全鏈路預熱（擴充既有） |
| `infra/golden/ai_stream_golden.jsonl` | SSE part 序列 golden |
| `backend/tests/test_api_*_unit.py` / `test_api_chat_sse_db.py` | 各單元 + 端到端 SSE |

測試命名全域唯一（`test_api_*`）。多數為 unit；端到端 SSE 用 mock encoder/LLM + 假資料（需 DB 的標 `db`，否則純 mock）。

---

# Part A — 輸入與契約

### Task A1: schemas.py — useChat 正規化 + 追問判定（DL-021）+ PageCitation

**Files:** Create `backend/src/anatomy_backend/api/schemas.py`；Test `backend/tests/test_api_schemas_unit.py`

- [ ] **Step 1: 失敗測試**

```python
# backend/tests/test_api_schemas_unit.py
import pytest
from anatomy_backend.api.schemas import PageCitation, normalize_chat


def _msg(role, text):
    return {"role": role, "parts": [{"type": "text", "text": text}]}


def test_single_turn_no_prev():
    body = {"messages": [_msg("user", "肱二頭肌起點？")]}
    n = normalize_chat(body)
    assert n.query == "肱二頭肌起點？"
    assert n.prev_query is None
    assert n.is_followup is False


def test_reads_only_last_two_user_messages():
    body = {"messages": [
        _msg("user", "第一問很久以前"),
        _msg("assistant", "答一"),
        _msg("user", "肱二頭肌起點？"),
        _msg("assistant", "答二"),
        _msg("user", "那它的神經支配呢？"),
    ]}
    n = normalize_chat(body)
    assert n.query == "那它的神經支配呢？"
    assert n.prev_query == "肱二頭肌起點？"   # 倒數第二則 user，非 assistant
    assert "第一問" not in (n.prev_query or "")  # 更早歷史不得進入


def test_followup_detected_by_pronoun():
    body = {"messages": [_msg("user", "肱二頭肌起點？"), _msg("assistant", "x"),
                         _msg("user", "那它的神經支配呢？")]}
    assert normalize_chat(body).is_followup is True


def test_followup_detected_by_short_length():
    body = {"messages": [_msg("user", "胸鎖乳突肌的作用是什麼？"), _msg("assistant", "x"),
                         _msg("user", "起點")]}  # <8 字
    assert normalize_chat(body).is_followup is True


def test_not_followup_without_prev_even_if_pronoun():
    body = {"messages": [_msg("user", "它")]}  # 無前一問 → 不算追問
    n = normalize_chat(body)
    assert n.is_followup is False
    assert n.prev_query is None


def test_metadata_filter_and_conversation_id_passthrough():
    body = {"messages": [_msg("user", "q")], "metadata_filter": {"anatomy_system": "musculoskeletal"},
            "conversation_id": "11111111-1111-1111-1111-111111111111"}
    n = normalize_chat(body)
    assert n.metadata_filter == {"anatomy_system": "musculoskeletal"}
    assert n.conversation_id == "11111111-1111-1111-1111-111111111111"


def test_content_string_messages_also_supported():
    # 部分 useChat 版本送 content 字串而非 parts
    body = {"messages": [{"role": "user", "content": "純文字訊息"}]}
    assert normalize_chat(body).query == "純文字訊息"


def test_empty_or_no_user_message_raises():
    with pytest.raises(ValueError):
        normalize_chat({"messages": [_msg("assistant", "x")]})
    with pytest.raises(ValueError):
        normalize_chat({"messages": []})


def test_page_citation_shape():
    c = PageCitation(book_title="Gray", edition="42", page=812, figure="Fig.7-23",
                     image_url="https://s3/x.png", snippet="…", score=0.9)
    d = c.model_dump()
    assert d["page"] == 812 and d["figure"] == "Fig.7-23"
```

- [ ] **Step 2: 跑→FAIL**　Run: `uv run pytest backend/tests/test_api_schemas_unit.py -v` → ModuleNotFoundError

- [ ] **Step 3: 實作**

```python
# backend/src/anatomy_backend/api/schemas.py
"""請求正規化（§5.7 / §5.9 DL-021）+ PageCitation。

後端 MUST 只讀最後兩則 user 訊息（當前＋前一問）；其餘歷史 MUST NOT 進入任何 LLM payload。
追問判定為純規則（零 LLM 成本）：含中英指代詞或長度 < 8 字、且存在前一問。
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

# DL-021 §5.9：追問指代詞（中英）
_PRONOUNS = ("它", "其", "這", "那", "該", "this", "it", "that", "those", "these")
_FOLLOWUP_LEN = 8


@dataclass(frozen=True)
class NormalizedChat:
    query: str
    prev_query: str | None
    metadata_filter: dict | None
    conversation_id: str | None
    is_followup: bool


class PageCitation(BaseModel):
    book_title: str
    edition: str | None = None
    page: int
    figure: str | None = None
    image_url: str
    snippet: str
    score: float


def _text_of(msg: dict) -> str:
    """支援 parts（[{type:text,text}]）與 content 字串兩種 useChat 形狀。"""
    parts = msg.get("parts")
    if isinstance(parts, list):
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    content = msg.get("content")
    return content if isinstance(content, str) else ""


def _is_followup(query: str, prev_query: str | None) -> bool:
    if prev_query is None:
        return False
    if len(query.strip()) < _FOLLOWUP_LEN:
        return True
    return any(p in query for p in _PRONOUNS)


def normalize_chat(body: dict) -> NormalizedChat:
    messages = body.get("messages") or []
    user_texts = [_text_of(m) for m in messages if m.get("role") == "user"]
    user_texts = [t for t in user_texts if t]
    if not user_texts:
        raise ValueError("請求無任何 user 訊息")
    query = user_texts[-1]
    prev_query = user_texts[-2] if len(user_texts) >= 2 else None
    return NormalizedChat(
        query=query,
        prev_query=prev_query,
        metadata_filter=body.get("metadata_filter"),
        conversation_id=body.get("conversation_id"),
        is_followup=_is_followup(query, prev_query),
    )
```

- [ ] **Step 4: 跑→PASS**；**Step 5: commit** `feat(api): schemas——useChat 正規化(最後兩則 user)+追問判定 DL-021+PageCitation`

---

### Task A2: encoder/client.py — HTTP→QueryRepr + mock + fallback

**Files:** Create `backend/src/anatomy_backend/encoder/__init__.py`、`encoder/client.py`；Test `backend/tests/test_api_encoder_client_unit.py`

先確認 `colpali_service` `/encode_query` 回 `{tokens_bin:[b64], pooled_f32:b64, translated_q, lang, mt_model}`（已驗）；`QueryRepr.from_encode_query_response(payload)` 直接吃此 dict。

- [ ] **Step 1: 失敗測試（注入 fake httpx，不開真連線）**

```python
# backend/tests/test_api_encoder_client_unit.py
import base64, struct
import pytest
from anatomy_backend.encoder.client import EncoderClient, MockEncoderClient
from anatomy_backend.retrieval.query_repr import QueryRepr


def _payload():
    tok = base64.b64encode(b"\x00" * 16).decode()
    pooled = base64.b64encode(struct.pack("<128f", *([0.1] * 128))).decode()
    return {"tokens_bin": [tok, tok], "pooled_f32": pooled, "translated_q": "biceps origin",
            "lang": "en", "mt_model": "mock"}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload; self.status_code = status
    def json(self): return self._p
    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _FakeHTTP:
    def __init__(self, resp, record): self._resp = resp; self._rec = record
    async def post(self, url, json):
        self._rec["url"] = url; self._rec["json"] = json
        return self._resp


async def test_encode_query_builds_queryrepr():
    rec = {}
    client = EncoderClient("http://encoder:8001/encode_query", http=_FakeHTTP(_FakeResp(_payload()), rec))
    qr = await client.encode_query("biceps 起點")
    assert isinstance(qr, QueryRepr)
    assert qr.translated_q == "biceps origin" and qr.lang == "en"
    assert len(qr.tokens_bin) == 2
    assert rec["json"] == {"query": "biceps 起點"}


async def test_mock_encoder_is_deterministic_queryrepr():
    m = MockEncoderClient()
    a = await m.encode_query("q"); b = await m.encode_query("q")
    assert isinstance(a, QueryRepr)
    assert a.tokens_bin == b.tokens_bin and a.pooled_f32 == b.pooled_f32


async def test_fallback_url_used_when_primary_fails():
    rec = {}
    import httpx
    class _FlakyHTTP:
        def __init__(self): self.calls = []
        async def post(self, url, json):
            self.calls.append(url)
            if "primary" in url:
                raise httpx.ConnectError("down")
            return _FakeResp(_payload())
    http = _FlakyHTTP()
    client = EncoderClient("http://primary/encode_query", http=http,
                           fallback_url="http://fallback/encode_query")
    qr = await client.encode_query("q")
    assert isinstance(qr, QueryRepr)
    assert http.calls == ["http://primary/encode_query", "http://fallback/encode_query"]
```

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/encoder/__init__.py
from anatomy_backend.encoder.client import (
    EncoderClient, EncoderClientProtocol, MockEncoderClient, build_encoder,
)
__all__ = ["EncoderClient", "EncoderClientProtocol", "MockEncoderClient", "build_encoder"]
```
```python
# backend/src/anatomy_backend/encoder/client.py
"""後端→ColPali encoder 微服務 HTTP client（§5.1）。回 QueryRepr（引擎中立查詢表示）。

主 URL 失敗（連線/逾時/5xx）→ 試 fallback URL（Modal scale-to-zero，DL-011）。mock 供測試/ make up。
"""
from __future__ import annotations

import base64
import struct
from typing import Protocol

import httpx

from anatomy_backend.retrieval.query_repr import QueryRepr

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class EncoderClientProtocol(Protocol):
    async def encode_query(self, text: str) -> QueryRepr: ...


class EncoderClient:
    def __init__(self, primary_url: str, *, fallback_url: str = "",
                 http: httpx.AsyncClient | None = None) -> None:
        self._primary = primary_url
        self._fallback = fallback_url
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT)

    async def _post(self, url: str, text: str) -> QueryRepr:
        resp = await self._http.post(url, json={"query": text})
        resp.raise_for_status()
        return QueryRepr.from_encode_query_response(resp.json())

    async def encode_query(self, text: str) -> QueryRepr:
        try:
            return await self._post(self._primary, text)
        except (httpx.HTTPError,) as exc:
            if not self._fallback:
                raise
            return await self._post(self._fallback, text)


class MockEncoderClient:
    """決定性 QueryRepr（測試/ make up；不開連線）。"""

    async def encode_query(self, text: str) -> QueryRepr:
        tok = bytes(range(16))
        pooled = tuple(0.01 * (i % 7) for i in range(128))
        return QueryRepr(pooled_f32=pooled, tokens_bin=(tok, tok),
                         translated_q=text, lang="zh")


def build_encoder(settings) -> EncoderClientProtocol:
    if getattr(settings, "encoder_mock", True):
        return MockEncoderClient()
    return EncoderClient(settings.colpali_primary_url, fallback_url=settings.colpali_fallback_url)
```

- [ ] **Step 4: PASS**；**Step 5: commit** `feat(api): encoder client——HTTP /encode_query→QueryRepr + fallback + mock`

---

### Task A3: cache/base.py — CacheProtocol + NoOpCache seam（Phase 7 填）

**Files:** Create `backend/src/anatomy_backend/cache/__init__.py`、`cache/base.py`；Test `backend/tests/test_api_cache_seam_unit.py`

- [ ] **Step 1: 失敗測試**

```python
# backend/tests/test_api_cache_seam_unit.py
from types import SimpleNamespace
from anatomy_backend.cache import NoOpCache, build_cache


async def test_noop_get_is_always_miss():
    c = NoOpCache()
    assert await c.get("q", 1) is None


async def test_noop_set_is_safe_noop():
    c = NoOpCache()
    await c.set("q", "ans", [], 1, verified=True)  # 不拋
    assert await c.get("q", 1) is None  # 仍 miss


def test_build_cache_returns_noop_in_v1():
    assert isinstance(build_cache(SimpleNamespace()), NoOpCache)
```

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/cache/__init__.py
from anatomy_backend.cache.base import CachedAnswer, CacheProtocol, NoOpCache, build_cache
__all__ = ["CachedAnswer", "CacheProtocol", "NoOpCache", "build_cache"]
```
```python
# backend/src/anatomy_backend/cache/base.py
"""語意快取 seam（§6.4）。v1 Phase 8 只定義介面 + NoOpCache（永遠 miss）；
真 SemanticCache（redisvl + 本地 embedding、只快取已驗證答案）為 Phase 7。
DL-021：追問不查/不寫快取——由 chat.py 控制，不在此。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CachedAnswer:
    answer: str
    sources: list[dict]


class CacheProtocol(Protocol):
    async def get(self, query: str, kb_version: int) -> CachedAnswer | None: ...
    async def set(self, query: str, answer: str, sources: list[dict],
                  kb_version: int, *, verified: bool) -> None: ...


class NoOpCache:
    async def get(self, query: str, kb_version: int) -> CachedAnswer | None:
        return None

    async def set(self, query: str, answer: str, sources: list[dict],
                  kb_version: int, *, verified: bool) -> None:
        return None


def build_cache(settings) -> CacheProtocol:
    return NoOpCache()  # Phase 7 改回真 SemanticCache（依 settings）
```

- [ ] **Step 4: PASS**；**Step 5: commit** `feat(api): cache seam——CacheProtocol + NoOpCache（Phase 7 填真 SemanticCache）`

---

# Part B — SSE emitter 與引文

### Task B1: ai_stream.py — UI Message Stream emitter（DL-018）

**Files:** Create `backend/src/anatomy_backend/api/ai_stream.py`；Test `backend/tests/test_api_ai_stream_unit.py`

- [ ] **Step 1: 失敗測試（part 形狀 + 標頭 + compact JSON）**

```python
# backend/tests/test_api_ai_stream_unit.py
import json
from anatomy_backend.api import ai_stream as ais


def test_part_builders_shapes():
    assert ais.start_part() == {"type": "start"}
    assert ais.text_start_part("t0") == {"type": "text-start", "id": "t0"}
    assert ais.text_delta_part("t0", "Hi") == {"type": "text-delta", "id": "t0", "delta": "Hi"}
    assert ais.text_end_part("t0") == {"type": "text-end", "id": "t0"}
    assert ais.finish_part() == {"type": "finish"}


def test_data_part_is_transient_by_default():
    p = ais.data_part("sources", {"sources": [1]})
    assert p == {"type": "data-sources", "data": {"sources": [1]}, "transient": True}


def test_headers_include_marker():
    assert ais.UI_MESSAGE_STREAM_HEADERS["x-vercel-ai-ui-message-stream"] == "v1"


def test_sse_event_is_compact_json_with_newline_sep():
    ev = ais.sse_event(ais.text_delta_part("t0", "你好"))
    # ServerSentEvent：data 為 compact JSON、sep="\n"
    assert ev.data == json.dumps({"type": "text-delta", "id": "t0", "delta": "你好"},
                                 separators=(",", ":"), ensure_ascii=False)
    assert ev.sep == "\n"


def test_done_event():
    ev = ais.done_event()
    assert ev.data == "[DONE]" and ev.sep == "\n"
```

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/api/ai_stream.py
"""Vercel AI SDK UI Message Stream emitter（DL-018；無官方 Python lib 之核准例外）。

每幀 data: <compact-json>\n\n；自訂資料部件 type 前綴 data-（transient 不寫進 message.parts、
僅經 useChat onData）。標頭 x-vercel-ai-ui-message-stream: v1 必注入。研究確認 v5/v6 一致。
"""
from __future__ import annotations

import json

from sse_starlette import ServerSentEvent

# sse-starlette 已設 text/event-stream / no-cache / keep-alive / x-accel-buffering:no；
# 這裡只補 AI SDK marker（EventSourceResponse(headers=...) 注入）。
UI_MESSAGE_STREAM_HEADERS = {"x-vercel-ai-ui-message-stream": "v1"}


def start_part(message_id: str | None = None) -> dict:
    p: dict = {"type": "start"}
    if message_id is not None:
        p["messageId"] = message_id
    return p


def text_start_part(text_id: str) -> dict:
    return {"type": "text-start", "id": text_id}


def text_delta_part(text_id: str, delta: str) -> dict:
    return {"type": "text-delta", "id": text_id, "delta": delta}


def text_end_part(text_id: str) -> dict:
    return {"type": "text-end", "id": text_id}


def finish_part() -> dict:
    return {"type": "finish"}


def data_part(name: str, data, *, transient: bool = True) -> dict:
    """自訂資料部件：type=data-<name>、payload 欄位 data、transient 預設 True。"""
    p = {"type": f"data-{name}", "data": data}
    if transient:
        p["transient"] = True
    return p


def _compact(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def sse_event(part: dict) -> ServerSentEvent:
    """part → ServerSentEvent（data=compact JSON、sep="\n"、無 event name）。"""
    return ServerSentEvent(data=_compact(part), sep="\n")


def done_event() -> ServerSentEvent:
    return ServerSentEvent(data="[DONE]", sep="\n")
```

- [ ] **Step 4: PASS**（如 `ServerSentEvent` 簽章不接受 `sep` 或屬性名不同，依安裝版 sse-starlette 調整並 PR 註明——先 `uv run python -c "from sse_starlette import ServerSentEvent; import inspect; print(inspect.signature(ServerSentEvent))"` 確認）；**Step 5: commit** `feat(api): ai_stream——UI Message Stream emitter（DL-018，data-/transient/[DONE]/marker 標頭）`

---

### Task B2: citations.py — build_citations_and_images + verify_citations（D-N/DL-012）

**Files:** Create `backend/src/anatomy_backend/api/citations.py`；Test `backend/tests/test_api_citations_unit.py`

- [ ] **Step 1: 失敗測試**

```python
# backend/tests/test_api_citations_unit.py
from uuid import uuid4
from anatomy_backend.api.citations import build_citations_and_images, verify_citations
from anatomy_backend.llm.image_routing import ImageRoutingDecision
from anatomy_backend.retrieval.types import RetrievalResult


def _r(page_num, book="Gray", figs=("Fig.7-23",), pt="figure_heavy"):
    return RetrievalResult(page_id=uuid4(), score=0.9, book_title=book, edition="42",
                           page_num=page_num, page_image_uri=f"s3://b/p{page_num}.png",
                           docling_md="肱二頭肌起於喙突。" * 30,
                           metadata={"page_type": pt, "figures": list(figs)})


def test_build_citations_snippet_figure_and_signed_url():
    results = [_r(812), _r(813, figs=())]
    cits, imgs = build_citations_and_images(
        results, ImageRoutingDecision(indices=(0,), detail="high"),
        sign_url=lambda uri: f"https://signed/{uri}",
        fetch_bytes=lambda uri: b"PNG:" + uri.encode(),
    )
    assert cits[0].page == 812 and cits[0].figure == "Fig.7-23"
    assert cits[0].image_url == "https://signed/s3://b/p812.png"
    assert len(cits[0].snippet) <= 200
    assert cits[1].figure is None          # 無 figures → None
    assert imgs == [b"PNG:s3://b/p812.png"]  # 只抓 routed index 0


def test_build_citations_no_images_when_routing_empty():
    results = [_r(812)]
    cits, imgs = build_citations_and_images(
        results, ImageRoutingDecision(indices=()),
        sign_url=lambda u: u, fetch_bytes=lambda u: b"x")
    assert imgs == [] and len(cits) == 1


def test_verify_citations_grounded_and_unverified():
    results = [_r(812, book="Gray", figs=("Fig.7-23",)), _r(813, book="Gray", figs=())]
    answer = ("肱二頭肌起於喙突 [Gray, p.812, Fig.7-23]。"
              "某段落 [Gray, p.999]。")  # p.999 不在 retrieved
    v = verify_citations(answer, results)
    assert v.has_citations is True
    assert v.all_grounded is False
    assert any("p.999" in u or "999" in u for u in v.unverified)


def test_verify_citations_figure_not_on_page_is_unverified():
    results = [_r(812, figs=("Fig.7-23",))]
    answer = "x [Gray, p.812, Fig.9-99]。"  # 頁對、圖號不在該頁 figures[]
    v = verify_citations(answer, results)
    assert v.all_grounded is False


def test_verify_citations_all_grounded():
    results = [_r(812, figs=("Fig.7-23",))]
    answer = "肱二頭肌起於喙突 [Gray, p.812, Fig.7-23]。"
    v = verify_citations(answer, results)
    assert v.all_grounded is True and v.unverified == []


def test_verify_citations_no_citation_flagged():
    v = verify_citations("一段沒有任何引文的文字。", [_r(812)])
    assert v.has_citations is False and v.all_grounded is False
```

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/api/citations.py
"""引文衍生與真實性驗證（§5.7 / DL-012 / D-N）。

build_citations_and_images：RetrievalResult → PageCitation（前端）+ 依路由抓 LLM 影像 bytes。
verify_citations：解析回答內行內引文 [書名, p.頁, Fig.圖]，cited page 對照 retrieved、figure 對照
該頁 figures[]；無法佐證者列 unverified（前端警告 banner、且不入快取）。強制引文是安全網核心。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Callable

from anatomy_backend.api.schemas import PageCitation
from anatomy_backend.llm.image_routing import ImageRoutingDecision
from anatomy_backend.retrieval.types import RetrievalResult

_SNIPPET_LEN = 200
# 行內引文：[書名簡寫, p.頁碼 (或 頁碼), Fig.圖號（選填）]
_CITATION_RE = re.compile(
    r"\[\s*([^\],]+?)\s*,\s*p\.?\s*(\d+)\s*(?:,\s*([^\]]+?))?\s*\]",
    re.IGNORECASE,
)


def build_citations_and_images(
    results: list[RetrievalResult],
    routing: ImageRoutingDecision,
    *,
    sign_url: Callable[[str], str],
    fetch_bytes: Callable[[str], bytes],
) -> tuple[list[PageCitation], list[bytes]]:
    citations: list[PageCitation] = []
    for r in results:
        figures = r.metadata.get("figures") or []
        citations.append(PageCitation(
            book_title=r.book_title, edition=r.edition, page=r.page_num,
            figure=(figures[0] if figures else None),
            image_url=sign_url(r.page_image_uri),
            snippet=r.docling_md[:_SNIPPET_LEN], score=r.score,
        ))
    images = [fetch_bytes(results[i].page_image_uri) for i in routing.indices]
    return citations, images


@dataclass(frozen=True)
class VerificationResult:
    has_citations: bool
    all_grounded: bool
    unverified: list[str]   # 未佐證引文的原文片段（供 log / 前端 banner）


def verify_citations(answer: str, results: list[RetrievalResult]) -> VerificationResult:
    pages = {r.page_num for r in results}
    figs_by_page: dict[int, set[str]] = {}
    for r in results:
        figs_by_page.setdefault(r.page_num, set()).update(
            f.lower() for f in (r.metadata.get("figures") or [])
        )
    matches = list(_CITATION_RE.finditer(answer))
    if not matches:
        return VerificationResult(has_citations=False, all_grounded=False, unverified=[])
    unverified: list[str] = []
    for m in matches:
        page = int(m.group(2))
        fig = (m.group(3) or "").strip().lower()
        ok = page in pages
        if ok and fig:
            ok = fig in figs_by_page.get(page, set())
        if not ok:
            unverified.append(m.group(0))
    return VerificationResult(has_citations=True, all_grounded=not unverified, unverified=unverified)
```

- [ ] **Step 4: PASS**；**Step 5: commit** `feat(api): citations——build_citations_and_images + verify_citations 真實性驗證（DL-012/D-N）`

---

# Part C — 橫切（auth / 限流）

### Task C1: auth.py — get_current_user dev stub（DL-016）

**Files:** Create `backend/src/anatomy_backend/api/auth.py`；Test `backend/tests/test_api_auth_unit.py`

- [ ] **Step 1: 失敗測試**

```python
# backend/tests/test_api_auth_unit.py
from types import SimpleNamespace
from anatomy_backend.api.auth import User, resolve_user


def _settings(uid="00000000-0000-0000-0000-000000000001", mode="dev"):
    return SimpleNamespace(dev_user_id=uid, auth_mode=mode)


def test_dev_stub_returns_configured_user():
    u = resolve_user(_settings(), headers={})
    assert isinstance(u, User)
    assert u.user_id == "00000000-0000-0000-0000-000000000001"
    assert u.is_admin is False


def test_dev_admin_header_grants_admin():
    u = resolve_user(_settings(), headers={"x-dev-admin": "1"})
    assert u.is_admin is True


def test_production_mode_without_oidc_raises_not_implemented():
    import pytest
    with pytest.raises(NotImplementedError):
        resolve_user(_settings(mode="production"), headers={})
```

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/api/auth.py
"""可插拔認證（DL-016）。v1 dev stub 注入固定 user_id；production 留 OIDC 介面。

MUST NOT 將 user_id/學號送 LLM（由 chat.py 的 forbidden_identifiers 護欄保證）。
admin（教師）不受限流（§6.8）：dev 用 x-dev-admin 標頭；production 由 OIDC claim 判定。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from anatomy_backend.config import Settings, get_settings


@dataclass(frozen=True)
class User:
    user_id: str
    is_admin: bool


def resolve_user(settings: Settings, headers: dict) -> User:
    if settings.auth_mode == "dev":
        is_admin = headers.get("x-dev-admin") == "1"
        return User(user_id=settings.dev_user_id, is_admin=is_admin)
    # production：接回校內 SSO（OIDC）時實作——驗證 token、取 sub 與 role claim
    raise NotImplementedError("production OIDC 未接：請設定 SSO 或用 auth_mode=dev")


async def get_current_user(request: Request,
                           settings: Settings = Depends(get_settings)) -> User:
    return resolve_user(settings, {k.lower(): v for k, v in request.headers.items()})
```

> 註：`get_settings` 須可作 FastAPI 依賴（既有）；若簽章不符，於測試用 `resolve_user` 純函式驗證（已涵蓋），`get_current_user` 留 chat 整合測試覆蓋。

- [ ] **Step 4: PASS**；**Step 5: commit** `feat(api): auth——get_current_user dev stub + OIDC 介面（DL-016）+ admin 標頭`

---

### Task C2: ratelimit.py — Redis atomic Lua token-bucket（§6.8）

**Files:** Create `backend/src/anatomy_backend/api/ratelimit.py`；Test `backend/tests/test_api_ratelimit_unit.py`（用 fake redis script，不需真 Redis）

- [ ] **Step 1: 失敗測試**

```python
# backend/tests/test_api_ratelimit_unit.py
import pytest
from anatomy_backend.api.ratelimit import RateLimiter, RateLimitResult


class _FakeScript:
    """模擬 register_script 回傳之 async callable：依預設腳本回 [allowed, retry_ms]。"""
    def __init__(self, results):  # results: list[[allowed, retry_ms]] 依呼叫順序
        self._results = list(results); self.calls = []
    async def __call__(self, keys, args):
        self.calls.append((keys, args))
        return self._results.pop(0)


def _limiter(script, **over):
    cfg = dict(per_min=15, per_day=300, global_rps=20)
    cfg.update(over)
    return RateLimiter(script=script, **cfg)


async def test_allows_when_all_buckets_ok():
    s = _FakeScript([[1, 0], [1, 0], [1, 0]])  # user-min, user-day, global
    r = await _limiter(s).check(user_id="u1", is_admin=False)
    assert r == RateLimitResult(allowed=True, retry_after=0)
    assert len(s.calls) == 3


async def test_denies_and_returns_retry_after_seconds():
    s = _FakeScript([[0, 2400]])  # 第一個桶就拒（retry 2400ms→ceil 3s）
    r = await _limiter(s).check(user_id="u1", is_admin=False)
    assert r.allowed is False and r.retry_after == 3


async def test_admin_bypasses_all_buckets():
    s = _FakeScript([])  # 不應呼叫腳本
    r = await _limiter(s).check(user_id="teacher", is_admin=True)
    assert r.allowed is True and s.calls == []


async def test_redis_failure_fails_open():
    class _Boom:
        async def __call__(self, keys, args):
            raise RuntimeError("redis down")
    r = await _limiter(_Boom()).check(user_id="u1", is_admin=False)
    assert r.allowed is True  # fail-open，不因 Redis 故障鎖死使用者
```

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/api/ratelimit.py
"""Redis atomic token-bucket 限流（§6.8 / DL-022）。

多 worker 共用一 Redis；check-and-decrement 必須原子 → Lua（避免 INCR 無 EXPIRE 永久鎖死）。
per-user/分 + per-user/日 + global 三桶，任一拒即 429。admin（教師）豁免。Redis 故障 fail-open
（不鎖死使用者，但記 log）。高頻拒絕事件不逐筆寫 DB（DL-022）——僅 Redis 計數。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# KEYS[1]=bucket key；ARGV: capacity, refill_per_sec, now_ms, cost → 回 {allowed, retry_ms}
TOKEN_BUCKET_LUA = """
local cap=tonumber(ARGV[1]); local rate=tonumber(ARGV[2])
local now=tonumber(ARGV[3]); local cost=tonumber(ARGV[4])
local b=redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens=tonumber(b[1]); local ts=tonumber(b[2])
if tokens==nil then tokens=cap; ts=now end
tokens=math.min(cap, tokens + (now-ts)/1000*rate)
local allowed=0; local retry=0
if tokens>=cost then tokens=tokens-cost; allowed=1
else retry=math.ceil((cost-tokens)/rate*1000) end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(cap/rate*1000)+1000)
return {allowed, retry}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int


class RateLimiter:
    def __init__(self, *, script, per_min: int, per_day: int, global_rps: int) -> None:
        self._script = script  # redis.asyncio register_script 回傳之 async callable
        self._buckets = [
            ("min", per_min, per_min / 60.0),       # capacity, refill/sec
            ("day", per_day, per_day / 86400.0),
            ("global", global_rps, float(global_rps)),
        ]

    async def check(self, *, user_id: str, is_admin: bool, now_ms: int | None = None) -> RateLimitResult:
        if is_admin:
            return RateLimitResult(allowed=True, retry_after=0)
        import time
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        try:
            for name, cap, rate in self._buckets:
                key = f"rl:{name}:{user_id}" if name != "global" else "rl:global"
                allowed, retry_ms = await self._script(keys=[key], args=[cap, rate, now, 1])
                if int(allowed) == 0:
                    return RateLimitResult(allowed=False, retry_after=math.ceil(int(retry_ms) / 1000))
            return RateLimitResult(allowed=True, retry_after=0)
        except Exception:
            logger.warning("ratelimit Redis 失敗→fail-open", exc_info=True)
            return RateLimitResult(allowed=True, retry_after=0)
```

> 整合（main.py lifespan）：`script = redis_client.register_script(TOKEN_BUCKET_LUA)`；限額讀 `settings.rate_limit_per_user_min/day` 與 `rate_limit_global_rps`。chat.py 依結果回 429 + `Retry-After`。

- [ ] **Step 4: PASS**；**Step 5: commit** `feat(api): ratelimit——Redis atomic Lua token-bucket（per-user/min+day+global、admin 豁免、fail-open、DL-022）`

---

# Part D — 編排與整合

### Task D1: chat.py — /chat 九步編排 + SSE（§5.6）

**Files:** Create `backend/src/anatomy_backend/api/chat.py`；Test `backend/tests/test_api_chat_unit.py`（純函式 `run_chat_stream` 用注入 fakes，不需 DB/Redis）

> 設計：把可測核心抽成 `async def chat_event_stream(deps, normalized, user) -> AsyncIterator[ServerSentEvent]` 純產生器（deps＝注入的 encoder/llm/cache/retrieve_fn/citation fns/settings），FastAPI route 只做 auth+ratelimit+正規化+包 `EventSourceResponse`。如此核心流程零 I/O 依賴、可單測 SSE 事件序。

- [ ] **Step 1: 失敗測試（注入 fakes，斷言事件序與規則）**

```python
# backend/tests/test_api_chat_unit.py
import json
from uuid import uuid4
import pytest

from anatomy_backend.api.chat import ChatDeps, chat_event_stream
from anatomy_backend.api.schemas import NormalizedChat
from anatomy_backend.api.auth import User
from anatomy_backend.cache import NoOpCache
from anatomy_backend.encoder.client import MockEncoderClient
from anatomy_backend.llm.mock import MockLLMClient
from anatomy_backend.retrieval.types import RetrievalResult


def _result(page=812, pt="figure_heavy"):
    return RetrievalResult(uuid4(), 0.9, "Gray", "42", page, f"s3://b/p{page}.png",
                           "肱二頭肌起於喙突。" * 20, {"page_type": pt, "figures": ["Fig.7-23"]})


def _deps(llm=None, cache=None, results=None, logs=None):
    async def _retrieve(query, query_repr, metadata_filter, kb_version, top_n):
        return results if results is not None else [_result()]
    async def _log(**kw):
        (logs if logs is not None else []).append(kw)
    return ChatDeps(
        encoder=MockEncoderClient(),
        llm=llm or MockLLMClient(tokens=["肱二頭肌", "起於喙突", " [Gray, p.812, Fig.7-23]。"]),
        cache=cache or NoOpCache(),
        retrieve_fn=_retrieve,
        sign_url=lambda u: f"https://signed/{u}",
        fetch_bytes=lambda u: b"PNG",
        log_query=_log,
        kb_version=1,
        is_disconnected=_never_disconnected,
    )


async def _never_disconnected():
    return False


async def _collect(agen):
    return [ev async for ev in agen]


def _json_parts(events):
    out = []
    for ev in events:
        if ev.data == "[DONE]":
            out.append("[DONE]"); continue
        out.append(json.loads(ev.data))
    return out


def _norm(query="肱二頭肌起點？", prev=None, followup=False):
    return NormalizedChat(query=query, prev_query=prev, metadata_filter=None,
                          conversation_id=None, is_followup=followup)


async def test_event_sequence_sources_before_first_delta():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    types = [p if p == "[DONE]" else p["type"] for p in parts]
    assert types[0] == "start"
    i_src = next(i for i, t in enumerate(types) if t == "data-sources")
    i_delta = next(i for i, t in enumerate(types) if t == "text-delta")
    assert i_src < i_delta                       # sources 必在第一個 delta 前
    assert types[-1] == "[DONE]"
    assert "finish" in types and "text-end" in types


async def test_sources_payload_has_page_citations():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    src = next(p for p in parts if isinstance(p, dict) and p["type"] == "data-sources")
    assert src["transient"] is True
    assert src["data"]["sources"][0]["page"] == 812


async def test_deltas_concatenate_to_answer():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    text = "".join(p["delta"] for p in parts if isinstance(p, dict) and p["type"] == "text-delta")
    assert "肱二頭肌" in text


async def test_verification_data_part_emitted_before_finish():
    user = User("u1", False)
    parts = _json_parts(await _collect(chat_event_stream(_deps(), _norm(), user)))
    types = [p["type"] for p in parts if isinstance(p, dict)]
    assert "data-verification" in types
    assert types.index("data-verification") < types.index("finish")


async def test_followup_skips_cache_and_passes_prev_query():
    # 追問：MUST 不查快取；user-message 帶前一問
    captured = {}
    class _SpyLLM(MockLLMClient):
        async def stream_complete(self, system, user, images, *, image_detail="high",
                                  forbidden_identifiers=frozenset()):
            captured["user"] = user
            captured["forbidden"] = forbidden_identifiers
            async for t in super().stream_complete(system, user, images,
                                                   image_detail=image_detail,
                                                   forbidden_identifiers=forbidden_identifiers):
                yield t
    class _BoomCache(NoOpCache):
        async def get(self, q, kb):
            raise AssertionError("追問不得查快取")
    deps = _deps(llm=_SpyLLM(tokens=["x"]), cache=_BoomCache())
    user = User("u1", False)
    await _collect(chat_event_stream(deps, _norm(prev="肱二頭肌起點？", followup=True), user))
    assert "前一問：肱二頭肌起點？" in captured["user"]
    assert "u1" in captured["forbidden"]          # PII 護欄：user_id 在禁止集


async def test_pii_user_id_in_forbidden_identifiers():
    captured = {}
    class _SpyLLM(MockLLMClient):
        async def stream_complete(self, system, user, images, *, image_detail="high",
                                  forbidden_identifiers=frozenset()):
            captured["forbidden"] = forbidden_identifiers
            async for t in super().stream_complete(system, user, images,
                                                   image_detail=image_detail,
                                                   forbidden_identifiers=forbidden_identifiers):
                yield t
    await _collect(chat_event_stream(_deps(llm=_SpyLLM(tokens=["x"])), _norm(), User("stud-42", False)))
    assert "stud-42" in captured["forbidden"]


async def test_cache_hit_short_circuits_with_sources_and_done():
    from anatomy_backend.cache import CachedAnswer
    class _HitCache(NoOpCache):
        async def get(self, q, kb):
            return CachedAnswer(answer="快取答案 [Gray, p.812]。",
                                sources=[{"book_title": "Gray", "edition": "42", "page": 812,
                                          "figure": None, "image_url": "u", "snippet": "s", "score": 0.9}])
    logs = []
    deps = _deps(cache=_HitCache(), logs=logs)
    parts = _json_parts(await _collect(chat_event_stream(deps, _norm(), User("u1", False))))
    types = [p if p == "[DONE]" else p["type"] for p in parts]
    assert "data-sources" in types and types[-1] == "[DONE]"
    text = "".join(p["delta"] for p in parts if isinstance(p, dict) and p["type"] == "text-delta")
    assert text == "快取答案 [Gray, p.812]。"
    assert logs and logs[-1].get("cache_hit") is True


async def test_disconnect_stops_streaming_early():
    state = {"n": 0}
    async def _disc():
        state["n"] += 1
        return state["n"] > 1     # 第二次檢查時已斷線
    deps = _deps(llm=MockLLMClient(tokens=["a", "b", "c", "d"]))
    deps = deps._replace(is_disconnected=_disc) if hasattr(deps, "_replace") else deps
    # 若 ChatDeps 非 namedtuple，改建構新 deps 設 is_disconnected=_disc
    parts = _json_parts(await _collect(chat_event_stream(deps, _norm(), User("u1", False))))
    deltas = [p for p in parts if isinstance(p, dict) and p["type"] == "text-delta"]
    assert len(deltas) < 4         # 提前中止
```

> 註：`ChatDeps` 用 dataclass，欄位含 `encoder, llm, cache, retrieve_fn, sign_url, fetch_bytes, log_query, kb_version, is_disconnected`。`is_disconnected` 為 `async () -> bool`。route 包裝時傳 `request.is_disconnected`。

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/api/chat.py
"""/chat 編排與 SSE（§5.6 九步 / DL-009/012/018/021）。

可測核心 chat_event_stream（注入 deps，零框架/IO 綁定），route 層做 auth+ratelimit+正規化+
EventSourceResponse。流程：快取（追問跳過）→ encode（追問串接 retrieval_q）→ 檢索（連線於此段歸還，
不跨串流 DL-012）→ 條件式附圖 + 建引文 → 先送 sources → 串流 LLM（user_id 入 forbidden_identifiers）
→ 驗證引文（data-verification）→ finish/[DONE] → 非同步 log/cache.set（追問且通過驗證才寫）。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from anatomy_backend.api import ai_stream as ais
from anatomy_backend.api.auth import User, get_current_user
from anatomy_backend.api.citations import build_citations_and_images, verify_citations
from anatomy_backend.api.ratelimit import RateLimiter
from anatomy_backend.api.schemas import NormalizedChat, normalize_chat
from anatomy_backend.cache import CacheProtocol
from anatomy_backend.config import Settings, get_settings
from anatomy_backend.encoder.client import EncoderClientProtocol
from anatomy_backend.llm.client import LLMClientProtocol
from anatomy_backend.llm.image_routing import QueryIntent, route_images
from anatomy_backend.llm.prompts import build_user_text, get_system_prompt
from anatomy_backend.retrieval.types import RetrievalResult

logger = logging.getLogger(__name__)
router = APIRouter()
_TEXT_ID = "t0"


@dataclass
class ChatDeps:
    encoder: EncoderClientProtocol
    llm: LLMClientProtocol
    cache: CacheProtocol
    retrieve_fn: Callable[..., Awaitable[list[RetrievalResult]]]
    sign_url: Callable[[str], str]
    fetch_bytes: Callable[[str], bytes]
    log_query: Callable[..., Awaitable[None]]
    kb_version: int
    is_disconnected: Callable[[], Awaitable[bool]]
    top_n: int = 3


def _intent(normalized: NormalizedChat) -> QueryIntent:
    # v1：簡單 heuristic（Phase 8 OPEN，真分類器後續）——含「圖/figure/標示」傾向看圖，否則純文字
    q = normalized.query.lower()
    if any(k in q for k in ("圖", "figure", "fig", "標", "示意", "構造", "解剖位置")):
        return QueryIntent.FIGURE
    return QueryIntent.PURE_TEXT


async def chat_event_stream(deps: ChatDeps, normalized: NormalizedChat,
                            user: User) -> AsyncIterator[ServerSentEvent]:
    kb = deps.kb_version
    # 1) 快取（追問 MUST NOT 查/寫，DL-021）
    if not normalized.is_followup:
        cached = await deps.cache.get(normalized.query, kb)
        if cached is not None:
            yield ais.sse_event(ais.start_part())
            yield ais.sse_event(ais.data_part("sources", {"sources": cached.sources}))
            yield ais.sse_event(ais.text_start_part(_TEXT_ID))
            yield ais.sse_event(ais.text_delta_part(_TEXT_ID, cached.answer))
            yield ais.sse_event(ais.text_end_part(_TEXT_ID))
            yield ais.sse_event(ais.finish_part())
            yield ais.done_event()
            await deps.log_query(user_id=user.user_id, query=normalized.query,
                                 conversation_id=normalized.conversation_id, cache_hit=True,
                                 status="ok")
            return

    # 2) retrieval_q：追問串接前一問（DL-021，不含 assistant 回答）
    retrieval_q = (f"{normalized.prev_query}\n{normalized.query}"
                   if normalized.is_followup and normalized.prev_query else normalized.query)
    # 3) encode → 4) 檢索（連線於 retrieve_fn 內取得並歸還，不跨串流 DL-012）
    query_repr = await deps.encoder.encode_query(retrieval_q)
    results = await deps.retrieve_fn(retrieval_q, query_repr, normalized.metadata_filter,
                                     kb, deps.top_n)
    # 5) 條件式附圖 + 建引文 + 抓影像 bytes（圖 fetch 也在串流前完成）
    routing = route_images(results, _intent(normalized))
    citations, images = build_citations_and_images(
        results, routing, sign_url=deps.sign_url, fetch_bytes=deps.fetch_bytes)
    sources_payload = [c.model_dump() for c in citations]

    # 6) start + sources（必在第一個 delta 前）
    yield ais.sse_event(ais.start_part())
    yield ais.sse_event(ais.data_part("sources", {"sources": sources_payload}))

    # 7) 串流 LLM（user_id/學號入 forbidden_identifiers；DB 連線此時已歸還）
    text_context = "\n\n".join(r.docling_md for r in results)
    system = get_system_prompt()
    user_text = build_user_text(text_context, normalized.query,
                                normalized.prev_query if normalized.is_followup else None)
    forbidden = frozenset({user.user_id} | ({normalized.conversation_id} if False else set()))
    answer_parts: list[str] = []
    yield ais.sse_event(ais.text_start_part(_TEXT_ID))
    status = "ok"
    try:
        async for delta in deps.llm.stream_complete(system, user_text, images,
                                                    image_detail=routing.detail,
                                                    forbidden_identifiers=forbidden):
            if await deps.is_disconnected():
                status = "client_disconnect"
                break
            answer_parts.append(delta)
            yield ais.sse_event(ais.text_delta_part(_TEXT_ID, delta))
    except Exception:  # noqa: BLE001 —— 推 error 事件 + raise 給 Sentry（Phase 9）
        logger.exception("LLM 串流失敗")
        yield ais.sse_event({"type": "error", "errorText": "生成失敗，請重試"})
        status = "error"
    yield ais.sse_event(ais.text_end_part(_TEXT_ID))

    # 8) 引文真實性驗證 → data-verification（前端 banner；不入快取依據）
    answer = "".join(answer_parts)
    verification = verify_citations(answer, results)
    yield ais.sse_event(ais.data_part("verification", {
        "verified": verification.all_grounded, "has_citations": verification.has_citations,
        "unverified": verification.unverified}))

    # 9) finish + [DONE]
    yield ais.sse_event(ais.finish_part())
    yield ais.done_event()

    # 副作用：log（cache.set 只在非追問且通過驗證；NoOpCache v1 為 no-op）
    await deps.log_query(user_id=user.user_id, query=normalized.query,
                         conversation_id=normalized.conversation_id, cache_hit=False,
                         status=status, model_used=None)
    if not normalized.is_followup and status == "ok":
        await deps.cache.set(normalized.query, answer, sources_payload, kb,
                             verified=verification.all_grounded)


@router.post("/chat")
async def chat(request: Request, settings: Settings = Depends(get_settings),
               user: User = Depends(get_current_user)):
    body = await request.json()
    normalized = normalize_chat(body)
    limiter: RateLimiter = request.app.state.ratelimiter
    rl = await limiter.check(user_id=user.user_id, is_admin=user.is_admin)
    if not rl.allowed:
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail="請求過於頻繁，請稍後再試",
                            headers={"Retry-After": str(rl.retry_after)})
    deps: ChatDeps = request.app.state.build_chat_deps(request)  # main.py 注入工廠
    return EventSourceResponse(
        (ev async for ev in chat_event_stream(deps, normalized, user)),
        headers=ais.UI_MESSAGE_STREAM_HEADERS, ping=3600, sep="\n")
```

> 實作注意：(a) `EventSourceResponse(..., sep="\n")` 若該版不支援 response 層 sep，改在每個 `ServerSentEvent` 設 sep（emitter 已設）。(b) `forbidden` 行的 `conversation_id` 佔位寫法請簡化為 `frozenset({user.user_id})`（conversation_id 非 PII，不需放）。(c) error part 直接用 dict（`{"type":"error","errorText":...}`）。(d) route 對 ValueError（normalize 失敗）回 400。

- [ ] **Step 2 跑→FAIL；Step 3 實作；Step 4 跑→PASS**：`uv run pytest backend/tests/test_api_chat_unit.py -v`（修正測試中 `_replace` 註記：ChatDeps 為一般 dataclass，disconnect 測試請直接以 `ChatDeps(...)` 建新實例設 `is_disconnected=_disc`）。
- [ ] **Step 5: commit** `feat(api): chat——/chat 九步編排 + SSE 事件序（sources 先於 delta、追問跳快取、PII 護欄、引文驗證、disconnect 取消）`

---

### Task D2: feedback.py — /feedback → query_logs（§6.5）

**Files:** Create `backend/src/anatomy_backend/api/feedback.py`；Test `backend/tests/test_api_feedback_unit.py`

先 `Read backend/src/anatomy_backend/db/migrations/versions/005_query_logs.py` 確認 `feedback`（SMALLINT）與 `feedback_text`（TEXT，DL-022）欄位名與 `conversation_id`。

- [ ] **Step 1: 失敗測試（注入 fake writer）**

```python
# backend/tests/test_api_feedback_unit.py
import pytest
from anatomy_backend.api.feedback import FeedbackInput, apply_feedback


async def test_thumbs_down_with_text_written():
    rec = {}
    async def _writer(**kw): rec.update(kw)
    await apply_feedback(FeedbackInput(conversation_id="c1", rating=-1, text="頁碼錯誤"),
                         user_id="u1", writer=_writer)
    assert rec["rating"] == -1 and rec["text"] == "頁碼錯誤" and rec["user_id"] == "u1"


async def test_rating_must_be_plus_minus_one():
    with pytest.raises(ValueError):
        FeedbackInput(conversation_id="c1", rating=0, text=None)


async def test_text_truncated_to_limit():
    rec = {}
    async def _writer(**kw): rec.update(kw)
    await apply_feedback(FeedbackInput(conversation_id="c1", rating=1, text="x" * 5000),
                         user_id="u1", writer=_writer)
    assert len(rec["text"]) <= 2000
```

- [ ] **Step 2: FAIL**；**Step 3: 實作**

```python
# backend/src/anatomy_backend/api/feedback.py
"""使用者回饋（§6.5）：👍/👎 + 文字 → query_logs.feedback / feedback_text（DL-022）。

rating ∈ {1,-1}；text 應用層截斷 ≤2000；MUST 經 auth（user_id 由 get_current_user 提供）。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request

from anatomy_backend.api.auth import User, get_current_user

router = APIRouter()
_TEXT_MAX = 2000


@dataclass(frozen=True)
class FeedbackInput:
    conversation_id: str
    rating: int
    text: str | None

    def __post_init__(self) -> None:
        if self.rating not in (1, -1):
            raise ValueError("rating 必須為 1 或 -1")


async def apply_feedback(fb: FeedbackInput, *, user_id: str,
                         writer: Callable[..., Awaitable[None]]) -> None:
    text = fb.text[:_TEXT_MAX] if fb.text else fb.text
    await writer(user_id=user_id, conversation_id=fb.conversation_id,
                 rating=fb.rating, text=text)


@router.post("/feedback")
async def feedback(request: Request, user: User = Depends(get_current_user)):
    body = await request.json()
    fb = FeedbackInput(conversation_id=body.get("conversation_id"),
                       rating=int(body.get("rating", 0)), text=body.get("text"))
    await apply_feedback(fb, user_id=user.user_id,
                         writer=request.app.state.write_feedback)
    return {"ok": True}
```

- [ ] **Step 4: PASS**；**Step 5: commit** `feat(api): feedback——/feedback 寫 query_logs.feedback/feedback_text（§6.5/DL-022）`

---

### Task D3: main.py — router 掛載 + lifespan 全鏈路預熱

**Files:** Modify `backend/src/anatomy_backend/api/main.py`（擴充既有 lifespan/healthz/warmup）；Test `backend/tests/test_api_main_wiring_unit.py`

- [ ] **Step 1: 失敗測試（app 啟動、路由存在、warmup 不打真服務）**

```python
# backend/tests/test_api_main_wiring_unit.py
from anatomy_backend.api.main import app


def test_chat_and_feedback_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/chat" in paths and "/feedback" in paths
    assert "/healthz" in paths and "/warmup" in paths
```

> 整合層（lifespan 真的建 pool/redis/encoder/llm/cache、register Lua、warmup dummy query）以既有 LifespanManager 模式或 db 整合測試覆蓋；單元只驗路由註冊（不啟動 lifespan）。

- [ ] **Step 2: FAIL**；**Step 3: 實作**（擴充 main.py）

要點（在既有 `main.py` 基礎上）：
- lifespan：`get_settings()` 後建立 `app.state`：`pool`（db.pool）、`redis`（redis.asyncio.from_url）、`ratelimiter = RateLimiter(script=redis.register_script(TOKEN_BUCKET_LUA), per_min=…, per_day=…, global_rps=…)`、`encoder = build_encoder(settings)`、`llm = build_llm(settings)`、`cache = build_cache(settings)`、`write_feedback`（寫 query_logs 的 async fn）、`build_chat_deps(request)`（組 `ChatDeps`：retrieve_fn 包 `retrieve(pool, …)`、sign_url/fetch_bytes 走 S3/MinIO client、log_query 寫 query_logs、is_disconnected=request.is_disconnected）。
- `app.include_router(chat.router)`、`app.include_router(feedback.router)`。
- `/warmup`：背景對 pipeline 跑一次 dummy（mock 模式為 no-op-ish）；不阻塞。
- 收尾：lifespan 結束關閉 pool/redis。
- **保留** 既有 `/healthz`。

```python
# 片段（與既有 main.py 整合；完整見實作）
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from anatomy_backend.api import chat, feedback
from anatomy_backend.api.ratelimit import RateLimiter, TOKEN_BUCKET_LUA
from anatomy_backend.cache import build_cache
from anatomy_backend.config import get_settings
from anatomy_backend.encoder.client import build_encoder
from anatomy_backend.llm import build_llm
# ... db pool / redis / s3 client imports

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # build pool/redis/clients → app.state.*（mock 模式 encoder/llm 為 mock）
    # app.state.ratelimiter = RateLimiter(script=app.state.redis.register_script(TOKEN_BUCKET_LUA), ...)
    # app.state.build_chat_deps = lambda request: ChatDeps(...)
    yield
    # close pool/redis

app = FastAPI(title="anatomy-rag-backend", version="0.0.0", lifespan=lifespan)
app.include_router(chat.router)
app.include_router(feedback.router)
# 既有 /healthz /warmup 保留
```

> 實作注意：mock 模式（`encoder_mock`/`llm_mock`=True）下 lifespan 仍可建 pool/redis（測試用 fake 或 db job）；單元測試只 import app 驗路由，不啟動 lifespan。S3/MinIO client（sign_url/fetch_bytes）：用 boto3（ingest 已用）或既有 storage 模式；mock/測試注入假 fn。

- [ ] **Step 4: PASS**（`uv run pytest backend/tests/test_api_main_wiring_unit.py -v`）；**Step 5: commit** `feat(api): main——掛載 chat/feedback router + lifespan 建 deps/ratelimiter/全鏈路預熱`

---

### Task D4: 端到端 SSE golden 對照（實際 wire bytes）

**Files:** Create `infra/golden/ai_stream_golden.jsonl`、Test `backend/tests/test_api_chat_sse_db.py`（或純 mock `test_api_chat_sse_unit.py`，若可不依賴 DB）

> 用 httpx `ASGITransport` + `asgi-lifespan`（既有 dev dep）跑真 app 的 `/chat`，注入 mock encoder/LLM + 假 retrieve（monkeypatch `app.state.build_chat_deps` 或設 `encoder_mock/llm_mock` + 注入 fake retrieve_fn/sign/fetch/log）。解析 SSE response，抽每個 `data:` 行。

- [ ] **Step 1: 建 golden（canonical 序列）**

`infra/golden/ai_stream_golden.jsonl`（每行一個 part；`[DONE]` 以字面字串行表示）：
```
{"type":"start"}
{"type":"data-sources","data":{"sources":[{"book_title":"Gray","edition":"42","page":812,"figure":"Fig.7-23","image_url":"https://signed/s3://b/p812.png","snippet":"…","score":0.9}]},"transient":true}
{"type":"text-start","id":"t0"}
{"type":"text-delta","id":"t0","delta":"肱二頭肌"}
{"type":"text-delta","id":"t0","delta":"起於喙突 [Gray, p.812, Fig.7-23]。"}
{"type":"text-end","id":"t0"}
{"type":"data-verification","data":{"verified":true,"has_citations":true,"unverified":[]}}
{"type":"finish"}
"[DONE]"
```
（snippet 等動態欄位在測試中以「結構＋關鍵欄位」比對，不逐字元鎖 snippet；golden 主要鎖 type 順序、data-sources 在 text-delta 前、verification 在 finish 前、[DONE] 收尾、標頭。）

- [ ] **Step 2: 失敗測試**

```python
# backend/tests/test_api_chat_sse_unit.py  （若需 DB 改 _db 後綴）
import json
import httpx
from asgi_lifespan import LifespanManager


def _parse_sse(text: str):
    parts = []
    for block in text.strip().split("\n\n"):
        line = block.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        parts.append(payload if payload == "[DONE]" else json.loads(payload))
    return parts


async def test_end_to_end_sse_wire_matches_golden_contract(monkeypatch):
    # 安排 app 用 mock encoder/llm + 假 retrieve/sign/fetch/log（透過 build_chat_deps 注入）
    from anatomy_backend.api.main import app
    # ...（monkeypatch app.state.build_chat_deps 為回傳注入 fakes 的 ChatDeps；
    #     llm 用決定性 MockLLMClient(tokens=["肱二頭肌","起於喙突 [Gray, p.812, Fig.7-23]。"])）
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post("/chat", json={"messages": [
                {"role": "user", "parts": [{"type": "text", "text": "肱二頭肌的構造？"}]}]})
            assert resp.status_code == 200
            assert resp.headers["x-vercel-ai-ui-message-stream"] == "v1"
            assert resp.headers["content-type"].startswith("text/event-stream")
            parts = _parse_sse(resp.text)
    types = [p if p == "[DONE]" else p["type"] for p in parts]
    assert types[0] == "start"
    assert types.index("data-sources") < types.index("text-delta")
    assert types.index("data-verification") < types.index("finish")
    assert types[-1] == "[DONE]"
    # data-sources 內容結構
    src = next(p for p in parts if isinstance(p, dict) and p["type"] == "data-sources")
    assert src["transient"] is True and src["data"]["sources"][0]["page"] == 812
```

- [ ] **Step 3: 讓測試過**（補齊 main.py deps 注入點，使 SSE 端到端跑通；確認 sse-starlette 實際輸出 `data: <json>\n\n`、標頭注入、ping 不干擾）
- [ ] **Step 4: 全套驗證**
  - `uv run pytest backend/tests/ -k api -v`（全部 api_* 綠）
  - `OPENAI_API_KEY="" uv run pytest backend/tests/ -k api -q`（零真實 OpenAI）
  - `uv run pytest backend/tests/ -m "not db and not gpu and not mt" -q`（無回歸）
  - `uv run ruff check backend/src/anatomy_backend/api/ backend/src/anatomy_backend/encoder/ backend/src/anatomy_backend/cache/ backend/tests/test_api_*.py`
- [ ] **Step 5: commit** `test(api): 端到端 SSE wire 對照 golden（sources 先於 delta、verification 先於 finish、標頭、[DONE]）`

---

## Phase 8 必守重點（交 Codex 對抗式審查挑戰）
- **SSE wire**：`data: <compact-json>\n\n`；`delta` 欄位名、`id` 三段共用；`data-sources` 在第一個 `text-delta` 前；標頭 `x-vercel-ai-ui-message-stream: v1`；`data: [DONE]` 收尾；`transient` 引文不入 message.parts。
- **DL-012 連線不跨串流**：檢索 + 圖 fetch 完成（retrieve_fn/ fetch_bytes 內）才進 LLM 串流；DB 連線不得於串流期間持有。
- **DL-021 多輪**：只讀最後兩則 user；追問串接 retrieval_q（不含 assistant）、生成帶前一問、**追問不查/不寫快取**。
- **合規**：送 OpenAI 的 payload 無 user_id（`forbidden_identifiers` 護欄 + Phase 6 fail-closed）；只用標準付費 API；安全網＝引文強制 + 浮水印（前端）+ 回饋，不拒答。
- **限流**：Redis atomic Lua（多 worker 一致、無 INCR-without-EXPIRE race）、429+整數 Retry-After、admin 豁免、Redis 故障 fail-open；高頻拒絕不逐筆寫 DB（DL-022）。
- **disconnect**：generator 內輪詢 `request.is_disconnected()` 提前取消 LLM（省 token）。
- **引文驗證 D-N**：cited page/figure 對照 retrieved/figures[]；未驗證 → `data-verification` 標示（前端 banner）+ 不入快取。
- **CI 零真實 OpenAI**：沿用 Phase 6 conftest 攔截 + mock；端到端用 ASGITransport + mock 注入。

## 延後（非 Phase 8）
- 真 SemanticCache（Phase 7，填 `build_cache`）。LangFuse span / Sentry 脫敏（Phase 9）。前端 useChat/onData/banner（Phase 10）。真 OpenAI smoke（手動，使用者 key）。query intent 真分類器（DL-009 OPEN；本層先 heuristic）。S3 sign/fetch 真實實作細節若 ingest 已有 storage 模式則複用。

## Self-Review（plan 對 spec）
- §5.6 SSE 九步 → D1 ✓；§5.7 schema/PageCitation/build_citations → A1/B2 ✓；§5.8 auth → C1 ✓；§5.9 DL-021 → A1+D1 ✓；§6.4 cache seam → A3（真 impl Phase 7）✓；§6.5 feedback → D2 ✓；§6.8 ratelimit → C2 ✓；DL-009 附圖 → D1（route_images）✓；DL-012 連線+引文驗證 → D1+B2 ✓；DL-018 emitter → B1 ✓；DL-022 限流不寫 DB → C2 ✓。
- Placeholder scan：高風險/契約檔具完整碼；main.py/storage 細節為「擴充既有 + 注入點」說明（實作者依既有 db/pool、ingest storage 模式補；spec-reviewer 驗）。
- Type consistency：`NormalizedChat`、`PageCitation`、`QueryRepr`、`ImageRoutingDecision`、`stream_complete(...forbidden_identifiers=)`、`retrieve(...)`、`ChatDeps`、`RateLimitResult`、`User`、`CachedAnswer` 跨檔一致。

---

## 終審後狀態與已知限制（review 收斂）

實作經 Opus spec/品質雙審 + Codex 終審 + Codex 確認，收斂修正：status DB 合法值（llm_error/cancelled）、golden 接線、feedback 400/UUID、影像逾時降級、encoder/retrieval 串流內失敗→error event+狀態 log（P1c）、非物件 body→400（P2）、追問英文指代詞大小寫不敏感（P2b）、非字串 feedback text→400（P2c）。**83 api 測試綠、全程 mock 零真實 OpenAI、全 unit 無回歸。**

**已知限制（刻意延後，非缺陷）：**
1. **真實 S3/MinIO 取頁圖未接線**：非 mock 模式 `sign_url`/`fetch_bytes` raise 清楚的 `NotImplementedError`。接線需後端**新增 boto3 依賴**（「新套件先問」）+ S3 憑證（`S3_ACCESS_KEY`/`S3_SECRET_KEY`）。Phase 8 為 mock-first、real-encoder 部署前須補（待使用者核可 boto3）。
2. **feedback 目前以 conversation_id 更新**：多輪對話同 conversation 有多列 query_logs，👍/👎 會套用到該會話所有回合。正解需前端（Phase 10）於 SSE/回饋帶**每回合識別碼（message_id / query_log id）**，後端據此精準更新單列。Phase 8 後端先以 conversation 粒度落地，Phase 10 接 message_id。
3. **query intent 仍為 heuristic**（DL-009 OPEN）；真分類器後續。`/warmup` mock 為近 no-op；真實預熱於部署。
