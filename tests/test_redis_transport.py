import json
from decimal import Decimal

import pytest
import fakeredis.aioredis

from findesk_billing.redis_transport import RedisTransport
from findesk_billing.schemas import DebitPayload


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
