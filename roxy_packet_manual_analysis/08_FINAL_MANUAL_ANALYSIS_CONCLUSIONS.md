# 08. 最终手动分析结论

这份文档是我对两个 roxy 抓包的结论，不是让你继续分析的索引。

分析范围只包含：

- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-102145`
- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-103548`

## 1. 总体结论

两个抓包一共解析到 **1782 个 request event**。其中：

- **严格完全重复**：36 组，覆盖 130 个请求。这些我判断为可以跳过逐包展开，原因是 method、完整 URL、headers、request body sha、status、response headers、response body sha、failure object 都完全一致。
- **同类但不完全重复**：126 个同类重复组，覆盖 1635 个请求。这些不能按“重复”跳过，因为至少有 URL、query、headers、body sha、response sha、状态码或 failure 差异。
- **真正需要语义分析的核心包族**：PayPal 导航/Next.js action、GraphQL/signup/authorize、auth captcha、FraudNet、FPTI/Tealeaf/logger/Datadog、Stripe/Google/静态资源。

最终判断：

1. `BA-*`、`EC-*`、Stripe client secret、email、phone、password、card、CPF、OTP、长数字字段都不是“假的信息”。它们都是真实抓包中的字段，只是语义不同。
2. `BA-*` / `EC-*` 是 PayPal 会话/协议状态核心字段。它们不能当静态值；它们由 PayPal/商户重定向和服务端响应生成，并贯穿 URL、GraphQL、FraudNet、FPTI、authorize。
3. Stripe `payment_intent_client_secret` 是第三方 Stripe 回跳状态，不属于 PayPal 生成，但它解释了 PayPal 失败后如何返回 `pay.openai.com`。
4. email、phone、password、card、CPF、OTP 是用户输入/测试资料字段，不是 SDK 自动静态值。它们出现在 GraphQL signup、OTP、card eligibility、Tealeaf DOM/event capture 中。
5. 长数字有多类：timestamp、OTP challenge id、auth id、tracking id、Tealeaf id、Datadog/Stripe event id、captcha request id，不能统一判断为“随机长数字”。

## 2. 抓包一 `roxy-paypal-20260705-102145` 的流程结论

这个抓包主要展示 PayPal `/pay` 页面、多次 auth/captcha 挑战、后续进入 signup warmup 的路径。

关键链路：

1. `#2 GET /pay?ssrt=1783218039565&token=BA-65R58767795383013&ul=1`
   - 这是初始 PayPal checkout 页面。
   - `token=BA-...` 是入口 billing agreement token。
   - HTML 生成后续 `ctxId`、`paypal_client_cfci`、FPTI、FraudNet、Next.js action 所需变量。
2. `#75 POST /pay?...paypal_client_cfci=...-no_interaction`
   - Next.js server action。
   - body 只有 `_1_ctxId` 和 `0`，表示无用户交互初始化 action。
   - 依赖 `#2` HTML/JS 中的 action id 和 context。
3. `#85 POST /pay?...paypal_client_cfci=...-Submit_Email`
   - 登录/提交邮箱路径。
   - body 包含 `_1_fn_sync_data`、`_1_login_email`、`_1_login_password`、`_1_login_phone_country_code` 等。
   - 这说明 email/password/phone country 不是假的，它们是提交给 PayPal action 的表单字段。
4. `#501` 起的 `/auth/logclientdata`、`#547/#584/#612/#648/#679` 等 `/auth/validatecaptcha`
   - 说明该 flow 被引入 authchallenge。
   - `_csrf`、`_sessionID`、`_requestId`、`_hash`、`jse`、captcha token/timing 都是动态挑战状态。
5. `#888 GET /checkoutweb/signup?...token=EC-4B298567BY525841A...`
   - 说明后续已拿到 EC token 并进入 signup 页面。
   - 此页面 HTML/JS 生成 signup GraphQL 所需的 `contentIdentifier`、`ecToken`、client metadata、logger config、FPTI 等。
