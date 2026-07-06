#!/usr/bin/env python3
# pyright: basic
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import shutil
import urllib.parse
import zlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path('/home/nonewhite/paypal-pay')
ANALYSIS_ROOT = ROOT / 'roxy_packet_manual_analysis'
PACKETS_ROOT = ANALYSIS_ROOT / 'packets'
CAPTURE_ROOTS = [
    ROOT / 'captures' / 'roxy-paypal-20260705-102145',
    ROOT / 'captures' / 'roxy-paypal-20260705-103548',
]

MAX_FIELDS = 180
MAX_VALUE = 700
MAX_LINKS = 30

IMPORTANT_HEADERS = {
    'accept', 'accept-language', 'content-type', 'origin', 'referer', 'user-agent',
    'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform', 'sec-fetch-site',
    'sec-fetch-mode', 'sec-fetch-dest', 'x-requested-with', 'x-app-name',
    'paypal-client-context', 'paypal-client-metadata-id', 'x-country', 'x-locale',
    'x-csrf-token', 'x-paypal-internal-euat', 'x-datadome-clientid', 'next-action',
    'content-length', 'cookie', 'authorization',
}

MARKERS = [
    '__INITIAL_DATA__', '__APPLICATION_METADATA__', 'PAYPAL.analytics.setup',
    'window.PAYPAL', 'contentManifestUrl', 'content-manifest', 'initialDataQuery',
    'releaseHash', 'loggerEndpoint', 'ecToken', 'clientMetadataId', 'ctxId',
    'fn_sync_data', 'csrf', 'sessionID', 'hcaptcha', 'recaptcha', 'siteKey',
    'baToken', 'window.fpti', 'merchantID',
]

@dataclass
class Packet:
    capture: str
    root: Path
    id: int
    request: dict[str, Any]
    response: dict[str, Any]
    finished: dict[str, Any]
    failed: dict[str, Any]
    bad_jsonl_rows: int = 0

    @property
    def method(self) -> str:
        return str(self.request.get('method') or '')

    @property
    def url(self) -> str:
        return str(self.request.get('url') or '')

    @property
    def resource_type(self) -> str:
        return str(self.request.get('resourceType') or '')

    @property
    def status(self) -> str:
        if self.response:
            return str(self.response.get('status') or '')
        if self.failed:
            return 'failed'
        return 'no-response'


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()


def safe_slug(value: str, max_len: int = 90) -> str:
    value = re.sub(r'^https?://', '', value or '')
    value = re.sub(r'[^a-zA-Z0-9._-]+', '_', value).strip('_')
    return (value[:max_len] or 'packet')


def read_events(root: Path) -> tuple[dict[int, dict[str, Any]], int]:
    groups: dict[int, dict[str, Any]] = defaultdict(dict)
    bad = 0
    path = root / 'network' / 'events.jsonl'
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            bad += 1
            continue
        gid = ev.get('id')
        if gid is None:
            continue
        typ = str(ev.get('type') or '')
        # Keep first event of each type; captures use one request/response/finished/failed per id.
        groups[int(gid)].setdefault(typ, ev)
    return groups, bad


def load_packets() -> list[Packet]:
    packets: list[Packet] = []
    for root in CAPTURE_ROOTS:
        groups, bad = read_events(root)
        for gid, g in sorted(groups.items()):
            if 'request' not in g:
                continue
            packets.append(Packet(
                capture=root.name,
                root=root,
                id=gid,
                request=g.get('request') or {},
                response=g.get('response') or {},
                finished=g.get('requestfinished') or {},
                failed=g.get('requestfailed') or {},
                bad_jsonl_rows=bad,
            ))
    return packets


def packet_payload_for_fp(p: Packet) -> dict[str, Any]:
    return {
        'method': p.request.get('method'),
        'url': p.request.get('url'),
        'resourceType': p.request.get('resourceType'),
        'requestHeaders': p.request.get('headers') or {},
        'postSha': (p.request.get('postData') or {}).get('sha256') or '',
        'status': p.response.get('status') if p.response else '',
        'responseHeaders': p.response.get('headers') or {},
        'responseSha': ((p.finished.get('responseBody') or {}).get('sha256') or ''),
        'failure': p.failed.get('failure') or '',
    }


def strict_fp(p: Packet) -> str:
    payload = packet_payload_for_fp(p)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def body_file_from_request(req: dict[str, Any]) -> Path | None:
    path = (req.get('postData') or {}).get('path')
    return Path(path) if path else None


def body_file_from_finished(fin: dict[str, Any]) -> Path | None:
    path = (fin.get('responseBody') or {}).get('path')
    return Path(path) if path else None


def read_bytes(path: Path | None, max_bytes: int = 2_000_000) -> bytes:
    if not path or not path.is_file():
        return b''
    data = path.read_bytes()
    return data[:max_bytes]


def decode_bytes(data: bytes) -> tuple[str, str]:
    if not data:
        return '', 'empty'
    if data[:2] == b'\x1f\x8b':
        try:
            return gzip.decompress(data).decode('utf-8', errors='replace'), 'gzip+utf8'
        except Exception:
            pass
    for wbits, label in [(zlib.MAX_WBITS, 'zlib+utf8'), (-zlib.MAX_WBITS, 'raw-zlib+utf8')]:
        try:
            return zlib.decompress(data, wbits).decode('utf-8', errors='replace'), label
        except Exception:
            pass
    try:
        return data.decode('utf-8', errors='replace'), 'utf8'
    except Exception:
        return base64.b64encode(data[:512]).decode('ascii'), 'binary-base64-preview'


def value_display(value: Any, max_len: int = MAX_VALUE) -> str:
    if isinstance(value, (dict, list)):
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    elif value is None:
        raw = ''
    else:
        raw = str(value)
    raw = raw.replace('\r', '\\r').replace('\n', '\\n')
    if len(raw) <= max_len:
        return raw
    return f"{raw[:320]}...{raw[-120:]} (len={len(raw)}, sha256={hashlib.sha256(raw.encode('utf-8', errors='replace')).hexdigest()})"


def md_escape(value: Any) -> str:
    s = value_display(value)
    s = s.replace('|', '\\|')
    return s


def flatten(obj: Any, prefix: str = '', limit: int = MAX_FIELDS) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    def rec(x: Any, p: str):
        if len(out) >= limit:
            return
        if isinstance(x, dict):
            if p:
                out.append((p, '<object>'))
            for k, v in x.items():
                np = f'{p}.{k}' if p else str(k)
                rec(v, np)
        elif isinstance(x, list):
            out.append((p or '[]', f'<array len={len(x)}>'))
            if x:
                rec(x[0], f'{p}[]' if p else '[]')
        else:
            out.append((p, x))
    rec(obj, prefix)
    return out[:limit]


