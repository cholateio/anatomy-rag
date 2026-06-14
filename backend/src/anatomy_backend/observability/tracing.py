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
import sys
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
    except BaseException:
        # 業務例外 / GeneratorExit(取消)：把錯誤轉發給 tracer span（標記 error，Codex#medium v3），
        # 抑制 tracer exit 失敗，再重新拋出原始例外（絕不吞）。
        if stack is not None:
            try:
                stack.__exit__(*sys.exc_info())
            except Exception:  # noqa: BLE001  tracer exit(error) 失敗→忽略
                logger.warning("tracer exit(error) 失敗（忽略）", exc_info=True)
            stack = None  # type: ignore[assignment]
        raise
    else:
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


# trace metadata 只允許這些 key（Codex#4 v2）；且 value 必須是 primitive 或短 enum（Codex#high v3）。
_ALLOWED_METADATA_KEYS = frozenset({"is_followup", "kb_version", "status", "cache_hit", "lang"})
# score name 固定 allowlist（Codex#high v3）；comment 一律不外送（防自由文字）。
_ALLOWED_SCORE_NAMES = frozenset({"cache_hit", "citation_verified", "latency_ms", "status_ok"})
_MAX_METADATA_STR = 16  # 短 enum 上限；query/檢索內容必更長→擋下自由文字偽裝成核准 key


def _safe_metadata_value(v) -> bool:
    """只允許 bool/int/float 或 ≤16 字短字串（enum）；擋下偽裝成核准 key 的自由文字/原始 id。"""
    if isinstance(v, (bool, int, float)):
        return True
    return isinstance(v, str) and len(v) <= _MAX_METADATA_STR


class LangfuseTracer:
    """包 LangFuse v4 client（OTel）。trace/span 經 _safe_cm fail-open；
    只收假名化 user_id（D-M）+ metadata key/value allowlist + score name allowlist；score/flush fail-open。"""

    def __init__(self, client, *, id_salt: str = "") -> None:
        self._lf = client
        self._salt = id_salt

    def trace(
        self, name: str, *, user_id: str | None = None, metadata: dict | None = None
    ) -> AbstractContextManager[None]:
        pseudo = _pseudonymize(user_id, self._salt)   # 只送假名，絕不送原始
        # metadata key + value allowlist：擋掉 query/檢索內容/原始 id（含偽裝成核准 key 的長字串）
        safe_md = {
            k: v for k, v in (metadata or {}).items()
            if k in _ALLOWED_METADATA_KEYS and _safe_metadata_value(v)
        }
        attrs = {"user_id": pseudo, "metadata": safe_md}
        return _safe_cm([
            lambda: self._lf.propagate_attributes(**attrs),
            lambda: self._lf.start_as_current_observation(name=name),
        ])

    def span(self, name: str) -> AbstractContextManager[None]:
        return _safe_cm([lambda: self._lf.start_as_current_observation(name=name)])

    def score(self, name: str, value: float, *, comment: str | None = None) -> None:
        # score name allowlist；comment 一律丟棄（不外送自由文字，Codex#high v3）
        if name not in _ALLOWED_SCORE_NAMES:
            logger.warning("LangfuseTracer.score 非 allowlist name=%r→丟棄", name)
            return
        try:
            self._lf.score_current_span(name=name, value=value)
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
