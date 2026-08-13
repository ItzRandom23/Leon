"""Registry-compatible GitHub integration and permission semantics."""

from __future__ import annotations

from collections.abc import Callable

from jarvis.integrations.auth import CredentialResolver, SecretCredential
from jarvis.integrations.base import (
    IntegrationMetadata,
    IntegrationOperation,
    OperationKind,
    StatefulIntegration,
)
from jarvis.integrations.errors import IntegrationNotConnectedError
from jarvis.integrations.github.client import GitHubClient
from jarvis.skills.base import RiskLevel

GITHUB_OPERATIONS = (
    IntegrationOperation(
        "github.list_repositories",
        OperationKind.READ,
        RiskLevel.SENSITIVE,
        "List accessible repositories",
    ),
    IntegrationOperation(
        "github.inspect_repository", OperationKind.READ, RiskLevel.SENSITIVE, "Inspect a repository"
    ),
    IntegrationOperation(
        "github.list_issues", OperationKind.READ, RiskLevel.SENSITIVE, "List issues"
    ),
    IntegrationOperation(
        "github.read_issue", OperationKind.READ, RiskLevel.SENSITIVE, "Read an issue"
    ),
    IntegrationOperation(
        "github.create_issue",
        OperationKind.WRITE,
        RiskLevel.SENSITIVE,
        "Create an issue",
        confirmation_required=True,
    ),
    IntegrationOperation(
        "github.list_pull_requests", OperationKind.READ, RiskLevel.SENSITIVE, "List pull requests"
    ),
    IntegrationOperation(
        "github.read_pull_request", OperationKind.READ, RiskLevel.SENSITIVE, "Read a pull request"
    ),
    IntegrationOperation(
        "github.list_workflows", OperationKind.READ, RiskLevel.SENSITIVE, "List workflows"
    ),
    IntegrationOperation(
        "github.inspect_workflow_status",
        OperationKind.READ,
        RiskLevel.SENSITIVE,
        "Inspect workflow runs",
    ),
    IntegrationOperation(
        "github.list_releases", OperationKind.READ, RiskLevel.SENSITIVE, "List releases"
    ),
    IntegrationOperation(
        "github.read_release", OperationKind.READ, RiskLevel.SENSITIVE, "Read a release"
    ),
)

GITHUB_METADATA = IntegrationMetadata(
    "github",
    "GitHub",
    "Read repositories and create explicitly confirmed issues through GitHub REST.",
    GITHUB_OPERATIONS,
)


class GitHubIntegration(StatefulIntegration):
    """Resolve a token at connect time and expose a typed GitHub client."""

    def __init__(
        self,
        credentials: CredentialResolver,
        *,
        credential_id: str = "github.token",
        client_factory: Callable[[SecretCredential], GitHubClient] = GitHubClient,
    ) -> None:
        super().__init__(GITHUB_METADATA)
        self._credentials = credentials
        self._credential_id = credential_id
        self._client_factory = client_factory
        self._client: GitHubClient | None = None
        self._account: str | None = None

    @property
    def account(self) -> str | None:
        return self._account

    @property
    def client(self) -> GitHubClient:
        if self._client is None:
            raise IntegrationNotConnectedError("GitHub is not connected")
        return self._client

    async def _connect(self) -> None:
        secret = self._credentials.resolve(self._credential_id)
        client = self._client_factory(secret)
        user = await client.authenticated_user()
        self._client = client
        self._account = user.login

    async def _disconnect(self) -> None:
        self._client = None
        self._account = None

    def __repr__(self) -> str:
        return f"GitHubIntegration(status={self.status.value!r}, account={self.account!r})"
