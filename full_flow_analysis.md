# PayPal → Stripe/OpenAI 支付全流程串联分析

## 1. 分析范围和方法

本文件只基于以下两个 roxy 原始抓包目录做人工分析：

- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-102145`
- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-103548`

分析方式：逐段读取原始 `network/requests.tsv`，再人工打开代表性的 `network/requests/*` 和 `network/bodies/*` 文件核对请求/响应字段。同一路径、同一功能的重复请求按一个包族归并；高频 telemetry、fingerprint、logger、静态资源不逐条堆叠，而是说明它们在流程中的位置和作用。

结论先行：主完整链路在 `roxy-paypal-20260705-103548` 中完成。PayPal 侧经历 BA 入口、风险识别、captcha、国家切换、guest signup、地址/分期/OTP、signup 尝试、Hermes/Hagrid review、`authorize`。PayPal `authorize` 返回了 Stripe return URL 和 BA token，但回到 Stripe/OpenAI 后，Stripe Checkout session 的 PaymentIntent 状态为 `requires_payment_method`，`last_payment_error.code=payment_method_provider_decline`，`decline_code=paypal_payment_declined`，PayPal 原因是 `PAYER_CANNOT_PAY`，Debug ID 为 `7ece1f92853e8`。因此最终不是前端加载失败，而是 PayPal 支付工具被支付提供方拒绝。

## 2. 两个抓包在流程中的角色

| 抓包目录 | 主要 token | 作用 |
|---|---|---|
| `roxy-paypal-20260705-102145` | `BA-65R58767795383013`、后续 `BA-4PT6003892106623L` | 早期 PayPal `/pay` 尝试，重点体现 BA 入口、email 提交、captcha/authchallenge、多轮重载、FraudNet/Tealeaf/observability 重复包。未进入完整 Stripe return 结局。 |
| `roxy-paypal-20260705-103548` | `BA-37R61061EU582084R`、`EC-79N40798NE0202548` | 主链路：从 `/agreements/approve` 进入 PayPal，转为 `EC` checkout token，完成 guest signup/OTP/Hagrid authorize，再回到 `pm-redirects.stripe.com`、`pay.openai.com` 和 Stripe Checkout，最终失败。 |

后续“完整流程”以第二组抓包为主；第一组用于补充说明同类 `/pay`、captcha、authchallenge、telemetry 的重复和分支行为。

## 3. 主完整流程时间线

| 阶段 | 时间范围 | 代表包 | 做了什么 |
|---|---:|---|---|
| 入口加载 | 02:36:33 - 02:36:36 | `GET /agreements/approve?ba_token=BA-37R61061EU582084R`、Next static assets | 打开 PayPal billing agreement approval 页面，加载 PayPal checkout 前端。 |
| 风控初始化 | 02:36:35 - 02:36:47 | `/mtr/...`、`c.paypal.com/v1/r/d/b/*`、`identity/di/log`、hCaptcha passive | 收集浏览器/设备/网络/地理位置/插件等风险特征，执行 passive captcha。 |
| BA `/pay` 状态推进 | 02:36:37 - 02:37:11 | `POST /pay?...token=BA...paypal_client_cfci=...`、`GET /pay/api/countries`、`country.x=BR` | PayPal modular checkout 从 BA 页面推进到国家选择/BR locale。 |
| 跳转 signup | 02:37:11 - 02:37:18 | `GET /pay/checkout/signup/contact`、`GET /agreements/approve?...ba_token=...`、`GET /checkoutweb/signup?...token=EC...` | guest 用户被导向 checkout signup，BA token 衍生出 `EC-79N40798NE0202548`。 |
| signup 初始化查询 | 02:37:17 - 02:37:19 | `DeferredFeature`、`CheckoutSessionDataQuery`、`SupportedFundingSourcesQuery` | 拉取 OTP context、merchant/session 信息、支持的 funding source。 |
| 表单输入与二次风控 | 02:37:20 - 02:39:53 | FraudNet、Tealeaf、logger、recaptcha、`InstallmentOptionsQuery`、`AddressAutocompleteFromPostalCodeQuery` | 收集输入行为；校验卡分期、邮编地址；加载 reCAPTCHA/hCaptcha 辅助风控。 |
| 手机 OTP | 02:39:54 - 02:43:41 | 多个 `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`、最后 `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` | 多次发起短信验证，最终用 PIN `798336` 确认成功。 |
| signup 尝试 | 02:43:43 | `SignUpNewMemberMutation` | 提交新用户、卡、地址、CPF、DOB、密码等 onboarding 信息；响应业务错误 `INSTRUMENT_SHARING_LIMIT_EXCEEDED` / `CARD_GENERIC_ERROR`。 |
| fallback 到 review | 02:43:45 - 02:43:57 | `/checkoutweb/drop`、`/webapps/hermes?...reason=Q0FSRF9HRU5FUklDX0VSUk9S`、Hagrid assets | signup/card 添加异常后进入 Hermes/Hagrid review fallback。 |
| PayPal authorize | 02:43:58 | GraphQL `authorize` | 对 `EC-79...` billing agreement 授权，返回 BA token、buyer userId、Stripe return URL。 |
| Stripe/OpenAI return | 02:44:04 - 02:44:10 | `pm-redirects.stripe.com/return/...status=success...ba_token=BA...`、`pay.openai.com/c/pay/{cs_live}?...redirect_status=failed`、`api.stripe.com/v1/payment_pages/{cs_live}/init` | Stripe 接收 PayPal return 后把用户带回 OpenAI hosted checkout；OpenAI URL 已带 `redirect_status=failed`，Stripe session 解释失败原因。 |
| Stripe 支撑加载 | 02:44:10 - 02:44:27 | Stripe JS/assets、`r.stripe.com/0`、`r.stripe.com/b`、`m.stripe.com/6`、hCaptcha、PerimeterX、Google Maps | 展示失败后的 Checkout UI，并继续收集 Stripe 风控和浏览器指纹。 |

