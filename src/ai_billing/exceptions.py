class BillingError(Exception):
    """Base exception for ai-billing."""


class ParseError(BillingError):
    """Failed to extract usage from AI response object."""


class UnknownModelError(BillingError, ValueError):
    """Model name not found in pricing registry.

    Subclass of both BillingError (lib base) and ValueError (per ARCHITECTURE.md §7.3
    contract: calculate_cost raises ValueError on unknown model_id).
    """
