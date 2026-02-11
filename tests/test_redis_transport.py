import json
from decimal import Decimal

import pytest
import fakeredis.aioredis

from ai_billing.redis_transport import RedisTransport
from ai_billing.schemas import DebitPayload


@pytest.fixture
def transport(monkeypatch):
    t = RedisTransport("redis://localhost:6380")
    t._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return t


class TestWriteDebit:
    async def test_creates_key_and_queue(self, transport):
        payload = DebitPayload(
            organization_id=123,
            amount_usd=Decimal("0.0004"),
            service="ai_chat",
            user_id=456,
            operation_id="test-op-1",
        )
        op_id = await transport.write_debit(payload)
        assert op_id == "test-op-1"

        redis = transport._redis
        raw = await redis.get("debit:test-op-1")
        assert raw is not None
        data = json.loads(raw)
        assert data["organization_id"] == 123
        assert data["user_id"] == 456
        assert data["service"] == "ai_chat"
        assert Decimal(data["amount_usd"]) == Decimal("0.0004")

        members = await redis.smembers("debit:queue")
        assert "test-op-1" in members

    async def test_ttl_set(self, transport):
        payload = DebitPayload(
            organization_id=1,
            amount_usd=Decimal("0.01"),
            service="test",
            user_id=1,
        )
        op_id = await transport.write_debit(payload)
        ttl = await transport._redis.ttl(f"debit:{op_id}")
        assert ttl > 0
        assert ttl <= 86400

    async def test_close(self, transport):
        await transport.close()
        assert transport._redis is None


class TestReadBalance:
    async def test_returns_balance(self, transport):
        await transport._redis.set(
            "credits:org:42",
            json.dumps({
                "balance": 50000,
                "owner_id": 7,
                "updated_at": "2026-02-11T10:00:00+00:00",
                "subscription_tier": "premium",
                "multiplier": "1.8",
            }),
        )
        info = await transport.read_balance(42)
        assert info is not None
        assert info.organization_id == 42
        assert info.balance == 50000
        assert info.owner_id == 7
        assert info.subscription_tier == "premium"

    async def test_returns_none_on_miss(self, transport):
        info = await transport.read_balance(999)
        assert info is None
