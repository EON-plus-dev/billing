from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from ai_billing.schemas import CostBreakdown, DebitPayload, Usage


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
