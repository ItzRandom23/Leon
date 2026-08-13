"""Provider-neutral integration lifecycle and operation metadata."""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from jarvis.skills.base import RiskLevel

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class IntegrationStatus(StrEnum):
    """Observable lifecycle state for an external integration."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    FAILED = "failed"
    CLOSED = "closed"


class OperationKind(StrEnum):
    """Whether an integration operation reads or mutates external state."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class IntegrationOperation:
    """Permission-ready semantics for an operation exposed by a provider."""

    name: str
    kind: OperationKind
    risk_level: RiskLevel
    description: str
    confirmation_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError(f"Invalid integration operation name: {self.name!r}")
        if not isinstance(self.kind, OperationKind):
            object.__setattr__(self, "kind", OperationKind(self.kind))
        if not isinstance(self.risk_level, RiskLevel):
            object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))
        description = self.description.strip()
        if not description:
            raise ValueError("operation description cannot be empty")
        object.__setattr__(self, "description", description)
        if not isinstance(self.confirmation_required, bool):
            raise TypeError("confirmation_required must be a boolean")
        if self.kind is not OperationKind.READ and self.risk_level is RiskLevel.READ:
            raise ValueError("write and delete operations cannot use READ risk")
        if self.kind is OperationKind.DELETE and self.risk_level is not RiskLevel.DESTRUCTIVE:
            raise ValueError("delete operations must be DESTRUCTIVE")

    @property
    def mutates_external_state(self) -> bool:
        return self.kind is not OperationKind.READ

    def to_json(self) -> dict[str, str | bool]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "risk_level": self.risk_level.value,
            "description": self.description,
            "confirmation_required": self.confirmation_required,
        }


@dataclass(frozen=True, slots=True)
class IntegrationMetadata:
    """Static, non-secret information about a registered integration."""

    name: str
    display_name: str
    description: str
    operations: tuple[IntegrationOperation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError(f"Invalid integration name: {self.name!r}")
        display_name = self.display_name.strip()
        description = self.description.strip()
        if not display_name or not description:
            raise ValueError("display_name and description cannot be empty")
        operations = tuple(self.operations)
        if not all(isinstance(item, IntegrationOperation) for item in operations):
            raise TypeError("operations must contain IntegrationOperation values")
        names = [item.name for item in operations]
        if len(names) != len(set(names)):
            raise ValueError("integration operation names must be unique")
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "operations", operations)

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "operations": [operation.to_json() for operation in self.operations],
        }


@dataclass(frozen=True, slots=True)
class IntegrationSnapshot:
    """A JSON-safe registry view that contains no implementation state."""

    metadata: IntegrationMetadata
    status: IntegrationStatus

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, IntegrationMetadata):
            raise TypeError("metadata must be IntegrationMetadata")
        if not isinstance(self.status, IntegrationStatus):
            object.__setattr__(self, "status", IntegrationStatus(self.status))

    def to_json(self) -> dict[str, object]:
        return {"metadata": self.metadata.to_json(), "status": self.status.value}


class Integration(ABC):
    """Lifecycle contract implemented by every external integration."""

    @property
    @abstractmethod
    def metadata(self) -> IntegrationMetadata:
        """Return static non-secret metadata."""

    @property
    @abstractmethod
    def status(self) -> IntegrationStatus:
        """Return current lifecycle status."""

    @abstractmethod
    async def connect(self) -> None:
        """Resolve credentials and establish or verify the provider session."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Release provider session state; the object may reconnect."""

    @abstractmethod
    async def close(self) -> None:
        """Permanently release this integration."""

    def snapshot(self) -> IntegrationSnapshot:
        return IntegrationSnapshot(self.metadata, self.status)


class StatefulIntegration(Integration, ABC):
    """Small reusable state machine for integration implementations."""

    def __init__(self, metadata: IntegrationMetadata) -> None:
        if not isinstance(metadata, IntegrationMetadata):
            raise TypeError("metadata must be IntegrationMetadata")
        self._metadata = metadata
        self._status = IntegrationStatus.DISCONNECTED
        self._lifecycle_lock = asyncio.Lock()

    @property
    def metadata(self) -> IntegrationMetadata:
        return self._metadata

    @property
    def status(self) -> IntegrationStatus:
        return self._status

    async def connect(self) -> None:
        async with self._lifecycle_lock:
            if self._status is IntegrationStatus.CLOSED:
                raise RuntimeError("The integration is closed")
            if self._status is IntegrationStatus.CONNECTED:
                return
            self._status = IntegrationStatus.CONNECTING
            try:
                await self._connect()
            except BaseException:
                self._status = IntegrationStatus.FAILED
                raise
            self._status = IntegrationStatus.CONNECTED

    async def disconnect(self) -> None:
        async with self._lifecycle_lock:
            if self._status in {IntegrationStatus.DISCONNECTED, IntegrationStatus.CLOSED}:
                return
            self._status = IntegrationStatus.DISCONNECTING
            try:
                await self._disconnect()
            except BaseException:
                self._status = IntegrationStatus.FAILED
                raise
            self._status = IntegrationStatus.DISCONNECTED

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._status is IntegrationStatus.CLOSED:
                return
            try:
                await self._disconnect()
            except BaseException:
                self._status = IntegrationStatus.FAILED
                raise
            self._status = IntegrationStatus.CLOSED

    @abstractmethod
    async def _connect(self) -> None:
        """Implementation hook for :meth:`connect`."""

    @abstractmethod
    async def _disconnect(self) -> None:
        """Implementation hook for disconnect and close."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.metadata.name!r}, status={self.status.value!r})"
