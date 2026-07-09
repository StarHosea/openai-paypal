from collections.abc import Callable
from typing import Any, Protocol, cast

import paypal.local_headless as local_headless


class MarkObservabilityAsDatadog(Protocol):
    def __call__(self, events: dict[str, object], *, reason: str) -> bool: ...


class HeadlessRequestDecision(Protocol):
    action: str
    reason: str
    family: str


class SeedHeadlessOptimizedRules(Protocol):
    def __call__(self, stage: str = "checkout") -> list[object]: ...


class HeadlessOptimizedRequestDecision(Protocol):
    def __call__(
        self,
        url: str,
        *,
        method: str,
        resource_type: str,
        rules: list[object],
        fail_open: bool = False,
    ) -> HeadlessRequestDecision: ...


headless_phase1_family = cast(Callable[[str], str], getattr(local_headless, "_headless_phase1_family"))
mark_observability_as_datadog = cast(
    MarkObservabilityAsDatadog,
    getattr(local_headless, "_headless_mark_paypal_observability_as_datadog"),
)
seed_headless_optimized_rules = cast(
    SeedHeadlessOptimizedRules,
    getattr(local_headless, "_seed_headless_optimized_rules"),
)
headless_optimized_request_decision = cast(
    HeadlessOptimizedRequestDecision,
    getattr(local_headless, "_headless_optimized_request_decision"),
)


def signup_context_decision(url: str, *, method: str, resource_type: str) -> HeadlessRequestDecision:
    return headless_optimized_request_decision(
        url,
        method=method,
        resource_type=resource_type,
        rules=seed_headless_optimized_rules("signup_context"),
    )


def test_current_paypal_browser_telemetry_urls_are_observability() -> None:
    assert headless_phase1_family("https://www.paypal.com/signin/client-log") == "observability"
    assert headless_phase1_family("https://t.paypal.com/ts?v=1.15.0&fpti_sdk_name=pa-js") == "observability"


def test_paypal_observability_can_fulfill_datadog_required_slot() -> None:
    events: dict[str, object] = {
        "counts": {"observability": 2, "datadog_rum": 0},
        "response_counts": {"observability": 2, "datadog_rum": 0},
        "observed_order": ["observability"],
        "runtime_signals": [],
    }

    fulfilled = mark_observability_as_datadog(
        events,
        reason="test_observability_fallback",
    )

    counts = cast(dict[str, object], events["counts"])
    response_counts = cast(dict[str, object], events["response_counts"])
    runtime_signals = cast(list[object], events["runtime_signals"])

    assert fulfilled is True
    assert counts["datadog_rum"] == 1
    assert response_counts["datadog_rum"] == 2
    assert events["datadog_runtime_fulfilled"] is True
    assert events["datadog_runtime_fulfilled_reason"] == "test_observability_fallback"
    assert cast(list[object], events["observed_order"])[-1] == "datadog_rum"
    assert cast(dict[str, object], runtime_signals[-1])["source"] == "paypal_observability"


