# Phase 9 — 觀測性（trace + Sentry 脫敏 + 告警）實作計畫 v3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為已可端到端串流的系統加上**可觀測性**——LangFuse 全鏈路 trace、Sentry 錯誤回報（`before_send` **default-deny 脫敏**，D-M）、§7.5 告警**條件邏輯與介面**——全部 **mock-first + fail-open**：無憑證時 no-op、零外部呼叫、零費用，CI 純單元。

**Architecture:** 新增 `observability/` 模組，沿用 `build_cache`/`build_llm` 的**工廠 + fail-open** 慣例。`Tracer.trace/span` 為 context manager、**只計時/記錄、不 yield、不改 wrapped 回傳值**；fail-open 用 `_safe_cm`（只抑制 tracer enter/exit 失敗，**絕不**吞業務例外、**絕不**二次 yield）。`ChatDeps.tracer` 預設 `NoOpTracer` → 既有 SSE golden 位元組不變。隱私為**硬性紅線**：LangFuse 只收**假名化（hash）user_id**、Sentry `before_send` **default-deny** 移除所有自由文字面。

**Tech Stack:** LangFuse v4（4.7.1，OTel）、sentry-sdk 2.x（**已在 deps**）。**零新套件、零外部呼叫。**

---

## Codex 對抗式審查（v1，2026-06-14）修訂摘要——本 v2 已逐項處置

v1 verdict＝needs-attention / no-ship（1 critical + 5 high）：

1. **[critical] Sentry 脫敏只比對 key→例外訊息/breadcrumbs/logentry 內的 query/PHI 原樣外送** → 改 **default-deny**：明確移除 `exception.values[].value`、frame `vars`、`message`、`logentry`、`breadcrumbs[].message/data`、`request.data/query_string/cookies/headers/env`、`extra` 整塊；再加遞迴 key-scrub 防漏；`include_local_variables=False`；出錯/非 dict→回 None 丟棄。每個洩漏面加注入測試。
2. **[high] contextmanager fail-open 二次 yield→`RuntimeError` 並掩蓋業務例外** → 用 `_safe_cm`（ExitStack）：只抑制 tracer enter/exit、`yield` 在 try/except 外、業務例外照常傳播。加 enter-fail/exit-fail/body-exc 測試。
3. **[high] root trace 包住 `deps.spawn`→create_task 複製 OTel context 給背景任務** → `_spawn` 改 `asyncio.create_task(coro, context=contextvars.Context())` 隔離；加真 create_task 測試。
4. **[high] 把原始 user_id 傳 LangFuse 違反 D-M** → LangFuse 只收**假名化 hash**（`_pseudonymize`）——同時滿足 §6.5「trace 可追蹤」與 D-M「移除識別資訊」；metadata 不含原始 id/query。加斷言 trace 屬性無原始 user_id。
5. **[high] build_tracer/init_sentry 初始化未 fail-open→可能擋啟動** → import + `Langfuse(...)` + `sentry_sdk.init` 全包 try/except→NoOp/False。加建構拋錯測試。
6. **[high] §7.5 MUST 告警無 metrics 來源/排程/通知路徑→永不觸發** → **誠實降級**：本 phase 只交付告警**條件邏輯 + 介面**；metrics 聚合、時間窗排程、真實通知管道（Slack/email）**明示延後 ops**（DL-011 Prometheus 延後）。移除「§7.5 MUST 已滿足」宣稱。

### Codex 對抗式覆審（v2）修訂摘要——本 v3 再處置（context 隔離/告警降級已獲認可）

1. **[critical] scrubber 仍是 key denylist→`user.id`/`tags`/未知 `contexts` 自由文字外送** → 改 **結構 allowlist**（只保留核准頂層 + 例外 type/stacktrace + 核准 contexts 型別，其餘整塊丟棄）。注入 `user.id`/`tags.note`/`contexts.custom` 測試。
2. **[high] 空 salt→假名可字典反查** → `_pseudonymize` 改 **HMAC-SHA256（128 bits）**；`build_tracer` **缺 salt 不啟用 LangFuse**。
3. **[high] `_safe_cm` enter 回滾 `stack.close()` 未保護** → 回滾 close 也包 try/except；加「部分 enter 成功 + 後續 enter 與回滾 exit 雙失敗」測試。
4. **[high] tracer metadata 邊界放任原始 query/user_id** → `LangfuseTracer` 內 **metadata allowlist**；加 query/user_id 拒送測試。
5. **[medium] 同步 flush 無 timeout→可卡 shutdown** → `flush_tracer` 用 `to_thread + wait_for(timeout)`；加阻塞 flush 有界返回測試。

---

## 範圍護欄（隱私為硬紅線；交 Codex 對抗式審查）

- **fail-open / no-op**：無 LangFuse 金鑰→`NoOpTracer`；無 Sentry DSN→`init_sentry` no-op；**SDK import/建構/init 失敗→NoOp/False（不擋啟動）**；tracer/score/flush 任一例外**絕不**中斷 `/chat`。
- **隱私（D-M / §0.3 / §5.8）**：
  - Sentry `before_send` **結構 allowlist**：只保留核准頂層欄位 + 例外 type/stacktrace 結構 + 核准 contexts 型別；`user`/`tags`/`extra`/`breadcrumbs`/`request`/`message`/`logentry`/未知頂層**整塊丟棄**；`send_default_pii=False`、`max_request_body_size="never"`、`include_local_variables=False`；非 dict/出錯→**回 None 丟棄**。
  - LangFuse **只收假名化 user_id**（**HMAC-SHA256 + 必填高熵 salt**，缺 salt 則不啟用）+ **metadata allowlist**；**MUST NOT** 收原始 user_id/學號/query/檢索內容。
  - shutdown flush **有界**（`to_thread` + `wait_for` timeout），不卡事件迴圈/容器終止。