def parse_body_fields(path: Path | None, headers: dict[str, Any]) -> tuple[str, list[tuple[str, Any]], str]:
    data = read_bytes(path)
    if not data:
        return 'none', [], ''
    text, encoding = decode_bytes(data)
    stripped = text.strip()
    content_type = str(headers.get('content-type') or headers.get('Content-Type') or '').lower()

    # JSON bodies, including JSON text saved as .txt.
    if stripped.startswith('{') or stripped.startswith('[') or 'json' in content_type:
        try:
            obj = json.loads(stripped)
            return f'json ({encoding})', flatten(obj), stripped
        except Exception:
            pass

    # x-www-form-urlencoded or plain query-string bodies.
    if 'application/x-www-form-urlencoded' in content_type or (('=' in stripped and '&' in stripped[:3000]) and not stripped.startswith('------')):
        try:
            pairs = urllib.parse.parse_qsl(stripped, keep_blank_values=True)
            if pairs:
                return f'form-urlencoded ({encoding})', pairs[:MAX_FIELDS], stripped
        except Exception:
            pass

    # Multipart form-data.
    if 'multipart/form-data' in content_type or stripped.startswith('------WebKitFormBoundary'):
        fields: list[tuple[str, Any]] = []
        parts = re.split(r'\r?\n--[-A-Za-z0-9_]+(?:--)?\r?\n', stripped)
        for part in parts:
            if 'Content-Disposition:' not in part:
                continue
            m = re.search(r'name="([^"]+)"', part)
            if not m:
                continue
            name = m.group(1)
            content = re.split(r'\r?\n\r?\n', part, maxsplit=1)
            value = content[1].strip('\r\n-') if len(content) > 1 else ''
            fields.append((name, value))
        if fields:
            return f'multipart-form ({encoding})', fields[:MAX_FIELDS], stripped

    if encoding == 'binary-base64-preview':
        return 'binary', [('body', f'<binary bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}>')], ''
    return f'text ({encoding})', [('body', stripped[:4000])], stripped


def parse_response_fields(path: Path | None, headers: dict[str, Any]) -> tuple[str, list[tuple[str, Any]], str]:
    data = read_bytes(path)
    if not data:
        return 'none', [], ''
    text, encoding = decode_bytes(data)
    stripped = text.strip()
    ct = str(headers.get('content-type') or headers.get('Content-Type') or '').lower()
    if stripped.startswith('{') or stripped.startswith('[') or 'json' in ct:
        try:
            obj = json.loads(stripped)
            return f'json ({encoding})', flatten(obj, limit=120), stripped
        except Exception:
            pass
    if 'text/html' in ct or '<html' in stripped[:2000].lower() or '<!doctype html' in stripped[:2000].lower():
        title = ''
        m = re.search(r'<title[^>]*>(.*?)</title>', stripped, re.I | re.S)
        if m:
            title = re.sub(r'\s+', ' ', m.group(1)).strip()
        found = [m for m in MARKERS if m in stripped]
        fields = []
        if title:
            fields.append(('html.title', title))
        fields.extend((f'html.marker.{m}', 'present') for m in found[:60])
        return f'html ({encoding})', fields, stripped
    if 'javascript' in ct or path and path.suffix == '.js':
        found = [m for m in MARKERS if m in stripped]
        return f'javascript ({encoding})', [(f'js.marker.{m}', 'present') for m in found[:80]], stripped
    if encoding == 'binary-base64-preview':
        return 'binary', [('body', f'<binary bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}>')], ''
    return f'text ({encoding})', [('body.preview', stripped[:2000])], stripped