## 4. 分阶段因果分析

### 4.1 PayPal BA 入口：`/agreements/approve` 与 `/pay`

主链路第一步是：

- `GET https://www.paypal.com/agreements/approve?ba_token=BA-37R61061EU582084R`

这里的 `BA-37R61061EU582084R` 是 merchant/OpenAI 侧发起 PayPal billing agreement 后交给 PayPal 的入口 token。PayPal 返回 HTML 文档并加载 `/pay/_next/static/...` 前端资源、PayPal logo、字体、captcha/fraud 脚本。这个阶段还没有出现 `EC` token；页面的任务是恢复 PayPal checkout session 并决定是否需要登录、guest signup 或风险挑战。

随后 PayPal 前端用 server component/fetch 方式推进 BA 页面状态：

- `POST /pay?ssrt=1783218992321&token=BA-37R61061EU582084R&ul=1&paypal_client_cfci=modxo_vaulted_not_recurring-no_interaction`
- 后续 captcha 通过后又出现 `paypal_client_cfci=modxo_vaulted_not_recurring-CAPTCHA_SOLVED`
- 国家选择后出现 `country.x=BR` 和 `ctxId=c5d20d7d-43c4-45fc-b36d-da68a1ca84f5`

这些包的 request body 是二进制/React RSC 内容，原始读取显示为不可直接解析；但 URL、响应 `text/x-component`、相邻跳转足以说明用途：前端把当前 BA checkout 状态、国家/locale、captcha 状态提交给 PayPal，使服务端返回下一步 UI。

第一组抓包中同一机制出现三次：

- `BA-65R58767795383013` 初始 `/pay?ssrt=1783218039565&token=...&ul=1`
- 同一 BA 的 `Submit_Email` 和 captcha/authchallenge 重载
- 后续 `BA-4PT6003892106623L` 携带 `ctxId=e990d911-884d-4399-9f09-179058bc5c13&country.x=BR` 重新进入 `/pay`

所以 `/pay` 包族不是最终支付确认包，而是 PayPal modular checkout 的状态推进入口。

### 4.2 PayPal 风控、fingerprint、captcha 包族

PayPal 在业务 GraphQL 之前密集发送风控包。它们不是支付金额/卡号提交，但决定是否允许继续。

#### `/mtr/1a7c...`

代表请求：

- `GET /mtr/1a7c3460cd8c343771081839499ed7a0/...?...q=QBzalmMuDFJIiZNebIWt`
- `POST /mtr/1a7c3460cd8c343771081839499ed7a0?chnl=iwc-mxo&cmid=BA-37R61061EU582084R&cr=undefined&btz=Pacific/Honolulu&emf=true&ci=js/3.12.1&q=QBzalmMuDFJIiZNebIWt`

请求体不可直接读取，响应返回 JSON：`v=2`、`requestId=1783218996246.qa8rTj`、长 `sealedResult`。这类包把当前 BA token、浏览器时区、channel、加密设备结果送给 PayPal 风控。

#### `c.paypal.com/v1/r/d/b/*`

代表请求：

- `POST /v1/r/d/b/p1`
- `POST /v1/r/d/b/p2`
- `POST /v1/r/d/b/pa`
- `POST /v1/r/d/b/w`
- `GET c6.paypal.com/v1/r/d/b/p3?...`

`p1` 代表字段：

