# PayPal 发包差异审查报告

## 1. 样本与结论

### 样本

- 代码基线：`paypal/flow.py`、`paypal/session.py`、`paypal/graphql.py`、`paypal/fingerprint.py`、`paypal/analytics.py`、`paypal/tealeaf.py`。
- 真实浏览器样本：`captures/roxy-paypal-20260705-103548`，由 `tools/roxy_cdp_capture.mjs` 记录，`summary.json` 记录总请求数 750。
- 程序侧主样本：`captures/program-paypal-20250666f7f6`，由 `paypal.traffic_recorder` 记录，`summary.json` 记录总请求数 122。
- 主对比输出：`/tmp/paypal_diff_20250666f7f6.json`，过滤静态资源后为 program 85 个 PayPal/挑战相关请求，roxy 220 个请求。

### 总结结论

当前代码的静态流程已经覆盖了主路径：`agreements/approve` 初始页、ModXO `/pay`、FraudNet/Tealeaf/analytics、Weasley signup GraphQL、idapps OTP challenge、2FA、`SignUpNewMemberMutation`、Hermes/Hagrid review、最终 `authorize`。这些阶段在 `paypal/flow.py:451` 的 `run()` 中串联。

但现有最完整 program 实录没有跑到 Hermes/Hagrid 与最终 authorize；它在多轮 `SignUpNewMemberMutation` 后被 authchallenge/validatecaptcha 路径截住，最后一个记录是 `POST https://www.paypal.com/auth/validatecaptcha`。因此报告必须区分“代码准备发送的后续阶段”和“当前程序实录已实际发送的请求”。

与 roxy 浏览器相比，program 主样本的主要差距不是单个 GraphQL body shape，而是浏览器环境级流量密度、挑战 iframe/recaptcha/hcaptcha 流程、FPTI/XO logger/Tealeaf/Datadog 节奏，以及 Hermes/authorize 后段是否实际到达。`SignUpNewMemberMutation`、2FA、地址/分期 GraphQL 在已匹配样本里的变量键基本一致。

## 2. 数据质量限制

- `captures/roxy-paypal-20260705-103548/network/events.jsonl` 有 2070 个非空行，其中 8 行 JSON 解析失败。
- 坏行示例包括 `Unterminated string starting at: line 1 column 2756`、`Invalid \\uXXXX escape`、`Expecting value: line 1 column 1`。
- `tools/compare_paypal_traffic.py` 的 `read_jsonl()` 会跳过坏 JSON 行，所以对比结果是“可解析事件子集”，不是 roxy 原始记录的 100% 完整复原。
- roxy `summary.json` 的总请求数 750 包含静态资源、页面资源、非 PayPal 周边请求；主差异报告过滤掉 image/font/stylesheet/script/media 和常见静态扩展后剩 220 个请求。

### 抓包目录横向校验

- program 抓包是 `paypal.traffic_recorder` 格式：`metadata.json`、`summary.json`、`network/events.jsonl`、`network/requests.tsv`、`network/requests/`、`network/bodies/`。
- roxy 抓包是 CDP/Chrome 格式：除 `network/events.jsonl` 外，还包含 `html/`、`js/`、`css/`、`screenshots/`、`storage/`、`resources.jsonl`。
- 现有 program 抓包请求数演进：早期样本约 67-68 个请求；`program-paypal-a3cebdb6e785` 为 76；`program-paypal-ba8eu-afterfix` 为 101；`program-paypal-ba8eu-afterfix2/3/4` 只有 15-16 个请求，过度跳过静态/前端路径，不适合作为主对齐样本；`program-paypal-20250666f7f6` 为 122，是当前最完整 program 样本。
- 现有 roxy 样本：`roxy-paypal-20260705-102145` 为 1036 个请求，`roxy-paypal-20260705-103548` 为 750 个请求；后者是 active/last 样本，因此本报告以它为主。

## 3. 当前代码的计划发包顺序

### Phase 0: 初始 approve 页面

来源：`paypal/flow.py:594`。

1. `GET https://www.paypal.com/agreements/approve?ba_token=...`
2. 如遇 302，跟随到 `/pay` 或 signup 相关页面，并解析 `ssrt`、`ctxId`、`EC-...`。
3. 如遇 DataDome 403，代码记录并尝试带 `YWRzZGRjYXB0Y2hh=1` 的 POST。

### Phase 1: 风控与遥测

来源：`paypal/flow.py:4594`。

