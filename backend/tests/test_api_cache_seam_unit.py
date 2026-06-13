"""Unit tests for cache seam — CacheProtocol, CachedAnswer, NoOpCache, build_cache."""
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
    # 舊 no-arg 形狀（無 redis_client）仍回 NoOp 退路
    assert isinstance(build_cache(SimpleNamespace()), NoOpCache)
