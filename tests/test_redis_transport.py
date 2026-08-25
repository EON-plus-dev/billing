import json
from decimal import Decimal

import pytest
import fakeredis.aioredis

from ai_billing.redis_transport import RedisTransport
from ai_billing.context_auth import verify_execution_context_signature
from ai_billing.schemas import BillingExecutionContextV1, DebitPayload


@pytest.fixture
def transport(monkeypatch):
    t = RedisTransport(
        "redis://localhost:6380",
        context_secret="test-context-secret",
        context_issuer="ai_chat",
    )
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

    async def test_context_roundtrip_preserves_exact_nested_payload(self, transport):
        context = BillingExecutionContextV1(
            actor_user_id=17,
            fop_organization_id=42,
            source="income_auto_sync",
            revision=5,
            context_id="auto-sync:42",
        )
        payload = DebitPayload(
            organization_id=42,
            amount_usd=Decimal("0.0042"),
            service="ai_chat",
            user_id=999,
            actor_user_id=17,
            operation_id="context-op",
            billing_execution_context=context,
        )

        await transport.write_debit(payload)

        raw = await transport._redis.get("debit:context-op")
        data = json.loads(raw)
        assert data["actor_user_id"] == 17
        assert data["billing_execution_context"] == {
            "version": 1,
            "actor_user_id": 17,
            "fop_organization_id": 42,
            "source": "income_auto_sync",
            "revision": 5,
            "context_id": "auto-sync:42",
        }
        assert data["context_issuer"] == "ai_chat"
        assert verify_execution_context_signature(
            secret="test-context-secret",
            issuer=data["context_issuer"],
            operation_id="context-op",
            context=data["billing_execution_context"],
            signature=data["context_signature"],
        )
        restored = DebitPayload.model_validate_json(raw)
        assert restored.billing_execution_context == context
        assert restored.actor_user_id == 17
        assert restored.context_issuer == "ai_chat"

    async def test_context_write_without_server_secret_fails_closed(self):
        transport = RedisTransport("redis://localhost:6380")
        transport._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        context = BillingExecutionContextV1(
            actor_user_id=17,
            fop_organization_id=42,
            source="ai_billing",
            revision=1,
            context_id="ai_chat:interactive:42:17",
        )
        payload = DebitPayload(
            organization_id=42,
            amount_usd=Decimal("0.0042"),
            service="ai_chat",
            user_id=999,
            actor_user_id=17,
            operation_id="unsigned-context-op",
            billing_execution_context=context,
        )

        with pytest.raises(RuntimeError, match="BILLING_CONTEXT_SIGNING_REQUIRED"):
            await transport.write_debit(payload)

    async def test_legacy_payload_omits_context_envelope(self, transport):
        payload = DebitPayload(
            organization_id=42,
            amount_usd=Decimal("0.0042"),
            service="ai_chat",
            user_id=7,
            operation_id="legacy-op",
        )

        await transport.write_debit(payload)

        raw = await transport._redis.get("debit:legacy-op")
        data = json.loads(raw)
        assert "billing_execution_context" not in data
        assert "actor_user_id" not in data
        restored = DebitPayload.model_validate_json(raw)
        assert restored.billing_execution_context is None
        assert restored.actor_user_id is None

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
