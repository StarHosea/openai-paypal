# PayPal 风控封控点、指纹变量与真实浏览器判断分析

## 1. 范围和口径

本文件继续基于两个 roxy 原始抓包目录做人工分析：

- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-102145`
- `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-103548`

重点回答三件事：

1. 哪些包是 PayPal 风控/封控相关包。
2. 这些包里哪些变量是风控评分、挑战或拒绝判断会用到的变量。
3. 浏览器指纹、设备信息、真实浏览器判断证据记录在哪里。

这里的“封控点”分三类：

- **确认拒绝点**：响应里明确返回业务拒绝或支付提供方拒绝。
- **挑战/门禁点**：需要 captcha、OTP 或安全挑战通过才继续。
- **评分采集点**：FraudNet、mtr、DFP、Tealeaf、FPTI、Datadog、DataDome 等只负责采集/打分，单包本身不一定返回“拒绝”。

## 2. 结论先行

本次链路里最关键的 PayPal 风控/封控点是：

| 等级 | 代表包 | 结论 |
|---|---|---|
| 确认拒绝 | `00478` `SignUpNewMemberMutation` | PayPal signup/绑卡阶段返回 `INSTRUMENT_SHARING_LIMIT_EXCEEDED`，checkpoint 是 `addCard`，字段是 `cardNumber`，错误码 `CARD_GENERIC_ERROR`。这是 PayPal 侧明确的业务拒绝点。 |
| 确认最终拒绝 | Stripe `00562` session init 响应 | Stripe 解释 PayPal 最终拒绝：`payment_method_provider_decline` / `paypal_payment_declined` / `PAYER_CANNOT_PAY`，Debug ID `7ece1f92853e8`。这是最终支付失败点。 |
| 挑战门禁 | hCaptcha / reCAPTCHA / `/auth/validatecaptcha` / `/auth/verifyhcaptchapassive` | PayPal 在 `/pay` 和 authchallenge 阶段反复要求 passive 或显式 captcha。第二组 passive captcha 通过后 `/pay` 才以 `CAPTCHA_SOLVED` 继续。 |
| 挑战门禁 | `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` / `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` | 手机 OTP 是风险触发的二次确认。最终 `state=CONFIRMED`，说明此门禁通过。 |
| 评分采集 | `c.paypal.com/v1/r/d/b/p1,p2,pa,w,p3` | PayPal FraudNet 浏览器/设备/行为/地理位置指纹。返回 `vf/sc/ddi` 等设备 token。 |
| 评分采集 | `/mtr/1a7c...` | PayPal mtr 采集，携带 `cmid`、timezone，响应 `sealedResult`。 |
| 评分采集 | `/identity/di/log` | DFPJS device intelligence 生命周期日志，绑定 `CMID=BA/EC token`。 |
| 评分采集 | `ddbm2.paypal.com/js/` | DataDome 类人机/设备采集，响应设置 `datadome` cookie。 |
| 评分采集 | `/platform/tealeaftarget`、`t.paypal.com/ts`、Datadog RUM | 记录 UI 行为、页面性能、客户端异常、浏览器环境和真实交互证据。 |

因此这次不是“单纯验证码没过”。验证码/OTP 这类门禁有通过记录；真正硬拒绝集中在 **PayPal addCard/signup** 和 **PayPal provider decline / PAYER_CANNOT_PAY**。

## 3. PayPal 风控/封控包族和关键变量

### 3.1 `/pay` 状态推进包：把风控状态写回 checkout

代表包：

- `00069_POST_www.paypal.com_pay_...paypal_client_cfci_modxo_vaulted_not_recurring-no_.bin`
- `00190_POST_www.paypal.com_pay_...paypal_client_cfci_modxo_vaulted_not_recurring-CAPTCHA_SOLVED.bin`
- 第一组同类 `00075`、`00085`、`00284`、`00287`、`00876`

关键变量：

| 变量 | 示例 | 作用 |
|---|---|---|
| `token` | `BA-37R61061EU582084R` | PayPal billing agreement 外层会话。 |
| `ssrt` | `1783218992321` | 页面/服务端状态时间戳类参数。 |
| `ul` | `1` | 当前 `/pay` 流程参数。 |
| `paypal_client_cfci` | `modxo_vaulted_not_recurring-no_interaction` / `...-CAPTCHA_SOLVED` / `...-Submit_Email` | 前端当前动作或风控状态。`CAPTCHA_SOLVED` 是重要门禁状态。 |
| `country.x` | `BR` | 国家选择进入 BR 本地 signup/合规路径。 |
| `ctxId` | `c5d20d7d-...` | checkout 前端上下文 id。 |

判断：这些包不是最终支付授权，但它们决定 PayPal 前端能否从 BA `/pay` 继续到 signup/Hermes。`paypal_client_cfci=...CAPTCHA_SOLVED` 说明 captcha 门禁结果已经被带回 `/pay`。

### 3.2 mtr：密封设备/环境结果

代表包：

- `00054 GET /mtr/1a7c...?...q=QBzalmMuDFJIiZNebIWt`
- `00066 POST /mtr/1a7c...?chnl=iwc-mxo&cmid=BA-37...&btz=Pacific/Honolulu&emf=true&ci=js/3.12.1&q=...`
- 第一组同类 `00074`、`00281`、`00469`

关键变量：

| 变量 | 示例 | 作用 |
|---|---|---|
| `cmid` | `BA-37R61061EU582084R` / `BA-65R...` / `BA-4PT...` | 把设备采集结果绑定到 PayPal checkout token。 |
| `chnl` | `iwc-mxo` | Modular checkout channel。 |
| `btz` | `Pacific/Honolulu` | 浏览器时区。 |
| `ci` | `js/3.12.1` | 采集库版本。 |
| `q` | `QBzalmMuDFJIiZNebIWt` | mtr challenge/query id。 |
| 响应 `requestId` | `1783218996246.qa8rTj` | 服务端追踪本次采集。 |
| 响应 `sealedResult` | 长密封字符串 | 前端采集后封装的设备/风险结果，明文不可直接读。 |

判断：`sealedResult` 是典型评分采集结果，不直接显示具体风险分，但它是 PayPal 服务端判断设备/环境可信度的输入之一。

### 3.3 FraudNet：核心浏览器/设备/行为指纹

代表包：

- `00080_POST_c.paypal.com_v1_r_d_b_p1.txt`
- `00077_POST_c.paypal.com_v1_r_d_b_p2.txt`
- `00078_POST_c.paypal.com_v1_r_d_b_pa.txt`
- `00079_POST_c.paypal.com_v1_r_d_b_w.txt`
- `c6.paypal.com/v1/r/d/b/p3?...`
- Hagrid 阶段同类：`00529`、`00530`

#### `p1` 变量

| 字段 | 抓包值/形态 | 风控意义 |
|---|---|---|
| `appId` | `IWC_NEXT_CHECKOUT`；Hagrid 为 `BILLINGUINODEWEB_BILLINGWITHOUTPURCHASE_LITE` | 当前 PayPal 页面/产品线。 |
| `correlationId` | BA 或 EC token | 指纹和 checkout 会话绑定。 |
| `payload.URL` | 当前 PayPal URL | 判断指纹采集发生在哪个页面。 |
| `navigator.userAgent` | Windows + Chrome/150 | 浏览器身份。 |
| `navigator.platform` | `Win32` | OS/平台。 |
| `navigator.vendor` | `Google Inc.` | 浏览器厂商。 |
| `navigator.cookieEnabled` | `true` | cookie 是否可用。 |
| `navigator.onLine` | `true` | 网络状态。 |
| `navigator.language` | `en-US` | 浏览器语言。 |
| `screen` | `1536x864`、`colorDepth=24` | 显示器基础指纹。 |
| `window` | `innerWidth/innerHeight/outerWidth/outerHeight/devicePixelRatio` | 窗口与 DPR 指纹，判断 headless/自动化时常用。 |
| `connectionData` | `effectiveType=3g`、`rtt=350/650`、`downlink=0.4` | 网络质量特征。 |
| `tz` / `tzName` | `-36000000` / `Pacific/Honolulu` | 时区一致性。 |
| `activeXDefined`、`flashVersion` | `false`、`0.0.0` | 老式插件/浏览器能力。 |
| `pkc` | `uvpa/cma/cc/ht/pkp` | passkey/WebAuthn/credential capability 类能力位。 |
| `asynchk.ph2` | hash 字符串 | 异步环境校验 hash。 |

#### `p2` 变量

| 字段 | 抓包值/形态 | 风控意义 |
|---|---|---|
| `plugins` | Chrome PDF Viewer、Chromium PDF Viewer、Microsoft Edge PDF Viewer、PDF Viewer、WebKit built-in PDF | 插件列表，判断浏览器真实度和一致性。 |
| `cv.h` | canvas 小图/base64 hash | canvas 指纹。 |
| `vm.cores` | `12` | CPU core 数。 |
| `vm.gpu.vendor/renderer` | `Google Inc. (AMD)` / `ANGLE (AMD Radeon HD 5570... Direct3D11)` | WebGL/GPU 指纹。 |
| `vm.jsMem` | used/total/limit JS heap | 浏览器内存环境。 |
| `vm.perfNav` | navigationStart、connectStart、responseStart 等 | 真实页面加载 timing。 |
| `timing` | 各采集模块耗时 | 采集执行耗时本身也是指纹。 |

#### `pa/w/p3` 变量

| 包 | 关键变量 | 风控意义 |
|---|---|---|
| `pa` | `latitude=21.307796`、`longitude=-157.859187`、`permissionTime`、`locationTime` | 地理位置权限与坐标。 |
| `w` | `pkc`、`slt`、`uvpat`、`cmat`、`capt` | 行为/能力状态与时间统计。 |
| `GET /w?d={rDT...}` | `rDT` 长时间序列 | 行为/时序特征。 |
| `p3` | `f=BA/EC token`、`s=source` | pixel/get 方式补充采集。 |

#### FraudNet 响应变量

| 响应字段 | 示例 | 意义 |
|---|---|---|
| `vf` | 长 token | visitor/fingerprint token。 |
| `sc` | 长 token | session/score token。 |
| `ddi` | 长 token | device data intelligence token。 |
| `error` | `false` | 采集成功。 |

判断：FraudNet 是本次最完整的浏览器指纹来源。它不是单独“封号包”，但 PayPal 业务决策会把它和会话、账号、卡、手机号、地理位置、captcha/OTP 结果一起看。

### 3.4 DFPJS `/identity/di/log`

代表包：

- `00096_POST_www.paypal.com_identity_di_log.txt`
- 第一组 `00109`、`00307`、`00486`

关键变量：

| 字段 | 示例 | 作用 |
|---|---|---|
| `event` | `DFPJS_LIB_LOADED`、`DFPJS_VENDOR_INVOKED`、`DFPJS_VENDOR_RESPONSE_RECEIVED`、`DFPJS_EDGE_MAPPING_COMPLETE` | 设备识别库生命周期。 |
| `CMID` | `BA-37R61061EU582084R` | 绑定当前 PayPal 会话。 |
| `browser_timezone` | `Pacific/Honolulu` | 与 FraudNet/mtr 时区交叉校验。 |
| `timestamp` | `178321899...` | 采集时序。 |

判断：这是 device intelligence 是否运行成功的日志。它确认 PayPal 的 DFPJS 供应商调用完成。

### 3.5 DataDome `ddbm2.paypal.com/js/`

代表包：

- `00176_POST_ddbm2.paypal.com_js.bin`
- `00196_POST_ddbm2.paypal.com_js.bin`
- `00524_POST_ddbm2.paypal.com_js.bin`
- 第一组同类 `00177`、`00199`、`00390`、`00856`、`00872`、`00909`、`00951`

请求体是二进制，响应形态：

```json
{"status":200,"cookie":"datadome=...; Max-Age=2592000; Domain=.paypal.com; Path=/; Secure; SameSite=None"}
```

关键变量：

| 变量 | 作用 |
|---|---|
| binary request body | 浏览器/设备/行为采集数据，明文不可直接读。 |
| `datadome` cookie | DataDome 对当前浏览器/设备会话的长期标识。 |
| `Domain=.paypal.com`、`SameSite=None` | 允许后续 PayPal 页面继续携带该设备判断 cookie。 |

判断：这类包属于人机/设备可信度采集。响应设置 cookie 表示采集成功并写入后续请求上下文。

### 3.6 hCaptcha / reCAPTCHA / authchallenge：显式门禁

代表包：

- `auth/createchallenge/.../hcaptchapassive.js?_sessionID=...`
- `hcaptchapassive.html`
- `api.hcaptcha.com/checksiteconfig?...sitekey=884d15d9-b649-4bbb-8d1c-2d6f0eed75eb`
- `api.hcaptcha.com/getcaptcha/...`
- `POST /auth/verifyhcaptchapassive`
- 第一组显式 `POST /auth/validatecaptcha`：`00547`、`00584`、`00612`、`00648`、`00679`、`00698-00701`
- `www.recaptcha.net/recaptcha/enterprise/reload...`

关键变量：

| 字段 | 示例 | 作用 |
|---|---|---|
| `_sessionID` / `nsid` | `fmjisagjua84gNU5g5kksPh2H-kV6oDN` | authchallenge 会话。 |
| `_csrf` | 长 token | 表单/挑战提交 CSRF。 |
| `sitekey` | `884d15d9-b649-4bbb-8d1c-2d6f0eed75eb`、`bf07db68-...` | hCaptcha 站点配置。 |
| `captchaState` | `CLIENT_SIDE_HCAPTCHA_PASSIVE_SERVED`、`...SCRIPT_ONLOAD`、`...PASSIVE_SOLVED`、`CLIENT_SIDE_HCAPTCHA_SERVED`、`CLIENT_SIDE_RECAPTCHA_SERVED`、`CLIENT_SIDE_RECAPTCHA_NOT_REACHABLE` | captcha 生命周期状态。 |
| HTML hidden `_requestId`、`_hash` | `gdFTV_...`、`s4Gb...` | 显式挑战表单状态。 |
| `hcaptcha_eval_start_time_utc` | `1783218241793` | challenge 开始时间。 |
| `data-captcha-type` | `hcaptcha` | 挑战类型。 |
| `data-jse` | hash/id | JS evaluation/session evidence。 |

判断：第二组里 passive captcha 最终带着 `paypal_client_cfci=...CAPTCHA_SOLVED` 继续 `/pay`，所以它不是最终失败点。第一组里多轮 `auth/validatecaptcha` 和 reCAPTCHA/hCaptcha 切换说明那次路径存在显式挑战和反复校验。

### 3.7 Tealeaf `/platform/tealeaftarget`：UI 行为录制

代表包：

- `00185_POST_www.paypal.com_platform_tealeaftarget_...CAPTCHA_SOLVED.txt`
- `00287/00296/00304/...` signup 阶段
- 第一组 `00546_POST_www.paypal.com_platform_tealeaftarget_Content-Type_application_2Fjson_X-PageId_...bin`

关键变量：

| 字段 | 示例 | 作用 |
|---|---|---|
| `X-PageId` | `P.DPSURPSFLB9YKX5PKFFGKMRJT7BV` | 页面/录制上下文。 |
| `X-Tealeaf` | `device (UIC) Lib/6.4.177` | Tealeaf 客户端库版本。 |
| `X-Tealeaf-MessageTypes` | `1,2` | 上报消息类型。 |
| `X-Tealeaf-SaaS-AppKey` | app key | SaaS 应用标识。 |
| `X-Tealeaf-SaaS-TLTSID` / `X-Tealeaf-TLTDID` | 长数字 id | Tealeaf session/device id。 |
| `Content-Encoding=gzip` | gzip | 行为 body 压缩。 |
| 响应 `id` | `810498215636` 等 | 服务端接受的目标 id。 |

判断：Tealeaf 记录输入、点击、焦点、页面变化等 UI replay 数据。它用于风控辅助和排错，不是单独的业务授权接口。

### 3.8 `t.paypal.com/ts` FPTI：最直观的真实浏览器字段

代表包：

- `/ts?...page=main:modularcheckoutnodeweb:pay...`
- `/ts?...page=main:authchallenge::auth:validatecaptcha...`
- Hagrid 阶段 `/ts?...page=main:billing:hagrid...`

重要字段：

| 字段 | 抓包形态 | 意义 |
|---|---|---|
| `pgrp/page/comp/tsrce` | `main:authchallenge::auth:validatecaptcha`、`authchallengenodeweb` | 当前页面和组件。 |
| `pxpguid` | `30083eff19f647e0e8bdd331ff08bdff` | PayPal 页面访客 id。 |
| `nsid` | session id | PayPal 页面 session。 |
| `rsta/ccpg` | `pt_BR` / `BR` | 页面 locale/country。 |
| `sw/sh/dw/dh/bw/bh/cd` | 屏幕、document、browser viewport、color depth | 浏览器显示环境。 |
| `view` | `t10/t11/tcp/et/nt/bt` | 页面加载/网络 timing。 |
| `ads_client_data` | URL 编码的 Navigator/History/screen/window/plugins/hardwareConcurrency 等 | 真实浏览器判断的直接字段集合。 |

`ads_client_data` 中可见的真实浏览器相关信号包括：

- `Navigator(appName=Netscape, appVersion=Windows NT 10.0..., userAgent=Chrome/150...)`
- `webdriverfalse`
- `deviceMemory8`
- `geolocation(Available)`
- `language=en-US`
- `onLine=true`
- `platform=Win32`
- `History(2/3)`
- `screen(1536,864,1536,864,24,24)`
- `window(... Chrome=[object Object] ... callPhantom=undefined ... _phantom=undefined ... devicePixelRatio=1)`
- PDF 插件列表
- `hardwareConcurrency(12)`

判断：这是 PayPal authchallenge 页面里最直观的“是不是正常浏览器环境”的采集字段之一。注意它只是证据集合，不代表某一个字段就能单独判定真实或虚假。

### 3.9 Datadog RUM / PayPal observability：异常和交互监控

代表包：

- `/pay/api/trpc/observability.handleClientEmit`
- `browser-intake-us5-datadoghq.com/api/v2/rum`
- `browser-intake-us5-datadoghq.com/api/v2/replay`

关键变量：

| 字段 | 示例 | 作用 |
|---|---|---|
| Datadog `session.id` / `view.id` / `tab.id` | UUID | RUM session/view/tab 关联。 |
| `source` | `browser` | 事件来源。 |
| `connectivity.effective_type` | `3g` | 网络环境。 |
| `device.locale/time_zone` | `en-US` / `Pacific/Honolulu` | 浏览器 locale/timezone。 |
| `privacy.replay_level` | `mask` | session replay 已启用但输入脱敏。 |
| `event_name=modxo_window_fetch_tampered` | fetch 被包装/篡改检测 | 可能是安全/调试信号，也可能由 PayPal 自家 captcha 脚本触发。 |
| `identity_meta_webauthn_support_check_false` | WebAuthn support 检查 | 能力检测。 |
| `isCookiesDisabled=false`、`isIframe=false` | 环境状态 | 真实浏览器/嵌入环境判断辅助。 |

判断：Datadog 主要是观测和排错，但它也记录环境异常、fetch tampering、WebAuthn 能力、cookie/iframe 状态，可作为风控辅助信息。

## 4. GraphQL 里的风控变量

### 4.1 `DeferredFeature` / `otpLoginContext`

代表包：`00244_POST_www.paypal.com_graphql_DeferredFeature.txt`

请求变量：

- `channel=WEB`
- `countryCodeAsString=BR`
- `integrationType=XoSignupAuth`
- `isBaslAsString=false`
- `isForcedGuest=false`
- `token=EC-79N40798NE0202548`

响应 `otpLoginContext.context` 是 base64 context blob。已能从字段语义看到：

- `csrfNonce`
- `fnData.sourceId=IWC_LOGIN_APP`
- `fnSessionId=EC-79N40798NE0202548`
- `beaconUrl`
- `enableTypingSpeed=true`
- `fnUrl=https://c.paypal.com/da/r/fb.js`
- `env=prod`
- locale `BR/pt`

