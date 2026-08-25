import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from ai_billing.client import BillingClient
from ai_billing.exceptions import UnknownModelError
from ai_billing.schemas import BalanceInfo, BillingExecutionContextV1
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


@pytest.fixture
def interactive_context():
    return BillingExecutionContextV1(
        actor_user_id=77,
        fop_organization_id=40,
        source="ai_billing",
        revision=1,
        context_id="request:test-77",
    )


class TestBillingExecutionContext:
    @staticmethod
    def assert_canonical_wire(payload):
        data = json.loads(payload.model_dump_json())
        assert data["actor_user_id"] == 77
        assert data["organization_id"] == 40
        assert data["billing_execution_context"] == {
            "version": 1,
            "actor_user_id": 77,
            "fop_organization_id": 40,
            "source": "ai_billing",
            "revision": 1,
            "context_id": "request:test-77",
        }

    async def test_v1_api_rejects_missing_context(self, client):
        with pytest.raises(TypeError, match="billing_execution_context"):
            await client.report_cost_v1(  # type: ignore[call-arg]
                Decimal("0.005"),
                organization_id=40,
                user_id=999,
            )

    async def test_legacy_report_accepts_optional_context(
        self, client, interactive_context
    ):
        resp = make_openai_response(prompt_tokens=100, completion_tokens=50)

        await client.report(
            resp,
            organization_id=40,
            user_id=999,
            billing_execution_context=interactive_context,
        )

        payload = client._transport.write_debit.await_args.args[0]
        assert payload.billing_execution_context == interactive_context
        assert payload.actor_user_id == 77
        assert payload.user_id == 999

    async def test_v1_report_requires_and_propagates_context(
        self, client, interactive_context
    ):
        resp = make_openai_response(prompt_tokens=100, completion_tokens=50)

        await client.report_v1(
            resp,
            organization_id=40,
            user_id=999,
            billing_execution_context=interactive_context,
        )

        payload = client._transport.write_debit.await_args.args[0]
        assert payload.billing_execution_context.actor_user_id == 77
        assert payload.actor_user_id == 77
        assert payload.organization_id == 40
        self.assert_canonical_wire(payload)

    async def test_v1_token_and_cost_reports_propagate_context(
        self, client, interactive_context
    ):
        await client.report_tokens_v1(
            "gpt-4o-mini",
            input_tokens=10,
            output_tokens=5,
            organization_id=40,
            user_id=999,
            billing_execution_context=interactive_context,
        )
        first = client._transport.write_debit.await_args.args[0]
        assert first.billing_execution_context == interactive_context
        self.assert_canonical_wire(first)

        await client.report_cost_v1(
            Decimal("0.005"),
            organization_id=40,
            user_id=999,
            billing_execution_context=interactive_context,
        )
        second = client._transport.write_debit.await_args.args[0]
        assert second.billing_execution_context == interactive_context
        self.assert_canonical_wire(second)

    async def test_context_target_must_match_organization(self):
        context = BillingExecutionContextV1(
            actor_user_id=77,
            fop_organization_id=41,
            source="ai_billing",
            revision=1,
            context_id="request:test-77",
        )
        strict_client = BillingClient(
            redis_url="redis://x",
            service_name="test_svc",
            fail_silently=False,
        )
        strict_client._transport = AsyncMock()

        with pytest.raises(ValueError, match="must mirror"):
            await strict_client.report_cost_v1(
                Decimal("0.005"),
                organization_id=40,
                user_id=999,
                billing_execution_context=context,
            )

    async def test_user_only_legacy_payload_has_no_context(self, client):
        await client.report_cost_by_user(Decimal("0.005"), user_id=7)

        payload = client._transport.write_debit.await_args.args[0]
        assert payload.organization_id is None
        assert payload.billing_execution_context is None
        assert payload.actor_user_id is None


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
    async def test_missing_or_invalid_context_fails_closed_even_when_fail_open(self, client):
        client._transport.read_balance = AsyncMock(
            side_effect=AssertionError("must not read without context")
        )

        assert await client.has_credits(organization_id=1) is False
        assert (
            await client.has_credits(
                organization_id=1,
                actor_user_id=7,
                operation="untrusted_operation",
            )
            is False
        )

    async def test_positive_balance(self, client):
        client._transport.read_balance = AsyncMock(
            return_value=BalanceInfo(organization_id=1, balance=100)
        )
        assert (
            await client.has_credits(
                organization_id=1,
                actor_user_id=7,
                operation="document_generation",
            )
            is True
        )

    async def test_zero_balance(self, client):
        client._transport.read_balance = AsyncMock(
            return_value=BalanceInfo(organization_id=1, balance=0)
        )
        assert (
            await client.has_credits(
                organization_id=1,
                actor_user_id=7,
                operation="document_generation",
            )
            is False
        )

    async def test_cache_miss_fail_open(self, client):
        client._transport.read_balance = AsyncMock(return_value=None)
        assert (
            await client.has_credits(
                organization_id=1,
                actor_user_id=7,
                operation="document_generation",
            )
            is True
        )


class TestCalculateCost:
    def test_static_method(self):
        cost = BillingClient.calculate_cost("gpt-4o-mini", input_tokens=1_000_000)
        assert cost == Decimal("0.15")
