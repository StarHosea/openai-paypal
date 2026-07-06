# roxy PayPal 抓包手工路径归并分析

## 范围与方法

本分析只使用以下两个 roxy 原始抓包目录中的原始索引、请求体、响应体、HTML、JS、CSS 文件：

- `captures/roxy-paypal-20260705-102145`
- `captures/roxy-paypal-20260705-103548`

归并口径：相同请求路径只算一个代表包。查询参数、token、时间戳、页面状态或重复上报导致的多次请求不重复展开；只在代表路径下说明动态字段和重复原因。二进制请求体如果 `read` 无法直接展开，本文明确标注为二进制不可直接读取，并结合同一行 `requests.tsv`、响应体、相邻请求和可读的 URL/query 字段做人工判断。

未使用脚本生成统计或旧逐包 Markdown 作为当前分析依据。

## 主流程概览

1. 用户进入 `www.paypal.com/agreements/approve`，携带 `ba_token=BA-*`，随后加载 `/pay` NextJS 页面和 PayPal 风控/遥测资源。
2. `/pay` 阶段以 `BA-*` billing agreement token 作为 PayPal checkout 外层会话标识，触发 Datadog RUM、FraudNet、mtr、hCaptcha passive、Tealeaf 等采集。
3. Captcha 通过后进入 `checkoutweb/signup`，URL 同时出现 `ba_token=BA-*` 与 `token=EC-*`。后续 Weasley/GraphQL 注册流主要使用 `EC-*` checkout token。
4. 注册流通过 GraphQL 查询国家/地址/资金来源/分期/OTP，并提交 `SignUpNewMemberMutation`。提交中包含邮箱、密码、手机号、身份证件、账单地址和卡信息。
5. 注册后进入 Hagrid/billing review，调用 `authorize` mutation。该接口返回 PayPal `billingAgreementToken` 和 Stripe `pm-redirects` return URL。
6. Stripe/OpenAI 支付页返回 `redirect_status=failed`，Stripe session 响应中显示 PayPal 被拒绝：`paypal_payment_declined` / `PAYER_CANNOT_PAY`。

## 路径归并表