判断：这里把 FraudNet beacon、typing speed、OTP/security context 绑到 EC token，是 signup 风控上下文初始化点。

### 4.2 OTP 风控二次确认

代表包：

- `00400_POST...InitiateRiskBasedTwoFactorPhoneConfirmationMutation.txt`
- `00476_POST...ConfirmRiskBasedTwoFactorPhoneConfirmationMutation.txt`

关键变量：

| 变量 | 示例 | 作用 |
|---|---|---|
| `phoneNumber` | `21983993158` 等 | 风险手机号验证目标。 |
| `phoneCountry` | `BR` | 手机国家。 |
| `locale` | `{country: BR, lang: pt}` | 本地化。 |
| `authId` / `challengeId` | 长数字 id | OTP challenge 上下文。 |
| `pin` | OTP 值 | 用户提交的一次性验证码。 |
| `state` | `PENDING` → `CONFIRMED` | 风控门禁状态。 |

判断：OTP 是明确门禁，但本次最终 `CONFIRMED`，所以 OTP 不是最终失败原因。

### 4.3 `SignUpNewMemberMutation`：PayPal 明确业务拒绝点

代表包：

- 请求：`00478_POST_www.paypal.com_graphql_SignUpNewMemberMutation.txt`
- 响应：`00478_resp_200_fetch_www.paypal.com_graphql_SignUpNewMemberMutation_4762a5dc0a.json`