- **user_id**：假名化後入 trace（§6.5「可追蹤」），原始值**MUST NOT** 進 LLM payload（既有 `forbidden_identifiers`）亦不進 trace。
- **不改回傳/不改 SSE**：span context manager 純記錄；`_safe_cm` 不二次 yield、不吞業務例外；`ChatDeps.tracer` 預設 NoOp→既有 golden 位元組不變。
- **背景任務 context 隔離**：`_spawn` 用乾淨 `contextvars.Context()`，detached log/cache 任務不繼承 span。
- **DL-012 不受影響**：tracing 不持 DB 連線；`flush` 只在 lifespan shutdown。
- **Prometheus 延後（DL-011）**：不加 `/metrics`、不加 prometheus_client。**告警僅邏輯+介面**，operational 路徑延後 ops。

**不新增套件**（`langfuse`/`sentry-sdk` 已在 deps）；**零外部呼叫**（測試 no-keys + monkeypatch）。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `backend/src/anatomy_backend/observability/__init__.py` | **Create** | 匯出 |
| `backend/src/anatomy_backend/observability/tracing.py` | **Create** | `Tracer` Protocol + `NoOpTracer` + `LangfuseTracer`(含 `_safe_cm`/`_pseudonymize`) + `build_tracer`(fail-open) |
| `backend/src/anatomy_backend/observability/errors.py` | **Create** | `scrub_event`(default-deny) + `init_sentry`(fail-open) |
| `backend/src/anatomy_backend/observability/alerts.py` | **Create** | `Alert` + `evaluate_alerts` + `Notifier`/`LogNotifier`（邏輯+介面） |
| `backend/src/anatomy_backend/config.py` | Modify | 加 `langfuse_user_id_salt: str = ""` |
| `backend/src/anatomy_backend/api/chat.py` | Modify | `ChatDeps.tracer`(預設 NoOp) + trace/span/score 接線 |
| `backend/src/anatomy_backend/api/main.py` | Modify | lifespan init_sentry+build_tracer+flush；`_spawn` context 隔離 |
| `backend/tests/test_observability_tracing_unit.py` | **Create** | NoOp/不改回傳/fail-open(enter/exit/body-exc)/build_tracer 分支+建構失敗/假名化/委派 |
| `backend/tests/test_observability_errors_unit.py` | **Create** | default-deny 各洩漏面注入/出錯丟棄/init no-op+參數 |
| `backend/tests/test_observability_alerts_unit.py` | **Create** | 條件門檻/LogNotifier |
| `backend/tests/test_api_chat_sse_unit.py` | Modify | `_RecordingTracer` 測試 + spawn context 隔離測試；golden 不變 |
| `docs/decisions.md` | Modify | 追加 **DL-026** |

---

## Task 1：`tracing.py` — `Tracer` + `NoOpTracer` + `_safe_cm`

**Files:** Create `tracing.py`; Test `test_observability_tracing_unit.py`

- [ ] **Step 1：寫失敗測試**

