# Phase 9 — 觀測性（trace + Sentry 脫敏 + 告警）實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為已可端到端串流的系統加上**可觀測性**——LangFuse 全鏈路 trace、Sentry 錯誤回報（`before_send` 脫敏，D-M）、§7.5 告警條件邏輯——全部 **mock-first + fail-open**：無憑證時 no-op、零外部呼叫、零費用，CI 純單元。

**Architecture:** 新增 `observability/` 模組，沿用既有 `build_cache`/`build_llm` 的**工廠 + fail-open** 慣例：`build_tracer(settings)` 回 `NoOpTracer`（無 LangFuse 金鑰）或 `LangfuseTracer`（包 LangFuse v4 client）；`init_sentry(settings)` 無 DSN 即 no-op，有 DSN 時裝 `before_send` 脫敏 processor；`alerts.py` 為純條件邏輯 + 可插拔 `Notifier`（預設 log）。`Tracer` 的 span 為 context manager、**不改 wrapped 回傳值**、disabled 時 no-op。`ChatDeps.tracer` 預設 `NoOpTracer` → 既有測試/golden 零行為變化。

**Tech Stack:** LangFuse v4（4.7.1，OTel-based：`start_as_current_observation` / `propagate_attributes` / `score_current_span` / `flush`，**已在 deps**）、sentry-sdk 2.x（`before_send` / `send_default_pii=False` / `EventScrubber`，**已在 deps**）。**零新套件、零外部呼叫**。

**研究結論（context7，2026-06-14）：**
- LangFuse v4 `start_as_current_observation(name=...)` 是 context manager，**disabled（無金鑰）時 operations 為 no-op**、**不改回傳值**；`propagate_attributes(user_id=..., metadata=...)` 設 trace 層屬性（user_id 入 trace 但**不**入 LLM payload，DL-012/§5.8）；`score_current_span(name, value)` 記 metric；`flush()` 收尾。
- sentry-sdk `before_send(event, hint)` 回修改後 event 或 None（丟棄）；`send_default_pii=False`（預設）+ `EventScrubber` 自動遮 secrets/IP，**但不含**本專案領域欄位（query 文字/prompt/檢索內容/user_id/學號）→ `before_send` 須**明確**遮這些（D-M）。空 DSN → SDK no-op。

**確認範圍（使用者 2026-06-14）：** mock-first + fail-open（不 standup 真實 LangFuse/Sentry/Slack）；metrics 走 LangFuse score + 結構化 log，**Prometheus/Grafana 維持延後（DL-011）**；告警＝條件邏輯 + 可插拔 no-op notifier，真 Slack/email webhook 為 ops 後續。

---

## 範圍護欄（交 Codex 對抗式審查）

- **fail-open / no-op**：無 LangFuse 金鑰→`NoOpTracer`；無 Sentry DSN→`init_sentry` no-op；tracer/score/flush 任一例外**絕不**中斷 `/chat`。
- **隱私（D-M / §0.3 / §5.8）**：Sentry `before_send` **MUST** 遮 query 文字、prompt、檢索內容、user_id/學號、ip/country/user_agent；`send_default_pii=False`、`max_request_body_size="never"`；脫敏處理本身出錯→**丟棄該 event**（回 None，寧可不送也不洩漏）。
- **user_id**：入 LangFuse trace 屬性可（§6.5 MUST trace 含 user_id），但**MUST NOT** 進 LLM payload（既有 `forbidden_identifiers` 已守，trace 與 LLM 兩路分離）。
- **不改回傳/不改 SSE**：span 為 context manager 純計時/記錄、**不 yield、不改 events**；`ChatDeps.tracer` 預設 NoOp → 既有 SSE golden 位元組不變。
- **不跨串流持有資源**：tracing 不持有 DB 連線（DL-012 不受影響）；`flush` 只在 lifespan shutdown，不在每請求 await。
- **Prometheus 延後（DL-011）**：本 phase 不加 `/metrics`、不加 prometheus_client。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `backend/src/anatomy_backend/observability/__init__.py` | **Create** | 匯出 `Tracer`/`NoOpTracer`/`LangfuseTracer`/`build_tracer`/`init_sentry`/`scrub_event`/`evaluate_alerts`/`Notifier`/`LogNotifier`/`Alert` |
| `backend/src/anatomy_backend/observability/tracing.py` | **Create** | `Tracer` Protocol + `NoOpTracer` + `LangfuseTracer`(包 LangFuse v4) + `build_tracer(settings)` |
| `backend/src/anatomy_backend/observability/errors.py` | **Create** | `scrub_event(event, hint)`（before_send 脫敏，純函式）+ `init_sentry(settings)` |
| `backend/src/anatomy_backend/observability/alerts.py` | **Create** | `Alert` dataclass + `evaluate_alerts(metrics)`（§7.5 條件）+ `Notifier` Protocol + `LogNotifier` |
| `backend/src/anatomy_backend/api/chat.py` | Modify | `ChatDeps` 加 `tracer` 欄位（預設 NoOp）；`chat_event_stream` 包 trace/span + score |
| `backend/src/anatomy_backend/api/main.py` | Modify | lifespan：`init_sentry(settings)` + `build_tracer(settings)` 注入 ChatDeps + shutdown `flush` |
| `backend/tests/test_observability_tracing_unit.py` | **Create** | NoOp no-op/不改回傳；build_tracer 分支；LangfuseTracer 對 fake client 委派 + fail-open |
| `backend/tests/test_observability_errors_unit.py` | **Create** | scrub_event 遮敏感欄位 + 出錯丟棄；init_sentry no-op/有 DSN 參數 |
| `backend/tests/test_observability_alerts_unit.py` | **Create** | evaluate_alerts 各條件門檻；LogNotifier |
| `backend/tests/test_api_chat_sse_unit.py` | Modify | 加 `_RecordingTracer` 測試：chat 記 cache_hit/citation_verified score + encode/retrieve/llm span；golden 不變 |
| `docs/decisions.md` | Modify | 追加 **DL-026** |

