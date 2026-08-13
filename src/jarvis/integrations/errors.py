"""Errors raised by external-service integrations.

Public error messages intentionally exclude response bodies, URLs, headers, and
credential values.  Provider-specific diagnostics belong in redacted debug logs.
"""

from __future__ import annotations


class IntegrationError(RuntimeError):
    """Base class for integration failures safe to present to a user."""


class IntegrationValidationError(IntegrationError, ValueError):
    """Raised when integration input or provider data is invalid."""


class IntegrationAuthError(IntegrationError):
    """Raised when credentials cannot be resolved or accepted."""


class CredentialNotFoundError(IntegrationAuthError, KeyError):
    """Raised when an injected credential is unavailable."""

    def __init__(self, credential_id: str) -> None:
        self.credential_id = credential_id
        super().__init__("The requested credential is not available")


class DuplicateIntegrationError(IntegrationError, ValueError):
    """Raised when an integration name is already registered."""


class IntegrationNotFoundError(IntegrationError, KeyError):
    """Raised when a registry does not contain the requested integration."""


class IntegrationRegistryClosedError(IntegrationError):
    """Raised when a closed registry is used."""


class IntegrationLifecycleError(IntegrationError):
    """Raised when connect, disconnect, or close fails."""

    def __init__(self, integration: str, operation: str) -> None:
        self.integration = integration
        self.operation = operation
        super().__init__(f"Integration {integration!r} could not {operation}")


class IntegrationNotConnectedError(IntegrationError):
    """Raised when an operation requires a connected provider."""


class IntegrationTransportError(IntegrationError):
    """Raised for sanitized network, protocol, and decoding failures."""


class IntegrationHTTPError(IntegrationTransportError):
    """A sanitized non-success HTTP result."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"External service request failed with HTTP status {status_code}")


class IntegrationDataError(IntegrationError):
    """Raised when a service returns a malformed documented response shape."""
