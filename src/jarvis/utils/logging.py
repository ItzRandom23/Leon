"""Structured-log redaction for secrets and terminal control content."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

_SECRET_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|auth[_-]?token|token|"
    r"password|passwd|secret|credential|cookie|session)",
    re.IGNORECASE,
)
_SECRET_TEXT = (
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(api[_-]?key|token|password|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a logging-safe copy of a basic structured value."""

    if key is not None and _SECRET_KEY.search(key):
        return "***" if value not in (None, "") else value
    if isinstance(value, str):
        cleaned = _CONTROL.sub("", value)
        cleaned = _SECRET_TEXT[0].sub(r"\1***", cleaned)
        return _SECRET_TEXT[1].sub(r"\1\2***", cleaned)
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    """Remove common credential forms from messages, arguments, and extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = str(record.msg)
        record.msg = redact(rendered)
        record.args = ()
        for field_name, field_value in tuple(record.__dict__.items()):
            if field_name in _LOG_RECORD_FIELDS:
                continue
            record.__dict__[field_name] = redact(field_value, key=field_name)
        return True


_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
