# 当前协议实现 vs 真实浏览器 PayPal 风控流量差异分析

## 1. 范围和口径

本文对比两类材料：

- 真实浏览器 roxy 抓包基线：
  - `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-102145`
  - `/home/nonewhite/paypal-pay/captures/roxy-paypal-20260705-103548`
- 当前协议代码实现：
  - `/home/nonewhite/paypal-pay/config.py`
  - `/home/nonewhite/paypal-pay/paypal/session.py`
  - `/home/nonewhite/paypal-pay/paypal/flow.py`
  - `/home/nonewhite/paypal-pay/paypal/fingerprint.py`
  - `/home/nonewhite/paypal-pay/paypal/analytics.py`
  - `/home/nonewhite/paypal-pay/paypal/tealeaf.py`
  - `/home/nonewhite/paypal-pay/paypal/graphql.py`
  - `/home/nonewhite/paypal-pay/paypal/capsolver.py`
  - `/home/nonewhite/paypal-pay/paypal/models.py`
  - `/home/nonewhite/paypal-pay/paypal/traffic_recorder.py`

分析方式是人工读取代码和既有抓包分析文档，不使用旧逐包生成脚本或统计脚本作为口径。这里的“像不像真实浏览器”不是判断某个单包，而是看 **包族是否完整、字段是否一致、时序是否自然、浏览器执行证据是否真实产生**。

## 2. 结论先行

当前代码已经在努力补齐真实浏览器会出现的 PayPal 风控包族，包括 FraudNet、DFPJS、FPTI、Tealeaf、Datadog、Weasley logger、captcha logclientdata、OTP、Hagrid authorize 等；它不是只发 GraphQL 主业务包的最简协议流。

但它仍然不像真实浏览器，核心原因是：

1. **大量浏览器指纹是 Python 合成的**：canvas、WebGL、audio、JS heap、navigation timing、rDT、field timing、Tealeaf 行为、Datadog RUM 都是随机或模板化生成，不是 Chrome 运行 JS 后自然产物。
2. **DataDome 只有边缘模拟，没有真实二进制采集链**：代码会读取 `datadome` cookie 和注入 `x-datadome-clientid`，也会加载 `ddbm2.paypal.com/tags.js`，但没有真实浏览器执行 `ddbm2.paypal.com/js/` 二进制 body 的能力。
3. **mtr sealedResult 缺失**：真实抓包有 `/mtr/1a7c...` GET/POST 和响应 `sealedResult`，当前主流程没有对应生成/提交能力。
4. **captcha 存在明显非浏览器路径**：默认/可选路径包含 `frontend_disable` 本地合成 200/204、fake token、CapSolver 后端代解、Node happy-dom helper。这些不是真实浏览器中 hCaptcha/reCAPTCHA iframe、hsw、postMessage、挑战脚本自然执行的完整证据链。
5. **Client Hints 和浏览器 profile 有硬编码痕迹**：`config.py` 固定 Chrome/150、BR、pt-BR、Windows、RTX 4060、DPR 1.25；`PayPalSession` 默认强制高熵 CH 已可用。真实 Chrome 的高熵 hints、Accept-CH、Permissions-Policy、跨源委派和请求顺序更细。
6. **行为时序不自然**：代码按阶段连续补发包，没有真实用户阅读、加载、输入、captcha、OTP、页面可见性切换、资源 waterfall 产生的自然间隔和失败/重试噪声。
7. **协议流成功不等于支付可用**：真实抓包中即使 captcha/OTP/authorize 通过，仍在 `SignUpNewMemberMutation` addCard 和 Stripe `PAYER_CANNOT_PAY` 处失败。代码尝试轮换卡、复用 access token、fallback 到 Hagrid，但这不能消除 PayPal provider 对 payer/instrument 的最终拒绝。

## 3. 包族覆盖对比

