"""Framework-neutral asynchronous GUI orchestration for a supplied JARVIS app."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, TypeAlias
from urllib.parse import urlsplit

from jarvis.core.actions import ActionRequest, ActionResult
from jarvis.gui.data import ApplicationDataProvider, GuiDataProvider
from jarvis.gui.models import (
    MAX_CHAT_CHARACTERS,
    MAX_ROWS,
    AboutView,
    ActionActivity,
    ActivityState,
    AssistantState,
    ChatMessageView,
    ChatRole,
    GuiUpdate,
    GuiUpdateKind,
    IntegrationView,
    LogView,
    MemoryView,
    Page,
    PageData,
    PermissionPrompt,
    PluginView,
    ReminderView,
    SettingView,
    StatusView,
    clean_text,
    utc_now,
)
from jarvis.gui.permissions import GuiPermissionBroker

logger = logging.getLogger(__name__)

UpdateListener: TypeAlias = Callable[[GuiUpdate], None | Awaitable[None]]


class GuiControllerError(RuntimeError):
    """Base class for recoverable controller errors."""


class GuiBusyError(GuiControllerError):
    """Raised when a second chat request is submitted concurrently."""


class GuiClosedError(GuiControllerError):
    """Raised when work is submitted after the controller has closed."""


class GuiController:
    """Coordinate chat, permissions, activity, and pages without a GUI toolkit.

    ``application`` must already own the configured runtime and services. The
    controller never calls a composition root and therefore cannot accidentally
    create a second memory database, scheduler, plugin manager, or permission
    policy stack.
    """

    def __init__(
        self,
        application: object,
        *,
        permission_broker: GuiPermissionBroker,
        data_provider: GuiDataProvider | None = None,
        max_messages: int = 500,
        max_activities: int = MAX_ROWS,
    ) -> None:
        """Create a controller around an existing app and its exact permission broker."""

        runtime = getattr(application, "runtime", None)
        if runtime is None or not callable(getattr(runtime, "process", None)):
            raise TypeError("application must expose a runtime with process(text)")
        if not isinstance(permission_broker, GuiPermissionBroker):
            raise TypeError("permission_broker must be an explicit GuiPermissionBroker")
        permissions = getattr(runtime, "permissions", None)
        if permissions is None or not _confirmer_uses_broker(
            getattr(permissions, "confirmer", None), permission_broker
        ):
            raise ValueError(
                "permission_broker must be the same broker bound to the runtime confirmer"
            )
        if (
            isinstance(max_messages, bool)
            or not isinstance(max_messages, int)
            or not 1 <= max_messages <= 2_000
        ):
            raise ValueError("max_messages must be between 1 and 2000")
        if (
            isinstance(max_activities, bool)
            or not isinstance(max_activities, int)
            or not 1 <= max_activities <= 1_000
        ):
            raise ValueError("max_activities must be between 1 and 1000")
        self.application = application
        self.runtime = runtime
        self.permission_broker = permission_broker
        self.data_provider = data_provider or ApplicationDataProvider(application)
        self.max_messages = max_messages
        self.max_activities = max_activities
        self._messages: list[ChatMessageView] = []
        self._activities: list[ActionActivity] = []
        self._page_cache: dict[Page, PageData] = {}
        self._listeners: list[UpdateListener] = []
        self._observer_tasks: set[asyncio.Task[Any]] = set()
        self._active_task: asyncio.Task[Any] | None = None
        self._closed = False
        self._event_unsubscribe: Callable[[], bool] | None = None
        self._permission_unsubscribe = self.permission_broker.subscribe(self._on_permission_change)
        self._status = _application_status(application)
        events = getattr(runtime, "events", None)
        subscribe = getattr(events, "subscribe", None)
        if callable(subscribe):
            try:
                self._event_unsubscribe = subscribe("*", self._on_runtime_event)
            except Exception:
                logger.exception("gui_event_subscription_failed")

    @property
    def messages(self) -> tuple[ChatMessageView, ...]:
        return tuple(self._messages)

    @property
    def activities(self) -> tuple[ActionActivity, ...]:
        return tuple(self._activities)

    @property
    def status(self) -> StatusView:
        return self._status

    @property
    def pending_permissions(self) -> tuple[PermissionPrompt, ...]:
        return self.permission_broker.pending

    @property
    def busy(self) -> bool:
        return self._active_task is not None and not self._active_task.done()

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(self, listener: UpdateListener) -> Callable[[], bool]:
        """Observe immutable controller updates; listener failures are isolated."""

        if not callable(listener):
            raise TypeError("GUI update listener must be callable")
        if listener not in self._listeners:
            self._listeners.append(listener)

        def unsubscribe() -> bool:
            if listener not in self._listeners:
                return False
            self._listeners.remove(listener)
            return True

        return unsubscribe

    async def send_message(self, text: str) -> Any | None:
        """Submit one bounded request while leaving the UI event loop responsive."""

        if self._closed:
            raise GuiClosedError("the GUI controller is closed")
        if self.busy:
            raise GuiBusyError("another request is already running")
        if not isinstance(text, str):
            raise TypeError("chat input must be text")
        normalized = clean_text(text, limit=MAX_CHAT_CHARACTERS)
        if not normalized.strip():
            raise ValueError("chat input cannot be empty")

        self._append_message(ChatMessageView(ChatRole.USER, normalized))
        task = asyncio.create_task(self.runtime.process(normalized), name="jarvis-gui-request")
        self._active_task = task
        self._set_state(AssistantState.WORKING, "Working…")
        try:
            response = await task
        except asyncio.CancelledError:
            outcome_unknown = self._resolve_cancelled_activities()
            cancellation_message = (
                "Cancellation was requested, but an action had already started and may "
                "still complete. Verify its result before retrying."
                if outcome_unknown
                else "Request cancelled."
            )
            self._append_message(ChatMessageView(ChatRole.SYSTEM, cancellation_message))
            if self._closed:
                self._set_state(AssistantState.STOPPED, "Closed")
            else:
                self._set_state(AssistantState.IDLE, "Ready")
            return None
        except Exception:
            logger.exception("gui_runtime_request_failed")
            self._append_message(
                ChatMessageView(
                    ChatRole.SYSTEM,
                    "JARVIS could not complete that request. Check Logs for details.",
                )
            )
            self._set_state(AssistantState.ERROR, "Request failed")
            return None
        finally:
            if self._active_task is task:
                self._active_task = None

        message = clean_text(getattr(response, "message", ""), limit=MAX_CHAT_CHARACTERS)
        if not message.strip():
            message = "Done."
        self._append_message(ChatMessageView(ChatRole.ASSISTANT, message))
        if bool(getattr(response, "should_exit", False)):
            self._set_state(AssistantState.STOPPED, "Session ended")
        elif self.pending_permissions:
            self._set_state(AssistantState.AWAITING_PERMISSION, "Awaiting permission")
        else:
            self._set_state(AssistantState.IDLE, "Ready")
        return response

    async def capture_voice(self) -> Any | None:
        """Capture one utterance through the application's configured STT adapter."""

        speech_to_text = getattr(self.application, "speech_to_text", None)
        listen = getattr(speech_to_text, "listen", None)
        if not callable(listen):
            raise GuiControllerError("Voice input is not configured")
        if self.busy:
            raise GuiBusyError("another request is already running")
        self._set_state(AssistantState.WORKING, "Listening…")
        try:
            text = await listen()
        except Exception:
            logger.exception("gui_voice_input_failed")
            self._set_state(AssistantState.ERROR, "Voice input failed")
            return None
        return await self.send_message(text)

    def cancel_current(self) -> bool:
        """Request cancellation of the active runtime turn, if any."""

        task = self._active_task
        if task is None or task.done():
            return False
        self._set_state(AssistantState.CANCELLING, "Cancelling…")
        task.cancel()
        return True

    async def cancel_and_wait(self) -> bool:
        """Cancel the active turn and wait for controller cleanup."""

        task = self._active_task
        if not self.cancel_current() or task is None:
            return False
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        return True

    def resolve_permission(self, prompt_id: str, approved: bool) -> bool:
        """Forward an explicit allow/deny decision to the shared permission stack."""

        return self.permission_broker.resolve(prompt_id, approved)

    async def load_page(self, page: Page | str) -> PageData:
        """Load one bounded dashboard page and isolate provider failures."""

        if self._closed:
            raise GuiClosedError("the GUI controller is closed")
        selected = Page(page)
        try:
            if isinstance(self.data_provider, ApplicationDataProvider) and selected in {
                Page.MEMORY,
                Page.TASKS,
            }:
                data = await self._load_sensitive_page(selected)
            else:
                result = self.data_provider.load(selected)
                data = await result if inspect.isawaitable(result) else result
                data = _bound_page_data(selected, data)
        except Exception as error:
            logger.exception("gui_page_load_failed", extra={"page": selected.value})
            data = ()
            self._notify(
                GuiUpdate(
                    GuiUpdateKind.DATA,
                    {"page": selected.value, "error": type(error).__name__},
                )
            )
        else:
            self._notify(GuiUpdate(GuiUpdateKind.DATA, {"page": selected.value}))
        self._page_cache[selected] = data
        return data

    async def _load_sensitive_page(self, page: Page) -> PageData:
        action = "list_memories" if page is Page.MEMORY else "list_reminders"
        result = await self.execute_action(action)
        if not result.success:
            return ()
        return _memory_page(result.data) if page is Page.MEMORY else _task_page(result.data)

    async def search_memories(self, query: str) -> tuple[MemoryView, ...]:
        result = await self.execute_action("search_memories", {"query": query})
        data = () if not result.success else _memory_page(result.data)
        self._page_cache[Page.MEMORY] = data
        return data

    async def execute_action(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ActionResult:
        """Execute one GUI-initiated action through shared validation and permissions."""

        if self._closed:
            raise GuiClosedError("the GUI controller is closed")
        if self.busy:
            raise GuiBusyError("another request is already running")
        task = asyncio.create_task(
            self.runtime.execute_requests((ActionRequest(name, arguments),)),
            name=f"jarvis-gui-action-{name}",
        )
        self._active_task = task
        self._set_state(AssistantState.WORKING, "Working…")
        try:
            results = await task
            result = results[0]
        except asyncio.CancelledError:
            outcome_unknown = self._resolve_cancelled_activities()
            result = ActionResult.failed(
                name,
                (
                    "Cancellation was requested after the action started; its outcome is unknown."
                    if outcome_unknown
                    else "The GUI action was cancelled before execution."
                ),
                message=(
                    "Cancellation requested; this action may still complete. Verify its "
                    "result before retrying."
                    if outcome_unknown
                    else "Action cancelled."
                ),
                error_code="outcome_unknown" if outcome_unknown else "cancelled",
            )
        finally:
            if self._active_task is task:
                self._active_task = None
        self._set_state(
            AssistantState.IDLE if result.success else AssistantState.ERROR,
            "Ready" if result.success else "Action failed",
        )
        return result

    async def delete_memory(self, category: str, key: str) -> ActionResult:
        return await self.execute_action("forget_memory", {"category": category, "key": key})

    async def create_reminder(
        self, message: str, scheduled_at: str, *, timezone: str = "UTC"
    ) -> ActionResult:
        return await self.execute_action(
            "create_reminder",
            {"message": message, "scheduled_at": scheduled_at, "timezone": timezone},
        )

    async def edit_reminder(
        self,
        reminder_id: int,
        message: str,
        scheduled_at: str,
        *,
        expected_message: str,
        expected_due_at: str,
    ) -> ActionResult:
        """Edit the exact scheduled reminder represented by the current UI row."""

        return await self.execute_action(
            "edit_reminder",
            {
                "reminder_id": reminder_id,
                "message": message,
                "scheduled_at": scheduled_at,
                "expected_message": expected_message,
                "expected_due_at": expected_due_at,
            },
        )

    async def cancel_reminder(self, reminder_id: int, expected_message: str) -> ActionResult:
        return await self.execute_action(
            "cancel_reminder",
            {"reminder_id": reminder_id, "expected_message": expected_message},
        )

    async def delete_reminder(self, reminder_id: int, expected_message: str) -> ActionResult:
        return await self.execute_action(
            "delete_reminder",
            {"reminder_id": reminder_id, "expected_message": expected_message},
        )

    async def enable_plugin(self, plugin_id: str) -> ActionResult:
        return await self.execute_action("plugin_enable", {"plugin_id": plugin_id})

    async def disable_plugin(self, plugin_id: str) -> ActionResult:
        return await self.execute_action("plugin_disable", {"plugin_id": plugin_id})

    def cached_page(self, page: Page | str) -> PageData | None:
        return self._page_cache.get(Page(page))

    def close(self) -> None:
        """Detach observers, reject permissions, and cancel in-flight work."""

        if self._closed:
            return
        self._closed = True
        self.permission_broker.close()
        task = self._active_task
        if task is not None and not task.done():
            task.cancel()
        if self._event_unsubscribe is not None:
            self._event_unsubscribe()
            self._event_unsubscribe = None
        self._permission_unsubscribe()
        self._set_state(AssistantState.STOPPED, "Closed")
        for task in tuple(self._observer_tasks):
            task.cancel()
        self._listeners.clear()

    async def aclose(self, *, timeout_seconds: float = 2.0) -> None:
        """Async close variant that observes active request cancellation."""

        if timeout_seconds <= 0:
            raise ValueError("close timeout must be positive")
        task = self._active_task
        self.close()
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout_seconds)
            except (TimeoutError, asyncio.CancelledError):
                pass

    async def _on_runtime_event(self, event: Any) -> None:
        name = str(getattr(event, "name", ""))
        payload = getattr(event, "payload", {})
        if not isinstance(payload, Mapping):
            payload = {}
        timestamp = getattr(event, "occurred_at", utc_now())
        if not isinstance(timestamp, datetime):
            timestamp = utc_now()
        if name == "action.requested":
            self._add_activity(payload, ActivityState.REQUESTED, timestamp)
        elif name == "action.started":
            self._update_activity(payload, ActivityState.RUNNING)
        elif name == "action.completed":
            self._update_activity(payload, ActivityState.COMPLETED, finished_at=timestamp)
        elif name == "action.failed":
            self._update_activity(
                payload,
                ActivityState.FAILED,
                finished_at=timestamp,
                error_code=_optional_text(payload.get("error_code")),
            )

    def _add_activity(
        self,
        payload: Mapping[str, Any],
        state: ActivityState,
        timestamp: datetime,
    ) -> None:
        action_name = _text(payload.get("action"), "action")
        request_id = _text(payload.get("request_id"), uuid.uuid4().hex)
        activity = ActionActivity(
            action_name=action_name,
            request_id=request_id,
            state=state,
            summary=action_name.replace("_", " ").title(),
            started_at=timestamp,
        )
        self._activities.append(activity)
        del self._activities[: max(0, len(self._activities) - self.max_activities)]
        self._notify(GuiUpdate(GuiUpdateKind.ACTIVITY, _activity_payload(activity)))

    def _update_activity(
        self,
        payload: Mapping[str, Any],
        state: ActivityState,
        *,
        finished_at: datetime | None = None,
        error_code: str | None = None,
    ) -> None:
        request_id = _optional_text(payload.get("request_id"))
        action_name = _text(payload.get("action"), "action")
        index = self._find_activity(request_id, action_name)
        if index is None:
            self._add_activity(payload, state, finished_at or utc_now())
            index = len(self._activities) - 1
        current = self._activities[index]
        updated = replace(
            current,
            state=state,
            finished_at=finished_at,
            error_code=error_code,
        )
        self._activities[index] = updated
        self._notify(GuiUpdate(GuiUpdateKind.ACTIVITY, _activity_payload(updated)))

    def _find_activity(self, request_id: str | None, action_name: str) -> int | None:
        for index in range(len(self._activities) - 1, -1, -1):
            item = self._activities[index]
            if request_id and item.request_id == request_id:
                return index
            if not request_id and item.action_name == action_name and item.finished_at is None:
                return index
        return None

    def _resolve_cancelled_activities(self) -> bool:
        """Finalize cancelled cards and report whether a side effect may have committed."""

        now = utc_now()
        outcome_unknown = False
        for index, activity in enumerate(self._activities):
            if activity.state is ActivityState.RUNNING:
                outcome_unknown = True
                self._activities[index] = replace(
                    activity,
                    state=ActivityState.OUTCOME_UNKNOWN,
                    error_code="outcome_unknown",
                    finished_at=now,
                )
            elif activity.state is ActivityState.REQUESTED:
                self._activities[index] = replace(
                    activity,
                    state=ActivityState.CANCELLED,
                    finished_at=now,
                )
        self._notify(GuiUpdate(GuiUpdateKind.ACTIVITY))
        return outcome_unknown

    def _on_permission_change(self, prompts: tuple[PermissionPrompt, ...]) -> None:
        if prompts:
            self._set_state(AssistantState.AWAITING_PERMISSION, "Awaiting permission")
        elif self.busy:
            self._set_state(AssistantState.WORKING, "Working…")
        elif not self._closed and self._status.state is AssistantState.AWAITING_PERMISSION:
            self._set_state(AssistantState.IDLE, "Ready")
        self._notify(
            GuiUpdate(
                GuiUpdateKind.PERMISSION,
                [
                    {
                        "id": prompt.id,
                        "action": prompt.action_name,
                        "risk": prompt.risk_level,
                    }
                    for prompt in prompts
                ],
            )
        )

    def _append_message(self, message: ChatMessageView) -> None:
        self._messages.append(message)
        del self._messages[: max(0, len(self._messages) - self.max_messages)]
        self._notify(
            GuiUpdate(
                GuiUpdateKind.CHAT,
                {"id": message.id, "role": message.role.value},
            )
        )

    def _set_state(self, state: AssistantState, message: str) -> None:
        self._status = replace(self._status, state=state, message=message)
        self._notify(
            GuiUpdate(
                GuiUpdateKind.STATUS,
                {"state": state.value, "message": message},
            )
        )

    def _notify(self, update: GuiUpdate) -> None:
        for listener in tuple(self._listeners):
            try:
                result = listener(update)
                if inspect.isawaitable(result):
                    try:
                        task = asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        close = getattr(result, "close", None)
                        if callable(close):
                            close()
                        continue
                    self._observer_tasks.add(task)
                    task.add_done_callback(self._observer_tasks.discard)
            except Exception:
                logger.exception("gui_update_listener_failed")


