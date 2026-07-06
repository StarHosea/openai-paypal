#!/usr/bin/env python3
"""Offline diagnostics for captured PayPal risk-control artifacts.

This script does not make network requests.  It inspects local HAR files and
cached authchallenge HTML files, then reports obvious consistency problems:
captcha type coverage, challenge locale/country mismatches, and captured
validatecaptcha field shapes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import BROWSER_PROFILE  # noqa: E402


def _html_attr(html: str, attr: str) -> str:
    m = re.search(r"\b" + re.escape(attr) + r"=[\"']([^\"']*)", html or "", re.I)
    return m.group(1) if m else ""


def _first_iframe_src(html: str) -> str:
    m = re.search(r"<iframe\b[^>]+src=[\"']([^\"']+)", html or "", re.I)
    return m.group(1).replace("&amp;", "&") if m else ""


def inspect_html(path: Path) -> dict:
    text = path.read_text(errors="replace")
    iframe = _first_iframe_src(text)
    query = parse_qs(urlparse(iframe).query)
    captcha_type = (_html_attr(text, "data-captcha-type") or "").lower()
    return {
        "path": str(path),
        "kind": "authchallenge_html",
        "captcha_type": captcha_type or None,
        "iframe_country": (query.get("country.x") or [""])[0] or None,
        "iframe_locale": (query.get("locale.x") or [""])[0] or None,
        "sitekey_present": bool((query.get("siteKey") or query.get("sitekey") or [""])[0] or _html_attr(text, "data-sitekey")),
        "has_recaptcha_v3": "recaptchav3" in text.lower() or "grcv3" in text.lower(),
        "has_hcaptcha_passive": "hcaptchapassive" in text.lower(),
    }


def inspect_har(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(errors="replace"))
    except Exception as exc:
        return [{"path": str(path), "kind": "har_error", "error": str(exc)}]
    out: list[dict] = []
    for idx, entry in enumerate(data.get("log", {}).get("entries", [])):
        req = entry.get("request", {})
        url = req.get("url", "")
        parsed = urlparse(url)
        if parsed.path.endswith("/auth/validatecaptcha"):
            params = (req.get("postData") or {}).get("params") or []
            field_names = [p.get("name") for p in params if p.get("name")]
            out.append({
                "path": str(path),
                "kind": "har_validatecaptcha",
                "entry": idx,
                "status": (entry.get("response") or {}).get("status"),
                "fields": field_names,
                "captcha_family": (
                    "recaptchav3" if "grcV3EntToken" in field_names else
                    "hcaptchapassive" if "hcaptchaToken" in field_names else
                    "hcaptcha" if "hcaptcha" in field_names else
                    "recaptcha" if "recaptcha" in field_names else
                    "unknown"
                ),
            })
        if "t.paypal.com" in parsed.netloc and parsed.path.endswith("/ts"):
            q = parse_qs(parsed.query)
            out.append({
                "path": str(path),
                "kind": "har_fpti",
                "entry": idx,
                "g": (q.get("g") or [""])[0] or None,
                "rsta": (q.get("rsta") or [""])[0] or None,
                "ccpg": (q.get("ccpg") or [""])[0] or None,
                "page": (q.get("page") or [""])[0] or None,
            })
    return out


def main() -> int:
    expected_country = str(BROWSER_PROFILE.get("country") or "")
    expected_locale = str(BROWSER_PROFILE.get("locale") or "")
    expected_tz = str(BROWSER_PROFILE.get("timezone_offset_minutes") or "")
    records: list[dict] = [{
        "kind": "expected_profile",
        "country": expected_country,
        "locale": expected_locale,
        "timezone_offset_minutes": expected_tz,
        "language": BROWSER_PROFILE.get("language"),
    }]

    for path in sorted(ROOT.glob("*.har")):
        records.extend(inspect_har(path))
    for path in sorted(Path("/tmp").glob("paypal_gql_*_last.html")) + sorted(Path("/tmp").glob("paypal_signup_metadata_last.html")):
        if path.is_file():
            records.append(inspect_html(path))

    findings: list[str] = []
    for rec in records:
        if rec.get("kind") == "authchallenge_html":
            if rec.get("iframe_country") and rec["iframe_country"] != expected_country:
                findings.append(f"{rec['path']}: iframe country {rec['iframe_country']} != expected {expected_country}")
            if rec.get("iframe_locale") and rec["iframe_locale"] != expected_locale:
                findings.append(f"{rec['path']}: iframe locale {rec['iframe_locale']} != expected {expected_locale}")
        if rec.get("kind") == "har_fpti":
            if rec.get("ccpg") and rec["ccpg"] != expected_country:
                findings.append(f"{rec['path']}#{rec['entry']}: FPTI ccpg {rec['ccpg']} != expected {expected_country}")
            if rec.get("rsta") and rec["rsta"] != expected_locale:
                findings.append(f"{rec['path']}#{rec['entry']}: FPTI rsta {rec['rsta']} != expected {expected_locale}")
            if rec.get("g") and rec["g"] != expected_tz:
                findings.append(f"{rec['path']}#{rec['entry']}: FPTI g {rec['g']} != expected {expected_tz}")

    print(json.dumps({"records": records, "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