| 风控/遥测包族 | 真实浏览器基线 | 当前代码实现 | 差异判断 |
|---|---|---|---|
| FraudNet `c.paypal.com/v1/r/d/b/p1,p2,pa,w,p3` | 有 BA、EC、Hagrid 多阶段采集，返回 `vf/sc/ddi`，字段来自真实 JS 环境 | `fingerprint.py` 合成 `p1/p2/w/pa/p3`，`flow.py` 多阶段调用 | 路径覆盖较完整，但 payload 是模板+随机数，不是真实浏览器执行结果 |
| FraudNet rDT `/w?d={rDT...}` | 有长时间序列，和页面行为时序绑定 | `_rdt_string()` 随机生成锚点序列 | 形态相似，行为来源不真实 |
| DFPJS `/identity/di/log` | 真实 DFPJS 生命周期：loaded、vendor invoked、response received、edge mapping complete | `send_identity_di_log()` 直接发送事件数组 | 事件名覆盖，但没有供应商 JS 真实运行、edge mapping 真实响应 |
| mtr `/mtr/1a7c...` | GET/POST 后返回 `sealedResult` | 主流程未实现 mtr sealed result 采集/提交 | 关键缺失 |
| DataDome `ddbm2.paypal.com/js/` | 二进制 body，响应设置 `datadome` cookie | 只加载 `ddbm2.paypal.com/tags.js`，捕获页面里的 client id，向 PayPal 请求注入 `x-datadome-clientid` | 缺少真实 DataDome JS 二进制采集和 cookie 生成链 |
| Tealeaf `/platform/tealeaftarget` | gzip UIC 行为录制，page id、TLTSID/TLTDID、DOM/input/click/mouse | `tealeaf.py` 生成 gzip JSON、随机 mouse path、模板 focus/input/click | 路径和 envelope 像，但 DOM 和事件不是页面真实产生 |
| FPTI `t.paypal.com/ts` | 页面、authchallenge、Hagrid 多阶段，含真实 screen/window/timing/ads_client_data | `send_analytics_ts()` 生成固定参数和少量随机 timing | 缺少完整 `ads_client_data` 级别的真实 window/navigator 快照 |
| Datadog RUM/replay | RUM view/action/resource/error/replay，含 service、view/session、fetch tampered 等 | `analytics.py` 只合成 view/action 到 RUM intake | 没有真实 resource waterfall、error、long task、replay 片段 |
| hCaptcha/reCAPTCHA/authchallenge | iframe、api.js、checksiteconfig、getcaptcha、hsw、logclientdata、validate/verify | `flow.py` 支持 frontend_disable、CapSolver、Node helper、fake close、logclientdata | 路径复杂但非浏览器执行；frontend_disable/fake token 是强异常 |
| OTP GraphQL | initiate 多次、confirm `CONFIRMED` | `flow.py` 交互式输入 OTP，先发 idapps getOtpChallengeOperation | 业务路径接近真实，但用户输入节奏和页面行为仍是合成 |
| Hagrid authorize | 加载 Hermes/Hagrid review，再 GraphQL authorize，return Stripe URL | `_phase4_authorize()` 加载 Hermes/Hagrid，发 Tealeaf/Datadog，再 authorize | 主业务路径接近，但前置浏览器上下文仍不完整 |

## 4. 最大不像真实浏览器的点

### 4.1 mtr sealedResult 缺失

真实浏览器基线中 `/mtr/1a7c...` 是非常重要的设备采集链：URL 绑定 `cmid=BA-*`、`chnl=iwc-mxo`、`btz`、`ci=js/3.12.1`，响应返回 `requestId` 和长 `sealedResult`。

当前代码中没有等价的 mtr 采集器。`flow.py` Phase 1 会发送 FraudNet、DFPJS、Tealeaf、FPTI、Datadog 和 observability，但没有 `/mtr/1a7c...` 的 GET/POST，也没有 sealed result。这个缺口很显眼，因为真实浏览器会在 BA `/pay` 前段就出现 mtr。

### 4.2 DataDome 不是完整浏览器链

真实抓包中 `ddbm2.paypal.com/js/` 请求体是二进制采集数据，响应明确设置：

```json
{"status":200,"cookie":"datadome=...; Domain=.paypal.com; Path=/; Secure; SameSite=None"}
```

