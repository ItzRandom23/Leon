"""GitHub REST integration."""

from jarvis.integrations.github.client import GITHUB_API_VERSION, GitHubClient
from jarvis.integrations.github.integration import (
    GITHUB_METADATA,
    GITHUB_OPERATIONS,
    GitHubIntegration,
)
from jarvis.integrations.github.models import (
    GitHubIssue,
    GitHubPullRequest,
    GitHubRelease,
    GitHubRepository,
    GitHubUser,
    GitHubWorkflow,
    GitHubWorkflowRun,
)

__all__ = [
    "GITHUB_API_VERSION",
    "GITHUB_METADATA",
    "GITHUB_OPERATIONS",
    "GitHubClient",
    "GitHubIntegration",
    "GitHubIssue",
    "GitHubPullRequest",
    "GitHubRelease",
    "GitHubRepository",
    "GitHubUser",
    "GitHubWorkflow",
    "GitHubWorkflowRun",
]
