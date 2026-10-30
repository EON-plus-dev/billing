from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from ai_billing.client import BillingClient
from ai_billing.exceptions import UnknownModelError
from ai_billing.schemas import BalanceInfo
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

    async def test_gpt55_cached_input_is_reflected_in_debit_amount(self, client):
        usage = await client.report_tokens(
            "gpt-5.5",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            organization_id=1,
            user_id=2,
        )
        assert usage is not None
        assert usage.cost_usd == Decimal("30.500000")
        payload = client._transport.write_debit.await_args.args[0]
        assert payload.amount_usd == Decimal("30.500000")
        assert payload.cached_input_tokens == 1_000_000

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


class TestCheckBalance:
    async def test_returns_balance(self, client):
        client._transport.read_balance = AsyncMock(
            return_value=BalanceInfo(organization_id=1, balance=50000)
        )
        info = await client.check_balance(organization_id=1)
        assert info is not None
        assert info.balance == 50000

    async def test_cache_miss(self, client):
        client._transport.read_balance = AsyncMock(return_value=None)
        info = await client.check_balance(organization_id=1)
        assert info is None

    async def test_fail_silently(self, client):
        client._transport.read_balance = AsyncMock(side_effect=Exception("boom"))
        info = await client.check_balance(organization_id=1)
        assert info is None


class TestHasCredits:
    async def test_positive_balance(self, client):
        client._transport.read_balance = AsyncMock(
            return_value=BalanceInfo(organization_id=1, balance=100)
        )
        assert await client.has_credits(organization_id=1) is True

    async def test_zero_balance(self, client):
        client._transport.read_balance = AsyncMock(
            return_value=BalanceInfo(organization_id=1, balance=0)
        )
        assert await client.has_credits(organization_id=1) is False

    async def test_cache_miss_fail_open(self, client):
        client._transport.read_balance = AsyncMock(return_value=None)
        assert await client.has_credits(organization_id=1) is True


class TestCalculateCost:
    def test_static_method(self):
        cost = BillingClient.calculate_cost("gpt-4o-mini", input_tokens=1_000_000)
        assert cost == Decimal("0.15")


class TestOperationId:
    """Caller-supplied operation_id is the idempotency key in credit_system.

    A retry of the same logical debit must reuse it; otherwise credit_system
    sees two distinct operations and charges twice.
    """

    async def test_report_tokens_passes_operation_id(self, client):
        await client.report_tokens(
            "gpt-4o-mini", input_tokens=500, output_tokens=200,
            organization_id=1, user_id=2, operation_id="codex:th:turn-1",
        )
        payload = client._transport.write_debit.await_args.args[0]
        assert payload.operation_id == "codex:th:turn-1"

    async def test_report_tokens_by_user_passes_operation_id(self, client):
        await client.report_tokens_by_user(
            "gpt-4o-mini", input_tokens=500, output_tokens=200,
            user_id=2, operation_id="codex:th:turn-2",
        )
        payload = client._transport.write_debit.await_args.args[0]
        assert payload.operation_id == "codex:th:turn-2"
        assert payload.organization_id is None

    async def test_report_cost_passes_operation_id(self, client):
        await client.report_cost(0.005, organization_id=1, user_id=2, operation_id="op-3")
        payload = client._transport.write_debit.await_args.args[0]
        assert payload.operation_id == "op-3"

    async def test_report_cost_by_user_passes_operation_id(self, client):
        await client.report_cost_by_user(0.005, user_id=2, operation_id="op-4")
        payload = client._transport.write_debit.await_args.args[0]
        assert payload.operation_id == "op-4"

    async def test_omitted_operation_id_stays_unique(self, client):
        for _ in range(2):
            await client.report_tokens(
                "gpt-4o-mini", input_tokens=1, output_tokens=1,
                organization_id=1, user_id=2,
            )
        first, second = (c.args[0].operation_id for c in client._transport.write_debit.await_args_list)
        assert first and second and first != second


class TestReportTokensByUserPricing:
    async def test_cached_input_priced_same_as_org_path(self, client):
        kwargs = dict(
            input_tokens=1_000_000, output_tokens=1_000_000,
            cached_input_tokens=1_000_000, cache_write_tokens=0,
        )
        org_usage = await client.report_tokens("gpt-5.5", organization_id=1, user_id=2, **kwargs)
        user_usage = await client.report_tokens_by_user("gpt-5.5", user_id=2, **kwargs)
        assert user_usage.cost_usd == org_usage.cost_usd == Decimal("30.500000")
        payload = client._transport.write_debit.await_args.args[0]
        assert payload.amount_usd == Decimal("30.500000")


class TestOperationIdValidation:
    @pytest.mark.parametrize(
        "bad",
        ["queue", "dlq", "dlq:abc", "  ", "a b", "x" * 256, "з", 123],
    )
    async def test_rejected_even_when_fail_silently(self, client, bad):
        with pytest.raises(ValueError):
            await client.report_cost(0.01, organization_id=1, user_id=2, operation_id=bad)
        with pytest.raises(ValueError):
            await client.report_tokens_by_user(
                "gpt-4o-mini", input_tokens=1, output_tokens=1, user_id=2, operation_id=bad,
            )
        client._transport.write_debit.assert_not_awaited()

    @pytest.mark.parametrize("good", ["codex:th_1:turn-2", "a.b-c_d:9", "x" * 255])
    async def test_accepted(self, client, good):
        await client.report_tokens(
            "gpt-4o-mini", input_tokens=1, output_tokens=1,
            organization_id=1, user_id=2, operation_id=good,
        )
        assert client._transport.write_debit.await_args.args[0].operation_id == good