1. `GET c6.paypal.com/v1/r/d/b/p3`
2. FraudNet rDT：`c.paypal.com/v1/r/d/b/w`
3. ModXO no-interaction server action：`POST www.paypal.com/pay?...paypal_client_cfci=...no_interaction`
4. FraudNet device fingerprint：`POST c.paypal.com/v1/r/d/b/p1`、`p2`、`w`
5. FPTI：`GET t.paypal.com/ts`
6. identity DI：`POST www.paypal.com/identity/di/log`
7. Tealeaf：`POST www.paypal.com/platform/tealeaftarget`
8. Datadog RUM 与 observability：`browser-intake-datadoghq.com`、`/pay/api/trpc/observability.handleClientEmit`

补充：Phase 1 末尾会调用 `_send_modxo_frontend_captcha_solved_packets()`，但该方法在 `paypal/flow.py:441` 当前是 no-op，直接 `return`。因此不能把它算作实际发送了浏览器里的 hCaptcha/passive iframe 或 PayPal `/auth/*` 挑战流。

### Phase 2: ModXO create-account 到 signup

来源：`paypal/flow.py:4670`。

1. 若已有 EC token，直接 `GET checkoutweb/signup` 并刷新 content manifest。
2. 否则先发 ModXO `Pay_With_Card` server action：`POST www.paypal.com/pay?...paypal_client_cfci=...Pay_With_Card`
3. 重新加载 `/pay` RSC：`GET www.paypal.com/pay?...&_rsc=...`
4. 发送 ddbm2/FraudNet/field event/DI。
5. 发送 `Continue_To_Payment` server action，期望返回 `onboardingRedirectUrl`。
6. 加载 signup HTML，抓取 Weasley `content-manifest.*.json` 和 locale/contentIdentifier。
7. Warm-up GraphQL：`GriffinMetadataQuery`、`DeferredFeature`、`CheckoutSessionDataQuery`、`SupportedFundingSourcesQuery`。

### Phase 3: signup、OTP、建号

来源：`paypal/flow.py:5201`。

1. Tealeaf signup 页面活动。
2. `POST www.paypal.com/idapps/graphql`，operation 为 `getOtpChallengeOperation`。
3. `InitiateRiskBasedTwoFactorPhoneConfirmationMutation` 发短信。
4. `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation` 验证 OTP。
5. Tealeaf 表单交互与 Datadog action。
6. 重新刷新 `content-manifest.*.json`。
7. `InstallmentOptionsQuery`、`AddressAutocompleteFromPostalCodeQuery`。
8. `SignUpNewMemberMutation`，带 `fn_sync_data`、卡、地址、身份文档、phone、legalAgreements、`contentIdentifier`。
9. 成功后设置 EUAT cookie。

### Phase 4: Hermes/Hagrid 与 authorize

来源：`paypal/flow.py:5386`。

1. 构造并加载 `GET www.paypal.com/webapps/hermes?...fromSignupLite=true...`
2. 加载 contingency Hermes URL 与 `billingLite=1#/billingweb/review` review 上下文。
3. Tealeaf、Datadog view/action。
4. `POST www.paypal.com/graphql/` batched `authorize`，变量为 `billingAgreementId`、`fundingPreference.balancePreference=OPT_OUT`、`legalAgreements={}`。
5. 如遇 `BUYER_NOT_SET`，重新加载 Hermes/Hagrid 并换 metadata id 重试。

## 4. 程序侧主样本实际发包时间线

主样本：`captures/program-paypal-20250666f7f6`。

过滤后 85 个请求、24 个唯一签名。关键顺序如下：

1. `GET www.paypal.com/agreements/approve`
2. `GET c6.paypal.com/v1/r/d/b/p3`
3. `GET c.paypal.com/v1/r/d/b/w`
4. `POST www.paypal.com/pay`
5. `POST c.paypal.com/v1/r/d/b/p1`、`p2`、`w`
6. `GET t.paypal.com/ts`
7. `POST www.paypal.com/identity/di/log`
8. `POST www.paypal.com/platform/tealeaftarget`
9. `POST www.paypal.com/pay/api/trpc/observability.handleClientEmit`，状态 400
10. `GET www.paypal.com/pay/api/countries`
11. `GET paypalobjects/checkoutweb/release/weasley/content-manifest.json`
12. `POST www.paypal.com/idapps/graphql`，operation `getOtpChallengeOperation`
13. 多个 `POST www.paypal.com/auth/logclientdata`
14. `POST www.paypal.com/auth/verifyhcaptchapassive`
15. `POST www.paypal.com/auth/validatecaptcha`
16. 多轮 `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`
17. `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`
18. `InstallmentOptionsQuery`
19. `AddressAutocompleteFromPostalCodeQuery`
20. 15 次 `GET c.paypal.com/v1/r/d/b/w`
21. 3 次 `SignUpNewMemberMutation`
22. 之后继续进入 `auth/logclientdata`、`verifyhcaptchapassive`、`validatecaptcha`