def test_headless_debug_log_is_disabled_by_default(monkeypatch: Any, tmp_path: Any) -> None:
    for name in (
        "PAYPAL_HEADLESS_DEBUG",
        "PAYPAL_HEADLESS_OPTIMIZED_DEBUG",
        "PAYPAL_HEADLESS_DEBUG_RAW",
        "PAYPAL_HEADLESS_OPTIMIZED_DEBUG_RAW",
        "PAYPAL_HEADLESS_DEBUG_DIR",
        "PAYPAL_HEADLESS_OPTIMIZED_DEBUG_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(local_headless, "_headless_optimized_debug_root", lambda: tmp_path)

    network_log = local_headless.HeadlessOptimizedNetworkLog(job_id="prod-no-debug")
    network_log.record({"event": "request", "url": "https://example.test"})

    assert network_log.path == ""
    assert network_log.write_raw(label="body.bin", content=b"payload") == ""
    assert not (tmp_path / "prod-no-debug").exists()


def test_paypal_observability_route_urls_can_fulfill_datadog_after_counter_reset() -> None:
    events: dict[str, object] = {
        "counts": {"observability": 0, "datadog_rum": 0},
        "response_counts": {"observability": 0, "datadog_rum": 0},
        "observed_order": ["ddbm", "identity_di_log"],
        "runtime_signals": [],
        "allowed_requests": [
            {
                "event": "route",
                "url": "https://www.paypal.com/signin/client-log",
                "method": "POST",
                "resource_type": "xhr",
            },
            {
                "event": "route",
                "url": "https://t.paypal.com/ts?v=1.15.0&fpti_sdk_name=pa-js",
                "method": "GET",
                "resource_type": "fetch",
            },
        ],
    }

    fulfilled = mark_observability_as_datadog(
        events,
        reason="test_route_url_fallback",
    )

    counts = cast(dict[str, object], events["counts"])
    runtime_signals = cast(list[object], events["runtime_signals"])

    assert fulfilled is True
    assert counts["datadog_rum"] == 1
    assert cast(list[object], events["observed_order"])[-1] == "datadog_rum"
    assert cast(dict[str, object], runtime_signals[-1])["observability_count"] == 2


def test_signup_context_identity_di_log_can_be_triggered_from_browser_runtime() -> None:
    class Page:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def evaluate(self, expression: str, arg: object = None) -> object:
            self.calls.append((expression, arg))
            return {"ok": True, "status": 204, "url": "https://www.paypal.com/identity/di/log"}

    session = local_headless.LocalHeadlessSession(runtime="roxy")
    page = Page()
    trigger = cast(Callable[..., dict[str, object]], getattr(session, "_trigger_identity_di_log"))

    result = trigger(
        page,
        app_id="CHECKOUTUINODEWEB_ONBOARDING_LITE",
        correlation_id="EC-TEST123",
        reason="test_roxy_browser_fetch",
    )

    counts = cast(dict[str, object], session.events["counts"])
    runtime_signals = cast(list[object], session.events["runtime_signals"])

    assert result["ok"] is True
    assert counts["identity_di_log"] == 1
    assert "identity_di_log" in cast(list[object], session.events["observed_order"])
    assert cast(dict[str, object], runtime_signals[-1])["source"] == "browser_fetch"
    assert cast(dict[str, object], page.calls[0][1])["correlationId"] == "EC-TEST123"


def test_signup_context_allows_checkout_graphql_fetches() -> None:
    decision = signup_context_decision(
        "https://www.paypal.com/graphql?CheckoutSessionDataQuery=",
        method="POST",
        resource_type="fetch",
    )

    assert decision.action == "allow"
    assert decision.reason == "signup_context_graphql"


def test_signup_context_allows_checkout_content_manifest_fetch() -> None:
    decision = signup_context_decision(
        "https://www.paypalobjects.com/checkoutweb/release/weasley/content-manifest.269d408d25fd72bcea4047a79fb8ff61.json",
        method="GET",
        resource_type="fetch",
    )

    assert decision.action == "allow"
    assert decision.reason == "signup_context_manifest"


def test_signup_context_allows_xoonboarding_fallback_document() -> None:
    decision = signup_context_decision(
        "https://www.paypal.com/webapps/xoonboarding?modxo_redirect_reason=guest_user&fromSignupLite=true",
        method="GET",
        resource_type="document",
    )

    assert decision.action == "allow"
    assert decision.reason == "signup_context_fallback"


def test_signup_context_allows_datadog_browser_sdk_script() -> None:
    decision = signup_context_decision(
        "https://www.datadoghq-browser-agent.com/us5/v5/datadog-rum.js",
        method="GET",
        resource_type="script",
    )

    assert decision.action == "allow"
    assert decision.reason == "signup_context_datadog_script"


def test_signup_context_allows_paypal_analytics_script() -> None:
    decision = signup_context_decision(
        "https://www.paypalobjects.com/pa/js/min/pa.js",
        method="GET",
        resource_type="script",
    )

    assert decision.action == "allow"
    assert decision.reason == "signup_context_observability_script"


def test_signup_context_allowlist_does_not_open_checkout_or_static_styles() -> None:
    checkout_decision = headless_optimized_request_decision(
        "https://www.paypal.com/graphql?CheckoutSessionDataQuery=",
        method="POST",
        resource_type="fetch",
        rules=seed_headless_optimized_rules("checkout"),
    )
    style_decision = signup_context_decision(
        "https://www.paypalobjects.com/checkoutweb/release/weasley/vendor.a4f4f83d16365c4e48c1.css",
        method="GET",
        resource_type="stylesheet",
    )

    assert checkout_decision.action == "abort"
    assert checkout_decision.reason == "not_allowlisted"
    assert style_decision.action == "abort"
    assert style_decision.reason == "static_resource_blocked"