| 归并路径 | 代表文件 | 作用 | 主要动态字段 |
|---|---|---|---|
| `GET /agreements/approve` | `requests.tsv` 中 `00001` / `00886` 等 | PayPal BA 审批入口 | `ba_token=BA-*`, `ssrt`, locale/country |
| `GET /pay` | `requests.tsv` 中 `/pay?...token=BA-*` | Modular checkout 页面 | `token=BA-*`, `ssrt`, `ul`, `country.x` |
| `GET /pay/_next/static/...` | 多个 `js/`、`css/` 静态响应 | 前端资源 | chunk hash、`dpl=1` |
| `POST /pay/api/trpc/observability.handleClientEmit` | `00053_POST_www.paypal.com_pay_api_trpc_observability.handleClientEmit.txt` | modxo UI/RUM 事件 | session/view/action UUID、BA token、时间戳 |
| `POST /mtr/1a7c...` | `00066_POST_www.paypal.com_mtr_...txt` + 响应 JSON | PayPal 设备/风控密封结果 | `cmid=BA-*`, timezone, sealedResult |
| `POST /v1/r/d/b/p1` | `00080_POST_c.paypal.com_v1_r_d_b_p1.txt` | FraudNet 浏览器指纹主包 | `correlationId`, UA, screen, time |
| `POST /v1/r/d/b/p2` | `00077_POST_c.paypal.com_v1_r_d_b_p2.txt` | FraudNet 第二阶段/令牌交换 | `correlationId`, device tokens |
| `POST /v1/r/d/b/w` | `00079_POST_c.paypal.com_v1_r_d_b_w.txt` | FraudNet 行为/时间序列 | `rDT`, token, app id |
| `POST /v1/r/d/b/pa` | `00078_POST_c.paypal.com_v1_r_d_b_pa.txt` | FraudNet 地理位置 | latitude/longitude, permission timings |
| `GET /v1/r/d/b/p3` | `requests.tsv` 中 `c6.paypal.com` 行 | FraudNet pixel/get | `f=BA-*` 或 `EC-*`, `s` source |
| `POST /identity/di/log` | `00096_POST_www.paypal.com_identity_di_log.txt` | DFPJS 设备识别日志 | event names, CMID, timezone |
| `POST /auth/verifyhcaptchapassive` | `.bin` 请求体 | 被动 hCaptcha 校验 | 二进制 token/挑战数据 |
| `POST api.hcaptcha.com/getcaptcha/{sitekey}` | `.bin` 请求体/响应 | hCaptcha 获取挑战 | sitekey, host, challenge payload |
| `POST /auth/logclientdata` | `00308_POST_www.paypal.com_auth_logclientdata.txt` | auth challenge FPTI 日志 | `_csrf`, `_sessionID`, captcha state |
| `POST /auth/validatecaptcha` | `.bin` 请求体 + HTML 响应 | PayPal captcha 页面校验 | captcha answer/challenge/session，二进制不可直接展开 |
| `POST /platform/tealeaftarget` | `00287/00296/00185...` + 响应 JSON | Tealeaf UI 行为录制 | page id, Tealeaf SaaS IDs, compressed body |
| `POST /xoplatform/logger/api/logger/` | `00243/00249/00509...` | Weasley/Hagrid 客户端日志 | `token=EC-*`, timestamp, event payload |
| `POST /graphql/DeferredFeature` | `00244_POST_www.paypal.com_graphql_DeferredFeature.txt` | deferred feature / experiment 查询 | country/channel/token/integrationType |
| `POST /graphql/GriffinMetadataQuery` | `00255_POST_www.paypal.com_graphql_GriffinMetadataQuery.txt` | BR 地址/电话/日期元数据 | country/lang/shipping country |
| `POST /graphql/CheckoutSessionDataQuery` | `00256_POST_www.paypal.com_graphql_CheckoutSessionDataQuery.txt` | checkout session/cart/merchant 信息 | `token=EC-*` |
| `POST /graphql/SupportedFundingSourcesQuery` | `00268_POST_www.paypal.com_graphql_SupportedFundingSourcesQuery.txt` | 可用资金来源 | `token=EC-*`, `userCountry=BR` |
| `POST /graphql/InstallmentOptionsQuery` | `00364_POST_www.paypal.com_graphql_InstallmentOptionsQuery.txt` | 卡分期/费用选项 | card number/type, buyerCountry, token |
| `POST /graphql/AddressAutocompleteFromPostalCodeQuery` | `00379_POST_www.paypal.com_graphql_AddressAutocompleteFromPostalCodeQuery.txt` | 巴西邮编地址补全 | postal code, country, token |
| `POST /graphql/InitiateRiskBasedTwoFactorPhoneConfirmationMutation` | `00400...Initiate...txt` | 发起手机 OTP | phone number, locale, token |
| `POST /graphql/ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` | `00476...Confirm...txt` | 提交 OTP | authId, challengeId, pin, token |
| `POST /graphql/SignUpNewMemberMutation` | `00478...SignUpNewMemberMutation.txt` | 新 PayPal 用户注册/绑卡 | card/email/password/phone/CPF/DOB/address/token |
| `POST /graphql/` `authorize` | `00540_POST_www.paypal.com_graphql.txt` | PayPal billing agreement 授权 | billingAgreementId, fundingPreference |
| `GET pm-redirects.stripe.com/return/...` | `requests.tsv` 行 `00550` | PayPal 授权后跳回 Stripe | `pa_nonce`, `status`, `token`, `ba_token` |
| `GET pay.openai.com/c/pay/{cs_live}` | `requests.tsv` 行 `00556` | OpenAI/Stripe Checkout 页面返回 | `payment_intent_client_secret`, `redirect_status` |
| `POST api.stripe.com/v1/payment_pages/{cs_live}/init` | `00562` 响应 JSON | Stripe Checkout session 初始化 | `cs_live`, `pi_*_secret`, customer email/address |
| `POST api.stripe.com/v1/payment_pages/{cs_live}` | `00579/00650` 响应 JSON | Stripe session 刷新 | config id, same session/customer/payment state |
| `POST r.stripe.com/0` / `POST r.stripe.com/b` | `.bin` 请求体 | Stripe telemetry | 二进制/文本遥测 payload，不逐条展开 |
| `POST m.stripe.com/6` | `00684_POST_m.stripe.com_6.txt` | Stripe cookie/fingerprint session | encoded browser fingerprint, `muid/guid/sid` response |
| `POST collector-px...` | `.bin` 请求体 | Stripe/PerimeterX 人机与风险采集 | collector id, binary event body |
| Google/Maps/Play/log 资源 | `requests.tsv` 尾段 | Stripe 地址 autocomplete / Google telemetry | API key、CSP probe、log body |

