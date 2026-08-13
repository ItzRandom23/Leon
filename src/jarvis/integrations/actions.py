"""Permissioned action adapters for registered external integrations."""

from __future__ import annotations

from datetime import datetime

from jarvis.core.actions import ActionParameter, ActionRegistry, ActionResult
from jarvis.integrations.calendar import (
    CalendarEventRequest,
    CalendarEventUpdate,
    CalendarProvider,
    CalendarSearch,
)
from jarvis.integrations.email import (
    EmailAddress,
    EmailDraftRequest,
    EmailProvider,
    EmailSearch,
)
from jarvis.integrations.errors import IntegrationError
from jarvis.integrations.github import GitHubIntegration
from jarvis.integrations.registry import IntegrationRegistry
from jarvis.skills.base import RiskLevel


def register_integration_actions(
    actions: ActionRegistry,
    integrations: IntegrationRegistry,
) -> None:
    """Register operations only for concrete providers present in the registry."""

    if "github" in integrations:
        _register_github(actions, integrations.get("github"))
    if "email" in integrations:
        _register_email(actions, integrations.get("email"))
    if "calendar" in integrations:
        _register_calendar(actions, integrations.get("calendar"))


def _register_github(actions: ActionRegistry, integration: object) -> None:
    if not isinstance(integration, GitHubIntegration):
        raise TypeError("github integration must be GitHubIntegration")
    repository_parameters = (
        ActionParameter("owner", str, min_length=1, max_length=100),
        ActionParameter("repository", str, min_length=1, max_length=100),
    )

    @actions.action(
        name="github_list_repositories",
        description="List repositories visible to the connected GitHub account.",
        parameters=(
            ActionParameter(
                "visibility",
                str,
                required=False,
                enum=("all", "public", "private"),
                default="all",
            ),
            _limit_parameter(30),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_list_repositories(visibility: str = "all", limit: int = 30) -> ActionResult:
        try:
            values = await integration.client.list_repositories(
                visibility=visibility,
                per_page=limit,
            )
            return _success(
                "github_list_repositories",
                f"Found {len(values)} GitHub repositories.",
                {"repositories": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("github_list_repositories", "GitHub repositories could not be listed.")

    @actions.action(
        name="github_inspect_repository",
        description="Read metadata for one GitHub repository.",
        parameters=repository_parameters,
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_inspect_repository(owner: str, repository: str) -> ActionResult:
        try:
            value = await integration.client.inspect_repository(owner, repository)
            return _success(
                "github_inspect_repository",
                f"Read repository {value.full_name}.",
                {"repository": value.to_json()},
            )
        except IntegrationError:
            return _failure(
                "github_inspect_repository", "That GitHub repository could not be read."
            )

    @actions.action(
        name="github_list_issues",
        description="List GitHub issues for one repository.",
        parameters=repository_parameters
        + (
            ActionParameter(
                "state", str, required=False, enum=("open", "closed", "all"), default="open"
            ),
            _limit_parameter(30),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_list_issues(
        owner: str,
        repository: str,
        state: str = "open",
        limit: int = 30,
    ) -> ActionResult:
        try:
            values = await integration.client.list_issues(
                owner,
                repository,
                state=state,
                per_page=limit,
            )
            return _success(
                "github_list_issues",
                f"Found {len(values)} GitHub issues.",
                {"issues": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("github_list_issues", "GitHub issues could not be listed.")

    @actions.action(
        name="github_read_issue",
        description="Read one GitHub issue, including its body.",
        parameters=repository_parameters + (ActionParameter("number", int, minimum=1),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_read_issue(owner: str, repository: str, number: int) -> ActionResult:
        try:
            value = await integration.client.read_issue(owner, repository, number)
            return _success(
                "github_read_issue",
                f"Read GitHub issue {number}.",
                {"issue": value.to_json()},
            )
        except IntegrationError:
            return _failure("github_read_issue", "That GitHub issue could not be read.")

    @actions.action(
        name="github_create_issue",
        description="Create a GitHub issue after explicit confirmation of its contents.",
        parameters=repository_parameters
        + (
            ActionParameter("title", str, min_length=1, max_length=256),
            ActionParameter("body", str, required=False, max_length=500, default=""),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_create_issue(
        owner: str,
        repository: str,
        title: str,
        body: str = "",
    ) -> ActionResult:
        try:
            value = await integration.client.create_issue(
                owner,
                repository,
                title=title,
                body=body,
            )
            return _success(
                "github_create_issue",
                f"Created GitHub issue {value.number}.",
                {"issue": value.to_json()},
            )
        except IntegrationError:
            return _failure("github_create_issue", "The GitHub issue could not be created.")

    _register_github_read_collections(actions, integration, repository_parameters)


def _register_github_read_collections(
    actions: ActionRegistry,
    integration: GitHubIntegration,
    repository_parameters: tuple[ActionParameter, ...],
) -> None:
    @actions.action(
        name="github_list_pull_requests",
        description="List pull requests for one GitHub repository.",
        parameters=repository_parameters
        + (
            ActionParameter(
                "state", str, required=False, enum=("open", "closed", "all"), default="open"
            ),
            _limit_parameter(30),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_list_pull_requests(
        owner: str, repository: str, state: str = "open", limit: int = 30
    ) -> ActionResult:
        try:
            values = await integration.client.list_pull_requests(
                owner, repository, state=state, per_page=limit
            )
            return _success(
                "github_list_pull_requests",
                f"Found {len(values)} pull requests.",
                {"pull_requests": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("github_list_pull_requests", "Pull requests could not be listed.")

    @actions.action(
        name="github_read_pull_request",
        description="Read one GitHub pull request.",
        parameters=repository_parameters + (ActionParameter("number", int, minimum=1),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_read_pull_request(owner: str, repository: str, number: int) -> ActionResult:
        try:
            value = await integration.client.read_pull_request(owner, repository, number)
            return _success(
                "github_read_pull_request",
                f"Read pull request {number}.",
                {"pull_request": value.to_json()},
            )
        except IntegrationError:
            return _failure("github_read_pull_request", "That pull request could not be read.")

    @actions.action(
        name="github_inspect_workflows",
        description="Read GitHub workflow definitions and recent run status.",
        parameters=repository_parameters + (_limit_parameter(30),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_inspect_workflows(
        owner: str, repository: str, limit: int = 30
    ) -> ActionResult:
        try:
            workflows = await integration.client.list_workflows(owner, repository, per_page=limit)
            runs = await integration.client.inspect_workflow_status(
                owner, repository, per_page=limit
            )
            return _success(
                "github_inspect_workflows",
                f"Read {len(workflows)} workflows and {len(runs)} runs.",
                {
                    "workflows": [item.to_json() for item in workflows],
                    "runs": [item.to_json() for item in runs],
                },
            )
        except IntegrationError:
            return _failure("github_inspect_workflows", "Workflow status could not be read.")

    @actions.action(
        name="github_list_releases",
        description="List releases for one GitHub repository.",
        parameters=repository_parameters + (_limit_parameter(30),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def github_list_releases(owner: str, repository: str, limit: int = 30) -> ActionResult:
        try:
            values = await integration.client.list_releases(owner, repository, per_page=limit)
            return _success(
                "github_list_releases",
                f"Found {len(values)} releases.",
                {"releases": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("github_list_releases", "Releases could not be listed.")


def _register_email(actions: ActionRegistry, integration: object) -> None:
    if not isinstance(integration, EmailProvider):
        raise TypeError("email integration must implement EmailProvider")

    @actions.action(
        name="email_list_messages",
        description="List recent email metadata without full message bodies.",
        parameters=(_limit_parameter(25),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def email_list_messages(limit: int = 25) -> ActionResult:
        try:
            values = await integration.list_messages(limit=limit)
            return _success(
                "email_list_messages",
                f"Found {len(values)} email messages.",
                {"messages": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("email_list_messages", "Email messages could not be listed.")

    @actions.action(
        name="email_search_messages",
        description="Search email; results contain message metadata only.",
        parameters=(
            ActionParameter("query", str, min_length=1, max_length=500),
            _limit_parameter(25),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def email_search_messages(query: str, limit: int = 25) -> ActionResult:
        try:
            values = await integration.search_messages(EmailSearch(text=query), limit=limit)
            return _success(
                "email_search_messages",
                f"Found {len(values)} matching messages.",
                {"messages": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("email_search_messages", "The email search could not be completed.")

    @actions.action(
        name="email_read_message",
        description="Read the full body of one email message.",
        parameters=(ActionParameter("message_id", str, min_length=1, max_length=512),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def email_read_message(message_id: str) -> ActionResult:
        try:
            value = await integration.read_message(message_id)
            return _success(
                "email_read_message",
                "Read the email message.",
                {"message": value.to_json()},
            )
        except IntegrationError:
            return _failure("email_read_message", "That email message could not be read.")

    @actions.action(
        name="email_create_draft",
        description="Create a reviewable email draft without sending it.",
        parameters=(
            ActionParameter("recipient", str, min_length=3, max_length=320),
            ActionParameter("subject", str, max_length=500),
            ActionParameter("body", str, max_length=500),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def email_create_draft(recipient: str, subject: str, body: str) -> ActionResult:
        try:
            value = await integration.create_draft(
                EmailDraftRequest((EmailAddress(recipient),), subject, body)
            )
            return _success(
                "email_create_draft",
                "Created an email draft; it has not been sent.",
                {"draft": value.to_json()},
            )
        except IntegrationError:
            return _failure("email_create_draft", "The email draft could not be created.")

    @actions.action(
        name="email_send_message",
        description="Send an existing reviewed email draft after explicit confirmation.",
        parameters=(
            ActionParameter("draft_id", str, min_length=1, max_length=512),
            ActionParameter("expected_recipient", str, min_length=3, max_length=320),
            ActionParameter("expected_subject", str, max_length=500),
            ActionParameter("expected_body", str, max_length=500),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def email_send_message(
        draft_id: str,
        expected_recipient: str,
        expected_subject: str,
        expected_body: str,
    ) -> ActionResult:
        try:
            draft = await integration.read_draft(draft_id)
            request = draft.request
            actual_recipients = tuple(address.address for address in request.recipients)
            if (
                actual_recipients != (expected_recipient,)
                or request.subject != expected_subject
                or request.body_text != expected_body
            ):
                return _failure(
                    "email_send_message",
                    "The email draft changed or did not match the confirmed contents.",
                )
            value = await integration.send_message(draft_id)
            return _success(
                "email_send_message",
                "Sent the reviewed email draft.",
                {"sent": value.to_json()},
            )
        except IntegrationError:
            return _failure("email_send_message", "The email draft could not be sent.")


def _register_calendar(actions: ActionRegistry, integration: object) -> None:
    if not isinstance(integration, CalendarProvider):
        raise TypeError("calendar integration must implement CalendarProvider")

    @actions.action(
        name="calendar_list_events",
        description="List upcoming calendar events.",
        parameters=(_limit_parameter(25),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def calendar_list_events(limit: int = 25) -> ActionResult:
        try:
            values = await integration.upcoming_events(limit=limit)
            return _success(
                "calendar_list_events",
                f"Found {len(values)} upcoming events.",
                {"events": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("calendar_list_events", "Calendar events could not be listed.")

    @actions.action(
        name="calendar_search_events",
        description="Search calendar event titles, descriptions, and locations.",
        parameters=(
            ActionParameter("query", str, min_length=1, max_length=500),
            _limit_parameter(25),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def calendar_search_events(query: str, limit: int = 25) -> ActionResult:
        try:
            values = await integration.search_events(CalendarSearch(query), limit=limit)
            return _success(
                "calendar_search_events",
                f"Found {len(values)} matching events.",
                {"events": [item.to_json() for item in values]},
            )
        except IntegrationError:
            return _failure("calendar_search_events", "The calendar search could not be completed.")

    @actions.action(
        name="calendar_create_event",
        description="Create a calendar event after explicit confirmation of all details.",
        parameters=_calendar_create_parameters(),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def calendar_create_event(
        title: str,
        start: str,
        end: str,
        timezone: str = "UTC",
        description: str = "",
        location: str = "",
    ) -> ActionResult:
        try:
            value = await integration.create_event(
                CalendarEventRequest(
                    title,
                    _datetime(start),
                    _datetime(end),
                    timezone,
                    description,
                    location,
                )
            )
            return _success(
                "calendar_create_event",
                "Created the calendar event.",
                {"event": value.to_json()},
            )
        except (IntegrationError, ValueError):
            return _failure("calendar_create_event", "The calendar event could not be created.")

    @actions.action(
        name="calendar_update_event",
        description="Replace a calendar event's title and time after explicit confirmation.",
        parameters=(ActionParameter("event_id", str, min_length=1, max_length=512),)
        + (ActionParameter("expected_current_title", str, min_length=1, max_length=500),)
        + _calendar_create_parameters(),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def calendar_update_event(
        event_id: str,
        expected_current_title: str,
        title: str,
        start: str,
        end: str,
        timezone: str = "UTC",
        description: str = "",
        location: str = "",
    ) -> ActionResult:
        try:
            current = await integration.read_event(event_id)
            if current.title != expected_current_title:
                return _failure(
                    "calendar_update_event",
                    "The calendar event title changed before confirmation completed.",
                )
            value = await integration.update_event(
                event_id,
                CalendarEventUpdate(
                    title,
                    _datetime(start),
                    _datetime(end),
                    timezone,
                    description,
                    location,
                ),
            )
            return _success(
                "calendar_update_event",
                "Updated the calendar event.",
                {"event": value.to_json()},
            )
        except (IntegrationError, ValueError):
            return _failure("calendar_update_event", "The calendar event could not be updated.")

    @actions.action(
        name="calendar_delete_event",
        description="Permanently delete a calendar event.",
        parameters=(
            ActionParameter("event_id", str, min_length=1, max_length=512),
            ActionParameter("expected_title", str, min_length=1, max_length=500),
            ActionParameter("expected_start", str, max_length=80),
        ),
        risk_level=RiskLevel.DESTRUCTIVE,
    )
    async def calendar_delete_event(
        event_id: str,
        expected_title: str,
        expected_start: str,
    ) -> ActionResult:
        try:
            current = await integration.read_event(event_id)
            if current.title != expected_title or current.start != _datetime(expected_start):
                return _failure(
                    "calendar_delete_event",
                    "The calendar event changed before confirmation completed.",
                )
            if not await integration.delete_event(event_id):
                return _failure("calendar_delete_event", "That calendar event does not exist.")
            return _success(
                "calendar_delete_event",
                "Deleted the calendar event.",
                {"event_id": event_id},
            )
        except (IntegrationError, ValueError):
            return _failure("calendar_delete_event", "The calendar event could not be deleted.")


def _calendar_create_parameters() -> tuple[ActionParameter, ...]:
    return (
        ActionParameter("title", str, min_length=1, max_length=500),
        ActionParameter("start", str, max_length=80),
        ActionParameter("end", str, max_length=80),
        ActionParameter("timezone", str, required=False, max_length=100, default="UTC"),
        ActionParameter("description", str, required=False, max_length=500, default=""),
        ActionParameter("location", str, required=False, max_length=500, default=""),
    )


def _limit_parameter(default: int) -> ActionParameter:
    return ActionParameter("limit", int, required=False, minimum=1, maximum=100, default=default)


def _datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("date/time must be ISO 8601") from None


def _success(action: str, message: str, data: object) -> ActionResult:
    return ActionResult.succeeded(action, message=message, data=data)


def _failure(action: str, message: str) -> ActionResult:
    return ActionResult.failed(
        action,
        "The integration provider reported a controlled failure.",
        message=message,
        error_code="integration_error",
    )