```python
"""Phase 9 tracing 單元測試（NoOp/Langfuse/build_tracer；零外部呼叫）。"""
from __future__ import annotations

import pytest

from anatomy_backend.observability.tracing import NoOpTracer


async def test_noop_trace_and_span_do_not_change_returns():
    t = NoOpTracer()
    with t.trace("chat", user_id="u1", metadata={"k": "v"}):
        with t.span("encode"):
            result = 42
    assert result == 42


def test_noop_score_and_flush_are_safe_noops():
    t = NoOpTracer()
    t.score("cache_hit", 1.0)
    t.flush()


async def test_noop_does_not_swallow_body_exception():
    t = NoOpTracer()
    with pytest.raises(ValueError):
        with t.trace("chat"):
            raise ValueError("body")
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_tracing_unit.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3：建立 `tracing.py`（Protocol + NoOp + _safe_cm）**

```python
"""LangFuse 全鏈路 trace 抽象（§6.5 / D-M / DL-011 / DL-026）。

工廠 + fail-open（同 build_cache/build_llm）：無金鑰/建構失敗→NoOpTracer。
trace/span 為 context manager，純計時/記錄、不改 wrapped 回傳值；fail-open 用 _safe_cm
（只抑制 tracer enter/exit 失敗，絕不二次 yield、絕不吞業務例外）。
隱私：LangFuse 只收假名化(hash) user_id（§6.5 可追蹤 + D-M 移除識別資訊），原始 user_id/
學號/query/檢索內容 MUST NOT 入 trace。flush 只在 lifespan shutdown。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import Protocol

logger = logging.getLogger(__name__)


def _pseudonymize(user_id: str | None, salt: str) -> str | None:
    """假名化：HMAC-SHA256(key=salt, msg=user_id) 前 32 hex(128 bits)；None/空 salt→None。
    HMAC + 高熵 salt 防低熵 ID 字典反查；build_tracer MUST 在無 salt 時不啟用 LangFuse。"""
    if not user_id or not salt:
        return None
    return hmac.new(salt.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]


@contextmanager
def _safe_cm(enters: list[Callable[[], AbstractContextManager]]) -> Iterator[None]:
    """進入一串 tracer context manager；只抑制 tracer enter/exit 失敗。

    yield 在 try/except 之外→業務例外照常傳播（絕不二次 yield、絕不吞業務例外）。
    """
    stack = ExitStack()
    try:
        for make in enters:
            stack.enter_context(make())
    except Exception:  # noqa: BLE001  tracer enter 失敗→無追蹤續行
        logger.warning("tracer enter 失敗→改無追蹤續行", exc_info=True)
        try:
            stack.close()   # 回滾已進入的 CM
        except Exception:  # noqa: BLE001  回滾 exit 也失敗→忽略，絕不逃出 _safe_cm
            logger.warning("tracer enter 回滾失敗（忽略）", exc_info=True)
        stack = None  # type: ignore[assignment]
    try:
        yield
    finally:
        if stack is not None:
            try:
                stack.close()
            except Exception:  # noqa: BLE001  tracer exit 失敗→忽略，不影響業務
                logger.warning("tracer exit 失敗（忽略）", exc_info=True)


class Tracer(Protocol):
    def trace(
        self, name: str, *, user_id: str | None = None, metadata: dict | None = None
    ) -> AbstractContextManager[None]: ...

    def span(self, name: str) -> AbstractContextManager[None]: ...

    def score(self, name: str, value: float, *, comment: str | None = None) -> None: ...

    def flush(self) -> None: ...


class NoOpTracer:
    """無 LangFuse 金鑰時的退路：全部 no-op（仍不改回傳、不吞例外）。"""

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
Expected: PASS（3 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/observability/tracing.py backend/tests/test_observability_tracing_unit.py
git commit -m "feat(obs): Tracer/NoOpTracer + _safe_cm/_pseudonymize（fail-open 不二次 yield、假名化）"
```

---

## Task 2：`tracing.py` — `LangfuseTracer` + `build_tracer`（fail-open 建構）+ config salt

**Files:** Modify `tracing.py`, `config.py`; Test `test_observability_tracing_unit.py`

- [ ] **Step 1：config 加 salt**

`config.py`（`sentry_dsn` 附近）加：

```python
    # 假名化 LangFuse user_id 的 salt（D-M：trace 不收原始識別碼，但可追蹤）
    langfuse_user_id_salt: str = ""
```

- [ ] **Step 2：寫失敗測試**（fake client + 建構失敗 + 假名化）

```python
from contextlib import contextmanager

from anatomy_backend.config import Settings
from anatomy_backend.observability.tracing import (
    LangfuseTracer,
    NoOpTracer,
    _pseudonymize,
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
    def __init__(self, *, fail_exit=False, fail_score=False):
        self.attrs = []
        self.spans = []
        self.scores = []
        self.flushed = 0
        self._fail_exit = fail_exit
        self._fail_score = fail_score

    @contextmanager
    def propagate_attributes(self, **kw):
        self.attrs.append(kw)
        yield

    @contextmanager
    def start_as_current_observation(self, *, name):
        self.spans.append(name)
        yield object()
        if self._fail_exit:
            raise RuntimeError("exit boom")

    def score_current_span(self, *, name, value, comment=None):
        if self._fail_score:
            raise RuntimeError("score boom")
        self.scores.append((name, value))

    def flush(self):
        self.flushed += 1


def test_pseudonymize_hmac_stable_not_raw_requires_salt():
    p = _pseudonymize("user-123", salt="high-entropy-salt")
    assert p and p != "user-123" and len(p) == 32          # 128 bits hex
    assert p == _pseudonymize("user-123", salt="high-entropy-salt")    # 穩定
    assert p != _pseudonymize("user-123", salt="other-salt")           # salt 影響
    assert _pseudonymize("user-123", salt="") is None      # 空 salt→None（不可反查）
    assert _pseudonymize(None, salt="s") is None


def test_build_tracer_noop_when_unconfigured():
    assert isinstance(build_tracer(_settings()), NoOpTracer)


def test_build_tracer_langfuse_when_configured(monkeypatch):
    class _FakeLF:
        def __init__(self, **kw):
            pass
    monkeypatch.setattr("langfuse.Langfuse", _FakeLF)
    t = build_tracer(_settings(
        langfuse_host="http://lf:3000", langfuse_public_key="pk", langfuse_secret_key="sk",
        langfuse_user_id_salt="high-entropy-salt"))
    assert isinstance(t, LangfuseTracer)


def test_build_tracer_noop_when_salt_missing(monkeypatch):
    # 有 LangFuse 金鑰但無 salt→拒啟用（NoOp），防低熵 ID 假名反查（Codex#2 v2）
    monkeypatch.setattr("langfuse.Langfuse", lambda **kw: object())
    t = build_tracer(_settings(
        langfuse_host="http://lf:3000", langfuse_public_key="pk", langfuse_secret_key="sk"))
    assert isinstance(t, NoOpTracer)


def test_build_tracer_fail_open_when_construction_raises(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("construct boom")
    monkeypatch.setattr("langfuse.Langfuse", _boom)
    t = build_tracer(_settings(
        langfuse_host="http://lf:3000", langfuse_public_key="pk", langfuse_secret_key="sk",
        langfuse_user_id_salt="s"))
    assert isinstance(t, NoOpTracer)   # 建構失敗→fail-open NoOp，不擋啟動


async def test_langfuse_tracer_pseudonymous_user_id_and_metadata_allowlist():
    fake = _FakeLangfuse()
    t = LangfuseTracer(fake, id_salt="high-entropy-salt")
    with t.trace("chat", user_id="raw-user-9",
                 metadata={"is_followup": False, "query": "肱二頭肌", "user_id": "raw-user-9"}):
        pass
    attrs = fake.attrs[0]
    assert attrs["user_id"] == _pseudonymize("raw-user-9", "high-entropy-salt")
    assert attrs["user_id"] != "raw-user-9"
    md = attrs["metadata"]
    assert md.get("is_followup") is False
    assert "query" not in md and "user_id" not in md   # metadata allowlist 擋掉自由文字/原始 id


async def test_langfuse_tracer_span_and_score_delegate():
    fake = _FakeLangfuse()
    t = LangfuseTracer(fake)
    with t.span("encode"):
        r = 5
    t.score("cache_hit", 1.0)
    t.flush()
    assert r == 5 and "encode" in fake.spans and ("cache_hit", 1.0) in fake.scores
    assert fake.flushed == 1


async def test_langfuse_tracer_exit_failure_is_fail_open():
    # span 結束時 langfuse __exit__ 拋→不得中斷業務、不得 RuntimeError
    t = LangfuseTracer(_FakeLangfuse(fail_exit=True))
    with t.span("encode"):
        r = 1
    assert r == 1


async def test_langfuse_tracer_does_not_swallow_body_exception():
    t = LangfuseTracer(_FakeLangfuse())
    with pytest.raises(ValueError):
        with t.trace("chat", user_id="u"):
            raise ValueError("body")


async def test_langfuse_tracer_score_fail_open():
    LangfuseTracer(_FakeLangfuse(fail_score=True)).score("x", 1.0)  # 不拋


async def test_safe_cm_partial_enter_then_rollback_failure_does_not_escape():
    # Codex#3 v2：第一個 CM enter OK 但 exit 拋；第二個 CM enter 拋→回滾第一個 exit 又拋。
    # _safe_cm 不得讓任何 tracer 例外逃出（業務照常執行）。
    from anatomy_backend.observability.tracing import _safe_cm

    @contextmanager
    def _ok_enter_bad_exit():
        yield
        raise RuntimeError("exit boom")

    def _bad_enter():
        raise RuntimeError("enter boom")

    with _safe_cm([lambda: _ok_enter_bad_exit(), _bad_enter]):
        r = 99
    assert r == 99
```

- [ ] **Step 3：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_tracing_unit.py -k "build_tracer or langfuse or pseudonym" -q`
Expected: FAIL（`ImportError: LangfuseTracer`）

- [ ] **Step 4：實作 `LangfuseTracer` + `build_tracer`（加到 `tracing.py`）**

```python
# trace metadata 只允許這些非自由文字、非識別性欄位進 LangFuse（其餘一律丟棄，Codex#4 v2）。
_ALLOWED_METADATA_KEYS = frozenset({"is_followup", "kb_version", "status", "cache_hit", "lang"})


class LangfuseTracer:
    """包 LangFuse v4 client（OTel）。trace/span 經 _safe_cm fail-open；
    只收假名化 user_id（D-M）+ metadata allowlist；score/flush fail-open。"""

    def __init__(self, client, *, id_salt: str = "") -> None:
        self._lf = client
        self._salt = id_salt

    def trace(
        self, name: str, *, user_id: str | None = None, metadata: dict | None = None
    ) -> AbstractContextManager[None]:
        pseudo = _pseudonymize(user_id, self._salt)   # 只送假名，絕不送原始
        # metadata allowlist：只放核准欄位，擋掉 query/檢索內容/原始 id 等誤傳
        safe_md = {k: v for k, v in (metadata or {}).items() if k in _ALLOWED_METADATA_KEYS}
        attrs = {"user_id": pseudo, "metadata": safe_md}
        return _safe_cm([
            lambda: self._lf.propagate_attributes(**attrs),
            lambda: self._lf.start_as_current_observation(name=name),
        ])

    def span(self, name: str) -> AbstractContextManager[None]:
        return _safe_cm([lambda: self._lf.start_as_current_observation(name=name)])

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
    """依設定回傳 tracer（DL-026）。三金鑰齊備**且** salt 非空才嘗試 LangfuseTracer；
    缺 salt→拒啟用（防假名反查）；import/建構任何失敗→fail-open NoOpTracer（絕不擋啟動）。"""
    host = getattr(settings, "langfuse_host", "")
    pk = getattr(settings, "langfuse_public_key", "")
    sk = getattr(settings, "langfuse_secret_key", "")
    salt = getattr(settings, "langfuse_user_id_salt", "")
    if not (host and pk and sk):
        return NoOpTracer()
    if not salt:
        logger.warning(
            "LangFuse 金鑰齊備但缺 langfuse_user_id_salt→拒啟用（防假名反查），改 NoOpTracer"
        )
        return NoOpTracer()
    try:
        import langfuse

        client = langfuse.Langfuse(
            host=host, public_key=pk, secret_key=sk, flush_at=50, flush_interval=2
        )
        return LangfuseTracer(client, id_salt=salt)
    except Exception:  # noqa: BLE001  SDK 缺失/建構失敗→fail-open
        logger.warning("build_tracer 建構 LangFuse 失敗→NoOpTracer", exc_info=True)
        return NoOpTracer()
```

> 註：`trace`/`span` 回傳 `_safe_cm(...)`（已是 context manager），不可再加 `@contextmanager`。`AbstractContextManager` import 已在 Task 1。

- [ ] **Step 5：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_observability_tracing_unit.py -q`
Expected: PASS（全部）

- [ ] **Step 6：commit**

```bash
git add backend/src/anatomy_backend/observability/tracing.py backend/src/anatomy_backend/config.py backend/tests/test_observability_tracing_unit.py
git commit -m "feat(obs): LangfuseTracer（_safe_cm fail-open、假名化 user_id）+ build_tracer 建構 fail-open + salt config"
```

---

## Task 3：`errors.py` — Sentry `before_send` **default-deny** 脫敏 + `init_sentry`

**Files:** Create `errors.py`; Test `test_observability_errors_unit.py`

- [ ] **Step 1：寫失敗測試**（每個洩漏面注入）

```python
"""Phase 9 Sentry default-deny 脫敏 + init（零外部呼叫；隱私硬紅線）。"""
from __future__ import annotations

from anatomy_backend.config import Settings
from anatomy_backend.observability.errors import init_sentry, scrub_event

_Q = "肱二頭肌的起止點是什麼"   # 代表 query/PHI 文字


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@localhost:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@localhost:5432/anatomy_rag",
        redis_url="redis://localhost:6379/0",
    )
    base.update(over)
    return Settings(**base)


def _has(obj, needle) -> bool:
    """遞迴搜尋值內是否含 needle 子字串。"""
    if isinstance(obj, str):
        return needle in obj
    if isinstance(obj, dict):
        return any(_has(v, needle) for v in obj.values())
    if isinstance(obj, list):
        return any(_has(v, needle) for v in obj)
    return False


def test_scrub_allowlist_drops_all_free_text_and_identifiers():
    ev = {
        "level": "error",
        "exception": {"values": [{"type": "ValueError", "value": f"invalid: {_Q}",
            "stacktrace": {"frames": [{"function": "f", "lineno": 1,
                                       "vars": {"query": _Q}, "context_line": _Q}]}}]},
        "message": f"failed {_Q}",
        "logentry": {"message": _Q},
        "breadcrumbs": {"values": [{"message": _Q, "data": {"q": _Q}}]},
        "request": {"data": {"query": _Q}, "query_string": f"q={_Q}"},
        "extra": {"whatever": _Q},
        "user": {"id": "B12345678", "username": _Q},
        "tags": {"note": _Q},
        "contexts": {"trace": {"trace_id": "abc", "user_id": "B12345678"},
                     "custom": {"note": _Q}},
    }
    out = scrub_event(ev, {})
    assert not _has(out, _Q)            # 任何自由文字皆不得殘留
    assert not _has(out, "B12345678")  # 原始識別碼亦不得殘留
    # 安全結構保留
    assert out["level"] == "error"
    assert out["exception"]["values"][0]["type"] == "ValueError"
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "f"
    # 自由文字/識別性容器整塊丟棄
    for dropped in ("user", "tags", "extra", "breadcrumbs", "request", "message", "logentry"):
        assert dropped not in out
    assert "custom" not in out["contexts"]   # 未核准 context 型別丟棄
    assert "trace" in out["contexts"]        # 核准型別保留（已 key-scrub→user_id 已遮）


def test_scrub_returns_none_on_non_dict():
    assert scrub_event("not a dict", {}) is None


def test_scrub_returns_none_on_error():
    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")
    assert scrub_event(_Boom(), {}) is None


def test_init_sentry_noop_without_dsn():
    assert init_sentry(_settings(sentry_dsn="")) is False


def test_init_sentry_configures_privacy_options(monkeypatch):
    cap = {}
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: cap.update(kw))
    assert init_sentry(_settings(sentry_dsn="https://x@example.invalid/1")) is True
    assert cap["before_send"] is scrub_event
    assert cap["send_default_pii"] is False
    assert cap["max_request_body_size"] == "never"
    assert cap["include_local_variables"] is False


def test_init_sentry_fail_open_on_init_error(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("init boom")
    monkeypatch.setattr("sentry_sdk.init", _boom)
    assert init_sentry(_settings(sentry_dsn="https://x@example.invalid/1")) is False
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_errors_unit.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3：實作 `errors.py`（default-deny）**

```python
"""Sentry 錯誤回報 + before_send 結構 allowlist 脫敏（§6.5 / D-M / DL-026）。

