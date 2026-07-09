import json
from types import SimpleNamespace
from typing import Any, cast

from paypal.analytics import (
    _DD_AUTHCHALLENGE_CONFIG,
    _DD_HAGRID_CONFIG,
    _DD_MODXO_CONFIG,
    _DD_WEASLEY_CONFIG,
    send_datadog_rum_action,
    send_datadog_rum_view,
)


class FakeSession:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            ba_token="BA-TESTTOKEN123",
            ec_token="EC-TESTTOKEN123",
            signup_url="https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
            datadog_session_id="session-hex-123",
            datadog_view_ids={},
            datadog_tab_id="tab-uuid-123",
            browser_profile={
                "connection_effective_type": "3g",
                "sec_ch_platform": '"Windows"',
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            },
            viewport={"width": 975, "height": 703},
        )
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> None:
        self.posts.append((url, kwargs))


def rum_events(session: FakeSession) -> list[dict[str, Any]]:
    assert session.posts
    body = str(session.posts[-1][1]["content"])
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def rum_event(session: FakeSession, event_type: str) -> dict[str, Any]:
    for event in rum_events(session):
        if event.get("type") == event_type:
            return event
    raise AssertionError(f"missing Datadog event type {event_type}")


def test_datadog_rum_view_includes_browser_context() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        referrer="https://www.paypal.com/pay/?token=BA-TESTTOKEN123",
        api="fetch",
    )

    url, kwargs = session.posts[-1]
    params = cast(dict[str, str], kwargs["params"])
    headers = cast(dict[str, str], kwargs["headers"])
    event = rum_event(session, "view")
    event_types = {event["type"] for event in rum_events(session)}

    assert url == "https://browser-intake-us5-datadoghq.com/api/v2/rum"
    assert params["ddtags"] == "sdk_version:5.35.1,api:fetch,service:weasley(checkoutuinodeweb),version:ebcfab6"
    assert "_dd.api" not in params
    assert headers["Accept"] == "*/*"
    assert headers["Referer"] == "https://www.paypal.com/pay/?token=BA-TESTTOKEN123"
    assert headers["User-Agent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    assert headers["sec-ch-ua-platform"] == '"Windows"'
    assert {"resource", "long_task", "view"}.issubset(event_types)
    assert event["source"] == "browser"
    assert "ddtags" not in event
    assert event["version"] == "ebcfab6"
    assert event["tab"] == {"id": "tab-uuid-123"}
    assert event["connectivity"] == {
        "status": "connected",
        "interfaces": ["unknown"],
        "effective_type": "3g",
    }
    assert event["display"]["viewport"] == {"width": 975, "height": 703}
    assert event["display"]["scroll"] == {
        "max_depth": 703,
        "max_depth_scroll_top": 0,
        "max_scroll_height": 703,
        "max_scroll_height_time": 0,
    }
    assert event["feature_flags"]["datadog-browser-sdk-v7-enabled"] is True
    assert event["_dd"]["sdk_name"] == "rum"
    assert event["_dd"]["configuration"] == {
        "session_sample_rate": 100,
        "session_replay_sample_rate": 0,
        "start_session_replay_recording_manually": True,
        "profiling_sample_rate": 0,
        "trace_sample_rate": 100,
        "beta_encode_cookie_options": False,
    }
    assert event["session"]["sampled_for_replay"] is False
    assert event["_dd"]["document_version"] == 1
    assert event["_dd"]["page_states"] == [{"start": 0, "state": "active"}]
    assert event["view"]["referrer"] == "https://www.paypal.com/pay/?token=BA-TESTTOKEN123"
    assert event["view"]["in_foreground"] is True
    assert event["view"]["loading_type"] == "initial_load"


def test_datadog_rum_action_includes_headers_and_context_without_replay() -> None:
    session = FakeSession()

    send_datadog_rum_action(
        session,
        "signup_form_fill",
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        referrer="https://www.paypal.com/pay/?token=BA-TESTTOKEN123",
        api="xhr",
    )

    _url, kwargs = session.posts[-1]
    params = cast(dict[str, str], kwargs["params"])
    headers = cast(dict[str, str], kwargs["headers"])
    event = rum_event(session, "action")
    event_types = {event["type"] for event in rum_events(session)}

    assert params["ddtags"] == "sdk_version:5.35.1,api:xhr,service:weasley(checkoutuinodeweb),version:ebcfab6"
    assert "_dd.api" not in params
    assert headers["Referer"] == "https://www.paypal.com/pay/?token=BA-TESTTOKEN123"
    assert {"resource", "action"}.issubset(event_types)
    assert event["source"] == "browser"
    assert "ddtags" not in event
    assert event["version"] == "ebcfab6"
    assert event["view"]["referrer"] == "https://www.paypal.com/pay/?token=BA-TESTTOKEN123"
    assert event["view"]["in_foreground"] is True
    assert event["action"]["frustration"] == {"count": 0, "type": []}
    assert event["context"]["token"] == "EC-TESTTOKEN123"
    assert event["context"]["ba_token"] == "BA-TESTTOKEN123"
    assert all("/api/v2/replay" not in post_url for post_url, _kwargs in session.posts)


def test_hagrid_datadog_config_matches_roxy_shape() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/webapps/hermes?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_HAGRID_CONFIG,
        api="fetch",
    )

    event = rum_event(session, "view")
    params = cast(dict[str, str], session.posts[-1][1]["params"])

    assert _DD_HAGRID_CONFIG == {
        "client_token": "pub0d65d12a15f063f50a51b21d246ce62c",
        "app_id": "bb542bc3-8372-49a6-8db7-725f60a5cc7a",
        "service": "hagrid",
        "sdk_version": "5.35.1",
        "version": "",
    }
    assert params["ddtags"] == "sdk_version:5.35.1,api:fetch,service:hagrid"
    assert "_dd.api" not in params
    assert "ddtags" not in event
    assert event["service"] == "hagrid"
    assert event["application"] == {"id": "bb542bc3-8372-49a6-8db7-725f60a5cc7a"}
    assert "version" not in event


