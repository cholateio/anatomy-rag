# Phase 6 — LLM 層（OpenAI 生成 + 模型 fallback + 條件式附圖 + mock）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `backend/src/anatomy_backend/llm/` 建立可獨立於檢索、以 mock 開發測試的 LLM 生成層：原生 `openai` SDK 串流、模型 fallback（per-attempt 計數 + sticky 切換）、版本化 prompt、條件式附圖路由（DL-009）、fail-closed 無 PII payload，全程 mock、CI 零 OpenAI 呼叫（執行期攔截 + grep）。

**Architecture:** 五個聚焦模組——`prompts.py`（版本化 system prompt 常數 + user-message builder，支援 DL-021 追問格式）、`image_routing.py`（表驅動 page_type+intent→送幾張/detail；預設 top-1、硬上限 2）、`client.py`（`build_chat_messages` 純函式 + `assert_no_identifiers` fail-closed PII 邊界 + `LLMClient` 原生 async 串流，`AsyncOpenAI(max_retries=0)`、`max_completion_tokens`、兩道串流防護、不帶 `user=`）、`fallback.py`（`ModelFallbackClient` per-attempt 計數 + sticky 模型切換 + tenacity backoff+jitter）、`mock.py`（決定性串流 + 失敗注入）。`__init__.py` 提供 `build_llm(settings)` 工廠，`settings.llm_mock=True`（預設）→ `MockLLMClient`，CI/本機免 key。真正抓圖 bytes / 引文真實性驗證 / SSE / 半開斷路器恢復探測屬 Phase 8–9，不在本層。

**Tech Stack:** Python 3.11+、`openai` 2.41.0（async、stream=True）、`tenacity`、pytest + pytest-asyncio（asyncio_mode=auto）、ruff（line-length 100）。

---

## 研究結論 + Codex 對抗式審查回應（已整合進設計）

研究（gemini scout，openai 2.x）+ Codex 對抗式審查（2026-06-13，needs-attention：3 high + 3 medium）共同收斂於下列定案。離線驗證（`uv run python` 內省 SDK，零 API 呼叫）：

- **token 上限參數＝`max_completion_tokens`**：openai 2.41.0 `AsyncCompletions.create` **已確認接受** `max_completion_tokens`（與 `max_tokens` 並存，後者對 gpt-5.x 棄用）。集中為常數 `TOKEN_LIMIT_PARAM`。spec §5.5 寫 `max_tokens` 為 v1 起手值，**已技術修正為 `max_completion_tokens`**（自主權範圍內）。
- **`AsyncOpenAI(max_retries=0)`**：必關 SDK 內建重試，否則 tenacity + fallback 計數看不到每次失敗。離線確認 `.max_retries==0` 可讀。
- **串流兩道防護**：`if not chunk.choices: continue`（usage-only chunk）、`if delta.content:`（首尾 chunk content 為 None）。
- **例外階層**（皆繼承 `openai.APIError`）：`APITimeoutError`、`RateLimitError`(429)、`InternalServerError`(5xx)、`APIConnectionError`(網路)。
- **`response_format=json_object`**：SDK 接受且現已相容 streaming，但 spec 規則「不可用」為架構選擇，**仍遵守**，create 不帶 `response_format`。
- **多模態**：chat.completions 仍用 `{"type":"image_url","image_url":{"url":"data:image/png;base64,...","detail":"high"}}`；`detail` high/low/auto 有效。

### Codex 對抗式審查 6 項處置

- **F1 [high] fallback 計數須為 provider 錯誤而非呼叫數** → `consecutive_errors` 改計**每次合格 provider 錯誤嘗試**；tenacity 每次嘗試重選模型。連續 3 次 5xx/429（含單呼叫內多次重試）如實切換。Task 7 含「3 次底層錯誤即切」測試。
- **F2 [high] 競態 + 單成功即回退主模型** → 切換改 **sticky**（`using_fallback` 跨呼叫保持，不因單一成功 token 回退，避免班級突發重擊故障主模型）。**技術澄清**：asyncio 單執行緒、整數遞增間無 await→無 torn write，不加 Lock（YAGNI）。主→備恢復（half-open 探測 + cooldown）**刻意延後 Phase 9** 觀測/健康層（DL-011/§6），本層為 v1 計數器 + sticky，已於 docstring/總結記錄為非靜默缺口。
- **F3 [high] 「無 user_id 參數」非結構性 PII 保證** → 加 `assert_no_identifiers(messages, forbidden)` **fail-closed** 邊界於 `create()` 前；偵測到禁止識別字串即 `raise PIILeakError`（不送、不在訊息印出洩漏內容）。對抗式測試把識別碼嵌入 system 與 user 字串證明會被攔下。不再宣稱「結構上不可能」。
- **F4 [medium] mock-only 無法驗真實 wire 參數** → 已離線驗 `max_completion_tokens` 為 SDK 合法參數；`temperature=0.2` 是否被 gpt-5.x 接受、實際影像 token 計費為**真正 live-only**，依**使用者明確指示**留 Phase 8 smoke（mock-only、零 token 費用為本 phase 既定範圍）。常數集中、一行可改；附 Phase 8 smoke 清單。
- **F5 [medium] CI grep 守門可繞過** → 加 autouse `conftest` fixture，對 `test_llm_*` 測試 monkeypatch `AsyncCompletions.create` 為**拋例外**（任何真實呼叫立即失敗，含經 LLMClient/build_llm 的間接路徑）；grep 守門保留為輔助。
- **F6 [medium] DL-009「預設 top-1」被實作成 top-2** → 拆 `DEFAULT_IMAGE_COUNT=1`（預設）與硬上限 `DL009_MAX_IMAGES=2`；預設送 1 張，`max_images` 夾擠至 2。

## §5.4↔§5.5 訊息結構技術調和（Codex 未列為問題，保留）

§5.4 把【教科書摘錄】/【頁面圖像】/【使用者問題】列在「System Prompt」概念區塊，但 §5.5 程式碼**權威**地把 text_context+user_query 放 **user message**、圖放 user message 的 image parts。故：`SYSTEM_PROMPT_V1` = 靜態行為準則常數（含強制引文格式）；動態摘錄/問題由 `build_user_text()` 組 user message 文字、圖由 `build_chat_messages()` 組 image parts。此為兩節一致化，非設計變更。

## 統一介面契約（跨檔一致，務必對齊）

```python
# Protocol / LLMClient / MockLLMClient / ModelFallbackClient 一律此簽章：
async def stream_complete(
    self, system: str, user: str, images: list[bytes], *,
    image_detail: str = "high",
    forbidden_identifiers: frozenset[str] = frozenset(),
) -> AsyncIterator[str]: ...
```
- `images: list[bytes]` 維持使用者指定的位置參數；`image_detail` / `forbidden_identifiers` 為 keyword-only 含預設（不破壞指定簽章）。
- `forbidden_identifiers`：Phase 8 orchestrator MUST 傳入該請求的 user_id/學號集合；本層在送 OpenAI 前 fail-closed 斷言。

## 檔案結構

