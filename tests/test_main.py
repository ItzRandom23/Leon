"""Tests for the installed console entry point."""

from __future__ import annotations

import logging
from unittest.mock import Mock

from jarvis import main as main_module


def test_main_configures_warning_logging_and_delegates_arguments(monkeypatch) -> None:
    configure_logging = Mock()
    run_cli = Mock(return_value=7)
    monkeypatch.setattr(main_module, "configure_logging", configure_logging)
    monkeypatch.setattr(main_module, "run_cli", run_cli)

    status = main_module.main(["version"])

    assert status == 7
    configure_logging.assert_called_once_with(logging.WARNING)
    run_cli.assert_called_once_with(["version"])


def test_main_enables_debug_logging_before_cli_dispatch(monkeypatch) -> None:
    configure_logging = Mock()
    run_cli = Mock(return_value=0)
    monkeypatch.setattr(main_module, "configure_logging", configure_logging)
    monkeypatch.setattr(main_module, "run_cli", run_cli)

    status = main_module.main(["--debug", "doctor"])

    assert status == 0
    configure_logging.assert_called_once_with(logging.DEBUG)
    run_cli.assert_called_once_with(["--debug", "doctor"])
