"""Lazy optional PySide6/qasync presentation adapter for JARVIS."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import importlib.util
import json
import logging
import sys
from types import SimpleNamespace
from typing import Any, cast

from jarvis.gui.controller import GuiBusyError, GuiController, GuiControllerError
from jarvis.gui.data import ApplicationDataProvider
from jarvis.gui.models import (
    AboutView,
    GuiUpdate,
    GuiUpdateKind,
    Page,
    PermissionPrompt,
    Theme,
)
from jarvis.gui.permissions import GuiPermissionBroker


class GuiUnavailableError(RuntimeError):
    """Raised with install guidance when optional GUI packages are unavailable."""


def is_gui_available() -> bool:
    """Check optional GUI packages without importing or initializing them."""

    return (
        importlib.util.find_spec("PySide6") is not None
        and importlib.util.find_spec("qasync") is not None
    )


def create_main_window(
    controller: GuiController,
    *,
    theme: Theme | str = Theme.SYSTEM,
    minimize_to_tray: bool = False,
    title: str = "JARVIS",
) -> Any:
    """Create a polished Qt window around an existing controller.

    Imports happen inside this function so headless CLI and test environments do
    not need Qt. The caller owns the QApplication and async event loop.
    """

    qt: Any = _load_gui_dependencies()
    selected_theme = Theme(theme)
    nav_pages = tuple(Page)

    class PermissionDialog(qt.QDialog):
        def __init__(self, prompt: PermissionPrompt, parent: Any = None) -> None:
            super().__init__(parent)
            self.prompt = prompt
            self.setWindowTitle("Permission required")
            self.setModal(True)
            self.setMinimumWidth(560)
            layout = qt.QVBoxLayout(self)
            risk = qt.QLabel(
                f"<b>{_html(prompt.risk_level.upper())}</b> · {_html(prompt.action_name)}"
            )
            risk.setTextFormat(qt.Qt.RichText)
            summary = qt.QLabel(_html(prompt.summary))
            summary.setWordWrap(True)
            details = qt.QPlainTextEdit()
            details.setReadOnly(True)
            details.setMaximumHeight(220)
            details.setPlainText(
                json.dumps(dict(prompt.details), ensure_ascii=False, indent=2, sort_keys=True)
            )
            hint = qt.QLabel("Review every detail before allowing this action.")
            hint.setWordWrap(True)
            buttons = qt.QDialogButtonBox(qt.QDialogButtonBox.Yes | qt.QDialogButtonBox.No)
            _configure_permission_buttons(buttons, qt)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(risk)
            layout.addWidget(summary)
            layout.addWidget(details)
            layout.addWidget(hint)
            layout.addWidget(buttons)

    class JarvisMainWindow(qt.QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.controller = controller
            self._tasks: set[asyncio.Task[Any]] = set()
            self._permission_dialog: Any | None = None
            self._tray: Any | None = None
            self._force_close = False
            self._minimize_to_tray = bool(minimize_to_tray)
            self._page_widgets: dict[Page, Any] = {}
            self._tables: dict[Page, Any] = {}
            self.setWindowTitle(title)
            self.resize(1180, 760)
            self.setMinimumSize(880, 600)
            self._build_ui()
            self._apply_theme(selected_theme)
            self._setup_tray()
            self._unsubscribe = controller.subscribe(self._on_update)
            self._refresh_chat()
            self._refresh_activity()
            self._refresh_status()

        def _build_ui(self) -> None:
            root = qt.QWidget()
            outer = qt.QVBoxLayout(root)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)

            header = qt.QFrame()
            header.setObjectName("header")
            header_layout = qt.QHBoxLayout(header)
            brand = qt.QLabel("JARVIS")
            brand.setObjectName("brand")
            self.status_label = qt.QLabel()
            self.cancel_button = qt.QPushButton("Cancel")
            self.cancel_button.setEnabled(False)
            self.cancel_button.clicked.connect(controller.cancel_current)
            header_layout.addWidget(brand)
            header_layout.addSpacing(18)
            header_layout.addWidget(self.status_label)
            header_layout.addStretch(1)
            header_layout.addWidget(self.cancel_button)

            body = qt.QSplitter()
            body.setChildrenCollapsible(False)
            self.navigation = qt.QListWidget()
            self.navigation.setObjectName("navigation")
            self.navigation.setMaximumWidth(210)
            self.navigation.setMinimumWidth(160)
            for page in nav_pages:
                item = qt.QListWidgetItem(page.value.title())
                item.setData(qt.Qt.UserRole, page.value)
                self.navigation.addItem(item)
            self.navigation.currentRowChanged.connect(self._select_page)

            self.pages = qt.QStackedWidget()
            self._build_chat_page()
            for page in nav_pages[1:]:
                self._build_data_page(page)
            body.addWidget(self.navigation)
            body.addWidget(self.pages)
            body.setStretchFactor(1, 1)

            outer.addWidget(header)
            outer.addWidget(body, 1)
            self.setCentralWidget(root)
            self.navigation.setCurrentRow(0)

        def _build_chat_page(self) -> None:
            page = qt.QWidget()
            layout = qt.QHBoxLayout(page)
            chat_panel = qt.QWidget()
            chat_layout = qt.QVBoxLayout(chat_panel)
            self.transcript = qt.QListWidget()
            self.transcript.setWordWrap(True)
            self.transcript.setAlternatingRowColors(True)
            self.chat_input = qt.QPlainTextEdit()
            self.chat_input.setPlaceholderText("Ask JARVIS…")
            self.chat_input.setMaximumHeight(120)
            self.send_button = qt.QPushButton("Send")
            self.send_button.setDefault(True)
            self.send_button.clicked.connect(self._send_message)
            self.voice_button = qt.QPushButton("Voice")
            self.voice_button.clicked.connect(self._capture_voice)
            composer = qt.QHBoxLayout()
            composer.addWidget(self.chat_input, 1)
            composer.addWidget(self.voice_button)
            composer.addWidget(self.send_button)
            chat_layout.addWidget(self.transcript, 1)
            chat_layout.addLayout(composer)

            activity_panel = qt.QWidget()
            activity_layout = qt.QVBoxLayout(activity_panel)
            activity_title = qt.QLabel("Action activity")
            activity_title.setObjectName("sectionTitle")
            self.activity_list = qt.QListWidget()
            self.activity_list.setMinimumWidth(270)
            activity_layout.addWidget(activity_title)
            activity_layout.addWidget(self.activity_list, 1)

            splitter = qt.QSplitter()
            splitter.addWidget(chat_panel)
            splitter.addWidget(activity_panel)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 1)
            layout.addWidget(splitter)
            self.pages.addWidget(page)
            self._page_widgets[Page.CHAT] = page

        def _build_data_page(self, page: Page) -> None:
            widget = qt.QWidget()
            layout = qt.QVBoxLayout(widget)
            title_row = qt.QHBoxLayout()
            title = qt.QLabel(page.value.title())
            title.setObjectName("pageTitle")
            refresh = qt.QPushButton("Refresh")
            refresh.clicked.connect(lambda _checked=False, item=page: self._load_page(item))
            title_row.addWidget(title)
            title_row.addStretch(1)
            title_row.addWidget(refresh)
            layout.addLayout(title_row)

            controls = qt.QHBoxLayout()
            if page is Page.MEMORY:
                search = qt.QLineEdit()
                search.setPlaceholderText("Search explicit memories")
                search_button = qt.QPushButton("Search")
                delete_button = qt.QPushButton("Delete selected")
                search_button.clicked.connect(
                    lambda _checked=False, field=search: self._search_memories(field.text())
                )
                delete_button.clicked.connect(self._delete_memory)
                controls.addWidget(search, 1)
                controls.addWidget(search_button)
                controls.addWidget(delete_button)
            elif page is Page.TASKS:
                create_button = qt.QPushButton("Create")
                edit_button = qt.QPushButton("Edit selected")
                cancel_button = qt.QPushButton("Cancel selected")
                delete_button = qt.QPushButton("Delete selected")
                create_button.clicked.connect(self._create_reminder)
                edit_button.clicked.connect(self._edit_reminder)
                cancel_button.clicked.connect(self._cancel_reminder)
                delete_button.clicked.connect(self._delete_reminder)
                controls.addWidget(create_button)
                controls.addWidget(edit_button)
                controls.addWidget(cancel_button)
                controls.addWidget(delete_button)
                controls.addStretch(1)
            elif page is Page.PLUGINS:
                warning = qt.QLabel(
                    "Plugins are trusted local Python code; they are not sandboxed."
                )
                warning.setWordWrap(True)
                enable_button = qt.QPushButton("Enable selected")
                disable_button = qt.QPushButton("Disable selected")
                enable_button.clicked.connect(self._enable_plugin)
                disable_button.clicked.connect(self._disable_plugin)
                controls.addWidget(warning, 1)
                controls.addWidget(enable_button)
                controls.addWidget(disable_button)
            if controls.count():
                layout.addLayout(controls)

            if page is Page.ABOUT:
                viewer = qt.QTextBrowser()
                viewer.setOpenExternalLinks(False)
                layout.addWidget(viewer)
                self._tables[page] = viewer
            else:
                columns = _PAGE_COLUMNS[page]
                table = qt.QTableWidget(0, len(columns))
                table.setHorizontalHeaderLabels([label for _, label in columns])
                table.setEditTriggers(qt.QAbstractItemView.NoEditTriggers)
                table.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
                table.setAlternatingRowColors(True)
                table.verticalHeader().setVisible(False)
                table.horizontalHeader().setStretchLastSection(True)
                table.horizontalHeader().setSectionResizeMode(qt.QHeaderView.ResizeToContents)
                layout.addWidget(table)
                self._tables[page] = table
            self.pages.addWidget(widget)
            self._page_widgets[page] = widget

        def _select_page(self, row: int) -> None:
            if not 0 <= row < len(nav_pages):
                return
            page = nav_pages[row]
            self.pages.setCurrentWidget(self._page_widgets[page])
            if page is not Page.CHAT:
                self._load_page(page)

        def _load_page(self, page: Page) -> None:
            async def load() -> None:
                data = await controller.load_page(page)
                self._render_page(page, data)

            self._schedule(load())

        def _render_page(self, page: Page, data: Any) -> None:
            target = self._tables[page]
            if page is Page.ABOUT and isinstance(data, AboutView):
                target.setHtml(
                    f"<h1>{_html(data.name)}</h1>"
                    f"<p>{_html(data.description)}</p>"
                    f"<p><b>Version:</b> {_html(data.version)}<br>"
                    f"<b>Python:</b> {_html(data.python_version)}</p>"
                    "<p>Permissioned by design. Side effects flow through the "
                    "same core runtime used by the CLI.</p>"
                )
                return
            columns = _PAGE_COLUMNS[page]
            target.setRowCount(len(data))
            for row, record in enumerate(data):
                values = (
                    dataclasses.asdict(cast(Any, record))
                    if dataclasses.is_dataclass(record) and not isinstance(record, type)
                    else {}
                )
                for column, (key, _label) in enumerate(columns):
                    value = values.get(key, "")
                    if isinstance(value, bool):
                        value = "yes" if value else "no"
                    item = qt.QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    target.setItem(row, column, item)
            target.resizeRowsToContents()

        def _send_message(self) -> None:
            text = self.chat_input.toPlainText()
            if not text.strip():
                return
            self.chat_input.clear()

            async def send() -> None:
                try:
                    await controller.send_message(text)
                except GuiBusyError:
                    self.chat_input.setPlainText(text)

            self._schedule(send())

        def _capture_voice(self) -> None:
            async def listen() -> None:
                try:
                    await controller.capture_voice()
                except GuiControllerError as error:
                    qt.QMessageBox.warning(self, "Voice input", str(error))

            self._schedule(listen())

        def _search_memories(self, query: str) -> None:
            if not query.strip():
                self._load_page(Page.MEMORY)
                return

            async def search() -> None:
                data = await controller.search_memories(query)
                self._render_page(Page.MEMORY, data)

            self._schedule(search())

        def _delete_memory(self) -> None:
            category = self._selected_value(Page.MEMORY, "category")
            key = self._selected_value(Page.MEMORY, "key")
            if category is None or key is None:
                self._selection_warning("Select a memory first.")
                return
            self._run_page_action(
                Page.MEMORY,
                controller.delete_memory(category, key),
            )

        def _create_reminder(self) -> None:
            message, accepted = qt.QInputDialog.getMultiLineText(
                self,
                "Create reminder",
                "Reminder text",
            )
            if not accepted or not message.strip():
                return
            scheduled_at, accepted = qt.QInputDialog.getText(
                self,
                "Create reminder",
                "ISO 8601 date/time (for example 2026-08-14T09:00:00+05:30)",
            )
            if not accepted or not scheduled_at.strip():
                return
            timezone = getattr(
                getattr(getattr(controller.application, "config", None), "scheduler", None),
                "timezone",
                "UTC",
            )
            self._run_page_action(
                Page.TASKS,
                controller.create_reminder(message, scheduled_at, timezone=str(timezone)),
            )

        def _edit_reminder(self) -> None:
            reminder_id = self._selected_integer(Page.TASKS, "id")
            current_message = self._selected_value(Page.TASKS, "message")
            current_due_at = self._selected_value(Page.TASKS, "due_at")
            if reminder_id is None or current_message is None or current_due_at is None:
                self._selection_warning("Select a reminder first.")
                return
            message, accepted = qt.QInputDialog.getText(
                self,
                "Edit reminder",
                "Reminder message:",
                text=current_message,
            )
            if not accepted or not message.strip():
                return
            due_at, accepted = qt.QInputDialog.getText(
                self,
                "Edit reminder",
                "Due time (ISO 8601, with timezone):",
                text=current_due_at,
            )
            if accepted and due_at.strip():
                self._run_page_action(
                    Page.TASKS,
                    controller.edit_reminder(
                        reminder_id,
                        message,
                        due_at,
                        expected_message=current_message,
                        expected_due_at=current_due_at,
                    ),
                )

        def _cancel_reminder(self) -> None:
            reminder_id = self._selected_integer(Page.TASKS, "id")
            message = self._selected_value(Page.TASKS, "message")
            if reminder_id is None or message is None:
                self._selection_warning("Select a reminder first.")
                return
            self._run_page_action(
                Page.TASKS,
                controller.cancel_reminder(reminder_id, message),
            )

        def _delete_reminder(self) -> None:
            reminder_id = self._selected_integer(Page.TASKS, "id")
            message = self._selected_value(Page.TASKS, "message")
            if reminder_id is None or message is None:
                self._selection_warning("Select a reminder first.")
                return
            self._run_page_action(
                Page.TASKS,
                controller.delete_reminder(reminder_id, message),
            )

        def _enable_plugin(self) -> None:
            plugin_id = self._selected_value(Page.PLUGINS, "name")
            if plugin_id is None:
                self._selection_warning("Select a plugin first.")
                return
            self._run_page_action(Page.PLUGINS, controller.enable_plugin(plugin_id))

        def _disable_plugin(self) -> None:
            plugin_id = self._selected_value(Page.PLUGINS, "name")
            if plugin_id is None:
                self._selection_warning("Select a plugin first.")
                return
            self._run_page_action(Page.PLUGINS, controller.disable_plugin(plugin_id))

        def _run_page_action(self, page: Page, awaitable: Any) -> None:
            async def run() -> None:
                result = await awaitable
                if result.success:
                    qt.QMessageBox.information(self, "JARVIS", result.message)
                    self._load_page(page)
                else:
                    qt.QMessageBox.warning(self, "JARVIS", result.message)

            self._schedule(run())

        def _selected_value(self, page: Page, key: str) -> str | None:
            table = self._tables[page]
            row = table.currentRow()
            if row < 0:
                return None
            keys = [name for name, _label in _PAGE_COLUMNS[page]]
            try:
                column = keys.index(key)
            except ValueError:
                return None
            item = table.item(row, column)
            return None if item is None or not item.text().strip() else item.text().strip()

        def _selected_integer(self, page: Page, key: str) -> int | None:
            value = self._selected_value(page, key)
            try:
                return None if value is None else int(value)
            except ValueError:
                return None

        def _selection_warning(self, message: str) -> None:
            qt.QMessageBox.warning(self, "JARVIS", message)

        def _schedule(self, coroutine: Any) -> None:
            task = asyncio.create_task(coroutine)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        def _on_update(self, update: GuiUpdate) -> None:
            if update.kind is GuiUpdateKind.CHAT:
                self._refresh_chat()
            elif update.kind is GuiUpdateKind.ACTIVITY:
                self._refresh_activity()
            elif update.kind is GuiUpdateKind.STATUS:
                self._refresh_status()
            elif update.kind is GuiUpdateKind.PERMISSION:
                self._show_next_permission()

        def _refresh_chat(self) -> None:
            self.transcript.clear()
            for message in controller.messages:
                prefix = {
                    "user": "You",
                    "assistant": "JARVIS",
                    "system": "System",
                }[message.role.value]
                item = qt.QListWidgetItem(f"{prefix}\n{message.text}")
                item.setData(qt.Qt.UserRole, message.id)
                self.transcript.addItem(item)
            self.transcript.scrollToBottom()

        def _refresh_activity(self) -> None:
            icons = {
                "requested": "○",
                "running": "◌",
                "completed": "✓",
                "failed": "!",
                "cancelled": "×",
                "outcome_unknown": "?",
            }
            self.activity_list.clear()
            for activity in reversed(controller.activities):
                detail = f" · {activity.error_code}" if activity.error_code else ""
                self.activity_list.addItem(
                    f"{icons[activity.state.value]} {activity.summary or activity.action_name}"
                    f"\n{activity.state.value}{detail}"
                )

        def _refresh_status(self) -> None:
            status = controller.status
            components = ", ".join(status.enabled_components) or "Core"
            self.status_label.setText(
                f"● {status.message}  ·  {status.ai_provider} ({status.execution_label})"
                f"  ·  {components}"
            )
            self.status_label.setProperty("state", status.state.value)
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            self.cancel_button.setEnabled(controller.busy)
            self.send_button.setEnabled(not controller.busy)
            self.voice_button.setEnabled(not controller.busy)

        def _show_next_permission(self) -> None:
            if self._permission_dialog is not None:
                return
            prompts = controller.pending_permissions
            if not prompts:
                return
            prompt = prompts[0]
            dialog = PermissionDialog(prompt, self)
            self._permission_dialog = dialog

            def finished(result: int) -> None:
                controller.resolve_permission(prompt.id, result == qt.QDialog.Accepted)
                self._permission_dialog = None
                qt.QTimer.singleShot(0, self._show_next_permission)

            dialog.finished.connect(finished)
            dialog.open()

        def _apply_theme(self, active: Theme) -> None:
            if active is Theme.SYSTEM:
                self.setStyleSheet(_BASE_STYLE)
            elif active is Theme.DARK:
                self.setStyleSheet(_BASE_STYLE + _DARK_STYLE)
            else:
                self.setStyleSheet(_BASE_STYLE + _LIGHT_STYLE)

        def _setup_tray(self) -> None:
            if not self._minimize_to_tray or not qt.QSystemTrayIcon.isSystemTrayAvailable():
                return
            tray = qt.QSystemTrayIcon(self)
            tray.setToolTip(title)
            tray.setIcon(self.style().standardIcon(qt.QStyle.SP_ComputerIcon))
            menu = qt.QMenu()
            show_action = qt.QAction("Show JARVIS", menu)
            quit_action = qt.QAction("Quit", menu)
            show_action.triggered.connect(self._restore_from_tray)
            quit_action.triggered.connect(self._quit_from_tray)
            menu.addAction(show_action)
            menu.addSeparator()
            menu.addAction(quit_action)
            tray.setContextMenu(menu)
            tray.activated.connect(lambda _reason: self._restore_from_tray())
            tray.show()
            self._tray = tray

        def _restore_from_tray(self) -> None:
            self.showNormal()
            self.raise_()
            self.activateWindow()

        def _quit_from_tray(self) -> None:
            self._force_close = True
            if self._tray is not None:
                self._tray.hide()
            self.close()
            qt.QApplication.quit()

        def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if self._tray is not None and not self._force_close:
                event.ignore()
                self.hide()
                self._tray.showMessage(
                    title,
                    "JARVIS is still running. Use the tray menu to quit.",
                    qt.QSystemTrayIcon.Information,
                    2500,
                )
                return
            self._unsubscribe()
            for task in tuple(self._tasks):
                task.cancel()
            controller.close()
            event.accept()

    return JarvisMainWindow()


def _configure_permission_buttons(buttons: Any, qt: Any) -> None:
    """Make denial the keyboard/default path; approval requires an explicit click."""

    allow = buttons.button(qt.QDialogButtonBox.Yes)
    deny = buttons.button(qt.QDialogButtonBox.No)
    allow.setText("Allow once")
    allow.setAutoDefault(False)
    allow.setDefault(False)
    deny.setText("Deny")
    deny.setAutoDefault(True)
    deny.setDefault(True)
    deny.setFocus()


def run_gui(
    application: object,
    permission_broker: GuiPermissionBroker,
    *,
    theme: Theme | str = Theme.SYSTEM,
    minimize_to_tray: bool = False,
    title: str = "JARVIS",
    close_application: bool = True,
) -> int:
    """Run the Qt event loop using the caller's already composed application.

    The broker must be the exact object whose ``confirm`` bound method was used
    when composing the runtime's :class:`PermissionManager`.
    """

    if not isinstance(permission_broker, GuiPermissionBroker):
        raise TypeError("permission_broker must be a GuiPermissionBroker")
    runtime = getattr(application, "runtime", None)
    permissions = getattr(runtime, "permissions", None)
    confirmer = getattr(permissions, "confirmer", None)
    if permissions is None or not (
        getattr(confirmer, "__self__", None) is permission_broker
        and getattr(confirmer, "__func__", None) is GuiPermissionBroker.confirm
    ):
        raise ValueError("permission_broker is not bound to the application's permission engine")
    qt = _load_gui_dependencies()
    qapp = qt.QApplication.instance() or qt.QApplication(sys.argv[:1])
    qapp.setApplicationName(title)
    qapp.setQuitOnLastWindowClosed(not minimize_to_tray)
    loop = qt.qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)
    controller = GuiController(application, permission_broker=permission_broker)
    root_logger = logging.getLogger()
    log_store = (
        controller.data_provider.logs
        if isinstance(controller.data_provider, ApplicationDataProvider)
        else None
    )
    if log_store is not None:
        root_logger.addHandler(log_store)
    window = create_main_window(
        controller,
        theme=theme,
        minimize_to_tray=minimize_to_tray,
        title=title,
    )
    qapp.aboutToQuit.connect(loop.stop)
    window.show()
    try:
        with loop:
            try:
                start = getattr(application, "start", None)
                if callable(start):
                    loop.run_until_complete(start())
                loop.run_forever()
            finally:
                loop.run_until_complete(controller.aclose())
                aclose = getattr(application, "aclose", None)
                if callable(aclose):
                    loop.run_until_complete(aclose())
    finally:
        if log_store is not None:
            root_logger.removeHandler(log_store)
        if close_application and not bool(getattr(application, "_closed", False)):
            close = getattr(application, "close", None)
            if callable(close):
                close()
    return 0


def _load_gui_dependencies() -> SimpleNamespace:
    try:
        qasync = importlib.import_module("qasync")
        core = importlib.import_module("PySide6.QtCore")
        gui = importlib.import_module("PySide6.QtGui")
        widgets = importlib.import_module("PySide6.QtWidgets")
    except ImportError as error:
        raise GuiUnavailableError(
            "The desktop GUI requires the optional PySide6 and qasync packages. "
            "Install JARVIS with its GUI extra, then run `jarvis gui` again."
        ) from error
    names = (
        "QAbstractItemView",
        "QApplication",
        "QDialog",
        "QDialogButtonBox",
        "QFrame",
        "QHeaderView",
        "QHBoxLayout",
        "QLabel",
        "QLineEdit",
        "QListWidget",
        "QListWidgetItem",
        "QMainWindow",
        "QMessageBox",
        "QMenu",
        "QPlainTextEdit",
        "QInputDialog",
        "QPushButton",
        "QSplitter",
        "QStackedWidget",
        "QStyle",
        "QSystemTrayIcon",
        "QTableWidget",
        "QTableWidgetItem",
        "QTextBrowser",
        "QVBoxLayout",
        "QWidget",
    )
    namespace = {name: getattr(widgets, name) for name in names}
    namespace.update(
        {
            "qasync": qasync,
            "Qt": core.Qt,
            "QTimer": core.QTimer,
            "QAction": gui.QAction,
        }
    )
    return SimpleNamespace(**namespace)


def _html(value: object) -> str:
    import html

    return html.escape(str(value), quote=True)


_PAGE_COLUMNS: dict[Page, tuple[tuple[str, str], ...]] = {
    Page.MEMORY: (
        ("category", "Category"),
        ("key", "Key"),
        ("value", "Value"),
        ("updated_at", "Updated"),
    ),
    Page.TASKS: (
        ("id", "ID"),
        ("message", "Reminder"),
        ("due_at", "Due"),
        ("recurrence", "Recurrence"),
        ("status", "Status"),
    ),
    Page.INTEGRATIONS: (
        ("name", "Integration"),
        ("provider", "Provider"),
        ("status", "Status"),
        ("detail", "Detail"),
    ),
    Page.PLUGINS: (
        ("name", "Plugin"),
        ("version", "Version"),
        ("status", "Status"),
        ("description", "Description"),
    ),
    Page.SETTINGS: (
        ("section", "Section"),
        ("key", "Setting"),
        ("value", "Value"),
        ("redacted", "Redacted"),
    ),
    Page.LOGS: (
        ("timestamp", "Time"),
        ("level", "Level"),
        ("logger", "Logger"),
        ("message", "Message"),
    ),
}

_BASE_STYLE = """
QMainWindow { font-size: 13px; }
#header { padding: 8px; }
#brand { font-size: 21px; font-weight: 700; letter-spacing: 2px; }
#pageTitle { font-size: 22px; font-weight: 650; }
#sectionTitle { font-size: 15px; font-weight: 600; }
#navigation { border: 0; padding: 8px; }
#navigation::item { padding: 11px 14px; border-radius: 6px; }
#navigation::item:selected { background: #3b82f6; color: white; }
QPushButton { min-height: 30px; padding: 2px 14px; border-radius: 5px; }
QPlainTextEdit, QListWidget, QTableWidget, QTextBrowser { border-radius: 6px; }
QLabel[state="working"], QLabel[state="awaiting_permission"] { color: #d97706; }
QLabel[state="error"] { color: #dc2626; }
"""

_DARK_STYLE = """
QMainWindow, QWidget { background: #111827; color: #e5e7eb; }
#header, #navigation { background: #0b1220; }
QPlainTextEdit, QListWidget, QTableWidget, QTextBrowser {
  background: #172033; color: #e5e7eb; border: 1px solid #334155;
}
QHeaderView::section { background: #1e293b; color: #e5e7eb; padding: 6px; }
QPushButton { background: #2563eb; color: white; border: 0; }
QPushButton:disabled { background: #374151; color: #9ca3af; }
"""

_LIGHT_STYLE = """
QMainWindow, QWidget { background: #f8fafc; color: #0f172a; }
#header, #navigation { background: #ffffff; }
QPlainTextEdit, QListWidget, QTableWidget, QTextBrowser {
  background: #ffffff; color: #0f172a; border: 1px solid #dbe3ee;
}
QHeaderView::section { background: #eef2f7; color: #0f172a; padding: 6px; }
QPushButton { background: #2563eb; color: white; border: 0; }
QPushButton:disabled { background: #cbd5e1; color: #64748b; }
"""