当前代码的 DataDome 逻辑主要在两处：

- `PayPalFlow._extract_datadome_clientid()` 从 HTML 中提取初始 client id。
- `PayPalSession._inject_datadome_header_if_needed()` 在没有 `datadome` cookie 时对 PayPal/Venmo host 注入 `x-datadome-clientid`。

这能模拟 PayPal 页面 monkey-patch fetch/XHR 后的请求头行为，但不能证明 DataDome JS 在真实浏览器里运行，也不能自然产生 `ddbm2.paypal.com/js/` 二进制请求体和 cookie。因此 DataDome 维度仍像“补 header”，不像真实执行。

### 4.3 指纹 payload 是合成的，不是采集的

`fingerprint.py` 明确在 Python 里构造浏览器环境：

- `generate_runtime_profile()` 固定或随机 screen、viewport、GPU、hardwareConcurrency、DPR、connection。
- `device_fingerprint` 中 `canvas_h`、`cv_sig`、`webgl_ext_hash`、`audio_val` 都由随机数/哈希生成。
- `_nav_timing()` 随机生成 navigationStart、connectStart、responseStart、domInteractive 等 timing。
- `_build_p1_payload()`、`_build_p2_payload()`、`_build_pa_payload()` 直接拼装 FraudNet payload。

这和真实浏览器的区别是根本性的：真实 `p1/p2/pa/w` 来自 PayPal/FraudNet JS 读取当前 Chrome 对象、Performance API、Canvas/WebGL/Audio、插件、权限和地理位置。当前代码只能“长得像”，不能保证跨 API 的细节一致，例如 WebGL renderer 与 canvas/audio hash、JS heap、performance timing、GPU driver、Chrome version 的真实组合。

### 4.4 captcha 存在 fake close / synthetic response 痕迹

`session.py` 支持 `PAYPAL_CAPTCHA_BYPASS_MODE=frontend_disable`，会对这些 URL 本地返回 synthetic response：

- `/auth/validatecaptcha` -> synthetic 200 HTML
- `/auth/verifyhcaptchapassive` -> synthetic 200 HTML
- captcha asset / iframe / createchallenge 类资源 -> synthetic 204

`flow.py` 又会发送 fake token 和 fake telemetry：

- `frontend-hcaptcha-disabled`
- `CLIENT_SIDE_HCAPTCHA_PASSIVE_SOLVED`
- `CLIENT_SIDE_PPCAPTCHA_SOLVED`
- fake `/auth/verifyhcaptchapassive`
- fake `/auth/validatecaptcha`

真实浏览器基线里，captcha 链条包含 hCaptcha/reCAPTCHA iframe、api.js、checksiteconfig、getcaptcha、hsw、challenge iframe、PayPal authchallenge HTML hidden fields、logclientdata、validate/verify。当前 fake close 能让协议流继续，但它在抓包里会缺少真实第三方 captcha 资源加载、真实 challenge token 来源、真实 iframe/postMessage/hsw 执行证据。

即使使用 `capsolver`，它也是后端代解：PayPal 侧看到的 token 可能有效，但本地浏览器环境中没有完整的人机交互和 iframe 执行链。

### 4.5 Client Hints / UA / profile 有硬编码组合

`config.py` 固定：

- UA：Windows Chrome/150
- `chrome_full_version=150.0.7857.1`
- `country=BR`、`language=pt-BR`、`locale=pt_BR`
- timezone：`America/Sao_Paulo`
- platform：`Win32`
- hardwareConcurrency：`8`
- deviceMemory：`8`
- DPR：`1.25`
- GPU：`NVIDIA GeForce RTX 4060`

`session.py` 会构造低熵 Client Hints，也会构造高熵 hints。更关键的是，`PayPalSession.__init__()` 默认把 `_accept_ch_received` 设为开启状态，除非显式关闭 `PAYPAL_FORCE_HIGH_ENTROPY_CH`。真实 Chrome 是先由服务端 `Accept-CH` 和 Permissions-Policy 决定后续同源/委派请求是否发送高熵 hints；初始 navigation 不会直接带完整高熵 hints。