| 檔案 | 職責 |
|---|---|
| `backend/src/anatomy_backend/llm/prompts.py` | `SYSTEM_PROMPT_V1` 版本化常數 + registry；`build_user_text(text_context, user_query, prev_query=None)`（DL-021 追問格式） |
| `backend/src/anatomy_backend/llm/image_routing.py` | `QueryIntent`、`ImageRoutingDecision`、`route_images(results, intent, max_images=DEFAULT_IMAGE_COUNT)`（DL-009：預設 1、上限 2） |
| `backend/src/anatomy_backend/llm/client.py` | 常數、`LLMClientProtocol`、`build_chat_messages(...)`、`PIILeakError`、`assert_no_identifiers(...)`、`LLMClient` |
| `backend/src/anatomy_backend/llm/mock.py` | `MockLLMClient`（決定性串流 + 失敗注入；同 Protocol） |
| `backend/src/anatomy_backend/llm/fallback.py` | `ModelFallbackClient`（per-attempt 計數 + sticky 切換 + tenacity）+ 例外集常數 |
| `backend/src/anatomy_backend/llm/__init__.py` | 匯出 + `build_llm(settings)` 工廠 |
| `backend/tests/conftest.py` | **附加** autouse `_block_live_openai` fixture（F5；勿破壞既有內容） |
| `backend/tests/test_llm_prompts_unit.py` | prompts 測試 |
| `backend/tests/test_llm_image_routing_unit.py` | 路由表 + 預設 top-1/上限 2 |
| `backend/tests/test_llm_messages_unit.py` | payload 結構 / image 形狀 / PII fail-closed |
| `backend/tests/test_llm_client_unit.py` | 串流防護 / create kwargs / max_retries=0 / 無 user= / 無 response_format / PII guard 阻擋 create |
| `backend/tests/test_llm_mock_unit.py` | mock 決定性串流 + 失敗注入 |
| `backend/tests/test_llm_fallback_unit.py` | per-attempt 計數 / 單呼叫內切換 / sticky 跨呼叫 / reset / 各例外型別 / APIConnectionError 不計數 / tenacity 重試 |
| `backend/tests/test_llm_factory_unit.py` | `build_llm` mock 旗標 |
| `backend/tests/test_llm_no_live_openai_unit.py` | CI 守門 grep（輔助）+ 預設 llm_mock |

所有測試為 unit（無 db/gpu marker）→ 預設 CI 跑；檔名全域唯一（`test_llm_*`）。

---

### Task 1: llm 套件骨架 + 常數 + Protocol + PII 邊界

**Files:**
- Modify: `backend/src/anatomy_backend/llm/__init__.py`
- Create: `backend/src/anatomy_backend/llm/client.py`（常數 + Protocol + build_chat_messages + PII；`LLMClient` 留 Task 5）

- [ ] **Step 1: 離線確認 SDK 介面（不打 API）**

Run:
```bash
uv run python -c "import inspect; from openai.resources.chat.completions import AsyncCompletions; p=inspect.signature(AsyncCompletions.create).parameters; print('max_completion_tokens', 'max_completion_tokens' in p); print('user', 'user' in p); from openai import AsyncOpenAI; print('max_retries', AsyncOpenAI(api_key='x', max_retries=0).max_retries)"
```
Expected: `max_completion_tokens True` / `user True` / `max_retries 0`。

- [ ] **Step 2: 寫 client.py 常數 + Protocol + build_chat_messages + PII（尚無 LLMClient）**

Create `backend/src/anatomy_backend/llm/client.py`:
```python
"""原生 openai SDK 生成客戶端（§5.3/§5.5）+ fail-closed PII 邊界（§0 合規紅線）。

常數集中於此，方便 Phase 8 smoke 一行校正：
- TOKEN_LIMIT_PARAM：gpt-5.x 用 max_completion_tokens（已離線驗 SDK 接受）。
- DEFAULT_TEMPERATURE：醫學事實型偏低；若 reasoning 模型僅允許 1，Phase 8 smoke 校正。
"""
from __future__ import annotations

import base64
import json
from typing import AsyncIterator, Protocol, runtime_checkable

DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_COMPLETION_TOKENS = 1500
TOKEN_LIMIT_PARAM = "max_completion_tokens"
DEFAULT_IMAGE_DETAIL = "high"


@runtime_checkable
class LLMClientProtocol(Protocol):
    """生成客戶端介面（LLMClient / MockLLMClient / ModelFallbackClient 共用）。"""

    def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]: ...


class PIILeakError(ValueError):
    """送往 OpenAI 的 payload 偵測到禁止識別資訊（user_id/學號）；fail-closed 拒送。"""


def build_chat_messages(
    system: str,
    user: str,
    images: list[bytes],
    *,
    image_detail: str = DEFAULT_IMAGE_DETAIL,
) -> list[dict]:
    """組 chat.completions messages（§5.5）。

    - system role：靜態行為準則（版本化常數）。
    - user role：text part（教科書摘錄 + 使用者問題）＋ 0..N image_url parts。
    """
    content: list[dict] = [{"type": "text", "text": user}]
    for img in images:
        b64 = base64.b64encode(img).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": image_detail},
            }
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def assert_no_identifiers(messages: list[dict], forbidden: frozenset[str]) -> None:
    """fail-closed PII 邊界（§0 / §5.8；Codex F3）。

    偵測 forbidden 中任一非空字串出現在序列化後的 messages → raise PIILeakError（拒送）。
    Phase 8 orchestrator MUST 傳入該請求的 user_id/學號。為避免二次外洩，例外訊息**不**印出
    洩漏內容本身，只報數量。
    """
    if not forbidden:
        return
    blob = json.dumps(messages, ensure_ascii=False)
    leaked = [s for s in forbidden if s and s in blob]
    if leaked:
        raise PIILeakError(
            f"OpenAI payload 含禁止識別資訊（{len(leaked)} 項），fail-closed 拒送"
        )
```

- [ ] **Step 3: 寫 __init__.py 暫時匯出（工廠 Task 8 補）**

Overwrite `backend/src/anatomy_backend/llm/__init__.py`:
```python
"""LLM 生成層（Phase 6）。"""
from anatomy_backend.llm.client import (
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_COMPLETION_TOKENS,
    DEFAULT_TEMPERATURE,
    LLMClientProtocol,
    PIILeakError,
    assert_no_identifiers,
    build_chat_messages,
)

__all__ = [
    "DEFAULT_IMAGE_DETAIL",
    "DEFAULT_MAX_COMPLETION_TOKENS",
    "DEFAULT_TEMPERATURE",
    "LLMClientProtocol",
    "PIILeakError",
    "assert_no_identifiers",
    "build_chat_messages",
]
```

- [ ] **Step 4: 匯入冒煙**

Run: `uv run python -c "from anatomy_backend.llm import build_chat_messages, assert_no_identifiers, PIILeakError; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/llm/client.py backend/src/anatomy_backend/llm/__init__.py
git commit -m "feat(llm): Phase 6 骨架——常數 + Protocol + build_chat_messages + fail-closed assert_no_identifiers"
```

---

### Task 2: prompts.py — 版本化 system prompt + user-message builder（DL-021）