---

## Task 1：`tracing.py` — `Tracer` + `NoOpTracer`

**Files:** Create `backend/src/anatomy_backend/observability/tracing.py`; Test `backend/tests/test_observability_tracing_unit.py`

- [ ] **Step 1：寫失敗測試**

```python
"""Phase 9 tracing 單元測試（NoOp/Langfuse/build_tracer；零外部呼叫）。"""
from __future__ import annotations

from anatomy_backend.observability.tracing import NoOpTracer


async def test_noop_trace_and_span_yield_and_do_not_change_returns():
    t = NoOpTracer()
    with t.trace("chat", user_id="u1", metadata={"k": "v"}):
        with t.span("encode"):
            result = 42
    assert result == 42  # 包裝不改回傳值


def test_noop_score_and_flush_are_safe_noops():
    t = NoOpTracer()
    t.score("cache_hit", 1.0)   # 不拋
    t.flush()                   # 不拋
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_tracing_unit.py -q`
Expected: FAIL（`ModuleNotFoundError: ...observability.tracing`）

- [ ] **Step 3：建立 `tracing.py`（Protocol + NoOp）**

```python
"""LangFuse 全鏈路 trace 抽象（§6.5 / DL-011 / DL-026）。

工廠 + fail-open（同 build_cache/build_llm）：無金鑰→NoOpTracer。
Tracer.trace/span 為 context manager，純計時/記錄、不改 wrapped 回傳值；
disabled 時全 no-op。user_id 入 trace 屬性可，但 MUST NOT 進 LLM payload（DL-012）。
flush 只在 lifespan shutdown，不在每請求 await。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

logger = logging.getLogger(__name__)


class Tracer(Protocol):
    def trace(
        self, name: str, *, user_id: str | None = None, metadata: dict | None = None
    ) -> AbstractContextManager[None]: ...

    def span(self, name: str) -> AbstractContextManager[None]: ...

    def score(self, name: str, value: float, *, comment: str | None = None) -> None: ...

    def flush(self) -> None: ...


class NoOpTracer:
    """無 LangFuse 金鑰時的退路：全部 no-op。"""

    @contextmanager
    def trace(
        self, name: str, *, user_id: str | None = None, metadata: dict | None = None
    ) -> Iterator[None]:
        yield

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        yield

    def score(self, name: str, value: float, *, comment: str | None = None) -> None:
        return None

    def flush(self) -> None:
        return None
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_observability_tracing_unit.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/observability/tracing.py backend/tests/test_observability_tracing_unit.py
git commit -m "feat(obs): Tracer Protocol + NoOpTracer（fail-open 退路，span 不改回傳）"
```

---

## Task 2：`tracing.py` — `LangfuseTracer` + `build_tracer`

**Files:** Modify `tracing.py`; Test `test_observability_tracing_unit.py`

- [ ] **Step 1：寫失敗測試**（fake langfuse client + build_tracer 分支）

