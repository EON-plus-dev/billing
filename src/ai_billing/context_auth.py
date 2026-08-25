"""HMAC authentication for server-issued billing execution contexts."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping


def execution_context_signature(
    *, secret: str, issuer: str, operation_id: str, context: Mapping[str, Any]
) -> str:
    """Return a deterministic signature bound to one producer and V1 context."""
    normalized_secret = secret.strip()
    normalized_issuer = issuer.strip()
    normalized_operation_id = operation_id.strip()
    if not normalized_secret or not normalized_issuer or not normalized_operation_id:
        raise ValueError("billing context signing credentials are required")
    signed = {
        "issuer": normalized_issuer,
        "operation_id": normalized_operation_id,
        "version": int(context["version"]),
        "actor_user_id": int(context["actor_user_id"]),
        "fop_organization_id": int(context["fop_organization_id"]),
        "source": str(context["source"]),
        "revision": int(context["revision"]),
        "context_id": str(context["context_id"]),
    }
    message = json.dumps(
        signed, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(
        normalized_secret.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def verify_execution_context_signature(
    *,
    secret: str,
    issuer: str,
    operation_id: str,
    context: Mapping[str, Any],
    signature: str,
) -> bool:
    """Verify a producer signature without leaking timing information."""
    try:
        expected = execution_context_signature(
            secret=secret,
            issuer=issuer,
            operation_id=operation_id,
            context=context,
        )
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, (signature or "").strip())
