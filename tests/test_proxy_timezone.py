from __future__ import annotations

from datetime import datetime, timezone

from paypal.flow import PayPalFlow
from paypal.models import BillingAddress, CardInfo, UserInfo
from paypal.proxy import (
    ProxyConfig,
    ProxyEntry,
    normalize_proxy_geo_payload,
    proxy_fingerprint_profile,
    proxy_timezone_profile,
    timezone_profile,
)


def test_timezone_profile_uses_javascript_offset_sign_and_dst() -> None:
    profile = timezone_profile(
        "America/Los_Angeles",
        now=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )

    assert profile == {
        "timezone": "America/Los_Angeles",
        "timezone_offset_minutes": 420,
        "timezone_offset_ms": 25_200_000,
        "dst": True,
    }


def test_proxy_timezone_explicit_override_does_not_need_network(monkeypatch) -> None:
    monkeypatch.setattr(
        "paypal.proxy._load_dotenv_value",
        lambda name: "America/Chicago" if name == "PAYPAL_PROXY_TIMEZONE" else "",
    )
    proxy = ProxyConfig(enabled=True, entry=ProxyEntry("127.0.0.1", 8080, "u", "p"))

    result = proxy_timezone_profile(proxy, {"timezone": "America/New_York"})

    assert result["timezone"] == "America/Chicago"
    assert result["timezone_offset_ms"] == result["timezone_offset_minutes"] * 60 * 1000


def test_proxy_timezone_keeps_fallback_when_lookup_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr("paypal.proxy._load_dotenv_value", lambda name: "false" if name == "PAYPAL_PROXY_GEO_LOOKUP" else "")
    fallback = {"timezone": "America/New_York", "timezone_offset_minutes": 240}
    proxy = ProxyConfig(enabled=True, entry=ProxyEntry("127.0.0.1", 8080, "u", "p"))

    assert proxy_timezone_profile(proxy, fallback) == fallback


def test_proxy_fingerprint_profile_uses_exit_geo_for_browser_identity(monkeypatch) -> None:
    monkeypatch.setattr("paypal.proxy._load_dotenv_value", lambda _name: "")
    monkeypatch.setattr(
        "paypal.proxy.lookup_proxy_geo",
        lambda _proxy: {
            "ip": "203.0.113.42",
            "country": "JP",
            "city": "Tokyo",
            "region": "Tokyo",
            "timezone": "Asia/Tokyo",
            "latitude": 35.6762,
            "longitude": 139.6503,
            "languages": "ja-JP,en;q=0.8",
        },
    )
    proxy = ProxyConfig(enabled=True, entry=ProxyEntry("127.0.0.1", 8080, "u", "p"))
    fallback = {
        "country": "BR",
        "language": "pt-BR",
        "locale": "pt_BR",
        "checkout_country": "US",
        "checkout_locale": "en_US",
    }

    profile = proxy_fingerprint_profile(proxy, fallback)

    assert profile["country"] == "JP"
    assert profile["language"] == "ja-JP"
    assert profile["locale"] == "ja_JP"
    assert profile["languages"] == ["ja-JP", "ja", "en-US", "en"]
    assert profile["timezone"] == "Asia/Tokyo"
    assert profile["timezone_offset_minutes"] == -540
    assert profile["proxy_exit_ip"] == "203.0.113.42"
    assert profile["proxy_city"] == "Tokyo"
    assert profile["geolocation"] == {
        "latitude": 35.6762,
        "longitude": 139.6503,
        "accuracy": 25_000,
    }
    # Network/browser geography must not rewrite the explicit checkout route.
    assert profile["checkout_country"] == "US"
    assert profile["checkout_locale"] == "en_US"


def test_proxy_fingerprint_profile_keeps_explicit_timezone_but_uses_ip_locale(monkeypatch) -> None:
    monkeypatch.setattr(
        "paypal.proxy._load_dotenv_value",
        lambda name: "America/Chicago" if name == "PAYPAL_PROXY_TIMEZONE" else "",
    )
    monkeypatch.setattr(
        "paypal.proxy.lookup_proxy_geo",
        lambda _proxy: {"country": "DE", "timezone": "Europe/Berlin"},
    )
    proxy = ProxyConfig(enabled=True, entry=ProxyEntry("127.0.0.1", 8080, "u", "p"))

    profile = proxy_fingerprint_profile(proxy, {"country": "BR", "language": "pt-BR"})

    assert profile["country"] == "DE"
    assert profile["language"] == "de-DE"
    assert profile["locale"] == "de_DE"
    assert profile["timezone"] == "America/Chicago"


def test_normalize_proxy_geo_payload_supports_ipwho_shape() -> None:
    geo = normalize_proxy_geo_payload(
        {
            "success": True,
            "ip": "2001:db8::2",
            "country": "Germany",
            "country_code": "DE",
            "region": "Hesse",
            "region_code": "HE",
            "city": "Frankfurt am Main",
            "latitude": 50.1109,
            "longitude": 8.6821,
            "timezone": {"id": "Europe/Berlin"},
            "connection": {"asn": "AS3320", "isp": "Example ISP"},
        }
    )

    assert geo == {
        "ip": "2001:db8::2",
        "country": "DE",
        "country_name": "Germany",
        "region": "Hesse",
        "region_code": "HE",
        "city": "Frankfurt am Main",
        "timezone": "Europe/Berlin",
        "latitude": 50.1109,
        "longitude": 8.6821,
        "asn": "AS3320",
        "isp": "Example ISP",
    }


def test_flow_separates_ip_fingerprint_geography_from_checkout_region(monkeypatch) -> None:
    monkeypatch.setattr("paypal.proxy._load_dotenv_value", lambda _name: "")
    monkeypatch.setattr(
        "paypal.proxy.lookup_proxy_geo",
        lambda _proxy: {"country": "JP", "timezone": "Asia/Tokyo", "languages": "ja-JP"},
    )
    proxy = ProxyConfig(enabled=True, entry=ProxyEntry("127.0.0.1", 8080, "u", "p"))
    flow = PayPalFlow(
        ba_token="BA-TEST",
        user=UserInfo(
            first_name="Ava",
            last_name="Smith",
            email="ava@example.test",
            phone="+14647681720",
            phone_local="4647681720",
            phone_country_code="+1",
            password="Password123!",
            dob="",
            cpf="",
        ),
        card=CardInfo("4111111111111111", "10/2028", "691"),
        address=BillingAddress(
            street="Babcock Road",
            house_number="2829",
            district="",
            city="San Antonio",
            state="TX",
            postal_code="78229",
            country="US",
        ),
        proxy_config=proxy,
        fingerprint_source="random",
        region="US",
    )
    try:
        profile = flow.state.browser_profile
        assert profile["country"] == "JP"
        assert profile["language"] == "ja-JP"
        assert profile["timezone"] == "Asia/Tokyo"
        assert profile["checkout_country"] == "US"
        assert profile["checkout_locale"] == "en_US"
        assert flow._profile_country() == "US"
        assert flow._profile_locale() == "en_US"
    finally:
        flow.close()