## PayPal 入口与页面加载

### `GET /agreements/approve`

代表索引在第二个目录 `requests.tsv` 第 5 行：`https://www.paypal.com/agreements/approve?ba_token=BA-37R61061EU582084R`。第一个目录也出现同类 `BA-65R...`、`BA-4PT...`。这是 Billing Agreement 审批入口，`BA-*` 是 PayPal 外层 billing agreement token，随页面跳转保留。

- 来源：PayPal/OpenAI 结账入口 URL。
- 用途：标识待批准的 billing agreement，并把用户带到 PayPal checkout。
- 动态性：每次 checkout 会话动态生成；同一会话内重复使用。

### `GET /pay` 与 `/pay/_next/static/...`

`/pay?ssrt=...&token=BA-...&ul=1` 加载 modular checkout。后续大量 `/_next/static/chunks/*`、字体、PayPal logo、flags 为静态资源。chunk hash 与部署版本相关，`token/ssrt/country.x/ul` 是会话和路由动态字段。

`/pay` 的 fetch/RSC 变体，例如 `POST /pay?...paypal_client_cfci=modxo_vaulted_not_recurring-CAPTCHA_SOLVED`，请求体为二进制，响应 content type 为 `text/x-component`。它表示前端状态推进后的 Next/RSC 片段刷新，不是新的业务实体。

## PayPal 观测、风控与行为采集

### `/pay/api/trpc/observability.handleClientEmit`

代表包 `00053_POST_www.paypal.com_pay_api_trpc_observability.handleClientEmit.txt` 包含 `window_onerror`、`modxo_window_onerror`、React minified error、chunk 文件名、line/col、metric id。Datadog RUM 代表包 `00057_POST_browser-intake-us5-datadoghq...txt` 进一步包含 view/session/action UUID、URL `token=BA-37R...`、connectivity、merchant id、merchant name `OpenAI OpCo, LLC`、country、flow、anonymous id。

- 来源：PayPal checkout 前端运行时和 RUM SDK。
- 用途：性能、错误、UI 事件、页面状态监控。
- 动态字段：时间戳、session/view/action/tab UUID、anonymous id、token、页面 name、metric event。
- 静态/半静态字段：service/version、SDK version、metric/event 名称。

### `/mtr/1a7c...`

代表请求 `00066_POST_www.paypal.com_mtr_...cmid_BA-37R...txt` 为二进制不可直接 `read`，但 URL 明确携带 `chnl=iwc-mxo`、`cmid=BA-37R61061EU582084R`、`btz=Pacific/Honolulu`、`ci=js/3.12.1`。响应 `00066_resp_200...json` 返回 `requestId` 与很长的 `sealedResult`。

- 来源：PayPal mtr 风控/设备采集库。
- 用途：把前端采集数据封装成服务端可验证的 sealed result。
- 动态字段：`cmid=BA-*`、`requestId`、`sealedResult`、timezone。

### FraudNet `c.paypal.com/v1/r/d/b/*`

`p1` 代表 `00080_POST_c.paypal.com_v1_r_d_b_p1.txt`，包含：

- `appId=IWC_NEXT_CHECKOUT`
- `correlationId=BA-37R61061EU582084R`
- 当前 URL `https://www.paypal.com/pay?...token=BA-37R...`
- 浏览器 UA、language、platform、cookieEnabled、screen/window、timezone、network effective type、rtt、downlink、performance/check hashes、WebAuthn capability。

`pa` 代表 `00078_POST_c.paypal.com_v1_r_d_b_pa.txt`，包含地理坐标 `latitude=21.307796`、`longitude=-157.859187` 以及 permission/location timing。`p2/w/p3` 是同一 FraudNet 链条的其他阶段：`p2` 返回 device/session token，`w` 上报行为时间序列，`p3` 是 pixel/get 触发。

- 来源：PayPal FraudNet 浏览器端采集。
- 用途：反欺诈、设备识别、行为关联。
- 动态字段：`BA-*` 或 `EC-*` correlation id、时间戳、行为序列、地理坐标、响应 token。
- 静态/环境字段：UA、屏幕尺寸、语言、timezone 在同一设备环境内相对稳定。