def _application_status(application: object) -> StatusView:
    config = getattr(application, "config", None)
    ai = getattr(config, "ai", None)
    enabled = bool(getattr(ai, "enabled", False))
    provider = _text(getattr(ai, "provider", None), "disabled") if enabled else "disabled"
    base_url = _optional_text(getattr(ai, "base_url", None))
    execution_label = "local"
    external_services = False
    if enabled:
        hostname = urlsplit(base_url).hostname if base_url else None
        external_services = not _is_local_host(hostname)
        execution_label = "cloud" if external_services else "local"

    vision = getattr(config, "vision", None)
    if bool(getattr(vision, "enabled", False)):
        vision_url = _optional_text(getattr(vision, "base_url", None))
        vision_host = urlsplit(vision_url).hostname if vision_url else None
        external_services = external_services or not _is_local_host(vision_host)
    voice = getattr(config, "voice", None)
    if bool(getattr(voice, "enabled", False)) and str(
        getattr(voice, "stt_provider", "none")
    ).casefold() in {"google", "speech-recognition"}:
        external_services = True
    integrations = getattr(config, "integrations", None)
    external_services = external_services or bool(getattr(integrations, "github_enabled", False))
    if not enabled and external_services:
        execution_label = "external data services active"

    components: list[str] = []
    for label, value in (
        ("AI", enabled),
        ("Memory", bool(getattr(getattr(config, "memory", None), "enabled", False))),
        ("Voice", bool(getattr(getattr(config, "voice", None), "enabled", False))),
        ("Vision", bool(getattr(getattr(config, "vision", None), "enabled", False))),
        ("Tasks", getattr(application, "reminder_service", None) is not None),
        ("Browser", getattr(application, "browser", None) is not None),
        ("Integrations", getattr(application, "integration_registry", None) is not None),
        ("Plugins", getattr(application, "plugin_manager", None) is not None),
    ):
        if value:
            components.append(label)
    return StatusView(
        state=AssistantState.IDLE,
        message="Ready",
        ai_provider=provider,
        execution_label=execution_label,
        enabled_components=tuple(components),
    )