```python
import pytest

from anatomy_backend.config import Settings
from anatomy_backend.observability.tracing import (
    LangfuseTracer,
    NoOpTracer,
    build_tracer,
)


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@localhost:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@localhost:5432/anatomy_rag",
        redis_url="redis://localhost:6379/0",
    )
    base.update(over)
    return Settings(**base)


class _FakeLangfuse:
    def __init__(self, *, fail: bool = False):
        self.scores = []
        self.flushed = 0
        self.spans = []
        self._fail = fail

    @contextmanager
    def propagate_attributes(self, **kw):
        self.spans.append(("propagate", kw))
        yield

    @contextmanager
    def start_as_current_observation(self, *, name):
        self.spans.append(("span", name))
        yield object()

    def score_current_span(self, *, name, value, comment=None):
        if self._fail:
            raise RuntimeError("langfuse down")
        self.scores.append((name, value))

    def flush(self):
        if self._fail:
            raise RuntimeError("langfuse down")
        self.flushed += 1


# 需在檔案頂部 import：from contextlib import contextmanager


def test_build_tracer_noop_when_unconfigured():
    assert isinstance(build_tracer(_settings()), NoOpTracer)  # 無金鑰→NoOp


def test_build_tracer_langfuse_when_configured(monkeypatch):
    created = {}

    class _FakeLF:
        def __init__(self, **kw):
            created.update(kw)

    monkeypatch.setattr("langfuse.Langfuse", _FakeLF)
    t = build_tracer(_settings(
        langfuse_host="http://lf:3000",
        langfuse_public_key="pk_x",
        langfuse_secret_key="sk_x",
    ))
    assert isinstance(t, LangfuseTracer)
    assert created["host"] == "http://lf:3000"


async def test_langfuse_tracer_delegates_and_scores():
    fake = _FakeLangfuse()
    t = LangfuseTracer(fake)
    with t.trace("chat", user_id="u1", metadata={"k": "v"}):
        with t.span("encode"):
            r = 7
    t.score("cache_hit", 1.0)
    t.flush()
    assert r == 7
    assert ("span", "encode") in fake.spans
    assert ("cache_hit", 1.0) in fake.scores
    assert fake.flushed == 1


async def test_langfuse_tracer_score_flush_fail_open():
    t = LangfuseTracer(_FakeLangfuse(fail=True))
    t.score("x", 1.0)   # 不拋
    t.flush()           # 不拋
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_tracing_unit.py -k "build_tracer or langfuse" -q`
Expected: FAIL（`ImportError: cannot import name 'LangfuseTracer'`）

- [ ] **Step 3：實作 `LangfuseTracer` + `build_tracer`（加到 `tracing.py`）**

```python
class LangfuseTracer:
    """包 LangFuse v4 client（OTel-based）。trace/span 為 context manager；
    score/flush fail-open（記 warning，不中斷 /chat）。"""

    def __init__(self, client) -> None:
        self._lf = client

    @contextmanager
    def trace(
        self, name: str, *, user_id: str | None = None, metadata: dict | None = None
    ) -> Iterator[None]:
        try:
            with self._lf.propagate_attributes(user_id=user_id, metadata=metadata or {}):
                with self._lf.start_as_current_observation(name=name):
                    yield
        except Exception:  # noqa: BLE001  tracing 絕不中斷 /chat
            logger.warning("LangfuseTracer.trace 失敗→改無追蹤續行", exc_info=True)
            yield

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        try:
            with self._lf.start_as_current_observation(name=name):
                yield
        except Exception:  # noqa: BLE001
            logger.warning("LangfuseTracer.span 失敗→續行", exc_info=True)
            yield

    def score(self, name: str, value: float, *, comment: str | None = None) -> None:
        try:
            self._lf.score_current_span(name=name, value=value, comment=comment)
        except Exception:  # noqa: BLE001
            logger.warning("LangfuseTracer.score 失敗（忽略）", exc_info=True)

    def flush(self) -> None:
        try:
            self._lf.flush()
        except Exception:  # noqa: BLE001
            logger.warning("LangfuseTracer.flush 失敗（忽略）", exc_info=True)


def build_tracer(settings) -> Tracer:
    """依設定回傳 tracer（DL-026）。三項金鑰齊備才回 LangfuseTracer，否則 NoOpTracer。"""
    host = getattr(settings, "langfuse_host", "")
    pk = getattr(settings, "langfuse_public_key", "")
    sk = getattr(settings, "langfuse_secret_key", "")
    if not (host and pk and sk):
        return NoOpTracer()
    import langfuse

    client = langfuse.Langfuse(
        host=host, public_key=pk, secret_key=sk, flush_at=50, flush_interval=2
    )
    return LangfuseTracer(client)
```

> 註：頂部需 `from contextlib import AbstractContextManager, contextmanager`（Step 3 Task 1 已建；確認 `contextmanager` 有 import）。

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_observability_tracing_unit.py -q`
Expected: PASS（全部）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/observability/tracing.py backend/tests/test_observability_tracing_unit.py
git commit -m "feat(obs): LangfuseTracer（包 v4 client，score/flush fail-open）+ build_tracer 工廠"
```