### `/identity/di/log`

代表 `00096_POST_www.paypal.com_identity_di_log.txt` 记录 `DFPJS_LIB_LOADED`、`DFPJS_VENDOR_INVOKED`、`DFPJS_VENDOR_RESPONSE_RECEIVED`、`DFPJS_EDGE_MAPPING_COMPLETE`，tracking 中含 `CMID=BA-37R61061EU582084R`。

- 来源：PayPal DFPJS device intelligence。
- 用途：记录设备识别库加载、供应商调用与返回。
- 动态字段：timestamp、CMID、timezone。

### `/platform/tealeaftarget`

多个代表请求体为二进制不可读，例如 `00287_POST_www.paypal.com_platform_tealeaftarget.txt`、`00296...txt`、`00185...CAPTCHA_SOLVED.txt`。响应如 `00287_resp_200_fetch_www.paypal.com_platform_tealeaftarget_4d9ada0ac5.json` 返回 `targetVersion` 与 `id`。带查询参数的 ping 变体包含 `X-PageId`、`X-Tealeaf=device (UIC) Lib/6.4.177`、`X-Tealeaf-MessageTypes`、`X-Tealeaf-SaaS-AppKey`、`TLTSID/TLTDID`、`Content-Encoding=gzip`。

- 来源：PayPal Tealeaf UI 行为录制库。
- 用途：页面点击、输入、UI 事件录制与回放。
- 动态字段：page id、session/device id、压缩 body、Tealeaf id。

### `/xoplatform/logger/api/logger/`

代表 `00243_POST_www.paypal.com_xoplatform_logger_api_logger.txt` 包含 Weasley 事件：`weasley_FAILED_TO_LOAD_CLIENT_INTERACTION_LIBRARY`、`WEASLEY_BNPL_ELIGIBLITY_CHECK_FPTI`、`WEASLEY_IS_ADDRESS_LESS_ELIGIBLE_FPTI` 等，payload 中有 `token=EC-79N40798NE0202548`、seller id `CF9F8FKTUYUAY`、`buyer_type=NewUser`、`context_type=EC-TOKEN`、`space_key=SKS09G`。代表 `00509_POST...` 是 Hagrid billing 页面 `BILLING_LITE_Initial_HTML_Load`。

- 来源：PayPal Weasley/Hagrid 前端日志。
- 用途：功能开关、eligibility、内容缺失、页面生命周期、错误追踪。
- 动态字段：clientTimestamp、token、visitor/session ids、event-specific payload。

## Captcha 与 auth challenge

### hCaptcha passive 与 `verifyhcaptchapassive`

`api.hcaptcha.com/checksiteconfig` URL 中有 host、sitekey，例如 PayPal sitekey `884d15d9-b649-4bbb-8d1c-2d6f0eed75eb`。`getcaptcha/{sitekey}` 请求体和响应体为 octet-stream。`/auth/verifyhcaptchapassive` 请求体为 `.bin`，不可直接 `read`。

- 来源：PayPal passive hCaptcha challenge。
- 用途：在用户进入 checkout/signup 前做无感/被动人机验证。
- 动态字段：sitekey 对应服务固定，challenge/rqdata/session 动态。

### `/auth/logclientdata`

代表 `00308_POST_www.paypal.com_auth_logclientdata.txt`：

- `fpti.pgrp/page=main:authchallenge::checkoutweb:signup`
- `fltk=EC-79N40798NE0202548`
- `captchaState=CLIENT_SIDE_RECAPTCHAV3_SERVED`
- `_csrf=12cPhR...`
- `_sessionID=WaRh_Kx-LfW51gq77EIkRTla-6Ms636c`

用途是记录 auth challenge/captcha 状态。`_csrf`、`_sessionID`、FPTI ids 都是动态会话字段。

### `/auth/validatecaptcha`

代表请求体如 `00331_POST_www.paypal.com_auth_validatecaptcha.bin`、`00547_POST_www.paypal.com_auth_validatecaptcha.bin` 均为二进制不可读。响应 HTML 为 `authchallengenodeweb`，加载 `https://www.paypalobjects.com/pa/js/pa.js`，analytics 指向 `https://t.paypal.com/ts`，出现 `space_key=SKSBBA`、`rsta=pt_BR`、`ccpg=BR`。

