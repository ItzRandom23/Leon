"""Tests for safe entry-point discovery and atomic plugin lifecycle management."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.actions import Action, ActionRegistry, ActionRequest
from jarvis.core.events import EventBus
from jarvis.integrations import (
    IntegrationMetadata,
    IntegrationStatus,
    StatefulIntegration,
)
from jarvis.integrations import (
    IntegrationRegistry as CoreIntegrationRegistry,
)
from jarvis.plugins import (
    JARVIS_PLUGIN_API,
    PLUGIN_ENTRY_POINT_GROUP,
    TRUSTED_PLUGIN_WARNING,
    InMemoryPluginStateRepository,
    Plugin,
    PluginContext,
    PluginManager,
    PluginMetadata,
    PluginNotFoundError,
    PluginRegistrationError,
    PluginStateError,
    PluginStatus,
    SQLitePluginStateRepository,
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def action(name: str = "plugin_action") -> Action:
    return Action(name, "A deterministic plugin action.", lambda: "ok")


class StubIntegration(StatefulIntegration):
    def __init__(self, name: str) -> None:
        super().__init__(IntegrationMetadata(name, name.title(), "Test integration."))

    async def _connect(self) -> None:
        return None

    async def _disconnect(self) -> None:
        return None


def integration(name: str) -> StubIntegration:
    return StubIntegration(name)


def metadata(
    *,
    name: str = "Test Plugin",
    api_version: int = JARVIS_PLUGIN_API,
) -> PluginMetadata:
    return PluginMetadata(
        name=name,
        version="1.2.3",
        author="Test author",
        description="Test plugin metadata.",
        permissions=("network", "external_service"),
        capabilities=("actions",),
        dependencies=("example-client>=1",),
        api_version=api_version,
    )


@dataclass
class FakeEntryPoint:
    name: str
    value: str
    target: object
    group: str = PLUGIN_ENTRY_POINT_GROUP
    distribution: str = "test-distribution"
    load_count: int = 0

    @property
    def dist(self) -> SimpleNamespace:
        return SimpleNamespace(name=self.distribution)

    def load(self) -> object:
        self.load_count += 1
        if isinstance(self.target, BaseException):
            raise self.target
        return self.target


class FakeEntryPoints(tuple[FakeEntryPoint, ...]):
    def select(self, *, group: str) -> tuple[FakeEntryPoint, ...]:
        return tuple(entry for entry in self if entry.group == group)


class RecordingPlugin(Plugin):
    def __init__(
        self,
        *,
        declared_metadata: PluginMetadata | None = None,
        contributed_action: Action | None = None,
        fail_initialize: bool = False,
        fail_register: bool = False,
        fail_shutdown: bool = False,
    ) -> None:
        self._metadata = declared_metadata or metadata()
        self.contributed_action = contributed_action
        self.fail_initialize = fail_initialize
        self.fail_register = fail_register
        self.fail_shutdown = fail_shutdown
        self.calls: list[str] = []
        self.context: PluginContext | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    async def initialize(self, context: PluginContext) -> None:
        self.calls.append("initialize")
        self.context = context
        if self.fail_initialize:
            raise RuntimeError("secret initialization detail")

    def register(self, context: PluginContext) -> None:
        self.calls.append("register")
        if self.contributed_action is not None:
            context.register_action(self.contributed_action)
        if self.fail_register:
            raise RuntimeError("secret registration detail")

    async def shutdown(self) -> None:
        self.calls.append("shutdown")
        if self.fail_shutdown:
            raise RuntimeError("secret shutdown detail")


class IntegrationRegistry:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.items: dict[str, object] = {}
        self.fail_on = fail_on
        self.removed: list[str] = []

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.items)

    def __contains__(self, name: object) -> bool:
        return name in self.items

    def register(self, name: str, integration: object) -> None:
        if name == self.fail_on:
            raise RuntimeError("simulated registry failure")
        if name in self.items:
            raise ValueError(name)
        self.items[name] = integration

    def unregister(self, name: str) -> None:
        self.removed.append(name)
        del self.items[name]


def manager_for(
    *entry_points: FakeEntryPoint,
    action_registry: ActionRegistry | None = None,
    event_bus: EventBus | None = None,
    integration_registry: object | None = None,
    state: InMemoryPluginStateRepository | SQLitePluginStateRepository | None = None,
) -> PluginManager:
    points = FakeEntryPoints(entry_points)
    return PluginManager(
        action_registry=action_registry,
        event_bus=event_bus,
        integration_registry=integration_registry,
        state_repository=state,
        entry_points_provider=lambda: points,
    )


def test_metadata_is_strict_immutable_and_complete() -> None:
    declared = metadata()

    assert declared.compatibility_version == JARVIS_PLUGIN_API
    assert declared.permissions == ("network", "external_service")
    assert declared.capabilities == ("actions",)
    assert declared.dependencies == ("example-client>=1",)
    with pytest.raises(AttributeError):
        declared.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="cannot be empty"):
        metadata(name=" ")
    with pytest.raises(TypeError, match="sequence"):
        PluginMetadata("P", "1", "A", "D", permissions="network")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lowercase identifiers"):
        PluginMetadata("P", "1", "A", "D", capabilities=("Bad value",))
    with pytest.raises(ValueError, match="control characters"):
        PluginMetadata("Bad\nName", "1", "A", "D")
    with pytest.raises(ValueError, match="control characters"):
        PluginMetadata("P", "1", "A", "D", dependencies=("bad\ndependency",))
    with pytest.raises(TypeError, match="integer"):
        PluginMetadata("P", "1", "A", "D", api_version=True)  # type: ignore[arg-type]


def test_discovery_uses_only_named_entry_point_group_without_importing() -> None:
    included = FakeEntryPoint("included", "package:Plugin", RecordingPlugin)
    excluded = FakeEntryPoint(
        "excluded",
        "other:Plugin",
        RuntimeError("must not load"),
        group="some.other.group",
    )
    manager = manager_for(included, excluded)

    discovered = manager.discover()

    assert [info.plugin_id for info in discovered] == ["included"]
    assert discovered[0].status is PluginStatus.DISCOVERED
    assert discovered[0].distribution == "test-distribution"
    assert included.load_count == 0
    assert excluded.load_count == 0
    assert "not sandboxed" in discovered[0].warning
    assert TRUSTED_PLUGIN_WARNING == discovered[0].warning


def test_discovery_supports_legacy_mapping_and_is_cached() -> None:
    entry = FakeEntryPoint("example", "package:Plugin", RecordingPlugin)
    calls = 0

    def provider() -> dict[str, tuple[FakeEntryPoint, ...]]:
        nonlocal calls
        calls += 1
        return {PLUGIN_ENTRY_POINT_GROUP: (entry,)}

    manager = PluginManager(entry_points_provider=provider)
    assert manager.discover()[0].plugin_id == "example"
    assert manager.discover()[0].plugin_id == "example"
    assert calls == 1


def test_duplicate_entry_point_identifiers_are_isolated() -> None:
    first = FakeEntryPoint("same", "one:Plugin", RecordingPlugin)
    second = FakeEntryPoint("SAME", "two:Plugin", RecordingPlugin)
    manager = manager_for(first, second)

    info = manager.discover()

    assert len(info) == 1
    assert info[0].status is PluginStatus.DUPLICATE
    assert run(manager.load("same")).status is PluginStatus.DUPLICATE
    assert first.load_count == second.load_count == 0


def test_malformed_entry_point_is_reported_without_breaking_discovery() -> None:
    malformed = FakeEntryPoint("bad name!", "", RecordingPlugin)
    healthy = FakeEntryPoint("healthy", "p:Healthy", RecordingPlugin)
    manager = manager_for(malformed, healthy)

    outcomes = manager.discover()

    assert len(outcomes) == 2
    assert {outcome.status for outcome in outcomes} == {
        PluginStatus.DISCOVERED,
        PluginStatus.FAILED,
    }
    failed = next(outcome for outcome in outcomes if outcome.status is PluginStatus.FAILED)
    assert failed.entry_point == "<invalid>"


def test_inspect_loads_metadata_but_does_not_initialize() -> None:
    plugin = RecordingPlugin()
    entry = FakeEntryPoint("example", "package:plugin", plugin)
    manager = manager_for(entry)

    info = manager.inspect("example")

    assert info.metadata == plugin.metadata
    assert info.status is PluginStatus.DISCOVERED
    assert info.loaded is False
    assert plugin.calls == []
    assert entry.load_count == 1
    assert manager.inspect("example").metadata == plugin.metadata
    assert entry.load_count == 1


def test_classes_and_zero_argument_factories_are_supported() -> None:
    class ConcretePlugin(RecordingPlugin):
        pass

    class Factory:
        def __call__(self) -> RecordingPlugin:
            return RecordingPlugin(declared_metadata=metadata(name="Factory Plugin"))

    class_manager = manager_for(FakeEntryPoint("class", "p:Class", ConcretePlugin))
    factory_manager = manager_for(FakeEntryPoint("factory", "p:factory", Factory()))

    assert class_manager.inspect("class").metadata is not None
    assert factory_manager.inspect("factory").metadata is not None


def test_incompatible_and_invalid_plugins_are_rejected_gracefully() -> None:
    incompatible = RecordingPlugin(declared_metadata=metadata(api_version=JARVIS_PLUGIN_API + 1))
    malformed = SimpleNamespace(metadata={"name": "not strict"})
    import_failure = RuntimeError("sensitive import detail")
    manager = manager_for(
        FakeEntryPoint("future", "p:Future", incompatible),
        FakeEntryPoint("invalid", "p:Invalid", malformed),
        FakeEntryPoint("broken", "p:Broken", import_failure),
    )

    outcomes = {info.plugin_id: info for info in manager.inspect_all()}

    assert outcomes["future"].status is PluginStatus.INCOMPATIBLE
    assert "API" in (outcomes["future"].error or "")
    assert outcomes["invalid"].status is PluginStatus.FAILED
    assert outcomes["broken"].status is PluginStatus.FAILED
    assert "sensitive import detail" not in (outcomes["broken"].error or "")
    assert incompatible.calls == []


def test_successful_load_commits_actions_integrations_and_events_atomically() -> None:
    registry = ActionRegistry()
    integrations = IntegrationRegistry()
    events = EventBus()
    received: list[str] = []

    class FullPlugin(RecordingPlugin):
        def register(self, context: PluginContext) -> None:
            super().register(context)
            context.register_integration("example_service", integration("example_service"))
            context.subscribe_event("example.event", lambda event: received.append(event.name))

    plugin = FullPlugin(contributed_action=action())
    manager = manager_for(
        FakeEntryPoint("full", "p:Full", plugin),
        action_registry=registry,
        event_bus=events,
        integration_registry=integrations,
    )

    info = run(manager.load("full"))
    run(events.publish("example.event"))

    assert info.status is PluginStatus.LOADED
    assert info.loaded is True
    assert registry.names == ("plugin_action",)
    assert integrations.names == ("example_service",)
    assert received == ["example.event"]
    assert info.details == {
        "actions": ("plugin_action",),
        "integrations": ("example_service",),
        "event_listeners": ("example.event",),
    }
    assert plugin.calls == ["initialize", "register"]


def test_registration_failure_discards_staging_and_calls_shutdown() -> None:
    registry = ActionRegistry()
    plugin = RecordingPlugin(contributed_action=action(), fail_register=True)
    manager = manager_for(
        FakeEntryPoint("failed", "p:Failed", plugin),
        action_registry=registry,
    )

    info = run(manager.load("failed"))

    assert info.status is PluginStatus.FAILED
    assert info.loaded is False
    assert registry.names == ()
    assert plugin.calls == ["initialize", "register", "shutdown"]
    assert "secret registration detail" not in (info.error or "")


def test_commit_failure_rolls_back_earlier_integrations() -> None:
    integrations = IntegrationRegistry(fail_on="second")

    class IntegrationPlugin(RecordingPlugin):
        def register(self, context: PluginContext) -> None:
            context.register_integration("first", integration("first"))
            context.register_integration("second", integration("second"))

    plugin = IntegrationPlugin()
    manager = manager_for(
        FakeEntryPoint("integrations", "p:Integrations", plugin),
        integration_registry=integrations,
    )

    info = run(manager.load("integrations"))

    assert info.status is PluginStatus.FAILED
    assert integrations.items == {}
    assert integrations.removed == ["first"]
    assert plugin.calls == ["initialize", "shutdown"]


def test_cancelled_commit_finishes_then_revokes_every_contribution_and_state() -> None:
    async def scenario() -> None:
        registry = ActionRegistry()
        events = EventBus()
        state = InMemoryPluginStateRepository()
        commit_entered = asyncio.Event()
        allow_commit = asyncio.Event()
        observed: list[str] = []

        class BlockingRegistry(IntegrationRegistry):
            async def register(self, name: str, contributed: object) -> None:
                super().register(name, contributed)
                if name == "second":
                    commit_entered.set()
                    await allow_commit.wait()

        integrations = BlockingRegistry()

        class FullPlugin(RecordingPlugin):
            def register(self, context: PluginContext) -> None:
                self.calls.append("register")
                context.register_integration("first", integration("first"))
                context.register_integration("second", integration("second"))
                context.subscribe_event(
                    "plugin.cancelled",
                    lambda event: observed.append(event.name),
                )
                context.register_action(action("cancelled_action"))

        plugin = FullPlugin()
        manager = manager_for(
            FakeEntryPoint("cancelled", "p:Cancelled", plugin),
            action_registry=registry,
            event_bus=events,
            integration_registry=integrations,
            state=state,
        )

        load_task = asyncio.create_task(manager.enable("cancelled"))
        await asyncio.wait_for(commit_entered.wait(), timeout=1)
        load_task.cancel()
        allow_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(load_task, timeout=1)

        await events.publish("plugin.cancelled")
        assert registry.names == ()
        assert integrations.items == {}
        assert observed == []
        assert manager.loaded_plugins == ()
        assert state.enabled_plugins() == ()
        assert plugin.calls == ["initialize", "register", "shutdown"]
        assert plugin.context is not None
        with pytest.raises(PluginRegistrationError, match="no longer active"):
            await plugin.context.publish_event("plugin.cancelled")

    asyncio.run(scenario())


def test_commit_cancellation_residue_is_failed_blocks_refresh_and_shutdown_retries() -> None:
    async def scenario() -> None:
        class CancellingRegistry(IntegrationRegistry):
            def __init__(self) -> None:
                super().__init__()
                self.cleanup_attempts = 0

            async def register(self, name: str, contributed: object) -> None:
                self.items[name] = contributed
                raise asyncio.CancelledError

            def unregister(self, name: str) -> None:
                self.cleanup_attempts += 1
                # _commit rolls back first and _abort_load retries.  Keep the
                # residue until the manager-wide shutdown performs a third pass.
                if self.cleanup_attempts < 3:
                    raise RuntimeError("temporary cleanup failure")
                super().unregister(name)

        integrations = CancellingRegistry()

        class IntegrationPlugin(RecordingPlugin):
            def register(self, context: PluginContext) -> None:
                self.calls.append("register")
                context.register_integration("residual", integration("residual"))

        plugin = IntegrationPlugin()
        manager = manager_for(
            FakeEntryPoint("residual", "p:Residual", plugin),
            integration_registry=integrations,
        )

        with pytest.raises(asyncio.CancelledError):
            await manager.load("residual")

        info = manager.list()[0]
        assert info.status is PluginStatus.FAILED
        assert info.loaded is False
        assert integrations.names == ("residual",)
        assert integrations.cleanup_attempts == 2
        with pytest.raises(RuntimeError, match="cleanup is pending"):
            manager.discover(refresh=True)

        outcomes = await manager.shutdown()
        assert outcomes[0].status is PluginStatus.DISCOVERED
        assert integrations.items == {}
        assert integrations.cleanup_attempts == 3
        assert plugin.calls == ["initialize", "register", "shutdown"]
        assert manager.discover(refresh=True)[0].status is PluginStatus.DISCOVERED

    asyncio.run(scenario())


def test_cancelled_disable_finishes_unload_and_persists_disabled_state() -> None:
    async def scenario() -> None:
        registry = ActionRegistry()
        state = InMemoryPluginStateRepository()
        shutdown_entered = asyncio.Event()
        allow_shutdown = asyncio.Event()

        class BlockingShutdownPlugin(RecordingPlugin):
            async def shutdown(self) -> None:
                self.calls.append("shutdown")
                shutdown_entered.set()
                await allow_shutdown.wait()

        plugin = BlockingShutdownPlugin(contributed_action=action("disable_action"))
        manager = manager_for(
            FakeEntryPoint("disable_cancel", "p:DisableCancel", plugin),
            action_registry=registry,
            state=state,
        )
        assert (await manager.enable("disable_cancel")).loaded is True

        disable_task = asyncio.create_task(manager.disable("disable_cancel"))
        await asyncio.wait_for(shutdown_entered.wait(), timeout=1)
        disable_task.cancel()
        allow_shutdown.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(disable_task, timeout=1)

        assert registry.names == ()
        assert manager.loaded_plugins == ()
        assert state.is_enabled("disable_cancel") is False
        assert plugin.context is not None
        with pytest.raises(PluginRegistrationError, match="no longer active"):
            await plugin.context.publish_event("after.disable")
        assert plugin.calls == ["initialize", "register", "shutdown"]

    asyncio.run(scenario())


def test_cancelled_manager_shutdown_still_unloads_every_plugin() -> None:
    async def scenario() -> None:
        registry = ActionRegistry()
        shutdown_entered = asyncio.Event()
        allow_shutdown = asyncio.Event()

        class FirstPlugin(RecordingPlugin):
            async def shutdown(self) -> None:
                self.calls.append("shutdown")

        class BlockingSecondPlugin(RecordingPlugin):
            async def shutdown(self) -> None:
                self.calls.append("shutdown")
                shutdown_entered.set()
                await allow_shutdown.wait()

        first = FirstPlugin(
            declared_metadata=metadata(name="First Shutdown Plugin"),
            contributed_action=action("first_shutdown_action"),
        )
        second = BlockingSecondPlugin(
            declared_metadata=metadata(name="Second Shutdown Plugin"),
            contributed_action=action("second_shutdown_action"),
        )
        manager = manager_for(
            FakeEntryPoint("first_shutdown", "p:First", first),
            FakeEntryPoint("second_shutdown", "p:Second", second),
            action_registry=registry,
        )
        assert (await manager.load("first_shutdown")).loaded is True
        assert (await manager.load("second_shutdown")).loaded is True

        shutdown_task = asyncio.create_task(manager.shutdown())
        await asyncio.wait_for(shutdown_entered.wait(), timeout=1)
        shutdown_task.cancel()
        allow_shutdown.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(shutdown_task, timeout=1)

        assert registry.names == ()
        assert manager.loaded_plugins == ()
        assert first.calls == ["initialize", "register", "shutdown"]
        assert second.calls == ["initialize", "register", "shutdown"]

    asyncio.run(scenario())


def test_plugin_manager_adapts_to_core_integration_registry() -> None:
    integrations = CoreIntegrationRegistry()
    contributed = integration("plugin_service")

    class IntegrationPlugin(RecordingPlugin):
        def register(self, context: PluginContext) -> None:
            context.register_integration("plugin_service", contributed)

    manager = manager_for(
        FakeEntryPoint("core_registry", "p:CoreRegistry", IntegrationPlugin()),
        integration_registry=integrations,
    )

    assert run(manager.load("core_registry")).loaded is True
    assert integrations.names == ("plugin_service",)
    assert run(manager.disable("core_registry")).status is PluginStatus.DISABLED
    assert integrations.names == ()
    assert contributed.status is IntegrationStatus.CLOSED


def test_context_rejects_mismatched_integration_metadata_name() -> None:
    context = PluginContext("example")

    with pytest.raises(ValueError, match="must match"):
        context.register_integration("declared", integration("different"))


def test_preflight_conflict_prevents_every_registry_mutation() -> None:
    registry = ActionRegistry((action("existing"),))
    integrations = IntegrationRegistry()

    class ConflictingPlugin(RecordingPlugin):
        def register(self, context: PluginContext) -> None:
            context.register_integration("service", integration("service"))
            context.register_action(action("existing"))

    manager = manager_for(
        FakeEntryPoint("conflict", "p:Conflict", ConflictingPlugin()),
        action_registry=registry,
        integration_registry=integrations,
    )

    info = run(manager.load("conflict"))

    assert info.status is PluginStatus.FAILED
    assert registry.names == ("existing",)
    assert integrations.items == {}


def test_disable_unregisters_everything_and_is_idempotent() -> None:
    registry = ActionRegistry()
    events = EventBus()
    integrations = IntegrationRegistry()
    state = InMemoryPluginStateRepository()
    received: list[str] = []

    class FullPlugin(RecordingPlugin):
        def register(self, context: PluginContext) -> None:
            context.register_action(action())
            context.register_integration("service", integration("service"))
            context.subscribe_event("plugin.test", lambda event: received.append(event.name))

    plugin = FullPlugin()
    manager = manager_for(
        FakeEntryPoint("example", "p:Plugin", plugin),
        action_registry=registry,
        event_bus=events,
        integration_registry=integrations,
        state=state,
    )
    assert run(manager.enable("example")).status is PluginStatus.ENABLED

    info = run(manager.disable("example"))
    run(events.publish("plugin.test"))

    assert info.status is PluginStatus.DISABLED
    assert info.enabled is False
    assert info.loaded is False
    assert registry.names == ()
    assert integrations.items == {}
    assert received == []
    assert plugin.calls == ["initialize", "shutdown"]
    assert run(manager.disable("example")).status is PluginStatus.DISABLED
    assert plugin.calls == ["initialize", "shutdown"]


def test_shutdown_failure_isolated_after_registrations_are_removed() -> None:
    registry = ActionRegistry()
    plugin = RecordingPlugin(contributed_action=action(), fail_shutdown=True)
    manager = manager_for(
        FakeEntryPoint("bad_shutdown", "p:BadShutdown", plugin),
        action_registry=registry,
    )
    assert run(manager.load("bad_shutdown")).loaded is True

    info = run(manager.disable("bad_shutdown"))

    assert info.status is PluginStatus.FAILED
    assert info.loaded is False
    assert registry.names == ()
    assert "secret shutdown detail" not in (info.error or "")


def test_failed_integration_cleanup_remains_tracked_and_can_be_retried() -> None:
    class FlakyCloseIntegration(StubIntegration):
        def __init__(self) -> None:
            super().__init__("flaky_service")
            self.close_attempts = 0

        async def close(self) -> None:
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise RuntimeError("temporary close failure")
            await super().close()

    integrations = CoreIntegrationRegistry()
    contributed = FlakyCloseIntegration()

    class IntegrationPlugin(RecordingPlugin):
        def register(self, context: PluginContext) -> None:
            context.register_integration("flaky_service", contributed)

    manager = manager_for(
        FakeEntryPoint("flaky", "p:Flaky", IntegrationPlugin()),
        integration_registry=integrations,
    )
    assert run(manager.load("flaky")).loaded is True

    first = run(manager.disable("flaky"))
    second = run(manager.disable("flaky"))

    assert first.status is PluginStatus.FAILED
    assert first.details is not None
    assert first.details["integrations"] == ("flaky_service",)
    assert second.status is PluginStatus.DISABLED
    assert integrations.names == ()
    assert contributed.close_attempts == 2


def test_context_is_sealed_after_registration_and_can_publish_attributed_events() -> None:
    events = EventBus()
    observed: list[tuple[str, str | None]] = []
    events.subscribe("custom.event", lambda event: observed.append((event.name, event.source)))
    plugin = RecordingPlugin()
    manager = manager_for(
        FakeEntryPoint("publisher", "p:Publisher", plugin),
        event_bus=events,
    )
    assert run(manager.load("publisher")).loaded is True
    assert plugin.context is not None

    run(plugin.context.publish_event("custom.event", {"safe": True}))

    assert observed == [("custom.event", "plugin:publisher")]
    with pytest.raises(PluginRegistrationError, match="sealed"):
        plugin.context.register_action(action("too_late"))
    run(manager.disable("publisher"))
    with pytest.raises(PluginRegistrationError, match="no longer active"):
        run(plugin.context.publish_event("custom.event"))


def test_plugin_action_and_event_process_exit_faults_do_not_escape_core() -> None:
    def exit_process() -> None:
        raise SystemExit("plugin attempted to exit")

    registry = ActionRegistry()
    events = EventBus()

    class FaultyPlugin(RecordingPlugin):
        def register(self, context: PluginContext) -> None:
            context.register_action(Action("faulty_action", "Faulty action.", exit_process))
            context.subscribe_event("faulty.event", lambda event: exit_process())

    manager = manager_for(
        FakeEntryPoint("faulty", "p:Faulty", FaultyPlugin()),
        action_registry=registry,
        event_bus=events,
    )
    assert run(manager.load("faulty")).loaded is True

    result = run(registry.invoke(ActionRequest("faulty_action")))
    event_errors = run(events.publish("faulty.event"))

    assert result.success is False
    assert result.error_code == "execution_failed"
    assert event_errors == ()


def test_sqlite_enablement_persists_across_repository_instances(tmp_path: Path) -> None:
    database = tmp_path / "private" / "plugins.sqlite3"
    first = SQLitePluginStateRepository(database)

    first.set_enabled("example", True)
    first.set_enabled("disabled", False)
    second = SQLitePluginStateRepository(database)

    assert second.is_enabled("example") is True
    assert second.is_enabled("missing") is False
    assert second.enabled_plugins() == ("example",)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_sqlite_repository_rejects_future_schema(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(PluginStateError, match="newer"):
        SQLitePluginStateRepository(database)


def test_sqlite_repository_rejects_symbolic_link_paths(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available to this test account")

    with pytest.raises(PluginStateError, match="symbolic"):
        SQLitePluginStateRepository(link / "plugins.sqlite3")


def test_load_enabled_restores_only_persisted_plugins_and_shutdown_keeps_state() -> None:
    state = InMemoryPluginStateRepository({"enabled": True, "disabled": False})
    enabled = RecordingPlugin(declared_metadata=metadata(name="Enabled Plugin"))
    disabled = RecordingPlugin(declared_metadata=metadata(name="Disabled Plugin"))
    manager = manager_for(
        FakeEntryPoint("enabled", "p:Enabled", enabled),
        FakeEntryPoint("disabled", "p:Disabled", disabled),
        state=state,
    )

    outcomes = run(manager.load_enabled())

    assert [outcome.plugin_id for outcome in outcomes] == ["enabled"]
    assert outcomes[0].status is PluginStatus.ENABLED
    assert manager.loaded_plugins == ("enabled",)
    run(manager.shutdown())
    assert state.is_enabled("enabled") is True
    assert manager.loaded_plugins == ()
    assert disabled.calls == []


def test_unknown_plugins_raise_a_bounded_lookup_error() -> None:
    manager = manager_for()

    with pytest.raises(PluginNotFoundError, match="No installed plugin"):
        manager.inspect("missing")
