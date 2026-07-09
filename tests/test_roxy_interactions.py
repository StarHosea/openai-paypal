import random
from typing import Any

import paypal.roxy_fingerprint as roxy_module
from paypal.roxy_fingerprint import (
    RoxyApiClient,
    RoxyCaptureConfig,
    _build_roxy_interaction_plan,
    _execute_roxy_interaction_plan,
    close_roxy_browser,
)


def test_roxy_interaction_plan_is_seeded_and_varied() -> None:
    settings = {
        "enabled": True,
        "moves_min": 6,
        "moves_max": 6,
        "wheels_min": 2,
        "wheels_max": 2,
        "think_min_ms": 10,
        "think_max_ms": 10,
        "pause_min_ms": 5,
        "pause_max_ms": 20,
    }
    plan_a = _build_roxy_interaction_plan(567, 700, rng=random.Random(1), settings=settings)
    plan_b = _build_roxy_interaction_plan(567, 700, rng=random.Random(2), settings=settings)

    assert plan_a != plan_b
    assert sum(1 for item in plan_a if item["type"] == "move") >= 6
    assert sum(1 for item in plan_a if item["type"] == "wheel") == 2
    assert {item["type"] for item in plan_a} <= {"pause", "move", "wheel"}
    for item in plan_a:
        if item["type"] == "move":
            assert 18 <= int(item["x"]) <= 567 - 18
            assert 18 <= int(item["y"]) <= 700 - 18
            assert int(item["steps"]) >= 1
        assert int(item.get("ms", 0)) >= 0


def test_execute_roxy_interaction_plan_uses_only_mouse_and_waits() -> None:
    class Mouse:
        def __init__(self) -> None:
            self.moves: list[tuple[int, int, int]] = []
            self.wheels: list[tuple[int, int]] = []

        def move(self, x: int, y: int, *, steps: int) -> None:
            self.moves.append((x, y, steps))

        def wheel(self, dx: int, dy: int) -> None:
            self.wheels.append((dx, dy))

    class Page:
        def __init__(self) -> None:
            self.mouse = Mouse()
            self.waits: list[int] = []

        def wait_for_timeout(self, ms: int) -> None:
            self.waits.append(ms)

    page = Page()
    summary = _execute_roxy_interaction_plan(
        page,
        [
            {"type": "pause", "ms": 10},
            {"type": "move", "x": 100, "y": 110, "steps": 4, "ms": 15},
            {"type": "wheel", "dx": 0, "dy": 180, "ms": 20},
        ],
    )

    assert page.mouse.moves == [(100, 110, 4)]
    assert page.mouse.wheels == [(0, 180)]
    assert page.waits == [10, 15, 20]
    assert summary == {"moves": 1, "wheels": 1, "pauses": 1, "total_pause_ms": 45}


