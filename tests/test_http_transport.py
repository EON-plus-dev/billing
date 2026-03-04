from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_billing.http_transport import HttpTransport


@pytest.fixture
def transport():
    return HttpTransport(
        credit_system_url="http://credit-system:7020",
        service_name="test_svc",
        secret_key="super-secret-key-for-testing-1234",
    )


class TestGetToken:
    def test_generates_valid_jwt(self, transport):
        import jwt

        token = transport._get_token()
        payload = jwt.decode(token, "super-secret-key-for-testing-1234", algorithms=["HS256"])
        assert payload["sub"] == "test_svc"
        assert payload["type"] == "internal_service"
        assert payload["exp"] > time.time()

    def test_caches_token(self, transport):
        token1 = transport._get_token()
        token2 = transport._get_token()
        assert token1 is token2

    def test_refreshes_expired_token(self, transport):
        token1 = transport._get_token()
        # Simulate token about to expire (within refresh margin)
        transport._token_expires_at = time.time() + 30  # < 60s margin
        token2 = transport._get_token()
        assert token1 is not token2


class TestCheckBalance:
    async def test_happy_path(self, transport):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"sufficient": True, "current_balance": 50000})

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.closed = False
        transport._session = mock_session

        result = await transport.check_balance(organization_id=42)

        assert result is not None
        assert result.organization_id == 42
        assert result.balance == 50000

        mock_session.post.assert_awaited_once()
        call_kwargs = mock_session.post.call_args
        assert "/internal/check-balance" in call_kwargs.args[0]
        assert call_kwargs.kwargs["json"] == {"organization_id": 42, "required_credits": 0}

    async def test_non_200_returns_none(self, transport):
        mock_resp = AsyncMock()
        mock_resp.status = 404

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_resp)
        mock_session.closed = False
        transport._session = mock_session

        result = await transport.check_balance(organization_id=999)
        assert result is None

    async def test_network_error_returns_none(self, transport):
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_session.closed = False
        transport._session = mock_session

        result = await transport.check_balance(organization_id=42)
        assert result is None

    async def test_timeout_returns_none(self, transport):
        import asyncio

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_session.closed = False
        transport._session = mock_session

        result = await transport.check_balance(organization_id=42)
        assert result is None


class TestClose:
    async def test_closes_session(self, transport):
        mock_session = AsyncMock()
        mock_session.closed = False
        transport._session = mock_session

        await transport.close()

        mock_session.close.assert_awaited_once()

    async def test_noop_when_no_session(self, transport):
        await transport.close()  # should not raise
