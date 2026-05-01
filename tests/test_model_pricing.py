"""Snapshot/approval test for MODEL_PRICING.

Goal: detect ANY silent change in model prices. If a developer updates
prices in src/ai_billing/pricing.py without consciously updating
EXPECTED_PRICING here, this test fails — by design.

When this test goes red:
  1. Did you intentionally change a price? Then update EXPECTED_PRICING
     here AND bump MODEL_PRICING_VERIFIED_AT in pricing.py.
  2. Did you add a new model? Then add it to EXPECTED_PRICING here.
  3. Did you remove a model? Then remove it from EXPECTED_PRICING here.

Reference: ARCHITECTURE.md §7.1, FinDesk_Dev_QA #15-#17.
"""
from decimal import Decimal
import re

import pytest

from ai_billing.pricing import MODEL_PRICING, MODEL_PRICING_VERIFIED_AT


# ---------------------------------------------------------------------------
# Golden master — locked-in prices as of MODEL_PRICING_VERIFIED_AT (2026-04-29).
# Each entry: input, output, thinking_output, cache_read, cache_write, provider.
# Prices are USD per 1M tokens, NO VAT.
# ---------------------------------------------------------------------------
EXPECTED_PRICING: dict[str, dict] = {
    # --- OpenAI — https://openai.com/api/pricing/ ---
    "gpt-4o": {
        "input": Decimal("2.50"), "output": Decimal("10.00"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    "gpt-4o-mini": {
        "input": Decimal("0.15"), "output": Decimal("0.60"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    "gpt-4.1-mini": {
        "input": Decimal("0.40"), "output": Decimal("1.60"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    "gpt-4.1-nano": {
        "input": Decimal("0.10"), "output": Decimal("0.40"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    "gpt-5-mini": {
        "input": Decimal("0.25"), "output": Decimal("2.00"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    "gpt-5-nano": {
        "input": Decimal("0.05"), "output": Decimal("0.40"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    "gpt-4": {
        "input": Decimal("30.00"), "output": Decimal("60.00"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    "text-embedding-3-small": {
        "input": Decimal("0.02"), "output": Decimal("0"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "openai",
    },
    # --- Google — https://ai.google.dev/gemini-api/docs/pricing ---
    "gemini-3-flash": {
        "input": Decimal("0.10"), "output": Decimal("0.40"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "google",
    },
    "gemini-2.5-flash": {
        "input": Decimal("0.30"), "output": Decimal("2.50"),
        "thinking_output": Decimal("2.50"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "google",
    },
    "gemini-2.0-flash": {
        "input": Decimal("0.10"), "output": Decimal("0.40"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "google",
    },
    "gemini-1.5-flash": {
        "input": Decimal("0.075"), "output": Decimal("0.30"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0"), "cache_write": Decimal("0"),
        "provider": "google",
    },
    # --- Anthropic — https://docs.claude.com/en/docs/about-claude/pricing ---
    # cache_write = 5-minute cache write (1.25x base input).
    "claude-sonnet-4-5-20250929": {
        "input": Decimal("3.00"), "output": Decimal("15.00"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0.30"), "cache_write": Decimal("3.75"),
        "provider": "anthropic",
    },
    "claude-sonnet-4-6": {
        "input": Decimal("3.00"), "output": Decimal("15.00"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0.30"), "cache_write": Decimal("3.75"),
        "provider": "anthropic",
    },
    "claude-haiku-4-5": {
        "input": Decimal("1.00"), "output": Decimal("5.00"),
        "thinking_output": Decimal("0"),
        "cache_read": Decimal("0.10"), "cache_write": Decimal("1.25"),
        "provider": "anthropic",
    },
}


class TestModelPricingSnapshot:
    """Snapshot test — every price must match the golden master exactly."""

    @pytest.mark.parametrize("model_id, expected", sorted(EXPECTED_PRICING.items()))
    def test_price_matches_snapshot(self, model_id, expected):
        assert model_id in MODEL_PRICING, (
            f"Model {model_id!r} is in EXPECTED_PRICING but missing from MODEL_PRICING. "
            f"Either add it to pricing.py or remove from EXPECTED_PRICING."
        )
        actual = MODEL_PRICING[model_id]
        for field, expected_value in expected.items():
            actual_value = getattr(actual, field)
            assert actual_value == expected_value, (
                f"{model_id}.{field}: expected {expected_value}, got {actual_value}. "
                f"If this price change is intentional — update EXPECTED_PRICING here "
                f"AND bump MODEL_PRICING_VERIFIED_AT in pricing.py."
            )

    def test_no_extra_models_in_pricing(self):
        """If a new model is added to MODEL_PRICING, this snapshot must be updated."""
        actual_ids = set(MODEL_PRICING.keys())
        expected_ids = set(EXPECTED_PRICING.keys())
        extra = actual_ids - expected_ids
        assert not extra, (
            f"New model(s) in MODEL_PRICING not covered by snapshot: {sorted(extra)}. "
            f"Add them to EXPECTED_PRICING in this file."
        )

    def test_no_missing_models_in_pricing(self):
        """If a model was removed from MODEL_PRICING, the snapshot must be updated."""
        actual_ids = set(MODEL_PRICING.keys())
        expected_ids = set(EXPECTED_PRICING.keys())
        missing = expected_ids - actual_ids
        assert not missing, (
            f"Model(s) removed from MODEL_PRICING but still in snapshot: {sorted(missing)}. "
            f"Remove them from EXPECTED_PRICING in this file."
        )


class TestModelPricingVerifiedAt:
    """MODEL_PRICING_VERIFIED_AT is the date prices were last reconciled with provider docs."""

    def test_is_set(self):
        assert MODEL_PRICING_VERIFIED_AT is not None
        assert MODEL_PRICING_VERIFIED_AT != ""

    def test_is_string(self):
        assert isinstance(MODEL_PRICING_VERIFIED_AT, str)

    def test_format_yyyy_mm_dd(self):
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", MODEL_PRICING_VERIFIED_AT), (
            f"MODEL_PRICING_VERIFIED_AT must be in YYYY-MM-DD format, "
            f"got {MODEL_PRICING_VERIFIED_AT!r}"
        )