---

## Task 3：`errors.py` — Sentry `before_send` 脫敏 + `init_sentry`

**Files:** Create `backend/src/anatomy_backend/observability/errors.py`; Test `backend/tests/test_observability_errors_unit.py`

- [ ] **Step 1：寫失敗測試**

```python
"""Phase 9 Sentry 脫敏 + init 單元測試（零外部呼叫）。"""
from __future__ import annotations

from anatomy_backend.config import Settings
from anatomy_backend.observability.errors import init_sentry, scrub_event


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@localhost:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@localhost:5432/anatomy_rag",
        redis_url="redis://localhost:6379/0",
    )
    base.update(over)
    return Settings(**base)


_REDACTED = "[redacted]"


def test_scrub_event_redacts_sensitive_keys_recursively():
    event = {
        "extra": {
            "query": "肱二頭肌的起止點",
            "prompt": "system+user prompt...",
            "retrieved": ["page1 text", "page2 text"],
            "user_id": "00000000-0000-0000-0000-000000000001",
            "student_id": "B12345678",
            "safe_field": "keep-me",
        },
        "request": {"data": {"query": "肱二頭肌", "ip": "1.2.3.4"}},
        "contexts": {"trace": {"user_agent": "Mozilla/5.0", "country": "TW"}},
    }
    out = scrub_event(event, {})
    assert out["extra"]["query"] == _REDACTED
    assert out["extra"]["prompt"] == _REDACTED
    assert out["extra"]["retrieved"] == _REDACTED
    assert out["extra"]["user_id"] == _REDACTED
    assert out["extra"]["student_id"] == _REDACTED
    assert out["extra"]["safe_field"] == "keep-me"      # 非敏感保留
    assert out["request"]["data"]["query"] == _REDACTED
    assert out["request"]["data"]["ip"] == _REDACTED
    assert out["contexts"]["trace"]["user_agent"] == _REDACTED
    assert out["contexts"]["trace"]["country"] == _REDACTED


def test_scrub_event_returns_none_on_error():
    # 脫敏本身出錯→丟棄 event（寧可不送也不洩漏）
    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")
    assert scrub_event(_Boom(), {}) is None


def test_init_sentry_noop_without_dsn():
    assert init_sentry(_settings(sentry_dsn="")) is False   # 無 DSN→no-op


def test_init_sentry_configures_scrubbing(monkeypatch):
    captured = {}

    def _fake_init(**kw):
        captured.update(kw)

    monkeypatch.setattr("sentry_sdk.init", _fake_init)
    ok = init_sentry(_settings(sentry_dsn="https://x@example.invalid/1"))
    assert ok is True
    assert captured["before_send"] is scrub_event
    assert captured["send_default_pii"] is False
    assert captured["max_request_body_size"] == "never"
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_errors_unit.py -q`
Expected: FAIL（`ModuleNotFoundError: ...observability.errors`）

- [ ] **Step 3：實作 `errors.py`**

```python
"""Sentry 錯誤回報 + before_send 脫敏（§6.5 / D-M / DL-026）。

D-M：不做內容層 PHI 攔截（與 §6.7 衝突），改在外送 Sentry 時脫敏——移除 query 文字、
prompt、檢索內容、識別資訊（user_id/學號/ip/country/user_agent）。空 DSN→no-op。
脫敏出錯→回 None 丟棄 event（寧可不送也不洩漏）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REDACTED = "[redacted]"

# 領域敏感欄位（小寫子字串比對）；Sentry 預設 denylist 不含這些。
_SENSITIVE_SUBSTRINGS = (
    "query", "prompt", "user_text", "system_prompt", "answer", "completion",
    "retrieved", "context", "snippet", "sources", "docling",
    "user_id", "userid", "student", "學號", "學生",
    "ip", "country", "user_agent", "useragent", "ua",
)


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in _SENSITIVE_SUBSTRINGS)


def _scrub(obj):
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _is_sensitive(str(k)) else _scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def scrub_event(event, hint):
    """Sentry before_send：遞迴遮蔽敏感鍵；出錯→回 None 丟棄 event。"""
    try:
        return _scrub(event)
    except Exception:  # noqa: BLE001  寧可丟棄也不洩漏
        logger.warning("Sentry scrub_event 失敗→丟棄該 event", exc_info=True)
        return None


def init_sentry(settings) -> bool:
    """有 DSN 才 init（裝 before_send 脫敏）；無 DSN→no-op 回 False。"""
    dsn = getattr(settings, "sentry_dsn", "")
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        before_send=scrub_event,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,   # v1 只收錯誤，不收 performance trace（LangFuse 負責 trace）
    )
    return True
```

