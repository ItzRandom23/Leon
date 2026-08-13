"""Minimal entry-point plugin implementation."""

from jarvis.plugins import JARVIS_PLUGIN_API, Plugin, PluginContext, PluginMetadata
from jarvis_example.skills import greeting_action


class ExamplePlugin(Plugin):
    """Register a single read-only example action."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="Example Plugin",
            version="0.1.0",
            author="JARVIS contributors",
            description="A minimal example of staged action registration.",
            permissions=("read",),
            capabilities=("actions",),
            dependencies=(),
            api_version=JARVIS_PLUGIN_API,
        )

    def register(self, context: PluginContext) -> None:
        context.register_action(greeting_action())