- `appId=IWC_NEXT_CHECKOUT`
- `correlationId=BA-37R61061EU582084R`
- `URL=https://www.paypal.com/pay?ssrt=1783218992321&token=BA-37R61061EU582084R&ul=1`
- browser: `Windows NT 10.0`, `Chrome/150.0.0.0`, `language=en-US`, `platform=Win32`
- screen/window: `1536x864`、`inner 567x700`
- network: `effectiveType=3g`, `rtt=350`, `downlink=0.4`
- timezone: `Pacific/Honolulu`

`p2` 代表字段：插件列表、GPU、JS heap、navigation timing、canvas/feature timing。后续 signup/Hagrid 阶段同一路径的 `correlationId` 改为 `EC-79N40798NE0202548`，`appId`/site 从 `IWC_NEXT_CHECKOUT` 变为 `CHECKOUTUINODEWEB_ONBOARDING_LITE` 或 `BILLINGUINODEWEB_BILLINGWITHOUTPURCHASE_LITE`，说明同一风控框架贯穿不同页面。

#### `/identity/di/log`

代表请求记录 DFPJS 生命周期：

- `DFPJS_LIB_LOADED`
- `DFPJS_EDGE_MAPPING_ENABLED`
- `DFPJS_VENDOR_INVOKED`
- `DFPJS_VENDOR_RESPONSE_RECEIVED`
- `DFPJS_EDGE_MAPPING_COMPLETE`

tracking 中携带 `CMID=BA-37R61061EU582084R`。这是 device intelligence 日志，不是支付授权。

#### hCaptcha / reCAPTCHA / authchallenge

主链路和第一组抓包都出现：

- `auth/createchallenge/.../hcaptchapassive.js`
- `paypalobjects.com/.../hcaptcha/hcaptchapassive.html`
- `js.hcaptcha.com/1/api.js?onload=checkVisitor&render=explicit`
- `api.hcaptcha.com/checksiteconfig`
- `api.hcaptcha.com/getcaptcha/...`
- `POST /auth/verifyhcaptchapassive`
- signup 阶段还出现 `recaptcha/enterprise/...` 和 `auth/logclientdata`

第一组抓包补充了显式 challenge 分支：

- `POST /auth/validatecaptcha`，请求体为 binary，响应 HTML。
- 后续 `hcaptcha.paypal.com/1/api.js?...hCaptchaCallback...`、`hcaptcha_fph.html`、`hcaptchapassive_eval.html`。

这些包的原因：PayPal 在进入 guest signup 或高风险路径前确认浏览器/用户交互不是自动化，captcha 通过后才允许 `/pay` 继续返回下一页。

### 4.3 国家和 signup 跳转：BA token 转为 EC token

主链路中，captcha 通过和国家选择后出现：

- `GET /pay/api/countries?country.x=BR&paypal_client_cfci=...CAPTCHA_SOLVED`
- `GET /pay/?ssrt=...&token=BA-37R61061EU582084R&ul=1&ctxId=...&country.x=BR&_rsc=...`
- `POST /pay/?...country.x=BR&paypal_client_cfci=...CAPTCHA_SOLVED`

随后 PayPal 做 307/302 跳转：

- `GET /pay/checkout/signup/contact?...token=BA-37R61061EU582084R&...locale.x=pt_BR&country.x=BR`
- `GET /agreements/approve?...ba_token=BA-37R61061EU582084R`
- `GET /checkoutweb/signup?...ba_token=BA-37R61061EU582084R&token=EC-79N40798NE0202548&rcache=1&cookieBannerVariant=hidden`

这里的关键因果是：`BA-37...` 是 merchant billing agreement 入口；`EC-79...` 是 PayPal checkout/signup 内部继续处理的 token。后续 GraphQL 请求几乎都用 `token=EC-79N40798NE0202548`。

### 4.4 signup 初始化 GraphQL

#### `DeferredFeature`

请求参数：

- `operationName=DeferredFeature`
- `channel=WEB`
- `countryCodeAsString=BR`
- `integrationType=XoSignupAuth`
- `isBaslAsString=false`
- `isForcedGuest=false`
- `token=EC-79N40798NE0202548`

返回参数：

- `otpLoginContext.context`：base64 context blob。
- 解码语义从字段可见：包含 `csrfNonce`、`fnData.sourceId=IWC_LOGIN_APP`、`fnSessionId=EC-79N40798NE0202548`、FraudNet beacon URL、`env=prod`、locale `BR/pt`。
- `elmoExperiment.treatments[0]`: `experimentName=stsSecurityContext`、`treatmentName=Trmt_stsSecurityContext`
- `correlationId=f390270b1a9bb`

作用：为 guest signup 建立 OTP/security context，同时决定前端实验配置。

#### `CheckoutSessionDataQuery`