**Files:**
- Create: `backend/src/anatomy_backend/llm/prompts.py`
- Test: `backend/tests/test_llm_prompts_unit.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_llm_prompts_unit.py`:
```python
import pytest

from anatomy_backend.llm import prompts


def test_system_prompt_is_versioned_constant():
    assert prompts.ACTIVE_SYSTEM_PROMPT_VERSION in prompts.SYSTEM_PROMPTS
    active = prompts.get_system_prompt()
    assert active is prompts.SYSTEM_PROMPTS[prompts.ACTIVE_SYSTEM_PROMPT_VERSION]
    assert active == prompts.SYSTEM_PROMPT_V1


def test_system_prompt_enforces_citation_and_no_fabrication():
    p = prompts.get_system_prompt()
    assert "[書名簡寫, 頁碼, 圖號" in p
    assert "教材中查無此項" in p


def test_system_prompt_has_no_refusal_rule():
    p = prompts.get_system_prompt()
    for banned in ("拒答", "請諮詢醫師", "請就醫", "無法回答臨床"):
        assert banned not in p


def test_get_system_prompt_unknown_version_raises():
    with pytest.raises(KeyError):
        prompts.get_system_prompt("v999")


def test_build_user_text_single_turn():
    out = prompts.build_user_text("肱二頭肌起於喙突…", "肱二頭肌起點？")
    assert "【教科書摘錄】" in out
    assert "肱二頭肌起於喙突…" in out
    assert "【使用者問題】" in out
    assert "肱二頭肌起點？" in out
    assert "前一問" not in out


def test_build_user_text_followup_carries_only_prev_question():
    out = prompts.build_user_text(
        text_context="尺神經支配…",
        user_query="那它的神經支配呢？",
        prev_query="肱二頭肌起點？",
    )
    assert "前一問：肱二頭肌起點？" in out
    assert "當前追問：那它的神經支配呢？" in out
    assert "尺神經支配…" in out
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest backend/tests/test_llm_prompts_unit.py -v`
Expected: FAIL（`ModuleNotFoundError` / `AttributeError`）

- [ ] **Step 3: 實作 prompts.py**

Create `backend/src/anatomy_backend/llm/prompts.py`:
```python
"""版本化 system prompt 與 user-message builder（§5.4 / §5.5 / §5.9 DL-021）。

MUST：system prompt 用版本化常數（不可雜在程式碼）。
MUST NOT：加入「拒答臨床問題」硬性規則（使用者皆醫學相關科系學生；安全網＝
引文強制 + 教育用途浮水印 + 回饋，不是拒答）。
"""
from __future__ import annotations

# 行為準則為靜態常數；動態的教科書摘錄/頁面圖像/使用者問題由 build_user_text() 與
# build_chat_messages() 組進 user message（§5.5 權威訊息結構）。
SYSTEM_PROMPT_V1 = """你是一位協助醫學系學生學習解剖學的助教。使用者皆為醫學相關科系學生，具備基本醫學素養。

【行為準則】
1. 僅能基於下方提供的「教科書摘錄」與「教科書頁面圖像」回答。
2. 若提供的資料不足以回答，明確說「教材中查無此項」，不得編造。
3. 每一項事實後面都必須附帶引文，格式為 [書名簡寫, 頁碼, 圖號（若有）]，
   例如：肱二頭肌起於肩胛骨喙突 [Gray42, p.812, Fig.7-23]。
4. 回答風格：簡潔、條列、優先使用教科書原文用語。可包含教科書中的臨床
   correlation（如手術解剖、神經損傷風險、病理機轉），但不主動延伸至診斷
   或治療建議；如使用者明確要求，可在引文範圍內回答。"""

SYSTEM_PROMPTS: dict[str, str] = {"v1": SYSTEM_PROMPT_V1}
ACTIVE_SYSTEM_PROMPT_VERSION = "v1"


def get_system_prompt(version: str | None = None) -> str:
    """取版本化 system prompt；version=None → active 版本。未知版本 → KeyError。"""
    return SYSTEM_PROMPTS[version or ACTIVE_SYSTEM_PROMPT_VERSION]


def build_user_text(
    text_context: str,
    user_query: str,
    prev_query: str | None = None,
) -> str:
    """組 user message 文字（§5.5）。

    追問（DL-021 §5.9）：prev_query 不為 None 時，【使用者問題】帶「前一問／當前追問」，
    **MUST NOT** 帶歷史回答或先前檢索內容（本函式無此參數，結構上不可能帶入）。
    text_context 為**本回合**檢索摘錄，非先前回合內容。
    """
    if prev_query is not None:
        question_block = f"前一問：{prev_query}\n當前追問：{user_query}"
    else:
        question_block = user_query
    return f"【教科書摘錄】\n{text_context}\n\n【使用者問題】\n{question_block}"
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest backend/tests/test_llm_prompts_unit.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/llm/prompts.py backend/tests/test_llm_prompts_unit.py
git commit -m "feat(llm): prompts.py——版本化 system prompt + DL-021 追問 user-message builder"
```

---

### Task 3: image_routing.py — 表驅動條件式附圖（DL-009：預設 top-1、上限 2）

