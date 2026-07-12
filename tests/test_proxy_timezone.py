from __future__ import annotations

from datetime import datetime, timezone

from paypal.proxy import ProxyConfig, ProxyEntry, proxy_timezone_profile, timezone_profile


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
