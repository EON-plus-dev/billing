from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, Field


class UsageInfo(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    thinking_output_tokens: int = 0
    cost_usd: Decimal


class DebitPayload(BaseModel):
    organization_id: int
    amount_usd: Decimal = Field(decimal_places=6)
    service: str
    user_id: int
    operation_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