请求参数：

- `operationName=CheckoutSessionDataQuery`
- `token=EC-79N40798NE0202548`

返回参数：

- `allowedCardIssuers=[MASTER_CARD,VISA,AMEX]`
- `checkoutSessionType=BILLING_WITHOUT_PURCHASE`
- merchant: `name=OpenAI OpCo, LLC`、`merchantId=CF9F8FKTUYUAY`、`country=US`
- cart: `description=Subscription creation`、`intent=SALE`
- `cancelUrl.href=https://pm-redirects.stripe.com/return/acct_1HOrSwC6h1nxGoI3/pa_nonce_...?...status=cancel&token=EC-79...`
- billing/shipping/payer/email 为空，说明还没填表完成。
- `correlationId=f3902703f731e`

作用：把 PayPal checkout 与 Stripe merchant/OpenAI session 绑定，告诉 PayPal 当前是无即时购买金额展示的 billing agreement/subscription creation 流程。

#### `SupportedFundingSourcesQuery`

请求参数：

- `token=EC-79N40798NE0202548`
- `userCountry=BR`

返回参数：

- `supportedFundingSources` 共 6 项，每项 `issuers=[]`
- `correlationId=f3722247bd3cd`

作用：查询 BR 用户在该 checkout session 下可用资金来源。虽然 issuer 列表为空，但 PayPal 仍继续展示 card/signup 表单。

### 4.5 表单输入、地址和分期

这一段高频出现 `platform/tealeaftarget`、`xoplatform/logger/api/logger`、`c.paypal.com/v1/r/d/b/w`。这些请求记录页面加载、滚动、字段 focus/输入时间、按钮点击和 UI replay。它们的意义是辅助风控和排错，不承载最终支付授权。

关键业务查询有两类。

#### `InstallmentOptionsQuery`

请求参数：

- `buyerCountry=BR`
- `cardNumber=4916079570949387`
- `cardType=VISA`
- `token=EC-79N40798NE0202548`

返回参数：

- `term=1`
- `monthlyPayment.currencyCode=USD`
- `monthlyPayment.currencyValue=0.00`
- `totalCost.currencyValue=0.00`
- `feeReferenceId=null`
- `correlationId=f190733cab41b`

作用：PayPal 根据卡 BIN/国家/checkout token 查询分期选项。由于本 billing agreement 创建阶段不是立即扣款，返回金额为 `$ 0.00 USD`。

#### `AddressAutocompleteFromPostalCodeQuery`

请求参数：

- `country=BR`
- `postalCode=80020-100`
- `token=EC-79N40798NE0202548`
- `processMode=FASTCOMPLETION`
- `scope=STREET_LEVEL`

返回参数：

- `line1=Praça Tiradentes, 35`
- `line2=Centro`
- `city=Curitiba`
- `state=PR`
- `postalCode=80020-100`
- `correlationId=f984731024aad`

作用：邮编补全 billing address，之后 signup 请求把这些字段连同门牌/补充地址提交。

### 4.6 手机 OTP：多次 initiate，最后 confirm

主链路中用户多次发起手机号短信验证，说明前面几个号码/挑战未走到最终确认或被更换。

已读到的 initiate 代表包：

| 时间 | phoneNumber | 返回 authId | 返回 challengeId | state | correlationId |
|---|---|---|---|---|---|
| 02:39:54 | `21983993158` | `7338728242888031169` | `11294189198968144673` | `PENDING` | `f823638cbe2ac` |
| 02:40:45 | `91980209533` | `2654725419823253415` | `10373793876211693866` | `PENDING` | `f5912768ec8b2` |
| 02:41:43 | `21980909145` | `6779923567332685181` | `11530692301777733840` | `PENDING` | `f8524793039fc` |
| 02:42:31 | `21979582263` | `14699684359080251926` | `10415388630694778519` | `PENDING` | `f18757502d39c` |
| 02:43:00 | `21979582263` | 同类 initiate，具体响应未复读 | 同类 initiate | `PENDING` | - |
| 02:43:36 | 同类 initiate | 同类响应 | 同类响应 | `PENDING` | - |

最终 confirm 请求：

- `operationName=ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`
- `authId=15958040049748644572`
- `challengeId=16814992908704624784`
- `pin=798336`
- `token=EC-79N40798NE0202548`

最终 confirm 响应：

- `state=CONFIRMED`
- `authId=null`
- `challengeId=null`
- `correlationId=f515496b26676`

注意：最终 confirm 使用的 `authId/challengeId` 与已列出的早期 initiate 响应不一致，说明中间存在未逐项展开的重试/新 challenge；不能把 00400 的 `authId=7338...` 强行对应到 00476。能确认的是：OTP 阶段整体最终成功，signup 才继续提交。