该样本没有出现：`GET www.paypal.com/webapps/hermes`、`POST www.paypal.com/graphql?authorize`、merchant return URL。

## 5. roxy 浏览器样本实际发包时间线

主样本：`captures/roxy-paypal-20260705-103548`。

过滤后 220 个请求、49 个唯一签名。关键顺序如下：

1. `GET www.paypal.com/agreements/approve`
2. 多个 `/pay/api/trpc/observability.handleClientEmit`
3. `GET www.paypal.com/mtr/.../x0` 与 `POST www.paypal.com/mtr/...`
4. `GET paypalobjects/.../hcaptcha/hcaptchapassive.html`
5. `POST www.paypal.com/pay`
6. FraudNet：`POST c.paypal.com/v1/r/d/b/p2`、`pa`、`w`、`p1`
7. hCaptcha iframe 与 API：`newassets.hcaptcha.com/.../hcaptcha.html`、`api.hcaptcha.com/checksiteconfig`、`api.hcaptcha.com/getcaptcha/...`
8. `POST www.paypal.com/identity/di/log`
9. `POST www.paypal.com/auth/verifyhcaptchapassive`
10. 多个 FPTI：`GET t.paypal.com/ts`
11. `POST ddbm2.paypal.com/js`
12. `GET www.paypal.com/pay/api/countries`
13. 多个 Tealeaf：`POST www.paypal.com/platform/tealeaftarget`
14. `POST www.paypal.com/pay` 返回 303
15. `GET www.paypal.com/pay` RSC/fetch
16. `GET www.paypal.com/pay/checkout/signup/contact` 返回 307
17. `GET www.paypal.com/agreements/approve` 返回 302
18. `GET paypalobjects/checkoutweb/release/weasley/content-manifest.json`
19. Weasley logger 与 GraphQL warm-up：`DeferredFeature`、`GriffinMetadataQuery`、`CheckoutSessionDataQuery`、`SupportedFundingSourcesQuery`
20. signup 阶段 FraudNet/Tealeaf/FPTI/idapps：`POST www.paypal.com/idapps/graphql`
21. recaptcha v3/v2 iframe 与 `/auth/logclientdata`、`/auth/validatecaptcha`
22. `InstallmentOptionsQuery`、`AddressAutocompleteFromPostalCodeQuery`
23. 多轮 `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`
24. `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`
25. `SignUpNewMemberMutation`
26. `GET www.paypal.com/webapps/hermes` 两次
27. Hermes/review 阶段 logger/FPTI/ddbm2/FraudNet
28. `POST www.paypal.com/graphql?authorize`
29. authorize 之后继续 FPTI/logger/ddbm2/FraudNet 和 hCaptcha API

## 6. 顺序差异

### A. program 过早进入协议主线，浏览器先产生更多前端挑战/遥测

roxy 在初始 `/pay` 与进入 signup 之前，有大量 observability、MTR、hCaptcha passive、FraudNet `pa`、ddbm2、FPTI、Tealeaf。program 有对应类别的一部分，但数量和交织顺序明显更稀疏。

典型差异：

- roxy `POST www.paypal.com/pay/api/trpc/observability.handleClientEmit` 33 次；program 1 次，而且该次状态为 400。
- roxy `POST www.paypal.com/xoplatform/logger/api/logger` 51 次；program 6 次。
- roxy `POST www.paypal.com/platform/tealeaftarget` 28 次；program 4 次。
- roxy `GET t.paypal.com/ts` 21 次；program 1 次。

### B. GraphQL warm-up 顺序不完全一致

现有测试 `tests/test_browser_flow_order.py:208` 期望 `_phase2_create_account()` 的 warm-up 顺序为：

1. `GriffinMetadataQuery`
2. `DeferredFeature`
3. `CheckoutSessionDataQuery`
4. `SupportedFundingSourcesQuery`