D-M：不做內容層 PHI 攔截，改在外送 Sentry 時**結構 allowlist**——只保留明確核准的非自由文字
欄位，其餘（user/tags/extra/breadcrumbs/request/message/logentry/未知頂層）整塊丟棄；例外僅留
type+stacktrace 結構（去 value/vars/context_line）；contexts 僅留核准型別並再 key-scrub。
空 DSN→no-op；init 失敗→fail-open False；非 dict/出錯→回 None 丟棄該 event。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REDACTED = "[redacted]"

_SENSITIVE_SUBSTRINGS = (
    "query", "prompt", "user_text", "system_prompt", "answer", "completion",
    "retrieved", "context", "snippet", "sources", "docling", "text",
    "user_id", "userid", "student", "學號", "學生",
    "ip", "country", "user_agent", "useragent", "ua", "email",
)


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in _SENSITIVE_SUBSTRINGS)


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: (_REDACTED if _is_sensitive(str(k)) else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


# 結構 allowlist：只保留明確安全（非自由文字、非識別性）的頂層欄位；其餘整塊丟棄。
_ALLOWED_TOP_KEYS = frozenset({
    "event_id", "timestamp", "level", "platform", "logger",
    "sdk", "release", "environment", "server_name", "transaction", "modules",
})
_ALLOWED_CONTEXT_TYPES = frozenset({"runtime", "os", "device", "trace"})
_ALLOWED_FRAME_KEYS = frozenset({"filename", "module", "function", "lineno", "in_app", "abs_path"})


def _safe_exc_value(v: dict) -> dict:
    """例外只保留 type 與 stacktrace 結構（frame 僅位置欄位）——去除 value/vars/context_line。"""
    out: dict = {}
    if isinstance(v.get("type"), str):
        out["type"] = v["type"]
    st = v.get("stacktrace")
    if isinstance(st, dict):
        out["stacktrace"] = {
            "frames": [
                {k: fr[k] for k in _ALLOWED_FRAME_KEYS if k in fr}
                for fr in (st.get("frames") or [])
                if isinstance(fr, dict)
            ]
        }
    return out


def scrub_event(event, hint):
    """Sentry before_send（**結構 allowlist**）：只保留核准頂層欄位，其餘（user/tags/extra/
    breadcrumbs/request/message/logentry/未知）整塊丟棄；例外僅留 type+stacktrace；contexts 僅留
    核准型別並再 key-scrub。非 dict/出錯→回 None 丟棄該 event。"""
    try:
        if not isinstance(event, dict):
            return None
        out = {k: event[k] for k in _ALLOWED_TOP_KEYS if k in event}
        exc = event.get("exception")
        if isinstance(exc, dict):
            out["exception"] = {
                "values": [
                    _safe_exc_value(v) for v in (exc.get("values") or []) if isinstance(v, dict)
                ]
            }
        ctx = event.get("contexts")
        if isinstance(ctx, dict):
            out["contexts"] = {
                t: _scrub(ctx[t]) for t in _ALLOWED_CONTEXT_TYPES if isinstance(ctx.get(t), dict)
            }
        return out
    except Exception:  # noqa: BLE001  寧可丟棄也不洩漏
        logger.warning("Sentry scrub_event 失敗→丟棄該 event", exc_info=True)
        return None


def init_sentry(settings) -> bool:
    """有 DSN 才 init（default-deny 脫敏）；無 DSN→no-op False；init 失敗→fail-open False。"""
    dsn = getattr(settings, "sentry_dsn", "")
    if not dsn:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            before_send=scrub_event,
            send_default_pii=False,
            max_request_body_size="never",
            include_local_variables=False,   # 不捕捉 frame 區域變數（防 query/user_text 入 stacktrace）
            traces_sample_rate=0.0,          # 只收錯誤，trace 由 LangFuse 負責
        )
        return True
    except Exception:  # noqa: BLE001  init 失敗→fail-open，不擋啟動
        logger.warning("init_sentry 失敗→停用 Sentry 續行", exc_info=True)
        return False
