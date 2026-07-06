# 05. FPTI, observability, XO logger, Tealeaf, Datadog packets

## Packet groups covered

| Packet family | Count/IDs | Duplicate decision |
| --- | --- | --- |
| `GET t.paypal.com/ts` | 44 requests | Same endpoint, dynamic query values; not strict duplicates. |
| `POST /pay/api/trpc/observability.handleClientEmit` | 77 combined ping/xhr-style requests | Same endpoint, event payloads differ; response often same. |
| `POST /xoplatform/logger/api/logger(/)` | 60 combined requests | Event/metric body differs. |
| `POST /platform/tealeaftarget` | 49 combined requests | Body is DOM/session replay; dynamic and often compressed/binary. |
| `POST /identity/di/log` | 4 observed | Risk lifecycle events; dynamic timestamps/correlation. |
| `POST browser-intake-us5-datadoghq.com/api/v2/rum` | 323 combined fetch/xhr/ping shapes | Most fail/no response but bodies differ by event id/timestamp/view/action. |
| `POST browser-intake-us5-datadoghq.com/api/v2/replay` | 29 combined | Multipart/binary session replay segments; dynamic. |

## FPTI `GET t.paypal.com/ts`

Representative IDs: `102145#165/#166/#167`, captcha IDs `#529/#537/#541`.

Query fields:

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `v` | FPTI SDK version (`1.15.0`). | PayPal analytics JS. | Static. |
| `t` | Event timestamp. | Browser JS clock. | Device/session dynamic. |
| `g` | Timezone offset. | Browser Date/timezone. | Device dynamic. |
| `pgrp`, `page` | Page group/name (`modularcheckoutnodeweb`, authchallenge, Hermes, signup). | `PAYPAL.analytics.setup` and page context. | Page dynamic. |
| `comp`, `tsrce` | Component/source app. | PayPal app config. | Static/page dynamic. |
| `cu`, `ef_policy`, `c_prefs` | Cookie/privacy policy dimensions. | Cookies/page config. | Session dynamic. |
| `pxpguid`, `pgst`, `calc`, `csci`, `nsid` | FPTI/session correlation ids and page-start time. | Cookies/bootstrap/analytics JS. | Session dynamic. |
| `rsta`, `ccpg`, `cnac` | Locale/country. | Page locale. | Page dynamic. |
| `fltk` | Flow token, often EC token. | Checkout session. | Session dynamic. |
| `event_name`, `action`, `pglk`, `uicomp`, `uitype` | UI event dimensions. | User/browser event. | User/page dynamic. |
| `bw`, `bh`, `sw`, `sh`, `dw`, `dh`, `cd` | Browser/document/screen size/color depth. | Browser APIs. | Device dynamic. |

Use: analytics beacon. Response body is small/binary tracking pixel; the query is the meaningful payload.

## Observability `/pay/api/trpc/observability.handleClientEmit`

Representative IDs: `102145#65/#66/#76/#77/#79/#84/#86/#160`, plus many later packets.

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| URL `token=BA-...` | Current PayPal checkout token for observability. | Page URL/session. | Session dynamic. |
| `json[].eventName` | Client event name. | PayPal ModXO app. | Page/user dynamic. |
| `json[].logLevel` | Log severity. | Client logger. | Static/event dynamic. |
| `payload.analytics.*` | Business analytics dimensions: intent, product, flow, client metadata id, country/language, request type, etc. | PayPal app state. | Page/session dynamic. |
| `payload.metrics.*` | Metric namespace/event/id/value and dimensions. | Client metrics runtime. | Device/page dynamic. |
| `payload.logs.*` | Structured logs/outcome/status. | PayPal client app. | Page dynamic. |
| `payload.common.memberOrGuestFlow` | Flow type. | App state. | Static/session flag. |
| Response `result.data.json.resp=ok` | Acknowledgement. | PayPal observability API. | Response-derived static. |

These are not fully duplicate because each event payload and timestamp differs even when response sha repeats.

## XO Logger `/xoplatform/logger/api/logger(/)`

Representative IDs: `102145#910/#912/#918/#922/#923/#926/#929/#931`.

Two main body families:

