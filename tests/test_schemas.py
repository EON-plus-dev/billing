from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_billing.schemas import (
    BillingExecutionContextV1,
    CostBreakdown,
    DebitPayload,
    Usage,
)


class TestUsage:
    def test_required_fields(self):
        u = Usage(input_tokens=100, output_tokens=50)
        assert u.input_tokens == 100
        assert u.output_tokens == 50
        assert u.cached_input_tokens == 0
        assert u.cache_write_tokens == 0

    def test_all_fields(self):
        u = Usage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=20,
            cache_write_tokens=10,
        )
        assert u.cached_input_tokens == 20
        assert u.cache_write_tokens == 10

    def test_frozen(self):
        u = Usage(input_tokens=100, output_tokens=50)
        with pytest.raises(FrozenInstanceError):
            u.input_tokens = 999  # type: ignore[misc]


class TestCostBreakdown:
    def test_construction(self):
        cb = CostBreakdown(
            cost_no_vat=Decimal("6.000000"),
            vat=Decimal("1.200000"),
            cost_total=Decimal("7.200000"),
            by_component={
                "input": Decimal("1.000000"),
                "output": Decimal("5.000000"),
            },
        )
        assert cb.cost_no_vat == Decimal("6.000000")
        assert cb.cost_total == cb.cost_no_vat + cb.vat
        assert cb.by_component["input"] == Decimal("1.000000")

    def test_frozen(self):
        cb = CostBreakdown(
            cost_no_vat=Decimal("0"),
            vat=Decimal("0"),
            cost_total=Decimal("0"),
            by_component={},
        )
        with pytest.raises(FrozenInstanceError):
            cb.cost_no_vat = Decimal("1")  # type: ignore[misc]

    def test_default_by_component_empty(self):
        cb = CostBreakdown(
            cost_no_vat=Decimal("0"),
            vat=Decimal("0"),
            cost_total=Decimal("0"),
        )
        assert cb.by_component == {}


class TestDebitPayload:
    """v0.5.0 — Phase 3 FIFO context fields backward-compat + serialization."""

    def test_v04_legacy_payload_no_context(self):
        """Caller передає тільки v0.4.0 поля → context fields = defaults."""
        p = DebitPayload(
            organization_id=42,
            amount_usd=Decimal("0.0123"),
            service="agreements",
            user_id=7,
        )
        assert p.model_id is None
        assert p.input_tokens == 0
        assert p.output_tokens == 0
        assert p.cached_input_tokens == 0
        assert p.cache_write_tokens == 0
        assert p.feature_type is None
        assert p.caller_user_role is None

    def test_v05_full_context(self):
        """v0.5.0 caller передає AI-context — DebitPayload зберігає все."""
        p = DebitPayload(
            organization_id=42,
            amount_usd=Decimal("0.0500"),
            service="ai_chat",
            user_id=7,
            model_id="claude-haiku-4-5",
            input_tokens=1000,
            output_tokens=500,
            cached_input_tokens=200,
            cache_write_tokens=100,
            feature_type="ai_chat",
            caller_user_role="owner",
        )
        assert p.model_id == "claude-haiku-4-5"
        assert p.input_tokens == 1000
        assert p.cached_input_tokens == 200
        assert p.feature_type == "ai_chat"
        assert p.caller_user_role == "owner"

    def test_user_only_no_organization(self):
        """organization_id може бути None для user-based debit."""
        p = DebitPayload(
            organization_id=None,
            amount_usd=Decimal("0.0050"),
            service="ai_chat",
            user_id=7,
        )
        assert p.organization_id is None
        assert p.user_id == 7

    def test_json_roundtrip_includes_context(self):
        """model_dump_json() → from_json — context fields переживають serialization."""
        original = DebitPayload(
            organization_id=42,
            amount_usd=Decimal("0.0500"),
            service="ai_chat",
            user_id=7,
            model_id="claude-sonnet-4-6",
            input_tokens=1500,
            output_tokens=800,
            feature_type="document_generation",
        )
        as_json = original.model_dump_json()
        restored = DebitPayload.model_validate_json(as_json)
        assert restored.model_id == "claude-sonnet-4-6"
        assert restored.input_tokens == 1500
        assert restored.feature_type == "document_generation"
        assert restored.amount_usd == Decimal("0.0500")


