"""Provider-neutral, permission-ready web browser foundations."""

from jarvis.browser.actions import BrowserActionService, register_browser_actions
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
    UNTRUSTED_BROWSER_CONTENT_NOTICE,
    BrowserElement,
    BrowserLimits,
    BrowserTab,
    DownloadMetadata,
    FindMatch,
    PageSnapshot,
)
from jarvis.browser.playwright import PlaywrightBrowserController, PlaywrightBrowserSession
from jarvis.browser.proxy import AuthenticatedLoopbackProxy
from jarvis.browser.validation import (
    AddressResolver,
    PublicHostPolicy,
    validate_redirect_url,
    validate_web_url,
)

__all__ = [
    "UNTRUSTED_BROWSER_CONTENT_NOTICE",
    "AddressResolver",
    "AuthenticatedLoopbackProxy",
    "BrowserController",
    "BrowserActionService",
    "BrowserDependencyError",
    "BrowserElement",
    "BrowserElementError",
    "BrowserError",
    "BrowserLimitError",
    "BrowserLimits",
    "BrowserNavigationError",
    "BrowserSession",
    "BrowserSessionError",
    "BrowserTab",
    "BrowserTimeoutError",
    "BrowserValidationError",
    "DownloadMetadata",
    "FindMatch",
    "PageSnapshot",
    "PlaywrightBrowserController",
    "PlaywrightBrowserSession",
    "PublicHostPolicy",
    "validate_redirect_url",
    "validate_web_url",
    "register_browser_actions",
]