**Files:**
- Create: `backend/src/anatomy_backend/llm/image_routing.py`
- Test: `backend/tests/test_llm_image_routing_unit.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_llm_image_routing_unit.py`:
```python
from uuid import uuid4

from anatomy_backend.llm.image_routing import (
    DEFAULT_IMAGE_COUNT,
    DL009_MAX_IMAGES,
    QueryIntent,
    route_images,
)
from anatomy_backend.retrieval.types import RetrievalResult


def _r(page_type: str, page_num: int = 1) -> RetrievalResult:
    return RetrievalResult(
        page_id=uuid4(),
        score=1.0,
        book_title="Gray",
        edition="42",
        page_num=page_num,
        page_image_uri="s3://x",
        docling_md="…",
        metadata={"page_type": page_type, "figures": ["Fig.7-23"]},
    )


def test_pure_text_intent_sends_zero_images():
    results = [_r("figure_heavy"), _r("mixed")]
    decision = route_images(results, QueryIntent.PURE_TEXT)
    assert decision.indices == ()


def test_figure_intent_default_is_top_one_high_detail():
    # DL-009：預設 top-1（即使有多張 figure_heavy）
    results = [_r("figure_heavy"), _r("mixed"), _r("figure_heavy")]
    decision = route_images(results, QueryIntent.FIGURE)
    assert decision.indices == (0,)
    assert decision.detail == "high"
    assert DEFAULT_IMAGE_COUNT == 1


def test_figure_intent_explicit_two_capped_at_max():
    results = [_r("figure_heavy"), _r("mixed"), _r("figure_heavy")]
    decision = route_images(results, QueryIntent.FIGURE, max_images=2)
    assert decision.indices == (0, 1)  # 依 RRF 既有順序


def test_max_images_clamped_to_hard_cap():
    results = [_r("figure_heavy"), _r("mixed"), _r("figure_heavy"), _r("mixed")]
    decision = route_images(results, QueryIntent.FIGURE, max_images=99)
    assert len(decision.indices) == DL009_MAX_IMAGES == 2


def test_figure_intent_skips_pure_text_and_table_pages():
    results = [_r("pure_text"), _r("table"), _r("figure_heavy")]
    decision = route_images(results, QueryIntent.FIGURE, max_images=2)
    assert decision.indices == (2,)


def test_figure_intent_no_eligible_pages_sends_zero():
    results = [_r("pure_text"), _r("table")]
    decision = route_images(results, QueryIntent.FIGURE)
    assert decision.indices == ()


def test_missing_page_type_metadata_treated_as_non_figure():
    r = RetrievalResult(uuid4(), 1.0, "Gray", "42", 1, "s3://x", "…", metadata={})
    decision = route_images([r], QueryIntent.FIGURE)
    assert decision.indices == ()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest backend/tests/test_llm_image_routing_unit.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 image_routing.py**

Create `backend/src/anatomy_backend/llm/image_routing.py`:
```python
"""條件式附圖路由（DL-009 / §5.5）。

表驅動：query intent + page_type → 送幾張圖、detail。Phase 6 只決定「送幾張/哪張/
detail」；真正 fetch 圖 bytes 在 Phase 8 orchestrator。intent 由上游分類器決定
（Phase 8，OPEN），本層僅消費。

DL-009：純文字題 0 圖；圖譜題只對 figure_heavy/mixed 頁送圖、**預設 top-1、硬上限 2**。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anatomy_backend.retrieval.types import RetrievalResult

_IMAGE_PAGE_TYPES = frozenset({"figure_heavy", "mixed"})
DEFAULT_IMAGE_COUNT = 1   # DL-009 預設 top-1
DL009_MAX_IMAGES = 2      # DL-009 硬上限（非固定 3）


class QueryIntent(str, Enum):
    PURE_TEXT = "pure_text"  # 純文字/概念題 → 不送圖（最大成本槓桿）
    FIGURE = "figure"        # 圖譜/判讀題 → 條件式送圖


@dataclass(frozen=True)
class ImageRoutingDecision:
    """indices：要附圖的 results 索引（RRF 既有順序）；空＝不送圖。
    detail：附圖時的 OpenAI detail（需判讀標籤 → high）。"""

    indices: tuple[int, ...]
    detail: str = "high"


def route_images(
    results: list[RetrievalResult],
    intent: QueryIntent,
    max_images: int = DEFAULT_IMAGE_COUNT,
) -> ImageRoutingDecision:
    """依 intent + page_type 路由。純文字題 0 圖；圖譜題取前 N 個 figure_heavy/mixed 頁
    （N = min(max_images, DL009_MAX_IMAGES)）、detail=high。"""
    if intent == QueryIntent.PURE_TEXT:
        return ImageRoutingDecision(indices=())
    cap = min(max_images, DL009_MAX_IMAGES)
    indices: list[int] = []
    for i, r in enumerate(results):
        if r.metadata.get("page_type") in _IMAGE_PAGE_TYPES:
            indices.append(i)
            if len(indices) >= cap:
                break
    return ImageRoutingDecision(indices=tuple(indices), detail="high")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest backend/tests/test_llm_image_routing_unit.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/llm/image_routing.py backend/tests/test_llm_image_routing_unit.py
git commit -m "feat(llm): image_routing.py——DL-009 表驅動（pure_text→0、figure→預設 top-1/硬上限 2 high）"
```

---

### Task 4: build_chat_messages + PII 邊界測試

**Files:**
- Test: `backend/tests/test_llm_messages_unit.py`（`build_chat_messages`/`assert_no_identifiers` 已於 Task 1 實作）

- [ ] **Step 1: 寫測試**

Create `backend/tests/test_llm_messages_unit.py`:
```python
import base64
import inspect

import pytest

from anatomy_backend.llm import (
    PIILeakError,
    assert_no_identifiers,
    build_chat_messages,
)


def test_text_only_message_has_no_image_parts():
    msgs = build_chat_messages("SYS", "USER 問題", images=[])
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == [{"type": "text", "text": "USER 問題"}]


def test_image_part_shape_and_detail():
    msgs = build_chat_messages("SYS", "U", images=[b"\x89PNG_fake"], image_detail="high")
    img = msgs[1]["content"][1]
    assert img["type"] == "image_url"
    assert img["image_url"]["detail"] == "high"
    assert img["image_url"]["url"].startswith("data:image/png;base64,")
    b64 = img["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(b64) == b"\x89PNG_fake"


def test_multiple_images_preserve_order_and_detail():
    msgs = build_chat_messages("S", "U", images=[b"a", b"b"], image_detail="low")
    parts = msgs[1]["content"]
    assert len(parts) == 3
    assert all(p["image_url"]["detail"] == "low" for p in parts[1:])


def test_build_chat_messages_has_no_user_id_param():
    # 結構性防線（非唯一防線）：簽章無 user_id
    assert "user_id" not in inspect.signature(build_chat_messages).parameters


def test_assert_no_identifiers_passes_for_clean_payload():
    msgs = build_chat_messages("系統提示", "肱二頭肌起點？", images=[b"img"])
    # 無 forbidden → 直接通過
    assert_no_identifiers(msgs, frozenset())
    # 有 forbidden 但未出現 → 通過
    assert_no_identifiers(msgs, frozenset({"00000000-0000-0000-0000-000000000001"}))


@pytest.mark.parametrize("field", ["system", "user"])
def test_assert_no_identifiers_fail_closed_when_id_embedded(field):
    # Codex F3：識別碼若被誤嵌入 system 或 user 字串，fail-closed 攔下
    uid = "stud-2026-00042"
    system = f"系統提示 {uid}" if field == "system" else "系統提示"
    user = f"肱二頭肌起點？ {uid}" if field == "user" else "肱二頭肌起點？"
    msgs = build_chat_messages(system, user, images=[b"img"])
    with pytest.raises(PIILeakError):
        assert_no_identifiers(msgs, frozenset({uid}))


def test_pii_error_message_does_not_echo_identifier():
    uid = "stud-secret-999"
    msgs = build_chat_messages("S", f"q {uid}", images=[])
    with pytest.raises(PIILeakError) as ei:
        assert_no_identifiers(msgs, frozenset({uid}))
    assert uid not in str(ei.value)  # 不二次外洩
```

- [ ] **Step 2: 跑測試確認通過（Task 1 已實作）**

Run: `uv run pytest backend/tests/test_llm_messages_unit.py -v`
Expected: PASS（含 parametrize 共 8 passed）

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_llm_messages_unit.py
git commit -m "test(llm): build_chat_messages 形狀 + assert_no_identifiers fail-closed PII 邊界"
```

---

### Task 5: LLMClient — async 串流 + create kwargs + max_retries=0 + PII guard

**Files:**
- Modify: `backend/src/anatomy_backend/llm/client.py`（加 `LLMClient`）
- Test: `backend/tests/test_llm_client_unit.py`

- [ ] **Step 1: 寫失敗測試（注入 fake AsyncOpenAI，零 API 呼叫）**

Create `backend/tests/test_llm_client_unit.py`:
```python
from types import SimpleNamespace

import pytest

from anatomy_backend.llm import PIILeakError
from anatomy_backend.llm.client import (
    DEFAULT_MAX_COMPLETION_TOKENS,
    LLMClient,
    TOKEN_LIMIT_PARAM,
)


def _chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def _usage_chunk():
    return SimpleNamespace(choices=[])


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for c in self._chunks:
            yield c


class _FakeCompletions:
    def __init__(self, chunks, rec):
        self._chunks = chunks
        self._rec = rec

    async def create(self, **kwargs):
        self._rec["kwargs"] = kwargs
        self._rec["create_calls"] = self._rec.get("create_calls", 0) + 1
        return _FakeStream(self._chunks)


class _FakeClient:
    def __init__(self, chunks, rec):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks, rec))


async def test_stream_yields_tokens_and_skips_none_and_usage_chunks():
    chunks = [_chunk(None), _chunk("肱"), _chunk("二頭肌"), _usage_chunk(), _chunk(None)]
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient(chunks, rec))
    out = [t async for t in client.stream_complete("SYS", "U", images=[])]
    assert out == ["肱", "二頭肌"]


async def test_create_kwargs_match_spec():
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient([_chunk("x")], rec))
    _ = [t async for t in client.stream_complete("SYS", "U", images=[])]
    kw = rec["kwargs"]
    assert kw["model"] == "gpt-5.5"
    assert kw["stream"] is True
    assert kw["temperature"] == 0.2
    assert kw[TOKEN_LIMIT_PARAM] == DEFAULT_MAX_COMPLETION_TOKENS
    assert "response_format" not in kw  # 不可用 json_object
    assert "user" not in kw             # 不送 user_id


async def test_real_client_constructed_with_max_retries_zero():
    client = LLMClient("gpt-5.5", api_key="sk-test-dummy")
    assert client._client.max_retries == 0


async def test_images_passed_as_image_url_parts():
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient([_chunk("x")], rec))
    _ = [t async for t in client.stream_complete("S", "U", images=[b"png"], image_detail="high")]
    user_parts = rec["kwargs"]["messages"][1]["content"]
    assert user_parts[1]["image_url"]["detail"] == "high"


async def test_pii_guard_blocks_create_when_identifier_present():
    # Codex F3：識別碼嵌入 user 字串 → create() 不得被呼叫
    rec = {}
    client = LLMClient("gpt-5.5", client=_FakeClient([_chunk("x")], rec))
    uid = "stud-2026-00042"
    with pytest.raises(PIILeakError):
        _ = [
            t
            async for t in client.stream_complete(
                "S", f"問題 {uid}", images=[], forbidden_identifiers=frozenset({uid})
            )
        ]
    assert rec.get("create_calls", 0) == 0  # 送出前即攔下
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest backend/tests/test_llm_client_unit.py -v`
Expected: FAIL（`ImportError: cannot import name 'LLMClient'`）

- [ ] **Step 3: 在 client.py 加 LLMClient**

Append to `backend/src/anatomy_backend/llm/client.py`:
```python
from openai import AsyncOpenAI  # noqa: E402  （置檔尾與常數/純函式分區）


class LLMClient:
    """單一模型的原生 openai async 串流客戶端（§5.5）。

    max_retries=0：關閉 SDK 內建重試，讓 tenacity + ModelFallbackClient 計數看到每次失敗。
    可注入 client 供測試（零 API 呼叫）。送 OpenAI 前先 assert_no_identifiers fail-closed。
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str = "",
        base_url: str | None = None,
        client: AsyncOpenAI | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_completion_tokens = max_completion_tokens
        self._client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=0
        )

    async def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]:
        messages = build_chat_messages(system, user, images, image_detail=image_detail)
        assert_no_identifiers(messages, forbidden_identifiers)  # fail-closed，送出前
        kwargs = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "temperature": self._temperature,
            TOKEN_LIMIT_PARAM: self._max_completion_tokens,
        }
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:  # usage-only chunk
                continue
            delta = chunk.choices[0].delta
            if delta.content:  # 首尾 chunk content 為 None
                yield delta.content
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest backend/tests/test_llm_client_unit.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/llm/client.py backend/tests/test_llm_client_unit.py
git commit -m "feat(llm): LLMClient——async 串流（兩道防護）+ max_retries=0 + max_completion_tokens + fail-closed PII"
```

---

### Task 6: mock.py — 決定性串流 + 失敗注入

**Files:**
- Create: `backend/src/anatomy_backend/llm/mock.py`
- Test: `backend/tests/test_llm_mock_unit.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_llm_mock_unit.py`:
```python
import httpx
import pytest
from openai import APITimeoutError