def test_roxy_workspace_retries_current_desktop_api_key(monkeypatch: Any) -> None:
    config = RoxyCaptureConfig(
        api_base="http://127.0.0.1:50000",
        api_key="old-api-key",
        force_temp_workspace=False,
    )
    client = RoxyApiClient(config)
    calls: list[str] = []

    def fake_request(method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(client.config.api_key)
        if len(calls) == 1:
            return {"code": 0, "data": {"rows": []}}
        return {
            "code": 0,
            "data": {
                "rows": [
                    {
                        "id": 132898,
                        "project_details": [{"projectId": 142551}],
                        "workspaceName": "Desktop Team",
                    }
                ]
            },
        }

    monkeypatch.setattr(client, "request", fake_request)
    monkeypatch.setattr(roxy_module, "_load_roxy_public_api_key", lambda: "desktop-api-key")

    try:
        workspace_id, project_id = client.get_workspace_project()
    finally:
        client.close()

    assert (workspace_id, project_id) == (132898, 142551)
    assert calls == ["old-api-key", "desktop-api-key"]
    assert client.config.api_key == "desktop-api-key"
    assert client.auto_workspace_created is False


def test_roxy_workspace_does_not_auto_create_team_when_disabled(monkeypatch: Any) -> None:
    config = RoxyCaptureConfig(
        api_base="http://127.0.0.1:50000",
        api_key="api-key",
        force_temp_workspace=True,
        auto_create_workspace=False,
    )
    client = RoxyApiClient(config)

    monkeypatch.setattr(client, "request", lambda *_args, **_kwargs: {"code": 0, "data": {"rows": []}})
    monkeypatch.setattr(client, "_retry_workspace_with_roxy_app_api_key", lambda: [])
    monkeypatch.setattr(client, "list_app_workspaces", lambda: [])

    create_calls = 0

    def fake_create_workspace() -> tuple[int, str]:
        nonlocal create_calls
        create_calls += 1
        raise AssertionError("should not create a Roxy team/workspace by default")

    try:
        monkeypatch.setattr(client, "create_workspace", fake_create_workspace)
        try:
            client.get_workspace_project()
        except roxy_module.RoxyFingerprintError as exc:
            assert "跳过自动创建团队" in str(exc)
        else:
            raise AssertionError("expected RoxyFingerprintError")
    finally:
        client.close()

    assert create_calls == 0
    assert client.auto_workspace_created is False


def test_roxy_workspace_auto_create_when_explicitly_enabled(monkeypatch: Any) -> None:
    config = RoxyCaptureConfig(
        api_base="http://127.0.0.1:50000",
        api_key="api-key",
        force_temp_workspace=False,
        auto_create_workspace=True,
    )
    client = RoxyApiClient(config)

    monkeypatch.setattr(client, "request", lambda *_args, **_kwargs: {"code": 0, "data": {"rows": []}})
    monkeypatch.setattr(client, "_retry_workspace_with_roxy_app_api_key", lambda: [])
    monkeypatch.setattr(client, "list_app_workspaces", lambda: [])

    def fake_create_workspace() -> tuple[int, str]:
        client.auto_workspace_created = True
        client.auto_workspace_id = 24680
        client.auto_workspace_name = "paypal-auto-test"
        return 24680, "paypal-auto-test"

    monkeypatch.setattr(client, "create_workspace", fake_create_workspace)

    try:
        workspace_id, project_id = client.get_workspace_project()
    finally:
        client.close()

    assert (workspace_id, project_id) == (24680, None)
    assert client.auto_workspace_created is True
    assert client.auto_workspace_name == "paypal-auto-test"


def test_close_roxy_browser_deletes_only_auto_workspace(monkeypatch: Any) -> None:
    events: list[tuple[str, int | str]] = []

    class FakeClient:
        def __init__(self, _config: RoxyCaptureConfig) -> None:
            pass

        def close_profile(self, dir_id: str) -> None:
            events.append(("close_profile", dir_id))

        def delete_profile(self, workspace_id: int, dir_id: str) -> None:
            events.append(("delete_profile", workspace_id))
            events.append(("delete_profile_dir", dir_id))

        def delete_workspace(self, workspace_id: int, workspace_name: str = "") -> None:
            events.append(("delete_workspace", workspace_id))
            events.append(("delete_workspace_name", workspace_name))

        def close(self) -> None:
            events.append(("close_client", ""))

    monkeypatch.setattr(roxy_module, "RoxyApiClient", FakeClient)
    monkeypatch.setattr(
        roxy_module,
        "load_roxy_capture_config",
        lambda: RoxyCaptureConfig(api_base="http://127.0.0.1:50000", api_key="api-key"),
    )

    close_roxy_browser(
        {
            "workspace_id": 111,
            "workspace_created": True,
            "workspace_name": "paypal-auto-test",
            "dir_id": "dir-1",
        },
        delete=True,
    )

    assert ("delete_workspace", 111) in events
    assert ("delete_workspace_name", "paypal-auto-test") in events

    events.clear()
    close_roxy_browser(
        {
            "workspace_id": 222,
            "workspace_created": False,
            "workspace_name": "existing",
            "dir_id": "dir-2",
        },
        delete=True,
    )

    assert ("delete_profile", 222) in events
    assert not any(name == "delete_workspace" for name, _value in events)
