"""Typed email values shared by provider implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from jarvis.integrations.errors import IntegrationValidationError


@dataclass(frozen=True, slots=True)
class EmailAddress:
    address: str
    name: str | None = None

    def __post_init__(self) -> None:
        address = self.address.strip()
        if (
            not address
            or len(address) > 320
            or address.count("@") != 1
            or any(char.isspace() or ord(char) < 32 for char in address)
        ):
            raise IntegrationValidationError("Email address is invalid")
        local, domain = address.rsplit("@", 1)
        if (
            not local
            or not domain
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
        ):
            raise IntegrationValidationError("Email address is invalid")
        name = None if self.name is None else self.name.strip()
        if name is not None and (not name or len(name) > 200 or _has_controls(name)):
            raise IntegrationValidationError("Email display name is invalid")
        object.__setattr__(self, "address", address)
        object.__setattr__(self, "name", name)

    def to_json(self) -> dict[str, str | None]:
        return {"address": self.address, "name": self.name}


@dataclass(frozen=True, slots=True)
class EmailSummary:
    id: str
    thread_id: str
    subject: str
    sender: EmailAddress
    recipients: tuple[EmailAddress, ...]
    received_at: datetime
    unread: bool = True
    snippet: str = ""

    def __post_init__(self) -> None:
        _identifier(self.id, "message id")
        _identifier(self.thread_id, "thread id")
        _header(self.subject, "subject", maximum=998)
        if not isinstance(self.sender, EmailAddress):
            raise IntegrationValidationError("Email sender must be an EmailAddress")
        _addresses(self.recipients, "recipients", required=True)
        object.__setattr__(self, "received_at", _aware(self.received_at, "received_at"))
        if (
            not isinstance(self.unread, bool)
            or not isinstance(self.snippet, str)
            or len(self.snippet) > 1000
            or _has_controls(self.snippet, allow_newlines=True)
        ):
            raise IntegrationValidationError("Email snippet is invalid")

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "sender": self.sender.to_json(),
            "recipients": [address.to_json() for address in self.recipients],
            "received_at": self.received_at.isoformat(),
            "unread": self.unread,
            "snippet": self.snippet,
        }


@dataclass(frozen=True, slots=True)
class EmailMessage:
    id: str
    thread_id: str
    subject: str
    sender: EmailAddress
    recipients: tuple[EmailAddress, ...]
    received_at: datetime
    body_text: str = field(repr=False)
    cc: tuple[EmailAddress, ...] = ()
    unread: bool = True

    def __post_init__(self) -> None:
        _identifier(self.id, "message id")
        _identifier(self.thread_id, "thread id")
        _header(self.subject, "subject", maximum=998)
        if not isinstance(self.sender, EmailAddress):
            raise IntegrationValidationError("Email sender must be an EmailAddress")
        _addresses(self.recipients, "recipients", required=True)
        _addresses(self.cc, "cc")
        object.__setattr__(self, "received_at", _aware(self.received_at, "received_at"))
        _body(self.body_text)
        if not isinstance(self.unread, bool):
            raise IntegrationValidationError("Email unread state must be a boolean")

    def summary(self) -> EmailSummary:
        snippet = " ".join(self.body_text.split())[:240]
        return EmailSummary(
            self.id,
            self.thread_id,
            self.subject,
            self.sender,
            self.recipients,
            self.received_at,
            self.unread,
            snippet,
        )

    def to_json(self) -> dict[str, object]:
        value = self.summary().to_json()
        value.update(
            {
                "body_text": self.body_text,
                "cc": [address.to_json() for address in self.cc],
            }
        )
        return value


@dataclass(frozen=True, slots=True)
class EmailDraftRequest:
    recipients: tuple[EmailAddress, ...]
    subject: str
    body_text: str = field(repr=False)
    cc: tuple[EmailAddress, ...] = ()
    bcc: tuple[EmailAddress, ...] = ()

    def __post_init__(self) -> None:
        _addresses(self.recipients, "recipients", required=True)
        _addresses(self.cc, "cc")
        _addresses(self.bcc, "bcc")
        _header(self.subject, "subject", maximum=998)
        _body(self.body_text)

    def to_json(self) -> dict[str, object]:
        return {
            "recipients": [address.to_json() for address in self.recipients],
            "subject": self.subject,
            "body_text": self.body_text,
            "cc": [address.to_json() for address in self.cc],
            "bcc": [address.to_json() for address in self.bcc],
        }


@dataclass(frozen=True, slots=True)
class EmailDraft:
    id: str
    request: EmailDraftRequest = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.id, "draft id")
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "request": self.request.to_json(),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SentEmail:
    id: str
    draft_id: str
    sent_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.id, "sent message id")
        _identifier(self.draft_id, "draft id")
        object.__setattr__(self, "sent_at", _aware(self.sent_at, "sent_at"))

    def to_json(self) -> dict[str, str]:
        return {"id": self.id, "draft_id": self.draft_id, "sent_at": self.sent_at.isoformat()}


@dataclass(frozen=True, slots=True)
class EmailSearch:
    text: str = ""
    sender: str | None = None
    recipient: str | None = None
    unread: bool | None = None
    after: datetime | None = None
    before: datetime | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.text, str)
            or len(self.text) > 500
            or _has_controls(self.text, allow_newlines=True)
        ):
            raise IntegrationValidationError("Email search text is invalid")
        for name in ("sender", "recipient"):
            value = getattr(self, name)
            if value is not None and (
                not value.strip() or len(value) > 320 or _has_controls(value)
            ):
                raise IntegrationValidationError(f"Email search {name} is invalid")
        if self.after is not None:
            object.__setattr__(self, "after", _aware(self.after, "after"))
        if self.before is not None:
            object.__setattr__(self, "before", _aware(self.before, "before"))
        if self.after is not None and self.before is not None and self.after >= self.before:
            raise IntegrationValidationError("Email search range is invalid")

    def to_json(self) -> dict[str, str | bool | None]:
        return {
            "text": self.text,
            "sender": self.sender,
            "recipient": self.recipient,
            "unread": self.unread,
            "after": None if self.after is None else self.after.isoformat(),
            "before": None if self.before is None else self.before.isoformat(),
        }


def utc_now() -> datetime:
    return datetime.now(UTC)


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or _has_controls(value):
        raise IntegrationValidationError(f"Email {name} is invalid")
    return value


def _header(value: str, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum or _has_controls(value):
        raise IntegrationValidationError(f"Email {name} is invalid")
    return value


def _body(value: str) -> None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1024 * 1024 or "\x00" in value:
        raise IntegrationValidationError("Email body is invalid or too large")


def _addresses(
    values: tuple[EmailAddress, ...], name: str, *, required: bool = False
) -> tuple[EmailAddress, ...]:
    if not isinstance(values, tuple) or not all(isinstance(item, EmailAddress) for item in values):
        raise IntegrationValidationError(f"Email {name} must be EmailAddress values")
    if required and not values:
        raise IntegrationValidationError(f"Email {name} cannot be empty")
    if len(values) > 100:
        raise IntegrationValidationError(f"Email {name} has too many addresses")
    return values


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntegrationValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _has_controls(value: str, *, allow_newlines: bool = False) -> bool:
    allowed = {"\n", "\r", "\t"} if allow_newlines else set()
    return any(ord(char) < 32 and char not in allowed for char in value)
