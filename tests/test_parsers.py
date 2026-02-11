from types import SimpleNamespace

import pytest

from findesk_billing.parsers import parse_response
from findesk_billing.exceptions import ParseError
from conftest import make_openai_response, make_anthropic_response, make_gemini_response


class TestOpenAI:
    def test_basic(self):
        resp = make_openai_response()
        usage = parse_response(resp)
        assert usage.model == "gpt-4o-mini"
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cost_usd > 0

    def test_versioned_model(self):
        resp = make_openai_response(model="gpt-5-nano-2025-08-07")
        usage = parse_response(resp)
        assert usage.model == "gpt-5-nano-2025-08-07"
        assert usage.input_tokens == 100


class TestAnthropic:
    def test_basic(self):
        resp = make_anthropic_response()
        usage = parse_response(resp)
        assert usage.model == "claude-sonnet-4-5-20250929"
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cost_usd > 0

    def test_no_stop_reason_falls_through(self):
        resp = SimpleNamespace(
            model="claude-sonnet-4-5-20250929",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )
        # No stop_reason -> not Anthropic, but has prompt_tokens? No.
        # Has usage.input_tokens but no usage.prompt_tokens and no stop_reason -> falls through
        with pytest.raises(ParseError):
            parse_response(resp)


class TestGemini:
    def test_basic_with_override(self):
        resp = make_gemini_response()
        usage = parse_response(resp, model_override="gemini-2.5-flash")
        assert usage.model == "gemini-2.5-flash"
        assert usage.input_tokens == 100

    def test_no_model_raises(self):
        resp = make_gemini_response()
        with pytest.raises(ParseError, match="model_override"):
            parse_response(resp)

    def test_model_from_response(self):
        resp = make_gemini_response(model="gemini-1.5-flash")
        usage = parse_response(resp)
        assert usage.model == "gemini-1.5-flash"

    def test_thinking_tokens(self):
        resp = make_gemini_response(thoughts_token_count=500, model="gemini-2.5-flash")
        usage = parse_response(resp)
        assert usage.thinking_output_tokens == 500


class TestUnknown:
    def test_plain_object(self):
        with pytest.raises(ParseError):
            parse_response(object())

    def test_dict(self):
        with pytest.raises(ParseError):
            parse_response({"usage": {"prompt_tokens": 10}})