```

- [ ] **Step 4：跑測試確認通過**

Run: `uv run --no-sync pytest backend/tests/test_observability_errors_unit.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/observability/errors.py backend/tests/test_observability_errors_unit.py
git commit -m "feat(obs): Sentry default-deny scrub_event（移除所有自由文字面）+ init fail-open + include_local_variables=False"
```

---

## Task 4：`alerts.py` — §7.5 告警**條件邏輯 + 介面**（operational 延後）

**Files:** Create `alerts.py`; Test `test_observability_alerts_unit.py`

> **誠實降級（Codex#6）**：本 task 只交付告警**條件評估邏輯 + notifier 介面**。metrics 來源彙整、連續時間窗排程、真實 Slack/email 通知管道**未**接線（DL-026 明示延後 ops）；故 §7.5 MUST 告警在 v1 **尚未 operational**。

- [ ] **Step 1：寫失敗測試**

```python
"""Phase 9 告警條件 + notifier（邏輯+介面；operational 延後）。"""
from __future__ import annotations

from anatomy_backend.observability.alerts import LogNotifier, evaluate_alerts


def test_p95_latency_breach_triggers_must():
    a = evaluate_alerts({"p95_latency_s": 9.0, "p95_breach_minutes": 10})
    assert any(x.name == "p95_latency" and x.severity == "must" for x in a)


