"""Console entry point for JARVIS."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from jarvis.cli import run_cli
from jarvis.utils.logging import RedactingFilter


def configure_logging(level: int = logging.WARNING) -> None:
    """Configure concise key-value-friendly diagnostics for the CLI."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s level=%(levelname)s logger=%(name)s message=%(message)s",
    )
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if not any(isinstance(item, RedactingFilter) for item in handler.filters):
            handler.addFilter(RedactingFilter())


def main(argv: Sequence[str] | None = None) -> int:
    """Start the professional Phase 1–6 command-line interface."""

    arguments = list(argv) if argv is not None else sys.argv[1:]
    debug = "--debug" in arguments
    configure_logging(logging.DEBUG if debug else logging.WARNING)
    return run_cli(arguments)


if __name__ == "__main__":  # pragma: no cover - exercised through ``python -m jarvis``
    raise SystemExit(main())
