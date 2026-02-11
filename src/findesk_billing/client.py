from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from .exceptions import BillingError
from .parsers import parse_response
from .pricing import calculate_cost as _calculate_cost
from .redis_transport import RedisTransport
from .schemas import DebitPayload, UsageInfo

logger = logging.getLogger("findesk_billing")


class BillingClient:
    __slots__ = ("_transport", "_service_name", "_fail_silently")

    def __init__(
        self,
        redis_url: str,
        service_name: str,
        *,
        fail_silently: bool = True,
    ) -> None:
        self._transport = RedisTransport(redis_url)
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
            logger.exception("findesk_billing: report() failed")
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
            usage = UsageInfo(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thinking_output_tokens=thinking_output_tokens,
                cost_usd=cost,
            )
            await self._write(cost, organization_id, user_id)
            return usage
        except Exception:
            if not self._fail_silently:
                raise
            logger.exception("findesk_billing: report_tokens() failed")
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
            logger.exception("findesk_billing: report_cost() failed")

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