6. `#913/#924/#925/#932` GraphQL warmup
   - 依次是 `DeferredFeature`、`GriffinMetadataQuery`、`CheckoutSessionDataQuery`、`SupportedFundingSourcesQuery`。
   - 它们不是重复资源包；它们拉取功能开关、locale metadata、checkout session、funding sources。

抓包一没有完整走完 `SignUpNewMemberMutation -> Hermes -> authorize -> merchant return`；它更多体现 challenge 和 signup 前置 warmup。

## 3. 抓包二 `roxy-paypal-20260705-103548` 的流程结论

这个抓包展示更完整的链路：approve -> pay -> captcha solved -> signup -> GraphQL signup -> Hermes -> authorize -> Stripe/OpenAI return failed。

关键链路：

1. `#1 GET /agreements/approve?ba_token=BA-37R61061EU582084R`
   - 初始 approve 页面。
   - `ba_token` 是全链路主 token。
2. `pages.jsonl` 显示跳转到 `/pay?ssrt=1783218992321&token=BA-37R61061EU582084R&ul=1`
   - `ssrt` 是 PayPal 生成的会话路由值。
3. `#190 POST /pay?...paypal_client_cfci=...-CAPTCHA_SOLVED` 返回 303
   - captcha 成功后的 Next.js action continuation。
   - body 只有 `1`、`0`，代表 RSC/action 协议 payload。
4. `#223 GET /checkoutweb/signup?...ba_token=BA-37R61061EU582084R&token=EC-79N40798NE0202548...`
   - 进入 signup 页面。
   - `EC-79...` 后续成为 GraphQL `variables.token`。
5. `#244/#255/#256/#268` GraphQL warmup
   - 获取 feature、locale、checkout session、funding source。
6. `#297 POST /idapps/graphql`
   - body 包含 `csrfNonce`、`variables.clientInfo.*`、`credentials.*`、`challengeInfo.*`、`fn_sync_data`。
   - 这是身份/OTP/risk challenge 相关 GraphQL，不是普通静态请求。
7. `#364 InstallmentOptionsQuery`
   - body 包含 `cardNumber`、`cardType`。
   - 用于卡 BIN/分期/卡种资格判断。
8. `#379 AddressAutocompleteFromPostalCodeQuery`
   - body 包含 `postalCode`、`country`。
   - 用于地址自动补全。
9. `#400/#415/#433/#447/#456/#466 InitiateRiskBasedTwoFactorPhoneConfirmationMutation`
   - 多次发起手机号 OTP。
   - body 中 `phoneNumber` 变化导致多个 body sha 不同；其中 `#447/#456` body sha 相同，属于同一输入的重复发送，但不是和所有 Initiate 包完全重复。
10. `#476 ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`
    - 使用 `authId`、`challengeId`、`pin`、`token`。
    - `authId/challengeId` 来自 initiate response；`pin` 是用户 OTP。
11. `#478 SignUpNewMemberMutation`
    - 最核心 signup 包。
    - body 包含 `card`、`email`、`firstName`、`lastName`、`phone`、`billingAddress`、`shippingAddress`、`contentIdentifier`、`password`、`dateOfBirth`、`identityDocument`、`crsData`、`legalAgreements`、`fn_sync_data`。
    - 这些字段都是真实提交语义字段，不是伪数据字段。
12. `#493 GET /webapps/hermes?...reason=Q0FSRF9HRU5FUklDX0VSUk9S...`
    - Hermes/fallback 页面。
    - `reason` 是服务端返回的 fallback/error reason；语义上对应 card generic error。
13. `#540 POST /graphql/ authorize`
    - final billing authorize。
    - body `billingAgreementId` 复用 BA token，`fundingPreference` 和 `legalAgreements` 控制最终授权。
14. `#556 GET pay.openai.com/c/pay/...redirect_status=failed...`
    - Stripe/OpenAI return 页面。
    - `payment_intent_client_secret`、`payment_intent`、`redirect_status=failed` 说明 PayPal 路径最终返回 Stripe 且失败。

## 4. 我判断为“严格完全重复，可以跳过”的包

这些包已经在 `07_STRICT_DUPLICATES.md` 中逐组列出。它们主要是：

