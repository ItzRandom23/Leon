"""Typed, JSON-serializable views of documented GitHub REST resources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jarvis.integrations.errors import IntegrationDataError


@dataclass(frozen=True, slots=True)
class GitHubUser:
    login: str
    id: int
    html_url: str

    def to_json(self) -> dict[str, str | int]:
        return {"login": self.login, "id": self.id, "html_url": self.html_url}


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    id: int
    name: str
    full_name: str
    owner: str
    private: bool
    html_url: str
    description: str | None
    default_branch: str

    def to_json(self) -> dict[str, str | int | bool | None]:
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "owner": self.owner,
            "private": self.private,
            "html_url": self.html_url,
            "description": self.description,
            "default_branch": self.default_branch,
        }


@dataclass(frozen=True, slots=True)
class GitHubIssue:
    id: int
    number: int
    title: str
    state: str
    html_url: str
    author: str
    body: str | None = field(repr=False)
    labels: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "html_url": self.html_url,
            "author": self.author,
            "body": self.body,
            "labels": list(self.labels),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class GitHubPullRequest:
    id: int
    number: int
    title: str
    state: str
    html_url: str
    author: str
    body: str | None = field(repr=False)
    draft: bool = False
    merged: bool | None = None
    head_ref: str = ""
    base_ref: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "html_url": self.html_url,
            "author": self.author,
            "body": self.body,
            "draft": self.draft,
            "merged": self.merged,
            "head_ref": self.head_ref,
            "base_ref": self.base_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class GitHubWorkflow:
    id: int
    name: str
    path: str
    state: str
    html_url: str

    def to_json(self) -> dict[str, str | int]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "state": self.state,
            "html_url": self.html_url,
        }


@dataclass(frozen=True, slots=True)
class GitHubWorkflowRun:
    id: int
    name: str
    workflow_id: int
    run_number: int
    event: str
    status: str
    conclusion: str | None
    html_url: str
    created_at: str
    updated_at: str

    def to_json(self) -> dict[str, str | int | None]:
        return {
            "id": self.id,
            "name": self.name,
            "workflow_id": self.workflow_id,
            "run_number": self.run_number,
            "event": self.event,
            "status": self.status,
            "conclusion": self.conclusion,
            "html_url": self.html_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class GitHubRelease:
    id: int
    tag_name: str
    name: str | None
    draft: bool
    prerelease: bool
    html_url: str
    published_at: str | None
    body: str | None = field(default=None, repr=False)

    def to_json(self) -> dict[str, str | int | bool | None]:
        return {
            "id": self.id,
            "tag_name": self.tag_name,
            "name": self.name,
            "draft": self.draft,
            "prerelease": self.prerelease,
            "html_url": self.html_url,
            "published_at": self.published_at,
            "body": self.body,
        }


def parse_user(value: object) -> GitHubUser:
    data = _object(value)
    return GitHubUser(_string(data, "login"), _integer(data, "id"), _string(data, "html_url"))


def parse_repository(value: object) -> GitHubRepository:
    data = _object(value)
    owner = _object(data.get("owner"))
    return GitHubRepository(
        _integer(data, "id"),
        _string(data, "name"),
        _string(data, "full_name"),
        _string(owner, "login"),
        _boolean(data, "private"),
        _string(data, "html_url"),
        _optional_string(data, "description"),
        _string(data, "default_branch"),
    )


def parse_issue(value: object) -> GitHubIssue:
    data = _object(value)
    author = _object(data.get("user"))
    labels: list[str] = []
    raw_labels = data.get("labels", [])
    if not isinstance(raw_labels, list):
        raise IntegrationDataError("GitHub returned malformed issue data")
    for raw_label in raw_labels:
        if isinstance(raw_label, str):
            labels.append(raw_label)
        else:
            labels.append(_string(_object(raw_label), "name"))
    return GitHubIssue(
        _integer(data, "id"),
        _integer(data, "number"),
        _string(data, "title"),
        _string(data, "state"),
        _string(data, "html_url"),
        _string(author, "login"),
        _optional_string(data, "body"),
        tuple(labels),
        _string(data, "created_at"),
        _string(data, "updated_at"),
    )


def parse_pull_request(value: object) -> GitHubPullRequest:
    data = _object(value)
    author = _object(data.get("user"))
    head = _object(data.get("head"))
    base = _object(data.get("base"))
    merged = data.get("merged")
    if merged is not None and not isinstance(merged, bool):
        raise IntegrationDataError("GitHub returned malformed pull request data")
    return GitHubPullRequest(
        _integer(data, "id"),
        _integer(data, "number"),
        _string(data, "title"),
        _string(data, "state"),
        _string(data, "html_url"),
        _string(author, "login"),
        _optional_string(data, "body"),
        _optional_boolean(data, "draft", default=False),
        merged,
        _string(head, "ref"),
        _string(base, "ref"),
        _string(data, "created_at"),
        _string(data, "updated_at"),
    )


def parse_workflow(value: object) -> GitHubWorkflow:
    data = _object(value)
    return GitHubWorkflow(
        _integer(data, "id"),
        _string(data, "name"),
        _string(data, "path"),
        _string(data, "state"),
        _string(data, "html_url"),
    )


def parse_workflow_run(value: object) -> GitHubWorkflowRun:
    data = _object(value)
    return GitHubWorkflowRun(
        _integer(data, "id"),
        _string(data, "name"),
        _integer(data, "workflow_id"),
        _integer(data, "run_number"),
        _string(data, "event"),
        _string(data, "status"),
        _optional_string(data, "conclusion"),
        _string(data, "html_url"),
        _string(data, "created_at"),
        _string(data, "updated_at"),
    )


def parse_release(value: object) -> GitHubRelease:
    data = _object(value)
    return GitHubRelease(
        _integer(data, "id"),
        _string(data, "tag_name"),
        _optional_string(data, "name"),
        _boolean(data, "draft"),
        _boolean(data, "prerelease"),
        _string(data, "html_url"),
        _optional_string(data, "published_at"),
        _optional_string(data, "body"),
    )


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value


def _string(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value


def _optional_string(data: Mapping[str, Any], name: str) -> str | None:
    value = data.get(name)
    if value is not None and not isinstance(value, str):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value


def _integer(data: Mapping[str, Any], name: str) -> int:
    value = data.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value


def _boolean(data: Mapping[str, Any], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value


def _optional_boolean(data: Mapping[str, Any], name: str, *, default: bool) -> bool:
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise IntegrationDataError("GitHub returned malformed response data")
    return value
