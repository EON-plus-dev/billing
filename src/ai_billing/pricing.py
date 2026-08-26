from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from .exceptions import UnknownModelError
from .schemas import CostBreakdown, Usage

_PER_M = Decimal("1_000_000")


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    thinking_output: Decimal = Decimal("0")
    cache_read: Decimal = Decimal("0")
    cache_write: Decimal = Decimal("0")
    provider: str = ""


MODEL_PRICING: dict[str, ModelPrice] = {
    # OpenAI — https://openai.com/api/pricing/
    "gpt-4o": ModelPrice(
        input=Decimal("2.50"), output=Decimal("10.00"), provider="openai",
    ),
    "gpt-4o-mini": ModelPrice(
        input=Decimal("0.15"), output=Decimal("0.60"), provider="openai",
    ),
    "gpt-4.1-mini": ModelPrice(
        input=Decimal("0.40"), output=Decimal("1.60"), provider="openai",
    ),
    "gpt-4.1-nano": ModelPrice(
        input=Decimal("0.10"), output=Decimal("0.40"), provider="openai",
    ),
    "gpt-5-mini": ModelPrice(
        input=Decimal("0.25"), output=Decimal("2.00"), provider="openai",
    ),
    "gpt-5-nano": ModelPrice(
        input=Decimal("0.05"), output=Decimal("0.40"), provider="openai",
    ),
    "gpt-5.5": ModelPrice(
        input=Decimal("5.00"), output=Decimal("30.00"),
        cache_read=Decimal("0.50"), provider="openai",
    ),
    "gpt-4": ModelPrice(
        input=Decimal("30.00"), output=Decimal("60.00"), provider="openai",
    ),
    "text-embedding-3-small": ModelPrice(
        input=Decimal("0.02"), output=Decimal("0"), provider="openai",
    ),
    # Google — https://ai.google.dev/gemini-api/docs/pricing
    "gemini-3-flash": ModelPrice(
        input=Decimal("0.10"), output=Decimal("0.40"), provider="google",
    ),
    "gemini-2.5-flash": ModelPrice(
        input=Decimal("0.30"), output=Decimal("2.50"),
        thinking_output=Decimal("2.50"), provider="google",
    ),
    "gemini-2.0-flash": ModelPrice(
        input=Decimal("0.10"), output=Decimal("0.40"), provider="google",
    ),
    "gemini-1.5-flash": ModelPrice(
        input=Decimal("0.075"), output=Decimal("0.30"), provider="google",
    ),
    # Anthropic — https://docs.claude.com/en/docs/about-claude/pricing
    # cache_write = 5-minute cache write (1.25x base input). 1-hour cache write (2x) not modelled.
    "claude-sonnet-4-5-20250929": ModelPrice(
        input=Decimal("3.00"), output=Decimal("15.00"),
        cache_read=Decimal("0.30"), cache_write=Decimal("3.75"),
        provider="anthropic",
    ),
    "claude-sonnet-4-6": ModelPrice(
        input=Decimal("3.00"), output=Decimal("15.00"),
        cache_read=Decimal("0.30"), cache_write=Decimal("3.75"),
        provider="anthropic",
    ),
    "claude-haiku-4-5": ModelPrice(
        input=Decimal("1.00"), output=Decimal("5.00"),
        cache_read=Decimal("0.10"), cache_write=Decimal("1.25"),
        provider="anthropic",
    ),
}

# Date pricing was last verified against provider docs. Bump on every price update.
MODEL_PRICING_VERIFIED_AT: str = "2026-08-26"

# Module constant — captured at import. READ-ONLY snapshot for inspection / logging.
# DO NOT use this in cost-calculation code paths — it does NOT pick up runtime
# changes (env mutations, admin UI toggles, monkeypatch in tests).
# For any runtime/computational use, call get_vat_multiplier() instead.
VAT_MULTIPLIER: Decimal = Decimal(os.getenv("VAT_MULTIPLIER", "1.20"))


def get_vat_multiplier() -> Decimal:
    """Read VAT multiplier from env at call time.

    Prefer this over the VAT_MULTIPLIER constant in any cost-calculation code
    path: it picks up runtime overrides (admin UI / BillingSettings.vat_multiplier
    via env propagation, monkeypatch in tests). Default 1.20 (Ukrainian VAT 20%).
    """
    return Decimal(os.getenv("VAT_MULTIPLIER", "1.20"))