### 4.7 `SignUpNewMemberMutation`：新账户/卡/身份信息提交，但返回卡共享限制错误

请求参数摘要：

- `token=EC-79N40798NE0202548`
- card:
  - `cardNumber=4916079570949387`
  - `expirationDate=11/2029`
  - `securityCode=633`
  - `type=VISA`
  - `productClass=CREDIT`
- user:
  - `country=BR`
  - `email=bkamwkcbajkwnmblbna@gmail.com`
  - `firstName=Matheus`
  - `lastName=Pereira`
  - `password=qaz2669164223wsx`
  - `phone.countryCode=55`
  - `phone.number=21984108976`
  - `phone.type=MOBILE`
- identity:
  - `dateOfBirth=12/12/2004`
  - `identityDocument.type=CPF`
  - `identityDocument.value=284.103.759-23`
- billingAddress:
  - `postalCode=80020-100`
  - `line1=Praça Tiradentes, 35, 450`
  - `line2=Centro`
  - `city=Curitiba`
  - `state=PR`
  - `country=BR`
  - `accountQuality.autoCompleteType=ANS`
  - `accountQuality.isUserModified=true`
- `contentIdentifier=BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms`
- `marketingOptOut=true`
- `supportedThreeDsExperiences=[IFRAME]`

响应参数：

- GraphQL `errors[0].message=INSTRUMENT_SHARING_LIMIT_EXCEEDED`
- `checkpoints=[addCard]`
- `errorData.0.field=cardNumber`
- `errorData.0.code=CARD_GENERIC_ERROR`
- `errorData.accessToken=...`
- `contingency=true`
- `path=[onboardAccount]`
- `data.onboardAccount=null`
- `statusCode=200`
- `correlationId=f6351058d5a95`

作用和影响：signup/onboarding 不是 HTTP 层失败，而是 PayPal 业务层拒绝把这张卡成功加入新账户，原因是 instrument sharing limit / generic card error。紧接着浏览器访问：

- `GET /checkoutweb/drop`
- `GET /webapps/hermes?...fromSignupLite=true&fallback=1&reason=Q0FSRF9HRU5FUklDX0VSUk9S`

`reason=Q0FSRF9HRU5FUklDX0VSUk9S` 解码语义是 `CARD_GENERIC_ERROR`。这说明 PayPal 前端把 signup/card 错误转入 Hermes/Hagrid fallback review，而不是停在 signup 页面。

### 4.8 Hagrid review 与 PayPal `authorize`

Hermes/Hagrid 页面加载后，FPTI 中出现：

- `page=main:billing:hagrid:billingwithoutpurchase:member:custom-review_hybrid`
- `cust=BYVBMMP4SPLDA`
- `party_id=BYVBMMP4SPLDA`
- `fltk=EC-79N40798NE0202548`
- `rsta=pt_BR`
- `ccpg=BR`

这表明 PayPal 已进入 member review 页面，买家标识是 `BYVBMMP4SPLDA`。

最终 PayPal 授权请求：

- `operationName=authorize`
- `billingAgreementId=EC-79N40798NE0202548`
- `fundingPreference.balancePreference=OPT_OUT`
- `legalAgreements={}`

响应：

- `billingAgreementToken=BA-37R61061EU582084R`
- `paymentAction=SALE`
- `returnURL.href=https://pm-redirects.stripe.com/return/acct_1HOrSwC6h1nxGoI3/pa_nonce_UpKZVShfniwoeTw86FIGaOsHoa8HN2j?status=success&token=EC-79N40798NE0202548`
- `buyer.userId=BYVBMMP4SPLDA`
- `correlationId=f924704324747`

作用：这是 PayPal 侧确认 billing agreement 的关键业务包。它把 PayPal 的 EC checkout token 授权为 BA billing agreement token，并给出 Stripe 的 return URL。注意这里 PayPal 返回 URL 中 `status=success`，代表 PayPal 页面流程完成并准备返回 Stripe；这不等于 Stripe/OpenAI 最终收款成功。

### 4.9 Stripe redirect bridge：PayPal success 回来后 Stripe 标记 failed

下一步浏览器访问：

- `GET https://pm-redirects.stripe.com/return/acct_1HOrSwC6h1nxGoI3/pa_nonce_UpKZVShfniwoeTw86FIGaOsHoa8HN2j?status=success&token=EC-79N40798NE0202548&ba_token=BA-37R61061EU582084R`

这里比 PayPal `authorize` 响应多了 `ba_token=BA-37R61061EU582084R`。这是 Stripe 的 PayPal return bridge：Stripe 接收 PayPal 回传的 EC token、BA token、状态和 nonce，然后决定 Checkout session 后续状态。