from anatomy_backend.llm.mock import MockLLMClient


async def test_deterministic_token_stream():
    m = MockLLMClient(tokens=["肱二頭肌", "起於", "喙突"])
    out1 = [t async for t in m.stream_complete("S", "U", images=[])]
    out2 = [t async for t in m.stream_complete("S", "U", images=[])]
    assert out1 == ["肱二頭肌", "起於", "喙突"]
    assert out1 == out2


async def test_default_tokens_non_empty():
    m = MockLLMClient()
    out = [t async for t in m.stream_complete("S", "U", images=[])]
    assert out


async def test_records_calls_including_forbidden_identifiers():
    m = MockLLMClient(tokens=["x"])
    _ = [
        t
        async for t in m.stream_complete(
            "SYS", "USER", images=[b"img"], image_detail="low",
            forbidden_identifiers=frozenset({"uid"}),
        )
    ]
    assert m.invocations == 1
    assert m.calls[0].system == "SYS"
    assert m.calls[0].user == "USER"
    assert m.calls[0].image_detail == "low"
    assert m.calls[0].forbidden_identifiers == frozenset({"uid"})


async def test_failure_injection_raises_then_succeeds():
    exc = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    m = MockLLMClient(tokens=["ok"], error=exc, fail_first=2)
    for _ in range(2):
        with pytest.raises(APITimeoutError):
            _ = [t async for t in m.stream_complete("S", "U", images=[])]
    out = [t async for t in m.stream_complete("S", "U", images=[])]
    assert out == ["ok"]
    assert m.invocations == 3
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest backend/tests/test_llm_mock_unit.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 mock.py**

Create `backend/src/anatomy_backend/llm/mock.py`:
```python
"""決定性 mock LLM 客戶端（供測試 + make up；零 OpenAI 呼叫、零 token 費用）。

同 LLMClientProtocol。支援失敗注入（fail_first 次建立期拋 error），給 fallback 計數測試。
forbidden_identifiers 僅記錄（mock 不送任何網路，無洩漏風險）。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncIterator

from anatomy_backend.llm.client import DEFAULT_IMAGE_DETAIL

_DEFAULT_MOCK_TOKENS: tuple[str, ...] = (
    "肱二頭肌",
    "起於肩胛骨喙突",
    " [Gray42, p.812, Fig.7-23]。\n\n",
    "（教育用途，內容基於教科書）",
)


class MockLLMClient:
    def __init__(
        self,
        *,
        tokens: list[str] | None = None,
        error: Exception | None = None,
        fail_first: int = 0,
        name: str = "mock",
    ) -> None:
        self.tokens = list(tokens) if tokens is not None else list(_DEFAULT_MOCK_TOKENS)
        self.error = error
        self.fail_first = fail_first
        self.name = name
        self.invocations = 0
        self.calls: list[SimpleNamespace] = []

    async def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]:
        self.invocations += 1
        self.calls.append(
            SimpleNamespace(
                system=system,
                user=user,
                images=list(images),
                image_detail=image_detail,
                forbidden_identifiers=forbidden_identifiers,
            )
        )
        if self.error is not None and self.invocations <= self.fail_first:
            raise self.error  # 建立期失敗（首個 __anext__ 觸發）
        for tok in self.tokens:
            yield tok
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest backend/tests/test_llm_mock_unit.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/llm/mock.py backend/tests/test_llm_mock_unit.py
git commit -m "feat(llm): mock.py——決定性串流 + 失敗注入（測試/ make up；零 OpenAI 呼叫）"
```

---

### Task 7: fallback.py — per-attempt 計數 + sticky 切換 + tenacity backoff（Codex F1/F2）

**Files:**
- Create: `backend/src/anatomy_backend/llm/fallback.py`
- Test: `backend/tests/test_llm_fallback_unit.py`

- [ ] **Step 1: 確認 openai 例外建構子（測試要造實例）**

Run:
```bash
uv run python -c "import httpx; from openai import RateLimitError, InternalServerError, APITimeoutError, APIConnectionError; req=httpx.Request('POST','https://api.openai.com/v1/chat/completions'); print(type(RateLimitError('x',response=httpx.Response(429,request=req),body=None))); print(type(InternalServerError('x',response=httpx.Response(500,request=req),body=None))); print(type(APITimeoutError(request=req))); print(type(APIConnectionError(request=req)))"
```
Expected: 印出四型別且無例外。若簽章不同，依實際調整測試 helper（PR 註明）。

- [ ] **Step 2: 寫失敗測試**