关键请求变量：

| 变量类别 | 字段 | 风控意义 |
|---|---|---|
| 会话 | `token=EC-79N40798NE0202548` | signup/checkout 会话。 |
| 卡 | `cardNumber`、`expirationDate`、`securityCode`、`type=VISA`、`productClass=CREDIT` | 绑卡/支付工具风险、卡共享、卡 BIN、3DS/issuer 检查。 |
| 用户 | `email`、`firstName`、`lastName`、`password` | 新账号注册身份。 |
| 手机 | `countryCode=55`、`number`、`type=MOBILE` | 联系方式和 OTP 风控关联。 |
| 身份 | `dateOfBirth`、`identityDocument.type=CPF`、`identityDocument.value` | BR KYC/合规。 |
| 地址 | `billingAddress`、`postalCode`、`city/state/country` | 账单地址、卡/身份/地理一致性。 |
| 地址质量 | `accountQuality.autoCompleteType=ANS`、`isUserModified=true` | 地址是否由自动补全生成、是否被用户修改。 |
| 合规 | `contentIdentifier`、`legalAgreements`、`marketingOptOut` | 条款/合规/营销选择。 |
| 3DS | `supportedThreeDsExperiences=[IFRAME]` | 可用 3DS 展示方式。 |

关键响应变量：

