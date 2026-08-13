"""Email provider contracts and a network-free in-memory implementation."""

from jarvis.integrations.email.models import (
    EmailAddress,
    EmailDraft,
    EmailDraftRequest,
    EmailMessage,
    EmailSearch,
    EmailSummary,
    SentEmail,
)
from jarvis.integrations.email.provider import (
    EMAIL_METADATA,
    EMAIL_OPERATIONS,
    EmailProvider,
    InMemoryEmailProvider,
)

__all__ = [
    "EMAIL_METADATA",
    "EMAIL_OPERATIONS",
    "EmailAddress",
    "EmailDraft",
    "EmailDraftRequest",
    "EmailMessage",
    "EmailProvider",
    "EmailSearch",
    "EmailSummary",
    "InMemoryEmailProvider",
    "SentEmail",
]