Create `backend/tests/test_llm_fallback_unit.py`:
```python
import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import wait_none

from anatomy_backend.llm.fallback import ModelFallbackClient
from anatomy_backend.llm.mock import MockLLMClient

_REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _timeout():
    return APITimeoutError(request=_REQ)


def _rate():
    return RateLimitError("rate", response=httpx.Response(429, request=_REQ), body=None)


def _server():
    return InternalServerError("srv", response=httpx.Response(500, request=_REQ), body=None)


def _conn():
    return APIConnectionError(request=_REQ)


def _mfc(primary, fallback, **kw):
    kw.setdefault("max_attempts", 1)   # 預設單次嘗試（多數呼叫級測試）
    kw.setdefault("wait", wait_none())  # 不真睡
    return ModelFallbackClient(primary, fallback, **kw)


async def _drain(client):
    return [t async for t in client.stream_complete("S", "U", images=[])]


async def test_three_provider_errors_within_one_call_switch_to_fallback():
    # Codex F1：單次呼叫內 3 次底層 5xx/429 即切備援（max_attempts 容納重試）
    primary = MockLLMClient(error=_server(), fail_first=99)
    fallback = MockLLMClient(tokens=["備援回答"])
    mfc = ModelFallbackClient(
        primary, fallback, switch_threshold=3, max_attempts=4, wait=wait_none()
    )
    out = await _drain(mfc)
    assert out == ["備援回答"]
    assert primary.invocations == 3      # 3 次錯誤
    assert fallback.invocations == 1     # 第 4 次嘗試切備援
    assert mfc.using_fallback is True
    assert mfc.consecutive_errors == 0   # 成功歸零


async def test_call_level_counting_across_calls_then_switch():
    primary = MockLLMClient(error=_timeout(), fail_first=99)
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback, switch_threshold=3)  # max_attempts=1：每呼叫一次嘗試
    for _ in range(3):
        with pytest.raises(APITimeoutError):
            await _drain(mfc)
    assert mfc.consecutive_errors == 3
    assert mfc.using_fallback is True
    assert primary.invocations == 3
    # 第 4 次呼叫：sticky → 用備援
    out = await _drain(mfc)
    assert out == ["備援"]
    assert fallback.invocations == 1


async def test_sticky_switch_persists_after_fallback_success():
    # Codex F2：切換後不因單一成功回退主模型
    primary = MockLLMClient(error=_timeout(), fail_first=99)
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback, switch_threshold=3)
    for _ in range(3):
        with pytest.raises(APITimeoutError):
            await _drain(mfc)
    assert mfc.using_fallback is True
    await _drain(mfc)  # 備援成功
    await _drain(mfc)  # 仍應走備援
    assert fallback.invocations == 2
    assert primary.invocations == 3  # 切換後主模型不再被呼叫
    assert mfc.using_fallback is True


async def test_success_before_threshold_resets_counter():
    primary = MockLLMClient(error=_server(), fail_first=2, tokens=["主回答"])
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback, switch_threshold=3)
    for _ in range(2):
        with pytest.raises(InternalServerError):
            await _drain(mfc)
    assert mfc.consecutive_errors == 2
    out = await _drain(mfc)  # 第三次主模型成功
    assert out == ["主回答"]
    assert mfc.consecutive_errors == 0
    assert mfc.using_fallback is False
    assert fallback.invocations == 0


@pytest.mark.parametrize("make_exc", [_timeout, _rate, _server])
async def test_each_trigger_type_increments_counter(make_exc):
    primary = MockLLMClient(error=make_exc(), fail_first=99)
    fallback = MockLLMClient(tokens=["x"])
    mfc = _mfc(primary, fallback)
    with pytest.raises(type(make_exc())):
        await _drain(mfc)
    assert mfc.consecutive_errors == 1


async def test_connection_error_retried_but_not_counted():
    # APIConnectionError 僅重試、不計入切換（主備同 vendor/endpoint）
    primary = MockLLMClient(error=_conn(), fail_first=99)
    fallback = MockLLMClient(tokens=["x"])
    mfc = _mfc(primary, fallback)
    with pytest.raises(APIConnectionError):
        await _drain(mfc)
    assert mfc.consecutive_errors == 0
    assert mfc.using_fallback is False


async def test_tenacity_retries_transient_then_succeeds_no_count():
    primary = MockLLMClient(error=_timeout(), fail_first=2, tokens=["成功"])
    fallback = MockLLMClient(tokens=["備援"])
    mfc = ModelFallbackClient(primary, fallback, max_attempts=3, wait=wait_none())
    out = await _drain(mfc)
    assert out == ["成功"]
    assert primary.invocations == 3       # 2 失敗 + 1 成功（同一呼叫內 tenacity 重試）
    assert mfc.consecutive_errors == 0
    assert fallback.invocations == 0


async def test_mid_stream_error_after_first_token_not_counted():
    # 建立成功（已吐 token）後中途斷：傳播、不計數
    class _MidFail:
        def __init__(self):
            self.invocations = 0

        async def stream_complete(self, system, user, images, *, image_detail="high",
                                  forbidden_identifiers=frozenset()):
            self.invocations += 1
            yield "第一段"
            raise _server()

    primary = _MidFail()
    fallback = MockLLMClient(tokens=["備援"])
    mfc = _mfc(primary, fallback)
    with pytest.raises(InternalServerError):
        await _drain(mfc)
    assert mfc.consecutive_errors == 0  # 已建立成功 → 不計建立期失敗
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `uv run pytest backend/tests/test_llm_fallback_unit.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 4: 實作 fallback.py**

Create `backend/src/anatomy_backend/llm/fallback.py`:
```python
"""模型 fallback（§5.3 / §6.1）——per-attempt 計數 + sticky 切換 + tenacity backoff+jitter。

語意（修正 Codex 對抗式審查 F1/F2）：
- consecutive_errors 計**每次合格 provider 錯誤嘗試**（非每次呼叫），連續達 threshold 即切。
  「連續 3 次 5xx/429」如實觸發（含單呼叫內 tenacity 重試所產生的多次錯誤）。
- 切換為 **sticky**：using_fallback 一旦為真即跨呼叫保持，不因單一成功 token 立即回退主模型
  （避免班級突發下反覆重擊故障主模型）。主→備恢復（half-open 探測 + cooldown）**延後
  Phase 9** 觀測/健康層（DL-011/§6）；本層為刻意最小範圍（v1 計數器 + sticky），非靜默缺口。
- 併發：asyncio 單執行緒、整數遞增間無 await → 無 torn write，不需 Lock（YAGNI）。
- APIConnectionError 僅 tenacity 重試、**不計入**切換（主備同 vendor/endpoint，切模型無益）。
- 建立成功後串流中途斷：傳播、不重試（避免重複 token）、不計數。
"""
from __future__ import annotations

from typing import AsyncIterator

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from anatomy_backend.llm.client import DEFAULT_IMAGE_DETAIL, LLMClientProtocol

# tenacity 重試集（含連線錯誤）
RETRYABLE_EXC: tuple[type[Exception], ...] = (
    APITimeoutError,
    RateLimitError,
    InternalServerError,
    APIConnectionError,
)
# 計入模型切換的觸發集（spec：Timeout/RateLimit/Server）
FALLBACK_TRIGGER_EXC: tuple[type[Exception], ...] = (
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

DEFAULT_SWITCH_THRESHOLD = 3
DEFAULT_MAX_ATTEMPTS = 3

_EMPTY = object()


def _default_wait():
    return wait_random_exponential(min=1, max=30)  # exponential backoff + jitter


class ModelFallbackClient:
    def __init__(
        self,
        primary: LLMClientProtocol,
        fallback: LLMClientProtocol,
        *,
        switch_threshold: int = DEFAULT_SWITCH_THRESHOLD,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        wait=None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._switch_threshold = switch_threshold
        self._max_attempts = max_attempts
        self._wait = wait if wait is not None else _default_wait()
        self.consecutive_errors = 0
        self.using_fallback = False  # sticky，跨呼叫保持

    def _active(self) -> LLMClientProtocol:
        return self._fallback if self.using_fallback else self._primary

    async def stream_complete(
        self,
        system: str,
        user: str,
        images: list[bytes],
        *,
        image_detail: str = DEFAULT_IMAGE_DETAIL,
        forbidden_identifiers: frozenset[str] = frozenset(),
    ) -> AsyncIterator[str]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(RETRYABLE_EXC),
            reraise=True,
        ):
            agen = None
            with attempt:
                client = self._active()  # 每次嘗試重選模型（達 threshold 後改備援）
                agen = client.stream_complete(
                    system,
                    user,
                    images,
                    image_detail=image_detail,
                    forbidden_identifiers=forbidden_identifiers,
                )
                try:
                    first = await agen.__anext__()
                except StopAsyncIteration:
                    first = _EMPTY
                except FALLBACK_TRIGGER_EXC:
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= self._switch_threshold:
                        self.using_fallback = True
                    raise  # 交 tenacity 重試 + backoff（APIConnectionError 不在此，直接交 tenacity）
            # 建立成功（with attempt 正常結束）
            self.consecutive_errors = 0
            if first is _EMPTY:
                return
            yield first
            async for tok in agen:  # 中途斷會傳播、不重試、不計數
                yield tok
            return
```