def _confirmer_uses_broker(confirmer: object, broker: GuiPermissionBroker) -> bool:
    return (
        getattr(confirmer, "__self__", None) is broker
        and getattr(confirmer, "__func__", None) is GuiPermissionBroker.confirm
    )


def _activity_payload(activity: ActionActivity) -> dict[str, str | None]:
    return {
        "request_id": activity.request_id,
        "action": activity.action_name,
        "state": activity.state.value,
        "error_code": activity.error_code,
    }


def _text(value: object, default: str) -> str:
    raw = getattr(value, "value", value)
    text = clean_text(raw, limit=128, collapse_whitespace=True)
    return text or default


def _optional_text(value: object) -> str | None:
    text = _text(value, "")
    return text or None


def _bound_page_data(page: Page, data: Any) -> PageData:
    if page is Page.ABOUT:
        return data if isinstance(data, AboutView) else ()
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        expected = {
            Page.MEMORY: MemoryView,
            Page.TASKS: ReminderView,
            Page.INTEGRATIONS: IntegrationView,
            Page.PLUGINS: PluginView,
            Page.SETTINGS: SettingView,
            Page.LOGS: LogView,
        }.get(page)
        if expected is None:
            return ()
        return tuple(item for item in data[:MAX_ROWS] if isinstance(item, expected))  # type: ignore[return-value]
    return ()