- 来源：用户/浏览器提交 captcha challenge 结果。
- 用途：服务端校验 captcha，决定是否继续 checkout/signup。
- 动态字段：captcha answer/token、session id、CSRF、challenge id。

## PayPal GraphQL/signup 流

### `DeferredFeature`

代表 `00244_POST_www.paypal.com_graphql_DeferredFeature.txt` 使用 `token=EC-79N40798NE0202548`、`countryCodeAsString=BR`、`channel=WEB`、`integrationType=XoSignupAuth`，查询 `otpLoginContext` 和 `elmoExperiment`。

- 来源：Weasley signup 页面初始化。
- 用途：决定 deferred feature、member-as-default、OTP login context。
- 动态字段：token；半静态字段：country/channel/integration type。

### `GriffinMetadataQuery`

代表 `00255_POST_www.paypal.com_graphql_GriffinMetadataQuery.txt` 查询 `countryCode=BR`、`languageCode=pt`、`shippingCountryCode=BR` 的地址布局、字段 regex、电话 mask、日期格式、territories。

- 来源：地区化表单元数据。
- 用途：驱动巴西地址、电话、日期输入校验和标签。
- 动态性：国家/语言选择可变，返回规则相对静态。

### `CheckoutSessionDataQuery`

请求 `00256_POST...CheckoutSessionDataQuery.txt` 只传 `token=EC-79N40798NE0202548`。响应显示 `checkoutSessionType=BILLING_WITHOUT_PURCHASE`、merchant `OpenAI OpCo, LLC`、merchantId `CF9F8FKTUYUAY`、intent `SALE`、description `Subscription creation`、cancelUrl 指向 Stripe return cancel URL。

- 来源：PayPal checkout session 服务。
- 用途：拉取 merchant/cart/session 基础信息。
- 动态字段：EC token、correlation id、return/cancel URL。

### `SupportedFundingSourcesQuery`

代表 `00268_POST...SupportedFundingSourcesQuery.txt` 传 `token=EC-79N40798NE0202548`、`userCountry=BR`，响应为多个 supported funding source，代表响应中 issuers 为空。

- 用途：根据国家与 checkout session 获取可用资金源列表。
- 动态字段：EC token、userCountry。

### `InstallmentOptionsQuery`

代表 `00364_POST...InstallmentOptionsQuery.txt` 传 `buyerCountry=BR`、`cardNumber=4916079570949387`、`cardType=VISA`、`token=EC-79N40798NE0202548`。

- 来源：用户输入的卡号和国家。
- 用途：计算巴西卡分期选项、月付、费用和折扣。
- 动态字段：完整卡号、卡类型、token。

### `AddressAutocompleteFromPostalCodeQuery`

代表 `00379_POST...AddressAutocompleteFromPostalCodeQuery.txt` 传 `country=BR`、`postalCode=80020-100`、`token=EC-79N40798NE0202548`，process mode 为 `FASTCOMPLETION`、scope 为 `STREET_LEVEL`。

- 来源：用户输入邮编。
- 用途：自动补全街道/城市/州。
- 动态字段：postal code、token。

### 手机 OTP 发起与确认

`00400_POST...InitiateRiskBasedTwoFactorPhoneConfirmationMutation.txt` 传 `phoneNumber=21983993158`、`phoneCountry=BR`、locale `{country: BR, lang: pt}`、token `EC-79N...`。`00476_POST...ConfirmRiskBasedTwoFactorPhoneConfirmationMutation.txt` 传 `authId=15958040049748644572`、`challengeId=16814992908704624784`、`pin=798336`、同一个 token。

- 来源：用户输入手机号与短信/OTP 输入。
- 用途：风险触发的手机二次确认。
- 动态字段：phone number、authId、challengeId、OTP pin、token。
- 静态字段：mutation 名称与 GraphQL 结构。

### `SignUpNewMemberMutation`

代表 `00478_POST_www.paypal.com_graphql_SignUpNewMemberMutation.txt` 是最敏感的业务提交包。关键字段：

