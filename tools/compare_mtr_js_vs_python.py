from __future__ import annotations

# pyright: basic, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnusedCallResult=false

import argparse
import json
import re
import sys
import time
import urllib.parse
import zlib
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Route, Request, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paypal.models import SessionState  # noqa: E402
from paypal.mtr import build_mtr_request_object, serialize_mtr_body  # noqa: E402


MTR_BASE = "https://www.paypal.com/mtr/1a7c3460cd8c343771081839499ed7a0"
MTR_X0_PATH_FRAGMENT = "/mtr/1a7c3460cd8c343771081839499ed7a0/AvQ9/"
DFP_SCRIPT_URL = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
DEFAULT_CAPTURE = PROJECT_ROOT / "captures" / "roxy-paypal-20260705-103548"
DEFAULT_PAGE_URL = "https://www.paypal.com/pay?token=BA-37R61061EU582084R"
DEFAULT_DFP_CONFIG = {
    "dfpChannel": "iwc-mxo",
    "clientMetaDataId": "BA-37R61061EU582084R",
    "isQA": False,
    "fppAPIKey": "QBzalmMuDFJIiZNebIWt",
}
ROXY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


def find_one(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No match under {root}: {pattern}")
    return matches[0]


def decode_mtr_body(body: bytes) -> dict[str, Any]:
    if len(body) < 14:
        raise ValueError(f"MTR body too short: {len(body)} bytes")
    seed = body[0]
    compressed = body[1] == (seed + 3) % 256 and body[2] == (seed + 14) % 256
    uncompressed = body[1] == (seed + 3) % 256 and body[2] == (seed + 13) % 256
    if not (compressed or uncompressed):
        raise ValueError("MTR body marker does not match compressed/uncompressed envelope")
    pad_len = (body[3] - seed) % 256
    if pad_len > 3:
        raise ValueError(f"Unexpected MTR padding length: {pad_len}")
    key_start = 4 + pad_len
    key = body[key_start:key_start + 9]
    if len(key) != 9:
        raise ValueError("MTR XOR key is missing or truncated")
    encoded = body[key_start + 9:]
    mixed = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(encoded))
    payload = zlib.decompress(mixed, wbits=-15) if compressed else mixed
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Decoded MTR payload is not a JSON object")
    return cast(dict[str, Any], decoded)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def short(value: Any, limit: int = 220) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def sorted_keys(keys: Iterable[str]) -> list[str]:
    def key_fn(key: str) -> tuple[int, int, str]:
        match = re.fullmatch(r"s(\d+)", key)
        if match:
            return (1, int(match.group(1)), key)
        return (0, 0, key)

    return sorted(keys, key=key_fn)


def signal_count(payload: dict[str, Any]) -> int:
    return sum(1 for key in payload if re.fullmatch(r"s\d+", key))


def extract_chrome_major(user_agent: str, fallback: str = "150") -> str:
    match = re.search(r"Chrome/(\d+)", user_agent or "")
    return match.group(1) if match else fallback


def int_value(value: object, default: int) -> int:
    try:
        if isinstance(value, (str, int, float)):
            return int(value)
    except Exception:
        return default
    return default


def float_value(value: object, default: float) -> float:
    try:
        if isinstance(value, (str, int, float)):
            return float(value)
    except Exception:
        return default
    return default


def normalize_runtime_profile(runtime: dict[str, Any]) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    profile = cast(dict[str, object], runtime.get("profile") or {})
    screen = cast(dict[str, object], runtime.get("screen") or {})
    viewport = cast(dict[str, object], runtime.get("viewport") or {})
    user_agent = str(profile.get("user_agent") or ROXY_UA)
    chrome_major = extract_chrome_major(user_agent)
    ua_data = cast(dict[str, Any], profile.get("user_agent_data") or {})
    high_entropy = cast(dict[str, Any], ua_data.get("highEntropy") or {})
    full_version = str(high_entropy.get("uaFullVersion") or high_entropy.get("fullVersion") or f"{chrome_major}.0.0.0")
    profile.update(
        {
            "fingerprint_source": "browser",
            "user_agent": user_agent,
            "chrome_major": chrome_major,
            "chrome_full_version": full_version,
            "language": profile.get("language") or "pt-BR",
            "languages": profile.get("languages") or ["pt-BR", "pt", "en-US", "en"],
            "timezone": profile.get("timezone") or "Pacific/Honolulu",
            "timezone_offset_minutes": int_value(profile.get("timezone_offset_minutes"), 600),
            "platform": profile.get("platform") or "Win32",
            "sec_ch_platform": f'"{high_entropy.get("platform") or ua_data.get("platform") or "Windows"}"',
            "sec_ch_platform_version": f'"{high_entropy.get("platformVersion") or ""}"',
            "sec_ch_arch": f'"{high_entropy.get("architecture") or "x86"}"',
            "sec_ch_bitness": str(high_entropy.get("bitness") or "64"),
            "device_memory": int_value(profile.get("device_memory"), 8),
            "hardware_concurrency": int_value(profile.get("hardware_concurrency"), 12),
            "device_pixel_ratio": float_value(profile.get("device_pixel_ratio"), 1.0),
            "mtr_outer_width": int_value(profile.get("outer_width"), int_value(viewport.get("width"), 567)),
            "mtr_outer_height": int_value(profile.get("outer_height"), int_value(viewport.get("height"), 700)),
            "mtr_inner_width": int_value(profile.get("inner_width"), int_value(viewport.get("width"), 567)),
            "mtr_inner_height": int_value(profile.get("inner_height"), int_value(viewport.get("height"), 700)),
        }
    )
    return profile, screen, viewport