def _memory_page(data: Any) -> tuple[MemoryView, ...]:
    if not isinstance(data, list):
        return ()
    result: list[MemoryView] = []
    for item in data[:MAX_ROWS]:
        if not isinstance(item, Mapping):
            continue
        result.append(
            MemoryView(
                id=_text(item.get("id"), ""),
                category=_text(item.get("category"), ""),
                key=_text(item.get("key"), ""),
                value=clean_text(item.get("value"), limit=4_000),
                updated_at=_text(
                    item.get("updated_at") or item.get("created_at"),
                    "",
                ),
            )
        )
    return tuple(result)


def _task_page(data: Any) -> tuple[ReminderView, ...]:
    if not isinstance(data, Mapping):
        return ()
    items = data.get("reminders")
    if not isinstance(items, list):
        return ()
    result: list[ReminderView] = []
    for item in items[:MAX_ROWS]:
        if not isinstance(item, Mapping):
            continue
        result.append(
            ReminderView(
                id=_text(item.get("id"), ""),
                message=clean_text(item.get("message"), limit=2_000),
                due_at=_text(item.get("due_at"), ""),
                timezone=_text(item.get("timezone"), ""),
                recurrence=_text(item.get("recurrence"), ""),
                status=_text(item.get("status"), ""),
            )
        )
    return tuple(result)


def _is_local_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.casefold().rstrip(".")
    if (
        normalized == "localhost"
        or normalized.endswith(".localhost")
        or normalized.endswith(".local")
    ):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)
