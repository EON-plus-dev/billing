from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from ai_billing.client import BillingClient
from ai_billing.exceptions import UnknownModelError
from conftest import make_openai_response, make_anthropic_response


@pytest.fixture
def client():
    c = BillingClient(redis_url="redis://localhost:6380", service_name="test_svc")
    c._transport = AsyncMock()
    c._transport.write_debit = AsyncMock()
    return c


class TestReport:
    async def test_openai_response(self, client):
        resp = make_openai_response(prompt_tokens=1000, completion_tokens=500)
        usage = await client.report(resp, organization_id=1, user_id=2)
        assert usage is not None
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.cost_usd > 0
        client._transport.write_debit.assert_awaited_once()

    async def test_anthropic_response(self, client):
        resp = make_anthropic_response(input_tokens=200, output_tokens=100)
        usage = await client.report(resp, organization_id=1, user_id=2)
        assert usage is not None
        assert usage.model == "claude-sonnet-4-5-20250929"

    async def test_fail_silently(self, client):
        usage = await client.report(object(), organization_id=1, user_id=2)
        assert usage is None

    async def test_fail_loudly(self):
        c = BillingClient(redis_url="redis://x", service_name="t", fail_silently=False)
        c._transport = AsyncMock()
        with pytest.raises(Exception):
            await c.report(object(), organization_id=1, user_id=2)


class TestReportTokens:
    async def test_basic(self, client):
        usage = await client.report_tokens(
            "gpt-4o-mini", input_tokens=500, output_tokens=200,
            organization_id=1, user_id=2,
        )
        assert usage is not None
        assert usage.cost_usd == Decimal("0.000195")
        client._transport.write_debit.assert_awaited_once()

    async def test_unknown_model_silent(self, client):
        usage = await client.report_tokens(
            "nonexistent", input_tokens=1, output_tokens=1,
            organization_id=1, user_id=2,
        )
        assert usage is None

    async def test_unknown_model_loud(self):
        c = BillingClient(redis_url="redis://x", service_name="t", fail_silently=False)
        c._transport = AsyncMock()
        with pytest.raises(UnknownModelError):
            await c.report_tokens(
                "nonexistent", input_tokens=1, output_tokens=1,
                organization_id=1, user_id=2,
            )


class TestReportCost:
    async def test_basic(self, client):
        await client.report_cost(0.005, organization_id=1, user_id=2)
        client._transport.write_debit.assert_awaited_once()


class TestCalculateCost:
    def test_static_method(self):
        cost = BillingClient.calculate_cost("gpt-4o-mini", input_tokens=1_000_000)
        assert cost == Decimal("0.15")
