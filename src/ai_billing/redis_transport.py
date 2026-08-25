from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from redis.asyncio import Redis

import json

from .context_auth import execution_context_signature
from .http_transport import BILLING_OPERATIONS
from .schemas import BalanceInfo, DebitPayload

if TYPE_CHECKING:
    from .http_transport import HttpTransport

logger = logging.getLogger("ai_billing")

_DEBIT_TTL = 86400  # 24h


class RedisTransport:
    __slots__ = (
        "_url",
        "_redis",
        "_http_fallback",
        "_context_secret",
        "_context_issuer",
    )

    def __init__(
        self,
        redis_url: str,
        http_fallback: HttpTransport | None = None,
        *,
        context_secret: str | None = None,
        context_issuer: str | None = None,
    ) -> None:
        self._url = redis_url
        self._redis: Redis | None = None
        self._http_fallback = http_fallback
        self._context_secret = (context_secret or "").strip()
        self._context_issuer = (context_issuer or "").strip()

    async def _get_redis(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(self._url, decode_responses=True)
        return self._redis

    async def write_debit(self, payload: DebitPayload) -> str:
        """Write a debit task to Redis. Returns the operation_id."""
        redis = await self._get_redis()
        op_id = payload.operation_id or uuid4().hex
        if payload.billing_execution_context is not None:
            if not self._context_secret or not self._context_issuer:
                raise RuntimeError("BILLING_CONTEXT_SIGNING_REQUIRED")
            context_payload = payload.billing_execution_context.model_dump()
            payload = payload.model_copy(
                update={
                    "context_issuer": self._context_issuer,
                    "context_signature": execution_context_signature(
                        secret=self._context_secret,
                        issuer=self._context_issuer,
                        operation_id=op_id,
                        context=context_payload,
                    ),
                }
            )
        key = f"debit:{op_id}"
        # Omit the v0.6.0 envelope for legacy producers so rolling deployments
        # retain their previous wire shape. Context-aware payloads preserve the
        # typed object verbatim as nested JSON.
        exclude = (
            {
                "billing_execution_context",
                "actor_user_id",
                "context_issuer",
                "context_signature",
            }
            if payload.billing_execution_context is None
            else None
        )
        data = payload.model_dump_json(exclude=exclude)
        logger.info(
            "ai_billing: write_debit key=%s org=%s amount=%s service=%s",
            key, payload.organization_id, payload.amount_usd, payload.service,
        )
        async with redis.pipeline(transaction=True) as pipe:
            pipe.set(key, data, ex=_DEBIT_TTL)
            pipe.sadd("debit:queue", op_id)
            result = await pipe.execute()
        logger.info("ai_billing: write_debit OK op=%s pipeline_result=%s", op_id, result)
        return op_id

    async def read_balance(
        self,
        organization_id: int,
        *,
        actor_user_id: int | None = None,
        operation: str | None = None,
        feature_type: str | None = None,
    ) -> BalanceInfo | None:
        """Resolve an organization balance with the actor/action context.

        ``credits:org:*`` is a legacy, organization-only cache and cannot
        represent the actor, operation, entitlement, or resolved payer.  It
        must therefore never answer an organization-scoped check: doing so
        would let a stale entry bypass the Credit System resolver (including
        after a refund).  Organization checks fail closed without a valid
        actor/action context and otherwise use the signed HTTP contract.

        The user-only API below intentionally retains its legacy cache path.
        """
        if (
            actor_user_id is None
            or actor_user_id <= 0
            or operation not in BILLING_OPERATIONS
        ):
            logger.warning(
                "ai_billing: refusing organization balance without actor/action context for org=%d",
                organization_id,
            )
            return None

        if self._http_fallback is None:
            logger.warning(
                "ai_billing: refusing legacy organization cache without HTTP resolver for org=%d",
                organization_id,
            )
            return None

        logger.info(
            "ai_billing: resolving organization balance via HTTP actor/action contract for org=%d",
            organization_id,
        )
        return await self._http_fallback.check_balance(
            organization_id,
            actor_user_id=actor_user_id,
            operation=operation,
            feature_type=feature_type,
        )

    async def read_balance_by_user(self, user_id: int) -> BalanceInfo | None:
        """Read cached balance for a user (no organization).

        On cache miss, falls back to HTTP if configured.
        Returns None only if both Redis and HTTP fail.
        """
        redis = await self._get_redis()
        raw = await redis.get(f"credits:user:{user_id}")
        if raw is not None:
            data = json.loads(raw)
            return BalanceInfo(organization_id=0, **data)

        if self._http_fallback is not None:
            logger.info("ai_billing: Redis cache miss for user=%d, trying HTTP fallback", user_id)
            return await self._http_fallback.check_balance_by_user(user_id)

        return None

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        if self._http_fallback is not None:
            await self._http_fallback.close()
