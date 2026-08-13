"""Deterministic Phase 9 integration, transport, and provider tests."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.integrations import (
    ChainedCredentialResolver,
    CredentialNotFoundError,
    DuplicateIntegrationError,
    EnvironmentCredentialResolver,
    HTTPSJSONTransport,
    IntegrationAuthError,
    IntegrationLifecycleError,
    IntegrationMetadata,
    IntegrationNotConnectedError,
    IntegrationOperation,
    IntegrationRegistry,
    IntegrationRegistryClosedError,
    IntegrationStatus,
    IntegrationTransportError,
    IntegrationValidationError,
    OperationKind,
    SecretCredential,
    StatefulIntegration,
    StaticCredentialResolver,
    StrictHTTPSRedirectHandler,
)
from jarvis.integrations.calendar import (
    CALENDAR_OPERATIONS,
    CalDAVCalendarProvider,
    CalendarEvent,
    CalendarEventRequest,
    CalendarEventUpdate,
    CalendarSearch,
    InMemoryCalendarProvider,
)
from jarvis.integrations.email import (
    EMAIL_OPERATIONS,
    EmailAddress,
    EmailDraftRequest,
    EmailMessage,
    EmailSearch,
    InMemoryEmailProvider,
    SMTPEmailProvider,
)
from jarvis.integrations.github import (
    GITHUB_API_VERSION,
    GITHUB_OPERATIONS,
    GitHubClient,
    GitHubIntegration,
    GitHubUser,
)
from jarvis.skills.base import RiskLevel

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


class DummyIntegration(StatefulIntegration):
    def __init__(
        self,
        name: str,
        *,
        fail_connect: bool = False,
        fail_disconnect: bool = False,
    ) -> None:
        super().__init__(IntegrationMetadata(name, name.title(), f"Test {name}"))
        self.fail_connect = fail_connect
        self.fail_disconnect = fail_disconnect
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def _connect(self) -> None:
        self.connect_calls += 1
        if self.fail_connect:
            raise RuntimeError("internal secret-token diagnostic")

    async def _disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.fail_disconnect:
            raise RuntimeError("internal secret-token diagnostic")


class QueueTransport:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, path: str, **kwargs: object) -> object:
        self.calls.append({"method": method, "path": path, **kwargs})
        return self.responses.pop(0)


def user_payload() -> dict[str, object]:
    return {"login": "octocat", "id": 1, "html_url": "https://github.com/octocat"}


def repository_payload() -> dict[str, object]:
    return {
        "id": 10,
        "name": "jarvis",
        "full_name": "octocat/jarvis",
        "owner": user_payload(),
        "private": True,
        "html_url": "https://github.com/octocat/jarvis",
        "description": "Assistant",
        "default_branch": "main",
    }


def issue_payload(*, number: int = 2, pull_request: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "id": 20 + number,
        "number": number,
        "title": "Fix startup",
        "state": "open",
        "html_url": f"https://github.com/octocat/jarvis/issues/{number}",
        "user": user_payload(),
        "body": "Details",
        "labels": [{"name": "bug"}],
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
    }
    if pull_request:
        result["pull_request"] = {"url": "https://api.github.com/pulls/3"}
    return result


def pull_payload() -> dict[str, object]:
    return {
        "id": 31,
        "number": 3,
        "title": "Improve startup",
        "state": "open",
        "html_url": "https://github.com/octocat/jarvis/pull/3",
        "user": user_payload(),
        "body": None,
        "draft": False,
        "merged": False,
        "head": {"ref": "feature"},
        "base": {"ref": "main"},
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
    }


def workflow_payload() -> dict[str, object]:
    return {
        "id": 40,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "state": "active",
        "html_url": "https://github.com/octocat/jarvis/actions/workflows/ci.yml",
    }


def workflow_run_payload() -> dict[str, object]:
    return {
        "id": 41,
        "name": "CI",
        "workflow_id": 40,
        "run_number": 12,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/octocat/jarvis/actions/runs/41",
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:01:00Z",
    }


def release_payload() -> dict[str, object]:
    return {
        "id": 50,
        "tag_name": "v0.2.0",
        "name": "Foundation",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/octocat/jarvis/releases/tag/v0.2.0",
        "published_at": "2026-08-10T00:00:00Z",
        "body": "Release notes",
    }


def test_operation_metadata_enforces_read_write_delete_semantics() -> None:
    write = IntegrationOperation(
        "service.write",
        OperationKind.WRITE,
        RiskLevel.SENSITIVE,
        "Write data",
        confirmation_required=True,
    )
    assert write.mutates_external_state is True
    assert write.to_json()["risk_level"] == "SENSITIVE"
    with pytest.raises(ValueError, match="cannot use READ"):
        IntegrationOperation("service.write", OperationKind.WRITE, RiskLevel.READ, "Write")
    with pytest.raises(ValueError, match="DESTRUCTIVE"):
        IntegrationOperation("service.delete", OperationKind.DELETE, RiskLevel.SENSITIVE, "Delete")


def test_builtin_operation_catalogues_mark_mutations_for_confirmation() -> None:
    operations = {
        item.name: item for item in (*GITHUB_OPERATIONS, *EMAIL_OPERATIONS, *CALENDAR_OPERATIONS)
    }
    assert operations["github.create_issue"].confirmation_required is True
    assert operations["email.send_message"].kind is OperationKind.WRITE
    assert operations["email.send_message"].confirmation_required is True
    assert operations["calendar.delete_event"].risk_level is RiskLevel.DESTRUCTIVE
    assert operations["calendar.delete_event"].confirmation_required is True


def test_credentials_are_injected_and_never_revealed_by_string_forms() -> None:
    token = SecretCredential("ghp_super-secret")
    static = StaticCredentialResolver({"github.token": token})
    environment = EnvironmentCredentialResolver(
        {"email.oauth": "EMAIL_OAUTH_TOKEN"},
        environ={"EMAIL_OAUTH_TOKEN": "email-super-secret"},
    )

    assert token.reveal() == "ghp_super-secret"
    assert "ghp_super-secret" not in repr(token)
    assert "ghp_super-secret" not in str(token)
    assert "ghp_super-secret" not in repr(static)
    assert environment.resolve("email.oauth").reveal() == "email-super-secret"
    assert "email-super-secret" not in repr(environment)


def test_chained_credential_resolver_and_missing_credentials_are_safe() -> None:
    resolver = ChainedCredentialResolver(
        [StaticCredentialResolver({}), StaticCredentialResolver({"github.token": "secret"})]
    )
    assert resolver.resolve("github.token").reveal() == "secret"
    with pytest.raises(CredentialNotFoundError) as caught:
        resolver.resolve("calendar.oauth")
    assert "secret" not in str(caught.value)


def test_registry_lifecycle_snapshots_and_duplicate_protection() -> None:
    async def scenario() -> None:
        registry = IntegrationRegistry()
        beta = registry.register(DummyIntegration("beta"))
        registry.register(DummyIntegration("alpha"))
        assert [snapshot.metadata.name for snapshot in registry.list()] == ["alpha", "beta"]
        assert registry.get("beta") is beta
        assert (await registry.connect("beta")).status is IntegrationStatus.CONNECTED
        assert (await registry.disconnect("beta")).status is IntegrationStatus.DISCONNECTED
        with pytest.raises(DuplicateIntegrationError):
            registry.register(DummyIntegration("beta"))
        removed = await registry.unregister("beta")
        assert removed is beta and beta.status is IntegrationStatus.CLOSED
        assert "beta" not in registry and registry.names == ("alpha",)
        assert await registry.unregister("missing") is None
        report = await registry.close()
        assert report.failures == ()
        assert report.closed == ("alpha",)
        assert json.loads(json.dumps(report.to_json())) == report.to_json()
        with pytest.raises(IntegrationRegistryClosedError):
            registry.get("alpha")

    asyncio.run(scenario())


def test_registry_sanitizes_lifecycle_errors_and_isolates_close_failures() -> None:
    async def scenario() -> None:
        registry = IntegrationRegistry()
        registry.register(DummyIntegration("bad-connect", fail_connect=True))
        bad_close = registry.register(DummyIntegration("bad-close", fail_disconnect=True))
        healthy = registry.register(DummyIntegration("healthy"))
        with pytest.raises(IntegrationLifecycleError) as caught:
            await registry.connect("bad-connect")
        assert "secret-token" not in str(caught.value)
        with pytest.raises(IntegrationLifecycleError):
            await registry.unregister("bad-close")
        assert "bad-close" in registry
        report = await registry.close()
        assert healthy.status is IntegrationStatus.CLOSED
        assert bad_close.status is IntegrationStatus.FAILED
        assert report.closed == ("bad-connect", "healthy")
        assert report.failures[0].integration == "bad-close"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.test",
        "https://user:password@api.example.test",
        "https://api.example.test?token=x",
        "https://api.example.test/#fragment",
    ],
)
def test_transport_requires_clean_absolute_https_base(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HTTPSJSONTransport(url)


def test_transport_builds_bounded_requests_and_copies_json_responses() -> None:
    calls: list[tuple[object, ...]] = []
    response = {"items": [{"ok": True}]}

    def requester(*args: object) -> object:
        calls.append(args)
        return response

    transport = HTTPSJSONTransport(
        "https://api.example.test/v1",
        max_response_bytes=2048,
        max_request_bytes=256,
        requester=requester,  # type: ignore[arg-type]
    )
    result = asyncio.run(
        transport.request(
            "POST",
            "/items",
            query={"page": 2, "active": True},
            json_body={"title": "hello"},
            headers={"Authorization": "Bearer test-token"},
        )
    )
    response["items"][0]["ok"] = False  # type: ignore[index]

    assert result == {"items": [{"ok": True}]}
    method, url, headers, body, timeout, maximum = calls[0]
    assert method == "POST"
    assert url == "https://api.example.test/v1/items?page=2&active=true"
    assert headers["Authorization"] == "Bearer test-token"  # type: ignore[index]
    assert json.loads(body) == {"title": "hello"}  # type: ignore[arg-type]
    assert timeout == 30.0
    assert maximum == 2048


@pytest.mark.parametrize("path", ["items", "//evil.test/items", "/items?q=x", "/bad\\path"])
def test_transport_rejects_ambiguous_or_origin_changing_paths(path: str) -> None:
    transport = HTTPSJSONTransport("https://api.example.test", requester=lambda *args: {})
    with pytest.raises(IntegrationValidationError, match="path"):
        asyncio.run(transport.request("GET", path))


def test_transport_rejects_header_injection_oversized_body_and_untrusted_return_types() -> None:
    transport = HTTPSJSONTransport(
        "https://api.example.test",
        max_request_bytes=16,
        requester=lambda *args: object(),
    )
    with pytest.raises(IntegrationValidationError, match="header value"):
        asyncio.run(transport.request("GET", "/x", headers={"Authorization": "ok\r\nevil: x"}))
    with pytest.raises(IntegrationValidationError, match="size"):
        asyncio.run(transport.request("POST", "/x", json_body={"value": "x" * 100}))
    with pytest.raises(IntegrationTransportError, match="non-JSON"):
        asyncio.run(transport.request("GET", "/x"))


def test_transport_sanitizes_arbitrary_requester_exceptions() -> None:
    secret = "secret-in-upstream-exception"

    def requester(*args: object) -> object:
        raise RuntimeError(secret)

    transport = HTTPSJSONTransport("https://api.example.test", requester=requester)
    with pytest.raises(IntegrationTransportError) as caught:
        asyncio.run(transport.request("GET", "/x", headers={"Authorization": f"Bearer {secret}"}))
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


def test_redirect_handler_blocks_cross_origin_downgrade_and_userinfo() -> None:
    handler = StrictHTTPSRedirectHandler()
    request = urllib.request.Request(
        "https://api.example.test/resource",
        headers={"Authorization": "Bearer secret"},
    )
    for target in (
        "https://evil.example/resource",
        "http://api.example.test/resource",
        "https://user@api.example.test/resource",
    ):
        with pytest.raises(urllib.error.HTTPError, match="blocked"):
            handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, target)


def test_github_client_uses_official_headers_and_repository_shapes() -> None:
    transport = QueueTransport(user_payload(), [repository_payload()], repository_payload())
    client = GitHubClient(SecretCredential("ghp_test-secret"), transport=transport)  # type: ignore[arg-type]

    user = asyncio.run(client.authenticated_user())
    repositories = asyncio.run(client.list_repositories(per_page=50))
    repository = asyncio.run(client.inspect_repository("octocat", "jarvis"))

    assert user.login == "octocat"
    assert repositories[0].private is True
    assert repository.full_name == "octocat/jarvis"
    first = transport.calls[0]
    assert first["path"] == "/user"
    assert first["headers"]["X-GitHub-Api-Version"] == GITHUB_API_VERSION  # type: ignore[index]
    assert first["headers"]["Authorization"] == "Bearer ghp_test-secret"  # type: ignore[index]
    assert "ghp_test-secret" not in repr(client)


def test_github_issues_filter_pull_requests_and_create_issue_uses_documented_body() -> None:
    transport = QueueTransport(
        [issue_payload(), issue_payload(number=3, pull_request=True)],
        issue_payload(number=4),
    )
    client = GitHubClient(SecretCredential("secret"), transport=transport)  # type: ignore[arg-type]

    issues = asyncio.run(client.list_issues("octocat", "jarvis", state="all"))
    created = asyncio.run(
        client.create_issue(
            "octocat",
            "jarvis",
            title="Fix startup",
            body="Steps",
            labels=("bug",),
            assignees=("octocat",),
        )
    )

    assert [issue.number for issue in issues] == [2]
    assert created.number == 4
    assert transport.calls[0]["path"] == "/repos/octocat/jarvis/issues"
    assert transport.calls[1]["method"] == "POST"
    assert transport.calls[1]["json_body"] == {
        "title": "Fix startup",
        "body": "Steps",
        "labels": ["bug"],
        "assignees": ["octocat"],
    }
    assert "Details" not in repr(created)
    assert json.loads(json.dumps(created.to_json())) == created.to_json()


def test_github_pull_workflow_run_and_release_endpoints_are_typed() -> None:
    transport = QueueTransport(
        [pull_payload()],
        pull_payload(),
        {"total_count": 1, "workflows": [workflow_payload()]},
        {"total_count": 1, "workflow_runs": [workflow_run_payload()]},
        [release_payload()],
        release_payload(),
    )
    client = GitHubClient(SecretCredential("secret"), transport=transport)  # type: ignore[arg-type]

    pulls = asyncio.run(client.list_pull_requests("octocat", "jarvis"))
    pull = asyncio.run(client.read_pull_request("octocat", "jarvis", 3))
    workflows = asyncio.run(client.list_workflows("octocat", "jarvis"))
    runs = asyncio.run(client.inspect_workflow_status("octocat", "jarvis", status="success"))
    releases = asyncio.run(client.list_releases("octocat", "jarvis"))
    release = asyncio.run(client.read_release("octocat", "jarvis", 50))

    assert pulls[0].head_ref == "feature" and pull.base_ref == "main"
    assert workflows[0].state == "active"
    assert runs[0].conclusion == "success"
    assert releases[0].tag_name == "v0.2.0" and release.id == 50
    assert [call["path"] for call in transport.calls] == [
        "/repos/octocat/jarvis/pulls",
        "/repos/octocat/jarvis/pulls/3",
        "/repos/octocat/jarvis/actions/workflows",
        "/repos/octocat/jarvis/actions/runs",
        "/repos/octocat/jarvis/releases",
        "/repos/octocat/jarvis/releases/50",
    ]


def test_github_client_rejects_unbounded_inputs_and_malformed_shapes() -> None:
    client = GitHubClient(SecretCredential("secret"), transport=QueueTransport({}))  # type: ignore[arg-type]
    with pytest.raises(IntegrationValidationError, match="per_page"):
        asyncio.run(client.list_repositories(per_page=101))
    with pytest.raises(IntegrationValidationError, match="repository"):
        asyncio.run(client.inspect_repository("octocat", "../private"))
    with pytest.raises(IntegrationValidationError, match="number"):
        asyncio.run(client.read_issue("octocat", "jarvis", 0))


def test_github_integration_resolves_token_only_when_connecting_and_clears_client() -> None:
    class FakeClient:
        async def authenticated_user(self) -> GitHubUser:
            return GitHubUser("octocat", 1, "https://github.com/octocat")

    calls: list[str] = []

    def factory(secret: SecretCredential):
        calls.append(secret.reveal())
        return FakeClient()

    integration = GitHubIntegration(
        StaticCredentialResolver({"github.token": "secret"}),
        client_factory=factory,  # type: ignore[arg-type]
    )
    assert calls == []
    with pytest.raises(IntegrationNotConnectedError):
        _ = integration.client
    asyncio.run(integration.connect())
    assert integration.account == "octocat" and calls == ["secret"]
    assert "secret" not in repr(integration)
    asyncio.run(integration.disconnect())
    assert integration.account is None


def sample_message() -> EmailMessage:
    return EmailMessage(
        "message-1",
        "thread-1",
        "Project status",
        EmailAddress("alice@example.com", "Alice"),
        (EmailAddress("me@example.com"),),
        NOW,
        "The private project is on schedule.",
        unread=True,
    )


def test_in_memory_email_provider_list_search_read_draft_and_send() -> None:
    async def scenario() -> None:
        provider = InMemoryEmailProvider([sample_message()], clock=lambda: NOW)
        with pytest.raises(IntegrationNotConnectedError):
            await provider.list_messages()
        await provider.connect()
        listed = await provider.list_messages()
        searched = await provider.search_messages(EmailSearch(text="schedule", unread=True))
        message = await provider.read_message("message-1")
        draft = await provider.create_draft(
            EmailDraftRequest(
                (EmailAddress("bob@example.com"),),
                "Re: Project status",
                "Thanks",
            )
        )
        sent = await provider.send_message(draft.id)
        replay = await provider.send_message(draft.id)

        assert listed[0].snippet == "The private project is on schedule."
        assert searched == listed
        assert message.body_text.startswith("The private")
        assert sent == replay and sent.draft_id == draft.id
        assert json.loads(json.dumps(message.to_json())) == message.to_json()
        assert "private project" not in repr(message)
        assert "Thanks" not in repr(draft)

    asyncio.run(scenario())


def test_email_models_reject_header_injection_naive_time_and_bad_limits() -> None:
    with pytest.raises(IntegrationValidationError, match="address"):
        EmailAddress("victim@example.com\r\nBcc: attacker@example.com")
    with pytest.raises(IntegrationValidationError, match="timezone-aware"):
        EmailMessage(
            "id",
            "thread",
            "subject",
            EmailAddress("a@example.com"),
            (EmailAddress("b@example.com"),),
            datetime(2026, 1, 1),
            "body",
        )
    provider = InMemoryEmailProvider()
    asyncio.run(provider.connect())
    with pytest.raises(IntegrationValidationError, match="limit"):
        asyncio.run(provider.list_messages(limit=101))


def event_request(title: str = "Team sync") -> CalendarEventRequest:
    return CalendarEventRequest(
        title,
        NOW + timedelta(hours=1),
        NOW + timedelta(hours=2),
        "Asia/Kolkata",
        "Private planning notes",
        "Room 1",
        ("alice@example.com",),
    )


def test_in_memory_calendar_provider_full_contract_and_json_safety() -> None:
    async def scenario() -> None:
        provider = InMemoryCalendarProvider(clock=lambda: NOW)
        with pytest.raises(IntegrationNotConnectedError):
            await provider.upcoming_events()
        await provider.connect()
        created = await provider.create_event(event_request())
        listed = await provider.list_events(start=NOW, end=NOW + timedelta(days=1))
        searched = await provider.search_events(CalendarSearch("planning"))
        upcoming = await provider.upcoming_events(now=NOW)
        updated = await provider.update_event(
            created.id,
            CalendarEventUpdate(title="Updated sync", location="Room 2"),
        )
        deleted = await provider.delete_event(created.id)
        replay = await provider.delete_event(created.id)

        assert listed == (created,) and searched == (created,) and upcoming == (created,)
        assert updated.title == "Updated sync" and updated.location == "Room 2"
        assert created.timezone == "Asia/Kolkata"
        assert created.start.tzinfo is UTC
        assert deleted is True and replay is False
        assert json.loads(json.dumps(created.to_json())) == created.to_json()
        assert "Private planning" not in repr(created)

    asyncio.run(scenario())


def test_calendar_models_validate_timezone_ranges_and_updates() -> None:
    with pytest.raises(IntegrationValidationError, match="timezone-aware"):
        CalendarEventRequest("Event", datetime(2026, 1, 1), NOW)
    with pytest.raises(IntegrationValidationError, match="after start"):
        CalendarEventRequest("Event", NOW, NOW)
    with pytest.raises(IntegrationValidationError, match="unknown"):
        CalendarEventRequest("Event", NOW, NOW + timedelta(hours=1), "Mars/Olympus")
    with pytest.raises(IntegrationValidationError, match="cannot be empty"):
        CalendarEventUpdate()
    request = event_request()
    event = CalendarEvent(
        "id",
        request.title,
        request.start,
        request.end,
        request.timezone,
        request.description,
        request.location,
        request.attendees,
    )
    assert event.id == "id"


def _smtp_criteria_match(message: _FakeEmailMessage, criteria: Sequence[str]) -> bool:
    for part in criteria:
        if part.startswith('TEXT "'):
            text = part[6:-1].casefold()
            haystack = f"{message.subject}\n{message.body}".casefold()
            if text not in haystack:
                return False
        elif part.startswith('FROM "'):
            if part[6:-1].casefold() not in message.sender.casefold():
                return False
        elif part == "SEEN" and not message.seen:
            return False
        elif part == "UNSEEN" and message.seen:
            return False
    return True


class _FakeEmailMessage:
    """Tiny MIME builder so provider tests never touch the network."""

    def __init__(
        self,
        *,
        uid: str,
        subject: str,
        sender: str,
        to: str,
        body: str,
        date: datetime,
        seen: bool = False,
    ) -> None:
        from email.message import EmailMessage as MIMEMessage

        message = MIMEMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = date.strftime("%a, %d %b %Y %H:%M:%S %z")
        message.set_content(body)
        self.uid = uid
        self.subject = subject
        self.sender = sender
        self.body = body
        self.payload = message.as_bytes()
        self.seen = seen


class _FakeIMAP:
    def __init__(self, messages: list[_FakeEmailMessage]) -> None:
        self.messages = messages
        self.logins: list[tuple[str, str]] = []
        self.selected: list[str] = []
        self.search_criteria: list[tuple[str, ...]] = []

    def login(self, username: str, password: str) -> None:
        self.logins.append((username, password))
        if password == "wrong":
            raise IntegrationAuthError("Email credentials were rejected")

    def select(self, mailbox: str = "INBOX") -> int:
        self.selected.append(mailbox)
        return len(self.messages)

    def search(self, criteria: Sequence[str]) -> list[str]:
        self.search_criteria.append(tuple(criteria))
        matched = [message for message in self.messages if _smtp_criteria_match(message, criteria)]
        return [message.uid for message in matched][::-1]

    def fetch(self, uids: Sequence[str], spec: str) -> list[tuple[str, bytes, bool]]:
        by_uid = {message.uid: message for message in self.messages}
        result: list[tuple[str, bytes, bool]] = []
        for uid in uids:
            message = by_uid.get(uid)
            if message is not None:
                result.append((uid, message.payload, message.seen))
        return result

    def logout(self) -> None:
        pass


class _FakeSMTP:
    def __init__(self) -> None:
        self.sessions = 0
        self.logins: list[tuple[str, str]] = []
        self.sent: list[tuple[str, list[str], bytes]] = []
        self.secure_called = 0

    def secure(self) -> None:
        self.secure_called += 1

    def login(self, username: str, password: str) -> None:
        self.logins.append((username, password))

    def send(self, sender: str, recipients: list[str], payload: bytes) -> None:
        self.sent.append((sender, recipients, payload))

    def quit(self) -> None:
        self.sessions += 1


class _FakeVObjectLine:
    def __init__(self, value: object, params: dict[str, object] | None = None) -> None:
        self.value = value
        self.params = params or {}


class _FakeVEVENT:
    def __init__(
        self,
        *,
        uid: str,
        summary: str,
        description: str = "",
        location: str = "",
        start: datetime,
        end: datetime,
        tzid: str,
        attendees: tuple[str, ...] = (),
        modified: datetime | None = None,
    ) -> None:
        self.uid = _FakeVObjectLine(uid)
        self.summary = _FakeVObjectLine(summary)
        self.description = _FakeVObjectLine(description)
        self.location = _FakeVObjectLine(location)
        params = {"TZID": [tzid]} if tzid else {}
        self.dtstart = _FakeVObjectLine(start, params)
        self.dtend = _FakeVObjectLine(end, params)
        if modified is not None:
            self.lastmodified = _FakeVObjectLine(modified)
        self.contents = {"attendee": [_FakeVObjectLine(f"mailto:{person}") for person in attendees]}


class _FakeVObject:
    def __init__(self, vevent: _FakeVEVENT) -> None:
        self.vevent = vevent


class _FakeEvent:
    def __init__(self, vevent: _FakeVEVENT, delete: bool = False) -> None:
        self.vobject_instance = _FakeVObject(vevent)
        self.data = ""
        self.save_calls = 0
        self.delete_calls = 0
        self._delete = delete

    def load(self) -> None:
        return None

    def save(self) -> None:
        self.save_calls += 1

    def delete(self) -> None:
        self.delete_calls += 1
        self._delete = True


class _FakeCalendar:
    def __init__(self, events: list[_FakeEvent] | None = None) -> None:
        self._events: dict[str, _FakeEvent] = {}
        self.created_ics: list[str] = []
        for event in events or []:
            self._events[event.vobject_instance.vevent.uid.value] = event

    def events(self) -> list[_FakeEvent]:
        return list(self._events.values())

    def event_by_uid(self, uid: str) -> _FakeEvent:
        if uid not in self._events:
            raise ValueError("event not found")
        return self._events[uid]

    def save_event(self, ics: str) -> _FakeEvent:
        uid = next((line[4:] for line in ics.splitlines() if line.startswith("UID:")), "created")
        event = _FakeEvent(
            _FakeVEVENT(
                uid=uid,
                summary="",
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 1, 1, tzinfo=UTC),
                tzid="UTC",
            )
        )
        self._events[uid] = event
        self.created_ics.append(ics)
        return event


class _FakePrincipal:
    def __init__(self, calendar: _FakeCalendar) -> None:
        self._calendar = calendar

    def calendars(self) -> list[_FakeCalendar]:
        return [self._calendar]


class _FakeCalDAVClient:
    def __init__(self, calendar: _FakeCalendar) -> None:
        self._principal = _FakePrincipal(calendar)

    def principal(self) -> _FakePrincipal:
        return self._principal


def _smtp_provider(
    imap_messages: list[_FakeEmailMessage],
    *,
    password: str = "secret",
) -> tuple[SMTPEmailProvider, _FakeIMAP, _FakeSMTP]:
    imap = _FakeIMAP(imap_messages)
    smtp = _FakeSMTP()
    provider = SMTPEmailProvider(
        StaticCredentialResolver({"email.password": password}),
        smtp_host="smtp.example.com",
        smtp_mode="starttls",
        imap_host="imap.example.com",
        username="leon@example.com",
        from_address="leon@example.com",
        imap_factory=lambda host, port, ssl, timeout: imap,
        smtp_factory=lambda host, port, ssl, timeout: smtp,
    )
    return provider, imap, smtp


def test_smtp_email_provider_full_contract() -> None:
    async def scenario() -> None:
        older = _FakeEmailMessage(
            uid="1",
            subject="Old sync",
            sender="alice@example.com",
            to="leon@example.com",
            body="first body",
            date=NOW - timedelta(hours=2),
        )
        newer = _FakeEmailMessage(
            uid="2",
            subject="Hello world",
            sender="bob@example.com",
            to="leon@example.com",
            body="second body",
            date=NOW,
            seen=True,
        )
        provider, imap, smtp = _smtp_provider([older, newer])
        with pytest.raises(IntegrationNotConnectedError):
            await provider.list_messages()
        await provider.connect()
        assert provider.status.value == "connected"

        listed = await provider.list_messages(limit=10)
        assert [item.id for item in listed] == ["2", "1"]
        assert listed[0].unread is False and listed[1].unread is True
        assert listed[1].subject == "Old sync"
        assert listed[0].sender.address == "bob@example.com"

        read = await provider.read_message("1")
        assert read.body_text.strip() == "first body"

        searched = await provider.search_messages(EmailSearch(text="hello", unread=False))
        assert [item.id for item in searched] == ["2"]
        assert imap.search_criteria[-1] == ('TEXT "hello"', "SEEN")

        draft = await provider.create_draft(
            EmailDraftRequest(
                (EmailAddress("alice@example.com"),),
                "Re: sync",
                "body text",
                cc=(EmailAddress("cc@example.com"),),
            )
        )
        replayed = await provider.read_draft(draft.id)
        assert replayed.id == draft.id
        sent = await provider.send_message(draft.id)
        resent = await provider.send_message(draft.id)
        assert sent.draft_id == draft.id and resent is sent
        assert len(smtp.sent) == 1
        sender, recipients, payload = smtp.sent[0]
        assert sender == "leon@example.com"
        assert recipients == ["alice@example.com", "cc@example.com"]
        assert b"Subject: Re: sync" in payload
        assert b"body text" in payload
        assert smtp.secure_called == 2

    asyncio.run(scenario())


def test_smtp_email_provider_rejects_bad_credentials() -> None:
    async def scenario() -> None:
        provider, imap, smtp = _smtp_provider([], password="wrong")
        with pytest.raises(IntegrationAuthError, match="credentials"):
            await provider.connect()
        assert provider.status.value == "failed"

    asyncio.run(scenario())


def _caldav_provider(
    events: list[_FakeEvent],
) -> tuple[CalDAVCalendarProvider, _FakeCalendar]:
    calendar = _FakeCalendar(events)
    provider = CalDAVCalendarProvider(
        StaticCredentialResolver({"calendar.password": "secret"}),
        url="https://example.com/dav/calendar/",
        username="leon",
        client_factory=lambda: _FakeCalDAVClient(calendar),
        clock=lambda: NOW,
    )
    return provider, calendar


def test_caldav_provider_full_contract() -> None:
    future = _FakeEvent(
        _FakeVEVENT(
            uid="future-1",
            summary="Planning sync",
            description="Private planning notes",
            location="Room 1",
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=2),
            tzid="Asia/Kolkata",
            attendees=("alice@example.com",),
            modified=NOW,
        )
    )
    past = _FakeEvent(
        _FakeVEVENT(
            uid="past-1",
            summary="Standup",
            description="Daily standup",
            start=NOW - timedelta(days=1),
            end=NOW - timedelta(days=1, hours=-1),
            tzid="UTC",
        )
    )

    async def scenario() -> None:
        provider, calendar = _caldav_provider([future, past])
        with pytest.raises(IntegrationNotConnectedError):
            await provider.upcoming_events()
        await provider.connect()
        assert provider.status.value == "connected"

        listed = await provider.list_events(start=NOW, end=NOW + timedelta(days=2))
        assert [event.id for event in listed] == ["future-1"]

        searched = await provider.search_events(CalendarSearch("planning"))
        assert [event.id for event in searched] == ["future-1"]

        upcoming = await provider.upcoming_events(now=NOW)
        assert [event.id for event in upcoming] == ["future-1"]
        assert upcoming[0].timezone == "Asia/Kolkata"
        assert upcoming[0].attendees == ("alice@example.com",)
        assert upcoming[0].updated_at == NOW

        read = await provider.read_event("future-1")
        assert read.title == "Planning sync"

        created = await provider.create_event(event_request())
        assert created.timezone == "Asia/Kolkata"
        assert "UID:" in calendar.created_ics[0]
        assert "SUMMARY:Team sync" in calendar.created_ics[0]
        assert "DTSTART;TZID=Asia/Kolkata:" in calendar.created_ics[0]

        stored = next(
            event
            for event in [future, past]
            if event.vobject_instance.vevent.uid.value == "future-1"
        )
        updated = await provider.update_event(
            "future-1", CalendarEventUpdate(title="Updated sync", location="Room 2")
        )
        assert updated.title == "Updated sync"
        assert updated.location == "Room 2"
        assert stored.save_calls == 1
        assert "SUMMARY:Updated sync" in stored.data

        deleted = await provider.delete_event("future-1")
        assert deleted is True
        assert stored.delete_calls == 1

    asyncio.run(scenario())


def test_caldav_provider_skips_malformed_events() -> None:
    malformed = _FakeEvent(
        _FakeVEVENT(
            uid="bad-1",
            summary="Zero length",
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=1),
            tzid="UTC",
        )
    )

    async def scenario() -> None:
        provider, _calendar = _caldav_provider([malformed])
        await provider.connect()
        assert await provider.list_events() == ()

    asyncio.run(scenario())
