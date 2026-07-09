import importlib
import json
import os
import re
import threading
import tempfile
import time
import zlib
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Final, Protocol, cast, final
from unittest.mock import patch


class FlowState(Protocol):
    ec_token: str
    ssrt: str
    ctx_id: str
    signup_url: str
    content_hash: str
    content_identifier: str
    paypal_captcha_solved: bool
    show_create_account_action_id: str
    create_user_action_id: str
    mtr_channel: str
    mtr_client_metadata_id: str
    mtr_api_key: str
    mtr_runtime_source: str
    mtr_dfp_script_url: str
    mtr_get_status: int
    mtr_post_status: int
    mtr_request_id: str
    mtr_sealed_result: str
    mtr_visitor_token: str
    mtr_completed: bool
    mtr_completed_cmid: str
    mtr_browser_result: dict[str, object]
    datadome_cookie: str
    datadome_clientid: str
    datadome_browser_solved: bool
    datadome_browser_result: dict[str, object]
    risk_signals_runtime_source: str
    risk_signals_browser_result: dict[str, object]
    user_id: str
    euat_token: str
    browser_profile: dict[str, object]
    screen: dict[str, object]
    viewport: dict[str, object]
    device_fingerprint: dict[str, object]


class FlowUnderTest(Protocol):
    state: FlowState
    session: object


class PayPalFlowFactory(Protocol):
    def __call__(
        self,
        *,
        ba_token: str,
        user: object,
        card: object,
        address: object,
        proxy_enabled: bool | None = None,
        risk_signals_mode: str | None = None,
    ) -> FlowUnderTest: ...


class UserInfoFactory(Protocol):
    def __call__(
        self,
        *,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        phone_local: str,
        phone_country_code: str,
        password: str,
        dob: str,
        cpf: str,
    ) -> object: ...


class CardInfoFactory(Protocol):
    def __call__(self, number: str, expiry: str, cvv: str, card_type: str) -> object: ...


class BillingAddressFactory(Protocol):
    def __call__(
        self,
        *,
        street: str,
        house_number: str,
        district: str,
        city: str,
        state: str,
        postal_code: str,
    ) -> object: ...


flow_module: Final[ModuleType] = importlib.import_module("paypal.flow")
session_module: Final[ModuleType] = importlib.import_module("paypal.session")
models_module: Final[ModuleType] = importlib.import_module("paypal.models")
mtr_module: Final[ModuleType] = importlib.import_module("paypal.mtr")
fingerprint_module: Final[ModuleType] = importlib.import_module("paypal.fingerprint")
local_headless_module: Final[ModuleType] = importlib.import_module("paypal.local_headless")
web_module: Final[ModuleType] = importlib.import_module("web")
PayPalFlow: Final[PayPalFlowFactory] = cast(PayPalFlowFactory, getattr(flow_module, "PayPalFlow"))
PayPalAuthChallenge: Final[type[RuntimeError]] = cast(type[RuntimeError], getattr(flow_module, "PayPalAuthChallenge"))
BillingAddress: Final[BillingAddressFactory] = cast(
    BillingAddressFactory,
    getattr(models_module, "BillingAddress"),
)
CardInfo: Final[CardInfoFactory] = cast(CardInfoFactory, getattr(models_module, "CardInfo"))
UserInfo: Final[UserInfoFactory] = cast(UserInfoFactory, getattr(models_module, "UserInfo"))


def decode_mtr_body(body: bytes) -> dict[str, object]:
    seed = body[0]
    compressed = body[1] == (seed + 3) % 256 and body[2] == (seed + 14) % 256
    assert compressed
    pad_len = (body[3] - seed) % 256
    assert pad_len <= 3
    key_start = 4 + pad_len
    key = body[key_start:key_start + 9]
    assert len(key) == 9
    encoded = body[key_start + 9:]
    compressed_payload = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(encoded))
    payload = zlib.decompress(compressed_payload, wbits=-15)
    decoded = cast(object, json.loads(payload.decode("utf-8")))
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


@final
class FakeResponse:
    def __init__(self, url: str, text: str = "", status_code: int = 200) -> None:
        self.url: str = url
        self.text: str = text
        self.status_code: int = status_code
        self.headers: dict[str, str] = {}
        self.content: bytes = text.encode("utf-8")

    def json(self) -> dict[str, object]:
        return {}


