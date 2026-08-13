"""Entry-point discovery and failure-isolated plugin lifecycle management."""

from __future__ import annotations

import asyncio
import builtins
import inspect
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from importlib import metadata as importlib_metadata
from typing import Any, Protocol, cast

from jarvis.core.actions import Action, ActionRegistry
from jarvis.core.events import Event, EventBus, EventName
from jarvis.plugins.api import PluginContext, PluginProtocol, invoke_lifecycle
from jarvis.plugins.models import (
    JARVIS_PLUGIN_API,
    PLUGIN_ENTRY_POINT_GROUP,
    PluginInfo,
    PluginMetadata,
    PluginNotFoundError,
    PluginRegistrationError,
    PluginStatus,
    validate_plugin_id,
)
from jarvis.plugins.state import InMemoryPluginStateRepository, PluginStateRepository


class EntryPointLike(Protocol):
    """Subset of ``importlib.metadata.EntryPoint`` used by the manager."""

    name: str
    value: str

    def load(self) -> object:
        """Import and return the declared plugin object."""


EntryPointProvider = Callable[[], object]


@dataclass(slots=True)
class _Record:
    plugin_id: str
    entry_point: EntryPointLike
    distribution: str | None = None
    status: PluginStatus = PluginStatus.DISCOVERED
    metadata: PluginMetadata | None = None
    instance: PluginProtocol | None = None
    context: PluginContext | None = None
    loaded: bool = False
    shutdown_pending: bool = False
    error: str | None = None
    handles: list[_RegistrationHandle] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _RegistrationHandle:
    kind: str
    name: str
    remove: Callable[[], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _RemovalOutcome:
    errors: tuple[BaseException, ...]
    failed_handles: tuple[_RegistrationHandle, ...]


class PluginManager:
    """Discover and manage explicitly installed, trusted local Python plugins.

    Discovery is restricted to the ``jarvis.plugins`` package entry-point group.
    Plugin imports and hooks are isolated from one another, and registrations are
    staged before a manager-owned commit. This is lifecycle isolation, not a Python
    security sandbox.
    """

    def __init__(
        self,
        *,
        action_registry: ActionRegistry | None = None,
        event_bus: EventBus | None = None,
        integration_registry: object | None = None,
        state_repository: PluginStateRepository | None = None,
        entry_points_provider: EntryPointProvider | None = None,
    ) -> None:
        self._actions = action_registry if action_registry is not None else ActionRegistry()
        self._events = event_bus if event_bus is not None else EventBus()
        self._integrations = integration_registry
        self._state = (
            state_repository if state_repository is not None else InMemoryPluginStateRepository()
        )
        self._entry_points_provider = (
            entry_points_provider
            if entry_points_provider is not None
            else importlib_metadata.entry_points
        )
        self._records: dict[str, _Record] = {}
        self._metadata_owners: dict[str, str] = {}
        self._owned_actions: dict[str, str] = {}
        self._owned_integrations: dict[str, str] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._discovered = False

    @property
    def action_registry(self) -> ActionRegistry:
        """Return the manager-owned action registry for core composition."""

        return self._actions

    @property
    def event_bus(self) -> EventBus:
        """Return the manager-owned event bus for core composition."""

        return self._events

    @property
    def loaded_plugins(self) -> tuple[str, ...]:
        """Return loaded plugin identifiers in discovery order."""

        return tuple(record.plugin_id for record in self._records.values() if record.loaded)

    def discover(self, *, refresh: bool = False) -> tuple[PluginInfo, ...]:
        """Enumerate entry points without importing plugin modules."""

        if self._discovered and not refresh:
            return self.list()
        if refresh and any(
            record.loaded or record.shutdown_pending or record.handles or record.context is not None
            for record in self._records.values()
        ):
            raise RuntimeError(
                "cannot refresh plugin discovery while plugins are active or cleanup is pending"
            )

        try:
            discovered = tuple(_select_entry_points(self._entry_points_provider()))
        except BaseException as exc:
            if _must_propagate(exc):
                raise
            self._records = {}
            self._discovered = True
            return ()

        records: list[_Record] = []
        for index, entry_point in enumerate(discovered):
            raw_name = getattr(entry_point, "name", "")
            try:
                plugin_id = validate_plugin_id(raw_name)
                value = str(getattr(entry_point, "value", "")).strip()
                if not value:
                    raise ValueError("entry point value cannot be empty")
            except (TypeError, ValueError) as exc:
                digest = sha256(f"{raw_name!r}:{index}".encode()).hexdigest()[:12]
                plugin_id = f"invalid-{digest}"
                record = _Record(plugin_id, entry_point)
                record.status = PluginStatus.FAILED
                record.error = _failure("Invalid entry point", exc)
                records.append(record)
                continue
            records.append(
                _Record(
                    plugin_id,
                    entry_point,
                    distribution=_distribution_name(entry_point),
                )
            )

        counts = Counter(record.plugin_id.casefold() for record in records)
        self._records = {}
        for record in sorted(records, key=lambda item: (item.plugin_id.casefold(), item.plugin_id)):
            key = record.plugin_id.casefold()
            if counts[key] > 1:
                record.status = PluginStatus.DUPLICATE
                record.error = "Duplicate plugin entry-point identifier."
            # A generated invalid identifier is unique, as are valid identifiers after
            # case-insensitive duplicate collapse. Keeping one record makes lookup safe.
            self._records.setdefault(key, record)
        self._metadata_owners.clear()
        self._discovered = True
        return self.list()

    def list(self) -> tuple[PluginInfo, ...]:
        """Return current plugin records without importing undiscovered code."""

        if not self._discovered:
            return self.discover()
        return tuple(self._info(record) for record in self._records.values())

    def inspect(self, plugin_id: str) -> PluginInfo:
        """Import and validate one plugin without initializing or registering it."""

        record = self._record(plugin_id)
        if record.status is PluginStatus.DUPLICATE or record.instance is not None:
            return self._info(record)
        if record.status is PluginStatus.FAILED and record.metadata is None:
            return self._info(record)

        try:
            loaded_object = record.entry_point.load()
            instance = _materialize_plugin(loaded_object)
            metadata = instance.metadata
            if not isinstance(metadata, PluginMetadata):
                raise TypeError("metadata must be a PluginMetadata instance")
            _validate_hooks(instance)
        except BaseException as exc:
            if _must_propagate(exc):
                raise
            record.status = PluginStatus.FAILED
            record.error = _failure("Plugin inspection failed", exc)
            return self._info(record)

        record.instance = instance
        record.metadata = metadata
        if metadata.api_version != JARVIS_PLUGIN_API:
            record.status = PluginStatus.INCOMPATIBLE
            record.error = (
                f"Plugin API {metadata.api_version} is incompatible with "
                f"JARVIS plugin API {JARVIS_PLUGIN_API}."
            )
            return self._info(record)

        metadata_key = metadata.name.casefold()
        owner = self._metadata_owners.get(metadata_key)
        if owner is not None and owner != record.plugin_id.casefold():
            record.status = PluginStatus.DUPLICATE
            record.error = f"Plugin metadata name conflicts with installed plugin {owner!r}."
            return self._info(record)
        self._metadata_owners[metadata_key] = record.plugin_id.casefold()
        record.status = PluginStatus.DISCOVERED
        record.error = None
        return self._info(record)

    def inspect_all(self) -> tuple[PluginInfo, ...]:
        """Inspect each plugin independently and return every outcome."""

        return tuple(self.inspect(record.plugin_id) for record in self._records_or_discover())

    async def load(self, plugin_id: str) -> PluginInfo:
        """Initialize and atomically register one plugin without persisting enablement."""

        async with self._lifecycle_lock:
            return await self._load_unlocked(plugin_id)

    async def enable(self, plugin_id: str) -> PluginInfo:
        """Load a plugin and persist its desired enabled state on success."""

        async with self._lifecycle_lock:
            info = await self._load_unlocked(plugin_id)
            record = self._record(plugin_id)
            if not record.loaded:
                return info
            try:
                self._state.set_enabled(record.plugin_id, True)
            except BaseException as exc:
                if _must_propagate(exc):
                    raise
                record.status = PluginStatus.FAILED
                record.error = _failure("Could not persist plugin enablement", exc)
                cleanup_task = asyncio.create_task(self._unload_record(record))
                await _await_lifecycle_task(cleanup_task)
                await self._emit("plugin.failed", record)
                return self._info(record)
            record.status = PluginStatus.ENABLED
            record.error = None
            await self._emit("plugin.enabled", record)
            return self._info(record)

    async def disable(self, plugin_id: str) -> PluginInfo:
        """Persist disablement, unregister capabilities, and shut the plugin down."""

        async with self._lifecycle_lock:
            record = self._record(plugin_id)
            try:
                self._state.set_enabled(record.plugin_id, False)
            except BaseException as exc:
                if _must_propagate(exc):
                    raise
                record.status = PluginStatus.FAILED
                record.error = _failure("Could not persist plugin disablement", exc)
                await self._emit("plugin.failed", record)
                return self._info(record)
            disable_task = asyncio.create_task(self._finish_disable(record))
            return await _await_lifecycle_task(disable_task)

    async def load_enabled(self) -> tuple[PluginInfo, ...]:
        """Load every discovered plugin whose durable desired state is enabled."""

        async with self._lifecycle_lock:
            self.discover()
            outcomes: list[PluginInfo] = []
            for record in self._records.values():
                if not self._is_enabled(record.plugin_id):
                    continue
                info = await self._load_unlocked(record.plugin_id)
                if record.loaded:
                    record.status = PluginStatus.ENABLED
                    info = self._info(record)
                outcomes.append(info)
            return tuple(outcomes)

    async def shutdown(self) -> tuple[PluginInfo, ...]:
        """Unload all plugins while preserving desired enablement for next startup."""

        async with self._lifecycle_lock:
            shutdown_task = asyncio.create_task(self._shutdown_unlocked())
            return await _await_lifecycle_task(shutdown_task)

    async def _finish_disable(self, record: _Record) -> PluginInfo:
        cleanup_errors = await self._unload_record(record)
        if cleanup_errors:
            record.status = PluginStatus.FAILED
            record.error = "Plugin disabled, but one or more cleanup hooks failed."
            await self._emit("plugin.failed", record)
        else:
            record.status = PluginStatus.DISABLED
            record.error = None
            await self._emit("plugin.disabled", record)
        return self._info(record)

    async def _shutdown_unlocked(self) -> tuple[PluginInfo, ...]:
        outcomes: list[PluginInfo] = []
        for record in reversed(tuple(self._records.values())):
            if (
                not record.loaded
                and not record.shutdown_pending
                and not record.handles
                and record.context is None
            ):
                continue
            cleanup_errors = await self._unload_record(record)
            if cleanup_errors:
                record.status = PluginStatus.FAILED
                record.error = "Plugin shutdown completed with cleanup failures."
            else:
                record.status = PluginStatus.DISCOVERED
                record.error = None
            outcomes.append(self._info(record))
        return tuple(outcomes)

    async def _load_unlocked(self, plugin_id: str) -> PluginInfo:
        info = self.inspect(plugin_id)
        record = self._record(plugin_id)
        if record.loaded:
            return info
        if record.status in {
            PluginStatus.FAILED,
            PluginStatus.INCOMPATIBLE,
            PluginStatus.DUPLICATE,
        }:
            return info
        assert record.instance is not None

        context = PluginContext(
            record.plugin_id,
            event_publisher=self._publisher(record.plugin_id),
        )
        record.context = context
        # From this point onward initialization may have acquired plugin-owned
        # resources, so shutdown remains pending until it completes successfully.
        record.shutdown_pending = True
        try:
            await invoke_lifecycle(record.instance.initialize, context)
            await invoke_lifecycle(record.instance.register, context)
            context.seal()
            # Registration can call async third-party registries.  Keep the commit
            # task alive if the caller is cancelled so its exact mutation set is
            # known before we start rollback.  Otherwise cancellation after a
            # registry mutation but before the handle is recorded can orphan it.
            commit_task = asyncio.create_task(self._commit(record, context))
            try:
                record.handles = await asyncio.shield(commit_task)
            except asyncio.CancelledError:
                try:
                    record.handles = await _finish_task(commit_task)
                except BaseException:
                    # _commit performs its own best-effort rollback on failure.
                    # The load-level cleanup below retries any handles it retained.
                    pass
                raise

            record.loaded = True
            record.status = (
                PluginStatus.ENABLED if self._is_enabled(record.plugin_id) else PluginStatus.LOADED
            )
            record.error = None
            await self._emit("plugin.loaded", record)
        except asyncio.CancelledError:
            context.seal()
            cleanup_task = asyncio.create_task(self._abort_load(record, context))
            cleanup_errors = await _finish_task(cleanup_task)
            if cleanup_errors or record.handles or record.shutdown_pending:
                record.status = PluginStatus.FAILED
                record.error = "Plugin load was cancelled and cleanup is incomplete."
            else:
                record.status = PluginStatus.DISCOVERED
                record.error = None
            raise
        except BaseException as exc:
            context.seal()
            if _must_propagate(exc):
                raise
            cleanup_task = asyncio.create_task(self._abort_load(record, context))
            await _await_lifecycle_task(cleanup_task)
            record.status = PluginStatus.FAILED
            record.error = _failure("Plugin initialization failed", exc)
            await self._emit("plugin.failed", record)
            return self._info(record)
        return self._info(record)

    async def _abort_load(
        self,
        record: _Record,
        context: PluginContext,
    ) -> tuple[BaseException, ...]:
        """Fully revoke a load transaction, including cancellation paths."""

        errors: list[BaseException] = []
        removal = await _remove_handles(record.handles, propagate_cancellation=False)
        errors.extend(removal.errors)
        record.handles = list(removal.failed_handles)

        # Revoke the context before invoking untrusted shutdown code so a plugin
        # cannot publish events while its registrations are being torn down.
        context.deactivate()
        record.context = None
        if record.shutdown_pending and record.instance is not None:
            try:
                await invoke_lifecycle(record.instance.shutdown)
            except BaseException as exc:
                errors.append(exc)
            else:
                record.shutdown_pending = False
        record.loaded = False
        return tuple(errors)

    async def _commit(
        self,
        record: _Record,
        context: PluginContext,
    ) -> builtins.list[_RegistrationHandle]:
        actions = context.staged_actions
        integrations = context.staged_integrations
        listeners = context.staged_event_listeners
        self._preflight(actions, integrations)

        committed: list[_RegistrationHandle] = []
        try:
            for integration in integrations:
                # Record the inverse before awaiting registration.  A third-party
                # registry may mutate and then be cancelled before returning.
                committed.append(
                    _RegistrationHandle(
                        "integration",
                        integration.name,
                        self._integration_remover(
                            integration.name,
                            integration.integration,
                        ),
                    )
                )
                await _registry_register(
                    self._integrations,
                    integration.name,
                    integration.integration,
                )
                self._owned_integrations[integration.name] = record.plugin_id
            for listener in listeners:
                unsubscribe = self._events.subscribe(
                    listener.name,
                    _isolated_event_handler(listener.handler),
                )

                def remove_listener(unsubscribe: Callable[[], bool] = unsubscribe) -> None:
                    unsubscribe()

                committed.append(_RegistrationHandle("event", str(listener.name), remove_listener))
            for action in actions:
                isolated_action = _isolated_action(action)
                self._actions.register(isolated_action)
                self._owned_actions[action.name] = record.plugin_id
                committed.append(
                    _RegistrationHandle(
                        "action",
                        action.name,
                        self._action_remover(isolated_action),
                    )
                )
        except BaseException as exc:
            # Rollback is part of the commit transaction even for cancellation.
            # Run it in its own task so repeated cancellation cannot interrupt it.
            rollback_task = asyncio.create_task(
                _remove_handles(committed, propagate_cancellation=False)
            )
            rollback = await _finish_task(rollback_task)
            record.handles = list(rollback.failed_handles)
            if _must_propagate(exc):
                raise
            if rollback.errors:
                raise PluginRegistrationError(
                    "plugin registration failed and rollback was incomplete"
                ) from exc
            raise PluginRegistrationError("plugin registration could not be committed") from exc
        return committed

    def _preflight(
        self,
        actions: tuple[Action, ...],
        integrations: tuple[Any, ...],
    ) -> None:
        action_names = [action.name for action in actions]
        if len(set(action_names)) != len(action_names):
            raise PluginRegistrationError("plugin staged duplicate action names")
        conflicts = sorted(name for name in action_names if name in self._actions)
        if conflicts:
            raise PluginRegistrationError(
                f"plugin action conflicts with registered action {conflicts[0]!r}"
            )
        if actions and not _can_remove_actions(self._actions):
            raise PluginRegistrationError("action registry does not support atomic rollback")

        if integrations and self._integrations is None:
            raise PluginRegistrationError("no integration registry is configured")
        integration_names = [integration.name for integration in integrations]
        if len(set(integration_names)) != len(integration_names):
            raise PluginRegistrationError("plugin staged duplicate integration names")
        for name in integration_names:
            if name in self._owned_integrations or _registry_contains(self._integrations, name):
                raise PluginRegistrationError(
                    f"plugin integration conflicts with registered integration {name!r}"
                )
        if integrations and not _can_unregister(self._integrations):
            raise PluginRegistrationError("integration registry does not support rollback")

    def _action_remover(self, action: Action) -> Callable[[], Awaitable[None]]:
        async def remove() -> None:
            if action.name not in self._actions:
                self._owned_actions.pop(action.name, None)
                return
            try:
                result = _action_unregister(self._actions, action)
                if inspect.isawaitable(result):
                    await result
            except BaseException:
                if action.name not in self._actions:
                    self._owned_actions.pop(action.name, None)
                    return
                raise
            self._owned_actions.pop(action.name, None)

        return remove

    def _integration_remover(
        self,
        name: str,
        integration: object,
    ) -> Callable[[], Awaitable[None]]:
        async def remove() -> None:
            if not _registry_contains(self._integrations, name):
                self._owned_integrations.pop(name, None)
                return
            try:
                result = _registry_unregister(self._integrations, name, integration)
                if inspect.isawaitable(result):
                    await result
            except BaseException:
                if not _registry_contains(self._integrations, name):
                    self._owned_integrations.pop(name, None)
                    return
                raise
            self._owned_integrations.pop(name, None)

        return remove

    async def _unload_record(self, record: _Record) -> tuple[BaseException, ...]:
        removal = await _remove_handles(record.handles, propagate_cancellation=False)
        errors = list(removal.errors)
        record.handles = list(removal.failed_handles)
        if record.context is not None:
            record.context.deactivate()
            record.context = None
        if record.shutdown_pending and record.instance is not None:
            try:
                await invoke_lifecycle(record.instance.shutdown)
            except BaseException as exc:
                errors.append(exc)
            else:
                record.shutdown_pending = False
        record.loaded = False
        return tuple(errors)

    def _publisher(self, plugin_id: str) -> Callable[..., Awaitable[tuple[Exception, ...]]]:
        async def publish(
            event: Event | EventName | str,
            payload: Mapping[str, Any] | None = None,
        ) -> tuple[Exception, ...]:
            if isinstance(event, Event):
                if payload is not None:
                    raise TypeError("payload cannot be supplied with an Event")
                return await self._events.publish(
                    event.name,
                    event.payload,
                    source=f"plugin:{plugin_id}",
                )
            return await self._events.publish(
                event,
                payload,
                source=f"plugin:{plugin_id}",
            )

        return publish

    async def _emit(self, name: str, record: _Record) -> None:
        payload: dict[str, Any] = {
            "plugin_id": record.plugin_id,
            "status": record.status.value,
        }
        if record.metadata is not None:
            payload["name"] = record.metadata.name
            payload["version"] = record.metadata.version
        await self._events.publish(name, payload, source="plugin-manager")

    def _record(self, plugin_id: str) -> _Record:
        normalized = validate_plugin_id(plugin_id).casefold()
        if not self._discovered:
            self.discover()
        try:
            return self._records[normalized]
        except KeyError as exc:
            raise PluginNotFoundError(f"No installed plugin named {plugin_id!r}") from exc

    def _records_or_discover(self) -> tuple[_Record, ...]:
        if not self._discovered:
            self.discover()
        return tuple(self._records.values())

    def _is_enabled(self, plugin_id: str) -> bool:
        try:
            return self._state.is_enabled(plugin_id)
        except BaseException as exc:
            if _must_propagate(exc):
                raise
            return False

    def _info(self, record: _Record) -> PluginInfo:
        entry_point_value = str(getattr(record.entry_point, "value", "")).strip()
        details = {
            "actions": tuple(handle.name for handle in record.handles if handle.kind == "action"),
            "integrations": tuple(
                handle.name for handle in record.handles if handle.kind == "integration"
            ),
            "event_listeners": tuple(
                handle.name for handle in record.handles if handle.kind == "event"
            ),
        }
        return PluginInfo(
            plugin_id=record.plugin_id,
            entry_point=entry_point_value or "<invalid>",
            distribution=record.distribution,
            status=record.status,
            enabled=self._is_enabled(record.plugin_id),
            loaded=record.loaded,
            metadata=record.metadata,
            error=record.error,
            details=details,
        )


def _select_entry_points(value: object) -> Iterable[EntryPointLike]:
    select = getattr(value, "select", None)
    if callable(select):
        return tuple(select(group=PLUGIN_ENTRY_POINT_GROUP))
    if isinstance(value, Mapping):
        return tuple(value.get(PLUGIN_ENTRY_POINT_GROUP, ()))
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return ()
    return tuple(
        cast(EntryPointLike, entry_point)
        for entry_point in value
        if getattr(entry_point, "group", PLUGIN_ENTRY_POINT_GROUP) == PLUGIN_ENTRY_POINT_GROUP
    )


def _materialize_plugin(loaded: object) -> PluginProtocol:
    candidate = loaded
    if inspect.isclass(candidate):
        candidate = candidate()
    elif not _looks_like_plugin(candidate) and callable(candidate):
        candidate = candidate()
    if not _looks_like_plugin(candidate):
        raise TypeError(
            "entry point must expose a Plugin instance, class, or zero-argument factory"
        )
    return candidate  # type: ignore[return-value]


def _looks_like_plugin(value: object) -> bool:
    return all(
        hasattr(value, attribute)
        for attribute in ("metadata", "initialize", "register", "shutdown")
    )


def _validate_hooks(instance: PluginProtocol) -> None:
    for name in ("initialize", "register", "shutdown"):
        if not callable(getattr(instance, name, None)):
            raise TypeError(f"plugin {name} hook must be callable")


def _distribution_name(entry_point: object) -> str | None:
    distribution = getattr(entry_point, "dist", None)
    name = getattr(distribution, "name", None)
    return str(name) if name else None


def _can_remove_actions(registry: ActionRegistry) -> bool:
    return callable(getattr(registry, "unregister", None)) or isinstance(
        getattr(registry, "_actions", None), dict
    )


def _action_unregister(
    registry: ActionRegistry,
    action: Action,
) -> None | Awaitable[None]:
    unregister = getattr(registry, "unregister", None)
    if callable(unregister):
        return unregister(action.name)
    storage = getattr(registry, "_actions", None)
    if isinstance(storage, dict) and storage.get(action.name) is action:
        del storage[action.name]
        return None
    raise PluginRegistrationError(f"could not safely unregister action {action.name!r}")


def _can_unregister(registry: object | None) -> bool:
    return registry is not None and (
        callable(getattr(registry, "unregister", None))
        or callable(getattr(registry, "remove", None))
    )


def _registry_contains(registry: object | None, name: str) -> bool:
    if registry is None:
        return False
    try:
        return name in registry  # type: ignore[operator]
    except (TypeError, AttributeError):
        names = getattr(registry, "names", ())
        return name in names


async def _registry_register(registry: object | None, name: str, value: object) -> None:
    if registry is None:
        raise PluginRegistrationError("no integration registry is configured")
    register = getattr(registry, "register", None)
    if not callable(register):
        raise PluginRegistrationError("integration registry has no register method")
    result = _invoke_registry_method(register, name, value)
    if inspect.isawaitable(result):
        await result


def _registry_unregister(
    registry: object | None,
    name: str,
    value: object,
) -> None | Awaitable[None]:
    if registry is None:
        raise PluginRegistrationError("no integration registry is configured")
    unregister = getattr(registry, "unregister", None) or getattr(registry, "remove", None)
    if not callable(unregister):
        raise PluginRegistrationError("integration registry has no unregister method")
    return _invoke_registry_method(unregister, name, value, prefer_name_only=True)


def _invoke_registry_method(
    method: Callable[..., Any],
    name: str,
    value: object,
    *,
    prefer_name_only: bool = False,
) -> Any:
    try:
        parameters = tuple(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):
        parameters = ()
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind
        in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    )
    has_varargs = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters
    )
    if prefer_name_only and not has_varargs and len(positional) <= 1:
        return method(name)
    if not prefer_name_only and not has_varargs and len(positional) <= 1:
        return method(value)
    return method(name, value)


