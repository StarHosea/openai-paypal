from __future__ import annotations

import re
import time
from pathlib import Path

from paypal.flow import PayPalFlow
from paypal.models import BillingAddress, CardInfo, UserInfo, generate_address, generate_card, generate_user
from paypal.proxy import timezone_profile
from paypal.smsbower import SMSBowerActivationStore, SMSBowerOtpProvider


def _luhn_is_valid(value: str) -> bool:
    total = 0
    alternate = False
    for char in reversed(value):
        digit = int(char)
        if alternate:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        alternate = not alternate
    return total % 10 == 0


def _us_flow() -> PayPalFlow:
    return PayPalFlow(
        ba_token="BA-TESTTOKEN123",
        user=UserInfo(
            first_name="Ava",
            last_name="Smith",
            email="ava.smith@example.com",
            phone="+14647681720",
            phone_local="4647681720",
            phone_country_code="+1",
            password="Password123!",
            dob="",
            cpf="",
        ),
        card=CardInfo("4111111111111111", "10/2028", "691", "CREDIT"),
        address=BillingAddress(
            street="Babcock Road",
            house_number="2829",
            district="",
            city="San Antonio",
            state="TX",
            postal_code="78229",
            country="US",
        ),
        proxy_enabled=False,
        fingerprint_source="random",
        region="US",
    )


def test_us_models_use_north_american_phone_and_address_shape() -> None:
    user = generate_user("4647681720", region="US")
    address = generate_address(region="US")
    card = generate_card(region="US")

    assert user.phone == "+14647681720"
    assert user.phone_country_code == "+1"
    assert user.phone_local == "4647681720"
    assert user.dob == ""
    assert user.cpf == ""
    assert re.fullmatch(r"[a-z]+\.[a-z]+\d{2,4}@(gmail|outlook|hotmail|icloud|yahoo)\.com", user.email)
    assert address.country == "US"
    assert re.fullmatch(r"[A-Z]{2}", address.state)
    assert re.fullmatch(r"\d{5}", address.postal_code)
    assert address.district == ""
    assert card.number.startswith(("411111", "555555"))
    assert _luhn_is_valid(card.number)


def test_us_capture_shape_is_used_for_phone_and_signup_variables(monkeypatch) -> None:
    flow = _us_flow()
    flow.state.content_identifier = "US:en:846cec6423261581a1dac780262688f5:compliance.signupTerms"

    variables = flow._build_signup_variables("EC-TEST123")

    assert flow.state.browser_profile["country"] == "US"
    assert flow.state.browser_profile["locale"] == "en_US"
    assert flow.state.browser_profile["language"] == "en-US"
    assert flow.state.browser_profile["timezone"] == "America/New_York"
    assert flow.state.browser_profile["timezone_offset_minutes"] == timezone_profile("America/New_York")["timezone_offset_minutes"]
    assert flow.state.browser_profile["timezone_offset_ms"] == flow.state.browser_profile["timezone_offset_minutes"] * 60 * 1000
    assert variables["country"] == "US"
    assert variables["phone"] == {"countryCode": "1", "number": "4647681720", "type": "MOBILE"}
    assert variables["billingAddress"]["line1"] == "2829 Babcock Road"
    assert "line2" not in variables["billingAddress"]
    assert variables["marketingOptOut"] is False
    assert "dateOfBirth" not in variables
    assert "identityDocument" not in variables
    assert "productClass" not in variables["card"]

    captured: dict[str, object] = {}
    monkeypatch.setattr("paypal.flow.send_weasley_log", lambda *_args, **kwargs: captured.update(kwargs))

    def graphql(operation: str, _query: str, payload: dict[str, object], _signup_url: str):
        captured["operation"] = operation
        captured["payload"] = payload
        return {
            "data": {
                "initiateRiskBasedTwoFactorPhoneConfirmation": {
                    "authId": "auth-1",
                    "challengeId": "challenge-1",
                    "state": "SENT",
                }
            }
        }

    monkeypatch.setattr(flow, "_graphql_with_authchallenge_frontend_retry", graphql)
    assert flow._initiate_2fa_phone_confirmation("EC-TEST123", "https://www.paypal.com/checkoutweb/signup") == ("auth-1", "challenge-1")
    assert captured["payload"] == {
        "phoneNumber": "4647681720",
        "locale": {"country": "US", "lang": "en"},
        "phoneCountry": "US",
        "token": "EC-TEST123",
    }


def test_us_autocomplete_result_is_used_as_one_complete_line(monkeypatch) -> None:
    flow = _us_flow()
    def graphql(operation: str, *_args, **_kwargs):
        if operation == "AddressAutocompleteQuery":
            return {
                "data": {
                    "addressAutoComplete": {
                        "suggestions": [{"mainText": "2829 Babcock Road", "placeId": "place-1"}]
                    }
                }
            }
        assert operation == "AddressFromAutocompletePlaceIdQuery"
        return {
            "data": {
                "addressFromAutoCompletePlaceId": {"address": {
                    "line1": "2829 Babcock Road",
                    "line2": None,
                    "city": "San Antonio",
                    "state": "TX",
                    "postalCode": "78229",
                }}
            }
        }

    monkeypatch.setattr(flow.session, "graphql", graphql)

    flow._send_address_autocomplete("EC-TEST123")

    assert flow.address.house_number == ""
    assert flow._billing_line1() == "2829 Babcock Road"
    variables = flow._build_signup_variables("EC-TEST123")
    assert variables["billingAddress"]["line1"] == "2829 Babcock Road"
    assert "line2" not in variables["billingAddress"]


class _USSMSClient:
    def __init__(self) -> None:
        self.price_request: tuple[str, str] | None = None
        self.number_request: tuple[str, str, str, float] | None = None

    def get_provider_prices(self, service: str, country: str):
        self.price_request = (service, country)
        return [{"provider_id": "us-1", "price": 0.5, "count": 1}]

    def get_number_v2(self, *, service: str, country: str, provider_id: str, max_price: float):
        self.number_request = (service, country, provider_id, max_price)
        return {
            "activationId": "us-activation",
            "phoneNumber": "4647681720",
            "activationOperator": provider_id,
            "activationCost": max_price,
        }

    def get_status(self, _activation_id: str) -> str:
        return "STATUS_WAIT_CODE"

    def set_status(self, _activation_id: str, _status: int) -> str:
        return "ACCESS_READY"


def test_smsbower_us_uses_us_country_id_and_does_not_reuse_brazil_number(tmp_path: Path) -> None:
    store = SMSBowerActivationStore(tmp_path / "smsbower.json")
    store.remember_success(
        activation_id="br-activation",
        phone_number="+55119991001",
        provider_id="br-1",
        price=0.2,
        expires_at=time.time() + 600,
        country="73",
    )
    client = _USSMSClient()
    provider = SMSBowerOtpProvider(
        client=client,
        store=store,
        region="US",
        wait_seconds=0.01,
        poll_interval_seconds=0.01,
    )

    activation = provider.reserve_number()

    assert client.price_request == ("ts", "12")
    assert client.number_request == ("ts", "12", "us-1", 0.5)
    assert activation.phone_number == "+14647681720"
    assert activation.country == "12"
