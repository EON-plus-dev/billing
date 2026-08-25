from __future__ import annotations

import logging
import time
from typing import Any, Literal

import aiohttp
import jwt

from .schemas import BalanceInfo

logger = logging.getLogger("ai_billing")

_REQUEST_TIMEOUT = 3  # seconds
_TOKEN_LIFETIME = 300  # 5 min
_TOKEN_REFRESH_MARGIN = 60  # refresh 1 min before expiry
BillingOperation = Literal[
    "ai_chat",
    "analytics",
    "document_generation",
    "income_auto_sync",
    "income_manual_sync",
    "bank_statement_parse",
]
BILLING_OPERATIONS = frozenset(
    {
        "ai_chat",
        "analytics",
        "document_generation",
        "income_auto_sync",
        "income_manual_sync",
        "bank_statement_parse",
    }
)


class HttpTransport:
    """HTTP fallback to credit_system /internal/check-balance."""

    __slots__ = (
        "_base_url",
        "_service_name",
        "_secret_key",
        "_session",
        "_cached_token",
        "_cached_actor_user_id",
        "_token_expires_at",
    )

    def __init__(self, credit_system_url: str, service_name: str, secret_key: str) -> None:
        self._base_url = credit_system_url.rstrip("/")
        self._service_name = service_name
        self._secret_key = secret_key
        self._session: aiohttp.ClientSession | None = None
        self._cached_token: str | None = None
        self._cached_actor_user_id: int | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self, actor_user_id: int) -> str:
        now = time.time()
        if (
            self._cached_token
            and self._cached_actor_user_id == actor_user_id
            and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN
        ):
            return self._cached_token

        exp = now + _TOKEN_LIFETIME
        payload: dict[str, Any] = {
            "sub": self._service_name,
            "type": "internal_service",
            "actor_user_id": actor_user_id,
            "exp": exp,
        }
        self._cached_token = jwt.encode(payload, self._secret_key, algorithm="HS256")
        self._cached_actor_user_id = actor_user_id
        self._token_expires_at = exp
        return self._cached_token

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            )
        return self._session

    async def check_balance(
        self,
        organization_id: int,
        *,
        actor_user_id: int | None = None,
        operation: BillingOperation | None = None,
        feature_type: str | None = None,
    ) -> BalanceInfo | None:
        """POST /internal/check-balance with signed actor/action context.

        The credit-system role gate binds ``actor_user_id`` in the request to
        the same claim in the signed internal-service JWT.  A cache miss must
        therefore fail closed when the caller has no server-issued actor and
        operation context; a request-controlled legacy ``user_id`` is never
        accepted as a substitute.
        """
        if (
            actor_user_id is None
            or actor_user_id <= 0
            or operation not in BILLING_OPERATIONS
        ):
            logger.warning(
                "ai_billing: refusing HTTP balance fallback without actor/action context for org=%d",
                organization_id,
            )
            return None
        try:
            session = await self._get_session()
            token = self._get_token(actor_user_id)
            request_body: dict[str, Any] = {
                "organization_id": organization_id,
                "user_id": actor_user_id,
                "actor_user_id": actor_user_id,
                "operation": operation,
                "required_credits": 0,
            }
            if feature_type is not None:
                request_body["feature_type"] = feature_type
            resp = await session.post(
                f"{self._base_url}/internal/check-balance",
                json=request_body,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status != 200:
                logger.warning(
                    "ai_billing: HTTP fallback returned %d for org=%d",
                    resp.status, organization_id,
                )
                return None

            data = await resp.json()
            return BalanceInfo(
                organization_id=organization_id,
                balance=data["current_balance"],
            )
        except Exception:
            logger.exception(
                "ai_billing: HTTP fallback failed for org=%d", organization_id,
            )
            return None

    async def check_balance_by_user(self, user_id: int) -> BalanceInfo | None:
        """POST /internal/check-balance-by-user → BalanceInfo or None on any error."""
        try:
            session = await self._get_session()
            # This endpoint is retained for user-pool compatibility. It does
            # not resolve an organization-scoped actor/payer contract.
            token = self._get_token(user_id)
            resp = await session.post(
                f"{self._base_url}/internal/check-balance-by-user",
                json={"user_id": user_id, "required_credits": 0},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status != 200:
                logger.warning(
                    "ai_billing: HTTP fallback returned %d for user=%d",
                    resp.status, user_id,
                )
                return None

            data = await resp.json()
            return BalanceInfo(
                organization_id=0,
                balance=data["current_balance"],
            )
        except Exception:
            logger.exception(
                "ai_billing: HTTP fallback failed for user=%d", user_id,
            )
            return None

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
