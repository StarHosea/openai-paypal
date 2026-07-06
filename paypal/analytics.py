"""PayPal Analytics, XO Logger, and Datadog RUM stubs.

These systems send telemetry/tracking data alongside the main flow.
They are not strictly required for the protocol but help avoid detection.
"""
import time
import uuid
import random
from loguru import logger
from config import USER_AGENT, SCREEN, VIEWPORT, BROWSER_PROFILE


def _state(session):
    return getattr(session, "state", None)


def _profile(session) -> dict:
    state = _state(session)
    return (getattr(state, "browser_profile", None) if state else None) or BROWSER_PROFILE


def _screen(session) -> dict:
    state = _state(session)
    return (getattr(state, "screen", None) if state else None) or SCREEN


def _viewport(session) -> dict:
    state = _state(session)
    return (getattr(state, "viewport", None) if state else None) or VIEWPORT


def _stable_uuid_attr(session, attr: str, hex_value: bool = False) -> str:
    state = _state(session)
    if not state:
        return uuid.uuid4().hex if hex_value else str(uuid.uuid4())
    value = getattr(state, attr, "")
    if not value:
        value = uuid.uuid4().hex if hex_value else str(uuid.uuid4())
        setattr(state, attr, value)
    return value


def _datadog_view_id(session, service: str, page_url: str) -> str:
    state = _state(session)
    if not state:
        return str(uuid.uuid4())
    if not getattr(state, "datadog_view_ids", None):
        state.datadog_view_ids = {}
    # Keep one stable view per service/path in the local protocol session.
    key = f"{service}:{page_url.split('?', 1)[0]}"
    if key not in state.datadog_view_ids:
        state.datadog_view_ids[key] = str(uuid.uuid4())
    return state.datadog_view_ids[key]