def test_modxo_datadog_feature_flag_matches_roxy_shape() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )

    event = rum_event(session, "view")
    params = cast(dict[str, str], session.posts[-1][1]["params"])

    assert "ddtags" not in params
    assert params["_dd.api"] == "fetch"
    assert event["ddtags"] == "sdk_version:6.33.0,service:modxo,version:modularcheckoutnodeweb-0.506.0_2026070118325682"
    assert event["feature_flags"]["datadog-browser-sdk-v7-enabled"] is False
    assert event["feature_flags"]["routing-in-modxo-enabled"] is False
    assert event["feature_flags"]["pay-token-path-rewrite-enabled"] is False


def test_authchallenge_datadog_config_matches_roxy_shape() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/auth/validatecaptcha",
        "BA-TESTTOKEN123",
        dd_config=_DD_AUTHCHALLENGE_CONFIG,
        referrer="https://www.paypal.com/auth/validatecaptcha",
        api="fetch",
    )

    event = rum_event(session, "view")
    params = cast(dict[str, str], session.posts[-1][1]["params"])

    assert _DD_AUTHCHALLENGE_CONFIG == {
        "client_token": "pubbc14edcb954efe6e30dbd32fac3e7fd7",
        "app_id": "094d6ec4-6434-4564-a7aa-7283dd0d40f1",
        "service": "authchallengenodeweb",
        "sdk_version": "6.31.0",
        "version": "",
        "session_replay_sample_rate": 5,
    }
    assert params["dd-api-key"] == "pubbc14edcb954efe6e30dbd32fac3e7fd7"
    assert "ddtags" not in params
    assert params["_dd.api"] == "fetch"
    assert event["ddtags"] == "sdk_version:6.31.0,service:authchallengenodeweb"
    assert event["service"] == "authchallengenodeweb"
    assert event["application"] == {"id": "094d6ec4-6434-4564-a7aa-7283dd0d40f1"}
    assert event["_dd"]["configuration"] == {
        "session_sample_rate": 100,
        "session_replay_sample_rate": 5,
        "start_session_replay_recording_manually": False,
        "profiling_sample_rate": 0,
        "trace_sample_rate": 100,
        "beta_encode_cookie_options": False,
    }
    assert event["session"]["sampled_for_replay"] is False
    assert event["_dd"]["document_version"] == 1
    assert event["_dd"]["page_states"] == [{"start": 0, "state": "active"}]
    assert "feature_flags" not in event
    assert "version" not in event


