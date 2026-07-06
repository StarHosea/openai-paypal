# 01. Navigation, PayPal HTML bootstrap, and Next.js `/pay` packets

## Packet groups covered

| Packet family | Representative IDs | Duplicate decision |
| --- | --- | --- |
| `GET /agreements/approve` | `103548#1`, `102145#886`, `103548#222` | Not identical: entry page vs later 302 redirects have different query state and response headers. |
| `GET /pay` | `102145#2`, `102145#206`, `103548#69` redirects/actions around same flow | Not identical: same shape but different BA token, ssrt, ctxId, cookies, response HTML sha. |
| `GET /pay/` | `102145#402`, `102145#490`, `102145#702`, `103548#202` | Not identical: some are documents, one is RSC fetch, query state differs. |
| `POST /pay?...paypal_client_cfci=...` | `102145#75`, `102145#85`, `102145#876`, `103548#190` | Not identical: different server action body fields and outcomes. |
| `GET /pay/api/countries` | `103548#181`, `103548#200` | Not identical: second adds `country.x=BR`. |
| `GET /checkoutweb/signup` | `102145#888`, `103548#223` | Same route shape but different BA/EC tokens and response HTML. |
| `GET /webapps/hermes` | `103548#490`, `103548#493` | Failed/aborted first navigation and completed second navigation. |
| `GET pay.openai.com/c/pay/...` | `103548#556` | Unique Stripe/OpenAI return page after PayPal failure. |

## `GET /agreements/approve`

Representative source: `roxy-paypal-20260705-103548#1`:

```text
https://www.paypal.com/agreements/approve?ba_token=BA-37R61061EU582084R
```

Later redirect form seen as `102145#886` and `103548#222`:

```text
/agreements/approve?ssrt=...&ul=1&modxo_redirect_reason=guest_user&ulOnboardRedirect=true&locale.x=pt_BR&country.x=BR&ba_token=BA-...
```

| Field | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `ba_token` | Billing agreement token. Binds the merchant checkout to PayPal agreement approval. | External merchant/OpenAI/Stripe redirect into PayPal. | Session dynamic. | Reappears in `/pay`, `/checkoutweb/signup`, `/webapps/hermes`, FraudNet correlation, authorize result. |
| `ssrt` | Session/routing timestamp-like PayPal state. | PayPal redirect chain after initial approve. | Session dynamic. | Carried through `/pay`, signup, Hermes, Tealeaf screenview. |
| `ul` | User-login/user-lite flow switch. | PayPal route configuration. | Static/session flag, observed `1`. | Propagates to `/pay`, signup, Hermes. |
| `modxo_redirect_reason` | Reason for redirect into modular checkout guest/signup path. | PayPal server redirect. | Session flag, value `guest_user`. | Explains movement from approval into guest signup. |
| `ulOnboardRedirect` | Marks onboarding redirect path. | PayPal server redirect. | Static/session flag. | Present before signup redirect. |
| `locale.x`, `country.x` | Locale/country routing. | PayPal server/browser locale resolution. | Page/session dynamic. | Controls HTML language, captcha locale, GraphQL country/language variables. |

Response use:

- `200` HTML on `103548#1` is the first visible approval page and contains bootstrap data.
- `302` responses on later IDs produce the next `/pay` URL. `Location` is response-derived and should be followed rather than guessed.
- Response headers set/refresh cookies (`ts`, `ts_c`, `tsrce`, `LANG`, `datadome`, etc.) used by all later requests.

## `GET /pay` and `GET /pay/`

Representative initial page: `102145#2`:

```text
/pay?ssrt=1783218039565&token=BA-65R58767795383013&ul=1
```

Representative context page: `102145#402`:

```text
/pay/?ssrt=1783217574892&token=BA-4PT6003892106623L&ul=1&ctxId=e990d911-884d-4399-9f09-179058bc5c13&country.x=BR
```

Representative RSC fetch: `103548#202`:

```text
/pay/?ssrt=1783218992321&token=BA-37R61061EU582084R&ul=1&ctxId=c5d20d7d-43c4-45fc-b36d-da68a1ca84f5&country.x=BR&_rsc=...
```