- `message=INSTRUMENT_SHARING_LIMIT_EXCEEDED`
- `checkpoints=[addCard]`
- `errorData.0.field=cardNumber`
- `errorData.0.code=CARD_GENERIC_ERROR`
- `contingency=true`
- `data.onboardAccount=null`
- `correlationId=f6351058d5a95`

判断：这是 PayPal 侧明确封控/拒绝点。它不是 HTTP 失败，而是 GraphQL 200 里的业务拒绝；拒绝对象是 addCard/cardNumber。

### 4.4 `authorize`：不是拒绝点，但把结果交给 Stripe

代表包：`00540_POST_www.paypal.com_graphql.txt`

请求变量：

- `billingAgreementId=EC-79N40798NE0202548`
- `fundingPreference.balancePreference=OPT_OUT`
- `legalAgreements={}`

响应变量：

- `billingAgreementToken=BA-37R61061EU582084R`
- `paymentAction=SALE`
- `returnURL.href=https://pm-redirects.stripe.com/return/...status=success&token=EC-79...`
- `buyer.userId=BYVBMMP4SPLDA`
- `correlationId=f924704324747`

判断：PayPal 页面流程这里给了 `status=success` return URL，但这只表示 PayPal 页面授权动作完成，不等于最终 payment method 被 Stripe/PayPal provider 接受。

