from decimal import Decimal

import pytest

from ai_billing.pricing import calculate_cost, resolve_model
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
        # input: 0.15, thinking: 3.50 -> 3.65
        assert cost == Decimal("3.650000")

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
