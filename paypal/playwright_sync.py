"""Small guards for using Playwright's synchronous API safely.

The synchronous Playwright API cannot be entered while an asyncio loop is
already active on the calling thread.  That also happens when a previously
started synchronous Playwright session is still alive: its dispatcher owns the
thread's running loop.  Browser operations that create their own Playwright
manager can use this module to move just that isolated operation to a short
lived worker thread.
"""
from __future__ import annotations

import asyncio
import contextvars
import re
import threading
from collections.abc import Callable
from types import TracebackType
from typing import TypeVar, cast


_T = TypeVar("_T")


def sync_playwright_needs_worker_thread() -> bool:
    """Return whether this thread cannot enter ``sync_playwright()`` now."""
    try:
        return bool(asyncio.get_running_loop().is_running())
    except RuntimeError:
        return False


def run_sync_playwright_operation(
    operation: Callable[[], _T],
    *,
    label: str = "playwright",
) -> _T:
    """Run an isolated synchronous Playwright operation in a safe thread.

    Normal CLI execution stays on the current thread.  When the caller is
    inside an asyncio loop (including an already-active sync Playwright
    dispatcher), the operation is run to completion in a fresh thread with a
    separate event loop.  Context variables are copied so job-scoped logging
    remains attached to the originating flow.
    """
    if not sync_playwright_needs_worker_thread():
        return operation()

    outcome: dict[str, object] = {}
    context = contextvars.copy_context()

    def invoke() -> None:
        try:
            outcome["value"] = operation()
        except BaseException as exc:  # propagate the original browser error
            outcome["error"] = exc
            outcome["traceback"] = exc.__traceback__

    def target() -> None:
        try:
            context.run(invoke)
        except BaseException as exc:  # pragma: no cover - defensive fallback
            outcome["error"] = exc
            outcome["traceback"] = exc.__traceback__

    name = re.sub(r"[^0-9A-Za-z_-]+", "-", label).strip("-") or "playwright"
    worker = threading.Thread(target=target, name=f"paypal-{name}", daemon=False)
    worker.start()
    worker.join()

    error = outcome.get("error")
    if isinstance(error, BaseException):
        traceback = outcome.get("traceback")
        raise error.with_traceback(cast(TracebackType | None, traceback))
    return cast(_T, outcome.get("value"))