## 5. 最终拒绝：Stripe 侧记录的 PayPal provider decline

代表响应：

- `00562_resp_200_fetch_api.stripe.com_v1_payment_pages_..._init.json`

关键字段：

- `last_payment_error.code=payment_method_provider_decline`
- `last_payment_error.decline_code=paypal_payment_declined`
- message: `The transaction has been declined by PayPal.`
- message: `PayPal issue: PAYER_CANNOT_PAY`
- Debug ID: `7ece1f92853e8`
- `payment_method.type=paypal`
- PayPal payment method object 中 `payer_email=null`、`payer_id=null`、`country=null`
- `PaymentIntent.status=requires_payment_method`
- `payment_status=unpaid`

判断：最终拒绝发生在 Stripe 消费 PayPal return 之后。PayPal 页面给 Stripe 的 redirect 是 success，但 Stripe 后端/PayPal provider 结算判断给出 `PAYER_CANNOT_PAY`。

## 6. 本抓包里如何判断“真实浏览器”

只从抓包看，真实浏览器不是靠单一变量判断，而是靠多套采集结果之间是否一致、是否有真实执行痕迹。

### 6.1 支持真实浏览器的证据

| 证据 | 出处 | 说明 |
|---|---|---|
| `source=browser`、Datadog session/view/tab UUID | Datadog RUM | 页面在浏览器上下文中运行。 |
| `navigator` 完整 | FraudNet `p1`、FPTI `ads_client_data` | UA、platform、vendor、language、cookieEnabled、onLine 等齐全。 |
| `webdriverfalse` | FPTI `ads_client_data` | 页面看到 `navigator.webdriver=false`。 |
| `window.Chrome=[object Object]`、`callPhantom=undefined`、`_phantom=undefined` | FPTI `ads_client_data` | 常见自动化/Phantom 检测字段。 |
| PDF 插件列表 | FraudNet `p2`、FPTI | Chrome/Chromium/Edge/PDF/WebKit PDF plugins。 |
| GPU/WebGL | FraudNet `p2` | ANGLE + AMD Radeon HD 5570 + D3D11。 |
| Canvas hash | FraudNet `p2.cv.h` | canvas 指纹存在。 |
| JS heap/performance navigation timing | FraudNet `p2` | 页面加载 timing 和 JS 内存可采集。 |
| `deviceMemory8`、`hardwareConcurrency(12)` | FPTI `ads_client_data` / FraudNet `p2` | 设备能力字段。 |
| hCaptcha/recaptcha frame 和 hsw.js 加载 | captcha 包族 | 挑战脚本真实执行。 |
| Tealeaf gzip UI events | `/platform/tealeaftarget` | 有页面行为录制。 |
| DataDome cookie | `ddbm2.paypal.com/js/` | 人机/设备采集成功并设置 cookie。 |
| FraudNet `vf/sc/ddi` | `p1/p2` 响应 | 指纹采集成功返回设备 token。 |

