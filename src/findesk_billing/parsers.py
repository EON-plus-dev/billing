from __future__ import annotations

from typing import Any

from .exceptions import ParseError
from .schemas import UsageInfo
from .pricing import calculate_cost


def _safe_getattr(obj: Any, *attrs: str) -> Any:
    """Walk nested attributes, return None on any miss."""
    for attr in attrs:
        obj = getattr(obj, attr, None)
        if obj is None:
            return None
    return obj


def _try_openai(response: Any) -> UsageInfo | None:
    """Detect OpenAI ChatCompletion: response.usage.prompt_tokens."""
    prompt_tokens = _safe_getattr(response, "usage", "prompt_tokens")
    if prompt_tokens is None:
        return None
    completion_tokens = _safe_getattr(response, "usage", "completion_tokens") or 0
    model = getattr(response, "model", None)
    if model is None:
        return None
    cost = calculate_cost(model, input_tokens=prompt_tokens, output_tokens=completion_tokens)
    return UsageInfo(
        model=model,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        cost_usd=cost,
    )


def _try_anthropic(response: Any) -> UsageInfo | None:
    """Detect Anthropic Message: response.usage.input_tokens + response.stop_reason."""
    input_tokens = _safe_getattr(response, "usage", "input_tokens")
    stop_reason = getattr(response, "stop_reason", None)
    if input_tokens is None or stop_reason is None:
        return None
    output_tokens = _safe_getattr(response, "usage", "output_tokens") or 0
    model = getattr(response, "model", None)
    if model is None:
        return None
    cost = calculate_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)
    return UsageInfo(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )


def _try_gemini(response: Any, model_override: str | None = None) -> UsageInfo | None:
    """Detect Gemini GenerateContentResponse: response.usage_metadata.prompt_token_count."""
    prompt_token_count = _safe_getattr(response, "usage_metadata", "prompt_token_count")
    if prompt_token_count is None:
        return None
    candidates_token_count = (
        _safe_getattr(response, "usage_metadata", "candidates_token_count") or 0
    )
    thinking_token_count = (
        _safe_getattr(response, "usage_metadata", "thoughts_token_count") or 0
    )
    model = model_override or getattr(response, "model", None)
    if model is None:
        raise ParseError("Gemini response detected but no model name; pass model_override")
    cost = calculate_cost(
        model,
        input_tokens=prompt_token_count,
        output_tokens=candidates_token_count,
        thinking_output_tokens=thinking_token_count,
    )
    return UsageInfo(
        model=model,
        input_tokens=prompt_token_count,
        output_tokens=candidates_token_count,
        thinking_output_tokens=thinking_token_count,
        cost_usd=cost,
    )


def parse_response(response: Any, *, model_override: str | None = None) -> UsageInfo:
    """Auto-detect provider and extract usage from an AI response object.

    Tries OpenAI -> Anthropic -> Gemini in order.
    """
    for parser in (_try_openai, _try_anthropic):
        result = parser(response)
        if result is not None:
            return result

    result = _try_gemini(response, model_override=model_override)
    if result is not None:
        return result

    raise ParseError(f"Cannot detect AI provider from response: {type(response).__name__}")