def test_p95_below_threshold_or_short_no_trigger():
    assert evaluate_alerts({"p95_latency_s": 9.0, "p95_breach_minutes": 9}) == []
    assert evaluate_alerts({"p95_latency_s": 7.9, "p95_breach_minutes": 30}) == []


def test_model_error_rate_triggers():
    assert "model_error_rate" in {x.name for x in
        evaluate_alerts({"model_error_rate": 0.06, "model_error_minutes": 5})}


def test_usage_ratio_triggers_at_80pct():
    assert "usage_ratio" in {x.name for x in evaluate_alerts({"usage_ratio": 0.80})}
    assert evaluate_alerts({"usage_ratio": 0.79}) == []


def test_citation_fail_is_should_severity():
    a = evaluate_alerts({"citation_fail_rate": 0.11, "citation_fail_minutes": 30})
    assert any(x.name == "citation_fail_rate" and x.severity == "should" for x in a)


def test_empty_metrics_no_alerts():
    assert evaluate_alerts({}) == []


def test_log_notifier_does_not_raise():
    LogNotifier().notify(evaluate_alerts({"usage_ratio": 0.9})[0])
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_observability_alerts_unit.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3：實作 `alerts.py`**

```python
"""§7.5 告警條件邏輯 + 可插拔 notifier（DL-026）。

v1 只交付條件評估與介面：metrics 來源彙整、連續時間窗排程、真實 Slack/email webhook
為 ops 後續（DL-011 Prometheus 延後）。預設 LogNotifier（log）。
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
    """依 §7.5 條件回傳觸發告警。metrics 由上游彙整提供（v1 未排程，邏輯先就位）。"""
    out: list[Alert] = []
    if metrics.get("p95_latency_s", 0) > 8 and metrics.get("p95_breach_minutes", 0) >= 10:
        out.append(Alert("p95_latency", "must", "p95 latency > 8s 連續 ≥10 分鐘", ("slack",)))
    if metrics.get("model_error_rate", 0) > 0.05 and metrics.get("model_error_minutes", 0) >= 5:
        out.append(Alert("model_error_rate", "must", "模型錯誤率 > 5% 連續 ≥5 分鐘",
                         ("slack", "email")))
    if metrics.get("usage_ratio", 0) >= 0.80:
        out.append(Alert("usage_ratio", "must", "RPM/TPM 用量達 80%", ("slack",)))
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
Expected: PASS（7 passed）

- [ ] **Step 5：commit**

```bash
git add backend/src/anatomy_backend/observability/alerts.py backend/tests/test_observability_alerts_unit.py
git commit -m "feat(obs): §7.5 evaluate_alerts 條件邏輯 + Notifier/LogNotifier（operational 延後 ops）"
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

Run: `uv run --no-sync python -c "from anatomy_backend.observability import build_tracer, init_sentry, evaluate_alerts, scrub_event; print('OK')"`
Expected: `OK`

- [ ] **Step 3：commit**

```bash
git add backend/src/anatomy_backend/observability/__init__.py
git commit -m "feat(obs): observability 套件 __init__ 匯出"
```

---

## Task 6：接線 `chat.py`（tracer 欄位 + trace/span/score）+ recording 測試

**Files:** Modify `chat.py`; Test `test_api_chat_sse_unit.py`

- [ ] **Step 1：寫失敗測試**（recording tracer；mirror `_make_chat_deps`）

```python
async def test_chat_records_trace_spans_and_citation_score():
    from contextlib import contextmanager

    from anatomy_backend.api.chat import chat_event_stream
    from anatomy_backend.api.schemas import normalize_chat

    class _RecordingTracer:
        def __init__(self):
            self.spans = []
            self.scores = []
            self.trace_user_id = "UNSET"
        @contextmanager
        def trace(self, name, *, user_id=None, metadata=None):
            self.spans.append(("trace", name)); self.trace_user_id = user_id; yield
        @contextmanager
        def span(self, name):
            self.spans.append(("span", name)); yield
        def score(self, name, value, *, comment=None):
            self.scores.append((name, value))
        def flush(self):
            pass

    tracer = _RecordingTracer()
    normalized = normalize_chat({"messages": [{"role": "user", "content": "肱二頭肌的起止點"}]})
    deps = _make_chat_deps(tracer=tracer)
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
    assert {"encode", "retrieve", "llm"} <= span_names
    assert any(n == "citation_verified" for n, _ in tracer.scores)
    # chat 把原始 user_id 交給 tracer.trace（由 tracer 端假名化；chat 不負責 hash）
    assert tracer.trace_user_id == user.user_id
```

> 工人備註：`_make_chat_deps(cache=None)` 擴成 `(cache=None, tracer=None)`，內部 `tracer or NoOpTracer()`。retrieve fake 須回非空 grounded 引文（既有 `_golden_result()` 滿足）。

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_chat_sse_unit.py::test_chat_records_trace_spans_and_citation_score -q`
Expected: FAIL（`ChatDeps` 無 tracer / `_make_chat_deps` 不收 tracer）

- [ ] **Step 3：`ChatDeps` 加 tracer**

`chat.py` import 改：`from dataclasses import dataclass, field`；加 `from anatomy_backend.observability.tracing import NoOpTracer, Tracer`。
`ChatDeps` 在 `top_n: int = 3` 後加：

```python
    tracer: Tracer = field(default_factory=NoOpTracer)
```

- [ ] **Step 4：包 trace/span/score**

把 `chat_event_stream` 主體（`kb = deps.kb_version` 之後全部）包進：

```python
    kb = deps.kb_version
    with deps.tracer.trace(
        "chat", user_id=user.user_id,
        metadata={"is_followup": normalized.is_followup, "kb_version": kb},
    ):
        # ...（原 Step1 快取 → ... → Step9 全部主體縮排進此 with；所有 return 留在 with 內）...