代码里已经有“只同源或委派 host 注入高熵 hints”的意识，但默认强制已接收 Accept-CH 仍可能造成早发、漏发或跨源委派细节不完全一致。

### 4.6 FPTI 和 Datadog 缺少真实浏览器快照深度

真实 FPTI 的 `ads_client_data` 能看到：

- `webdriverfalse`
- `window.Chrome=[object Object]`
- `callPhantom=undefined`
- `_phantom=undefined`
- PDF 插件列表
- hardwareConcurrency、deviceMemory、History、screen/window、geolocation

当前 `send_analytics_ts()` 只发送 page、locale、screen、viewport、timezone、pxpguid、calc 等基础参数，没有构造真实 `ads_client_data` 等深字段。

Datadog 方面，真实浏览器会有 resource count、long task、view/action、可能还有 replay、errors、fetch tampering、WebAuthn support、cookie/iframe 状态。当前 `send_datadog_rum_view()` 和 `send_datadog_rum_action()` 只是直接 POST 两类事件，缺少真实 SDK 从页面运行态收集出来的 resource waterfall、replay chunk 和错误上下文。

### 4.7 Tealeaf 行为不像真实用户输入

`tealeaf.py` 的优点是 envelope、gzip、AppKey、serialNumber、pageId/tabId、focus/input/blur/click/scroll 等结构都覆盖到了。

但数据是模板生成：

- 鼠标轨迹是两点之间随机 jitter。
- 字段输入只记录 value length 和 masked，不来自真实 DOM。
- 页面 DOM capture 为空或截断传入 HTML，不是 Tealeaf JS 的真实 DOM diff。
- offset/gap 是随机区间，没有和真实 GraphQL、captcha、用户阅读、OTP 输入形成同一时间线。

因此 Tealeaf 路径“存在”，但行为真实性不足。

### 4.8 时序和资源 waterfall 不自然

真实浏览器会自然加载大量静态资源、JS chunk、CSS、font、image、iframe、captcha 资源、Datadog/Tealeaf/FPTI beacon，并且这些请求与用户停顿、输入、captcha、OTP、页面切换交错。

当前 `flow.py` 是阶段式：

1. `_phase0_initial_load()` 加载入口。
2. `_phase1_risk_controls()` 连续发送风险包。
3. `_phase2_create_account()` 连续推进 ModXO 和 signup warm-up。
4. `_phase3_signup_and_2fa()` 等待命令行 OTP，再批量 Tealeaf/Datadog/action/signUp。
5. `_phase4_authorize()` 加载 Hagrid 后 authorize。

这种阶段式补包在功能上清楚，但和真实浏览器 waterfall 的并发、资源依赖、微小失败、缓存、重试、visibility/page lifecycle 差别很大。

## 5. 代码里已经做得接近真实浏览器的部分

不是所有实现都很粗糙，当前代码有一些正确方向：

1. **阶段划分贴近真实链路**：BA `/agreements/approve` -> `/pay` -> captcha/country -> EC signup -> GraphQL warm-up -> OTP -> SignUpNewMember -> Hermes/Hagrid -> authorize -> Stripe return。
2. **GraphQL schema 与真实包相近**：`graphql.py` 中的 `DeferredFeature`、`CheckoutSessionDataQuery`、`SupportedFundingSourcesQuery`、`InstallmentOptionsQuery`、`AddressAutocompleteFromPostalCodeQuery`、OTP、SignUp、authorize 都对应真实抓包中的业务路径。
3. **PayPal headers 有浏览器意识**：`session.py` 处理 `Sec-Fetch-*`、Origin、Referer、Client Hints、PayPal-Client-Context、Metadata-Id、X-App-Name、country/locale。
4. **内容 manifest 不再完全硬编码**：`flow.py` 会从 signup HTML、content manifest、JS asset、cache/env 中解析 `contentIdentifier`，并优先做 live manifest request。
5. **Hagrid 前置上下文被补上**：`_phase4_authorize()` 不是直接打 authorize，而是先加载 Hermes/Hagrid review context，减少 `BUYER_NOT_SET`。
6. **能记录程序侧流量**：`traffic_recorder.py` 能把 PayPalSession 和 CapSolver 请求写成 roxy-like 目录，便于离线和真实抓包对比。