# Sorted longest-first for dated snapshot matching
_SORTED_PREFIXES = sorted(MODEL_PRICING.keys(), key=len, reverse=True)
_SNAPSHOT_SUFFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_model(model: str) -> tuple[str, ModelPrice]:
    """Resolve a model name (possibly versioned) to its canonical name and price.

    Uses prefix matching: 'gpt-5-nano-2025-08-07' -> 'gpt-5-nano'.
    """
    if model in MODEL_PRICING:
        return model, MODEL_PRICING[model]
    for prefix in _SORTED_PREFIXES:
        snapshot_suffix = model.removeprefix(f"{prefix}-")
        if model.startswith(f"{prefix}-") and _SNAPSHOT_SUFFIX_RE.fullmatch(snapshot_suffix):
            return prefix, MODEL_PRICING[prefix]
    raise UnknownModelError(
        f"Unknown model: {model!r}. "
        f"Valid models: {', '.join(sorted(MODEL_PRICING.keys()))}"
    )


def _calculate_cost_legacy(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    thinking_output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal:
    """Legacy positional-token cost calc — Decimal USD, no VAT, no cache.

    Kept for parsers.py (Anthropic/OpenAI/Gemini response parsers that emit
    UsageInfo.cost_usd: Decimal) and BillingClient.calculate_cost facade.
    Note: keeps thinking_output_tokens support for Gemini, which is absent
    from the new Usage dataclass.

    For new code use the public calculate_cost(model_id, usage) -> CostBreakdown.
    """
    _, price = resolve_model(model)
    billable_input_tokens = input_tokens
    if price.provider == "openai" and price.cache_read:
        billable_input_tokens = max(input_tokens - cached_input_tokens, 0)
    cost = (
        price.input * billable_input_tokens
        + price.output * output_tokens
        + price.thinking_output * thinking_output_tokens
        + price.cache_read * cached_input_tokens
        + price.cache_write * cache_write_tokens
    ) / _PER_M
    return cost.quantize(Decimal("0.000001"))


def calculate_cost(model_id: str, usage: Usage) -> CostBreakdown:
    """Calculate per-component cost breakdown with VAT (ARCHITECTURE.md §7.3).

    Components: input, output, cache_read, cache_write — each
    usage_tokens * price / 1_000_000. cost_no_vat = sum(components);
    vat = cost_no_vat * (VAT_MULTIPLIER - 1); cost_total = cost_no_vat + vat.
    All amounts quantized to 6 decimal places (USD).

    For models without cache_read/cache_write pricing,
    cache components silently return Decimal("0") even if usage carries
    cache tokens — matches the fail-silent style of the rest of the lib.

    OpenAI input_tokens includes cached_input_tokens. For OpenAI models with
    cache pricing, cached tokens are subtracted from regular input before the
    two components are priced separately.

    Raises:
        UnknownModelError (also a ValueError) if model_id is not in
        MODEL_PRICING. Message includes the list of valid model ids.
    """
    _, price = resolve_model(model_id)
    billable_input_tokens = usage.input_tokens
    if price.provider == "openai" and price.cache_read:
        billable_input_tokens = max(usage.input_tokens - usage.cached_input_tokens, 0)

    q = Decimal("0.000001")
    by_component: dict[str, Decimal] = {
        "input":       (price.input * billable_input_tokens / _PER_M).quantize(q),
        "output":      (price.output * usage.output_tokens / _PER_M).quantize(q),
        "cache_read":  (price.cache_read * usage.cached_input_tokens / _PER_M).quantize(q),
        "cache_write": (price.cache_write * usage.cache_write_tokens / _PER_M).quantize(q),
    }

    cost_no_vat = sum(by_component.values(), Decimal("0")).quantize(q)
    vat_mult = get_vat_multiplier()
    vat = (cost_no_vat * (vat_mult - Decimal("1"))).quantize(q)
    cost_total = (cost_no_vat + vat).quantize(q)

    return CostBreakdown(
        cost_no_vat=cost_no_vat,
        vat=vat,
        cost_total=cost_total,
        # MappingProxyType: read-only view, callers cannot mutate the breakdown.
        by_component=MappingProxyType(by_component),
    )
