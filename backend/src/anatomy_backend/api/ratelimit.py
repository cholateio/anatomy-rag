"""Redis atomic token-bucket 限流（§6.8 / DL-022）。

三桶（user-min, user-day, global）**單一 atomic Lua call，all-or-nothing**：
先 refill 並檢查全部三桶；**全部有餘裕才一起扣**，任一不足則均不扣，
回 allowed=0 + 三桶中最大 retry_ms（F4/H override）。

per-user/分 + per-user/日 + global 三桶，任一拒即 429。admin（教師）豁免。
Redis 故障 fail-open（不鎖死使用者，記 log）。
高頻拒絕不逐筆寫 DB（DL-022）——僅 Redis 計數。
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# KEYS=3 桶；ARGV: cap1,cap2,cap3, rate1,rate2,rate3, now_ms, cost
# all-or-nothing: 全部 refill+check；全部 >= cost 才一起扣，否則均不扣，回最大 retry_ms。
TOKEN_BUCKET_LUA = """
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
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int  # 秒（整數）


class RateLimiter:
    def __init__(self, *, script, per_min: int, per_day: int, global_rps: int) -> None:
        self._script = script  # redis.asyncio register_script 回傳之 async callable
        self._per_min = per_min
        self._per_day = per_day
        self._global_rps = global_rps

    async def check(
        self, *, user_id: str, is_admin: bool, now_ms: int | None = None
    ) -> RateLimitResult:
        if is_admin:
            return RateLimitResult(allowed=True, retry_after=0)
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        k_min = f"rl:min:{user_id}"
        k_day = f"rl:day:{user_id}"
        k_global = "rl:global"
        # capacities（token-bucket 上限）
        cap_min = self._per_min
        cap_day = self._per_day
        cap_global = self._global_rps
        # refill rates（tokens/sec）
        rate_min = self._per_min / 60.0
        rate_day = self._per_day / 86400.0
        rate_global = float(self._global_rps)
        try:
            result = await self._script(
                keys=[k_min, k_day, k_global],
                args=[
                    cap_min,
                    cap_day,
                    cap_global,
                    rate_min,
                    rate_day,
                    rate_global,
                    now,
                    1,  # cost
                ],
            )
            allowed, retry_ms = int(result[0]), int(result[1])
            if allowed == 0:
                return RateLimitResult(allowed=False, retry_after=math.ceil(retry_ms / 1000))
            return RateLimitResult(allowed=True, retry_after=0)
        except Exception:
            logger.warning("ratelimit Redis 失敗→fail-open", exc_info=True)
            return RateLimitResult(allowed=True, retry_after=0)