1. `metrics[]` with dimensions and metric namespace/event name.
2. `events[]` with event level, event name, payload, and `meta.integrationData`.

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `events[].event` | Lifecycle event, e.g. content loaded, risk update, terms, hcaptcha/health check. | Weasley/Hagrid JS. | Page/event dynamic. |
| `events[].level` | Log level. | Logger runtime. | Static/event dynamic. |
| `events[].payload.clientCountry`, `clientLocale` | Locale dimensions. | Page/profile. | Page dynamic. |
| `events[].payload.clientTimestamp`, `timestamp` | Event time. | Browser JS clock. | Device dynamic. |
| `events[].payload.token` | EC/BA token. | Session state. | Session dynamic. |
| `events[].payload.apiCorrelationId` | API/debug correlation. | Response/client runtime. | Dynamic. |
| `metrics[].dimensions.*` | App/flow/page/status/error dimensions. | Client metrics. | Page/event dynamic. |
| `meta.integrationData.contextId/contextType` | EC context token. | Signup page state. | Session dynamic. |
| `meta.integrationData.integrationMethod=FULLPAGE`, `integrationType=EC` | Integration descriptors. | PayPal config. | Static. |
| `meta.merchantData.*` | Merchant metadata. | Checkout GraphQL/session response. | Response-derived dynamic/static per merchant. |

## Tealeaf `/platform/tealeaftarget`

Representative IDs: `102145#102/#194/#302/#396/#539/#546/#570/#572`.

Request bodies may be compressed/binary or text; they represent browser session replay and DOM events.

| Field family | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| URL `X-PageId`, `X-Tealeaf`, `Content-Type` query fields | Tealeaf client metadata. | Tealeaf JS. | Session/static mixed. |
| URL `paypal_client_cfci` | Flow/action correlation. | PayPal page/JS. | Page/session dynamic. |
| `sessions[].id`, `tabId` | Tealeaf session/tab ids. | Tealeaf runtime. | Session dynamic. |
| `sessions[].messages[]` | DOM, load, click, input, screenview events. | Browser DOM/event recorder. | User/device dynamic. |
| `messages[].target.*` | Element metadata and sometimes input state. | DOM event recorder. | User/page dynamic. |
| `screenview.queryParams.*` | Mirrors current PayPal URL params. | Browser URL. | Session dynamic. |
| `clientEnvironment.webEnvironment.*` | Browser/window/page metadata. | Browser APIs. | Device/page dynamic. |

Relation: Tealeaf packets explain how user-entered fields and page transitions are recorded independently of GraphQL bodies.

## Identity DI log `/identity/di/log`

Representative IDs: `102145#109/#307/#486`, `103548#96`.

Fields:

- `events[].event`: DFP lifecycle events.
- `events[].payload.timestamp`, `comp`, `btz`, `ul_corr_id`.
- `tracking[].event_name`, `component`, `browser_timezone`, `ul_corr_id`.

Use: logs device fingerprint library loading/vendor invocation/edge mapping. Source is FraudNet/DFP JS, dynamic by timestamp and correlation id.

## Datadog RUM `/api/v2/rum`

Representative initial packet: `102145#1`; many later IDs in both captures.

URL query fields:

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `ddsource=browser` | Source type. | Datadog SDK. | Static. |
| `dd-api-key` | Public Datadog client token. | Datadog SDK config. | Static per app/deployment. |
| `dd-evp-origin-version=6.33.0` | SDK version. | Datadog SDK. | Static/build. |
| `dd-request-id` | Per-request UUID. | Datadog SDK. | Request dynamic. |
| `batch_time`, `_dd.api` | Batch/send metadata. | SDK runtime. | Device/session dynamic. |

Body fields:

| Field family | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `type` | RUM event type: action, view, resource, error, long_task, etc. | Datadog SDK. | Event dynamic. |
| `_dd.*` | SDK metadata/config. | Datadog SDK config. | Static/session mixed. |
| `application.id` | Datadog app id. | SDK config. | Static per app. |
| `date` | Event timestamp. | Browser clock. | Dynamic. |
| `view.*` | View id/name/url/referrer. | SDK runtime/page URL. | Page/session dynamic. |
| `action.*` | Action id/target/loading time. | User events. | User/event dynamic. |
| `resource.*`, `error.*`, `device.*`, `connectivity.*`, `display.*` | Browser telemetry. | Browser APIs/SDK. | Device/event dynamic. |
| `feature_flags.*` | PayPal feature flag state. | PayPal app/Datadog integration. | Build/session dynamic. |

Most RUM requests have no completed response because they are beacons/pings, but they are still outbound packets and carry meaningful telemetry.

## Datadog Replay `/api/v2/replay`

Representative IDs: `102145#3/#96/#100/#117/#169/#184/#193/#203`.

Body is multipart/binary replay segment. It contains browser session replay chunks. The packet is SDK-generated, dynamic, and not useful to replay exactly except as evidence of page/DOM session capture.
