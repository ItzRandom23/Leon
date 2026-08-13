"""Lazy optional Playwright adapter for the provider-neutral browser API."""

from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, TypeVar

from jarvis.browser.base import BrowserController, BrowserSession
from jarvis.browser.errors import (
    BrowserDependencyError,
    BrowserElementError,
    BrowserError,
    BrowserLimitError,
    BrowserNavigationError,
    BrowserSessionError,
    BrowserTimeoutError,
    BrowserValidationError,
)
from jarvis.browser.models import (
    BrowserElement,
    BrowserLimits,
    BrowserTab,
    DownloadMetadata,
    FindMatch,
    PageSnapshot,
)
from jarvis.browser.proxy import AuthenticatedLoopbackProxy
from jarvis.browser.validation import (
    AddressResolver,
    PublicHostPolicy,
    sanitize_page_text,
    validate_find_query,
    validate_identifier,
    validate_key,
    validate_redirect_url,
    validate_typed_text,
    validate_web_url,
)

_PLAYWRIGHT_INSTALL_MESSAGE = (
    "The browser adapter requires optional Playwright: install 'playwright', then run "
    "'playwright install chromium'"
)
_INTERACTIVE_SELECTOR = "a[href],button,input,textarea,select,[role],[contenteditable='true']"
_TAG_NAME_SCRIPT = "element => element.tagName.toLowerCase()"
_IS_CONNECTED_SCRIPT = "element => element.isConnected"
_ROLE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_DISABLE_DIRECT_SOCKET_APIS = """
() => {
  for (const name of [
    'RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCDataChannel',
    'WebTransport', 'TCPSocket', 'UDPSocket'
  ]) {
    try {
      Object.defineProperty(globalThis, name, {
        value: undefined, writable: false, configurable: false
      });
    } catch (_) {}
  }
}
"""
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _BoundElement:
    """Stable DOM handle plus the exact identity shown at consent time."""

    handle: Any
    page: Any
    tab_id: str
    page_url: str | None
    tag: str
    explicit_role: str
    input_type: str
    role: str
    name: str
    enabled: bool


async def _finish_async_cleanup(awaitable: Awaitable[Any]) -> bool:
    """Observe cleanup to completion even after caller cancellation."""

    task = asyncio.create_task(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
            continue
    await task
    return cancelled


async def _with_timeout(
    awaitable: Awaitable[_T],
    *,
    timeout_ms: int,
    error_message: str,
    error_type: type[BrowserSessionError] = BrowserSessionError,
) -> _T:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_ms / 1000)
    except BrowserError:
        raise
    except TimeoutError:
        raise BrowserTimeoutError("The browser operation timed out") from None
    except Exception:
        raise error_type(error_message) from None


def _load_playwright() -> Any:
    try:
        return importlib.import_module("playwright.async_api")
    except (ImportError, ModuleNotFoundError):
        raise BrowserDependencyError(_PLAYWRIGHT_INSTALL_MESSAGE) from None


