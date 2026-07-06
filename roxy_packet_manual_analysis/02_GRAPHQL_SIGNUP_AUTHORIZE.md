# 02. GraphQL, idapps, signup, card/profile fields, and authorize packets

## Packet groups covered

| Operation / packet family | IDs | Duplicate decision |
| --- | --- | --- |
| `DeferredFeature` | `102145#913`, `103548#244` | Same operation, not identical: token differs, body sha differs. |
| `GriffinMetadataQuery` | `102145#924`, `103548#255` | Body sha identical; response differs by capture/session metadata. Analyze once. |
| `CheckoutSessionDataQuery` | `102145#925`, `103548#256` | Same operation, token/session and response differ. |
| `SupportedFundingSourcesQuery` | `102145#932`, `103548#268` | Same operation, token/user country body differs. |
| `idapps/graphql getOtpChallengeOperation` | `103548#297` | Unique. |
| `InstallmentOptionsQuery` | `103548#364` | Unique; contains card number/card type. |
| `AddressAutocompleteFromPostalCodeQuery` | `103548#379` | Unique; contains postal code/address lookup. |
| `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` | `103548#400`, `#415`, `#433`, `#447`, `#456`, `#466` | Same operation but repeated attempts; phone/body sha differs in several packets, two body shas repeat. Not globally identical. |
| `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` | `103548#476` | Unique; consumes OTP and challenge IDs. |
| `SignUpNewMemberMutation` | `103548#478` | Unique; highest-value signup body. |
| `authorize` | `103548#540` | Unique final billing authorization. |

## Common GraphQL packet structure

| Field/header | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `operationName` | Selects the GraphQL operation. | PayPal client bundle / query URL. | Static per operation. |
| `query` | GraphQL document text. | PayPal JS bundle. | Build/static. |
| `variables` | Operation input. | Mix of URL state, HTML bootstrap, user input, previous responses. | Mostly dynamic. |
| `fn_sync_data` top-level | FraudNet/device sync payload. | Fraud/risk JS runtime. | Risk dynamic. |
| `PayPal-Client-Context` header | Current EC/BA token. | Signup HTML/session state. | Session dynamic. |
| `PayPal-Client-Metadata-Id` header | Client metadata/correlation id. | PayPal HTML/JS. | Session dynamic. |
| `X-App-Name` | GraphQL client app id. | PayPal frontend config. | Static per app/operation. |
| `X-Country`, `X-Locale` | Buyer locale routing. | Browser/profile/page state. | Page/session dynamic. |
| `X-PayPal-Internal-EUAT` | Auth token after signup. | Signup response. | Response-derived dynamic. |

## Warm-up GraphQL operations

### `DeferredFeature` (`102145#913`, `103548#244`)

Variables:

| Variable | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `channel` | Checkout channel/experience selector. | Signup page state. | Page dynamic. | Controls feature eligibility. |
| `countryCodeAsString` | Country code as string. | Locale/page state. | Page dynamic. | Usually `BR`. |
| `integrationType` | Integration/config type. | PayPal frontend config. | Static/page config. | Used for experiment and feature gating. |
| `isBaslAsString` | Billing-agreement/signup-lite flag. | Checkout context. | Static/session flag. | Indicates BA signup-lite branch. |
| `isForcedGuest` | Guest checkout flag. | ModXO redirect state. | Static/session flag. | Affects guest/signup eligibility. |
| `token` | EC token. | Signup URL `token=EC-...`. | Session dynamic. | Used by all signup GraphQL calls. |

Response: feature/treatment data used by the client to decide UI/flow behavior.

### `GriffinMetadataQuery` (`102145#924`, `103548#255`)

Variables: `countryCode`, `languageCode`, `shippingCountryCode`.

Use: returns country/language metadata, labels, validation rules, address format, and localized signup constraints. The request body sha is identical across both captures because the locale/country tuple is the same; response can still carry session/cache headers.

### `CheckoutSessionDataQuery` (`102145#925`, `103548#256`)