### 6.2 风险不一致或可疑信号

| 信号 | 抓包表现 | 为什么可疑 |
|---|---|---|
| 地理/身份不一致 | 浏览器 `tzName=Pacific/Honolulu`，FraudNet 坐标约 Honolulu；业务国家/手机号/CPF/地址是 BR | 设备地理位置与 PayPal signup 国家身份不一致。 |
| 语言/国家不一致 | `navigator.language=en-US`，页面 `pt_BR`、`country.x=BR` | 浏览器环境和本地化身份不完全一致。 |
| 网络质量异常组合 | Windows desktop + `effectiveType=3g`、低 downlink | 不是一定有问题，但会进入一致性评分。 |
| 多轮 captcha/authchallenge | 第一组多次 `/auth/validatecaptcha`，captcha 状态从 hCaptcha 到 reCAPTCHA | 风险挑战反复触发。 |
| `modxo_window_fetch_tampered` | Datadog RUM | fetch 被包装/改写被记录；可能来自 PayPal/captcha 自身脚本，也可能是环境异常信号。 |
| 多手机号 OTP initiate | 多个不同 phoneNumber 发起 OTP | 风险/注册行为异常信号。 |
| 卡共享限制 | `INSTRUMENT_SHARING_LIMIT_EXCEEDED` | PayPal 明确认为卡/支付工具触发限制。 |
| 最终 `PAYER_CANNOT_PAY` | Stripe session | PayPal provider 拒绝该 payer/instrument 支付。 |