- hCaptcha iframe/static JS：如 `newassets.hcaptcha.com/.../hcaptcha.html`、`hsw.js`。
- reCAPTCHA 静态资源：如 `recaptcha__pt_br.js`、`styles__ltr.css`、logo/refresh/audio/info 图片。
- Google 静态资源：OneGoogle JS/CSS、Google logo。
- PayPalObjects 静态 iframe/script/font：`hcaptchapassive.html`、`authchallenge.js`、PayPal fonts。
- DDBM static `tags.js`。
- Datadog browser agent JS。
- Stripe deploy status probe。
- OneGoogle async RPC 的 3 个失败请求，完全相同。

跳过理由不是“看起来相似”，而是严格 fingerprint 完全一致。除此以外的同 endpoint 包不跳过。

## 5. 我判断为“同类但不能跳过”的重复包

这些包数量很多，但我不把它们当完全重复：

| 包族 | 为什么不能跳过 | 已分析文档 |
| --- | --- | --- |
| Datadog RUM | 每个 body 的 `date`、`event_id`、view/action/resource/error payload 不同。 | `05_TELEMETRY_OBSERVABILITY.md` |
| Datadog Replay | multipart replay segment 内容不同。 | `05_TELEMETRY_OBSERVABILITY.md` |
| FPTI `t.paypal.com/ts` | query 内 timestamp、page、event、correlation id、screen/timing 不同。 | `05_TELEMETRY_OBSERVABILITY.md` |
| Observability | `json[].eventName`、analytics、logs、metrics 不同。 | `05_TELEMETRY_OBSERVABILITY.md` |
| XO Logger | events/metrics payload 不同。 | `05_TELEMETRY_OBSERVABILITY.md` |
| Tealeaf | DOM/session replay body 动态，包含输入/点击/页面状态。 | `05_TELEMETRY_OBSERVABILITY.md` |
| FraudNet p1/p2/pa/w | browser fingerprint、timing、token correlation、URL 不同。 | `04_RISK_FRAUDNET.md` |
| hCaptcha `checksiteconfig/getcaptcha` | sitekey、host、binary challenge body/response 不同。 | `03_AUTH_CAPTCHA.md` |
| reCAPTCHA anchor/reload | sitekey、callback、binary/proof body 不同。 | `03_AUTH_CAPTCHA.md` |
| GraphQL warmup/signup/authorize | token、user/card/profile/response-derived fields 不同。 | `02_GRAPHQL_SIGNUP_AUTHORIZE.md` |
| `/pay` Next.js action | multipart action body、`paypal_client_cfci` action suffix、response status 不同。 | `01_NAVIGATION_NEXTJS.md` |

## 6. 敏感/长字段的具体结论

| 字段/形态 | 我判断的真实语义 | 来源 | 动态性 | 用途 |
| --- | --- | --- | --- | --- |
| `BA-*` | Billing Agreement token | merchant/OpenAI/Stripe -> PayPal redirect 或 PayPal response | Session dynamic | 绑定 billing agreement，authorize 使用 |
| `EC-*` | PayPal checkout token | PayPal signup redirect/server response | Session dynamic | GraphQL token、signup、funding source 查询 |
| Stripe `pi_*_secret_*` | PaymentIntent client secret | Stripe return URL | Third-party dynamic | Stripe/OpenAI 页面恢复 payment intent 状态 |
| `email` | 新账号邮箱/credential | 用户/测试资料输入 | User dynamic | signup/account identity |
| `password` | 新账号密码或 login password | 用户输入 | User dynamic | login/signup credential |
| `phoneNumber` / `phone` | 手机号，OTP 接收和 signup 资料 | 用户输入 | User dynamic | 2FA/OTP 和 account phone |
| `pin` | OTP 验证码 | 用户输入/短信 | User dynamic | Confirm 2FA |
| `cardNumber` / `card.*` | 卡号、卡类型、CVV/expiry/name | 用户/测试卡输入，cardType 由前端派生 | User dynamic | installment/card eligibility 和 signup funding instrument |
| `identityDocument` | CPF/身份证明字段 | 用户/测试资料输入 | User dynamic | 巴西 signup compliance/KYC |
| `authId`, `challengeId` | OTP challenge response ids | `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` response | Response-derived dynamic | `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` 消费 |
| `_requestId`, `_hash`, `jse` | PayPal captcha challenge state | authchallenge HTML/JS/server | Challenge dynamic | `/auth/validatecaptcha` 必需 |
| `hcaptchaToken`, `grcV3EntToken` | captcha proof token | hCaptcha/reCAPTCHA SDK | Challenge dynamic | PayPal captcha validation |
| `fn_sync_data` | FraudNet/device sync payload | FraudNet JS/browser runtime | Risk dynamic | `/pay` action、idapps、signup GraphQL risk assessment |
| `contentIdentifier` | signup legal terms/content identifier | signup HTML + content manifest hash | Build/page dynamic | `SignUpNewMemberMutation` legal content binding |
| long timestamps | page start, event time, render/eval timing, browser perf timing | browser clock/performance API | Device/session dynamic | telemetry/risk/captcha validation |
| long opaque ids | Datadog request ids, Stripe event ids, Tealeaf session ids, PayPal debug ids | SDK/server runtime | Request/session dynamic | observability/correlation |

