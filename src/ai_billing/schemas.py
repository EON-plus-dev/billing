from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from pydantic import BaseModel, Field


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


class DebitPayload(BaseModel):
    """Redis-payload для debit-задачі.

    Базові поля (v0.4.0): organization_id, amount_usd, service, user_id,
    operation_id, created_at — ідентифікують операцію + готовий cost.

    Phase-3 FIFO context (v0.5.0+): опційні поля для запису в credit_system
    `ai_usage_events` partitioned table та `credit_transactions.role_at_request`:
      - model_id            — каноничне ім'я AI-моделі (claude-haiku-4-5, ...).
      - input_tokens        — input usage tokens.
      - output_tokens       — output usage tokens.
      - cached_input_tokens — provider-reported prompt cache reads.
      - cache_write_tokens  — Anthropic prompt cache writes (5-min cache).
      - feature_type        — 'ai_chat' | 'document_generation' | 'analytics'.
      - caller_user_role    — snapshot ролі юзера на момент запиту.

    Усі context-поля nullable → backward-compat з v0.4.0 caller-ами, які їх
    не передають. credit_system FIFO-flow: коли model_id IS NOT NULL — пише
    повний рядок у ai_usage_events; інакше пропускає (legacy degradation).
    """

    organization_id: int | None = None
    amount_usd: Decimal = Field(decimal_places=6)
    service: str
    user_id: int
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