- [ ] **Step 5: 跑測試確認通過**

Run: `uv run pytest backend/tests/test_llm_fallback_unit.py -v`
Expected: PASS（含 parametrize 共 10 passed）

- [ ] **Step 6: Commit**

```bash
git add backend/src/anatomy_backend/llm/fallback.py backend/tests/test_llm_fallback_unit.py
git commit -m "feat(llm): fallback.py——per-attempt 計數 + sticky 切換 gpt-5.5→5.4 + tenacity backoff（Codex F1/F2）"
```

---

### Task 8: __init__.py 工廠 build_llm + 匯出收尾

**Files:**
- Modify: `backend/src/anatomy_backend/llm/__init__.py`
- Test: `backend/tests/test_llm_factory_unit.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_llm_factory_unit.py`:
```python
from types import SimpleNamespace

from anatomy_backend.llm import build_llm
from anatomy_backend.llm.fallback import ModelFallbackClient
from anatomy_backend.llm.mock import MockLLMClient


def _settings(**over):
    base = dict(
        llm_mock=True,
        openai_api_key="sk-dummy",
        openai_model_primary="gpt-5.5",
        openai_model_fallback="gpt-5.4",
        openai_base_url="https://api.openai.com/v1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_build_llm_returns_mock_when_llm_mock_true():
    assert isinstance(build_llm(_settings(llm_mock=True)), MockLLMClient)


def test_build_llm_returns_fallback_when_llm_mock_false():
    # 僅建構（AsyncOpenAI 離線建立），不打 API
    assert isinstance(build_llm(_settings(llm_mock=False)), ModelFallbackClient)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest backend/tests/test_llm_factory_unit.py -v`
Expected: FAIL（`ImportError: cannot import name 'build_llm'`）

- [ ] **Step 3: 更新 __init__.py（加工廠 + 完整匯出）**

Overwrite `backend/src/anatomy_backend/llm/__init__.py`:
```python
"""LLM 生成層（Phase 6）。

build_llm(settings)：settings.llm_mock=True（預設）→ MockLLMClient（零 OpenAI 呼叫）；
否則 → ModelFallbackClient（gpt-5.5 主 / gpt-5.4 備）。Phase 8 orchestrator 由此取客戶端。
settings 為 duck-typed（只讀 llm_mock / openai_* 屬性）。
"""
from anatomy_backend.llm.client import (
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_COMPLETION_TOKENS,
    DEFAULT_TEMPERATURE,
    LLMClient,
    LLMClientProtocol,
    PIILeakError,
    assert_no_identifiers,
    build_chat_messages,
)
from anatomy_backend.llm.fallback import ModelFallbackClient
from anatomy_backend.llm.image_routing import (
    DEFAULT_IMAGE_COUNT,
    DL009_MAX_IMAGES,
    ImageRoutingDecision,
    QueryIntent,
    route_images,
)
from anatomy_backend.llm.mock import MockLLMClient
from anatomy_backend.llm.prompts import (
    ACTIVE_SYSTEM_PROMPT_VERSION,
    SYSTEM_PROMPTS,
    build_user_text,
    get_system_prompt,
)


def build_llm(settings) -> LLMClientProtocol:
    if getattr(settings, "llm_mock", True):
        return MockLLMClient()
    primary = LLMClient(
        settings.openai_model_primary,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    fallback = LLMClient(
        settings.openai_model_fallback,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    return ModelFallbackClient(primary, fallback)


__all__ = [
    "ACTIVE_SYSTEM_PROMPT_VERSION",
    "DEFAULT_IMAGE_COUNT",
    "DEFAULT_IMAGE_DETAIL",
    "DEFAULT_MAX_COMPLETION_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DL009_MAX_IMAGES",
    "ImageRoutingDecision",
    "LLMClient",
    "LLMClientProtocol",
    "MockLLMClient",
    "ModelFallbackClient",
    "PIILeakError",
    "QueryIntent",
    "SYSTEM_PROMPTS",
    "assert_no_identifiers",
    "build_chat_messages",
    "build_llm",
    "build_user_text",
    "get_system_prompt",
    "route_images",
]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest backend/tests/test_llm_factory_unit.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/src/anatomy_backend/llm/__init__.py backend/tests/test_llm_factory_unit.py
git commit -m "feat(llm): build_llm 工廠（llm_mock→Mock / 否則 ModelFallbackClient）+ 套件匯出"
```

---

### Task 9: CI 零 API 守門（執行期攔截 + grep）+ 全層整合驗收（Codex F5）

**Files:**
- Modify: `backend/tests/conftest.py`（**附加** fixture，先 Read 既有內容再 Edit，勿覆蓋）
- Create: `backend/tests/test_llm_no_live_openai_unit.py`

- [ ] **Step 1: 先讀既有 conftest，於其後附加 autouse 攔截 fixture**

先 `Read backend/tests/conftest.py`，在檔尾附加（若無 `import pytest` 則加上）：
```python
import pytest as _pytest  # noqa: E402  （若檔案已 import pytest，沿用既有名稱即可）


@_pytest.fixture(autouse=True)
def _block_live_openai(request, monkeypatch):
    """Codex F5：任何 test_llm_* 測試若真打 OpenAI（含經 LLMClient/build_llm 的間接路徑），
    立即失敗。llm 測試一律用 MockLLMClient 或注入 fake。"""
    if not request.node.fspath.basename.startswith("test_llm_"):
        return
    try:
        from openai.resources.chat.completions import AsyncCompletions
    except Exception:
        return

    async def _boom(*a, **k):
        raise RuntimeError("LLM 測試禁止真打 OpenAI（請用 MockLLMClient 或注入 fake client）")

    monkeypatch.setattr(AsyncCompletions, "create", _boom)
```
> 註：若既有 conftest 已 `import pytest`，直接用 `@pytest.fixture` 即可，不要重複 import。

- [ ] **Step 2: 寫 grep 守門測試（輔助層）**

