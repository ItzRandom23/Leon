"""Reusable, permission-ready external integration foundations."""

from jarvis.integrations.actions import register_integration_actions
from jarvis.integrations.auth import (
    ChainedCredentialResolver,
    CredentialResolver,
    EnvironmentCredentialResolver,
    SecretCredential,
    StaticCredentialResolver,
)
from jarvis.integrations.base import (
    Integration,
    IntegrationMetadata,
    IntegrationOperation,
    IntegrationSnapshot,
    IntegrationStatus,
    OperationKind,
    StatefulIntegration,
)
from jarvis.integrations.errors import (
    CredentialNotFoundError,
    DuplicateIntegrationError,
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationError,
    IntegrationHTTPError,
    IntegrationLifecycleError,
    IntegrationNotConnectedError,
    IntegrationNotFoundError,
    IntegrationRegistryClosedError,
    IntegrationTransportError,
    IntegrationValidationError,
)
from jarvis.integrations.registry import (
    IntegrationFailure,
    IntegrationRegistry,
    RegistryCloseReport,
)
from jarvis.integrations.transport import (
    HTTPSJSONTransport,
    JSONValue,
    StrictHTTPSRedirectHandler,
    json_snapshot,
)

__all__ = [
    "ChainedCredentialResolver",
    "CredentialNotFoundError",
    "CredentialResolver",
    "DuplicateIntegrationError",
    "EnvironmentCredentialResolver",
    "HTTPSJSONTransport",
    "Integration",
    "IntegrationAuthError",
    "IntegrationDataError",
    "IntegrationError",
    "IntegrationFailure",
    "IntegrationHTTPError",
    "IntegrationLifecycleError",
    "IntegrationMetadata",
    "IntegrationNotConnectedError",
    "IntegrationNotFoundError",
    "IntegrationOperation",
    "IntegrationRegistry",
    "IntegrationRegistryClosedError",
    "IntegrationSnapshot",
    "IntegrationStatus",
    "IntegrationTransportError",
    "IntegrationValidationError",
    "JSONValue",
    "OperationKind",
    "RegistryCloseReport",
    "SecretCredential",
    "StatefulIntegration",
    "StaticCredentialResolver",
    "StrictHTTPSRedirectHandler",
    "json_snapshot",
    "register_integration_actions",
]
