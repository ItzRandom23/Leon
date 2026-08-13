"""Provider-neutral asynchronous browser contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod

from jarvis.browser.models import BrowserTab, DownloadMetadata, FindMatch, PageSnapshot


class BrowserSession(ABC):
    """One isolated browser context with tabs and element-ID-only interaction."""

    @property
    @abstractmethod
    def session_id(self) -> str:
        """Return the opaque session identifier."""

    @abstractmethod
    async def list_tabs(self) -> tuple[BrowserTab, ...]:
        """Return bounded metadata for registered tabs."""

    @abstractmethod
    async def new_tab(self, url: str | None = None) -> BrowserTab:
        """Open a tab and optionally navigate to a validated web URL."""

    @abstractmethod
    async def close_tab(self, tab_id: str) -> None:
        """Close a tab, preserving at least one tab in the session."""

    @abstractmethod
    async def switch_tab(self, tab_id: str) -> BrowserTab:
        """Bring an existing tab to the foreground."""

    @abstractmethod
    async def navigate(self, url: str) -> str:
        """Navigate the active tab to a validated web URL."""

    @abstractmethod
    async def back(self) -> str | None:
        """Navigate backward in the active tab history."""

    @abstractmethod
    async def forward(self) -> str | None:
        """Navigate forward in the active tab history."""

    @abstractmethod
    async def reload(self) -> str | None:
        """Reload the active tab."""

    @abstractmethod
    async def title(self) -> str:
        """Return a bounded, untrusted active-page title."""

    @abstractmethod
    async def url(self) -> str | None:
        """Return the validated active-page URL, or ``None`` for a blank page."""

    @abstractmethod
    async def visible_text(self) -> str:
        """Return bounded visible text as untrusted data."""

    @abstractmethod
    async def snapshot(self) -> PageSnapshot:
        """Create a numbered, bounded page snapshot and fresh element IDs."""

    @abstractmethod
    async def click(self, element_id: str) -> None:
        """Click an element from the latest snapshot by opaque ID only."""

    @abstractmethod
    async def type_text(self, element_id: str, text: str, *, clear: bool = True) -> None:
        """Type bounded text into a latest-snapshot element by opaque ID only."""

    @abstractmethod
    async def find(self, text: str) -> tuple[FindMatch, ...]:
        """Find bounded literal matches in visible text."""

    @abstractmethod
    async def scroll(self, delta_y: int) -> None:
        """Scroll the active page vertically by a bounded delta."""

    @abstractmethod
    async def press_key(self, element_id: str, key: str) -> None:
        """Press one allowlisted key on a latest-snapshot element."""

    @abstractmethod
    async def list_downloads(self) -> tuple[DownloadMetadata, ...]:
        """Return bounded download metadata; never file contents."""

    @abstractmethod
    async def close(self) -> None:
        """Close the browser session and all of its tabs."""


class BrowserController(ABC):
    """Provider-neutral owner of isolated browser sessions."""

    @abstractmethod
    async def create_session(self) -> BrowserSession:
        """Create and register an isolated browser session."""

    async def start_session(self) -> BrowserSession:
        """Alias for ``create_session`` for command-oriented callers."""

        return await self.create_session()

    @abstractmethod
    async def list_sessions(self) -> tuple[str, ...]:
        """Return registered opaque session IDs."""

    @abstractmethod
    def get_session(self, session_id: str) -> BrowserSession:
        """Resolve an opaque session ID without creating a session."""

    @abstractmethod
    async def close_session(self, session_id: str) -> None:
        """Close and unregister one session."""

    @abstractmethod
    async def close(self) -> None:
        """Close every session and release the browser backend."""