### 6.3 判断方法总结

对这类抓包，判断是否真实浏览器应看以下交叉一致性：

1. **浏览器身份一致性**：UA、platform、vendor、plugins、GPU、screen/window、devicePixelRatio 是否互相匹配。
2. **自动化迹象**：`webdriver`、Phantom、Chrome object、插件列表、canvas、WebGL、permissions、performance timing 是否呈现正常浏览器特征。
3. **采集链完整性**：FraudNet 是否返回 `vf/sc/ddi`，mtr 是否返回 `sealedResult`，DataDome 是否设置 cookie，captcha/hsw 是否加载。
4. **行为真实性**：Tealeaf、FPTI、Datadog 是否有页面加载、点击、输入、等待、challenge 的时序，而不是只出现业务 POST。
5. **环境与身份一致性**：timezone、geolocation、browser language、PayPal country、手机号国家、CPF/地址、卡 BIN/issuer 是否一致。
6. **业务历史/支付工具风险**：卡是否触发 sharing limit，payer 是否可支付，provider 是否返回 `PAYER_CANNOT_PAY`。

本次抓包里，浏览器执行痕迹是完整的，但业务身份/地理/支付工具风险很重：Honolulu 时区/坐标 + BR 身份资料 + 多手机号 OTP + signup addCard 被拒 + 最终 PayPal provider decline。也就是说，它不像是“页面没跑起来”，而是“PayPal 风控和支付工具层最终不接受该 payer/instrument”。