def run_real_js_mtr(
    *,
    dfp_js: str,
    x0_token: str,
    mtr_response: str,
    dfp_config: dict[str, Any],
    page_url: str,
    wait_seconds: float,
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    html = "".join(
        [
            "<!doctype html><html lang='pt-BR' dir='ltr' data-ppui-mode='light'><head>",
            "<meta charset='utf-8'><title>MTR JS Harness</title>",
            f"<script>window.PAYPAL={{dfpData:{json.dumps(dfp_config, separators=(',', ':'))}}};</script>",
            f"<script id='dfpconfig' type='application/json'>{json.dumps(dfp_config, separators=(',', ':'))}</script>",
            "</head><body><main>mtr harness</main></body></html>",
        ]
    )
    requests: list[dict[str, Any]] = []
    console: list[dict[str, str]] = []
    captured: dict[str, bytes] = {}
    runtime_profile: dict[str, Any] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--enable-unsafe-swiftshader", "--disable-search-engine-choice-screen"],
        )
        context = browser.new_context(
            user_agent=ROXY_UA,
            viewport={"width": 567, "height": 700},
            screen={"width": 1536, "height": 864},
            locale="pt-BR",
            timezone_id="Pacific/Honolulu",
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            java_script_enabled=True,
        )
        page = context.new_page()
        page.on("console", lambda msg: console.append({"type": msg.type, "text": msg.text}))

        def route_handler(route: Route, request: Request) -> None:
            url = request.url
            method = request.method.upper()
            record: dict[str, Any] = {"method": method, "url": url, "resourceType": request.resource_type}
            requests.append(record)
            if url == page_url:
                route.fulfill(status=200, content_type="text/html", body=html)
                return
            if url.startswith(DFP_SCRIPT_URL):
                route.fulfill(status=200, content_type="application/javascript", body=dfp_js)
                return
            if MTR_X0_PATH_FRAGMENT in url:
                route.fulfill(status=200, content_type="text/plain; charset=utf-8", body=x0_token)
                return
            if url.startswith(f"{MTR_BASE}?") and method == "POST":
                body = request.post_data_buffer or b""
                captured["body"] = body
                record["bytes"] = len(body)
                route.fulfill(status=200, content_type="application/json; charset=utf-8", body=mtr_response)
                return
            if "observability.handleClientEmit" in url or "identity/di/log" in url:
                route.fulfill(status=200, content_type="application/json", body="{}")
                return
            route.fulfill(status=204, body="")

        context.route("**/*", route_handler)
        page.goto(page_url, wait_until="domcontentloaded", timeout=10_000)
        runtime_profile = page.evaluate(
            """async () => {
                const safe = (fn, fallback = null) => { try { return fn(); } catch (_e) { return fallback; } };
                const rectValue = (element) => {
                    const rect = element.getBoundingClientRect();
                    const value = {};
                    for (const key of ['x', 'y', 'left', 'right', 'bottom', 'height', 'top', 'width']) {
                        if (key in rect) value[key] = rect[key];
                    }
                    value.font = getComputedStyle(element, null).getPropertyValue('font-family');
                    return value;
                };
                const canvasSources = () => {
                    const canvas = document.createElement('canvas');
                    canvas.width = 1;
                    canvas.height = 1;
                    const ctx = canvas.getContext('2d');
                    if (!ctx || !canvas.toDataURL) return { winding: false, geometry_data_url: 'unsupported', text_data_url: 'unsupported' };
                    ctx.rect(0, 0, 10, 10);
                    ctx.rect(2, 2, 6, 6);
                    const winding = !ctx.isPointInPath(5, 5, 'evenodd');
                    canvas.width = 240;
                    canvas.height = 60;
                    ctx.textBaseline = 'alphabetic';
                    ctx.fillStyle = '#f60';
                    ctx.fillRect(100, 1, 62, 20);
                    ctx.fillStyle = '#069';
                    ctx.font = '11pt "Times New Roman"';
                    const text = `Cwm fjordbank gly ${String.fromCharCode(55357, 56835)}`;
                    ctx.fillText(text, 2, 15);
                    ctx.fillStyle = 'rgba(102, 204, 0, 0.2)';
                    ctx.font = '18pt Arial';
                    ctx.fillText(text, 4, 45);
                    const textDataUrl = canvas.toDataURL();
                    if (textDataUrl !== canvas.toDataURL()) return { winding, geometry_data_url: 'unstable', text_data_url: 'unstable' };
                    canvas.width = 122;
                    canvas.height = 110;
                    ctx.globalCompositeOperation = 'multiply';
                    for (const [fill, x, y] of [['#f2f', 40, 40], ['#2ff', 80, 40], ['#ff2', 60, 80]]) {
                        ctx.fillStyle = fill;
                        ctx.beginPath();
                        ctx.arc(x, y, 40, 0, 2 * Math.PI, true);
                        ctx.closePath();
                        ctx.fill();
                    }
                    ctx.fillStyle = '#f9c';
                    ctx.arc(60, 60, 60, 0, 2 * Math.PI, true);
                    ctx.arc(60, 60, 20, 0, 2 * Math.PI, true);
                    ctx.fill('evenodd');
                    return { winding, geometry_data_url: canvas.toDataURL(), text_data_url: textDataUrl };
                };
                const mathFingerprintString = () => {
                    const cn = Math;
                    const an = () => 0;
                    const acos = cn.acos || an, acosh = cn.acosh || an, asin = cn.asin || an, asinh = cn.asinh || an;
                    const atanh = cn.atanh || an, atan = cn.atan || an, sin = cn.sin || an, sinh = cn.sinh || an;
                    const cos = cn.cos || an, cosh = cn.cosh || an, tan = cn.tan || an, tanh = cn.tanh || an;
                    const exp = cn.exp || an, expm1 = cn.expm1 || an, log1p = cn.log1p || an;
                    const values = {
                        acos: acos(.12312423423423424),
                        acosh: acosh(1e308),
                        acoshPf: (h => cn.log(h + cn.sqrt(h * h - 1)))(1e154),
                        asin: asin(.12312423423423424),
                        asinh: asinh(1),
                        asinhPf: cn.log(1 + cn.sqrt(2)),
                        atanh: atanh(.5),
                        atanhPf: cn.log(3) / 2,
                        atan: atan(.5),
                        sin: sin(-1e300),
                        sinh: sinh(1),
                        sinhPf: cn.exp(1) - 1 / cn.exp(1) / 2,
                        cos: cos(10.000000000123),
                        cosh: cosh(1),
                        coshPf: (cn.exp(1) + 1 / cn.exp(1)) / 2,
                        tan: tan(-1e300),
                        tanh: tanh(1),
                        tanhPf: (cn.exp(2) - 1) / (cn.exp(2) + 1),
                        exp: exp(1),
                        expm1: expm1(1),
                        expm1Pf: cn.exp(1) - 1,
                        log1p: log1p(10),
                        log1pPf: cn.log(11),
                        powPI: cn.pow(cn.PI, -100)
                    };
                    return Object.keys(values).map(key => `${key}=${values[key]}`).join(',');
                };
                const webglRenderDataUrl = () => {
                    const canvas = document.createElement('canvas');
                    let gl = null;
                    for (const name of ['webgl', 'experimental-webgl']) {
                        try { gl = canvas.getContext(name); } catch (_e) {}
                        if (gl) break;
                    }
                    if (!gl) return null;
                    gl.clearColor(0, 0, 1, 1);
                    const program = gl.createProgram();
                    if (!program) return canvas.toDataURL();
                    const attach = (offset, source) => {
                        const shader = gl.createShader(35633 - offset);
                        if (shader) {
                            gl.shaderSource(shader, source);
                            gl.compileShader(shader);
                            gl.attachShader(program, shader);
                        }
                    };
                    attach(0, 'attribute vec2 p;uniform float t;void main(){float s=sin(t);float c=cos(t);gl_Position=vec4(p*mat2(c,s,-s,c),1,1);}');
                    attach(1, 'void main(){gl_FragColor=vec4(1,0,0,1);}');
                    gl.linkProgram(program);
                    gl.useProgram(program);
                    gl.enableVertexAttribArray(0);
                    const uniform = gl.getUniformLocation(program, 't');
                    const buffer = gl.createBuffer();
                    gl.bindBuffer(34962, buffer);
                    gl.bufferData(34962, new Float32Array([0, 1, -1, -1, 1, -1]), 35044);
                    gl.vertexAttribPointer(0, 2, 5126, false, 0, 0);
                    gl.clear(16384);
                    gl.uniform1f(uniform, 3.65);
                    gl.drawArrays(4, 0, 3);
                    return canvas.toDataURL();
                };
                const sharedIframeRects = async () => {
                    const iframe = document.createElement('iframe');
                    iframe.style.setProperty('display', 'block', 'important');
                    iframe.style.position = 'absolute';
                    iframe.style.top = '0';
                    iframe.style.left = '0';
                    iframe.style.visibility = 'hidden';
                    iframe.src = 'about:blank';
                    await new Promise((resolve, reject) => {
                        iframe.onload = () => resolve();
                        iframe.onerror = reject;
                        document.body.appendChild(iframe);
                        const check = () => iframe.contentWindow && iframe.contentWindow.document.readyState === 'complete' ? resolve() : setTimeout(check, 10);
                        check();
                    });
                    const frameWindow = iframe.contentWindow;
                    const frameDocument = frameWindow.document;
                    while (!frameDocument.body) await new Promise(resolve => setTimeout(resolve, 10));
                    const frameRect = (element) => {
                        const rect = element.getBoundingClientRect();
                        const value = {};
                        for (const key of ['x', 'y', 'left', 'right', 'bottom', 'height', 'top', 'width']) {
                            if (key in rect) value[key] = rect[key];
                        }
                        value.font = frameWindow.getComputedStyle(element, null).getPropertyValue('font-family');
                        return value;
                    };
                    let emojiText = '';
                    for (let code = 128512; code <= 128591; code++) emojiText += String.fromCodePoint(code);
                    const emoji = frameDocument.createElement('span');
                    emoji.style.whiteSpace = 'nowrap';
                    emoji.innerText = emojiText;
                    frameDocument.body.append(emoji);
                    const emojiValue = frameRect(emoji);
                    frameDocument.body.removeChild(emoji);
                    let mathHtml = '<mrow><munderover><mmultiscripts><mo>∏</mo>';
                    const parts = [['𝔈', 'υ', 'τ', 'ρ', 'σ'], ['𝔇', 'π', 'ο', 'ν', 'ξ'], ['𝔄', 'δ', 'γ', 'α', 'β'], ['𝔅', 'θ', 'η', 'ε', 'ζ'], ['𝔉', 'ω', 'ψ', 'ϕ', 'χ'], ['ℭ', 'μ', 'λ', 'ι', 'κ']];
                    const row = (a, b, c, d, e) => `<mmultiscripts><mi>${a}</mi><mi>${b}</mi><mi>${c}</mi><mprescripts></mprescripts><mi>${d}</mi><mi>${e}</mi></mmultiscripts>`;
                    for (const item of parts) mathHtml += row(...item);
                    mathHtml += '</munderover></mrow>';
                    const math = frameDocument.createElement('math');
                    math.style.whiteSpace = 'nowrap';
                    math.innerHTML = mathHtml;
                    frameDocument.body.append(math);
                    const mathValue = frameRect(math);
                    frameDocument.body.removeChild(math);
                    document.body.removeChild(iframe);
                    return { emoji_rect: emojiValue, mathml_rect: mathValue };
                };
                const measureFontWidths = () => {
                    const body = document.body;
                    const previousWidth = body.style.width;
                    const previousWebkitTextSizeAdjust = body.style.webkitTextSizeAdjust;
                    const previousTextSizeAdjust = body.style.textSizeAdjust;
                    body.style.width = '4000px';
                    body.style.webkitTextSizeAdjust = body.style.textSizeAdjust = 'none';
                    const container = document.createElement('div');
                    container.textContent = [...Array(200)].map(() => 'word').join(' ');
                    body.appendChild(container);
                    const configs = {
                        default: {},
                        apple: { font: '-apple-system-body' },
                        serif: { fontFamily: 'serif' },
                        sans: { fontFamily: 'sans-serif' },
                        mono: { fontFamily: 'monospace' },
                        min: { fontSize: '1px' },
                        system: { fontFamily: 'system-ui' }
                    };
                    const widths = {};
                    for (const key of Object.keys(configs)) {
                        const span = document.createElement('span');
                        span.textContent = 'mmMwWLliI0fiflO&1';
                        span.style.whiteSpace = 'nowrap';
                        for (const styleKey of Object.keys(configs[key])) {
                            span.style[styleKey] = configs[key][styleKey];
                        }
                        container.append(document.createElement('br'), span);
                        widths[key] = span.getBoundingClientRect().width;
                    }
                    body.removeChild(container);
                    body.style.width = previousWidth;
                    body.style.webkitTextSizeAdjust = previousWebkitTextSizeAdjust;
                    body.style.textSizeAdjust = previousTextSizeAdjust;
                    return widths;
                };
                const cssSystemColors = () => {
                    const aliases = {
                        AccentColor: 'ac', AccentColorText: 'act', ActiveText: 'at', ActiveBorder: 'ab',
                        ActiveCaption: 'aca', AppWorkspace: 'aw', Background: 'b', ButtonHighlight: 'bh',
                        ButtonShadow: 'bs', ButtonBorder: 'bb', ButtonFace: 'bf', ButtonText: 'bt', FieldText: 'ft',
                        GrayText: 'gt', Highlight: 'h', HighlightText: 'ht', InactiveBorder: 'ib',
                        InactiveCaption: 'ic', InactiveCaptionText: 'ict', InfoBackground: 'ib', InfoText: 'it',
                        LinkText: 'lt', Mark: 'm', Menu: 'me', Scrollbar: 's', ThreeDDarkShadow: 'tdds',
                        ThreeDFace: 'tdf', ThreeDHighlight: 'tdh', ThreeDLightShadow: 'tdls', ThreeDShadow: 'tds',
                        VisitedText: 'vt', Window: 'w', WindowFrame: 'wf', WindowText: 'wt',
                        Selecteditem: 'si', Selecteditemtext: 'sit'
                    };
                    const div = document.createElement('div');
                    document.body.appendChild(div);
                    const colors = {};
                    for (const colorName of Object.keys(aliases)) {
                        div.style.color = colorName;
                        colors[aliases[colorName]] = getComputedStyle(div).color;
                    }
                    document.body.removeChild(div);
                    return colors;
                };
                const browserMarkers = () => {
                    const names = ['chrome', 'safari', '__crWeb', '__gCrWeb', 'yandex', '__yb', '__ybro', '__firefox__', '__edgeTrackingPreventionStatistics', 'webkit', 'oprt', 'samsungAr', 'ucweb', 'UCShellJava', 'puffinDevice'];
                    return names.filter(name => {
                        const value = window[name];
                        return value && typeof value === 'object';
                    }).sort();
                };
                const performanceNowDeltas = () => {
                    if (!performance || !performance.now) return null;
                    let t = 1;
                    let e = 1;
                    let r = performance.now();
                    let o = r;
                    for (let i = 0; i < 50000; i++) {
                        if ((r = o) < (o = performance.now())) {
                            const n = o - r;
                            if (n > t) {
                                if (n < e) e = n;
                            } else if (n < t) {
                                e = t;
                                t = n;
                            }
                        }
                    }
                    return [t, e];
                };
                const mathmlRect = () => {
                    let html = '<mrow><munderover><mmultiscripts><mo>∏</mo>';
                    const parts = [['𝔈', 'υ', 'τ', 'ρ', 'σ'], ['𝔇', 'π', 'ο', 'ν', 'ξ'], ['𝔄', 'δ', 'γ', 'α', 'β'], ['𝔅', 'θ', 'η', 'ε', 'ζ'], ['𝔉', 'ω', 'ψ', 'ϕ', 'χ'], ['ℭ', 'μ', 'λ', 'ι', 'κ']];
                    const row = (a, b, c, d, e) => `<mmultiscripts><mi>${a}</mi><mi>${b}</mi><mi>${c}</mi><mprescripts></mprescripts><mi>${d}</mi><mi>${e}</mi></mmultiscripts>`;
                    for (const item of parts) html += row(...item);
                    html += '</munderover></mrow>';
                    const node = document.createElement('math');
                    node.style.whiteSpace = 'nowrap';
                    node.innerHTML = html;
                    document.body.append(node);
                    const value = rectValue(node);
                    document.body.removeChild(node);
                    return value;
                };
                const emojiRect = () => {
                    let text = '';
                    for (let code = 128512; code <= 128591; code++) text += String.fromCodePoint(code);
                    const span = document.createElement('span');
                    span.style.whiteSpace = 'nowrap';
                    span.innerText = text;
                    document.body.append(span);
                    const value = rectValue(span);
                    document.body.removeChild(span);
                    return value;
                };
                const notificationPermissionMismatch = async () => {
                    if (!window.Notification || !navigator.permissions || typeof navigator.permissions.query !== 'function') return false;
                    const permission = await navigator.permissions.query({ name: 'notifications' }).catch(() => null);
                    return window.Notification.permission === 'denied' && permission && permission.state === 'prompt';
                };
                const navigatorPrototypeMarkers = () => {
                    const names = Object.getOwnPropertyNames(Navigator.prototype);
                    const wanted = new Set(['onLine', 'webdriver', 'getGamepads']);
                    return { l: names.length, p: names.map((name, index) => ({ i: index, n: name })).filter(item => wanted.has(item.n)) };
                };
                const plugins = Array.from(navigator.plugins || []).map(plugin => ({
                    name: plugin.name,
                    description: plugin.description,
                    mimeTypes: Array.from(plugin).map(mime => ({ type: mime.type, suffixes: mime.suffixes }))
                }));
                const mimeTypeCount = navigator.mimeTypes && typeof navigator.mimeTypes.length !== 'undefined' ? navigator.mimeTypes.length : 0;
                const storageEstimate = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate().catch(() => ({})) : {};
                const uaData = navigator.userAgentData ? {
                    brands: navigator.userAgentData.brands || [],
                    mobile: navigator.userAgentData.mobile,
                    platform: navigator.userAgentData.platform,
                    highEntropy: await navigator.userAgentData.getHighEntropyValues([
                        'architecture', 'bitness', 'brands', 'fullVersionList', 'mobile',
                        'model', 'platform', 'platformVersion', 'uaFullVersion', 'wow64'
                    ]).catch(() => ({}))
                } : null;
                const iframeRects = await sharedIframeRects();
                const canvasSource = canvasSources();
                return {
                    profile: {
                        user_agent: navigator.userAgent,
                        app_version: navigator.appVersion,
                        platform: navigator.platform,
                        language: navigator.language,
                        languages: Array.from(navigator.languages || []),
                        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                        timezone_offset_minutes: new Date().getTimezoneOffset(),
                        hardware_concurrency: navigator.hardwareConcurrency || 0,
                        device_memory: navigator.deviceMemory || 0,
                        device_pixel_ratio: window.devicePixelRatio || 1,
                        max_touch_points: navigator.maxTouchPoints || 0,
                        outer_width: window.outerWidth,
                        outer_height: window.outerHeight,
                        inner_width: window.innerWidth,
                        inner_height: window.innerHeight,
                        user_agent_data: uaData
                    },
                    screen: {
                        width: screen.width,
                        height: screen.height,
                        availWidth: screen.availWidth,
                        availHeight: screen.availHeight,
                        colorDepth: screen.colorDepth,
                        pixelDepth: screen.pixelDepth
                    },
                    viewport: { width: window.innerWidth, height: window.innerHeight },
                    device_fingerprint: {
                        ab_noop: 'a',
                        plugins,
                        mime_type_count: mimeTypeCount,
                        canvas_geometry_data_url: canvasSource.geometry_data_url,
                        canvas_text_data_url: canvasSource.text_data_url,
                        canvas_winding: canvasSource.winding,
                        math_fingerprint_source: mathFingerprintString(),
                        webgl_render_data_url: webglRenderDataUrl(),
                        browser_markers: browserMarkers(),
                        storage_quota: Math.trunc(storageEstimate.quota || 0),
                        js_heap_size_limit: performance.memory ? performance.memory.jsHeapSizeLimit : 4395630592,
                        performance_time_origin: performance.timeOrigin || (Date.now() - performance.now()),
                        performance_now_deltas: performanceNowDeltas(),
                        font_widths: measureFontWidths(),
                        css_system_colors: cssSystemColors(),
                        pdf_viewer_enabled: Boolean(navigator.pdfViewerEnabled),
                        connection_rtt: navigator.connection && typeof navigator.connection.rtt !== 'undefined' ? navigator.connection.rtt : 0,
                        notification_permission_mismatch: await notificationPermissionMismatch(),
                        browser_components: { wv: false, wvp: false, pr: false, ck: false, pt: false, fp: false },
                        window_property_markers: 'Iterator' in window ? ['Iterator'] : [],
                        navigator_prototype_markers: navigatorPrototypeMarkers(),
                        mathml_rect: iframeRects.mathml_rect || mathmlRect(),
                        emoji_rect: iframeRects.emoji_rect || emojiRect()
                    }
                };
            }"""
        )
        page.evaluate(
            """() => {
                const calls = [];
                const push = entry => { if (calls.length < 3000) calls.push(entry); };
                const stack = () => { try { return (new Error()).stack || ''; } catch (_e) { return ''; } };
                Object.defineProperty(window, '__mtrSourceCalls', { value: calls, configurable: true });
                const originalDateNow = Date.now.bind(Date);
                Date.now = function() {
                    const value = originalDateNow();
                    push({ kind: 'date_now', value, stack: stack() });
                    return value;
                };
                const originalRandom = Math.random.bind(Math);
                Math.random = function() {
                    const value = originalRandom();
                    push({ kind: 'math_random', value, stack: stack() });
                    return value;
                };
                const cryptoObject = window.crypto;
                if (cryptoObject && typeof cryptoObject.getRandomValues === 'function') {
                    const originalGetRandomValues = cryptoObject.getRandomValues.bind(cryptoObject);
                    cryptoObject.getRandomValues = function(array) {
                        const result = originalGetRandomValues(array);
                        push({
                            kind: 'crypto_get_random_values',
                            ctor: array && array.constructor ? array.constructor.name : '',
                            length: array && typeof array.length === 'number' ? array.length : 0,
                            values: Array.from(array || []).slice(0, 64),
                            stack: stack()
                        });
                        return result;
                    };
                }
                const wrapStorage = (storage, storageName) => {
                    if (!storage || typeof storage.setItem !== 'function') return;
                    const originalSetItem = storage.setItem.bind(storage);
                    storage.setItem = function(key, value) {
                        push({ kind: 'storage_set_item', storage: storageName, key: String(key), value: String(value), stack: stack() });
                        return originalSetItem(key, value);
                    };
                };
                try { wrapStorage(window.sessionStorage, 'sessionStorage'); } catch (_e) {}
                try { wrapStorage(window.localStorage, 'localStorage'); } catch (_e) {}
                const originalObjectKeys = Object.keys.bind(Object);
                Object.keys = function(value) {
                    const result = originalObjectKeys(value);
                    if (value === window) {
                        const originalSlice = result.slice.bind(result);
                        result.slice = function(...args) {
                            const sliced = originalSlice(...args);
                            push({ kind: 'window_keys_slice', args, result: sliced, stack: stack() });
                            return sliced;
                        };
                    }
                    return result;
                };
                window.__mtrExtractSourceFacts = () => {
                    const sourceCalls = window.__mtrSourceCalls || [];
                    const stackIncludes = (entry, needle) => String(entry.stack || '').includes(needle);
                    const ccRandoms = sourceCalls
                        .filter(entry => entry.kind === 'math_random' && stackIncludes(entry, 'cc'))
                        .map(entry => entry.value)
                        .slice(0, 7);
                    const dateEntry = sourceCalls.find(entry => entry.kind === 'date_now' && (stackIncludes(entry, '_u') || stackIncludes(entry, ':1:976')));
                    const hex = '0123456789abcdef';
                    const uuidCrypto = sourceCalls
                        .filter(entry => entry.kind === 'crypto_get_random_values' && entry.ctor === 'Uint32Array' && entry.length === 2 && (stackIncludes(entry, 'ye') || stackIncludes(entry, ':1:594')))
                        .slice(0, 32);
                    let uuid = '';
                    if (uuidCrypto.length >= 32) {
                        const chars = uuidCrypto.map(entry => {
                            const values = entry.values || [];
                            const random = (1048576 * values[0] + (1048575 & values[1])) / 4503599627370496;
                            return hex.charAt(random * hex.length);
                        });
                        const take = count => chars.splice(0, count).join('');
                        uuid = [take(8), take(4), take(4), take(4), take(12)].join('-');
                    }
                    const storageUuid = sourceCalls
                        .filter(entry => entry.kind === 'storage_set_item')
                        .map(entry => `${entry.key} ${entry.value}`.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i))
                        .filter(Boolean)
                        .map(match => match[0])[0] || '';
                    const windowKeySlice = sourceCalls
                        .filter(entry => entry.kind === 'window_keys_slice' && stackIncludes(entry, 'Ea'))
                        .map(entry => entry.result)
                        .find(result => Array.isArray(result) && result.length > 0) || [];
                    return {
                        source_call_count: sourceCalls.length,
                        source_call_counts: sourceCalls.reduce((acc, entry) => { acc[entry.kind] = (acc[entry.kind] || 0) + 1; return acc; }, {}),
                        mtr_now_ms: dateEntry ? dateEntry.value : null,
                        random_probe_randoms: ccRandoms,
                        session_storage_uuid: uuid || storageUuid,
                        window_key_slice: windowKeySlice,
                        source_call_samples: sourceCalls
                            .filter(entry => entry.kind === 'window_keys_slice' || entry.kind === 'crypto_get_random_values' || entry.kind === 'date_now' || entry.kind === 'math_random')
                            .slice(-80)
                    };
                };
                document.addEventListener('dfp-completed-check', event => {
                    window.__mtrCompletedDetail = event.detail || true;
                }, { once: false });
                try {
                    sessionStorage.removeItem('4g3-fd7gc5k5');
                    sessionStorage.removeItem('4g3-fd7gc5k5-CMID');
                } catch (e) {}
            }"""
        )
        page.add_script_tag(url=DFP_SCRIPT_URL)
        deadline = time.time() + wait_seconds
        while time.time() < deadline and "body" not in captured:
            page.wait_for_timeout(250)
        runtime_profile["completed"] = page.evaluate(
            """() => ({
                detail: window.__mtrCompletedDetail || null,
                done: (() => { try { return sessionStorage.getItem('4g3-fd7gc5k5'); } catch(e) { return null; } })(),
                cmid: (() => { try { return sessionStorage.getItem('4g3-fd7gc5k5-CMID'); } catch(e) { return null; } })()
            })"""
        )
        source_facts = page.evaluate(
            """() => window.__mtrExtractSourceFacts ? window.__mtrExtractSourceFacts() : {}"""
        )
        runtime_dfp = runtime_profile.setdefault("device_fingerprint", {})
        if isinstance(runtime_dfp, dict) and isinstance(source_facts, dict):
            runtime_dfp.update(cast(dict[str, Any], source_facts))
        browser.close()

    body = captured.get("body", b"")
    if not body:
        raise RuntimeError(f"dfp.js did not POST MTR within {wait_seconds:.1f}s; saw {len(requests)} requests")
    return body, runtime_profile, requests, console


