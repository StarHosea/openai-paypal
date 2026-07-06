"""Capture a runtime browser fingerprint from a random RoxyBrowser profile."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from config import (
    BROWSER_PROFILE,
    ROXY_API_HOST,
    ROXY_API_KEY,
    ROXY_API_PORT,
    ROXY_HEADLESS,
    ROXY_PROJECT_ID,
    ROXY_WORKSPACE_ID,
    SCREEN,
    USER_AGENT,
    VIEWPORT,
)


class RoxyFingerprintError(RuntimeError):
    """Raised when RoxyBrowser cannot provide a runtime fingerprint."""


def _load_dotenv_value(name: str) -> str:
    """Read one value from local .env without adding a runtime dependency."""
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


def _env_str(name: str, default: str = "") -> str:
    value = _load_dotenv_value(name)
    return value if value != "" else default


def _env_int(name: str, default: int | None = None) -> int | None:
    value = _load_dotenv_value(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = _load_dotenv_value(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _load_dotenv_value(name).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "y"}


def _roxy_timezone_value(timezone_name: str, offset_minutes: int) -> str:
    # Roxy API uses strings such as "GMT-03:00 America/Sao_Paulo".  JavaScript
    # Date#getTimezoneOffset uses the opposite sign: UTC-3 => +180 minutes.
    signed_minutes = -int(offset_minutes)
    sign = "+" if signed_minutes >= 0 else "-"
    signed_minutes = abs(signed_minutes)
    hours, minutes = divmod(signed_minutes, 60)
    return f"GMT{sign}{hours:02d}:{minutes:02d} {timezone_name}"


def _roxy_proxy_info(proxy_url: str) -> dict[str, Any]:
    value = (proxy_url or "").strip()
    if not value:
        return {
            "moduleId": 0,
            "proxyMethod": "custom",
            "proxyCategory": "noproxy",
            "ipType": "IPV4",
        }
    parsed = urllib.parse.urlsplit(value)
    scheme = (parsed.scheme or "http").lower()
    category = {
        "http": "HTTP",
        "https": "HTTPS",
        "socks5": "SOCKS5",
        "socks5h": "SOCKS5",
    }.get(scheme, "HTTP")
    if not parsed.hostname or not parsed.port:
        return {
            "moduleId": 0,
            "proxyMethod": "custom",
            "proxyCategory": "noproxy",
            "ipType": "IPV4",
        }
    return {
        "moduleId": 0,
        "proxyMethod": "custom",
        "proxyCategory": category,
        "ipType": "IPV4",
        "host": parsed.hostname,
        "port": str(parsed.port),
        "proxyUserName": urllib.parse.unquote(parsed.username or ""),
        "proxyPassword": urllib.parse.unquote(parsed.password or ""),
        "checkChannel": "IPRust.io",
    }


def configured_roxy_api_key() -> str:
    return (
        _env_str("PAYPAL_ROXY_API_KEY")
        or _env_str("ROXY_API_KEY")
        or _env_str("ROXY_API_TOKEN")
        or ROXY_API_KEY
    ).strip()


@dataclass(slots=True)
class RoxyCaptureConfig:
    api_base: str
    api_key: str
    workspace_id: int | None = None
    project_id: int | None = None
    headless: bool = True
    timeout_seconds: float = 12.0
    close_after_capture: bool = True
    delete_after_capture: bool = True
    open_width: int = 1365
    open_height: int = 768
    screen_width: int = 1536
    screen_height: int = 864
    language: str = "pt-BR"
    display_language: str = "pt-BR"
    timezone: str = "America/Sao_Paulo"
    follow_ip: bool = False
    core_version: str = ""
    os_name: str = "Windows"
    os_version: str = "11"
    proxy_url: str = ""


def load_roxy_capture_config(proxy_url: str | None = None) -> RoxyCaptureConfig:
    port = _env_int("PAYPAL_ROXY_API_PORT", ROXY_API_PORT) or ROXY_API_PORT
    host = _env_str("PAYPAL_ROXY_API_HOST", ROXY_API_HOST)
    api_base = (
        _env_str("PAYPAL_ROXY_API_BASE")
        or _env_str("ROXY_API_BASE")
        or f"http://{host}:{port}"
    ).rstrip("/")
    language = str(BROWSER_PROFILE.get("language") or "pt-BR")
    timezone = _roxy_timezone_value(
        str(BROWSER_PROFILE.get("timezone") or "America/Sao_Paulo"),
        int(BROWSER_PROFILE.get("timezone_offset_minutes") or 180),
    )
    return RoxyCaptureConfig(
        api_base=api_base,
        api_key=configured_roxy_api_key(),
        workspace_id=_env_int("PAYPAL_ROXY_WORKSPACE_ID", ROXY_WORKSPACE_ID),
        project_id=_env_int("PAYPAL_ROXY_PROJECT_ID", ROXY_PROJECT_ID),
        headless=_env_bool("PAYPAL_ROXY_HEADLESS", ROXY_HEADLESS),
        timeout_seconds=max(2.0, _env_float("PAYPAL_ROXY_API_TIMEOUT_SECONDS", 12.0)),
        close_after_capture=_env_bool("PAYPAL_ROXY_CLOSE_AFTER_CAPTURE", True),
        delete_after_capture=_env_bool("PAYPAL_ROXY_DELETE_AFTER_CAPTURE", True),
        open_width=_env_int("PAYPAL_ROXY_OPEN_WIDTH", int(VIEWPORT.get("width", 1365) or 1365)) or 1365,
        open_height=_env_int("PAYPAL_ROXY_OPEN_HEIGHT", int(VIEWPORT.get("height", 768) or 768)) or 768,
        screen_width=_env_int("PAYPAL_ROXY_SCREEN_WIDTH", int(SCREEN.get("width", 1536) or 1536)) or 1536,
        screen_height=_env_int("PAYPAL_ROXY_SCREEN_HEIGHT", int(SCREEN.get("height", 864) or 864)) or 864,
        language=_env_str("PAYPAL_ROXY_LANGUAGE", language),
        display_language=_env_str("PAYPAL_ROXY_DISPLAY_LANGUAGE", language),
        timezone=_env_str("PAYPAL_ROXY_TIMEZONE", timezone),
        follow_ip=_env_bool("PAYPAL_ROXY_FOLLOW_IP", False),
        core_version=_env_str("PAYPAL_ROXY_CORE_VERSION", ""),
        os_name=_env_str("PAYPAL_ROXY_OS", "Windows"),
        os_version=_env_str("PAYPAL_ROXY_OS_VERSION", "11"),
        proxy_url=(proxy_url or _env_str("PAYPAL_ROXY_PROXY_URL") or "").strip(),
    )


class RoxyApiClient:
    def __init__(self, config: RoxyCaptureConfig):
        if not config.api_key:
            raise RoxyFingerprintError("PAYPAL_ROXY_API_KEY 未配置")
        self.config = config
        self.client = httpx.Client(
            base_url=config.api_base,
            timeout=config.timeout_seconds,
            headers={"token": config.api_key},
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception as exc:
            raise RoxyFingerprintError(f"Roxy API {path} 返回非 JSON 响应") from exc
        code = payload.get("code")
        if code not in (0, "0", None):
            msg = payload.get("msg") or payload.get("message") or payload
            raise RoxyFingerprintError(f"Roxy API {path} failed: {msg}")
        return payload

    def get_workspace_project(self) -> tuple[int, int | None]:
        if self.config.workspace_id is not None:
            return self.config.workspace_id, self.config.project_id
        payload = self.request("GET", "/browser/workspace", params={"page_index": 1, "page_size": 50})
        data = payload.get("data") or {}
        rows = data.get("rows") or []
        if not rows:
            raise RoxyFingerprintError("Roxy API 没有返回 workspace")
        row = rows[0]
        workspace_id = int(row.get("id"))
        project_id = self.config.project_id
        projects = row.get("project_details") or []
        if project_id is None and projects:
            project_id = int(projects[0].get("projectId"))
        return workspace_id, project_id

    def create_profile(self, workspace_id: int, project_id: int | None) -> str:
        payload: dict[str, Any] = {
            "workspaceId": workspace_id,
            "windowName": f"paypal-fp-{uuid.uuid4().hex[:10]}",
            "coreType": "Chrome",
            "os": self.config.os_name,
            "osVersion": self.config.os_version,
            "cookie": [],
            "searchEngine": "Google",
            "defaultOpenUrl": ["about:blank"],
            "windowRemark": "paypal runtime fingerprint capture",
            "proxyInfo": _roxy_proxy_info(self.config.proxy_url),
            "fingerInfo": {
                "isLanguageBaseIp": self.config.follow_ip,
                "language": self.config.language,
                "isDisplayLanguageBaseIp": self.config.follow_ip,
                "displayLanguage": self.config.display_language,
                "isTimeZone": self.config.follow_ip,
                "timeZone": self.config.timezone,
                "position": 0,
                "isPositionBaseIp": self.config.follow_ip,
                "forbidAudio": False,
                "forbidImage": False,
                "forbidMedia": False,
                "openWidth": str(self.config.open_width),
                "openHeight": str(self.config.open_height),
                "openBookmarks": False,
                "positionSwitch": False,
                "isDisplayName": False,
                "syncBookmark": False,
                "syncHistory": False,
                "syncTab": False,
                "syncCookie": False,
                "syncExtensions": False,
                "syncPassword": False,
                "syncIndexedDb": False,
                "syncLocalStorage": False,
                "clearCacheFile": True,
                "clearCookie": True,
                "clearLocalStorage": True,
                "randomFingerprint": True,
                "forbidSavePassword": True,
                "stopOpenNet": False,
                "stopOpenIP": False,
                "stopOpenPosition": False,
                "openWorkbench": 0,
                "resolutionType": True,
                "resolutionX": str(self.config.screen_width),
                "resolutionY": str(self.config.screen_height),
                "fontType": True,
                "webRTC": 2,
                "webGL": True,
                "webGLInfo": True,
                "webGLManufacturer": "",
                "webGLRender": "",
                "webGpu": "webgl",
                "canvas": True,
                "audioContext": True,
                "speechVoices": True,
                "doNotTrack": False,
                "clientRects": True,
                "deviceInfo": True,
                "deviceNameSwitch": True,
                "macInfo": True,
                "hardwareConcurrent": "",
                "deviceMemory": "",
                "disableSsl": False,
                "disableSslList": [],
                "portScanProtect": True,
                "portScanList": "",
                "useGpu": True,
                "sandboxPermission": False,
                "startupParam": "",
            },
        }
        if self.config.core_version:
            payload["coreVersion"] = self.config.core_version
        if project_id is not None:
            payload["projectId"] = project_id
        response = self.request("POST", "/browser/create", json=payload)
        dir_id = ((response.get("data") or {}).get("dirId") or "").strip()
        if not dir_id:
            raise RoxyFingerprintError("Roxy /browser/create 未返回 dirId")
        return dir_id

    def randomize_profile(self, workspace_id: int, dir_id: str) -> None:
        self.request("POST", "/browser/random_env", json={"workspaceId": workspace_id, "dirId": dir_id})

    def open_profile(self, workspace_id: int, dir_id: str) -> dict[str, Any]:
        # Roxy 的 Local API 用 `headless` 字段控制无头模式。不要把
        # `--headless` 写进 args：Roxy 文档说明部分内置/启动参数会被软件接管，
        # 用参数开关反而可能仍拉起可见窗口。
        args = ["--remote-allow-origins=*", "--disable-audio-output"]
        payload = {
            "workspaceId": workspace_id,
            "dirId": dir_id,
            "args": args,
            "forceOpen": False,
            "headless": bool(self.config.headless),
        }
        response = self.request("POST", "/browser/open", json=payload)
        data = response.get("data") or {}
        if not data.get("ws") and not data.get("http"):
            raise RoxyFingerprintError("Roxy /browser/open 未返回 CDP ws/http")
        return data

    def close_profile(self, dir_id: str) -> None:
        self.request("POST", "/browser/close", json={"dirId": dir_id})

    def delete_profile(self, workspace_id: int, dir_id: str) -> None:
        self.request(
            "POST",
            "/browser/delete",
            json={"workspaceId": workspace_id, "dirIds": [dir_id], "isSoftDelete": False},
        )


def _sha256_hex(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if value is None:
        value = ""
    if not isinstance(value, (bytes, bytearray)):
        value = str(value).encode("utf-8", "ignore")
    return hashlib.sha256(value).hexdigest()


def _sha256_b64(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if value is None:
        value = ""
    if not isinstance(value, (bytes, bytearray)):
        value = str(value).encode("utf-8", "ignore")
    return base64.b64encode(hashlib.sha256(value).digest()).decode("ascii")


def _parse_chrome_major(user_agent: str, fallback: int = 150) -> int:
    match = re.search(r"(?:Chrome|Chromium|Edg)/(\d+)", user_agent or "")
    if not match:
        return fallback
    try:
        return int(match.group(1))
    except ValueError:
        return fallback


def _full_version_from_ua_data(ua_data: dict[str, Any] | None, user_agent: str, major: int) -> str:
    if isinstance(ua_data, dict):
        for item in ua_data.get("fullVersionList") or []:
            brand = str(item.get("brand") or "")
            version = str(item.get("version") or "")
            if version and ("Chrome" in brand or "Chromium" in brand):
                return version
        version = str(ua_data.get("uaFullVersion") or "")
        if version:
            return version
    match = re.search(r"(?:Chrome|Chromium)/([0-9.]+)", user_agent or "")
    if match:
        return match.group(1)
    return f"{major}.0.0.0"


def _sec_ch_platform(ua_data: dict[str, Any] | None, platform: str) -> str:
    value = ""
    if isinstance(ua_data, dict):
        value = str(ua_data.get("platform") or "")
    if not value:
        lower = (platform or "").lower()
        if "win" in lower:
            value = "Windows"
        elif "mac" in lower:
            value = "macOS"
        elif "linux" in lower:
            value = "Linux"
        elif "android" in lower:
            value = "Android"
        else:
            value = str(BROWSER_PROFILE.get("sec_ch_platform", '"Windows"')).strip('"')
    return json.dumps(value)


def _sec_ch_arch(ua_data: dict[str, Any] | None) -> str:
    if isinstance(ua_data, dict):
        arch = str(ua_data.get("architecture") or "").lower()
        bitness = str(ua_data.get("bitness") or "")
        if arch in {"x86", "x86_64", "amd64"}:
            return '"x86"'
        if arch in {"arm", "arm64", "aarch64"}:
            return '"arm"'
        if bitness == "64":
            return '"x86"'
    return str(BROWSER_PROFILE.get("sec_ch_arch") or '"x86"')


def _locale_from_language(language: str) -> str:
    value = (language or str(BROWSER_PROFILE.get("language") or "pt-BR")).strip()
    return value.replace("-", "_")


def _country_from_locale(locale: str) -> str:
    if "_" in locale:
        return locale.rsplit("_", 1)[-1].upper()
    return str(BROWSER_PROFILE.get("country") or "BR")


def _connect_over_cdp(cdp_info: dict[str, Any]) -> str:
    ws = str(cdp_info.get("ws") or "")
    if ws:
        return ws
    http = str(cdp_info.get("http") or "")
    if not http:
        raise RoxyFingerprintError("Roxy CDP 信息为空")
    if not http.startswith(("http://", "https://")):
        http = f"http://{http}"
    return http


def _evaluate_cdp_fingerprint(
    cdp_info: dict[str, Any],
    timeout_ms: int,
    *,
    close_browser: bool = True,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise RoxyFingerprintError("playwright 未安装，无法连接 Roxy CDP") from exc

    endpoint = _connect_over_cdp(cdp_info)
    script = r"""
