"""Non-blocking, fail-closed permission bridge for graphical interfaces."""

from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from jarvis.gui.models import PermissionPrompt, clean_text

PromptListener: TypeAlias = Callable[[tuple[PermissionPrompt, ...]], None | object]


@dataclass(slots=True)
class _PendingDecision:
    prompt: PermissionPrompt
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[bool]
    decision_scheduled: bool = False


class GuiPermissionBroker:
    """Adapt core permission callbacks to asynchronous, explicit GUI decisions.

    The broker never defaults to approval. Closing the UI, timing out, cancelling
    the request, receiving an invalid result, or encountering an observer error
    all leave the action denied. ``resolve`` is thread-safe for adapters whose UI
    callback is delivered outside the asyncio loop thread.
    """

    def __init__(self, *, timeout_seconds: float | None = 300.0) -> None:
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("permission timeout must be a positive finite number or None")
        self.timeout_seconds = None if timeout_seconds is None else float(timeout_seconds)
        self._pending: dict[str, _PendingDecision] = {}
        self._listeners: list[PromptListener] = []
        self._closed = False

    @property
    def pending(self) -> tuple[PermissionPrompt, ...]:
        """Return immutable pending prompts in creation order."""

        return tuple(item.prompt for item in self._pending.values())

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(self, listener: PromptListener) -> Callable[[], bool]:
        """Observe pending-prompt snapshots; observer failures are isolated."""

        if not callable(listener):
            raise TypeError("permission listener must be callable")
        if listener not in self._listeners:
            self._listeners.append(listener)

        def unsubscribe() -> bool:
            if listener not in self._listeners:
                return False
            self._listeners.remove(listener)
            return True

        return unsubscribe

    async def confirm(self, request: Any) -> bool:
        """Core ``Confirmer`` callback: wait without blocking the event loop."""

        if self._closed:
            return False
        try:
            prompt = PermissionPrompt(
                id=uuid.uuid4().hex,
                risk_level=_risk_text(request),
                action_name=request.action_name,
                summary=request.summary,
                details=request.details,
            )
        except Exception:
            return False

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[prompt.id] = _PendingDecision(prompt, loop, future)
        self._notify()
        try:
            if self.timeout_seconds is None:
                return (await future) is True
            return (
                await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout_seconds)
            ) is True
        except (TimeoutError, asyncio.CancelledError):
            current_task = asyncio.current_task()
            if isinstance(current_task, asyncio.Task) and current_task.cancelling():
                raise
            return False
        except Exception:
            return False
        finally:
            pending = self._pending.pop(prompt.id, None)
            if pending is not None and not pending.future.done():
                pending.future.cancel()
            self._notify()

    def resolve(self, prompt_id: str, approved: bool) -> bool:
        """Resolve one prompt exactly once; invalid or unknown IDs do nothing."""

        if not isinstance(approved, bool):
            raise TypeError("permission decision must be a boolean")
        identifier = clean_text(prompt_id, limit=128, collapse_whitespace=True)
        pending = self._pending.get(identifier)
        if pending is None or pending.future.done() or pending.decision_scheduled:
            return False
        pending.decision_scheduled = True
        pending.loop.call_soon_threadsafe(_set_decision, pending.future, approved is True)
        return True

    def deny_all(self) -> int:
        """Fail closed for every pending prompt and return the affected count."""

        count = 0
        for prompt_id in tuple(self._pending):
            if self.resolve(prompt_id, False):
                count += 1
        return count

    def close(self) -> None:
        """Permanently reject new confirmations and deny all pending ones."""

        self._closed = True
        self.deny_all()

    def _notify(self) -> None:
        snapshot = self.pending
        for listener in tuple(self._listeners):
            try:
                result = listener(snapshot)
                if inspect.isawaitable(result):
                    # Listeners are presentation notifications, never part of the
                    # permission decision. Run async observers independently.
                    try:
                        asyncio.ensure_future(result)
                    except RuntimeError:
                        close = getattr(result, "close", None)
                        if callable(close):
                            close()
            except Exception:
                continue


def _risk_text(request: Any) -> str:
    value = request.risk_level
    raw = getattr(value, "value", value)
    text = clean_text(raw, limit=50, collapse_whitespace=True)
    if not text:
        raise ValueError("permission risk level cannot be empty")
    return text


def _set_decision(future: asyncio.Future[bool], approved: bool) -> None:
    if not future.done():
        future.set_result(approved is True)
