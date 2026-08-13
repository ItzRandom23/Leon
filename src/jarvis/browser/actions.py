"""Permission-ready browser service and explicit JARVIS action registration."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from urllib.parse import urlencode, urlsplit

from jarvis.browser.base import BrowserController, BrowserSession
from jarvis.browser.errors import BrowserElementError, BrowserError, BrowserSessionError
from jarvis.browser.models import BrowserElement, PageSnapshot
from jarvis.core.actions import Action, ActionParameter, ActionRegistry, ActionResult
from jarvis.core.events import EventBus, EventName
from jarvis.skills.base import RiskLevel


class BrowserActionService:
    """Maintain one explicit active browser session and snapshot target state.

    Element actions require the caller to repeat the role and accessible name
    observed in the latest snapshot. This keeps the permission prompt meaningful
    and prevents an opaque element identifier from being approved in isolation.
    """

    def __init__(self, controller: BrowserController, *, events: EventBus | None = None) -> None:
        if not isinstance(controller, BrowserController):
            raise TypeError("controller must implement BrowserController")
        self.controller = controller
        self.events = events
        self._active: BrowserSession | None = None
        self._snapshot: PageSnapshot | None = None
        self._lock = asyncio.Lock()

    @property
    def active_session_id(self) -> str | None:
        return None if self._active is None else self._active.session_id

    async def start(self) -> BrowserSession:
        async with self._lock:
            if self._active is not None:
                return self._active
            self._active = await self.controller.create_session()
            self._snapshot = None
        await self._emit(EventName.BROWSER_STARTED, {"session_id": self._active.session_id})
        return self._active

    async def close(self) -> None:
        """Close the active session while keeping the controller reusable."""

        async with self._lock:
            session = self._active
            self._active = None
            self._snapshot = None
        if session is not None:
            await self.controller.close_session(session.session_id)

    async def shutdown(self) -> None:
        """Close every browser resource owned by the controller."""

        async with self._lock:
            self._active = None
            self._snapshot = None
        await self.controller.close()

    def session(self) -> BrowserSession:
        if self._active is None:
            raise BrowserSessionError("Start the browser before using browser actions")
        return self._active

    async def navigate(self, url: str) -> str:
        destination = await self.session().navigate(url)
        self._snapshot = None
        await self._emit(
            EventName.BROWSER_NAVIGATION_COMPLETED,
            {
                "session_id": self.session().session_id,
                "origin": _origin(destination),
                "path_character_count": len(urlsplit(destination).path),
                "has_query": bool(urlsplit(destination).query),
            },
        )
        return destination

    async def snapshot(self) -> PageSnapshot:
        self._snapshot = await self.session().snapshot()
        return self._snapshot

    def target(self, element_id: str, expected_role: str, expected_name: str) -> BrowserElement:
        if self._snapshot is None:
            raise BrowserElementError("Take a fresh browser snapshot before interacting")
        element = next(
            (item for item in self._snapshot.elements if item.element_id == element_id),
            None,
        )
        if element is None:
            raise BrowserElementError("The element is not present in the latest snapshot")
        if element.role != expected_role or element.name != expected_name:
            raise BrowserElementError("The element role or accessible name changed")
        if element.disabled:
            raise BrowserElementError("The requested browser element is disabled")
        return element

    async def click(self, element_id: str, expected_role: str, expected_name: str) -> None:
        self.target(element_id, expected_role, expected_name)
        await self.session().click(element_id)
        self._snapshot = None

    async def type_text(
        self,
        element_id: str,
        expected_role: str,
        expected_name: str,
        text: str,
        *,
        clear: bool,
    ) -> None:
        self.target(element_id, expected_role, expected_name)
        await self.session().type_text(element_id, text, clear=clear)
        self._snapshot = None

    async def press_key(
        self,
        element_id: str,
        expected_role: str,
        expected_name: str,
        key: str,
    ) -> None:
        self.target(element_id, expected_role, expected_name)
        await self.session().press_key(element_id, key)
        self._snapshot = None

    async def _emit(self, name: EventName, payload: dict[str, object]) -> None:
        if self.events is not None:
            await self.events.publish(name, payload, source="browser")


def register_browser_actions(
    registry: ActionRegistry,
    service: BrowserActionService,
) -> None:
    """Register bounded browser operations without selectors or script execution."""

    element_parameters = (
        ActionParameter("element_id", str, "Opaque ID from the latest snapshot.", max_length=80),
        ActionParameter("expected_role", str, "Role shown in that snapshot.", max_length=80),
        ActionParameter(
            "expected_name",
            str,
            "Exact accessible name shown in that snapshot and the permission prompt.",
            max_length=500,
        ),
    )

    @registry.action(
        name="browser_start",
        description="Start an isolated browser automation session.",
        risk_level=RiskLevel.ACTION,
    )
    async def browser_start() -> ActionResult:
        try:
            session = await service.start()
            return ActionResult.succeeded(
                "browser_start",
                message="Browser session started.",
                data={"session_id": session.session_id},
            )
        except BrowserError:
            return _failure("browser_start", "The browser session could not be started.")

    @registry.action(
        name="browser_close",
        description="Close the active automated browser session and its tabs.",
        risk_level=RiskLevel.DESTRUCTIVE,
    )
    async def browser_close() -> ActionResult:
        try:
            await service.close()
            return ActionResult.succeeded("browser_close", message="Browser session closed.")
        except BrowserError:
            return _failure("browser_close", "The browser session could not be closed.")

    @registry.action(
        name="browser_navigate",
        description="Navigate the active browser tab to an explicit public HTTP(S) URL.",
        parameters=(ActionParameter("url", str, max_length=4096),),
        risk_level=RiskLevel.ACTION,
    )
    async def browser_navigate(url: str) -> ActionResult:
        try:
            destination = await service.navigate(url)
            return ActionResult.succeeded(
                "browser_navigate",
                message="Browser navigation completed.",
                data={"url": destination, "content_trust": "untrusted"},
            )
        except BrowserError:
            return _failure("browser_navigate", "The browser could not navigate to that URL.")

    @registry.action(
        name="browser_search_web",
        description="Send a search query to DuckDuckGo in the active browser tab.",
        parameters=(ActionParameter("query", str, min_length=1, max_length=500),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_search_web(query: str) -> ActionResult:
        try:
            destination = await service.navigate(
                "https://duckduckgo.com/?" + urlencode({"q": query})
            )
            return ActionResult.succeeded(
                "browser_search_web",
                message="Search results opened.",
                data={"url": destination, "content_trust": "untrusted"},
            )
        except BrowserError:
            return _failure("browser_search_web", "The web search could not be opened.")

    @registry.action(
        name="browser_snapshot",
        description="Read a bounded DOM/accessibility snapshot as explicitly untrusted content.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_snapshot() -> ActionResult:
        try:
            snapshot = await service.snapshot()
            return ActionResult.succeeded(
                "browser_snapshot",
                message=f"Read {len(snapshot.elements)} interactive browser elements.",
                data=snapshot.as_model_data(),
            )
        except BrowserError:
            return _failure("browser_snapshot", "The page snapshot could not be read.")

    @registry.action(
        name="browser_click",
        description="Click a verified element from the latest browser snapshot.",
        parameters=element_parameters,
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_click(
        element_id: str,
        expected_role: str,
        expected_name: str,
    ) -> ActionResult:
        try:
            await service.click(element_id, expected_role, expected_name)
            return ActionResult.succeeded(
                "browser_click", message=f"Clicked {expected_role}: {expected_name}."
            )
        except BrowserError:
            return _failure("browser_click", "The verified browser element could not be clicked.")

    @registry.action(
        name="browser_type",
        description="Type confirmed text into a verified element from the latest snapshot.",
        parameters=element_parameters
        + (
            ActionParameter("text", str, max_length=500),
            ActionParameter("clear", bool, required=False, default=True),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_type(
        element_id: str,
        expected_role: str,
        expected_name: str,
        text: str,
        clear: bool = True,
    ) -> ActionResult:
        try:
            await service.type_text(
                element_id,
                expected_role,
                expected_name,
                text,
                clear=clear,
            )
            return ActionResult.succeeded("browser_type", message="Typed into the verified field.")
        except BrowserError:
            return _failure("browser_type", "Text could not be typed into that browser element.")

    _register_navigation_actions(registry, service, element_parameters)
    _register_read_actions(registry, service)


def _register_navigation_actions(
    registry: ActionRegistry,
    service: BrowserActionService,
    element_parameters: tuple[ActionParameter, ...],
) -> None:
    async def navigate_history(name: str) -> ActionResult:
        try:
            method = getattr(service.session(), name.removeprefix("browser_"))
            url = await method()
            service._snapshot = None
            return ActionResult.succeeded(
                name,
                message="Browser navigation completed.",
                data={"url": url},
            )
        except BrowserError:
            return _failure(name, "The browser navigation could not be completed.")

    for name, description in (
        ("browser_back", "Navigate back in the active browser tab."),
        ("browser_forward", "Navigate forward in the active browser tab."),
        ("browser_reload", "Reload the active browser tab."),
    ):
        registry.register(
            Action(
                name=name,
                description=description,
                handler=lambda _name=name: navigate_history(_name),
                risk_level=RiskLevel.ACTION,
            )
        )

    @registry.action(
        name="browser_scroll",
        description="Scroll the active browser page by a bounded vertical delta.",
        parameters=(ActionParameter("delta_y", int, minimum=-10_000, maximum=10_000),),
        risk_level=RiskLevel.ACTION,
    )
    async def browser_scroll(delta_y: int) -> ActionResult:
        try:
            await service.session().scroll(delta_y)
            service._snapshot = None
            return ActionResult.succeeded("browser_scroll", message="Browser page scrolled.")
        except BrowserError:
            return _failure("browser_scroll", "The browser page could not be scrolled.")

    @registry.action(
        name="browser_press_key",
        description="Press one allowlisted key on a verified browser element.",
        parameters=element_parameters + (ActionParameter("key", str, max_length=20),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_press_key(
        element_id: str,
        expected_role: str,
        expected_name: str,
        key: str,
    ) -> ActionResult:
        try:
            await service.press_key(element_id, expected_role, expected_name, key)
            return ActionResult.succeeded("browser_press_key", message=f"Pressed {key} in browser.")
        except BrowserError:
            return _failure("browser_press_key", "That browser key could not be pressed.")


def _register_read_actions(registry: ActionRegistry, service: BrowserActionService) -> None:
    @registry.action(
        name="browser_list_tabs",
        description="List bounded metadata for browser tabs.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_list_tabs() -> ActionResult:
        try:
            tabs = await service.session().list_tabs()
            return ActionResult.succeeded(
                "browser_list_tabs",
                message=f"Found {len(tabs)} browser tabs.",
                data={"tabs": [asdict(tab) for tab in tabs], "content_trust": "untrusted"},
            )
        except BrowserError:
            return _failure("browser_list_tabs", "Browser tabs could not be listed.")

    @registry.action(
        name="browser_visible_text",
        description="Read bounded visible page text as explicitly untrusted content.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_visible_text() -> ActionResult:
        try:
            page_text = await service.session().visible_text()
            return ActionResult.succeeded(
                "browser_visible_text",
                message="Read visible browser text.",
                data={"text": page_text, "content_trust": "untrusted"},
            )
        except BrowserError:
            return _failure("browser_visible_text", "Visible browser text could not be read.")

    @registry.action(
        name="browser_find_text",
        description="Find literal text in the bounded visible browser page.",
        parameters=(ActionParameter("text", str, min_length=1, max_length=256),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_find_text(text: str) -> ActionResult:
        try:
            matches = await service.session().find(text)
            return ActionResult.succeeded(
                "browser_find_text",
                message=f"Found {len(matches)} matches.",
                data={
                    "matches": [asdict(match) for match in matches],
                    "content_trust": "untrusted",
                },
            )
        except BrowserError:
            return _failure("browser_find_text", "The browser text search could not be completed.")

    @registry.action(
        name="browser_list_downloads",
        description="List bounded browser download metadata, never downloaded file contents.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def browser_list_downloads() -> ActionResult:
        try:
            downloads = await service.session().list_downloads()
            return ActionResult.succeeded(
                "browser_list_downloads",
                message=f"Found {len(downloads)} browser download records.",
                data={
                    "downloads": [asdict(item) for item in downloads],
                    "content_trust": "untrusted",
                },
            )
        except BrowserError:
            return _failure("browser_list_downloads", "Download metadata could not be listed.")


def _failure(action: str, message: str) -> ActionResult:
    return ActionResult.failed(
        action,
        "The browser provider reported a controlled failure.",
        message=message,
        error_code="browser_error",
    )


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"