Variable: `token`.

Response fields include `checkoutSession.*`, merchant metadata, funding source availability, cart/order intent, allowed card issuers, and buyer/session state. These response fields feed later UI decisions and final authorize expectations.

### `SupportedFundingSourcesQuery` (`102145#932`, `103548#268`)

Variables: `token`, `userCountry`.

Use: determines which funding sources and payment methods are available for the current EC token and buyer country.

## idapps GraphQL OTP/challenge packet

Representative: `103548#297` (`POST /idapps/graphql`, operation `getOtpChallengeOperation`).

Top-level fields: `operationName`, `query`, `csrfNonce`, `variables`, `fn_sync_data`.

| Field | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `csrfNonce` | CSRF nonce for identity app GraphQL. | idapps page/challenge bootstrap. | Page/session dynamic. | Required by idapps endpoint. |
| `variables.clientInfo.fnId` | FraudNet id. | Fraud/risk runtime. | Risk dynamic. | Correlates identity challenge with browser fingerprint. |
| `variables.clientInfo.ctxId` | Checkout context. | `/pay` HTML/page state. | Page dynamic. | Binds idapps challenge to checkout. |
| `variables.clientInfo.rData` | Nested risk/device data. | Browser/risk JS. | Device/risk dynamic. | Used by identity risk scoring. |
| `variables.credentials.credentialValue` | Credential value, e.g. email/phone depending type. | User input/page state. | User dynamic. | Starts OTP challenge. |
| `variables.credentials.credentialType` | Credential type selector. | Client app. | Static/user dynamic. | Tells idapps what credential is submitted. |
| `variables.challengeInfo.autoSmsOtp` | OTP behavior flag. | Client config. | Static/session flag. | Controls SMS OTP auto behavior. |
| `fn_sync_data` | FraudNet payload. | Risk JS. | Risk dynamic. | Same family as signup risk field. |

Response: HTML-like response body in capture; used to drive challenge UI/state.

## Card, address, phone, OTP, and signup operations

### `InstallmentOptionsQuery` (`103548#364`)

Variables: `buyerCountry`, `cardNumber`, `cardType`, `token`.

| Variable | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `buyerCountry` | Country for installment eligibility. | Profile/page state. | Page/session dynamic. |
| `cardNumber` | Card PAN submitted for installment/card eligibility lookup. | User/test card input. | User input dynamic. |
| `cardType` | Network classification such as Visa/Mastercard. | Derived by client from card number/BIN. | User-derived dynamic. |
| `token` | EC token. | Signup URL. | Session dynamic. |

Use: checks installment eligibility and card-network behavior before signup/card submission. This is not a static packet even if the same card is reused, because token and card input bind it to a current session.

### `AddressAutocompleteFromPostalCodeQuery` (`103548#379`)

Variables: `country`, `postalCode`, `token`.

Use: converts a postal code into address suggestions/metadata. `postalCode` is user/address input; response feeds billing/shipping address fields in signup.

### `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` (`103548#400/#415/#433/#447/#456/#466`)

Variables: `locale`, `phoneCountry`, `phoneNumber`, `token`.

| Variable | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `locale` | Country/language object for OTP messaging. | Page/profile state. | Page dynamic. |
| `phoneCountry` | Phone country ISO. | User/profile. | User/page dynamic. |
| `phoneNumber` | Phone number to receive OTP. | User/test phone input. | User input dynamic. |
| `token` | EC token. | Signup URL. | Session dynamic. |

These packets repeat because the flow can resend/change phone. They are **not all exact duplicates**: multiple request-body shas are observed, so phone/timing/session data changed. Response produces `authId`, `challengeId`, and `state=PENDING`.

### `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` (`103548#476`)

Variables: `authId`, `challengeId`, `pin`, `token`.

