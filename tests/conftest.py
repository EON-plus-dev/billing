from __future__ import annotations

from types import SimpleNamespace

import pytest


def make_openai_response(
    model: str = "gpt-4o-mini",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def make_anthropic_response(
    model: str = "claude-sonnet-4-5-20250929",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def make_gemini_response(
    prompt_token_count: int = 100,
    candidates_token_count: int = 50,
    thoughts_token_count: int = 0,
    model: str | None = None,
) -> SimpleNamespace:
    meta = SimpleNamespace(
        prompt_token_count=prompt_token_count,
        candidates_token_count=candidates_token_count,
        thoughts_token_count=thoughts_token_count,
    )
    resp = SimpleNamespace(usage_metadata=meta)
    if model is not None:
        resp.model = model
    return resp
