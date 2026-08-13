"""Live email adapter over standard-library SMTP and IMAP.

The provider performs real SMTP sending and IMAP reading while keeping the
permissioned draft-review contract: sending always targets an existing draft
and records an idempotent sent result in memory.
"""

from __future__ import annotations

import asyncio
import html
import imaplib
import logging
import re
import smtplib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage as MIMEMessage
from email.message import Message
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parsedate_to_datetime
from uuid import uuid4

from jarvis.integrations.auth import CredentialResolver, SecretCredential
from jarvis.integrations.email.models import (
    EmailAddress,
    EmailDraft,
    EmailDraftRequest,
    EmailMessage,
    EmailSearch,
    EmailSummary,
    SentEmail,
    utc_now,
)
from jarvis.integrations.email.provider import EmailProvider
from jarvis.integrations.errors import (
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationNotConnectedError,
    IntegrationTransportError,
    IntegrationValidationError,
)

logger = logging.getLogger(__name__)

_SUMMARY_SPEC = "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID)] RFC822.SIZE FLAGS)"
_UID_PATTERN = re.compile(rb"\bUID\s+(\d+)")
_TAG_PATTERN = re.compile(r"<[^>]+>")
_INVALID_UID = re.compile(r"[^\w.:+=-]")

ImapFactory = Callable[[str, int, bool, float], "_IMAPConnection"]
SmtpFactory = Callable[[str, int, bool, float], "_SMTPConnection"]


class _IMAPConnection:
    """Bound standard-library IMAP session converted into safe values."""

    def __init__(self, host: str, port: int, ssl: bool, timeout: float) -> None:
        try:
            if ssl:
                self._client: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=timeout)
            else:
                self._client = imaplib.IMAP4(host, port, timeout=timeout)
        except OSError:
            raise IntegrationTransportError("The email server could not be reached") from None

    def login(self, username: str, password: str) -> None:
        try:
            status, _data = self._client.login(username, password)
        except imaplib.IMAP4.error:
            raise IntegrationAuthError("Email credentials were rejected") from None
        if status != "OK":
            raise IntegrationAuthError("Email credentials were rejected")

    def select(self, mailbox: str = "INBOX") -> int:
        try:
            status, data = self._client.select(mailbox)
        except imaplib.IMAP4.error:
            raise IntegrationTransportError("The email mailbox could not be opened") from None
        if status != "OK":
            raise IntegrationTransportError("The email mailbox could not be opened")
        count = data[0] if data else None
        return int(count) if isinstance(count, bytes) else 0

    def search(self, criteria: Sequence[str]) -> list[str]:
        try:
            status, data = self._client.uid("SEARCH", *criteria)
        except imaplib.IMAP4.error:
            raise IntegrationTransportError("Email search failed") from None
        if status != "OK":
            raise IntegrationTransportError("Email search failed")
        tokens = b" ".join(token for token in data if isinstance(token, bytes)).split()
        return [token.decode("ascii") for token in tokens]

    def fetch(self, uids: Sequence[str], spec: str) -> list[tuple[str, bytes, bool]]:
        if not uids:
            return []
        try:
            status, data = self._client.uid("FETCH", ",".join(uids), spec)
        except imaplib.IMAP4.error:
            raise IntegrationTransportError("The email message could not be fetched") from None
        if status != "OK":
            raise IntegrationTransportError("The email message could not be fetched")
        return _parse_fetch(data)

    def logout(self) -> None:
        try:
            self._client.logout()
        except Exception:
            pass


