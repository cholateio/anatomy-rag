import math

from anatomy_backend.api.ratelimit import RateLimiter, RateLimitResult


class _FakeScript:
    """模擬 register_script 回傳之 async callable：回 [allowed, retry_ms]。
    F4 override: 三桶 all-or-nothing 單一腳本呼叫。"""

    def __init__(self, results):  # results: list[[allowed, retry_ms]] 依呼叫順序
        self._results = list(results)
        self.calls = []

    async def __call__(self, keys, args):
        self.calls.append((keys, args))
        return self._results.pop(0)


def _limiter(script, **over):
    cfg = dict(per_min=15, per_day=300, global_rps=20)
    cfg.update(over)
    return RateLimiter(script=script, **cfg)


async def test_allows_when_all_buckets_ok():
    # single atomic call returns [1, 0] → allowed; only ONE script call made
    s = _FakeScript([[1, 0]])
    r = await _limiter(s).check(user_id="u1", is_admin=False)
    assert r == RateLimitResult(allowed=True, retry_after=0)
    assert len(s.calls) == 1  # all-or-nothing: single script call


async def test_denies_and_returns_retry_after_seconds():
    # single call returns [0, retry_ms] → denied; retry_after=ceil(retry_ms/1000)
    s = _FakeScript([[0, 2400]])
    r = await _limiter(s).check(user_id="u1", is_admin=False)
    assert r.allowed is False
    assert r.retry_after == math.ceil(2400 / 1000)  # == 3
    assert len(s.calls) == 1  # still only one call


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


async def test_single_call_passes_three_keys_and_eight_args():
    """確認單次呼叫傳 3 個 keys（user-min, user-day, global）+ 8 個 args。"""
    s = _FakeScript([[1, 0]])
    await _limiter(s).check(user_id="u1", is_admin=False, now_ms=1_000_000)
    keys, args = s.calls[0]
    assert keys == ["rl:min:u1", "rl:day:u1", "rl:global"]
    # args: cap_min, cap_day, cap_global, rate_min, rate_day, rate_global, now_ms, cost
    assert len(args) == 8
    assert args[6] == 1_000_000  # now_ms
    assert args[7] == 1  # cost=1


async def test_retry_after_zero_when_allowed():
    s = _FakeScript([[1, 0]])
    r = await _limiter(s).check(user_id="u1", is_admin=False)
    assert r.retry_after == 0