> 註：`_scrub` 遞迴遮蔽——`retrieved` 是 list 但 key 命中敏感→整個值被 `_REDACTED`（在 dict 層先判 key）。`test_scrub_event_returns_none_on_error` 用覆寫 `.get` 觸發例外路徑；`_scrub` 用 `.items()`，故測試的 `_Boom` 需讓 `.items()` 拋——改：`_Boom` 覆寫 `items` 拋。**工人請據此調整測試**：`_Boom(dict)` 覆寫 `def items(self): raise RuntimeError`。

- [ ] **Step 4：跑測試確認通過**（先修正 Step 1 的 `_Boom` 改覆寫 `items`）

Run: `uv run --no-sync pytest backend/tests/test_observability_errors_unit.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/observability/errors.py backend/tests/test_observability_errors_unit.py
git commit -m "feat(obs): Sentry init + before_send 脫敏（D-M：遮 query/prompt/檢索/識別資訊；出錯丟棄）"
```

---

## Task 4：`alerts.py` — §7.5 告警條件 + Notifier

**Files:** Create `backend/src/anatomy_backend/observability/alerts.py`; Test `backend/tests/test_observability_alerts_unit.py`

- [ ] **Step 1：寫失敗測試**

```python
"""Phase 9 告警條件 + notifier 單元測試。"""
from __future__ import annotations

from anatomy_backend.observability.alerts import (
    LogNotifier,
    evaluate_alerts,
)


def test_p95_latency_breach_triggers():
    a = evaluate_alerts({"p95_latency_s": 9.0, "p95_breach_minutes": 10})
    names = {x.name for x in a}
    assert "p95_latency" in names
    assert all(x.severity == "must" for x in a if x.name == "p95_latency")


def test_p95_latency_below_threshold_or_short_no_trigger():
    assert evaluate_alerts({"p95_latency_s": 9.0, "p95_breach_minutes": 9}) == []
    assert evaluate_alerts({"p95_latency_s": 7.9, "p95_breach_minutes": 30}) == []


def test_model_error_rate_triggers():
    a = {x.name for x in evaluate_alerts({"model_error_rate": 0.06, "model_error_minutes": 5})}
    assert "model_error_rate" in a


def test_usage_ratio_triggers_at_80pct():
    assert "usage_ratio" in {x.name for x in evaluate_alerts({"usage_ratio": 0.80})}
    assert evaluate_alerts({"usage_ratio": 0.79}) == []


def test_citation_fail_rate_is_should_severity():
    a = evaluate_alerts({"citation_fail_rate": 0.11, "citation_fail_minutes": 30})
    assert any(x.name == "citation_fail_rate" and x.severity == "should" for x in a)


def test_log_notifier_does_not_raise():
    LogNotifier().notify(evaluate_alerts({"usage_ratio": 0.9})[0])  # 不拋
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_alerts_unit.py -q`
Expected: FAIL（`ModuleNotFoundError: ...observability.alerts`）

- [ ] **Step 3：實作 `alerts.py`**

```python
"""§7.5 線上告警條件邏輯 + 可插拔 notifier（DL-026）。

純條件評估（單元可測）；metrics 來源彙整（Prometheus/LangFuse 聚合）與真實 Slack/email
webhook 為 ops 後續（DL-011 Prometheus 延後）。預設 LogNotifier（no-op/log）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    name: str
    severity: str   # "must" | "should"
    message: str
    channels: tuple[str, ...]


def evaluate_alerts(metrics: dict) -> list[Alert]:
    """依 §7.5 條件回傳觸發的告警。metrics 由上游彙整提供（v1 未排程，邏輯先就位）。"""
    out: list[Alert] = []
    # MUST：p95 latency > 8s 連續 10 分鐘 → Slack
    if metrics.get("p95_latency_s", 0) > 8 and metrics.get("p95_breach_minutes", 0) >= 10:
        out.append(Alert("p95_latency", "must", "p95 latency > 8s 連續 ≥10 分鐘", ("slack",)))
    # MUST：模型錯誤率 > 5% 連續 5 分鐘 → Slack + email
    if metrics.get("model_error_rate", 0) > 0.05 and metrics.get("model_error_minutes", 0) >= 5:
        out.append(Alert("model_error_rate", "must", "模型錯誤率 > 5% 連續 ≥5 分鐘",
                         ("slack", "email")))
    # MUST：RPM/TPM 用量達 80% → Slack
    if metrics.get("usage_ratio", 0) >= 0.80:
        out.append(Alert("usage_ratio", "must", "RPM/TPM 用量達 80%", ("slack",)))
    # SHOULD：引文格式驗證失敗率 > 10% 連續 30 分鐘 → Slack
    if metrics.get("citation_fail_rate", 0) > 0.10 and metrics.get("citation_fail_minutes", 0) >= 30:
        out.append(Alert("citation_fail_rate", "should", "引文驗證失敗率 > 10% 連續 ≥30 分鐘",
                         ("slack",)))
    return out


class Notifier(Protocol):
    def notify(self, alert: Alert) -> None: ...


class LogNotifier:
    """v1 預設：寫 log。真實 Slack/email webhook 為 ops 後續（新連線，先問）。"""

    def notify(self, alert: Alert) -> None:
        logger.warning("ALERT[%s/%s] %s → %s", alert.severity, alert.name,
                       alert.message, ",".join(alert.channels))
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_observability_alerts_unit.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/observability/alerts.py backend/tests/test_observability_alerts_unit.py
git commit -m "feat(obs): §7.5 evaluate_alerts 條件邏輯 + Notifier/LogNotifier（真 webhook 延後 ops）"
```