```

加（不改任何 yield）：
- 快取命中分支：`deps.tracer.score("cache_hit", 1.0)`。
- `with deps.tracer.span("encode"):` 包 encode。
- `with deps.tracer.span("retrieve"):` 包 retrieve + build_citations。
- `with deps.tracer.span("llm"):` 包 `async for delta in deps.llm.stream_complete(...)`。
- 驗證後：`deps.tracer.score("cache_hit", 0.0)` 與 `deps.tracer.score("citation_verified", 1.0 if verification.all_grounded else 0.0)`。

> 原始 `user.user_id` 交給 `tracer.trace`，**假名化由 LangfuseTracer 端負責**（NoOp 端忽略）——故原始 id 不會送往 LangFuse（Task 2 已驗）。SSE 事件位元組不變（NoOp 預設；golden 測試守）。

- [ ] **Step 5：跑測試確認通過 + golden 不破**

Run: `uv run --no-sync pytest backend/tests/test_api_chat_sse_unit.py backend/tests/test_api_chat_unit.py -q`
Expected: PASS（含新測試 + golden 位元組 + metadata_filter）

- [ ] **Step 6：commit**

```bash
git add backend/src/anatomy_backend/api/chat.py backend/tests/test_api_chat_sse_unit.py
git commit -m "feat(obs): chat 接線 trace/span/score（tracer 預設 NoOp；golden 不變；原始 user_id 不入 LangFuse）"
```

---

## Task 7：接線 `main.py`（init_sentry + build_tracer + flush）+ `_spawn` context 隔離

**Files:** Modify `main.py`; Test `test_api_chat_sse_unit.py`

- [ ] **Step 1：寫失敗測試**（spawn context 隔離；真 create_task）

加到 `test_api_chat_sse_unit.py`：

```python
async def test_spawn_isolates_contextvars():
    """production _spawn 用乾淨 contextvars.Context()，背景任務不繼承呼叫端 contextvar（防 OTel span 洩漏）。"""
    import asyncio
    import contextvars

    from anatomy_backend.api.main import _spawn

    cv = contextvars.ContextVar("probe", default="default")
    cv.set("parent-value")
    seen = {}

    async def _job():
        seen["v"] = cv.get()

    _spawn(_job())
    await asyncio.sleep(0.05)
    assert seen["v"] == "default"   # 未繼承 parent-value → context 已隔離


async def test_flush_tracer_does_not_raise_on_error():
    from anatomy_backend.api.main import flush_tracer

    class _BoomTracer:
        def flush(self):
            raise RuntimeError("flush boom")

    await flush_tracer(_BoomTracer(), timeout=1.0)   # 不拋（fail-open）


async def test_flush_tracer_bounded_by_timeout():
    # Codex#5 v2：flush 阻塞時，flush_tracer 仍在 timeout 附近返回，不卡 shutdown。
    import asyncio
    import time

    from anatomy_backend.api.main import flush_tracer

    class _StuckTracer:
        def flush(self):
            time.sleep(0.5)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await flush_tracer(_StuckTracer(), timeout=0.05)
    assert loop.time() - start < 0.4   # 未等滿 0.5s
```

- [ ] **Step 2：跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_api_chat_sse_unit.py::test_spawn_isolates_contextvars -q`
Expected: FAIL（目前 `_spawn` 用預設 context，背景任務繼承 `parent-value`）

- [ ] **Step 3：`_spawn` 用乾淨 context**

`main.py` 頂部加 `import contextvars`；`_spawn` 改：

```python
def _spawn(coro) -> None:
    """production spawn：create_task（乾淨 context，防 OTel span 等 contextvar 洩漏）+ 保留參考 + 記錯。"""
    t = asyncio.create_task(coro, context=contextvars.Context())
    _BG.add(t)
```

- [ ] **Step 4：lifespan 裝 Sentry + tracer + flush**

lifespan 早段（建 settings 後）加：

```python
    from anatomy_backend.observability import build_tracer, init_sentry

    init_sentry(settings)            # 無 DSN/失敗→no-op False
    tracer = build_tracer(settings)  # 無金鑰/建構失敗→NoOpTracer
```

`_build_chat_deps` 的 `ChatDeps(...)` 加 `tracer=tracer`；`app.state.tracer = tracer`。

並在 `main.py` 模組層（`_spawn` 附近）加**有界 flush** helper（Codex#5 v2：同步 flush 可能阻塞，須 to_thread + timeout，不可卡事件迴圈/容器終止）：

```python
async def flush_tracer(tracer, *, timeout: float = 2.0) -> None:
    """有界 flush：to_thread + wait_for；逾時(TimeoutError⊂Exception)/失敗→記錄續行。"""
    try:
        await asyncio.wait_for(asyncio.to_thread(tracer.flush), timeout=timeout)
    except Exception:  # noqa: BLE001
        logger.warning("tracer.flush 逾時/失敗（忽略），繼續關閉", exc_info=True)
```

cleanup 段（`await pool.close()` 旁）改呼叫：`await flush_tracer(tracer)`（**不**直接 `tracer.flush()`）。

- [ ] **Step 5：跑測試確認通過 + 不破**

Run: `uv run --no-sync pytest backend/tests/ -k "spawn or lifespan or chat or main or cache" -q`
Expected: PASS

- [ ] **Step 6：commit**

```bash
git add backend/src/anatomy_backend/api/main.py backend/tests/test_api_chat_sse_unit.py
git commit -m "feat(obs): main lifespan init_sentry+build_tracer+flush；_spawn 乾淨 context 隔離（防 span 洩漏）"
```

---

## Task 8：DL-026 + 全套件回歸 + lint

**Files:** Modify `docs/decisions.md`

- [ ] **Step 1：追加 DL-026**

