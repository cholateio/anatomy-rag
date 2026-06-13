"""Phase 7 SemanticCache 真 Redis 整合測試。

需 REDIS_URL（CI db-integration job 已設 redis://localhost:6379/0；本機用 compose redis）。
無 REDIS_URL → 自動 skip。每個 cache 用唯一 key_prefix 隔離，測試後清掉自身 namespace。
"""
from __future__ import annotations

import os
import uuid

import pytest

from anatomy_backend.cache.semantic_cache import SemanticCache

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("REDIS_URL"),
        reason="需真 Redis：設 REDIS_URL（CI db-integration 已設；本機用 compose redis）",
    ),
]

SRC = [{"book_title": "Gray's", "edition": "42e", "page": 12, "figure": None,
        "image_url": "http://x/y.png", "snippet": "...", "score": 0.9}]


@pytest.fixture
async def redis_client():
    import redis.asyncio as aioredis

    client = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def cache(redis_client):
    prefix = f"semcache_test_{uuid.uuid4().hex}"
    c = SemanticCache(redis_client, ttl_seconds=60, key_prefix=prefix)
    try:
        yield c
    finally:
        async for k in redis_client.scan_iter(match=f"{prefix}:*", count=500):
            await redis_client.unlink(k)


async def test_real_redis_set_get_hit(cache):
    await cache.set("肱二頭肌起止點", "起於...[Gray's, p.12]", SRC, 1, verified=True)
    hit = await cache.get(" 肱二頭肌起止點 ", 1)
    assert hit is not None and hit.answer == "起於...[Gray's, p.12]" and hit.sources == SRC


async def test_real_redis_cross_kb_miss(cache):
    await cache.set("q", "a", SRC, 1, verified=True)
    assert await cache.get("q", 2) is None


async def test_real_redis_cross_metadata_filter_miss(cache):
    await cache.set("q", "musc", SRC, 1, verified=True, metadata_filter={"anatomy_system": "musculoskeletal"})
    assert await cache.get("q", 1, {"anatomy_system": "nervous"}) is None
    assert await cache.get("q", 1, None) is None
    hit = await cache.get("q", 1, {"anatomy_system": "musculoskeletal"})
    assert hit is not None and hit.answer == "musc"


async def test_real_redis_unverified_not_written(cache, redis_client):
    await cache.set("q", "a", SRC, 1, verified=False)
    assert await cache.get("q", 1) is None
    assert await redis_client.get(cache._key("q", 1)) is None


async def test_real_redis_ttl_applied(cache, redis_client):
    await cache.set("q", "a", SRC, 1, verified=True)
    ttl = await redis_client.ttl(cache._key("q", 1))
    assert 0 < ttl <= 60


async def test_real_redis_clear_kb_version(cache):
    await cache.set("q1", "a1", SRC, 1, verified=True)
    await cache.set("q2", "a2", SRC, 2, verified=True)
    await cache.clear_kb_version(1)
    assert await cache.get("q1", 1) is None
    assert await cache.get("q2", 2) is not None