---

## Task 5：`__init__.py` 匯出

**Files:** Create `backend/src/anatomy_backend/observability/__init__.py`

- [ ] **Step 1：建立 `__init__.py`**

```python
from anatomy_backend.observability.alerts import (
    Alert,
    LogNotifier,
    Notifier,
    evaluate_alerts,
)
from anatomy_backend.observability.errors import init_sentry, scrub_event
from anatomy_backend.observability.tracing import (
    LangfuseTracer,
    NoOpTracer,
    Tracer,
    build_tracer,
)

__all__ = [
    "Alert", "LogNotifier", "Notifier", "evaluate_alerts",
    "init_sentry", "scrub_event",
    "LangfuseTracer", "NoOpTracer", "Tracer", "build_tracer",
]
```

- [ ] **Step 2：驗證 import**

Run: `uv run --no-sync python -c "from anatomy_backend.observability import build_tracer, init_sentry, evaluate_alerts; print('OK')"`
Expected: `OK`

- [ ] **Step 3：commit**

```bash
git add backend/src/anatomy_backend/observability/__init__.py
git commit -m "feat(obs): observability 套件 __init__ 匯出"
```

---

## Task 6：接線 `chat.py`（tracer 欄位 + trace/span/score）+ recording 測試

**Files:** Modify `backend/src/anatomy_backend/api/chat.py`; Test `backend/tests/test_api_chat_sse_unit.py`

> 重點：`ChatDeps.tracer` 預設 `NoOpTracer` → 既有測試/golden 零行為變化。span 只計時/記分、**不 yield、不改 events**。

- [ ] **Step 1：寫失敗測試**（recording tracer；mirror 既有 `_make_chat_deps` harness）

```python
async def test_chat_records_trace_spans_and_cache_hit_score():
    """chat 記 cache_hit/citation_verified score 與 encode/retrieve/llm span（NoOp 時不影響 SSE）。"""
    from contextlib import contextmanager

    from anatomy_backend.api.chat import chat_event_stream
    from anatomy_backend.api.schemas import normalize_chat

    class _RecordingTracer:
        def __init__(self):
            self.spans = []
            self.scores = []
        @contextmanager
        def trace(self, name, *, user_id=None, metadata=None):
            self.spans.append(("trace", name)); yield
        @contextmanager
        def span(self, name):
            self.spans.append(("span", name)); yield
        def score(self, name, value, *, comment=None):
            self.scores.append((name, value))
        def flush(self):
            pass

    tracer = _RecordingTracer()
    normalized = normalize_chat({"messages": [{"role": "user", "content": "肱二頭肌的起止點"}]})
    deps = _make_chat_deps(tracer=tracer)   # ← _make_chat_deps 需支援 tracer kwarg
    user = _make_user()
    spawned = []
    deps.spawn = lambda coro: spawned.append(coro)
    async for _ in chat_event_stream(deps, normalized, user):
        pass
    for coro in spawned:
        try:
            await coro
        except Exception:
            pass
    span_names = {n for _, n in tracer.spans}
    assert "encode" in span_names and "retrieve" in span_names and "llm" in span_names
    assert any(n == "citation_verified" for n, _ in tracer.scores)
```

> 工人備註：`_make_chat_deps` 目前簽章為 `(cache=None)`；擴成 `(cache=None, tracer=None)`，`tracer or NoOpTracer()`。retrieve fake 須回非空且引文 grounded（既有 `_golden_result()` 已滿足），使流程到 LLM 完成 + citation 驗證。

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_chat_sse_unit.py::test_chat_records_trace_spans_and_cache_hit_score -q`
Expected: FAIL（`ChatDeps` 無 `tracer` 欄位 / `_make_chat_deps` 不收 tracer / 無 span 記錄）

- [ ] **Step 3：`ChatDeps` 加 `tracer` 欄位**（`chat.py`）

在 import 區加：`from anatomy_backend.observability.tracing import NoOpTracer, Tracer`
在 `ChatDeps` 的 `top_n: int = 3` 之後加（兩者皆有預設、順序合法）：

```python
    tracer: Tracer = field(default_factory=NoOpTracer)
