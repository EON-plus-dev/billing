class BillingError(Exception):
    """Base exception for findesk-billing."""


class ParseError(BillingError):
    """Failed to extract usage from AI response object."""


class UnknownModelError(BillingError):
    """Model name not found in pricing registry."""