roxy 时间线中对应段为：

1. `DeferredFeature`
2. `GriffinMetadataQuery`
3. `CheckoutSessionDataQuery`
4. `SupportedFundingSourcesQuery`

因此当前测试与此 roxy 样本的实际顺序不一致。program 主样本里这些 warm-up query 没完整出现在 filtered timeline 的早段；主 diff 把它们列为 browser missing/extra 的混合项，说明样本间 BA/EC 阶段没有完全对齐。

### C. program 主样本没到达后段 Hermes/authorize

roxy 在 `SignUpNewMemberMutation` 后继续：

- `GET www.paypal.com/webapps/hermes` x2
- review 阶段 logger/FPTI/FraudNet/ddbm2
- `POST www.paypal.com/graphql?authorize` x1

program 主样本在 `SignUpNewMemberMutation` 后进入重复 authchallenge 相关请求，未出现 Hermes/authorize。因此“代码里已有 Phase 4”不能等同于“当前 program 实录已验证 Phase 4 发包”。

### D. hCaptcha/recaptcha 处理策略不同

roxy 有真实浏览器挑战链路：PayPal/paypalobjects challenge document、`newassets.hcaptcha.com` iframe、`api.hcaptcha.com/checksiteconfig`、`getcaptcha`、recaptcha enterprise anchor/reload/clr，再到 PayPal `/auth/logclientdata`、`/auth/verifyhcaptchapassive`、`/auth/validatecaptcha`。

program 主样本只记录到 PayPal `/auth/*` 子集和 synthetic 响应，没有同等的 iframe/API 交通；再结合 `_send_modxo_frontend_captcha_solved_packets()` 的 no-op 状态，说明当前程序不是在协议层完整复刻浏览器挑战环境，而是在被挑战后走较窄的验证/重试路径。

## 7. 内容与 header 差异

### A. GraphQL body shape

主 diff 中匹配到的 GraphQL 请求体结构大体一致：

- `AddressAutocompleteFromPostalCodeQuery`：两边变量键均为 `country`、`postalCode`、`token`。
- `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`：两边变量键均为 `authId`、`challengeId`、`pin`、`token`。
- `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`：两边变量键均为 `locale`、`phoneCountry`、`phoneNumber`、`token`。
- `InstallmentOptionsQuery`：两边变量键均为 `buyerCountry`、`cardNumber`、`cardType`、`token`。
- `SignUpNewMemberMutation`：两边都有 `fn_sync_data`，变量键一致；`contentIdentifier` 都是 `BR:pt:759169e5b7de230616d673bd3498ac79:compliance.signupTerms`。

这说明当前主要不是这些 matched GraphQL body 的字段缺失问题。

### B. Client Hints 与语言

已观察到的 header 差异：

- `sec-ch-device-memory`：roxy 为 `32`，program 为 `8`。
- `accept-language`：roxy signup 示例为 `en-US,en;q=0.9`，program 为 `pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7`。
- `POST www.paypal.com/platform/tealeaftarget`：roxy `x-requested-with=fetch`，program `XMLHttpRequest`。
- 多个 program 请求额外带 `accept-language`、`sec-fetch-*`，或在 `/pay` POST 缺少 roxy 样本里的 `content-type`。

代码来源：`paypal/session.py:70` 的 `build_common_headers()` 固定生成低熵 client hints 与 `Accept-Language`；`paypal/session.py:97` 的 `build_high_entropy_hints()` 使用 profile 里的 `device_memory`。

### C. GraphQL app headers

roxy GraphQL 样本有：

- `x-requested-with: fetch`
- `x-app-name: checkoutuinodeweb_weasley`
- `x-country: BR`
- `x-locale: pt_BR`
- `paypal-client-context: EC-...`
- `paypal-client-metadata-id: EC-...`

当前 program 对部分请求已经有 app headers，但不同 endpoint/阶段仍存在 `extra_headers`、`None` 清除、大小写和 locale 来源差异，需要按 operation 逐个对齐，不能只靠全局 common headers。

## 8. 缺失、额外和数量差异

### roxy 有而 program 主样本缺失/不足

高频或关键类别：

