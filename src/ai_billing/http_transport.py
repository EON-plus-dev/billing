from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
import jwt

from .schemas import BalanceInfo

logger = logging.getLogger("ai_billing")

_REQUEST_TIMEOUT = 3  # seconds
_TOKEN_LIFETIME = 300  # 5 min
_TOKEN_REFRESH_MARGIN = 60  # refresh 1 min before expiry


class HttpTransport:
    """HTTP fallback to credit_system /internal/check-balance."""

    __slots__ = (
        "_base_url",
        "_service_name",
        "_secret_key",
        "_session",
        "_cached_token",
        "_token_expires_at",
    )

    def __init__(self, credit_system_url: str, service_name: str, secret_key: str) -> None:
        self._base_url = credit_system_url.rstrip("/")
        self._service_name = service_name
        self._secret_key = secret_key
        self._session: aiohttp.ClientSession | None = None
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        now = time.time()
        if self._cached_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN:
            return self._cached_token

        exp = now + _TOKEN_LIFETIME
        payload: dict[str, Any] = {
            "sub": self._service_name,
            "type": "internal_service",
            "exp": exp,
        }
        self._cached_token = jwt.encode(payload, self._secret_key, algorithm="HS256")
        self._token_expires_at = exp
        return self._cached_token

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            )
        return self._session

    async def check_balance(self, organization_id: int) -> BalanceInfo | None:
        """POST /internal/check-balance → BalanceInfo or None on any error."""
        try:
            session = await self._get_session()
            token = self._get_token()
            resp = await session.post(
                f"{self._base_url}/internal/check-balance",
                json={"organization_id": organization_id, "required_credits": 0},
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
            token = self._get_token()
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
