"""Optional desktop GUI for the shared JARVIS runtime.

Importing this package never imports Qt. Applications may use the neutral
controller and permission broker in tests or alternative UI toolkits, then opt
into :func:`run_gui` when PySide6 and qasync are installed.
"""

from jarvis.gui.controller import (
    GuiBusyError,
    GuiClosedError,
    GuiController,
    GuiControllerError,
)
from jarvis.gui.data import ApplicationDataProvider, GuiDataProvider, GuiLogStore
from jarvis.gui.models import (
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
    PermissionPrompt,
    PluginView,
    ReminderView,
    SettingView,
    StatusView,
    Theme,
)
from jarvis.gui.permissions import GuiPermissionBroker
from jarvis.gui.pyside import (
    GuiUnavailableError,
    create_main_window,
    is_gui_available,
    run_gui,
)

__all__ = [
    "AboutView",
    "ActionActivity",
    "ActivityState",
    "ApplicationDataProvider",
    "AssistantState",
    "ChatMessageView",
    "ChatRole",
    "GuiBusyError",
    "GuiClosedError",
    "GuiController",
    "GuiControllerError",
    "GuiDataProvider",
    "GuiLogStore",
    "GuiPermissionBroker",
    "GuiUnavailableError",
    "GuiUpdate",
    "GuiUpdateKind",
    "IntegrationView",
    "LogView",
    "MemoryView",
    "Page",
    "PermissionPrompt",
    "PluginView",
    "ReminderView",
    "SettingView",
    "StatusView",
    "Theme",
    "create_main_window",
    "is_gui_available",
    "run_gui",
]