- `GET t.paypal.com/ts`：roxy 21，program 1。
- `POST www.paypal.com/xoplatform/logger/api/logger`：roxy 51，program 6。
- `POST www.paypal.com/platform/tealeaftarget`：roxy 28，program 4。
- `POST www.paypal.com/pay/api/trpc/observability.handleClientEmit`：roxy 33，program 1。
- `POST ddbm2.paypal.com/js`：roxy 6，program 主样本没有对应 POST。
- `POST c.paypal.com/v1/r/d/b/pa`：roxy 5，program 主样本没有同签名 POST。
- hCaptcha/recaptcha iframe/API：roxy 有完整 iframe/API 流，program 只记录 PayPal `/auth/*` 子集。
- `GET www.paypal.com/webapps/hermes` 与 `POST www.paypal.com/graphql?authorize`：roxy 有，program 主样本没有。

### program 主样本额外/异常突出的类别

- `GET c.paypal.com/v1/r/d/b/w`：program 15，roxy 对应主要是 POST `w` 与 POST `pa`，GET `w` 在主 diff 中被列为 extra。
- `POST www.paypal.com/auth/logclientdata`：program 25，roxy 6。
- `POST www.paypal.com/auth/verifyhcaptchapassive`：program 5，roxy 1。
- `POST www.paypal.com/auth/validatecaptcha`：program 5，roxy 1。
- `SignUpNewMemberMutation`：program 3，roxy 1。

这些额外项符合“program 被挑战/重试路径困住”的特征。

### 资产噪声与关键请求分类

对后续 diff，建议默认忽略或低权重处理：

- Next.js 静态 chunk、CSS、字体、图片、source map。
- 高频但只用于遥测/行为轨迹的重复 `t.paypal.com/ts`、Tealeaf、XO logger、Datadog RUM、observability；这些不能完全忽略数量/顺序，但不应与业务 GraphQL 同权重。
- 重复的 FraudNet `w`/`p3` 轮询；保留初始 p1/p2/w/pa 和阶段切换处的关键发送，降低后续 204 噪声权重。

必须保留或高权重处理：

- `GET /agreements/approve`、`POST /pay` ModXO server action、`GET /pay` RSC/fetch、`GET /pay/checkout/signup/contact`。
- `GET paypalobjects/checkoutweb/release/weasley/content-manifest.*.json` 和 locale/contentIdentifier 解析。
- `POST /idapps/graphql`。
- `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`、`ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`、`InstallmentOptionsQuery`、`AddressAutocompleteFromPostalCodeQuery`、`SignUpNewMemberMutation`。
- Hermes/Hagrid `GET /webapps/hermes`、review context、final `authorize`。

## 9. 主要风险判断

1. **实录未验证最终授权路径**：Phase 4 代码存在，但主 program capture 没到达 Hermes/authorize，无法证明真实运行能完成 billing agreement approve。
2. **挑战路径比例过高**：program 中 `/auth/logclientdata`、`verifyhcaptchapassive`、`validatecaptcha` 与重复 signup 明显多于 roxy，说明当前协议流更容易被挑战或在挑战后重试。
3. **遥测节奏不足**：FPTI、XO logger、Tealeaf、observability、ddbm2、FraudNet `pa` 的数量和交错顺序与浏览器差距大。
4. **浏览器环境不一致**：device memory、Accept-Language、Tealeaf `x-requested-with` 等 header 与 roxy 样本不一致。
5. **测试顺序可能过期**：`test_browser_flow_order.py` 的 Weasley warm-up 顺序和当前 roxy 样本顺序不一致。
6. **挑战模拟入口是 no-op**：`_send_modxo_frontend_captcha_solved_packets()` 当前不发包，若后续代码或测试假设它会补齐 CAPTCHA_SOLVED 前端包，会误判实际覆盖度。

## 10. 建议的下一步对齐顺序

1. 先让 program 实录稳定跑到 Hermes/Hagrid 与 `authorize`，否则后段差异只能基于静态代码推断。
2. 修正 observability 400，确保 `/pay/api/trpc/observability.handleClientEmit` 至少不是格式错误。
3. 按 roxy 顺序补齐/重排 early `/pay` 前端遥测：MTR、hCaptcha passive iframe 前后、FraudNet `pa`、ddbm2、FPTI、Tealeaf、logger。
4. 对齐 Weasley warm-up 顺序，并更新 `tests/test_browser_flow_order.py` 的期望或说明为何采用不同样本顺序。
5. 按 operation 校准 GraphQL headers，而不是只改全局 headers。
6. 统一浏览器 profile：`device_memory`、locale/language、timezone、viewport/screen 与 roxy 样本一致。