| Variable | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `authId` | OTP auth transaction id. | Initiate response. | Response-derived dynamic. | Must match initiate response. |
| `challengeId` | OTP challenge id. | Initiate response. | Response-derived dynamic. | Must match initiate response. |
| `pin` | SMS OTP code. | User input. | User dynamic. | Verifies phone ownership. |
| `token` | EC token. | Signup URL. | Session dynamic. | Binds confirmation to checkout. |

Response confirms phone challenge, enabling signup.

### `SignUpNewMemberMutation` (`103548#478`)

Top-level: `operationName`, `variables`, `query`, `fn_sync_data`.

Variables observed: `card`, `country`, `email`, `firstName`, `lastName`, `phone`, `supportedThreeDsExperiences`, `token`, `billingAddress`, `shippingAddress`, `contentIdentifier`, `marketingOptOut`, `password`, `dateOfBirth`, `identityDocument`, `crsData`, `legalAgreements`.

| Variable | Meaning/use | Source/generation | Static/dynamic | Relation |
| --- | --- | --- | --- | --- |
| `card.number` / `card.*` | Payment card details and metadata. | User/test card input; card type derived by client. | User dynamic. | Used to create PayPal account funding instrument; can trigger Hermes fallback/error. |
| `email` | New PayPal account login/email. | User/test profile input. | User dynamic. | Account identity. |
| `password` | New account password. | User/test profile input. | User dynamic. | Credential creation. |
| `firstName`, `lastName` | Account holder names. | User/test profile input. | User dynamic. | KYC/profile creation. |
| `phone` | Phone object with country/national number. | User/test phone input and OTP flow. | User dynamic/response-confirmed. | Must align with OTP confirmation. |
| `dateOfBirth` | Date of birth. | User/test profile input. | User dynamic. | KYC/account eligibility. |
| `identityDocument` | Brazil CPF/identity document. | User/test profile input. | User dynamic. | Country-specific compliance/KYC. |
| `billingAddress`, `shippingAddress` | Address objects. | User/test address plus autocomplete response. | User dynamic/response-derived. | Billing/shipping identity and card checks. |
| `country` | Signup country. | Page/profile state. | Page dynamic. | Controls compliance and address schema. |
| `supportedThreeDsExperiences` | Client 3DS capability, observed as iframe-style capability. | Client bundle constant. | Static capability flag. | Tells server what 3DS UX can be used. |
| `token` | EC token. | Signup URL. | Session dynamic. | Binds signup to checkout. |
| `contentIdentifier` | Terms/content identifier. | Derived from signup HTML/content manifest. | Build/page dynamic. | Required legal terms identifier. |
| `marketingOptOut` | Marketing consent choice. | User choice/default. | User/static choice. | Stored on account. |
| `legalAgreements` | Legal agreement flags/object. | Signup UI. | User/static choice. | Required for signup. |
| `crsData` | Compliance/tax residence data. | Country/profile compliance logic. | User/page dynamic. | Compliance payload. |
| `fn_sync_data` | FraudNet/device sync payload. | Risk JS/runtime. | Risk dynamic. | Server-side fraud assessment. |

Response relation:

- Produces buyer/account state and may produce an access token / internal auth state used by subsequent authorize headers.
- In this capture the later `/webapps/hermes` URL contains `reason=Q0FSRF9HRU5FUklDX0VSUk9S`, indicating signup/card path fell back with a card generic error.

## Final authorize (`103548#540`)

Endpoint: `POST https://www.paypal.com/graphql/`, operation `authorize`.

Variables: `billingAgreementId`, `fundingPreference`, `legalAgreements`.

| Variable | Meaning/use | Source/generation | Static/dynamic |
| --- | --- | --- | --- |
| `billingAgreementId` | BA token to authorize/finalize. | Original approval token / session state. | Session dynamic. |
| `fundingPreference.balancePreference` | Balance-use preference. | Client config/user setting. | Static/session flag. |
| `legalAgreements` | Legal agreement object. | UI/user consent. | Static/user choice. |

Response produces `billing.authorize.*`, including authorization state, billing agreement token, buyer info, and return URL. That return URL leads out to merchant/Stripe/OpenAI.