| Field | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `token` | Checkout/session token. On `/pay` it can be a BA token; later GraphQL primarily uses EC token. | PayPal redirect URL. | Session dynamic. | Feeds page bootstrap, FraudNet correlation, telemetry. |
| `ctxId` | Checkout UI context UUID. | PayPal page render / server action response. | Page/session dynamic. | Required by `/pay` server actions and RSC updates. |
| `country.x` | Selected/inferred country. | User selection or PayPal locale state. | Page/session dynamic. | Drives country API, signup route, GraphQL locale variables. |
| `_rsc` | React Server Components cache/action key. | Next.js client runtime. | Page/build dynamic. | Used only for RSC fetches; not reusable across sessions. |

HTML/JS generation notes:

- `/pay` HTML response files such as `00002_resp_200_document_...html`, `00402_resp_200_document_...html`, and `00490_resp_200_document_...html` contain `__INITIAL_DATA__`, `PAYPAL.analytics.setup`, `window.PAYPAL`, `ctxId`, `clientMetadataId`, `paypal_client_cfci`, `loggerEndpoint`, `releaseHash`, `contentManifestUrl`/Next.js asset links.
- PayPal JS chunks loaded immediately after `/pay` generate server-action posts, RSC fetches, telemetry, FraudNet calls, and captcha fallback calls.
- Build/resource fields such as `_next/static/chunks/...`, `dpl=1`, `releaseHash`, and deployment IDs are build-scoped static; the token/csrf/context values are dynamic.

## `POST /pay?...paypal_client_cfci=...` Next.js server actions

Representative server action packet `102145#75`:

```text
POST /pay?...&paypal_client_cfci=modxo_vaulted_not_recurring-no_interaction
multipart fields: _1_ctxId, 0
```

Representative submit-email/action packet `102145#85` and `102145#876`:

```text
multipart fields: _1_fn_sync_data, _1_ctxId, _1_passkeyChallenge, _1_rpId,
_1_login_email, _1_login_password, _1_login_phone_country_code, _1_formName, 0
```

Representative captcha-solved continuation `103548#190`:

```text
multipart fields: 1, 0
status=303
```

| Field | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `paypal_client_cfci` query | Client flow/correlation id plus semantic action suffix (`no_interaction`, `Submit_Email`, `CAPTCHA_SOLVED`). | PayPal HTML bootstrap + JS action layer. | Page/session dynamic. | Correlates `/pay` server action with observability/Tealeaf. |
| `Next-Action` header | Next.js server action identifier. | Inline Next.js/HTML action manifest. | Page/build dynamic. | Selects server function to execute. |
| `_1_ctxId` | Checkout context UUID. | From `/pay` bootstrap. | Page/session dynamic. | Must match the active context. |
| `_1_fn_sync_data` | Fraud/device sync payload. | FraudNet JS/runtime. | Risk dynamic. | Later also appears in signup GraphQL. |
| `_1_login_email`, `_1_login_password`, `_1_login_phone_country_code` | Login form inputs. | User/browser form state. | User input dynamic. | If wrong/missing, server returns login/authchallenge HTML. |
| `_1_passkeyChallenge`, `_1_rpId` | Passkey/WebAuthn challenge metadata. | PayPal login/passkey runtime. | Page/challenge dynamic. | Used for passkey-capable login flows. |
| `_1_formName` | Server-side form selector. | Client bundle constant for a form. | Static/page dynamic. | Tells action which form branch was submitted. |
| `0`, `1` multipart fields | Encoded RSC/server action payload references. | Next.js action protocol. | Page/action dynamic. | Response can be RSC text, HTML challenge, or 303 redirect. |

Response relation:

- `200 text/plain` responses are RSC payloads or action results.
- `200 text/html` responses indicate page/challenge HTML returned through an action.
- `303` carries a redirect `Location` to the next route; `Location` is response-derived.

## `GET /pay/api/countries`

