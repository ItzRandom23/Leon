"""Bounded GitHub REST adapter using documented resource shapes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from jarvis.integrations.auth import SecretCredential
from jarvis.integrations.errors import IntegrationDataError, IntegrationValidationError
from jarvis.integrations.github.models import (
    GitHubIssue,
    GitHubPullRequest,
    GitHubRelease,
    GitHubRepository,
    GitHubUser,
    GitHubWorkflow,
    GitHubWorkflowRun,
    parse_issue,
    parse_pull_request,
    parse_release,
    parse_repository,
    parse_user,
    parse_workflow,
    parse_workflow_run,
)
from jarvis.integrations.transport import HTTPSJSONTransport, JSONValue, QueryValue

GITHUB_API_VERSION = "2022-11-28"


class GitHubClient:
    """Typed subset of GitHub REST; it never formats credentials in repr/errors."""

    def __init__(
        self,
        token: SecretCredential,
        *,
        base_url: str = "https://api.github.com",
        transport: HTTPSJSONTransport | None = None,
    ) -> None:
        if not isinstance(token, SecretCredential):
            raise TypeError("token must be a SecretCredential")
        self._token = token
        self._transport = transport or HTTPSJSONTransport(
            base_url,
            default_headers={"User-Agent": "JARVIS-Assistant"},
        )

    async def authenticated_user(self) -> GitHubUser:
        return parse_user(await self._request("GET", "/user"))

    async def list_repositories(
        self,
        *,
        visibility: str = "all",
        sort: str = "full_name",
        direction: str = "asc",
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[GitHubRepository, ...]:
        _choice(visibility, "visibility", {"all", "public", "private"})
        _choice(sort, "sort", {"created", "updated", "pushed", "full_name"})
        _choice(direction, "direction", {"asc", "desc"})
        query = _pagination(page, per_page)
        query.update({"visibility": visibility, "sort": sort, "direction": direction})
        values = _array(await self._request("GET", "/user/repos", query=query))
        return tuple(parse_repository(item) for item in values)

    async def inspect_repository(self, owner: str, repository: str) -> GitHubRepository:
        path = _repository_path(owner, repository)
        return parse_repository(await self._request("GET", path))

    async def list_issues(
        self,
        owner: str,
        repository: str,
        *,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[GitHubIssue, ...]:
        _choice(state, "state", {"open", "closed", "all"})
        query = _pagination(page, per_page)
        query["state"] = state
        values = _array(
            await self._request("GET", f"{_repository_path(owner, repository)}/issues", query=query)
        )
        # GitHub's issues endpoint also returns pull requests. Keep this API truly issue-only.
        return tuple(
            parse_issue(item)
            for item in values
            if not (isinstance(item, Mapping) and "pull_request" in item)
        )

    async def read_issue(self, owner: str, repository: str, number: int) -> GitHubIssue:
        return parse_issue(
            await self._request(
                "GET", f"{_repository_path(owner, repository)}/issues/{_positive(number, 'number')}"
            )
        )

    async def create_issue(
        self,
        owner: str,
        repository: str,
        *,
        title: str,
        body: str | None = None,
        labels: Sequence[str] = (),
        assignees: Sequence[str] = (),
    ) -> GitHubIssue:
        normalized_title = _bounded_text(title, "title", maximum=256)
        normalized_body = (
            None if body is None else _bounded_text(body, "body", maximum=65536, empty=True)
        )
        payload: dict[str, JSONValue] = {
            "title": normalized_title,
            "labels": _string_list(labels, "labels", maximum_items=20, item_maximum=100),
            "assignees": _string_list(assignees, "assignees", maximum_items=10, item_maximum=100),
        }
        if normalized_body is not None:
            payload["body"] = normalized_body
        return parse_issue(
            await self._request(
                "POST", f"{_repository_path(owner, repository)}/issues", json_body=payload
            )
        )

    async def list_pull_requests(
        self,
        owner: str,
        repository: str,
        *,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[GitHubPullRequest, ...]:
        _choice(state, "state", {"open", "closed", "all"})
        query = _pagination(page, per_page)
        query["state"] = state
        values = _array(
            await self._request("GET", f"{_repository_path(owner, repository)}/pulls", query=query)
        )
        return tuple(parse_pull_request(item) for item in values)

    async def read_pull_request(
        self, owner: str, repository: str, number: int
    ) -> GitHubPullRequest:
        return parse_pull_request(
            await self._request(
                "GET", f"{_repository_path(owner, repository)}/pulls/{_positive(number, 'number')}"
            )
        )

    async def list_workflows(
        self,
        owner: str,
        repository: str,
        *,
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[GitHubWorkflow, ...]:
        payload = _object(
            await self._request(
                "GET",
                f"{_repository_path(owner, repository)}/actions/workflows",
                query=_pagination(page, per_page),
            )
        )
        return tuple(parse_workflow(item) for item in _array(payload.get("workflows")))

    async def inspect_workflow_status(
        self,
        owner: str,
        repository: str,
        *,
        branch: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[GitHubWorkflowRun, ...]:
        query = _pagination(page, per_page)
        if branch is not None:
            query["branch"] = _bounded_text(branch, "branch", maximum=255)
        if status is not None:
            query["status"] = _choice(
                status,
                "status",
                {
                    "completed",
                    "action_required",
                    "cancelled",
                    "failure",
                    "neutral",
                    "skipped",
                    "stale",
                    "success",
                    "timed_out",
                    "in_progress",
                    "queued",
                    "requested",
                    "waiting",
                    "pending",
                },
            )
        payload = _object(
            await self._request(
                "GET",
                f"{_repository_path(owner, repository)}/actions/runs",
                query=query,
            )
        )
        return tuple(parse_workflow_run(item) for item in _array(payload.get("workflow_runs")))

    async def list_releases(
        self,
        owner: str,
        repository: str,
        *,
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[GitHubRelease, ...]:
        values = _array(
            await self._request(
                "GET",
                f"{_repository_path(owner, repository)}/releases",
                query=_pagination(page, per_page),
            )
        )
        return tuple(parse_release(item) for item in values)

    async def read_release(self, owner: str, repository: str, release_id: int) -> GitHubRelease:
        return parse_release(
            await self._request(
                "GET",
                f"{_repository_path(owner, repository)}/releases/"
                f"{_positive(release_id, 'release_id')}",
            )
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        json_body: JSONValue = None,
    ) -> JSONValue:
        return await self._transport.request(
            method,
            path,
            query=query,
            json_body=json_body,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token.reveal()}",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )

    def __repr__(self) -> str:
        return "GitHubClient(token=[REDACTED])"


def _repository_path(owner: str, repository: str) -> str:
    return f"/repos/{_segment(owner, 'owner')}/{_segment(repository, 'repository')}"


def _segment(value: str, name: str) -> str:
    normalized = _bounded_text(value, name, maximum=100)
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise IntegrationValidationError(f"{name} is invalid")
    return quote(normalized, safe="")


def _positive(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise IntegrationValidationError(f"{name} must be a positive integer")
    return value


def _pagination(page: int, per_page: int) -> dict[str, QueryValue]:
    if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
        raise IntegrationValidationError("page must be a positive integer")
    if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= 100:
        raise IntegrationValidationError("per_page must be between 1 and 100")
    return {"page": page, "per_page": per_page}


def _choice(value: str, name: str, choices: set[str]) -> str:
    if value not in choices:
        raise IntegrationValidationError(f"{name} has an unsupported value")
    return value


def _bounded_text(value: str, name: str, *, maximum: int, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise IntegrationValidationError(f"{name} must be text")
    if not empty and not value.strip():
        raise IntegrationValidationError(f"{name} cannot be empty")
    if len(value) > maximum or any(ord(char) == 0 for char in value):
        raise IntegrationValidationError(f"{name} is invalid")
    return value.strip() if not empty else value


def _string_list(
    values: Sequence[str], name: str, *, maximum_items: int, item_maximum: int
) -> list[JSONValue]:
    if isinstance(values, (str, bytes)) or len(values) > maximum_items:
        raise IntegrationValidationError(f"{name} is invalid")
    return [_bounded_text(value, name, maximum=item_maximum) for value in values]


def _array(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value