```

並確認頂部已 `from dataclasses import dataclass, field`（原為 `from dataclasses import dataclass`→改）。

- [ ] **Step 4：在 `chat_event_stream` 包 trace/span + score**

把整個產生器主體包進 trace（user_id 入 trace 屬性、不入 LLM）：在 `kb = deps.kb_version` 之後、第一個 yield 之前開 trace context，包住整個流程主體。具體：

```python
    kb = deps.kb_version
    with deps.tracer.trace(
        "chat",
        user_id=user.user_id,
        metadata={"is_followup": normalized.is_followup, "kb_version": kb},
    ):
        # ...（原本 Step 1 快取 → ... → Step 9 全部主體縮排進此 with）...
```

在對應位置加 span/score（不改任何 yield）：
- 快取命中分支：`deps.tracer.score("cache_hit", 1.0)` 後續維持原 yield/log。
- encode：`with deps.tracer.span("encode"):` 包 `query_repr = await deps.encoder.encode_query(...)`。
- retrieve：`with deps.tracer.span("retrieve"):` 包 `results = await deps.retrieve_fn(...)` 與 `build_citations_and_images(...)`。
- LLM 串流：`with deps.tracer.span("llm"):` 包 `async for delta in deps.llm.stream_complete(...)` 迴圈。
- 驗證後：`deps.tracer.score("cache_hit", 0.0)`（非命中路徑）、`deps.tracer.score("citation_verified", 1.0 if verification.all_grounded else 0.0)`。

> 縮排注意：trace `with` 會把整段主體縮排。**所有原本的 `return` 改為在 with 內 return**（context manager 正常退出，span 收尾）。確認 NoOpTracer 下產生的 SSE 事件位元組與 golden 完全一致（既有 golden 測試會驗）。

- [ ] **Step 5：跑測試確認通過 + golden/既有不破**

Run: `uv run --no-sync pytest backend/tests/test_api_chat_sse_unit.py backend/tests/test_api_chat_unit.py -q`
Expected: PASS（含新 recording 測試、既有 golden 位元組對照、metadata_filter 測試）

- [ ] **Step 6：commit**

```bash
git add backend/src/anatomy_backend/api/chat.py backend/tests/test_api_chat_sse_unit.py
git commit -m "feat(obs): chat 接線 trace/span/score（tracer 預設 NoOp；SSE golden 不變；user_id 入 trace 不入 LLM）"
```

---

## Task 7：接線 `main.py` lifespan（init_sentry + build_tracer + flush）

**Files:** Modify `backend/src/anatomy_backend/api/main.py`

- [ ] **Step 1：lifespan 啟動裝 Sentry + tracer**

在 lifespan 早段（建 settings 後、或 redis 之前皆可）加：

```python
    from anatomy_backend.observability import build_tracer, init_sentry

    init_sentry(settings)            # 無 DSN→no-op
    tracer = build_tracer(settings)  # 無金鑰→NoOpTracer
```

- [ ] **Step 2：注入 ChatDeps + app.state**

`_build_chat_deps` 的 `ChatDeps(...)` 加參數 `tracer=tracer`；並 `app.state.tracer = tracer`。

- [ ] **Step 3：shutdown flush**

在 cleanup 段（`await pool.close()` 旁）加：

```python
    tracer.flush()
```

- [ ] **Step 4：驗證 lifespan / e2e 不破**

Run: `uv run --no-sync pytest backend/tests/ -k "lifespan or chat or main or cache" -q`
Expected: PASS（e2e ASGITransport 不啟 lifespan；LifespanManager 測試走 mock 設定→NoOpTracer/Sentry no-op，不連外）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/api/main.py
git commit -m "feat(obs): main lifespan init_sentry + build_tracer 注入 ChatDeps + shutdown flush"
```

---

## Task 8：DL-026 + 全套件回歸 + lint

**Files:** Modify `docs/decisions.md`

- [ ] **Step 1：追加 DL-026**