随后浏览器进入 OpenAI hosted payment page：

- `GET https://pay.openai.com/c/pay/cs_live_a124RoYSRP3IQQqvh1vmV2BL1TPlPQblflfSB31CbLLQsNBvIWrzOIW7fk?...payment_intent=pi_3TpftsC6h1nxGoI32SJNZnj6&payment_intent_client_secret=pi_3TpftsC6h1nxGoI32SJNZnj6_secret_7bw0w8u0cSCD6uXqy5RSZCeZ6&redirect_pm_type=paypal&redirect_status=failed&ui_mode=custom`

关键点：PayPal return bridge 输入中是 `status=success`，但 OpenAI/Stripe 页面 URL 已经是 `redirect_status=failed`。失败判定发生在 Stripe 处理 PayPal return 到 OpenAI Checkout 页面之间。

### 4.10 Stripe Checkout session init：最终失败原因

Stripe 侧主请求：

- `POST https://api.stripe.com/v1/payment_pages/cs_live_a124RoYSRP3IQQqvh1vmV2BL1TPlPQblflfSB31CbLLQsNBvIWrzOIW7fk/init`

该 request body 是 binary，原始读取不可直接解析。响应 JSON 是关键证据。

主要 session/customer/product 参数：

- Stripe account: `acct_1HOrSwC6h1nxGoI3`
- Checkout session id: `cs_live_a124RoYSRP3IQQqvh1vmV2BL1TPlPQblflfSB31CbLLQsNBvIWrzOIW7fk`
- Payment page id: `ppage_1Tpft8C6h1nxGoI3qWbzUxMt`
- merchant display: `OpenAI OpCo, LLC`
- support: `ar@openai.com`, `+14158799686`, `https://help.openai.com/`
- currency: `usd`
- amount / total: `2000`
- mode: `subscription`
- payment methods: `card`, `paypal`
- customer:
  - `id=cus_UpIXXZLZ3hS6p2`
  - `email=alexanderbarrientos398+3@googlemail.com`
  - `name=Joao Silva`
  - address: `Avenida Afonso Pena 1500`, `Belo Horizonte`, `MG`, `30130-005`, `BR`
- product:
  - `description/name=ChatGPT Plus Subscription`
  - `price=price_1MWW51C6h1nxGoI30udZUgPl`
  - `product=prod_N9DMeqbltqZchv`
  - recurring monthly, unit amount `2000`
- PaymentIntent:
  - `id=pi_3TpftsC6h1nxGoI32SJNZnj6`
  - `client_secret=pi_3TpftsC6h1nxGoI32SJNZnj6_secret_7bw0w8u0cSCD6uXqy5RSZCeZ6`
  - `amount=2000`
  - `currency=usd`
  - `description=Subscription creation`
  - `payment_method_types=[paypal]`
  - `payment_method_options.paypal.setup_future_usage=off_session`
  - `status=requires_payment_method`

最终错误字段：

- `last_payment_error.code=payment_method_provider_decline`
- `last_payment_error.decline_code=paypal_payment_declined`
- `last_payment_error.type=card_error`
- message:
  - `The transaction has been declined by PayPal.`
  - `Ask the user to use a different instrument to complete the payment, or switch to another payment method.`
  - `Debug ID: 7ece1f92853e8`
  - `PayPal issue: PAYER_CANNOT_PAY`
- `last_payment_error.payment_method.id=pm_1TpftjC6h1nxGoI3ChQuBOj3`
- `last_payment_error.payment_method.type=paypal`
- PayPal payment method object 中 `payer_email=null`、`payer_id=null`、`country=null`
- `payment_status=unpaid`
- Checkout `status=open`
- `return_url=https://chatgpt.com/checkout/verify?stripe_session_id=cs_live_...&processor_entity=openai_llc&plan_type=plus`

后续 `POST /v1/payment_pages/{cs_live}` 响应继续保持同一类 session 状态和同一产品/客户上下文，说明页面只是刷新/继续展示失败后的 Checkout 状态，并没有出现成功支付。

## 5. 所有主要包族在完整流程中的位置

