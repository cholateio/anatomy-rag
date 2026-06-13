"""Phase 7 SemanticCache 單元測試（exact-normalized-query；手刻 _FakeRedis，無真 Redis）。"""
from __future__ import annotations

import fnmatch
import json

import pytest

from anatomy_backend.config import Settings


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@localhost:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@localhost:5432/anatomy_rag",
        redis_url="redis://localhost:6379/0",
    )
    base.update(over)
    return Settings(**base)


def test_cache_config_defaults():
    s = _settings()
    assert s.cache_enabled is True
    assert s.cache_mode == "exact"
    assert s.cache_ttl_seconds == 1209600  # 14 天（7–30 天區間）
