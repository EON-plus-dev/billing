from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .exceptions import UnknownModelError

_PER_M = Decimal("1_000_000")


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    thinking_output: Decimal = Decimal("0")
    provider: str = ""


MODEL_PRICING: dict[str, ModelPrice] = {
    "gpt-4o-mini": ModelPrice(
        input=Decimal("0.15"), output=Decimal("0.60"), provider="openai",
    ),
    "gpt-4.1-mini": ModelPrice(
        input=Decimal("0.10"), output=Decimal("0.40"), provider="openai",
    ),
    "gpt-4.1-nano": ModelPrice(
        input=Decimal("0.10"), output=Decimal("0.40"), provider="openai",
    ),
    "gpt-5-nano": ModelPrice(
        input=Decimal("0.05"), output=Decimal("0.40"), provider="openai",
    ),
    "gpt-4": ModelPrice(
        input=Decimal("30.00"), output=Decimal("60.00"), provider="openai",
    ),
    "text-embedding-3-small": ModelPrice(
        input=Decimal("0.02"), output=Decimal("0"), provider="openai",
    ),
    "gemini-2.5-flash": ModelPrice(
        input=Decimal("0.15"), output=Decimal("0.60"),
        thinking_output=Decimal("3.50"), provider="google",
    ),
    "gemini-1.5-flash": ModelPrice(
        input=Decimal("0.075"), output=Decimal("0.30"), provider="google",
    ),
    "claude-sonnet-4-5-20250929": ModelPrice(
        input=Decimal("3.00"), output=Decimal("15.00"), provider="anthropic",
    ),
}

# Sorted longest-first for greedy prefix match
_SORTED_PREFIXES = sorted(MODEL_PRICING.keys(), key=len, reverse=True)


def resolve_model(model: str) -> tuple[str, ModelPrice]:
    """Resolve a model name (possibly versioned) to its canonical name and price.

    Uses prefix matching: 'gpt-5-nano-2025-08-07' -> 'gpt-5-nano'.
    """
    if model in MODEL_PRICING:
        return model, MODEL_PRICING[model]
    for prefix in _SORTED_PREFIXES:
        if model.startswith(prefix):
            return prefix, MODEL_PRICING[prefix]
    raise UnknownModelError(f"Unknown model: {model!r}")


def calculate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    thinking_output_tokens: int = 0,
) -> Decimal:
    """Calculate cost in USD for the given token counts."""
    _, price = resolve_model(model)
    cost = (
        price.input * input_tokens
        + price.output * output_tokens
        + price.thinking_output * thinking_output_tokens
    ) / _PER_M
    return cost.quantize(Decimal("0.000001"))
