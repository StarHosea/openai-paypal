"""Proxy helpers for outbound HTTP requests.

Supports custom environment proxy lines in the form:
    host:port:username:password
and direct proxy URLs in the form:
    http://username:password@host:port

Both formats are converted to httpx-compatible proxy URLs.
"""
from __future__ import annotations

import os
import random
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TRUE_VALUES = {"1", "true", "yes", "on", "enable", "enabled", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled", "n", ""}


def _load_dotenv_value(name: str) -> str:
    """Read a single value from local .env without an extra dependency."""
    if os.getenv(name):
        return os.getenv(name, "").strip()
    roots = [Path.cwd(), Path(__file__).resolve().parents[1]]
    seen: set[Path] = set()
    for root in roots:
        env_path = root / ".env"
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() != name:
                    continue
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(name, value)
                return value
        except Exception:
            continue
    return ""


@dataclass(frozen=True)
class ProxyEntry:
    host: str
    port: int
    username: str
    password: str
    scheme: str = "http"

    @classmethod
    def parse(cls, raw: str) -> "ProxyEntry":
        value = (raw or "").strip()
        if not value:
            raise ValueError("代理配置为空")

        # Already a URL.  This path is mainly for env overrides such as
        # PAYPAL_PROXY_URL=http://user:pass@host:port.
        if "://" in value:
            from urllib.parse import urlsplit, unquote

            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https", "socks5", "socks5h"}:
                raise ValueError(f"不支持的代理协议：{parsed.scheme}")
            if not parsed.hostname or not parsed.port:
                raise ValueError("代理 URL 必须包含 host 和 port")
            return cls(
                host=parsed.hostname,
                port=int(parsed.port),
                username=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                scheme=parsed.scheme,
            )

        parts = value.split(":", 3)
        if len(parts) != 4:
            raise ValueError("代理格式应为 host:port:username:password")
        host, port_text, username, password = [part.strip() for part in parts]
        if not host:
            raise ValueError("代理 host 不能为空")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError("代理 port 必须是数字") from exc
        if not (1 <= port <= 65535):
            raise ValueError("代理 port 超出范围")
        if not username or not password:
            raise ValueError("代理 username/password 不能为空")
        return cls(host=host, port=port, username=username, password=password)

    @property
    def url(self) -> str:
        user = quote(self.username, safe="")
        password = quote(self.password, safe="")
        auth = f"{user}:{password}@" if self.username or self.password else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    @property
    def masked(self) -> str:
        auth = "***:***@" if self.username or self.password else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


@dataclass(frozen=True)
class ProxyConfig:
    enabled: bool
    entry: ProxyEntry | None = None

    @property
    def url(self) -> str | None:
        return self.entry.url if self.enabled and self.entry else None

    @property
    def label(self) -> str:
        if not self.enabled or not self.entry:
            return "代理关闭"
        return self.entry.masked


# A browser locale is not a checkout locale.  The former is part of the
# device/network fingerprint and should describe the proxy exit, while the
# latter is selected by the checkout flow.  Keep this intentionally small and
# use the geo provider's advertised language when it is available; the table
# supplies a sensible BCP-47 fallback for providers that only return a country
# code.
_COUNTRY_LANGUAGE_DEFAULTS: dict[str, str] = {
    "AE": "ar-AE",
    "AR": "es-AR",
    "AT": "de-AT",
    "AU": "en-AU",
    "BE": "nl-BE",
    "BG": "bg-BG",
    "BO": "es-BO",
    "BR": "pt-BR",
    "CA": "en-CA",
    "CH": "de-CH",
    "CL": "es-CL",
    "CN": "zh-CN",
    "CO": "es-CO",
    "CR": "es-CR",
    "CZ": "cs-CZ",
    "DE": "de-DE",
    "DK": "da-DK",
    "DO": "es-DO",
    "EC": "es-EC",
    "EE": "et-EE",
    "EG": "ar-EG",
    "ES": "es-ES",
    "FI": "fi-FI",
    "FR": "fr-FR",
    "GB": "en-GB",
    "GR": "el-GR",
    "GT": "es-GT",
    "HK": "zh-HK",
    "HN": "es-HN",
    "HR": "hr-HR",
    "HU": "hu-HU",
    "ID": "id-ID",
    "IE": "en-IE",
    "IL": "he-IL",
    "IN": "en-IN",
    "IS": "is-IS",
    "IT": "it-IT",
    "JP": "ja-JP",
    "KE": "en-KE",
    "KR": "ko-KR",
    "LT": "lt-LT",
    "LU": "fr-LU",
    "LV": "lv-LV",
    "MA": "ar-MA",
    "MX": "es-MX",
    "MY": "ms-MY",
    "NG": "en-NG",
    "NL": "nl-NL",
    "NO": "nb-NO",
    "NZ": "en-NZ",
    "PA": "es-PA",
    "PE": "es-PE",
    "PH": "en-PH",
    "PK": "en-PK",
    "PL": "pl-PL",
    "PR": "es-PR",
    "PT": "pt-PT",
    "RO": "ro-RO",
    "RS": "sr-RS",
    "RU": "ru-RU",
    "SA": "ar-SA",
    "SE": "sv-SE",
    "SG": "en-SG",
    "SI": "sl-SI",
    "SK": "sk-SK",
    "TH": "th-TH",
    "TR": "tr-TR",
    "TW": "zh-TW",
    "UA": "uk-UA",
    "US": "en-US",
    "UY": "es-UY",
    "VE": "es-VE",
    "VN": "vi-VN",
    "ZA": "en-ZA",
}

_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?$")


def _first_text(mapping: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalise_country_code(value: object) -> str:
    country = str(value or "").strip().upper()
    return country if re.fullmatch(r"[A-Z]{2}", country) else ""


def _normalise_language_tag(value: object, country: str = "") -> str:
    """Return one safe browser language tag from a geo-provider value."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    # ipapi.co returns e.g. ``en-US,es-US,haw``.  The first language is the
    # provider's primary locale and is a better choice than guessing solely
    # from the country.
    raw = re.split(r"[,;\s]+", raw, maxsplit=1)[0].replace("_", "-")
    if not _LANGUAGE_TAG_RE.fullmatch(raw):
        return ""
    parts = raw.split("-", 1)
    language = parts[0].lower()
    region = parts[1].upper() if len(parts) == 2 and len(parts[1]) == 2 else ""
    if not region and country:
        # Avoid emitting a bare ``en`` / ``pt`` locale when a country is
        # known.  Browsers conventionally expose the country-specific form.
        return f"{language}-{country}"
    return f"{language}-{region}" if region else language


def _language_profile(country: str, advertised_languages: object = "") -> dict[str, object]:
    """Build coherent navigator/Accept-Language values for a country."""
    language = _normalise_language_tag(advertised_languages, country)
    if not language:
        language = _COUNTRY_LANGUAGE_DEFAULTS.get(country, "en-US")
    language_root = language.split("-", 1)[0]
    values: list[str] = []
    for item in (language, language_root, "en-US", "en"):
        if item and item not in values:
            values.append(item)
    return {
        "language": language,
        "locale": language.replace("-", "_"),
        "languages": values,
    }


def _number_or_none(value: object, *, minimum: float, maximum: float) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    if not minimum <= number <= maximum:
        return None
    # Geo-IP precision is neither reliable nor useful beyond this level.  It
    # also prevents a provider's noisy precision from making profiles differ
    # unnecessarily between otherwise identical exit locations.
    return round(number, 4)


def _timezone_from_payload(payload: Mapping[str, object]) -> str:
    raw = payload.get("timezone")
    if isinstance(raw, Mapping):
        raw = _first_text(raw, "id", "name", "timezone")
    candidate = str(raw or _first_text(payload, "time_zone", "timeZone") or "").strip()
    if not candidate:
        return ""
    try:
        return ZoneInfo(candidate).key
    except (ZoneInfoNotFoundError, ValueError):
        return ""


def normalize_proxy_geo_payload(payload: object) -> dict[str, object]:
    """Normalize common Geo-IP response formats into browser-safe fields.

    Supported shapes include ipapi.co, ipwho.is, ipinfo.io, ip-api.com and a
    simple custom endpoint.  Invalid/incomplete values are discarded instead
    of being copied into browser-visible profile fields.
    """
    if not isinstance(payload, Mapping):
        return {}
    # ipwho.is and ip-api.com explicitly report an unsuccessful lookup.
    if payload.get("success") is False or str(payload.get("status") or "").lower() == "fail":
        return {}

    country = _normalise_country_code(
        _first_text(payload, "country_code", "countryCode", "country", "countryCode2")
    )
    # Some endpoints expose a country object instead of a code.
    raw_country = payload.get("country")
    if not country and isinstance(raw_country, Mapping):
        country = _normalise_country_code(
            _first_text(raw_country, "code", "iso_code", "isoCode", "country_code")
        )
    country_name = ""
    if isinstance(raw_country, str) and not _normalise_country_code(raw_country):
        country_name = raw_country.strip()
    if not country_name:
        country_name = _first_text(payload, "country_name", "countryName")
    if isinstance(raw_country, Mapping) and not country_name:
        country_name = _first_text(raw_country, "name", "country_name")

    ip = _first_text(payload, "ip", "query", "ip_address", "ipAddress")
    timezone_name = _timezone_from_payload(payload)
    region = _first_text(payload, "region", "region_name", "regionName", "state", "province")
    region_code = _first_text(payload, "region_code", "regionCode", "state_code", "stateCode")
    city = _first_text(payload, "city", "town")
    postal_code = _first_text(payload, "postal", "postal_code", "postalCode", "zip")
    latitude = _number_or_none(
        payload.get("latitude", payload.get("lat")), minimum=-90, maximum=90
    )
    longitude = _number_or_none(
        payload.get("longitude", payload.get("lon", payload.get("lng"))), minimum=-180, maximum=180
    )
    # ipinfo.io exposes coordinates as ``loc: \"latitude,longitude\"``.
    if latitude is None or longitude is None:
        loc = _first_text(payload, "loc", "location")
        if "," in loc:
            lat_text, lon_text = loc.split(",", 1)
            latitude = latitude if latitude is not None else _number_or_none(lat_text, minimum=-90, maximum=90)
            longitude = longitude if longitude is not None else _number_or_none(lon_text, minimum=-180, maximum=180)

    connection = payload.get("connection")
    connection_data = connection if isinstance(connection, Mapping) else {}
    asn = _first_text(payload, "asn", "as", "org") or _first_text(connection_data, "asn", "org", "isp")
    isp = _first_text(payload, "isp", "org") or _first_text(connection_data, "isp", "org")
    advertised_languages = _first_text(payload, "languages", "language", "locale")

    result: dict[str, object] = {}
    if ip:
        result["ip"] = ip
    if country:
        result["country"] = country
    if country_name:
        result["country_name"] = country_name
    if region:
        result["region"] = region
    if region_code:
        result["region_code"] = region_code
    if city:
        result["city"] = city
    if postal_code:
        result["postal_code"] = postal_code
    if timezone_name:
        result["timezone"] = timezone_name
    if latitude is not None and longitude is not None:
        result["latitude"] = latitude
        result["longitude"] = longitude
    if asn:
        result["asn"] = asn
    if isp:
        result["isp"] = isp
    if advertised_languages:
        result["languages"] = advertised_languages
    return result


def lookup_proxy_geo(proxy: ProxyConfig) -> dict[str, object]:
    """Look up the selected proxy's exit location through that same proxy."""
    if not proxy.enabled or not proxy.url:
        return {}
    endpoint = _load_dotenv_value("PAYPAL_PROXY_GEO_URL") or "https://ipapi.co/json/"
    try:
        timeout = float(_load_dotenv_value("PAYPAL_PROXY_GEO_TIMEOUT_SECONDS") or "4")
    except ValueError:
        timeout = 4.0
    try:
        import httpx

        with httpx.Client(proxy=proxy.url, timeout=max(0.5, timeout)) as client:
            payload = client.get(endpoint).json()
        return normalize_proxy_geo_payload(payload)
    except Exception:
        # Geo-IP enrichment is optional.  A temporary provider/proxy failure
        # must never prevent a browser session from being created.
        return {}


def _proxy_geo_lookup_enabled() -> bool:
    # ``parse_bool(\"\", True)`` intentionally treats an explicitly empty
    # boolean as false for generic config flags.  Geo lookup is documented as
    # opt-out, though, so an absent/blank value must retain its default-on
    # behaviour rather than silently skipping IP-based fingerprinting.
    raw = _load_dotenv_value("PAYPAL_PROXY_GEO_LOOKUP")
    return parse_bool(raw, True) if str(raw).strip() else True


def _apply_proxy_geo_metadata(profile: dict[str, object], geo: Mapping[str, object]) -> None:
    """Attach non-browser metadata without leaking provider-specific keys."""
    if not geo:
        return
    profile["proxy_geo"] = dict(geo)
    profile["proxy_geo_resolved"] = True
    if geo.get("ip"):
        profile["proxy_exit_ip"] = str(geo["ip"])
    for source, target in (
        ("country", "proxy_country"),
        ("region", "proxy_region"),
        ("region_code", "proxy_region_code"),
        ("city", "proxy_city"),
        ("asn", "proxy_asn"),
        ("isp", "proxy_isp"),
    ):
        if geo.get(source):
            profile[target] = geo[source]
    latitude = geo.get("latitude")
    longitude = geo.get("longitude")
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        profile["geolocation"] = {
            "latitude": float(latitude),
            "longitude": float(longitude),
            # City-level Geo-IP typically has kilometre-scale uncertainty.
            "accuracy": 25_000,
        }


def proxy_fingerprint_profile(proxy: ProxyConfig, fallback: Mapping[str, object]) -> dict[str, object]:
    """Return browser fingerprint overrides aligned with the proxy exit IP.

    The checkout region is intentionally kept in ``checkout_*`` fields by the
    caller.  This function updates browser-facing country/language/locale,
    timezone and optional geolocation only, so a US checkout can still be
    selected explicitly when the user is travelling behind another country's
    proxy.

    ``PAYPAL_PROXY_FINGERPRINT_GEO=0`` retains the legacy behaviour of using
    only the proxy timezone while still recording no country/locale override.
    ``PAYPAL_PROXY_TIMEZONE`` always wins over Geo-IP timezone data.
    """
    profile = dict(fallback)
    configured_timezone = _load_dotenv_value("PAYPAL_PROXY_TIMEZONE")
    if configured_timezone:
        try:
            profile.update(timezone_profile(configured_timezone))
        except ValueError:
            # Keep the regional fallback if an operator supplied an invalid
            # override; this matches the failure-tolerant lookup behaviour.
            pass

    if not proxy.enabled or not proxy.url or not _proxy_geo_lookup_enabled():
        return profile

    geo = lookup_proxy_geo(proxy)
    if not geo:
        return profile
    _apply_proxy_geo_metadata(profile, geo)

    if not configured_timezone and isinstance(geo.get("timezone"), str):
        try:
            profile.update(timezone_profile(str(geo["timezone"])))
        except ValueError:
            pass

    fingerprint_geo = _load_dotenv_value("PAYPAL_PROXY_FINGERPRINT_GEO")
    if str(fingerprint_geo).strip() and not parse_bool(fingerprint_geo, True):
        return profile
    country = _normalise_country_code(geo.get("country"))
    if country:
        profile["country"] = country
        profile.update(_language_profile(country, geo.get("languages", "")))
    return profile


def timezone_profile(timezone_name: str, *, now: datetime | None = None) -> dict[str, object]:
    """Return browser timezone fields for an IANA timezone.

    JavaScript's ``Date#getTimezoneOffset`` has the inverse sign of UTC
    offsets.  Calculate it from the zone at runtime so US DST transitions do
    not leave a stale, hard-coded offset in Tealeaf/FraudNet payloads.
    """
    try:
        zone = ZoneInfo(str(timezone_name))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"无效 IANA timezone：{timezone_name!r}") from exc
    instant = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    offset = instant.astimezone(zone).utcoffset()
    offset_minutes = -int((offset.total_seconds() if offset else 0) // 60)
    return {
        "timezone": zone.key,
        "timezone_offset_minutes": offset_minutes,
        "timezone_offset_ms": offset_minutes * 60 * 1000,
        "dst": bool(instant.astimezone(zone).dst()),
    }


def proxy_timezone_profile(proxy: ProxyConfig, fallback: dict[str, object]) -> dict[str, object]:
    """Resolve the browser timezone from the proxy exit IP when configured.

    ``PAYPAL_PROXY_TIMEZONE`` is an explicit, deterministic override.  When
    it is absent and a proxy is active, the optional lookup endpoint is called
    *through that same proxy*, preventing the local machine's timezone from
    leaking into the browser profile.  The endpoint may return either
    ``timezone`` as a string (ipapi.co style) or ``timezone.id`` (ipwho.is
    style).  Failures retain the supplied regional fallback.
    """
    configured = _load_dotenv_value("PAYPAL_PROXY_TIMEZONE")
    if configured:
        return timezone_profile(configured)
    if not proxy.enabled or not proxy.url:
        return dict(fallback)
    if not _proxy_geo_lookup_enabled():
        return dict(fallback)

    geo = lookup_proxy_geo(proxy)
    raw_timezone = geo.get("timezone") if geo else None
    if isinstance(raw_timezone, str) and raw_timezone:
        try:
            return timezone_profile(raw_timezone)
        except ValueError:
            pass
    return dict(fallback)


def parse_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


def _split_pool(raw: str) -> list[str]:
    lines: list[str] = []
    for item in (raw or "").replace(",", "\n").splitlines():
        item = item.strip()
        if item and not item.startswith("#"):
            lines.append(item)
    return lines


def load_proxy_pool() -> list[str]:
    env_url = _load_dotenv_value("PAYPAL_PROXY_URL")
    if env_url:
        return [env_url]

    env_pool = _split_pool(_load_dotenv_value("PAYPAL_PROXY_POOL"))
    if env_pool:
        return env_pool

    return []


def choose_proxy_entry(pool: Iterable[str] | None = None, index: int | None = None) -> ProxyEntry:
    entries = list(pool if pool is not None else load_proxy_pool())
    if not entries:
        raise ValueError("未配置代理池")
    if index is not None:
        if index < 0 or index >= len(entries):
            raise ValueError(f"代理序号超出范围：{index}，可用范围 0-{len(entries) - 1}")
        raw = entries[index]
    else:
        raw = random.choice(entries)
    return ProxyEntry.parse(raw)


def build_proxy_config(
    enabled: bool | None = None,
    index: int | None = None,
    proxy_url: str | None = None,
) -> ProxyConfig:
    """Return a selected proxy config.

    enabled=None means use config/env default.  If disabled, no proxy is selected.
    proxy_url is a per-run custom/chained proxy URL or host:port:user:pass line.
    When enabled is None, providing proxy_url implicitly enables the proxy.
    """
    custom_proxy = (proxy_url or "").strip()
    if enabled is None:
        # Env can override the default at process startup without code changes.
        should_enable = bool(custom_proxy) or parse_bool(_load_dotenv_value("PAYPAL_PROXY_ENABLED"), False)
    else:
        # Explicit CLI/API choices must win so the proxy can be toggled dynamically.
        should_enable = bool(enabled)
    if not should_enable:
        return ProxyConfig(enabled=False)
    if custom_proxy:
        return ProxyConfig(enabled=True, entry=ProxyEntry.parse(custom_proxy))
    return ProxyConfig(enabled=True, entry=choose_proxy_entry(index=index))
