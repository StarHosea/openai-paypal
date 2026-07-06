import importlib
import json
import os
import re
import zlib
import unittest
from collections.abc import Callable
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

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.requests.append(("GET", url))
        if "/mtr/" in url and "/x0" in url:
            return FakeResponse(url, "cys3bHRVUUtdbjB0cEFjaVJ1bnRpbWVCb290c3RyYXBUb2tlbg==")
        return FakeResponse(url, "<html></html>")

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append(("POST", url))
        self.last_post_kwargs = dict(kwargs)
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


def make_flow() -> tuple[FlowUnderTest, FakeSession]:
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

    def test_captcha_automation_modes_are_configurable(self):
        captcha_mode = cast(Callable[[], str], getattr(session_module, "paypal_captcha_bypass_mode"))
        frontend_disable = cast(Callable[[], bool], getattr(session_module, "captcha_frontend_disable_enabled"))

        with patch.dict(os.environ, {"PAYPAL_CAPTCHA_BYPASS_MODE": "frontend_disable"}):
            self.assertEqual(captcha_mode(), "manual_required")
            self.assertFalse(frontend_disable())

        with patch.dict(os.environ, {
            "PAYPAL_CAPTCHA_BYPASS_MODE": "frontend_disable",
            "PAYPAL_ALLOW_SYNTHETIC_CAPTCHA": "1",
        }):
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
            patch.dict(os.environ, {"PAYPAL_HCAPTCHA_PASSIVE_SOLVER": "node"}),
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

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mtr_runtime_mode(), "python_generated")
            payload = build_mtr_request_object(
                flow.state,
                page_url="https://www.paypal.com/pay?token=BA-TESTTOKEN123",
                x0_token="bootstrap-token",
        )
        body = serialize_mtr_body(payload)
        numbered_signal_keys = [key for key in payload if re.fullmatch(r"s\d+", key)]
        expected_viewport = {
            "w": cast(int, flow.state.viewport["width"]),
            "h": cast(int, flow.state.viewport["height"]),
        }

        self.assertEqual(payload["c"], "QBzalmMuDFJIiZNebIWt")
        self.assertEqual(payload["s56"], {"s": 0, "v": "bootstrap-token"})
        self.assertEqual(payload["m"], "s")
        self.assertEqual(payload["gt"], 1)
        self.assertEqual(payload["ab"], {"noop": "a"})
        self.assertEqual(payload["lr"], [])
        self.assertEqual(payload["s55"], {"s": -1, "v": None})
        self.assertEqual(payload["s67"], {"s": -1, "v": None})
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
        self.assertIsInstance(cast(dict[str, object], payload["s58"])["v"], dict)
        self.assertEqual(cast(dict[str, object], payload["s84"])["v"], expected_viewport)
        self.assertIsInstance(cast(dict[str, object], payload["s101"])["v"], str)
        for key in numbered_signal_keys:
            self.assertEqual(set(cast(dict[str, object], payload[key])), {"s", "v"})
        decoded = decode_mtr_body(body)
        self.assertEqual(decoded["m"], "s")
        self.assertEqual(decoded["gt"], 1)
        self.assertEqual(decoded["ab"], {"noop": "a"})
        decoded_numbered_signal_keys = [key for key in decoded if re.fullmatch(r"s\d+", key)]
        self.assertGreaterEqual(len(decoded_numbered_signal_keys), 127)
        self.assertGreater(len(body), 200)

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

    def test_mtr_send_block_runtime_still_raises_when_required(self):
        send_mtr_signals = cast(Callable[..., bool], getattr(mtr_module, "send_mtr_signals"))
        flow, fake = make_flow()

        with (
            patch.dict(os.environ, {"PAYPAL_MTR_RUNTIME": "block", "PAYPAL_REQUIRE_MTR": "1"}),
            self.assertRaises(RuntimeError),
        ):
            _ = send_mtr_signals(fake, flow.state, page_url="https://www.paypal.com/pay?token=BA-TEST")

        self.assertEqual(flow.state.mtr_runtime_source, "missing_real_browser_runtime")

    def test_strict_preflight_allows_phase1_after_mtr_success(self):
        flow, _fake = make_flow()
        flow.state.mtr_channel = "iwc-mxo"
        flow.state.mtr_client_metadata_id = "BA-37R61061EU582084R"
        flow.state.mtr_api_key = "QBzalmMuDFJIiZNebIWt"
        preflight = cast(Callable[[str], None], getattr(flow, "_strict_risk_preflight_or_raise"))

        with patch.dict(os.environ, {"PAYPAL_MTR_RUNTIME": "python_generated", "PAYPAL_STRICT_BROWSER_RISK": "1"}):
            preflight("https://www.paypal.com/pay?token=BA-TEST")

        report = cast(Callable[[], dict[str, object]], getattr(flow, "_risk_runtime_report"))()
        blockers = cast(list[str], report["strict_blockers"])
        self.assertNotIn("mtr_sealedResult_missing", blockers)

    def test_risk_runtime_report_lists_strict_blockers(self):
        flow, _fake = make_flow()
        report = cast(Callable[[], dict[str, object]], getattr(flow, "_risk_runtime_report"))()
        blockers = cast(list[str], report["strict_blockers"])

        self.assertIn("mtr_sealedResult_missing", blockers)
        self.assertIn("synthetic_fraudnet_fpti_tealeaf_datadog", blockers)


if __name__ == "__main__":
    _ = unittest.main()
