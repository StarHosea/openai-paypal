"""Regional checkout settings shared by the CLI, web UI, and flow.

The captured US checkout uses ``X-Country: US``, ``X-Locale: en_US`` and a
10-digit NANP phone number with dialing code ``+1``.  Keep those values in
one place so a generated address, the SMS provider and GraphQL variables do
not accidentally use different countries.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class PayPalRegion:
    code: str
    display_name: str
    language: str
    locale: str
    dialing_code: str
    local_phone_lengths: tuple[int, ...]
    phone_example: str
    fallback_phone: str
    smsbower_country: str
    requires_identity_document: bool
    requires_date_of_birth: bool
    marketing_opt_out: bool
    timezone: str
    timezone_offset_minutes: int
    timezone_offset_ms: int
    dst: bool

    @property
    def language_code(self) -> str:
        return self.language.split("-", 1)[0].split("_", 1)[0].lower()

    @property
    def dialing_code_with_plus(self) -> str:
        return f"+{self.dialing_code}"

    def normalize_phone(self, value: object) -> tuple[str, str, str]:
        """Return E.164, dialing code, and local number for this region."""
        raw = str(value or "").strip()
        if raw.lower().startswith("phone:"):
            raw = raw.split(":", 1)[1].strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            raise ValueError("phone number is empty")

        dialing = self.dialing_code
        local = ""
        explicit_international = raw.lstrip().startswith("+")
        # A bare local BR number can legitimately begin with its country code
        # digits (area code 55).  Prefer the local interpretation unless the
        # input is explicitly E.164 or longer than the local format.
        looks_international = (
            explicit_international
            or len(digits) > max(self.local_phone_lengths)
            or (self.code == "BR" and len(digits) > 10)
        )
        if looks_international and digits.startswith(dialing):
            candidate = digits[len(dialing):]
            if len(candidate) in self.local_phone_lengths:
                local = candidate
        if not local and len(digits) in self.local_phone_lengths:
            local = digits
        if not local:
            expected = "/".join(str(length) for length in self.local_phone_lengths)
            raise ValueError(
                f"{self.code} phone number must contain {expected} local digits"
            )
        return f"+{dialing}{local}", f"+{dialing}", local

    def browser_profile_overrides(self) -> dict[str, object]:
        return {
            "country": self.code,
            "language": self.language,
            "locale": self.locale,
            "timezone": self.timezone,
            "timezone_offset_minutes": self.timezone_offset_minutes,
            "timezone_offset_ms": self.timezone_offset_ms,
            "dst": self.dst,
        }


# Region entries provide a fallback timezone only.  A proxied flow replaces it
# with the exit IP's IANA timezone before the browser profile is created.
_REGIONS: Mapping[str, PayPalRegion] = {
    "US": PayPalRegion(
        code="US",
        display_name="United States",
        language="en-US",
        locale="en_US",
        dialing_code="1",
        local_phone_lengths=(10,),
        phone_example="+14647681720",
        fallback_phone="+14647681720",
        # SMS-Activate/SMSBower country id for the United States.
        smsbower_country="12",
        requires_identity_document=False,
        requires_date_of_birth=False,
        marketing_opt_out=False,
        timezone="America/New_York",
        # These values are recalculated at flow creation; keep the fallback
        # internally consistent with JavaScript Date#getTimezoneOffset.
        timezone_offset_minutes=240,
        timezone_offset_ms=240 * 60 * 1000,
        dst=True,
    ),
    "BR": PayPalRegion(
        code="BR",
        display_name="Brazil",
        language="pt-BR",
        locale="pt_BR",
        dialing_code="55",
        # Retain the historical permissive range for existing BR providers.
        local_phone_lengths=(8, 9, 10, 11),
        phone_example="+5591980133818",
        fallback_phone="+5500000000000",
        smsbower_country="73",
        requires_identity_document=True,
        requires_date_of_birth=True,
        marketing_opt_out=True,
        timezone="America/Sao_Paulo",
        timezone_offset_minutes=180,
        timezone_offset_ms=180 * 60 * 1000,
        dst=False,
    ),
}

_ALIASES = {
    "USA": "US",
    "UNITED_STATES": "US",
    "UNITED-STATES": "US",
    "BRAZIL": "BR",
    "BRASIL": "BR",
}


def get_region(value: object | None = None, *, default: str = "BR") -> PayPalRegion:
    """Resolve a supported region code without relying on process-global state."""
    raw = str(value or default).strip().upper().replace(" ", "_")
    code = _ALIASES.get(raw, raw)
    try:
        return _REGIONS[code]
    except KeyError as exc:
        supported = ", ".join(_REGIONS)
        raise ValueError(f"Unsupported PayPal region {value!r}; supported: {supported}") from exc


def supported_region_codes() -> tuple[str, ...]:
    return tuple(_REGIONS)


def configured_region_code(default: str = "US") -> str:
    """Read ``PAYPAL_REGION`` from the environment or local ``.env`` safely."""
    raw = os.getenv("PAYPAL_REGION", "").strip()
    if not raw:
        roots = (Path.cwd(), Path(__file__).resolve().parents[1])
        seen: set[Path] = set()
        for root in roots:
            env_path = root / ".env"
            if env_path in seen or not env_path.is_file():
                continue
            seen.add(env_path)
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    item = line.strip()
                    if not item or item.startswith("#") or "=" not in item:
                        continue
                    key, value = item.split("=", 1)
                    if key.strip() == "PAYPAL_REGION":
                        raw = value.strip().strip('"').strip("'")
                        break
            except Exception:
                continue
            if raw:
                break
    return get_region(raw or default, default=default).code