## 7. 包间关联结论

最终依赖链如下：

```text
BA token入口
  -> /agreements/approve
  -> /pay HTML生成 ctxId / paypal_client_cfci / FPTI / FraudNet bootstrap
  -> /pay Next.js server actions
  -> auth/captcha 或 country/context selection
  -> /checkoutweb/signup 得到 EC token + signup bootstrap
  -> content manifest / GraphQL warmup
  -> idapps / OTP / card / address queries
  -> SignUpNewMemberMutation
  -> /webapps/hermes fallback/review
  -> /graphql/ authorize
  -> Stripe/OpenAI return URL
```

字段流向：

| Producer packet/family | Produces | Consumer packet/family |
| --- | --- | --- |
| `/agreements/approve` / initial merchant URL | `ba_token` | `/pay`, FraudNet, signup URL, Hermes, authorize |
| `/pay` HTML | `ctxId`, `paypal_client_cfci`, action IDs, FPTI setup, cookies | `/pay` POST actions, observability, Tealeaf, FPTI |
| `/pay` action/redirect | `country.x`, `ctxId`, signup redirect, EC token eventually | `/checkoutweb/signup`, GraphQL |
| Signup HTML/content manifest | `contentIdentifier`, `ecToken`, logger config, client metadata | GraphQL warmup and signup |
| FraudNet JS | `fn_sync_data`, device/risk payloads | `/pay` actions, idapps GraphQL, signup GraphQL |
| `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` | `authId`, `challengeId` | `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` |
| Address/card queries | address/card eligibility metadata | `SignUpNewMemberMutation` |
| `SignUpNewMemberMutation` | account/funding/auth state or fallback reason | Hermes / authorize |
| Hermes | review/fallback context | `/graphql/ authorize` and merchant return |
| `/graphql/ authorize` | return URL / billing result | `pay.openai.com` Stripe return |

## 8. 最终文件对应

- `01_NAVIGATION_NEXTJS.md`: 我对导航和 Next.js action 包的手动语义分析。
- `02_GRAPHQL_SIGNUP_AUTHORIZE.md`: 我对 GraphQL、用户资料、卡、OTP、signup、authorize 包的手动语义分析。
- `03_AUTH_CAPTCHA.md`: 我对 captcha/auth 包的手动语义分析。
- `04_RISK_FRAUDNET.md`: 我对 FraudNet/device risk 包的手动语义分析。
- `05_TELEMETRY_OBSERVABILITY.md`: 我对 FPTI、Tealeaf、logger、Datadog 包的手动语义分析。
- `06_THIRD_PARTY_ASSETS.md`: 我对 Stripe、Google、静态资源包的手动语义分析。
- `07_STRICT_DUPLICATES.md`: 我判断可跳过的严格完全重复包。
- `09_PACKET_DECISION_TABLE.md`: 每一个 request id 对应到“已分析包族”或“严格重复跳过组”。