class TestBillingExecutionContextV1:
    @pytest.mark.parametrize(
        "source",
        [
            "ai_billing",
            "income_auto_sync",
            "income_manual_sync",
            "fop_limits_schedule",
        ],
    )
    def test_accepts_canonical_sources(self, source):
        context = BillingExecutionContextV1(
            actor_user_id=7,
            fop_organization_id=42,
            source=source,
            revision=1,
            context_id="source-record:42",
        )
        assert context.source == source

    def test_canonical_context(self):
        context = BillingExecutionContextV1(
            actor_user_id=7,
            fop_organization_id=42,
            source="ai_billing",
            revision=1,
            context_id="request:abc123",
        )
        assert context.version == 1
        assert context.fop_organization_id == 42
        assert context.context_id == "request:abc123"

        with pytest.raises(ValidationError):
            BillingExecutionContextV1(
                version=2,
                actor_user_id=7,
                fop_organization_id=42,
                source="ai_billing",
                revision=1,
                context_id="request:abc123",
            )

    @pytest.mark.parametrize("source", ["client_supplied", "", "documents"])
    def test_context_rejects_unknown_source(self, source):
        with pytest.raises(ValidationError):
            BillingExecutionContextV1(
                actor_user_id=7,
                fop_organization_id=42,
                source=source,
                revision=1,
                context_id="request:abc123",
            )

    @pytest.mark.parametrize("field", ["actor_user_id", "fop_organization_id", "revision"])
    def test_context_requires_positive_ids_and_revision(self, field):
        values = {
            "actor_user_id": 7,
            "fop_organization_id": 42,
            "source": "income_auto_sync",
            "revision": 3,
            "context_id": "auto-sync:org:42",
        }
        values[field] = 0
        with pytest.raises(ValidationError):
            BillingExecutionContextV1(**values)

    @pytest.mark.parametrize("context_id", ["", "   "])
    def test_context_requires_non_empty_opaque_id(self, context_id):
        with pytest.raises(ValidationError):
            BillingExecutionContextV1(
                actor_user_id=7,
                fop_organization_id=42,
                source="income_auto_sync",
                revision=3,
                context_id=context_id,
            )

    def test_debit_payload_json_roundtrip_preserves_exact_context(self):
        context = BillingExecutionContextV1(
            actor_user_id=17,
            fop_organization_id=42,
            source="income_auto_sync",
            revision=4,
            context_id="scheduled-report:9",
        )
        original = DebitPayload(
            organization_id=42,
            amount_usd=Decimal("0.0050"),
            service="documents",
            user_id=99,
            actor_user_id=17,
            billing_execution_context=context,
        )

        restored = DebitPayload.model_validate_json(original.model_dump_json())

        assert restored.billing_execution_context == context
        assert restored.actor_user_id == 17
        assert restored.user_id == 99  # legacy subject remains audit-only

    def test_debit_payload_rejects_context_mirror_mismatch(self):
        context = BillingExecutionContextV1(
            actor_user_id=17,
            fop_organization_id=42,
            source="ai_billing",
            revision=1,
            context_id="request:abc123",
        )
        with pytest.raises(ValidationError, match="must mirror"):
            DebitPayload(
                organization_id=41,
                amount_usd=Decimal("0.0050"),
                service="ai_chat",
                user_id=99,
                actor_user_id=17,
                billing_execution_context=context,
            )

    def test_signed_context_requires_complete_authentication_fields(self):
        context = BillingExecutionContextV1(
            actor_user_id=17,
            fop_organization_id=42,
            source="ai_billing",
            revision=1,
            context_id="ai_chat:interactive:42:17",
        )
        with pytest.raises(ValidationError, match="provided together"):
            DebitPayload(
                organization_id=42,
                amount_usd=Decimal("0.0050"),
                service="ai_chat",
                user_id=17,
                actor_user_id=17,
                billing_execution_context=context,
                context_issuer="ai_chat",
            )