@final
class FakeSession:
    def __init__(self, state: object) -> None:
        self.state: object = state
        self.graphql_operations: list[str] = []
        self.graphql_variables: list[tuple[str, dict[str, object]]] = []
        self.requests: list[tuple[str, str]] = []
        self.last_post_kwargs: dict[str, object] = {}
        self.post_calls: list[tuple[str, dict[str, object]]] = []
        self.browser_cookies: list[dict[str, object]] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.requests.append(("GET", url))
        if "/mtr/" in url and "/x0" in url:
            return FakeResponse(url, "cys3bHRVUUtdbjB0cEFjaVJ1bnRpbWVCb290c3RyYXBUb2tlbg==")
        return FakeResponse(url, "<html></html>")

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append(("POST", url))
        self.last_post_kwargs = dict(kwargs)
        self.post_calls.append((url, dict(kwargs)))
        if "/mtr/" in url:
            return FakeResponse(
                url,
                '{"v":"2","requestId":"REQ12345678901234567","sealedResult":"sealed-test-result","products":{"identification":{"data":{"visitorToken":"visitor-123"}}}}',
            )
        if "Continue_To_Payment" in url:
            return FakeResponse(
                url,
                '{"onboardingRedirectUrl":"https://www.paypal.com/pay/checkout/signup/contact?token=EC-TEST123&ssrt=123"}',
            )
        return FakeResponse(url)

    def graphql(
        self,
        operation_name: str,
        _query: str,
        variables: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        self.graphql_operations.append(operation_name)
        self.graphql_variables.append((operation_name, variables))
        if operation_name == "AddressAutocompleteFromPostalCodeQuery":
            return {
                "data": {
                    "addressNormalization": {
                        "line1": "Praça Tiradentes, 35, 450",
                        "line2": "Centro",
                        "city": "Curitiba",
                        "state": "PR",
                        "postalCode": "80020-100",
                    }
                }
            }
        if operation_name == "SignUpNewMemberMutation":
            return {"data": {"onboardAccount": {"buyer": {"userId": "user-123"}}}}
        return {"data": {}}

    def purge_security_challenge_state(
        self,
        _challenge_html: str = "",
        reason: str = "",
        *,
        clear_cookies: bool = False,
        clear_files: bool = False,
    ) -> dict[str, object]:
        self.requests.append(("PURGE", reason))
        return {
            "reason": reason,
            "clear_cookies": clear_cookies,
            "clear_files": clear_files,
        }

    def export_cookies_for_browser(self) -> list[dict[str, object]]:
        return [dict(cookie) for cookie in self.browser_cookies]

    def import_browser_cookies(self, cookies: list[dict[str, object]]) -> None:
        for cookie in cookies:
            item = dict(cookie)
            self.browser_cookies.append(item)
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
            if name == "datadome":
                setattr(self.state, "datadome_cookie", value)


def make_flow(*, risk_signals_mode: str | None = None) -> tuple[FlowUnderTest, FakeSession]:
    flow = PayPalFlow(
        ba_token="BA-TESTTOKEN123",
        user=UserInfo(
            first_name="Matheus",
            last_name="Pereira",
            email="matheus@example.com",
            phone="+5521984108976",
            phone_local="21984108976",
            phone_country_code="+55",
            password="Password123!",
            dob="01/01/1990",
            cpf="123.456.789-09",
        ),
        card=CardInfo("4111111111111111", "11/2029", "123", "CREDIT"),
        address=BillingAddress(
            street="Praça Tiradentes",
            house_number="35",
            district="Centro",
            city="Curitiba",
            state="PR",
            postal_code="80020-100",
        ),
        proxy_enabled=False,
        risk_signals_mode=risk_signals_mode,
    )
    flow.state.ssrt = "123"
    flow.state.ctx_id = "ctx-123"
    flow.state.show_create_account_action_id = "show-action"
    flow.state.create_user_action_id = "create-action"
    fake = FakeSession(flow.state)
    flow.session = fake
    return flow, fake


def run_phase2_create_account(flow: FlowUnderTest) -> None:
    phase = cast(Callable[[], None], getattr(flow, "_phase2_create_account"))
    phase()


def run_signup_attempt(flow: FlowUnderTest) -> None:
    signup_attempt = cast(
        Callable[[str, str], dict[str, object]],
        getattr(flow, "_send_signup_attempt"),
    )
    _ = signup_attempt(
        "EC-TEST123",
        "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
    )


class BrowserFlowOrderTest(unittest.TestCase):
    def test_modxo_action_ids_use_static_defaults_without_chunk_scan(self):
        flow, fake = make_flow()
        flow.state.show_create_account_action_id = ""
        flow.state.create_user_action_id = ""
        flow.state.submit_public_credential_action_id = ""
        flow.state.fetch_device_fingerprint_action_id = ""

        with patch.dict(os.environ, {"PAYPAL_MODXO_STATIC_ACTION_IDS": "1"}):
            cast(Callable[[str, str], None], getattr(flow, "_extract_modxo_action_ids"))(
                "<html></html>",
                "https://www.paypal.com/pay?token=BA-TESTTOKEN123",
            )

        self.assertEqual(fake.requests, [])
        self.assertEqual(flow.state.fetch_device_fingerprint_action_id, "40119ea45de7135869f32892c6e0436cc9722b7775")
        self.assertEqual(flow.state.show_create_account_action_id, "408cdbfcfb063642520b8dde73b124955e07000967")
        self.assertEqual(flow.state.submit_public_credential_action_id, "403375d290e5845b191b7f22e6b940617e87334e8b")
        self.assertEqual(flow.state.create_user_action_id, "60187d0e8cbc4131987e2c84c8e430dce698c2ace3")

    def test_modxo_action_chunks_are_fetched_concurrently(self):
        flow, _fake = make_flow()
        flow.state.show_create_account_action_id = ""
        flow.state.create_user_action_id = ""
        flow.state.submit_public_credential_action_id = ""
        flow.state.fetch_device_fingerprint_action_id = ""

        ids = {
            "fetchDeviceFingerprintDataAction": "a" * 40,
            "showCreateAccountAction": "b" * 40,
            "submitPublicCredential": "c" * 40,
            "createUserAction": "d" * 40,
        }
        chunk_texts = {
            "chunk-a.js": f'(0,x.createServerReference)("{ids["fetchDeviceFingerprintDataAction"]}",x,x,x,"fetchDeviceFingerprintDataAction");',
            "chunk-b.js": f'(0,x.createServerReference)("{ids["showCreateAccountAction"]}",x,x,x,"showCreateAccountAction");',
            "chunk-c.js": f'(0,x.createServerReference)("{ids["submitPublicCredential"]}",x,x,x,"submitPublicCredential");',
            "chunk-d.js": f'(0,x.createServerReference)("{ids["createUserAction"]}",x,x,x,"createUserAction");',
        }

        class ConcurrentChunkSession:
            def __init__(self) -> None:
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def get(self, url: str, **_kwargs: object) -> FakeResponse:
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.05)
                    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
                    return FakeResponse(url, chunk_texts.get(name, ""))
                finally:
                    with self.lock:
                        self.active -= 1

        concurrent_session = ConcurrentChunkSession()
        flow.session = concurrent_session
        html = "".join(
            f'<script src="/pay/_next/static/chunks/{name}?dpl=1"></script>'
            for name in chunk_texts
        )

        with patch.dict(os.environ, {
            "PAYPAL_MODXO_ACTION_CHUNK_CONCURRENCY": "4",
            "PAYPAL_MODXO_STATIC_ACTION_IDS": "0",
        }):
            cast(Callable[[str, str], None], getattr(flow, "_extract_modxo_action_ids"))(
                html,
                "https://www.paypal.com/pay?token=BA-TESTTOKEN123",
            )

        self.assertGreaterEqual(concurrent_session.max_active, 2)
        self.assertEqual(flow.state.fetch_device_fingerprint_action_id, ids["fetchDeviceFingerprintDataAction"])
        self.assertEqual(flow.state.show_create_account_action_id, ids["showCreateAccountAction"])
        self.assertEqual(flow.state.submit_public_credential_action_id, ids["submitPublicCredential"])
        self.assertEqual(flow.state.create_user_action_id, ids["createUserAction"])

    def test_random_synthetic_profile_uses_mainstream_screen_pool_by_default(self):
        generate_runtime_profile = cast(Callable[..., dict[str, object]], getattr(fingerprint_module, "generate_runtime_profile"))
        screen_choices = cast(list[tuple[dict[str, object], int]], getattr(fingerprint_module, "_MAINSTREAM_SCREEN_CHOICES"))
        expected_pairs = {
            (cast(int, screen["width"]), cast(int, screen["height"]))
            for screen, _weight in screen_choices
        }
        selected_screen = screen_choices[0][0]

        self.assertIn((1920, 1080), expected_pairs)
        self.assertIn((1366, 768), expected_pairs)
        self.assertIn((1536, 864), expected_pairs)
        self.assertIn((1440, 900), expected_pairs)
        self.assertIn((2560, 1440), expected_pairs)
        with patch.dict(os.environ, {}, clear=False):
            _removed_env = os.environ.pop("PAYPAL_RANDOMIZE_BROWSER_PROFILE", None)
            with patch("paypal.fingerprint.random.choices", return_value=[selected_screen]) as choices:
                runtime = generate_runtime_profile("random")

        choices.assert_called_once()
        screen = cast(dict[str, object], runtime["screen"])
        viewport = cast(dict[str, object], runtime["viewport"])
        profile = cast(dict[str, object], runtime["browser_profile"])
        dfp = cast(dict[str, object], runtime["device_fingerprint"])
        self.assertEqual(profile["fingerprint_source"], "random")
        self.assertEqual(dfp["source"], "random")
        self.assertEqual((screen["width"], screen["height"]), (1920, 1080))
        self.assertEqual(screen["colorDepth"], 24)
        self.assertEqual(screen["pixelDepth"], 24)
        self.assertLessEqual(cast(int, screen["availHeight"]), cast(int, screen["height"]))
        self.assertGreaterEqual(cast(int, viewport["width"]), 980)
        self.assertGreaterEqual(cast(int, viewport["height"]), 560)
        self.assertLessEqual(cast(int, viewport["width"]) + 16, cast(int, screen["width"]))
        self.assertLessEqual(cast(int, viewport["height"]) + 88, cast(int, screen["height"]))
        chrome_full_version = cast(str, profile["chrome_full_version"])
        self.assertRegex(chrome_full_version, r"^\d+\.\d+\.\d+\.\d+$")
        self.assertEqual(profile["chrome_major"], int(chrome_full_version.split(".", 1)[0]))
        self.assertIn(f"Chrome/{chrome_full_version}", cast(str, profile["user_agent"]))

    def test_random_synthetic_linux_profile_uses_linux_mtr_defaults(self):
        generate_runtime_profile = cast(Callable[..., dict[str, object]], getattr(fingerprint_module, "generate_runtime_profile"))
        build_mtr_request_object = cast(Callable[..., dict[str, object]], getattr(mtr_module, "build_mtr_request_object"))
        flow, _fake = make_flow()
        runtime = generate_runtime_profile("random")
        flow.state.browser_profile = cast(dict[str, object], runtime["browser_profile"])
        flow.state.screen = cast(dict[str, object], runtime["screen"])
        flow.state.viewport = cast(dict[str, object], runtime["viewport"])
        flow.state.device_fingerprint = cast(dict[str, object], runtime["device_fingerprint"])
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"

        payload = build_mtr_request_object(
            flow.state,
            page_url="https://www.paypal.com/pay?token=BA-TESTTOKEN123",
            x0_token="bootstrap-token",
        )

        chrome_major = str(flow.state.browser_profile["chrome_major"])
        chrome_full_version = cast(str, flow.state.browser_profile["chrome_full_version"])
        s20 = cast(list[str], cast(dict[str, object], payload["s20"])["v"])
        s58 = cast(dict[str, object], cast(dict[str, object], payload["s58"])["v"])
        s74 = cast(dict[str, object], cast(dict[str, object], payload["s74"])["v"])
        self.assertIn("DejaVu Sans", s20)
        self.assertNotIn("Calibri", s20)
        self.assertIn({"b": "Google Chrome", "v": chrome_major}, cast(list[dict[str, str]], s58["b"]))
        self.assertIn(chrome_full_version, cast(dict[str, str], s58["h"])["fullVersionList"])
        self.assertEqual(s74["vendorUnmasked"], flow.state.browser_profile["gpu_vendor"])
        self.assertEqual(s74["rendererUnmasked"], flow.state.browser_profile["gpu_renderer"])

    def test_fraudnet_preserves_explicit_zero_timing_and_memory_values(self):
        build_p2 = cast(Callable[..., dict[str, object]], getattr(fingerprint_module, "_build_p2_payload"))
        build_pa = cast(Callable[..., list[dict[str, object]]], getattr(fingerprint_module, "_build_pa_payload"))
        flow, fake = make_flow()
        flow.state.device_fingerprint.update(
            {
                "canvas_h": "canvas-zero",
                "js_heap_size_limit": 0,
                "js_memory": {"used": 0, "total": 0},
                "timings": {
                    "tt_dfp": 0.0,
                    "tt_canvas": 0.0,
                    "tt_webgl_basic": 0.0,
                    "tt_webgl_ext": 0.0,
                    "tt_storage": 0.0,
                    "tt_math": 0.0,
                },
            }
        )

        p2 = build_p2(fake, "https://www.paypal.com/pay?token=BA-TEST")
        pa = build_pa(fake, "corr-123", "IWC_NEXT_CHECKOUT")

        p2_data = cast(dict[str, object], p2["data"])
        cv = cast(dict[str, object], p2_data["cv"])
        js_mem = cast(dict[str, object], cast(dict[str, object], p2_data["vm"])["jsMem"])
        pa_d = cast(dict[str, object], cast(list[dict[str, object]], pa[0]["dfp"])[0]["d"])
        web_canvas = cast(dict[str, object], cast(dict[str, object], pa_d["wGlCnv"])["attr"])
        wb_canvas = cast(dict[str, object], cast(dict[str, object], web_canvas["wbCnv"])["data"])
        wb_basic = cast(dict[str, object], web_canvas["wbBsc"])
        wb_ext = cast(dict[str, object], web_canvas["wbExt"])
        self.assertEqual(cv["t"], "0.00")
        self.assertEqual(js_mem["usedJSHeapSize"], 0)
        self.assertEqual(js_mem["totalJSHeapSize"], 0)
        self.assertEqual(js_mem["jsHeapSizeLimit"], 4_395_630_592)
        self.assertEqual(pa_d["ttDfp"], 0.0)
        self.assertEqual(wb_canvas["ttCvSig"], 0.0)
        self.assertEqual(wb_basic["ttWbBsc"], 0.0)
        self.assertEqual(cast(dict[str, object], wb_ext["data"])["ttWbExt"], 0.0)
        self.assertEqual(cast(dict[str, object], pa_d["strg"])["ttStrg"], 0.0)
        self.assertEqual(cast(dict[str, object], pa_d["mth"])["ttMth"], 0.0)

    def test_fraudnet_p1_reports_captured_webdriver_flag(self):
        build_p1 = cast(Callable[..., dict[str, object]], getattr(fingerprint_module, "_build_p1_payload"))
        flow, fake = make_flow()
        flow.state.browser_profile["fingerprint_source"] = "headless"
        flow.state.device_fingerprint["navigator_webdriver"] = True

        payload = build_p1(
            fake,
            "corr-123",
            "IWC_NEXT_CHECKOUT",
            "https://www.paypal.com/pay?token=BA-TEST",
            "https://www.paypal.com/",
        )

        self.assertTrue(cast(dict[str, object], payload["hlb"])["wd"])

    def test_web_create_job_defaults_to_local_headless_modes(self):
        create_job = cast(Callable[..., object], getattr(web_module, "create_job"))
        jobs = cast(dict[str, object], getattr(web_module, "JOBS"))

        class NoopThread:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def start(self) -> None:
                pass

        with patch("web.threading.Thread", NoopThread):
            job = create_job(
                owner_device_id="0123456789abcdef0123456789abcdef",
                ba_token="BA-TESTTOKEN123",
                phone="+12345678",
                debug=False,
                max_card_attempts=5,
            )

        try:
            self.assertEqual(getattr(job, "fingerprint_source"), "headless")
            self.assertEqual(getattr(job, "datadome_mode"), "headless")
            self.assertEqual(getattr(job, "mtr_runtime"), "headless")
            self.assertEqual(getattr(job, "risk_signals_mode"), "headless")
            self.assertFalse(getattr(job, "proxy_enabled"))
            self.assertEqual(getattr(job, "proxy_label"), "代理关闭")
            getattr(job, "set_status")("running", "执行前置准备")
            getattr(job, "add_log")("INFO", "Runtime modes: fingerprint=headless datadome=headless mtr=headless")
            getattr(job, "complete")({
                "ok": True,
                "risk_runtime": {"synthetic_risk_families": {"mode": "headless"}},
                "synthetic_risk_families": {"mode": "headless"},
            })
            payload = getattr(job, "to_dict")()
            self.assertNotIn("risk_signals_mode", payload)
            self.assertNotIn("risk_runtime", payload["result"])
            self.assertNotIn("synthetic_risk_families", payload["result"])
            self.assertNotIn("Phase1", payload["stage"])
            self.assertNotIn("风控", payload["stage"])
            self.assertFalse(any("risk=" in log["message"] for log in payload["logs"]))
        finally:
            job_id = cast(str, getattr(job, "id"))
            _removed = jobs.pop(job_id, None)

    def test_web_job_payload_sanitizes_risk_logs_and_errors(self):
        web_job = cast(Callable[..., object], getattr(web_module, "WebJob"))
        job = web_job(
            id="job-risk-log",
            owner_device_id="0123456789abcdef0123456789abcdef",
            ba_token="BA-TESTTOKEN123",
            phone="+12345678",
        )

        getattr(job, "add_log")("INFO", "Browser risk signal dispatch")
        getattr(job, "add_log")("WARNING", "Risk signal runtime failed")
        try:
            raise RuntimeError("Risk signal runtime failed before SignUpNewMemberMutation")
        except RuntimeError as exc:
            getattr(job, "fail")(exc)

        payload = getattr(job, "to_dict")()
        browser_text = json.dumps(
            {
                "stage": payload["stage"],
                "error": payload["error"],
                "traceback": payload["traceback"],
                "logs": payload["logs"],
            },
            ensure_ascii=False,
        ).lower()
        self.assertNotIn("phase1", browser_text)
        self.assertNotIn("phase 1", browser_text)
        self.assertNotIn("risk", browser_text)
        self.assertNotIn("风控", browser_text)

    def test_web_form_defaults_proxy_off_and_hides_risk_control(self):
        html = (Path(__file__).resolve().parents[1] / "web_static" / "index.html").read_text(encoding="utf-8")
        script = (Path(__file__).resolve().parents[1] / "web_static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="proxyEnabled" name="proxyEnabled" type="checkbox"', html)
        self.assertNotIn('id="proxyEnabled" name="proxyEnabled" type="checkbox" checked', html)
        self.assertNotIn("riskSignalsMode", html)
        self.assertNotIn("Phase1 风控信号", html)
        self.assertNotIn("riskSignalsMode", script)
        self.assertNotIn("risk_signals_mode", script)
        self.assertNotIn("Risk:", script)

    def test_web_create_job_rejects_traffic_dir_outside_captures(self):
        create_job = cast(Callable[..., object], getattr(web_module, "create_job"))

        class NoopThread:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def start(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            with patch("web.threading.Thread", NoopThread), self.assertRaises(ValueError) as raised:
                create_job(
                    owner_device_id="0123456789abcdef0123456789abcdef",
                    ba_token="BA-TESTTOKEN123",
                    phone="+12345678",
                    debug=False,
                    max_card_attempts=5,
                    record_traffic=True,
                    traffic_dir=tmp,
                )

        self.assertIn("captures", str(raised.exception))

    def test_strict_browser_risk_is_explicit_opt_in(self):
        strict_enabled = cast(Callable[[], bool], getattr(session_module, "strict_browser_risk_enabled"))

        with patch.object(session_module, "_load_dotenv_value", return_value=""):
            self.assertFalse(strict_enabled())

        with patch.object(session_module, "_load_dotenv_value", return_value="1"):
            self.assertTrue(strict_enabled())

        with patch.object(session_module, "_load_dotenv_value", return_value="0"):
            self.assertFalse(strict_enabled())

    def test_weasley_warmup_matches_roxy_order(self):
        flow, fake = make_flow()

        run_phase2_create_account(flow)

        self.assertEqual(
            fake.graphql_operations,
            [
                "DeferredFeature",
                "GriffinMetadataQuery",
                "CheckoutSessionDataQuery",
                "SupportedFundingSourcesQuery",
            ],
        )

    def test_supported_funding_sources_can_be_explicitly_skipped(self):
        with patch.dict(os.environ, {"PAYPAL_SKIP_SUPPORTED_FUNDING_SOURCES": "1"}):
            flow, fake = make_flow()
            run_phase2_create_account(flow)

        self.assertEqual(
            fake.graphql_operations,
            [
                "DeferredFeature",
                "GriffinMetadataQuery",
                "CheckoutSessionDataQuery",
            ],
        )

    def test_address_autocomplete_runs_during_signup_attempt(self):
        flow, fake = make_flow()
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.content_identifier = (
            "BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms"
        )
        flow.state.content_hash = "759169e5b7de230616d673bd3498ac79"

        run_signup_attempt(flow)

        self.assertEqual(
            fake.graphql_operations,
            [
                "InstallmentOptionsQuery",
                "AddressAutocompleteFromPostalCodeQuery",
                "SignUpNewMemberMutation",
            ],
        )
        address_variables = dict(fake.graphql_variables)["AddressAutocompleteFromPostalCodeQuery"]
        self.assertEqual(address_variables["token"], "EC-TEST123")
        self.assertEqual(address_variables["postalCode"], "80020-100")
        signup_variables = dict(fake.graphql_variables)["SignUpNewMemberMutation"]
        billing_address = cast(dict[str, object], signup_variables["billingAddress"])
        self.assertEqual(billing_address["line1"], "Praça Tiradentes, 35, 450")

    def test_signup_attempt_sends_signup_context_risk_before_signup(self):
        flow, fake = make_flow()
        setattr(flow, "_roxy_runtime_disabled_reason", "")
        setattr(flow, "risk_signals_mode", "roxy")
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.content_identifier = (
            "BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms"
        )
        flow.state.content_hash = "759169e5b7de230616d673bd3498ac79"

        with (
            patch.object(
                flow,
                "_send_signup_context_risk_signals_with_roxy",
                return_value=True,
            ) as signup_context_risk,
            patch.object(
                flow,
                "_send_signup_field_events",
                return_value=None,
            ) as signup_field_events,
        ):
            run_signup_attempt(flow)

        signup_context_risk.assert_called_once_with(
            "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
            "EC-TEST123",
        )
        signup_field_events.assert_called_once()
        self.assertEqual(
            fake.graphql_operations,
            [
                "InstallmentOptionsQuery",
                "AddressAutocompleteFromPostalCodeQuery",
                "SignUpNewMemberMutation",
            ],
        )

    def test_signup_context_roxy_runs_even_when_legacy_enable_flag_is_off(self):
        flow, fake = make_flow(risk_signals_mode="roxy")
        setattr(flow, "_roxy_runtime_disabled_reason", "")
        send_signup_context = cast(Callable[[str, str], bool], getattr(flow, "_send_signup_context_risk_signals_with_roxy"))

        result = {
            "ok": True,
            "status": 200,
            "url": "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
            "cookies": [{"name": "datadome", "value": "dd-roxy", "domain": ".paypal.com", "path": "/"}],
            "observed": ["fraudnet_p1", "fraudnet_p2", "fraudnet_w", "identity_di_log", "datadog_rum"],
            "missing": [],
            "required_missing": [],
            "counts": {
                "fraudnet_p1": 1,
                "fraudnet_p2": 1,
                "fraudnet_w": 1,
                "identity_di_log": 1,
                "datadog_rum": 1,
            },
        }

        with (
            patch.dict(os.environ, {"PAYPAL_ENABLE_SIGNUP_CONTEXT_RISK": "0"}, clear=True),
            patch.object(flow, "_ensure_roxy_browser_for_datadome", return_value={"cdp_info": {}}),
            patch("paypal.roxy_fingerprint.run_phase1_risk_with_roxy_browser", return_value=result) as roxy_runner,
        ):
            self.assertTrue(
                send_signup_context(
                    "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
                    "EC-TEST123",
                )
            )

        roxy_runner.assert_called_once()
        signup_context = cast(dict[str, object], flow.state.risk_signals_browser_result["signup_context"])
        self.assertEqual(signup_context["correlation_id"], "EC-TEST123")
        self.assertEqual(fake.browser_cookies[-1]["value"], "dd-roxy")

    def test_signup_attempt_falls_back_to_headless_when_roxy_context_fails_before_signup(self):
        flow, _fake = make_flow(risk_signals_mode="roxy")
        setattr(flow, "_roxy_runtime_disabled_reason", "")
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.content_identifier = (
            "BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms"
        )
        flow.state.content_hash = "759169e5b7de230616d673bd3498ac79"
        calls: list[str] = []

        def roxy_signup(_signup_url: str, _token: str) -> bool:
            calls.append("roxy_signup_context")
            setattr(flow, "_roxy_runtime_disabled_reason", "roxy failed")
            return False

        def headless_signup(_signup_url: str, _token: str) -> bool:
            calls.append("headless_signup_context")
            return True

        def field_events(*_args: object, **_kwargs: object) -> None:
            calls.append("field_events")

        def post_signup(*_args: object, **_kwargs: object) -> dict[str, object]:
            calls.append("signup")
            return {"data": {"onboardAccount": {"buyer": {"userId": "user-123"}}}}

        with (
            patch.object(flow, "_send_signup_context_risk_signals_with_roxy", side_effect=roxy_signup) as roxy_context,
            patch.object(flow, "_send_signup_context_risk_signals_with_headless", side_effect=headless_signup) as headless_context,
            patch.object(flow, "_send_signup_field_events", side_effect=field_events),
            patch.object(flow, "_post_signup_with_authchallenge_ignore", side_effect=post_signup),
        ):
            run_signup_attempt(flow)

        roxy_context.assert_called_once_with(
            "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
            "EC-TEST123",
        )
        headless_context.assert_called_once_with(
            "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
            "EC-TEST123",
        )
        self.assertEqual(
            calls,
            ["roxy_signup_context", "headless_signup_context", "field_events", "signup"],
        )

    def test_signup_attempt_runs_context_before_strict_signup_preflight(self):
        flow, _fake = make_flow(risk_signals_mode="off")
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.content_identifier = (
            "BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms"
        )
        flow.state.content_hash = "759169e5b7de230616d673bd3498ac79"
        calls: list[str] = []

        def headless_signup(_signup_url: str, _token: str) -> bool:
            calls.append("headless_signup_context")
            return True

        def strict_preflight() -> None:
            calls.append("strict_preflight")
            raise RuntimeError("strict still blocks after context")

        with (
            patch("paypal.flow.strict_browser_risk_enabled", return_value=True),
            patch.object(flow, "_send_signup_context_risk_signals_with_headless", side_effect=headless_signup),
            patch.object(flow, "_strict_signup_preflight_or_raise", side_effect=strict_preflight),
            patch.object(flow, "_send_signup_field_events", side_effect=AssertionError("field events ran before strict preflight")),
            self.assertRaises(RuntimeError) as raised,
        ):
            run_signup_attempt(flow)

        self.assertIn("strict still blocks after context", str(raised.exception))
        self.assertEqual(calls, ["headless_signup_context", "strict_preflight"])

    def test_signup_attempt_blocks_when_forced_headless_context_fails(self):
        flow, _fake = make_flow(risk_signals_mode="off")
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.content_identifier = (
            "BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms"
        )
        flow.state.content_hash = "759169e5b7de230616d673bd3498ac79"

        with (
            patch.dict(os.environ, {"PAYPAL_HEADLESS_RUNTIME_FALLBACK": "1"}, clear=True),
            patch(
                "paypal.local_headless.run_local_headless_mtr_phase1",
                side_effect=RuntimeError("signup context headless unavailable"),
            ),
            patch.object(flow, "_send_signup_field_events", side_effect=AssertionError("field events ran after failed context")),
            patch.object(flow, "_post_signup_with_authchallenge_ignore", side_effect=AssertionError("SignUp ran after failed context")),
            self.assertRaises(RuntimeError) as raised,
        ):
            run_signup_attempt(flow)

        self.assertIn("signup context headless unavailable", str(raised.exception))

    def test_signup_attempt_sends_signup_context_risk_with_headless_before_signup(self):
        flow, fake = make_flow()
        setattr(flow, "risk_signals_mode", "headless")
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.content_identifier = (
            "BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms"
        )
        flow.state.content_hash = "759169e5b7de230616d673bd3498ac79"
        calls: list[str] = []

        def headless_signup(_signup_url: str, _token: str) -> bool:
            calls.append("headless_signup_context")
            return True

        def field_events(*_args: object, **_kwargs: object) -> None:
            calls.append("field_events")

        with (
            patch.object(
                flow,
                "_send_signup_context_risk_signals_with_headless",
                side_effect=headless_signup,
                create=True,
            ) as signup_context_risk,
            patch.object(
                flow,
                "_send_signup_field_events",
                side_effect=field_events,
            ) as signup_field_events,
        ):
            run_signup_attempt(flow)

        signup_context_risk.assert_called_once_with(
            "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
            "EC-TEST123",
        )
        signup_field_events.assert_called_once()
        self.assertLess(calls.index("headless_signup_context"), calls.index("field_events"))
        self.assertEqual(
            fake.graphql_operations,
            [
                "InstallmentOptionsQuery",
                "AddressAutocompleteFromPostalCodeQuery",
                "SignUpNewMemberMutation",
            ],
        )

    def test_signup_attempt_forces_headless_context_when_phase1_risk_is_off(self):
        flow, fake = make_flow(risk_signals_mode="off")
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.content_identifier = (
            "BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms"
        )
        flow.state.content_hash = "759169e5b7de230616d673bd3498ac79"
        calls: list[str] = []

        def headless_signup(_signup_url: str, _token: str) -> bool:
            calls.append("headless_signup_context")
            return True

        def field_events(*_args: object, **_kwargs: object) -> None:
            calls.append("field_events")

        with (
            patch.object(
                flow,
                "_send_signup_context_risk_signals_with_headless",
                side_effect=headless_signup,
            ) as signup_context_risk,
            patch.object(
                flow,
                "_send_signup_field_events",
                side_effect=field_events,
            ),
        ):
            run_signup_attempt(flow)

        signup_context_risk.assert_called_once_with(
            "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123",
            "EC-TEST123",
        )
        self.assertLess(calls.index("headless_signup_context"), calls.index("field_events"))
        self.assertEqual(
            fake.graphql_operations,
            [
                "InstallmentOptionsQuery",
                "AddressAutocompleteFromPostalCodeQuery",
                "SignUpNewMemberMutation",
            ],
        )

    def test_risk_signals_mode_accepts_local_headless_aliases(self):
        flow, _fake = make_flow()
        risk_signals_mode = cast(Callable[[], str], getattr(flow, "_risk_signals_mode"))

        for value in ("headless", "local_headless", "playwright", "local-playwright"):
            setattr(flow, "risk_signals_mode", value)
            self.assertEqual(risk_signals_mode(), "headless")

        setattr(flow, "risk_signals_mode", "off")
        self.assertEqual(risk_signals_mode(), "off")

    def test_risk_runtime_outputs_redact_mtr_artifacts(self):
        flow, _fake = make_flow()
        apply_headless = cast(Callable[[dict[str, object]], None], getattr(flow, "_apply_headless_mtr_result"))
        risk_report = cast(Callable[[], dict[str, object]], getattr(flow, "_risk_runtime_report"))
        strict_signup = cast(Callable[[], None], getattr(flow, "_strict_signup_preflight_or_raise"))
        sanitize_for_log = cast(Callable[[object], object], getattr(session_module, "sanitize_for_log"))
        safe_result_payload = cast(Callable[[object], object], getattr(web_module, "safe_result_payload"))

        apply_headless(
            {
                "status": 200,
                "url": "https://www.paypal.com/pay?token=EC-SECRET123456",
                "requestId": "REQ-SECRET-123456789",
                "sealedResult": "sealed-optimized-secret",
                "visitorToken": "visitor-optimized-secret",
                "x0_status": 200,
                "post_status": 200,
            }
        )
        public_payload = {
            "requestId": "REQ-SECRET-123456789",
            "sealedResult": "sealed-optimized-secret",
            "visitorToken": "visitor-optimized-secret",
            "correlationId": "CORR-SECRET-123456789",
            "clientMetadataId": "CMID-SECRET-123456789",
            "cmid": "CMID-SECRET-123456789",
            "ssrt": "SSRT-SECRET-123456789",
            "ctxId": "CTX-SECRET-123456789",
            "billingAgreementToken": "BA-BILLING-SECRET123456789",
            "url": (
                "https://www.paypal.com/pay?token=EC-URL-SECRET123456789"
                "&ssrt=SSRT-URL-SECRET-123456789"
                "&ctxId=CTX-URL-SECRET-123456789"
                "&cmid=CMID-URL-SECRET-123456789"
                "&clientMetadataId=CLIENTMETA-URL-SECRET-123456789"
                "&requestId=REQ-URL-SECRET-123456789"
                "&billingAgreementToken=BA-URL-BILLING-SECRET123456789"
            ),
            "error": (
                "browser failed at https://www.paypal.com/pay?token=EC-ERROR-SECRET123456789"
                "&ssrt=SSRT-ERROR-SECRET-123456789"
                "&ctxId=CTX-ERROR-SECRET-123456789"
                "&cmid=CMID-ERROR-SECRET-123456789"
                "&clientMetadataId=CLIENTMETA-ERROR-SECRET-123456789"
                "&requestId=REQ-ERROR-SECRET-123456789"
                "&billingAgreementToken=BA-ERROR-BILLING-SECRET123456789"
                " billingAgreementToken=BA-INLINE-BILLING-SECRET123456789"
                " {\"requestId\":\"REQ-JSON-SECRET-123456789\","
                "\"billingAgreementToken\":\"BA-JSON-BILLING-SECRET123456789\"}"
            ),
        }
        url_secret_values = [
            "EC-URL-SECRET123456789",
            "SSRT-URL-SECRET-123456789",
            "CTX-URL-SECRET-123456789",
            "CMID-URL-SECRET-123456789",
            "CLIENTMETA-URL-SECRET-123456789",
            "REQ-URL-SECRET-123456789",
            "EC-ERROR-SECRET123456789",
            "SSRT-ERROR-SECRET-123456789",
            "CTX-ERROR-SECRET-123456789",
            "CMID-ERROR-SECRET-123456789",
            "CLIENTMETA-ERROR-SECRET-123456789",
            "REQ-ERROR-SECRET-123456789",
            "BA-URL-BILLING-SECRET123456789",
            "BA-ERROR-BILLING-SECRET123456789",
            "BA-INLINE-BILLING-SECRET123456789",
            "REQ-JSON-SECRET-123456789",
            "BA-JSON-BILLING-SECRET123456789",
            "BA-BILLING-SECRET123456789",
        ]
        flow.state.risk_signals_browser_result = {"ok": False, **public_payload}
        flow.state.datadome_browser_result = dict(public_payload)

        report_text = json.dumps(risk_report(), ensure_ascii=False)
        for secret in public_payload.values():
            self.assertNotIn(str(secret), report_text)

        log_text = json.dumps(sanitize_for_log(public_payload), ensure_ascii=False)
        web_text = json.dumps(safe_result_payload(public_payload), ensure_ascii=False)
        for secret in public_payload.values():
            self.assertNotIn(str(secret), log_text)
            self.assertNotIn(str(secret), web_text)
        for secret in url_secret_values:
            self.assertNotIn(secret, log_text)
            self.assertNotIn(secret, web_text)

        flow.state.mtr_sealed_result = ""
        flow.state.mtr_browser_result = dict(public_payload)
        with patch.dict(os.environ, {"PAYPAL_STRICT_BROWSER_RISK": "1"}, clear=True), self.assertRaises(RuntimeError) as raised:
            strict_signup()
        strict_error = str(raised.exception)
        for secret in public_payload.values():
            self.assertNotIn(str(secret), strict_error)
        for secret in url_secret_values:
            self.assertNotIn(secret, strict_error)

    def test_browser_runtime_exception_paths_redact_raised_errors(self):
        error_text = (
            "browser failed at https://www.paypal.com/pay?token=EC-ERROR-SECRET123456789"
            "&ssrt=SSRT-ERROR-SECRET-123456789"
            "&ctxId=CTX-ERROR-SECRET-123456789"
            "&cmid=CMID-ERROR-SECRET-123456789"
            "&clientMetadataId=CLIENTMETA-ERROR-SECRET-123456789"
            "&requestId=REQ-ERROR-SECRET-123456789"
            "&billingAgreementToken=BA-ERROR-BILLING-SECRET123456789"
            " billingAgreementToken=BA-INLINE-BILLING-SECRET123456789"
            " {\"requestId\":\"REQ-JSON-SECRET-123456789\","
            "\"billingAgreementToken\":\"BA-JSON-BILLING-SECRET123456789\"}"
        )
        raw_values = [
            "EC-ERROR-SECRET123456789",
            "SSRT-ERROR-SECRET-123456789",
            "CTX-ERROR-SECRET-123456789",
            "CMID-ERROR-SECRET-123456789",
            "CLIENTMETA-ERROR-SECRET-123456789",
            "REQ-ERROR-SECRET-123456789",
            "BA-ERROR-BILLING-SECRET123456789",
            "BA-INLINE-BILLING-SECRET123456789",
            "REQ-JSON-SECRET-123456789",
            "BA-JSON-BILLING-SECRET123456789",
        ]

        def assert_redacted(exc: RuntimeError, result: object) -> None:
            text = f"{exc} {json.dumps(result, ensure_ascii=False)}"
            for raw_value in raw_values:
                self.assertNotIn(raw_value, text)


        flow, _fake = make_flow(risk_signals_mode="roxy")
        setattr(flow, "_roxy_runtime_disabled_reason", "")
        setattr(flow, "risk_signals_mode", "roxy")
        send_roxy_context = cast(Callable[[str, str], bool], getattr(flow, "_send_signup_context_risk_signals_with_roxy"))
        with (
            patch.dict(os.environ, {"PAYPAL_ROXY_RUNTIME_FALLBACK": "0"}, clear=True),
            patch.object(flow, "_ensure_roxy_browser_for_datadome", return_value={"cdp_info": {}}),
            patch("paypal.roxy_fingerprint.run_phase1_risk_with_roxy_browser", side_effect=RuntimeError(error_text)),
            self.assertRaises(RuntimeError) as raised,
        ):
            send_roxy_context("https://www.paypal.com/checkoutweb/signup?token=EC-TEST123", "EC-TEST123")
        assert_redacted(raised.exception, flow.state.risk_signals_browser_result)

        flow, _fake = make_flow(risk_signals_mode="off")
        send_headless_context = cast(Callable[[str, str], bool], getattr(flow, "_send_signup_context_risk_signals_with_headless"))
        with (
            patch("paypal.local_headless.run_local_headless_mtr_phase1", side_effect=RuntimeError(error_text)),
            self.assertRaises(RuntimeError) as raised,
        ):
            send_headless_context("https://www.paypal.com/checkoutweb/signup?token=EC-TEST123", "EC-TEST123")
        assert_redacted(raised.exception, flow.state.risk_signals_browser_result)

    def test_signup_context_headless_reuses_session_with_new_page(self):
        flow, fake = make_flow()
        setattr(flow, "risk_signals_mode", "headless")
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "EC-TEST123"
        flow.state.mtr_api_key = "api-key"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        fake.browser_cookies.append({"name": "nsid", "value": "nsid-123", "domain": ".paypal.com", "path": "/"})

        class FakeHeadlessSession:
            def __init__(self) -> None:
                self.imported: list[list[dict[str, object]]] = []

            def import_cookies(self, cookies: list[dict[str, object]] | None) -> None:
                self.imported.append([dict(cookie) for cookie in (cookies or [])])

        headless_session = FakeHeadlessSession()
        setattr(flow, "_headless_session", headless_session)

        def fake_headless(_signup_url: str, **kwargs: object) -> dict[str, object]:
            self.assertIs(kwargs.get("session"), headless_session)
            self.assertEqual(kwargs.get("stage"), "signup_context")
            self.assertTrue(kwargs.get("new_page"))
            self.assertEqual(kwargs.get("app_id"), "CHECKOUTUINODEWEB_ONBOARDING_LITE")
            self.assertEqual(kwargs.get("correlation_id"), "EC-TEST123")
            return {
                "ok": True,
                "runtime": "headless",
                "status": 200,
                "url": _signup_url,
                "cookies": [{"name": "datadome", "value": "dd-signup", "domain": ".paypal.com", "path": "/"}],
                "observed": ["fraudnet_p1", "fraudnet_p2", "fraudnet_w", "identity_di_log", "datadog_rum"],
                "missing": [],
                "required_missing": [],
                "counts": {
                    "fraudnet_p1": 1,
                    "fraudnet_p2": 1,
                    "fraudnet_w": 1,
                    "identity_di_log": 1,
                    "datadog_rum": 1,
                },
                "debug_log_path": "/tmp/headless-signup-test",
                "intercept": {"allowed_count": 4, "blocked_count": 1},
            }

        send_signup_context = cast(Callable[[str, str], bool], getattr(flow, "_send_signup_context_risk_signals_with_headless"))
        with patch("paypal.local_headless.run_local_headless_mtr_phase1", side_effect=fake_headless) as headless_runner:
            self.assertTrue(send_signup_context("https://www.paypal.com/checkoutweb/signup?token=EC-TEST123", "EC-TEST123"))

        headless_runner.assert_called_once()
        self.assertEqual(headless_session.imported, [[{"name": "nsid", "value": "nsid-123", "domain": ".paypal.com", "path": "/"}]])
        signup_context = cast(dict[str, object], flow.state.risk_signals_browser_result["signup_context"])
        self.assertEqual(signup_context["runtime"], "headless")
        self.assertEqual(signup_context["intercept"], {"allowed_count": 4, "blocked_count": 1})
        self.assertEqual(signup_context["debug_log_path"], "/tmp/headless-signup-test")
        self.assertEqual(fake.browser_cookies[-1]["value"], "dd-signup")

    def test_captcha_automation_modes_are_configurable(self):
        captcha_mode = cast(Callable[[], str], getattr(session_module, "paypal_captcha_bypass_mode"))
        frontend_disable = cast(Callable[[], bool], getattr(session_module, "captcha_frontend_disable_enabled"))

        with patch.dict(os.environ, {
            "PAYPAL_CAPTCHA_BYPASS_MODE": "frontend_disable",
            "PAYPAL_STRICT_BROWSER_RISK": "1",
        }):
            self.assertEqual(captcha_mode(), "manual_required")
            self.assertFalse(frontend_disable())

        with patch.dict(os.environ, {
            "PAYPAL_CAPTCHA_BYPASS_MODE": "frontend_disable",
            "PAYPAL_STRICT_BROWSER_RISK": "1",
            "PAYPAL_ALLOW_SYNTHETIC_CAPTCHA": "1",
        }):
            self.assertEqual(captcha_mode(), "frontend_disable")
            self.assertTrue(frontend_disable())

        with patch.dict(os.environ, {"PAYPAL_CAPTCHA_BYPASS_MODE": "frontend_disable"}, clear=True):
            self.assertEqual(captcha_mode(), "frontend_disable")
            self.assertTrue(frontend_disable())

        with patch.dict(os.environ, {"PAYPAL_CAPTCHA_BYPASS_MODE": "capsolver"}):
            self.assertEqual(captcha_mode(), "manual_required")
            self.assertFalse(frontend_disable())

        with patch.dict(os.environ, {
            "PAYPAL_CAPTCHA_BYPASS_MODE": "capsolver",
            "PAYPAL_ALLOW_EXTERNAL_CAPTCHA_SOLVER": "1",
        }):
            self.assertEqual(captcha_mode(), "manual_required")
            self.assertFalse(frontend_disable())

    def test_authchallenge_validator_manual_mode_does_not_call_local_solvers(self):
        flow, _fake = make_flow()
        validator = cast(Callable[[str, str], object], getattr(flow, "_validate_authchallenge_if_possible"))
        challenge_html = '<html><body authchallenge data-captcha-type="hcaptcha"></body></html>'

        with (
            patch.dict(os.environ, {"PAYPAL_CAPTCHA_BYPASS_MODE": "manual_required"}),
            patch.object(flow, "_frontend_disable_authchallenge_close", side_effect=AssertionError("frontend close called")),
            patch.object(flow, "_validate_paypal_hcaptcha", side_effect=AssertionError("hcaptcha solver called")),
            patch.object(flow, "_validate_paypal_recaptcha", side_effect=AssertionError("recaptcha solver called")),
        ):
            self.assertFalse(validator(challenge_html, "https://www.paypal.com/checkoutweb/signup"))

    def test_authchallenge_validator_emits_datadog_rum_when_challenge_seen(self):
        flow, fake = make_flow()
        validator = cast(Callable[[str, str], object], getattr(flow, "_validate_authchallenge_if_possible"))
        challenge_html = '<html><body authchallenge data-captcha-type="hcaptcha"></body></html>'

        with patch.dict(os.environ, {"PAYPAL_CAPTCHA_BYPASS_MODE": "manual_required"}):
            self.assertFalse(validator(challenge_html, "https://www.paypal.com/checkoutweb/signup"))

        rum_posts = [
            (url, kwargs)
            for url, kwargs in fake.post_calls
            if "browser-intake-us5-datadoghq.com/api/v2/rum" in url
        ]
        self.assertEqual(len(rum_posts), 2)
        event_types: set[str] = set()
        for _url, kwargs in rum_posts:
            params = cast(dict[str, str], kwargs["params"])
            self.assertEqual(params["dd-api-key"], "pubbc14edcb954efe6e30dbd32fac3e7fd7")
            self.assertNotIn("ddtags", params)
            self.assertEqual(params["_dd.api"], "fetch")
            for line in str(kwargs["content"]).splitlines():
                if line.strip():
                    event_data = cast(dict[str, object], json.loads(line))
                    event_types.add(str(event_data.get("type")))
        self.assertIn("view", event_types)
        self.assertIn("action", event_types)

    def test_authchallenge_capsolver_mode_is_removed(self):
        flow, _fake = make_flow()
        validator = cast(Callable[[str, str], object], getattr(flow, "_validate_authchallenge_if_possible"))
        challenge_html = '<html><body authchallenge data-captcha-type="hcaptchapassive"></body></html>'
        signup_url = "https://www.paypal.com/checkoutweb/signup"

        with (
            patch.dict(os.environ, {
                "PAYPAL_CAPTCHA_BYPASS_MODE": "capsolver",
                "PAYPAL_ALLOW_EXTERNAL_CAPTCHA_SOLVER": "1",
            }),
            patch.object(flow, "_frontend_disable_authchallenge_close", side_effect=AssertionError("frontend skip called")),
            patch.object(flow, "_validate_authchallenge_real_solver", side_effect=AssertionError("real solver called")),
        ):
            self.assertFalse(validator(challenge_html, signup_url))

    def test_hcaptchapassive_node_helper_is_skipped_in_strict_mode(self):
        flow, _fake = make_flow()
        mint = cast(Callable[..., tuple[str, dict[str, object]]], getattr(flow, "_mint_hcaptcha_passive_token"))

        with (
            patch.dict(os.environ, {
                "PAYPAL_HCAPTCHA_PASSIVE_SOLVER": "node",
                "PAYPAL_STRICT_BROWSER_RISK": "1",
            }),
            patch.object(flow, "_mint_hcaptcha_passive_token_via_node", side_effect=AssertionError("node helper called")),
        ):
            token, data = mint(
                iframe_url="https://www.paypalobjects.com/auth/hcaptcha/hcaptchapassive.html",
                parent_url="https://www.paypal.com/checkoutweb/signup",
                site_key="site-key",
            )

        self.assertEqual(token, "")
        self.assertEqual(data, {})

    def test_signup_authchallenge_returns_manual_error_without_fallback(self):
        flow, _fake = make_flow()
        signup = cast(
            Callable[[str, str, dict[str, object]], dict[str, object]],
            getattr(flow, "_post_signup_with_authchallenge_ignore"),
        )
        challenge = PayPalAuthChallenge(
            "SignUpNewMemberMutation",
            200,
            "debug-123",
            '<html><body authchallenge data-captcha-type="hcaptchapassive"></body></html>',
        )

        with (
            patch.dict(os.environ, {"PAYPAL_CAPTCHA_BYPASS_MODE": "manual_required"}),
            patch.object(flow, "_post_signup_once", side_effect=challenge),
            patch.object(flow, "_post_authchallenge_form_close", side_effect=AssertionError("form close called")),
        ):
            result = signup("EC-TEST123", "https://www.paypal.com/checkoutweb/signup", {})

        errors = cast(list[dict[str, object]], result["errors"])
        self.assertEqual(errors[0]["message"], "AUTHCHALLENGE_MANUAL_VERIFICATION_REQUIRED")
        data = cast(dict[str, object], errors[0]["data"])
        self.assertTrue(data["manualVerificationRequired"])

    def test_modxo_cfci_never_replays_synthetic_captcha_solved_marker(self):
        flow, _fake = make_flow()
        flow.state.paypal_captcha_solved = True
        cfci = cast(Callable[[str], str], getattr(flow, "_modxo_cfci"))

        value = cfci("Continue_To_Payment")

        self.assertEqual(value, "modxo_vaulted_not_recurring-Continue_To_Payment")
        self.assertNotIn("CAPTCHA_SOLVED", value)

    def test_paypal_return_url_gets_ba_token_for_stripe_bridge(self):
        flow, _fake = make_flow()
        add_ba = cast(
            Callable[[str, str], str],
            getattr(flow, "_paypal_return_url_with_ba_token"),
        )

        value = add_ba(
            "https://pm-redirects.stripe.com/return/acct_x/pa_nonce_y?status=success&token=EC-TEST123",
            "BA-TESTTOKEN123",
        )

        self.assertIn("status=success", value)
        self.assertIn("token=EC-TEST123", value)
        self.assertIn("ba_token=BA-TESTTOKEN123", value)

    def test_mtr_config_and_urls_are_extracted_for_diagnostics(self):
        extract_mtr_config = cast(Callable[[str], dict[str, object]], getattr(mtr_module, "extract_mtr_config"))
        ensure_mtr_config = cast(Callable[..., bool], getattr(mtr_module, "ensure_mtr_config"))
        extract_dfp_script_url = cast(Callable[[str], str], getattr(mtr_module, "extract_dfp_script_url"))
        mtr_get_url = cast(Callable[[str], str], getattr(mtr_module, "mtr_get_url"))
        mtr_post_url = cast(Callable[..., str], getattr(mtr_module, "mtr_post_url"))
        html = """
        <html><head>
        <script src="https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"></script>
        <script id="dfpconfig" type="application/json">
        {"dfpChannel":"iwc-mxo","clientMetaDataId":"BA-37R61061EU582084R","isQA":false,"fppAPIKey":"QBzalmMuDFJIiZNebIWt"}
        </script>
        </head></html>
        """

        config = extract_mtr_config(html)

        self.assertEqual(config["dfpChannel"], "iwc-mxo")
        self.assertEqual(config["clientMetaDataId"], "BA-37R61061EU582084R")
        self.assertEqual(config["fppAPIKey"], "QBzalmMuDFJIiZNebIWt")
        escaped = r'''
        <script>self.__next_f.push(["{\"dfpChannel\":\"iwc-mxo\",\"clientMetaDataId\":\"BA-ESCAPED123\",\"isQA\":false,\"fppAPIKey\":\"QBzalmMuDFJIiZNebIWt\"}"])</script>
        '''
        escaped_config = extract_mtr_config(escaped)
        self.assertEqual(escaped_config["clientMetaDataId"], "BA-ESCAPED123")
        flight = (
            r'41:[\"$\",\"$L63\",null,{\"dfpMetaData\":{\"dfpSrc\":\"https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js\",'
            r'\"dfpConfig\":\"{\\\"dfpChannel\\\":\\\"iwc-mxo\\\",\\\"clientMetaDataId\\\":\\\"BA-FLIGHT123\\\",\\\"isQA\\\":false,\\\"fppAPIKey\\\":\\\"QBzalmMuDFJIiZNebIWt\\\"}\",'
            r'\"isEligibleForDFP\":false}}]'
        )
        flight_config = extract_mtr_config(flight)
        self.assertEqual(flight_config["dfpChannel"], "iwc-mxo")
        self.assertEqual(flight_config["clientMetaDataId"], "BA-FLIGHT123")
        self.assertEqual(flight_config["fppAPIKey"], "QBzalmMuDFJIiZNebIWt")
        self.assertEqual(extract_mtr_config('{"clientMetadataId":"73e1b8cb-5390-4faa-a0cc-8c40de799d4b"}'), {})
        self.assertTrue(extract_dfp_script_url(html).endswith("/dfp.js"))
        self.assertIn("/mtr/1a7c3460cd8c343771081839499ed7a0/AvQ9/", mtr_get_url("QBzalmMuDFJIiZNebIWt"))
        post = mtr_post_url(
            channel="iwc-mxo",
            cmid="BA-37R61061EU582084R",
            browser_timezone="Pacific/Honolulu",
            api_key="QBzalmMuDFJIiZNebIWt",
        )
        self.assertIn("cmid=BA-37R61061EU582084R", post)
        self.assertIn("btz=Pacific%2FHonolulu", post)
        flow, _fake = make_flow()
        with patch.dict(os.environ, {"PAYPAL_MTR_API_KEY": "ENVKEY123"}, clear=True):
            self.assertTrue(ensure_mtr_config(flow.state, page_url="https://www.paypal.com/pay?token=BA-FALLBACK123"))
        self.assertEqual(flow.state.mtr_channel, "iwc-mxo")
        self.assertEqual(flow.state.mtr_client_metadata_id, "BA-FALLBACK123")
        self.assertEqual(flow.state.mtr_api_key, "ENVKEY123")

    def test_mtr_python_generated_request_contains_report_fields(self):
        mtr_runtime_mode = cast(Callable[[], str], getattr(mtr_module, "mtr_runtime_mode"))
        build_mtr_request_object = cast(Callable[..., dict[str, object]], getattr(mtr_module, "build_mtr_request_object"))
        serialize_mtr_body = cast(Callable[[dict[str, object]], bytes], getattr(mtr_module, "serialize_mtr_body"))
        flow, _fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        flow.state.browser_profile.update(
            {
                "mtr_outer_width": 1242,
                "mtr_outer_height": 788,
                "mtr_inner_width": 567,
                "mtr_inner_height": 700,
            }
        )

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mtr_runtime_mode(), "python_generated")
            payload = build_mtr_request_object(
                flow.state,
                page_url="https://www.paypal.com/pay?token=BA-TESTTOKEN123",
                x0_token="bootstrap-token",
        )
        body = serialize_mtr_body(payload)
        numbered_signal_keys = [key for key in payload if re.fullmatch(r"s\d+", key)]
        expected_screen_width = cast(int, flow.state.screen["width"])
        expected_screen_height = cast(int, flow.state.screen["height"])
        expected_screen = {"w": expected_screen_width, "h": expected_screen_height}

        self.assertEqual(payload["c"], "QBzalmMuDFJIiZNebIWt")
        self.assertEqual(payload["s56"], {"s": 0, "v": "bootstrap-token"})
        self.assertEqual(payload["m"], "s")
        self.assertEqual(payload["gt"], 1)
        self.assertEqual(payload["ab"], {"noop": "b"})
        self.assertEqual(payload["lr"], [])
        self.assertEqual(payload["s55"], {"s": -1, "v": None})
        self.assertEqual(payload["s67"], {"s": -1, "v": None})
        self.assertEqual(cast(dict[str, object], payload["s4"])["v"], 8)
        self.assertEqual(cast(dict[str, object], payload["s5"])["v"], [expected_screen_height, expected_screen_width])
        self.assertIn("s48", payload)
        self.assertEqual(payload["mo"], ["id", "bd", "si"])
        self.assertEqual(payload["url"], "https://www.paypal.com/pay?token=BA-TESTTOKEN123")
        self.assertGreaterEqual(len(numbered_signal_keys), 127)
        self.assertNotIn("id", payload)
        self.assertNotIn("bd", payload)
        self.assertNotIn("si", payload)
        self.assertNotIn("fingerprintHash", payload)
        self.assertNotIn("profileHash", payload)
        self.assertNotIn("pr", payload)
        s58 = cast(dict[str, object], cast(dict[str, object], payload["s58"])["v"])
        brands = cast(list[dict[str, str]], s58["b"])
        expected_chrome_major = str(flow.state.browser_profile.get("chrome_major") or "150")
        expected_chrome_full_version = str(
            flow.state.browser_profile.get("chrome_full_version")
            or f"{expected_chrome_major}.0.0.0"
        )
        self.assertIn({"b": "Google Chrome", "v": expected_chrome_major}, brands)
        hints = cast(dict[str, str], s58["h"])
        full_version_list = cast(list[dict[str, str]], json.loads(hints["fullVersionList"]))
        self.assertIn({"brand": "Google Chrome", "version": expected_chrome_full_version}, full_version_list)
        self.assertEqual(cast(dict[str, object], payload["s49"])["v"], [0.09999999776482582, 0.10000000149011612])
        self.assertEqual(cast(dict[str, object], payload["s84"])["v"], expected_screen)
        self.assertEqual(
            cast(dict[str, object], payload["s150"])["v"],
            {"outerWidth": 1242, "outerHeight": 788, "innerWidth": 567, "innerHeight": 700},
        )
        s94 = cast(dict[str, object], cast(dict[str, object], payload["s94"])["v"])
        self.assertRegex(cast(str, s94["u"]), r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertNotEqual(s94["u"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(cast(dict[str, object], payload["s131"])["v"], ["lang", "dir", "data-ppui-mode"])
        self.assertIn("sendBeacon", cast(list[str], cast(dict[str, object], payload["s145"])["v"]))
        self.assertIsInstance(cast(dict[str, object], payload["s101"])["v"], str)
        for key in numbered_signal_keys:
            self.assertEqual(set(cast(dict[str, object], payload[key])), {"s", "v"})
        decoded = decode_mtr_body(body)
        self.assertEqual(decoded["m"], "s")
        self.assertEqual(decoded["gt"], 1)
        self.assertEqual(decoded["ab"], {"noop": "b"})
        self.assertEqual(cast(dict[str, object], decoded["s4"])["v"], 8)
        self.assertEqual(cast(dict[str, object], decoded["s5"])["v"], [expected_screen_height, expected_screen_width])
        self.assertEqual(cast(dict[str, object], decoded["s49"])["v"], [0.09999999776482582, 0.10000000149011612])
        self.assertEqual(cast(dict[str, object], decoded["s84"])["v"], expected_screen)
        self.assertEqual(cast(dict[str, object], decoded["s131"])["v"], ["lang", "dir", "data-ppui-mode"])
        decoded_numbered_signal_keys = [key for key in decoded if re.fullmatch(r"s\d+", key)]
        self.assertGreaterEqual(len(decoded_numbered_signal_keys), 127)
        self.assertGreater(len(body), 200)

    def test_mtr_python_generated_accepts_browser_runtime_overrides(self):
        build_mtr_request_object = cast(Callable[..., dict[str, object]], getattr(mtr_module, "build_mtr_request_object"))
        serialize_mtr_body = cast(Callable[[dict[str, object]], bytes], getattr(mtr_module, "serialize_mtr_body"))
        flow, _fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        flow.state.browser_profile.update(
            {
                "fingerprint_source": "browser",
                "mtr_payload_overrides": {"ab": {"noop": "a"}},
                "uaData": {
                    "brands": [
                        {"brand": "Chromium", "version": "136"},
                        {"brand": "HeadlessChrome", "version": "136"},
                        {"brand": "Not.A/Brand", "version": "99"},
                    ],
                    "mobile": False,
                    "platform": "Windows",
                    "highEntropy": {
                        "architecture": "x64",
                        "bitness": "64",
                        "brands": [
                            {"brand": "Chromium", "version": "136"},
                            {"brand": "HeadlessChrome", "version": "136"},
                            {"brand": "Not.A/Brand", "version": "99"},
                        ],
                        "fullVersionList": [
                            {"brand": "Chromium", "version": "136.0.7103.25"},
                            {"brand": "HeadlessChrome", "version": "136.0.7103.25"},
                            {"brand": "Not.A/Brand", "version": "99.0.0.0"},
                        ],
                        "mobile": False,
                        "model": "",
                        "platform": "Windows",
                        "platformVersion": "10.0",
                        "uaFullVersion": "136.0.7103.25",
                    },
                },
            }
        )
        override_values: dict[str, object] = {
            "s17": {"geometry": "f7ca1dbf30e1191e5b09e202df76dccd", "text": "33858ed1ba16549e2cf8422740ea8b25", "winding": True},
            "s20": ["Calibri", "MT Extra", "Marlett", "Segoe UI Light", "SimHei"],
            "s21": 124.04347527516074,
            "s29": 1840547330,
            "s45": [1783326987454, 1783290987454],
            "s46": "5f030fa7d2e5f9f757bfaf81642eb1a6",
            "s48": [-567900424, 1443817542, -319475134, 242472274, 505067316, -1698610800],
            "s51": {"apple": 149.3125, "default": 149.3125, "min": 9.34375, "mono": 132.609375, "sans": 144.015625, "serif": 149.3125, "system": 151.703125},
            "s75": {"contextAttributes": "ctx", "parameters": "params", "parameters2": "params2", "shaderPrecisions": "shader", "extensions": "ext", "extensionParameters": "extp", "extensionParameters2": "extp2", "unsupportedExtensions": []},
            "s76": "ee7c3bfc83ff69be44f42ae889cbfd7d",
            "s92": {"bottom": 26, "font": "\"Times New Roman\"", "height": 17, "left": 8, "right": 274.078125, "top": 9, "width": 266.078125, "x": 8, "y": 9},
            "s93": {"bottom": 26, "font": "\"Times New Roman\"", "height": 17, "left": 8, "right": 1608, "top": 9, "width": 1600, "x": 8, "y": 9},
            "s94": {"e": [], "s": [], "u": "91967c04-8734-bca7-75da-05127979f0c4"},
            "s104": 0,
            "s154": {"ck": False, "fp": False, "pr": False, "pt": False, "wv": False, "wvp": False},
            "s200": 1783326986905.1,
        }
        flow.state.device_fingerprint.update(
            {"mtr_signal_overrides": {key: {"s": 0, "v": value} for key, value in override_values.items()}}
        )

        payload = build_mtr_request_object(
            flow.state,
            page_url="https://www.paypal.com/pay?token=BA-TESTTOKEN123",
            x0_token="bootstrap-token",
        )

        self.assertEqual(payload["ab"], {"noop": "a"})
        s58 = cast(dict[str, object], cast(dict[str, object], payload["s58"])["v"])
        self.assertEqual(
            s58["b"],
            [
                {"b": "Chromium", "v": "136"},
                {"b": "HeadlessChrome", "v": "136"},
                {"b": "Not.A/Brand", "v": "99"},
            ],
        )
        hints = cast(dict[str, str], s58["h"])
        self.assertEqual(hints["platform"], "Windows")
        self.assertEqual(hints["uaFullVersion"], "136.0.7103.25")
        self.assertEqual(json.loads(hints["fullVersionList"])[1], {"brand": "HeadlessChrome", "version": "136.0.7103.25"})
        for key, expected in override_values.items():
            self.assertEqual(cast(dict[str, object], payload[key])["v"], expected)

        decoded = decode_mtr_body(serialize_mtr_body(payload))
        self.assertEqual(decoded["ab"], {"noop": "a"})
        for key, expected in override_values.items():
            self.assertEqual(cast(dict[str, object], decoded[key])["v"], expected)

    def test_mtr_python_generated_recomputes_browser_source_facts_without_signal_overrides(self):
        build_mtr_request_object = cast(Callable[..., dict[str, object]], getattr(mtr_module, "build_mtr_request_object"))
        flow, _fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        flow.state.browser_profile.update(
            {
                "fingerprint_source": "browser",
                "timezone_offset_minutes": 600,
            }
        )
        math_rect = {"bottom": 26, "font": "\"Times New Roman\"", "height": 17, "left": 8, "right": 274.078125, "top": 9, "width": 266.078125, "x": 8, "y": 9}
        emoji_rect = {"bottom": 26, "font": "\"Times New Roman\"", "height": 17, "left": 8, "right": 1608, "top": 9, "width": 1600, "x": 8, "y": 9}
        flow.state.device_fingerprint.update(
            {
                "ab_noop": "a",
                "canvas_geometry_data_url": "geometry-source",
                "canvas_text_data_url": "text-source",
                "canvas_winding": True,
                "math_fingerprint_source": "math-source",
                "webgl_render_data_url": "webgl-source",
                "random_probe_randoms": [0.9, 0.1, 0.8, 0.25, 0.75, 0.5, 0.625],
                "mtr_now_ms": 1783338679067,
                "session_storage_uuid": "59e1e695-0096-5ea1-ee5d-93511a5c5061",
                "window_key_slice": ["event", "clientInformation", "screenLeft", "screenTop"],
                "mathml_rect": math_rect,
                "emoji_rect": emoji_rect,
            }
        )

        payload = build_mtr_request_object(
            flow.state,
            page_url="https://www.paypal.com/pay?token=BA-TESTTOKEN123",
            x0_token="bootstrap-token",
        )

        self.assertNotIn("mtr_signal_overrides", flow.state.device_fingerprint)
        self.assertEqual(payload["ab"], {"noop": "a"})
        self.assertEqual(
            cast(dict[str, object], payload["s17"])["v"],
            {"winding": True, "geometry": "fc30f7d7c0ab772fce6f1afc987b064f", "text": "c00aa87eab9586b35743952deb6b6293"},
        )
        self.assertEqual(cast(dict[str, object], payload["s45"])["v"], [1783338679067, 1783302679067])
        self.assertEqual(cast(dict[str, object], payload["s46"])["v"], "41dc8439246434150a668cf1a2eae56f")
        self.assertEqual(cast(dict[str, object], payload["s48"])["v"], [1717986918, -1503238553, 1181116006, -1073741824, 536870912, -268435456])
        self.assertEqual(cast(dict[str, object], payload["s76"])["v"], "43e68ab9dd8b0db8d7f3df4c940a9118")
        self.assertEqual(
            cast(dict[str, object], payload["s77"])["v"],
            {
                "1001261735": {"i": True, "t": None, "s": "ent", "e": 12, "p": "undefined"},
                "1559911021": {"i": True, "t": None, "s": "ion", "e": 12, "p": "undefined"},
                "701226668": {"i": True, "t": None, "s": "eft", "e": 12, "p": "undefined"},
                "24374072": {"i": True, "t": None, "s": "Top", "e": 12, "p": "undefined"},
            },
        )
        self.assertEqual(cast(dict[str, object], payload["s92"])["v"], math_rect)
        self.assertEqual(cast(dict[str, object], payload["s93"])["v"], emoji_rect)
        self.assertEqual(cast(dict[str, object], cast(dict[str, object], payload["s94"])["v"])["u"], "59e1e695-0096-5ea1-ee5d-93511a5c5061")

    def _datadog_action_params(self, fake: FakeSession, action_name: str) -> dict[str, str]:
        for _url, kwargs in fake.post_calls:
            params = cast(dict[str, str], kwargs.get("params", {}))
            if "browser-intake-us5-datadoghq.com/api/v2/rum" not in _url:
                continue
            for line in str(kwargs.get("content", "")).splitlines():
                if not line.strip():
                    continue
                event = cast(dict[str, object], json.loads(line))
                if event.get("type") != "action":
                    continue
                action = cast(dict[str, object], event.get("action", {}))
                target = cast(dict[str, object], action.get("target", {}))
                if target.get("name") == action_name:
                    return params
        raise AssertionError(f"missing Datadog action {action_name}")

    def test_weasley_signup_actions_use_xhr_datadog_api(self):
        flow, fake = make_flow()
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"

        def complete_signup(_token: str, _signup_url: str) -> None:
            flow.state.euat_token = "EUAT-TEST"
            flow.state.user_id = "USER12345678"

        phase = cast(Callable[[], None], getattr(flow, "_phase3_signup_and_2fa"))
        with (
            patch.object(flow, "_send_tealeaf_data"),
            patch.object(flow, "_send_idapps_get_otp_challenge"),
            patch.object(flow, "_confirm_phone_with_retry"),
            patch.object(flow, "_send_tealeaf_form_interaction_batch"),
            patch.object(flow, "_ensure_live_signup_content_manifest"),
            patch.object(flow, "_content_metadata_is_unresolved", return_value=False),
            patch.object(flow, "_strict_signup_preflight_or_raise"),
            patch.object(flow, "_signup_with_card_retry", side_effect=complete_signup),
            patch.object(flow, "_ensure_euat_cookie"),
            patch("paypal.flow.send_analytics_ts"),
        ):
            phase()

        self.assertEqual(
            self._datadog_action_params(fake, "signup_form_fill")["ddtags"],
            "sdk_version:5.35.1,api:xhr,service:weasley(checkoutuinodeweb),version:ebcfab6",
        )
        self.assertEqual(
            self._datadog_action_params(fake, "signup_complete")["ddtags"],
            "sdk_version:5.35.1,api:xhr,service:weasley(checkoutuinodeweb),version:ebcfab6",
        )

    def test_hagrid_review_action_uses_xhr_datadog_api(self):
        flow, fake = make_flow()
        flow.state.ec_token = "EC-TEST123"
        flow.state.signup_url = "https://www.paypal.com/checkoutweb/signup?token=EC-TEST123"
        flow.state.euat_token = "EUAT-TEST"
        phase = cast(Callable[[], dict[str, object]], getattr(flow, "_phase4_authorize"))

        with (
            patch.object(flow, "_load_hagrid_review_context", return_value=True),
            patch.object(flow, "_send_tealeaf_data"),
            patch("paypal.flow.send_analytics_ts"),
        ):
            result = phase()

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            self._datadog_action_params(fake, "review_page_loaded")["ddtags"],
            "sdk_version:5.35.1,api:xhr,service:hagrid",
        )

    def test_mtr_send_python_generated_posts_and_updates_state(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(send_mtr_signals(fake, flow.state, page_url="https://www.paypal.com/pay?token=BA-TEST"))

        self.assertEqual(flow.state.mtr_runtime_source, "python_generated")
        self.assertEqual(flow.state.mtr_get_status, 200)
        self.assertEqual(flow.state.mtr_post_status, 200)
        self.assertEqual(flow.state.mtr_request_id, "REQ12345678901234567")
        self.assertEqual(flow.state.mtr_sealed_result, "sealed-test-result")
        self.assertEqual(flow.state.mtr_visitor_token, "visitor-123")
        self.assertTrue(flow.state.mtr_completed)
        self.assertEqual(flow.state.mtr_completed_cmid, "BA-37R61061EU582084R")
        self.assertTrue(any(method == "GET" and "/x0" in url for method, url in fake.requests))
        self.assertTrue(any(method == "POST" and "/mtr/1a7c3460cd8c343771081839499ed7a0?" in url for method, url in fake.requests))
        headers = cast(dict[str, str], fake.last_post_kwargs["headers"])
        self.assertEqual(headers["Content-Type"], "text/plain")
        self.assertIsInstance(fake.last_post_kwargs["content"], bytes)
        decoded = decode_mtr_body(cast(bytes, fake.last_post_kwargs["content"]))
        self.assertEqual(decoded["m"], "s")

    def test_local_headless_launch_prefers_system_chrome_channel(self):
        launch_kwargs = cast(Callable[[str | None], dict[str, object]], getattr(local_headless_module, "_launch_kwargs"))

        with patch.dict(os.environ, {}, clear=True):
            kwargs = launch_kwargs(None)

        self.assertEqual(kwargs["channel"], "chrome")
        self.assertTrue(kwargs["headless"])
        self.assertIn("--disable-blink-features=AutomationControlled", cast(list[str], kwargs["args"]))

    def test_local_headless_launch_can_use_bundled_chromium(self):
        launch_kwargs = cast(Callable[[str | None], dict[str, object]], getattr(local_headless_module, "_launch_kwargs"))

        with patch.dict(os.environ, {"PAYPAL_LOCAL_HEADLESS_BROWSER_CHANNEL": "bundled"}, clear=True):
            kwargs = launch_kwargs("http://proxy.local:8080")

        self.assertNotIn("channel", kwargs)
        self.assertEqual(kwargs["proxy"], {"server": "http://proxy.local:8080"})
        self.assertIn("--disable-blink-features=AutomationControlled", cast(list[str], kwargs["args"]))

    def test_local_headless_launch_splits_authenticated_proxy_for_playwright(self):
        launch_kwargs = cast(Callable[[str | None], dict[str, object]], getattr(local_headless_module, "_launch_kwargs"))

        with patch.dict(os.environ, {"PAYPAL_LOCAL_HEADLESS_BROWSER_CHANNEL": "bundled"}, clear=True):
            kwargs = launch_kwargs("http://user%40name:pass%3Aword@proxy.local:8080")

        self.assertEqual(
            kwargs["proxy"],
            {"server": "http://proxy.local:8080", "username": "user@name", "password": "pass:word"},
        )

    def test_local_headless_launch_falls_back_to_bundled_chromium_when_channel_missing(self):
        launch_browser = cast(Callable[[object, str | None], object], getattr(local_headless_module, "_launch_browser"))

        class FakeChromium:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def launch(self, **kwargs: object) -> str:
                self.calls.append(dict(kwargs))
                if kwargs.get("channel") == "chrome":
                    raise RuntimeError("chrome channel missing")
                return "bundled-browser"

        class FakePlaywright:
            def __init__(self) -> None:
                self.chromium: FakeChromium = FakeChromium()

        fake = FakePlaywright()

        with patch.dict(os.environ, {}, clear=True):
            browser = launch_browser(fake, None)

        self.assertEqual(browser, "bundled-browser")
        self.assertEqual(fake.chromium.calls[0]["channel"], "chrome")
        self.assertNotIn("channel", fake.chromium.calls[1])

    def test_headless_policy_blocks_static_and_challenges_but_allows_core_signals(self):
        seed_rules = cast(Callable[[str], list[object]], getattr(local_headless_module, "_seed_headless_rules"))
        decide = cast(Callable[..., object], getattr(local_headless_module, "_headless_request_decision"))
        rules = seed_rules("checkout")

        p1_decision = decide(
            "https://c.paypal.com/v1/r/d/b/p1?f=BA-TEST",
            method="POST",
            resource_type="fetch",
            rules=rules,
        )
        self.assertEqual(getattr(p1_decision, "action"), "allow")
        self.assertEqual(getattr(p1_decision, "family"), "fraudnet_p1")

        p1_image_decision = decide(
            "https://c.paypal.com/v1/r/d/b/p1?f=BA-TEST",
            method="GET",
            resource_type="image",
            rules=rules,
        )
        self.assertEqual(getattr(p1_image_decision, "action"), "allow")
        self.assertEqual(getattr(p1_image_decision, "family"), "fraudnet_p1")

        static_decision = decide(
            "https://www.paypalobjects.com/checkoutweb/app.css",
            method="GET",
            resource_type="stylesheet",
            rules=rules,
        )
        self.assertEqual(getattr(static_decision, "action"), "abort")
        self.assertEqual(getattr(static_decision, "reason"), "static_resource_blocked")

        challenge_decision = decide(
            "https://www.paypal.com/auth/createchallenge?token=BA-TEST",
            method="GET",
            resource_type="document",
            rules=rules,
            fail_open=True,
        )
        self.assertEqual(getattr(challenge_decision, "action"), "abort")
        self.assertEqual(getattr(challenge_decision, "reason"), "challenge_blocked")

        unknown_decision = decide(
            "https://www.paypal.com/unrelated/noise",
            method="GET",
            resource_type="fetch",
            rules=rules,
        )
        self.assertEqual(getattr(unknown_decision, "action"), "abort")
        self.assertEqual(getattr(unknown_decision, "reason"), "not_allowlisted")

        fail_open_decision = decide(
            "https://www.paypal.com/unrelated/noise",
            method="GET",
            resource_type="fetch",
            rules=rules,
            fail_open=True,
        )
        self.assertEqual(getattr(fail_open_decision, "action"), "allow")
        self.assertEqual(getattr(fail_open_decision, "reason"), "fail_open_unknown")

    def test_headless_allowlist_cache_and_debug_log_are_private_and_structured(self):
        rule_class = getattr(local_headless_module, "HeadlessAllowlistRule")
        save_rules = cast(Callable[[list[object]], list[dict[str, object]]], getattr(local_headless_module, "_save_headless_cached_rules"))
        load_rules = cast(Callable[[], list[object]], getattr(local_headless_module, "_load_headless_cached_rules"))
        network_log_class = getattr(local_headless_module, "HeadlessOptimizedNetworkLog")

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "var" / "headless_allowlist_cache.json"
            with patch.dict(os.environ, {"PAYPAL_HEADLESS_ALLOWLIST_CACHE": str(cache_path)}, clear=True):
                written = save_rules([
                    rule_class(
                        "c.paypal.com",
                        "/v1/r/d/b/p1",
                        methods=("POST",),
                        resource_types=("fetch",),
                        reason="learned_fraudnet_p1",
                    )
                ])
                loaded = load_rules()

            self.assertEqual(written[0]["host"], "c.paypal.com")
            self.assertEqual(getattr(loaded[0], "reason"), "learned_fraudnet_p1")
            self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cache_path.parent.stat().st_mode & 0o777, 0o700)

            log_root = Path(tmp) / "debug" / "job-1"
            network_log = network_log_class(job_id="job-1", root=log_root)
            network_log.record({"event": "route", "url": "https://c.paypal.com/v1/r/d/b/p1"})
            raw_path = Path(network_log.write_raw(label="body ?.bin", content=b"payload"))

            manifest_lines = network_log.manifest_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(log_root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(network_log.raw_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(network_log.manifest_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(raw_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(raw_path.read_bytes(), b"payload")
            self.assertEqual(json.loads(manifest_lines[0])["job_id"], "job-1")
            self.assertIn("body_.bin", raw_path.name)

    def test_traffic_recorder_defaults_to_private_metadata_without_sensitive_body_capture(self):
        from paypal.traffic_recorder import TrafficRecorder

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "capture"
            with patch.dict(os.environ, {}, clear=True):
                recorder = TrafficRecorder(root)
                request_id = recorder.record_request(
                    "POST",
                    "https://www.paypal.com/graphql",
                    {
                        "json": {
                            "cardNumber": "4111111111111111",
                            "securityCode": "123",
                            "password": "Password123!",
                        }
                    },
                    headers={"Cookie": "nsid=secret-cookie"},
                )
                recorder.record_response(
                    request_id,
                    "POST",
                    "https://www.paypal.com/graphql",
                    FakeResponse("https://www.paypal.com/graphql", '{"accessToken":"secret-token"}'),
                )
                recorder.close()

            self.assertFalse(recorder.raw_bodies)
            self.assertFalse(recorder.response_bodies)
            self.assertFalse(any((root / "network" / "requests").iterdir()))
            self.assertFalse(any((root / "network" / "bodies").iterdir()))
            events_text = (root / "network" / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("4111111111111111", events_text)
            self.assertNotIn("Password123!", events_text)
            self.assertNotIn("secret-cookie", events_text)
            self.assertNotIn("secret-token", events_text)
            for directory in (root, root / "network", root / "network" / "requests", root / "network" / "bodies"):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            for file_path in (root / "metadata.json", root / "summary.json", root / "network" / "events.jsonl", root / "network" / "requests.tsv"):
                self.assertEqual(file_path.stat().st_mode & 0o777, 0o600)

    def test_headless_debug_path_uses_random_job_id_not_ba_token(self):
        flow, _fake = make_flow()
        setattr(flow, "ba_token", "BA-65R97063NB8382917")
        get_session = cast(Callable[[], object], getattr(flow, "_get_headless_session"))

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAYPAL_HEADLESS_DEBUG_DIR": tmp}, clear=False):
                session = get_session()
                debug_log_path = str(getattr(session, "debug_log_path"))
                cast(Callable[[], None], getattr(session, "close"))()

        self.assertNotIn("BA-65R97063NB8382917", debug_log_path)
        self.assertNotIn("65R97063NB8382917", debug_log_path)
        self.assertIn("headless-", Path(debug_log_path).name)

    def test_headless_start_failure_releases_semaphore(self):
        session_class = getattr(local_headless_module, "LocalHeadlessSession")

        class FakeSemaphore:
            def __init__(self) -> None:
                self.acquire_calls = 0
                self.release_calls = 0

            def acquire(self) -> None:
                self.acquire_calls += 1

            def release(self) -> None:
                self.release_calls += 1

        fake_semaphore = FakeSemaphore()
        session = session_class(job_id="unit-start-failure")

        with (
            patch("paypal.local_headless._headless_semaphore", return_value=fake_semaphore),
            patch("paypal.local_headless._load_sync_playwright", side_effect=RuntimeError("playwright missing")),
            self.assertRaises(RuntimeError),
        ):
            session.start()

        self.assertEqual(fake_semaphore.acquire_calls, 1)
        self.assertEqual(fake_semaphore.release_calls, 1)
        self.assertFalse(bool(getattr(session, "_semaphore_acquired")))

    def test_headless_response_body_debug_is_opt_in(self):
        session_class = getattr(local_headless_module, "LocalHeadlessSession")

        class FakeRequest:
            method = "POST"

        class FakeHeadlessResponse:
            url = "https://www.paypal.com/graphql"
            status = 200
            request = FakeRequest()

            def body(self) -> bytes:
                return b'{"accessToken":"secret-token"}'

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PAYPAL_HEADLESS_DEBUG_DIR": tmp}, clear=True):
                session = session_class(job_id="unit-body-debug")
                capture_response = cast(Callable[[object], None], getattr(session, "_capture_response"))
                capture_response(FakeHeadlessResponse())
                raw_entries = list((Path(tmp) / "unit-body-debug" / "raw").iterdir())
                manifest_text = (Path(tmp) / "unit-body-debug" / "manifest.jsonl").read_text(encoding="utf-8")

        self.assertEqual(raw_entries, [])
        self.assertNotIn("secret-token", manifest_text)
        self.assertNotIn("response_body_path", manifest_text)

    def test_local_headless_wait_helpers_skip_page_waits_when_ready(self):
        wait_for_state = cast(Callable[..., bool], getattr(local_headless_module, "_wait_for_page_state_or_ready"))
        wait_for_timeout = cast(Callable[..., bool], getattr(local_headless_module, "_wait_for_timeout_or_ready"))

        class ReadyPage:
            def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
                raise AssertionError("load-state wait should be skipped when evidence is already ready")

            def wait_for_timeout(self, _timeout: float) -> None:
                raise AssertionError("timeout wait should be skipped when evidence is already ready")

        page = ReadyPage()

        self.assertTrue(wait_for_state(page, "networkidle", timeout_ms=8000, max_wait_ms=8000, ready=lambda: True))
        self.assertTrue(wait_for_timeout(page, 1500, ready=lambda: True))

    def test_local_headless_wait_helpers_poll_when_missing_signals(self):
        wait_for_state = cast(Callable[..., bool], getattr(local_headless_module, "_wait_for_page_state_or_ready"))
        wait_for_timeout = cast(Callable[..., bool], getattr(local_headless_module, "_wait_for_timeout_or_ready"))

        class WaitingPage:
            def __init__(self) -> None:
                self.load_state_calls: list[tuple[str, int]] = []
                self.timeout_calls: list[int] = []

            def wait_for_load_state(self, state: str, **kwargs: object) -> None:
                raw_timeout = kwargs.get("timeout")
                timeout = int(raw_timeout) if isinstance(raw_timeout, (int, float, str)) else 0
                self.load_state_calls.append((state, timeout))
                raise RuntimeError("state not ready")

            def wait_for_timeout(self, timeout: float) -> None:
                self.timeout_calls.append(int(timeout))

        page = WaitingPage()

        self.assertFalse(wait_for_state(page, "networkidle", timeout_ms=1, max_wait_ms=1, ready=lambda: False, poll_ms=1))
        self.assertFalse(wait_for_timeout(page, 1, ready=lambda: False, poll_ms=1))
        self.assertEqual(page.load_state_calls[0][0], "networkidle")
        self.assertGreaterEqual(len(page.timeout_calls), 1)

    def test_local_headless_runtime_mapper_preserves_browser_probe_values(self):
        mapper = cast(Callable[[dict[str, object]], dict[str, object]], getattr(local_headless_module, "_runtime_profile_from_js"))
        math_rect = {"bottom": 26, "font": "Times New Roman", "height": 17, "left": 8, "right": 274, "top": 9, "width": 266, "x": 8, "y": 9}
        emoji_rect = {"bottom": 26, "font": "Times New Roman", "height": 17, "left": 8, "right": 1608, "top": 9, "width": 1600, "x": 8, "y": 9}
        ua_data = {
            "brands": [{"brand": "HeadlessChrome", "version": "136"}],
            "mobile": False,
            "platform": "Linux",
            "highEntropy": {
                "architecture": "x86",
                "bitness": "64",
                "fullVersionList": [{"brand": "HeadlessChrome", "version": "136.0.7103.25"}],
                "platform": "Linux",
                "platformVersion": "6.1",
                "uaFullVersion": "136.0.7103.25",
            },
        }
        runtime = mapper(
            {
                "capturedAt": 1783338679067,
                "userAgent": "Mozilla/5.0 HeadlessChrome/136.0.7103.25 Safari/537.36",
                "platform": "Linux x86_64",
                "language": "pt-BR",
                "languages": ["pt-BR", "pt", "en-US", "en"],
                "timezone": "America/Sao_Paulo",
                "timezoneOffsetMinutes": 180,
                "hardwareConcurrency": 16,
                "deviceMemory": 8,
                "maxTouchPoints": 0,
                "webdriver": True,
                "uaData": ua_data,
                "screen": {"width": 1701, "height": 903, "availWidth": 1701, "availHeight": 863, "colorDepth": 24, "pixelDepth": 24},
                "window": {"innerWidth": 611, "innerHeight": 713, "outerWidth": 627, "outerHeight": 801, "devicePixelRatio": 2},
                "connection": {"effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False},
                "plugins": [{"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format", "mimeTypes": [{"type": "application/pdf", "suffixes": "pdf"}]}],
                "mimeTypeCount": 1,
                "pdfViewerEnabled": True,
                "canvas": {"dataUrl": "data:image/png;base64,text", "geometryDataUrl": "data:image/png;base64,geometry", "textDataUrl": "data:image/png;base64,text", "dataUrlLength": 26, "previewHash": "abc123", "winding": True, "ttCanvas": 3.5},
                "webgl": {"version": "WebGL 1.0", "vendor": "WebKit", "renderer": "WebKit WebGL", "shadingLanguageVersion": "WebGL GLSL ES", "unmaskedVendor": "Google Inc. (Google)", "unmaskedRenderer": "ANGLE SwiftShader Test", "extensions": ["WEBGL_debug_renderer_info"], "params": {"MAX_TEXTURE_SIZE": 16384}, "contextAttributes": {"alpha": True}, "shaderPrecisions": {"VERTEX_SHADER": {}}, "renderDataUrl": "data:image/png;base64,webgl", "ttWebgl": 4.25},
                "audio": {"value": "0.000123", "sampleRate": 44100, "ttAudio": 5.5},
                "memory": {"usedJSHeapSize": 123456, "totalJSHeapSize": 345678, "jsHeapSizeLimit": 987654321},
                "timing": {"ttCanvas": 3.5, "ttWebglBasic": 4.25, "ttWebglExt": 4.25, "ttAudio": 5.5},
                "fontWidths": {"default": 149.3125, "serif": 149.3125},
                "mathFingerprintSource": "acos=1,asin=2",
                "mathmlRect": math_rect,
                "emojiRect": emoji_rect,
                "cssSystemColors": {"w": "rgb(255, 255, 255)"},
                "browserMarkers": ["chrome"],
                "browserComponents": {"wv": False, "wvp": False, "pr": False, "ck": False, "pt": False, "fp": False},
                "windowPropertyMarkers": ["event", "clientInformation"],
                "navigatorPrototypeMarkers": {"l": 80, "p": [{"i": 22, "n": "webdriver"}]},
                "storageQuota": 123456789,
                "performanceTimeOrigin": 1783338679000.5,
                "performanceNowDeltas": [0.1, 0.2],
                "notificationPermissionMismatch": False,
                "sessionStorageUuid": "59e1e695-0096-5ea1-ee5d-93511a5c5061",
            }
        )

        profile = cast(dict[str, object], runtime["browser_profile"])
        screen = cast(dict[str, object], runtime["screen"])
        viewport = cast(dict[str, object], runtime["viewport"])
        dfp = cast(dict[str, object], runtime["device_fingerprint"])
        self.assertEqual(profile["fingerprint_source"], "headless")
        self.assertEqual(profile["gpu_renderer"], "ANGLE SwiftShader Test")
        self.assertEqual(profile["chrome_full_version"], "136.0.7103.25")
        self.assertEqual(profile["user_agent_data"], ua_data)
        self.assertEqual(screen["width"], 1701)
        self.assertEqual(viewport["height"], 713)
        self.assertEqual(dfp["source"], "headless")
        self.assertEqual(dfp["canvas_text_data_url"], "data:image/png;base64,text")
        self.assertEqual(dfp["canvas_geometry_data_url"], "data:image/png;base64,geometry")
        self.assertEqual(dfp["webgl_render_data_url"], "data:image/png;base64,webgl")
        self.assertEqual(dfp["audio_val"], "0.000123")
        self.assertEqual(dfp["js_heap_size_limit"], 987654321)
        self.assertEqual(dfp["mathml_rect"], math_rect)
        self.assertEqual(dfp["emoji_rect"], emoji_rect)
        self.assertEqual(dfp["ab_noop"], "a")
        self.assertTrue(dfp["navigator_webdriver"])

    def test_local_headless_ua_fallback_uses_seed_full_version_when_ua_data_is_absent(self):
        mapper = cast(Callable[..., dict[str, object]], getattr(local_headless_module, "_runtime_profile_from_js"))
        build_common_headers = cast(Callable[..., dict[str, str]], getattr(session_module, "build_common_headers"))
        build_high_entropy_hints = cast(Callable[..., dict[str, str]], getattr(session_module, "build_high_entropy_hints"))
        seed_profile: dict[str, object] = {
            "chrome_major": 150,
            "chrome_full_version": "150.0.7871.46",
            "language": "pt-BR",
            "sec_ch_platform": '"Linux"',
            "sec_ch_arch": '"x86"',
            "device_memory": 8,
        }

        runtime = mapper(
            {
                "capturedAt": 1783338679067,
                "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                "platform": "Linux x86_64",
                "language": "pt-BR",
                "languages": ["pt-BR", "pt", "en-US", "en"],
                "timezone": "America/Sao_Paulo",
                "timezoneOffsetMinutes": 180,
                "hardwareConcurrency": 12,
                "deviceMemory": 8,
                "maxTouchPoints": 0,
                "webdriver": True,
                "uaData": None,
                "screen": {"width": 1536, "height": 864, "availWidth": 1536, "availHeight": 824, "colorDepth": 24, "pixelDepth": 24},
                "window": {"innerWidth": 1200, "innerHeight": 720, "outerWidth": 1216, "outerHeight": 808, "devicePixelRatio": 1},
                "connection": {"effectiveType": "4g", "rtt": 100, "downlink": 10, "saveData": False},
                "plugins": [],
                "mimeTypeCount": 0,
                "pdfViewerEnabled": True,
                "canvas": {"dataUrl": "data:image/png;base64,text", "ttCanvas": 0.0},
                "webgl": {"version": "WebGL 1.0", "vendor": "WebKit", "renderer": "WebKit WebGL", "unmaskedVendor": "Google Inc. (Google)", "unmaskedRenderer": "ANGLE SwiftShader", "extensions": [], "ttWebgl": 0.0},
                "audio": {"value": "0.000123", "sampleRate": 44100, "ttAudio": 0.0},
                "memory": None,
                "timing": {"ttCanvas": 0.0, "ttWebglBasic": 0.0, "ttWebglExt": 0.0, "ttAudio": 0.0},
                "fontWidths": {},
                "performanceTimeOrigin": 1783338679000.5,
                "performanceNowDeltas": [0.1, 0.2],
            },
            seed_profile,
        )

        profile = cast(dict[str, object], runtime["browser_profile"])
        dfp = cast(dict[str, object], runtime["device_fingerprint"])
        flow, _fake = make_flow()
        flow.state.browser_profile = profile
        self.assertEqual(profile["chrome_full_version"], "150.0.7871.46")
        self.assertEqual(profile["chrome_major"], 150)
        self.assertEqual(cast(dict[str, object], profile["user_agent_data"]), {})
        self.assertTrue(dfp["navigator_webdriver"])
        self.assertNotIn("js_memory", dfp)
        self.assertIn('"Google Chrome";v="150"', build_common_headers(flow.state)["sec-ch-ua"])
        self.assertIn('"Google Chrome";v="150.0.7871.46"', build_high_entropy_hints(flow.state)["sec-ch-ua-full-version-list"])

    def test_generate_runtime_profile_headless_uses_local_playwright_capture(self):
        generate_runtime_profile = cast(Callable[..., dict[str, object]], getattr(fingerprint_module, "generate_runtime_profile"))
        runtime: dict[str, object] = {
            "browser_profile": {"user_agent": "UA-HEADLESS", "fingerprint_source": "draft"},
            "screen": {"width": 1701, "height": 903},
            "viewport": {"width": 611, "height": 713},
            "device_fingerprint": {"source": "draft", "canvas_h": "canvas-headless", "audio_val": "0.000123", "webgl_ext_hash": "webgl-headless"},
        }

        with patch("paypal.local_headless.capture_runtime_fingerprint_with_local_headless", return_value=runtime) as capture:
            result = generate_runtime_profile("local_headless", roxy_proxy_url="http://proxy.local:8080")

        capture.assert_called_once()
        self.assertEqual(capture.call_args.kwargs["proxy_url"], "http://proxy.local:8080")
        seeded_profile = cast(dict[str, object], capture.call_args.kwargs["browser_profile"])
        seeded_screen = cast(dict[str, object], capture.call_args.kwargs["screen"])
        seeded_viewport = cast(dict[str, object], capture.call_args.kwargs["viewport"])
        self.assertEqual(seeded_profile["fingerprint_source"], "random")
        self.assertGreaterEqual(cast(int, seeded_viewport["width"]), 980)
        self.assertGreaterEqual(cast(int, seeded_viewport["height"]), 560)
        self.assertLessEqual(cast(int, seeded_viewport["width"]) + 16, cast(int, seeded_screen["width"]))
        self.assertLessEqual(cast(int, seeded_viewport["height"]) + 88, cast(int, seeded_screen["height"]))
        self.assertIs(result, runtime)
        profile = cast(dict[str, object], result["browser_profile"])
        dfp = cast(dict[str, object], result["device_fingerprint"])
        self.assertEqual(profile["fingerprint_source"], "headless")
        self.assertEqual(dfp["source"], "headless")
        self.assertEqual(dfp["canvas_h"], "canvas-headless")
        self.assertEqual(dfp["audio_val"], "0.000123")
        self.assertEqual(dfp["webgl_ext_hash"], "webgl-headless")

    def test_mtr_python_generated_treats_headless_profile_as_browser_runtime(self):
        build_mtr_request_object = cast(Callable[..., dict[str, object]], getattr(mtr_module, "build_mtr_request_object"))
        build_mtr_bd_module = cast(Callable[..., dict[str, object]], getattr(mtr_module, "build_mtr_bd_module"))
        flow, _fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        flow.state.browser_profile.update(
            {
                "fingerprint_source": "headless",
                "outer_width": 1001,
                "outer_height": 802,
                "inner_width": 611,
                "inner_height": 713,
                "uaData": {
                    "brands": [{"brand": "HeadlessChrome", "version": "136"}],
                    "mobile": False,
                    "platform": "Linux",
                    "highEntropy": {
                        "brands": [{"brand": "HeadlessChrome", "version": "136"}],
                        "fullVersionList": [{"brand": "HeadlessChrome", "version": "136.0.7103.25"}],
                        "uaFullVersion": "136.0.7103.25",
                        "architecture": "x86",
                        "bitness": "64",
                        "platform": "Linux",
                        "platformVersion": "6.1",
                    },
                },
            }
        )
        flow.state.device_fingerprint.update({"ab_noop": "a", "navigator_webdriver": True})

        payload = build_mtr_request_object(
            flow.state,
            page_url="https://www.paypal.com/pay?token=BA-TESTTOKEN123",
            x0_token="bootstrap-token",
        )

        self.assertEqual(payload["ab"], {"noop": "a"})
        s58 = cast(dict[str, object], cast(dict[str, object], payload["s58"])["v"])
        self.assertEqual(s58["b"], [{"b": "HeadlessChrome", "v": "136"}])
        self.assertEqual(cast(dict[str, str], s58["h"])["uaFullVersion"], "136.0.7103.25")
        self.assertEqual(
            cast(dict[str, object], payload["s150"])["v"],
            {"outerWidth": 1001, "outerHeight": 802, "innerWidth": 611, "innerHeight": 713},
        )
        detection = cast(dict[str, object], cast(dict[str, object], payload["s157"])["v"])
        bd = build_mtr_bd_module(flow.state)
        self.assertTrue(detection["webdriver"])
        self.assertTrue(detection["headless_chrome"])
        self.assertTrue(bd["webdriver"])
        self.assertTrue(bd["headless"])
        self.assertEqual(bd["automationGlobals"], ["navigator.webdriver"])

    def test_mtr_runtime_mode_accepts_local_headless_aliases(self):
        mtr_runtime_mode = cast(Callable[..., str], getattr(mtr_module, "mtr_runtime_mode"))

        self.assertEqual(mtr_runtime_mode("headless"), "headless")
        self.assertEqual(mtr_runtime_mode("local_headless"), "headless")
        self.assertEqual(mtr_runtime_mode("playwright"), "headless")
        self.assertEqual(mtr_runtime_mode("local-playwright"), "headless")

    def test_mtr_send_headless_runtime_uses_local_helper_and_updates_state(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        flow.state.browser_profile.update({"user_agent": "UA-STATE-HEADLESS", "device_pixel_ratio": 2})
        flow.state.screen.update({"width": 1601, "height": 901})
        flow.state.viewport.update({"width": 601, "height": 701})
        fake.browser_cookies.append({"name": "nsid", "value": "nsid-123", "domain": ".paypal.com", "path": "/"})

        with (
            patch.dict(os.environ, {"PAYPAL_REQUIRE_MTR": "1"}, clear=True),
            patch("paypal.mtr._ensure_mtr_runtime_fingerprint_source", return_value=None),
            patch("paypal.mtr.mtr_headless_wait_seconds", return_value=3.5),
            patch(
                "paypal.local_headless.run_mtr_with_local_headless",
                return_value={
                    "ok": True,
                    "runtime": "headless",
                    "status": 200,
                    "url": "https://www.paypal.com/pay?token=BA-TEST",
                    "x0_status": 200,
                    "post_status": 200,
                    "requestId": "REQ-HEADLESS-123",
                    "sealedResult": "sealed-headless-result",
                    "visitorToken": "visitor-headless-123",
                    "cookies": [
                        {"name": "datadome", "value": "dd-headless", "domain": ".paypal.com", "path": "/"}
                    ],
                    "responses": [{"method": "POST", "status": 200}],
                    "injected_dfp": True,
                },
            ) as headless_runner,
        ):
            self.assertTrue(
                send_mtr_signals(
                    fake,
                    flow.state,
                    page_url="https://www.paypal.com/pay?token=BA-TEST",
                    runtime_mode="local_headless",
                )
            )

        headless_runner.assert_called_once()
        self.assertEqual(headless_runner.call_args.args[0], "https://www.paypal.com/pay?token=BA-TEST")
        self.assertEqual(headless_runner.call_args.kwargs["wait_seconds"], 3.5)
        self.assertEqual(headless_runner.call_args.kwargs["proxy_url"], "")
        self.assertEqual(
            headless_runner.call_args.kwargs["cookies"],
            [{"name": "nsid", "value": "nsid-123", "domain": ".paypal.com", "path": "/"}],
        )
        headless_browser_profile = cast(object, headless_runner.call_args.kwargs["browser_profile"])
        headless_screen = cast(object, headless_runner.call_args.kwargs["screen"])
        headless_viewport = cast(object, headless_runner.call_args.kwargs["viewport"])
        self.assertIs(headless_browser_profile, flow.state.browser_profile)
        self.assertIs(headless_screen, flow.state.screen)
        self.assertIs(headless_viewport, flow.state.viewport)
        self.assertEqual(flow.state.mtr_runtime_source, "headless")
        self.assertEqual(flow.state.mtr_get_status, 200)
        self.assertEqual(flow.state.mtr_post_status, 200)
        self.assertEqual(flow.state.mtr_request_id, "REQ-HEADLESS-123")
        self.assertEqual(flow.state.mtr_sealed_result, "sealed-headless-result")
        self.assertEqual(flow.state.mtr_visitor_token, "visitor-headless-123")
        self.assertTrue(flow.state.mtr_completed)
        self.assertEqual(flow.state.datadome_cookie, "dd-headless")
        self.assertEqual(flow.state.mtr_browser_result["runtime"], "headless")
        self.assertTrue(flow.state.mtr_browser_result["injected_dfp"])

    def test_mtr_headless_runtime_replaces_mixed_random_state_before_browser_run(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()
        flow.state.browser_profile["fingerprint_source"] = "random"
        flow.state.device_fingerprint["source"] = "random"
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        flow.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        headless_runtime: dict[str, object] = {
            "browser_profile": {"fingerprint_source": "headless", "user_agent": "UA-HEADLESS-SAME", "device_pixel_ratio": 2},
            "screen": {"width": 1600, "height": 900},
            "viewport": {"width": 1200, "height": 720},
            "device_fingerprint": {"source": "headless", "navigator_webdriver": True},
        }

        with (
            patch.dict(os.environ, {"PAYPAL_REQUIRE_MTR": "1"}, clear=True),
            patch("paypal.fingerprint.generate_runtime_profile", return_value=headless_runtime) as generate_profile,
            patch(
                "paypal.local_headless.run_mtr_with_local_headless",
                return_value={
                    "ok": True,
                    "runtime": "headless",
                    "status": 200,
                    "x0_status": 200,
                    "post_status": 200,
                    "requestId": "REQ-HEADLESS-SAME",
                    "sealedResult": "sealed-headless-same",
                    "visitorToken": "visitor-headless-same",
                    "cookies": [],
                    "responses": [],
                },
            ) as headless_runner,
        ):
            self.assertTrue(send_mtr_signals(fake, flow.state, page_url="https://www.paypal.com/pay?token=BA-TEST", runtime_mode="headless"))

        generate_profile.assert_called_once()
        self.assertEqual(generate_profile.call_args.args[0], "headless")
        self.assertEqual(flow.state.browser_profile["fingerprint_source"], "headless")
        self.assertEqual(flow.state.device_fingerprint["source"], "headless")
        headless_call_kwargs = cast(dict[str, object], headless_runner.call_args.kwargs)
        self.assertIs(headless_call_kwargs["browser_profile"], flow.state.browser_profile)
        self.assertEqual(flow.state.mtr_runtime_source, "headless")

    def test_mtr_headless_runtime_raises_when_source_alignment_falls_back_to_random(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()
        flow.state.browser_profile["fingerprint_source"] = "random"
        flow.state.device_fingerprint["source"] = "random"
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        random_runtime: dict[str, object] = {
            "browser_profile": {"fingerprint_source": "random"},
            "screen": {},
            "viewport": {},
            "device_fingerprint": {"source": "random"},
        }

        with (
            patch.dict(os.environ, {"PAYPAL_REQUIRE_MTR": "0", "PAYPAL_HEADLESS_RUNTIME_FALLBACK": "0"}, clear=True),
            patch("paypal.fingerprint.generate_runtime_profile", return_value=random_runtime),
            self.assertRaises(RuntimeError) as raised,
        ):
            _ = send_mtr_signals(fake, flow.state, page_url="https://www.paypal.com/pay?token=BA-TEST", runtime_mode="headless")

        self.assertIn("requires fingerprint_source=headless", str(raised.exception))

    def test_mtr_headless_connection_failure_falls_back_to_python_generated(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"

        with (
            patch.dict(os.environ, {"PAYPAL_REQUIRE_MTR": "0", "PAYPAL_HEADLESS_RUNTIME_FALLBACK": "1"}, clear=True),
            patch("paypal.mtr._ensure_mtr_runtime_fingerprint_source", return_value=None),
            patch(
                "paypal.local_headless.run_mtr_with_local_headless",
                side_effect=RuntimeError("headless chromium unavailable"),
            ),
        ):
            self.assertTrue(
                send_mtr_signals(
                    fake,
                    flow.state,
                    page_url="https://www.paypal.com/pay?token=BA-TEST",
                    runtime_mode="headless",
                )
            )

        self.assertEqual(flow.state.mtr_runtime_source, "python_generated")
        self.assertTrue(any(method == "POST" and "/mtr/" in url for method, url in fake.requests))
        self.assertEqual(flow.state.mtr_browser_result["runtime"], "headless")
        self.assertIn("headless chromium unavailable", str(flow.state.mtr_browser_result.get("error") or ""))

    def test_mtr_headless_connection_failure_raises_when_fallback_disabled(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"

        with (
            patch.dict(os.environ, {"PAYPAL_REQUIRE_MTR": "0", "PAYPAL_HEADLESS_RUNTIME_FALLBACK": "0"}, clear=True),
            patch("paypal.mtr._ensure_mtr_runtime_fingerprint_source", return_value=None),
            patch(
                "paypal.local_headless.run_mtr_with_local_headless",
                side_effect=RuntimeError("headless chromium unavailable"),
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            _ = send_mtr_signals(
                fake,
                flow.state,
                page_url="https://www.paypal.com/pay?token=BA-TEST",
                runtime_mode="headless",
            )

        self.assertIn("headless chromium unavailable", str(raised.exception))

    def test_mtr_roxy_connection_failure_falls_back_to_python_generated(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"

        with (
            patch.dict(os.environ, {"PAYPAL_REQUIRE_MTR": "0", "PAYPAL_ROXY_RUNTIME_FALLBACK": "1"}, clear=True),
            patch(
                "paypal.roxy_fingerprint.capture_roxy_runtime_profile",
                side_effect=ConnectionError("[Errno 111] Connection refused"),
            ),
        ):
            self.assertTrue(
                send_mtr_signals(
                    fake,
                    flow.state,
                    page_url="https://www.paypal.com/pay?token=BA-TEST",
                    runtime_mode="roxy",
                )
            )

        self.assertEqual(flow.state.mtr_runtime_source, "python_generated")
        self.assertTrue(any(method == "POST" and "/mtr/" in url for method, url in fake.requests))
        self.assertIn("Connection refused", str(flow.state.mtr_browser_result.get("error") or ""))

    def test_datadome_headless_runtime_uses_local_helper_and_imports_cookie(self):
        flow, fake = make_flow()
        setattr(flow, "datadome_mode", "headless")
        solve_datadome = cast(Callable[..., bool], getattr(flow, "_solve_datadome_with_roxy_browser"))
        datadome_mode = cast(Callable[[], str], getattr(flow, "_datadome_mode"))
        flow.state.browser_profile.update({"user_agent": "UA-STATE-DATADOME", "device_pixel_ratio": 3})
        flow.state.screen.update({"width": 1602, "height": 902})
        flow.state.viewport.update({"width": 602, "height": 702})
        fake.browser_cookies.append({"name": "nsid", "value": "nsid-123", "domain": ".paypal.com", "path": "/"})

        class FakeHeadlessDatadomeSession:
            def __init__(self) -> None:
                self.calls: list[tuple[str, float | None]] = []

            def solve_datadome(self, url: str, *, wait_seconds: float | None = None) -> dict[str, object]:
                self.calls.append((url, wait_seconds))
                return {
                    "ok": True,
                    "runtime": "headless",
                    "status": 200,
                    "url": "https://www.paypal.com/agreements/approve?ba_token=BA-TEST",
                    "cookies": [
                        {"name": "datadome", "value": "dd-headless", "domain": ".paypal.com", "path": "/"}
                    ],
                    "datadome": "dd-headless",
                    "clientid": "clientid-headless",
                }

        headless_datadome = FakeHeadlessDatadomeSession()
        with (
            patch.dict(os.environ, {"PAYPAL_DATADOME_HEADLESS_WAIT_SECONDS": "4"}, clear=True),
            patch.object(flow, "_get_headless_session", return_value=headless_datadome),
        ):
            self.assertTrue(
                solve_datadome(
                    "https://www.paypal.com/agreements/approve?ba_token=BA-TEST",
                    reason="unit_test",
                )
            )

        self.assertEqual(
            headless_datadome.calls,
            [("https://www.paypal.com/agreements/approve?ba_token=BA-TEST", 4.0)],
        )
        self.assertEqual(datadome_mode(), "headless")
        self.assertEqual(flow.state.datadome_cookie, "dd-headless")
        self.assertEqual(flow.state.datadome_clientid, "clientid-headless")
        self.assertTrue(flow.state.datadome_browser_solved)
        self.assertEqual(flow.state.datadome_browser_result["runtime"], "headless")
        self.assertEqual(flow.state.datadome_browser_result["reason"], "unit_test")

    def test_datadome_headless_403_cookie_is_not_treated_as_solved(self):
        flow, fake = make_flow()
        setattr(flow, "datadome_mode", "headless")
        solve_datadome = cast(Callable[..., bool], getattr(flow, "_solve_datadome_with_roxy_browser"))
        datadome_mode = cast(Callable[[], str], getattr(flow, "_datadome_mode"))

        class FakeHeadlessDatadomeSession:
            def solve_datadome(self, _url: str, *, wait_seconds: float | None = None) -> dict[str, object]:
                return {
                    "ok": False,
                    "runtime": "headless",
                    "status": 403,
                    "url": "https://www.paypal.com/agreements/approve?ba_token=BA-TEST",
                    "cookies": [
                        {"name": "datadome", "value": "dd-challenge-only", "domain": ".paypal.com", "path": "/"}
                    ],
                    "datadome": "dd-challenge-only",
                    "clientid": "clientid-from-challenge-page",
                    "blocked_by_datadome": True,
                }

        with (
            patch.dict(os.environ, {"PAYPAL_HEADLESS_RUNTIME_FALLBACK": "1"}, clear=True),
            patch.object(flow, "_get_headless_session", return_value=FakeHeadlessDatadomeSession()),
        ):
            self.assertFalse(
                solve_datadome(
                    "https://www.paypal.com/agreements/approve?ba_token=BA-TEST",
                    reason="unit_test",
                )
            )

        self.assertEqual(datadome_mode(), "protocol")
        self.assertEqual(flow.state.datadome_clientid, "clientid-from-challenge-page")
        self.assertEqual(flow.state.datadome_cookie, "")
        self.assertFalse(flow.state.datadome_browser_solved)
        self.assertEqual(flow.state.datadome_browser_result["runtime"], "headless")
        self.assertEqual(flow.state.datadome_browser_result["status"], 403)
        self.assertFalse(flow.state.datadome_browser_result["ok"])
        self.assertEqual(fake.browser_cookies, [])

    def test_datadome_headless_connection_failure_respects_disabled_fallback(self):
        flow, _fake = make_flow()
        setattr(flow, "datadome_mode", "headless")
        solve_datadome = cast(Callable[..., bool], getattr(flow, "_solve_datadome_with_roxy_browser"))

        class FailingHeadlessDatadomeSession:
            def solve_datadome(self, _url: str, *, wait_seconds: float | None = None) -> dict[str, object]:
                raise RuntimeError("playwright missing")

        with (
            patch.dict(os.environ, {"PAYPAL_HEADLESS_RUNTIME_FALLBACK": "0"}, clear=True),
            patch.object(flow, "_get_headless_session", return_value=FailingHeadlessDatadomeSession()),
            self.assertRaises(RuntimeError) as raised,
        ):
            _ = solve_datadome(
                "https://www.paypal.com/agreements/approve?ba_token=BA-TEST",
                reason="unit_test",
            )

        self.assertIn("playwright missing", str(raised.exception))
        self.assertEqual(flow.state.datadome_browser_result["runtime"], "headless")
        self.assertIn("playwright missing", str(flow.state.datadome_browser_result.get("error") or ""))

    def test_datadome_roxy_connection_failure_disables_roxy_runtime_for_job(self):
        flow, _fake = make_flow()
        setattr(flow, "_roxy_runtime_disabled_reason", "")
        setattr(flow, "datadome_mode", "roxy")
        setattr(flow, "mtr_runtime", "roxy")
        setattr(flow, "risk_signals_mode", "roxy")
        solve_datadome = cast(Callable[..., bool], getattr(flow, "_solve_datadome_with_roxy_browser"))
        datadome_mode = cast(Callable[[], str], getattr(flow, "_datadome_mode"))
        mtr_runtime_mode = cast(Callable[[], str], getattr(flow, "_mtr_runtime_mode"))
        risk_signals_mode = cast(Callable[[], str], getattr(flow, "_risk_signals_mode"))

        with (
            patch.dict(os.environ, {"PAYPAL_ROXY_RUNTIME_FALLBACK": "1"}, clear=True),
            patch(
                "paypal.roxy_fingerprint.capture_roxy_runtime_profile",
                side_effect=ConnectionError("[Errno 111] Connection refused"),
            ),
        ):
            self.assertFalse(
                solve_datadome(
                    "https://www.paypal.com/agreements/approve?ba_token=BA-TEST",
                    reason="unit_test",
                )
            )

        self.assertEqual(datadome_mode(), "protocol")
        self.assertEqual(mtr_runtime_mode(), "python_generated")
        self.assertEqual(risk_signals_mode(), "protocol")
        self.assertIn("Connection refused", str(flow.state.datadome_browser_result.get("error") or ""))

    def test_mtr_send_block_runtime_still_raises_when_required(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()

        with (
            patch.dict(os.environ, {"PAYPAL_MTR_RUNTIME": "block", "PAYPAL_REQUIRE_MTR": "1"}),
            self.assertRaises(RuntimeError),
        ):
            _ = send_mtr_signals(fake, flow.state, page_url="https://www.paypal.com/pay?token=BA-TEST")

        self.assertEqual(flow.state.mtr_runtime_source, "missing_real_browser_runtime")

    def test_risk_runtime_report_lists_strict_blockers(self):
        with patch.dict(os.environ, {"PAYPAL_STRICT_BROWSER_RISK": "1"}):
            flow, _fake = make_flow()
            report = cast(Callable[[], dict[str, object]], getattr(flow, "_risk_runtime_report"))()
        blockers = cast(list[str], report["strict_blockers"])

        self.assertIn("mtr_sealedResult_missing", blockers)
        self.assertIn("synthetic_fraudnet_fpti_tealeaf_datadog", blockers)


if __name__ == "__main__":
    _ = unittest.main()