def test_datadog_rum_resource_uses_sdk_like_timing_shape() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )

    resource = rum_event(session, "resource")["resource"]

    assert resource["method"] == "GET"
    assert resource["status_code"] == 200
    assert set(resource["first_byte"]) == {"start", "duration"}
    assert set(resource["download"]) == {"start", "duration"}
    assert resource["first_byte"]["start"] >= 0
    assert resource["first_byte"]["duration"] > 0
    assert resource["download"]["start"] >= resource["first_byte"]["start"]
    assert resource["download"]["duration"] >= 0


def test_datadog_rum_long_task_entry_type_matches_sdk_family() -> None:
    v5_session = FakeSession()
    v6_session = FakeSession()

    send_datadog_rum_view(
        v5_session,
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        api="fetch",
    )
    send_datadog_rum_view(
        v6_session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )

    assert rum_event(v5_session, "long_task")["long_task"]["entry_type"] == "long-task"
    assert rum_event(v6_session, "long_task")["long_task"]["entry_type"] == "long-animation-frame"


def test_datadog_rum_replay_sampling_follows_service_rate() -> None:
    modxo_session = FakeSession()
    hagrid_session = FakeSession()

    send_datadog_rum_view(
        modxo_session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )
    send_datadog_rum_view(
        hagrid_session,
        "https://www.paypal.com/webapps/hermes?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_HAGRID_CONFIG,
        api="fetch",
    )

    modxo = rum_event(modxo_session, "view")
    hagrid = rum_event(hagrid_session, "view")

    assert modxo["session"]["sampled_for_replay"] is True
    assert modxo["_dd"]["configuration"]["start_session_replay_recording_manually"] is False
    assert hagrid["session"]["sampled_for_replay"] is False
    assert hagrid["_dd"]["configuration"]["start_session_replay_recording_manually"] is True


def test_datadog_rum_resource_includes_connection_phase_timings() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )

    resource = rum_event(session, "resource")["resource"]

    for phase in ("dns", "connect", "ssl"):
        assert set(resource[phase]) == {"start", "duration"}
        assert resource[phase]["start"] >= 0
        assert resource[phase]["duration"] >= 0
    assert resource["dns"]["start"] <= resource["connect"]["start"] <= resource["first_byte"]["start"]
    assert resource["ssl"]["start"] <= resource["first_byte"]["start"]


def test_datadog_rum_view_includes_sdk_paint_layout_metrics() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )

    view = rum_event(session, "view")["view"]

    assert view["loading_time"] >= 0
    assert view["first_byte"] >= 0
    assert view["first_contentful_paint"] >= view["first_byte"]
    assert view["largest_contentful_paint"] >= view["first_contentful_paint"]
    assert view["largest_contentful_paint_target_selector"] == "body"
    assert view["cumulative_layout_shift"] == 0
    assert view["performance"]["fcp"]["timestamp"] == view["first_contentful_paint"]
    assert view["performance"]["lcp"]["timestamp"] == view["largest_contentful_paint"]
    assert view["performance"]["lcp"]["target_selector"] == "body"
    assert set(view["performance"]["lcp"]["sub_parts"]) == {"load_delay", "load_time", "render_delay"}


def test_datadog_rum_v6_long_task_includes_animation_frame_details() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )

    long_task = rum_event(session, "long_task")["long_task"]

    assert long_task["entry_type"] == "long-animation-frame"
    assert long_task["start_time"] >= 0
    assert long_task["blocking_duration"] >= 0
    assert long_task["render_start"] >= long_task["start_time"]
    assert long_task["style_and_layout_start"] >= long_task["render_start"]
    assert long_task["first_ui_event_timestamp"] == 0
    assert len(long_task["scripts"]) == 1
    script = long_task["scripts"][0]
    assert script["invoker"] == "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1"
    assert script["invoker_type"] == "classic-script"
    assert script["source_url"] == "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1"
    assert script["window_attribution"] == "self"
    assert script["duration"] >= 0


