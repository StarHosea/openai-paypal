# Real `dfp.js` runtime vs Python MTR diff

Generated at: 2026-07-06T11:55:02.170229+00:00

This run executes the captured PayPal `dfp.js` inside Chromium with a mocked PayPal page, intercepted `/mtr/.../x0` bootstrap, and intercepted MTR response. It then builds the Python `paypal.mtr` payload from the same runtime profile and compares decoded JSON payloads field-by-field.

## Artifacts

- Output dir: `/home/nonewhite/paypal-pay/captures/mtr-js-vs-python-latest`
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
| Real JS body bytes | 3007 |
| Python body bytes | 3025 |
| Python body bytes with JS overrides | 3025 |
| JS top-level keys | 135 |
| Python top-level keys | 135 |
| Common keys | 135 |
| Equal keys | 135 |
| Changed keys | 0 |
| JS `s*` signals | 127 |
| Python `s*` signals | 127 |
| `s*` status differences | 0 |
| `s*` value differences | 0 |

Only in JS: `none`

Only in Python: `none`

## JS runtime override replay

This replay keeps the Python fallback structure but feeds the changed JS-captured signal/top-level values back through `mtr_signal_overrides` and `mtr_payload_overrides`. It measures whether the fallback can accept browser-runtime values when a real browser collector provides them.

| Metric | Value |
|---|---:|
| Override signal keys | 0 |
| Override top-level keys | 0 |
| Equal keys after overrides | 135 |
| Changed keys after overrides | 0 |
| `s*` value differences after overrides | 0 |

Signal overrides: `none`

Top-level overrides: `none`

### Remaining changed fields after JS overrides

| Field | JS | Python |
|---|---|---|
| none | same | same |

## Previously targeted fields

| Field | Result | JS | Python |
|---|---|---|---|
| `ab` | equal | same | same |
| `s4` | equal | same | same |
| `s5` | equal | same | same |
| `s49` | equal | same | same |
| `s58` | equal | same | same |
| `s84` | equal | same | same |
| `s94` | equal | same | same |
| `s131` | equal | same | same |
| `s145` | equal | same | same |
| `s150` | equal | same | same |

## First changed fields

| Field | JS | Python |
|---|---|---|
| none | same | same |
