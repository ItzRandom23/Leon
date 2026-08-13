"""Provider-neutral email contract and side-effect-free in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import uuid4

from jarvis.integrations.base import (
    IntegrationMetadata,
    IntegrationOperation,
    OperationKind,
    StatefulIntegration,
)
from jarvis.integrations.email.models import (
    EmailDraft,
    EmailDraftRequest,
    EmailMessage,
    EmailSearch,
    EmailSummary,
    SentEmail,
    utc_now,
)
from jarvis.integrations.errors import IntegrationNotConnectedError, IntegrationValidationError
from jarvis.skills.base import RiskLevel

EMAIL_OPERATIONS = (
    IntegrationOperation(
        "email.list_messages", OperationKind.READ, RiskLevel.SENSITIVE, "List recent email"
    ),
    IntegrationOperation(
        "email.search_messages", OperationKind.READ, RiskLevel.SENSITIVE, "Search email"
    ),
    IntegrationOperation(
        "email.read_message", OperationKind.READ, RiskLevel.SENSITIVE, "Read an email"
    ),
    IntegrationOperation(
        "email.create_draft", OperationKind.WRITE, RiskLevel.SENSITIVE, "Create an email draft"
    ),
    IntegrationOperation(
        "email.send_message",
        OperationKind.WRITE,
        RiskLevel.SENSITIVE,
        "Send an email draft",
        confirmation_required=True,
    ),
)

EMAIL_METADATA = IntegrationMetadata(
    "email",
    "Email",
    "Provider-neutral email access; sending requires explicit permission.",
    EMAIL_OPERATIONS,
)


class EmailProvider(StatefulIntegration, ABC):
    """Contract for Gmail or another future secure email adapter."""

    def __init__(self, metadata: IntegrationMetadata = EMAIL_METADATA) -> None:
        super().__init__(metadata)

    @abstractmethod
    async def list_messages(self, *, limit: int = 25) -> tuple[EmailSummary, ...]:
        """List recent messages without returning full bodies."""

    @abstractmethod
    async def search_messages(
        self, search: EmailSearch, *, limit: int = 25
    ) -> tuple[EmailSummary, ...]:
        """Search message metadata and provider-indexed content."""

    @abstractmethod
    async def read_message(self, message_id: str) -> EmailMessage:
        """Read one full message."""

    @abstractmethod
    async def create_draft(self, request: EmailDraftRequest) -> EmailDraft:
        """Create a reviewable draft without sending it."""

    @abstractmethod
    async def read_draft(self, draft_id: str) -> EmailDraft:
        """Read one immutable draft so consent can bind to its exact contents."""

    @abstractmethod
    async def send_message(self, draft_id: str) -> SentEmail:
        """Send an existing draft after the caller's permission check."""


class InMemoryEmailProvider(EmailProvider):
    """Deterministic provider for tests/demos; it performs no network or SMTP calls."""

    def __init__(
        self,
        messages: Sequence[EmailMessage] = (),
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        super().__init__()
        self._messages = {message.id: message for message in messages}
        if len(self._messages) != len(messages):
            raise IntegrationValidationError("Email message ids must be unique")
        self._drafts: dict[str, EmailDraft] = {}
        self._sent: dict[str, SentEmail] = {}
        self._clock = clock

    async def _connect(self) -> None:
        return None

    async def _disconnect(self) -> None:
        return None

    async def list_messages(self, *, limit: int = 25) -> tuple[EmailSummary, ...]:
        self._ensure_connected()
        bounded = _limit(limit)
        values = sorted(self._messages.values(), key=lambda item: item.received_at, reverse=True)
        return tuple(message.summary() for message in values[:bounded])

    async def search_messages(
        self, search: EmailSearch, *, limit: int = 25
    ) -> tuple[EmailSummary, ...]:
        self._ensure_connected()
        if not isinstance(search, EmailSearch):
            raise TypeError("search must be EmailSearch")
        matched = [message for message in self._messages.values() if _matches(message, search)]
        matched.sort(key=lambda item: item.received_at, reverse=True)
        return tuple(message.summary() for message in matched[: _limit(limit)])

    async def read_message(self, message_id: str) -> EmailMessage:
        self._ensure_connected()
        try:
            return self._messages[message_id]
        except KeyError:
            raise IntegrationValidationError("Email message was not found") from None

    async def create_draft(self, request: EmailDraftRequest) -> EmailDraft:
        self._ensure_connected()
        if not isinstance(request, EmailDraftRequest):
            raise TypeError("request must be EmailDraftRequest")
        draft = EmailDraft(uuid4().hex, request, self._clock())
        self._drafts[draft.id] = draft
        return draft

    async def read_draft(self, draft_id: str) -> EmailDraft:
        self._ensure_connected()
        try:
            return self._drafts[draft_id]
        except KeyError:
            raise IntegrationValidationError("Email draft was not found") from None

    async def send_message(self, draft_id: str) -> SentEmail:
        self._ensure_connected()
        if draft_id in self._sent:
            return self._sent[draft_id]
        if draft_id not in self._drafts:
            raise IntegrationValidationError("Email draft was not found")
        sent = SentEmail(uuid4().hex, draft_id, self._clock())
        self._sent[draft_id] = sent
        return sent

    def _ensure_connected(self) -> None:
        if self.status.value != "connected":
            raise IntegrationNotConnectedError("Email provider is not connected")


def _limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise IntegrationValidationError("Email result limit must be between 1 and 100")
    return value


def _matches(message: EmailMessage, search: EmailSearch) -> bool:
    if search.unread is not None and message.unread is not search.unread:
        return False
    if search.after is not None and message.received_at <= search.after:
        return False
    if search.before is not None and message.received_at >= search.before:
        return False
    sender = f"{message.sender.name or ''} {message.sender.address}".casefold()
    recipients = " ".join(
        f"{address.name or ''} {address.address}" for address in (*message.recipients, *message.cc)
    ).casefold()
    if search.sender and search.sender.casefold() not in sender:
        return False
    if search.recipient and search.recipient.casefold() not in recipients:
        return False
    if search.text:
        haystack = f"{message.subject}\n{message.body_text}\n{sender}\n{recipients}".casefold()
        if search.text.casefold() not in haystack:
            return False
    return True