def family(p: Packet) -> tuple[str, str, str, str]:
    u = p.url
    parsed = urllib.parse.urlparse(u)
    host = (parsed.hostname or '').lower()
    path = parsed.path or '/'
    m = p.method
    rt = p.resource_type
    if '/agreements/approve' in path:
        return ('PayPal approval navigation', '01_NAVIGATION_NEXTJS.md', 'Entry/redirect page for billing agreement approval.', 'HTML/server redirect creates or carries BA-token route state.')
    if path in ['/pay', '/pay/'] and m == 'POST':
        return ('PayPal `/pay` Next.js server action', '01_NAVIGATION_NEXTJS.md', 'Client-side Next.js action submitted to PayPal checkout page.', 'Generated by `/pay` HTML, Next.js action manifest, and PayPal JS; body is multipart action protocol.')
    if path in ['/pay', '/pay/']:
        return ('PayPal `/pay` page/RSC navigation', '01_NAVIGATION_NEXTJS.md', 'Checkout page or React Server Components fetch.', 'Generated by PayPal redirect and Next.js client runtime.')
    if '/pay/api/countries' in path:
        return ('PayPal country catalog API', '01_NAVIGATION_NEXTJS.md', 'Fetches country selector/catalog for checkout UI.', 'Generated by PayPal client when country/locale UI needs country metadata.')
    if '/checkoutweb/signup' in path:
        return ('PayPal signup page', '01_NAVIGATION_NEXTJS.md', 'Loads Weasley checkoutweb signup page and bootstrap variables.', 'Generated by PayPal redirect after checkout action; HTML/JS creates GraphQL and logger variables.')
    if '/webapps/hermes' in path:
        return ('PayPal Hermes review/fallback page', '01_NAVIGATION_NEXTJS.md', 'Loads billing/Hermes review or fallback page.', 'Generated by PayPal signup/funding result and client router.')
    if host == 'pay.openai.com':
        return ('OpenAI/Stripe return page', '06_THIRD_PARTY_ASSETS.md', 'Merchant return after PayPal/Stripe redirect.', 'Generated by Stripe/OpenAI return URL after PayPal outcome.')
    if '/idapps/graphql' in path:
        return ('PayPal idapps GraphQL', '02_GRAPHQL_SIGNUP_AUTHORIZE.md', 'Identity/risk/OTP GraphQL call.', 'Generated by identity app JS using csrfNonce, clientInfo, credentials, and fn_sync_data.')
    if path in ['/graphql', '/graphql/'] or '/graphql' in path:
        return ('PayPal GraphQL', '02_GRAPHQL_SIGNUP_AUTHORIZE.md', 'Checkout/signup/payment GraphQL operation.', 'Generated by PayPal signup/Hermes JS bundle and current EC/BA session state.')
    if '/auth/logclientdata' in path:
        return ('PayPal auth challenge telemetry', '03_AUTH_CAPTCHA.md', 'Logs captcha/authchallenge client state.', 'Generated by authchallenge HTML and FPTI/captcha JS.')
    if '/auth/verifyhcaptchapassive' in path:
        return ('PayPal passive hCaptcha verification', '03_AUTH_CAPTCHA.md', 'Submits passive hCaptcha token to PayPal.', 'Generated by hCaptcha passive iframe/JS and authchallenge page state.')
    if '/auth/validatecaptcha' in path:
        return ('PayPal captcha validation', '03_AUTH_CAPTCHA.md', 'Submits hCaptcha/reCAPTCHA challenge result to PayPal.', 'Generated by authchallenge HTML plus captcha SDK proof/timing values.')
    if 'hcaptcha' in host or 'hcaptcha' in path or 'recaptcha' in host or 'recaptcha' in path:
        return ('Third-party captcha SDK/resource', '03_AUTH_CAPTCHA.md', 'Loads or requests hCaptcha/reCAPTCHA challenge resources/config/proofs.', 'Generated by captcha iframe/SDK embedded by PayPal challenge page.')
    if host in {'c.paypal.com', 'c6.paypal.com'} or '/v1/r/d/b/' in path or 'ddbm2.paypal.com' in host or path.endswith('/da/r/fb_fp.js'):
        return ('PayPal risk/FraudNet/DDBM', '04_RISK_FRAUDNET.md', 'Device fingerprint/risk collection or risk SDK asset.', 'Generated by PayPal risk JS using browser APIs, cookies, current token, and runtime timings.')
    if host == 't.paypal.com':
        return ('PayPal FPTI analytics', '05_TELEMETRY_OBSERVABILITY.md', 'PayPal analytics beacon.', 'Generated by PAYPAL.analytics/FPTI setup in HTML and browser event runtime.')
    if '/pay/api/trpc/observability.handleClientEmit' in path:
        return ('PayPal observability event', '05_TELEMETRY_OBSERVABILITY.md', 'Client observability log/metric emission.', 'Generated by ModXO/checkout client logging runtime.')
    if '/xoplatform/logger/api/logger' in path:
        return ('PayPal XO logger', '05_TELEMETRY_OBSERVABILITY.md', 'Weasley/Hagrid lifecycle event or metric logger.', 'Generated by checkoutweb/Hermes JS logger.')
    if '/platform/tealeaftarget' in path:
        return ('PayPal Tealeaf session replay', '05_TELEMETRY_OBSERVABILITY.md', 'DOM/session replay and user-event capture.', 'Generated by Tealeaf browser recorder from DOM/user/page state.')
    if '/identity/di/log' in path:
        return ('PayPal identity DI log', '05_TELEMETRY_OBSERVABILITY.md', 'DFP/risk lifecycle log.', 'Generated by identity/FraudNet risk JS.')
    if 'browser-intake-us5-datadoghq.com' in host:
        return ('Datadog RUM/replay', '05_TELEMETRY_OBSERVABILITY.md', 'Datadog browser RUM or session replay beacon.', 'Generated by Datadog browser SDK embedded in PayPal/OpenAI pages.')
    if 'stripe.com' in host:
        return ('Stripe SDK/telemetry/resource', '06_THIRD_PARTY_ASSETS.md', 'Stripe Checkout SDK resource or telemetry.', 'Generated by Stripe JS on pay.openai.com return/checkout page.')
    if any(x in host for x in ['google', 'gstatic', 'googletagmanager', 'googleapis', 'fonts.gstatic.com', 'ogads-pa.clients6.google.com']):
        return ('Google/GTM/Maps/static service', '06_THIRD_PARTY_ASSETS.md', 'Google SDK/static/telemetry resource used by captcha, tags, fonts, or maps.', 'Generated by embedded Google/GTM/reCAPTCHA/Maps scripts.')
    if 'paypalobjects.com' in host or rt in {'script', 'stylesheet', 'font', 'image'} or re.search(r'\.(js|css|png|svg|woff2?|jpg|gif|ico)$', path):
        return ('Static/build resource', '06_THIRD_PARTY_ASSETS.md', 'Static JS/CSS/font/image resource for page/runtime.', 'Generated by HTML script/link tags or client runtime asset loader.')
    if 'paypal.com' in host:
        return ('Other PayPal packet', '01_NAVIGATION_NEXTJS.md', 'PayPal first-party API/page request not in a more specific family.', 'Generated by PayPal page/client runtime.')
    return ('Other third-party/static packet', '06_THIRD_PARTY_ASSETS.md', 'Third-party resource or telemetry outside PayPal protocol core.', 'Generated by embedded third-party SDK or browser resource loader.')


