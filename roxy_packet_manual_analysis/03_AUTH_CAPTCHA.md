# 03. Auth challenge, hCaptcha, and reCAPTCHA packets

## Packet groups covered

| Packet family | Representative IDs | Duplicate decision |
| --- | --- | --- |
| `POST /auth/logclientdata` | `102145#501`, `#530`, `#535`, `#544`, `#553`, `#560`, `#564`, `#581` | Same endpoint but dynamic FPTI/captcha state changes; not strict duplicates. |
| `POST /auth/verifyhcaptchapassive` | `102145#108`, `#299`, `#480`, `#836`, `103548#99` | Same form keys but token/timestamps/session differ. |
| `POST /auth/validatecaptcha` | `102145#547`, `#584`, `#612`, `#648`, `#679`, `#698`, `#699`, `#700` | Different hCaptcha/reCAPTCHA branches and different challenge values; not duplicates. |
| `POST *hcaptcha*/checksiteconfig` | `102145#93`, `#99`, `#275`, `#296`, `#471`, `#538`, `#565`, `#780` | Same shape, response config differs per host/sitekey/session. |
| `POST api.hcaptcha.com/getcaptcha/...` | `102145#106`, `#289`, `#478`, `#542`, `#789`, `103548#98`, `#743`, `#744` | Binary encrypted payloads differ by challenge and sitekey. |
| `GET recaptcha enterprise anchor` | `102145#600`, `#624`, `#659`, `103548#318`, `#340` | Same shape, sitekey/session/challenge frame differ. |
| `POST recaptcha enterprise reload` | `102145#613`, `#637`, `#673`, `103548#325` | Binary/proof payload differs. |

## `/auth/logclientdata`

Request body top-level: `fpti`, `_csrf`, `_sessionID`.

| Field | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `_csrf` | CSRF token for auth challenge endpoint. | Auth challenge HTML/form bootstrap. | Page/session dynamic. | Required by `/auth/validatecaptcha` and `/auth/verifyhcaptchapassive`. |
| `_sessionID` | Auth challenge session id. | Auth challenge page. | Page/session dynamic. | Binds all auth challenge calls. |
| `fpti.pgrp`, `fpti.page` | Analytics page group/name. | `PAYPAL.analytics.setup` / challenge HTML. | Page dynamic. | Tracks challenge page view. |
| `fpti.comp`, `fpti.tsrce` | Component/source app. | PayPal page config. | Static/page dynamic. | Identifies `authchallengenodeweb` or checkout source. |
| `fpti.pxpguid`, `fpti.csci`, `fpti.nsid`, `fpti.calc` | Session and analytics correlation IDs. | Cookies/bootstrap/JS runtime. | Session dynamic. | Correlates captcha with PayPal analytics. |
| `fpti.rsta`, `fpti.ccpg` | Locale/country. | Page locale state. | Page dynamic. | Challenge locale/routing. |
| `fpti.captchaState`, `fpti.message`, `fpti.space_key`, `xe`, `xt` | Challenge telemetry dimensions. | Captcha JS runtime. | Challenge dynamic. | Captures challenge status/progress. |

Use: telemetry/diagnostic logging for the challenge. It does not itself solve the challenge but records state.

## `/auth/verifyhcaptchapassive`

Form keys: `_csrf`, `hcaptcha_passive_eval_start_time_utc`, `hcaptchaToken`, `publicKey`, `hcaptcha_passive_render_start_time_utc`, `hcaptcha_passive_render_end_time_utc`, `hcaptcha_passive_verification_time_utc`, `_sessionID`.

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `publicKey` | hCaptcha public/site key. | Captcha iframe/page config. | Challenge dynamic. |
| `hcaptchaToken` | Passive hCaptcha proof token. | hCaptcha passive JS executed in browser. | Risk/challenge dynamic. |
| `hcaptcha_passive_*_utc` | Render/eval/verification timings. | Browser JS timestamps. | Challenge/device dynamic. |
| `_csrf`, `_sessionID` | Auth form binding. | Challenge page. | Session dynamic. |

Use: verifies passive hCaptcha before or during challenge flow. Repeats are not identical because the token and timing are per run.

## `/auth/validatecaptcha`

There are two major body shapes.

### hCaptcha branch (`102145#547`, `#584`, `#700`)

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `_csrf`, `_sessionID` | Auth session binding. | Challenge HTML. | Session dynamic. |
| `_requestId` | Opaque challenge request id. | Challenge page/server. | Challenge dynamic. |
| `_hash` | Challenge hash. | Challenge page/server. | Challenge dynamic. |
| `jse` | JS evaluation token. | PayPal captcha JS. | Challenge dynamic. |
| `hcaptchaToken` | hCaptcha solution/proof. | hCaptcha SDK. | Challenge dynamic. |
| `hcaptcha` | Status marker, sometimes `NOT_REACHABLE`. | Captcha JS runtime. | Challenge dynamic. |
| `hcaptcha_eval_start_time_utc` and passive times | Timing telemetry. | Browser JS. | Device/challenge dynamic. |
| `YWRzZGRjb29raWU=` | Base64-looking field name; likely challenge cookie/proof field. | Captcha/ads challenge script. | Challenge dynamic. |

### reCAPTCHA Enterprise branch (`102145#612`, `#648`, `#679`, `#698`, `#699`)

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `_recaptchaEnterpriseEnabled` | Enterprise reCAPTCHA flag. | Challenge page config. | Static/session flag. |
| `_adsRecaptchaSiteKey` | reCAPTCHA site key. | Challenge page/iframe config. | Challenge dynamic. |
| `grc_eval_start_time_utc` | reCAPTCHA eval timing. | Browser JS. | Challenge/device dynamic. |
| `isPolicyBasedChallenge` | Policy challenge branch flag. | PayPal challenge config. | Static/challenge flag. |
| `recaptcha` | reCAPTCHA status/proof marker. | reCAPTCHA SDK. | Challenge dynamic. |

Use: submits the actual challenge result to PayPal. Response can be a new challenge HTML, a failed/aborted navigation, or a successful redirect/action continuation.

## hCaptcha third-party packets

### `checksiteconfig`

Query/body fields in URL: `v`, `host`, `sitekey`, `sc`, `swa`, `spst`.

| Field | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `v` | hCaptcha asset/version hash. | hCaptcha script URL. | Build/static for captcha deployment. |
| `host` | Embedding host (`www.paypalobjects.com`, `hcaptcha.paypal.com`). | iframe context. | Static/page dynamic. |
| `sitekey` | hCaptcha site key UUID. | PayPal captcha page config. | Challenge dynamic. |
| `sc`, `swa`, `spst` | hCaptcha SDK flags. | hCaptcha JS. | Static/challenge config. |
| Response `pass`, `features`, `c.type`, `c.req` | Challenge mode/config and hsw requirement. | hCaptcha service. | Challenge dynamic. |

### `getcaptcha/<sitekey>`

Encrypted/binary request body and binary/opaque response. It requests or refreshes the actual challenge. It is not a duplicate when the sitekey/body/response sha changes.

## reCAPTCHA third-party packets

### `enterprise/anchor`

Key URL fields: `ar`, `k`, `co`, `hl`, `v`, `size`, `cb`.

- `k`: site key from PayPal challenge config.
- `co`: encoded origin, here PayPalObjects origin.
- `hl`: locale (`pt-BR`).
- `v`: reCAPTCHA release version.
- `cb`: per-load callback/cache buster.

### `enterprise/reload`

Query `k` carries the site key. Body is binary/protobuf-like proof/reload data generated by reCAPTCHA JS. Response returns proof/config used by the challenge iframe. Not static and not reusable.
