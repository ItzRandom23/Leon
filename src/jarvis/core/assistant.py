"""Interactive assistant orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable

from jarvis.core.router import Router, create_default_router
from jarvis.skills.base import SkillResult

logger = logging.getLogger(__name__)

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


class Assistant:
    """Connect a command router to an interactive terminal session."""

    def __init__(
        self,
        router: Router,
        *,
        input_fn: InputFunction = input,
        output_fn: OutputFunction = print,
    ) -> None:
        self._router = router
        self._input = input_fn
        self._output = output_fn

    @property
    def router(self) -> Router:
        """Return the router used by this assistant."""

        return self._router

    def process(self, command: str) -> SkillResult:
        """Process one command without starting an interactive session."""

        return self._router.route(command)

    def run(self) -> None:
        """Run until an exit skill, end-of-input, or keyboard interrupt."""

        self._output("JARVIS")
        while True:
            try:
                command = self._input("\nYou > ")
            except (EOFError, KeyboardInterrupt):
                logger.info("interactive_session_interrupted")
                self._output("\nJarvis > Goodbye.")
                return

            if not command.strip():
                continue

            result = self.process(command)
            self._output(f"\nJarvis > {result.message}")
            if result.should_exit:
                return


def create_default_assistant(
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
) -> Assistant:
    """Create an assistant containing all Phase 1 built-in skills."""

    return Assistant(create_default_router(), input_fn=input_fn, output_fn=output_fn)