- `token=EC-79N40798NE0202548`：signup/checkout session token，动态。
- 卡信息：`cardNumber=4916079570949387`、`expirationDate=11/2029`、`securityCode=633`、`type=VISA`、`productClass=CREDIT`。来源为用户输入，用途为绑卡/支付工具添加，动态且高度敏感。
- 邮箱：`bkamwkcbajkwnmblbna@gmail.com`。来源为注册表单，用途为 PayPal 账号标识/联系邮箱，动态敏感。
- 密码：`qaz2669164223wsx`。来源为注册表单，用途为新账号凭据，动态高度敏感。
- 姓名：`Matheus Pereira`。来源为注册表单，用途为账号实名/账单地址姓名。
- 手机：country code `55`、number `21984108976`、type `MOBILE`。来源为注册表单，用途为账号联系和风险验证，动态敏感。
- CPF：`284.103.759-23`，type `CPF`。来源为巴西身份信息表单，用途为 KYC/本地合规，动态高度敏感。
- DOB：`12/12/2004`。来源为注册表单，用途为年龄/合规校验。
- 账单地址：`Praça Tiradentes, 35, 450`、`Centro`、`Curitiba`、`PR`、`80020-100`、`BR`，并有 `accountQuality.autoCompleteType=ANS`、`isUserModified=true`。
- shippingAddress 为空字段但 country/name 存在，说明此 billing-without-purchase 流程无实物配送地址。
- `contentIdentifier=BR:pt:...:compliance.signupTerms`、`marketingOptOut=true`、`legalAgreements={}` 是条款/营销/合规模块字段。

响应 `00478_resp_200_fetch...json` 返回业务错误：`INSTRUMENT_SHARING_LIMIT_EXCEEDED`，checkpoint `addCard`，`cardNumber` 对应 `CARD_GENERIC_ERROR`，同时含 `accessToken` 和 `correlationId=f6351058d5a95`。这说明 GraphQL HTTP 状态 200，但业务层绑卡/注册失败。

## PayPal billing authorize 与 Stripe/OpenAI 回跳

### `POST /graphql/` `authorize`

代表 `00540_POST_www.paypal.com_graphql.txt` 发送 GraphQL 数组，operationName `authorize`，变量：

- `billingAgreementId=EC-79N40798NE0202548`
- `fundingPreference.balancePreference=OPT_OUT`
- `legalAgreements={}`

响应 `00540_resp_200_fetch_www.paypal.com_graphql_f71dc8263c.json`：

- `billingAgreementToken=BA-37R61061EU582084R`
- `paymentAction=SALE`
- `returnURL.href=https://pm-redirects.stripe.com/return/acct_1HOrSwC6h1nxGoI3/pa_nonce_...?...status=success&token=EC-79N40798NE0202548`
- `buyer.userId=BYVBMMP4SPLDA`
- `correlationId=f924704324747`

用途是把 PayPal EC checkout token 授权成 BA billing agreement，并返回 Stripe 可消费的回跳 URL。

### `pm-redirects.stripe.com/return/...`

`requests.tsv` 中 `00550` 是 `GET 302`，URL：`https://pm-redirects.stripe.com/return/acct_1HOrSwC6h1nxGoI3/pa_nonce_UpKZVShfniwoeTw86FIGaOsHoa8HN2j?status=success&token=EC-79N40798NE0202548&ba_token=BA-37R61061EU582084R`。

- 来源：PayPal authorize 响应返回的 Stripe redirect。
- 用途：把 PayPal 授权结果交回 Stripe/OpenAI。
- 动态字段：account id、`pa_nonce`、status、EC token、BA token。

### `pay.openai.com/c/pay/{cs_live}`

`requests.tsv` 中 `00556` 是 `GET 200 document`，URL 带：

- `cs_live_a124RoYSRP3IQQqvh1vmV2BL1TPlPQblflfSB31CbLLQsNBvIWrzOIW7fk`
- `lid=39a072b6-cc6a-4512-8828-82eab614a79a`
- `payment_intent=pi_3TpftsC6h1nxGoI32SJNZnj6`
- `payment_intent_client_secret=pi_3TpftsC6h1nxGoI32SJNZnj6_secret_7bw0w8u0cSCD6uXqy5RSZCeZ6`
- `redirect_pm_type=paypal`
- `redirect_status=failed`
- `ui_mode=custom`

这说明 Stripe/OpenAI 页面收到 PayPal redirect 后，最终状态是失败。`payment_intent_client_secret` 是 Stripe 前端用来读取/继续处理 PaymentIntent 的动态 secret，敏感，不能静态复用。

## Stripe/OpenAI Checkout