| 包族 / path | 所属阶段 | 发送的关键参数 | 返回/效果 | 是否直接决定最终支付 |
|---|---|---|---|---|
| PayPal static assets (`/_next/static`, `paypalobjects.com`) | 页面加载 | chunk/css/font/image URL | 前端运行所需 JS/CSS/字体/图片 | 否 |
| `/agreements/approve` | BA 入口 | `ba_token=BA-37...` | PayPal approval HTML | 间接，是流程入口 |
| `/pay` RSC/fetch | BA 状态推进 | `ssrt`、`token=BA...`、`ul=1`、`country.x=BR`、`paypal_client_cfci`、`ctxId` | RSC/HTML/303 跳转 | 间接，决定下一页 |
| `/pay/api/countries` | 国家选择 | `country.x=BR` | country metadata JSON | 否 |
| `/pay/api/trpc/observability.handleClientEmit` | telemetry | `eventName`、error/log payload、`token=BA...` | `{}`/200 | 否 |
| `/mtr/1a7c...` | 风控 | `cmid=BA...`、`chnl=iwc-mxo`、browser timezone、encrypted body | `requestId`、`sealedResult` | 间接 |
| `c.paypal.com/v1/r/d/b/p1,p2,p3,pa,w` | FraudNet/browser fingerprint | `appId`、`correlationId`、URL、navigator、screen、plugins、GPU、timing | JSON/204/image beacon | 间接 |
| `/identity/di/log` | device intelligence log | DFPJS lifecycle events、`CMID=BA...` | 200 JSON | 否/间接 |
| hCaptcha/reCAPTCHA assets and APIs | challenge | sitekey、host、locale、captcha binary payload | captcha config/challenge/passive verification | 间接，解锁下一步 |
| `/auth/verifyhcaptchapassive` | PayPal passive captcha | binary body | 200 | 间接 |
| `/auth/logclientdata` | authchallenge telemetry | challenge/browser/client data | 200 JSON | 否/间接 |
| `/auth/validatecaptcha` | explicit captcha branch in first capture | binary body | HTML challenge result | 间接 |
| `/platform/tealeaftarget` | UI replay/session analytics | page id、gzip/binary or JSON UI events | 200 JSON/ping | 否/间接 |
| `/xoplatform/logger/api/logger` | PayPal checkout logger | UI page/action/error logs | JSON 200 | 否 |
| `/checkoutweb/signup` | guest signup page | `ba_token=BA...`、`token=EC...`、`locale.x=pt_BR`、`country.x=BR` | signup HTML | 进入 EC signup |
| GraphQL `DeferredFeature` | signup init | `token=EC...`、country/channel/integration | OTP context、experiment | 间接 |
| GraphQL `CheckoutSessionDataQuery` | merchant/session init | `token=EC...` | OpenAI merchant、cancelUrl、intent、checkout type | 是，绑定 merchant/session |
| GraphQL `SupportedFundingSourcesQuery` | funding source | `token=EC...`、`userCountry=BR` | supported funding source list | 间接 |
| GraphQL `InstallmentOptionsQuery` | card/installment | `buyerCountry=BR`、card number/type、`token=EC...` | 1 installment, zero due now | 间接 |
| GraphQL `AddressAutocompleteFromPostalCodeQuery` | address | `postalCode=80020-100`、`country=BR`、`token=EC...` | normalized Curitiba/PR address | 间接 |
| GraphQL `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` | OTP send | phone number、locale、`token=EC...` | `PENDING` + `authId/challengeId` | 间接 |
| GraphQL `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` | OTP confirm | `pin=798336`、auth/challenge、`token=EC...` | `state=CONFIRMED` | 间接，允许 signup |
| GraphQL `SignUpNewMemberMutation` | account/card onboarding | card、email、password、phone、CPF、DOB、billing address、`token=EC...` | `INSTRUMENT_SHARING_LIMIT_EXCEEDED` / `CARD_GENERIC_ERROR` | 是，暴露卡/账户问题 |
| `/checkoutweb/drop` and `/webapps/hermes` | fallback/review | `fromSignupLite=true`、`fallback=1`、`reason=CARD_GENERIC_ERROR` | Hermes/Hagrid review HTML | 间接，继续流程 |
| GraphQL `authorize` | PayPal BA authorization | `billingAgreementId=EC...`、`balancePreference=OPT_OUT` | `billingAgreementToken=BA...`、Stripe return URL、buyer id | 是，PayPal 返回 Stripe |
| `pm-redirects.stripe.com/return` | Stripe return bridge | Stripe account, nonce, `status=success`, `token=EC...`, `ba_token=BA...` | 302 到 OpenAI Checkout | 是，桥接 PayPal/Stripe |
| `pay.openai.com/c/pay/{cs_live}` | OpenAI hosted checkout | `payment_intent`、client secret、`redirect_pm_type=paypal`、`redirect_status=failed` | Checkout HTML | 是，显示失败状态 |
| `api.stripe.com/v1/payment_pages/{cs_live}/init` | Stripe session init | binary request; session id in URL | session JSON、PaymentIntent error | 是，最终失败证据 |
| `api.stripe.com/v1/payment_pages/{cs_live}` | Stripe session refresh | binary request; session id in URL | session JSON refresh | 是，保持失败状态 |
| Stripe JS/assets, `r.stripe.com/0`, `r.stripe.com/b`, `m.stripe.com/6`, hCaptcha, PerimeterX, Google Maps | Stripe UI/risk support | browser fingerprint, telemetry, captcha config, maps/address support | tracking responses, `muid/guid/sid` 等 | 否/间接 |