def build_python_mtr(
    *,
    runtime_profile: dict[str, Any],
    dfp_config: dict[str, Any],
    page_url: str,
    x0_token: str,
    signal_overrides: dict[str, Any] | None = None,
    payload_overrides: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any], dict[str, object], dict[str, object], dict[str, object]]:
    profile, screen, viewport = normalize_runtime_profile(runtime_profile)
    runtime_dfp = runtime_profile.get("device_fingerprint")
    dfp: dict[str, object] = dict(cast(dict[str, object], runtime_dfp)) if isinstance(runtime_dfp, dict) else {}
    if signal_overrides:
        dfp["mtr_signal_overrides"] = signal_overrides
    if payload_overrides:
        dfp["mtr_payload_overrides"] = payload_overrides
    state = SessionState(ba_token=str(dfp_config["clientMetaDataId"]))
    state.mtr_channel = str(dfp_config["dfpChannel"])
    state.mtr_client_metadata_id = str(dfp_config["clientMetaDataId"])
    state.mtr_api_key = str(dfp_config["fppAPIKey"])
    state.mtr_dfp_script_url = DFP_SCRIPT_URL
    state.browser_profile = profile
    state.screen = screen
    state.viewport = viewport
    state.device_fingerprint = dfp
    payload = build_mtr_request_object(state, page_url=page_url, x0_token=x0_token)
    return serialize_mtr_body(payload), payload, profile, screen, viewport