```markdown

## DL-026: 觀測性 v1＝tracer 抽象 + fail-open + Sentry before_send 脫敏；Prometheus/Slack 延後

- **狀態**：APPROVED　**提案者**：main Claude（Phase 9）　**日期**：2026-06-14　**裁決者**：專案負責人（2026-06-14 確認 mock-first + metrics 走 LangFuse）
- **影響檔案**：ARCHITECTURE.md §6.5、§7.5；`backend/.../observability/*`、`api/main.py`、`api/chat.py`

### 背景
DL-011 定觀測先 LangFuse+Sentry、Prometheus 延後；D-M 定不做內容層 PHI 攔截、改 Sentry/LangFuse 脫敏。Phase 9 落地需定案如何在「無外部後端 standup」下交付且可測。

### 提案（與 DL-011/D-M 一致，屬落地記錄）
1. **mock-first + fail-open**：`build_tracer` 無 LangFuse 金鑰→`NoOpTracer`；`init_sentry` 無 DSN→no-op；tracer/score/flush 任一例外不中斷 `/chat`。CI 零外部呼叫、零費用。
2. **Sentry before_send 脫敏（D-M）**：`send_default_pii=False` + `max_request_body_size="never"` + 自訂 `scrub_event` 遞迴遮 query/prompt/檢索內容/user_id/學號/ip/country/user_agent；脫敏出錯→回 None 丟棄 event。
3. **user_id 入 LangFuse trace 屬性、MUST NOT 入 LLM payload**（DL-012/§5.8）；trace 與 LLM 兩路分離。
4. **metrics 走 LangFuse score + 結構化 log**（cache_hit / citation_verified / latency）；**Prometheus/Grafana 維持延後（DL-011）**，本 phase 不加 `/metrics`。
5. **告警**＝`evaluate_alerts` 純條件邏輯（§7.5 門檻）+ 可插拔 `Notifier`（預設 `LogNotifier`）；真 Slack/email webhook 與 metrics 來源排程為 **ops 後續**（新連線，先問）。
6. **不新增套件**：`langfuse`/`sentry-sdk` 已在 deps。

### 後果
- v1 trace/錯誤回報需設 LangFuse/Sentry 憑證才真正送出；未設則靜默 no-op（log 仍在）。
- 告警目前只有條件邏輯與介面；接真實 metrics 來源與通知管道屬部署/ops。
```

- [ ] **Step 2：ruff（勿 format）**

Run: `uv run --no-sync ruff check backend/src/anatomy_backend/observability backend/src/anatomy_backend/api/chat.py backend/src/anatomy_backend/api/main.py backend/tests/test_observability_tracing_unit.py backend/tests/test_observability_errors_unit.py backend/tests/test_observability_alerts_unit.py backend/tests/test_api_chat_sse_unit.py`
Expected: `All checks passed!`（import 排序用 `ruff check --fix`；**勿** `ruff format`）

- [ ] **Step 3：全 backend 回歸**

Run: `uv run --no-sync pytest backend/tests -q`
Expected: 全綠（整合測試無 redis 時 skip）

- [ ] **Step 4：commit**

```bash
git add docs/decisions.md
git commit -m "docs(decisions): DL-026 觀測性 v1=tracer 抽象+fail-open+Sentry 脫敏；Prometheus/Slack 延後"
```

---

## Self-Review（spec + 範圍對照）

| spec / 驗收（roadmap Phase 9 + §6.5/§7.5） | 對應 task |
|---|---|
| LangFuse 全鏈路 trace（手動 + span 包裝不改回傳） | Task 1/2 + Task 6 |
| Sentry before_send 移除敏感欄位（注入 query 斷言被遮蔽） | Task 3 |
| 告警條件單元測試（§7.5 門檻） | Task 4 |
| LangFuse/Sentry 缺席 fail-open（無金鑰 no-op） | Task 1/2/3 + Task 7 |
| trace 含 user_id 但 user_id 不送 LLM | Task 6（trace 屬性）+ 既有 forbidden_identifiers |
| metrics 走 LangFuse score + log；Prometheus 延後 | Task 6 + DL-026 |
| SSE golden 不變（NoOp 預設） | Task 6 Step 5 |
| 零新套件、零外部呼叫 | 全程（已在 deps；測試 monkeypatch/no-keys） |
| decisions.md DL-026 | Task 8 |

**Placeholder scan：** 無 TODO/TBD；工人備註（Task 3 `_Boom` 改覆寫 `items`、Task 6 `_make_chat_deps` 加 tracer kwarg）為明確指示非佔位。
**Type 一致性：** `Tracer.trace(name,*,user_id=None,metadata=None)`/`span(name)`/`score(name,value,*,comment=None)`/`flush()`、`build_tracer(settings)`、`scrub_event(event,hint)`/`init_sentry(settings)->bool`、`Alert(name,severity,message,channels)`/`evaluate_alerts(metrics)->list[Alert]`、`ChatDeps.tracer` 跨 task 一致。