```markdown

## DL-026: 觀測性 v1＝tracer 抽象 + fail-open + Sentry default-deny 脫敏 + 假名化 trace id；告警邏輯先行、operational 延後

- **狀態**：APPROVED　**提案者**：main Claude（Phase 9，含 Codex 對抗式審查修訂）　**日期**：2026-06-14　**裁決者**：專案負責人（mock-first + metrics 走 LangFuse）
- **影響檔案**：ARCHITECTURE.md §6.5、§7.5；`backend/.../observability/*`、`api/main.py`、`api/chat.py`、`config.py`

### 背景
DL-011 定觀測先 LangFuse+Sentry、Prometheus 延後；D-M 定改 Sentry/LangFuse 脫敏並 strip user_id；§6.5 又要求「每筆 trace 含 user_id 以可追蹤」。Phase 9 落地需在「無外部 standup」下交付且可測，並化解 §6.5 與 D-M 的張力。

### 提案（與 DL-011/D-M 一致；化解 §6.5×D-M）
1. **mock-first + fail-open**：`build_tracer` 無金鑰/缺 salt/import/建構失敗→`NoOpTracer`；`init_sentry` 無 DSN/init 失敗→False；tracer trace/span/score/flush 任一例外不中斷 `/chat`（`_safe_cm` 只抑制 tracer enter/exit、不二次 yield、不吞業務例外，含 enter 回滾再失敗）；**shutdown flush 有界**（to_thread + wait_for timeout）不卡關閉。CI 零外部呼叫。
2. **假名化 trace id 化解 §6.5×D-M**：LangFuse 只收 **HMAC-SHA256(key=高熵 salt, msg=user_id)** 假名（128 bits；§6.5「可追蹤」），**缺 salt 則不啟用 LangFuse**（防低熵 ID 字典反查）；trace metadata 採 **allowlist**；**MUST NOT** 收原始 user_id/學號/query/檢索內容（D-M）。
3. **Sentry 結構 allowlist 脫敏（D-M）**：`send_default_pii=False` + `max_request_body_size="never"` + `include_local_variables=False` + `before_send` **只保留核准頂層欄位 + 例外 type/stacktrace 結構 + 核准 contexts 型別**，其餘（user/tags/extra/breadcrumbs/request/message/logentry/未知）整塊丟棄；非 dict/出錯→回 None 丟棄 event。
4. **背景任務 context 隔離**：`_spawn` 用乾淨 `contextvars.Context()`，detached log/cache 任務不繼承 OTel span（防錯置/洩漏）。
5. **metrics 走 LangFuse score + 結構化 log**（cache_hit/citation_verified/latency）；**Prometheus/Grafana 維持延後（DL-011）**。
6. **告警誠實降級**：本 phase 只交付 `evaluate_alerts` 條件邏輯 + `Notifier` 介面（預設 `LogNotifier`）。**§7.5 MUST 告警在 v1 尚未 operational**——metrics 來源彙整、連續時間窗排程、真實 Slack/email 管道與 owner **延後 ops**（新連線，先問）。
7. **不新增套件**：`langfuse`/`sentry-sdk` 已在 deps。

### 後果
- 設 LangFuse/Sentry 憑證才真正送出；未設則靜默 no-op（log 仍在）。
- §7.5 告警目前僅邏輯+介面；接真實 metrics 來源、排程與通知管道屬部署/ops（後續 phase 或 runbook）。
- LangFuse 端只能以假名分群追蹤；要對應回原始學號須在受控環境以同 salt 重算 hash。
```

- [ ] **Step 2：ruff（勿 format）**

Run: `uv run --no-sync ruff check backend/src/anatomy_backend/observability backend/src/anatomy_backend/api/chat.py backend/src/anatomy_backend/api/main.py backend/src/anatomy_backend/config.py backend/tests/test_observability_tracing_unit.py backend/tests/test_observability_errors_unit.py backend/tests/test_observability_alerts_unit.py backend/tests/test_api_chat_sse_unit.py`
Expected: `All checks passed!`（import 排序用 `ruff check --fix`；**勿** `ruff format`）

- [ ] **Step 3：全 backend 回歸**

Run: `uv run --no-sync pytest backend/tests -q`
Expected: 全綠（整合測試無 redis→skip）

- [ ] **Step 4：commit**

```bash
git add docs/decisions.md
git commit -m "docs(decisions): DL-026 觀測性 v1（fail-open+假名化+default-deny 脫敏+context 隔離；告警 operational 延後）"
```

---

## Self-Review（spec + Codex 對照）

| spec / 驗收 / Codex finding | 對應 task |
|---|---|
| LangFuse 全鏈路 trace（span 不改回傳） | Task 1/2 + Task 6 |
| **[Codex#1 critical] Sentry default-deny 移除所有自由文字面** | Task 3（exception/message/breadcrumb/request/extra 注入測試） |
| **[Codex#2] contextmanager 不二次 yield、不吞業務例外** | Task 1（_safe_cm）+ Task 2（exit/body-exc 測試） |
| **[Codex#3] 背景任務 context 隔離** | Task 7（_spawn + 真 create_task 測試） |
| **[Codex#4] LangFuse 只收假名化 user_id** | Task 2（_pseudonymize + 斷言無原始 id）+ DL-026#2 |
| **[Codex#5] build_tracer/init_sentry fail-open** | Task 2/3（建構/init 拋錯測試） |
| **[Codex#6] 告警誠實降級（operational 延後）** | Task 4 + DL-026#6 |
| Sentry before_send 注入 query 斷言被遮蔽 | Task 3 |
| 告警條件單元測試 | Task 4 |
| LangFuse/Sentry 缺席 fail-open | Task 2/3 + Task 7 |
| trace 含（假名）user_id 但原始不送 LLM | Task 6 + 既有 forbidden_identifiers |
| SSE golden 不變（NoOp 預設） | Task 6 Step 5 |
| 零新套件、零外部呼叫 | 全程 |
| decisions.md DL-026 | Task 8 |

**Placeholder scan：** 無 TODO/TBD；工人備註（Task 6 `_make_chat_deps` 加 tracer）為明確指示。
**Type 一致性：** `Tracer.trace(name,*,user_id=None,metadata=None)`/`span(name)`/`score(name,value,*,comment=None)`/`flush()`、`_safe_cm(enters)`、`_pseudonymize(user_id,salt="")`、`build_tracer(settings)`、`LangfuseTracer(client,*,id_salt="")`、`scrub_event(event,hint)`/`init_sentry(settings)->bool`、`evaluate_alerts(metrics)->list[Alert]`、`ChatDeps.tracer`、`_spawn(coro)` 跨 task 一致。