def compare_payloads(js_payload: dict[str, Any], py_payload: dict[str, Any]) -> dict[str, Any]:
    js_keys = set(js_payload)
    py_keys = set(py_payload)
    common = sorted_keys(js_keys & py_keys)
    changed = []
    equal = []
    signal_status_changed = []
    signal_value_changed = []
    for key in common:
        js_value = js_payload[key]
        py_value = py_payload[key]
        if js_value == py_value:
            equal.append(key)
            continue
        item = {
            "key": key,
            "js": js_value,
            "python": py_value,
            "js_preview": short(js_value),
            "python_preview": short(py_value),
        }
        if re.fullmatch(r"s\d+", key) and isinstance(js_value, dict) and isinstance(py_value, dict):
            item["status_equal"] = js_value.get("s") == py_value.get("s")
            item["value_equal"] = js_value.get("v") == py_value.get("v")
            if not item["status_equal"]:
                signal_status_changed.append(key)
            if not item["value_equal"]:
                signal_value_changed.append(key)
        changed.append(item)
    return {
        "counts": {
            "js_keys": len(js_keys),
            "python_keys": len(py_keys),
            "common_keys": len(common),
            "equal_keys": len(equal),
            "changed_keys": len(changed),
            "js_signal_keys": signal_count(js_payload),
            "python_signal_keys": signal_count(py_payload),
            "signal_status_changed": len(signal_status_changed),
            "signal_value_changed": len(signal_value_changed),
        },
        "only_js": sorted_keys(js_keys - py_keys),
        "only_python": sorted_keys(py_keys - js_keys),
        "equal": equal,
        "changed": changed,
        "signal_status_changed": signal_status_changed,
        "signal_value_changed": signal_value_changed,
    }