### `POST /v1/payment_pages/{cs_live}/init` 与 `/v1/payment_pages/{cs_live}`

代表响应 `00562_resp_200_fetch_api.stripe.com_v1_payment_pages_..._init.json` 显示：

- 商户：`OpenAI OpCo, LLC`，account `acct_1HOrSwC6h1nxGoI3`，support email `ar@openai.com`，support phone `+14158799686`。
- 客户：`cus_UpIXXZLZ3hS6p2`，email `alexanderbarrientos398+3@googlemail.com`，name `Joao Silva`，地址 `Avenida Afonso Pena 1500`, `Belo Horizonte`, `MG`, `30130-005`, `BR`。
- session：`cs_live_a124RoYS...`，currency `usd`，mode `subscription`，amount/total `2000`。
- 商品：`ChatGPT Plus Subscription`，price `price_1MWW51C6h1nxGoI30udZUgPl`，product `prod_N9DMeqbltqZchv`，monthly recurring。
- 支付方式：`card`, `paypal`，ordered methods 还含 `apple_pay`, `google_pay`。
- PaymentIntent：`pi_3TpftsC6h1nxGoI32SJNZnj6`，`client_secret=pi_3Tpfts..._secret_...`。
- PayPal failure：`last_payment_error.code=payment_method_provider_decline`，`decline_code=paypal_payment_declined`，message 中写明 `PayPal issue: PAYER_CANNOT_PAY`，Debug ID `7ece1f92853e8`。
- `payment_method.type=paypal`，但 PayPal payer email/id/country 为空。
- `return_url=https://chatgpt.com/checkout/verify?...plan_type=plus`。
- hCaptcha/link fields：`hcaptcha_site_key=24ed0064-62cf-4d42-9960-5dd1a41d4e29`、`site_key=ec637546-e9b8-447a-ab81-b5fb6d228ab8`、`rqdata/hcaptcha_rqdata`。

`00579` 与 `00650` 是同一路径的后续刷新，响应主体相同类别，主要差异是 `config_id`、资源 host 和前端状态。

### Stripe telemetry

`r.stripe.com/0` 和 `r.stripe.com/b` 在 `requests.tsv` 中大量重复，request body 为 `.bin`，content type 记为 `text/plain`，`read` 无法直接展开。它们是 Stripe 前端事件/性能/风险遥测，不代表业务提交。`m.stripe.com/6` 代表请求 `00684_POST_m.stripe.com_6.txt` 是 URL 编码/base64 样式的浏览器指纹 blob，响应返回：

- `muid=ac09308b-1fb2-4bbd-ae11-b9a72ca8a88cb5b4fc`
- `guid=df035871-8aae-48d6-b3c8-d4886e6c8f87327cf9`
- `sid=c97cdd73-6e60-409e-a11c-9310fc9a6f7eceb076`

这些是 Stripe 设备/会话关联 cookie id，动态生成。

Stripe 还加载 `b.stripecdn.com` 的 HCaptchaInvisible、HumanSecurity、GoogleMaps frame，调用 `collector-pxvl48pwoc.*` 的 PerimeterX collector，以及 `maps.googleapis.com`、`api.hcaptcha.com`。这些用于人机验证、地址自动补全和前端风险控制。

## 敏感字段来源、用途、静态/动态分类