def classify_field(name: str, value: Any, p: Packet, location: str) -> tuple[str, str, str, str]:
    lname = name.lower()
    sval = '' if value is None else str(value)
    lval = sval.lower()
    ctx_family = family(p)[0]

    def ret(cls: str, src: str, meaning: str, use: str):
        return cls, src, meaning, use

    if sval.startswith('BA-') or lname in {'ba_token', 'batoken', 'billingagreementid', 'variables.billingagreementid'} or 'billingagreementid' in lname:
        return ret('Session dynamic', 'Merchant/PayPal redirect or PayPal response', 'Billing Agreement token (`BA-*`).', 'Binds approval, signup, Hermes, FraudNet correlation, and final authorize.')
    if sval.startswith('EC-') or lname in {'ectoken'} or (lname.endswith('token') and 'ec-' in lval):
        return ret('Session dynamic', 'PayPal checkout/signup redirect or HTML bootstrap', 'PayPal EC checkout token.', 'Used as GraphQL token, funding-source lookup token, FPTI flow token, and signup context.')
    if lname in {'token', 'variables.token'}:
        return ret('Session dynamic', 'PayPal URL/session state', 'Current checkout token. It can be BA on `/pay` or EC in signup GraphQL.', 'Carries the active checkout/session context into this packet.')
    if lname.endswith('ssrt') or lname == 'ssrt':
        return ret('Session dynamic', 'PayPal redirect chain', 'Session routing timestamp-like value.', 'Carried through `/pay`, signup, Hermes, and telemetry to bind route state.')
    if lname in {'ul', 'fallback', 'billinglite', 'redirecttohermes', 'fromsignuplite', 'rcache', 'cookiebannervariant', 'ulonboardredirect'}:
        return ret('Static/session flag', 'PayPal route/config flag', 'Checkout routing or UI behavior flag.', 'Selects a checkout/signup/Hermes branch for this session.')
    if 'ctxid' in lname:
        return ret('Page/session dynamic', 'PayPal HTML bootstrap / Next.js action state', 'Checkout context UUID.', 'Binds RSC/action/idapps/FraudNet state to the active page context.')
    if 'paypal_client_cfci' in lname or 'cfci' in lname:
        return ret('Page/session dynamic', 'PayPal HTML/JS client flow correlation', 'Client flow/correlation id, often with action suffix.', 'Correlates server actions, observability, Tealeaf, and page state.')
    if 'clientmetadataid' in lname or 'paypal-client-metadata-id' in lname:
        return ret('Page/session dynamic', 'PayPal HTML/JS or SDK bootstrap', 'Client metadata/correlation id.', 'Sent in PayPal headers and risk/GraphQL contexts.')
    if lname in {'country.x', 'country', 'countrycode', 'countrycodeasstring', 'shippingcountrycode', 'usercountry', 'buyercountry', 'phonecountry', 'x-country'} or lname.endswith('.country'):
        return ret('Page/user dynamic', 'Locale/profile/page state', 'Country or buyer country selector.', 'Controls locale metadata, signup compliance, card/address behavior, captcha locale, and routing.')
    if lname in {'locale.x', 'locale', 'languagecode', 'x-locale', 'hl', 'rsta'} or 'locale' in lname:
        return ret('Page/user dynamic', 'Browser/profile/page locale state', 'Language/locale selector.', 'Controls rendered language, GraphQL locale metadata, captcha locale, and analytics.')
    if lname in {'operationname'} or lname.endswith('.operationname'):
        return ret('Static/page dynamic', 'PayPal JS GraphQL client', 'GraphQL operation selector.', 'Chooses the query/mutation semantics for this request.')
    if lname == 'query' or lname.endswith('.query'):
        return ret('Build/static', 'PayPal JS bundle', 'GraphQL document text.', 'Defines the fields and mutation/query shape expected by PayPal GraphQL.')
    if 'contentidentifier' in lname:
        return ret('Build/page dynamic', 'Signup HTML + content manifest hash', 'Legal/content terms identifier.', 'Required by `SignUpNewMemberMutation` to bind accepted terms/content version.')
    if 'fn_sync_data' in lname or 'fnid' in lname:
        return ret('Risk/challenge dynamic', 'FraudNet/device fingerprint JS', 'FraudNet/device sync payload or id.', 'Feeds risk assessment in `/pay` action, idapps GraphQL, and signup GraphQL.')
    if lname in {'email', 'credentialvalue', '_1_login_email'} or lname.endswith('.email') or 'email' in lname:
        return ret('User input', 'User/test profile or login form input', 'Email/credential value.', 'Used for login/signup identity and sometimes idapps credential challenge.')
    if 'password' in lname:
        return ret('User input', 'User/test profile or login form input', 'Password credential.', 'Used for login or new account creation.')
    if 'phonenumber' in lname or lname.endswith('.phone') or lname in {'phone', 'phonecountry', '_1_login_phone_country_code'}:
        return ret('User input', 'User/test phone profile and OTP UI', 'Phone number/country information.', 'Used for OTP initiation, signup phone profile, and phone validation.')
    if lname.endswith('pin') or lname == 'pin' or 'otp' in lname:
        return ret('User input', 'User received SMS/OTP input', 'OTP/PIN verification code or OTP behavior field.', 'Confirms risk-based phone challenge.')
    if 'authid' in lname:
        return ret('Response-derived', 'Initiate 2FA GraphQL response', 'OTP auth transaction id.', 'Consumed by confirm-phone mutation.')
    if 'challengeid' in lname:
        return ret('Response-derived', 'Initiate 2FA / captcha response state', 'Challenge id for OTP or captcha.', 'Binds confirmation/verification to the issued challenge.')
    if 'cardnumber' in lname or lname.endswith('.card.number') or 'card.number' in lname:
        return ret('User input', 'User/test card entry; client may derive network/BIN metadata', 'Payment card number.', 'Used for installment eligibility and signup funding instrument creation.')
    if 'cardtype' in lname or 'card.' in lname or lname.endswith('card'):
        return ret('User input / derived', 'Card entry plus client-side card metadata', 'Card detail or derived card metadata.', 'Used for card eligibility, signup, and funding setup.')
    if 'billingaddress' in lname or 'shippingaddress' in lname or 'postalcode' in lname or 'address' in lname:
        return ret('User input / response-derived', 'User/test address form and address autocomplete response', 'Address/postal field.', 'Used for signup profile, billing/shipping, compliance, and card checks.')
    if 'identitydocument' in lname or 'cpf' in lname or 'document' in lname:
        return ret('User input', 'User/test identity profile', 'Identity document / CPF compliance field.', 'Used for country-specific signup/KYC requirements.')
    if 'dateofbirth' in lname or lname.endswith('.dob'):
        return ret('User input', 'User/test profile', 'Date of birth field.', 'Used for account eligibility and compliance.')
    if 'firstname' in lname or 'lastname' in lname or 'nameoncard' in lname:
        return ret('User input', 'User/test profile or card form', 'Personal/cardholder name field.', 'Used for signup profile and card holder identity.')
    if 'legalagreements' in lname or 'marketingoptout' in lname or 'supportedthreeds' in lname or 'crsdata' in lname:
        return ret('User choice / static capability', 'Signup UI or client capability config', 'Legal/marketing/compliance/3DS capability field.', 'Used by signup mutation to satisfy legal/compliance/capability requirements.')
    if lname in {'_csrf', 'csrf', 'csrfnonce'} or 'csrf' in lname:
        return ret('Page/session dynamic', 'PayPal auth/signup HTML bootstrap', 'CSRF token/nonce.', 'Authorizes form/GraphQL challenge submission for the active page session.')
    if lname in {'_sessionid', 'sessionid'} or lname.endswith('sessionid'):
        return ret('Page/session dynamic', 'Auth challenge or SDK session bootstrap', 'Session identifier.', 'Binds captcha/auth/telemetry packets to current browser session.')
    if lname in {'_hash', '_requestid', 'jse'} or 'requestid' in lname:
        return ret('Risk/challenge dynamic', 'PayPal captcha challenge page/server', 'Captcha/challenge opaque request/hash/evaluation field.', 'Required by PayPal challenge validation.')
    if 'hcaptchatoken' in lname or 'grcv3enttoken' in lname or lname in {'hcaptcha', 'recaptcha', 'publickey', 'sitekey', '_adsrecaptchasitekey'}:
        return ret('Risk/challenge dynamic', 'hCaptcha/reCAPTCHA SDK and PayPal challenge config', 'Captcha site key, status marker, or proof token.', 'Used to obtain/submit challenge verification.')
    if 'render' in lname and 'time' in lname or 'eval' in lname and 'time' in lname or lname.endswith('_utc'):
        return ret('Risk/device dynamic', 'Browser JS timing around captcha or UI event', 'Render/evaluation timestamp.', 'Feeds challenge telemetry and anti-bot timing checks.')
    if lname in {'appid', 'correlationid'} or lname.startswith('payload.navigator') or lname.startswith('payload.screen') or lname.startswith('payload.window') or lname.startswith('payload.connectiondata') or lname.startswith('payload.data') or lname.startswith('payload.tz'):
        return ret('Device/risk dynamic', 'PayPal FraudNet/risk JS using browser APIs', 'Risk/device fingerprint field.', 'Feeds PayPal device/risk assessment and correlates to checkout token.')
    if lname.startswith('fpti.') or lname in {'pgrp', 'page', 'pgst', 'pxpguid', 'calc', 'csci', 'nsid', 'comp', 'tsrce', 'fltk', 'event_name', 'action', 'pglk', 'uicomp', 'uitype'}:
        return ret('Page/session dynamic', 'PayPal analytics/FPTI bootstrap and runtime', 'FPTI analytics dimension.', 'Tracks page, flow, UI, timing, and correlation state.')
    if lname.startswith('_dd') or 'dd-' in lname or lname.startswith('application.') or lname.startswith('view.') or lname.startswith('action.') or lname.startswith('resource.') or lname.startswith('error.'):
        return ret('Third-party SDK dynamic', 'Datadog browser SDK', 'Datadog RUM/replay telemetry field.', 'Reports browser view/action/resource/error/session replay data.')
    if lname in {'payment_intent_client_secret', 'payment_intent', 'redirect_pm_type', 'redirect_status', 'ui_mode', 'status_from_redirect', 'checkout_session_id', 'session_id'} or 'stripe' in lname or 'publishable_key' in lname:
        return ret('Third-party SDK dynamic', 'Stripe/OpenAI checkout SDK or return URL', 'Stripe checkout/payment return field.', 'Restores Stripe payment state and records PayPal redirect outcome.')
    if lname in {'dd-api-key', 'key', 'publishable_key', 'public_key'} and ('stripe' in ctx_family.lower() or 'datadog' in ctx_family.lower()):
        return ret('Third-party SDK static/dynamic', 'Third-party SDK config', 'Public client/config key.', 'Lets SDK send telemetry or identify merchant/app context.')
    if lname in {'user-agent', 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform', 'accept-language'}:
        return ret('Device/environment dynamic', 'Browser request headers', 'Browser identity/client-hints header.', 'Used by server/CDN/risk for content negotiation and fingerprinting.')
    if lname in {'referer', 'origin'}:
        return ret('Page/session dynamic', 'Browser navigation/security model', 'Origin/referrer context.', 'Shows which page initiated the request and controls CORS/security behavior.')
    if lname == 'cookie':
        return ret('Response-derived/session dynamic', 'Browser cookie jar from previous Set-Cookie responses', 'Cookie header.', 'Carries PayPal/DataDome/Cloudflare/session state into the request.')
    if 'timestamp' in lname or lname in {'t', 'date', 'created', 'batch_time', 'pgst'} or re.fullmatch(r'\d{12,}', sval):
        return ret('Device/session dynamic', 'Browser/SDK/server clock or generated id', 'Timestamp-like or long numeric correlation value.', 'Used for ordering, telemetry, challenge freshness, or response-derived challenge state.')
    if re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', sval):
        return ret('Session/request dynamic', 'Browser/SDK/server UUID generator', 'UUID correlation/session/event id.', 'Correlates one SDK, page, challenge, or request instance.')
    if 'token' in lname or 'secret' in lname or 'access' in lname:
        return ret('Session/request dynamic', 'Server/SDK/client bootstrap', 'Opaque token/secret/access value.', 'Authenticates, correlates, or resumes a current-session operation.')
    if location == 'url_query':
        return ret('Static/session mixed', 'URL route/query', 'Route parameter for this endpoint.', 'Controls routing, filtering, cache, locale, SDK, or session selection.')
    if location == 'header':
        return ret('Static/session mixed', 'Browser or SDK request header', 'HTTP header field.', 'Controls negotiation, security context, correlation, or browser identity.')
    return ret('Unknown/mixed', 'Observed in captured packet', 'Field observed in this packet.', 'Meaning inferred from endpoint family; inspect raw body for endpoint-specific usage.')


def url_fields(url: str) -> list[tuple[str, Any]]:
    p = urllib.parse.urlparse(url)
    pairs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    if not pairs:
        return []
    return pairs[:MAX_FIELDS]


def selected_headers(headers: dict[str, Any]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for k, v in sorted((headers or {}).items(), key=lambda x: str(x[0]).lower()):
        kl = str(k).lower()
        if kl in IMPORTANT_HEADERS or kl.startswith('x-') or kl.startswith('paypal-'):
            if kl == 'cookie':
                cookies = []
                for part in str(v).split(';'):
                    name = part.split('=', 1)[0].strip()
                    if name:
                        cookies.append(name)
                out.append((str(k), f"<cookie header len={len(str(v))}; names={', '.join(cookies[:80])}>"))
            elif kl == 'authorization':
                out.append((str(k), f"<authorization header len={len(str(v))}; sha256={hashlib.sha256(str(v).encode()).hexdigest()}>"))
            else:
                out.append((str(k), v))
    return out[:MAX_FIELDS]


def field_table(rows: list[tuple[str, Any]], p: Packet, location: str) -> list[str]:
    if not rows:
        return ['No fields observed in this location.']
    lines = ['| Field | Value | Static/dynamic | Source/generation | Meaning | Use |', '| --- | --- | --- | --- | --- | --- |']
    for name, value in rows[:MAX_FIELDS]:
        cls, src, meaning, use = classify_field(str(name), value, p, location)
        lines.append(f'| `{md_escape(name)}` | `{md_escape(value)}` | {cls} | {md_escape(src)} | {md_escape(meaning)} | {md_escape(use)} |')
    if len(rows) > MAX_FIELDS:
        lines.append(f'| `<truncated>` | `{len(rows) - MAX_FIELDS} additional fields omitted; raw body file has full payload` |  |  |  |  |')
    return lines


def response_header_table(headers: dict[str, Any]) -> list[str]:
    if not headers:
        return ['No response headers captured.']
    interesting = []
    for k, v in sorted(headers.items(), key=lambda x: str(x[0]).lower()):
        kl = str(k).lower()
        if kl in {'set-cookie', 'location', 'paypal-debug-id', 'server-timing', 'x-nextjs-deployment-id', 'x-datadome', 'content-type', 'cache-control', 'etag', 'cf-cache-status', 'x-cache', 'x-cache-hits', 'x-served-by', 'x-timer'} or kl.startswith('x-'):
            if kl == 'set-cookie':
                val = str(v)
                # Roxy header map may collapse repeated Set-Cookie values; preserve names and length.
                names = re.findall(r'(^|,\s*)([A-Za-z0-9_.$-]+)=', val)
                cookie_names = [n[1] for n in names[:80]]
                interesting.append((k, f"<set-cookie len={len(val)}; names={', '.join(cookie_names)}>"))
            else:
                interesting.append((k, v))
    if not interesting:
        interesting = list(headers.items())[:40]
    lines = ['| Header | Value/summary | Meaning |', '| --- | --- | --- |']
    for k, v in interesting[:80]:
        kl = str(k).lower()
        if kl == 'location':
            meaning = 'Server redirect target; produces next route/query/session values.'
        elif kl == 'set-cookie':
            meaning = 'Response-derived browser state used by later requests.'
        elif kl == 'paypal-debug-id':
            meaning = 'PayPal server-side debug/correlation id.'
        elif kl in {'server-timing', 'x-cache', 'x-cache-hits', 'x-served-by', 'x-timer', 'cf-cache-status'}:
            meaning = 'CDN/cache/timing diagnostics.'
        elif kl == 'x-nextjs-deployment-id':
            meaning = 'Next.js deployment/build identifier.'
        elif kl == 'content-type':
            meaning = 'Response MIME type controlling parser/runtime handling.'
        else:
            meaning = 'Response metadata/header for this packet.'
        lines.append(f'| `{md_escape(k)}` | `{md_escape(v)}` | {meaning} |')
    return lines


def operation_name(p: Packet, body_fields: list[tuple[str, Any]]) -> str:
    for k, v in body_fields:
        if str(k).lower() == 'operationname':
            return str(v)
    parsed = urllib.parse.urlparse(p.url)
    q = parsed.query
    if q and '=' not in q and '&' not in q:
        return urllib.parse.unquote(q)
    pairs = urllib.parse.parse_qsl(q, keep_blank_values=True)
    if pairs and pairs[0][0] and pairs[0][0] not in {'_'}:
        return pairs[0][0]
    return ''


def family_specific_relation(p: Packet, body_fields: list[tuple[str, Any]]) -> list[str]:
    fam, _, _, _ = family(p)
    op = operation_name(p, body_fields)
    out = []
    if 'approval' in fam:
        out.append('Produces or carries `ba_token`; later packets reuse it as checkout/fraud/authorize correlation.')
    elif '`/pay` page' in fam or '/pay' in fam:
        out.append('Depends on initial BA token/ssrt route state; produces HTML bootstrap (`ctxId`, `paypal_client_cfci`, action ids, analytics/risk config) consumed by `/pay` POSTs and telemetry.')
    elif 'server action' in fam:
        out.append('Depends on `/pay` HTML Next-Action ids, `ctxId`, and risk/form fields; response may produce RSC updates, HTML challenge, or redirect to signup.')
    elif 'signup page' in fam:
        out.append('Consumes BA/EC tokens and locale route state; produces signup bootstrap values and content-manifest inputs consumed by GraphQL signup calls.')
    elif 'GraphQL' in fam:
        if op:
            out.append(f'GraphQL operation `{op}` determines this packet\'s semantic role.')
        if op == 'InitiateRiskBasedTwoFactorPhoneConfirmationMutation':
            out.append('Produces `authId` and `challengeId` for a later confirm-phone packet.')
        elif op == 'ConfirmRiskBasedTwoFactorPhoneConfirmationMutation':
            out.append('Consumes `authId`, `challengeId`, `pin`, and EC token from prior OTP initiation/session state.')
        elif op == 'SignUpNewMemberMutation':
            out.append('Consumes user/profile/card/address/legal/contentIdentifier/fn_sync_data values; produces signup/funding/account state and can trigger Hermes fallback.')
        elif op == 'authorize':
            out.append('Consumes BA token and legal/funding preferences; produces billing authorization result and merchant return URL.')
        else:
            out.append('Consumes EC/locale/session variables and returns page/session data for later signup or checkout decisions.')
    elif 'captcha' in fam.lower() or 'hcaptcha' in fam.lower() or 'recaptcha' in fam.lower():
        out.append('Depends on authchallenge HTML/iframe config; produces or submits captcha proof/timing values used by PayPal challenge validation.')
    elif 'risk' in fam.lower() or 'fraud' in fam.lower():
        out.append('Depends on current BA/EC correlation token plus browser APIs; produces device/risk state that feeds `fn_sync_data` and PayPal risk decisions.')
    elif 'FPTI' in fam or 'observability' in fam or 'logger' in fam or 'Tealeaf' in fam or 'Datadog' in fam:
        out.append('Telemetry packet: depends on current page/session/browser state and records state rather than directly advancing checkout.')
    elif 'Stripe' in fam or 'OpenAI' in fam:
        out.append('Third-party return/telemetry packet: depends on Stripe/OpenAI checkout state and records PayPal redirect result.')
    else:
        out.append('Resource/support packet: loaded by HTML/JS runtime; usually supports page rendering, SDK execution, captcha, telemetry, or fonts/assets.')
    return out


def scalar_occurrence_values(_p: Packet, fields: list[tuple[str, Any]]) -> list[tuple[str, str]]:
    vals = []
    for name, value in fields:
        if isinstance(value, (dict, list)) or value is None:
            continue
        s = str(value)
        if not s or len(s) > 300:
            continue
        if s.startswith(('BA-', 'EC-', 'pi_')) or re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', s) or re.fullmatch(r'\d{10,}', s) or 'secret_' in s or len(s) >= 16 and re.fullmatch(r'[A-Za-z0-9_~.-]+', s):
            vals.append((str(name), s))
    return vals


def build_value_links(packets: list[Packet], parsed_body_by_key: dict[tuple[str, int], list[tuple[str, Any]]]) -> dict[str, list[tuple[str, int, str]]]:
    links: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for p in packets:
        all_fields = []
        all_fields.extend(url_fields(p.url))
        all_fields.extend(parsed_body_by_key.get((p.capture, p.id), []))
        for name, val in scalar_occurrence_values(p, all_fields):
            links[val].append((p.capture, p.id, name))
    return links


def source_files(p: Packet) -> list[str]:
    lines = []
    req_path = body_file_from_request(p.request)
    resp_path = body_file_from_finished(p.finished)
    if req_path:
        pd = p.request.get('postData') or {}
        lines.append(f'- Request body: `{req_path}` ({pd.get("bytes", "?")} bytes, sha256 `{pd.get("sha256", "")}`)')
    else:
        lines.append('- Request body: none captured')
    if resp_path:
        rb = p.finished.get('responseBody') or {}
        lines.append(f'- Response body: `{resp_path}` ({rb.get("bytes", "?")} bytes, sha256 `{rb.get("sha256", "")}`, content-type `{md_escape(rb.get("contentType", ""))}`)')
    else:
        if p.failed:
            lines.append(f'- Response body: none; request failed with `{md_escape(p.failed.get("failure") or {})}`')
        else:
            lines.append('- Response body: none captured')
    return lines


def html_js_evidence(p: Packet, _resp_kind: str, resp_text: str) -> list[str]:
    fam = family(p)[0]
    lines = []
    if resp_text:
        found = [m for m in MARKERS if m in resp_text]
        if found:
            lines.append(f'- Response content contains markers: {", ".join(f"`{m}`" for m in found[:40])}.')
    if 'navigation' in fam.lower() or '`/pay` page' in fam or 'signup page' in fam or 'Hermes' in fam:
        lines.append('- HTML/JS source: server-rendered PayPal page plus deployed JS bundles create bootstrap variables, action ids, telemetry config, risk SDK calls, and GraphQL inputs.')
    elif 'server action' in fam:
        lines.append('- HTML/JS source: Next.js client action runtime serializes form/action fields into multipart body and uses `Next-Action`/RSC protocol.')
    elif 'GraphQL' in fam:
        lines.append('- HTML/JS source: PayPal checkoutweb/Hermes/idapps JS bundle selects the operation and fills variables from URL, HTML bootstrap, user form state, and prior GraphQL responses.')
    elif 'captcha' in fam.lower():
        lines.append('- HTML/JS source: PayPal authchallenge HTML embeds hCaptcha/reCAPTCHA iframes and challenge JS; SDKs generate tokens/timings.')
    elif 'risk' in fam.lower() or 'fraud' in fam.lower():
        lines.append('- JS source: PayPal FraudNet/DDBM scripts enumerate browser/device APIs and correlate payloads to current BA/EC token.')
    elif 'Datadog' in fam:
        lines.append('- JS source: Datadog browser SDK watches view/action/resource/error/replay events and emits this packet.')
    elif 'Stripe' in fam or 'OpenAI' in fam:
        lines.append('- JS/source: Stripe/OpenAI checkout page or Stripe SDK generates return/telemetry state.')
    else:
        lines.append('- Source: loaded by HTML script/link/img/font/fetch references or embedded third-party SDK runtime.')
    return lines


def write_packet_doc(p: Packet, strict_groups: dict[str, list[Packet]], representative: dict[tuple[str, int], Packet], value_links: dict[str, list[tuple[str, int, str]]]) -> Path:
    fam, doc, purpose, generation = family(p)
    req_headers = p.request.get('headers') or {}
    resp_headers = p.response.get('headers') or {}
    req_path = body_file_from_request(p.request)
    resp_path = body_file_from_finished(p.finished)
    req_kind, req_body_fields, _req_text = parse_body_fields(req_path, req_headers)
    resp_kind, resp_fields, resp_text = parse_response_fields(resp_path, resp_headers)
    url_q = url_fields(p.url)
    header_rows = selected_headers(req_headers)
    fp = strict_fp(p)
    dup_items = strict_groups.get(fp, [])
    rep = representative.get((p.capture, p.id), p)
    is_rep = (rep.capture == p.capture and rep.id == p.id)
    is_dup_group = len(dup_items) > 1
    decision = 'ANALYZED'
    if is_dup_group and not is_rep:
        decision = 'SKIP_STRICT_DUPLICATE'
    elif is_dup_group and is_rep:
        decision = 'ANALYZED (representative for strict duplicate group)'

    rel_lines = family_specific_relation(p, req_body_fields)
    link_lines = []
    all_for_links = url_q + req_body_fields
    seen_values = set()
    for name, val in scalar_occurrence_values(p, all_for_links):
        if val in seen_values:
            continue
        seen_values.add(val)
        occ = value_links.get(val, [])
        occ = [x for x in occ if not (x[0] == p.capture and x[1] == p.id)]
        if occ:
            samples = ', '.join(f'`{c}#{i}:{field}`' for c, i, field in occ[:MAX_LINKS])
            link_lines.append(f'- `{md_escape(name)}` value `{md_escape(val)}` also appears in {len(occ)} other packet field(s): {samples}')
        if len(link_lines) >= 12:
            break
    if not link_lines:
        link_lines.append('- No reusable scalar token/id from this packet was found in another parsed request field, or values are only present in raw bodies/headers.')

    parsed = urllib.parse.urlparse(p.url)
    out_dir = PACKETS_ROOT / p.capture
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f'{p.id:05d}_{safe_slug(p.method + "_" + (parsed.netloc + parsed.path))}.md'
    out = out_dir / fname

    lines = []
    lines.append(f'# Packet `{p.capture}#{p.id}`')
    lines.append('')
    lines.append('## My decision')
    lines.append('')
    lines.append(f'- Decision: **{decision}**')
    lines.append(f'- Covered by packet-family analysis: `{doc}`')
    lines.append(f'- Family: **{fam}**')
    lines.append(f'- Purpose: {purpose}')
    lines.append(f'- Generated by: {generation}')
    if is_dup_group:
        ids = ', '.join(f'`{x.capture}#{x.id}`' for x in dup_items[:80])
        lines.append(f'- Strict duplicate group: {len(dup_items)} packet(s). Representative: `{rep.capture}#{rep.id}`. Members: {ids}')
        if not is_rep:
            lines.append('- This packet is not re-expanded semantically beyond this document because its full strict fingerprint matches the representative. The field/source meaning is identical to the representative.')
    else:
        lines.append('- Strict duplicate group: none; this packet is analyzed as its own packet instance.')
    lines.append('')
    lines.append('## Request/response summary')
    lines.append('')
    lines.append('| Item | Value |')
    lines.append('| --- | --- |')
    lines.append(f'| Capture | `{p.capture}` |')
    lines.append(f'| ID | `{p.id}` |')
    lines.append(f'| Time | `{md_escape(p.request.get("time", ""))}` |')
    lines.append(f'| Page ID | `{md_escape(p.request.get("pageId", ""))}` |')
    lines.append(f'| Method | `{p.method}` |')
    lines.append(f'| Resource type | `{p.resource_type}` |')
    lines.append(f'| Navigation request | `{p.request.get("isNavigationRequest", "")}` |')
    lines.append(f'| Status | `{p.status}` |')
    lines.append(f'| URL | `{md_escape(p.url)}` |')
    if p.failed:
        lines.append(f'| Failure | `{md_escape(p.failed.get("failure") or {})}` |')
    lines.append('')
    lines.append('## Source files')
    lines.append('')
    lines.extend(source_files(p))
    lines.append('')
    lines.append('## URL query fields')
    lines.append('')
    lines.extend(field_table(url_q, p, 'url_query'))
    lines.append('')
    lines.append('## Request headers that matter')
    lines.append('')
    lines.extend(field_table(header_rows, p, 'header'))
    lines.append('')
    lines.append('## Request body fields')
    lines.append('')
    lines.append(f'- Body parser result: `{req_kind}`')
    lines.extend(field_table(req_body_fields, p, 'body'))
    lines.append('')
    lines.append('## Response analysis')
    lines.append('')
    lines.append(f'- Response parser result: `{resp_kind}`')
    lines.append('')
    lines.append('### Response headers')
    lines.append('')
    lines.extend(response_header_table(resp_headers))
    lines.append('')
    lines.append('### Response body fields / markers')
    lines.append('')
    lines.extend(field_table(resp_fields, p, 'response'))
    lines.append('')
    lines.append('## HTML/JS generation/source analysis')
    lines.append('')
    lines.extend(html_js_evidence(p, resp_kind, resp_text))
    lines.append('')
    lines.append('## Packet relationship analysis')
    lines.append('')
    lines.extend(f'- {x}' for x in rel_lines)
    lines.append('')
    lines.append('### Same-value links found across parsed request fields')
    lines.append('')
    lines.extend(link_lines)
    lines.append('')
    lines.append('## Static vs dynamic conclusion')
    lines.append('')
    lines.append(f'- Overall packet class: **{fam}**.')
    if 'static' in fam.lower() or p.resource_type in {'script', 'stylesheet', 'font', 'image'}:
        lines.append('- Static/resource components may be deployment-scoped, but request headers/cookies/cache response metadata can still be session/edge dynamic.')
    elif 'GraphQL' in fam or 'signup' in fam or 'authorize' in fam:
        lines.append('- The operation name/query shape is mostly build-static; variables are session/user/response/risk dynamic.')
    elif 'captcha' in fam.lower():
        lines.append('- Challenge config may be page-static for a challenge, but proof tokens, timing, hashes, and request IDs are challenge dynamic.')
    elif 'risk' in fam.lower() or 'FraudNet' in fam:
        lines.append('- App/source constants are static per phase; fingerprint values and correlation IDs are device/session dynamic.')
    elif 'Telemetry' in fam or 'Datadog' in fam or 'FPTI' in fam or 'logger' in fam:
        lines.append('- SDK/app ids can be static; event ids, timestamps, page ids, dimensions, and payloads are runtime dynamic.')
    else:
        lines.append('- Route/resource identifiers can be static/build-scoped; query/session/cookie/SDK values are dynamic where listed above.')
    _ = out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return out


def write_indexes(packets: list[Packet], doc_paths: dict[tuple[str, int], Path], strict_groups: dict[str, list[Packet]], representative: dict[tuple[str, int], Packet]) -> None:
    PACKETS_ROOT.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append('# Per-packet analysis documents')
    lines.append('')
    lines.append('One Markdown document exists for every parsed request packet from the two approved roxy captures.')
    lines.append('')
    lines.append(f'- Total docs: {len(doc_paths)}')
    lines.append(f'- Strict duplicate groups: {sum(1 for v in strict_groups.values() if len(v) > 1)}')
    lines.append(f'- Strict duplicate packet count: {sum(len(v) for v in strict_groups.values() if len(v) > 1)}')
    lines.append('')
    lines.append('| Capture | ID | Decision | Family | Status | Method | Resource | Document | URL |')
    lines.append('| --- | ---: | --- | --- | --- | --- | --- | --- | --- |')
    for p in sorted(packets, key=lambda x: (x.capture, x.id)):
        rep = representative.get((p.capture, p.id), p)
        decision = 'SKIP_STRICT_DUPLICATE' if (rep.capture, rep.id) != (p.capture, p.id) else 'ANALYZED'
        fam = family(p)[0]
        rel = doc_paths[(p.capture, p.id)].relative_to(ANALYSIS_ROOT)
        url = md_escape(p.url if len(p.url) < 140 else p.url[:137] + '...')
        lines.append(f'| `{p.capture}` | {p.id} | {decision} | {md_escape(fam)} | `{p.status}` | `{p.method}` | `{p.resource_type}` | [`{rel}`](../{rel.as_posix()}) | `{url}` |')
    _ = (PACKETS_ROOT / 'INDEX.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    by_capture: dict[str, list[Packet]] = defaultdict(list)
    for p in packets:
        by_capture[p.capture].append(p)
    for capture, items in by_capture.items():
        cap_lines = [f'# Packet docs for `{capture}`', '', f'- Docs: {len(items)}', '', '| ID | Decision | Family | Status | Method | Resource | Document |', '| ---: | --- | --- | --- | --- | --- | --- |']
        for p in sorted(items, key=lambda x: x.id):
            rep = representative.get((p.capture, p.id), p)
            decision = 'SKIP_STRICT_DUPLICATE' if (rep.capture, rep.id) != (p.capture, p.id) else 'ANALYZED'
            rel = doc_paths[(p.capture, p.id)].relative_to(PACKETS_ROOT / capture)
            cap_lines.append(f'| {p.id} | {decision} | {md_escape(family(p)[0])} | `{p.status}` | `{p.method}` | `{p.resource_type}` | [`{rel}`]({rel.as_posix()}) |')
        _ = (PACKETS_ROOT / capture / 'INDEX.md').write_text('\n'.join(cap_lines) + '\n', encoding='utf-8')


def main() -> int:
    packets = load_packets()
    if PACKETS_ROOT.exists():
        shutil.rmtree(PACKETS_ROOT)
    PACKETS_ROOT.mkdir(parents=True)

    fp_to_packets: dict[str, list[Packet]] = defaultdict(list)
    for p in packets:
        fp_to_packets[strict_fp(p)].append(p)
    representative: dict[tuple[str, int], Packet] = {}
    for group in fp_to_packets.values():
        rep = sorted(group, key=lambda x: (x.capture, x.id))[0]
        for p in group:
            representative[(p.capture, p.id)] = rep

    parsed_body_by_key: dict[tuple[str, int], list[tuple[str, Any]]] = {}
    for p in packets:
        req_path = body_file_from_request(p.request)
        _kind, fields, _text = parse_body_fields(req_path, p.request.get('headers') or {})
        parsed_body_by_key[(p.capture, p.id)] = fields
    value_links = build_value_links(packets, parsed_body_by_key)

    doc_paths: dict[tuple[str, int], Path] = {}
    for p in packets:
        path = write_packet_doc(p, fp_to_packets, representative, value_links)
        doc_paths[(p.capture, p.id)] = path
    write_indexes(packets, doc_paths, fp_to_packets, representative)

    summary = {
        'packetDocs': len(doc_paths),
        'captures': {cap: sum(1 for p in packets if p.capture == cap) for cap in sorted({p.capture for p in packets})},
        'strictDuplicateGroups': sum(1 for v in fp_to_packets.values() if len(v) > 1),
        'strictDuplicatePackets': sum(len(v) for v in fp_to_packets.values() if len(v) > 1),
        'output': str(PACKETS_ROOT),
    }
    _ = (PACKETS_ROOT / 'generation_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
