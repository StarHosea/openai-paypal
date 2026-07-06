# 06. Third-party return flow, Stripe telemetry, Google/GTM/Maps, and static assets

## Stripe/OpenAI return and Stripe SDK packets

### OpenAI/Stripe return page (`103548#556`)

```text
GET https://pay.openai.com/c/pay/cs_live_...?lid=...&payment_intent=...&payment_intent_client_secret=...&redirect_pm_type=paypal&redirect_status=failed&ui_mode=custom#...
```

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `cs_live_...` path | Stripe Checkout session id. | Stripe/OpenAI checkout. | Third-party session dynamic. |
| `lid` | Stripe logger/link id. | Stripe SDK/runtime. | Third-party dynamic. |
| `payment_intent` | PaymentIntent id. | Stripe return. | Third-party dynamic. |
| `payment_intent_client_secret` | PaymentIntent client secret. | Stripe return. | Third-party dynamic. |
| `redirect_pm_type=paypal` | Return payment method. | Stripe config. | Static/session flag. |
| `redirect_status=failed` | PayPal redirect result. | Stripe/PayPal return. | Response/session dynamic. |
| `ui_mode=custom` | Checkout UI mode. | Stripe config. | Static. |
| URL fragment | Stripe client-side state. | Stripe JS. | Dynamic. |

Relation: this page is loaded after Hermes/fallback and marks PayPal redirect failure into Stripe/OpenAI.

### Stripe telemetry `POST r.stripe.com/0` and `/b`

Representative IDs: `103548#612` through `#619`, plus many `/b` packets.

Fields include:

- `event_name`, `client_id`, `created`, `batching_enabled`, `event_count`.
- Browser fields: `os`, `browserFamily`, `browserClassification`, `connection_rtt`, `connection_downlink`, `connection_effective_type`.
- SDK/config fields: `version`, `team_identifier`, `deploy_status`, `publishable_key`, `key`, `livemode`, `routing`, `request_surface`.
- Session/event fields: `event_id`, `session_id`, `eid`, `logger_id`.
- Redirect fields: `payment_method_from_redirect`, `returned_from_redirect`, `status_from_redirect`, `ui_mode`, `from_server`, `use_cookies`.

Classification: SDK static/config plus third-party dynamic event/session fields. Not strict duplicates because `event_id`, `created`, and event-specific dimensions differ.

### Stripe metrics `POST m.stripe.com/6`

Representative IDs: `103548#684`, `#715`, `#716`, `#717`.

Body is encoded telemetry containing m/s ids, URL, package version tags, browser metrics, and Stripe session identifiers. It is SDK-generated and dynamic.

## Google, GTM, Maps, OneGoogle

### GTM / marketing scripts

Examples:

- `GET www.googletagmanager.com/gtm.js?id=GTM-T562K64Q&l=gtmMktgDataLayer`
- PayPalObjects marketing scripts: `mktgtagmanager.js`, `mktconf.js`, `gtm-bootstrap.js`.

Fields:

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `id=GTM-T562K64Q` | GTM container id. | PayPal page config. | Static/deployment config. |
| `l=gtmMktgDataLayer` | Data layer name. | PayPal page config. | Static. |

Several script loads are strict duplicates and listed in `07_STRICT_DUPLICATES.md`.

### Google Maps/Places

Observed through Google script URLs and address-autocomplete dependencies. Fields like `callback`, `key`, `libraries=places`, and `loading=async` are SDK config/static per deployment; the address query itself is in PayPal GraphQL `AddressAutocompleteFromPostalCodeQuery`.

### OneGoogle async RPC

Representative strict duplicate group:

```text
POST https://ogads-pa.clients6.google.com/$rpc/google.internal.onegoogle.asyncdata.v1.AsyncDataService/GetAsyncData
IDs: 102145#403, 102145#1036, 103548#740
```

This is a Google/OneGoogle async-data packet triggered by Google assets/widgets. In strict duplicate calculation, these three were completely identical in full outbound/inbound fingerprint and can be skipped after one representative.

## Static resources and exact duplicates

Strict duplicate requests are mostly static resources:

- hCaptcha static iframe HTML and hsw/script files.
- reCAPTCHA static JS/CSS/image/font resources.
- Google logo/static resources.
- PayPalObjects captcha/authchallenge scripts.
- DDBM static `tags.js`.
- Datadog browser agent JS.

Static asset fields:

| Field | Meaning/use | Static/dynamic |
| --- | --- | --- |
| Asset URL path/hash | Selects exact deployed resource. | Build/resource static. |
| Cache headers (`etag`, `cache-control`, `x-cache`, `cf-cache-status`) | CDN/cache state. | Response-derived; may vary by edge. |
| Response body sha | Exact asset content identity. | Static for that deployment. |
| Query `dpl=1`, version path segments | Deployment/version selectors. | Build static. |

Skip rule: if method, URL, request headers, body sha, status, response headers, and response sha all match, the packet is listed only in `07_STRICT_DUPLICATES.md` and does not need a separate semantic analysis.
