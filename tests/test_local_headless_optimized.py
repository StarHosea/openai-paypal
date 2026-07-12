import json
import time
from collections.abc import Callable
from typing import Any, Protocol, cast

import paypal.fingerprint as fingerprint
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
headless_url_is_challenge = cast(Callable[[str], bool], getattr(local_headless, "_headless_url_is_challenge"))
datadome_challenge_present = cast(Callable[[int, str], bool], getattr(local_headless, "_datadome_challenge_present"))
signup_context_document_assessment = cast(
    Callable[[str, int, str], dict[str, object]],
    getattr(local_headless, "_signup_context_document_assessment"),
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
    assert headless_phase1_family("https://www.paypal.com/xoplatform/logger/api/logger/") == "observability"
    assert headless_phase1_family("https://b.stats.paypal.com/v2/counter.cgi?p=EC-TEST&s=CHECKOUT") == "observability"


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


def test_local_ios_profile_uses_mobile_geometry_and_no_ch_headers() -> None:
    runtime = fingerprint._generate_ios_phone_runtime_profile(
        {
            "country": "US",
            "language": "en-US",
            "locale": "en_US",
            "timezone": "America/New_York",
        }
    )
    profile = cast(dict[str, object], runtime["browser_profile"])
    options = local_headless._context_options(
        browser_profile=profile,
        screen=cast(dict[str, object], runtime["screen"]),
        viewport=cast(dict[str, object], runtime["viewport"]),
    )
    headers = local_headless._headless_extra_http_headers(profile)
    script = local_headless._stealth_init_script(
        browser_profile=profile,
        screen=cast(dict[str, object], runtime["screen"]),
        viewport=cast(dict[str, object], runtime["viewport"]),
    )

    assert local_headless._is_ios_webkit_profile(profile)
    assert profile["user_agent"] == fingerprint.IOS_PHONE_USER_AGENT
    assert options["is_mobile"] is True
    assert options["has_touch"] is True
    assert options["device_scale_factor"] == 3.0
    assert options["viewport"] == {"width": 480, "height": 854}
    assert options["screen"] == {"width": 480, "height": 854}
    assert headers == {"Accept-Language": "en-US,en;q=0.9"}
    assert "iosWebKit" in script
    assert '"userAgentData", () => undefined' in script


def test_local_ios_route_removes_chromium_client_hints() -> None:
    runtime = fingerprint._generate_ios_phone_runtime_profile({"language": "en-US"})
    profile = cast(dict[str, object], runtime["browser_profile"])
    session = local_headless.LocalHeadlessSession(browser_profile=profile)

    class Request:
        url = "https://www.paypal.com/agreements/approve"
        method = "GET"
        resource_type = "document"
        headers = {
            "user-agent": fingerprint.IOS_PHONE_USER_AGENT,
            "sec-ch-ua": '"Chromium";v="150"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"iOS"',
        }

    class Route:
        request = Request()

        def __init__(self) -> None:
            self.continued: dict[str, object] | None = None
            self.aborted = False

        def continue_(self, **kwargs: object) -> None:
            self.continued = kwargs

        def abort(self) -> None:
            self.aborted = True

    class Context:
        def __init__(self) -> None:
            self.handler: Callable[[object], None] | None = None

        def route(self, _pattern: str, handler: Callable[[object], None]) -> None:
            self.handler = handler

    context = Context()
    session._context = context
    session._network_mode = "datadome"
    session._install_network_policy()
    assert context.handler is not None
    route = Route()
    context.handler(route)

    assert route.aborted is False
    assert route.continued is not None
    normalized = cast(dict[str, object], route.continued["headers"])
    assert "user-agent" in normalized
    assert not any(name.lower().startswith("sec-ch-") for name in normalized)


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


def test_signup_context_allows_paypal_tealeaf_sdk_script() -> None:
    runtime = signup_context_decision(
        "https://www.paypalobjects.com/pa/3pjs/tl/6.4.177/patleaf.js",
        method="GET",
        resource_type="script",
    )
    config = signup_context_decision(
        "https://www.paypalobjects.com/pa/3pjs/tl/6.4.177/patlcfg.js",
        method="GET",
        resource_type="script",
    )

    assert runtime.action == "allow"
    assert runtime.reason == "signup_context_tealeaf_script"
    assert config.action == "allow"
    assert config.reason == "signup_context_tealeaf_script"


def test_signup_context_allows_paypal_marketing_analytics_dependencies() -> None:
    marketing = signup_context_decision(
        "https://www.paypalobjects.com/martech/tm/paypal/mktgtagmanager.js",
        method="GET",
        resource_type="script",
    )
    analytics_config = signup_context_decision(
        "https://www.paypalobjects.com/pa/mi/paypal/latmconf.js",
        method="GET",
        resource_type="script",
    )
    logger_route = signup_context_decision(
        "https://www.paypal.com/xoplatform/logger/api/logger/",
        method="POST",
        resource_type="xhr",
    )
    stats = signup_context_decision(
        "https://b.stats.paypal.com/v2/counter.cgi?p=EC-TEST&s=CHECKOUTUINODEWEB_ONBOARDING_LITE",
        method="GET",
        resource_type="image",
    )

    assert marketing.action == "allow"
    assert marketing.reason == "signup_context_marketing_script"
    assert analytics_config.action == "allow"
    assert analytics_config.reason == "signup_context_analytics_config"
    assert logger_route.action == "allow"
    assert logger_route.reason == "signup_context_logger"
    assert logger_route.family == "observability"
    assert stats.action == "allow"
    assert stats.reason == "signup_context_stats"
    assert stats.family == "observability"


def test_signup_context_allows_datadome_and_fraudnet_error_dependencies() -> None:
    datadome_script = signup_context_decision(
        "https://ct.ddc.paypal.com/i.js",
        method="GET",
        resource_type="script",
    )
    datadome_challenge_script = signup_context_decision(
        "https://ct.ddc.paypal.com/c.js",
        method="GET",
        resource_type="script",
    )
    fraudnet_error = signup_context_decision(
        "https://c.paypal.com/v1/r/d/b/e?appId=CHECKOUT&correlationID=EC-TEST",
        method="GET",
        resource_type="script",
    )

    assert datadome_script.action == "allow"
    assert datadome_script.reason == "datadome_script"
    assert datadome_challenge_script.action == "allow"
    assert datadome_challenge_script.reason == "datadome_script"
    assert fraudnet_error.action == "allow"
    assert fraudnet_error.reason == "fraudnet_error"


def test_signup_context_detects_geo_ddc_captcha_as_challenge_document() -> None:
    challenge_url = "https://geo.ddc.paypal.com/captcha/?referer=https%3A%2F%2Fwww.paypal.com%2Fcheckoutweb%2Fsignup"
    html = "<html><body>DataDome captcha <script>device_check_redirect_to_slider()</script></body></html>"

    assessment = signup_context_document_assessment(challenge_url, 200, html)

    assert headless_url_is_challenge(challenge_url) is True
    assert datadome_challenge_present(200, html) is True
    assert assessment["ok"] is False
    assert assessment["reason"] == "signup_context_datadome_challenge"
    assert assessment["blocked_by_datadome"] is True


def test_signup_context_accepts_real_paypal_signup_document() -> None:
    url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
    html = "<html><script src='/checkoutweb/release/weasley/app.js'></script><body>Create account</body></html>"

    assessment = signup_context_document_assessment(url, 200, html)

    assert assessment["ok"] is True
    assert assessment["reason"] == "ok"
    assert "weasley" in cast(list[object], assessment["normal_markers"])


def test_signup_context_accepts_normal_signup_document_with_datadome_bootstrap_urls() -> None:
    url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
    html = """
    <html>
      <head>
        <script>
          window.__DD_BOOTSTRAP__ = {
            endpoint: "https://geo.ddc.paypal.com/captcha/?referer=/checkoutweb/signup",
            tpl: "https://static.ddc.paypal.com/captcha/assets/tpl/index.css",
            vendor: "captcha-delivery",
            datadomeBlockedReason: "blocked request templates are declared here"
          };
        </script>
        <script src="/checkoutweb/release/weasley/app.js"></script>
      </head>
      <body data-app="CHECKOUTUINODEWEB_ONBOARDING_LITE">Create account</body>
    </html>
    """

    assessment = signup_context_document_assessment(url, 200, html)

    assert datadome_challenge_present(200, html) is False
    assert assessment["ok"] is True
    assert assessment["reason"] == "ok"
    assert assessment["challenge_markers"] == []


def test_signup_context_accepts_normal_document_when_playwright_status_is_stale_403() -> None:
    url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
    html = "<html><script src='/checkoutweb/release/weasley/app.js'></script><body>Create account</body></html>"

    assessment = signup_context_document_assessment(url, 403, html)

    assert assessment["ok"] is True
    assert assessment["stale_challenge_status_with_normal_doc"] is True


def test_signup_context_runs_risk_on_bootstrapped_document_without_replaying_navigation(monkeypatch: Any) -> None:
    signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"

    class FakeResponse:
        status = 200

    class FakePage:
        def __init__(self) -> None:
            self.url = ""
            self.goto_calls: list[str] = []

        def goto(self, url: str, **_kwargs: object) -> FakeResponse:
            self.goto_calls.append(url)
            self.url = url
            return FakeResponse()

        def content(self) -> str:
            return "<html><script src='/checkoutweb/release/weasley/app.js'></script><body>Create account</body></html>"

        def wait_for_load_state(self, _state: str, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _timeout: float) -> None:
            return None

        def evaluate(self, _expression: str, _arg: object = None) -> object:
            return {}

        def on(self, _event: str, _callback: Callable[[object], None]) -> object:
            return None

    class FakeContext:
        def __init__(self) -> None:
            self.page = FakePage()

        def new_page(self) -> FakePage:
            return self.page

        def cookies(self, _urls: list[str]) -> list[dict[str, object]]:
            return [{"name": "datadome", "value": "dd-cookie-value", "domain": ".paypal.com", "path": "/"}]

    session = local_headless.LocalHeadlessSession(runtime="headless")
    fake_context = FakeContext()
    session._context = fake_context
    session._browser = object()
    monkeypatch.setattr(session, "start", lambda: None)
    monkeypatch.setattr(local_headless, "_wait_for_page_state_or_ready", lambda *args, **kwargs: True)

    def fake_inject(_page: object, **_kwargs: object) -> None:
        counts = cast(dict[str, object], session.events["counts"])
        for family in ("fraudnet_p1", "fraudnet_p2", "fraudnet_w", "identity_di_log", "datadog_rum"):
            counts[family] = 1

    monkeypatch.setattr(session, "_inject_mtr_and_phase1_scripts", fake_inject)

    result = session.run_mtr_phase1(
        signup_url,
        dfp_config={},
        dfp_script_url="https://www.paypalobjects.com/rdaAssets/fraudnet/ext/dfp.js",
        wait_seconds=0.01,
        mtr_wait_seconds=0.01,
        stage="signup_context",
        new_page=True,
        run_mtr=False,
    )

    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert fake_context.page.goto_calls == [signup_url]
    assert cast(dict[str, object], result["signup_context_page"])["ok"] is True


def test_signup_context_can_seed_protocol_signup_html_without_datadome_bootstrap(monkeypatch: Any) -> None:
    signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
    html = "<html><script src='/checkoutweb/release/weasley/app.js'></script><body>Create account</body></html>"

    class FakeResponse:
        status = 200

    class FakeRoute:
        def __init__(self, page: "FakePage") -> None:
            self.page = page

        @property
        def request(self) -> object:
            class Request:
                url = signup_url
                method = "GET"
                resource_type = "document"

            return Request()

        def fulfill(self, **kwargs: object) -> None:
            self.page.fulfilled = dict(kwargs)
            self.page.url = signup_url
            self.page.html = str(kwargs.get("body") or "")

        def continue_(self) -> None:
            return None

    class FakePage:
        def __init__(self) -> None:
            self.url = ""
            self.html = ""
            self.goto_calls: list[str] = []
            self.route_handler: Callable[[object], None] | None = None
            self.fulfilled: dict[str, object] = {}

        def route(self, _url: str, callback: Callable[[object], None]) -> None:
            self.route_handler = callback

        def goto(self, url: str, **_kwargs: object) -> FakeResponse:
            self.goto_calls.append(url)
            if self.route_handler:
                self.route_handler(FakeRoute(self))
            else:
                self.url = url
                self.html = html
            return FakeResponse()

        def content(self) -> str:
            return self.html

        def wait_for_load_state(self, _state: str, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _timeout: float) -> None:
            return None

        def evaluate(self, _expression: str, _arg: object = None) -> object:
            return {}

        def on(self, _event: str, _callback: Callable[[object], None]) -> object:
            return None

    class FakeContext:
        def __init__(self) -> None:
            self.page = FakePage()

        def new_page(self) -> FakePage:
            return self.page

        def cookies(self, _urls: list[str]) -> list[dict[str, object]]:
            return []

    session = local_headless.LocalHeadlessSession(runtime="headless")
    fake_context = FakeContext()
    session._context = fake_context
    session._browser = object()
    monkeypatch.setattr(session, "start", lambda: None)

    def fake_inject(_page: object, **_kwargs: object) -> None:
        counts = cast(dict[str, object], session.events["counts"])
        for family in ("fraudnet_p1", "fraudnet_p2", "fraudnet_w", "identity_di_log", "datadog_rum"):
            counts[family] = 1

    monkeypatch.setattr(session, "_inject_mtr_and_phase1_scripts", fake_inject)

    result = session.run_mtr_phase1(
        signup_url,
        dfp_config={},
        dfp_script_url="https://www.paypalobjects.com/rdaAssets/fraudnet/ext/dfp.js",
        wait_seconds=0.01,
        mtr_wait_seconds=0.01,
        stage="signup_context",
        new_page=True,
        run_mtr=False,
        document_html=html,
        document_status=200,
    )

    assert result["ok"] is True
    assert fake_context.page.goto_calls == [signup_url]
    assert fake_context.page.fulfilled["body"] == html
    assert cast(dict[str, object], result["signup_context_seeded_document"])["enabled"] is True


def test_roxy_session_close_unroutes_network_handler_and_closes_owned_page() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.unrouted: list[tuple[str, object]] = []

        def unroute(self, pattern: str, handler: object) -> None:
            self.unrouted.append((pattern, handler))

    def handler(_route: object) -> None:
        return None

    session = local_headless.LocalHeadlessSession(
        roxy_browser={"cdp_info": {"http": "127.0.0.1:9222"}},
        runtime="roxy",
    )
    page = FakePage()
    context = FakeContext()
    session._browser = object()
    session._context = context
    session._network_route_handler = handler
    session._network_installed = True
    session._owned_pages.append(page)

    session.close()

    assert context.unrouted == [("**/*", handler)]
    assert page.closed is True
    assert session._network_route_handler is None
    assert session._network_installed is False
    assert session._owned_pages == []


def test_roxy_page_keeps_native_fingerprint_without_headless_cdp_override(monkeypatch: Any) -> None:
    calls: list[str] = []

    class FakePage:
        def on(self, _event: str, _callback: object) -> None:
            return None

    monkeypatch.setattr(
        local_headless,
        "_apply_cdp_stealth_overrides",
        lambda *_args, **_kwargs: calls.append("override"),
    )
    session = local_headless.LocalHeadlessSession(
        roxy_browser={"cdp_info": {"http": "127.0.0.1:9222"}},
        runtime="roxy",
    )
    session._context = object()

    session._prepare_page_for_runtime(FakePage())

    assert calls == []


def test_signup_context_datadome_bootstrap_challenge_does_not_inject_risk(monkeypatch: Any) -> None:
    signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"

    class FakeResponse:
        status = 200

    class FakePage:
        def __init__(self) -> None:
            self.url = ""

        def goto(self, _url: str, **_kwargs: object) -> FakeResponse:
            self.url = "https://geo.ddc.paypal.com/captcha/?referer=signup"
            return FakeResponse()

        def content(self) -> str:
            return "<html><body>DataDome captcha device_check_redirect_to_slider</body></html>"

        def wait_for_load_state(self, _state: str, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _timeout: float) -> None:
            return None

        def evaluate(self, _expression: str, _arg: object = None) -> object:
            return {}

        def on(self, _event: str, _callback: Callable[[object], None]) -> object:
            return None

    class FakeContext:
        def __init__(self) -> None:
            self.page = FakePage()

        def new_page(self) -> FakePage:
            return self.page

        def cookies(self, _urls: list[str]) -> list[dict[str, object]]:
            return [{"name": "datadome", "value": "dd-cookie-value", "domain": ".paypal.com", "path": "/"}]

    session = local_headless.LocalHeadlessSession(runtime="headless")
    fake_context = FakeContext()
    session._context = fake_context
    session._browser = object()
    monkeypatch.setattr(session, "start", lambda: None)
    monkeypatch.setattr(local_headless, "_wait_for_page_state_or_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(session, "_inject_mtr_and_phase1_scripts", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("risk injection should not run on DataDome challenge")))

    result = session.run_mtr_phase1(
        signup_url,
        dfp_config={},
        dfp_script_url="https://www.paypalobjects.com/rdaAssets/fraudnet/ext/dfp.js",
        wait_seconds=0.01,
        mtr_wait_seconds=0.01,
        stage="signup_context",
        new_page=True,
        run_mtr=False,
    )

    assert result["ok"] is False
    assert result["reason"] == "signup_context_datadome_challenge"
    assert result["blocked_by_datadome"] is True


def test_headless_default_policy_uses_frozen_learned_cache_rules() -> None:
    rules = seed_headless_optimized_rules("checkout")

    ddbm_decision = headless_optimized_request_decision(
        "https://ddbm2.paypal.com/js/runtime",
        method="POST",
        resource_type="xhr",
        rules=rules,
    )
    identity_ping_decision = headless_optimized_request_decision(
        "https://www.paypal.com/identity/di/log",
        method="POST",
        resource_type="ping",
        rules=rules,
    )
    datadog_ping_decision = headless_optimized_request_decision(
        "https://browser-intake-us5-datadoghq.com/api/v2/rum",
        method="POST",
        resource_type="ping",
        rules=rules,
    )

    assert ddbm_decision.action == "allow"
    assert ddbm_decision.reason == "learned_ddbm"
    assert identity_ping_decision.action == "allow"
    assert identity_ping_decision.reason == "learned_identity_di_log"
    assert datadog_ping_decision.action == "allow"
    assert datadog_ping_decision.reason == "learned_datadog_rum"


def test_signup_context_allows_real_risk_signal_ping_resource_type() -> None:
    fraudnet_decision = signup_context_decision(
        "https://c.paypal.com/v1/r/d/b/p1",
        method="POST",
        resource_type="ping",
    )
    tealeaf_decision = signup_context_decision(
        "https://www.paypal.com/platform/tealeaftarget",
        method="POST",
        resource_type="ping",
    )
    datadog_decision = signup_context_decision(
        "https://browser-intake-us5-datadoghq.com/api/v2/rum",
        method="POST",
        resource_type="ping",
    )

    assert fraudnet_decision.action == "allow"
    assert fraudnet_decision.reason == "fraudnet_p1"
    assert tealeaf_decision.action == "allow"
    assert tealeaf_decision.reason == "tealeaf_observe"
    assert datadog_decision.action == "allow"
    assert datadog_decision.reason == "learned_datadog_rum"


def test_headless_allowlist_cache_is_ignored_after_rules_are_frozen(monkeypatch: Any, tmp_path: Any) -> None:
    build_rules = cast(Callable[[str], list[object]], getattr(local_headless, "_headless_rules"))
    learning_enabled = cast(Callable[[], bool], getattr(local_headless, "headless_allowlist_learning_enabled"))
    cache_path = tmp_path / "headless_allowlist_cache.json"
    now = time.time()

    monkeypatch.setenv("PAYPAL_HEADLESS_ALLOWLIST_CACHE", str(cache_path))
    monkeypatch.delenv("PAYPAL_HEADLESS_IGNORE_CACHE", raising=False)
    monkeypatch.delenv("PAYPAL_HEADLESS_OPTIMIZED_IGNORE_CACHE", raising=False)
    monkeypatch.setenv("PAYPAL_HEADLESS_ALLOWLIST_LEARNING", "1")
    cache_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": now,
                "rules": [
                    {
                        "host": "dynamic-cache.example",
                        "path_prefix": "/learned",
                        "methods": ["GET"],
                        "resource_types": ["fetch"],
                        "reason": "learned_dynamic_cache",
                        "created_at": now,
                        "last_seen": now,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rules = build_rules("checkout")
    before = cache_path.read_text(encoding="utf-8")

    assert all(getattr(rule, "host") != "dynamic-cache.example" for rule in rules)
    assert any(getattr(rule, "host") == "ddbm2.paypal.com" and getattr(rule, "reason") == "learned_ddbm" for rule in rules)
    assert any(getattr(rule, "host") == "www.paypal.com" and getattr(rule, "reason") == "learned_identity_di_log" for rule in rules)
    assert any(getattr(rule, "host") == "browser-intake-us5-datadoghq.com" and getattr(rule, "reason") == "learned_datadog_rum" for rule in rules)
    assert learning_enabled() is False
    assert cache_path.read_text(encoding="utf-8") == before


def test_signup_context_missing_required_signals_writes_redacted_diagnostic(monkeypatch: Any, tmp_path: Any) -> None:
    diagnostic_path = tmp_path / "headless_last_missing_signup_context.json"
    monkeypatch.setenv("PAYPAL_HEADLESS_MISSING_DIAGNOSTIC_PATH", str(diagnostic_path))

    session = local_headless.LocalHeadlessSession(runtime="headless")
    session.events["injected_scripts"] = ["https://c.paypal.com/da/r/fb_fp.js"]
    session.events["blocked_requests"] = [
        {
            "event": "route",
            "url": "https://www.paypalobjects.com/martech/tm/paypal/mktgtagmanager.js",
            "method": "GET",
            "resource_type": "script",
            "decision": {"action": "abort", "reason": "not_allowlisted"},
        }
    ]
    writer = cast(Callable[..., str], getattr(session, "_write_signup_context_missing_diagnostic"))

    written = writer(
        page_url="https://www.paypal.com/checkoutweb/signup?ssrt=SECRETSSRT&ba_token=BA-SECRET&token=EC-SECRET",
        status=200,
        observed=["identity_di_log", "ddbm"],
        missing=["fraudnet_p1", "datadog_rum"],
        required_missing=["fraudnet_p1", "datadog_rum"],
        run_mtr=True,
    )

    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert written == str(diagnostic_path)
    assert payload["required_missing"] == ["fraudnet_p1", "datadog_rum"]
    assert payload["page_url"] == "https://www.paypal.com/checkoutweb/signup?ssrt=%3Credacted%3E&ba_token=%3Credacted%3E&token=%3Credacted%3E"
    assert "SECRET" not in diagnostic_path.read_text(encoding="utf-8")
    assert payload["important_decisions"]["fraudnet_p1_rt_p"]["action"] == "allow"


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
