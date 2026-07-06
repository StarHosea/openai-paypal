#!/usr/bin/env python3
"""Compare program-side traffic recorder output with a Roxy/Chrome capture.

Example:
  python3 tools/compare_paypal_traffic.py \
    --program captures/program-paypal-20260705-120000 \
    --roxy captures/roxy-paypal-20260705-103548
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paypal.traffic_recorder import redact  # noqa: E402


PAYPAL_HOST_RE = re.compile(
    r"(^|\.)paypal\.com$|(^|\.)paypalobjects\.com$|(^|\.)paypal\.cn$|(^|\.)venmo\.com$|"
    r"(^|\.)hcaptcha\.com$|(^|\.)recaptcha\.net$|(^|\.)google\.com$",
    re.I,
)

IMPORTANT_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "origin",
    "referer",
    "user-agent",
    "x-requested-with",
    "x-app-name",
    "paypal-client-context",
    "paypal-client-metadata-id",
    "x-paypal-internal-euat",
    "x-country",
    "x-locale",
    "x-datadome-clientid",
    "sec-fetch-site",
    "sec-fetch-mode",
    "sec-fetch-dest",
    "sec-fetch-user",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-arch",
    "sec-ch-ua-full-version-list",
    "sec-ch-device-memory",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def capture_kind(root: Path) -> str:
    meta = root / "metadata.json"
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            recorder = str(data.get("recorder") or "")
            if "roxy" in recorder or "cdp" in recorder:
                return "roxy"
            if "traffic_recorder" in recorder:
                return "program"
        except Exception:
            pass
    if (root / "resources.jsonl").is_file():
        return "roxy"
    return "program"


def event_file(root: Path) -> Path:
    return root / "network" / "events.jsonl"


def load_requests(root: Path, *, kind: str | None = None, include_static: bool = False) -> list[dict[str, Any]]:
    kind = kind or capture_kind(root)
    events = read_jsonl(event_file(root))
    responses_by_id: dict[Any, dict[str, Any]] = {}
    request_events: list[dict[str, Any]] = []
    for ev in events:
        typ = ev.get("type")
        if typ == "response":
            responses_by_id[ev.get("id")] = ev
        elif typ == "request":
            request_events.append(ev)

    out: list[dict[str, Any]] = []
    for ev in request_events:
        url = str(ev.get("url") or "")
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host or not PAYPAL_HOST_RE.search(host):
            continue
        resource_type = str(ev.get("resourceType") or "").lower()
        path_lower = urllib.parse.urlparse(url).path.lower()
        if not include_static and (
            resource_type in {"image", "font", "stylesheet", "script", "media"}
            or path_lower.endswith(
                (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif",
                    ".webp",
                    ".svg",
                    ".ico",
                    ".woff",
                    ".woff2",
                    ".ttf",
                    ".css",
                    ".js",
                    ".mjs",
                    ".map",
                )
            )
        ):
            # Static assets are useful for page fidelity but hide protocol gaps.
            # content-manifest/locales are fetches and stay included.
            continue
        record = dict(ev)
        record["captureKind"] = kind
        record["response"] = responses_by_id.get(ev.get("id")) or {}
        record["bodyText"] = load_body_text(root, ev)
        record["signature"] = signature(record)
        out.append(record)
    return out


def load_body_text(root: Path, ev: dict[str, Any]) -> str:
    post = ev.get("postData") or ev.get("requestBody") or {}
    path = post.get("path") if isinstance(post, dict) else ""
    if path:
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    return p.read_bytes().decode("utf-8", errors="replace")
                except Exception:
                    return ""
    preview = post.get("textPreview") or post.get("preview") if isinstance(post, dict) else ""
    return str(preview or "")


def parse_body_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None
    return None


def graphql_operation_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = parsed.query or ""
    if not query:
        return ""
    if "=" not in query and "&" not in query:
        return urllib.parse.unquote(query)
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    for key, value in pairs:
        if key and not value and key not in {"_"}:
            return key
    if pairs and pairs[0][0] and pairs[0][0] not in {"_"}:
        return pairs[0][0]
    return ""


def graphql_payload(ev: dict[str, Any]) -> dict[str, Any]:
    text = (ev.get("bodyText") or "").lstrip()
    if not text.startswith(("{", "[")):
        # Authchallenge form submits can POST back to /graphql?SignUp... with
        # application/x-www-form-urlencoded bodies such as _csrf=...&hcaptchaToken=...
        # They should not be treated as GraphQL payloads for body-shape checks.
        return {}
    body = parse_body_json(text)
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        return body
    return {}


def graphql_operation(ev: dict[str, Any]) -> str:
    op = graphql_operation_from_url(str(ev.get("url") or ""))
    payload = graphql_payload(ev)
    return str(payload.get("operationName") or op or "")


def normalize_path(path: str) -> str:
    if re.search(r"/checkoutweb/release/weasley/content-manifest\.[^/]+\.json$", path):
        return "/checkoutweb/release/weasley/content-manifest.json"
    if re.search(r"/checkoutweb/release/weasley/locales/[^/]+/[^/]+\.json$", path):
        return "/checkoutweb/release/weasley/locales/<country>/<lang.hash>.json"
    if re.search(r"/web/res/.+/(hcaptcha|recaptcha)/", path):
        return re.sub(r"/web/res/[^/]+/", "/web/res/<hash>/", path)
    return path.rstrip("/") or "/"


def signature(ev: dict[str, Any]) -> str:
    method = str(ev.get("method") or "GET").upper()
    url = str(ev.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = normalize_path(parsed.path or "/")
    if path in {"/graphql", "/graphql/"}:
        op = graphql_operation(ev)
        return f"{method} {host}/graphql?{op or '<unknown>'}"
    if host.endswith("paypalobjects.com"):
        host = "paypalobjects"
    return f"{method} {host}{path}"


def header_map(ev: dict[str, Any]) -> dict[str, str]:
    headers = ev.get("headers") or {}
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def body_shape(ev: dict[str, Any]) -> dict[str, Any]:
    payload = graphql_payload(ev)
    if not payload:
        return {}
    variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
    return {
        "topLevelKeys": sorted(str(k) for k in payload.keys()),
        "variableKeys": sorted(str(k) for k in variables.keys()),
        "contentIdentifier": variables.get("contentIdentifier"),
        "hasFnSyncData": "fn_sync_data" in payload,
    }


def compare_headers(roxy: dict[str, Any], program: dict[str, Any]) -> dict[str, Any]:
    rh = header_map(roxy)
    ph = header_map(program)
    rkeys = set(rh) & IMPORTANT_HEADERS
    pkeys = set(ph) & IMPORTANT_HEADERS
    missing = sorted(rkeys - pkeys)
    extra = sorted(pkeys - rkeys)
    different = []
    for key in sorted((rkeys & pkeys) - {"cookie", "authorization"}):
        rv = rh.get(key, "")
        pv = ph.get(key, "")
        if not rv or not pv:
            continue
        # Dynamic ids/tokens/referers can legitimately differ. Keep the signal
        # but do not compare exact values for these.
        if key in {
            "paypal-client-context",
            "paypal-client-metadata-id",
            "x-paypal-internal-euat",
            "referer",
        }:
            continue
        if rv != pv:
            different.append({"header": key, "roxy": rv[:180], "program": pv[:180]})
    return {"missing": missing, "extra": extra, "different": different}


def first_by_signature(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for item in items:
        out.setdefault(item["signature"], item)
    return out


def make_findings(
    missing: list[dict[str, Any]],
    extra: list[dict[str, Any]],
    matched_diffs: list[dict[str, Any]],
    program: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    missing_sigs = {m["signature"] for m in missing}
    if any("content-manifest" in sig for sig in missing_sigs):
        findings.append(
            {
                "level": "high",
                "title": "程序没有抓 checkoutweb content-manifest",
                "detail": "contentIdentifier 的 hash 来自 content-manifest 或 HTML contentHash；缺失会导致 SignUpNewMember 使用短 contentIdentifier。",
                "suggestion": "确保 signup HTML 正常加载后立即提取 contentManifestUrl，并请求 manifest 的 BR_pt hash；已在 flow.py 中补强缓存兜底。",
            }
        )
    for ev in program:
        if "/graphql?SignUpNewMember" in ev["signature"]:
            shape = body_shape(ev)
            if not shape:
                continue
            cid = str(shape.get("contentIdentifier") or "")
            if re.fullmatch(r"[A-Z]{2}:[a-z]{2}:compliance\.signupTerms", cid, re.I):
                findings.append(
                    {
                        "level": "high",
                        "title": "SignUpNewMember 仍在使用短 contentIdentifier",
                        "detail": cid,
                        "suggestion": "检查 /tmp 或 ./cache 的 paypal_signup_content_manifest_last.json 是否存在；或设置 PAYPAL_SIGNUP_CONTENT_HASH。",
                    }
                )
            if not shape.get("hasFnSyncData"):
                findings.append(
                    {
                        "level": "high",
                        "title": "SignUpNewMember 缺少 fn_sync_data",
                        "detail": "浏览器请求在 GraphQL 顶层带 fn_sync_data。",
                        "suggestion": "调用 session.graphql 时通过 extra_body 注入 build_signup_fn_sync_data 的结果。",
                    }
                )
    if any("idapps/graphql" in sig for sig in missing_sigs):
        findings.append(
            {
                "level": "medium",
                "title": "浏览器存在 idapps/graphql 风控请求，程序侧未匹配",
                "detail": "该请求通常围绕身份/挑战上下文，缺失可能提高后续 authchallenge 概率。",
                "suggestion": "从抓包里提取 idapps/graphql 的 body 和 headers，在 2FA/Signup 前复刻。",
            }
        )
    for diff in matched_diffs:
        if diff.get("headerDiff", {}).get("missing"):
            important = [
                h
                for h in diff["headerDiff"]["missing"]
                if h
                in {
                    "x-requested-with",
                    "x-app-name",
                    "paypal-client-context",
                    "paypal-client-metadata-id",
                    "x-country",
                    "x-locale",
                    "x-datadome-clientid",
                    "sec-fetch-site",
                    "sec-fetch-mode",
                    "sec-fetch-dest",
                }
            ]
            if important:
                findings.append(
                    {
                        "level": "medium",
                        "title": f"{diff['signature']} 缺少关键 header",
                        "detail": important,
                        "suggestion": "在 PayPalSession.graphql 或对应调用处补齐浏览器同款 header。",
                    }
                )
    # Deduplicate by title/detail.
    seen = set()
    unique = []
    for finding in findings:
        key = json.dumps([finding.get("title"), finding.get("detail")], ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def compare(program_dir: Path, roxy_dir: Path, *, include_static: bool = False) -> dict[str, Any]:
    program = load_requests(program_dir, kind="program", include_static=include_static)
    roxy = load_requests(roxy_dir, kind="roxy", include_static=include_static)
    pc = collections.Counter(item["signature"] for item in program)
    rc = collections.Counter(item["signature"] for item in roxy)

    missing = []
    for sig, count in sorted((rc - pc).items()):
        sample = next(item for item in roxy if item["signature"] == sig)
        missing.append({"signature": sig, "count": count, "sampleUrl": sample.get("url"), "resourceType": sample.get("resourceType")})

    extra = []
    for sig, count in sorted((pc - rc).items()):
        sample = next(item for item in program if item["signature"] == sig)
        extra.append({"signature": sig, "count": count, "sampleUrl": sample.get("url")})

    roxy_first = first_by_signature(roxy)
    program_first = first_by_signature(program)
    matched_diffs = []
    for sig in sorted(set(roxy_first) & set(program_first)):
        r = roxy_first[sig]
        p = program_first[sig]
        header_diff = compare_headers(r, p)
        body_diff = {}
        if "/graphql?" in sig:
            rb = body_shape(r)
            pb = body_shape(p)
            if rb or pb:
                body_diff = {
                    "roxy": redact(rb),
                    "program": redact(pb),
                    "missingTopLevelKeys": sorted(set(rb.get("topLevelKeys", [])) - set(pb.get("topLevelKeys", []))),
                    "missingVariableKeys": sorted(set(rb.get("variableKeys", [])) - set(pb.get("variableKeys", []))),
                    "extraVariableKeys": sorted(set(pb.get("variableKeys", [])) - set(rb.get("variableKeys", []))),
                }
        if header_diff["missing"] or header_diff["extra"] or header_diff["different"] or body_diff.get("missingTopLevelKeys") or body_diff.get("missingVariableKeys"):
            matched_diffs.append(
                {
                    "signature": sig,
                    "headerDiff": header_diff,
                    "bodyDiff": body_diff,
                }
            )

    report = {
        "programDir": str(program_dir),
        "roxyDir": str(roxy_dir),
        "programRequestCount": len(program),
        "roxyRequestCount": len(roxy),
        "programUniqueSignatures": len(pc),
        "roxyUniqueSignatures": len(rc),
        "missingInProgram": missing,
        "extraInProgram": extra,
        "matchedDiffs": matched_diffs[:200],
    }
    report["findings"] = make_findings(missing, extra, matched_diffs, program)
    return report


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# PayPal traffic diff report",
        "",
        f"- Program: `{report['programDir']}`",
        f"- Roxy: `{report['roxyDir']}`",
        f"- Program requests: {report['programRequestCount']} ({report['programUniqueSignatures']} unique)",
        f"- Roxy requests: {report['roxyRequestCount']} ({report['roxyUniqueSignatures']} unique)",
        "",
        "## Findings",
        "",
    ]
    if not report.get("findings"):
        lines.append("未触发内置高风险规则。")
    for item in report.get("findings", []):
        lines.extend(
            [
                f"### [{item.get('level')}] {item.get('title')}",
                "",
                f"- Detail: `{item.get('detail')}`",
                f"- Suggestion: {item.get('suggestion')}",
                "",
            ]
        )
    lines.extend(["## Missing in program", ""])
    for item in report.get("missingInProgram", [])[:80]:
        lines.append(f"- x{item['count']} `{item['signature']}`")
    lines.extend(["", "## Extra in program", ""])
    for item in report.get("extraInProgram", [])[:80]:
        lines.append(f"- x{item['count']} `{item['signature']}`")
    lines.extend(["", "## Matched diffs", ""])
    for item in report.get("matchedDiffs", [])[:60]:
        miss = item.get("headerDiff", {}).get("missing") or []
        body_miss = item.get("bodyDiff", {}).get("missingVariableKeys") or []
        if miss or body_miss:
            lines.append(f"- `{item['signature']}` headers_missing={miss} vars_missing={body_miss}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare program traffic against Roxy capture")
    parser.add_argument("--program", required=True, help="program traffic dir from --record-traffic")
    parser.add_argument("--roxy", required=True, help="Roxy CDP capture dir")
    parser.add_argument("--out", default="", help="Output report JSON path")
    parser.add_argument("--include-static", action="store_true", help="include scripts/css/images/fonts")
    args = parser.parse_args()

    program_dir = Path(args.program).expanduser().resolve()
    roxy_dir = Path(args.roxy).expanduser().resolve()
    if not program_dir.is_dir():
        print(f"program dir not found: {program_dir}", file=sys.stderr)
        return 2
    if not roxy_dir.is_dir():
        print(f"roxy dir not found: {roxy_dir}", file=sys.stderr)
        return 2

    report = compare(program_dir, roxy_dir, include_static=args.include_static)
    out = Path(args.out).expanduser().resolve() if args.out else program_dir / "traffic_diff_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, out.with_suffix(".md"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nreport: {out}")
    print(f"markdown: {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
