# JARVIS example plugin

This package demonstrates the Phase 10 plugin API. Installing it adds the
`example` entry point in the `jarvis.plugins` group; JARVIS never scans plugin
directories or executes loose Python files.

Plugins are trusted local software. They run as ordinary Python code with the
same operating-system access as JARVIS and are not sandboxed. Review a plugin and
its declared permissions before enabling it.

The example stages one read-only `example_greeting` action. Registration becomes
visible to JARVIS only after initialization and registration both finish.
