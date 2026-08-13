"""Public API for trusted local JARVIS plugins."""

from jarvis.plugins.actions import register_plugin_actions
from jarvis.plugins.api import Plugin, PluginContext, PluginProtocol
from jarvis.plugins.manager import PluginManager
from jarvis.plugins.models import (
    JARVIS_PLUGIN_API,
    PLUGIN_ENTRY_POINT_GROUP,
    TRUSTED_PLUGIN_WARNING,
    PluginCompatibilityError,
    PluginError,
    PluginInfo,
    PluginMetadata,
    PluginNotFoundError,
    PluginRegistrationError,
    PluginStateError,
    PluginStatus,
)
from jarvis.plugins.state import (
    InMemoryPluginStateRepository,
    PluginStateRepository,
    SQLitePluginStateRepository,
)

__all__ = [
    "JARVIS_PLUGIN_API",
    "PLUGIN_ENTRY_POINT_GROUP",
    "TRUSTED_PLUGIN_WARNING",
    "InMemoryPluginStateRepository",
    "Plugin",
    "PluginCompatibilityError",
    "PluginContext",
    "PluginError",
    "PluginInfo",
    "PluginManager",
    "PluginMetadata",
    "PluginNotFoundError",
    "PluginProtocol",
    "PluginRegistrationError",
    "PluginStateError",
    "PluginStateRepository",
    "PluginStatus",
    "SQLitePluginStateRepository",
    "register_plugin_actions",
]
