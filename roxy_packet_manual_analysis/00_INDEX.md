# Roxy packet manual analysis index

Scope is only these two capture directories:

- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-102145`
- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-103548`

This analysis uses `network/events.jsonl` as the source of truth and `network/requests.tsv` only as the completed-response/body index.

## Counts and duplicate policy

| Capture | request events parsed | response events parsed | failed events parsed | bad JSONL rows |
| --- | ---: | ---: | ---: | ---: |
| `roxy-paypal-20260705-102145` | 1036 | 772 | 199 | 0 |
| `roxy-paypal-20260705-103548` | 746 parsed / 750 summary | 579 parsed / 583 summary | 150 | 8 |
| Total parsed | 1782 | 1351 | 349 | 8 |

I used two duplicate levels:

1. **Strict complete duplicate**: method, full URL, request headers, request body sha256, response status, response headers, response body sha256, resource type, and failure object are all identical. These are safe to skip after one representative explanation. Result: **36 strict duplicate groups, 130 requests**.
2. **Same endpoint shape but not identical**: same endpoint family or resource pattern, but dynamic values differ. These are **not** skipped. They are analyzed by packet family below.

The strict duplicate appendix is `07_STRICT_DUPLICATES.md`.

## Completed manual analysis documents

| Doc | Covers | Why grouped |
| --- | --- | --- |
| `01_NAVIGATION_NEXTJS.md` | PayPal page navigation, `/pay` server actions, signup/Hermes/OpenAI return | These packets carry route/query/session state and generate the later request variables. |
| `02_GRAPHQL_SIGNUP_AUTHORIZE.md` | PayPal GraphQL, idapps GraphQL, OTP, signup, card/address/profile fields, authorize | GraphQL bodies carry the highest-value user/session fields and response-derived IDs. |
| `03_AUTH_CAPTCHA.md` | `/auth/*`, hCaptcha, reCAPTCHA challenge packets | Challenge packets are repeated but token/timing/proof values are dynamic. |
| `04_RISK_FRAUDNET.md` | `c.paypal.com/v1/r/d/b/*`, `c6.paypal.com`, DDBM, device/risk fields | These packets generate browser/device/fraud variables from JS/runtime. |
| `05_TELEMETRY_OBSERVABILITY.md` | FPTI, observability, XO logger, Tealeaf, Datadog | Many repeats are telemetry events; not exact duplicates because timestamps/event IDs/payloads differ. |
| `06_THIRD_PARTY_ASSETS.md` | Stripe return/telemetry, Google/GTM/Maps/static assets | Static resources can be skipped when strict duplicate; Stripe return state is dynamic. |
| `07_STRICT_DUPLICATES.md` | Strict complete duplicate groups | I already decided these packets can be skipped individually because the full packet fingerprint matches. |
| `08_FINAL_MANUAL_ANALYSIS_CONCLUSIONS.md` | Final conclusion in Chinese | Direct answer: my completed packet-family conclusions, sensitive/long-field meanings, and packet dependency chain. |
| `09_PACKET_DECISION_TABLE.md` | Every parsed request id | Per-request decision: analyzed by which doc, or skipped as strict duplicate with representative id. |

## What I already analyzed

For each non-strict-duplicate packet family, I already analyzed:

- packet IDs and representative source files;
- fields sent in URL/query/header/body;
- field meaning and use;
- source/generation path from HTML, JS, response, SDK runtime, browser APIs, or user input;
- static vs dynamic classification;
- relation to previous/next packets.

You do not need to decide which packets are duplicates: `07_STRICT_DUPLICATES.md` contains the strict duplicate groups, and `09_PACKET_DECISION_TABLE.md` maps every parsed request id to my decision. `08_FINAL_MANUAL_ANALYSIS_CONCLUSIONS.md` is the main conclusion document.

## Per-packet document output

I generated one Markdown document for every parsed request packet:

- Per-packet index: `packets/INDEX.md`
- Capture 1 packet docs: `packets/roxy-paypal-20260705-102145/` (1036 docs)
- Capture 2 packet docs: `packets/roxy-paypal-20260705-103548/` (746 docs)
- Generation summary: `packets/generation_summary.json`
- Reproducible generator: `generate_per_packet_docs.py`

Each packet doc contains: decision, packet family, duplicate status, request/response summary, body/header/query field tables, static-vs-dynamic classification, source/generation inference, response analysis, HTML/JS evidence, and same-value links to related packets.