Create `backend/tests/test_llm_no_live_openai_unit.py`:
```python
"""CI 守門（輔助）：llm 測試不得建構真 OpenAI 客戶端字樣（執行期攔截見 conftest）。"""
from pathlib import Path


def test_llm_tests_never_construct_live_openai_client():
    here = Path(__file__).resolve().parent
    me = Path(__file__).name
    offenders: list[str] = []
    for f in sorted(here.glob("test_llm_*.py")):
        if f.name == me:
            continue
        src = f.read_text(encoding="utf-8")
        for needle in ("AsyncOpenAI(", "OpenAI("):
            if needle in src:
                offenders.append(f"{f.name}: 含 '{needle}'（請改用 Mock/注入 fake）")
    assert not offenders, "LLM 測試不得建構真 OpenAI 客戶端：\n" + "\n".join(offenders)


def test_default_settings_llm_mock_is_true():
    from anatomy_backend.config import Settings

    assert Settings.model_fields["llm_mock"].default is True
```

- [ ] **Step 3: 跑守門測試**

Run: `uv run pytest backend/tests/test_llm_no_live_openai_unit.py -v`
Expected: PASS（2 passed）

- [ ] **Step 4: 跑全 llm 測試套件**

Run: `uv run pytest backend/tests/ -k llm -v`
Expected: PASS（全部 llm_* 測試綠燈）

- [ ] **Step 5: 確認既有測試未被 conftest 改動破壞**

Run: `uv run pytest backend/tests/ -m "not db and not gpu and not mt" -q`
Expected: PASS（Phase 0–5 既有 unit 測試仍綠；conftest fixture 只對 test_llm_* 生效）

- [ ] **Step 6: ruff 檢查（lint only，勿跑 format）**

Run: `uv run ruff check backend/src/anatomy_backend/llm/ backend/tests/test_llm_*.py backend/tests/conftest.py`
Expected: `All checks passed!`（如有 import 排序/未用匯入，修正後重跑）

- [ ] **Step 7: 空 key 離線驗（證明零 OpenAI 呼叫）**

Run: `OPENAI_API_KEY="" uv run pytest backend/tests/ -k llm -q`
Expected: PASS（空 key 亦全綠）

- [ ] **Step 8: Commit**

```bash
git add backend/tests/conftest.py backend/tests/test_llm_no_live_openai_unit.py
git commit -m "test(llm): CI 守門——conftest 執行期攔截真 OpenAI 呼叫 + grep 輔助 + 預設 llm_mock 斷言"
```

---

## Phase 8 smoke 待辦（用真 key，非本層；Codex F4 交接）

實作完成後交接給 Phase 8 整合/手動 smoke（使用者的 key），驗證本層 mock 無法涵蓋者：
1. `temperature=0.2` 是否被 gpt-5.5/gpt-5.4 接受（reasoning 模型或僅允許 1）。
2. `max_completion_tokens=1500` 對單題輸出是否足夠（reasoning 預算不致吃光輸出）。
3. 實際影像 token 計費（detail:high 整頁圖）。
4. 串流首尾 chunk / usage-only chunk 行為與兩道防護吻合。
皆集中於 `client.py` 常數，一行可改。

## Phase 9 待辦（觀測/健康層）

5. 主→備 **half-open 恢復探測 + cooldown**（本層僅 sticky 切換，不自動回主模型）。
6. LangFuse span（model / prompt+completion token / TTFT）— §5.5 觀測，Phase 8/9 在 orchestrator 埋。
7. client-disconnect 取消（`Request.is_disconnected()`）— §5.6 屬 SSE/orchestrator（Phase 8）。

## Self-Review（plan 對 spec）

**Spec coverage：**
- §5.3 LLM 生成 / 模型 fallback（連 3 切備援、tenacity）→ Task 5 + Task 7 ✓
- §5.4 版本化 system prompt + 強制引文 + 無拒答 → Task 2 ✓
- §5.5 多模態 + 條件式附圖 + detail:high + 不用 json_object + stream=True + temperature + token 上限 → Task 3 + Task 4/5 ✓
- §5.6 SSE → Phase 8（本層提供 `AsyncIterator[str]`，介面對得上）✓（不在本 plan）
- §5.7 PageCitation → Phase 8 ✓（不在本 plan）
- §5.9 DL-021 生成側追問（只帶前一問、不帶歷史回答/先前檢索）→ Task 2 ✓
- 合規紅線（無 PII payload fail-closed、只用標準付費 API、引文安全網不拒答）→ Task 1/4/5（assert_no_identifiers + 無 user=）+ Task 2（無拒答）✓
- 驗收：mock 串流（Task 6）/ fallback 計數（Task 7）/ image_routing 表驅動（Task 3）/ 無 PII payload 斷言（Task 4/5）/ 版本化常數（Task 2）/ CI 零 API（Task 9 執行期攔截 + grep）✓
- Codex F1–F6 → 全數落地（見上方處置表 + Task 3/4/5/7/9）✓

**Placeholder scan：** 無 TBD/TODO；每步含完整程式碼與指令。

**Type consistency：** `stream_complete(system, user, images, *, image_detail="high", forbidden_identifiers=frozenset()) -> AsyncIterator[str]` 在 Protocol / LLMClient / MockLLMClient / ModelFallbackClient 一致；`route_images(results, intent, max_images=DEFAULT_IMAGE_COUNT)`、`ImageRoutingDecision(indices, detail)`、`assert_no_identifiers(messages, forbidden)`、`PIILeakError`、`build_user_text(text_context, user_query, prev_query=None)`、`build_chat_messages(system, user, images, *, image_detail)`、`get_system_prompt(version=None)`、`build_llm(settings)` 各任務引用一致。

---

## 實作後 review 收斂修正（程式碼為最終真相，以下記錄與上方計畫碼的差異）

實作過程經 TDD + Opus spec/品質雙審 + Codex 終審，下列為相對上方「verbatim 計畫碼」的必要修正（皆已落地、測試覆蓋）：

1. **`fallback.py` `_ok` sentinel（TDD 抓出計畫 bug）**：上方 Task 7 的 `stream_complete` 在 tenacity 吞下可重試例外後會 fall-through 到成功路徑，存取未綁定的 `first`（`UnboundLocalError`）。最終版於 `with attempt:` 前設 `_ok=False`、成功路徑末端設 `_ok=True`、區塊後 `if not _ok: continue` 跳過 yield/return。語意不變。
2. **`fallback.py` `DEFAULT_MAX_ATTEMPTS`（Codex 終審 P1）**：原 `=3` 與 `DEFAULT_SWITCH_THRESHOLD=3` 相等，導致預設配置下主模型連 3 錯時 tenacity 已耗盡嘗試、`using_fallback` 雖被設真但備援模型於該次呼叫永不被試。改為 `DEFAULT_MAX_ATTEMPTS = DEFAULT_SWITCH_THRESHOLD + 1`（留一次給備援），觸發呼叫本身即恢復。新增以「預設值」為前提的測試。
3. **串流資源清理（Opus 品質審 Important）**：`LLMClient.stream_complete` 以 `try/finally: await stream.close()`（openai `AsyncStream` 為 async `close()`）、`ModelFallbackClient` 成功路徑以 `try/finally: await agen.aclose()`，確保 SSE 消費端提前中止（GeneratorExit）時即時釋放 httpx 連線。
4. **`image_routing.py` `cap<=0` 短路（Codex 終審 P2）**：`max_images=0` 原仍送 1 張（先 append 才檢查 cap）。加 `if cap <= 0: return ImageRoutingDecision(indices=())`，尊重明確「0 圖」。
5. **ruff 等價調整**：`AsyncIterator` 由 `collections.abc` 匯入；`QueryIntent(StrEnum)`；system prompt 字串首行以 `\` 接續（去前導換行）。皆零行為變更。

最終驗收：llm 測試 49 passed（含零 key 證明零 OpenAI 呼叫）、全 unit 回歸 79 passed 無回歸、`ruff check` 全綠、Codex 終審「No actionable correctness issues」。
