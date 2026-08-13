"""Composition root shared by every JARVIS interface."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from functools import partial

from jarvis.actions import ActionServices, build_action_registry
from jarvis.ai import LLMProvider, OpenAICompatibleProvider, OpenAIResponsesProvider
from jarvis.browser import (
    BrowserActionService,
    BrowserLimits,
    PlaywrightBrowserController,
    register_browser_actions,
)
from jarvis.computer import (
    ApplicationController,
    KeyboardController,
    MouseController,
    ScreenController,
    ScreenshotStore,
    SystemInfoProvider,
    WindowsController,
)
from jarvis.core.config import ConfigError, JarvisConfig
from jarvis.core.events import EventBus, EventName
from jarvis.core.permissions import Confirmer, PermissionManager
from jarvis.core.router import Router
from jarvis.core.runtime import JarvisRuntime
from jarvis.core.safety import DesktopExecutionGuard
from jarvis.integrations import (
    IntegrationRegistry,
    StaticCredentialResolver,
    register_integration_actions,
)
from jarvis.integrations.calendar import InMemoryCalendarProvider
from jarvis.integrations.email import InMemoryEmailProvider
from jarvis.integrations.github import GitHubClient, GitHubIntegration
from jarvis.memory import MemoryManager, SQLiteMemoryRepository
from jarvis.plugins import (
    PluginManager,
    SQLitePluginStateRepository,
    register_plugin_actions,
)
from jarvis.skills.datetime_skill import DateSkill, TimeSkill
from jarvis.skills.general import GreetingSkill, HelpSkill
from jarvis.tasks import (
    DesktopNotifier,
    Reminder,
    ReminderActionService,
    ReminderService,
    Scheduler,
    SQLiteReminderRepository,
    TerminalNotifier,
    register_reminder_actions,
)
from jarvis.vision import (
    OpenAICompatibleVisionProvider,
    OpenAIResponsesVisionProvider,
    VisionAnalyzer,
)
from jarvis.voice import Pyttsx3TTS, SpeechRecognitionSTT, SpeechToText, TextToSpeech

logger = logging.getLogger(__name__)


class _EventingNotifier:
    """Publish non-content reminder lifecycle metadata around a notifier."""

    def __init__(self, notifier: TerminalNotifier | DesktopNotifier, events: EventBus) -> None:
        self._notifier = notifier
        self._events = events

    async def notify(self, reminder: Reminder) -> None:
        await self._events.publish(
            EventName.TASK_TRIGGERED,
            {"reminder_id": reminder.id, "recurrence": reminder.recurrence.value},
            source="tasks",
        )
        await self._notifier.notify(reminder)
        await self._events.publish(
            EventName.TASK_COMPLETED,
            {"reminder_id": reminder.id},
            source="tasks",
        )


@dataclass(slots=True)
class JarvisApplication:
    """Live services and their owned resources for one CLI session."""

    config: JarvisConfig
    runtime: JarvisRuntime
    memory_repository: SQLiteMemoryRepository | None = None
    speech_to_text: SpeechToText | None = None
    text_to_speech: TextToSpeech | None = None
    memory: MemoryManager | None = None
    reminder_repository: SQLiteReminderRepository | None = None
    reminders: ReminderService | None = None
    scheduler: Scheduler | None = None
    browser: BrowserActionService | None = None
    integration_registry: IntegrationRegistry | None = None
    plugin_manager: PluginManager | None = None
    _scheduler_task: asyncio.Task[None] | None = None
    _started: bool = False
    _closed: bool = False
    _start_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def reminder_service(self) -> ReminderService | None:
        """Compatibility/readability alias used by interface adapters."""

        return self.reminders

    async def start(self) -> None:
        """Start optional async services once, isolating provider/plugin failures."""

        async with self._start_lock:
            if self._closed:
                raise RuntimeError("The JARVIS application is closed")
            if self._started:
                return
            if self.integration_registry is not None:
                for name in self.integration_registry.names:
                    try:
                        snapshot = await self.integration_registry.connect(name)
                        await self.runtime.events.publish(
                            EventName.INTEGRATION_CONNECTED,
                            {"integration": name, "status": snapshot.status.value},
                        )
                    except Exception:
                        await self.runtime.events.publish(
                            EventName.INTEGRATION_FAILED,
                            {"integration": name},
                        )
            if self.plugin_manager is not None:
                self.plugin_manager.discover()
                if self.config.plugins.auto_load:
                    await self.plugin_manager.load_enabled()
            if self.scheduler is not None:
                self._scheduler_task = asyncio.create_task(
                    self.scheduler.run(),
                    name="jarvis-reminder-scheduler",
                )
            self._started = True

    async def aclose(self) -> None:
        """Asynchronously release every optional service and persistent store."""

        if self._closed:
            return
        errors: list[Exception] = []
        cancellation_requested = False
        if self.scheduler is not None:
            self.scheduler.stop()
        operations = []
        if self._scheduler_task is not None:
            operations.append(self._scheduler_task)
        if self.browser is not None:
            operations.append(asyncio.create_task(self.browser.shutdown()))
        if self.plugin_manager is not None:
            operations.append(asyncio.create_task(self.plugin_manager.shutdown()))
        if self.integration_registry is not None:
            operations.append(asyncio.create_task(self.integration_registry.close()))
        for operation in operations:
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    cancellation_requested = True
                    continue
                except Exception as error:
                    errors.append(error)
                    break
            if operation.done() and not operation.cancelled():
                try:
                    operation.result()
                except Exception as error:
                    if error not in errors:
                        errors.append(error)
        self._scheduler_task = None
        for repository in (self.reminder_repository, self.memory_repository):
            if repository is not None:
                try:
                    repository.close()
                except Exception as error:
                    errors.append(error)
        self._closed = True
        if errors:
            for _error in errors:
                # Exception strings can contain paths/provider details. Logging
                # records only the stable exception type; the CLI/GUI still exits.
                logger.error(
                    "application_cleanup_failed",
                    extra={"error_type": type(_error).__name__},
                )
        if cancellation_requested:
            raise asyncio.CancelledError

    def close(self) -> None:
        """Release owned persistent resources; safe to call more than once."""

        if self._closed:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError("Use 'await application.aclose()' inside an active event loop")

    async def __aenter__(self) -> JarvisApplication:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def __enter__(self) -> JarvisApplication:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def create_application(
    config: JarvisConfig,
    *,
    confirmer: Confirmer | None = None,
    voice_mode: bool = False,
) -> JarvisApplication:
    """Build the fully permissioned runtime from immutable configuration."""

    events = EventBus()
    windows = WindowsController()
    screen = ScreenController(
        ScreenshotStore(config.screenshots.directory),
        windows=windows,
    )
    repository: SQLiteMemoryRepository | None = None
    reminder_repository: SQLiteReminderRepository | None = None
    try:
        memory: MemoryManager | None = None
        if config.memory.enabled:
            repository = SQLiteMemoryRepository(config.database.path)
            memory = MemoryManager(repository)

        vision_provider = _create_vision_provider(config)
        vision = VisionAnalyzer(screen, vision_provider) if vision_provider is not None else None
        services = ActionServices(
            applications=ApplicationController(),
            system=SystemInfoProvider(),
            mouse=MouseController(),
            keyboard=KeyboardController(),
            screen=screen,
            windows=windows,
            memory=memory,
            vision=vision,
            events=events,
        )
        registry = build_action_registry(services)
        browser_service: BrowserActionService | None = None
        if config.browser.enabled:
            browser_controller = PlaywrightBrowserController(
                limits=BrowserLimits(
                    max_sessions=config.browser.max_sessions,
                    max_tabs_per_session=config.browser.max_tabs,
                ),
                browser_type=config.browser.browser_type,
                headless=config.browser.headless,
            )
            browser_service = BrowserActionService(browser_controller, events=events)
            register_browser_actions(registry, browser_service)

        reminders: ReminderService | None = None
        scheduler: Scheduler | None = None
        if config.scheduler.enabled:
            reminder_repository = SQLiteReminderRepository(config.scheduler.database_path)
            notifier_backend = (
                DesktopNotifier() if config.scheduler.desktop_notifications else TerminalNotifier()
            )
            scheduler = Scheduler(
                reminder_repository,
                _EventingNotifier(notifier_backend, events),
                poll_interval_seconds=config.scheduler.poll_interval_seconds,
            )
            reminders = ReminderService(reminder_repository, on_change=scheduler.wake)
            register_reminder_actions(
                registry,
                ReminderActionService(reminders, config.scheduler.timezone, events),
            )

        integrations = _build_integrations(config)
        register_integration_actions(registry, integrations)
        plugin_manager: PluginManager | None = None
        if config.plugins.enabled:
            plugin_manager = PluginManager(
                action_registry=registry,
                event_bus=events,
                integration_registry=integrations,
                state_repository=SQLitePluginStateRepository(config.plugins.state_path),
            )
            register_plugin_actions(registry, plugin_manager)
        permissions = PermissionManager(config.permissions.as_mapping(), confirmer=confirmer)
        # The fallback contains only audited READ skills. All side effects flow
        # through ActionRegistry -> PermissionManager -> ExecutionGuard.
        fallback = Router([GreetingSkill(), HelpSkill(), TimeSkill(), DateSkill()])
        runtime = JarvisRuntime(
            registry,
            permissions,
            llm=_create_llm_provider(config),
            events=events,
            fallback_router=fallback,
            execution_guard=DesktopExecutionGuard(windows),
        )
        stt = _create_speech_to_text(config, voice_mode=voice_mode)
        tts = _create_text_to_speech(config)
        return JarvisApplication(
            config,
            runtime,
            repository,
            stt,
            tts,
            memory,
            reminder_repository,
            reminders,
            scheduler,
            browser_service,
            integrations,
            plugin_manager,
        )
    except Exception:
        if repository is not None:
            repository.close()
        if reminder_repository is not None:
            reminder_repository.close()
        raise


def _build_integrations(config: JarvisConfig) -> IntegrationRegistry:
    registry = IntegrationRegistry()
    if config.integrations.github_enabled:
        token = config.integrations.github_token
        if token is None:
            raise ConfigError("GitHub integration is enabled but JARVIS_GITHUB_TOKEN is missing")
        credentials = StaticCredentialResolver({"github.token": token})
        registry.register(
            GitHubIntegration(
                credentials,
                client_factory=partial(
                    GitHubClient,
                    base_url=config.integrations.github_base_url,
                ),
            )
        )
    if config.integrations.email_provider in {"memory", "in-memory"}:
        registry.register(InMemoryEmailProvider())
    elif config.integrations.email_provider != "none":
        raise ConfigError(f"Unsupported email provider: {config.integrations.email_provider!r}")
    if config.integrations.calendar_provider in {"memory", "in-memory"}:
        registry.register(InMemoryCalendarProvider())
    elif config.integrations.calendar_provider != "none":
        raise ConfigError(
            f"Unsupported calendar provider: {config.integrations.calendar_provider!r}"
        )
    return registry


def _create_llm_provider(config: JarvisConfig) -> LLMProvider | None:
    settings = config.ai
    if not settings.enabled:
        return None
    provider = settings.provider.casefold()
    base_url = settings.base_url or "https://api.openai.com/v1"
    common = {
        "model": settings.model,
        "base_url": base_url,
        "api_key": settings.api_key,
        "timeout_seconds": settings.timeout_seconds,
    }
    try:
        if provider == "openai":
            return OpenAIResponsesProvider(**common)
        if provider == "openai-compatible":
            return OpenAICompatibleProvider(**common)
    except ValueError as error:
        raise ConfigError(f"Invalid AI provider configuration: {error}") from None
    raise ConfigError(f"Unsupported AI provider: {settings.provider!r}")


def _create_vision_provider(config: JarvisConfig):  # type: ignore[no-untyped-def]
    settings = config.vision
    if not settings.enabled:
        return None
    provider = settings.provider.casefold()
    base_url = settings.base_url or "https://api.openai.com/v1"
    common = {
        "model": settings.model,
        "base_url": base_url,
        "api_key": settings.api_key,
        "timeout_seconds": settings.timeout_seconds,
    }
    try:
        if provider == "openai":
            return OpenAIResponsesVisionProvider(**common)
        if provider == "openai-compatible":
            return OpenAICompatibleVisionProvider(**common)
    except ValueError as error:
        raise ConfigError(f"Invalid vision provider configuration: {error}") from None
    raise ConfigError(f"Unsupported vision provider: {settings.provider!r}")


def _create_speech_to_text(
    config: JarvisConfig,
    *,
    voice_mode: bool,
) -> SpeechToText | None:
    if not (voice_mode or config.voice.enabled):
        return None
    provider = config.voice.stt_provider.casefold()
    if provider == "none":
        raise ConfigError(
            "Voice input requires an explicit stt_provider such as 'google' "
            "or 'speech-recognition'."
        )
    if provider in {"speech-recognition", "speechrecognition", "google"}:
        return SpeechRecognitionSTT(language=config.voice.language)
    raise ConfigError(f"Unsupported speech-to-text provider: {config.voice.stt_provider!r}")


def _create_text_to_speech(config: JarvisConfig) -> TextToSpeech | None:
    if not config.voice.tts_enabled:
        return None
    provider = config.voice.tts_provider.casefold()
    if provider in {"none", "pyttsx3", "system"}:
        return Pyttsx3TTS()
    raise ConfigError(f"Unsupported text-to-speech provider: {config.voice.tts_provider!r}")