async def _remove_handles(
    handles: Iterable[_RegistrationHandle],
    *,
    propagate_cancellation: bool = True,
) -> _RemovalOutcome:
    errors: list[BaseException] = []
    failed: list[_RegistrationHandle] = []
    for handle in reversed(tuple(handles)):
        try:
            result = handle.remove()
            if inspect.isawaitable(result):
                await result
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt) or (
                isinstance(exc, asyncio.CancelledError) and propagate_cancellation
            ):
                raise
            errors.append(exc)
            failed.append(handle)
    # Restore registration order so a later cleanup retry still removes in reverse.
    failed.reverse()
    return _RemovalOutcome(tuple(errors), tuple(failed))


async def _finish_task(task: asyncio.Task[Any]) -> Any:
    """Wait for *task* despite cancellation of the current lifecycle caller."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
            continue
    return task.result()


async def _await_lifecycle_task(task: asyncio.Task[Any]) -> Any:
    """Shield lifecycle cleanup and re-raise cancellation only after it completes."""

    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _finish_task(task)
        raise


def _failure(prefix: str, exception: BaseException) -> str:
    """Return bounded diagnostics without echoing plugin-supplied exception text."""

    return f"{prefix} ({type(exception).__name__})."


def _must_propagate(exception: BaseException) -> bool:
    return isinstance(exception, (KeyboardInterrupt, asyncio.CancelledError))


class _PluginExecutionFailure(Exception):
    """Convert process-exiting plugin faults into an ordinary isolated exception."""


def _isolated_action(action: Action) -> Action:
    async def invoke(**arguments: Any) -> Any:
        try:
            result = action.handler(**arguments)
            if inspect.isawaitable(result):
                return await result
            return result
        except BaseException as exc:
            if _must_propagate(exc):
                raise
            raise _PluginExecutionFailure("plugin action failed") from exc

    return replace(action, handler=invoke)


def _isolated_event_handler(handler: Callable[..., Any]) -> Callable[..., Awaitable[None]]:
    async def dispatch(event: Event) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except BaseException as exc:
            if _must_propagate(exc):
                raise

    return dispatch
