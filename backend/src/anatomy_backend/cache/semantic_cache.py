"""語意快取（§6.4 / DL-004 / DL-012 / DL-025）。

v1 預設＝exact-normalized-query：把『正規化 query（NFKC→trim→casefold→摺疊空白）+ canonical metadata_filter』
雜湊成決定性 key（namespace 含 kb_version），zero embedding 套件、決定性、誤命中極低、命中
<30ms（§1.6）。語意向量比對為後續 config 開關（fastembed，torch-free；cache_mode="semantic"）。

硬性規則：
- 只快取已驗證答案：set(verified=False) 一律拒寫（信任邊界＝chat.py verify_citations, DL-012）。
- 完整隔離：key 納入 kb_version + metadata_filter；命中時再校驗 envelope kb_version。
- 本地 lookup：本模組 MUST NOT import openai（DL-012；CI grep 守門）。
- Redis fail-open（§1.8）：get→miss、set/clear→no-op（含序列化/mid-scan 失敗），絕不中斷 /chat。
誤命中：決定性正規化會把『正規化等價字串』併為同 key（如 casefold 後 US/us）；非語意比對故無向量誤命中。
即便誤命中，回的仍是已驗證有引文的答案（安全網守住）。醫學術語 precision 語料列 Phase 11 eval gate。
DL-021 追問不查/不寫由 chat.py 控制，本類保持追問無關。
"""
from __future__ import annotations

import hashlib
import json
import logging
import unicodedata

from anatomy_backend.cache.base import CachedAnswer

logger = logging.getLogger(__name__)


class SemanticCache:
    """exact-normalized-query 快取，實作 CacheProtocol（get/set）。"""

    _SCHEMA = 1
    _DEFAULT_PREFIX = "semcache"

    def __init__(self, redis, *, ttl_seconds: int, key_prefix: str | None = None) -> None:
        self._redis = redis
        self._ttl = int(ttl_seconds)
        self._prefix = key_prefix or self._DEFAULT_PREFIX

    @staticmethod
    def normalize_query(query: str) -> str:
        """決定性正規化：NFKC（全→半形/相容字）→ strip → casefold → 摺疊空白。"""
        s = unicodedata.normalize("NFKC", query)
        s = s.strip().casefold()
        s = " ".join(s.split())
        return s

    @staticmethod
    def _canonical_filter(metadata_filter: dict | None) -> str:
        """canonical 形式：空→""；否則 sort_keys 的精簡 JSON（順序不敏感、決定性）。"""
        if not metadata_filter:
            return ""
        return json.dumps(
            metadata_filter, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @staticmethod
    def _sources_ok(sources) -> bool:
        """sources 須為 list；每個 present 元素須為 dict 且具 book_title(非空 str)+page(int)。
        允許空 list（教材查無此項類已驗證答案無 citation）。"""
        if not isinstance(sources, list):
            return False
        for s in sources:
            if not isinstance(s, dict):
                return False
            bt = s.get("book_title")
            pg = s.get("page")
            if not isinstance(bt, str) or not bt:
                return False
            if not isinstance(pg, int) or isinstance(pg, bool):
                return False
        return True

    def _key(self, query: str, kb_version: int, metadata_filter: dict | None = None) -> str:
        norm = self.normalize_query(query)
        canon = self._canonical_filter(metadata_filter)
        digest = hashlib.sha256(f"{norm}\x00{canon}".encode("utf-8")).hexdigest()
        return f"{self._prefix}:kb{kb_version}:{digest}"

    async def get(
        self, query: str, kb_version: int, metadata_filter: dict | None = None
    ) -> CachedAnswer | None:
        try:
            raw = await self._redis.get(self._key(query, kb_version, metadata_filter))
        except Exception:  # noqa: BLE001  fail-open（§1.8）
            logger.warning("SemanticCache.get Redis 失敗→miss", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)   # json.loads 接受 bytes/str
        except (ValueError, TypeError):
            logger.warning("SemanticCache 損壞值→miss", exc_info=True)
            return None
        if not isinstance(payload, dict):
            return None
        # 防禦性校驗：schema / kb_version / verified / source 形狀，任一不符→miss
        if payload.get("v") != self._SCHEMA:
            return None
        if payload.get("kb_version") != kb_version:
            return None
        if not payload.get("verified"):
            return None
        answer = payload.get("answer")
        sources = payload.get("sources")
        if not isinstance(answer, str) or not self._sources_ok(sources):
            return None
        return CachedAnswer(answer=answer, sources=sources)

    async def set(
        self,
        query: str,
        answer: str,
        sources: list[dict],
        kb_version: int,
        *,
        verified: bool,
        metadata_filter: dict | None = None,
    ) -> None:
        # MUST：只快取已驗證答案（信任邊界＝chat.py verify_citations, DL-012）。
        if not verified:
            return
        # 結構防禦（Codex#2/#5）：拒絕非字串答案 / 損壞 source 形狀。
        if not isinstance(answer, str) or not self._sources_ok(sources):
            logger.warning("SemanticCache.set 拒絕：answer/sources 形狀不合法（防損壞引文入快取）")
            return
        try:
            payload = {
                "v": self._SCHEMA,
                "answer": answer,
                "sources": sources,
                "kb_version": kb_version,
                "verified": True,
            }
            value = json.dumps(payload, ensure_ascii=False).encode("utf-8")  # 序列化在 try 內
            await self._redis.set(self._key(query, kb_version, metadata_filter), value, ex=self._ttl)
        except Exception:  # noqa: BLE001  fail-open（含序列化失敗）
            logger.warning("SemanticCache.set Redis/序列化 失敗→no-op", exc_info=True)

    async def clear_kb_version(self, kb_version: int) -> None:
        """清空指定 kb_version 的快取（§6.6 版本切換之記憶體回收）。

        以 namespace pattern SCAN + UNLINK，只清該版本——不用 FLUSHDB（避免誤清同
        Redis 的限流桶）。非原子；但版本隔離正確性來自 namespace+active-only 讀取，
        殘留 key 不致錯答（見本 task 設計論證）。fail-open。
        """
        pattern = f"{self._prefix}:kb{kb_version}:*"
        try:
            batch: list = []
            async for key in self._redis.scan_iter(match=pattern, count=500):
                batch.append(key)
                if len(batch) >= 500:
                    await self._redis.unlink(*batch)
                    batch = []
            if batch:
                await self._redis.unlink(*batch)
        except Exception:  # noqa: BLE001  fail-open
            logger.warning("SemanticCache.clear_kb_version Redis 失敗（部分清除）", exc_info=True)