Representative packets: `103548#181`, `103548#200`.

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `paypal_client_cfci` | Current flow/action correlation. | `/pay` HTML/JS. | Page/session dynamic. |
| `country.x=BR` | Optional filter/selected country. | User/page locale state. | Page/session dynamic. |
| Response array `key`, `labelText` | Country selector catalog. | PayPal API. | Mostly static catalog for deployment/locale. |

## `GET /checkoutweb/signup`

Representative packets: `102145#888`, `103548#223`.

```text
/checkoutweb/signup?ssrt=...&ul=1&modxo_redirect_reason=guest_user&locale.x=pt_BR&country.x=BR&ba_token=BA-...&token=EC-...&rcache=1&cookieBannerVariant=hidden
```

| Field | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `ba_token` | Original billing agreement token. | Carried from approve/pay. | Session dynamic. | Used to bind signup to original agreement. |
| `token=EC-...` | EC checkout token. | PayPal redirect/server action response. | Session dynamic. | Used as GraphQL `variables.token`. |
| `rcache=1` | Routing/cache flag. | PayPal route config. | Static/session flag. | Passed through Hermes. |
| `cookieBannerVariant=hidden` | Cookie banner experiment/config. | PayPal page config. | Static/session flag. | Telemetry dimension. |
| `locale.x`, `country.x` | Locale/country for signup page. | PayPal route state. | Page/session dynamic. | Feeds content manifest and GraphQL locale. |

HTML/JS source:

- Signup HTML contains `contentManifestUrl`, `contentHash`, `__INITIAL_DATA__`, `__APPLICATION_METADATA__`, `loggerEndpoint`, `ecToken`, `clientMetadataId`, `fn_sync_data`, captcha markers, FPTI setup, and Weasley JS chunk URLs.
- `contentIdentifier` for signup terms is derived from country/language plus the content manifest hash; it is used later by `SignUpNewMemberMutation`.

## `GET /webapps/hermes`

Representative completed packet: `103548#493`; aborted/failed first attempt: `103548#490`.

```text
/webapps/hermes?ssrt=...&ul=1&modxo_redirect_reason=guest_user&locale.x=pt_BR&country.x=BR&ba_token=BA-...&token=EC-...&rcache=1&cookieBannerVariant=hidden&fromSignupLite=true&fallback=1&reason=Q0FSRF9HRU5FUklDX0VSUk9S
```

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `fromSignupLite=true` | Indicates Hermes was reached from signup-lite. | PayPal server redirect. | Static/session flag. |
| `fallback=1` | Fallback path selected. | PayPal server redirect. | Static/session flag. |
| `reason=Q0FSRF9HRU5FUklDX0VSUk9S` | Encoded fallback reason. Decodes semantically to a card generic error marker. | PayPal signup/payment response. | Response/session dynamic. |
| `billingLite=1` fragment path later | Billing-lite review UI. | Hermes client/router. | Static/session flag. |

Relation: Hermes is reached after signup/card flow. It reloads PayPal logger/FPTI/FraudNet and then sends final `/graphql/` authorize.

## `GET pay.openai.com/c/pay/...` Stripe/OpenAI return

Representative packet: `103548#556`.

Fields observed:

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `cs_live_...` path segment | Stripe Checkout session path. | Stripe/OpenAI checkout. | Third-party session dynamic. |
| `lid` | Stripe logger/link id. | Stripe SDK. | Third-party dynamic. |
| `payment_intent` | Stripe PaymentIntent id. | Stripe return URL. | Third-party dynamic. |
| `payment_intent_client_secret` | Client secret for PaymentIntent. | Stripe return URL. | Third-party dynamic. |
| `redirect_pm_type=paypal` | Payment method type. | Stripe return config. | Static/session flag. |
| `redirect_status=failed` | Redirect outcome. | Stripe/PayPal return result. | Response/session dynamic. |
| `ui_mode=custom` | Stripe Checkout UI mode. | Stripe config. | Static config. |
| URL fragment `#fid...` | Stripe client-side state fragment. | Stripe JS. | Third-party dynamic. |

Relation: this is outside PayPal but marks final failure return after PayPal/Hermes path.
