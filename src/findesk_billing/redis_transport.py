from __future__ import annotations

import logging
from uuid import uuid4

from redis.asyncio import Redis

from .schemas import DebitPayload

logger = logging.getLogger("findesk_billing")

_DEBIT_TTL = 86400  # 24h


class RedisTransport:
    __slots__ = ("_url", "_redis")

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._redis: Redis | None = None

    async def _get_redis(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(self._url, decode_responses=True)
        return self._redis

    async def write_debit(self, payload: DebitPayload) -> str:
        """Write a debit task to Redis. Returns the operation_id."""
        redis = await self._get_redis()
        op_id = payload.operation_id or uuid4().hex
        key = f"debit:{op_id}"
        data = payload.model_dump_json()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.set(key, data, ex=_DEBIT_TTL)
            pipe.sadd("debit:queue", op_id)
            await pipe.execute()
        return op_id

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