class _SMTPConnection:
    """Bound standard-library SMTP session with sanitized failures."""

    def __init__(self, host: str, port: int, ssl: bool, timeout: float) -> None:
        try:
            if ssl:
                self._client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout)
            else:
                self._client = smtplib.SMTP(host, port, timeout=timeout)
        except OSError:
            raise IntegrationTransportError("The email server could not be reached") from None
        try:
            self._client.ehlo()
        except smtplib.SMTPException:
            raise IntegrationTransportError("The email server rejected the session") from None

    def secure(self) -> None:
        try:
            self._client.starttls()
            self._client.ehlo()
        except smtplib.SMTPException:
            raise IntegrationTransportError("The email server rejected the session") from None

    def login(self, username: str, password: str) -> None:
        try:
            self._client.login(username, password)
        except smtplib.SMTPAuthenticationError:
            raise IntegrationAuthError("Email credentials were rejected") from None
        except smtplib.SMTPException:
            raise IntegrationTransportError("The email server rejected the session") from None

    def send(self, sender: str, recipients: Sequence[str], payload: bytes) -> None:
        try:
            self._client.sendmail(sender, list(recipients), payload)
        except smtplib.SMTPRecipientsRefused:
            raise IntegrationTransportError("The email server refused some recipients") from None
        except smtplib.SMTPSenderRefused:
            raise IntegrationTransportError("The email server refused the sender") from None
        except smtplib.SMTPException:
            raise IntegrationTransportError("The email server could not send the message") from None

    def quit(self) -> None:
        try:
            self._client.quit()
        except Exception:
            self._client.close()