async () => {
  const perfNow = () => (performance && performance.now ? performance.now() : Date.now());
  const safe = (fn, fallback = null) => { try { return fn(); } catch (e) { return fallback; } };
  const hash32 = (text) => {
    // FNV-1a fallback hash, only used inside the page for compact preview values.
    let h = 0x811c9dc5;
    const s = String(text || "");
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h.toString(16).padStart(8, "0");
  };
  const canvasStart = perfNow();
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 180;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.textBaseline = "top";
    ctx.font = "16px Arial";
    ctx.fillStyle = "#f60";
    ctx.fillRect(15, 10, 120, 36);
    ctx.fillStyle = "#069";
    ctx.fillText("Roxy fingerprint ✓ 𝌆", 8, 8);
    ctx.strokeStyle = "rgba(102,204,0,0.7)";
    ctx.arc(82, 72, 50, 0, Math.PI * 2, true);
    ctx.stroke();
    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = "rgb(255,0,255)";
    ctx.fillRect(70, 42, 75, 75);
    ctx.fillStyle = "rgb(0,255,255)";
    ctx.fillRect(115, 42, 75, 75);
  }
  const canvasDataUrl = safe(() => canvas.toDataURL("image/png"), "");
  const ttCanvas = perfNow() - canvasStart;

  const webglStart = perfNow();
  const glCanvas = document.createElement("canvas");
  const gl = safe(() => glCanvas.getContext("webgl") || glCanvas.getContext("experimental-webgl"), null);
  let webgl = {};
  if (gl) {
    const dbg = safe(() => gl.getExtension("WEBGL_debug_renderer_info"), null);
    const params = {};
    const names = [
      "ALIASED_LINE_WIDTH_RANGE", "ALIASED_POINT_SIZE_RANGE", "ALPHA_BITS",
      "BLUE_BITS", "DEPTH_BITS", "GREEN_BITS", "MAX_COMBINED_TEXTURE_IMAGE_UNITS",
      "MAX_CUBE_MAP_TEXTURE_SIZE", "MAX_FRAGMENT_UNIFORM_VECTORS", "MAX_RENDERBUFFER_SIZE",
      "MAX_TEXTURE_IMAGE_UNITS", "MAX_TEXTURE_SIZE", "MAX_VARYING_VECTORS",
      "MAX_VERTEX_ATTRIBS", "MAX_VERTEX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_UNIFORM_VECTORS",
      "RED_BITS", "STENCIL_BITS"
    ];
    for (const name of names) {
      params[name] = safe(() => Array.from(gl.getParameter(gl[name]) || []), null);
      if (params[name] === null) params[name] = safe(() => gl.getParameter(gl[name]), null);
    }
    webgl = {
      version: safe(() => gl.getParameter(gl.VERSION), ""),
      vendor: safe(() => gl.getParameter(gl.VENDOR), ""),
      renderer: safe(() => gl.getParameter(gl.RENDERER), ""),
      shadingLanguageVersion: safe(() => gl.getParameter(gl.SHADING_LANGUAGE_VERSION), ""),
      unmaskedVendor: dbg ? safe(() => gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL), "") : "",
      unmaskedRenderer: dbg ? safe(() => gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL), "") : "",
      extensions: safe(() => gl.getSupportedExtensions(), []) || [],
      params
    };
  }
  const ttWebgl = perfNow() - webglStart;

  const audioStart = perfNow();
  let audioValue = "";
  let audioError = "";
  try {
    const AC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (AC) {
      const audioCtx = new AC(1, 5000, 44100);
      const osc = audioCtx.createOscillator();
      const compressor = audioCtx.createDynamicsCompressor();
      osc.type = "triangle";
      osc.frequency.value = 10000;
      compressor.threshold.value = -50;
      compressor.knee.value = 40;
      compressor.ratio.value = 12;
      compressor.attack.value = 0;
      compressor.release.value = 0.25;
      osc.connect(compressor);
      compressor.connect(audioCtx.destination);
      osc.start(0);
      const buffer = await audioCtx.startRendering();
      const data = buffer.getChannelData(0);
      let sum = 0;
      for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i] || 0);
      audioValue = String(sum);
    }
  } catch (e) {
    audioError = String(e && e.message || e);
  }
  const ttAudio = perfNow() - audioStart;

  const nav = safe(() => performance.getEntriesByType("navigation")[0]?.toJSON?.(), null);
  const uaData = navigator.userAgentData
    ? await navigator.userAgentData.getHighEntropyValues([
        "architecture", "bitness", "brands", "fullVersionList", "mobile",
        "model", "platform", "platformVersion", "uaFullVersion", "wow64"
      ]).catch(() => null)
    : null;
  const memory = performance.memory ? {
    usedJSHeapSize: performance.memory.usedJSHeapSize,
    totalJSHeapSize: performance.memory.totalJSHeapSize,
    jsHeapSizeLimit: performance.memory.jsHeapSizeLimit
  } : null;
  return {
    capturedAt: Date.now(),
    userAgent: navigator.userAgent,
    appVersion: navigator.appVersion,
    platform: navigator.platform,
    vendor: navigator.vendor,
    productSub: navigator.productSub,
    language: navigator.language,
    languages: Array.from(navigator.languages || []),
    cookieEnabled: navigator.cookieEnabled,
    onLine: navigator.onLine,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory,
    doNotTrack: navigator.doNotTrack,
    webdriver: navigator.webdriver,
    uaData,
    screen: {
      width: screen.width,
      height: screen.height,
      availWidth: screen.availWidth,
      availHeight: screen.availHeight,
      colorDepth: screen.colorDepth,
      pixelDepth: screen.pixelDepth
    },
    window: {
      innerWidth, innerHeight, outerWidth, outerHeight,
      devicePixelRatio: window.devicePixelRatio
    },
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    timezoneOffsetMinutes: new Date().getTimezoneOffset(),
    connection: navigator.connection ? {
      effectiveType: navigator.connection.effectiveType,
      rtt: navigator.connection.rtt,
      downlink: navigator.connection.downlink,
      saveData: navigator.connection.saveData
    } : null,
    plugins: Array.from(navigator.plugins || []).map(p => ({
      name: p.name, filename: p.filename, description: p.description,
      mimeTypes: Array.from(p).map(m => ({ type: m.type, suffixes: m.suffixes, description: m.description }))
    })),
    canvas: {
      dataUrl: canvasDataUrl,
      dataUrlLength: canvasDataUrl.length,
      previewHash: hash32(canvasDataUrl),
      ttCanvas
    },
    webgl,
    audio: { value: audioValue, error: audioError, ttAudio },
    memory,
    navigation: nav,
    timing: {
      ttCanvas,
      ttWebglBasic: ttWebgl,
      ttWebglExt: ttWebgl,
      ttAudio
    }
  };
}
"""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, timeout=timeout_ms)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("about:blank", wait_until="domcontentloaded", timeout=timeout_ms)
        result = page.evaluate(script)
        if close_browser:
            browser.close()
        return result


def _runtime_to_profile(js: dict[str, Any], cdp_info: dict[str, Any]) -> dict[str, Any]:
    user_agent = str(js.get("userAgent") or USER_AGENT)
    platform = str(js.get("platform") or BROWSER_PROFILE.get("platform") or "Win32")
    ua_data = js.get("uaData") if isinstance(js.get("uaData"), dict) else None
    major = _parse_chrome_major(user_agent, int(BROWSER_PROFILE.get("chrome_major") or 150))
    full_version = _full_version_from_ua_data(ua_data, user_agent, major)
    language = str(js.get("language") or BROWSER_PROFILE.get("language") or "pt-BR")
    locale = _locale_from_language(language)
    timezone_offset_minutes = int(js.get("timezoneOffsetMinutes") or BROWSER_PROFILE.get("timezone_offset_minutes") or 0)
    connection = js.get("connection") if isinstance(js.get("connection"), dict) else {}
    webgl = js.get("webgl") if isinstance(js.get("webgl"), dict) else {}
    profile = dict(BROWSER_PROFILE)
    profile.update(
        {
            "fingerprint_source": "roxy",
            "roxy_cdp_http": cdp_info.get("http", ""),
            "roxy_core_version": cdp_info.get("coreVersion", ""),
            "country": _env_str("PAYPAL_ROXY_COUNTRY", _country_from_locale(locale)),
            "language": language,
            "locale": locale,
            "timezone": str(js.get("timezone") or BROWSER_PROFILE.get("timezone") or "America/Sao_Paulo"),
            "timezone_offset_minutes": timezone_offset_minutes,
            "timezone_offset_ms": timezone_offset_minutes * 60 * 1000,
            "dst": bool(BROWSER_PROFILE.get("dst", False)),
            "chrome_major": major,
            "chrome_full_version": full_version,
            "platform": platform,
            "sec_ch_platform": _sec_ch_platform(ua_data, platform),
            "sec_ch_arch": _sec_ch_arch(ua_data),
            "device_memory": int(float(js.get("deviceMemory") or BROWSER_PROFILE.get("device_memory") or 8)),
            "hardware_concurrency": int(js.get("hardwareConcurrency") or BROWSER_PROFILE.get("hardware_concurrency") or 8),
            "device_pixel_ratio": float((js.get("window") or {}).get("devicePixelRatio") or 1),
            "connection_effective_type": str(connection.get("effectiveType") or BROWSER_PROFILE.get("connection_effective_type") or "4g"),
            "connection_rtt": str(connection.get("rtt") or BROWSER_PROFILE.get("connection_rtt") or "150"),
            "connection_downlink": str(connection.get("downlink") or BROWSER_PROFILE.get("connection_downlink") or "10"),
            "gpu_vendor": str(webgl.get("unmaskedVendor") or webgl.get("vendor") or BROWSER_PROFILE.get("gpu_vendor") or ""),
            "gpu_renderer": str(webgl.get("unmaskedRenderer") or webgl.get("renderer") or BROWSER_PROFILE.get("gpu_renderer") or ""),
            "webgl_vendor": str(webgl.get("vendor") or BROWSER_PROFILE.get("webgl_vendor") or "WebKit"),
            "webgl_renderer": str(webgl.get("renderer") or BROWSER_PROFILE.get("webgl_renderer") or "WebKit WebGL"),
            "user_agent": user_agent,
        }
    )
    return profile


def _runtime_screen(js: dict[str, Any]) -> dict[str, int]:
    source = js.get("screen") if isinstance(js.get("screen"), dict) else {}
    return {
        "colorDepth": int(source.get("colorDepth") or SCREEN.get("colorDepth") or 24),
        "pixelDepth": int(source.get("pixelDepth") or SCREEN.get("pixelDepth") or 24),
        "height": int(source.get("height") or SCREEN.get("height") or 864),
        "width": int(source.get("width") or SCREEN.get("width") or 1536),
        "availHeight": int(source.get("availHeight") or source.get("height") or SCREEN.get("availHeight") or 864),
        "availWidth": int(source.get("availWidth") or source.get("width") or SCREEN.get("availWidth") or 1536),
    }


def _runtime_viewport(js: dict[str, Any]) -> dict[str, int]:
    source = js.get("window") if isinstance(js.get("window"), dict) else {}
    return {
        "width": int(source.get("innerWidth") or VIEWPORT.get("width") or 1365),
        "height": int(source.get("innerHeight") or VIEWPORT.get("height") or 768),
    }


def _runtime_device_fingerprint(js: dict[str, Any]) -> dict[str, Any]:
    canvas = js.get("canvas") if isinstance(js.get("canvas"), dict) else {}
    webgl = js.get("webgl") if isinstance(js.get("webgl"), dict) else {}
    audio = js.get("audio") if isinstance(js.get("audio"), dict) else {}
    memory = js.get("memory") if isinstance(js.get("memory"), dict) else {}
    timing = js.get("timing") if isinstance(js.get("timing"), dict) else {}
    plugins = js.get("plugins") if isinstance(js.get("plugins"), list) else []
    webgl_extensions = webgl.get("extensions") if isinstance(webgl.get("extensions"), list) else []
    canvas_material = canvas.get("dataUrl") or canvas
    webgl_material = {
        "version": webgl.get("version"),
        "vendor": webgl.get("vendor"),
        "renderer": webgl.get("renderer"),
        "shadingLanguageVersion": webgl.get("shadingLanguageVersion"),
        "unmaskedVendor": webgl.get("unmaskedVendor"),
        "unmaskedRenderer": webgl.get("unmaskedRenderer"),
        "extensions": webgl_extensions,
        "params": webgl.get("params"),
    }
    return {
        "source": "roxy",
        "captured_at": int(js.get("capturedAt") or time.time() * 1000),
        "device_salt": "roxy:" + _sha256_hex({"canvas": canvas.get("previewHash"), "webgl": webgl_material, "audio": audio.get("value")})[:32],
        "canvas_h": _sha256_b64(canvas_material),
        "canvas_data_url_length": int(canvas.get("dataUrlLength") or 0),
        "cv_sig": _sha256_hex(canvas_material),
        "webgl_ext_hash": _sha256_hex(webgl_material),
        "audio_val": str(audio.get("value") or ""),
        "js_heap_size_limit": int(memory.get("jsHeapSizeLimit") or 4_395_630_592),
        "webgl_extensions": webgl_extensions,
        "font_hash": _sha256_hex({"plugins": plugins, "languages": js.get("languages")})[:32],
        "timings": {
            "tt_dfp": float(timing.get("ttCanvas") or 0.0) + float(timing.get("ttWebglBasic") or 0.0) + float(timing.get("ttAudio") or 0.0),
            "tt_canvas": float(timing.get("ttCanvas") or canvas.get("ttCanvas") or 0.0),
            "tt_webgl_basic": float(timing.get("ttWebglBasic") or 0.0),
            "tt_webgl_ext": float(timing.get("ttWebglExt") or 0.0),
            "tt_storage": 0.0,
            "tt_math": 0.10000000149011612,
        },
        "js_memory": {
            "used": int(memory.get("usedJSHeapSize") or 0),
            "total": int(memory.get("totalJSHeapSize") or 0),
        },
        "raw_runtime_hash": _sha256_hex(js),
    }


def capture_roxy_runtime_profile(
    config: RoxyCaptureConfig | None = None,
    *,
    keep_browser: bool = False,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    config = config or load_roxy_capture_config(proxy_url=proxy_url)
    if keep_browser:
        config.close_after_capture = False
        config.delete_after_capture = False
    client = RoxyApiClient(config)
    workspace_id: int | None = None
    dir_id = ""
    try:
        workspace_id, project_id = client.get_workspace_project()
        dir_id = client.create_profile(workspace_id, project_id)
        client.randomize_profile(workspace_id, dir_id)
        cdp_info = client.open_profile(workspace_id, dir_id)
        js = _evaluate_cdp_fingerprint(
            cdp_info,
            timeout_ms=int(config.timeout_seconds * 1000),
            close_browser=not keep_browser,
        )
        profile = _runtime_to_profile(js, cdp_info)
        runtime = {
            "browser_profile": profile,
            "screen": _runtime_screen(js),
            "viewport": _runtime_viewport(js),
            "device_fingerprint": _runtime_device_fingerprint(js),
        }
        if keep_browser:
            runtime["roxy_browser"] = {
                "workspace_id": workspace_id,
                "dir_id": dir_id,
                "cdp_info": cdp_info,
                "api_base": config.api_base,
                "headless": config.headless,
                "created_for": "fingerprint",
            }
        return runtime
    finally:
        if dir_id and config.close_after_capture:
            try:
                client.close_profile(dir_id)
            except Exception as exc:
                logger.debug("Roxy close profile failed: {}", exc)
        if dir_id and workspace_id is not None and config.delete_after_capture:
            try:
                client.delete_profile(workspace_id, dir_id)
            except Exception as exc:
                logger.debug("Roxy delete profile failed: {}", exc)
        client.close()


def close_roxy_browser(roxy_browser: dict[str, Any], *, delete: bool = True) -> None:
    if not isinstance(roxy_browser, dict) or not roxy_browser.get("dir_id"):
        return
    config = load_roxy_capture_config()
    if roxy_browser.get("api_base"):
        config.api_base = str(roxy_browser.get("api_base")).rstrip("/")
    client = RoxyApiClient(config)
    workspace_id = int(roxy_browser.get("workspace_id") or config.workspace_id or 0)
    dir_id = str(roxy_browser.get("dir_id") or "")
    try:
        try:
            client.close_profile(dir_id)
        except Exception as exc:
            logger.debug("Roxy close profile failed: {}", exc)
        if delete and workspace_id:
            try:
                client.delete_profile(workspace_id, dir_id)
            except Exception as exc:
                logger.debug("Roxy delete profile failed: {}", exc)
    finally:
        client.close()


def _extract_datadome_clientid_from_html(html: str) -> str:
    if "datadome" not in (html or "").lower():
        return ""
    for pattern in (
        r"\bvar\s+c\s*=\s*['\"]([^'\"]{40,})['\"]",
        r"\bc\s*=\s*['\"]([^'\"]{40,})['\"][^<]{0,600}datadome",
        r"x-datadome-clientid['\"]?\s*[:=]\s*['\"]([^'\"]{40,})",
    ):
        match = re.search(pattern, html or "", re.I | re.S)
        if match:
            return match.group(1)
    return ""


def solve_datadome_with_roxy(
    roxy_browser: dict[str, Any],
    url: str,
    *,
    cookies: list[dict[str, Any]] | None = None,
    wait_seconds: float = 12.0,
) -> dict[str, Any]:
    """Load PayPal/DataDome in the already-open Roxy browser and return cookies."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise RoxyFingerprintError("playwright 未安装，无法连接 Roxy CDP") from exc

    cdp_info = roxy_browser.get("cdp_info") if isinstance(roxy_browser, dict) else {}
    if not isinstance(cdp_info, dict) or not (cdp_info.get("ws") or cdp_info.get("http")):
        raise RoxyFingerprintError("Roxy browser CDP 信息不存在，无法执行 DataDome")

    endpoint = _connect_over_cdp(cdp_info)
    target_urls = ["https://www.paypal.com", "https://www.paypal.com/", "https://ddbm2.paypal.com"]
    wait_ms = max(1000, int(wait_seconds * 1000))
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, timeout=wait_ms)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        if cookies:
            sanitized: list[dict[str, Any]] = []
            for cookie in cookies:
                if not cookie.get("name") or cookie.get("value") is None:
                    continue
                item = {
                    "name": str(cookie.get("name")),
                    "value": str(cookie.get("value")),
                    "path": str(cookie.get("path") or "/"),
                    "secure": bool(cookie.get("secure", True)),
                }
                domain = str(cookie.get("domain") or "")
                if domain:
                    item["domain"] = domain
                else:
                    item["url"] = "https://www.paypal.com"
                same_site = str(cookie.get("sameSite") or cookie.get("same_site") or "")
                if same_site in {"Strict", "Lax", "None"}:
                    item["sameSite"] = same_site
                sanitized.append(item)
            if sanitized:
                context.add_cookies(sanitized)
        page = context.pages[0] if context.pages else context.new_page()
        status = 0
        final_url = ""
        html = ""
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=wait_ms)
            status = int(response.status) if response is not None else 0
        except Exception as exc:
            logger.debug("Roxy DataDome navigation did not finish cleanly: {}", exc)
        try:
            page.wait_for_load_state("networkidle", timeout=min(wait_ms, 8000))
        except Exception:
            pass
        try:
            page.add_script_tag(url="https://ddbm2.paypal.com/tags.js")
        except Exception as exc:
            logger.debug("Roxy DataDome explicit tags.js load skipped/failed: {}", exc)
        deadline = time.time() + wait_seconds
        browser_cookies: list[dict[str, Any]] = []
        datadome_cookie = ""
        while time.time() < deadline:
            browser_cookies = context.cookies(target_urls)
            for cookie in browser_cookies:
                if cookie.get("name") == "datadome" and cookie.get("value"):
                    datadome_cookie = str(cookie.get("value"))
                    break
            if datadome_cookie:
                break
            page.wait_for_timeout(500)
        try:
            final_url = page.url
            html = page.content()
        except Exception:
            html = ""
        if not browser_cookies:
            browser_cookies = context.cookies(target_urls)
        client_id = _extract_datadome_clientid_from_html(html)
        return {
            "ok": bool(datadome_cookie),
            "status": status,
            "url": final_url,
            "cookies": browser_cookies,
            "datadome": datadome_cookie,
            "clientid": client_id,
            "html_head": (html or "")[:2000],
        }


