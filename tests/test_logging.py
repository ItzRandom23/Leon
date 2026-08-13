"""Tests for structured log redaction."""

from __future__ import annotations

import logging

from jarvis.utils.logging import RedactingFilter, redact


def test_redact_masks_nested_secret_keys_and_inline_credentials() -> None:
    value = {
        "api_key": "top-secret",
        "nested": {"cookie": "session-cookie", "safe": "visible"},
        "message": "Authorization: Bearer abc.def and password=hunter2",
    }

    assert redact(value) == {
        "api_key": "***",
        "nested": {"cookie": "***", "safe": "visible"},
        "message": "Authorization: Bearer *** and password=***",
    }


def test_logging_filter_redacts_message_arguments_and_extra_fields() -> None:
    record = logging.LogRecord(
        "jarvis.test",
        logging.INFO,
        __file__,
        1,
        "request token=%s",
        ("private-token",),
        None,
    )
    record.authorization = "Bearer private"

    assert RedactingFilter().filter(record) is True
    assert record.authorization == "***"
    assert record.getMessage() == "request token=***"