def send_xo_logger(session, event_data: dict):
    """Send client event log to PayPal XO Logger."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "x-app-name": "checkoutuinodeweb",
        "Origin": "https://www.paypal.com",
        "Referer": getattr(getattr(session, "state", None), "signup_url", "")
        or f"https://www.paypal.com/pay?token={getattr(getattr(session, 'state', None), 'ba_token', '')}&ul=1",
    }
    try:
        session.post(
            "https://www.paypal.com/xoplatform/logger/api/logger/",
            json=event_data,
            headers=headers,
        )
    except Exception as e:
        logger.warning(f"XO Logger failed: {e}")


def send_analytics_ts(session, page_name: str, ba_token: str,
                      ec_token: str = "", user_id: str = "",
                      event: str = "im"):
    """Send PayPal Analytics tracking pixel (t.paypal.com/ts)."""
    ts = int(time.time() * 1000)
    state = _state(session)
    profile = _profile(session)
    screen = _screen(session)
    viewport = _viewport(session)
    pxp_guid = getattr(state, "pxp_guid", "") if state else ""
    if state and not pxp_guid:
        state.pxp_guid = uuid.uuid4().hex
        pxp_guid = state.pxp_guid
    page_start = getattr(state, "page_start_time_ms", 0) if state else 0
    if state and not page_start:
        state.page_start_time_ms = ts - random.randint(2000, 5000)
        page_start = state.page_start_time_ms
    calc = getattr(state, "fpti_calc", "") if state else ""
    if state and not calc:
        state.fpti_calc = uuid.uuid4().hex[:13]
        calc = state.fpti_calc
    params = {
        "v": "1.15.0",
        "t": str(ts),
        # PayPal FPTI expects JavaScript Date#getTimezoneOffset() in minutes.
        # For UTC+8 this is -480; for São Paulo UTC-3 this is +180.
        # Keep this aligned with FraudNet p1's tz/tzName.
        "g": str(profile["timezone_offset_minutes"]),
        "pgrp": "main:billing:hagrid",
        "page": page_name,
        "pgtf": "Nodejs",
        "s": "ci",
        "env": "live",
        "comp": "checkoutuinodeweb",
        "tsrce": "checkoutuinodeweb",
        "cu": "1",
        "ef_policy": "ccpa",
        "c_prefs": "T=1,P=1,F=1,type=explicit_banner",
        "pxpguid": pxp_guid or uuid.uuid4().hex,
        "pgst": str(page_start or (ts - random.randint(2000, 5000))),
        "calc": calc or uuid.uuid4().hex[:13],
        "rsta": profile["locale"],
        "ccpg": profile["country"],
        "cnac": profile["country"],
        "flnm": "Hagrid",
        "e": event,
        "fpti_sdk_name": "pa-js",
        "cd": str(screen["colorDepth"]),
        "sw": str(screen["width"]),
        "sh": str(screen["height"]),
        "bw": str(viewport["width"]),
        "bh": str(viewport["height"]),
        "ce": "1",
    }
    if ec_token:
        params["fltk"] = ec_token
    if user_id:
        params["cust"] = user_id
        params["party_id"] = user_id
        params["acnt"] = "personal"
        params["aver"] = "unverified"
        params["rstr"] = "unrestricted"

    try:
        session.get("https://t.paypal.com/ts", params=params)
    except Exception as e:
        logger.warning(f"Analytics ts failed: {e}")


def send_observability_emit(session, ba_token: str):
    """Send observability emit (trpc endpoint)."""
    state = getattr(session, "state", None)
    referer = getattr(state, "signup_url", "") or f"https://www.paypal.com/pay?token={ba_token}&ul=1"
    try:
        session.post(
            f"https://www.paypal.com/pay/api/trpc/observability.handleClientEmit?token={ba_token}",
            content=b"",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://www.paypal.com",
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except Exception as e:
        logger.warning(f"Observability emit failed: {e}")


def send_weasley_log(session, ec_token: str, signup_url: str, event_names: list[str],
                     country: str = "BR", lang: str = "pt",
                     extra_payload: dict | None = None):
    """Send checkoutweb/weasley client logger events in browser-like order."""
    if not ec_token or not event_names:
        return

    now = int(time.time() * 1000)
    locale = f"{lang}_{country}"
    events = []
    for i, name in enumerate(event_names):
        payload = {
            "clientCountry": country,
            "clientLocale": locale,
            "clientTimestamp": now + i,
            "timestamp": str(now + i),
            "token": ec_token,
        }
        if extra_payload:
            payload.update(extra_payload)
        events.append({"level": "info", "event": name, "payload": payload})

    body = {
        "events": events,
        "meta": {
            "integrationData": {
                "contextId": ec_token,
                "contextType": ec_token,
                "integrationMethod": "FULLPAGE",
                "integrationType": "EC",
            }
        },
        "tracking": [],
        "metrics": [],
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.paypal.com",
        "Referer": signup_url,
        "X-Requested-With": "XMLHttpRequest",
        "X-App-Name": "checkoutuinodeweb_weasley",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    try:
        session.post(
            "https://www.paypal.com/xoplatform/logger/api/logger/",
            json=body,
            headers=headers,
        )
    except Exception as e:
        logger.debug(f"Weasley logger failed: {e}")


_DD_MODXO_CONFIG = {
    "client_token": "pub09bb4929a2fe5661ad79710dbf90a55a",
    "app_id": "e04156c3-60a0-43e6-93fc-69f3b371849d",
    "service": "modxo",
    "sdk_version": "6.33.0",
}
_DD_WEASLEY_CONFIG = {
    "client_token": "pub415c6a9024efa76be0dc3ee2e2099763",
    "app_id": "223dbba5-b459-4d87-80e7-b063c4436787",
    "service": "weasley(checkoutuinodeweb)",
    "sdk_version": "5.35.1",
}

def send_datadog_rum_view(
    session,
    page_url: str,
    ba_token: str,
    dd_config: dict[str, str] = _DD_MODXO_CONFIG,
):
    try:
        _ = ba_token
        now = int(time.time() * 1000)
        client_token = dd_config["client_token"]
        app_id = dd_config["app_id"]
        service = dd_config["service"]
        sdk_version = dd_config["sdk_version"]
        dd_session_id = _stable_uuid_attr(session, "datadog_session_id", hex_value=True)
        dd_view_id = _datadog_view_id(session, service, page_url)
        safe_page_url = page_url.replace("\\", "\\\\").replace('"', '\\"')
        safe_service = service.replace("\\", "\\\\").replace('"', '\\"')
        params = {
            "ddsource": "browser",
            "ddtags": f"sdk_version:{sdk_version},env:production,service:{service}",
            "dd-api-key": client_token,
            "dd-evp-origin": "browser",
            "dd-evp-origin-version": sdk_version,
            "dd-request-id": str(uuid.uuid4()),
            "batch_time": str(now),
        }
        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "https://www.paypal.com",
        }
        body = (
            f'{{"type":"view","date":{now},'
            f'"service":"{safe_service}",'
            f'"view":{{"id":"{dd_view_id}","url":"{safe_page_url}",'
            f'"referrer":"","loading_type":"route_change",'
            f'"time_spent":{random.randint(3000, 15000)},'
            f'"long_task":{{"count":0}},'
            f'"resource":{{"count":{random.randint(5, 20)}}},'
            f'"error":{{"count":0}},'
            f'"action":{{"count":{random.randint(1, 5)}}},'
            f'"dom_complete":{random.randint(800, 3000)},'
            f'"dom_content_loaded":{random.randint(500, 2000)},'
            f'"dom_interactive":{random.randint(400, 1500)},'
            f'"load_event":{random.randint(1000, 4000)}}},'
            f'"application":{{"id":"{app_id}"}},'
            f'"session":{{"id":"{dd_session_id}","type":"user"}},'
            f'"_dd":{{"format_version":2,"drift":0,'
            f'"session":{{"plan":"pro"}}}}}}\n'
        )
        session.post(
            "https://browser-intake-us5-datadoghq.com/api/v2/rum",
            params=params,
            content=body,
            headers=headers,
        )
    except Exception as e:
        logger.debug(f"Datadog RUM view failed: {e}")


def send_datadog_rum_action(
    session,
    action_name: str,
    page_url: str,
    dd_config: dict[str, str] = _DD_MODXO_CONFIG,
):
    try:
        now = int(time.time() * 1000)
        client_token = dd_config["client_token"]
        app_id = dd_config["app_id"]
        service = dd_config["service"]
        sdk_version = dd_config["sdk_version"]
        dd_session_id = _stable_uuid_attr(session, "datadog_session_id", hex_value=True)
        dd_view_id = _datadog_view_id(session, service, page_url)
        safe_action_name = action_name.replace("\\", "\\\\").replace('"', '\\"')
        safe_page_url = page_url.replace("\\", "\\\\").replace('"', '\\"')
        safe_service = service.replace("\\", "\\\\").replace('"', '\\"')
        params = {
            "ddsource": "browser",
            "ddtags": f"sdk_version:{sdk_version},env:production,service:{service}",
            "dd-api-key": client_token,
            "dd-evp-origin": "browser",
            "dd-evp-origin-version": sdk_version,
            "dd-request-id": str(uuid.uuid4()),
            "batch_time": str(now),
        }
        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "https://www.paypal.com",
        }
        body = (
            f'{{"type":"action","date":{now},'
            f'"service":"{safe_service}",'
            f'"action":{{"id":"{uuid.uuid4()}","type":"custom",'
            f'"target":{{"name":"{safe_action_name}"}},'
            f'"loading_time":{random.randint(100, 2000)},'
            f'"resource":{{"count":0}},'
            f'"error":{{"count":0}},'
            f'"long_task":{{"count":0}}}},'
            f'"application":{{"id":"{app_id}"}},'
            f'"session":{{"id":"{dd_session_id}","type":"user"}},'
            f'"view":{{"id":"{dd_view_id}","url":"{safe_page_url}"}},'
            f'"_dd":{{"format_version":2}}}}\n'
        )
        session.post(
            "https://browser-intake-us5-datadoghq.com/api/v2/rum",
            params=params,
            content=body,
            headers=headers,
        )
    except Exception as e:
        logger.debug(f"Datadog RUM action failed: {e}")
