"""Contributor-facing plugin protocol and staged registration context."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias, runtime_checkable

from jarvis.core.actions import Action
from jarvis.core.events import Event, EventHandler, EventName
from jarvis.integrations import Integration
from jarvis.plugins.models import PluginMetadata, PluginRegistrationError, validate_plugin_id

LifecycleResult: TypeAlias = None | Awaitable[None]
EventPublisher: TypeAlias = Callable[
    [Event | EventName | str, dict[str, Any] | None], Awaitable[tuple[Exception, ...]]
]


@runtime_checkable
class PluginProtocol(Protocol):
    """Structural contract accepted from an installed entry point."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return immutable plugin metadata."""

    def initialize(self, context: PluginContext) -> LifecycleResult:
        """Allocate private resources without mutating JARVIS registries directly."""

    def register(self, context: PluginContext) -> LifecycleResult:
        """Stage capabilities through the provided context."""

    def shutdown(self) -> LifecycleResult:
        """Release private resources."""


class Plugin(ABC):
    """Convenience base class for trusted local JARVIS plugins.

    Plugins are normal Python code and are not sandboxed. Registration is staged by
    :class:`PluginContext`; JARVIS Core receives changes only after both lifecycle
    setup and registration complete successfully.
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return the plugin's immutable declaration."""

    def initialize(self, context: PluginContext) -> LifecycleResult:
        """Allocate private resources before registration; the default is a no-op."""

        return None

    @abstractmethod
    def register(self, context: PluginContext) -> LifecycleResult:
        """Stage actions, integrations, and event listeners."""

    def shutdown(self) -> LifecycleResult:
        """Release plugin-owned resources; the default is a no-op."""

        return None


@dataclass(frozen=True, slots=True)
class StagedIntegration:
    """One named integration waiting for an atomic commit."""

    name: str
    integration: Integration


@dataclass(frozen=True, slots=True)
class StagedEventListener:
    """One event subscription waiting for an atomic commit."""

    name: EventName | str
    handler: EventHandler


class PluginContext:
    """A plugin-scoped staging area with no direct registry references."""

    def __init__(
        self,
        plugin_id: str,
        *,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self._plugin_id = validate_plugin_id(plugin_id)
        self._event_publisher = event_publisher
        self._actions: list[Action] = []
        self._integrations: list[StagedIntegration] = []
        self._listeners: list[StagedEventListener] = []
        self._sealed = False
        self._active = True

    @property
    def plugin_id(self) -> str:
        """Return the owning entry-point identifier."""

        return self._plugin_id

    @property
    def sealed(self) -> bool:
        """Whether the staging phase has ended."""

        return self._sealed

    @property
    def staged_actions(self) -> tuple[Action, ...]:
        """Return a read-only snapshot for the manager's commit phase."""

        return tuple(self._actions)

    @property
    def staged_integrations(self) -> tuple[StagedIntegration, ...]:
        """Return a read-only snapshot for the manager's commit phase."""

        return tuple(self._integrations)

    @property
    def staged_event_listeners(self) -> tuple[StagedEventListener, ...]:
        """Return a read-only snapshot for the manager's commit phase."""

        return tuple(self._listeners)

    def register_action(self, action: Action) -> Action:
        """Stage one action, rejecting malformed or locally duplicated names."""

        self._ensure_open()
        if not isinstance(action, Action):
            raise TypeError("register_action expects an Action")
        if any(existing.name == action.name for existing in self._actions):
            raise PluginRegistrationError(f"duplicate staged action {action.name!r}")
        self._actions.append(action)
        return action

    def register_tool(self, action: Action) -> Action:
        """Alias for action registration in model-tool terminology."""

        return self.register_action(action)

    def register_skill(self, action: Action) -> Action:
        """Alias for registering an action-based plugin skill."""

        return self.register_action(action)

    def register_integration(self, name: str, integration: Integration) -> Integration:
        """Stage one opaque integration for an injected integration registry."""

        self._ensure_open()
        normalized = validate_plugin_id(name)
        if not isinstance(integration, Integration):
            raise TypeError("integration must implement jarvis.integrations.Integration")
        if integration.metadata.name != normalized:
            raise ValueError("staged integration name must match integration.metadata.name")
        if any(existing.name == normalized for existing in self._integrations):
            raise PluginRegistrationError(f"duplicate staged integration {normalized!r}")
        self._integrations.append(StagedIntegration(normalized, integration))
        return integration

    def subscribe_event(
        self,
        name: EventName | str,
        handler: EventHandler,
    ) -> EventHandler:
        """Stage an event listener without subscribing it yet."""

        self._ensure_open()
        if not isinstance(name, (str, EventName)) or not str(name).strip():
            raise ValueError("event name cannot be empty")
        if not callable(handler):
            raise TypeError("event handler must be callable")
        self._listeners.append(StagedEventListener(name, handler))
        return handler

    def register_event_listener(
        self,
        name: EventName | str,
        handler: EventHandler,
    ) -> EventHandler:
        """Readable alias for :meth:`subscribe_event`."""

        return self.subscribe_event(name, handler)

    async def publish_event(
        self,
        event: Event | EventName | str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[Exception, ...]:
        """Publish through the manager-owned bus with plugin source attribution."""

        if self._event_publisher is None:
            raise PluginRegistrationError("no event bus is available to this plugin")
        if not self._active:
            raise PluginRegistrationError("plugin context is no longer active")
        return await self._event_publisher(event, payload)

    def seal(self) -> None:
        """Prevent plugin code from changing the completed staging transaction."""

        self._sealed = True

    def deactivate(self) -> None:
        """Revoke runtime services after plugin shutdown."""

        self._sealed = True
        self._active = False

    def _ensure_open(self) -> None:
        if self._sealed:
            raise PluginRegistrationError("plugin registration context is sealed")


async def invoke_lifecycle(method: Callable[..., Any], *arguments: Any) -> None:
    """Invoke a sync or async lifecycle hook and require a no-value result."""

    result = method(*arguments)
    if inspect.isawaitable(result):
        result = await result
    if result is not None:
        raise TypeError("plugin lifecycle hooks must return None")
