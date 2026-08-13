"""Permissioned management actions for trusted local Python plugins."""

from __future__ import annotations

from jarvis.core.actions import ActionParameter, ActionRegistry, ActionResult
from jarvis.plugins.manager import PluginManager
from jarvis.plugins.models import PluginError, PluginInfo
from jarvis.skills.base import RiskLevel


def register_plugin_actions(registry: ActionRegistry, manager: PluginManager) -> None:
    """Expose plugin lifecycle through the same permission engine as other tools."""

    plugin_id = ActionParameter(
        "plugin_id",
        str,
        "Installed jarvis.plugins entry-point identifier.",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )

    @registry.action(
        name="plugin_list",
        description="Discover installed plugin entry points without importing plugin code.",
        risk_level=RiskLevel.READ,
    )
    async def plugin_list() -> ActionResult:
        try:
            values = manager.discover()
            return ActionResult.succeeded(
                "plugin_list",
                message=f"Found {len(values)} installed plugin entry points.",
                data={"plugins": [_info_data(item) for item in values]},
            )
        except PluginError:
            return _failure("plugin_list", "Plugin discovery could not be completed.")

    @registry.action(
        name="plugin_inspect",
        description="Import trusted local plugin code to inspect its validated metadata.",
        parameters=(plugin_id,),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def plugin_inspect(plugin_id: str) -> ActionResult:
        try:
            info = manager.inspect(plugin_id)
            return _info_result("plugin_inspect", info, "Inspected the plugin metadata.")
        except PluginError:
            return _failure("plugin_inspect", "That plugin could not be inspected.")

    @registry.action(
        name="plugin_enable",
        description="Import, initialize, and enable trusted local Python plugin code.",
        parameters=(plugin_id,),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def plugin_enable(plugin_id: str) -> ActionResult:
        try:
            info = await manager.enable(plugin_id)
            return _info_result("plugin_enable", info, "Enabled the trusted plugin.")
        except PluginError:
            return _failure("plugin_enable", "That plugin could not be enabled.")

    @registry.action(
        name="plugin_disable",
        description="Disable a plugin and remove its registered capabilities.",
        parameters=(plugin_id,),
        risk_level=RiskLevel.ACTION,
    )
    async def plugin_disable(plugin_id: str) -> ActionResult:
        try:
            info = await manager.disable(plugin_id)
            return _info_result("plugin_disable", info, "Disabled the plugin.")
        except PluginError:
            return _failure("plugin_disable", "That plugin could not be disabled.")


def _info_result(action: str, info: PluginInfo, message: str) -> ActionResult:
    if info.error is not None:
        return _failure(action, info.error)
    return ActionResult.succeeded(action, message=message, data={"plugin": _info_data(info)})


def _info_data(info: PluginInfo) -> dict[str, object]:
    metadata = info.metadata
    return {
        "plugin_id": info.plugin_id,
        "status": info.status.value,
        "enabled": info.enabled,
        "loaded": info.loaded,
        "distribution": info.distribution,
        "metadata": None
        if metadata is None
        else {
            "name": metadata.name,
            "version": metadata.version,
            "author": metadata.author,
            "description": metadata.description,
            "permissions": list(metadata.permissions),
            "capabilities": list(metadata.capabilities),
            "dependencies": list(metadata.dependencies),
            "api_version": metadata.api_version,
        },
        "warning": info.warning,
    }


def _failure(action: str, message: str) -> ActionResult:
    return ActionResult.failed(
        action,
        "The plugin manager reported a controlled failure.",
        message=message,
        error_code="plugin_error",
    )