| 字段 | 示例 | 来源 | 用途 | 分类 |
|---|---|---|---|---|
| PayPal BA token | `BA-37R61061EU582084R`, `BA-65R...`, `BA-4PT...` | PayPal/OpenAI checkout URL 与 authorize 响应 | billing agreement 外层会话与授权结果关联 | 动态会话 token |
| PayPal EC token | `EC-79N40798NE0202548`, `EC-4B298...` | PayPal checkout/signup URL 与 GraphQL | checkout session / signup / billing authorize | 动态会话 token |
| Stripe Checkout session | `cs_live_a124Ro...` | pay.openai.com / Stripe API | Stripe Checkout 页面 session | 动态 session id |
| Stripe PaymentIntent | `pi_3TpftsC6h1nxGoI32SJNZnj6` | Stripe API / return URL | 支付意图 | 动态 id |
| Stripe client secret | `pi_3Tpfts..._secret_7bw0...` | OpenAI return URL 与 Stripe API response | 前端读取/处理 PaymentIntent | 动态 secret，高敏 |
| Email - PayPal signup | `bkamwkcbajkwnmblbna@gmail.com` | PayPal signup 表单 | 新 PayPal 账号邮箱 | 用户输入，敏感动态 |
| Email - Stripe customer | `alexanderbarrientos398+3@googlemail.com` | Stripe customer/session | OpenAI/Stripe customer 联系邮箱 | 账户数据，敏感动态 |
| Password | `qaz2669164223wsx` | PayPal signup 表单 | 新 PayPal 账号凭据 | 用户输入，高敏动态 |
| Phone - OTP | `21983993158` | OTP 发起表单 | 风险手机验证 | 用户输入，敏感动态 |
| Phone - signup | `+55 21984108976` | PayPal signup 表单 | 账号联系/验证 | 用户输入，敏感动态 |
| Card | `4916079570949387`, `11/2029`, `633`, `VISA` | PayPal signup/绑卡表单 | 添加支付工具、分期查询 | 用户输入，高敏动态 |
| CPF | `284.103.759-23` | PayPal BR 身份字段 | KYC/合规 | 用户输入，高敏动态 |
| OTP PIN | `798336` | 用户短信/OTP 输入 | 确认 phone challenge | 一次性动态密钥 |
| auth/challenge ids | `15958040049748644572`, `16814992908704624784` | PayPal OTP initiate 响应/状态 | 绑定 OTP 确认上下文 | 动态长数字 id |
| `pa_nonce` | `pa_nonce_UpKZVShfniwoeTw86FIGaOsHoa8HN2j` | PayPal authorize / Stripe redirect | Stripe/PayPal 回跳关联 | 动态 nonce |
| PayPal buyer id | `BYVBMMP4SPLDA` | authorize 响应 / Hagrid telemetry | PayPal buyer identity | 动态/账号标识，敏感 |
| Merchant id | `CF9F8FKTUYUAY` | PayPal checkout session | OpenAI merchant 标识 | 相对静态商户配置 |
| Stripe account | `acct_1HOrSwC6h1nxGoI3` | Stripe API | OpenAI Stripe account | 相对静态商户配置 |
| Debug/correlation ids | `f6351058d5a95`, `f924704324747`, `7ece1f92853e8` | PayPal/Stripe responses | 排查、链路追踪 | 动态请求 id |
| Geo coordinates | `21.307796,-157.859187` | FraudNet geolocation | 设备位置风险信号 | 环境动态，高敏 |

## 重复路径处理说明

- `/pay/api/trpc/observability.handleClientEmit`、Datadog RUM、`t.paypal.com/ts`、`xoplatform/logger`、`platform/tealeaftarget` 是典型高频观测路径。重复原因是页面加载、点击、错误、性能、FPTI、visibility、beacon/ping 分批上报，不是不同业务步骤。
- FraudNet 的 `p1/p2/w/pa/p3` 在 BA 阶段和 EC/Hagrid 阶段都会出现，path 相同但 `correlationId/f` 从 `BA-*` 变成 `EC-*` 或 source 从 `IWC_NEXT_CHECKOUT` 变成 `BILLINGUINODEWEB_BILLINGWITHOUTPURCHASE_LITE`。本文按路径归并，并说明 token/source 的阶段差异。
- `/auth/validatecaptcha` 在第一个目录出现多次，代表用户多轮 captcha/challenge 尝试；请求体为二进制，响应 HTML 和相邻 auth log 显示均属于同一 captcha 校验路径。
- Stripe 的 `/v1/payment_pages/{cs_live}`、`r.stripe.com/0`、`r.stripe.com/b` 重复很多次。业务上只有 checkout session 初始化/刷新和 telemetry 两类；重复 telemetry 不逐条展开。

## 结论

两个 roxy 抓包覆盖的是同一类 OpenAI/Stripe 通过 PayPal Billing Agreement 付款的失败链路。PayPal 侧先以 `BA-*` 进入 checkout，captcha/风控通过后以 `EC-*` 进入 signup/billing 流；注册提交含完整用户资料、卡、CPF、密码和手机 OTP。随后 PayPal authorize 返回 Stripe redirect，但 Stripe/OpenAI 页面最终记录 `redirect_status=failed`，Stripe PaymentIntent 的 `last_payment_error` 明确为 PayPal provider decline：`paypal_payment_declined` / `PAYER_CANNOT_PAY`。