这些说明当前代码不是“完全不像”；更准确说，它是一个**协议级重放/合成浏览器信号实现**，而不是一个真实 Chrome 页面执行实现。

## 6. 与真实抓包中的失败原因关系

真实抓包已经证明：浏览器执行痕迹完整并不保证支付成功。第二组抓包里：

- passive captcha 继续到 `CAPTCHA_SOLVED`。
- OTP 最终 `CONFIRMED`。
- PayPal authorize 返回 Stripe `status=success` return URL。
- 但 `SignUpNewMemberMutation` 返回 `INSTRUMENT_SHARING_LIMIT_EXCEEDED` / `CARD_GENERIC_ERROR`，checkpoint 是 `addCard`。
- Stripe 最终返回 `payment_method_provider_decline` / `paypal_payment_declined` / `PAYER_CANNOT_PAY`。

因此当前代码即使补齐更多浏览器信号，也不能绕过 payer/instrument 层面的拒绝。它目前最可能暴露的不是单个 header 错，而是：

1. 浏览器执行证据不足，导致更频繁的 captcha/authchallenge 或更差的风控评分。
2. 即使协议走到 authorize，PayPal/Stripe 仍可能因为账号、卡、手机号、身份、地理、支付工具历史而拒绝。
3. 卡错误后自动轮换卡会改变业务身份/支付工具组合，可能增加账户/设备/手机号与 instrument 不一致的风险信号。

## 7. 优先级排序：哪些差异最值得关注

| 优先级 | 差异 | 为什么重要 |
|---|---|---|
| 1 | mtr sealedResult 缺失 | 真实 BA `/pay` 风控前段有明确 sealed device result；当前无等价包族。 |
| 2 | DataDome 二进制采集缺失 | 真实浏览器拿到 `datadome` cookie；当前主要是 client id/header 模拟。 |
| 3 | captcha fake/synthetic 路径 | `frontend_disable` 和 fake token 与真实 iframe/hsw/challenge 执行链差别最大。 |
| 4 | FraudNet 指纹合成 | 路径有，但 canvas/WebGL/audio/performance/rDT 不是 Chrome 真实采集。 |
| 5 | FPTI `ads_client_data` 深字段缺失 | 真实浏览器判断字段最直观，当前只发基础 ts 参数。 |
| 6 | Tealeaf/Datadog 行为模板化 | 有 envelope，但缺少真实 DOM、resource、replay、长任务和用户时序。 |
| 7 | Client Hints 默认强制高熵 | 可能与真实 Accept-CH / Permissions-Policy 时序不一致。 |
| 8 | 固定 BR/Chrome/RTX profile | 比完全随机好，但仍容易和代理/IP、浏览器版本、GPU/OS/driver 组合不一致。 |

## 8. 最终判断

当前实现已经覆盖了 PayPal 主业务链路和多类风控遥测路径，但它仍不是“真实浏览器流量”。它更像是：

> Python HTTP session + curl_cffi impersonation + 手工构造的 PayPal 风控/行为包 + 可选 captcha 后端代解/前端 fake close。

真实浏览器流量的关键特征是：PayPal/第三方 JS 在同一个 Chrome runtime 中自然读取环境、执行挑战、产生 cookies、发送 telemetry，并且所有结果在时间线和资源 waterfall 中互相印证。当前代码的关键缺口正是这些“同一真实 runtime 产生的证据链”。

如果只问“哪些包最不像真实浏览器”，排序是：

1. 缺失 `/mtr/... sealedResult`。
2. 缺失真实 `ddbm2.paypal.com/js/` DataDome 二进制采集和 cookie 生成。
3. `frontend_disable` synthetic captcha 200/204 与 fake token。
4. Python 合成的 FraudNet canvas/WebGL/audio/performance/rDT。
5. 模板化 Tealeaf/Datadog/FPTI，而非真实 SDK runtime 输出。
