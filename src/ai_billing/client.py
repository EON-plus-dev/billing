from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from .exceptions import BillingError
from .http_transport import HttpTransport
from .parsers import parse_response
from .pricing import calculate_cost as _calculate_cost
from .redis_transport import RedisTransport
from .schemas import BalanceInfo, DebitPayload, UsageInfo

logger = logging.getLogger("ai_billing")


class BillingClient:
    __slots__ = ("_transport", "_service_name", "_fail_silently")

    def __init__(
        self,
        redis_url: str,
        service_name: str,
        *,
        fail_silently: bool = True,
        credit_system_url: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        http_fallback = None
        if credit_system_url and secret_key:
            http_fallback = HttpTransport(credit_system_url, service_name, secret_key)
        self._transport = RedisTransport(redis_url, http_fallback=http_fallback)
        self._service_name = service_name
        self._fail_silently = fail_silently

    # -- public API --------------------------------------------------------

    async def report(
        self,
        response: Any,
        *,
        organization_id: int,
        user_id: int,
        model_override: str | None = None,
    ) -> UsageInfo | None:
        """Auto-detect AI response, calculate cost, write debit."""
        try:
            usage = parse_response(response, model_override=model_override)
            await self._write(usage.cost_usd, organization_id, user_id)
            return usage
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("ai_billing: report() failed")
            return None

    async def report_tokens(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_output_tokens: int = 0,
        organization_id: int,
        user_id: int,
    ) -> UsageInfo | None:
        """Calculate cost from token counts and write debit."""
        try:
            cost = _calculate_cost(
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_output_tokens=thinking_output_tokens,
            )
            logger.info(
                "ai_billing: report_tokens cost=%s model=%s in=%d out=%d org=%d",
                cost, model, input_tokens, output_tokens, organization_id,
            )
            usage = UsageInfo(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_output_tokens=thinking_output_tokens,
                cost_usd=cost,
            )
            await self._write(cost, organization_id, user_id)
            logger.info("ai_billing: report_tokens write OK org=%d", organization_id)
            return usage
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("ai_billing: report_tokens() failed")
            return None

    async def report_cost(
        self,
        cost_usd: float | Decimal,
        *,
        organization_id: int,
        user_id: int,
    ) -> None:
        """Write a debit with a pre-calculated cost."""
        try:
            await self._write(Decimal(str(cost_usd)), organization_id, user_id)
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("ai_billing: report_cost() failed")

    async def check_balance(self, organization_id: int) -> BalanceInfo | None:
        """Read cached credit balance from Redis.

        Returns None if no cache entry exists (cache miss or expired).
        Note: cache may be up to 30 min stale.
        """
        try:
            return await self._transport.read_balance(organization_id)
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("ai_billing: check_balance() failed")
            return None

    async def has_credits(self, organization_id: int) -> bool:
        """Quick check: does the organization have a positive credit balance?

        Fail-open: returns True when both Redis and HTTP fallback are unavailable,
        so AI features are not blocked by infrastructure issues.
        """
        balance = await self.check_balance(organization_id)
        if balance is None:
            return True
        return balance.balance > 0

    # -- user-based API (no organization) -----------------------------------

    async def report_tokens_by_user(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_output_tokens: int = 0,
        user_id: int,
    ) -> UsageInfo | None:
        """Calculate cost from token counts and write debit for a user (no org)."""
        try:
            cost = _calculate_cost(
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_output_tokens=thinking_output_tokens,
            )
            logger.info(
                "ai_billing: report_tokens_by_user cost=%s model=%s in=%d out=%d user=%d",
                cost, model, input_tokens, output_tokens, user_id,
            )
            usage = UsageInfo(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_output_tokens=thinking_output_tokens,
                cost_usd=cost,
            )
            await self._write_by_user(cost, user_id)
            logger.info("ai_billing: report_tokens_by_user write OK user=%d", user_id)
            return usage
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("ai_billing: report_tokens_by_user() failed")
            return None

    async def report_cost_by_user(
        self,
        cost_usd: float | Decimal,
        *,
        user_id: int,
    ) -> None:
        """Write a debit with a pre-calculated cost for a user (no org)."""
        try:
            await self._write_by_user(Decimal(str(cost_usd)), user_id)
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("ai_billing: report_cost_by_user() failed")

    async def check_balance_by_user(self, user_id: int) -> BalanceInfo | None:
        """Read cached credit balance for a user (no organization).

        Returns None if no cache entry exists (cache miss or expired).
        """
        try:
            return await self._transport.read_balance_by_user(user_id)
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("ai_billing: check_balance_by_user() failed")
            return None

    async def has_credits_by_user(self, user_id: int) -> bool:
        """Quick check: does the user have a positive credit balance?

        Fail-open: returns True when both Redis and HTTP fallback are unavailable,
        so AI features are not blocked by infrastructure issues.
        """
        balance = await self.check_balance_by_user(user_id)
        if balance is None:
            return True
        return balance.balance > 0

    @staticmethod
    def calculate_cost(
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_output_tokens: int = 0,
    ) -> Decimal:
        """Pure calculation — no Redis, no side effects."""
        return _calculate_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_output_tokens=thinking_output_tokens,
        )

    async def close(self) -> None:
        await self._transport.close()

    # -- internals ---------------------------------------------------------

    async def _write(self, cost_usd: Decimal, organization_id: int, user_id: int) -> None:
        payload = DebitPayload(
            organization_id=organization_id,
            amount_usd=cost_usd,
            service=self._service_name,
            user_id=user_id,
        )
        await self._transport.write_debit(payload)

    async def _write_by_user(self, cost_usd: Decimal, user_id: int) -> None:
        payload = DebitPayload(
            organization_id=None,
            amount_usd=cost_usd,
            service=self._service_name,
            user_id=user_id,
        )
        await self._transport.write_debit(payload)
