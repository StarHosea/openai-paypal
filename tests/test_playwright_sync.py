from __future__ import annotations

import asyncio
import threading
from typing import Any

import paypal.local_headless as local_headless
import paypal.roxy_fingerprint as roxy_fingerprint


def test_local_headless_fingerprint_moves_out_of_running_asyncio_loop(monkeypatch: Any) -> None:
    caller_thread = threading.get_ident()

    def fake_capture(**_kwargs: object) -> dict[str, int]:
        return {"thread": threading.get_ident()}

    monkeypatch.setattr(
        local_headless,
        "_capture_runtime_fingerprint_with_local_headless_direct",
        fake_capture,
    )

    async def run() -> dict[str, int]:
        return local_headless.capture_runtime_fingerprint_with_local_headless()

    result = asyncio.run(run())
    assert result["thread"] != caller_thread


def test_roxy_cdp_capture_moves_out_of_running_asyncio_loop(monkeypatch: Any) -> None:
    caller_thread = threading.get_ident()

    def fake_evaluate(
        _cdp_info: dict[str, object],
        _timeout_ms: int,
        *,
        close_browser: bool = True,
    ) -> dict[str, object]:
        return {"thread": threading.get_ident(), "close_browser": close_browser}

    monkeypatch.setattr(roxy_fingerprint, "_evaluate_cdp_fingerprint_direct", fake_evaluate)

    async def run() -> dict[str, object]:
        return roxy_fingerprint._evaluate_cdp_fingerprint(
            {"http": "127.0.0.1:9222"},
            1_000,
            close_browser=False,
        )

    result = asyncio.run(run())
    assert result["thread"] != caller_thread
    assert result["close_browser"] is False


def test_one_shot_phase_runner_moves_out_of_running_asyncio_loop(monkeypatch: Any) -> None:
    caller_thread = threading.get_ident()

    def fake_run(
        _page_url: str,
        **_kwargs: object,
    ) -> dict[str, int]:
        return {"thread": threading.get_ident()}

    monkeypatch.setattr(local_headless, "_run_local_headless_mtr_phase1_direct", fake_run)

    async def run() -> dict[str, int]:
        return local_headless.run_local_headless_mtr_phase1(
            "https://www.paypal.com/checkoutweb/signup",
            dfp_config={},
            dfp_script_url="",
            session=None,
            run_mtr=False,
        )

    result = asyncio.run(run())
    assert result["thread"] != caller_thread
