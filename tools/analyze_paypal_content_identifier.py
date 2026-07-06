#!/usr/bin/env python3
"""Diagnose PayPal checkoutweb signup contentIdentifier provenance.

Usage:
  python3 tools/analyze_paypal_content_identifier.py captures/roxy-paypal-...
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paypal.flow import PayPalFlow  # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _first(paths):
    return next(iter(paths), None)


def _offset(path: Path, needle: str) -> int:
    if not path or not path.is_file():
        return -1
    return _read(path).find(needle)


def _request_content_identifier(path: Path) -> str:
    if not path or not path.is_file():
        return ""
    text = _read(path)
    match = re.search(
        r'"contentIdentifier"\s*:\s*"([^"]+signupTerms)"',
        text,
        re.I,
    )
    return match.group(1) if match else ""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2

    cap = Path(sys.argv[1]).expanduser().resolve()
    if not cap.is_dir():
        print(f"capture dir not found: {cap}", file=sys.stderr)
        return 2

    html_path = _first(
        sorted(
            (cap / "html").glob("*checkoutweb_signup*.html"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
    )
    manifest_path = _first(sorted((cap / "network" / "bodies").glob("*content-manifest*.json")))
    signup_req_path = _first(
        sorted((cap / "network" / "requests").glob("*SignUpNewMemberMutation*.txt"))
    )
    js_paths = sorted((cap / "js").glob("*.js"))
    main_js = _first([p for p in js_paths if "weasley_main" in p.name]) or _first(
        [p for p in js_paths if "main" in p.name]
    )

    result = {
        "capture": str(cap),
        "signup_html": str(html_path) if html_path else "",
        "manifest_json": str(manifest_path) if manifest_path else "",
        "weasley_main_js": str(main_js) if main_js else "",
        "signup_request": str(signup_req_path) if signup_req_path else "",
    }

    initial = {}
    if html_path:
        html = _read(html_path)
        initial = PayPalFlow._extract_window_initial_data(html)
        manifest_url = PayPalFlow._extract_content_manifest_url(html, initial)
        content_hash = PayPalFlow._extract_content_hash(
            html,
            initial,
            include_generic_content_hash=True,
        )
        result.update(
            {
                "html.locality": initial.get("locality"),
                "html.contentHash": content_hash,
                "html.contentManifest": initial.get("contentManifest"),
                "html.contentManifestUrl": manifest_url,
            }
        )

    country = str(((initial.get("locality") or {}).get("country") or "BR")).upper()
    lang = str(((initial.get("locality") or {}).get("language") or "pt")).lower()
    manifest_hash = ""
    manifest_key = f"{country}_{lang}"
    if manifest_path:
        manifest = json.loads(_read(manifest_path))
        manifest_hash = str(manifest.get(manifest_key) or "")
        result.update(
            {
                "manifest.key": manifest_key,
                "manifest.hash": manifest_hash,
            }
        )

    hash_for_identifier = manifest_hash or str(result.get("html.contentHash") or "")
    if hash_for_identifier:
        result["derived.contentIdentifier"] = (
            f"{country}:{lang}:{hash_for_identifier}:compliance.signupTerms"
        )
    result["request.contentIdentifier"] = _request_content_identifier(signup_req_path)

    if main_js:
        result["js.offsets"] = {
            "contentIdentifier": _offset(main_js, "contentIdentifier"),
            "compliance.signupTerms": _offset(main_js, "compliance.signupTerms"),
            "contentManifestUrl": _offset(main_js, "contentManifestUrl"),
            "content_successfully_fetched": _offset(main_js, "content_successfully_fetched"),
            "updating_content_cache": _offset(main_js, "updating_content_cache"),
            "Object(s.j)().contentHash": _offset(main_js, "Object(s.j)().contentHash"),
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