## 7. 按封控点排序的排查索引

| 优先级 | 要看什么 | 代表文件 |
|---|---|---|
| 1 | 最终 PayPal 拒绝原因 | `00562_resp_200_fetch_api.stripe.com_v1_payment_pages_..._init.json` |
| 2 | PayPal signup/addCard 拒绝 | `00478_POST_www.paypal.com_graphql_SignUpNewMemberMutation.txt` 与 `00478_resp_200_fetch_...json` |
| 3 | OTP 是否通过 | `00400/00415/00433/00447/00456/00466` initiate 响应，`00476` confirm 响应 |
| 4 | captcha 是否通过/反复挑战 | `00099` verify passive，第一组 `00547/00584/00612/00648/00679/00698-00701` validatecaptcha，auth logclientdata |
| 5 | FraudNet 指纹是否完整 | `00080/00077/00078/00079`，Hagrid `00529/00530` |
| 6 | mtr sealed result | `00066` 响应 `sealedResult` |
| 7 | DFPJS / DataDome 是否运行 | `00096`、`00176/00524` |
| 8 | UI 行为和真实浏览器证据 | `/platform/tealeaftarget`、`t.paypal.com/ts`、Datadog RUM |

## 8. 最终判断

这次 PayPal 风控链路可以概括为：

1. `/pay` 阶段先采集设备、浏览器、网络、时区、地理位置和行为。
2. PayPal 触发 captcha/passive challenge，第二组通过后以 `CAPTCHA_SOLVED` 继续。
3. signup 阶段建立 FraudNet/OTP context，并要求手机号 OTP。
4. OTP 最终确认成功。
5. `SignUpNewMemberMutation` 在 addCard checkpoint 明确拒绝卡：`INSTRUMENT_SHARING_LIMIT_EXCEEDED` / `CARD_GENERIC_ERROR`。
6. PayPal fallback 到 Hagrid review 后仍返回 Stripe success URL。
7. Stripe 消费 PayPal return 后得到最终 provider decline：`PAYER_CANNOT_PAY`。

所以主要封控点不是静态资源、页面加载、captcha JS 或 OpenAI Checkout 页面，而是 PayPal 的 **设备/身份/支付工具综合风控**，其中硬证据是 `addCard` 拒绝和最终 `PAYER_CANNOT_PAY`。
