"""Dependency-free ``KEY=VALUE`` environment-file loading.

JARVIS intentionally avoids a dotenv dependency and its import-time side
effects. This module provides a small, validated parser plus a loader that
exports parsed values into the process environment. Real environment variables
always win: a file value is applied only when the variable is not already set.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def parse_dotenv(content: str) -> dict[str, str]:
    """Parse dotenv text into a mapping without mutating the environment.

    Blank lines, full-line comments, and lines without an ``=`` are ignored.
    Surrounding single or double quotes are stripped from values.
    """

    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(
    path: str | os.PathLike[str],
    *,
    environment: Mapping[str, str] | None = None,
    overwrite: bool = False,
) -> dict[str, str]:
    """Load one environment file, exporting only variables not already present.

    Empty values are skipped so a reference-style file does not force empty
    required fields; those variables simply keep their defaults.  ``environment``
    defaults to ``os.environ``; it is accepted as a mutable mapping for tests.
    Returns the variables actually applied.
    """

    target = os.environ if environment is None else environment
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    applied: dict[str, str] = {}
    for key, value in parse_dotenv(content).items():
        if not value:
            continue
        if overwrite or key not in target:
            try:
                target[key] = value  # type: ignore[index]
            except TypeError:  # pragma: no cover - immutable mapping in tests
                continue
            applied[key] = value
    return applied


def load_env_file_from_default_locations(
    *, environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Load the first existing ``.env`` file from the default locations.

    Candidates, in order: ``.env`` in the current working directory, then
    ``~/.jarvis/.env``. Real environment variables still take precedence.
    """

    candidates = (Path.cwd() / ".env", Path.home() / ".jarvis" / ".env")
    for candidate in candidates:
        if candidate.is_file():
            return load_env_file(candidate, environment=environment)
    return {}