## 6. 参数传递链路

1. Merchant/OpenAI 先把用户带到 PayPal：`ba_token=BA-37R61061EU582084R`。
2. PayPal `/agreements/approve` / `/pay` 使用 BA token 建立 checkout UI 状态。
3. PayPal 在 guest signup 跳转中生成/暴露 `token=EC-79N40798NE0202548`。
4. signup 期间所有关键 GraphQL 都围绕 `EC-79...`：session data、funding source、地址、分期、OTP、signup。
5. signup/card onboarding 返回 `CARD_GENERIC_ERROR`，PayPal fallback 到 Hermes/Hagrid review。
6. Hagrid `authorize` 用 `billingAgreementId=EC-79...` 返回原 BA token 和 Stripe return URL。
7. 浏览器访问 Stripe return bridge，把 `status=success`、`token=EC-79...`、`ba_token=BA-37...` 交给 Stripe。
8. Stripe/OpenAI 页面 URL 变为 `redirect_status=failed`，并保留 `payment_intent=pi_3Tpfts...` 和 `client_secret=...`。
9. Stripe session init 返回最终原因：PayPal payment method provider decline，`PAYER_CANNOT_PAY`。

## 7. 为什么 PayPal 侧 success 但 Stripe/OpenAI 侧 failed

PayPal `authorize` 的 success 只说明 PayPal checkout 页面完成了 billing agreement 授权动作，并生成了可返回 Stripe 的 URL。Stripe 仍需要用返回的 BA token / PayPal payment method 去确认 PaymentIntent 或建立后续 subscription payment method。

Stripe 的最终响应明确显示：

- PaymentIntent 仍是 `requires_payment_method`
- `payment_status=unpaid`
- `last_payment_error.code=payment_method_provider_decline`
- `decline_code=paypal_payment_declined`
- PayPal issue 是 `PAYER_CANNOT_PAY`

因此失败点不是：

- PayPal 页面没加载；
- captcha 没过；
- OTP 没确认；
- Stripe session 找不到；
- OpenAI 页面打不开。

失败点是：Stripe 在 PayPal return 后调用/解释 PayPal 支付结果时，PayPal 作为 payment method provider 拒绝该付款/付款工具，且要求换一个 instrument 或支付方式。

## 8. 第一组抓包的补充结论

第一组 `roxy-paypal-20260705-102145` 没有完整 Stripe/OpenAI return，但它说明了 PayPal 在正式 signup 之前可能经历多轮相同 path family：

1. `BA-65R58767795383013` 初始进入 `/pay`。
2. 发送 `/mtr`、`c.paypal.com/v1/r/d/b/*`、`identity/di/log`、`observability`。
3. `Submit_Email` 后触发 hCaptcha / authchallenge。
4. 多次重载 `/pay` 和 captcha 页面。
5. 后续出现新 BA `BA-4PT6003892106623L` 和 `country.x=BR`。
6. 显式 `POST /auth/validatecaptcha` 后进入 PayPal authchallenge HTML/JS。

这组抓包支持主链路判断：PayPal 的大量重复包不是多个独立支付，而是同一 checkout 在风险挑战、国家切换、UI 重载和 telemetry 中反复发送同类请求。

## 9. 最终结论

完整链路是：OpenAI/Stripe 发起 PayPal billing agreement → PayPal 用 BA token 加载 checkout → 风控/captcha 通过 → 用户进入 BR guest signup → PayPal 收集卡、身份、地址、OTP → signup/card onboarding 出现 `INSTRUMENT_SHARING_LIMIT_EXCEEDED` / `CARD_GENERIC_ERROR` → PayPal fallback 到 Hagrid review → PayPal `authorize` 返回 `BA-37R61061EU582084R` 和 Stripe return URL → Stripe/OpenAI 接收 PayPal return → Stripe PaymentIntent 因 PayPal provider decline 变为失败。

最终失败原因以 Stripe 响应为准：

- `payment_method_provider_decline`
- `paypal_payment_declined`
- `PAYER_CANNOT_PAY`
- PayPal Debug ID: `7ece1f92853e8`

可操作含义：如果要复现/修复该支付链路，不应优先排查静态资源、captcha 或 OpenAI Checkout 页面加载，而应更换 PayPal payer/payment instrument，或拿 Debug ID `7ece1f92853e8` 到 PayPal 侧查询为什么该 payer/instrument 不能支付。