def _extract_mtr_response_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    products = value.get("products") if isinstance(value.get("products"), dict) else {}
    identification = products.get("identification") if isinstance(products.get("identification"), dict) else {}
    data = identification.get("data") if isinstance(identification.get("data"), dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    visitor_token = (
        data.get("visitorToken")
        or data.get("visitor_token")
        or result.get("visitorToken")
        or result.get("visitor_token")
        or ""
    )
    return {
        "requestId": value.get("requestId") or "",
        "sealedResult": value.get("sealedResult") or "",
        "visitorToken": visitor_token,
        "raw": value,
    }


def run_mtr_with_roxy_browser(
    roxy_browser: dict[str, Any],
    page_url: str,
    *,
    dfp_config: dict[str, Any],
    dfp_script_url: str,
    cookies: list[dict[str, Any]] | None = None,
    wait_seconds: float = 20.0,
) -> dict[str, Any]:
    """Run PayPal dfp.js/MTR in the already-open Roxy browser and capture sealedResult."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise RoxyFingerprintError("playwright 未安装，无法连接 Roxy CDP") from exc

    cdp_info = roxy_browser.get("cdp_info") if isinstance(roxy_browser, dict) else {}
    if not isinstance(cdp_info, dict) or not (cdp_info.get("ws") or cdp_info.get("http")):
        raise RoxyFingerprintError("Roxy browser CDP 信息不存在，无法执行 MTR")

    endpoint = _connect_over_cdp(cdp_info)
    wait_ms = max(1000, int(wait_seconds * 1000))
    mtr_path = "/mtr/1a7c3460cd8c343771081839499ed7a0"
    target_urls = ["https://www.paypal.com", "https://www.paypal.com/", "https://ddbm2.paypal.com"]
    events: dict[str, Any] = {
        "x0_status": 0,
        "x0_url": "",
        "x0_text_len": 0,
        "post_status": 0,
        "post_url": "",
        "requestId": "",
        "sealedResult": "",
        "visitorToken": "",
        "responses": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, timeout=wait_ms)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        if cookies:
            sanitized: list[dict[str, Any]] = []
            for cookie in cookies:
                if not cookie.get("name") or cookie.get("value") is None:
                    continue
                item = {
                    "name": str(cookie.get("name")),
                    "value": str(cookie.get("value")),
                    "path": str(cookie.get("path") or "/"),
                    "secure": bool(cookie.get("secure", True)),
                }
                domain = str(cookie.get("domain") or "")
                if domain:
                    item["domain"] = domain
                else:
                    item["url"] = "https://www.paypal.com"
                same_site = str(cookie.get("sameSite") or cookie.get("same_site") or "")
                if same_site in {"Strict", "Lax", "None"}:
                    item["sameSite"] = same_site
                sanitized.append(item)
            if sanitized:
                context.add_cookies(sanitized)

        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response: Any) -> None:
            try:
                url = str(response.url or "")
                if mtr_path not in url:
                    return
                method = str(response.request.method or "")
                record = {"method": method, "url": url, "status": int(response.status or 0)}
                events["responses"].append(record)
                if "/x0" in url and method.upper() == "GET":
                    text = response.text()
                    events["x0_status"] = int(response.status or 0)
                    events["x0_url"] = url
                    events["x0_text_len"] = len(text or "")
                    return
                if method.upper() == "POST":
                    text = response.text()
                    data: dict[str, Any] = {}
                    try:
                        parsed = json.loads(text or "{}")
                        if isinstance(parsed, dict):
                            data = parsed
                    except Exception:
                        data = {}
                    extracted = _extract_mtr_response_data(data)
                    events["post_status"] = int(response.status or 0)
                    events["post_url"] = url
                    events["requestId"] = extracted.get("requestId") or ""
                    events["sealedResult"] = extracted.get("sealedResult") or ""
                    events["visitorToken"] = extracted.get("visitorToken") or ""
                    events["raw_response"] = extracted.get("raw") or data
            except Exception as exc:
                logger.debug("Roxy MTR response capture failed: {}", exc)

        page.on("response", on_response)
        try:
            page.evaluate(
                """() => {
                    window.__mtrCompletedDetail = null;
                    document.addEventListener('dfp-completed-check', event => {
                        window.__mtrCompletedDetail = event.detail || true;
                    }, { once: false });
                    try {
                        sessionStorage.removeItem('4g3-fd7gc5k5');
                        sessionStorage.removeItem('4g3-fd7gc5k5-CMID');
                    } catch (e) {}
                }"""
            )
        except Exception:
            pass

        status = 0
        try:
            response = page.goto(page_url, wait_until="domcontentloaded", timeout=wait_ms)
            status = int(response.status) if response is not None else 0
        except Exception as exc:
            logger.debug("Roxy MTR navigation did not finish cleanly: {}", exc)
        try:
            page.wait_for_load_state("networkidle", timeout=min(wait_ms, 8000))
        except Exception:
            pass

        try:
            live_config = page.evaluate(
                """() => {
                    const parseMaybe = (value) => {
                        if (!value) return null;
                        let current = value;
                        for (let i = 0; i < 5; i++) {
                            if (current && typeof current === "object") return current;
                            if (typeof current !== "string") return null;
                            const text = current.trim();
                            if (!text) return null;
                            try {
                                current = JSON.parse(text);
                                continue;
                            } catch (e) {}
                            const unescaped = text
                                .replace(/\\\\u0022/g, '"')
                                .replace(/\\\\\\//g, "/")
                                .replace(/\\\\+"/g, '"');
                            if (unescaped === text) return null;
                            current = unescaped;
                        }
                        return current && typeof current === "object" ? current : null;
                    };
                    const fromWindow = parseMaybe(window.PAYPAL && window.PAYPAL.dfpData);
                    if (fromWindow) return { source: "window.PAYPAL.dfpData", config: fromWindow };
                    const node = document.getElementById("dfpconfig");
                    const fromNode = parseMaybe(node && node.textContent);
                    if (fromNode) return { source: "script#dfpconfig", config: fromNode };
                    const scripts = Array.from(document.scripts || []);
                    for (const script of scripts) {
                        const text = script.textContent || "";
                        const idx = text.indexOf("dfpConfig");
                        if (idx < 0) continue;
                        const windowText = text.slice(idx, idx + 5000);
                        const objectMatches = windowText.match(/\\{[\\s\\S]{0,1800}?\\}/g) || [];
                        for (const item of objectMatches) {
                            if (!item.includes("dfpChannel") && !item.includes("fppAPIKey")) continue;
                            const parsed = parseMaybe(item);
                            if (parsed) return { source: "script-text-dfpConfig", config: parsed };
                        }
                    }
                    return null;
                }"""
            )
        except Exception as exc:
            live_config = {"error": str(exc)}

        if isinstance(live_config, dict):
            extracted = live_config.get("config")
            if isinstance(extracted, dict):
                # The live page/DOM wins over any fallback supplied by Python.
                for key in ("dfpChannel", "clientMetaDataId", "fppAPIKey", "csrfNonce", "isQA"):
                    if extracted.get(key) not in (None, ""):
                        dfp_config[key] = extracted.get(key)
                events["extracted_dfp_config"] = {
                    "source": live_config.get("source") or "browser",
                    "channel": dfp_config.get("dfpChannel") or "",
                    "cmid": dfp_config.get("clientMetaDataId") or "",
                    "api_key_present": bool(dfp_config.get("fppAPIKey")),
                }
            elif live_config.get("error"):
                events["extract_dfp_config_error"] = live_config.get("error")

        if not (dfp_config.get("fppAPIKey") and dfp_config.get("clientMetaDataId")):
            raise RoxyFingerprintError("MTR dfpconfig 缺少 fppAPIKey/clientMetaDataId，且浏览器页面未提取到完整 dfpconfig")

        def inject_dfp() -> None:
            config_json = json.dumps(dfp_config, ensure_ascii=False, separators=(",", ":"))
            page.evaluate(
                """(cfgText) => {
                    const cfg = JSON.parse(cfgText);
                    window.PAYPAL = window.PAYPAL || {};
                    window.PAYPAL.dfpData = cfg;
                    let node = document.getElementById('dfpconfig');
                    if (!node) {
                        node = document.createElement('script');
                        node.id = 'dfpconfig';
                        node.type = 'application/json';
                        document.head.appendChild(node);
                    }
                    node.textContent = JSON.stringify(cfg);
                    try {
                        sessionStorage.removeItem('4g3-fd7gc5k5');
                        sessionStorage.removeItem('4g3-fd7gc5k5-CMID');
                    } catch (e) {}
                }""",
                config_json,
            )
            page.add_script_tag(url=dfp_script_url)

        deadline = time.time() + wait_seconds
        injected = False
        while time.time() < deadline:
            if events.get("sealedResult"):
                break
            completed = None
            try:
                completed = page.evaluate(
                    """() => ({
                        detail: window.__mtrCompletedDetail || null,
                        done: (() => { try { return sessionStorage.getItem('4g3-fd7gc5k5'); } catch(e) { return null; } })(),
                        cmid: (() => { try { return sessionStorage.getItem('4g3-fd7gc5k5-CMID'); } catch(e) { return null; } })()
                    })"""
                )
            except Exception:
                completed = None
            if completed and not events.get("completed"):
                events["completed"] = completed
            # If the live page did not trigger MTR quickly, force the exact
            # dfpconfig + dfp.js chain inside the PayPal origin.
            if not injected and time.time() + wait_seconds - deadline > 2.0 and not events.get("post_url"):
                try:
                    inject_dfp()
                    injected = True
                    events["injected_dfp"] = True
                except Exception as exc:
                    injected = True
                    events["inject_error"] = str(exc)
                    logger.debug("Roxy MTR dfp.js injection failed: {}", exc)
            page.wait_for_timeout(500)

        browser_cookies = context.cookies(target_urls)
        try:
            final_url = page.url
        except Exception:
            final_url = ""
        return {
            "ok": bool(events.get("requestId") and events.get("sealedResult")),
            "status": status,
            "url": final_url,
            "cookies": browser_cookies,
            "dfp_config": {
                "dfpChannel": dfp_config.get("dfpChannel") or "",
                "clientMetaDataId": dfp_config.get("clientMetaDataId") or "",
                "fppAPIKey": dfp_config.get("fppAPIKey") or "",
                "csrfNonce": dfp_config.get("csrfNonce") or "",
                "isQA": bool(dfp_config.get("isQA", False)),
            },
            **events,
        }


def run_phase1_risk_with_roxy_browser(
    roxy_browser: dict[str, Any],
    page_url: str,
    *,
    cookies: list[dict[str, Any]] | None = None,
    wait_seconds: float = 18.0,
    app_id: str = "IWC_NEXT_CHECKOUT",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Let the already-open Roxy Chrome execute PayPal Phase-1 risk scripts."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise RoxyFingerprintError("playwright 未安装，无法连接 Roxy CDP") from exc

    cdp_info = roxy_browser.get("cdp_info") if isinstance(roxy_browser, dict) else {}
    if not isinstance(cdp_info, dict) or not (cdp_info.get("ws") or cdp_info.get("http")):
        raise RoxyFingerprintError("Roxy browser CDP 信息不存在，无法执行 Phase1 风控脚本")

    endpoint = _connect_over_cdp(cdp_info)
    wait_ms = max(1000, int(wait_seconds * 1000))
    target_urls = [
        "https://www.paypal.com",
        "https://www.paypal.com/",
        "https://c.paypal.com",
        "https://c6.paypal.com",
        "https://ddbm2.paypal.com",
        "https://browser-intake-us5-datadoghq.com",
    ]
    events: dict[str, Any] = {
        "responses": [],
        "counts": {
            "fraudnet_p3": 0,
            "fraudnet_p1": 0,
            "fraudnet_p2": 0,
            "fraudnet_w": 0,
            "identity_di_log": 0,
            "tealeaf": 0,
            "datadog_rum": 0,
            "observability": 0,
        },
        "injected_scripts": [],
        "inject_errors": [],
    }

    def classify_url(url: str) -> str:
        if "c6.paypal.com/v1/r/d/b/p3" in url:
            return "fraudnet_p3"
        if "c.paypal.com/v1/r/d/b/p1" in url:
            return "fraudnet_p1"
        if "c.paypal.com/v1/r/d/b/p2" in url:
            return "fraudnet_p2"
        if "c.paypal.com/v1/r/d/b/w" in url:
            return "fraudnet_w"
        if "paypal.com/identity/di/log" in url:
            return "identity_di_log"
        if "paypal.com/platform/tealeaftarget" in url:
            return "tealeaf"
        if "browser-intake" in url and "/api/v2/rum" in url:
            return "datadog_rum"
        if "observability.handleClientEmit" in url or "observability" in url:
            return "observability"
        return ""

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, timeout=wait_ms)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        if cookies:
            sanitized: list[dict[str, Any]] = []
            for cookie in cookies:
                if not cookie.get("name") or cookie.get("value") is None:
                    continue
                item = {
                    "name": str(cookie.get("name")),
                    "value": str(cookie.get("value")),
                    "path": str(cookie.get("path") or "/"),
                    "secure": bool(cookie.get("secure", True)),
                }
                domain = str(cookie.get("domain") or "")
                if domain:
                    item["domain"] = domain
                else:
                    item["url"] = "https://www.paypal.com"
                same_site = str(cookie.get("sameSite") or cookie.get("same_site") or "")
                if same_site in {"Strict", "Lax", "None"}:
                    item["sameSite"] = same_site
                sanitized.append(item)
            if sanitized:
                context.add_cookies(sanitized)

        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response: Any) -> None:
            try:
                url = str(response.url or "")
                family = classify_url(url)
                if not family:
                    return
                method = str(response.request.method or "")
                record = {"family": family, "method": method, "url": url, "status": int(response.status or 0)}
                events["responses"].append(record)
                counts = events.get("counts") if isinstance(events.get("counts"), dict) else {}
                counts[family] = int(counts.get(family) or 0) + 1
                events["counts"] = counts
            except Exception as exc:
                logger.debug("Roxy Phase1 risk response capture failed: {}", exc)

        page.on("response", on_response)

        status = 0
        try:
            response = page.goto(page_url, wait_until="domcontentloaded", timeout=wait_ms)
            status = int(response.status) if response is not None else 0
        except Exception as exc:
            logger.debug("Roxy Phase1 navigation did not finish cleanly: {}", exc)
        try:
            page.wait_for_load_state("networkidle", timeout=min(wait_ms, 8000))
        except Exception:
            pass

        # Trigger browser-native observers (FraudNet timing/rDT, Tealeaf
        # activity, Datadog view/action resource hooks) without constructing
        # protocol payloads in Python.
        try:
            width = int(page.evaluate("() => Math.max(320, window.innerWidth || 800)") or 800)
            height = int(page.evaluate("() => Math.max(240, window.innerHeight || 600)") or 600)
            for index in range(6):
                x = max(10, min(width - 10, 40 + index * max(20, width // 8)))
                y = max(10, min(height - 10, 50 + ((index * 73) % max(50, height - 80))))
                page.mouse.move(x, y, steps=4)
                page.wait_for_timeout(120)
            page.mouse.wheel(0, 240)
            page.wait_for_timeout(250)
            page.mouse.wheel(0, -120)
        except Exception as exc:
            events["interaction_error"] = str(exc)

        # If PayPal's current HTML did not include every script in the first
        # paint, ask the real page to load the public runtime scripts.  CSP can
        # reject some of these; that is recorded and the natural page traffic
        # remains authoritative.
        scripts = [
            "https://c.paypal.com/da/r/fb_fp.js",
            "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js",
            "https://www.paypalobjects.com/rdaAssets/fraudnet/ext/dfp.js",
        ]
        try:
            page.evaluate(
                """(meta) => {
                    window.PAYPAL = window.PAYPAL || {};
                    window.PAYPAL.ulData = window.PAYPAL.ulData || {};
                    window.PAYPAL.ulData.app_id = meta.appId;
                    window.PAYPAL.ulData.correlation_id = meta.correlationId;
                    window.PAYPAL.ulData.page = location.href;
                }""",
                {"appId": app_id, "correlationId": correlation_id},
            )
        except Exception:
            pass
        for script_url in scripts:
            try:
                page.add_script_tag(url=script_url)
                events["injected_scripts"].append(script_url)
                page.wait_for_timeout(500)
            except Exception as exc:
                events["inject_errors"].append({"url": script_url, "error": str(exc)})

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            counts = events.get("counts") if isinstance(events.get("counts"), dict) else {}
            if (
                int(counts.get("fraudnet_p1") or 0)
                and int(counts.get("fraudnet_p2") or 0)
                and int(counts.get("fraudnet_w") or 0)
                and (int(counts.get("tealeaf") or 0) or int(counts.get("datadog_rum") or 0))
            ):
                break
            page.wait_for_timeout(500)

        browser_cookies = context.cookies(target_urls)
        try:
            final_url = page.url
        except Exception:
            final_url = ""

        counts = events.get("counts") if isinstance(events.get("counts"), dict) else {}
        observed = [name for name, count in counts.items() if int(count or 0) > 0]
        missing = [
            name
            for name in ("fraudnet_p1", "fraudnet_p2", "fraudnet_w", "identity_di_log", "tealeaf", "datadog_rum")
            if int(counts.get(name) or 0) <= 0
        ]
        return {
            "ok": bool(observed),
            "status": status,
            "url": final_url,
            "cookies": browser_cookies,
            "observed": observed,
            "missing": missing,
            **events,
        }