def runtime_overrides_from_diff(js_payload: dict[str, Any], diff: dict[str, Any]) -> dict[str, dict[str, Any]]:
    signal_overrides: dict[str, Any] = {}
    payload_overrides: dict[str, Any] = {}
    for item in cast(list[dict[str, Any]], diff["changed"]):
        key = str(item["key"])
        js_value = js_payload.get(key)
        if re.fullmatch(r"s\d+", key) and isinstance(js_value, dict) and "v" in js_value:
            signal_overrides[key] = js_value
        else:
            payload_overrides[key] = js_value
    return {"signal_overrides": signal_overrides, "payload_overrides": payload_overrides}


def markdown_report(
    *,
    out_dir: Path,
    js_body_len: int,
    py_body_len: int,
    diff: dict[str, Any],
    override_body_len: int,
    override_diff: dict[str, Any],
    override_keys: dict[str, Any],
    target_keys: list[str],
) -> str:
    counts = cast(dict[str, Any], diff["counts"])
    override_counts = cast(dict[str, Any], override_diff["counts"])
    changed = cast(list[dict[str, Any]], diff["changed"])
    override_changed = cast(list[dict[str, Any]], override_diff["changed"])
    by_key = {str(item["key"]): item for item in changed}
    rows = []
    for key in target_keys:
        item = by_key.get(key)
        if item:
            rows.append(f"| `{key}` | different | `{item['js_preview']}` | `{item['python_preview']}` |")
        else:
            rows.append(f"| `{key}` | equal | same | same |")
    top_changed = "\n".join(
        f"| `{item['key']}` | `{item['js_preview']}` | `{item['python_preview']}` |"
        for item in changed[:40]
    )
    top_override_changed = "\n".join(
        f"| `{item['key']}` | `{item['js_preview']}` | `{item['python_preview']}` |"
        for item in override_changed[:40]
    )
    signal_override_keys = sorted_keys(cast(dict[str, Any], override_keys["signal_overrides"]).keys())
    payload_override_keys = sorted_keys(cast(dict[str, Any], override_keys["payload_overrides"]).keys())
    return f"""# Real `dfp.js` runtime vs Python MTR diff

Generated at: {datetime.now(timezone.utc).isoformat()}

This run executes the captured PayPal `dfp.js` inside Chromium with a mocked PayPal page, intercepted `/mtr/.../x0` bootstrap, and intercepted MTR response. It then builds the Python `paypal.mtr` payload from the same runtime profile and compares decoded JSON payloads field-by-field.

## Artifacts

- Output dir: `{out_dir}`
- Real JS decoded payload: `js_payload.json`
- Python decoded payload: `python_payload.json`
- Python decoded payload with JS runtime overrides: `python_payload_with_js_overrides.json`
- Full diff JSON: `diff.json`
- Override replay diff JSON: `diff_with_js_overrides.json`
- Browser runtime profile: `runtime_profile.json`
- Raw bodies: `js_body.bin`, `python_body.bin`

## Summary

| Metric | Value |
|---|---:|
| Real JS body bytes | {js_body_len} |
| Python body bytes | {py_body_len} |
| Python body bytes with JS overrides | {override_body_len} |
| JS top-level keys | {counts['js_keys']} |
| Python top-level keys | {counts['python_keys']} |
| Common keys | {counts['common_keys']} |
| Equal keys | {counts['equal_keys']} |
| Changed keys | {counts['changed_keys']} |
| JS `s*` signals | {counts['js_signal_keys']} |
| Python `s*` signals | {counts['python_signal_keys']} |
| `s*` status differences | {counts['signal_status_changed']} |
| `s*` value differences | {counts['signal_value_changed']} |

Only in JS: `{', '.join(cast(list[str], diff['only_js'])) or 'none'}`

Only in Python: `{', '.join(cast(list[str], diff['only_python'])) or 'none'}`

## JS runtime override replay

This replay keeps the Python fallback structure but feeds the changed JS-captured signal/top-level values back through `mtr_signal_overrides` and `mtr_payload_overrides`. It measures whether the fallback can accept browser-runtime values when a real browser collector provides them.

| Metric | Value |
|---|---:|
| Override signal keys | {len(signal_override_keys)} |
| Override top-level keys | {len(payload_override_keys)} |
| Equal keys after overrides | {override_counts['equal_keys']} |
| Changed keys after overrides | {override_counts['changed_keys']} |
| `s*` value differences after overrides | {override_counts['signal_value_changed']} |

Signal overrides: `{', '.join(signal_override_keys) or 'none'}`

Top-level overrides: `{', '.join(payload_override_keys) or 'none'}`

### Remaining changed fields after JS overrides

| Field | JS | Python |
|---|---|---|
{top_override_changed or '| none | same | same |'}

## Previously targeted fields

| Field | Result | JS | Python |
|---|---|---|---|
{chr(10).join(rows)}

## First changed fields

| Field | JS | Python |
|---|---|---|
{top_changed or '| none | same | same |'}
"""


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run captured PayPal dfp.js in Chromium and compare its MTR payload with paypal.mtr Python output.")
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--wait-seconds", type=float, default=15.0)
    parser.add_argument("--page-url", default=DEFAULT_PAGE_URL)
    args = parser.parse_args()

    capture = args.capture.resolve()
    out_dir = args.out or PROJECT_ROOT / "captures" / f"mtr-js-vs-python-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    dfp_path = find_one(capture, "js/*dfp.js*.js")
    x0_path = find_one(capture, "network/bodies/*_mtr_*_x0_*.txt")
    mtr_response_path = find_one(capture, "network/bodies/*_mtr_*_chnl_*.json")
    roxy_request_path = find_one(capture, "network/requests/*_POST_www.paypal.com_mtr_*.txt")

    dfp_js = dfp_path.read_text(encoding="utf-8")
    x0_token = x0_path.read_text(encoding="utf-8").strip()
    mtr_response = mtr_response_path.read_text(encoding="utf-8")
    dfp_config = dict(DEFAULT_DFP_CONFIG)
    query = urllib.parse.urlsplit(args.page_url).query
    token = urllib.parse.parse_qs(query).get("token", [""])[0]
    if token:
        dfp_config["clientMetaDataId"] = token

    js_body, runtime_profile, requests, console = run_real_js_mtr(
        dfp_js=dfp_js,
        x0_token=x0_token,
        mtr_response=mtr_response,
        dfp_config=dfp_config,
        page_url=args.page_url,
        wait_seconds=args.wait_seconds,
    )
    js_payload = decode_mtr_body(js_body)
    py_body, py_payload_raw, py_profile, py_screen, py_viewport = build_python_mtr(
        runtime_profile=runtime_profile,
        dfp_config=dfp_config,
        page_url=args.page_url,
        x0_token=x0_token,
    )
    py_payload = decode_mtr_body(py_body)
    roxy_capture_payload = decode_mtr_body(roxy_request_path.read_bytes())
    diff = compare_payloads(js_payload, py_payload)
    override_keys = runtime_overrides_from_diff(js_payload, diff)
    override_py_body, override_py_payload_raw, override_py_profile, override_py_screen, override_py_viewport = build_python_mtr(
        runtime_profile=runtime_profile,
        dfp_config=dfp_config,
        page_url=args.page_url,
        x0_token=x0_token,
        signal_overrides=cast(dict[str, Any], override_keys["signal_overrides"]),
        payload_overrides=cast(dict[str, Any], override_keys["payload_overrides"]),
    )
    override_py_payload = decode_mtr_body(override_py_body)
    override_diff = compare_payloads(js_payload, override_py_payload)
    roxy_diff = compare_payloads(roxy_capture_payload, py_payload)

    (out_dir / "js_body.bin").write_bytes(js_body)
    (out_dir / "python_body.bin").write_bytes(py_body)
    (out_dir / "python_body_with_js_overrides.bin").write_bytes(override_py_body)
    write_json(out_dir / "js_payload.json", js_payload)
    write_json(out_dir / "python_payload.json", py_payload)
    write_json(out_dir / "python_payload_pre_envelope.json", py_payload_raw)
    write_json(out_dir / "python_payload_with_js_overrides.json", override_py_payload)
    write_json(out_dir / "python_payload_with_js_overrides_pre_envelope.json", override_py_payload_raw)
    write_json(out_dir / "roxy_capture_payload.json", roxy_capture_payload)
    write_json(out_dir / "runtime_profile.json", runtime_profile)
    write_json(out_dir / "python_profile_used.json", {"profile": py_profile, "screen": py_screen, "viewport": py_viewport})
    write_json(out_dir / "python_profile_with_js_overrides_used.json", {"profile": override_py_profile, "screen": override_py_screen, "viewport": override_py_viewport})
    write_json(out_dir / "requests.json", requests)
    write_json(out_dir / "console.json", console)
    write_json(out_dir / "diff.json", diff)
    write_json(out_dir / "js_runtime_overrides.json", override_keys)
    write_json(out_dir / "diff_with_js_overrides.json", override_diff)
    write_json(out_dir / "roxy_capture_vs_python_diff.json", roxy_diff)
    write_json(
        out_dir / "inputs.json",
        {
            "capture": str(capture),
            "dfp_js": str(dfp_path),
            "x0": str(x0_path),
            "mtr_response": str(mtr_response_path),
            "roxy_request": str(roxy_request_path),
            "page_url": args.page_url,
            "dfp_config": dfp_config,
        },
    )
    report = markdown_report(
        out_dir=out_dir,
        js_body_len=len(js_body),
        py_body_len=len(py_body),
        diff=diff,
        override_body_len=len(override_py_body),
        override_diff=override_diff,
        override_keys=override_keys,
        target_keys=["ab", "s4", "s5", "s49", "s58", "s84", "s94", "s131", "s145", "s150"],
    )
    (out_dir / "summary.md").write_text(report, encoding="utf-8")
    (PROJECT_ROOT / "mtr_js_runtime_vs_python_diff.md").write_text(report, encoding="utf-8")

    print(json.dumps({"out_dir": str(out_dir), "js_body_bytes": len(js_body), "python_body_bytes": len(py_body), "python_body_with_js_overrides_bytes": len(override_py_body), "counts": diff["counts"], "counts_with_js_overrides": override_diff["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
