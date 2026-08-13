"""Failure-isolating registry for external integrations."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.integrations.base import Integration, IntegrationSnapshot
from jarvis.integrations.errors import (
    DuplicateIntegrationError,
    IntegrationLifecycleError,
    IntegrationNotFoundError,
    IntegrationRegistryClosedError,
)


@dataclass(frozen=True, slots=True)
class IntegrationFailure:
    """Sanitized lifecycle failure recorded while handling a registry batch."""

    integration: str
    operation: str

    def to_json(self) -> dict[str, str]:
        return {"integration": self.integration, "operation": self.operation}


@dataclass(frozen=True, slots=True)
class RegistryCloseReport:
    """Result of closing every integration, including isolated failures."""

    closed: tuple[str, ...]
    failures: tuple[IntegrationFailure, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "closed": list(self.closed),
            "failures": [failure.to_json() for failure in self.failures],
        }


class IntegrationRegistry:
    """Own named integrations without allowing one failure to crash its peers."""

    def __init__(self) -> None:
        self._integrations: dict[str, Integration] = {}
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def names(self) -> tuple[str, ...]:
        """Return registered names without exposing integration implementation state."""

        return tuple(sorted(self._integrations))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._integrations

    def register(self, integration: Integration) -> Integration:
        self._ensure_open()
        if not isinstance(integration, Integration):
            raise TypeError("integration must implement Integration")
        name = integration.metadata.name
        if name in self._integrations:
            raise DuplicateIntegrationError(f"Integration {name!r} is already registered")
        self._integrations[name] = integration
        return integration

    def list(self) -> tuple[IntegrationSnapshot, ...]:
        """Return stable non-secret snapshots sorted by integration name."""

        return tuple(self._integrations[name].snapshot() for name in sorted(self._integrations))

    def get(self, name: str) -> Integration:
        self._ensure_open()
        try:
            return self._integrations[name]
        except KeyError:
            raise IntegrationNotFoundError(f"Integration {name!r} is not registered") from None

    async def connect(self, name: str) -> IntegrationSnapshot:
        integration = self.get(name)
        try:
            await integration.connect()
        except Exception:
            raise IntegrationLifecycleError(name, "connect") from None
        return integration.snapshot()

    async def disconnect(self, name: str) -> IntegrationSnapshot:
        integration = self.get(name)
        try:
            await integration.disconnect()
        except Exception:
            raise IntegrationLifecycleError(name, "disconnect") from None
        return integration.snapshot()

    async def unregister(self, name: str, *, close: bool = True) -> Integration | None:
        """Remove an integration, optionally closing it before removal.

        Closing occurs first so a failed cleanup cannot silently orphan a live
        provider.  The failure is sanitized and the integration remains registered
        so callers can retry or inspect its status.
        """

        self._ensure_open()
        integration = self._integrations.get(name)
        if integration is None:
            return None
        if close:
            try:
                await integration.close()
            except Exception:
                raise IntegrationLifecycleError(name, "close") from None
        return self._integrations.pop(name)

    async def close(self) -> RegistryCloseReport:
        if self._closed:
            return RegistryCloseReport((), ())
        closed: list[str] = []
        failures: list[IntegrationFailure] = []
        for name in sorted(self._integrations):
            try:
                await self._integrations[name].close()
            except Exception:
                failures.append(IntegrationFailure(name, "close"))
            else:
                closed.append(name)
        self._closed = not failures
        return RegistryCloseReport(tuple(closed), tuple(failures))

    def _ensure_open(self) -> None:
        if self._closed:
            raise IntegrationRegistryClosedError("The integration registry is closed")
