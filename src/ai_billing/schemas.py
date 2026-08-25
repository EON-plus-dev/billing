from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class UsageInfo(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    thinking_output_tokens: int = 0
    cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage for cache-aware cost calculation.

    For in-memory cost calculation (input to calculate_cost).
    Use UsageInfo (Pydantic) for serialization/reporting.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Detailed cost breakdown with per-component split and VAT.

    by_component keys: 'input', 'output', 'cache_read', 'cache_write'.
    All amounts in USD, quantized to 6 decimal places.

    by_component is exposed as a read-only Mapping. calculate_cost wraps
    the internal dict in MappingProxyType so callers cannot mutate the
    breakdown after construction (e.g. cb.by_component["input"] = ... raises
    TypeError).
    """
    cost_no_vat: Decimal
    vat: Decimal
    cost_total: Decimal
    by_component: Mapping[str, Decimal] = field(
        default_factory=lambda: MappingProxyType({})
    )


class BalanceInfo(BaseModel):
    organization_id: int
    balance: int
    owner_id: int | None = None
    subscription_tier: str | None = None
    multiplier: Decimal | None = None
    updated_at: datetime | None = None


class BillingExecutionContextV1(BaseModel):
    """Canonical server-owned context for organization-scoped billing.

    context_id identifies a server-owned source record. Interactive Redis
    envelopes are HMAC-authenticated by the transport; durable background
    sources are additionally revalidated against their authoritative service.
    """

    version: Literal[1] = 1
    actor_user_id: int = Field(gt=0)
    fop_organization_id: int = Field(gt=0)
    source: Literal[
        "ai_billing",
        "income_auto_sync",
        "income_manual_sync",
        "fop_limits_schedule",
        "documents_upload",
        "documents_reprocess",
        "documents_refine",
        "staff_doc_http",
        "staff_doc_processing",
        "staff_doc_websocket",
    ]
    revision: int = Field(ge=1)
    context_id: str = Field(min_length=1, max_length=255)

    @field_validator("context_id")
    @classmethod
    def validate_context_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("context_id must not be blank")
        return normalized


class DebitPayload(BaseModel):
    """Redis-payload для debit-задачі.

    Базові поля (v0.4.0): organization_id, amount_usd, service, user_id,
    operation_id, created_at — ідентифікують операцію + готовий cost.

    Phase-3 FIFO context (v0.5.0+): опційні поля для запису в credit_system
    `ai_usage_events` partitioned table та `credit_transactions.role_at_request`:
      - model_id            — каноничне ім'я AI-моделі (claude-haiku-4-5, ...).
      - input_tokens        — input usage tokens.
      - output_tokens       — output usage tokens.
      - cached_input_tokens — Anthropic prompt cache reads.
      - cache_write_tokens  — Anthropic prompt cache writes (5-min cache).
      - feature_type        — 'ai_chat' | 'document_generation' | 'analytics'.
      - caller_user_role    — snapshot ролі юзера на момент запиту.

    Усі context-поля nullable → backward-compat з v0.4.0 caller-ами, які їх
    не передають. credit_system FIFO-flow: коли model_id IS NOT NULL — пише
    повний рядок у ai_usage_events; інакше пропускає (legacy degradation).

    v0.6.0 billing_execution_context: versioned server-owned actor/initiator
    envelope.
    Optional у legacy payload тільки для rolling deployment; нові org-scoped
    producers використовують context-required BillingClient v1 methods.
    """

    organization_id: int | None = None
    amount_usd: Decimal = Field(decimal_places=6)
    service: str
    user_id: int
    actor_user_id: int | None = Field(default=None, gt=0)
    operation_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Phase-3 FIFO context (v0.5.0+).
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    feature_type: str | None = None
    caller_user_role: str | None = None
    # v0.6.0 server-owned actor context. Optional only for rolling compatibility;
    # new organization-scoped producers must provide it.
    billing_execution_context: BillingExecutionContextV1 | None = None
    context_issuer: str | None = Field(default=None, min_length=1, max_length=100)
    context_signature: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def validate_execution_context_mirrors(self) -> "DebitPayload":
        context = self.billing_execution_context
        if context is not None and (
            self.actor_user_id != context.actor_user_id
            or self.organization_id != context.fop_organization_id
        ):
            raise ValueError(
                "actor_user_id and organization_id must mirror "
                "billing_execution_context"
            )
        if (self.context_issuer is None) != (self.context_signature is None):
            raise ValueError(
                "context_issuer and context_signature must be provided together"
            )
        if self.context_signature is not None and context is None:
            raise ValueError("signed context requires billing_execution_context")
        return self