def test_datadog_rum_action_includes_context_source_and_frustration_type() -> None:
    session = FakeSession()

    send_datadog_rum_action(
        session,
        "signup_form_fill",
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        referrer="https://www.paypal.com/pay/?token=BA-TESTTOKEN123",
        api="xhr",
    )

    action = rum_event(session, "action")

    assert action["context"]["source"] == "paypal-checkout"
    assert action["action"]["frustration"] == {"count": 0, "type": []}


def test_datadog_rum_sampled_replay_metadata_stays_rum_only() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/pay/?token=BA-TESTTOKEN123&ul=1",
        "BA-TESTTOKEN123",
        dd_config=_DD_MODXO_CONFIG,
        api="fetch",
    )

    event = rum_event(session, "view")

    assert event["session"]["has_replay"] is True
    assert event["_dd"]["replay_stats"] == {
        "records_count": 4,
        "segments_count": 1,
        "segments_total_raw_size": 0,
    }
    assert all("/api/v2/replay" not in post_url for post_url, _kwargs in session.posts)


def test_datadog_rum_view_resource_and_long_task_use_empty_action_id_arrays() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        api="fetch",
    )

    resource = rum_event(session, "resource")
    long_task = rum_event(session, "long_task")

    assert resource["action"] == {"id": []}
    assert long_task["action"] == {"id": []}


def test_datadog_rum_action_resource_links_to_current_action_id_array() -> None:
    session = FakeSession()

    send_datadog_rum_action(
        session,
        "signup_form_fill",
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        referrer="https://www.paypal.com/pay/?token=BA-TESTTOKEN123",
        api="xhr",
    )

    resource = rum_event(session, "resource")
    action = rum_event(session, "action")

    assert isinstance(action["action"]["id"], str)
    assert resource["action"] == {"id": [action["action"]["id"]]}


def test_weasley_datadog_context_matches_roxy_release_fields() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        api="fetch",
    )

    event = rum_event(session, "view")

    assert event["context"]["is_basl"] is False
    assert event["context"]["weasley_init_corr_id"] == "EC-TESTTOKEN123"
    assert event["context"]["weasley_release_hash"] == "ebcfab6"
    assert event["context"]["weasley_release_date"] == _DD_WEASLEY_CONFIG["release_date"]


def test_datadog_resource_includes_redirect_timing_shape() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        api="fetch",
    )

    resource = rum_event(session, "resource")["resource"]

    assert resource["redirect"] == {"start": 0, "duration": 0}


def test_datadog_action_includes_roxy_target_geometry() -> None:
    session = FakeSession()

    send_datadog_rum_action(
        session,
        "signup_form_fill",
        "https://www.paypal.com/checkoutweb/signup?token=EC-TESTTOKEN123",
        dd_config=_DD_WEASLEY_CONFIG,
        referrer="https://www.paypal.com/pay/?token=BA-TESTTOKEN123",
        api="xhr",
    )

    action = rum_event(session, "action")

    assert action["_dd"]["action"] == {
        "target": {
            "width": 1,
            "height": 1,
            "selector": "[data-datadog-action-name=\"signup_form_fill\"]",
        },
        "position": {"x": 0, "y": 0},
    }


def test_datadog_view_includes_interaction_to_next_paint() -> None:
    session = FakeSession()

    send_datadog_rum_view(
        session,
        "https://www.paypal.com/webapps/hermes?token=EC-TESTTOKEN123",
        "BA-TESTTOKEN123",
        dd_config=_DD_HAGRID_CONFIG,
        api="fetch",
    )

    view = rum_event(session, "view")["view"]

    assert view["interaction_to_next_paint"] >= 0
    assert view["interaction_to_next_paint_time"] >= 0
    assert view["interaction_to_next_paint_target_selector"] == "body"
