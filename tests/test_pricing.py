import re
from decimal import Decimal

import pytest

from ai_billing.pricing import (
    MODEL_PRICING,
    MODEL_PRICING_VERIFIED_AT,
    calculate_cost,
    get_vat_multiplier,
    resolve_model,
)
from ai_billing.exceptions import UnknownModelError


class TestResolveModel:
    def test_exact_match(self):
        name, price = resolve_model("gpt-4o-mini")
        assert name == "gpt-4o-mini"
        assert price.provider == "openai"

    def test_prefix_match(self):
        name, price = resolve_model("gpt-5-nano-2025-08-07")
        assert name == "gpt-5-nano"

    def test_prefix_match_gpt5_mini(self):
        name, price = resolve_model("gpt-5-mini-2025-08-07")
        assert name == "gpt-5-mini"
        assert price.input == Decimal("0.25")
        assert price.output == Decimal("2.00")

    def test_unknown_model(self):
        with pytest.raises(UnknownModelError):
            resolve_model("unknown-model-xyz")


class TestCalculateCost:
    def test_gpt4o_mini_1m_input(self):
        cost = calculate_cost("gpt-4o-mini", input_tokens=1_000_000)
        assert cost == Decimal("0.15")

    def test_gpt4o_mini_1m_output(self):
        cost = calculate_cost("gpt-4o-mini", output_tokens=1_000_000)
        assert cost == Decimal("0.60")

    def test_mixed_tokens(self):
        cost = calculate_cost("gpt-4o-mini", input_tokens=500, output_tokens=200)
        # input: 0.15 * 500 / 1M = 0.000075
        # output: 0.60 * 200 / 1M = 0.00012
        # total = 0.000195
        assert cost == Decimal("0.000195")

    def test_gemini_thinking_tokens(self):
        cost = calculate_cost(
            "gemini-2.5-flash",
            input_tokens=1_000_000,
            output_tokens=0,
            thinking_output_tokens=1_000_000,
        )
        # input: 0.30, thinking: 2.50 -> 2.80
        assert cost == Decimal("2.800000")

    def test_zero_tokens(self):
        cost = calculate_cost("gpt-4o-mini")
        assert cost == Decimal("0")

    def test_embedding_no_output_cost(self):
        cost = calculate_cost("text-embedding-3-small", input_tokens=1_000_000, output_tokens=1_000_000)
        # output price is 0
        assert cost == Decimal("0.02")

    def test_prefix_versioned_model(self):
        cost = calculate_cost("gpt-5-nano-2025-08-07", input_tokens=1_000_000)
        assert cost == Decimal("0.05")


class TestModelPriceCachePricing:
    """Anthropic prompt caching pricing — added 2026-04-29."""

    def test_haiku_4_5_present_with_full_cache_pricing(self):
        haiku = MODEL_PRICING["claude-haiku-4-5"]
        assert haiku.input == Decimal("1.00")
        assert haiku.output == Decimal("5.00")
        assert haiku.cache_read == Decimal("0.10")
        assert haiku.cache_write == Decimal("1.25")
        assert haiku.provider == "anthropic"

    def test_sonnet_4_6_has_cache_pricing(self):
        sonnet = MODEL_PRICING["claude-sonnet-4-6"]
        assert sonnet.cache_read == Decimal("0.30")
        assert sonnet.cache_write == Decimal("3.75")

    def test_sonnet_4_5_has_cache_pricing(self):
        sonnet = MODEL_PRICING["claude-sonnet-4-5-20250929"]
        assert sonnet.cache_read == Decimal("0.30")
        assert sonnet.cache_write == Decimal("3.75")

    def test_openai_models_default_zero_cache(self):
        gpt = MODEL_PRICING["gpt-4o"]
        assert gpt.cache_read == Decimal("0")
        assert gpt.cache_write == Decimal("0")

    def test_gemini_models_default_zero_cache(self):
        gemini = MODEL_PRICING["gemini-2.5-flash"]
        assert gemini.cache_read == Decimal("0")
        assert gemini.cache_write == Decimal("0")


class TestModelPricingVerifiedAt:
    def test_is_string(self):
        assert isinstance(MODEL_PRICING_VERIFIED_AT, str)

    def test_format_yyyy_mm_dd(self):
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", MODEL_PRICING_VERIFIED_AT)


class TestVatMultiplier:
    def test_default_120_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("VAT_MULTIPLIER", raising=False)
        assert get_vat_multiplier() == Decimal("1.20")

    def test_get_vat_reads_env_at_call_time(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.50")
        assert get_vat_multiplier() == Decimal("1.50")

        monkeypatch.setenv("VAT_MULTIPLIER", "0.00")
        assert get_vat_multiplier() == Decimal("0.00")

    def test_get_vat_zero_vat_country(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.00")
        assert get_vat_multiplier() == Decimal("1.00")