class SMTPEmailProvider(EmailProvider):
    """Real SMTP sending with IMAP reading, preserving draft-review consent."""

    def __init__(
        self,
        credentials: CredentialResolver,
        *,
        credential_id: str = "email.password",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_mode: str = "starttls",
        imap_host: str = "",
        imap_port: int = 993,
        imap_ssl: bool = True,
        username: str = "",
        from_address: str = "",
        timeout_seconds: float = 30.0,
        clock: Callable[[], datetime] = utc_now,
        imap_factory: ImapFactory | None = None,
        smtp_factory: SmtpFactory | None = None,
    ) -> None:
        super().__init__()
        self._credentials = credentials
        self._credential_id = credential_id
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_mode = smtp_mode
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._imap_ssl = imap_ssl
        self._username = username
        self._from_address = from_address or username
        self._timeout = timeout_seconds
        self._clock = clock
        self._imap_factory = imap_factory
        self._smtp_factory = smtp_factory
        self._password: SecretCredential | None = None
        self._drafts: dict[str, EmailDraft] = {}
        self._sent: dict[str, SentEmail] = {}

    async def _connect(self) -> None:
        self._password = self._credentials.resolve(self._credential_id)
        if not self._username:
            raise IntegrationAuthError("Email username is not configured")
        if not self._smtp_host and not self._imap_host:
            raise IntegrationAuthError("No email server host is configured")
        if self._imap_host:
            await asyncio.to_thread(self._verify_imap)
        if self._smtp_host:
            await asyncio.to_thread(self._verify_smtp)

    async def _disconnect(self) -> None:
        self._password = None

    async def list_messages(self, *, limit: int = 25) -> tuple[EmailSummary, ...]:
        self._ensure_connected()
        bounded = _bounded(limit)
        return await asyncio.to_thread(self._list_messages, bounded)

    async def search_messages(
        self, search: EmailSearch, *, limit: int = 25
    ) -> tuple[EmailSummary, ...]:
        self._ensure_connected()
        if not isinstance(search, EmailSearch):
            raise TypeError("search must be EmailSearch")
        bounded = _bounded(limit)
        return await asyncio.to_thread(self._search_messages, search, bounded)

    async def read_message(self, message_id: str) -> EmailMessage:
        self._ensure_connected()
        _message_id(message_id)
        return await asyncio.to_thread(self._read_message, message_id)

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
        if not self._smtp_host:
            raise IntegrationTransportError("No email sending server is configured")
        sent = await asyncio.to_thread(self._send_draft, draft_id)
        self._sent[draft_id] = sent
        return sent

    def _list_messages(self, limit: int) -> tuple[EmailSummary, ...]:
        imap = self._imap_session()
        try:
            imap.login(self._username, self._secret.reveal())
            imap.select("INBOX")
            uids = imap.search(("ALL",))
            summaries = self._summaries(imap, uids[-limit:])
        finally:
            imap.logout()
        summaries.sort(key=lambda item: item.received_at, reverse=True)
        return tuple(summaries[:limit])

    def _search_messages(self, search: EmailSearch, limit: int) -> tuple[EmailSummary, ...]:
        imap = self._imap_session()
        try:
            imap.login(self._username, self._secret.reveal())
            imap.select("INBOX")
            uids = imap.search(_imap_search_criteria(search))
            summaries = self._summaries(imap, uids[-limit:])
        finally:
            imap.logout()
        summaries.sort(key=lambda item: item.received_at, reverse=True)
        return tuple(summaries[:limit])

    def _read_message(self, message_id: str) -> EmailMessage:
        imap = self._imap_session()
        try:
            imap.login(self._username, self._secret.reveal())
            imap.select("INBOX")
            items = imap.fetch((message_id,), "(RFC822)")
            if not items:
                raise IntegrationValidationError("Email message was not found")
            uid, payload, seen = items[0]
            return _parse_message(uid, payload, seen, self._clock())
        finally:
            imap.logout()

    def _summaries(self, imap: _IMAPConnection, uids: Sequence[str]) -> list[EmailSummary]:
        if not uids:
            return []
        summaries: list[EmailSummary] = []
        for uid, payload, seen in imap.fetch(uids, _SUMMARY_SPEC):
            try:
                summaries.append(_parse_message(uid, payload, seen, self._clock()).summary())
            except (IntegrationValidationError, IntegrationDataError):
                logger.debug("email_message_skipped", extra={"uid": uid})
        return summaries

    def _send_draft(self, draft_id: str) -> SentEmail:
        draft = self._drafts[draft_id]
        recipients = (*draft.request.recipients, *draft.request.cc, *draft.request.bcc)
        smtp = self._smtp_session()
        try:
            if self._smtp_mode == "starttls":
                smtp.secure()
            smtp.login(self._username, self._secret.reveal())
            smtp.send(
                self._from_address,
                [address.address for address in recipients],
                _build_message(draft.request, self._from_address),
            )
        finally:
            smtp.quit()
        return SentEmail(uuid4().hex, draft_id, self._clock())

    def _verify_imap(self) -> None:
        imap = self._imap_session()
        try:
            imap.login(self._username, self._secret.reveal())
            imap.select("INBOX")
        finally:
            imap.logout()

    def _verify_smtp(self) -> None:
        smtp = self._smtp_session()
        try:
            if self._smtp_mode == "starttls":
                smtp.secure()
            smtp.login(self._username, self._secret.reveal())
        finally:
            smtp.quit()

    def _imap_session(self) -> _IMAPConnection:
        factory = self._imap_factory or _IMAPConnection
        return factory(self._imap_host, self._imap_port, self._imap_ssl, self._timeout)

    def _smtp_session(self) -> _SMTPConnection:
        factory = self._smtp_factory or _SMTPConnection
        return factory(self._smtp_host, self._smtp_port, self._smtp_mode == "ssl", self._timeout)

    @property
    def _secret(self) -> SecretCredential:
        if self._password is None:
            raise IntegrationNotConnectedError("Email provider is not connected")
        return self._password

    def _ensure_connected(self) -> None:
        if self.status.value != "connected":
            raise IntegrationNotConnectedError("Email provider is not connected")


def _parse_fetch(data: Sequence[object]) -> list[tuple[str, bytes, bool]]:
    result: list[tuple[str, bytes, bool]] = []
    for item in data:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        meta, payload = item
        if not isinstance(meta, bytes) or not isinstance(payload, bytes):
            continue
        match = _UID_PATTERN.search(meta)
        if match is None:
            continue
        uid = match.group(1).decode("ascii")
        result.append((uid, payload, b"\\Seen" in meta))
    return result


