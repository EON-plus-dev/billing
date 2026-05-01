"""Focused test suite for calculate_cost(model_id, usage) -> CostBreakdown.

Covers the 5 scenarios required by the FinDesk billing-lib estimate:
  1. VAT round-trip (parametrized across providers)
  2. Each of the 4 components in isolation (input/output/cache_read/cache_write)
  3. Haiku spec example: input=600, output=200, cached_input=0 -> 1.6 milli-USD
  4. Overflow: input_tokens=1_000_000_000 must keep full Decimal precision
  5. Unknown model_id -> ValueError with list of valid models

Reference: ARCHITECTURE.md §7.3, ai-billing estimate task 1.5.
"""
from decimal import Decimal

import pytest

from ai_billing import (
    MODEL_PRICING,
    CostBreakdown,
    Usage,
    UnknownModelError,
    calculate_cost,
)


_Q = Decimal("0.000001")  # 6-decimal quantization used inside calculate_cost


# ---------------------------------------------------------------------------
# 1. VAT round-trip across providers
# ---------------------------------------------------------------------------

class TestVatRoundTrip:
    """cost_total must equal (cost_no_vat * VAT_MULTIPLIER) quantized to 6 decimals.

    Parametrized across all three provider families to ensure VAT is applied
    uniformly regardless of model (per ARCHITECTURE.md §8.1 — VAT is internal
    cost-counter logic, not provider-specific).
    """

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-haiku-4-5",
            "claude-sonnet-4-6",
            "gpt-4o",
            "gemini-2.5-flash",
        ],
    )
    def test_vat_default_120(self, model_id, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
        cb = calculate_cost(model_id, usage)
        # Round-trip invariant: total = no_vat * 1.20 (both quantized to 6 dp)
        expected_total = (cb.cost_no_vat * Decimal("1.20")).quantize(_Q)
        assert cb.cost_total == expected_total, (
            f"{model_id}: cost_total ({cb.cost_total}) != "
            f"cost_no_vat * 1.20 ({expected_total})"
        )
        # And vat = no_vat * 0.20
        expected_vat = (cb.cost_no_vat * Decimal("0.20")).quantize(_Q)
        assert cb.vat == expected_vat
        # And total = no_vat + vat (definitional)
        assert cb.cost_total == (cb.cost_no_vat + cb.vat).quantize(_Q)

    def test_vat_custom_50pct(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.50")
        usage = Usage(input_tokens=1_000_000, output_tokens=0)
        cb = calculate_cost("claude-haiku-4-5", usage)
        # input only: 1.00 USD; vat 50% = 0.50; total = 1.50
        assert cb.cost_no_vat == Decimal("1.000000")
        assert cb.vat == Decimal("0.500000")
        assert cb.cost_total == Decimal("1.500000")

    def test_vat_zero(self, monkeypatch):
        """Zero-VAT country (or VAT-disabled) — total == no_vat exactly."""
        monkeypatch.setenv("VAT_MULTIPLIER", "1.00")
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        cb = calculate_cost("claude-sonnet-4-6", usage)
        # sonnet-4-6: 3 + 15 = 18 USD; vat = 0; total = 18
        assert cb.cost_no_vat == Decimal("18.000000")
        assert cb.vat == Decimal("0.000000")
        assert cb.cost_total == cb.cost_no_vat


# ---------------------------------------------------------------------------
# 2. Each of the 4 components in isolation
# ---------------------------------------------------------------------------

class TestComponentsIsolation:
    """Feed only one component at a time; the other three must be zero.

    Uses claude-haiku-4-5 because it has all 4 prices non-zero, so we can
    reliably tell isolation from accidental cross-contamination.
    """

    def test_only_input(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=1_000_000, output_tokens=0)
        cb = calculate_cost("claude-haiku-4-5", usage)
        assert cb.by_component["input"] == Decimal("1.000000")
        assert cb.by_component["output"] == Decimal("0")
        assert cb.by_component["cache_read"] == Decimal("0")
        assert cb.by_component["cache_write"] == Decimal("0")
        assert cb.cost_no_vat == Decimal("1.000000")

    def test_only_output(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=0, output_tokens=1_000_000)
        cb = calculate_cost("claude-haiku-4-5", usage)
        assert cb.by_component["input"] == Decimal("0")
        assert cb.by_component["output"] == Decimal("5.000000")
        assert cb.by_component["cache_read"] == Decimal("0")
        assert cb.by_component["cache_write"] == Decimal("0")
        assert cb.cost_no_vat == Decimal("5.000000")

    def test_only_cache_read(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=0, output_tokens=0, cached_input_tokens=1_000_000)
        cb = calculate_cost("claude-haiku-4-5", usage)
        assert cb.by_component["input"] == Decimal("0")
        assert cb.by_component["output"] == Decimal("0")
        assert cb.by_component["cache_read"] == Decimal("0.100000")
        assert cb.by_component["cache_write"] == Decimal("0")
        assert cb.cost_no_vat == Decimal("0.100000")

    def test_only_cache_write(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=0, output_tokens=0, cache_write_tokens=1_000_000)
        cb = calculate_cost("claude-haiku-4-5", usage)
        assert cb.by_component["input"] == Decimal("0")
        assert cb.by_component["output"] == Decimal("0")
        assert cb.by_component["cache_read"] == Decimal("0")
        assert cb.by_component["cache_write"] == Decimal("1.250000")
        assert cb.cost_no_vat == Decimal("1.250000")

    def test_returns_cost_breakdown_type(self):
        """Sanity: return type is the public CostBreakdown dataclass."""
        cb = calculate_cost("claude-haiku-4-5", Usage(input_tokens=100, output_tokens=0))
        assert isinstance(cb, CostBreakdown)
        # by_component keys are exactly the 4 expected ones
        assert set(cb.by_component.keys()) == {
            "input", "output", "cache_read", "cache_write",
        }


# ---------------------------------------------------------------------------
# 3. Haiku "spec example" from estimate task 1.5
# ---------------------------------------------------------------------------

class TestHaikuSpecExample:
    """input=600, output=200, cached_input=0
       -> cost_no_vat = 600*1/1M + 200*5/1M = 0.000600 + 0.001000
                      = 0.001600 USD = 1.6 milli-USD
    """

    def test_spec_example(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=600, output_tokens=200, cached_input_tokens=0)
        cb = calculate_cost("claude-haiku-4-5", usage)
        # 0.0016 USD = 1.6 milli-USD (NOT mega-USD — "m" = milli here)
        assert cb.by_component["input"] == Decimal("0.000600")
        assert cb.by_component["output"] == Decimal("0.001000")
        assert cb.by_component["cache_read"] == Decimal("0")
        assert cb.by_component["cache_write"] == Decimal("0")
        assert cb.cost_no_vat == Decimal("0.001600")
        # VAT 20%: 0.0016 * 1.20 = 0.00192 -> quantized to 0.000192
        # vat alone: 0.0016 * 0.20 = 0.00032 -> 0.000320
        assert cb.vat == Decimal("0.000320")
        assert cb.cost_total == Decimal("0.001920")


# ---------------------------------------------------------------------------
# 4. Overflow / large-input precision
# ---------------------------------------------------------------------------

class TestOverflow:
    """Decimal must keep full precision for billion-token inputs."""

    def test_one_billion_input_haiku(self, monkeypatch):
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=1_000_000_000, output_tokens=0)
        cb = calculate_cost("claude-haiku-4-5", usage)
        # 1B tokens * $1/M = $1000 exact
        assert cb.by_component["input"] == Decimal("1000.000000")
        assert cb.cost_no_vat == Decimal("1000.000000")
        # VAT 20%: 200 -> total 1200
        assert cb.vat == Decimal("200.000000")
        assert cb.cost_total == Decimal("1200.000000")

    def test_one_billion_input_sonnet_46(self, monkeypatch):
        """Sonnet has higher prices — verify scaling stays exact."""
        monkeypatch.setenv("VAT_MULTIPLIER", "1.20")
        usage = Usage(input_tokens=1_000_000_000, output_tokens=1_000_000_000)
        cb = calculate_cost("claude-sonnet-4-6", usage)
        # 1B*3 + 1B*15 = 3000 + 15000 = 18_000 USD
        assert cb.by_component["input"] == Decimal("3000.000000")
        assert cb.by_component["output"] == Decimal("15000.000000")
        assert cb.cost_no_vat == Decimal("18000.000000")
        # VAT 20%: 3600 -> total 21600
        assert cb.vat == Decimal("3600.000000")
        assert cb.cost_total == Decimal("21600.000000")


# ---------------------------------------------------------------------------
# 5. Unknown model_id -> ValueError with list of valid models
# ---------------------------------------------------------------------------

class TestUnknownModel:
    """ARCHITECTURE.md §7.3 contract: unknown model_id raises ValueError
    with the list of valid models in the message.

    UnknownModelError is a subclass of both BillingError and ValueError,
    so existing callers that catch UnknownModelError keep working.
    """

    def test_raises_value_error(self):
        usage = Usage(input_tokens=100, output_tokens=50)
        with pytest.raises(ValueError):
            calculate_cost("definitely-not-a-real-model", usage)

    def test_also_raises_unknown_model_error(self):
        """Backward compat: existing UnknownModelError catchers keep working."""
        usage = Usage(input_tokens=100, output_tokens=50)
        with pytest.raises(UnknownModelError):
            calculate_cost("definitely-not-a-real-model", usage)

    def test_message_contains_invalid_model_id(self):
        usage = Usage(input_tokens=100, output_tokens=50)
        with pytest.raises(ValueError) as exc_info:
            calculate_cost("foo-bar-baz", usage)
        assert "foo-bar-baz" in str(exc_info.value)

    def test_message_lists_valid_models(self):
        """The error message must include the list of valid model ids
        so the developer knows what to use without grepping the lib."""
        usage = Usage(input_tokens=100, output_tokens=50)
        with pytest.raises(ValueError) as exc_info:
            calculate_cost("foo-bar-baz", usage)
        msg = str(exc_info.value)
        assert "Valid models" in msg
        # At least one real model id from MODEL_PRICING must appear
        for real_model in MODEL_PRICING:
            if real_model in msg:
                break
        else:
            pytest.fail(
                f"Error message must list valid models from MODEL_PRICING, got: {msg}"
            )

    def test_empty_string_also_raises(self):
        """Edge case: empty model_id is also unknown."""
        usage = Usage(input_tokens=100, output_tokens=50)
        with pytest.raises(ValueError):
            calculate_cost("", usage)