class PlaywrightBrowserController(BrowserController):
    """Own Playwright runtime resources while exposing provider-neutral sessions."""

    def __init__(
        self,
        *,
        limits: BrowserLimits | None = None,
        browser_type: str = "chromium",
        headless: bool = True,
        module_loader: Callable[[], Any] = _load_playwright,
        address_resolver: AddressResolver | None = None,
    ) -> None:
        if browser_type not in {"chromium", "firefox", "webkit"}:
            raise BrowserValidationError("browser_type must name a supported Playwright browser")
        self._limits = limits or BrowserLimits()
        self._browser_type = browser_type
        self._headless = bool(headless)
        self._module_loader = module_loader
        self._address_resolver = address_resolver
        self._runtime: Any | None = None
        self._browser: Any | None = None
        self._sessions: dict[str, PlaywrightBrowserSession] = {}
        self._next_session = 1
        self._closed = False
        self._lock = asyncio.Lock()

    async def create_session(self) -> BrowserSession:
        async with self._lock:
            if self._closed:
                raise BrowserSessionError("The browser controller is closed")
            if len(self._sessions) >= self._limits.max_sessions:
                raise BrowserLimitError("The browser session limit has been reached")
            browser = await self._ensure_browser()
            host_policy = PublicHostPolicy(
                self._address_resolver,
                timeout_ms=self._limits.timeout_ms,
            )
            egress_proxy = AuthenticatedLoopbackProxy(
                host_policy,
                timeout_ms=self._limits.timeout_ms,
                max_url_chars=self._limits.max_url_chars,
            )
            try:
                await egress_proxy.start()
                context = await asyncio.wait_for(
                    browser.new_context(
                        accept_downloads=False,
                        service_workers="block",
                        proxy=egress_proxy.playwright_proxy,
                    ),
                    timeout=self._limits.timeout_ms / 1000,
                )
            except BaseException as error:
                try:
                    await _finish_async_cleanup(egress_proxy.close())
                except (Exception, asyncio.CancelledError):
                    pass
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, TimeoutError):
                    raise BrowserTimeoutError("Creating a browser session timed out") from None
                if isinstance(error, BrowserError):
                    raise
                if not isinstance(error, Exception):
                    raise
                raise BrowserSessionError("The browser session could not be created") from None
            session_id = f"session-{self._next_session}"
            self._next_session += 1
            session = PlaywrightBrowserSession(
                session_id,
                context,
                limits=self._limits,
                host_policy=host_policy,
                egress_proxy=egress_proxy,
            )
            try:
                await session.initialize()
            except BaseException:
                try:
                    await _finish_async_cleanup(context.close())
                except (Exception, asyncio.CancelledError):
                    pass
                try:
                    await _finish_async_cleanup(egress_proxy.close())
                except (Exception, asyncio.CancelledError):
                    pass
                raise
            self._sessions[session_id] = session
            return session

    async def list_sessions(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(self._sessions)

    def get_session(self, session_id: str) -> BrowserSession:
        validate_identifier(session_id, label="session_id")
        try:
            return self._sessions[session_id]
        except KeyError:
            raise BrowserSessionError("Unknown browser session ID") from None

    async def close_session(self, session_id: str) -> None:
        validate_identifier(session_id, label="session_id")
        async with self._lock:
            try:
                session = self._sessions.pop(session_id)
            except KeyError:
                raise BrowserSessionError("Unknown browser session ID") from None
        await session.close()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
            browser = self._browser
            runtime = self._runtime
            self._browser = None
            self._runtime = None
        for session in sessions:
            try:
                await session.close()
            except BrowserError:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if runtime is not None:
            try:
                await runtime.stop()
            except Exception:
                pass

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        runtime: Any | None = None
        try:
            module = self._module_loader()
            manager = module.async_playwright()
            runtime = await asyncio.wait_for(
                manager.start(),
                timeout=self._limits.timeout_ms / 1000,
            )
            browser_launcher = getattr(runtime, self._browser_type)
            launch_options: dict[str, Any] = {
                "headless": self._headless,
                "timeout": self._limits.timeout_ms,
            }
            if self._browser_type == "chromium":
                launch_options["args"] = [
                    "--disable-background-networking",
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                ]
            elif self._browser_type == "firefox":
                launch_options["firefox_user_prefs"] = {
                    "media.peerconnection.enabled": False,
                    "network.dns.disablePrefetch": True,
                    "network.http.http3.enable": False,
                    "network.prefetch-next": False,
                }
            browser = await asyncio.wait_for(
                browser_launcher.launch(**launch_options),
                timeout=self._limits.timeout_ms / 1000,
            )
        except BaseException as error:
            if runtime is not None:
                try:
                    await _finish_async_cleanup(runtime.stop())
                except (Exception, asyncio.CancelledError):
                    pass
            if isinstance(error, (BrowserDependencyError, asyncio.CancelledError)):
                raise
            if not isinstance(error, Exception):
                raise
            raise BrowserDependencyError(_PLAYWRIGHT_INSTALL_MESSAGE) from None
        self._runtime = runtime
        self._browser = browser
        return browser


class PlaywrightBrowserSession(BrowserSession):
    """Playwright-backed session with fixed internal locators and opaque action IDs."""

    def __init__(
        self,
        session_id: str,
        context: Any,
        *,
        limits: BrowserLimits | None = None,
        address_resolver: AddressResolver | None = None,
        host_policy: PublicHostPolicy | None = None,
        egress_proxy: AuthenticatedLoopbackProxy | None = None,
    ) -> None:
        self._session_id = validate_identifier(session_id, label="session_id")
        self._context = context
        self._limits = limits or BrowserLimits()
        self._tabs: dict[str, Any] = {}
        self._page_ids: dict[int, str] = {}
        self._active_tab_id: str | None = None
        self._next_tab = 1
        self._snapshot_generation = 0
        self._elements: dict[str, _BoundElement] = {}
        if host_policy is not None and address_resolver is not None:
            raise ValueError("host_policy and address_resolver cannot both be supplied")
        self._host_policy = host_policy or PublicHostPolicy(
            address_resolver,
            timeout_ms=self._limits.timeout_ms,
        )
        self._egress_proxy = egress_proxy
        self._downloads: list[DownloadMetadata] = []
        self._page_tasks: set[asyncio.Task[Any]] = set()
        self._next_download = 1
        self._closed = False
        self._initialized = False
        self._lock = asyncio.Lock()

    @property
    def session_id(self) -> str:
        return self._session_id

    async def initialize(self) -> None:
        """Install the fixed navigation guard and create the initial blank tab."""

        async with self._lock:
            if self._initialized:
                return
            try:
                on = getattr(self._context, "on", None)
                if callable(on):
                    on("page", self._on_context_page)
                add_init_script = getattr(self._context, "add_init_script", None)
                if callable(add_init_script):
                    await asyncio.wait_for(
                        add_init_script(script=_DISABLE_DIRECT_SOCKET_APIS),
                        timeout=self._limits.timeout_ms / 1000,
                    )
                await asyncio.wait_for(
                    self._context.route("**/*", self._guard_route),
                    timeout=self._limits.timeout_ms / 1000,
                )
                pages = tuple(self._context.pages)
                if not pages:
                    pages = (
                        await asyncio.wait_for(
                            self._context.new_page(),
                            timeout=self._limits.timeout_ms / 1000,
                        ),
                    )
            except TimeoutError:
                raise BrowserTimeoutError("Initializing the browser session timed out") from None
            except Exception:
                raise BrowserSessionError("The browser session could not be initialized") from None
            if len(pages) > self._limits.max_tabs_per_session:
                raise BrowserLimitError("The browser tab limit was exceeded during initialization")
            for page in pages:
                self._register_page(page)
            self._active_tab_id = next(iter(self._tabs))
            self._initialized = True

    async def list_tabs(self) -> tuple[BrowserTab, ...]:
        async with self._lock:
            self._ensure_open()
            return await _with_timeout(
                self._list_tabs(),
                timeout_ms=self._limits.timeout_ms,
                error_message="Browser tab metadata could not be read",
            )

    async def new_tab(self, url: str | None = None) -> BrowserTab:
        validated_url = (
            validate_web_url(url, max_chars=self._limits.max_url_chars) if url is not None else None
        )
        if validated_url is not None:
            await self._host_policy.validate(
                validated_url,
                max_chars=self._limits.max_url_chars,
            )
        async with self._lock:
            self._ensure_open()
            if len(self._tabs) >= self._limits.max_tabs_per_session:
                raise BrowserLimitError("The browser tab limit has been reached")
            page = await _with_timeout(
                self._context.new_page(),
                timeout_ms=self._limits.timeout_ms,
                error_message="A browser tab could not be opened",
            )
            tab_id = self._register_page(page)
            self._active_tab_id = tab_id
            if validated_url is not None:
                await _with_timeout(
                    self._navigate_page(page, validated_url),
                    timeout_ms=self._limits.timeout_ms,
                    error_message="The browser could not navigate to the requested URL",
                    error_type=BrowserNavigationError,
                )
            return await self._tab_metadata(tab_id, page)

    async def close_tab(self, tab_id: str) -> None:
        validate_identifier(tab_id, label="tab_id")
        async with self._lock:
            self._ensure_open()
            if len(self._tabs) == 1:
                raise BrowserLimitError("The last browser tab cannot be closed")
            page = self._get_tab(tab_id)
            await _with_timeout(
                page.close(),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser tab could not be closed",
            )
            del self._tabs[tab_id]
            self._page_ids.pop(id(page), None)
            if self._active_tab_id == tab_id:
                self._active_tab_id = next(iter(self._tabs))
            self._invalidate_snapshot()

    async def switch_tab(self, tab_id: str) -> BrowserTab:
        validate_identifier(tab_id, label="tab_id")
        async with self._lock:
            self._ensure_open()
            page = self._get_tab(tab_id)
            await _with_timeout(
                page.bring_to_front(),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser tab could not be activated",
            )
            self._active_tab_id = tab_id
            self._invalidate_snapshot()
            return await self._tab_metadata(tab_id, page)

    async def navigate(self, url: str) -> str:
        validated_url = validate_web_url(url, max_chars=self._limits.max_url_chars)
        await self._host_policy.validate(
            validated_url,
            max_chars=self._limits.max_url_chars,
        )
        async with self._lock:
            self._ensure_open()
            page = self._active_page()
            return await _with_timeout(
                self._navigate_page(page, validated_url),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser could not navigate to the requested URL",
                error_type=BrowserNavigationError,
            )

    async def back(self) -> str | None:
        return await self._history_action("go_back", "back")

    async def forward(self) -> str | None:
        return await self._history_action("go_forward", "forward")

    async def reload(self) -> str | None:
        return await self._history_action("reload", "reload")

    async def title(self) -> str:
        async with self._lock:
            self._ensure_open()
            page = self._active_page()
            await self._ensure_safe_page_url(page)
            return await _with_timeout(
                self._page_title(page),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser title could not be read",
            )

    async def url(self) -> str | None:
        async with self._lock:
            self._ensure_open()
            return await self._ensure_safe_page_url(self._active_page())

    async def visible_text(self) -> str:
        async with self._lock:
            self._ensure_open()
            page = self._active_page()
            await self._ensure_safe_page_url(page)
            return await _with_timeout(
                self._visible_text(page),
                timeout_ms=self._limits.timeout_ms,
                error_message="Visible browser text could not be read",
            )

    async def snapshot(self) -> PageSnapshot:
        async with self._lock:
            self._ensure_open()
            page = self._active_page()
            return await _with_timeout(
                self._snapshot(page),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser snapshot could not be created",
            )

    async def click(self, element_id: str) -> None:
        validate_identifier(element_id, label="element_id")
        async with self._lock:
            self._ensure_open()
            handle = await _with_timeout(
                self._resolve_element(element_id),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser element could not be verified",
                error_type=BrowserElementError,
            )
            page = self._active_page()
            await _with_timeout(
                handle.click(timeout=self._limits.timeout_ms),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser element could not be clicked",
                error_type=BrowserElementError,
            )
            await self._ensure_safe_page_url(page)
            self._invalidate_snapshot()

    async def type_text(self, element_id: str, text: str, *, clear: bool = True) -> None:
        validate_identifier(element_id, label="element_id")
        validated_text = validate_typed_text(text, max_chars=self._limits.max_type_chars)
        async with self._lock:
            self._ensure_open()
            handle = await _with_timeout(
                self._resolve_element(element_id),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser element could not be verified",
                error_type=BrowserElementError,
            )
            if clear:
                operation = handle.fill(validated_text, timeout=self._limits.timeout_ms)
            elif validated_text:
                operation = handle.type(validated_text, timeout=self._limits.timeout_ms)
            else:
                self._invalidate_snapshot()
                return
            await _with_timeout(
                operation,
                timeout_ms=self._limits.timeout_ms,
                error_message="Text could not be entered into the browser element",
                error_type=BrowserElementError,
            )
            await self._ensure_safe_page_url(self._active_page())
            self._invalidate_snapshot()

    async def find(self, text: str) -> tuple[FindMatch, ...]:
        query = validate_find_query(text, max_chars=self._limits.max_find_query_chars)
        async with self._lock:
            self._ensure_open()
            visible = await _with_timeout(
                self._visible_text(self._active_page()),
                timeout_ms=self._limits.timeout_ms,
                error_message="Visible browser text could not be searched",
            )
            matches: list[FindMatch] = []
            for match in re.finditer(re.escape(query), visible, flags=re.IGNORECASE):
                start, end = match.span()
                excerpt = visible[max(0, start - 40) : min(len(visible), end + 80)]
                matches.append(FindMatch(len(matches) + 1, start, end, excerpt))
                if len(matches) >= self._limits.max_find_matches:
                    break
            return tuple(matches)

    async def scroll(self, delta_y: int) -> None:
        if (
            isinstance(delta_y, bool)
            or not isinstance(delta_y, int)
            or delta_y == 0
            or abs(delta_y) > self._limits.max_scroll_delta
        ):
            raise BrowserValidationError("Scroll delta is outside the configured limit")
        async with self._lock:
            self._ensure_open()
            page = self._active_page()
            await _with_timeout(
                page.mouse.wheel(0, delta_y),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser page could not be scrolled",
            )
            self._invalidate_snapshot()

    async def press_key(self, element_id: str, key: str) -> None:
        validated_key = validate_key(key)
        async with self._lock:
            self._ensure_open()
            page = self._active_page()
            handle = await _with_timeout(
                self._resolve_element(element_id),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser element could not be verified",
                error_type=BrowserElementError,
            )
            await _with_timeout(
                handle.press(validated_key, timeout=self._limits.timeout_ms),
                timeout_ms=self._limits.timeout_ms,
                error_message="The browser key could not be pressed",
                error_type=BrowserElementError,
            )
            await self._ensure_safe_page_url(page)
            self._invalidate_snapshot()

    async def list_downloads(self) -> tuple[DownloadMetadata, ...]:
        async with self._lock:
            self._ensure_open()
            return tuple(self._downloads)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._tabs.clear()
            self._page_ids.clear()
            self._invalidate_snapshot()
            page_tasks = tuple(self._page_tasks)
            for task in page_tasks:
                task.cancel()

        async def cleanup() -> None:
            close_error: BrowserSessionError | None = None
            try:
                await asyncio.wait_for(
                    self._context.close(),
                    timeout=self._limits.timeout_ms / 1000,
                )
            except TimeoutError:
                close_error = BrowserTimeoutError("Closing the browser session timed out")
            except Exception:
                close_error = BrowserSessionError("The browser session could not be closed")
            if self._egress_proxy is not None:
                try:
                    await _finish_async_cleanup(self._egress_proxy.close())
                except (Exception, asyncio.CancelledError):
                    if close_error is None:
                        close_error = BrowserSessionError("The browser session could not be closed")
            if page_tasks:
                await asyncio.gather(*page_tasks, return_exceptions=True)
            if close_error is not None:
                raise close_error from None

        cancelled = await _finish_async_cleanup(cleanup())
        if cancelled:
            raise asyncio.CancelledError()

    async def _list_tabs(self) -> tuple[BrowserTab, ...]:
        tabs: list[BrowserTab] = []
        for tab_id, page in self._tabs.items():
            tabs.append(await self._tab_metadata(tab_id, page))
        return tuple(tabs)

    async def _tab_metadata(self, tab_id: str, page: Any) -> BrowserTab:
        return BrowserTab(
            tab_id=tab_id,
            url=await self._ensure_safe_page_url(page),
            title=await self._page_title(page),
            active=tab_id == self._active_tab_id,
        )

    async def _navigate_page(self, page: Any, url: str) -> str:
        await self._host_policy.validate(url, max_chars=self._limits.max_url_chars)
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self._limits.timeout_ms,
        )
        try:
            final_url = self._public_url(page)
            if final_url is None:
                raise BrowserValidationError("Navigation ended on a blank page")
            validate_redirect_url(url, final_url, max_chars=self._limits.max_url_chars)
            await self._host_policy.validate(
                final_url,
                max_chars=self._limits.max_url_chars,
            )
        except (BrowserNavigationError, BrowserValidationError):
            await self._recover_from_unsafe_navigation(page)
            raise BrowserNavigationError(
                "The browser reached an unsafe navigation target"
            ) from None
        self._invalidate_snapshot()
        return final_url

    async def _history_action(self, method_name: str, action_name: str) -> str | None:
        async with self._lock:
            self._ensure_open()
            page = self._active_page()

            async def run() -> str | None:
                operation = getattr(page, method_name)
                await operation(
                    wait_until="domcontentloaded",
                    timeout=self._limits.timeout_ms,
                )
                self._invalidate_snapshot()
                return await self._ensure_safe_page_url(page)

            return await _with_timeout(
                run(),
                timeout_ms=self._limits.timeout_ms,
                error_message=f"Browser {action_name} navigation failed",
                error_type=BrowserNavigationError,
            )

    async def _page_title(self, page: Any) -> str:
        title = await page.title()
        return sanitize_page_text(title, max_chars=self._limits.max_element_name_chars)

    async def _visible_text(self, page: Any) -> str:
        body = page.locator("body")
        text = await body.inner_text(timeout=self._limits.timeout_ms)
        return sanitize_page_text(text, max_chars=self._limits.max_visible_text_chars)

    async def _snapshot(self, page: Any) -> PageSnapshot:
        title = await self._page_title(page)
        url = await self._ensure_safe_page_url(page)
        visible_text = await self._visible_text(page)
        candidates = page.locator(_INTERACTIVE_SELECTOR)
        count = min(await candidates.count(), self._limits.max_snapshot_elements)
        self._snapshot_generation += 1
        generation = self._snapshot_generation
        elements: list[BrowserElement] = []
        element_map: dict[str, _BoundElement] = {}
        for index in range(count):
            locator = candidates.nth(index)
            try:
                handle = await locator.element_handle(timeout=self._limits.timeout_ms)
                if handle is None or not await handle.is_visible():
                    continue
                enabled = await handle.is_enabled()
                tag = await self._element_tag(handle)
                explicit_role = await self._attribute(handle, "role", 32)
                input_type = await self._attribute(handle, "type", 32)
                role = self._element_role(tag, explicit_role, input_type)
                name = await self._element_name(handle)
            except Exception:
                continue
            ordinal = len(elements) + 1
            element_id = f"s{generation}-e{ordinal}"
            elements.append(BrowserElement(ordinal, element_id, role, name, not enabled))
            element_map[element_id] = _BoundElement(
                handle=handle,
                page=page,
                tab_id=self._active_tab_id or "tab-unknown",
                page_url=url,
                tag=tag,
                explicit_role=explicit_role,
                input_type=input_type,
                role=role,
                name=name,
                enabled=enabled,
            )
        self._elements = element_map
        return PageSnapshot(
            tab_id=self._active_tab_id or "tab-unknown",
            url=url,
            title=title,
            visible_text=visible_text,
            elements=tuple(elements),
        )

    async def _element_tag(self, locator: Any) -> str:
        value = await locator.evaluate(_TAG_NAME_SCRIPT)
        value = sanitize_page_text(value, max_chars=32).casefold()
        return value if _ROLE_TOKEN.fullmatch(value) else "interactive"

    async def _attribute(self, locator: Any, name: str, maximum: int) -> str:
        value = await locator.get_attribute(name)
        return sanitize_page_text(value, max_chars=maximum)

    async def _element_name(self, locator: Any) -> str:
        for attribute in ("aria-label", "alt", "placeholder", "title"):
            value = await self._attribute(
                locator,
                attribute,
                self._limits.max_element_name_chars,
            )
            if value:
                return value
        value = await locator.inner_text()
        return sanitize_page_text(value, max_chars=self._limits.max_element_name_chars)

    @staticmethod
    def _element_role(tag: str, explicit_role: str, input_type: str) -> str:
        normalized_role = explicit_role.casefold()
        if _ROLE_TOKEN.fullmatch(normalized_role):
            return normalized_role
        normalized_type = input_type.casefold()
        if tag == "a":
            return "link"
        if tag == "button" or normalized_type in {"button", "reset", "submit"}:
            return "button"
        if (
            tag == "textarea"
            or tag == "input"
            and normalized_type
            not in {
                "button",
                "checkbox",
                "hidden",
                "radio",
                "range",
                "reset",
                "submit",
            }
        ):
            return "textbox"
        if normalized_type in {"checkbox", "radio"}:
            return normalized_type
        if normalized_type == "range":
            return "slider"
        if tag == "select":
            return "combobox"
        return "interactive"

    async def _guard_route(self, route: Any, request: Any) -> None:
        try:
            target = request.url
            redirected_from = request.redirected_from
            if redirected_from is None:
                validate_web_url(target, max_chars=self._limits.max_url_chars)
            else:
                validate_redirect_url(
                    redirected_from.url,
                    target,
                    max_chars=self._limits.max_url_chars,
                )
            await self._host_policy.validate(
                target,
                max_chars=self._limits.max_url_chars,
            )
        except (BrowserValidationError, AttributeError, TypeError, ValueError):
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _recover_from_unsafe_navigation(self, page: Any) -> None:
        try:
            await page.go_back(
                wait_until="domcontentloaded",
                timeout=self._limits.timeout_ms,
            )
            await self._ensure_safe_page_url(page)
        except Exception:
            try:
                await page.goto("about:blank", timeout=self._limits.timeout_ms)
            except Exception:
                pass
        self._invalidate_snapshot()

    def _register_page(self, page: Any) -> str:
        existing = self._page_ids.get(id(page))
        if existing is not None:
            return existing
        if len(self._tabs) >= self._limits.max_tabs_per_session:
            raise BrowserLimitError("The browser tab limit has been reached")
        tab_id = f"tab-{self._next_tab}"
        self._next_tab += 1
        self._tabs[tab_id] = page
        self._page_ids[id(page)] = tab_id
        try:
            page.on("download", self._record_download)
        except Exception:
            pass
        return tab_id

    def _on_context_page(self, page: Any) -> None:
        """Adopt script-created popups under the same tab cap or close them."""

        try:
            task = asyncio.get_running_loop().create_task(self._adopt_context_page(page))
        except RuntimeError:
            return
        self._page_tasks.add(task)
        task.add_done_callback(self._page_tasks.discard)

    async def _adopt_context_page(self, page: Any) -> None:
        close_page = False
        async with self._lock:
            if self._closed:
                close_page = True
            elif id(page) in self._page_ids:
                return
            elif len(self._tabs) >= self._limits.max_tabs_per_session:
                close_page = True
            else:
                tab_id = self._register_page(page)
                self._active_tab_id = tab_id
                self._invalidate_snapshot()
        if close_page:
            try:
                await page.close()
            except Exception:
                pass

    def _record_download(self, download: Any) -> None:
        if len(self._downloads) >= self._limits.max_downloads:
            return
        filename = self._safe_filename(getattr(download, "suggested_filename", "download"))
        source_url: str | None
        try:
            source_url = validate_web_url(
                getattr(download, "url", ""),
                max_chars=self._limits.max_url_chars,
            )
        except BrowserValidationError:
            source_url = None
        metadata = DownloadMetadata(
            download_id=f"download-{self._next_download}",
            suggested_filename=filename,
            source_url=source_url,
        )
        self._next_download += 1
        self._downloads.append(metadata)

    @staticmethod
    def _safe_filename(value: object) -> str:
        cleaned = sanitize_page_text(value, max_chars=255).replace("\\", "/")
        name = PurePath(cleaned).name.strip().strip(".")
        return name or "download"

    def _public_url(self, page: Any) -> str | None:
        value = getattr(page, "url", "")
        if value in {"", "about:blank"}:
            return None
        try:
            return validate_web_url(value, max_chars=self._limits.max_url_chars)
        except BrowserValidationError:
            raise BrowserNavigationError("The browser is displaying an unsafe URL") from None

    async def _ensure_safe_page_url(self, page: Any) -> str | None:
        value = self._public_url(page)
        if value is None:
            return None
        try:
            return await self._host_policy.validate(
                value,
                max_chars=self._limits.max_url_chars,
            )
        except BrowserValidationError:
            raise BrowserNavigationError("The browser is displaying an unsafe URL") from None

    def _active_page(self) -> Any:
        if self._active_tab_id is None:
            raise BrowserSessionError("The browser session has no active tab")
        return self._get_tab(self._active_tab_id)

    def _get_tab(self, tab_id: str) -> Any:
        try:
            return self._tabs[tab_id]
        except KeyError:
            raise BrowserSessionError("Unknown browser tab ID") from None

    def _get_element(self, element_id: str) -> _BoundElement:
        try:
            return self._elements[element_id]
        except KeyError:
            raise BrowserElementError("Unknown or stale browser element ID") from None

    async def _resolve_element(self, element_id: str) -> Any:
        """Re-verify the exact stable node and consent fingerprint before acting."""

        bound = self._get_element(element_id)
        if (
            self._active_tab_id != bound.tab_id
            or self._active_page() is not bound.page
            or await self._ensure_safe_page_url(bound.page) != bound.page_url
        ):
            raise BrowserElementError("The browser element is stale or belongs to another page")
        try:
            connected = await bound.handle.evaluate(_IS_CONNECTED_SCRIPT)
            visible = await bound.handle.is_visible()
            enabled = await bound.handle.is_enabled()
            tag = await self._element_tag(bound.handle)
            explicit_role = await self._attribute(bound.handle, "role", 32)
            input_type = await self._attribute(bound.handle, "type", 32)
            role = self._element_role(tag, explicit_role, input_type)
            name = await self._element_name(bound.handle)
        except BrowserError:
            raise
        except Exception:
            raise BrowserElementError("The browser element is detached or changed") from None
        if (
            connected is not True
            or visible is not True
            or enabled is not True
            or bound.enabled is not True
            or tag != bound.tag
            or explicit_role != bound.explicit_role
            or input_type != bound.input_type
            or role != bound.role
            or name != bound.name
        ):
            raise BrowserElementError("The browser element is detached or changed")
        return bound.handle

    def _invalidate_snapshot(self) -> None:
        self._elements.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise BrowserSessionError("The browser session is closed")
        if not self._initialized:
            raise BrowserSessionError("The browser session is not initialized")