def _imap_search_criteria(search: EmailSearch) -> list[str]:
    parts: list[str] = []
    if search.text:
        parts.append(f'TEXT "{_imap_quote(search.text)}"')
    if search.sender:
        parts.append(f'FROM "{_imap_quote(search.sender)}"')
    if search.recipient:
        quoted = _imap_quote(search.recipient)
        parts.append(f'(OR TO "{quoted}" CC "{quoted}")')
    if search.unread is not None:
        parts.append("UNSEEN" if search.unread else "SEEN")
    if search.after is not None:
        parts.append(f'SINCE "{_imap_date(search.after)}"')
    if search.before is not None:
        parts.append(f'BEFORE "{_imap_date(search.before)}"')
    return parts or ["ALL"]


def _imap_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _imap_date(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d-%b-%Y")


def _build_message(request: EmailDraftRequest, sender: str) -> bytes:
    message = MIMEMessage()
    message["From"] = _format_address(sender)
    message["To"] = ", ".join(_format_address(address) for address in request.recipients)
    if request.cc:
        message["Cc"] = ", ".join(_format_address(address) for address in request.cc)
    message["Subject"] = request.subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    message.set_content(request.body_text)
    return message.as_bytes()


def _format_address(value: EmailAddress | str) -> str:
    if isinstance(value, EmailAddress):
        return formataddr((value.name, value.address)) if value.name else value.address
    return value


def _parse_message(uid: str, payload: bytes, seen: bool, fallback: datetime) -> EmailMessage:
    message = message_from_bytes(payload)
    sender_values = _addresses(message.get("From"))
    if not sender_values:
        raise IntegrationDataError("Email message is missing a sender")
    sender = sender_values[0]
    recipients = _addresses(message.get("To")) or (sender,)
    cc = _addresses(message.get("Cc"))
    return EmailMessage(
        uid,
        uid,
        _decode_header(message.get("Subject", "")),
        sender,
        recipients,
        _received_at(message.get("Date"), fallback),
        _body_text(message),
        cc,
        not seen,
    )


def _decode_header(value: str) -> str:
    if not value:
        return ""
    pieces: list[str] = []
    for raw, charset in decode_header(value):
        if isinstance(raw, bytes):
            pieces.append(raw.decode(charset or "utf-8", errors="replace"))
        else:
            pieces.append(str(raw))
    return "".join(pieces).strip()


def _addresses(value: str | None) -> tuple[EmailAddress, ...]:
    if not value:
        return ()
    result: list[EmailAddress] = []
    for name, address in getaddresses([value]):
        address = address.strip()
        if not address:
            continue
        try:
            result.append(EmailAddress(address, name or None))
        except IntegrationValidationError:
            continue
    return tuple(result)


def _received_at(value: str | None, fallback: datetime) -> datetime:
    if value:
        parsed = parsedate_to_datetime(value)
        if parsed is not None:
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return fallback.astimezone(UTC)


def _body_text(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.is_multipart():
                return _payload_text(part)
        for part in message.walk():
            if part.get_content_type() == "text/html":
                return _strip_html(_payload_text(part))
        return ""
    if message.get_content_type() == "text/html":
        return _strip_html(_payload_text(message))
    return _payload_text(message)


def _payload_text(message: Message) -> str:
    decoded = message.get_payload(decode=True)
    if not isinstance(decoded, bytes):
        return ""
    charset = message.get_content_charset() or "utf-8"
    return decoded.decode(charset, errors="replace")


def _strip_html(value: str) -> str:
    return html.unescape(_TAG_PATTERN.sub(" ", value))


def _message_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or _INVALID_UID.search(value):
        raise IntegrationValidationError("Email message id is invalid")
    return value


def _bounded(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise IntegrationValidationError("Email result limit must be between 1 and 100")
    return value
